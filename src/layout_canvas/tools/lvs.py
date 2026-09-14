"""Fail-closed Netgen LVS runner with a stable result contract."""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass
class LVSResult:
    status: str  # passed | failed | unavailable | error
    match: bool | None
    report_path: Path | None
    cell_name: str | None
    setup_path: Path | None
    stdout: str = ""
    stderr: str = ""
    returncode: int | None = None
    errors: list[str] | None = None
    unmatched_nets: list[str] | None = None
    unmatched_devices: list[str] | None = None

    @property
    def clean(self) -> bool | None:
        """Compatibility alias for clients that called LVS cleanliness."""
        return self.match

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        for key in ("report_path", "setup_path"):
            value[key] = str(getattr(self, key)) if getattr(self, key) else None
        value["clean"] = self.match
        return value


def run_lvs(
    gds_path: str | Path | None = None,
    spice_path: str | Path | None = None,
    cell_name: str | None = None,
    tech: str = "sky130",
    *,
    layout_path: str | Path | None = None,
    schematic_path: str | Path | None = None,
    setup_path: str | Path | None = None,
    executable: str = "netgen",
    timeout: int = 300,
) -> LVSResult:
    """Compare GDS and SPICE. ``layout_path``/``schematic_path`` are MCP aliases."""
    gds = Path(layout_path or gds_path) if (layout_path or gds_path) else None
    spice = Path(schematic_path or spice_path) if (schematic_path or spice_path) else None
    report = gds.with_suffix(".lvs.out") if gds else None
    if gds is None or not gds.is_file():
        return _unavailable(report, f"GDS/layout not found: {gds}")
    if spice is None or not spice.is_file():
        return _unavailable(report, f"SPICE/schematic not found: {spice}")
    if shutil.which(executable) is None and not Path(executable).is_file():
        return _unavailable(report, f"Netgen executable not found: {executable}")
    setup = Path(setup_path) if setup_path else _default_setup(tech)
    if setup is None or not setup.is_file():
        return _unavailable(report, f"Netgen setup file not found for {tech}: {setup}")
    cell = cell_name or gds.stem
    cmd = [executable, "-batch", "lvs", f"{gds} {cell}", f"{spice} {cell}", str(setup), str(report)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        return LVSResult("error", None, report if report and report.exists() else None, cell, setup,
                         _text(exc.stdout), _text(exc.stderr), None, ["Netgen timed out"])
    except OSError as exc:
        return LVSResult("error", None, None, cell, setup, "", str(exc), None, [str(exc)])
    if not report.is_file():
        status = "failed" if proc.returncode else "error"
        return LVSResult(status, None, None, cell, setup, proc.stdout, proc.stderr, proc.returncode,
                         ["Netgen did not produce an LVS report"])
    text = report.read_text(encoding="utf-8", errors="replace")
    match = _check_match(text)
    if match is None:
        return LVSResult("error", None, report, cell, setup, proc.stdout, proc.stderr,
                         proc.returncode, ["Unable to parse LVS report"])
    if proc.returncode != 0 or not match:
        return LVSResult("failed", False, report, cell, setup, proc.stdout, proc.stderr,
                         proc.returncode, ["LVS mismatch" if not match else "Netgen exited non-zero"])
    return LVSResult("passed", True, report, cell, setup, proc.stdout, proc.stderr, proc.returncode, [])


def _default_setup(tech: str) -> Path | None:
    value = os.environ.get("NETGEN_SETUP") or os.environ.get(f"{tech.upper()}_NETGEN_SETUP")
    return Path(value) if value else None


def _unavailable(report: Path | None, message: str) -> LVSResult:
    return LVSResult("unavailable", None, report if report and report.exists() else None, None, None, errors=[message])


def _text(value: Any) -> str:
    return value.decode(errors="replace") if isinstance(value, bytes) else (value or "")


def _check_match(report: str) -> bool | None:
    if "circuits match uniquely" in report.lower():
        return True
    if "do not match" in report.lower() or "mismatch" in report.lower() or "net mismatch" in report.lower():
        return False
    return None
