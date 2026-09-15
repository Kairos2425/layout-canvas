"""Common-centroid differential pair (L1 block)."""

from __future__ import annotations

import gdsfactory as gf

from layout_canvas.blocks.base import register
from layout_canvas.blocks.sky130 import layers
from layout_canvas.blocks.sky130.geom import add_port, rect, snap
from layout_canvas.ir.model import BlockSpec, ParamSpec, PortSpec


def register_diff_pair() -> None:
    spec = BlockSpec(
        name="sky130.diff_pair",
        pdk="sky130",
        level="L1",
        summary="Common-centroid differential pair with tail current source",
        params=[
            ParamSpec(
                name="fingers",
                type="int",
                default=4,
                min=2,
                max=32,
                description="Fingers per input transistor (ABBA pattern)",
            ),
            ParamSpec(
                name="width",
                type="float",
                default=2.0,
                unit="um",
                min=0.42,
                max=10.0,
                description="Input pair width per finger",
            ),
            ParamSpec(
                name="length",
                type="float",
                default=0.5,
                unit="um",
                min=0.15,
                max=5.0,
                description="Gate length",
            ),
            ParamSpec(
                name="tail_width",
                type="float",
                default=4.0,
                unit="um",
                min=1.0,
                max=20.0,
                description="Tail current source width",
            ),
        ],
        ports=[
            PortSpec(name="inp", layer="met1", direction="input"),
            PortSpec(name="inn", layer="met1", direction="input"),
            PortSpec(name="outp", layer="met1", direction="output"),
            PortSpec(name="outn", layer="met1", direction="output"),
            PortSpec(name="tail", layer="met1", direction="input"),
        ],
        constraints=["common_centroid_ABBA", "symmetric_vertical"],
        tags=["analog", "diff_pair", "opamp"],
    )

    @register(spec, netlist=_netlist_diff_pair)
    def _build(fingers: int, width: float, length: float, tail_width: float) -> gf.Component:
        c = gf.Component(name=f"diff_pair_f{fingers}_w{width}_l{length}")

        # ABBA interdigitation for common-centroid
        finger_pitch = snap(width + 0.6)
        pattern = ["A", "B", "B", "A"] * (fingers // 2)
        total_width = finger_pitch * len(pattern)

        # Input pair active region
        diff_h = snap(length + 1.0)
        rect(c, layers.DIFF, 0, 3, total_width, 3 + diff_h)
        rect(c, layers.NSDM, -0.1, 2.9, total_width + 0.1, 3 + diff_h + 0.1)

        # Poly gates
        poly_y0 = snap(2.8)
        poly_y1 = snap(3 + diff_h + 0.2)
        for i, side in enumerate(pattern):
            x = snap(i * finger_pitch + width / 2)
            rect(c, layers.POLY, x - 0.075, poly_y0, x + 0.075, poly_y1)

        # Tail current source (centered below)
        tail_h = snap(length + 1.0)
        tail_x0 = snap(total_width / 2 - tail_width / 2)
        tail_x1 = snap(total_width / 2 + tail_width / 2)
        rect(c, layers.DIFF, tail_x0, 0, tail_x1, tail_h)

        # Ports
        add_port(c, "inp", layers.MET1, (total_width * 0.25, 3 + diff_h / 2), 0.8, 180)
        add_port(c, "inn", layers.MET1, (total_width * 0.75, 3 + diff_h / 2), 0.8, 0)
        add_port(c, "outp", layers.MET1, (total_width * 0.25, 3 + diff_h + 0.5), 0.8, 90)
        add_port(c, "outn", layers.MET1, (total_width * 0.75, 3 + diff_h + 0.5), 0.8, 90)
        add_port(c, "tail", layers.MET1, (total_width / 2, tail_h / 2), 1.0, 180)

        return c


def _netlist_diff_pair(fingers: int, width: float, length: float, tail_width: float) -> str:
    # Canonical SkyWater cell: X-instantiated sky130_fd_pr__nfet_01v8 subckt.
    return f""".subckt diff_pair inp inn outp outn tail vdd vss
X1 outp inp tail vss sky130_fd_pr__nfet_01v8 w={width}u l={length}u nf={fingers}
X2 outn inn tail vss sky130_fd_pr__nfet_01v8 w={width}u l={length}u nf={fingers}
X3 tail tail vss vss sky130_fd_pr__nfet_01v8 w={tail_width}u l={length}u
.ends"""
