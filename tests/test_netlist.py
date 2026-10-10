"""Tests for Block IR to SPICE netlist compiler."""

from __future__ import annotations

from layout_canvas.compiler.netlist import compile_netlist, export_spice
from layout_canvas.ir.model import Design, Instance, Net, Port


def test_compile_netlist_basic(tmp_path):
    design = Design(
        name="test_top",
        pdk="sky130",
        instances=[
            Instance(id="X1", block="sky130.diff_pair", params={"fingers": 4}),
            Instance(id="X2", block="sky130.current_mirror", params={"fingers": 4, "type": "nmos"}),
        ],
        ports=[
            Port(name="VDD", direction="inout", pin="X1.tail"),
            Port(name="OUTP", direction="output", pin="X1.outp"),
        ],
        nets=[
            Net(name="net_tail", pins=["X1.tail", "X2.in"]),
        ],
    )

    spice_str = compile_netlist(design)
    assert ".subckt test_top" in spice_str
    assert "XX1" in spice_str
    assert "XX2" in spice_str
    assert "diff_pair" in spice_str
    assert "current_mirror" in spice_str

    out_file = tmp_path / "top.spice"
    export_spice(design, str(out_file))
    assert out_file.exists()
    assert ".ends test_top" in out_file.read_text(encoding="utf-8")
