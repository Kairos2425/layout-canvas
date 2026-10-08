"""Register an external GDS file (e.g. a Virtuoso stream-out) as a cell block.

The layout side is closed: ``build`` re-imports the GDS verbatim through
``gf.import_gds``. The port contract comes from pin labels — texts on the
PDK's pin-label datatype (``PDK.pin_purpose``) — walked recursively through
the cell hierarchy so labels buried in subcells still surface.

Netlist is opt-in: pass ``spice_text`` carrying a ``.subckt <cell_name>``
declaration whose pin count matches the discovered labels and the block
gets a real transistor-level emitter (retargeted to the alias). Without it
the block keeps ``netlist=None`` — the existing fail-closed path makes
``simulate_design``/``simulate_auto`` refuse by name and LVS compares
against a black-box, never a false pass.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import gdsfactory as gf

from layout_canvas.blocks import base
from layout_canvas.ir.model import BlockSpec, PortSpec
from layout_canvas.pdk import get_pdk

# Datatypes considered "drawing" when mapping a pin label's layer number
# back to a conductor: sky130 uses 20, IHP uses 0. Vias/contacts share the
# layer number and are excluded by name.
_DRAWING_DATATYPES = (0, 20)
_NON_DRAWING_NAMES = ("via", "con", "mcon", "licon", "tap")


@dataclass(frozen=True)
class _Pin:
    name: str
    layer_name: str
    layer_tuple: tuple[int, int]
    x: float
    y: float


def _drawing_layer_name(pdk_layers: dict[str, tuple[int, int]], layer_number: int) -> str | None:
    """Resolve a pin-label layer number to its conductor layer name."""
    candidates = [
        name for name, (ln, dt) in pdk_layers.items()
        if ln == layer_number and dt in _DRAWING_DATATYPES
        and not any(k in name for k in _NON_DRAWING_NAMES)
    ]
    return sorted(candidates)[0] if candidates else None


def _pin_labels(gds_path: Path, cell_name: str | None, pdk_name: str) -> tuple[str, list[_Pin]]:
    """Read the GDS, pick the top cell, harvest pin-label texts (um)."""
    import klayout.db as db

    ly = db.Layout()
    ly.read(str(gds_path))
    tops = list(ly.top_cells())
    if not tops:
        raise ValueError(f"{gds_path}: GDS has no top cell")
    if cell_name is None:
        if len(tops) > 1:
            names = sorted(c.name for c in tops)
            raise ValueError(
                f"{gds_path}: multiple top cells {names}; pass cell_name to pick one"
            )
        cell = tops[0]
    else:
        cell = ly.cell(cell_name)
        if cell is None:
            raise ValueError(
                f"{gds_path}: cell {cell_name!r} not found; "
                f"tops: {sorted(c.name for c in tops)}"
            )

    pdk = get_pdk(pdk_name)
    pins: list[_Pin] = []
    seen: set[str] = set()
    for li in ly.layer_indexes():
        info = ly.get_info(li)
        if info.datatype != pdk.pin_purpose:
            continue
        layer_name = _drawing_layer_name(pdk.layers, info.layer)
        if layer_name is None:
            continue
        it = db.RecursiveShapeIterator(ly, cell, li)
        while not it.at_end():
            sh = it.shape()
            if sh.is_text():
                text = sh.text_string.strip()
                pt = it.trans() * sh.text_trans * db.Point(0, 0)
                if text and text not in seen:
                    seen.add(text)
                    pins.append(_Pin(
                        name=text,
                        layer_name=layer_name,
                        layer_tuple=(info.layer, pdk.layers[layer_name][1]),
                        x=pt.x * ly.dbu,
                        y=pt.y * ly.dbu,
                    ))
            it.next()
    if not pins:
        raise ValueError(
            f"{gds_path} cell {cell.name!r}: no pin labels found on "
            f"(layer/{pdk.pin_purpose}) for pdk {pdk_name!r}. "
            "Export from Virtuoso with pin labels (e.g. on met*/pn layers)."
        )
    return cell.name, pins


def _retarget_spice(spice_text: str, cell_name: str, alias: str, n_ports: int) -> str:
    """Validate the supplied netlist and rename its top subckt to ``alias``."""
    lines = spice_text.strip().splitlines()
    decl = None
    for i, line in enumerate(lines):
        tok = line.strip().split()
        if len(tok) > 1 and tok[0].lower() == ".subckt" and tok[1].lower() == cell_name.lower():
            decl = i
            subckt_pins = [t for t in tok[2:] if "=" not in t]
            if len(subckt_pins) != n_ports:
                raise ValueError(
                    f"netlist .subckt {cell_name} declares {len(subckt_pins)} pins "
                    f"but the GDS exposes {n_ports} pin labels"
                )
            lines[i] = f".subckt {alias} {' '.join(tok[2:])}".rstrip()
            break
    if decl is None:
        raise ValueError(
            f"spice_text has no '.subckt {cell_name}' declaration matching the GDS cell"
        )
    for j in range(decl + 1, len(lines)):
        if lines[j].strip().lower().startswith(".ends"):
            lines[j] = f".ends {alias}"
            break
    return "\n".join(lines)


def register_gds_cell(
    alias: str,
    gds_path: str | Path,
    pdk: str,
    cell_name: str | None = None,
    spice_text: str | None = None,
) -> base.Block:
    """Register a GDS file under ``alias`` so parent IR can instantiate it."""
    path = Path(gds_path)
    if not path.is_file():
        raise ValueError(f"GDS not found: {path}")
    resolved_cell, pins = _pin_labels(path, cell_name, pdk)

    ports = [
        PortSpec(name=p.name, layer=p.layer_name, direction="inout") for p in pins
    ]
    spec = BlockSpec(
        name=alias,
        pdk=pdk,
        level="L2",
        summary=f"External GDS cell {resolved_cell!r} imported from {path.name}",
        params=[],
        ports=ports,
        constraints=["imported fixed layout"],
        tags=["cell", "external", "gds"],
    )

    emitter = None
    if spice_text is not None:
        netlist_text = _retarget_spice(spice_text, resolved_cell, alias, len(ports))
        emitter = lambda **_: netlist_text

    pin_width = 0.2
    try:
        pin_width = float(get_pdk(pdk).rules.get("min_pin_width_um", 0.2))
    except (KeyError, TypeError, ValueError):
        pass

    def _build() -> gf.Component:
        comp = gf.import_gds(str(path), cellname=resolved_cell)
        for p in pins:
            comp.add_port(
                name=p.name,
                center=(p.x, p.y),
                width=pin_width,
                orientation=0,
                layer=p.layer_tuple,
            )
        return comp

    block = base.Block(spec=spec, build=lambda **_: _build(), netlist=emitter)
    base._REGISTRY[alias] = block
    _IMPORTED[alias] = (resolved_cell, pins, path, pdk)
    return block


_IMPORTED: dict[str, tuple[str, list[_Pin], Path, str]] = {}


def import_summary(block: base.Block) -> dict:
    """Payload shared by the web ``import_gds`` action and the MCP tool."""
    cell_name, pins, path, pdk = _IMPORTED.get(
        block.spec.name, (block.spec.name, [], Path(""), block.spec.pdk))
    bb = block.component().bbox()
    drc: dict[str, Any]
    try:
        from layout_canvas.tools.drc import run_drc

        res = run_drc(str(path), tech=pdk, engine="pya")
        drc = {"status": res.status, "violations": res.total_violations}
    except Exception as exc:
        drc = {"status": "unavailable", "violations": None, "error": str(exc)}
    return {
        "alias": block.spec.name,
        "cell": cell_name,
        "ports": [
            {"name": p.name, "layer": p.layer_name,
             "x": round(p.x, 4), "y": round(p.y, 4)}
            for p in pins
        ],
        "bbox": [float(bb.left), float(bb.bottom), float(bb.right), float(bb.top)],
        "drc": drc,
    }
