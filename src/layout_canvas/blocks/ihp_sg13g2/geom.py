"""IHP SG13G2 geometry helpers — same helpers as sky130 but pin datatype 2."""

from __future__ import annotations

import gdsfactory as gf

from layout_canvas.blocks.ihp_sg13g2.layers import PIN_PURPOSE
from layout_canvas.blocks.sky130.geom import centered_count, centered_positions, rect, snap, square

__all__ = [
    "add_port",
    "centered_count",
    "centered_positions",
    "rect",
    "snap",
    "square",
]


def add_port(
    c: gf.Component,
    name: str,
    layer,
    center: tuple[float, float],
    width: float,
    orientation: int,
) -> None:
    cx, cy = snap(center[0]), snap(center[1])
    c.add_port(
        name=name,
        center=(cx, cy),
        width=snap(width, 0.01),
        orientation=orientation,
        layer=layer,
        port_type="electrical",
    )
    try:
        layer_num = layer[0] if isinstance(layer, (tuple, list)) else getattr(layer, "layer", 8)
        c.add_label(text=name, position=(cx, cy), layer=(layer_num, PIN_PURPOSE))
    except Exception:
        pass
