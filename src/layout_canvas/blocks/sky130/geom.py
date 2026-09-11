"""Small geometry helpers on top of gdsfactory Components (all units in um)."""

from __future__ import annotations

import gdsfactory as gf


def snap(v: float, grid: float = 0.005) -> float:
    return round(round(v / grid) * grid, 4)


def rect(c: gf.Component, layer, x0: float, y0: float, x1: float, y1: float) -> None:
    x0, x1 = sorted((snap(x0), snap(x1)))
    y0, y1 = sorted((snap(y0), snap(y1)))
    c.add_polygon([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], layer=layer)


def square(c: gf.Component, layer, cx: float, cy: float, size: float) -> None:
    h = size / 2
    rect(c, layer, cx - h, cy - h, cx + h, cy + h)


def centered_count(span: float, size: float, spacing: float, edge: float) -> int:
    """How many `size` squares with `spacing` fit into `span` keeping `edge` margin."""
    usable = span - 2 * edge
    if usable < size:
        return 0
    return int((usable - size) // (size + spacing)) + 1


def centered_positions(center: float, n: int, pitch: float) -> list[float]:
    return [snap(center + (i - (n - 1) / 2) * pitch) for i in range(n)]


def add_port(
    c: gf.Component,
    name: str,
    layer,
    center: tuple[float, float],
    width: float,
    orientation: int,
) -> None:
    c.add_port(
        name=name,
        center=(snap(center[0]), snap(center[1])),
        width=snap(width, 0.01),
        orientation=orientation,
        layer=layer,
        port_type="electrical",
    )
