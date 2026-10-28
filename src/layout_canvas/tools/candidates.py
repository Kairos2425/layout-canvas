"""Multi-candidate generation + side-by-side comparison.

Given a base design and a list of ``variants`` — each ``{name, edits}`` —
every candidate's edits are applied to a FRESH copy of the design through
the same ``DesignSession.transact`` validation boundary the session and
optimizer use (a rejected edit list lands that candidate in ``failed``
with the real diagnostics; it never reaches the compiler).  Each valid
candidate is then compiled, PPA-measured, physically verified
(extract/DRC/LVS + declared-constraint quality) and — with ``run_sims`` —
simulated on the design's ``source="schematic"`` testbenches.
``source="extracted"`` (PEX) benches are not run during a sweep — one
extraction per candidate is already paid by verify, a second PEX netlist
per bench is too expensive; they are listed under ``specs.skipped``.

Fail-closed by construction: every candidate reports its own honest
status and one broken variant never poisons the run — the remaining
candidates are still fully evaluated.  ``ranking`` is a *mechanical*
sort over measured numbers (verify passed → spec pass count → smaller
area), not a quality judgment.
"""

from __future__ import annotations

from typing import Any

from layout_canvas.compiler.compile import compile_design
from layout_canvas.compiler.ppa import extract_ppa
from layout_canvas.engine.session import DesignSession
from layout_canvas.ir.model import Design, Placement
from layout_canvas.tools.testbench import _spec_status, run_testbench
from layout_canvas.tools.verify import verify_design

# Stage statuses that mean "the check could not run" rather than "ran and
# produced a bad verdict" — a missing engine/recipe (unavailable) or a
# crashed check (error) keeps the candidate unverifiable, not failed.
_NO_VERDICT = ("unavailable", "error")

_POLICY_NOTES = (
    "status policy: 'fail' only when every candidate failed; 'unavailable' "
    "only when every candidate could not be evaluated at all; otherwise "
    "'ok' and each candidate's own status stands",
    "candidate status reflects edit validity + compile + physical "
    "verification (extract/DRC/LVS/quality); spec verdicts are reported "
    "per candidate under 'specs' and steer the ranking, not the status",
    "ranking is a mechanical sort (verify passed → spec pass count → "
    "smaller area) — a deterministic ordering of measured numbers, not a "
    "quality judgment",
)


def _fmt_val(value: Any) -> str:
    """Compact scalar for diff lines (params/placement fields)."""
    if value is None:
        return "(unset)"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _diff_summary(design: Design, edits: list[Any]) -> list[str]:
    """Human-readable per-edit description resolved against ``design``.

    ``set_params``/``set_placement`` ops read their old values off the
    base design so every line reads ``instance.field: old → new``; other
    ops describe the add/remove/change they request.  Works whether or not
    the edits later validate — a rejected candidate still shows what it
    attempted.
    """
    insts = {i.id: i for i in design.instances}
    lines: list[str] = []
    for edit in edits:
        if not isinstance(edit, dict):
            lines.append(f"malformed edit (not an object): {edit!r}")
            continue
        op = edit.get("op")
        if op == "set_params":
            iid = edit.get("instance")
            inst = insts.get(iid)
            params = edit.get("params")
            if not isinstance(params, dict):
                lines.append(f"{iid}: set_params without a 'params' object")
                continue
            for key, val in params.items():
                old = inst.params.get(key) if inst is not None else None
                lines.append(
                    f"{iid}.{key}: {_fmt_val(old)} → {_fmt_val(val)}")
        elif op == "set_placement":
            iid = edit.get("instance")
            inst = insts.get(iid)
            fields = {k: v for k, v in edit.items()
                      if k in Placement.model_fields}
            if not fields:
                lines.append(f"{iid}: set_placement with no placement fields")
            for key, val in fields.items():
                old = getattr(inst.placement, key, None) if inst is not None else None
                lines.append(
                    f"{iid}.placement.{key}: {_fmt_val(old)} → {_fmt_val(val)}")
        elif op == "add_instance":
            payload = edit.get("instance") or {}
            lines.append(
                f"add instance {payload.get('id')!r} ({payload.get('block')})")
        elif op == "remove_instance":
            lines.append(f"remove instance {edit.get('instance')!r}")
        elif op == "add_net":
            payload = edit.get("net") or {}
            lines.append(
                f"add net {payload.get('name')!r} "
                f"({len(payload.get('pins') or [])} pins)")
        elif op == "remove_net":
            lines.append(f"remove net {edit.get('net')!r}")
        elif op == "set_net_pins":
            name = edit.get("net")
            old_net = next((n for n in design.nets if n.name == name), None)
            old = f"{len(old_net.pins)} pins" if old_net is not None else "(unknown net)"
            lines.append(f"net {name!r}.pins: {old} → {edit.get('pins')}")
        elif op == "add_port":
            payload = edit.get("port") or {}
            lines.append(
                f"add port {payload.get('name')!r} ({payload.get('pin')})")
        elif op == "remove_port":
            lines.append(f"remove port {edit.get('port')!r}")
        elif op == "add_constraint":
            payload = edit.get("constraint") or {}
            lines.append(
                f"add constraint {payload.get('type')} "
                f"on {payload.get('instances')}")
        elif op == "remove_constraint":
            idx = edit.get("index")
            old = (design.constraints[idx]
                   if isinstance(idx, int)
                   and not isinstance(idx, bool)
                   and 0 <= idx < len(design.constraints) else None)
            detail = (f" ({old.type} on {old.instances})"
                      if old is not None else "")
            lines.append(f"remove constraint[{idx}]{detail}")
        elif op == "set_meta":
            key = edit.get("key")
            lines.append(
                f"meta.{key}: {_fmt_val(design.meta.get(key))} → "
                f"{_fmt_val(edit.get('value'))}")
        elif op == "set_testbenches":
            payload = edit.get("testbenches")
            n = len(payload) if isinstance(payload, list) else "?"
            lines.append(
                f"testbenches: {len(design.testbenches)} → {n} bench(es)")
        else:
            lines.append(f"{op or 'unknown-op'}: {edit}")
    return lines or ["(no edits — baseline copy)"]


def _verify_summary(ver: dict[str, Any]) -> dict[str, Any]:
    """Trim a ``verify_design`` payload to the comparison fields."""
    ext, drc, lvs, qual = (ver["extract"], ver["drc"], ver["lvs"],
                           ver["quality"])
    return {
        "passed": ver["passed"],
        "extract_status": ext["status"],
        "extract_errors": list(ext["errors"] or []),
        "devices": ext["devices"],
        "nets": ext["nets"],
        "drc_status": drc.get("status"),
        "drc_violations": drc.get("total_violations"),
        "lvs_status": lvs.get("status"),
        "lvs_match": lvs.get("match"),
        "lvs_unmatched_nets": list(lvs.get("unmatched_nets") or []),
        "lvs_errors": list(lvs.get("errors") or []),
        "quality_status": qual.get("status"),
    }


def _verify_status(ver: dict[str, Any]) -> str:
    """Roll the verify stages up to ran-clean / ran-dirty / couldn't-run."""
    ext, drc, lvs = ver["extract"], ver["drc"], ver["lvs"]
    if (ext["status"] in _NO_VERDICT or drc.get("status") in _NO_VERDICT
            or lvs.get("status") in _NO_VERDICT):
        return "unavailable"
    return "passed" if ver["passed"] else "failed"


def _verify_unavailable_reason(ver: dict[str, Any]) -> str:
    """Name exactly which verification stage could not produce a verdict."""
    ext, drc, lvs = ver["extract"], ver["drc"], ver["lvs"]
    parts: list[str] = []
    if ext["status"] in _NO_VERDICT:
        parts.append(
            f"extract {ext['status']}: "
            + ("; ".join(str(e) for e in ext["errors"]) or "no detail"))
    if drc.get("status") in _NO_VERDICT:
        detail = "; ".join(str(e) for e in (drc.get("errors") or []))
        parts.append(f"drc {drc['status']}: {detail or 'no detail'}")
    if lvs.get("status") in _NO_VERDICT:
        detail = "; ".join(str(e) for e in (lvs.get("errors") or []))
        parts.append(f"lvs {lvs['status']}: {detail or 'no detail'}")
    return "verification unavailable — " + "; ".join(parts)


def _verify_fail_reason(ver: dict[str, Any]) -> str:
    """Summarize the real failing verdicts of a verify run."""
    ext, drc, lvs, qual = (ver["extract"], ver["drc"], ver["lvs"],
                           ver["quality"])
    parts: list[str] = []
    if ext["errors"]:
        parts.append("extract errors: " + "; ".join(str(e) for e in ext["errors"]))
    if (drc.get("total_violations") or 0) > 0:
        parts.append(f"drc: {drc['total_violations']} violations")
    if lvs.get("match") is not True:
        unmatched = lvs.get("unmatched_nets") or []
        parts.append(
            f"lvs: {lvs.get('status')}"
            + (f" (unmatched nets: {', '.join(str(n) for n in unmatched)})"
               if unmatched else ""))
    if qual.get("status") == "fail":
        failed = [c["id"] for c in qual.get("checks", [])
                  if c.get("status") == "fail"]
        parts.append("quality: fail"
                     + (f" ({', '.join(failed)})" if failed else ""))
    return "; ".join(parts) or "verify failed"


def _run_summary(run: dict[str, Any]) -> dict[str, Any]:
    """Trim a ``run_testbench`` payload — spec entries stay, waves go."""
    out = {
        "testbench": run.get("testbench"),
        "sim_status": run.get("status"),
        "spec_status": run.get("spec_status"),
        "errors": [str(e) for e in (run.get("errors") or [])],
        "specs": run.get("specs") or [],
    }
    stress = run.get("stress")
    if isinstance(stress, dict):
        out["stress"] = {
            "status": stress.get("status"),
            "spec_status": stress.get("spec_status"),
            "corner": stress.get("corner"),
            "errors": [str(e) for e in (stress.get("errors") or [])],
        }
    return out


def _eval_specs(
    design: Design,
    *,
    run_sims: bool,
    executable: str | None,
) -> tuple[dict[str, Any], str | None]:
    """Run the schematic testbenches of one candidate.

    Returns ``(specs_section, unavailable_reason)`` — the second value is
    non-None only when spec evaluation was requested and *no* bench could
    produce a run at all (missing simulator, refused netlist, runner
    error), which demotes the candidate to ``unavailable``.
    """
    schematic = [t for t in design.testbenches if t.source == "schematic"]
    extracted = [t for t in design.testbenches if t.source == "extracted"]
    skipped = [
        {"testbench": t.name,
         "reason": "source='extracted' (PEX) — skipped in candidate sweeps; "
                   "run it on the chosen candidate"}
        for t in extracted
    ]
    total = sum(len(t.specs) for t in schematic)
    specs: dict[str, Any] = {
        "status": "no_specs",
        "passed": None,
        "total": total,
        "runs": [],
        "skipped": skipped,
        "reason": "",
    }
    if not schematic:
        if extracted:
            specs["reason"] = (
                "no schematic-source testbenches; only "
                f"{len(extracted)} PEX bench(es) declared (skipped)")
        return specs, None
    if not run_sims:
        specs["status"] = "unavailable"
        specs["reason"] = "run_sims=False — testbenches not executed"
        return specs, None
    runs: list[dict[str, Any]] = []
    for tb in schematic:
        try:
            runs.append(_run_summary(
                run_testbench(design, tb.name, executable=executable)))
        except Exception as exc:  # a crashed bench is its own honest status
            runs.append({
                "testbench": tb.name,
                "sim_status": "error",
                "spec_status": "unavailable",
                "errors": [f"testbench run raised: {exc}"],
                "specs": [],
            })
    specs["runs"] = runs
    entries = [e for r in runs for e in r["specs"]]
    specs["passed"] = sum(1 for e in entries if e.get("status") == "pass")
    specs["total"] = len(entries)
    specs["status"] = _spec_status(entries)
    dead = [r for r in runs if r["sim_status"] in _NO_VERDICT + ("refused",)]
    if len(dead) == len(runs):
        first = dead[0]
        detail = "; ".join(first["errors"]) or first["sim_status"]
        reason = (f"spec evaluation unavailable — every schematic bench "
                  f"failed to run (e.g. {first['testbench']}: "
                  f"{first['sim_status']}; {detail})")
        return specs, reason
    return specs, None


def _eval_one(
    design: Design,
    variant: dict[str, Any],
    index: int,
    *,
    run_sims: bool,
    executable: str | None,
) -> dict[str, Any]:
    """Evaluate one variant end-to-end; never raises on bad input."""
    name = f"variant_{index}"
    cand: dict[str, Any] = {
        "name": name,
        "status": "failed",
        "stage": "edits",
        "reason": "",
        "edits_valid": False,
        "diff_summary": [],
        "diagnostics": [],
        "area_um2": None,
        "bbox": None,
        "width_um": None,
        "height_um": None,
        "hpwl_um": None,
        "instance_count": None,
        "net_count": None,
        "verify": None,
        "specs": None,
    }
    if not isinstance(variant, dict):
        cand["reason"] = (
            f"variant #{index} must be an object {{name, edits}}; "
            f"got {type(variant).__name__}")
        return cand
    cand["name"] = str(variant.get("name") or name)
    edits = variant.get("edits")
    if not isinstance(edits, list):
        cand["reason"] = (
            f"variant {cand['name']!r}: 'edits' must be a list of edit ops")
        return cand
    cand["diff_summary"] = _diff_summary(design, edits)

    # The same transactional validation the session/optimizer use: a dry
    # run applies the ops to a fresh copy and hands back the would-be
    # design; a rejection carries located diagnostics.
    env = DesignSession(design).transact(edits, dry_run=True)
    if not env.ok:
        cand["reason"] = "edit(s) rejected: " + "; ".join(
            d.message for d in env.diagnostics)
        cand["diagnostics"] = [d.to_dict() for d in env.diagnostics]
        return cand
    cand["edits_valid"] = True
    cand_design = Design.model_validate(env.data["design"])

    try:
        comp = compile_design(cand_design)
        ppa = extract_ppa(comp, cand_design)
    except Exception as exc:
        cand.update(stage="compile", reason=f"compile failed: {exc}")
        return cand
    cand.update({
        "stage": "verify",
        "area_um2": ppa["area_um2"],
        "bbox": ppa["bbox"],
        "width_um": ppa["width_um"],
        "height_um": ppa["height_um"],
        "hpwl_um": ppa["hpwl_um"],
        "instance_count": ppa["instance_count"],
        "net_count": ppa["net_count"],
    })

    try:
        ver = verify_design(cand_design)
    except Exception as exc:
        cand.update(reason=f"verify raised: {exc}")
        return cand
    vsummary = _verify_summary(ver)
    vstatus = _verify_status(ver)
    vsummary["status"] = vstatus
    cand["verify"] = vsummary
    if vstatus == "unavailable":
        cand.update(status="unavailable",
                    reason=_verify_unavailable_reason(ver))
    elif vstatus == "failed":
        cand.update(reason=_verify_fail_reason(ver))
    else:
        cand["status"] = "passed"

    specs, spec_dead = _eval_specs(
        cand_design, run_sims=run_sims, executable=executable)
    cand["specs"] = specs
    if spec_dead is not None:
        # The spec stage was requested but produced no run at all — the
        # candidate could not be fully evaluated (simulator missing or
        # netlist refused), whatever the physical verdict said.
        cand["status"] = "unavailable"
        cand["reason"] = spec_dead
        cand["stage"] = "specs"
        return cand
    if cand["status"] == "passed":
        cand["stage"] = "done"
    return cand


def _rank_key(cand: dict[str, Any]) -> tuple[float, float, float, str]:
    """Mechanical ordering: verify passed, then specs passed, then area."""
    verify = cand.get("verify") or {}
    specs = cand.get("specs") or {}
    verified = 1.0 if verify.get("passed") else 0.0
    # Unmeasured specs count as zero verified passes — never a credit.
    passed = float(specs["passed"]) if specs.get("passed") is not None else 0.0
    area = cand["area_um2"] if cand.get("area_um2") is not None else float("inf")
    return (-verified, -passed, area, str(cand["name"]))


def eval_candidates(
    design: Design,
    variants: list[dict[str, Any]],
    *,
    run_sims: bool = True,
    executable: str | None = None,
) -> dict[str, Any]:
    """Evaluate K design variants against one base design, side by side.

    ``variants`` is a list of ``{"name": str, "edits": [edit ops]}`` — the
    same typed ops ``transact`` accepts.  Each is applied to a fresh copy
    via ``DesignSession.transact`` (dry-run: validated, never committed),
    then compiled → PPA → ``verify_design`` → schematic testbenches.

    Returns ``{status, candidates, ranking, notes}``: per-candidate name,
    diff summary (``instance.param: old → new`` lines), area/DRC/LVS/
    spec numbers and an honest status; ``ranking`` is the mechanical sort
    (verify passed → specs passed → area asc) of the same rows.
    """
    notes = list(_POLICY_NOTES)
    if run_sims:
        notes.append(
            "testbench spec evaluation covers source='schematic' benches "
            "only; source='extracted' (PEX) benches are skipped during "
            "sweeps — run them on the chosen candidate")
    else:
        notes.append(
            "run_sims=False — testbenches not executed; spec sections "
            "report 'unavailable', not measured values")
    report: dict[str, Any] = {
        "status": "fail",
        "design": design.name,
        "pdk": design.pdk,
        "run_sims": bool(run_sims),
        "candidates": [],
        "ranking": [],
        "notes": notes,
    }
    if not isinstance(variants, list) or not variants:
        report["reason"] = (
            "variants must be a non-empty list of {name, edits} objects "
            "— nothing to evaluate")
        return report

    candidates = [
        _eval_one(design, variant, index,
                  run_sims=run_sims, executable=executable)
        for index, variant in enumerate(variants)
    ]
    report["candidates"] = candidates
    ordered = sorted(candidates, key=_rank_key)
    report["ranking"] = [
        {
            "rank": rank,
            "name": c["name"],
            "status": c["status"],
            "verify_passed": bool((c.get("verify") or {}).get("passed")),
            "specs_passed": (c.get("specs") or {}).get("passed"),
            "specs_total": (c.get("specs") or {}).get("total"),
            "area_um2": c.get("area_um2"),
        }
        for rank, c in enumerate(ordered, start=1)
    ]
    statuses = {c["status"] for c in candidates}
    if statuses == {"failed"}:
        report["status"] = "fail"
        report["reason"] = "every candidate failed"
    elif statuses == {"unavailable"}:
        report["status"] = "unavailable"
        report["reason"] = "no candidate could be fully evaluated"
    else:
        report["status"] = "ok"
    return report
