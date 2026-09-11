"""Interdigitated current mirror (L0 block)."""

from __future__ import annotations

import gdsfactory as gf

from layout_canvas.blocks.base import register
from layout_canvas.blocks.sky130 import layers
from layout_canvas.blocks.sky130.geom import add_port, rect, snap
from layout_canvas.ir.model import BlockSpec, ParamSpec, PortSpec


def register_current_mirror() -> None:
    spec = BlockSpec(
        name="sky130.current_mirror",
        pdk="sky130",
        level="L0",
        summary="Interdigitated NMOS/PMOS current mirror with common-centroid layout",
        params=[
            ParamSpec(
                name="fingers",
                type="int",
                default=4,
                min=2,
                max=64,
                description="Number of interdigitated fingers",
            ),
            ParamSpec(
                name="width",
                type="float",
                default=1.0,
                unit="um",
                min=0.42,
                max=10.0,
                description="Transistor width per finger",
            ),
            ParamSpec(
                name="length",
                type="float",
                default=0.15,
                unit="um",
                min=0.15,
                max=10.0,
                description="Gate length",
            ),
            ParamSpec(
                name="type",
                type="str",
                default="nmos",
                choices=["nmos", "pmos"],
                description="Transistor type",
            ),
        ],
        ports=[
            PortSpec(name="in", layer="met1", direction="input"),
            PortSpec(name="out", layer="met1", direction="output"),
            PortSpec(name="gate", layer="poly", direction="input"),
        ],
        constraints=["common_centroid", "matched_orientation"],
        tags=["analog", "current_source"],
    )

    @register(spec, netlist=_netlist_current_mirror)
    def _build(fingers: int, width: float, length: float, type: str) -> gf.Component:
        c = gf.Component(name=f"current_mirror_f{fingers}_w{width}_l{length}_{type}")

        # Simplified interdigitated layout
        finger_pitch = snap(width + 0.5)
        total_width = finger_pitch * fingers

        # Active diffusion stripe
        diff_h = snap(length + 0.8)
        rect(c, layers.DIFF, 0, 0, total_width, diff_h)

        # Poly gates (interdigitated)
        poly_y0 = snap(-0.2)
        poly_y1 = snap(diff_h + 0.2)
        for i in range(fingers):
            x = snap(i * finger_pitch + width / 2)
            rect(c, layers.POLY, x - 0.075, poly_y0, x + 0.075, poly_y1)

        # Contacts and metal1 routing
        # (Simplified - production would add LICON, implants, well, guard rings)

        # Ports
        add_port(c, "in", layers.MET1, (total_width * 0.25, diff_h / 2), 0.5, 180)
        add_port(c, "out", layers.MET1, (total_width * 0.75, diff_h / 2), 0.5, 0)
        add_port(c, "gate", layers.POLY, (total_width / 2, poly_y0), 0.3, 270)

        return c


def _netlist_current_mirror(fingers: int, width: float, length: float, type: str) -> str:
    model = "nfet_01v8" if type == "nmos" else "pfet_01v8"
    w_total = width * fingers
    return f".subckt current_mirror in out gate\nM1 out gate in in {model} w={w_total}u l={length}u m=1\n.ends"
