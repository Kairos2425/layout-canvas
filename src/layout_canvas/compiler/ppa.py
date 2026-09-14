"""Deterministic physical metrics for compiled Block IR layouts."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import gdsfactory as gf

from layout_canvas.ir.model import Design


def _bbox_values(component: gf.Component) -> tuple[float, float, float, float, float, float]:
    bbox = component.bbox() if hasattr(component, "bbox") else None
    if bbox is None:
        return 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
    left = float(getattr(bbox, "left", 0.0))
    bottom = float(getattr(bbox, "bottom", 0.0))
    right = float(getattr(bbox, "right", 0.0))
    top = float(getattr(bbox, "top", 0.0))
    width = max(0.0, right - left)
    height = max(0.0, top - bottom)
    return left, bottom, right, top, width, height


def _point(port: Any) -> tuple[float, float] | None:
    center = getattr(port, "center", None)
    if center is None:
        return None
    try:
        return float(center[0]), float(center[1])
    except (IndexError, TypeError, ValueError):
        return None


def _instance_ports(component: gf.Component, design: Design) -> dict[str, dict[str, tuple[float, float]]]:
    """Map IR instance/port names to transformed coordinates in the top cell."""
    result: dict[str, dict[str, tuple[float, float]]] = {}
    refs: Iterable[Any] = component.insts
    # compile_design adds references in the same order as design.instances.
    for inst, ref in zip(design.instances, refs, strict=False):
        ports: dict[str, tuple[float, float]] = {}
        for port in ref.ports:
            point = _point(port)
            if point is not None:
                ports[str(port.name)] = point
        result[inst.id] = ports
    return result


def _net_hpwl(design: Design | None, component: gf.Component) -> tuple[float, dict[str, float]]:
    """Compute per-net and total half-perimeter wire length from real pins."""
    if design is None:
        return 0.0, {}
    ports = _instance_ports(component, design)
    per_net: dict[str, float] = {}
    for net in design.nets:
        points: list[tuple[float, float]] = []
        for pin in net.pins:
            inst_id, separator, port_name = pin.partition(".")
            if separator and port_name in ports.get(inst_id, {}):
                points.append(ports[inst_id][port_name])
        if len(points) >= 2:
            xs = [point[0] for point in points]
            ys = [point[1] for point in points]
            per_net[net.name] = max(xs) - min(xs) + max(ys) - min(ys)
        else:
            per_net[net.name] = 0.0
    return sum(per_net.values()), per_net


def extract_ppa(component: gf.Component, design: Design | None = None) -> dict[str, Any]:
    """Extract geometry/topology metrics; electrical PPA remains unavailable.

    ``hpwl_um`` is the sum of net bounding-box half-perimeters from transformed
    instance-port coordinates. It is a routing lower bound, not final metal
    length. Power and performance require external simulation inputs.
    """
    left, bottom, right, top, width, height = _bbox_values(component)
    instance_count = len(design.instances) if design is not None else len(component.insts)
    net_count = len(design.nets) if design is not None else 0
    port_count = len(component.ports)
    hpwl, net_hpwl = _net_hpwl(design, component)
    return {
        "area_um2": round(width * height, 3),
        "bbox": [round(left, 3), round(bottom, 3), round(right, 3), round(top, 3)],
        "width_um": round(width, 3),
        "height_um": round(height, 3),
        "aspect_ratio": round(width / height, 3) if height > 1e-12 else None,
        "instance_count": instance_count,
        "net_count": net_count,
        "port_count": port_count,
        "hpwl_um": round(hpwl, 3),
        "net_hpwl_um": {name: round(value, 3) for name, value in net_hpwl.items()},
        # Backward-compatible alias; explicitly the HPWL lower bound.
        "estimated_wire_length_um": round(hpwl, 3),
        "power": None,
        "power_status": "unavailable",
        "performance": None,
        "performance_status": "unavailable",
    }
