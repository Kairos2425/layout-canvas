"""Unit capacitor array with common-centroid layout (L1 block)."""

from __future__ import annotations

import gdsfactory as gf

from layout_canvas.blocks.base import register
from layout_canvas.blocks.sky130 import layers
from layout_canvas.blocks.sky130.geom import add_port, centered_positions, rect, snap, square
from layout_canvas.ir.model import BlockSpec, ParamSpec, PortSpec

# sky130 MIM marker layer (capm) — defines the capacitor area seen by
# extraction; the reference netlist uses the same area so C matches.
CAPM = (89, 44)
MIM_FF_PER_UM2 = 2.0      # sky130 met2/met3 MIM density
TOP_PLATE_RATIO = 0.85    # met3 top plate vs met2 bottom plate


def register_cap_array() -> None:
    spec = BlockSpec(
        name="sky130.cap_array",
        pdk="sky130",
        level="L1",
        summary="MIM capacitor array with common-centroid layout for ratio matching",
        params=[
            ParamSpec(
                name="unit_size",
                type="float",
                default=10.0,
                unit="um",
                min=5.0,
                max=50.0,
                description="Unit capacitor size (square)",
            ),
            ParamSpec(
                name="rows",
                type="int",
                default=4,
                min=2,
                max=16,
                description="Number of rows",
            ),
            ParamSpec(
                name="cols",
                type="int",
                default=4,
                min=2,
                max=16,
                description="Number of columns",
            ),
            ParamSpec(
                name="spacing",
                type="float",
                default=2.0,
                unit="um",
                min=1.0,
                max=10.0,
                description="Spacing between units",
            ),
        ],
        ports=[
            PortSpec(name="top", layer="met3", direction="inout", tap_layer="met3"),
            PortSpec(name="bot", layer="met2", direction="inout", tap_layer="met2"),
        ],
        constraints=["common_centroid_grid", "symmetric_xy"],
        tags=["passive", "capacitor", "matching"],
    )

    @register(spec, netlist=_netlist_cap_array)
    def _build(unit_size: float, rows: int, cols: int, spacing: float) -> gf.Component:
        c = gf.Component(name=f"cap_array_{rows}x{cols}_u{unit_size}")

        pitch = snap(unit_size + spacing)
        total_w = snap(cols * pitch - spacing)
        total_h = snap(rows * pitch - spacing)
        top_w = snap(unit_size * TOP_PLATE_RATIO)
        bus_w = 0.6

        x_pos = centered_positions(total_w / 2, cols, pitch)
        y_pos = centered_positions(total_h / 2, rows, pitch)

        # Unit capacitors: met2 bottom plate, capm marker covering the
        # bottom plate (the extractor measures C from this region — the
        # reference formula uses the same area), met3 top plate.
        for y in y_pos:
            for x in x_pos:
                square(c, layers.MET2, x, y, unit_size)
                square(c, CAPM, x, y, unit_size)
                square(c, layers.MET3, x, y, top_w)

        # Bottom-plate bus: a met2 strip through each row centre overlaps
        # every bottom plate in the row; a met2 rail on the left joins
        # all row strips -> 'bot'.
        rail_x0, rail_x1 = -1.6, -1.0
        for y in y_pos:
            rect(c, layers.MET2, rail_x1, y - bus_w / 2,
                 x_pos[-1] + unit_size / 2, y + bus_w / 2)
        rect(c, layers.MET2, rail_x0, y_pos[0] - bus_w / 2,
             rail_x1, y_pos[-1] + bus_w / 2)

        # Top-plate bus: a met3 strip through each column centre overlaps
        # every top plate in the column; a met3 rail on top joins all
        # column strips -> 'top'.
        rail_y0, rail_y1 = total_h + 1.0, total_h + 1.6
        for x in x_pos:
            rect(c, layers.MET3, x - bus_w / 2, y_pos[0] - top_w / 2,
                 x + bus_w / 2, rail_y1)
        rect(c, layers.MET3, x_pos[0] - top_w / 2, rail_y0,
             x_pos[-1] + top_w / 2, rail_y1)

        # Ports + in-cell pads (labels only attach to same-cell metal).
        add_port(c, "bot", layers.MET2,
                 ((rail_x0 + rail_x1) / 2, y_pos[0]), 0.8, 270)
        add_port(c, "top", layers.MET3,
                 (x_pos[0], (rail_y0 + rail_y1) / 2), 0.8, 90)
        rect(c, layers.MET2, rail_x0, y_pos[0] - 0.24, rail_x1 + 0.2,
             y_pos[0] + 0.24)
        rect(c, layers.MET3, x_pos[0] - 0.24, rail_y0,
             x_pos[0] + 0.24, rail_y1)

        return c


def _netlist_cap_array(unit_size: float, rows: int, cols: int, spacing: float) -> str:
    # The extractor measures C from the plate overlap, which is the
    # smaller met3 top plate (TOP_PLATE_RATIO x bottom) — the reference
    # uses the same area so values agree within grid rounding.
    plate = unit_size * TOP_PLATE_RATIO
    c_femtofarads = MIM_FF_PER_UM2 * (plate ** 2) * rows * cols
    return f"""* Sky130 MIM Capacitor Array ({rows}x{cols})
.subckt cap_array top bot
C1 top bot {c_femtofarads:.3f}f
.ends
"""


register_cap_array()
