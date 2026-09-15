"""Lightweight SVG rendering of compiled components for agent/human preview."""

from __future__ import annotations

from typing import Any


def render_svg(comp: Any, canvas_px: int = 600) -> dict[str, Any]:
    """Render a gdsfactory component as a geometric SVG preview string."""
    bb = comp.bbox()
    left = float(bb.left) if hasattr(bb, "left") else -10.0
    bottom = float(bb.bottom) if hasattr(bb, "bottom") else -10.0
    width = float(bb.width()) if hasattr(bb, "width") else 20.0
    height = float(bb.height()) if hasattr(bb, "height") else 20.0

    svg_lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{left - 1.0} {bottom - 1.0} {width + 2.0} {height + 2.0}" width="{canvas_px}" height="{canvas_px}">',
        f'  <rect x="{left}" y="{bottom}" width="{width}" height="{height}" fill="#1e1e1e" stroke="#555" stroke-width="0.1"/>',
    ]
    for p in comp.ports:
        px, py = float(p.center[0]), float(p.center[1])
        svg_lines.append(f'  <circle cx="{px}" cy="{py}" r="0.2" fill="#ff4444" />')
        svg_lines.append(
            f'  <text x="{px + 0.3}" y="{py}" font-size="0.4" fill="#ffffff">{p.name}</text>'
        )
    svg_lines.append("</svg>")

    return {
        "format": "svg",
        "bbox": [left, bottom, left + width, bottom + height],
        "svg": "\n".join(svg_lines),
    }
