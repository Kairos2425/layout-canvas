"""Frozen benchmark suite (``bench/``) — runner, baselines, diagnostics.

These exercise the real task directories end-to-end on the in-process
KLayout engine: a verify task must actually compile, extract, DRC-clean
and LVS-match. Simulation-gated tasks report ``unavailable`` when no
ngspice is present — the same fail-closed contract the runner encodes.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

pytest.importorskip("klayout.db", reason="klayout python module required")

REPO = Path(__file__).resolve().parent.parent
BENCH = REPO / "bench"
RUN_PY = BENCH / "run.py"


def _bench():
    """Load bench/run.py once — dataclasses resolve via sys.modules."""
    mod = sys.modules.get("lc_bench_run")
    if mod is not None:
        return mod
    spec = importlib.util.spec_from_file_location("lc_bench_run", RUN_PY)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    mod._register_blocks()
    return mod


@pytest.fixture(scope="module")
def bench():
    return _bench()


@pytest.fixture(scope="module")
def ihp_result(bench):
    """One real end-to-end verify run, shared by the read-only tests."""
    task = bench.load_task(BENCH / "tasks" / "ihp-mirror-verify")
    return task, bench.run_task(task)


# --- End-to-end --------------------------------------------------------

def test_verify_task_end_to_end(ihp_result):
    task, res = ihp_result
    assert res["status"] == "pass", res["failure"]
    ids = {c["id"]: c for c in res["checks"]}
    # hidden (unstated) checks ran and passed on real geometry
    assert ids["hidden.drc_clean"]["status"] == "pass"
    assert ids["hidden.drc_clean"]["measured_value"] == 0
    assert ids["hidden.lvs_match"]["measured_value"] is True
    assert ids["hidden.lvs_match"]["deterministic"] is True
    assert ids["bound.extract.devices"]["measured_value"] == 4
    # provenance: every input file hashed
    assert any(k.endswith("design.json") for k in res["input_sha256"])


def test_cli_bench_subcommand(tmp_path, monkeypatch, capsys):
    from layout_canvas.cli import main

    monkeypatch.setattr(
        sys, "argv",
        ["layout-canvas", "bench", "--task", "ihp-mirror-verify",
         "--check-baseline", "--out", str(tmp_path)])
    assert main() == 0
    card = json.loads((tmp_path / "scorecard.json").read_text())
    assert card["release_id"] == "layout-canvas-bench-v5"
    assert card["metric_schema"] == "flat-scalar-v2"
    assert card["coverage"] == {"runnable": 1, "total": 1}
    assert card["provenance"]["git_sha"] != "unknown"
    out = capsys.readouterr().out
    assert "no regressions" in out


# --- Baseline gate ------------------------------------------------------

def test_baseline_compare_clean(ihp_result):
    task, res = ihp_result
    bench = _bench()
    regressions, notes = bench._baseline_diffs(task, res)
    assert regressions == []


def test_baseline_compare_flags_sabotage(ihp_result, tmp_path):
    bench = _bench()
    task, res = ihp_result
    # a sabotaged reference: deterministic metric drifted
    ref = json.loads(
        (task.dir / "results" / "reference.json").read_text())
    for c in ref["checks"]:
        if c["id"] == "hidden.drc_clean":
            c["measured_value"] = 9
    victim = tmp_path / "ihp-mirror-verify"
    shutil.copytree(task.dir, victim)
    (victim / "results" / "reference.json").write_text(json.dumps(ref))
    t2 = bench.load_task(victim)
    regressions, _ = bench._baseline_diffs(t2, res)
    assert any("deterministic mismatch" in r for r in regressions)


def test_missing_reference_is_a_note(ihp_result, tmp_path, bench):
    task, res = ihp_result
    victim = tmp_path / "ihp-mirror-verify"
    shutil.copytree(task.dir, victim)
    (victim / "results" / "reference.json").unlink()
    t2 = bench.load_task(victim)
    regressions, notes = bench._baseline_diffs(t2, res)
    assert regressions == []
    assert any("unfrozen" in n for n in notes)


# --- Task loading diagnostics ------------------------------------------

def test_malformed_task_toml_names_file_and_field(tmp_path, bench):
    d = tmp_path / "bad-task"
    d.mkdir()
    (d / "task.toml").write_text("{ this is not toml", encoding="utf-8")
    with pytest.raises(bench.TaskError, match="invalid TOML"):
        bench.load_task(d)


def test_task_id_must_match_dir(tmp_path, bench):
    d = tmp_path / "real-name"
    d.mkdir()
    (d / "task.toml").write_text(
        'id = "other"\ntype = "verify"\ndeliverable = "starter/design.json"\n',
        encoding="utf-8")
    with pytest.raises(bench.TaskError, match="directory name"):
        bench.load_task(d)


def test_unknown_bound_key_rejected(tmp_path, bench):
    d = tmp_path / "bounds-task"
    d.mkdir()
    (d / "task.toml").write_text(
        'id = "bounds-task"\ntype = "verify"\n'
        'deliverable = "starter/design.json"\n'
        '[bounds]\n"x" = { min = 1, bogus = 2 }\n',
        encoding="utf-8")
    with pytest.raises(bench.TaskError, match="bogus"):
        bench.load_task(d)


def test_unknown_task_id_diag(bench):
    with pytest.raises(bench.TaskError, match="unknown task"):
        bench.discover_tasks(BENCH, "no-such-task")


# --- Capability semantics ----------------------------------------------

_HAS_NGSPICE = bool(
    os.environ.get("LAYOUT_CANVAS_NGSPICE") or shutil.which("ngspice"))


@pytest.mark.skipif(_HAS_NGSPICE, reason="ngspice present — task would evaluate")
def test_spec_task_unavailable_without_ngspice(bench, tmp_path):
    """No simulator → capability 'unavailable', never a pass/fail fake."""
    task = bench.load_task(BENCH / "tasks" / "sky130-ota-lab-specs")
    res = bench.run_task(task)
    assert res["status"] == "unavailable"
    assert res["passed"] is False
    spec_checks = [c for c in res["checks"] if c["id"].startswith("spec.")]
    assert spec_checks and all(
        c["status"] == "unavailable" and c["capability"] for c in spec_checks)
    # the deterministic hidden checks still measured real geometry
    hidden = {c["id"]: c for c in res["checks"]
              if c["id"].startswith("hidden.")}
    assert hidden["hidden.drc_clean"]["status"] == "pass"
    assert hidden["hidden.lvs_match"]["status"] == "pass"
    # coverage accounting: unavailable counts in total, not runnable
    card = bench._scorecard([task], [res], bench.load_manifest(BENCH))
    assert card["coverage"] == {"runnable": 0, "total": 1}
    assert card["evidence_complete"] is False


# --- Reliability rung: accelerated stress corner -------------------------

def test_stress_corner_task_loads(bench):
    task = bench.load_task(BENCH / "tasks" / "sky130-ota-stress-corner")
    assert task.type == "spec"
    metrics = {b.metric for b in task.bounds}
    assert "specs.total" in metrics and "ppa.area_um2" in metrics


@pytest.mark.skipif(not _HAS_NGSPICE, reason="ngspice not installed")
def test_stress_corner_task_end_to_end(bench):
    """Real ngspice double run: stress + drift specs, evidence rungs."""
    task = bench.load_task(BENCH / "tasks" / "sky130-ota-stress-corner")
    res = bench.run_task(task)
    assert res["status"] == "pass", res["failure"]
    ids = {c["id"]: c for c in res["checks"]}
    stress = ids["spec.tb_op_stress.outp_stress"]
    drift = ids["spec.tb_op_stress.outp_drift"]
    assert stress["status"] == "pass"
    assert stress["measured_value"] == pytest.approx(0.4658, abs=0.02)
    assert stress["evidence_level"] == "stress_corner"
    assert drift["status"] == "pass"
    # measured 2026-10-24, ngspice_con-47: |0.465776 - 0.609836| = 0.144
    assert drift["measured_value"] == pytest.approx(0.144, abs=0.01)
    assert drift["evidence_level"] == "stress_corner"
    assert "nominal=" in drift["message"] and "stress=" in drift["message"]
    assert (ids["spec.tb_op_schematic.outp_op"]["evidence_level"]
            == "solver_verified")
    assert ids["hidden.drc_clean"]["evidence_level"] == "deterministic"


@pytest.mark.skipif(_HAS_NGSPICE, reason="ngspice present — task would evaluate")
def test_stress_corner_task_unavailable_without_ngspice(bench):
    """No simulator → the stress run never fabricates: every spec.*
    check reports capability 'unavailable', the task 'unavailable'."""
    task = bench.load_task(BENCH / "tasks" / "sky130-ota-stress-corner")
    res = bench.run_task(task)
    assert res["status"] == "unavailable"
    spec_checks = [c for c in res["checks"] if c["id"].startswith("spec.")]
    assert spec_checks and all(
        c["status"] == "unavailable" and c["capability"]
        for c in spec_checks)
    assert any(c["id"] == "spec.tb_op_stress.outp_drift"
               and c["evidence_level"] == "stress_corner"
               for c in spec_checks)


# --- Reliability rung: parametric device stress (delvto/mulu0) ----------

def test_aging_proxy_task_loads(bench):
    task = bench.load_task(BENCH / "tasks" / "sky130-ota-aging-proxy")
    assert task.type == "spec"
    metrics = {b.metric for b in task.bounds}
    assert "specs.total" in metrics and "ppa.area_um2" in metrics


@pytest.mark.skipif(not _HAS_NGSPICE, reason="ngspice not installed")
def test_aging_proxy_task_end_to_end(bench):
    """Real ngspice run: BSIM4 delvto/mulu0 injected on the stress corner
    → parametric_stress rung; i(v_vdd) rides the EM budget spec."""
    task = bench.load_task(BENCH / "tasks" / "sky130-ota-aging-proxy")
    res = bench.run_task(task)
    assert res["status"] == "pass", res["failure"]
    ids = {c["id"]: c for c in res["checks"]}
    aging = ids["spec.tb_aging.outp_aging"]
    assert aging["status"] == "pass"
    # measured 2026-10-24 ngspice_con-47: 0.775238 V under
    # {vdd:1.98, temp:125, dvth_nmos:0.03, mu0_scale:0.85}
    assert aging["measured_value"] == pytest.approx(0.7752, abs=0.02)
    assert aging["evidence_level"] == "parametric_stress"
    drift = ids["spec.tb_aging.outp_aging_drift"]
    assert drift["status"] == "pass"
    assert drift["measured_value"] == pytest.approx(0.1654, abs=0.02)
    assert drift["evidence_level"] == "parametric_stress"
    em = ids["spec.tb_aging.i_vdd_budget"]
    assert em["status"] == "pass"
    # i(v_vdd) = supply current delivered on the stressed corner:
    # ~241 µA (positive = sourcing)
    assert em["measured_value"] == pytest.approx(2.41e-4, rel=0.05)
    assert em["evidence_level"] == "parametric_stress"


@pytest.mark.skipif(_HAS_NGSPICE, reason="ngspice present — task would evaluate")
def test_aging_proxy_task_unavailable_without_ngspice(bench):
    """No simulator → parametric-stress specs fail closed too."""
    task = bench.load_task(BENCH / "tasks" / "sky130-ota-aging-proxy")
    res = bench.run_task(task)
    assert res["status"] == "unavailable"
    spec_checks = [c for c in res["checks"] if c["id"].startswith("spec.")]
    assert spec_checks and all(
        c["status"] == "unavailable" and c["capability"]
        for c in spec_checks)
    assert any(c["id"] == "spec.tb_aging.outp_aging"
               and c["evidence_level"] == "parametric_stress"
               for c in spec_checks)


# --- Multi-candidate sweep (candidates task type) ----------------------

def test_candidates_task_loads(bench):
    task = bench.load_task(BENCH / "tasks" / "sky130-ota-candidates")
    assert task.type == "candidates"
    metrics = {b.metric for b in task.bounds}
    assert {"candidates.passed", "candidates.failed",
            "candidates.ranking_top"} <= metrics
    # the designed-fail variant is pinned in evaluator config
    assert task.cand_cfg["expect"]["ghost"] == "failed"
    assert task.cand_cfg["expect_specs"]["w100"] == 5
    # the variant file is the exact {name, edits} list the tool takes
    variants = bench._load_variants(task)
    assert [v["name"] for v in variants] == ["w080", "w100", "w120",
                                           "ghost"]
    assert all(set(v) == {"name", "edits"} for v in variants)


@pytest.mark.skipif(not _HAS_NGSPICE, reason="ngspice not installed")
def test_candidates_task_end_to_end(bench):
    """Real ngspice run: 3 valid variants pass with measured spec counts,
    the ghost variant reports its designed 'failed' status, and every
    evaluated candidate faces the hidden DRC/LVS gate."""
    task = bench.load_task(BENCH / "tasks" / "sky130-ota-candidates")
    res = bench.run_task(task)
    assert res["status"] == "pass", res["failure"]
    ids = {c["id"]: c for c in res["checks"]}
    assert ids["candidates.report"]["measured_value"] == "ok"
    for name, n_specs in (("w080", 3), ("w100", 5), ("w120", 4)):
        c = ids[f"candidate.{name}.status"]
        assert c["status"] == "pass" and c["measured_value"] == "passed"
        assert c["capability"] and not c["deterministic"]
        assert ids[f"hidden.drc_clean.{name}"]["measured_value"] == 0
        assert ids[f"hidden.lvs_match.{name}"]["measured_value"] is True
        sp = ids[f"candidate.{name}.specs_passed"]
        assert sp["measured_value"] == n_specs and sp["status"] == "pass"
        assert sp["capability"]
        assert sp["evidence_level"] == "solver_verified"
    ghost = ids["candidate.ghost.status"]
    # designed-fail is a real check: measured 'failed' == expected
    assert ghost["status"] == "pass"
    assert ghost["measured_value"] == "failed"
    assert ghost["deterministic"] and not ghost["capability"]
    # the rejected variant never compiled — no hidden/spec checks for it
    assert "hidden.drc_clean.ghost" not in ids
    assert "candidate.ghost.specs_passed" not in ids
    # frozen numbers: deterministic areas + the mechanical ranking
    assert ids["bound.candidate.w080.area_um2"]["measured_value"] \
        == pytest.approx(1701.546)
    assert ids["bound.candidate.w120.area_um2"]["measured_value"] \
        == pytest.approx(1762.506)
    assert ids["bound.candidates.ranking_top"]["measured_value"] == "w100"
    metrics = {m["id"]: m["value"] for m in res["metrics"]}
    assert metrics["candidates.ranking"] == ["w100", "w120", "w080",
                                             "ghost"]


@pytest.mark.skipif(_HAS_NGSPICE, reason="ngspice present — task would evaluate")
def test_candidates_task_unavailable_without_ngspice(bench):
    """No simulator → spec-evaluated candidates report capability
    'unavailable'; the designed-fail ghost check still passes — the
    fail-closed pin needs no simulator."""
    task = bench.load_task(BENCH / "tasks" / "sky130-ota-candidates")
    res = bench.run_task(task)
    assert res["status"] == "unavailable"
    ids = {c["id"]: c for c in res["checks"]}
    for name in ("w080", "w100", "w120"):
        c = ids[f"candidate.{name}.status"]
        assert c["status"] == "unavailable" and c["capability"]
        assert ids[f"candidate.{name}.specs_passed"]["status"] \
            == "unavailable"
    ghost = ids["candidate.ghost.status"]
    assert ghost["status"] == "pass"
    assert ghost["measured_value"] == "failed"
