"""Fail-closed ngspice simulation runner.

Analog Canvas ADR 0055: simulation is part of the product, and it refuses
honestly — a design containing a block with no transistor-level emitter is
rejected with the blocks named; a missing simulator or model deck is
``unavailable``, never a false clean result.

Two entry modes:

- ``simulate_design`` — compile a Design's golden netlist, wrap it with
  caller-supplied stimulus (sources, top instantiation, analyses) and run.
- ``run_netlist`` — run a complete caller-provided deck as-is.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from layout_canvas.blocks import base
from layout_canvas.compiler.netlist import compile_netlist
from layout_canvas.ir.model import Design


@dataclass
class SimResult:
    status: str  # passed | failed | refused | unavailable | error
    log_path: Path | None
    deck_path: Path | None
    stdout: str = ""
    stderr: str = ""
    returncode: int | None = None
    errors: list[str] | None = None
    unresolved_blocks: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        for key in ("log_path", "deck_path"):
            value[key] = str(getattr(self, key)) if getattr(self, key) else None
        return value


def simulate_design(
    design: Design,
    stimulus: str,
    *,
    includes: list[str] | None = None,
    executable: str = "ngspice",
    timeout: int = 120,
    workdir: str | Path | None = None,
) -> SimResult:
    """Simulate a Design. ``stimulus`` supplies sources, the top-level
    ``X`` instantiation and analyses; ``includes`` are .include'd model decks."""
    unresolved = set()
    for inst in design.instances:
        try:
            if base.get(inst.block).netlist is None:
                unresolved.add(inst.block)
        except KeyError:
            unresolved.add(inst.block)
    unresolved = sorted(unresolved)
    if unresolved:
        return SimResult(
            "refused",
            None,
            None,
            errors=[
                "design contains blocks without transistor-level emitters; "
                "refusing to simulate: " + ", ".join(unresolved)
            ],
            unresolved_blocks=unresolved,
        )
    deck = _build_deck(compile_netlist(design), stimulus, includes or [])
    return run_netlist(deck, executable=executable, timeout=timeout, workdir=workdir)


def run_netlist(
    deck: str,
    *,
    executable: str = "ngspice",
    timeout: int = 120,
    workdir: str | Path | None = None,
) -> SimResult:
    """Run a complete SPICE deck through ngspice in batch mode."""
    if shutil.which(executable) is None and not Path(executable).is_file():
        return SimResult("unavailable", None, None, errors=[f"ngspice executable not found: {executable}"])
    if ".end" not in deck.lower():
        deck = deck.rstrip() + "\n.end\n"

    root = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="lc_sim_"))
    root.mkdir(parents=True, exist_ok=True)
    deck_path = root / "deck.cir"
    log_path = root / "ngspice.log"
    deck_path.write_text(deck, encoding="utf-8")

    try:
        proc = subprocess.run(
            [executable, "-b", "-o", str(log_path), str(deck_path)],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            cwd=root,
        )
    except subprocess.TimeoutExpired as exc:
        return SimResult("error", log_path, deck_path, _text(exc.stdout), _text(exc.stderr),
                         None, ["ngspice timed out"])
    except OSError as exc:
        return SimResult("error", None, deck_path, "", str(exc), None, [str(exc)])

    log_text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.is_file() else ""
    errors = _find_errors(log_text or proc.stdout)
    if errors or proc.returncode not in (0, None):
        return SimResult("failed", log_path, deck_path, proc.stdout, proc.stderr,
                         proc.returncode, errors or [f"ngspice exited with {proc.returncode}"])
    return SimResult("passed", log_path, deck_path, proc.stdout, proc.stderr, proc.returncode, [])


def _build_deck(netlist: str, stimulus: str, includes: list[str]) -> str:
    lines = ["* layout-canvas simulation deck", ""]
    for inc in includes:
        lines.append(f".include {inc}")
    if includes:
        lines.append("")
    lines.append(netlist.rstrip())
    lines.append("")
    lines.append(stimulus.rstrip())
    lines.append("")
    lines.append(".end")
    return "\n".join(lines)


def _text(value: Any) -> str:
    return value.decode(errors="replace") if isinstance(value, bytes) else (value or "")


_ERROR_RE = re.compile(
    r"^\s*(?:error|fatal|\*\*.*error)|undefined (?:model|subcircuit)|"
    r"no such (?:model|device|subckt)|simulation aborted|analysis failed",
    re.IGNORECASE,
)


def _find_errors(log: str) -> list[str]:
    if not log:
        return []
    found: list[str] = []
    for line in log.splitlines():
        line = line.strip()
        if line and _ERROR_RE.search(line) and line not in found:
            found.append(line)
    return found
