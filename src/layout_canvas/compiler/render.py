"""Lightweight SVG rendering of compiled components for agent/human preview.

Renders real layout geometry: every polygon, colored per (layer, datatype),
with port markers on top. Coordinates are converted from database units to
micrometers and y-flipped for SVG's top-left origin.
"""

from __future__ import annotations

from typing import Any

# Deterministic palette: known layers get canonical colors, everything else
# falls back to a hash of (layer, datatype) into a muted hue wheel.
_LAYER_COLORS: dict[tuple[int, int], str] = {
    # sky130 drawing layers
    (65, 20): "#3d9970",   # diff
    (65, 44): "#7fc97f",   # tap
    (64, 20): "#8e6fb8",   # nwell (translucent-ish hue)
    (66, 20): "#d64545",   # poly
    (66, 44): "#ff9c9c",   # licon
    (67, 20): "#a8845e",   # li1
    (67, 44): "#d9b38c",   # mcon
    (68, 20): "#4a90d9",   # met1
    (68, 44): "#9fd0f5",   # via1
    (69, 20): "#7c5cd6",   # met2
    (70, 20): "#e07bb4",   # met3
    (71, 20): "#d9a441",   # met4
    (72, 20): "#e0d45c",   # met5
    (93, 44): "#1f6f50",   # nsdm
    (94, 20): "#c46a1e",   # psdm
    # ihp_sg13g2 drawing layers
    (1, 0): "#3d9970",     # activ
    (5, 0): "#d64545",     # gatpoly
    (6, 0): "#e8e4d8",     # cont
    (8, 0): "#4a90d9",     # metal1
    (10, 0): "#7c5cd6",    # metal2
    (30, 0): "#e07bb4",    # metal3
    (31, 0): "#8e6fb8",    # nwell
    (28, 0): "#555555",    # salblock
}

# Well/implant layers render translucent so the active region stays visible.
_TRANSLUCENT = {(64, 20), (31, 0), (93, 44), (94, 20), (28, 0)}

_DBU_PER_UM = 1000.0


def _color(layer: tuple[int, int]) -> str:
    if layer in _LAYER_COLORS:
        return _LAYER_COLORS[layer]
    h = (layer[0] * 37 + layer[1] * 101) % 360
    return f"hsl({h},55%,55%)"


def _points(polygon: Any, ymax: float) -> str:
    pts = [
        f"{pt.x / _DBU_PER_UM:.3f},{ymax - pt.y / _DBU_PER_UM:.3f}"
        for pt in polygon.each_point_hull()
    ]
    return " ".join(pts)


def render_svg(comp: Any, canvas_px: int = 600, max_polygons: int = 20000) -> dict[str, Any]:
    """Render a gdsfactory component to an SVG preview with per-layer geometry."""
    bb = comp.bbox()
    left = float(bb.left) if hasattr(bb, "left") else -10.0
    bottom = float(bb.bottom) if hasattr(bb, "bottom") else -10.0
    width = float(bb.width()) if hasattr(bb, "width") else 20.0
    height = float(bb.height()) if hasattr(bb, "height") else 20.0
    ymax = bottom + height

    by_layer = comp.get_polygons(by="tuple")  # {(layer, dt): [Polygon]}
    truncated = False
    budget = max_polygons

    # Points are y-flipped into [0, height]; viewBox follows that space.
    svg_lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{left - 1.0} -1.0 '
        f'{width + 2.0} {height + 2.0}" width="{canvas_px}">',
        f'  <rect x="{left - 1.0}" y="-1.0" width="{width + 2.0}" '
        f'height="{height + 2.0}" fill="#1e1e1e"/>',
    ]
    layers_seen: dict[str, dict[str, Any]] = {}
    for layer, polygons in sorted(by_layer.items()):
        if not polygons:
            continue
        layer_t = (int(layer[0]), int(layer[1]))
        color = _color(layer_t)
        opacity = 0.45 if layer_t in _TRANSLUCENT else 0.9
        drawn = 0
        for poly in polygons:
            if budget <= 0:
                truncated = True
                break
            svg_lines.append(
                f'  <polygon points="{_points(poly, ymax)}" fill="{color}" '
                f'fill-opacity="{opacity}" stroke="{color}" stroke-width="0.02"/>'
            )
            drawn += 1
            budget -= 1
        if drawn:
            layers_seen[f"{layer_t[0]}/{layer_t[1]}"] = {
                "color": color,
                "polygons": drawn,
            }

    for p in comp.ports:
        px, py = float(p.center[0]), ymax - float(p.center[1])
        svg_lines.append(f'  <circle cx="{px:.3f}" cy="{py:.3f}" r="0.2" fill="#ff4444"/>')
        svg_lines.append(
            f'  <text x="{px + 0.3:.3f}" y="{py:.3f}" font-size="0.45" '
            f'fill="#ffffff" font-family="monospace">{p.name}</text>'
        )
    svg_lines.append("</svg>")

    return {
        "format": "svg",
        "bbox": [left, bottom, left + width, bottom + height],
        "layers": layers_seen,
        "truncated": truncated,
        "svg": "\n".join(svg_lines),
    }
