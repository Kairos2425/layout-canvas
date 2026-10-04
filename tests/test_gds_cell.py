"""External GDS import -> instantiable cell block (round-trip).

The core evidence: compile a design, stream it out to GDS, re-import it
through ``register_gds_cell`` and prove the imported cell still verifies
(LVS match, zero DRC) inside a parent design. Without a SPICE netlist the
block is layout-only and must stay fail-closed.
"""

from pathlib import Path

import pytest

import layout_canvas.blocks.sky130  # noqa: F401
from layout_canvas.blocks import base
from layout_canvas.blocks.gds_cell import register_gds_cell
from layout_canvas.compiler.compile import compile_design
from layout_canvas.compiler.netlist import compile_netlist
from layout_canvas.ir.model import Design
from layout_canvas.mcp.server import LayoutCanvasMCPServer
from layout_canvas.tools.sim import simulate_design

pytest.importorskip("klayout.db", reason="klayout python module required")


def _child_design(pdk: str = "sky130", block: str = "sky130.diff_pair") -> Design:
    pins = ["inp", "inn", "outp", "outn", "tail", "vss"]
    return Design.model_validate({
        "name": f"src_dp_{pdk.replace('ihp_sg13g2', 'ihp')}",
        "pdk": pdk,
        "instances": [
            {"id": "dp", "block": block,
             "params": {"fingers": 2, "width": 1.0}},
        ],
        "ports": [{"name": n, "pin": f"dp.{n}", "direction": "inout"}
                  for n in pins],
    })


def _write_gds(design: Design, tmp_path: Path) -> Path:
    comp = compile_design(design)
    gds = tmp_path / f"{design.name}.gds"
    comp.write_gds(str(gds))
    return gds


def _parent_design() -> Design:
    return Design.model_validate({
        "name": "parent_ext",
        "pdk": "sky130",
        "instances": [
            {"id": "u1", "block": "ext.dp", "params": {}},
            {"id": "u2", "block": "sky130.current_mirror",
             "params": {"fingers": 2},
             "placement": {"relative_to": "u1", "relation": "right_of",
                           "margin": 1.0}},
        ],
        "nets": [
            {"name": "tail", "pins": ["u1.tail", "u2.in"]},
            {"name": "vss", "pins": ["u1.vss", "u2.vss"]},
        ],
        "ports": [
            {"name": "INP", "pin": "u1.inp", "direction": "input"},
            {"name": "OUTP", "pin": "u1.outp", "direction": "output"},
        ],
    })


@pytest.fixture()
def ext_dp(tmp_path):
    child = _child_design()
    gds = _write_gds(child, tmp_path)
    spice = compile_netlist(child)
    yield child, gds, spice
    base._REGISTRY.pop("ext.dp", None)
    base._REGISTRY.pop("ext.dp_layout", None)


def test_roundtrip_ports_and_verify(ext_dp):
    child, gds, spice = ext_dp
    block = register_gds_cell("ext.dp", gds, "sky130", spice_text=spice)
    assert {p.name for p in block.spec.ports} == {p.name for p in child.ports}
    assert block.netlist is not None

    parent = _parent_design()
    comp = compile_design(parent)
    assert len(comp.insts) == 2

    from layout_canvas.tools.verify import verify_design

    res = verify_design(parent)
    assert res["drc"]["total_violations"] == 0
    assert res["lvs"]["status"] == "passed", res["lvs"].get("errors")
    assert res["lvs"]["match"] is True
    assert res["passed"] is True


def test_layout_only_fails_closed(ext_dp):
    child, gds, _spice = ext_dp
    block = register_gds_cell("ext.dp_layout", gds, "sky130")
    assert block.netlist is None

    parent = Design.model_validate({
        "name": "parent_lo",
        "pdk": "sky130",
        "instances": [{"id": "u1", "block": "ext.dp_layout", "params": {}}],
    })
    res = simulate_design(parent, stimulus="X1 parent_lo\n.op")
    assert res.status == "refused"
    assert "ext.dp_layout" in res.unresolved_blocks

    from layout_canvas.tools.verify import verify_design

    ver = verify_design(parent)
    assert ver["lvs"]["status"] != "passed"
    assert ver["lvs"]["match"] is not True
    assert ver["lvs"]["errors"]
    assert ver["passed"] is False


def test_ihp_roundtrip_lvs(tmp_path):
    import layout_canvas.blocks.ihp_sg13g2  # noqa: F401

    child = _child_design(pdk="ihp_sg13g2", block="ihp_sg13g2.diff_pair")
    gds = _write_gds(child, tmp_path)
    spice = compile_netlist(child)
    alias = "ext.ihp_dp"
    try:
        block = register_gds_cell(alias, gds, "ihp_sg13g2", spice_text=spice)
        assert {p.name for p in block.spec.ports} == {p.name for p in child.ports}
        parent = Design.model_validate({
            "name": "parent_ihp",
            "pdk": "ihp_sg13g2",
            "instances": [{"id": "u1", "block": alias, "params": {}}],
        })
        from layout_canvas.tools.verify import verify_design

        res = verify_design(parent)
        assert res["lvs"]["status"] == "passed", res["lvs"].get("errors")
        assert res["lvs"]["match"] is True
    finally:
        base._REGISTRY.pop(alias, None)


def test_multiple_tops_requires_cell_name(tmp_path):
    import klayout.db as db

    ly = db.Layout()
    ly.dbu = 0.001
    li = ly.layer(68, 16)
    for name in ("top_a", "top_b"):
        c = ly.create_cell(name)
        c.shapes(li).insert(db.Text("P", db.Trans(0, 0)))
    gds = tmp_path / "two_tops.gds"
    ly.write(str(gds))

    with pytest.raises(ValueError, match="multiple top cells"):
        register_gds_cell("ext.multi", gds, "sky130")
    with pytest.raises(ValueError, match="not found"):
        register_gds_cell("ext.multi2", gds, "sky130", cell_name="nope")
    block = register_gds_cell("ext.multi3", gds, "sky130", cell_name="top_a")
    try:
        assert [p.name for p in block.spec.ports] == ["P"]
    finally:
        base._REGISTRY.pop("ext.multi3", None)


def test_spice_pin_count_mismatch(tmp_path):
    child = _child_design()
    gds = _write_gds(child, tmp_path)
    bad = ".subckt src_dp_sky130 inp inn\n.ends\n"
    with pytest.raises(ValueError, match="pins"):
        register_gds_cell("ext.bad", gds, "sky130", spice_text=bad)
    with pytest.raises(ValueError, match="subckt"):
        register_gds_cell("ext.bad2", gds, "sky130",
                          spice_text=".subckt other inp inn\n.ends\n")


def test_mcp_import_gds(tmp_path):
    child = _child_design()
    gds = _write_gds(child, tmp_path)
    sp = tmp_path / "cell.cir"
    sp.write_text(compile_netlist(child), encoding="utf-8")

    server = LayoutCanvasMCPServer()
    tools = {t["name"] for t in server.get_tool_definitions()}
    assert "import_gds" in tools
    try:
        res = server.execute_tool("import_gds", {
            "alias": "ext.mcp_dp",
            "path": str(gds),
            "pdk": "sky130",
            "spice_path": str(sp),
        })
        assert res["alias"] == "ext.mcp_dp"
        assert res["cell"] == child.name
        assert {p["name"] for p in res["ports"]} == {
            p.name for p in child.ports}
        assert res["bbox"][2] > res["bbox"][0]
        assert base.get("ext.mcp_dp").netlist is not None
    finally:
        base._REGISTRY.pop("ext.mcp_dp", None)
