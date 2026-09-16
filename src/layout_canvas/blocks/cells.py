"""Register a compiled Design as a reusable cell block.

This is the consumption half of ``compiler/hierarchy.cell_abstract``: a
design compiled once can be instantiated by a parent design as
``block: "<alias>"`` after registration. The cell's PortSpec contract is
derived from the child design's formal ports.

The cell's netlist emitter flattens the child's full hierarchical netlist
into the parent deck: every primitive subckt is inlined and the child's top
``.subckt`` is retargeted to this block's name, so ``simulate_design`` can
run real device-level simulation instead of refusing.
"""

from __future__ import annotations

import gdsfactory as gf

from layout_canvas.blocks import base
from layout_canvas.compiler.compile import compile_design
from layout_canvas.compiler.netlist import compile_netlist
from layout_canvas.ir.model import BlockSpec, Design, PortSpec


def _flattened_netlist(design: Design, alias: str, ports: list[PortSpec]) -> str:
    """Inline ``design``'s complete netlist, renaming its top to ``alias``.

    The emitted source keeps every primitive ``.subckt`` the child needs;
    the parent's compiler then renames the alias-decl to the variant name.
    """
    lines = compile_netlist(design).strip().splitlines()
    port_names = " ".join(p.name for p in ports)
    for i, line in enumerate(lines):
        tok = line.strip().split()
        if len(tok) > 1 and tok[0].lower() == ".subckt" and tok[1] == design.name:
            lines[i] = f".subckt {alias} {port_names}".rstrip()
            for j in range(i + 1, len(lines)):
                if lines[j].strip().lower().startswith(".ends"):
                    lines[j] = f".ends {alias}"
                    break
            break
    return "\n".join(lines)


def register_design_cell(alias: str, design: Design) -> base.Block:
    """Register ``design`` under ``alias`` so parent IR can instantiate it."""
    ports = [
        PortSpec(name=p.name, layer="met1", direction=p.direction) for p in design.ports
    ]
    spec = BlockSpec(
        name=alias,
        pdk=design.pdk,
        level="L2",
        summary=f"Compiled cell macro for design {design.name!r}",
        params=[],
        ports=ports,
        constraints=["fixed internal layout; reuse via abstract pins"],
        tags=["cell", "macro"],
        version=design.ir_version,
    )

    def _build() -> gf.Component:
        return compile_design(design)

    block = base.Block(
        spec=spec,
        build=lambda **_: _build(),
        netlist=lambda **_: _flattened_netlist(design, alias, ports),
    )
    base._REGISTRY[alias] = block
    return block
