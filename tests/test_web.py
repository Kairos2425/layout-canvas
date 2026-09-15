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
