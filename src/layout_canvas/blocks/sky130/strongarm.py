"""StrongARM latch comparator (L2 block)."""

from __future__ import annotations

import gdsfactory as gf

from layout_canvas.blocks.base import register
from layout_canvas.blocks.sky130 import layers
from layout_canvas.blocks.sky130.geom import add_port, rect
from layout_canvas.ir.model import BlockSpec, ParamSpec, PortSpec


def register_strongarm() -> None:
    spec = BlockSpec(
        name="sky130.strongarm",
        pdk="sky130",
        level="L2",
        summary="StrongARM latch-based comparator with reset",
        params=[
            ParamSpec(
                name="fingers",
                type="int",
                default=4,
                min=2,
                max=16,
                description="Input pair fingers",
            ),
            ParamSpec(
                name="width",
                type="float",
                default=1.5,
                unit="um",
                min=0.42,
                max=5.0,
                description="Transistor width",
            ),
        ],
        ports=[
            PortSpec(name="inp", layer="met1", direction="input", tap_layer="poly"),
            PortSpec(name="inn", layer="met1", direction="input", tap_layer="poly"),
            PortSpec(name="outp", layer="met1", direction="output", tap_layer="diff"),
            PortSpec(name="outn", layer="met1", direction="output", tap_layer="diff"),
            PortSpec(name="clk", layer="met1", direction="input", tap_layer="poly"),
            PortSpec(name="vdd", layer="met1", direction="supply", tap_layer="diff"),
        ],
        constraints=["symmetric_vertical", "cross_coupled_matching"],
        tags=["comparator", "strongarm", "digital"],
    )

    @register(spec, netlist=_netlist_strongarm)
    def _build(fingers: int, width: float) -> gf.Component:
        c = gf.Component(name=f"strongarm_f{fingers}_w{width}")

        # Simplified placeholder - production would use hierarchical composition
        total_w = 25.0

        # Cross-coupled latch (PMOS top)
        rect(c, layers.DIFF, 2, 12, 12, 18)
        rect(c, layers.DIFF, 13, 12, 23, 18)
        rect(c, layers.NWELL, 1, 11, 24, 19)

        # Input pair (NMOS middle)
        rect(c, layers.DIFF, 5, 6, 20, 11)

        # Reset/tail switches (NMOS bottom)
        rect(c, layers.DIFF, 8, 0, 17, 5)

        # Ports
        add_port(c, "inp", layers.MET1, (8, 8.5), 1.0, 180)
        add_port(c, "inn", layers.MET1, (17, 8.5), 1.0, 0)
        add_port(c, "outp", layers.MET1, (7, 15), 1.0, 90)
        add_port(c, "outn", layers.MET1, (18, 15), 1.0, 90)
        add_port(c, "clk", layers.MET1, (12.5, 2.5), 1.5, 270)
        add_port(c, "vdd", layers.MET1, (12.5, 17), 2.0, 90)

        return c


def _netlist_strongarm(fingers: int, width: float) -> str:
    return f""".subckt strongarm inp inn outp outn clk vdd
* Input pair
X1 d1 inp tail vss sky130_fd_pr__nfet_01v8 w={width}u l=0.15u nf={fingers}
X2 d2 inn tail vss sky130_fd_pr__nfet_01v8 w={width}u l=0.15u nf={fingers}
* Tail switch
X3 tail clk vss vss sky130_fd_pr__nfet_01v8 w={width * 2}u l=0.15u
* Cross-coupled latch
X4 outn outp vdd vdd sky130_fd_pr__pfet_01v8 w={width}u l=0.15u nf={fingers}
X5 outp outn vdd vdd sky130_fd_pr__pfet_01v8 w={width}u l=0.15u nf={fingers}
X6 outn d1 vss vss sky130_fd_pr__nfet_01v8 w={width}u l=0.15u nf={fingers}
X7 outp d2 vss vss sky130_fd_pr__nfet_01v8 w={width}u l=0.15u nf={fingers}
.ends"""


register_strongarm()
