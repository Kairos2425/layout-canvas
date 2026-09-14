"""Fail-closed KLayout batch DRC runner."""
from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass
class DRCResult:
    status: str  # passed | failed | unavailable | error
    clean: bool | None
    violations: list[dict[str, Any]]
    total_violations: int | None
    report_path: Path | None
    stdout: str = ""
    stderr: str = ""
    returncode: int | None = None
    errors: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["report_path"] = str(self.report_path) if self.report_path else None
        return value


def run_drc(gds_path: str, tech: str = "sky130", deck_path: str | None = None) -> DRCResult:
    return run_klayout_drc(Path(gds_path), Path(deck_path) if deck_path else None, tech=tech)


def run_klayout_drc(gds_path: Path, deck_path: Path | None = None, *, tech: str = "sky130",
                    executable: str = "klayout", timeout: int = 60) -> DRCResult:
    """Run KLayout DRC; missing prerequisites never become a clean result."""
    gds = Path(gds_path)
    report = gds.with_suffix(".drc.txt")
    if not gds.is_file():
        return _unavailable(report, f"GDS not found: {gds}")
    if shutil.which(executable) is None and not Path(executable).is_file():
        return _unavailable(report, f"KLayout executable not found: {executable}")
    if deck_path is None:
        return _unavailable(report, f"DRC deck is required for {tech}; pass deck_path")
    deck = Path(deck_path)
    if not deck.is_file():
        return _unavailable(report, f"DRC deck not found: {deck}")
    cmd = [executable, "-b", "-r", str(deck), "-rd", f"input={gds}", "-rd", f"report={report}"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        return DRCResult("error", None, [], None, report if report.exists() else None,
                         _text(exc.stdout), _text(exc.stderr), None, ["KLayout timed out"])
    except OSError as exc:
        return DRCResult("error", None, [], None, None, "", str(exc), None, [str(exc)])
    if not report.is_file():
        status = "failed" if proc.returncode else "error"
        return DRCResult(status, None, [], None, None, proc.stdout, proc.stderr, proc.returncode,
                         ["KLayout did not produce a DRC report"])
    text = report.read_text(encoding="utf-8", errors="replace")
    count = _count_violations(text)
    if count is None:
        return DRCResult("error", None, [], None, report, proc.stdout, proc.stderr,
                         proc.returncode, ["Unable to parse DRC report"])
    if proc.returncode != 0 or count > 0:
        errors = ["DRC violations reported"] if count > 0 else ["KLayout exited non-zero"]
        return DRCResult("failed", False, [], count, report, proc.stdout, proc.stderr,
                         proc.returncode, errors)
    return DRCResult("passed", True, [], count, report, proc.stdout, proc.stderr, proc.returncode, [])


def _unavailable(report: Path, message: str) -> DRCResult:
    return DRCResult("unavailable", None, [], None, report if report.exists() else None, errors=[message])


def _text(value: Any) -> str:
    return value.decode(errors="replace") if isinstance(value, bytes) else (value or "")


def _count_violations(report: str) -> int | None:
    if re.search(r"\b(?:no\s+violations|total\s+violations\s*[:=]\s*0)\b", report, re.I):
        return 0
    matches = re.findall(r"(?:total\s+)?violations?\s*[:=]\s*(\d+)|\b(\d+)\s+violations?\b", report, re.I)
    if matches:
        pair = matches[-1]
        return int(pair[0] or pair[1])
    xml = re.search(r"<item[^>]*(?:count|num)=[\"'](\d+)[\"']", report, re.I)
    return int(xml.group(1)) if xml else None
