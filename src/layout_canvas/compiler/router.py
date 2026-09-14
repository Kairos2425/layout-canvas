"""Analog and differential routing engine for Block IR designs.

Supports point-to-point Manhattan routing, metal layer alternation,
and symmetric differential pair routing with optional shielding.
"""

from __future__ import annotations

import math
from typing import Any

import gdsfactory as gf

from layout_canvas.blocks.sky130 import layers
from layout_canvas.blocks.sky130.geom import rect, snap
from layout_canvas.ir.model import ConstraintType, Design


def route_design_nets(top: gf.Component, design: Design, inst_refs: dict[str, Any]) -> None:
    """Route declared nets in design onto top component.

    Checks for symmetric / differential constraints between pairs of nets.
    """
    # 1. Inspect design constraints for differential / symmetric pairs
    diff_pairs: list[tuple[str, str]] = []
    for constraint in design.constraints:
        # Routing needs explicit net names.  Older IR revisions only carried
        # ``instances`` for symmetry/matching constraints; treating those IDs
        # as net names caused an AttributeError and, worse, silently made the
        # compiler unusable for otherwise valid designs.  New IR documents can
        # opt in with ``nets: ["outp", "outn"]``.
        if (
            constraint.type in (ConstraintType.symmetric, ConstraintType.match)
            and constraint.nets is not None
            and len(constraint.nets) == 2
        ):
            diff_pairs.append((constraint.nets[0], constraint.nets[1]))

    routed_nets: set[str] = set()

    # 2. Route differential pairs first with matched symmetric paths
    for net_a, net_b in diff_pairs:
        obj_a = next((n for n in design.nets if n.name == net_a), None)
        obj_b = next((n for n in design.nets if n.name == net_b), None)
        if obj_a and obj_b:
            _route_differential_pair(top, obj_a, obj_b, inst_refs)
            routed_nets.add(net_a)
            routed_nets.add(net_b)

    # 3. Route standard single-ended nets
    for net in design.nets:
        if net.name in routed_nets:
            continue
        _route_single_net(top, net, inst_refs)


def _get_pin_coords(pin_str: str, inst_refs: dict[str, Any]) -> tuple[float, float] | None:
    """Extract (x, y) center coordinates of an instance port."""
    inst_id, _, port_name = pin_str.partition(".")
    if inst_id not in inst_refs:
        return None
    ref, comp = inst_refs[inst_id]
    if port_name not in comp.ports:
        return None
    # Instance port coordinates in top cell
    p = ref.ports[port_name]
    return (float(p.center[0]), float(p.center[1]))


def _get_obstacles(inst_refs: dict[str, Any]) -> list[tuple[float, float, float, float]]:
    """Gather bounding box obstacles of all placed instances."""
    obs = []
    for ref, comp in inst_refs.values():
        bb = ref.bbox()
        if hasattr(bb, "left"):
            obs.append((float(bb.left), float(bb.bottom), float(bb.right), float(bb.top)))
    return obs


def _intersects_obstacle(x0: float, y0: float, x1: float, y1: float, obstacles: list[tuple[float, float, float, float]]) -> bool:
    """Check if line segment intersects with any obstacle bounding box."""
    min_x, max_x = min(x0, x1), max(x0, x1)
    min_y, max_y = min(y0, y1), max(y0, y1)
    for ox0, oy0, ox1, oy1 in obstacles:
        # Check box overlap with margin
        if not (max_x < ox0 or min_x > ox1 or max_y < oy0 or min_y > oy1):
            return True
    return False


def _route_single_net(top: gf.Component, net: Any, inst_refs: dict[str, Any]) -> None:
    """Perform Manhattan routing between pins with obstacle-aware channel bypass."""
    coords = [_get_pin_coords(p, inst_refs) for p in net.pins]
    valid_coords = [c for c in coords if c is not None]
    if len(valid_coords) < 2:
        return

    obstacles = _get_obstacles(inst_refs)

    # Route consecutively between pin pairs
    for i in range(len(valid_coords) - 1):
        x1, y1 = valid_coords[i]
        x2, y2 = valid_coords[i + 1]

        wire_w = snap(getattr(net, "width", 0.48) or 0.48)

        # Standard L-route: horizontal on MET1, vertical on MET2
        # Check if direct L-turn hits obstacle
        direct_h_blocked = _intersects_obstacle(x1, y1, x2, y1, obstacles)
        direct_v_blocked = _intersects_obstacle(x2, y1, x2, y2, obstacles)

        if not (direct_h_blocked or direct_v_blocked):
            # Clean direct L-route
            hx0, hx1 = min(x1, x2), max(x1, x2)
            if hx1 > hx0:
                rect(top, layers.MET1, hx0 - wire_w / 2, y1 - wire_w / 2, hx1 + wire_w / 2, y1 + wire_w / 2)
            rect(top, layers.VIA1, x2 - 0.13, y1 - 0.13, x2 + 0.13, y1 + 0.13)
            vy0, vy1 = min(y1, y2), max(y1, y2)
            if vy1 > vy0:
                rect(top, layers.MET2, x2 - wire_w / 2, vy0 - wire_w / 2, x2 + wire_w / 2, vy1 + wire_w / 2)
        else:
            # Channel detour (Z-shape): route via intermediate channel Y
            # Compute safe detour above or below obstacle cluster
            max_top = max([o[3] for o in obstacles], default=max(y1, y2))
            detour_y = max_top + 2.0  # 2um routing channel

            # 1. Vertical escape from (x1, y1) to (x1, detour_y) on MET2
            rect(top, layers.VIA1, x1 - 0.13, y1 - 0.13, x1 + 0.13, y1 + 0.13)
            vy0, vy1 = min(y1, detour_y), max(y1, detour_y)
            rect(top, layers.MET2, x1 - wire_w / 2, vy0 - wire_w / 2, x1 + wire_w / 2, vy1 + wire_w / 2)

            # 2. Horizontal channel trunk on MET1
            rect(top, layers.VIA1, x1 - 0.13, detour_y - 0.13, x1 + 0.13, detour_y + 0.13)
            hx0, hx1 = min(x1, x2), max(x1, x2)
            rect(top, layers.MET1, hx0 - wire_w / 2, detour_y - wire_w / 2, hx1 + wire_w / 2, detour_y + wire_w / 2)

            # 3. Vertical drop from (x2, detour_y) to (x2, y2) on MET2
            rect(top, layers.VIA1, x2 - 0.13, detour_y - 0.13, x2 + 0.13, detour_y + 0.13)
            vy0, vy1 = min(detour_y, y2), max(detour_y, y2)
            rect(top, layers.MET2, x2 - wire_w / 2, vy0 - wire_w / 2, x2 + wire_w / 2, vy1 + wire_w / 2)
            rect(top, layers.VIA1, x2 - 0.13, y2 - 0.13, x2 + 0.13, y2 + 0.13)


def route_differential_pair(
    top: gf.Component,
    net_p: Any = None,
    net_n: Any = None,
    inst_refs: dict[str, Any] | None = None,
    p_start: tuple[float, float] | None = None,
    p_end: tuple[float, float] | None = None,
    n_start: tuple[float, float] | None = None,
    n_end: tuple[float, float] | None = None,
    width: float = 0.48,
    spacing: float = 0.5,
    shield: bool = False,
) -> None:
    """Route a symmetric differential pair with parallel twin trunks and matched delays."""
    wire_w = snap(width)

    # Support direct coordinate input
    if p_start is not None and p_end is not None and n_start is not None and n_end is not None:
        xp1, yp1 = p_start
        xp2, yp2 = p_end
        xn1, yn1 = n_start
        xn2, yn2 = n_end
    elif net_p is not None and net_n is not None and inst_refs is not None:
        coords_p = [_get_pin_coords(p, inst_refs) for p in net_p.pins]
        coords_n = [_get_pin_coords(p, inst_refs) for p in net_n.pins]

        valid_p = [c for c in coords_p if c is not None]
        valid_n = [c for c in coords_n if c is not None]

        if len(valid_p) < 2 or len(valid_n) < 2:
            return

        xp1, yp1 = valid_p[0]
        xp2, yp2 = valid_p[1]
        xn1, yn1 = valid_n[0]
        xn2, yn2 = valid_n[1]
    else:
        return

    # Trunk P
    hx0_p, hx1_p = min(xp1, xp2), max(xp1, xp2)
    if hx1_p > hx0_p:
        rect(top, layers.MET1, hx0_p - wire_w / 2, yp1 - wire_w / 2, hx1_p + wire_w / 2, yp1 + wire_w / 2)
    if abs(yp2 - yp1) > 1e-4:
        rect(top, layers.VIA1, xp2 - 0.13, yp1 - 0.13, xp2 + 0.13, yp1 + 0.13)
        vyp0, vyp1 = min(yp1, yp2), max(yp1, yp2)
        rect(top, layers.MET2, xp2 - wire_w / 2, vyp0 - wire_w / 2, xp2 + wire_w / 2, vyp1 + wire_w / 2)

    # Trunk N
    hx0_n, hx1_n = min(xn1, xn2), max(xn1, xn2)
    if hx1_n > hx0_n:
        rect(top, layers.MET1, hx0_n - wire_w / 2, yn1 - wire_w / 2, hx1_n + wire_w / 2, yn1 + wire_w / 2)
    if abs(yn2 - yn1) > 1e-4:
        rect(top, layers.VIA1, xn2 - 0.13, yn1 - 0.13, xn2 + 0.13, yn1 + 0.13)
        vyn0, vyn1 = min(yn1, yn2), max(yn1, yn2)
        rect(top, layers.MET2, xn2 - wire_w / 2, vyn0 - wire_w / 2, xn2 + wire_w / 2, vyn1 + wire_w / 2)

    # Optional Ground / Substrate shielding lines
    if shield:
        shield_w = snap(wire_w * 0.8)
        shield_y_p = max(yp1, yp2) + spacing
        shield_y_n = min(yn1, yn2) - spacing
        rect(top, layers.MET1, min(xp1, xn1) - wire_w / 2, shield_y_p - shield_w / 2, max(xp2, xn2) + wire_w / 2, shield_y_p + shield_w / 2)
        rect(top, layers.MET1, min(xp1, xn1) - wire_w / 2, shield_y_n - shield_w / 2, max(xp2, xn2) + wire_w / 2, shield_y_n + shield_w / 2)

    return top


def route_symmetric_nets(
    top: gf.Component,
    p_route: list[tuple[float, float]],
    n_route: list[tuple[float, float]],
    width: float = 0.48,
    layer: tuple[int, int] = layers.MET1,
) -> None:
    """Route differential/symmetric traces along matched path waypoints."""
    wire_w = snap(width)

    for route in (p_route, n_route):
        for i in range(len(route) - 1):
            pt1 = route[i]
            pt2 = route[i + 1]
            x0, x1 = min(pt1[0], pt2[0]), max(pt1[0], pt2[0])
            y0, y1 = min(pt1[1], pt2[1]), max(pt1[1], pt2[1])
            if abs(x1 - x0) > 1e-4:
                rect(top, layer, x0 - wire_w / 2, pt1[1] - wire_w / 2, x1 + wire_w / 2, pt1[1] + wire_w / 2)
            if abs(y1 - y0) > 1e-4:
                rect(top, layer, pt2[0] - wire_w / 2, y0 - wire_w / 2, pt2[0] + wire_w / 2, y1 + wire_w / 2)

    return top
