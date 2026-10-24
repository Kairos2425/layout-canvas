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
    assert card["release_id"] == "layout-canvas-bench-v1"
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
