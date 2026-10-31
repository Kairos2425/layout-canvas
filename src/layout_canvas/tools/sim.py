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

import math
import os
import re
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

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
    deck = _build_deck(
        _rewrite_mos_cards(compile_netlist(design), design.pdk),
        stimulus, includes or [])
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


def default_model_prelude(pdk: str) -> str:
    """Deck preamble (before the netlist): model includes, corner libs and
    required .param lines for the PDK. Empty string when the PDK has no
    resolvable local models — the caller decides whether that is fatal."""
    repo = Path(__file__).resolve().parents[3]
    # Descriptor PDKs carry their prelude as a file the JSON points at:
    # model_libs.spice_prelude_file resolves against the descriptor's
    # directory so a commercial PDK ships models beside its *.pdk.json.
    from layout_canvas.pdk import get_pdk

    try:
        desc = get_pdk(pdk)
    except KeyError:
        desc = None
    if desc is not None and desc.model_libs.get("spice_prelude_file"):
        lib = Path(desc.model_libs["spice_prelude_file"])
        if not lib.is_absolute():
            lib = Path(desc.base_dir or ".") / lib
        if lib.is_file():
            # utf-8-sig: a BOM at deck top would leak into the SPICE parser.
            return lib.read_text(encoding="utf-8-sig").strip()
        return ""  # declared but missing — no prelude, fail closed below
    if pdk == "sky130":
        lib = repo / "examples" / "models" / "sky130" / "sky130_tt.lib"
        if lib.is_file():
            return f'.include "{lib.as_posix()}"'
        return ""
    if pdk == "ihp_sg13g2":
        # IHP models are not redistributed — point LAYOUT_CANVAS_IHP_MODELS
        # at the ngspice model dir of a local IHP-Open-PDK checkout
        # (ihp-sg13g2/libs.tech/ngspice/models).
        models_env = os.environ.get("LAYOUT_CANVAS_IHP_MODELS")
        if not models_env:
            return ""
        mos = Path(models_env) / "cornerMOSlv.lib"
        if not mos.is_file():
            return ""
        return (f'.lib "{mos.as_posix()}" mos_tt\n'
                ".param pre_layout=1")
    return ""


def default_control_prelude(pdk: str) -> list[str]:
    """.control lines that must run before the analysis (OSDI loads etc.)."""
    if pdk == "ihp_sg13g2":
        # PSP103 is OSDI-compiled; LAYOUT_CANVAS_OSDI_DIR points at the
        # directory containing psp103.osdi (e.g. an ngspice lib dir).
        osdi_env = os.environ.get("LAYOUT_CANVAS_OSDI_DIR")
        if not osdi_env:
            return []
        lines = []
        for name in ("psp103.osdi", "psp103_nqs.osdi"):
            f = Path(osdi_env) / name
            if f.is_file():
                lines.append(f"pre_osdi {f.as_posix()}")
        return lines
    return []


def _bias_line(name: str, vdd: float) -> str:
    """Port-name heuristics -> DC bias statement. Honest defaults: a port we
    cannot classify gets a weak pull to mid-rail so .op still converges."""
    n = name.lower()
    if any(k in n for k in ("vdd", "vcc", "supply")):
        return f"V_{name} {name} 0 {vdd}"
    if any(k in n for k in ("vss", "gnd", "vsub", "vbb")):
        return f"V_{name} {name} 0 0"
    if any(k in n for k in ("tail", "bias", "ibias")):
        return f"I_{name} {name} vss 10u"
    if n.startswith(("in", "vip", "vin")):
        return f"V_{name} {name} 0 {vdd / 2:g}"
    # Outputs and unknowns: 10k pull to supply + 1Meg to gnd — generic load.
    return f"R_{name}_u vdd {name} 10k\nR_{name}_d {name} vss 1Meg"


_SUPPLY_TOKENS = ("vdd", "vcc", "vss", "gnd", "vsub", "vbb", "supply")


def _supply_current_elements(stimulus: str) -> list[str]:
    """Voltage-source element names that feed supply nets in a generated
    stimulus (``V_<port> <net> 0 ...`` plus synthesized ``V_VDD``/``V_VSS``
    rails). ngspice lowercases element names, so ``V_vdd`` and ``V_VDD``
    are the same vector ``v_vdd#branch`` — returned deduped, lowercased.
    The waves key for each is ``i(<elem>)``: the current the source
    *delivers* (positive = sourcing), i.e. ``-`` ngspice's branch
    convention — the natural sign for a current-budget (EM) spec."""
    out: list[str] = []
    for line in stimulus.splitlines():
        m = re.match(r"^V(\S+)\s+(\S+)\s+0\b", line)
        if not m:
            continue
        if m.group(2).lower() not in _SUPPLY_TOKENS:
            continue
        elem = ("v" + m.group(1)).lower()
        if elem not in out:
            out.append(elem)
    return out


def _i_vector(elem: str) -> str:
    """``let``-vector name carrying ``-i(<elem>)`` for the wrdata row."""
    return f"lc_i_{re.sub(r'[^A-Za-z0-9_]', '_', elem)}"


def _current_capture(stimulus: str) -> tuple[list[str], list[str], list[str]]:
    """(.control let-lines, wave labels, element names) instrumenting
    every supply source found by ``_supply_current_elements``.
    ``wrdata`` cannot take a bare ``-i(v_x)`` expression when mixed with
    other vectors (the leading ``-`` is parsed as a flag — the column
    silently drops), so the negated branch current is materialised as a
    named vector first."""
    elems = _supply_current_elements(stimulus)
    lets = [f"let {_i_vector(e)} = -i({e})" for e in elems]
    labels = [f"i({e})" for e in elems]
    return lets, labels, elems


def _drive_port(design: Design, ports: list[str]) -> str | None:
    """Pick the port that carries the ac/dc stimulus source: the first
    port declared ``direction='input'`` on the design, else the first
    non-supply port in the compiled subckt's port order."""
    inputs = {p.name for p in design.ports if p.direction == "input"}
    for p in ports:
        if p in inputs:
            return p
    for p in ports:
        if not any(k in p.lower() for k in _SUPPLY_TOKENS):
            return p
    return ports[0] if ports else None


def _apply_drive(
    lines: list[str], port: str, vdd: float, analysis: str
) -> tuple[list[str], str]:
    """Attach the ac/dc drive to ``port``. When the port is already biased
    by its own ``V_`` source, the source card is extended in place
    (``ac 1`` for ac); otherwise a dedicated mid-rail source is appended.
    Returns the updated lines and the drive source element name."""
    suffix = " ac 1" if analysis == "ac" else ""
    out = []
    for line in lines:
        if line.startswith(f"V_{port} "):
            line = line + suffix
        out.append(line)
    if any(line.startswith(f"V_{port} ") or line.startswith(f"V_{port}\t")
           for line in lines):
        return out, f"V_{port}"
    out.append(f"V_ac_{port} {port} 0 {vdd / 2:g}{suffix}")
    return out, f"V_ac_{port}"


def _ac_waves(names: list[str], waves_raw: dict[str, list[float]]) -> dict[str, list[float]]:
    """Assemble ac waveforms: wrdata emits (real, imag) column pairs, so
    the wave under each port/probe name is |v| = hypot(re, im); a sibling
    ``<name>__db`` key carries 20*log10(|v|)."""
    waves: dict[str, list[float]] = {}
    for i, name in enumerate(names, start=1):
        re_xs = waves_raw.get(f"v{i}")
        if re_xs is None:
            continue
        im_xs = waves_raw.get(f"v{i}i") or [0.0] * len(re_xs)
        mags = [math.hypot(r, m) for r, m in zip(re_xs, im_xs, strict=False)]
        waves[name] = mags
        waves[f"{name}__db"] = [20.0 * math.log10(max(m, 1e-12)) for m in mags]
    return waves


def _sweep_run(analysis: str, tran_step: str, tran_stop: str,
               src: str | None, vdd: float) -> str:
    """The .control run line for each supported analysis."""
    if analysis == "ac":
        return "ac dec 10 1k 1G"
    if analysis == "dc":
        if src is None:
            return "op"  # no port to sweep — an op point is the honest deck
        return f"dc {src} 0 {vdd:g} {vdd / 50:g}"
    if analysis == "op":
        return "op"
    return f"tran {tran_step} {tran_stop}"


def default_stimulus(
    design: Design,
    *,
    vdd: float = 1.8,
    analysis: str = "op",
    tran_stop: str = "5u",
    tran_step: str = "10n",
    out_file: str = "waves.dat",
    digital_sources: dict[str, str] | None = None,
) -> str:
    """Auto-generated stimulus for the canvas Simulate button: instantiate
    the compiled top, bias every port by name heuristics, run .op/.tran/
    .ac/.dc, and wrdata every port voltage for the UI to plot.

    ``digital_sources`` maps node name → a complete source card (the PWL
    cards from ``tools.mixed_signal.digital_stimulus``). A node present
    there *replaces* the auto-bias for that node — the ``_bias_line`` for
    the port (and the synthesized ``V_VDD``/``V_VSS`` rail when the rail
    itself is digitally driven) is skipped so the node is never
    double-driven."""
    nl = compile_netlist(design)
    m = re.search(
        rf"^\s*\.subckt\s+{re.escape(design.name)}\s+(?P<ports>.+?)\s*$",
        nl, re.MULTILINE)
    ports = m.group("ports").split() if m else [p.name for p in design.ports]
    dig = digital_sources or {}
    dig_l = {n.lower() for n in dig}
    lines = [_bias_line(p, vdd) for p in ports if p not in dig]
    # Supply-named nets *inside* the top subckt are SPICE-local nodes: the
    # auto-bias rails at top level cannot reach them unless the names are
    # declared .global (verified: without it X1.<net> pins sit at ~0 V via
    # gmin and the "bias" comes only from the pull resistors).  Collect the
    # supply tokens actually referenced inside the subckt body — explicit
    # nets plus the per-instance supply fallback names compile_netlist
    # emits — and globalize them so V_VDD/V_VSS truly power the interior.
    body = re.search(
        rf"\.subckt\s+{re.escape(design.name)}\b(?P<body>.*?)\.ends",
        nl, re.IGNORECASE | re.DOTALL)
    globals_: list[str] = []
    if body:
        for tok in body.group("body").split():
            t = tok.lower().rstrip("!")
            if (t in _SUPPLY_TOKENS or t == "vssx") and tok not in ports \
                    and tok not in globals_:
                globals_.append(tok)
    if globals_:
        lines.insert(0, ".global " + " ".join(globals_))
    # The bias network references implicit rail nodes vdd/vss; if the design
    # doesn't expose them as ports, source them so nothing floats — unless a
    # digital source already drives that rail node.
    if not any(p.lower() in ("vdd", "vcc") for p in ports) \
            and not (dig_l & {"vdd", "vcc"}):
        lines.insert(0 if not globals_ else 1, f"V_VDD vdd 0 {vdd}")
    if not any(p.lower() in ("vss", "gnd") for p in ports) \
            and not (dig_l & {"vss", "gnd"}):
        lines.insert(0 if not globals_ else 1, "V_VSS vss 0 0")
    drive = None
    if analysis in ("ac", "dc"):
        drive = _drive_port(design, ports)
        if drive is not None:
            lines, drive = _apply_drive(lines, drive, vdd, analysis)
    lines.append(f"X1 {' '.join(ports)} {design.name}")
    lines.extend(dig.values())
    lets, _labels, elems = _current_capture("\n".join(lines))
    vectors = " ".join(f"v({p})" for p in ports)
    if elems:
        vectors += " " + " ".join(_i_vector(e) for e in elems)
    run = _sweep_run(analysis, tran_step, tran_stop, drive, vdd)
    lines.append(".control")
    lines.extend(default_control_prelude(design.pdk))
    lines.append(run)
    # Branch currents exist only after the analysis ran — let-lines sit
    # between the run and wrdata.
    lines.extend(lets)
    lines.append(f"wrdata {out_file} {vectors}")
    lines.append(".endc")
    return "\n".join(lines)


def simulate_auto(
    design: Design,
    *,
    analysis: str = "op",
    vdd: float = 1.8,
    temp: float | None = None,
    tran_stop: str = "5u",
    tran_step: str = "10n",
    simulator: str = "auto",
    executable: str | None = None,
    timeout: int = 120,
    workdir: str | Path | None = None,
    device_stress: dict[str, float] | None = None,
    digital_sources: dict[str, str] | None = None,
) -> dict[str, Any]:
    """One-click simulation for the canvas: model prelude + auto stimulus +
    backend run + waveform extraction. Returns the SimResult fields plus
    ``ports`` (bias vector order) and ``waves`` (parsed wrdata columns).

    ``temp`` (°C) emits a ``.temp`` card — the circuit simulation
    temperature, which ngspice applies to every analysis in the deck and
    which takes precedence over ``.options temp``. It does not rewrite the
    models' ``tnom`` (the temperature their parameters were extracted at);
    temperature dependence stays the models' own equations. ``None`` keeps
    the simulator default (ngspice: 27 °C).

    ``device_stress`` carries the parametric aging-proxy keys
    (``dvth_nmos``/``dvth_pmos``/``mu0_scale`` — see
    ``_inject_device_stress``). When set, the deck's MOS cards gain
    ``delvto``/``mulu0`` instance params and the model prelude is inlined
    and patched so PDK wrapper subckts forward them to the BSIM4 device
    — if any stressed device model has no such wiring path the run
    reports ``unavailable`` with the unapplied wrappers named, never a
    silently un-stressed "stress" run.

    ``waves`` also carries ``i(v_<src>)`` for every supply-feeding
    voltage source in the auto stimulus — the current the source
    *delivers* (positive = sourcing), so an EM-style budget is a plain
    ``max`` bound.

    ``digital_sources`` (node → complete PWL/DC source card, as built by
    ``tools.mixed_signal.digital_stimulus``) replaces the auto-bias on
    its nodes — see ``default_stimulus``. Driveable nodes are the
    compiled subckt's ports plus the implicit rails the stimulus can
    synthesise (``_SUPPLY_TOKENS``); anything else is an ``error`` — a
    digital signal must never silently drive a node that is not there.
    """
    root = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="lc_sim_"))
    root.mkdir(parents=True, exist_ok=True)
    prelude = default_model_prelude(design.pdk)
    netlist = compile_netlist(design)
    if digital_sources:
        pm = re.search(
            rf"^\s*\.subckt\s+{re.escape(design.name)}\s+(?P<ports>.+?)\s*$",
            netlist, re.MULTILINE)
        top_ports = pm.group("ports").split() if pm else [
            p.name for p in design.ports]
        allowed = set(top_ports) | set(_SUPPLY_TOKENS)
        dead = sorted(n for n in digital_sources if n not in allowed)
        if dead:
            return {"status": "error",
                    "errors": [
                        "digital stimulus node(s) not driveable on the "
                        "schematic deck: " + ", ".join(dead) +
                        " — driveable nodes are the design ports ("
                        + ", ".join(top_ports) + ") plus implicit rails "
                        + ", ".join(sorted(_SUPPLY_TOKENS))]}
    stimulus = default_stimulus(
        design, vdd=vdd, analysis=analysis,
        tran_stop=tran_stop, tran_step=tran_step, out_file="waves.dat",
        digital_sources=digital_sources)
    device_report: dict[str, Any] | None = None
    if device_stress:
        netlist, injected_models = _inject_device_stress(netlist, device_stress)
        patched, device_report = _device_stress_prelude(
            design.pdk, injected_models)
        device_report["requested"] = {
            k: float(v) for k, v in device_stress.items()}
        device_report["injected_models"] = sorted(injected_models)
        if patched is not None:
            prelude = patched
    netlist = _rewrite_mos_cards(netlist, design.pdk)
    deck_parts = ["* layout-canvas auto deck", ""]
    if prelude:
        deck_parts += [prelude, ""]
    deck_parts += [netlist.rstrip(), "", stimulus.rstrip(), ""]
    if temp is not None:
        deck_parts += [f".temp {float(temp):g}", ""]
    deck_parts += [".end"]

    unresolved = sorted(
        inst.block for inst in design.instances
        if _emitter_missing(inst.block))
    if unresolved:
        return {"status": "refused",
                "errors": ["blocks without transistor-level emitters: "
                           + ", ".join(unresolved)],
                "unresolved_blocks": unresolved}
    if design.pdk in ("sky130", "ihp_sg13g2") and not prelude:
        return {"status": "unavailable",
                "errors": [f"no local model deck resolved for pdk {design.pdk!r}"]}
    if device_report and device_report["unapplied"]:
        return {
            "status": "unavailable",
            "errors": [
                "device_stress params cannot reach the device models: "
                "wrapper subckts without a delvto/mulu0 forwarding path: "
                + ", ".join(device_report["unapplied"])],
            "device_stress": device_report}

    result = run_netlist(
        "\n".join(deck_parts), simulator=simulator,
        executable=executable, timeout=timeout, workdir=root)
    m = re.search(
        rf"^\s*\.subckt\s+{re.escape(design.name)}\s+(?P<ports>.+?)\s*$",
        netlist, re.MULTILINE)
    ports = m.group("ports").split() if m else [p.name for p in design.ports]
    i_labels = [f"i({e})" for e in _supply_current_elements(stimulus)]
    wave_names = ports + i_labels
    waves_raw = parse_wrdata(root / "waves.dat")
    # v0 is the sweep column; v1..vN map positionally onto `wave_names`
    # (ac keeps the imaginary parts under v{i}i keys).
    if analysis == "ac":
        waves = _ac_waves(wave_names, waves_raw)
    else:
        waves = {wave_names[i - 1]: waves_raw[f"v{i}"]
                 for i in range(1, len(wave_names) + 1) if f"v{i}" in waves_raw}
    out = result.to_dict()
    out["ports"] = ports
    out["sweep"] = waves_raw.get("v0", [])
    out["waves"] = waves
    if device_report is not None:
        out["device_stress"] = device_report
    if analysis == "ac":
        out["ac_source"] = _drive_port(design, ports)
    return out


def _emitter_missing(block_name: str) -> bool:
    try:
        return base.get(block_name).netlist is None
    except KeyError:
        return True


# --- Post-layout simulation ----------------------------------------------
# The extracted netlist is the physical truth: run IT through the foundry
# models, not the golden schematic. Two surgeries make the extracted text
def _rewrite_mos_cards(netlist: str, pdk: str) -> str:
    """Golden-netlist M-cards carry the PDK wrapper subckt name as their
    model token (``M1 d g s b sky130_fd_pr__nfet_01v8 w=.. l=..``). ngspice
    resolves M-cards against .model only, so rewrite them as X-cards onto
    the wrapper subckt — the same trick the extracted path uses."""
    wrappers = set(_leaf_to_wrapper(pdk).values())
    out = []
    for line in netlist.splitlines():
        m = re.match(r"^(M\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)(.*)$",
                     line)
        if m and m.group(6) in wrappers:
            out.append(f"X_{m.group(1)} {m.group(2)} {m.group(3)} "
                       f"{m.group(4)} {m.group(5)} {m.group(6)}{m.group(7)}")
        else:
            out.append(line)
    return "\n".join(out)


# simulatable: leaf M-cards are rewritten as X-cards onto the PDK's wrapper
# subckts (extracted ``nfet_01v8`` -> ``sky130_fd_pr__nfet_01v8`` — an
# M-card cannot call a subckt), and the root circuit's .SUBCKT/.ENDS shell
# is removed so its labelled nets become top-level bias targets (the
# extraction top has no pins — it is the world boundary).

def _leaf_to_wrapper(pdk: str) -> dict[str, str]:
    from layout_canvas.tools.extract import leaf_devices_for

    return {leaf: sub for sub, (leaf, _pol) in leaf_devices_for(pdk).items()}


# --- Parametric device stress (NBTI/HCI proxy) ----------------------------
#
# ``device_stress`` carries the ``Testbench.stress`` device keys
# (``dvth_nmos``/``dvth_pmos``/``mu0_scale``). They are *parametric
# proxies* — injected as BSIM4 instance parameters ``delvto`` (ΔVth0, in
# the model's own sign convention: positive raises an nmos Vth, a pfet
# |Vth| increase takes a *negative* value) and ``mulu0`` (low-field
# mobility multiplier). This is not a foundry aging model: no time
# evolution, no statistics.
#
# Wiring reality (verified on ngspice-47 + bundled sky130 TT models):
# the PDK's MOS are wrapper ``.subckt``s whose inner M-cards forward only
# ``l w ad as pd ps nrd nrs sa sb sd nf`` — a ``delvto`` passed on the
# X-card lands in subckt parameter scope and reaches nothing. The models
# themselves are *subckt-scoped* (``.model`` lives inside the wrapper
# body), so retargeting the M-card at ``<wrapper>__model`` fails
# ("could not find a valid modelname"). The only path that moves the op
# point: inline the model prelude, add ``delvto``/``mulu0`` to each MOS
# wrapper's ``.param`` defaults and forward them on the inner M-card —
# then an injected netlist param rides the X-card into the device.

_M_CARD = re.compile(r"^\s*([mM]\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)(.*)$")
_NMOS_MODEL = ("nfet", "nmos", "nch")
_PMOS_MODEL = ("pfet", "pmos", "pch")
DEVICE_STRESS_KEYS = ("dvth_nmos", "dvth_pmos", "mu0_scale")


def _inject_device_stress(
    netlist: str, device_stress: dict[str, float]
) -> tuple[str, set[str]]:
    """Append BSIM4 stress params to MOS M-cards, by polarity.

    ``dvth_nmos``/``dvth_pmos`` → ``delvto=<v>`` on M-cards whose model
    token names an n- resp. p-channel device (``nfet|nmos|nch`` /
    ``pfet|pmos|pch`` — covers leaf names ``nfet_01v8``, wrapper names
    ``sky130_fd_pr__nfet_01v8`` and generic ``nch``/``pch`` models alike).
    ``mu0_scale`` → ``mulu0=<f>`` on *every* M-card. Returns the patched
    text plus the set of distinct model tokens that received params —
    the caller checks which of them are wrapper subckts needing the
    prelude forward-patch.
    """
    dv_n = device_stress.get("dvth_nmos")
    dv_p = device_stress.get("dvth_pmos")
    mu = device_stress.get("mu0_scale")
    injected: set[str] = set()
    out = []
    for line in netlist.splitlines():
        m = _M_CARD.match(line)
        if m is None:
            out.append(line)
            continue
        tok = m.group(6)
        low = tok.lower()
        existing = line.lower()
        params: list[tuple[str, float]] = []
        if dv_n is not None and any(k in low for k in _NMOS_MODEL):
            params.append(("delvto", float(dv_n)))
        if dv_p is not None and any(k in low for k in _PMOS_MODEL):
            params.append(("delvto", float(dv_p)))
        if mu is not None:
            params.append(("mulu0", float(mu)))
        # a card that already carries a param keeps its own value — the
        # stress request never silently overrides deck text
        add = " ".join(f"{k}={v:g}" for k, v in params if k not in existing)
        if add:
            line = line.rstrip() + " " + add
            injected.add(tok)
        out.append(line)
    return "\n".join(out), injected


_INCLUDE_RE = re.compile(r'^\s*\.include\s+["\']?([^"\'\s]+)["\']?', re.IGNORECASE)


def _expand_includes(
    text: str, base_dir: Path, _seen: set[Path] | None = None
) -> str | None:
    """Inline every ``.include`` recursively (resolved like ngspice:
    relative to the including file's directory). ``None`` when a target
    file is missing — fail-closed, a half-expanded prelude is worse than
    none."""
    if _seen is None:
        _seen = set()
    out: list[str] = []
    for line in text.splitlines():
        m = _INCLUDE_RE.match(line)
        if m is None:
            out.append(line)
            continue
        inc = Path(m.group(1))
        if not inc.is_absolute():
            inc = (base_dir / inc).resolve()
        if inc in _seen or not inc.is_file():
            return None
        _seen.add(inc)
        sub = _expand_includes(
            inc.read_text(encoding="utf-8-sig", errors="replace"),
            inc.parent, _seen)
        if sub is None:
            return None
        out.append(sub)
    return "\n".join(out)


def _expanded_prelude(pdk: str) -> str | None:
    """The model prelude as fully-inlined text (``.include`` resolved).
    ``None`` when the PDK has no resolvable local model text — the
    device-stress path then reports its keys unapplied."""
    from layout_canvas.pdk import get_pdk

    try:
        desc = get_pdk(pdk)
    except KeyError:
        desc = None
    if desc is not None and desc.model_libs.get("spice_prelude_file"):
        lib = Path(desc.model_libs["spice_prelude_file"])
        if not lib.is_absolute():
            lib = Path(desc.base_dir or ".") / lib
        if not lib.is_file():
            return None
        return _expand_includes(
            lib.read_text(encoding="utf-8-sig", errors="replace"),
            lib.parent)
    prelude = default_model_prelude(pdk)
    if not prelude:
        return None
    return _expand_includes(prelude, Path.cwd())


_SUBCKT_RE = re.compile(
    r"^\s*\.subckt\s+(\S+)\b.*?^\s*\.ends[^\n]*",
    re.IGNORECASE | re.MULTILINE | re.DOTALL)


def _patch_prelude_for_device_stress(
    prelude_text: str, wanted: set[str]
) -> tuple[str, set[str]]:
    """Give each ``wanted`` wrapper subckt ``delvto``/``mulu0`` params:
    appended to its ``.param`` defaults (``delvto = 0 mulu0 = 1`` — a
    vanilla instance is unaffected) and forwarded onto every inner
    M-card (``delvto = {delvto} mulu0 = {mulu0}``). Returns the patched
    text and the set of subckt names actually patched; a subckt that
    lacks a patchable ``.param`` line still gets one inserted."""
    patched: set[str] = set()

    def _fix(m: re.Match) -> str:
        name = m.group(1)
        if name not in wanted:
            return m.group(0)
        body = m.group(0)
        mos_lines = [ln for ln in body.splitlines() if _M_CARD.match(ln)]
        if not mos_lines:
            return body  # no inner MOS card — cannot forward
        new_body = body
        for ln in mos_lines:
            if "delvto" in ln.lower() or "mulu0" in ln.lower():
                continue
            new_body = new_body.replace(
                ln, ln.rstrip() + " delvto = {delvto} mulu0 = {mulu0}", 1)
        if new_body == body:
            patched.add(name)
            return body
        pm = re.search(r"^\s*\.param\b[^\n]*", new_body, re.IGNORECASE | re.MULTILINE)
        if pm is not None and "delvto" not in pm.group(0).lower():
            new_body = new_body.replace(
                pm.group(0), pm.group(0).rstrip() + " delvto = 0 mulu0 = 1", 1)
        elif pm is None:
            # declare defaults right after the .subckt header (incl.
            # its '+' pin-list continuations)
            new_body = re.sub(
                r"(^\s*\.subckt[^\n]*\n(?:\s*\+[^\n]*\n)*)",
                lambda h: h.group(1) + ".param delvto = 0 mulu0 = 1\n",
                new_body, count=1, flags=re.IGNORECASE | re.MULTILINE)
        patched.add(name)
        return new_body

    out = _SUBCKT_RE.sub(_fix, prelude_text)
    return out, patched


def _device_stress_prelude(
    pdk: str, injected_models: set[str]
) -> tuple[str | None, dict[str, Any]]:
    """Model-prelude text patched so injected ``delvto``/``mulu0``
    actually reach a device.

    ``injected_models`` are the M-card model tokens the netlist
    injection touched. Tokens that are PDK wrapper subckts (or leaf
    devices mapping to one) *must* be patched — the params ride the
    X-card ``name=value`` into subckt scope and only a patched wrapper
    forwards them to the inner BSIM4 card. Tokens resolving to a real
    ``.model`` take the params directly (ngspice errors at run time if
    the model does not support them — fail-closed). Returns
    ``(prelude_text_or_None, report)``; ``report["unapplied"]`` empty
    means every injected param has a wiring path. ``None`` prelude
    means "keep the default prelude" (nothing needed patching).
    """
    leaf2wrap = _leaf_to_wrapper(pdk)
    wrappers = set(leaf2wrap.values())
    needed = {
        tok if tok in wrappers else leaf2wrap[tok]
        for tok in injected_models if tok in wrappers or tok in leaf2wrap
    }
    report: dict[str, Any] = {
        "wrappers_needed": sorted(needed),
        "patched_wrappers": [],
        "unapplied": [],
    }
    if not needed:
        return None, report
    expanded = _expanded_prelude(pdk)
    if expanded is None:
        report["unapplied"] = sorted(needed)
        return None, report
    patched_text, patched = _patch_prelude_for_device_stress(expanded, needed)
    report["patched_wrappers"] = sorted(patched)
    report["unapplied"] = sorted(needed - patched)
    return patched_text, report


def _unwrap_extracted_top(text: str) -> str | None:
    """Drop the root circuit's .SUBCKT/.ENDS shell so its contents sit at
    deck top level. Returns None when the root cannot be identified."""
    names = re.findall(r"^\s*\.SUBCKT\s+(\S+)", text, flags=re.MULTILINE)
    refs = {m.group(1).upper() for m in re.finditer(
        r"^\s*X\S+\s+.*?(\S+)\s*$", text, flags=re.MULTILINE)}
    roots = [n for n in names if n.upper() not in refs]
    if len(roots) != 1:
        return None
    root = roots[0]
    out, in_root = [], False
    for line in text.splitlines():
        if re.match(rf"^\s*\.SUBCKT\s+{re.escape(root)}\b", line, re.IGNORECASE):
            in_root = True
            continue
        if in_root and re.match(
                rf"^\s*\.ENDS(\s+{re.escape(root)})?\s*$", line, re.IGNORECASE):
            in_root = False
            continue
        out.append(line)
    return "\n".join(out)


def simulate_extracted(
    design: Design,
    *,
    analysis: str = "op",
    vdd: float = 1.8,
    temp: float | None = None,
    tran_stop: str = "5u",
    tran_step: str = "10n",
    stimulus: str | None = None,
    probes: list[str] | None = None,
    simulator: str = "auto",
    executable: str | None = None,
    timeout: int = 120,
    workdir: str | Path | None = None,
    device_stress: dict[str, float] | None = None,
    digital_sources: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Post-layout simulation: compile → GDS → extract → simulate the
    extracted netlist. Fails closed on extraction errors. ``stimulus``
    supplies a caller testbench (sources + .control); ``probes`` adds
    named extracted nets to the wrdata columns. ``temp`` (°C) emits a
    ``.temp`` card — the circuit simulation temperature for every analysis
    in the deck (overrides ``.options temp``, never the models' ``tnom``);
    ``None`` keeps the simulator default.

    ``device_stress``: same parametric aging-proxy keys as
    ``simulate_auto`` (``dvth_nmos``/``dvth_pmos``/``mu0_scale`` → BSIM4
    ``delvto``/``mulu0`` on the extracted MOS cards, plus the wrapper-
    forwarding prelude patch; unwired wrappers → ``unavailable``).

    ``digital_sources`` (node → complete PWL/DC source card) replaces the
    auto-bias on its nodes, exactly like ``simulate_auto`` — except the
    driveable nodes are the *extracted* top-level nets: a node that does
    not survive extraction is an ``error``. With a custom ``stimulus``
    the cards are prepended to the caller text, but a custom source
    already driving the same node is refused (never a double-drive)."""
    from layout_canvas.compiler.compile import compile_design
    from layout_canvas.tools.extract import extract_netlist

    root = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="lc_xsim_"))
    root.mkdir(parents=True, exist_ok=True)
    comp = compile_design(design)
    gds = root / f"{design.name}.gds"
    comp.write_gds(str(gds))
    ext = extract_netlist(str(gds), design.pdk)
    if ext.status != "ok" or ext.errors:
        return {"status": "failed",
                "errors": ["extraction failed: "
                            + "; ".join(ext.errors or [ext.status])]}

    leaf2wrap = _leaf_to_wrapper(design.pdk)
    netlist_text = ext.netlist_text
    if device_stress:
        netlist_text, _stress_models = _inject_device_stress(
            netlist_text, device_stress)
    else:
        _stress_models = set()
    lines = []
    for line in netlist_text.splitlines():
        # Extracted MOS cards are named like 'n$3'/'p$7' with the leaf
        # model as the 6th token — rewrite to X-cards on the PDK wrapper
        # subckts (w/l/as/ad/ps/pd params pass through unchanged).
        m = re.match(r"^(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)(.*)$",
                     line)
        if m and m.group(6) in leaf2wrap and not m.group(1).startswith(
                ("X", ".", "*")):
            safe = re.sub(r"[^A-Za-z0-9_]", "_", m.group(1))
            lines.append(
                f"X_{safe} {m.group(2)} {m.group(3)} {m.group(4)} "
                f"{m.group(5)} {leaf2wrap[m.group(6)]}{m.group(7)}")
        elif line.startswith("D"):
            # Extracted diode cards carry KLayout's A/P parameter names
            # (junction area/perimeter); ngspice wants area/pj —
            # translate, values unchanged (units already SI).
            line = re.sub(r"\bA=", "area=", line)
            lines.append(re.sub(r"\bP=", "pj=", line))
        else:
            lines.append(line)
    flat = _unwrap_extracted_top("\n".join(lines))
    if flat is not None:
        # Extracted instance names carry '$' (X$1) which ngspice cannot
        # parse inside a hierarchical probe vector (x$1.tail fails as
        # "1.tail"). Renaming the instance line is safe — the name is
        # local to the deck and never referenced elsewhere.
        # lowercase too: ngspice stores instance vectors lowercased, and
        # probing v(Xd1.tail) misses while v(xd1.tail) resolves.
        flat = re.sub(r"^X\$(\d+)", r"xd\1", flat, flags=re.MULTILINE)
        # Anonymous nets come out '\$11' — ngspice treats '$' as variable
        # syntax so neither v(\$11) nor v($11) resolves. Rename to plain
        # n<digits> everywhere (token-boundary safe).
        flat = re.sub(r"\\\$(\d+)\b", r"n\1", flat)
    if flat is None:
        return {"status": "failed",
                "errors": ["could not identify the extracted root circuit"]}
    if re.search(r"^\s*C\S+\s+.*\bmim\b", flat, re.MULTILINE):
        flat += "\n.model mim c\n"  # cap cards name 'mim' as their model

    # Bias targets: the design's port names that survive as extracted net
    # names (labels). Anything not extracted is skipped rather than
    # creating a phantom floating node. ``probes`` widens wrdata to any
    # named net in the extracted top (internal nodes like ``tail``);
    # ``stimulus`` replaces the whole auto-bias block with caller text
    # (sources + .control) for a real testbench.
    port_names = [p.name for p in design.ports
                  if re.search(rf"\b{re.escape(p.name)}\b", flat)]
    # Probe resolution: a top-level extracted net probes as v(net); a name
    # that only exists inside an instance subckt (e.g. the diff pair's
    # 'tail') probes hierarchically as v(x<inst>.<name>) — ngspice exposes
    # subcircuit nodes through the instance name. Each entry is
    # (wave label, vector expression).
    subckts = {m.group(1): m.group(0)
               for m in re.finditer(
                   r"^\.SUBCKT\s+(\S+)\s.*?^\.ENDS[^\n]*", flat,
                   re.MULTILINE | re.DOTALL | re.IGNORECASE)}
    # subckt pin lists — for mapping an instance pin to the top net
    sub_pins = {m.group(1): m.group(2).split()
                for m in re.finditer(
                    r"^\.SUBCKT\s+(\S+)\s+([^\n]*)", flat,
                    re.MULTILINE | re.IGNORECASE)}
    top_text = re.sub(r"^\.SUBCKT\s+\S+\s.*?^\.ENDS[^\n]*", "", flat,
                      flags=re.MULTILINE | re.DOTALL | re.IGNORECASE)
    # comment lines ("* pin tail" etc.) are not connectivity — strip them
    # or a pin annotation would resolve as a top-level probe net.
    top_text = re.sub(r"^\*[^\n]*", "", top_text, flags=re.MULTILINE)
    # instance -> (args, subckt name); ngspice collapses subckt PIN nodes
    # onto the parent net, so a pin probe must name the top-level net the
    # pin binds to — v(xd1.tail) fails while v(<topnet>) resolves.
    insts = [(m.group(1), m.group(2).split(), m.group(3))
             for m in re.finditer(
                 r"^([Xx]\S+)\s+(.*?)\s+(\S+)\s*$", top_text, re.MULTILINE)]

    def _resolve(name: str) -> list[tuple[str, str]]:
        if re.search(rf"\b{re.escape(name)}\b", top_text):
            return [(name, f"v({name})")]
        out = []
        for xname, args, subname in insts:
            body = subckts.get(subname, "")
            pins = sub_pins.get(subname, [])
            if name in pins and pins.index(name) < len(args):
                # pin -> the top net bound at that argument position
                top_net = args[pins.index(name)]
                out.append((f"{xname}.{name}", f"v({top_net})"))
            elif re.search(rf"\b{re.escape(name)}\b", body):
                # true internal node survives hierarchically
                out.append((f"{xname}.{name}", f"v({xname}.{name})"))
        return out

    probe_vecs: list[tuple[str, str]] = [(p, f"v({p})") for p in port_names]
    unresolved_probes: list[str] = []
    for extra in probes or []:
        hits = _resolve(extra)
        if not hits:
            unresolved_probes.append(extra)
        for label, vec in hits:
            if vec not in [v for _, v in probe_vecs]:
                probe_vecs.append((label, vec))
    probe_names = [label for label, _ in probe_vecs]

    dig = digital_sources or {}
    dig_l = {n.lower() for n in dig}
    if dig:
        # Every digital node must resolve to a real top-level extracted
        # net — a source on a phantom node drives nothing, silently.
        dead = sorted(n for n in dig
                      if not re.search(rf"\b{re.escape(n)}\b", top_text))
        if dead:
            return {"status": "error",
                    "errors": [
                        "digital stimulus node(s) not present as "
                        "top-level extracted nets: " + ", ".join(dead)]}

    if stimulus:
        stimulus_text = stimulus.rstrip()
        if dig:
            # The custom text owns its sources; adding a PWL on a node it
            # already drives would parallel two voltage sources — refuse.
            conflicts = sorted(
                n for n in dig
                if re.search(rf"^[vViIrR]\S*\s+{re.escape(n)}\s",
                             stimulus_text, re.MULTILINE))
            if conflicts:
                return {"status": "error",
                        "errors": [
                            "digital stimulus node(s) already driven by "
                            "the custom stimulus — refusing to "
                            "double-drive: " + ", ".join(conflicts)]}
            stimulus_text = ("\n".join(dig.values()) + "\n"
                             + stimulus_text)
        i_elems: list[str] = []
    else:
        stimulus_lines = [_bias_line(p, vdd) for p in port_names
                          if p not in dig]
        if not any(p.lower() in ("vdd", "vcc") for p in port_names) \
                and not (dig_l & {"vdd", "vcc"}):
            stimulus_lines.insert(0, f"V_VDD vdd 0 {vdd}")
        if not any(p.lower() in ("vss", "gnd") for p in port_names) \
                and not (dig_l & {"vss", "gnd"}):
            stimulus_lines.insert(0, "V_VSS vss 0 0")
        drive = None
        if analysis in ("ac", "dc"):
            drive = _drive_port(design, port_names)
            if drive is not None:
                stimulus_lines, drive = _apply_drive(
                    stimulus_lines, drive, vdd, analysis)
        # Digital PWL sources stand in for the skipped auto-bias lines.
        stimulus_lines += list(dig.values())
        # supply-branch currents ride the same wrdata row — i(v_<src>) is
        # the positive-sourcing current (see _supply_current_elements).
        i_elems = _supply_current_elements("\n".join(stimulus_lines))
        for e in i_elems:
            probe_vecs.append((f"i({e})", _i_vector(e)))
        probe_names = [label for label, _ in probe_vecs]
        vectors = " ".join(vec for _, vec in probe_vecs)
        run = _sweep_run(analysis, tran_step, tran_stop, drive, vdd)
        stimulus_lines += [".control"]
        stimulus_lines.extend(default_control_prelude(design.pdk))
        stimulus_lines += [run]
        stimulus_lines += [f"let {_i_vector(e)} = -i({e})"
                           for e in i_elems]
        stimulus_lines += [f"wrdata waves.dat {vectors}", ".endc"]
        stimulus_text = "\n".join(stimulus_lines)

    prelude = default_model_prelude(design.pdk)
    device_report: dict[str, Any] | None = None
    if device_stress:
        patched, device_report = _device_stress_prelude(
            design.pdk, _stress_models)
        device_report["requested"] = {
            k: float(v) for k, v in device_stress.items()}
        device_report["injected_models"] = sorted(_stress_models)
        if patched is not None:
            prelude = patched
        if device_report["unapplied"]:
            return {
                "status": "unavailable",
                "errors": [
                    "device_stress params cannot reach the device models: "
                    "wrapper subckts without a delvto/mulu0 forwarding "
                    "path: " + ", ".join(device_report["unapplied"])],
                "device_stress": device_report}
    deck_parts = ["* layout-canvas post-layout deck", ""]
    if prelude:
        deck_parts += [prelude, ""]
    deck_parts += [flat.rstrip(), "", stimulus_text, ""]
    if temp is not None:
        deck_parts += [f".temp {float(temp):g}", ""]
    deck_parts += [".end"]

    result = run_netlist(
        "\n".join(deck_parts), simulator=simulator,
        executable=executable, timeout=timeout, workdir=root)
    waves_raw = parse_wrdata(root / "waves.dat")
    if analysis == "ac":
        waves = _ac_waves(probe_names, waves_raw)
    else:
        waves = {probe_names[i - 1]: waves_raw[f"v{i}"]
                 for i in range(1, len(probe_names) + 1) if f"v{i}" in waves_raw}
    out = result.to_dict()
    out["ports"] = probe_names
    if unresolved_probes:
        out["unresolved_probes"] = unresolved_probes
    out["sweep"] = waves_raw.get("v0", [])
    out["waves"] = waves
    if device_report is not None:
        out["device_stress"] = device_report
    if analysis == "ac":
        # Custom stimulus paths pick their own source; the auto path's
        # choice is reported so the bench is auditable.
        out["ac_source"] = _drive_port(design, port_names) if not stimulus else None
    out["extracted"] = {"devices": ext.devices, "nets": ext.nets}
    return out


def parse_wrdata(path: Path) -> dict[str, list[float]]:
    """ngspice `wrdata` output: each vector is written as a column group
    carrying its own scale — ``(sweep, real)`` for real analyses
    (op/tran/dc) and ``(sweep, real, imag)`` for ac. Returns ``{v0: sweep,
    v1..vN: real samples}`` plus ``{v1i..vNi: imaginary samples}`` mapped
    positionally; wrdata does not embed vector names."""
    if not path.is_file():
        return {}
    rows: list[list[float]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        try:
            vals = [float(x) for x in parts]
        except ValueError:
            continue
        rows.append(vals)
    if not rows:
        return {}
    width = max(len(r) for r in rows)

    def _scale_equal(step: int) -> bool:
        """True when every row repeats its first value at scale positions."""
        for r in rows:
            base = r[0]
            for j in range(step, width, step):
                if j < len(r) and r[j] != base:
                    return False
        return True

    # Complex (ac) files use 3-col groups; real files use 2-col groups.
    # Prefer the 2-col reading when its scale columns line up (ac scale
    # positions 0,2,4,... hit imag/real values and do not repeat).
    group = 2
    if width % 3 == 0 and not (width % 2 == 0 and _scale_equal(2)):
        if _scale_equal(3):
            group = 3
    out: dict[str, list[float]] = {"v0": [r[0] for r in rows]}
    for g in range(width // group):
        re_col, im_col = g * group + 1, g * group + 2
        out[f"v{g + 1}"] = [r[re_col] if re_col < len(r) else 0.0
                            for r in rows]
        out[f"v{g + 1}i"] = [r[im_col] if im_col < len(r) else 0.0
                             for r in rows]
    return out


def _read_log(log_path: Path | None) -> str:
    if log_path and log_path.is_file():
        return log_path.read_text(encoding="utf-8", errors="replace")
    return ""


def _build_deck(netlist: str, stimulus: str, includes: list[str]) -> str:
    lines = ["* layout-canvas simulation deck", ""]
    for inc in includes:
        # Quote paths — SPICE treats whitespace as a delimiter.
        lines.append(f'.include "{inc}"')
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
