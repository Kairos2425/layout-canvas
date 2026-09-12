"""Block IR to SPICE/CDL Netlist Compiler.

Generates golden hierarchical SPICE/CDL netlists directly from Block IR designs
for closed-loop LVS (Layout Versus Schematic) verification with Netgen and Xyce simulation.
"""

from __future__ import annotations

from pathlib import Path

from layout_canvas.blocks import base
from layout_canvas.ir.model import Design


def compile_netlist(design: Design) -> str:
    """Generate complete hierarchical SPICE netlist from Block IR Design."""
    lines: list[str] = [
        f"* Layout Canvas Generated SPICE Netlist for {design.name}",
        f"* PDK: {design.pdk}",
        "",
    ]

    # 1. Collect and emit subcircuit definitions for each referenced block type
    referenced_blocks: dict[str, base.Block] = {}
    for inst in design.instances:
        if inst.block not in referenced_blocks:
            referenced_blocks[inst.block] = base.get(inst.block)

    lines.append("* --- Primitive Subcircuit Models ---")
    for block_name, block in referenced_blocks.items():
        # Get block default netlist or emit subcircuit definition
        defaults = block.defaults()
        try:
            subckt_code = block.spice(**defaults)
            lines.append(subckt_code.strip())
            lines.append("")
        except NotImplementedError:
            # Fallback black-box subcircuit declaration
            port_names = " ".join([p.name for p in block.spec.ports])
            subckt_name = block_name.replace(".", "_")
            lines.append(f".subckt {subckt_name} {port_names}")
            lines.append(f"* Blackbox model for {block_name}")
            lines.append(".ends")
            lines.append("")

    # 2. Build pin-to-net connectivity map
    # net_name -> list of pin connections (e.g., "M1.in")
    pin_to_net: dict[str, str] = {}
    for net in design.nets:
        for pin in net.pins:
            pin_to_net[pin] = net.name

    # 3. Emit Top-level Subcircuit
    top_ports_str = " ".join([p.name for p in design.ports])
    lines.append(f"* --- Top Level Design: {design.name} ---")
    lines.append(f".subckt {design.name} {top_ports_str}")

    for inst in design.instances:
        block = base.get(inst.block)
        subckt_name = inst.block.replace(".", "_")

        # Map each port of the block to its connected net name
        conn_nets: list[str] = []
        for port in block.spec.ports:
            pin_id = f"{inst.id}.{port.name}"
            # If explicit net connected, use net name; otherwise connect to top port or float
            if pin_id in pin_to_net:
                conn_nets.append(pin_to_net[pin_id])
            else:
                # Check if matches a top-level port pin
                top_match = next((p.name for p in design.ports if p.pin == pin_id), None)
                if top_match:
                    conn_nets.append(top_match)
                else:
                    # Unconnected / internal floating net
                    conn_nets.append(f"net_{inst.id}_{port.name}")

        ports_line = " ".join(conn_nets)
        lines.append(f"X{inst.id} {ports_line} {subckt_name}")

    lines.append(f".ends {design.name}")
    lines.append("")
    return "\n".join(lines)


def export_spice(design: Design, output_path: str | Path) -> None:
    """Compile and save SPICE netlist to file."""
    p = Path(output_path).resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    spice_text = compile_netlist(design)
    p.write_text(spice_text, encoding="utf-8")
