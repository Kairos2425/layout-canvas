"""Sky130 Parametric Substrate and Well Guard Ring Generator.

Provides latchup prevention, noise isolation, and reliable substrate/well
biasing with DRC-clean TAP, DIFF, LICON, LI, and MET1 enclosures.
"""

from __future__ import annotations

from typing import Any

import gdsfactory as gf

from layout_canvas.blocks.base import get, register
from layout_canvas.blocks.sky130 import layers
from layout_canvas.blocks.sky130.geom import add_port, rect, snap
from layout_canvas.ir.model import BlockSpec, ParamSpec, PortSpec


def build_guard_ring(
    inner_width: float = 10.0,
    inner_height: float = 10.0,
    ring_width: float = 0.8,
    ring_type: str = "ptap",
) -> gf.Component:
    """Standalone builder function for guard rings."""
    block = get("sky130.guard_ring")
    return block.component(
        inner_width=inner_width,
        inner_height=inner_height,
        ring_width=ring_width,
        ring_type=ring_type,
    )


def register_guard_ring() -> None:
    spec = BlockSpec(
        name="sky130.guard_ring",
        pdk="sky130",
        level="L0",
        summary="Parametric P+/N+ substrate guard ring for noise isolation and latchup prevention",
        params=[
            ParamSpec(
                name="inner_width",
                type="float",
                default=10.0,
                unit="um",
                min=1.0,
                max=500.0,
                description="Inner enclosed cavity width in micrometers",
            ),
            ParamSpec(
                name="inner_height",
                type="float",
                default=10.0,
                unit="um",
                min=1.0,
                max=500.0,
                description="Inner enclosed cavity height in micrometers",
            ),
            ParamSpec(
                name="ring_width",
                type="float",
                default=0.8,
                unit="um",
                min=0.48,
                max=10.0,
                description="Width of the guard ring diffusion/metal trace",
            ),
            ParamSpec(
                name="ring_type",
                type="str",
                default="ptap",
                choices=["ptap", "ntap"],
                description="Substrate tap type: 'ptap' (P+ substrate tap) or 'ntap' (N+ N-well tap)",
            ),
        ],
        ports=[
            PortSpec(name="tap", layer="met1", direction="inout", tap_layer="tap"),
            PortSpec(name="tap_n", layer="met1", direction="inout", tap_layer="tap"),
            PortSpec(name="tap_s", layer="met1", direction="inout", tap_layer="tap"),
            PortSpec(name="tap_w", layer="met1", direction="inout", tap_layer="tap"),
            PortSpec(name="tap_e", layer="met1", direction="inout", tap_layer="tap"),
        ],
        constraints=["guard_ring_enclosure", "continuous_ring"],
        tags=["analog", "isolation", "guard_ring", "substrate"],
    )

    @register(spec, netlist=_netlist_guard_ring)
    def _build(
        inner_width: float,
        inner_height: float,
        ring_width: float,
        ring_type: str,
    ) -> gf.Component:
        c = gf.Component(name=f"guard_ring_{ring_type}_{inner_width}x{inner_height}")

        w_in = snap(inner_width)
        h_in = snap(inner_height)
        rw = snap(ring_width)

        # Outer dimensions centered around origin (0, 0)
        x_inner_min = -w_in / 2
        x_inner_max = w_in / 2
        y_inner_min = -h_in / 2
        y_inner_max = h_in / 2

        x_outer_min = x_inner_min - rw
        x_outer_max = x_inner_max + rw
        y_outer_min = y_inner_min - rw
        y_outer_max = y_inner_max + rw

        total_w = x_outer_max - x_outer_min
        total_h = y_outer_max - y_outer_min

        # 4 Rectangular Segments (Bottom, Top, Left, Right) forming a closed loop
        # Bottom segment
        rect(c, layers.DIFF, x_outer_min, y_outer_min, x_outer_max, y_inner_min)
        rect(c, layers.TAP, x_outer_min, y_outer_min, x_outer_max, y_inner_min)
        rect(c, layers.LI, x_outer_min, y_outer_min, x_outer_max, y_inner_min)
        rect(c, layers.MET1, x_outer_min, y_outer_min, x_outer_max, y_inner_min)

        # Top segment
        rect(c, layers.DIFF, x_outer_min, y_inner_max, x_outer_max, y_outer_max)
        rect(c, layers.TAP, x_outer_min, y_inner_max, x_outer_max, y_outer_max)
        rect(c, layers.LI, x_outer_min, y_inner_max, x_outer_max, y_outer_max)
        rect(c, layers.MET1, x_outer_min, y_inner_max, x_outer_max, y_outer_max)

        # Left segment
        rect(c, layers.DIFF, x_outer_min, y_inner_min, x_inner_min, y_inner_max)
        rect(c, layers.TAP, x_outer_min, y_inner_min, x_inner_min, y_inner_max)
        rect(c, layers.LI, x_outer_min, y_inner_min, x_inner_min, y_inner_max)
        rect(c, layers.MET1, x_outer_min, y_inner_min, x_inner_min, y_inner_max)

        # Right segment
        rect(c, layers.DIFF, x_inner_max, y_inner_min, x_outer_max, y_inner_max)
        rect(c, layers.TAP, x_inner_max, y_inner_min, x_outer_max, y_inner_max)
        rect(c, layers.LI, x_inner_max, y_inner_min, x_outer_max, y_inner_max)
        rect(c, layers.MET1, x_inner_max, y_inner_min, x_outer_max, y_inner_max)

        # Implants and Wells
        implant_margin = 0.12
        if ring_type == "ptap":
            # P+ implant over tap for P-substrate contact
            rect(
                c,
                layers.PSDM,
                x_outer_min - implant_margin,
                y_outer_min - implant_margin,
                x_outer_max + implant_margin,
                y_outer_max + implant_margin,
            )
        else:
            # N+ implant + NWELL for N-well tap
            nwell_margin = 0.35
            rect(
                c,
                layers.NSDM,
                x_outer_min - implant_margin,
                y_outer_min - implant_margin,
                x_outer_max + implant_margin,
                y_outer_max + implant_margin,
            )
            rect(
                c,
                layers.NWELL,
                x_outer_min - nwell_margin,
                y_outer_min - nwell_margin,
                x_outer_max + nwell_margin,
                y_outer_max + nwell_margin,
            )

        # Regular contact array (LICON + MCON) along each segment
        contact_size = 0.17
        contact_pitch = 0.36
        inset = (rw - contact_size) / 2

        def place_contacts_h(x0: float, x1: float, y_center: float) -> None:
            curr_x = x0 + contact_pitch / 2
            while curr_x + contact_size <= x1:
                rect(
                    c,
                    layers.LICON,
                    curr_x,
                    y_center - contact_size / 2,
                    curr_x + contact_size,
                    y_center + contact_size / 2,
                )
                rect(
                    c,
                    layers.MCON,
                    curr_x,
                    y_center - contact_size / 2,
                    curr_x + contact_size,
                    y_center + contact_size / 2,
                )
                curr_x += contact_pitch

        def place_contacts_v(y0: float, y1: float, x_center: float) -> None:
            curr_y = y0 + contact_pitch / 2
            while curr_y + contact_size <= y1:
                rect(
                    c,
                    layers.LICON,
                    x_center - contact_size / 2,
                    curr_y,
                    x_center + contact_size / 2,
                    curr_y + contact_size,
                )
                rect(
                    c,
                    layers.MCON,
                    x_center - contact_size / 2,
                    curr_y,
                    x_center + contact_size / 2,
                    curr_y + contact_size,
                )
                curr_y += contact_pitch

        # Contact lines in bottom, top, left, right bars
        place_contacts_h(x_outer_min, x_outer_max, y_outer_min + rw / 2)
        place_contacts_h(x_outer_min, x_outer_max, y_outer_max - rw / 2)
        place_contacts_v(y_inner_min, y_inner_max, x_outer_min + rw / 2)
        place_contacts_v(y_inner_min, y_inner_max, x_outer_max - rw / 2)

        # Expose ports along all 4 cardinal directions and a center default
        add_port(c, "tap", layers.MET1, (0.0, y_outer_min + rw / 2), rw, 270)
        add_port(c, "tap_s", layers.MET1, (0.0, y_outer_min + rw / 2), rw, 270)
        add_port(c, "tap_n", layers.MET1, (0.0, y_outer_max - rw / 2), rw, 90)
        add_port(c, "tap_w", layers.MET1, (x_outer_min + rw / 2, 0.0), rw, 180)
        add_port(c, "tap_e", layers.MET1, (x_outer_max - rw / 2, 0.0), rw, 0)

        return c


def _netlist_guard_ring(
    inner_width: float,
    inner_height: float,
    ring_width: float,
    ring_type: str,
) -> str:
    return f"""* Sky130 Guard Ring ({ring_type})
.subckt guard_ring_{ring_type} tap tap_n tap_s tap_w tap_e
.ends
"""
