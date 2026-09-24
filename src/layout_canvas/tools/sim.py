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

import os
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
    if pdk == "sky130":
        lib = repo / "examples" / "models" / "sky130" / "sky130_tt.lib"
        if lib.is_file():
            return f'.include "{lib.as_posix()}"'
        return ""
    if pdk == "ihp_sg13g2":
        models = Path(os.environ.get(
            "LAYOUT_CANVAS_IHP_MODELS",
            r"E:\Agentic TCAD\PDK\official_sources\IHP-Open-PDK"
            r"\ihp-sg13g2\libs.tech\ngspice\models"))
        mos = models / "cornerMOSlv.lib"
        if not mos.is_file():
            return ""
        return (f'.lib "{mos.as_posix()}" mos_tt\n'
                ".param pre_layout=1")
    return ""


def default_control_prelude(pdk: str) -> list[str]:
    """.control lines that must run before the analysis (OSDI loads etc.)."""
    if pdk == "ihp_sg13g2":
        osdi_dirs = ([Path(os.environ["LAYOUT_CANVAS_OSDI_DIR"])]
                     if os.environ.get("LAYOUT_CANVAS_OSDI_DIR")
                     else [Path(r"E:\Reliability-PINN-Lab\.tmp\ngspice47"
                                r"\Spice64\lib\ngspice")])
        lines = []
        for d in osdi_dirs:
            for name in ("psp103.osdi", "psp103_nqs.osdi"):
                f = d / name
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


def default_stimulus(
    design: Design,
    *,
    vdd: float = 1.8,
    analysis: str = "op",
    tran_stop: str = "5u",
    tran_step: str = "10n",
    out_file: str = "waves.dat",
) -> str:
    """Auto-generated stimulus for the canvas Simulate button: instantiate
    the compiled top, bias every port by name heuristics, run .op or .tran,
    and wrdata every port voltage for the UI to plot."""
    nl = compile_netlist(design)
    m = re.search(
        rf"^\s*\.subckt\s+{re.escape(design.name)}\s+(?P<ports>.+?)\s*$",
        nl, re.MULTILINE)
    ports = m.group("ports").split() if m else [p.name for p in design.ports]
    lines = [_bias_line(p, vdd) for p in ports]
    # The bias network references implicit rail nodes vdd/vss; if the design
    # doesn't expose them as ports, source them so nothing floats.
    if not any(p.lower() in ("vdd", "vcc") for p in ports):
        lines.insert(0, f"V_VDD vdd 0 {vdd}")
    if not any(p.lower() in ("vss", "gnd") for p in ports):
        lines.insert(0, "V_VSS vss 0 0")
    lines.append(f"X1 {' '.join(ports)} {design.name}")
    vectors = " ".join(f"v({p})" for p in ports)
    run = "op" if analysis == "op" else f"tran {tran_step} {tran_stop}"
    lines.append(".control")
    lines.extend(default_control_prelude(design.pdk))
    lines.append(run)
    lines.append(f"wrdata {out_file} {vectors}")
    lines.append(".endc")
    return "\n".join(lines)


def simulate_auto(
    design: Design,
    *,
    analysis: str = "op",
    vdd: float = 1.8,
    tran_stop: str = "5u",
    tran_step: str = "10n",
    simulator: str = "auto",
    executable: str | None = None,
    timeout: int = 120,
    workdir: str | Path | None = None,
) -> dict[str, Any]:
    """One-click simulation for the canvas: model prelude + auto stimulus +
    backend run + waveform extraction. Returns the SimResult fields plus
    ``ports`` (bias vector order) and ``waves`` (parsed wrdata columns).
    """
    root = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="lc_sim_"))
    root.mkdir(parents=True, exist_ok=True)
    prelude = default_model_prelude(design.pdk)
    stimulus = default_stimulus(
        design, vdd=vdd, analysis=analysis,
        tran_stop=tran_stop, tran_step=tran_step, out_file="waves.dat")
    netlist = compile_netlist(design)
    deck_parts = ["* layout-canvas auto deck", ""]
    if prelude:
        deck_parts += [prelude, ""]
    deck_parts += [netlist.rstrip(), "", stimulus.rstrip(), "", ".end"]

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

    result = run_netlist(
        "\n".join(deck_parts), simulator=simulator,
        executable=executable, timeout=timeout, workdir=root)
    m = re.search(
        rf"^\s*\.subckt\s+{re.escape(design.name)}\s+(?P<ports>.+?)\s*$",
        netlist, re.MULTILINE)
    ports = m.group("ports").split() if m else [p.name for p in design.ports]
    waves_raw = parse_wrdata(root / "waves.dat")
    # v0 is the sweep column; v1..vN map positionally onto `ports`.
    waves = {ports[i - 1]: waves_raw[f"v{i}"]
             for i in range(1, len(ports) + 1) if f"v{i}" in waves_raw}
    out = result.to_dict()
    out["ports"] = ports
    out["sweep"] = waves_raw.get("v0", [])
    out["waves"] = waves
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
    from layout_canvas.tools.extract import LEAF_DEVICES

    return {leaf: sub for sub, (leaf, _pol) in LEAF_DEVICES.get(pdk, {}).items()}


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
    tran_stop: str = "5u",
    tran_step: str = "10n",
    stimulus: str | None = None,
    probes: list[str] | None = None,
    simulator: str = "auto",
    executable: str | None = None,
    timeout: int = 120,
    workdir: str | Path | None = None,
) -> dict[str, Any]:
    """Post-layout simulation: compile → GDS → extract → simulate the
    extracted netlist. Fails closed on extraction errors. ``stimulus``
    supplies a caller testbench (sources + .control); ``probes`` adds
    named extracted nets to the wrdata columns."""
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
    lines = []
    for line in ext.netlist_text.splitlines():
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

    if stimulus:
        stimulus_text = stimulus.rstrip()
    else:
        stimulus_lines = [_bias_line(p, vdd) for p in port_names]
        if not any(p.lower() in ("vdd", "vcc") for p in port_names):
            stimulus_lines.insert(0, f"V_VDD vdd 0 {vdd}")
        if not any(p.lower() in ("vss", "gnd") for p in port_names):
            stimulus_lines.insert(0, "V_VSS vss 0 0")
        vectors = " ".join(vec for _, vec in probe_vecs)
        run = "op" if analysis == "op" else f"tran {tran_step} {tran_stop}"
        stimulus_lines += [".control"]
        stimulus_lines.extend(default_control_prelude(design.pdk))
        stimulus_lines += [run, f"wrdata waves.dat {vectors}", ".endc"]
        stimulus_text = "\n".join(stimulus_lines)

    prelude = default_model_prelude(design.pdk)
    deck_parts = ["* layout-canvas post-layout deck", ""]
    if prelude:
        deck_parts += [prelude, ""]
    deck_parts += [flat.rstrip(), "", stimulus_text, "", ".end"]

    result = run_netlist(
        "\n".join(deck_parts), simulator=simulator,
        executable=executable, timeout=timeout, workdir=root)
    waves_raw = parse_wrdata(root / "waves.dat")
    waves = {probe_names[i - 1]: waves_raw[f"v{i}"]
             for i in range(1, len(probe_names) + 1) if f"v{i}" in waves_raw}
    out = result.to_dict()
    out["ports"] = probe_names
    if unresolved_probes:
        out["unresolved_probes"] = unresolved_probes
    out["sweep"] = waves_raw.get("v0", [])
    out["waves"] = waves
    out["extracted"] = {"devices": ext.devices, "nets": ext.nets}
    return out


def parse_wrdata(path: Path) -> dict[str, list[float]]:
    """ngspice `wrdata` output: first column is the sweep, then each vector
    is written as an interleaved (real, imag) column pair — DC/tran data has
    zero imaginary parts. Returns {v0: sweep, v1..vN: real samples} mapped
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
    out: dict[str, list[float]] = {"v0": [r[0] for r in rows]}
    for i in range(1, width, 2):
        out[f"v{(i + 1) // 2}"] = [r[i] if i < len(r) else 0.0 for r in rows]
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
