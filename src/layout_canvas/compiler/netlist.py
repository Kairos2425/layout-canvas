"""Block IR to SPICE/CDL Netlist Compiler.

Generates golden hierarchical SPICE/CDL netlists directly from Block IR designs
for closed-loop LVS (Layout Versus Schematic) verification with Netgen and Xyce simulation.
"""

from __future__ import annotations

import hashlib
import json
import re
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
    # A block is a *parametric* primitive.  Never emit one global definition
    # per block name: two instances with different parameters must not silently
    # share the first instance's geometry/netlist.  The digest is derived from
    # the resolved parameter values, making names deterministic across runs.
    variants: dict[tuple[str, tuple[tuple[str, object], ...]],
                   tuple[base.Block, dict[str, object], str]] = {}
    for inst in design.instances:
        try:
            block = base.get(inst.block)
        except KeyError:
            # Descriptor-only PDK: name the boundary, don't dump a KeyError.
            gap = base.describe_pdk_block_gap(design.pdk)
            if gap is not None:
                raise ValueError(gap) from None
            raise
        resolved = block.resolve_params(inst.params)
        key = (inst.block, tuple(sorted(resolved.items())))
        if key not in variants:
            variants[key] = (block, resolved, _variant_name(inst.block, resolved))

    lines.append("* --- Primitive Subcircuit Models ---")
    for block, params, subckt_name in variants.values():
        # Get block default netlist or emit subcircuit definition
        try:
            subckt_code = block.spice(**params)
            lines.append(_rename_subckt(subckt_code, subckt_name, block))
            lines.append("")
        except NotImplementedError:
            # Fallback black-box subcircuit declaration
            port_names = " ".join([p.name for p in block.spec.ports])
            port_names = " ".join(p.name for p in block.spec.ports)
            lines.append(f".subckt {subckt_name} {port_names}")
            lines.append(f"* Blackbox model for {block.name}")
            lines.append(f".ends {subckt_name}")
            lines.append("")

    # Pins named like supplies join a global net even when the IR leaves
    # them unconnected — matching the physical substrate/power rings.
    _GLOBAL_PIN_NAMES = {"vss", "vdd", "gnd", "vcc", "vsub", "vssx"}

    def _interior_pin(inst, port, resolved) -> bool:
        """True when a supply-named pin is a real interior net, not the
        shared substrate.  The PMOS current_mirror keeps its source strap
        separate from the (tapless) guard ring — its ``vss`` pin is the
        transistor source, so an unconnected one floats as its own net
        instead of joining the global vss. The descriptor-driven
        ``*.gen_current_mirror`` variants carry the same bare-frame
        semantics (the IHP mirror instead draws a real p-tap ring — its
        ``vss`` is genuinely substrate and must keep the global name)."""
        return ((inst.block == "sky130.current_mirror"
                 or inst.block.endswith(".gen_current_mirror"))
                and port.name == "vss" and resolved.get("type") == "pmos")

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
        resolved = block.resolve_params(inst.params)
        subckt_name = variants[(inst.block, tuple(sorted(resolved.items())))][2]

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
                elif port.name.lower().rstrip("!") in _GLOBAL_PIN_NAMES \
                        and not _interior_pin(inst, port, resolved):
                    # Supply/substrate pins join a same-named global net —
                    # physically every guard ring shares the substrate, so
                    # the reference must not keep per-instance vss nets.
                    conn_nets.append(port.name.lower().rstrip("!"))
                else:
                    # Unconnected / internal floating net
                    conn_nets.append(f"net_{inst.id}_{port.name}")

        ports_line = " ".join(conn_nets)
        lines.append(f"X{inst.id} {ports_line} {subckt_name}")

    lines.append(f".ends {design.name}")
    lines.append("")
    return "\n".join(lines)


def _variant_name(block_name: str, params: dict[str, object]) -> str:
    """Return a SPICE-safe, stable name for one resolved parameter variant."""
    payload = json.dumps(params, sort_keys=True, separators=(",", ":"), default=str)
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:10]
    stem = re.sub(r"[^A-Za-z0-9_]", "_", block_name)
    return f"{stem}__{digest}"


def _rename_subckt(source: str, name: str, block: base.Block) -> str:
    """Normalize an emitter's declaration to the BlockSpec port contract.

    Individual emitters historically used hand-written port lists (and some
    omitted ``.ends`` names).  The compiler is the contract boundary, so make
    the declaration and terminator agree with the IR regardless of emitter
    spelling.  Internal device lines are intentionally left untouched.

    Emitters may return several ``.subckt`` blocks (a flattened cell brings
    its whole child hierarchy).  The subckt to rename is the one whose name
    matches ``block.name`` — the emitter's self-declared top — falling back
    to the first declaration for single-subckt primitives.
    """
    ports = " ".join(p.name for p in block.spec.ports)
    lines = source.strip().splitlines()
    declaration = f".subckt {name} {ports}".rstrip()

    decl_idx: int | None = None
    for i, line in enumerate(lines):
        tok = line.strip().split()
        if tok and tok[0].lower() == ".subckt":
            if len(tok) > 1 and tok[1] == block.name:
                decl_idx = i
                break
            if decl_idx is None:
                decl_idx = i
    if decl_idx is None:
        lines.insert(0, declaration)
        decl_idx = 0
    else:
        lines[decl_idx] = declaration
    # SPICE subckts cannot nest: the first .ends at or after the declaration
    # closes it.
    for j in range(decl_idx + 1, len(lines)):
        if lines[j].strip().lower().startswith(".ends"):
            lines[j] = f".ends {name}"
            break
    else:
        lines.append(f".ends {name}")
    return "\n".join(lines)


def export_spice(design: Design, output_path: str | Path) -> None:
    """Compile and save SPICE netlist to file."""
    p = Path(output_path).resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    spice_text = compile_netlist(design)
    p.write_text(spice_text, encoding="utf-8")
