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
            PortSpec(name="tap", layer="metal1", direction="inout"),
            PortSpec(name="tap_n", layer="metal1", direction="inout"),
            PortSpec(name="tap_s", layer="metal1", direction="inout"),
            PortSpec(name="tap_w", layer="metal1", direction="inout"),
            PortSpec(name="tap_e", layer="metal1", direction="inout"),
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

        margin = 0.15
        if ring_type == "ntap":
            nw = 0.62
            rect(c, layers.NWELL, xo0 - nw, yo0 - nw, xo1 + nw, yo1 + nw)
            rect(c, layers.ACTIV, xo0 - margin, yo0 - margin, xo1 + margin, yo1 + margin)

        contact = 0.16
        pitch = 0.3
        inset = (rw - contact) / 2

        def contacts_h(x0: float, x1: float, yc: float) -> None:
            x = x0 + pitch / 2
            while x + contact <= x1:
                rect(c, layers.CONT, x, yc - contact / 2, x + contact, yc + contact / 2)
                x += pitch

        def contacts_v(y0: float, y1: float, xc: float) -> None:
            y = y0 + pitch / 2
            while y + contact <= y1:
                rect(c, layers.CONT, xc - contact / 2, y, xc + contact / 2, y + contact)
                y += pitch

        contacts_h(xo0 + inset, xo1 - inset, yo0 + rw / 2)
        contacts_h(xo0 + inset, xo1 - inset, yo1 - rw / 2)
        contacts_v(yi0, yi1, xo0 + rw / 2)
        contacts_v(yi0, yi1, xo1 - rw / 2)

        add_port(c, "tap", layers.METAL1, (0.0, yo0 + rw / 2), rw, 270)
        add_port(c, "tap_s", layers.METAL1, (0.0, yo0 + rw / 2), rw, 270)
        add_port(c, "tap_n", layers.METAL1, (0.0, yo1 - rw / 2), rw, 90)
        add_port(c, "tap_w", layers.METAL1, (xo0 + rw / 2, 0.0), rw, 180)
        add_port(c, "tap_e", layers.METAL1, (xo1 - rw / 2, 0.0), rw, 0)
        return c


def _netlist_guard_ring(inner_width: float, inner_height: float, ring_width: float,
                        ring_type: str) -> str:
    port = "vss" if ring_type == "ptap" else "vdd"
    return f"""* IHP SG13G2 guard ring ({ring_type})
.subckt guard_ring_{ring_type} tap
Rtap tap {port} 0.001
.ends
"""


register_guard_ring()
