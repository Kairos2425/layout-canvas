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

    def test_cell_netlist_flattens_into_parent(self):
        """A registered cell inlines its child's whole hierarchy into the deck."""
        import re

        from layout_canvas.compiler.netlist import compile_netlist

        child = Design.model_validate(
            {
                "name": "child_flat",
                "pdk": "sky130",
                "instances": [{"id": "dp", "block": "sky130.diff_pair", "params": {}}],
                "ports": [{"name": "INP", "pin": "dp.inp", "direction": "input"}],
            }
        )
        register_design_cell("cells.flat", child)
        try:
            parent = Design.model_validate(
                {
                    "name": "parent_flat",
                    "pdk": "sky130",
                    "instances": [{"id": "u1", "block": "cells.flat", "params": {}}],
                }
            )
            spice = compile_netlist(parent)
            # Child primitives are inlined: the diff_pair variant subckt and
            # its real devices appear inside the cell variant's subckt block.
            cell_variant = re.search(r"\.subckt (cells_flat__\w+) INP", spice)
            assert cell_variant, spice
            assert "sky130_diff_pair__" in spice
            assert "sky130_fd_pr__nfet_01v8" in spice
            assert "Blackbox" not in spice
        finally:
            del base._REGISTRY["cells.flat"]

    def test_cell_flattened_real_simulation(self, tmp_path):
        """Flattened cell sim runs real ngspice when the binary is present."""
        import os
        import shutil
        from pathlib import Path

        from layout_canvas.tools.sim import simulate_design

        ngspice = os.environ.get("LAYOUT_CANVAS_NGSPICE") or shutil.which("ngspice")
        lib = Path(__file__).parent.parent / "examples" / "models" / "sky130" / "sky130_tt.lib"
        if ngspice is None or not lib.is_file():
            import pytest

            pytest.skip("ngspice or bundled sky130 TT models not present")

        child = Design.model_validate(
            {
                "name": "child_sim",
                "pdk": "sky130",
                "instances": [{"id": "dp", "block": "sky130.diff_pair", "params": {}}],
                "ports": [
                    {"name": "INP", "pin": "dp.inp", "direction": "input"},
                    {"name": "INN", "pin": "dp.inn", "direction": "input"},
                    {"name": "OUTP", "pin": "dp.outp", "direction": "output"},
                    {"name": "OUTN", "pin": "dp.outn", "direction": "output"},
                    {"name": "TAIL", "pin": "dp.tail", "direction": "inout"},
                ],
            }
        )
        register_design_cell("cells.sim", child)
        try:
            parent = Design.model_validate(
                {
                    "name": "parent_sim",
                    "pdk": "sky130",
                    "instances": [{"id": "u1", "block": "cells.sim", "params": {}}],
                    "ports": [
                        {"name": "INP", "pin": "u1.INP", "direction": "input"},
                        {"name": "INN", "pin": "u1.INN", "direction": "input"},
                        {"name": "OUTP", "pin": "u1.OUTP", "direction": "output"},
                        {"name": "OUTN", "pin": "u1.OUTN", "direction": "output"},
                        {"name": "TAIL", "pin": "u1.TAIL", "direction": "inout"},
                    ],
                }
            )
            stimulus = """
VDD vdd 0 1.8
VINP inp 0 0.95
VINN inn 0 0.9
IT tail 0 10u
RDP vdd outp 10k
RDN vdd outn 10k
Xtop inp inn outp outn tail parent_sim
.op
"""
            res = simulate_design(
                parent, stimulus, includes=[str(lib)], executable=ngspice, workdir=tmp_path
            )
            assert res.status == "passed", res.errors
        finally:
            del base._REGISTRY["cells.sim"]


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
