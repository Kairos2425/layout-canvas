"""IHP SG13G2 parametric guard ring (L0 block)."""

from __future__ import annotations

import gdsfactory as gf

from layout_canvas.blocks.base import register
from layout_canvas.blocks.ihp_sg13g2 import layers
from layout_canvas.blocks.ihp_sg13g2.geom import add_port, rect, snap
from layout_canvas.ir.model import BlockSpec, ParamSpec, PortSpec


def register_guard_ring() -> None:
    spec = BlockSpec(
        name="ihp_sg13g2.guard_ring",
        pdk="ihp_sg13g2",
        level="L0",
        summary="Parametric substrate/well guard ring for noise isolation",
        params=[
            ParamSpec(name="inner_width", type="float", default=10.0, unit="um",
                      min=1.0, max=500.0, description="Inner cavity width"),
            ParamSpec(name="inner_height", type="float", default=10.0, unit="um",
                      min=1.0, max=500.0, description="Inner cavity height"),
            ParamSpec(name="ring_width", type="float", default=0.6, unit="um",
                      min=0.31, max=10.0, description="Ring trace width"),
            ParamSpec(name="ring_type", type="str", default="ptap",
                      choices=["ptap", "ntap"], description="Tap type"),
        ],
        ports=[
            PortSpec(name="tap", layer="metal1", direction="inout", tap_layer="activ"),
            PortSpec(name="tap_n", layer="metal1", direction="inout", tap_layer="activ"),
            PortSpec(name="tap_s", layer="metal1", direction="inout", tap_layer="activ"),
            PortSpec(name="tap_w", layer="metal1", direction="inout", tap_layer="activ"),
            PortSpec(name="tap_e", layer="metal1", direction="inout", tap_layer="activ"),
        ],
        constraints=["guard_ring_enclosure", "continuous_ring"],
        tags=["analog", "isolation", "guard_ring", "ihp"],
    )

    @register(spec, netlist=_netlist_guard_ring)
    def _build(inner_width: float, inner_height: float, ring_width: float,
               ring_type: str) -> gf.Component:
        c = gf.Component(name=f"ihp_guard_ring_{ring_type}_{inner_width}x{inner_height}")

        w_in, h_in, rw = snap(inner_width), snap(inner_height), snap(ring_width)
        xi0, xi1 = -w_in / 2, w_in / 2
        yi0, yi1 = -h_in / 2, h_in / 2
        xo0, xo1 = xi0 - rw, xi1 + rw
        yo0, yo1 = yi0 - rw, yi1 + rw

        for x0, y0, x1, y1 in (
            (xo0, yo0, xo1, yi0),
            (xo0, yi1, xo1, yo1),
            (xo0, yi0, xi0, yi1),
            (xi1, yi0, xo1, yi1),
        ):
            rect(c, layers.ACTIV, x0, y0, x1, y1)
            rect(c, layers.METAL1, x0, y0, x1, y1)

        # implant marks the tap polarity: extraction derives ptap as
        # (activ & psdm) - nwell and ntap as (activ & nsdm) & nwell —
        # without the implant the ring is just floating diffusion.
        margin = 0.15
        implant = layers.PSDM if ring_type == "ptap" else layers.NSDM
        rect(c, implant, xo0 - margin, yo0 - margin,
             xo1 + margin, yo1 + margin)
        if ring_type == "ntap":
            nw = 0.62
            rect(c, layers.NWELL, xo0 - nw, yo0 - nw, xo1 + nw, yo1 + nw)
            rect(c, layers.ACTIV, xo0 - margin, yo0 - margin, xo1 + margin, yo1 + margin)

        # continuous contact bars inside each rail — discrete holes at a
        # pitch collide at corners and trip Cnt.b 0.18 spacing
        contact = 0.17
        half = contact / 2
        for xa, ya, xb, yb in (
            (xo0 + 0.2, yo0 + rw / 2 - half, xo1 - 0.2, yo0 + rw / 2 + half),
            (xo0 + 0.2, yo1 - rw / 2 - half, xo1 - 0.2, yo1 - rw / 2 + half),
            (xo0 + rw / 2 - half, yi0 + 0.2, xo0 + rw / 2 + half, yi1 - 0.2),
            (xo1 - rw / 2 - half, yi0 + 0.2, xo1 - rw / 2 + half, yi1 - 0.2),
        ):
            rect(c, layers.CONT, xa, ya, xb, yb)

        add_port(c, "tap", layers.METAL1, (0.0, yo0 + rw / 2), rw, 270)
        add_port(c, "tap_s", layers.METAL1, (0.0, yo0 + rw / 2), rw, 270)
        add_port(c, "tap_n", layers.METAL1, (0.0, yo1 - rw / 2), rw, 90)
        add_port(c, "tap_w", layers.METAL1, (xo0 + rw / 2, 0.0), rw, 180)
        add_port(c, "tap_e", layers.METAL1, (xo1 - rw / 2, 0.0), rw, 0)
        return c


def _netlist_guard_ring(inner_width: float, inner_height: float, ring_width: float,
                        ring_type: str) -> str:
    return f"""* IHP SG13G2 guard ring ({ring_type})
.subckt guard_ring_{ring_type} tap tap_n tap_s tap_w tap_e
.ends
"""


register_guard_ring()
