"""IHP SG13G2 interdigitated current mirror (L0 block)."""

from __future__ import annotations

import gdsfactory as gf

from layout_canvas.blocks.base import register
from layout_canvas.blocks.ihp_sg13g2 import layers
from layout_canvas.blocks.ihp_sg13g2.geom import add_port, rect, snap
from layout_canvas.ir.model import BlockSpec, ParamSpec, PortSpec

NMOS = "sg13_lv_nmos"
PMOS = "sg13_lv_pmos"


def register_current_mirror() -> None:
    spec = BlockSpec(
        name="ihp_sg13g2.current_mirror",
        pdk="ihp_sg13g2",
        level="L0",
        summary="Interdigitated NMOS/PMOS current mirror (sg13_lv_*)",
        params=[
            ParamSpec(name="fingers", type="int", default=4, min=2, max=64,
                      description="Number of interdigitated fingers"),
            ParamSpec(name="width", type="float", default=0.5, unit="um", min=0.15, max=10.0,
                      description="Transistor width per finger"),
            ParamSpec(name="length", type="float", default=0.13, unit="um", min=0.13, max=10.0,
                      description="Gate length"),
            ParamSpec(name="type", type="str", default="nmos", choices=["nmos", "pmos"],
                      description="Transistor type"),
        ],
        ports=[
            PortSpec(name="in", layer="metal1", direction="input"),
            PortSpec(name="out", layer="metal1", direction="output"),
            PortSpec(name="gate", layer="gatpoly", direction="input"),
        ],
        constraints=["common_centroid", "matched_orientation"],
        tags=["analog", "current_source", "ihp"],
    )

    @register(spec, netlist=_netlist_current_mirror)
    def _build(fingers: int, width: float, length: float, type: str) -> gf.Component:
        c = gf.Component(name=f"ihp_current_mirror_f{fingers}_w{width}_l{length}_{type}")

        finger_pitch = snap(width + 0.35)
        total_width = finger_pitch * fingers
        diff_h = snap(length + 0.5)

        rect(c, layers.ACTIV, 0, 0, total_width, diff_h)
        if type == "pmos":
            rect(c, layers.NWELL, -0.31, -0.31, total_width + 0.31, diff_h + 0.31)

        poly_y0 = snap(-0.15)
        poly_y1 = snap(diff_h + 0.15)
        for i in range(fingers):
            x = snap(i * finger_pitch + width / 2)
            rect(c, layers.GATPOLY, x - 0.065, poly_y0, x + 0.065, poly_y1)

        add_port(c, "in", layers.METAL1, (total_width * 0.25, diff_h / 2), 0.4, 180)
        add_port(c, "out", layers.METAL1, (total_width * 0.75, diff_h / 2), 0.4, 0)
        add_port(c, "gate", layers.GATPOLY, (total_width / 2, poly_y0), 0.13, 270)

        return c


def _netlist_current_mirror(fingers: int, width: float, length: float, type: str) -> str:
    model = NMOS if type == "nmos" else PMOS
    bulk = "vss" if type == "nmos" else "vdd"
    return (
        f".subckt current_mirror in out gate {bulk}\n"
        f"X1 out gate in {bulk} {model} w={width}u l={length}u ng={fingers} m=1\n"
        f".ends"
    )
