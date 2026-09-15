"""Tests for session-aware optimizer and reusable cell blocks."""

import gdsfactory as gf

import layout_canvas.blocks.sky130  # noqa: F401
from layout_canvas.blocks import base
from layout_canvas.blocks.cells import register_design_cell
from layout_canvas.compiler.compile import compile_design
from layout_canvas.derived.connectivity import inspect_connectivity
from layout_canvas.engine import DesignSession, optimize_session
from layout_canvas.ir.model import Design
from layout_canvas.mcp.server import LayoutCanvasMCPServer


def _two_block_design() -> Design:
    return Design.model_validate(
        {
            "name": "opt_test",
            "pdk": "sky130",
            "instances": [
                {"id": "a", "block": "sky130.diff_pair", "params": {}},
                {
                    "id": "b",
                    "block": "sky130.current_mirror",
                    "params": {},
                    "placement": {"relative_to": "a", "relation": "right_of", "margin": 0.0},
                },
            ],
        }
    )


class TestSessionOptimizer:
    def test_iterations_commit_through_transact(self):
        s = DesignSession(_two_block_design())
        env = optimize_session(s, target_aspect_ratio=99.0, min_clearance=1.0, max_iterations=2)
        assert env.ok
        assert env.data["iterations"] >= 1
        # margin edit was committed via transact → revision bumped + undoable
        assert s.revision >= 1
        assert s.design.instance("b").placement.margin == 1.0
        assert s.undo().ok
        assert s.design.instance("b").placement.margin == 0.0

    def test_no_edits_needed_stops_cleanly(self):
        s = DesignSession(_two_block_design())
        env = optimize_session(s, target_aspect_ratio=99.0, min_clearance=0.0)
        assert env.ok
        assert env.data["iterations"] == 1
        assert s.revision == 0


class TestCellBlocks:
    def test_register_and_instantiate(self):
        child = Design.model_validate(
            {
                "name": "child_cell",
                "pdk": "sky130",
                "instances": [{"id": "dp", "block": "sky130.diff_pair", "params": {}}],
                "ports": [
                    {"name": "INP", "pin": "dp.inp", "direction": "input"},
                    {"name": "OUTP", "pin": "dp.outp", "direction": "output"},
                ],
            }
        )
        register_design_cell("cells.child", child)
        try:
            block = base.get("cells.child")
            assert [p.name for p in block.spec.ports] == ["INP", "OUTP"]

            parent = Design.model_validate(
                {
                    "name": "parent_cell",
                    "pdk": "sky130",
                    "instances": [{"id": "u1", "block": "cells.child", "params": {}}],
                }
            )
            comp = compile_design(parent)
            assert len(comp.insts) == 1

            conn = inspect_connectivity(parent)
            assert conn["instances"]["u1"]["unconnected"] == ["INP", "OUTP"]
        finally:
            del base._REGISTRY["cells.child"]

    def test_cell_blackbox_in_netlist_and_refused_in_sim(self):
        from layout_canvas.compiler.netlist import compile_netlist
        from layout_canvas.tools.sim import simulate_design

        child = Design.model_validate(
            {
                "name": "child_bb",
                "pdk": "sky130",
                "instances": [{"id": "g", "block": "sky130.guard_ring", "params": {}}],
                "ports": [{"name": "TAP", "pin": "g.tap", "direction": "inout"}],
            }
        )
        register_design_cell("cells.bb", child)
        try:
            parent = Design.model_validate(
                {
                    "name": "parent_bb",
                    "pdk": "sky130",
                    "instances": [{"id": "u1", "block": "cells.bb", "params": {}}],
                }
            )
            spice = compile_netlist(parent)
            assert ".subckt cells_bb__" in spice or "Blackbox" in spice

            res = simulate_design(parent, ".op")
            assert res.status == "refused"
            assert "cells.bb" in res.unresolved_blocks
        finally:
            del base._REGISTRY["cells.bb"]


class TestMCPNewTools:
    def test_optimize_and_register_cell_via_mcp(self, tmp_path):
        server = LayoutCanvasMCPServer()
        opened = server.execute_tool(
            "open_design", {"ir_json": _two_block_design().model_dump()}
        )
        sid = opened["session_id"]

        opt = server.execute_tool(
            "optimize", {"session_id": sid, "min_clearance": 1.0, "target_aspect_ratio": 99.0}
        )
        assert opt["status"] == "ok"
        assert opt["revision"] >= 1

        saved = server.execute_tool(
            "save_project", {"session_id": sid, "path": str(tmp_path / "c.lcproj.json")}
        )
        reg = server.execute_tool(
            "register_cell", {"alias": "cells.mcp", "path": saved["path"]}
        )
        assert reg["alias"] == "cells.mcp"
        try:
            assert base.get("cells.mcp").spec.name == "cells.mcp"
        finally:
            del base._REGISTRY["cells.mcp"]
