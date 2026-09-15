"""Fail-closed circuit simulation runner over pluggable SPICE backends.

Analog Canvas ADR 0055: simulation is part of the product, and it refuses
honestly — a design containing a block with no transistor-level emitter is
rejected with the blocks named; a missing simulator or model deck is
``unavailable``, never a false clean result.

Backend selection goes through ``tools.backends``: ``simulator="auto"``
picks the first probed-available simulator (FOSS first), a named simulator
is used when present, and license-gated tools that are installed still run
— a failed license checkout surfaces as a real ``failed``/``error`` result
with the tool's own diagnostics, not a guess.

Two entry modes:

- ``simulate_design`` — compile a Design's golden netlist, wrap it with
  caller-supplied stimulus (sources, top instantiation, analyses) and run.
- ``run_netlist`` — run a complete caller-provided deck as-is.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from layout_canvas.blocks import base
from layout_canvas.compiler.netlist import compile_netlist
from layout_canvas.ir.model import Design
from layout_canvas.tools import backends
from layout_canvas.tools.backends import ToolAdapter


@dataclass
class SimResult:
    status: str  # passed | failed | refused | unavailable | error
    log_path: Path | None
    deck_path: Path | None
    simulator: str | None = None
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
    simulator: str = "auto",
    executable: str | None = None,
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
    return run_netlist(
        deck, simulator=simulator, executable=executable, timeout=timeout, workdir=workdir
    )


def run_netlist(
    deck: str,
    *,
    simulator: str = "auto",
    executable: str | None = None,
    timeout: int = 120,
    workdir: str | Path | None = None,
) -> SimResult:
    """Run a complete SPICE deck through the selected simulator."""
    adapter, binary, early = _select_backend(simulator, executable)
    if early is not None:
        return early
    assert adapter is not None and binary is not None

    if ".end" not in deck.lower() and adapter.deck_hint == "spice":
        deck = deck.rstrip() + "\n.end\n"

    root = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="lc_sim_"))
    root.mkdir(parents=True, exist_ok=True)
    deck_path = root / "deck.cir"
    deck_path.write_text(deck, encoding="utf-8")

    cmd, log_path = _SIM_RUNNERS[adapter.name](binary, deck_path, root)
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False, cwd=root
        )
    except subprocess.TimeoutExpired as exc:
        return SimResult("error", log_path, deck_path, adapter.name, _text(exc.stdout),
                         _text(exc.stderr), None, [f"{adapter.name} timed out"])
    except OSError as exc:
        return SimResult("error", None, deck_path, adapter.name, "", str(exc), None, [str(exc)])

    log_text = _read_log(log_path) or proc.stdout
    errors = _find_errors(log_text)
    if errors or proc.returncode not in (0, None):
        return SimResult(
            "failed", log_path, deck_path, adapter.name, proc.stdout, proc.stderr,
            proc.returncode, errors or [f"{adapter.name} exited with {proc.returncode}"],
        )
    return SimResult(
        "passed", log_path, deck_path, adapter.name, proc.stdout, proc.stderr,
        proc.returncode, [],
    )


def _select_backend(
    simulator: str, executable: str | None
) -> tuple[ToolAdapter | None, str | None, SimResult | None]:
    """Pick adapter+binary or return an early fail-closed SimResult."""
    if executable:
        # Explicit binary override: treat as ngspice-syntax deck runner.
        if not Path(executable).is_file():
            import shutil

            if shutil.which(executable) is None:
                return None, None, SimResult(
                    "unavailable", None, None, None,
                    errors=[f"simulator executable not found: {executable}"],
                )
        return backends.NGSPICE, executable, None

    if simulator and simulator != "auto":
        try:
            adapter = backends.get_adapter(simulator)
        except KeyError as exc:
            return None, None, SimResult("unavailable", None, None, None, errors=[str(exc)])
        if adapter.kind != "spice_simulator" or not adapter.can_run:
            return None, None, SimResult(
                "unavailable", None, None, adapter.name,
                errors=[f"tool {adapter.name!r} is a {adapter.kind}, not a runnable simulator"],
            )
        binary = adapter.find_binary()
        if binary is None:
            return None, None, SimResult(
                "unavailable", None, None, adapter.name,
                errors=[f"{adapter.name} not found; tried {adapter.executables}"],
            )
        return adapter, binary, None

    for adapter in backends.adapters("spice_simulator"):
        if not adapter.can_run:
            continue
        binary = adapter.find_binary()
        if binary:
            return adapter, binary, None
    names = ", ".join(a.name for a in backends.adapters("spice_simulator"))
    return None, None, SimResult(
        "unavailable", None, None, None,
        errors=[f"no SPICE simulator found on PATH; checked: {names}"],
    )


# --- Per-simulator command builders -------------------------------------
# Each returns (argv, expected_log_path_or_None).  Missing logs are fine —
# stdout is used instead.

def _run_ngspice(binary: str, deck: Path, root: Path) -> tuple[list[str], Path]:
    log = root / "ngspice.log"
    return [binary, "-b", "-o", str(log), str(deck)], log


def _run_xyce(binary: str, deck: Path, root: Path) -> tuple[list[str], Path]:
    log = root / "xyce.log"
    return [binary, "-o", str(log), str(deck)], log


def _run_ltspice(binary: str, deck: Path, root: Path) -> tuple[list[str], Path]:
    # LTspice writes <deck>.log beside the deck in batch mode.
    return [binary, "-b", str(deck)], deck.with_suffix(".log")


def _run_spectre(binary: str, deck: Path, root: Path) -> tuple[list[str], Path]:
    log = root / "spectre.log"
    return [binary, "+spice", "-log", str(log), str(deck)], log


def _run_hspice(binary: str, deck: Path, root: Path) -> tuple[list[str], Path]:
    return [binary, str(deck), "-o", str(root / "hspice")], root / "hspice.lis"


def _run_eldo(binary: str, deck: Path, root: Path) -> tuple[list[str], Path]:
    return [binary, str(deck)], root / "eldo.log"


_SIM_RUNNERS: dict[str, Callable[[str, Path, Path], tuple[list[str], Path]]] = {
    "ngspice": _run_ngspice,
    "xyce": _run_xyce,
    "ltspice": _run_ltspice,
    "spectre": _run_spectre,
    "hspice": _run_hspice,
    "eldo": _run_eldo,
}


def _read_log(log_path: Path | None) -> str:
    if log_path and log_path.is_file():
        return log_path.read_text(encoding="utf-8", errors="replace")
    return ""


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
    r"no such (?:model|device|subckt)|simulation aborted|analysis failed|"
    r"license.*(?:fail|denied|expired)|cannot (?:check out|acquire)",
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
