"""Golden common-centroid diff-pair exemplar (work-plan P1.5).

``examples/golden_diffpair.json`` is the narrative counter-example to the
ViraStack tsmc65 card — a design that passed DRC/LVS while carrying no
matching-quality evidence. This file pins down the golden contract:

- ``verify_design`` passes end-to-end (extract/DRC/LVS/quality);
- the ``quality`` section reports ``pass`` with measured deviations for
  every declared symmetric/match/align/order constraint, inside the
  checker's own tolerance plus a small margin;
- the guard-ring enclosure claim is true — the ring's inner cavity
  geometrically contains every device cell it claims to enclose;
- the verification payload retains the evidence fields the gallery card
  reads (quality checks + measured values, DRC violations, LVS match,
  testbench spec verdicts);
- the numbers stamped into ``design.meta`` match what the verifier
  measures — stamped evidence that cannot silently go stale.

No fabricated fixtures: the real compiled GDS, the real KLayout-pya
extraction/DRC/LVS and real ngspice runs produce every number asserted.
"""

import os
import shutil
from pathlib import Path

import pytest

import layout_canvas.blocks.generic  # noqa: F401 - registers generic blocks
import layout_canvas.blocks.sky130  # noqa: F401 - registers sky130 blocks
from layout_canvas.compiler.compile import compile_design
from layout_canvas.ir.model import Design
from layout_canvas.tools.matching import TOL_UM, _resolve_bboxes
from layout_canvas.tools.testbench import run_testbench
from layout_canvas.tools.verify import verify_design

pytest.importorskip("klayout.db", reason="klayout python module required")

_EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "golden_diffpair.json"
_ENCLOSED = ("DP_L", "DP_R", "LOAD", "TAIL")
_MARGIN_UM = 2 * TOL_UM  # small margin above the pass tolerance

_HAS_NGSPICE = bool(os.environ.get("LAYOUT_CANVAS_NGSPICE") or shutil.which("ngspice"))


def _design() -> Design:
    return Design.from_json(_EXAMPLE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def design() -> Design:
    return _design()


@pytest.fixture(scope="module")
def verify_result(design: Design) -> dict:
    return verify_design(design)


@pytest.fixture(scope="module")
def bboxes(design: Design):
    comp = compile_design(design)
    return _resolve_bboxes(design, comp)


def _check(quality: dict, type_name: str) -> dict:
    return next(c for c in quality["checks"] if c["type"] == type_name)


def test_verify_passes_end_to_end(verify_result: dict):
    assert verify_result["passed"] is True
    assert verify_result["extract"]["status"] == "ok"
    assert verify_result["extract"]["errors"] == []
    assert verify_result["drc"]["status"] == "passed"
    assert verify_result["drc"]["total_violations"] == 0
    assert verify_result["lvs"]["status"] == "passed"
    assert verify_result["lvs"]["match"] is True


def test_quality_status_is_pass(verify_result: dict):
    # A declared-constraint verdict of "pass" is the point of the golden —
    # unavailable/not_applicable would mean the evidence is absent.
    assert verify_result["quality"]["status"] == "pass"
    assert all(c["status"] == "pass" for c in verify_result["quality"]["checks"])


def test_symmetric_deviations_within_tolerance(verify_result: dict):
    chk = _check(verify_result["quality"], "symmetric")
    measured = chk["measured"]
    assert measured["axis"] == "vertical"
    assert len(measured["members"]) == 2
    for member in measured["members"]:
        assert member["mirror_partner"] is not None
        assert abs(member["dx_deviation_um"]) <= TOL_UM + _MARGIN_UM
        assert abs(member["dy_deviation_um"]) <= TOL_UM + _MARGIN_UM


def test_match_deviations_within_tolerance(verify_result: dict):
    chk = _check(verify_result["quality"], "match")
    measured = chk["measured"]
    assert measured["same_block_and_params"] is True
    assert measured["congruent_bbox"] is True
    assert measured["uniform_pitch"] is True
    assert measured["differing_params"] == []
    assert measured["max_dim_deviation_um"] <= TOL_UM + _MARGIN_UM
    assert measured["max_pitch_deviation_um"] <= TOL_UM + _MARGIN_UM


def test_align_deviations_within_tolerance(verify_result: dict):
    aligns = [c for c in verify_result["quality"]["checks"] if c["type"] == "align"]
    assert len(aligns) == 2
    for chk in aligns:
        for member in chk["measured"]["members"]:
            assert abs(member["deviation_um"]) <= TOL_UM + _MARGIN_UM


def test_order_check_passes(verify_result: dict):
    chk = _check(verify_result["quality"], "order")
    assert chk["measured"]["actual_order"] == chk["measured"]["expected_order"]


def test_guard_ring_encloses_device_cells(design: Design, bboxes: dict):
    """The design claims a ptap guard ring enclosing all four device cells.

    Check the claim geometrically: the ring's inner cavity (placement ±
    inner_width/2 × inner_height/2) must strictly contain each placed
    device bbox — a real containment check on compiled geometry, not a
    declared-but-unverified note.
    """
    gr = design.instance("GR")
    cx, cy = gr.placement.x, gr.placement.y
    hw, hh = (float(gr.params["inner_width"]) / 2.0, float(gr.params["inner_height"]) / 2.0)
    cavity = (cx - hw, cy - hh, cx + hw, cy + hh)

    # The ring's own placed bbox must be consistent with its declared
    # cavity + ring width (plus the generator's fixed implant overhang).
    gr_l, gr_b, gr_r, gr_t = bboxes["GR"]
    ring_w = float(gr.params["ring_width"])
    assert gr_r - gr_l == pytest.approx(
        float(gr.params["inner_width"]) + 2 * ring_w + 0.24, abs=0.01
    )
    assert gr_t - gr_b == pytest.approx(
        float(gr.params["inner_height"]) + 2 * ring_w + 0.24, abs=0.01
    )

    for inst_id in _ENCLOSED:
        left, bot, right, top = bboxes[inst_id]
        assert left > cavity[0], f"{inst_id} west of the ring cavity"
        assert bot > cavity[1], f"{inst_id} south of the ring cavity"
        assert right < cavity[2], f"{inst_id} east of the ring cavity"
        assert top < cavity[3], f"{inst_id} north of the ring cavity"


def test_verify_payload_exposes_gallery_evidence(verify_result: dict):
    """The gallery card reads these fields off the verify payload — they
    must exist and carry real data, not just a bare ``passed`` flag."""
    quality = verify_result["quality"]
    assert quality["status"] == "pass"
    assert quality["tolerance_um"] == pytest.approx(TOL_UM)
    assert len(quality["checks"]) >= 4
    for chk in quality["checks"]:
        for key in ("id", "type", "instances", "status", "measured", "note"):
            assert key in chk
        assert chk["measured"], f"{chk['id']} carries no measured evidence"
    assert verify_result["drc"]["total_violations"] == 0
    assert verify_result["lvs"]["match"] is True
    assert verify_result["extract"]["devices"] > 0


def test_meta_stamped_evidence_matches_measured(design: Design, verify_result: dict):
    """Numbers stamped into design.meta are evidence for the card — pin
    them to what the verifier actually measures so they cannot go stale."""
    meta = design.meta
    sym = _check(verify_result["quality"], "symmetric")["measured"]
    worst_sym = max(
        max(abs(m["dx_deviation_um"]), abs(m["dy_deviation_um"])) for m in sym["members"]
    )
    assert meta["measured_symmetric_dev_um"] == pytest.approx(worst_sym)

    match = _check(verify_result["quality"], "match")["measured"]
    assert meta["measured_match_max_dev_um"] == pytest.approx(match["max_dim_deviation_um"])
    pitch = match["pitch_um"][0]
    assert meta["measured_match_pitch_um"] == f"{pitch[0]:g} x {pitch[1]:g}"

    align = next(
        c
        for c in verify_result["quality"]["checks"]
        if c["type"] == "align" and "GR" in c["instances"]
    )
    worst_align = max(abs(m["deviation_um"]) for m in align["measured"]["members"])
    assert meta["measured_align_center_x_dev_um"] == pytest.approx(worst_align)


@pytest.mark.skipif(not _HAS_NGSPICE, reason="ngspice not installed")
def test_schematic_testbench_specs_pass(design: Design):
    result = run_testbench(design, "tb_op_schematic")
    assert result["status"] == "passed"
    assert result["spec_status"] == "pass"
    assert all(s["status"] == "pass" for s in result["specs"])
    waves = result["waves"]
    # Differential balance: the symmetric pair lands both outputs within
    # a millivolt of each other on the reference netlist.
    assert abs(waves["outp"][0] - waves["outn"][0]) < 1e-3
    # every spec carries a measured value for the card
    assert all(s["value"] is not None for s in result["specs"])


@pytest.mark.skipif(not _HAS_NGSPICE, reason="ngspice not installed")
def test_extracted_testbench_specs_pass(design: Design):
    result = run_testbench(design, "tb_op_extracted")
    assert result["status"] == "passed"
    assert result["spec_status"] == "pass"
    assert all(s["status"] == "pass" for s in result["specs"])
    assert "unresolved_probes" not in result
    waves = result["waves"]
    assert abs(waves["outp"][0] - waves["outn"][0]) < 1e-3


def test_gallery_verification_snapshot_shape(design: Design):
    """The publish receipt stores {status, drc_violations, lvs_match,
    passed} — confirm the design produces the verified-card fields."""
    from layout_canvas.web.gallery import _verification_report

    report = _verification_report(design)
    assert report["status"] == "ok"
    assert report["drc_violations"] == 0
    assert report["lvs_match"] is True
    assert report["passed"] is True
