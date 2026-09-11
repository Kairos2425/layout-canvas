"""LVS runner using netgen."""

from __future__ import annotations

import subprocess
from pathlib import Path


def run_lvs(
    gds_path: str,
    spice_path: str,
    cell_name: str,
    tech: str = "sky130",
) -> tuple[bool, str]:
    """Run LVS comparing GDS layout vs SPICE netlist.

    Returns (match, report_text)
    """
    gds = Path(gds_path)
    spice = Path(spice_path)

    if not gds.exists():
        raise FileNotFoundError(f"GDS not found: {gds_path}")
    if not spice.exists():
        raise FileNotFoundError(f"SPICE not found: {spice_path}")

    report = gds.with_suffix(".lvs.out")

    # netgen command for Sky130
    cmd = [
        "netgen",
        "-batch",
        "lvs",
        f"{gds} {cell_name}",
        f"{spice} {cell_name}",
        f"/path/to/{tech}_setup.tcl",
        str(report),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

    if not report.exists():
        return False, f"LVS failed:\n{result.stderr}"

    report_text = report.read_text()
    match = _check_match(report_text)

    return match, report_text


def _check_match(report: str) -> bool:
    """Parse netgen LVS report for match/mismatch."""
    # Netgen reports "Circuits match uniquely" on success
    if "Circuits match uniquely" in report:
        return True
    if "do not match" in report.lower() or "mismatch" in report.lower():
        return False
    return False
