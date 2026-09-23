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


# Contact stacks per (pdk, tap layer): ordered bottom-up to reach the first
# routing metal. A pin whose declared layer is already the routing metal gets
# no stack — its port geometry is the pad.
_ACCESS_STACK: dict[str, dict[str, list[tuple[str, float]]]] = {
    "sky130": {
        "diff": [("licon", 0.17), ("li1", 0.34), ("mcon", 0.13)],
        "poly": [("licon", 0.17), ("li1", 0.34), ("mcon", 0.13)],
        "tap": [("licon", 0.17), ("li1", 0.34), ("mcon", 0.13)],
        "li1": [("mcon", 0.13)],
    },
    "ihp_sg13g2": {
        "activ": [("cont", 0.16)],
        "gatpoly": [("cont", 0.16)],
        "metal1": [],
    },
}

# Layers a tap must not land on: contacting poly over active punctures gate
# oxide; contacting diff under a gate lands on the channel, not the S/D.
_AVOID: dict[str, dict[str, list[str]]] = {
    # li1 avoids diff: gate-side li1 taps (diff_pair.inp) must land on the
    # riser below the active area, never on an S/D stub inside it — tapping
    # a stub shorts the input pin to a drain (observed inp<->outn merge).
    "sky130": {"poly": ["diff"], "diff": ["poly"], "li1": ["diff"]},
    "ihp_sg13g2": {"gatpoly": ["activ"], "activ": ["gatpoly"]},
}

_STUB_LAYER = {"sky130": "li1", "ihp_sg13g2": "metal1"}


def _closest_pt_on_polygon(poly: Any, px: int, py: int) -> tuple[int, int]:
    """Closest point of a klayout polygon (dbu) to (px, py)."""
    bb = poly.bbox()
    cx = min(max(px, bb.left), bb.right)
    cy = min(max(py, bb.bottom), bb.top)
    cand = (cx, cy)
    if poly.inside(__import__("klayout").db.Point(cx, cy)):
        return cand
    # walk edges, keep the nearest projection
    best, best_d2 = cand, float("inf")
    for e in poly.each_edge():
        x1, y1, x2, y2 = e.p1.x, e.p1.y, e.p2.x, e.p2.y
        dx, dy = x2 - x1, y2 - y1
        t = 0.0 if (dx == 0 and dy == 0) else max(
            0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / float(dx * dx + dy * dy)))
        qx, qy = x1 + t * dx, y1 + t * dy
        d2 = (qx - px) ** 2 + (qy - py) ** 2
        if d2 < best_d2:
            best_d2, best = d2, (round(qx), round(qy))
    return best


def _safe_tap_point(
    polys: dict[Any, Any],
    tap_layer: tuple[int, int],
    avoid: list[tuple[int, int]],
    px: int,
    py: int,
    contact_half_dbu: int = 150,
) -> tuple[int, int] | None:
    """Point on tap_layer nearest (px, py) that avoids `avoid` regions.

    Uses Region booleans: poly taps skip diffusion (no contacts on gate
    oxide); diff taps skip poly (S/D contacts never land on the channel).
    Falls back to the raw tap layer when the safe region is empty, and to
    any connective layer when the tap layer has no geometry at all.
    """
    import klayout.db as db

    def region_of(layer: tuple[int, int]) -> Any:
        r = db.Region()
        for p in polys.get(layer, []):
            r.insert(p)
        return r

    reg = region_of(tap_layer)
    if reg.is_empty():
        # graceful degradation: try any connective layer, highest first
        for layer in (tap_layer, *avoid, *polys.keys()):
            reg = region_of(layer)
            if not reg.is_empty():
                break
        if reg.is_empty():
            return None
    avoid_reg = db.Region()
    for layer in avoid:
        for p in polys.get(layer, []):
            avoid_reg.insert(p)
    safe = reg - avoid_reg if not avoid_reg.is_empty() else reg
    # The contact must clear the forbidden layer by its radius + enclosure.
    # Grow the avoid region by that halo and subtract — this keeps a narrow
    # finger usable (only the boundary halo is removed) instead of eroding
    # the safe strip out of existence.
    if not avoid_reg.is_empty():
        halo = avoid_reg.sized(contact_half_dbu)
        target = safe - halo
        if target.is_empty():
            target = safe
    else:
        target = safe
    if target.is_empty():
        target = reg

    best, best_d2 = None, float("inf")
    for p in target.each():
        q = _closest_pt_on_polygon(p, px, py)
        d2 = (q[0] - px) ** 2 + (q[1] - py) ** 2
        if d2 < best_d2:
            best_d2, best = d2, q
    return best


def add_pin_accesses(
    top: gf.Component,
    design: Design,
    inst_refs: dict[str, Any],
    pdk: Any = None,
) -> list[str]:
    """Back every declared block pin with real geometry.

    For each formal port whose tap layer is below the routing metal, locate
    the nearest safe point on that layer's polygons (Region boolean, dbu),
    transform it to top-cell space, and drop the contact stack plus a stub
    run to the port — so routed metal actually lands on silicon.
    Returns a list of pins that could not be tapped (honest diagnostics).
    """
    from layout_canvas.blocks import base
    from layout_canvas.pdk import descriptor

    pdk_desc = pdk
    if pdk_desc is None:
        try:
            pdk_desc = descriptor.get_pdk(design.pdk)
        except KeyError:
            pdk_desc = None
    stacks = _ACCESS_STACK.get(design.pdk, {})
    avoids = _AVOID.get(design.pdk, {})
    stub_name = _STUB_LAYER.get(design.pdk, "met1")
    stub_layer = pdk_desc.layer(stub_name) if pdk_desc else layers.LI
    untapped: list[str] = []

    for inst in design.instances:
        if inst.id not in inst_refs:
            continue
        ref, comp = inst_refs[inst.id]
        try:
            spec = base.get(inst.block).spec
        except KeyError:
            continue
        polys = comp.get_polygons(by="tuple")
        port_layer = {
            p.name: (p.tap_layer or p.layer) for p in spec.ports
        }
        for pname, tap_name in port_layer.items():
            if pname not in comp.ports or not pdk_desc:
                continue
            try:
                tap_tuple = pdk_desc.layer(tap_name)
            except KeyError:
                tap_tuple = None
            try:
                declared = pdk_desc.layer(
                    next(p.layer for p in spec.ports if p.name == pname))
            except (KeyError, StopIteration):
                declared = None
            stack = stacks.get(tap_name) if tap_name in stacks else None
            if tap_tuple is None or (stack is None and tap_name != declared):
                stack = stacks.get(tap_name, [])

            pc = comp.ports[pname].center
            px, py = int(round(pc[0] * 1000)), int(round(pc[1] * 1000))
            avoid = [pdk_desc.layer(a) for a in avoids.get(tap_name, [])
                     if a in pdk_desc.layers]
            if tap_tuple and stack:
                # contact radius + enclosure margin keeps the tap inside the
                # tapped material, never straddling a layer boundary
                half_dbu = int(round((stack[0][1] / 2 + 0.065) * 1000))
                tap = _safe_tap_point(polys, tap_tuple, avoid, px, py,
                                      contact_half_dbu=half_dbu)
            else:
                tap = (px, py)
            if tap is None:
                untapped.append(f"{inst.id}.{pname}")
                continue

            # transform dbu local point -> top cell (dbu), then µm for rect()
            tp = ref.trans * __import__("klayout").db.Point(*tap)
            tx, ty = tp.x / 1000.0, tp.y / 1000.0
            pp = ref.trans * __import__("klayout").db.Point(px, py)
            ox, oy = pp.x / 1000.0, pp.y / 1000.0

            # widen narrow tap material first: a 0.17 contact cannot fit
            # inside a 0.15 poly finger, so drop a tap-layer pad that merges
            # with the finger and encloses the contact. Only pad when the
            # existing material cannot enclose the contact — a gratuitous
            # pad can violate same-layer spacing (observed on the mirror's
            # gate stub: pad edge 0.185 from a neighbouring finger).
            if stack and tap_tuple:
                import klayout.db as _db
                # does the existing material already enclose the contact?
                # (local dbu coordinates — `tap` and `polys` share that space)
                need_pad = True
                erode_dbu = int(round((stack[0][1] / 2 + 0.065) * 1000))
                mat = _db.Region()
                for p in polys.get(tap_tuple, []):
                    mat.insert(p)
                inner = mat.sized(-erode_dbu)
                pt = _db.Region(_db.Box(tap[0], tap[1], tap[0] + 1, tap[1] + 1))
                if not (inner & pt).is_empty():
                    need_pad = False
                if need_pad:
                    pad = stack[0][1] + 0.24
                    half = pad / 2
                    rect(top, tap_tuple, tx - half, ty - half,
                         tx + half, ty + half)
            # contact stack at the tap point
            for lname, size in (stack or []):
                l = pdk_desc.layer(lname)
                half = size / 2
                rect(top, l, tx - half, ty - half, tx + half, ty + half)
            # stub run between tap and port on the DECLARED (routing)
            # layer: the stack's top contact already lands the tap on that
            # layer, and running the stub on li1 through the active area
            # shorts the pin to whatever stubs it crosses (observed: inp's
            # li1 stub + port-side mcon landed on an outn S/D stub).
            run_layer = declared or stub_layer
            if run_layer and (abs(tx - ox) > 1e-6 or abs(ty - oy) > 1e-6):
                hw = 0.17
                rect(top, run_layer,
                     min(tx, ox) - hw, min(ty, oy) - hw,
                     max(tx, ox) + hw, max(ty, oy) + hw)
            # routing-metal pad at the declared port point
            if declared:
                hw = 0.24
                rect(top, declared, ox - hw, oy - hw, ox + hw, oy + hw)
    return untapped


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

    # 3. Route standard single-ended nets — each net gets its own
    # detour channel. Sharing one channel y merged every trunk on met1
    # (observed: outp/outn/vdd all shorted at y=max_top+2.0).
    channel_index = 0
    for net in design.nets:
        if net.name in routed_nets:
            continue
        _route_single_net(top, net, inst_refs, channel_index=channel_index)
        channel_index += 1


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


def _route_single_net(top: gf.Component, net: Any, inst_refs: dict[str, Any],
                      channel_index: int = 0) -> None:
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
            # Channel detour (Z-shape): route via intermediate channel Y.
            # Every net owns a distinct channel so trunks cannot merge.
            max_top = max([o[3] for o in obstacles], default=max(y1, y2))
            detour_y = max_top + 2.0 + channel_index * (wire_w + 0.4)

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
