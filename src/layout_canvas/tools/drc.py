"""DRC runner using KLayout batch mode."""

from __future__ import annotations

import subprocess
from pathlib import Path


def run_drc(gds_path: str, tech: str = "sky130") -> tuple[bool, str]:
    """Run DRC on GDS file using KLayout batch mode.

    Returns (pass, report_text)
    """
    gds = Path(gds_path)
    if not gds.exists():
        raise FileNotFoundError(f"GDS not found: {gds_path}")

    # DRC script path - assumes klayout-drc repo or PDK-provided scripts
    # For production, use sky130A.drc from sky130 PDK
    drc_script = f"/path/to/{tech}_drc.lydrc"

    report = gds.with_suffix(".drc.txt")

    cmd = [
        "klayout",
        "-b",  # batch mode
        "-r", drc_script,
        "-rd", f"input={gds}",
        "-rd", f"report={report}",
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)

    if not report.exists():
        return False, f"DRC failed to generate report:\n{result.stderr}"

    report_text = report.read_text()
    violations = _count_violations(report_text)

    return violations == 0, report_text


def _count_violations(report: str) -> int:
    """Parse KLayout DRC report for violation count."""
    # Simplified parser - real implementation depends on report format
    if "Total violations: 0" in report or "No violations" in report:
        return 0
    # Parse actual count from report
    for line in report.splitlines():
        if "violations" in line.lower():
            try:
                return int(line.split()[0])
            except (ValueError, IndexError):
                pass
    return -1  # unknown
