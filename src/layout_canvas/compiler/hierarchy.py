"""Cell abstract export for hierarchical layout reuse.

An abstract is the upper-level view of a compiled design: bounding box,
port positions, and per-layer blockage outlines. It lets an agent place and
route around a compiled cell without re-reading its full geometry — the
layout-side analogue of Analog Canvas's formal-port hierarchy (ADR 0025).
"""

from __future__ import annotations

from typing import Any

import gdsfactory as gf

from layout_canvas.compiler.compile import compile_design
from layout_canvas.ir.model import Design


def cell_abstract(design: Design, component: gf.Component | None = None) -> dict[str, Any]:
    """Return the placement/routing abstract of a compiled design."""
    comp = component if component is not None else compile_design(design)
    bb = comp.bbox()
    left = float(getattr(bb, "left", 0.0))
    bottom = float(getattr(bb, "bottom", 0.0))
    right = float(getattr(bb, "right", 0.0))
    top = float(getattr(bb, "top", 0.0))

    pins = []
    for port in comp.ports:
        layer = getattr(port, "layer", None)
        pins.append(
            {
                "name": str(port.name),
                "x": round(float(port.center[0]), 3),
                "y": round(float(port.center[1]), 3),
                "layer": list(layer) if isinstance(layer, (tuple, list)) else layer,
                "width_um": round(float(getattr(port, "width", 0.0) or 0.0), 3),
            }
        )

    # Blockages: one outline per (layer,datatype) occupied by flattened shapes.
    blockages: dict[str, int] = {}
    try:
        for layer in comp.layers:
            polys = comp.get_polygons(by="tuple").get(layer)
            if polys:
                blockages[f"{layer[0]}/{layer[1]}"] = len(polys)
    except Exception:
        pass

    return {
        "name": design.name,
        "pdk": design.pdk,
        "bbox": [round(left, 3), round(bottom, 3), round(right, 3), round(top, 3)],
        "width_um": round(right - left, 3),
        "height_um": round(top - bottom, 3),
        "pins": pins,
        "polygon_count_by_layer": blockages,
        "instance_count": len(design.instances),
        "ir_version": design.ir_version,
    }
