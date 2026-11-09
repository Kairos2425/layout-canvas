"""Tests for tools.inspect.inspect_geometry (agent-side dimension picker)."""

from pathlib import Path

import pytest

import layout_canvas.blocks.sky130  # noqa: F401 - registers sky130 blocks
from layout_canvas.compiler.compile import compile_design
from layout_canvas.ir.model import Design
from layout_canvas.tools.inspect import inspect_geometry

_DP = "sky130.diff_pair"


def _diffamp_gds(tmp_path: Path) -> Path:
    design = Design.model_validate({
        "name": "insp_dp", "pdk": "sky130",
        "instances": [
            {"id": "X1", "block": _DP,
             "params": {"width": 4.0, "length": 0.5, "fingers": 4, "dummies": 1},
             "placement": {"x": 0.0, "y": 0.0}},
        ],
    })
    comp = compile_design(design)
    gds = tmp_path / "insp.gds"
    comp.write_gds(str(gds))
    return gds


def test_inspect_geometry_census(tmp_path):
    gds = _diffamp_gds(tmp_path)
    r = inspect_geometry(str(gds))
    assert r["status"] == "ok"
    assert r["top_cell"] == "insp_dp"
    assert r["cell_count"] >= 1
    assert r["dbu_um"] == pytest.approx(0.001)
    for info in r["layers"].values():
        assert isinstance(info["shapes"], int)
        assert info["area_um2"] >= 0.0
    assert any(i["shapes"] > 0 for i in r["layers"].values())


def test_inspect_geometry_box_query(tmp_path):
    gds = _diffamp_gds(tmp_path)
    r = inspect_geometry(str(gds), box_um=[-1000, -1000, 1000, 1000])
    assert r["status"] == "ok"
    shapes = r.get("shapes", [])
    assert shapes, "box query returned no shapes"
    for s in shapes[:20]:
        assert s["w_um"] >= 0 and s["h_um"] >= 0
        assert len(s["bbox_um"]) == 4


def test_inspect_geometry_spacing(tmp_path):
    gds = _diffamp_gds(tmp_path)
    r0 = inspect_geometry(str(gds))
    present = [tuple(map(int, k.split("/"))) for k, v in r0["layers"].items()
               if v["shapes"] > 0]
    if len(present) < 2:
        pytest.skip("need >=2 populated layers")
    r = inspect_geometry(str(gds), spacing_layers=[present[0], present[1]])
    assert r["status"] == "ok"
    sp = r["spacing"]
    if sp.get("status") == "unavailable":
        pytest.skip("spacing unavailable: " + sp.get("reason", ""))
    assert sp["min_gap_um"] is None or sp["min_gap_um"] >= 0


def test_inspect_geometry_fail_closed(tmp_path):
    r = inspect_geometry(str(tmp_path / "nope.gds"))
    assert r["status"] == "error"
