"""IHP SG13G2 common-centroid differential pair (L1 block)."""

from __future__ import annotations

import gdsfactory as gf

from layout_canvas.blocks.base import register
from layout_canvas.blocks.ihp_sg13g2 import layers
from layout_canvas.blocks.ihp_sg13g2.geom import add_port, rect, snap
from layout_canvas.ir.model import BlockSpec, ParamSpec, PortSpec

NMOS = "sg13_lv_nmos"
PMOS = "sg13_lv_pmos"


def register_diff_pair() -> None:
    spec = BlockSpec(
        name="ihp_sg13g2.diff_pair",
        pdk="ihp_sg13g2",
        level="L1",
        summary="Common-centroid differential pair with tail current source (sg13_lv_nmos)",
        params=[
            ParamSpec(
                name="fingers", type="int", default=4, min=2, max=32,
                description="Fingers per input transistor (ABBA pattern)",
            ),
            ParamSpec(
                name="width", type="float", default=1.0, unit="um", min=0.15, max=10.0,
                description="Input pair width per finger",
            ),
            ParamSpec(
                name="length", type="float", default=0.34, unit="um", min=0.13, max=5.0,
                description="Gate length (sg13_lv min 0.13um)",
            ),
            ParamSpec(
                name="tail_width", type="float", default=2.0, unit="um", min=0.15, max=20.0,
                description="Tail current source width",
            ),
        ],
        ports=[
            PortSpec(name="inp", layer="metal1", direction="input", tap_layer="gatpoly"),
            PortSpec(name="inn", layer="metal1", direction="input", tap_layer="gatpoly"),
            PortSpec(name="outp", layer="metal1", direction="output", tap_layer="activ"),
            PortSpec(name="outn", layer="metal1", direction="output", tap_layer="activ"),
            PortSpec(name="tail", layer="metal1", direction="input", tap_layer="activ"),
        ],
        constraints=["common_centroid_ABBA", "symmetric_vertical"],
        tags=["analog", "diff_pair", "opamp", "ihp"],
    )

    @register(spec, netlist=_netlist_diff_pair)
    def _build(fingers: int, width: float, length: float, tail_width: float) -> gf.Component:
        c = gf.Component(name=f"ihp_diff_pair_f{fingers}_w{width}_l{length}")

        finger_pitch = snap(width + 0.42)
        pattern = ["A", "B", "B", "A"] * (fingers // 2)
        total_width = finger_pitch * len(pattern)

        diff_h = snap(length + 0.6)
        rect(c, layers.ACTIV, 0, 2.0, total_width, 2.0 + diff_h)

        poly_y0 = snap(1.8)
        poly_y1 = snap(2.0 + diff_h + 0.15)
        for i, _side in enumerate(pattern):
            x = snap(i * finger_pitch + width / 2)
            rect(c, layers.GATPOLY, x - 0.065, poly_y0, x + 0.065, poly_y1)

        tail_h = snap(length + 0.6)
        tail_x0 = snap(total_width / 2 - tail_width / 2)
        tail_x1 = snap(total_width / 2 + tail_width / 2)
        rect(c, layers.ACTIV, tail_x0, 0, tail_x1, tail_h)

        add_port(c, "inp", layers.METAL1, (total_width * 0.25, 2.0 + diff_h / 2), 0.4, 180)
        add_port(c, "inn", layers.METAL1, (total_width * 0.75, 2.0 + diff_h / 2), 0.4, 0)
        add_port(c, "outp", layers.METAL1, (total_width * 0.25, 2.0 + diff_h + 0.3), 0.4, 90)
        add_port(c, "outn", layers.METAL1, (total_width * 0.75, 2.0 + diff_h + 0.3), 0.4, 90)
        add_port(c, "tail", layers.METAL1, (total_width / 2, tail_h / 2), 0.5, 180)

        return c


def _netlist_diff_pair(fingers: int, width: float, length: float, tail_width: float) -> str:
    # sg13_lv_* devices are PDK subckt wrappers (d g s b), instantiated with X.
    return f""".subckt diff_pair inp inn outp outn tail vdd vss
X1 outp inp tail vss {NMOS} w={width}u l={length}u ng={fingers} m=1
X2 outn inn tail vss {NMOS} w={width}u l={length}u ng={fingers} m=1
X3 tail tail vss vss {NMOS} w={tail_width}u l={length}u m=1
.ends"""
