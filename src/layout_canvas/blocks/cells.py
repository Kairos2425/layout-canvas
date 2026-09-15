"""Register a compiled Design as a reusable cell block.

This is the consumption half of ``compiler/hierarchy.cell_abstract``: a
design compiled once can be instantiated by a parent design as
``block: "<alias>"`` after registration. The cell's PortSpec contract is
derived from the child design's formal ports.

Simulation deliberately stays fail-closed: cell blocks expose no inline
transistor-level emitter, so ``simulate_design`` refuses and names them,
while ``compile_netlist`` emits a blackbox ``.subckt`` shell for
hierarchical LVS.
"""

from __future__ import annotations

import gdsfactory as gf

from layout_canvas.blocks import base
from layout_canvas.compiler.compile import compile_design
from layout_canvas.ir.model import BlockSpec, Design, PortSpec


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

    block = base.Block(spec=spec, build=lambda **_: _build(), netlist=None)
    base._REGISTRY[alias] = block
    return block
