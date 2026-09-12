"""Unit capacitor array with common-centroid layout (L1 block)."""

from __future__ import annotations

import gdsfactory as gf

from layout_canvas.blocks.base import register
from layout_canvas.blocks.sky130 import layers
from layout_canvas.blocks.sky130.geom import centered_count, centered_positions, rect, snap, square
from layout_canvas.ir.model import BlockSpec, ParamSpec, PortSpec


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
            PortSpec(name="top", layer="met3", direction="inout"),
            PortSpec(name="bot", layer="met2", direction="inout"),
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

        # Unit capacitors (MIM: met2 bottom, capm dielectric, met3 top)
        x_pos = centered_positions(total_w / 2, cols, pitch)
        y_pos = centered_positions(total_h / 2, rows, pitch)

        for y in y_pos:
            for x in x_pos:
                # Bottom plate (met2)
                square(c, layers.MET2, x, y, unit_size)
                # Dielectric marker (simplified - real PDK has capm layer)
                square(c, (89, 44), x, y, unit_size * 0.9)
                # Top plate (met3)
                square(c, layers.MET3, x, y, unit_size * 0.85)

        # Bus bars
        rect(c, layers.MET3, -1, total_h / 2 - 0.5, -0.5, total_h / 2 + 0.5)  # top bus
        rect(c, layers.MET2, -1, -0.5, -0.5, 0.5)  # bot bus

        return c


def _netlist_cap_array(unit_size: float, rows: int, cols: int, spacing: float) -> str:
    # Sky130 MIM capacitor unit capacitance ~ 2.0 fF/um^2
    total_area = (unit_size ** 2) * rows * cols
    c_femtofarads = total_area * 2.0
    return f"""* Sky130 MIM Capacitor Array ({rows}x{cols})
.subckt cap_array top bot
C1 top bot {c_femtofarads:.3f}f
.ends
"""


register_cap_array()
