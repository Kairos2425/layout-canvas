"""Tests for the local web review canvas API layer."""

import json

import layout_canvas.blocks.sky130  # noqa: F401  populate block registry
from layout_canvas.web.app import _api


def _ir() -> dict:
    return {
        "name": "web_test",
        "pdk": "sky130",
        "instances": [{"id": "dp", "block": "sky130.diff_pair", "params": {}}],
        "nets": [],
        "ports": [],
    }


def test_preview_returns_svg():
    res = _api("preview", {"ir_json": _ir()})
    assert res["status"] == "ok"
    assert res["data"]["svg"].startswith("<svg")


def test_preview_renders_real_geometry():
    """The preview draws per-layer polygons, not just port markers."""
    from layout_canvas.compiler.render import render_svg
    from layout_canvas.blocks import base

    comp = base.get("sky130.diff_pair").component()
    res = render_svg(comp)
    svg = res["svg"]
    assert svg.count("<polygon") >= 10  # diff + poly fingers + implant
    assert "65/20" in res["layers"]  # sky130 diff
    assert res["truncated"] is False
    # y-flip: all polygon y coords land inside [0, height]
    bb = res["bbox"]
    height = bb[3] - bb[1]
    import re

    ys = [float(y) for _, y in re.findall(r'(-?\d+\.\d+),(-?\d+\.\d+)', svg)]
    assert all(-1.0 <= y <= height + 1.0 for y in ys)


def test_ppa_and_connectivity():
    ppa = _api("ppa", {"ir_json": _ir()})
    assert ppa["status"] == "ok"
    assert ppa["data"]["area_um2"] > 0
    conn = _api("connectivity", {"ir_json": _ir()})
    assert conn["status"] == "ok"
    assert "dp" in conn["data"]["instances"]


def test_abstract():
    res = _api("abstract", {"ir_json": _ir()})
    assert res["status"] == "ok"
    assert res["data"]["name"] == "web_test"
    assert len(res["data"]["bbox"]) == 4


def test_invalid_ir_returns_structured_error():
    res = _api("preview", {"ir_json": "{bad json"})
    assert res["status"] == "error"
    assert "invalid IR" in res["error"]


def test_netlist_action():
    res = _api("netlist", {"ir_json": _ir()})
    assert res["status"] == "ok"
    assert ".subckt web_test" in res["data"]["spice"]


def test_session_lifecycle_and_edit():
    """Human edits go through the same transactional boundary as the agent."""
    from layout_canvas.web import app

    try:
        res = _api("session/open", {"ir_json": _ir()})
        assert res["status"] == "ok"
        assert res["revision"] == 0
        assert res["data"]["design"]["name"] == "web_test"

        res = _api(
            "session/edit",
            {
                "edits": [{"op": "set_placement", "instance": "dp", "x": 5.0, "y": 3.0}],
                "expected_revision": 0,
            },
        )
        assert res["status"] == "ok"
        assert res["revision"] == 1
        pl = res["data"]["design"]["instances"][0]["placement"]
        assert (pl["x"], pl["y"]) == (5.0, 3.0)

        # Stale revision is rejected, not silently overwritten.
        res = _api(
            "session/edit",
            {"edits": [{"op": "set_placement", "instance": "dp", "x": 0.0}], "expected_revision": 0},
        )
        assert res["status"] == "rejected"
        assert res["diagnostics"][0]["code"] == "revision-conflict"

        res = _api("session/undo", {})
        assert res["status"] == "ok"
        pl = res["data"]["design"]["instances"][0]["placement"]
        assert (pl["x"], pl["y"]) == (0.0, 0.0)
    finally:
        _api("session/close", {})
    assert app._SESSION is None


def test_preview_reports_instance_bboxes():
    res = _api("preview", {"ir_json": _ir()})
    assert res["status"] == "ok"
    box = res["data"]["instances"]["dp"]
    assert len(box) == 4 and box[2] > box[0] and box[3] > box[1]


def test_preview_reports_pin_positions():
    """Pin coordinates are the click targets for the wiring gesture."""
    res = _api("preview", {"ir_json": _ir()})
    assert res["status"] == "ok"
    pins = res["data"]["pins"]["dp"]
    # diff_pair exposes the five formal pins in top-cell coordinates
    for name in ("inp", "inn", "outp", "outn", "tail"):
        assert name in pins
        x, y = pins[name]
        box = res["data"]["instances"]["dp"]
        assert box[0] - 1 <= x <= box[2] + 1
        assert box[1] - 1 <= y <= box[3] + 1
    assert res["data"]["nets"] == []


def test_simulate_auto_runs_or_reports_unavailable():
    """The canvas Simulate button goes through simulate_auto: real ngspice
    when present, fail-closed unavailable/refused otherwise."""
    res = _api("simulate", {"ir_json": _ir(), "analysis": "op"})
    assert res["status"] == "ok"
    assert res["data"]["status"] in ("passed", "failed", "unavailable",
                                     "refused", "error")
