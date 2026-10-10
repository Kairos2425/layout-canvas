"""Tests for session-aware optimizer and reusable cell blocks."""

import pytest

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


_LAB = __import__("pathlib").Path(__file__).parent.parent / "examples" / "ota_lab.json"


def _spec_opt_design(**over):
    payload = {
        "name": "spec_opt",
        "pdk": "sky130",
        "instances": [{"id": "dp", "block": "sky130.diff_pair",
                       "params": {"width": 2.0, "fingers": 4}}],
        "testbenches": [{"name": "tb", "source": "schematic", "specs": [
            {"name": "s", "signal": "outp", "min": 0}]}],
    }
    payload.update(over)
    return Design.model_validate(payload)


class TestSpecObjectiveOptimizer:
    """objective='specs': coordinate descent on bounded numeric params,
    scored by testbench spec results (fail-closed on missing benches)."""

    def test_no_testbenches_is_an_error(self):
        env = optimize_session(
            DesignSession(_spec_opt_design(testbenches=[])),
            objective="specs")
        assert env.status == "error"
        assert env.diagnostics[0].code == "no-testbenches"

    def test_unknown_testbench_is_an_error(self):
        env = optimize_session(
            DesignSession(_spec_opt_design()),
            objective="specs", testbench="nope")
        assert env.status == "error"
        assert env.diagnostics[0].code == "unknown-testbench"

    def test_bad_objective_is_an_error(self):
        env = optimize_session(
            DesignSession(_spec_opt_design()), objective="bogus")
        assert env.status == "error"
        assert env.diagnostics[0].code == "bad-objective"

    def test_coordinate_descent_mocked(self, monkeypatch):
        """Deterministic descent: width >= 2.6 flips the spec to pass."""
        import pytest

        from layout_canvas.engine import optimize as opt

        def fake_score(session, tb):
            w = float(session.design.instance("dp").params.get("width", 2.0))
            ok = w >= 2.6
            return (1.0 if ok else 0.0, -1.0), "pass" if ok else "fail"

        s = DesignSession(_spec_opt_design())
        monkeypatch.setattr(opt, "_score", fake_score)
        env = opt.optimize_session(s, objective="specs", testbench="tb")
        assert env.ok
        data = env.data
        assert data["objective"] == "specs"
        assert data["spec_status"] == "pass" and data["all_passed"] is True
        # width climbed 2.0 -> 2.66 (x1.33); losing candidates reverted
        assert s.design.instance("dp").params["width"] == pytest.approx(2.66)
        assert s.design.instance("dp").params["fingers"] == 4
        names = [h["param"] for h in data["history"] if h["param"]]
        assert "dp.width" in names and "dp.fingers" in names
        # every trial went through the revisioned boundary
        assert s.revision == len(s._history)
        # the run journal got its one optimize line
        assert s.runs[-1]["kind"] == "optimize"
        assert s.runs[-1]["objective"] == "specs"
        assert s.runs[-1]["all_passed"] is True

    def test_unavailable_sim_is_reported_not_faked(self, monkeypatch):
        """Every spec eval unavailable → honest unavailable, not a pass."""
        from layout_canvas.engine import optimize as opt

        monkeypatch.setattr(
            opt, "_score", lambda s, t: ((0.0, -1.0), "unavailable"))
        env = optimize_session(
            DesignSession(_spec_opt_design()), objective="specs",
            max_iterations=1)
        assert env.ok  # the optimizer ran; the SIM is what's unavailable
        assert env.data["spec_status"] == "unavailable"
        assert env.data["all_passed"] is False
        assert all(h["spec_status"] == "unavailable"
                   for h in env.data["history"])

    def test_area_tiebreak_picks_smaller(self, monkeypatch):
        """Equal pass counts resolve on the smaller-area candidate."""
        import pytest

        from layout_canvas.engine import optimize as opt

        def fake_score(session, tb):
            w = float(session.design.instance("dp").params.get("width", 2.0))
            return (0.0, -w), "fail"  # same pass count; -w favours small

        s = DesignSession(_spec_opt_design())
        monkeypatch.setattr(opt, "_score", fake_score)
        env = opt.optimize_session(s, objective="specs", testbench="tb",
                                 max_iterations=1)
        assert env.ok
        assert s.design.instance("dp").params["width"] == pytest.approx(1.5)


@pytest.mark.skipif(
    not __import__("os").environ.get("LAYOUT_CANVAS_NGSPICE")
    and not __import__("shutil").which("ngspice"),
    reason="ngspice not installed",
)
def test_optimize_specs_real_ngspice(tmp_path):
    """Sabotaged spec on the lab: the optimizer must really tune params."""
    import json

    payload = json.loads(_LAB.read_text())
    # keep only the schematic bench (cheap) and make outp unreachable
    payload["testbenches"] = [
        {**tb, "specs": [{"name": "outp_low", "signal": "outp",
                          "measure": "final", "max": 0.5, "unit": "V"}]}
        for tb in payload["testbenches"] if tb["name"] == "tb_op_schematic"]
    s = DesignSession(Design.model_validate(payload))
    env = optimize_session(s, objective="specs", testbench="tb_op_schematic",
                           max_iterations=1)
    assert env.ok
    tried = {h["param"] for h in env.data["history"] if h.get("param")}
    assert any(p.endswith(".width") or p.endswith(".fingers") for p in tried)
    # real sims ran and every eval was honestly scored
    evals = [h for h in env.data["history"] if h.get("param")]
    assert all(h["spec_status"] in ("fail", "pass") for h in evals)
    assert s.revision > 0
    assert s.runs[-1]["kind"] == "optimize"
    assert s.runs[-1]["spec_status"] in ("fail", "pass", "unavailable")

    # and on the passing design it stops at once with all_passed
    s2 = DesignSession(Design.model_validate(payload | {
        "testbenches": [tb for tb in json.loads(_LAB.read_text())["testbenches"]
                        if tb["name"] == "tb_op_schematic"]}))
    env2 = optimize_session(s2, objective="specs",
                            testbench="tb_op_schematic", max_iterations=3)
    assert env2.ok and env2.data["spec_status"] == "pass"
    assert env2.data["all_passed"] is True
    assert s2.runs[-1]["kind"] == "optimize"


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
