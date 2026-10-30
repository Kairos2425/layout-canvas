#!/usr/bin/env python3
"""layout-canvas frozen benchmark runner.

Scores the task set declared in ``bench/benchmark.toml`` against the
checked-in task directories and writes a ``flat-scalar-v2`` scorecard to
``bench/runs/<timestamp>/scorecard.json``.

Methodology (see bench/README.md):
  * scores only mean something against a frozen ``release_id``;
  * the public ``starter/`` is deliberately NOT the whole evaluation —
    every task that compiles additionally faces the hidden
    ``drc.total_violations == 0`` and ``lvs.match == true`` checks, even
    when ``instruction.md`` never mentions them;
  * ``--check-baseline`` gates deterministic metrics (drc violations,
    lvs match, device counts) on exact equality with the frozen
    ``results/reference.json`` and sim-derived values inside their
    recorded tolerance;
  * fail-closed everywhere: a missing simulator/engine reports
    ``unavailable`` — it counts in ``coverage.total`` but never in
    ``coverage.runnable`` and never as a pass.

Usage:
  python bench/run.py [--task ID] [--check-baseline] [--freeze]
                      [--out DIR] [--bench-dir DIR]
or via the CLI: ``layout-canvas bench ...``
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

METRIC_SCHEMA = "flat-scalar-v2"
BENCH_FILE = "benchmark.toml"
TASK_TYPES = ("verify", "spec", "optimize", "import", "candidates")

# Check statuses (flat-scalar-v2): pass | fail | unavailable | error
PASS, FAIL, UNAVAILABLE, ERROR = "pass", "fail", "unavailable", "error"

BENCH_DIR = Path(__file__).resolve().parent
REPO_ROOT = BENCH_DIR.parent


# ---------------------------------------------------------------------------
# Task loading
# ---------------------------------------------------------------------------


@dataclass
class Bound:
    metric: str
    min: float | None = None
    max: float | None = None
    equals: Any = None
    unit: str = ""
    tol: float | None = None  # absolute baseline tolerance
    tol_rel: float = 0.0  # relative baseline tolerance (fraction of |ref|)
    deterministic: bool = False  # exact-equality baseline gate
    note: str = ""


@dataclass
class Task:
    id: str
    type: str
    title: str
    deliverable: str
    pdk: str
    dir: Path
    bounds: list[Bound] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    run_cfg: dict[str, Any] = field(default_factory=dict)
    import_cfg: dict[str, Any] = field(default_factory=dict)
    cand_cfg: dict[str, Any] = field(default_factory=dict)
    sim_tol_rel: float = 0.05
    sim_tol_abs: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)


class TaskError(ValueError):
    """Malformed task directory — diagnostic names the file and field."""


def _req(cond: bool, msg: str) -> None:
    if not cond:
        raise TaskError(msg)


def load_task(task_dir: Path) -> Task:
    """Parse and validate one ``tasks/<id>/task.toml`` — fail-closed.

    Every problem raises :class:`TaskError` naming the file and the
    offending field; a malformed task never silently scores zero or
    crashes the suite with a bare traceback.
    """
    file = task_dir / "task.toml"
    if not file.is_file():
        raise TaskError(f"{task_dir}: missing task.toml")
    try:
        doc = tomllib.loads(file.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise TaskError(f"{file}: invalid TOML — {exc}") from None
    _req(isinstance(doc, dict), f"{file}: top-level must be a TOML table")

    tid = doc.get("id")
    _req(isinstance(tid, str) and bool(tid.strip()),
         f"{file}: missing/invalid 'id'")
    _req(tid == task_dir.name,
         f"{file}: id {tid!r} must equal the task directory name "
         f"{task_dir.name!r}")
    ttype = doc.get("type")
    _req(ttype in TASK_TYPES,
         f"{file}: 'type' must be one of {TASK_TYPES}, got {ttype!r}")
    deliverable = doc.get("deliverable")
    _req(isinstance(deliverable, str) and bool(deliverable),
         f"{file}: missing 'deliverable'")
    pdk = doc.get("pdk", "")
    _req(isinstance(pdk, str), f"{file}: 'pdk' must be a string")

    bounds: list[Bound] = []
    raw_bounds = doc.get("bounds", {})
    _req(isinstance(raw_bounds, dict),
         f"{file}: 'bounds' must be a table of metric -> spec")
    for metric, spec in raw_bounds.items():
        _req(isinstance(spec, dict),
             f"{file}: bounds[{metric!r}] must be a table "
             "({min,max,equals,unit,tol,tol_rel,deterministic})")
        known = {"min", "max", "equals", "unit", "tol", "tol_rel",
                 "deterministic", "note"}
        unknown = sorted(set(spec) - known)
        _req(not unknown,
             f"{file}: bounds[{metric!r}] unknown keys {unknown}; "
             f"known: {sorted(known)}")
        has_bound = any(k in spec for k in ("min", "max", "equals"))
        _req(has_bound,
             f"{file}: bounds[{metric!r}] needs min, max or equals")
        bounds.append(Bound(
            metric=str(metric),
            min=(float(spec["min"]) if spec.get("min") is not None else None),
            max=(float(spec["max"]) if spec.get("max") is not None else None),
            equals=spec.get("equals"),
            unit=str(spec.get("unit", "")),
            tol=(float(spec["tol"]) if spec.get("tol") is not None else None),
            tol_rel=float(spec.get("tol_rel", 0.0)),
            deterministic=bool(spec.get("deterministic", False)),
            note=str(spec.get("note", "")),
        ))

    env = doc.get("env", {})
    _req(isinstance(env, dict) and all(
        isinstance(k, str) and isinstance(v, str) for k, v in env.items()),
        f"{file}: 'env' must be a string->string table")
    run_cfg = doc.get("run", {})
    _req(isinstance(run_cfg, dict), f"{file}: 'run' must be a table")
    import_cfg = doc.get("import", {})
    _req(isinstance(import_cfg, dict),
         f"{file}: 'import' must be a table")
    if ttype == "import":
        _req(isinstance(import_cfg.get("alias"), str)
             and isinstance(import_cfg.get("gds"), str),
             f"{file}: type 'import' needs [import] alias + gds")
    if ttype == "optimize":
        _req(isinstance(run_cfg.get("max_iterations"), int),
             f"{file}: type 'optimize' needs [run] max_iterations")

    cand_cfg = doc.get("candidates", {})
    _req(isinstance(cand_cfg, dict),
         f"{file}: 'candidates' must be a table")
    known_cand = {"variants", "expect", "expect_specs"}
    unknown_cand = sorted(set(cand_cfg) - known_cand)
    _req(not unknown_cand,
         f"{file}: 'candidates' unknown keys {unknown_cand}; "
         f"known: {sorted(known_cand)}")
    _req(isinstance(cand_cfg.get("variants", "starter/variants.json"), str),
         f"{file}: 'candidates.variants' must be a path string")
    expect = cand_cfg.get("expect", {})
    _req(isinstance(expect, dict) and all(
        isinstance(k, str)
        and v in ("passed", "failed", "unavailable")
        for k, v in expect.items()),
        f"{file}: 'candidates.expect' must map candidate names to "
        "passed|failed|unavailable")
    expect_specs = cand_cfg.get("expect_specs", {})
    _req(isinstance(expect_specs, dict) and all(
        isinstance(k, str) and isinstance(v, int)
        and not isinstance(v, bool)
        for k, v in expect_specs.items()),
        f"{file}: 'candidates.expect_specs' must map candidate names "
        "to integer spec pass counts")

    tolerances = doc.get("tolerances", {})
    _req(isinstance(tolerances, dict),
         f"{file}: 'tolerances' must be a table")
    return Task(
        id=tid, type=ttype, title=str(doc.get("title", tid)),
        deliverable=deliverable, pdk=pdk, dir=task_dir,
        bounds=bounds,
        env=dict(env), run_cfg=dict(run_cfg), import_cfg=dict(import_cfg),
        cand_cfg=dict(cand_cfg),
        sim_tol_rel=float(tolerances.get("sim_rel", 0.05)),
        sim_tol_abs=float(tolerances.get("sim_abs", 0.0)),
        raw=doc,
    )


def load_manifest(bench_dir: Path) -> dict[str, Any]:
    file = bench_dir / BENCH_FILE
    if not file.is_file():
        raise TaskError(f"{bench_dir}: missing {BENCH_FILE}")
    try:
        doc = tomllib.loads(file.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise TaskError(f"{file}: invalid TOML — {exc}") from None
    for key in ("release_id", "state"):
        if key not in doc:
            raise TaskError(f"{file}: missing required key {key!r}")
    slots = [t["id"] for t in doc.get("tasks", []) if "id" in t]
    return {"release_id": doc["release_id"], "state": doc["state"],
            "task_ids": slots, "doc": doc}


def discover_tasks(bench_dir: Path, only: str | None) -> list[Task]:
    manifest = load_manifest(bench_dir)
    ids = manifest["task_ids"] or sorted(
        d.name for d in (bench_dir / "tasks").iterdir() if d.is_dir())
    if only is not None:
        if only not in ids:
            raise TaskError(
                f"unknown task {only!r}; declared: {ids}")
        ids = [only]
    return [load_task(bench_dir / "tasks" / tid) for tid in ids]


# ---------------------------------------------------------------------------
# Environment / provenance
# ---------------------------------------------------------------------------


def _register_blocks() -> None:
    import layout_canvas.blocks.generic  # noqa: F401
    import layout_canvas.blocks.sky130  # noqa: F401
    try:
        import layout_canvas.blocks.ihp_sg13g2  # noqa: F401
    except Exception:
        pass


class _task_env:
    """Temporarily apply a task's env vars (paths resolved vs task dir)."""

    def __init__(self, task: Task):
        self._saved: dict[str, str | None] = {}
        self._task = task

    def __enter__(self):
        for key, val in self._task.env.items():
            self._saved[key] = os.environ.get(key)
            p = Path(val)
            os.environ[key] = str(
                p if p.is_absolute() else (self._task.dir / p).resolve())
        return self

    def __exit__(self, *exc):
        for key, old in self._saved.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old
        return False


def _git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT,
            capture_output=True, text=True, timeout=15, check=False)
        sha = out.stdout.strip()
        return sha if out.returncode == 0 and sha else "unknown"
    except Exception:
        return "unknown"


def _tool_versions() -> dict[str, Any]:
    vers: dict[str, Any] = {}
    try:
        from layout_canvas.tools import backends

        rep = backends.NGSPICE.probe(timeout=8)
        vers["ngspice"] = {
            "status": rep["status"], "version": rep["version"],
            "binary": rep["binary"]}
    except Exception as exc:
        vers["ngspice"] = {"status": "unavailable", "error": str(exc)}
    try:
        import importlib.metadata as md

        vers["klayout"] = {"version": md.version("klayout")}
    except Exception:
        vers["klayout"] = {"version": None}
    try:
        import klayout.db  # noqa: F401

        vers["klayout"]["pya"] = True
    except ImportError:
        vers["klayout"]["pya"] = False
    return vers


def _input_sha256(task: Task) -> dict[str, str]:
    """SHA-256 of every file the task consumes (starter + inputs)."""
    files: list[Path] = []
    starter = task.dir / "starter"
    inputs = task.dir / "inputs"
    for d in (starter, inputs):
        if d.is_dir():
            files.extend(sorted(p for p in d.rglob("*") if p.is_file()))
    out = {}
    for p in files:
        out[str(p.relative_to(task.dir)).replace("\\", "/")] = hashlib.sha256(
            p.read_bytes()).hexdigest()
    return out


# ---------------------------------------------------------------------------
# Metrics / checks (flat-scalar-v2)
# ---------------------------------------------------------------------------


def _check(cid: str, measured: Any, spec: str, status: str,
           unit: str = "", message: str = "",
           deterministic: bool = False, tol: float | None = None,
           tol_rel: float = 0.0, capability: bool = False,
           evidence_level: str | None = None) -> dict[str, Any]:
    return {
        "id": cid,
        "measured_value": measured,
        "spec": spec,
        "status": status,
        "unit": unit,
        "message": message,
        "deterministic": deterministic,
        "tol": tol,
        "tol_rel": tol_rel,
        # True when this check needs an external capability (simulator,
        # engine) to produce a verdict — its unavailability means the
        # task could not be judged, never a pass.
        "capability": capability,
        # Reliability evidence ladder rung (see bench/README.md):
        # "deterministic" (structural truth) | "solver_verified" (a real
        # simulator produced the number) | "stress_corner" (accelerated
        # V/T run) | "parametric_stress" (corner run that additionally
        # injects BSIM4 delvto/mulu0 device params — a transparent aging
        # proxy, still solver evidence, never an aging model). None on
        # checks that produced no evidence rung (load/run errors).
        "evidence_level": evidence_level,
    }


def _metric(mid: str, value: Any, unit: str = "",
            note: str = "") -> dict[str, Any]:
    return {"id": mid, "value": value, "unit": unit, "note": note}


def _bound_check(b: Bound, value: Any) -> dict[str, Any]:
    cid = f"bound.{b.metric}"
    spec_txt = (f"== {b.equals}" if b.equals is not None else
                f"[{b.min if b.min is not None else '-inf'},"
                f" {b.max if b.max is not None else '+inf'}]")
    if b.unit:
        spec_txt += f" {b.unit}"
    # The evidence rung follows the metric's nature: an exact-equality
    # gate is deterministic evidence; a tolerance-gated bound on a
    # sim-derived metric is solver-verified.
    level = "deterministic" if b.deterministic else "solver_verified"
    if value is None:
        return _check(cid, None, spec_txt, UNAVAILABLE, b.unit,
                      f"metric {b.metric!r} was not produced",
                      deterministic=b.deterministic, capability=True,
                      evidence_level=level)
    if b.equals is not None:
        ok = value == b.equals
        return _check(cid, value, spec_txt, PASS if ok else FAIL,
                      b.unit, "" if ok else f"{value!r} != {b.equals!r}",
                      deterministic=b.deterministic, evidence_level=level)
    lo_ok = b.min is None or (isinstance(value, (int, float))
                              and value >= b.min)
    hi_ok = b.max is None or (isinstance(value, (int, float))
                              and value <= b.max)
    ok = lo_ok and hi_ok
    msg = ""
    if not ok:
        which = "< min" if not lo_ok else "> max"
        msg = f"{value!r} {which} bound"
    return _check(cid, value, spec_txt, PASS if ok else FAIL, b.unit,
                  msg, deterministic=b.deterministic, evidence_level=level)


def _hidden_checks(metrics: dict[str, Any],
                   compile_ok: bool) -> list[dict[str, Any]]:
    """The unstated eval surface: every compiled task must be DRC-clean
    and LVS-matched, whether or not instruction.md says so."""
    if not compile_ok:
        return []
    out = []
    tv = metrics.get("drc.total_violations")
    drc_status = metrics.get("drc.status")
    if tv is None:
        out.append(_check("hidden.drc_clean", None, "== 0 violations",
                          UNAVAILABLE,
                          message=f"drc engine {drc_status or 'unavailable'}",
                          deterministic=True, capability=True,
                          evidence_level="deterministic"))
    else:
        out.append(_check("hidden.drc_clean", tv, "== 0 violations",
                          PASS if tv == 0 else FAIL,
                          deterministic=True, evidence_level="deterministic"))
    match = metrics.get("lvs.match")
    lvs_status = metrics.get("lvs.status")
    if match is None:
        out.append(_check("hidden.lvs_match", None, "== true",
                          UNAVAILABLE,
                          message=f"lvs engine {lvs_status or 'unavailable'}",
                          deterministic=True, capability=True,
                          evidence_level="deterministic"))
    else:
        out.append(_check("hidden.lvs_match", match, "== true",
                          PASS if match is True else FAIL,
                          deterministic=True, evidence_level="deterministic"))
    return out


def _status_of(checks: list[dict[str, Any]]) -> str:
    """Roll check verdicts up to a task status.

    A missing capability (simulator/engine absent — the check could not
    produce a verdict) dominates real failures: the task was not
    *evaluable*, so it reports ``unavailable`` — recorded in coverage,
    never counted as a pass. The failed checks stay in the record.
    """
    statuses = {c["status"] for c in checks}
    if ERROR in statuses:
        return ERROR
    if any(c["status"] == UNAVAILABLE and c.get("capability")
           for c in checks):
        return UNAVAILABLE
    if FAIL in statuses:
        return FAIL
    if UNAVAILABLE in statuses:
        return UNAVAILABLE
    return PASS


def _collect_verify_metrics(design) -> dict[str, Any]:
    """compile → ppa → extract + DRC + LVS; every field honest."""
    from layout_canvas.compiler.compile import compile_design
    from layout_canvas.compiler.ppa import extract_ppa
    from layout_canvas.tools.verify import verify_design

    metrics: dict[str, Any] = {}
    comp = compile_design(design)
    ppa = extract_ppa(comp, design)
    metrics["compile.ok"] = True
    for k in ("area_um2", "width_um", "height_um", "hpwl_um",
              "instance_count", "net_count", "port_count"):
        metrics[f"ppa.{k}"] = ppa.get(k)

    res = verify_design(design)
    ext = res.get("extract", {})
    metrics["extract.status"] = ext.get("status")
    metrics["extract.devices"] = ext.get("devices")
    metrics["extract.nets"] = ext.get("nets")
    metrics["drc.status"] = res["drc"].get("status")
    metrics["drc.total_violations"] = res["drc"].get("total_violations")
    metrics["lvs.status"] = res["lvs"].get("status")
    metrics["lvs.match"] = res["lvs"].get("match")
    metrics["verify.passed"] = res.get("passed")
    return metrics


def _spec_checks(design, task: Task,
                 metrics: dict[str, Any]) -> list[dict[str, Any]]:
    """Run every declared testbench; one check per spec entry."""
    from layout_canvas.tools.testbench import run_all

    checks: list[dict[str, Any]] = []
    margins: list[tuple[float, str]] = []
    n_pass = n_total = n_unavail = 0
    try:
        runs = run_all(design)
    except Exception as exc:  # a bad bench declaration is an honest error
        metrics["specs.error"] = str(exc)
        return [_check("specs.run", None, "all testbenches run",
                       ERROR, message=str(exc))]

    # Which corners carry device-level stress params (dvth_*/mu0_scale)?
    # Specs routed onto those runs earn the "parametric_stress" rung —
    # solver evidence + explicit device-parameter injection, still not an
    # aging model.
    parametric = {
        t.name for t in getattr(design, "testbenches", [])
        if t.stress and any(k in t.stress
                            for k in ("dvth_nmos", "dvth_pmos", "mu0_scale"))
    }
    worst_note = ""
    for run in runs:
        tb = run.get("testbench", "?")
        for entry in run.get("specs", []):
            n_total += 1
            cid = f"spec.{tb}.{entry['name']}"
            lo, hi = entry.get("min"), entry.get("max")
            spec_txt = (f"[{lo if lo is not None else '-inf'},"
                        f" {hi if hi is not None else '+inf'}] "
                        f"{entry.get('unit', '')}").strip()
            status = entry.get("status", UNAVAILABLE)
            if status == PASS:
                n_pass += 1
            elif status == UNAVAILABLE:
                n_unavail += 1
            # Evidence rung: stress/drift entries come from the
            # accelerated-corner run — "parametric_stress" when the corner
            # also injects BSIM4 device params, else "stress_corner" —
            # solver-verified stress proxies, never aging-model claims.
            level = ("solver_verified"
                     if entry.get("on") not in ("stress", "drift")
                     else "parametric_stress" if tb in parametric
                     else "stress_corner")
            msg = entry.get("reason", "")
            if entry.get("on") == "drift" and \
                    entry.get("nominal_value") is not None:
                msg = (f"nominal={entry['nominal_value']!r} "
                       f"stress={entry['stress_value']!r}"
                       + (f"; {msg}" if msg else ""))
            checks.append(_check(
                cid, entry.get("value"), spec_txt, status,
                entry.get("unit", ""), msg,
                tol=task.sim_tol_abs or None, tol_rel=task.sim_tol_rel,
                capability=True, evidence_level=level))
            # worst-case annotation: which spec is closest to its bound
            val, meas = entry.get("value"), entry.get("measure")
            if isinstance(val, (int, float)):
                if lo is not None and hi is not None and hi > lo:
                    frac = (min(val - lo, hi - val) / ((hi - lo) / 2.0))
                elif lo is not None:
                    frac = (val - lo) / abs(lo) if lo else val - lo
                elif hi is not None:
                    frac = (hi - val) / abs(hi) if hi else hi - val
                else:
                    frac = 0.0
                margins.append((frac, f"{cid} ({meas})"))
    if margins:
        margins.sort(key=lambda t: t[0])
        metrics["specs.worst_margin_frac"] = round(margins[0][0], 4)
        worst_note = margins[0][1]
    metrics["specs.worst_case"] = worst_note or None
    metrics["specs.passed"] = n_pass
    metrics["specs.total"] = n_total
    metrics["specs.unavailable"] = n_unavail
    return checks


# ---------------------------------------------------------------------------
# Task runners
# ---------------------------------------------------------------------------


def _load_design(task: Task):
    from layout_canvas.ir.model import Design

    file = task.dir / task.deliverable
    if not file.is_file():
        raise TaskError(
            f"{task.dir}: deliverable {task.deliverable!r} not found")
    return Design.from_json(file.read_text(encoding="utf-8"))


def _run_verify_like(task: Task) -> tuple[dict[str, Any],
                                        list[dict[str, Any]]]:
    """verify | spec | import: compile → verify → (optional) spec evals."""
    metrics: dict[str, Any] = {}
    checks: list[dict[str, Any]] = []
    alias = None
    try:
        if task.type == "import":
            from layout_canvas.blocks import base
            from layout_canvas.blocks.gds_cell import register_gds_cell

            cfg = task.import_cfg
            alias = cfg["alias"]
            gds = task.dir / cfg["gds"]
            if not gds.is_file():
                raise TaskError(f"{task.dir}: import gds {cfg['gds']!r} "
                                "not found")
            spice_text = None
            if cfg.get("spice"):
                sp = task.dir / cfg["spice"]
                if sp.is_file():
                    spice_text = sp.read_text(encoding="utf-8")
            block = register_gds_cell(
                alias, gds, cfg.get("pdk") or task.pdk,
                cell_name=cfg.get("cell_name"), spice_text=spice_text)
            metrics["import.alias"] = alias
            metrics["import.ports"] = len(block.spec.ports)
            metrics["import.has_netlist"] = block.netlist is not None

        design = _load_design(task)
        metrics.update(_collect_verify_metrics(design))
        if task.type == "spec":
            checks.extend(_spec_checks(design, task, metrics))
    finally:
        if alias is not None:
            from layout_canvas.blocks import base
            base._REGISTRY.pop(alias, None)
    return metrics, checks


def _run_optimize(task: Task) -> tuple[dict[str, Any],
                                     list[dict[str, Any]]]:
    """optimize: bounded coordinate descent scored by spec results."""
    from layout_canvas.compiler.compile import compile_design
    from layout_canvas.compiler.ppa import extract_ppa
    from layout_canvas.engine import DesignSession, optimize_session

    metrics: dict[str, Any] = {}
    checks: list[dict[str, Any]] = []
    design = _load_design(task)
    session = DesignSession(design)
    env = optimize_session(
        session, objective="specs",
        testbench=task.run_cfg.get("testbench"),
        max_iterations=int(task.run_cfg["max_iterations"]))
    metrics["optimize.status"] = env.status
    if not env.ok:
        diag = "; ".join(d.message for d in env.diagnostics)
        return metrics, [_check("optimize.run", None, "optimize completes",
                                ERROR, message=diag)]
    data = env.data or {}
    metrics["optimize.iterations"] = data.get("iterations")
    metrics["optimize.evals"] = data.get("evals")
    metrics["optimize.all_passed"] = data.get("all_passed")
    metrics["optimize.spec_status"] = data.get("spec_status")
    metrics["optimize.initial_specs"] = (data.get("initial_score")
                                         or [None])[0]
    metrics["optimize.final_specs"] = (data.get("final_score") or [None])[0]

    final = session.design
    try:
        ppa = extract_ppa(compile_design(final), final)
        metrics["ppa.area_um2"] = ppa.get("area_um2")
        metrics["ppa.hpwl_um"] = ppa.get("hpwl_um")
    except Exception as exc:
        metrics["ppa.error"] = str(exc)

    # Final physical verification of the tuned layout (hidden surface).
    try:
        metrics.update(_collect_verify_metrics(final))
    except Exception as exc:
        metrics["verify.error"] = str(exc)

    # Score surface: specs passed (of total), iteration count, final area.
    metrics["score.specs_passed"] = metrics["optimize.final_specs"]
    metrics["score.iterations"] = metrics["optimize.iterations"]
    metrics["score.final_area_um2"] = metrics.get("ppa.area_um2")
    # Re-score the tuned design's benches so individual spec checks exist.
    checks.extend(_spec_checks(final, task, metrics))
    return metrics, checks


def _load_variants(task: Task) -> list[Any]:
    """The task's variant list — the exact ``{name, edits}`` objects
    ``eval_candidates`` consumes (default ``starter/variants.json``,
    overridable via ``[candidates] variants``)."""
    rel = task.cand_cfg.get("variants", "starter/variants.json")
    file = task.dir / rel
    if not file.is_file():
        raise TaskError(f"{task.dir}: variants file {rel!r} not found")
    try:
        variants = json.loads(file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise TaskError(f"{file}: invalid JSON — {exc}") from None
    _req(isinstance(variants, list),
         f"{file}: top-level must be a list of {{name, edits}} objects")
    return variants


def _run_candidates(task: Task) -> tuple[dict[str, Any],
                                     list[dict[str, Any]]]:
    """candidates: K variants through the transact boundary, each
    compiled + verified + simulated on the schematic benches, compared
    side by side.

    Fail-closed per candidate: a rejected edit list is a real ``failed``
    verdict on that one candidate — ``[candidates.expect]`` declares the
    status each variant is *designed* to report (default ``passed``), so
    a designed-fail variant is a gate, not noise.  Every candidate that
    reaches verification faces the same hidden DRC/LVS contract as a
    compiled task; spec pass counts are capability checks (a live
    simulator produced them — ``unavailable`` is reported, never faked).
    """
    from layout_canvas.tools.candidates import eval_candidates

    metrics: dict[str, Any] = {}
    checks: list[dict[str, Any]] = []
    design = _load_design(task)
    variants = _load_variants(task)
    expects = task.cand_cfg.get("expect", {})
    expect_specs = task.cand_cfg.get("expect_specs", {})
    # Same sim policy as 'spec' tasks: always attempt the benches; a
    # missing simulator demotes candidates to 'unavailable', which the
    # capability-flagged checks report honestly.
    run_sims = bool(task.run_cfg.get("run_sims", True))
    rep = eval_candidates(design, variants, run_sims=run_sims)

    cands = rep.get("candidates") or []
    ranking = rep.get("ranking") or []
    metrics["candidates.report_status"] = rep.get("status")
    metrics["candidates.count"] = len(cands)
    metrics["candidates.ranking"] = [r.get("name") for r in ranking]
    metrics["candidates.ranking_top"] = (
        ranking[0].get("name") if ranking else None)
    tally = {"passed": 0, "failed": 0, "unavailable": 0}
    for cand in cands:
        if cand.get("status") in tally:
            tally[cand["status"]] += 1
    for key, val in tally.items():
        metrics[f"candidates.{key}"] = val

    # The report rollup is the headline gate: 'ok' means every variant
    # was evaluated on its own merits (one broken variant never poisons
    # the sweep); 'fail'/'unavailable' mean the sweep itself could not
    # produce per-candidate verdicts.
    rep_status = rep.get("status")
    checks.append(_check(
        "candidates.report", rep_status, "== ok",
        PASS if rep_status == "ok" else (
            UNAVAILABLE if rep_status == "unavailable" else FAIL),
        message=("" if rep_status == "ok"
                 else str(rep.get("reason") or f"report {rep_status!r}")),
        deterministic=True, capability=rep_status == "unavailable",
        evidence_level="deterministic"))

    for index, cand in enumerate(cands):
        name = str(cand.get("name") or f"variant_{index}")
        status = cand.get("status")
        expect = str(expects.get(name, "passed"))
        metrics[f"candidate.{name}.status"] = status
        metrics[f"candidate.{name}.stage"] = cand.get("stage")
        metrics[f"candidate.{name}.edits_valid"] = cand.get("edits_valid")
        metrics[f"candidate.{name}.area_um2"] = cand.get("area_um2")
        ver = cand.get("verify") or {}
        metrics[f"candidate.{name}.drc_violations"] = \
            ver.get("drc_violations")
        metrics[f"candidate.{name}.lvs_match"] = ver.get("lvs_match")
        specs = cand.get("specs")
        metrics[f"candidate.{name}.specs_passed"] = \
            (specs or {}).get("passed")
        metrics[f"candidate.{name}.specs_total"] = \
            (specs or {}).get("total")

        # With run_sims the verdict of an edit-valid candidate rides the
        # spec stage too — sim presence is a capability, so the check is
        # capability-gated (unavailable -> 'lost evidence' on baseline
        # diff, not a silent deterministic mismatch).  A candidate
        # rejected at the transact boundary never reaches the spec
        # stage: its 'failed' verdict is pure deterministic evidence.
        sim_dependent = run_sims and bool(cand.get("edits_valid"))
        if status == "unavailable":
            checks.append(_check(
                f"candidate.{name}.status", status, f"== {expect}",
                UNAVAILABLE,
                message=cand.get("reason") or "candidate not evaluable",
                capability=True, evidence_level="solver_verified"))
        else:
            checks.append(_check(
                f"candidate.{name}.status", status, f"== {expect}",
                PASS if status == expect else FAIL,
                message=("" if status == expect
                         else (cand.get("reason")
                               or f"{status!r} != expected {expect!r}")),
                deterministic=not sim_dependent,
                capability=sim_dependent,
                evidence_level=("solver_verified" if sim_dependent
                                else "deterministic")))

        # Hidden surface, per candidate that reached verification — the
        # same drc-clean / lvs-match contract every compiled task faces.
        if cand.get("verify") is not None:
            dv = ver.get("drc_violations")
            if dv is None:
                checks.append(_check(
                    f"hidden.drc_clean.{name}", None, "== 0 violations",
                    UNAVAILABLE,
                    message="drc engine "
                            f"{ver.get('drc_status') or 'unavailable'}",
                    deterministic=True, capability=True,
                    evidence_level="deterministic"))
            else:
                checks.append(_check(
                    f"hidden.drc_clean.{name}", dv, "== 0 violations",
                    PASS if dv == 0 else FAIL, deterministic=True,
                    evidence_level="deterministic"))
            lm = ver.get("lvs_match")
            if lm is None:
                checks.append(_check(
                    f"hidden.lvs_match.{name}", None, "== true",
                    UNAVAILABLE,
                    message="lvs engine "
                            f"{ver.get('lvs_status') or 'unavailable'}",
                    deterministic=True, capability=True,
                    evidence_level="deterministic"))
            else:
                checks.append(_check(
                    f"hidden.lvs_match.{name}", lm, "== true",
                    PASS if lm is True else FAIL, deterministic=True,
                    evidence_level="deterministic"))

        # Spec pass count — a capability check: the number comes from a
        # live simulator on the schematic benches (PEX benches are
        # skipped in sweeps).  [candidates.expect_specs] pins the
        # designed count; unlisted names expect every spec to pass.
        if specs is not None:
            passed = specs.get("passed")
            total = specs.get("total")
            exp = expect_specs.get(name, total)
            spec_txt = f"== {exp} of {total} spec passes"
            if passed is None:
                checks.append(_check(
                    f"candidate.{name}.specs_passed", None, spec_txt,
                    UNAVAILABLE,
                    message=(specs.get("reason")
                             or "spec evaluation produced no verdict"),
                    tol=task.sim_tol_abs or None,
                    tol_rel=task.sim_tol_rel, capability=True,
                    evidence_level="solver_verified"))
            else:
                ok = passed == exp
                checks.append(_check(
                    f"candidate.{name}.specs_passed", passed, spec_txt,
                    PASS if ok else FAIL,
                    message=("" if ok else
                             f"{passed} spec passes != designed {exp}"),
                    tol=task.sim_tol_abs or None,
                    tol_rel=task.sim_tol_rel, capability=True,
                    evidence_level="solver_verified"))
    return metrics, checks


def run_task(task: Task) -> dict[str, Any]:
    """Execute one task; return its scorecard fragment. Never raises for
    expected failures — they become status fail/unavailable/error."""
    metrics: dict[str, Any] = {}
    checks: list[dict[str, Any]] = []
    failure = None
    with _task_env(task):
        try:
            if task.type == "optimize":
                metrics, checks = _run_optimize(task)
            elif task.type == "candidates":
                metrics, checks = _run_candidates(task)
            else:
                metrics, checks = _run_verify_like(task)
        except TaskError as exc:
            failure = str(exc)
            checks.append(_check("task.load", None, "task loads",
                                 ERROR, message=str(exc)))
        except Exception as exc:  # honest error, not a crash
            failure = f"{type(exc).__name__}: {exc}"
            checks.append(_check("task.run", None, "task completes",
                                 ERROR, message=failure))

    compile_ok = bool(metrics.get("compile.ok"))
    checks.extend(_hidden_checks(metrics, compile_ok))
    if failure is None or compile_ok:
        for bound in task.bounds:
            checks.append(_bound_check(bound, metrics.get(bound.metric)))

    status = _status_of(checks)
    if status == FAIL and failure is None:
        bad = next(c for c in checks if c["status"] == FAIL)
        failure = f"{bad['id']}: {bad.get('message') or 'check failed'}"
    metric_list = [
        _metric(k, v, note=(k == "specs.worst_case"
                          and "worst-case spec (nearest bound)" or ""))
        for k, v in sorted(metrics.items())
    ]
    return {
        "task": task.id,
        "type": task.type,
        "status": status,
        "passed": status == PASS,
        "failure": failure,
        "checks": checks,
        "metrics": metric_list,
        "input_sha256": _input_sha256(task),
    }


# ---------------------------------------------------------------------------
# Scorecard / baseline / freeze
# ---------------------------------------------------------------------------


def _scorecard(tasks: list[Task], results: list[dict[str, Any]],
               manifest: dict[str, Any]) -> dict[str, Any]:
    runnable = sum(1 for r in results if r["status"] in (PASS, FAIL))
    card = {
        "metric_schema": METRIC_SCHEMA,
        "release_id": manifest["release_id"],
        "release_state": manifest["state"],
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "tasks": {r["task"]: r for r in results},
        "coverage": {"runnable": runnable, "total": len(results)},
        "provenance": {
            "git_sha": _git_sha(),
            "platform": platform.platform(),
            "python": platform.python_version(),
            "tools": _tool_versions(),
            "input_sha256": {r["task"]: r["input_sha256"]
                             for r in results},
        },
    }
    # Evidence is complete only when every check produced a real verdict —
    # an unavailable tool/spec leaves the record explicitly incomplete.
    card["evidence_complete"] = all(
        c["status"] in (PASS, FAIL)
        for r in results for c in r["checks"]) and bool(results)
    return card


def _baseline_diffs(task: Task, live: dict[str, Any]) -> tuple[
        list[str], list[str]]:
    """Compare a live task result to its frozen reference.

    Returns (regressions, notes). Deterministic checks must match exactly;
    sim-valued checks compare inside their recorded tolerance; an
    unavailable -> measured transition is an evidence *gain* reported as a
    note, while measured -> unavailable is a regression.
    """
    ref_file = task.dir / "results" / "reference.json"
    if not ref_file.is_file():
        return [], [f"{task.id}: no frozen reference (unfrozen task)"]
    try:
        ref = json.loads(ref_file.read_text(encoding="utf-8"))
    except Exception as exc:
        return [f"{task.id}: unreadable reference.json — {exc}"], []
    ref_checks = {c["id"]: c for c in ref.get("checks", [])}
    live_checks = {c["id"]: c for c in live.get("checks", [])}
    regress: list[str] = []
    notes: list[str] = []
    for cid, rc in ref_checks.items():
        lc = live_checks.get(cid)
        if lc is None:
            regress.append(f"{task.id}.{cid}: missing in live run")
            continue
        rstat, lstat = rc["status"], lc["status"]
        if rc.get("deterministic"):
            if rstat != lstat or rc["measured_value"] != lc["measured_value"]:
                regress.append(
                    f"{task.id}.{cid}: deterministic mismatch "
                    f"({rstat}:{rc['measured_value']!r} -> "
                    f"{lstat}:{lc['measured_value']!r})")
            continue
        rv, lv = rc["measured_value"], lc["measured_value"]
        if rstat == UNAVAILABLE:
            if lstat == UNAVAILABLE:
                continue
            notes.append(f"{task.id}.{cid}: evidence gained "
                         f"(was unavailable, now {lstat}={lv!r})")
            continue
        if lstat == UNAVAILABLE:
            regress.append(f"{task.id}.{cid}: lost evidence "
                           f"(was {rstat}={rv!r}, now unavailable)")
            continue
        if rstat != lstat:
            regress.append(f"{task.id}.{cid}: status flipped "
                           f"{rstat} -> {lstat}")
            continue
        if isinstance(rv, (int, float)) and isinstance(lv, (int, float)):
            tol = max(rc.get("tol") or 0.0,
                      (rc.get("tol_rel") or 0.0) * abs(rv))
            if abs(lv - rv) > tol:
                regress.append(
                    f"{task.id}.{cid}: value drift {rv!r} -> {lv!r} "
                    f"(tol {tol:g})")
        elif rv != lv:
            regress.append(
                f"{task.id}.{cid}: value changed {rv!r} -> {lv!r}")
    return regress, notes


def _freeze(tasks: list[Task], results: list[dict[str, Any]],
            manifest: dict[str, Any]) -> list[Path]:
    """Write ``tasks/<id>/results/reference.json`` — the golden baseline.

    Freezing records the truth of this environment (including honest
    ``unavailable`` verdicts); it is a gate for future regression diffs,
    not a claim that every capability ran.
    """
    written: list[Path] = []
    by_id = {t.id: t for t in tasks}
    for r in results:
        task = by_id[r["task"]]
        out = task.dir / "results" / "reference.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "task": r["task"],
            "type": r["type"],
            "release_id": manifest["release_id"],
            "frozen_at": r.get("generated_at") or time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "status": r["status"],
            "checks": r["checks"],
            "metrics": r["metrics"],
            "provenance": {
                "git_sha": _git_sha(),
                "platform": platform.platform(),
                "python": platform.python_version(),
                "tools": _tool_versions(),
                "input_sha256": r["input_sha256"],
            },
        }
        out.write_text(json.dumps(payload, indent=2) + "\n",
                       encoding="utf-8")
        written.append(out)
    return written


def _print_result(r: dict[str, Any]) -> None:
    marks = {PASS: "PASS", FAIL: "FAIL",
             UNAVAILABLE: "UNAVAIL", ERROR: "ERROR"}
    print(f"[{marks.get(r['status'], '?'):7s}] {r['task']} "
          f"({r['type']}) — {sum(1 for c in r['checks'] if c['status'] == PASS)}"
          f"/{len(r['checks'])} checks pass")
    for c in r["checks"]:
        if c["status"] == PASS:
            continue
        detail = f" — {c['message']}" if c.get("message") else ""
        mv = c["measured_value"]
        print(f"    {c['status']:>10s} {c['id']}"
              f"{'' if mv is None else f' = {mv!r}'}{detail}")
    interesting = ("ppa.area_um2", "extract.devices", "specs.passed",
                   "specs.total", "specs.worst_case",
                   "optimize.iterations", "optimize.final_specs",
                   "score.final_area_um2", "candidates.passed",
                   "candidates.ranking_top")
    ms = {m["id"]: m["value"] for m in r["metrics"]}
    shown = [f"{k}={ms[k]!r}" for k in interesting if ms.get(k) is not None]
    if shown:
        print(f"    metrics: {', '.join(shown)}")


def main(argv: list[str] | None = None, bench_dir: Path | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="layout-canvas bench",
        description="Run the frozen layout-compiler benchmark suite")
    parser.add_argument("--task", help="run a single task id")
    parser.add_argument("--check-baseline", action="store_true",
                        help="gate against tasks/*/results/reference.json; "
                             "exit 1 on any regression")
    parser.add_argument("--freeze", action="store_true",
                        help="rewrite tasks/*/results/reference.json from "
                             "this run (baseline freeze)")
    parser.add_argument("--out", help="scorecard output directory "
                        "(default bench/runs/<timestamp>/)")
    parser.add_argument("--bench-dir", help="bench root "
                        "(default: directory containing this script)")
    args = parser.parse_args(argv)

    root = Path(args.bench_dir).resolve() if args.bench_dir else (
        bench_dir or BENCH_DIR)
    _register_blocks()
    try:
        manifest = load_manifest(root)
        tasks = discover_tasks(root, args.task)
    except TaskError as exc:
        print(f"bench: {exc}", file=sys.stderr)
        return 2
    if manifest["state"] != "frozen":
        print(f"bench: release {manifest['release_id']} is "
              f"state={manifest['state']!r} — scores are not "
              "release-meaningful", file=sys.stderr)

    results = [run_task(t) for t in tasks]
    card = _scorecard(tasks, results, manifest)
    now = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    for r in results:
        r["generated_at"] = card["generated_at"]

    out_dir = Path(args.out) if args.out else root / "runs" / now
    out_dir.mkdir(parents=True, exist_ok=True)
    card_path = out_dir / "scorecard.json"
    card_path.write_text(json.dumps(card, indent=2) + "\n",
                         encoding="utf-8")

    print(f"layout-canvas bench — release {card['release_id']} "
          f"({card['release_state']}), schema {METRIC_SCHEMA}")
    for r in results:
        _print_result(r)
    print(f"coverage: {card['coverage']['runnable']} runnable "
          f"of {card['coverage']['total']} tasks; "
          f"evidence_complete={card['evidence_complete']}")
    print(f"scorecard: {card_path}")

    exit_code = 0
    if any(r["status"] in (FAIL, ERROR) for r in results):
        exit_code = 1

    if args.freeze:
        written = _freeze(tasks, results, manifest)
        for w in written:
            print(f"froze {w.relative_to(root)}")

    if args.check_baseline:
        regressions: list[str] = []
        notes: list[str] = []
        by_id = {r["task"]: r for r in results}
        for t in tasks:
            rg, nt = _baseline_diffs(t, by_id[t.id])
            regressions.extend(rg)
            notes.extend(nt)
        for n in notes:
            print(f"baseline note: {n}")
        if regressions:
            print("baseline REGRESSIONS:")
            for line in regressions:
                print(f"  {line}")
            exit_code = 1
        else:
            print("baseline check: no regressions")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
