"""Tests for the fail-closed ngspice runner."""

import gdsfactory as gf

from layout_canvas.blocks import base
from layout_canvas.blocks.base import Block
from layout_canvas.ir.model import BlockSpec, Design
from layout_canvas.tools.sim import run_netlist, simulate_design


def _design_with(block_name: str) -> Design:
    return Design.model_validate(
        {
            "name": "sim_test",
            "pdk": "sky130",
            "instances": [{"id": "x1", "block": block_name, "params": {}}],
        }
    )


def test_unresolvable_block_refused_and_named():
    base._REGISTRY["test.blackbox"] = Block(
        spec=BlockSpec(
            name="test.blackbox",
            pdk="sky130",
            level="L0",
            summary="no emitter",
            params=[],
            ports=[],
        ),
        build=lambda: gf.Component(),
        netlist=None,
    )
    try:
        result = simulate_design(_design_with("test.blackbox"), "V1 a 0 1")
        assert result.status == "refused"
        assert "test.blackbox" in result.unresolved_blocks
        assert result.log_path is None
    finally:
        del base._REGISTRY["test.blackbox"]


def test_missing_ngspice_is_unavailable():
    result = run_netlist("V1 a 0 1\n.op\n.end", executable="definitely-not-ngspice-xyz")
    assert result.status == "unavailable"
    assert "definitely-not-ngspice-xyz" in result.errors[0]


def test_error_scanner_catches_ngspice_failures(tmp_path):
    from layout_canvas.tools.sim import _find_errors

    log = "some output\nError: no such model nfet_01v8\nmore output"
    assert _find_errors(log) == ["Error: no such model nfet_01v8"]
    assert _find_errors("all good") == []


def test_deck_builder_includes_stimulus_and_end():
    from layout_canvas.tools.sim import _build_deck

    deck = _build_deck(".subckt a x\n.ends", "X1 x a\n.op", ["/models/sky130.lib"])
    assert ".include /models/sky130.lib" in deck
    assert deck.rstrip().endswith(".end")
