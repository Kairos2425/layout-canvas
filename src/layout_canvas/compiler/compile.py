"""IR → GDS compiler.

Resolves block instances, applies placements, wires nets, and exports to GDS/OASIS.
"""

from __future__ import annotations

import gdsfactory as gf

from layout_canvas.blocks import base
from layout_canvas.compiler.router import route_design_nets
from layout_canvas.ir.model import Design
from layout_canvas.pdk import get_pdk


def _resolve_relative_placements(design: Design, comp_map: dict[str, gf.Component]) -> dict[str, tuple[float, float]]:
    """Resolve absolute (x, y) coordinates for all instances, handling relative constraints.

    Supports relation: 'right_of', 'left_of', 'above', 'below'
    Supports align: 'bottom', 'top', 'left', 'right', 'center_x', 'center_y'
    """
    coords: dict[str, tuple[float, float]] = {}
    remaining = list(design.instances)
    max_passes = len(remaining) + 1

    # First pass: identify absolute coordinates
    for _ in range(max_passes):
        progress = False
        for inst in list(remaining):
            pl = inst.placement
            if pl.relative_to is None:
                coords[inst.id] = (float(pl.x), float(pl.y))
                remaining.remove(inst)
                progress = True
            elif pl.relative_to in coords:
                ref_id = pl.relative_to
                ref_x, ref_y = coords[ref_id]
                ref_comp = comp_map[ref_id]
                curr_comp = comp_map[inst.id]

                ref_bb = ref_comp.bbox()
                curr_bb = curr_comp.bbox()

                # bboxes are in component coordinates; absolute edges are
                # instance origin + bb edge. The instance origin is NOT the
                # bbox corner (blocks draw geometry left/below origin —
                # guard rings, well overhangs), so every relation/align is
                # edge-to-edge: `margin` is the real gap between bboxes.
                ref_l = float(getattr(ref_bb, "left", 0.0))
                ref_r = float(getattr(ref_bb, "right", 0.0))
                ref_b = float(getattr(ref_bb, "bottom", 0.0))
                ref_t = float(getattr(ref_bb, "top", 0.0))
                curr_l = float(getattr(curr_bb, "left", 0.0))
                curr_r = float(getattr(curr_bb, "right", 0.0))
                curr_b = float(getattr(curr_bb, "bottom", 0.0))
                curr_t = float(getattr(curr_bb, "top", 0.0))

                # Compute base (x, y) based on relation
                calc_x = ref_x
                calc_y = ref_y
                m = float(pl.margin)

                if pl.relation == "right_of":
                    calc_x = ref_x + ref_r - curr_l + m
                elif pl.relation == "left_of":
                    calc_x = ref_x + ref_l - curr_r - m
                elif pl.relation == "above":
                    calc_y = ref_y + ref_t - curr_b + m
                elif pl.relation == "below":
                    calc_y = ref_y + ref_b - curr_t - m

                # Compute alignment
                if pl.align == "bottom":
                    calc_y = ref_y + ref_b - curr_b
                elif pl.align == "top":
                    calc_y = ref_y + ref_t - curr_t
                elif pl.align == "left":
                    calc_x = ref_x + ref_l - curr_l
                elif pl.align == "right":
                    calc_x = ref_x + ref_r - curr_r
                elif pl.align == "center_x":
                    calc_x = (ref_x + (ref_l + ref_r) / 2.0
                              - (curr_l + curr_r) / 2.0)
                elif pl.align == "center_y":
                    calc_y = (ref_y + (ref_b + ref_t) / 2.0
                              - (curr_b + curr_t) / 2.0)

                # Add any manual delta offset
                calc_x += float(pl.x)
                calc_y += float(pl.y)

                coords[inst.id] = (calc_x, calc_y)
                remaining.remove(inst)
                progress = True

        if not progress or not remaining:
            break

    # Fallback for circular or unresolvable dependencies
    for inst in remaining:
        coords[inst.id] = (float(inst.placement.x), float(inst.placement.y))

    return coords


def _fresh_top(name: str) -> gf.Component:
    """Create the top component, replacing any stale same-named cell.

    kfactory rejects duplicate cell names in the process-wide KCLayout, so
    repeated compiles of one design (preview → ppa → export) must retire the
    previous top cell first.
    """
    import kfactory as kf

    existing = kf.kcl.layout_cell(name)
    if existing is not None:
        kf.kcl.delete_cell(existing.cell_index())
    return gf.Component(name=name)


def compile_design(design: Design) -> gf.Component:
    """Compile Block IR Design to a gdsfactory Component."""
    top = _fresh_top(design.name)
    try:
        pdk = get_pdk(design.pdk)
    except KeyError:
        pdk = None

    # 1. Pre-generate all components to query bounding boxes
    comp_map = {}
    for inst in design.instances:
        try:
            block = base.get(inst.block)
        except KeyError:
            # A registered PDK with zero blocks (descriptor-only) deserves a
            # named boundary, not an opaque "unknown block" KeyError.
            gap = base.describe_pdk_block_gap(design.pdk)
            if gap is not None:
                raise ValueError(gap) from None
            raise
        comp_map[inst.id] = block.component(**inst.params)

    # 2. Resolve relative placement coordinates
    resolved_coords = _resolve_relative_placements(design, comp_map)

    # 3. Build all instances
    inst_refs = {}
    for inst in design.instances:
        comp = comp_map[inst.id]
        ref = top.add_ref(comp)
        x, y = resolved_coords[inst.id]
        ref.move((x, y))
        ref.rotate(inst.placement.rotation)
        if inst.placement.mirror:
            ref.mirror()

        inst_refs[inst.id] = (ref, comp)

    # Pin access: every declared block pin gets real terminal geometry
    # (contact stack up to the routing metal) before any net routing.
    from layout_canvas.compiler.router import add_pin_accesses
    untapped = add_pin_accesses(top, design, inst_refs, pdk)
    if untapped:
        import warnings
        warnings.warn(f"untapped pins (no geometry to connect): {untapped}")

    # Route nets if defined in IR
    if design.nets:
        route_design_nets(top, design, inst_refs)

    # Name interior supply nets for extraction: an unlabeled interior net
    # extracts as an anonymous $N node, which no top-level V_VDD/V_VSS
    # bias source can reach — PEX decks then ran unpowered (observed: the
    # pex op readings were resistor-pull artifacts).  A pin label stamped
    # on the net's own metal gives it its IR name in the extracted netlist
    # and gives netgen the same name as the golden side.
    _supply_net_names = {"vdd", "vcc", "vss", "gnd", "vsub", "vssx",
                         "vbb", "supply"}
    _port_names = {p.name for p in design.ports}
    for net in design.nets:
        if net.name.lower() not in _supply_net_names \
                or net.name in _port_names or not net.pins:
            continue
        inst_id, _, port_name = net.pins[0].partition(".")
        if inst_id not in inst_refs:
            continue
        ref, comp = inst_refs[inst_id]
        if port_name not in comp.ports:
            continue
        try:
            p = ref.ports[port_name]
            info = comp.kcl.layout.get_info(p.layer)
            lbl_layer = (pdk.pin_label_layer((info.layer, info.datatype))
                         if pdk is not None else (info.layer, 16))
            top.add_label(text=net.name, position=p.center, layer=lbl_layer)
        except Exception:
            pass

    # Expose top-level ports and inject top-level GDS labels for LVS
    for port in design.ports:
        inst_id, _, port_name = port.pin.partition(".")
        if inst_id in inst_refs:
            ref, comp = inst_refs[inst_id]
            if port_name in comp.ports:
                p = ref.ports[port_name]
                top.add_port(name=port.name, port=p)
                # Inject text pin label for LVS netlist extraction.
                # p.layer is a kfactory-internal layer index, not the
                # (layer, datatype) pair — resolve it via the layout.
                try:
                    info = comp.kcl.layout.get_info(p.layer)
                    p_layer = (info.layer, info.datatype)
                    if pdk is not None:
                        pin_layer = pdk.pin_label_layer(p_layer)
                    else:
                        pin_layer = (p_layer[0], 16)
                    top.add_label(text=port.name, position=p.center, layer=pin_layer)
                except Exception:
                    pass

    return top


def export_gds(design: Design, output_path: str) -> None:
    """Compile and write GDS file."""
    comp = compile_design(design)
    comp.write_gds(output_path)


def export_oas(design: Design, output_path: str) -> None:
    """Compile and write OASIS file."""
    comp = compile_design(design)
    comp.write_oas(output_path)
