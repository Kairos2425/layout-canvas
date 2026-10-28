"""Matching/symmetry quality checker tests.

``check_constraints`` evaluates declared IR constraints against placed
instance bboxes — the geometry evidence a green DRC/LVS never provided.
"""
import pytest

import layout_canvas.blocks.sky130  # noqa: F401 - registers sky130 blocks
from layout_canvas.compiler.compile import compile_design
from layout_canvas.ir.model import Constraint, Design
from layout_canvas.tools.matching import TOL_UM, check_constraints

_MIRROR = "sky130.current_mirror"


def _design(instances: list[dict], constraints: list[dict] | None = None,
            nets: list[dict] | None = None,
            ports: list[dict] | None = None) -> Design:
    return Design.model_validate({
        "name": "match_test", "pdk": "sky130",
        "instances": instances,
        "constraints": constraints or [],
        "nets": nets or [],
        "ports": ports or [],
    })


def _mirror(iid: str, x: float, y: float,
            params: dict | None = None) -> dict:
    return {"id": iid, "block": _MIRROR,
            "params": params or {"fingers": 2},
            "placement": {"x": x, "y": y}}


def _only(res: dict) -> dict:
    assert len(res["checks"]) == 1
    return res["checks"][0]


def test_symmetric_pair_mirrored_passes():
    d = _design(
        [_mirror("a", 0, 0), _mirror("b", 8, 0)],
        [{"type": "symmetric", "instances": ["a", "b"],
          "axis": "vertical"}])
    res = check_constraints(d, component=compile_design(d))
    assert res["status"] == "pass"
    chk = _only(res)
    assert chk["status"] == "pass"
    members = {m["instance"]: m for m in chk["measured"]["members"]}
    assert members["a"]["mirror_partner"] == "b"
    assert members["b"]["mirror_partner"] == "a"
    assert members["a"]["dx_deviation_um"] == pytest.approx(0.0, abs=1e-4)
    assert members["a"]["dy_deviation_um"] == pytest.approx(0.0, abs=1e-4)


def test_symmetric_pair_off_axis_fails():
    d = _design(
        [_mirror("a", 0, 0), _mirror("b", 8, 0.1)],
        [{"type": "symmetric", "instances": ["a", "b"],
          "axis": "vertical"}])
    res = check_constraints(d, component=compile_design(d))
    assert res["status"] == "fail"
    chk = _only(res)
    assert chk["status"] == "fail"
    assert "a" in chk["note"] and "b" in chk["note"]
    members = {m["instance"]: m for m in chk["measured"]["members"]}
    assert members["b"]["mirror_partner"] is None
    assert members["b"]["dy_deviation_um"] == pytest.approx(0.1, abs=1e-3)


def test_symmetric_odd_count_middle_on_axis():
    d = _design(
        [_mirror("a", 0, 0), _mirror("mid", 4, 0), _mirror("b", 8, 0)],
        [{"type": "symmetric", "instances": ["a", "mid", "b"],
          "axis": "vertical"}])
    res = check_constraints(d, component=compile_design(d))
    assert res["status"] == "pass"
    members = {m["instance"]: m
               for m in _only(res)["measured"]["members"]}
    assert members["mid"]["on_axis"] is True


def test_symmetric_odd_count_middle_off_axis_fails():
    d = _design(
        [_mirror("a", 0, 0), _mirror("mid", 4.2, 0), _mirror("b", 8, 0)],
        [{"type": "symmetric", "instances": ["a", "mid", "b"],
          "axis": "vertical"}])
    res = check_constraints(d, component=compile_design(d))
    assert res["status"] == "fail"
    assert "mid" in _only(res)["note"]


def test_match_pass_and_param_fail():
    d = _design(
        [_mirror("m1", 0, 0), _mirror("m2", 9, 0), _mirror("m3", 18, 0)],
        [{"type": "match", "instances": ["m1", "m2", "m3"]}])
    res = check_constraints(d, component=compile_design(d))
    assert res["status"] == "pass"
    assert "not inspected" in _only(res)["note"]

    d2 = _design(
        [_mirror("m1", 0, 0), _mirror("m2", 9, 0, {"fingers": 2, "width": 2.0}),
         _mirror("m3", 18, 0)],
        [{"type": "match", "instances": ["m1", "m2", "m3"]}])
    res2 = check_constraints(d2, component=compile_design(d2))
    chk = _only(res2)
    assert chk["status"] == "fail"
    assert "width" in chk["measured"]["differing_params"]
    assert "width" in chk["note"]
    assert chk["measured"]["same_block_and_params"] is False


def test_match_nonuniform_pitch_fails():
    d = _design(
        [_mirror("m1", 0, 0), _mirror("m2", 9, 0), _mirror("m3", 18.4, 0)],
        [{"type": "match", "instances": ["m1", "m2", "m3"]}])
    res = check_constraints(d, component=compile_design(d))
    chk = _only(res)
    assert chk["status"] == "fail"
    assert chk["measured"]["uniform_pitch"] is False
    assert chk["measured"]["max_pitch_deviation_um"] == pytest.approx(
        0.4, abs=1e-3)


def test_order_left_to_right():
    insts = [_mirror("m1", 0, 0), _mirror("m2", 9, 0), _mirror("m3", 18, 0)]
    ok = _design(insts, [{"type": "order",
                          "instances": ["m1", "m2", "m3"],
                          "direction": "left_to_right"}])
    res = check_constraints(ok, component=compile_design(ok))
    assert _only(res)["status"] == "pass"

    bad = _design(insts, [{"type": "order",
                           "instances": ["m1", "m3", "m2"],
                           "direction": "left_to_right"}])
    res2 = check_constraints(bad, component=compile_design(bad))
    chk = _only(res2)
    assert chk["status"] == "fail"
    assert chk["measured"]["actual_order"] == ["m1", "m2", "m3"]


def test_align_and_abut():
    ok = _design(
        [_mirror("a", 0, 0), _mirror("b", 0, 9)],
        [{"type": "align", "instances": ["a", "b"], "edge": "left"}])
    res = check_constraints(ok, component=compile_design(ok))
    assert _only(res)["status"] == "pass"

    off = _design(
        [_mirror("a", 0, 0), _mirror("b", 0.2, 9)],
        [{"type": "align", "instances": ["a", "b"], "edge": "left"}])
    res2 = check_constraints(off, component=compile_design(off))
    chk = _only(res2)
    assert chk["status"] == "fail"
    members = {m["instance"]: m for m in chk["measured"]["members"]}
    assert members["b"]["deviation_um"] == pytest.approx(0.2, abs=1e-3)

    touching = _design(
        [_mirror("a", 0, 0),
         {"id": "b", "block": _MIRROR, "params": {"fingers": 2},
          "placement": {"relative_to": "a", "relation": "right_of",
                        "margin": 0.0}}],
        [{"type": "abut", "instances": ["a", "b"],
          "direction": "left_to_right"}])
    res3 = check_constraints(touching,
                             component=compile_design(touching))
    assert _only(res3)["status"] == "pass"

    gapped = _design(
        [_mirror("a", 0, 0),
         {"id": "b", "block": _MIRROR, "params": {"fingers": 2},
          "placement": {"relative_to": "a", "relation": "right_of",
                        "margin": 0.5}}],
        [{"type": "abut", "instances": ["a", "b"],
          "direction": "left_to_right"}])
    res4 = check_constraints(gapped, component=compile_design(gapped))
    chk4 = _only(res4)
    assert chk4["status"] == "fail"
    assert chk4["measured"]["max_gap_um"] == pytest.approx(0.5, abs=1e-3)


def test_unknown_instance_unavailable_and_not_applicable():
    # Design-level validation normally forbids dangling ids; a session
    # constraint appended after validation must still fail closed.
    d = _design([_mirror("a", 0, 0)])
    d.constraints.append(Constraint.model_validate(
        {"type": "symmetric", "instances": ["a", "ghost"],
         "axis": "vertical"}))
    res = check_constraints(d, component=compile_design(d))
    chk = _only(res)
    assert chk["status"] == "fail"
    assert "ghost" in chk["note"]

    no_axis = _design(
        [_mirror("a", 0, 0), _mirror("b", 8, 0)],
        [{"type": "symmetric", "instances": ["a", "b"]}])
    res2 = check_constraints(no_axis, component=compile_design(no_axis))
    assert res2["status"] == "unavailable"
    assert _only(res2)["status"] == "unavailable"

    plain = _design([_mirror("a", 0, 0)])
    res3 = check_constraints(plain, component=compile_design(plain))
    assert res3["status"] == "not_applicable"
    assert res3["checks"] == []


def test_verify_design_gates_on_quality():
    from layout_canvas.tools.verify import verify_design

    # A clean extract/DRC/LVS design whose declared match constraint is
    # broken: the designer asked for it, so it gates the verdict.
    d = _design(
        [_mirror("m1", 0, 0, {"fingers": 2}),
         _mirror("m2", 9, 0, {"fingers": 2, "width": 2.0})],
        [{"type": "match", "instances": ["m1", "m2"]}],
        ports=[{"name": "IN1", "pin": "m1.in", "direction": "input"},
               {"name": "IN2", "pin": "m2.in", "direction": "input"}])
    res = verify_design(d)
    assert res["quality"]["status"] == "fail"
    assert any(c["type"] == "match" and c["status"] == "fail"
               for c in res["quality"]["checks"])
    assert res["passed"] is False

    # No declared constraints: quality is a report, not a gate.
    d2 = _design(
        [_mirror("cm", 0, 0, {"fingers": 2})],
        ports=[{"name": "IN", "pin": "cm.in", "direction": "input"}])
    res2 = verify_design(d2)
    assert res2["quality"]["status"] == "not_applicable"
    assert res2["passed"] is True


def test_tolerance_is_snap_grid():
    assert TOL_UM == pytest.approx(0.005)
