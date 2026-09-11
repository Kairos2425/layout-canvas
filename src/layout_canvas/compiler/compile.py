"""IR → GDS compiler.

Resolves block instances, applies placements, wires nets, and exports to GDS/OASIS.
"""

from __future__ import annotations

import gdsfactory as gf

from layout_canvas.blocks import base
from layout_canvas.ir.model import Design


def compile_design(design: Design) -> gf.Component:
    """Compile Block IR Design to a gdsfactory Component."""
    top = gf.Component(name=design.name)

    # Build all instances
    inst_refs = {}
    for inst in design.instances:
        block = base.get(inst.block)
        comp = block.component(**inst.params)

        # Apply placement transformation
        ref = top.add_ref(comp)
        ref.move((inst.placement.x, inst.placement.y))
        ref.rotate(inst.placement.rotation)
        if inst.placement.mirror:
            ref.mirror()

        inst_refs[inst.id] = (ref, comp)

    # Expose top-level ports
    for port in design.ports:
        inst_id, _, port_name = port.pin.partition(".")
        if inst_id in inst_refs:
            ref, comp = inst_refs[inst_id]
            if port_name in comp.ports:
                top.add_port(name=port.name, port=ref.ports[port_name])

    return top


def export_gds(design: Design, output_path: str) -> None:
    """Compile and write GDS file."""
    comp = compile_design(design)
    comp.write_gds(output_path)


def export_oas(design: Design, output_path: str) -> None:
    """Compile and write OASIS file."""
    comp = compile_design(design)
    comp.write_oas(output_path)
