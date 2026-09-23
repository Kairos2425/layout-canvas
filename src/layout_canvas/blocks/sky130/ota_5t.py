"""5-transistor OTA (L2 block - composed from L0/L1 primitives)."""

from __future__ import annotations

import gdsfactory as gf

from layout_canvas.blocks.base import register
from layout_canvas.blocks.sky130 import layers
from layout_canvas.blocks.sky130.geom import add_port, rect
from layout_canvas.ir.model import BlockSpec, ParamSpec, PortSpec


def register_ota_5t() -> None:
    spec = BlockSpec(
        name="sky130.ota_5t",
        pdk="sky130",
        level="L2",
        summary="5-transistor OTA: diff pair + active load + tail current",
        params=[
            ParamSpec(
                name="diff_fingers",
                type="int",
                default=4,
                min=2,
                max=16,
                description="Differential pair fingers",
            ),
            ParamSpec(
                name="load_fingers",
                type="int",
                default=4,
                min=2,
                max=16,
                description="Active load fingers",
            ),
            ParamSpec(
                name="width",
                type="float",
                default=2.0,
                unit="um",
                min=0.42,
                max=10.0,
                description="Transistor width",
            ),
        ],
        ports=[
            PortSpec(name="inp", layer="met1", direction="input", tap_layer="poly"),
            PortSpec(name="inn", layer="met1", direction="input", tap_layer="poly"),
            PortSpec(name="out", layer="met1", direction="output", tap_layer="diff"),
            PortSpec(name="vdd", layer="met1", direction="supply", tap_layer="diff"),
            PortSpec(name="vss", layer="met1", direction="supply", tap_layer="diff"),
        ],
        constraints=["symmetric_vertical", "compact_stacking"],
        tags=["analog", "opamp", "ota"],
    )

    @register(spec, netlist=_netlist_ota_5t)
    def _build(diff_fingers: int, load_fingers: int, width: float) -> gf.Component:
        c = gf.Component(name=f"ota_5t_d{diff_fingers}_l{load_fingers}")

        # Simplified placeholder - production would instantiate sub-blocks
        # Stack: PMOS load (top) | NMOS diff pair (middle) | tail (bottom)
        total_w = 20.0
        load_h = 5.0
        diff_h = 6.0
        tail_h = 4.0

        # Active load (PMOS)
        rect(c, layers.DIFF, 0, 15, total_w, 15 + load_h)
        rect(c, layers.NWELL, -0.5, 14.5, total_w + 0.5, 20.5)

        # Diff pair (NMOS)
        rect(c, layers.DIFF, 0, 8, total_w, 8 + diff_h)

        # Tail (NMOS)
        rect(c, layers.DIFF, 7, 0, 13, tail_h)

        # Ports
        add_port(c, "inp", layers.MET1, (5, 11), 1.0, 180)
        add_port(c, "inn", layers.MET1, (15, 11), 1.0, 0)
        add_port(c, "out", layers.MET1, (10, 17), 1.5, 90)
        add_port(c, "vdd", layers.MET1, (10, 19), 2.0, 90)
        add_port(c, "vss", layers.MET1, (10, 1), 2.0, 270)

        return c


def _netlist_ota_5t(diff_fingers: int, load_fingers: int, width: float) -> str:
    return f""".subckt ota_5t inp inn out vdd vss
* Diff pair
X1 out1 inp tail vss sky130_fd_pr__nfet_01v8 w={width}u l=0.5u nf={diff_fingers}
X2 out inn tail vss sky130_fd_pr__nfet_01v8 w={width}u l=0.5u nf={diff_fingers}
* Tail
X3 tail vbias vss vss sky130_fd_pr__nfet_01v8 w={width * 2}u l=0.5u
* Active load
X4 out1 out1 vdd vdd sky130_fd_pr__pfet_01v8 w={width}u l=0.5u nf={load_fingers}
X5 out out1 vdd vdd sky130_fd_pr__pfet_01v8 w={width}u l=0.5u nf={load_fingers}
.ends"""


register_ota_5t()
