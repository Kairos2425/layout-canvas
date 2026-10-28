"""Multi-candidate evaluation: variants through the transact boundary,
each compiled + verified + (optionally) simulated, compared in one
fail-closed report — a broken variant fails alone, never poisons the rest."""

import os
import shutil
from pathlib import Path

import pytest

import layout_canvas.blocks.sky130  # noqa: F401  populate block registry
from layout_canvas.ir.model import Design
from layout_canvas.tools.candidates import eval_candidates

REPO = Path(__file__).resolve().parent.parent
OTA_JSON = REPO / "examples" / "ota_lab.json"

_NGSPICE = os.environ.get("LAYOUT_CANVAS_NGSPICE") or shutil.which("ngspice")


def _ota() -> Design:
    return Design.from_json(OTA_JSON.read_text(encoding="utf-8"))


def _width_variants(factors=(0.8, 1.0, 1.2)) -> list[dict]:
    """TAIL.width × factor — moves the mirror-row cell top, hence real
    per-candidate area deltas (measured sky130: 1701.5 / 1732.0 / 1762.5)."""
    return [
        {"name": f"w{int(f * 100):03d}",
         "edits": [{"op": "set_params", "instance": "TAIL",
                    "params": {"width": 2.0 * f}}]}
        for f in factors
    ]


def test_variants_evaluate_with_real_numbers():
    """ota_lab + w×{0.8,1.0,1.2}: every candidate compiles, verifies and
    reports real area/drc/lvs — sims optional (spec numbers need ngspice)."""
    rep = eval_candidates(_ota(), _width_variants(), run_sims=False)
    assert rep["status"] == "ok"
    assert len(rep["candidates"]) == 3
    areas = []
    for c in rep["candidates"]:
        assert c["status"] == "passed", c["reason"]
        assert c["edits_valid"] is True
        assert c["diff_summary"] and "TAIL.width" in c["diff_summary"][0]
        assert "→" in c["diff_summary"][0]
        assert c["area_um2"] > 0
        assert c["verify"]["drc_violations"] == 0
        assert c["verify"]["lvs_match"] is True
        assert c["verify"]["lvs_status"] == "passed"
        assert c["verify"]["quality_status"] in (
            "pass", "unavailable", "not_applicable")
        areas.append(c["area_um2"])
    # width sweep really moved the cell top — three distinct areas,
    # ascending with width.
    assert len(set(areas)) == 3
    assert areas == sorted(areas)


@pytest.mark.skipif(_NGSPICE is None, reason="ngspice not installed")
def test_variants_real_spec_numbers_and_ranking():
    """With a live simulator the sweep reports per-candidate spec counts;
    ranking is the mechanical sort (verify → specs passed → area)."""
    rep = eval_candidates(_ota(), _width_variants(), run_sims=True)
    by_name = {c["name"]: c for c in rep["candidates"]}
    base = by_name["w100"]  # TAIL.width 2.0 — the untouched baseline
    assert base["status"] == "passed"
    assert base["specs"]["status"] == "pass"
    # 2 schematic benches × specs; tb_op_pex is PEX — skipped, not run.
    assert base["specs"]["total"] == 5
    assert base["specs"]["passed"] == 5
    assert [s["testbench"] for s in base["specs"]["skipped"]] == ["tb_op_pex"]
    for c in rep["candidates"]:
        assert c["specs"]["passed"] is not None
        assert 0 <= c["specs"]["passed"] <= 5
    # ranking is exactly the documented mechanical ordering of the fields
    def key(c):
        return (-int(bool(c["verify"]["passed"])),
                -(c["specs"]["passed"] or 0),
                c["area_um2"] if c["area_um2"] is not None else float("inf"))
    expect = sorted(rep["candidates"], key=key)
    assert [r["name"] for r in rep["ranking"]] == [c["name"] for c in expect]
    # the untouched baseline wins on spec count — measured 5/5 on
    # ngspice_con-47 + bundled sky130 TT models.
    assert rep["ranking"][0]["name"] == "w100"


def test_invalid_edit_fails_alone():
    """A rejected edit list lands that candidate in `failed` with the
    transact diagnostics — the others still evaluate fully."""
    variants = _width_variants()[:2] + [
        {"name": "ghost",
         "edits": [{"op": "set_params", "instance": "NOPE",
                    "params": {"width": 1.0}}]},
        {"name": "bad_param",
         "edits": [{"op": "set_params", "instance": "TAIL",
                    "params": {"width": "fat"}}]},
    ]
    rep = eval_candidates(_ota(), variants, run_sims=False)
    assert rep["status"] == "ok"  # failures are per-candidate, never global
    by_name = {c["name"]: c for c in rep["candidates"]}
    assert by_name["w080"]["status"] == "passed"
    assert by_name["w100"]["status"] == "passed"
    ghost = by_name["ghost"]
    assert ghost["status"] == "failed"
    assert ghost["edits_valid"] is False
    assert "no instance 'NOPE'" in ghost["reason"]
    assert ghost["diagnostics"]  # real transact diagnostics carried
    assert ghost["stage"] == "edits"
    bad = by_name["bad_param"]
    assert bad["status"] == "failed"
    # a value the block can't consume survives session validation (the
    # session is schema-agnostic) and fails honestly at compile time.
    assert bad["stage"] == "compile"
    # failed candidates still rank — mechanically last (no verify, no area)
    assert rep["ranking"][-1]["status"] in ("failed", "unavailable")


def test_run_sims_false_reports_unavailable_not_fabricated():
    """run_sims=False on a spec design: candidates still compile+verify;
    the spec section is honestly unavailable, never invented numbers."""
    rep = eval_candidates(_ota(), _width_variants()[:2], run_sims=False)
    assert rep["status"] == "ok"
    for c in rep["candidates"]:
        assert c["status"] == "passed"
        sp = c["specs"]
        assert sp["status"] == "unavailable"
        assert sp["passed"] is None
        assert sp["total"] == 5  # static declaration count, not measured
        assert "run_sims=False" in sp["reason"]
        assert sp["runs"] == []
        # the PEX bench is still accounted for — skipped, not dropped
        assert [s["testbench"] for s in sp["skipped"]] == ["tb_op_pex"]


def test_empty_and_malformed_variants_fail_closed():
    """Zero variants → status fail with a reason, never a crash; a
    malformed variant is a failed candidate, not an exception."""
    rep = eval_candidates(_ota(), [], run_sims=False)
    assert rep["status"] == "fail"
    assert rep["reason"]
    assert rep["candidates"] == [] and rep["ranking"] == []
    rep = eval_candidates(_ota(), "not-a-list", run_sims=False)
    assert rep["status"] == "fail" and rep["reason"]
    rep = eval_candidates(_ota(), None, run_sims=False)
    assert rep["status"] == "fail" and rep["reason"]
    rep = eval_candidates(
        _ota(), [{"name": "no_edits"}, 42], run_sims=False)
    assert rep["status"] == "fail"  # both candidates failed → all-failed
    assert all(c["status"] == "failed" for c in rep["candidates"])
    assert rep["candidates"][0]["reason"]
    # ... and a single working variant mixed with a broken one still reports ok
    rep = eval_candidates(
        _ota(), [{"name": "no_edits"}] + _width_variants()[:1],
        run_sims=False)
    assert rep["status"] == "ok"


def test_web_endpoint_and_candidate_preview():
    """/api/candidates mirrors the engine; /api/candidate_preview renders
    one variant's layout on demand — both structured, never a 500."""
    from layout_canvas.web.app import _api

    ir = _ota().model_dump()
    res = _api("candidates", {"ir_json": ir,
                              "variants": _width_variants()[:2],
                              "run_sims": False})
    assert res["status"] == "ok", res.get("error")
    rep = res["data"]
    assert rep["status"] == "ok"
    assert {c["name"] for c in rep["candidates"]} == {"w080", "w100"}
    prev = _api("candidate_preview", {"name": "w080"})
    assert prev["status"] == "ok"
    assert prev["data"]["svg"].startswith("<svg")
    prev = _api("candidate_preview", {"name": "nope"})
    assert prev["status"] == "error"
    # bad variants payload → structured error, no exception
    bad = _api("candidates", {"ir_json": ir, "variants": "junk"})
    assert bad["status"] == "ok"  # engine-level fail, still structured
    assert bad["data"]["status"] == "fail"


def test_mcp_candidates_eval_tool():
    """The tool is registered and dispatches through execute_tool."""
    from layout_canvas.mcp.server import LayoutCanvasMCPServer

    server = LayoutCanvasMCPServer()
    names = [t["name"] for t in server.get_tool_definitions()]
    assert "candidates_eval" in names
    rep = server.execute_tool("candidates_eval", {
        "ir_json": _ota().model_dump(),
        "variants": _width_variants()[:2],
        "run_sims": False,
    })
    assert rep["status"] == "ok"
    assert len(rep["candidates"]) == 2
    assert all("verify" in c for c in rep["candidates"])
    # session source works too and journals the run
    opened = server.execute_tool(
        "open_design", {"ir_json": _ota().model_dump()})
    rep = server.execute_tool("candidates_eval", {
        "session_id": opened["session_id"],
        "variants": _width_variants()[:1],
        "run_sims": False,
    })
    assert rep["status"] == "ok"
    snap = server.execute_tool("snapshot",
                               {"session_id": opened["session_id"]})
    assert snap["data"]["runs"][-1]["kind"] == "candidates"
    with pytest.raises(ValueError, match="needs 'session_id' or 'ir_json'"):
        server.execute_tool("candidates_eval", {"variants": []})
