"""One-shot physical verification: extract + DRC + LVS in a single call.

Shared by the web ``verify`` action and the MCP ``verify_design`` tool so
agents and the canvas see identical, fail-closed results — an unavailable
engine reports as such, never as a pass.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from layout_canvas.compiler.compile import compile_design
from layout_canvas.compiler.netlist import compile_netlist
from layout_canvas.ir.model import Design
from layout_canvas.tools.drc import run_drc
from layout_canvas.tools.extract import extract_netlist
from layout_canvas.tools.lvs import run_lvs


def verify_design(design: Design) -> dict[str, Any]:
    """Compile the design and run extraction, DRC and LVS.

    Returns a structured dict: ``extract`` (device/net counts and
    extraction errors), ``drc`` (full DRCResult payload), ``lvs`` (full
    LVSResult payload) and ``passed`` — true only when extraction is
    clean, DRC reports zero violations and LVS matched.
    """
    comp = compile_design(design)
    tmp = Path(tempfile.mkdtemp())
    gds = tmp / f"{design.name}.gds"
    comp.write_gds(str(gds))
    ext = extract_netlist(str(gds), design.pdk)
    drc = run_drc(str(gds), tech=design.pdk, engine="pya")
    spice_path = tmp / f"{design.name}.cir"
    spice_path.write_text(compile_netlist(design), encoding="utf-8")
    lvs = run_lvs(layout_path=str(gds), schematic_path=str(spice_path),
                  cell_name=design.name, tech=design.pdk, engine="pya")
    drc_d = drc.to_dict() if hasattr(drc, "to_dict") else drc.__dict__
    for k, v in list(drc_d.items()):
        if isinstance(v, Path):
            drc_d[k] = str(v)
    return {
        "design": design.name,
        "pdk": design.pdk,
        "extract": {"status": ext.status, "devices": ext.devices,
                    "nets": ext.nets, "errors": ext.errors},
        "drc": drc_d,
        "lvs": lvs.to_dict(),
        "passed": bool(lvs.match) and drc.total_violations == 0
                  and not ext.errors,
    }
