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
    engine: str = "auto",  # auto | netgen | pya
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
    if engine == "pya" or (engine == "auto" and not _has_executable(executable)):
        return _run_pya_lvs(gds, spice, cell_name, tech, report)
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
        return LVSResult("failed", False, report, cell, setup, proc.stdout,
                         proc.stderr, proc.returncode,
                         ["LVS mismatch" if not match else "Netgen exited non-zero"])
    return LVSResult("passed", True, report, cell, setup, proc.stdout,
                     proc.stderr, proc.returncode, [])


def _has_executable(exe: str) -> bool:
    return shutil.which(exe) is not None or Path(exe).is_file()


def _run_pya_lvs(
    gds: Path, spice: Path, cell_name: str | None, tech: str, report: Path | None
) -> LVSResult:
    """In-process LVS: KLayout engine extraction + NetlistComparer.

    The reference SPICE is augmented with device-abstract wrappers so leaf
    X-cards resolve to single MOS4 devices, matching extraction granularity.
    Comparison is topology-level (device parameters cleared); a ``failed``
    result means the extracted connectivity genuinely differs — e.g. a layout
    with no internal wiring will not match, and that is a real finding.
    """
    try:
        import klayout.db as db
    except ImportError:
        return _unavailable(report, "klayout python module not installed")
    from .extract import extract_netlist, lvs_device_wrappers

    ext = extract_netlist(gds, tech)
    if ext.status != "ok":
        return _unavailable(report, "; ".join(ext.errors) or "extraction failed")
    # The in-memory extracted netlist keeps implicit (unlinked) terminals;
    # round-tripping through the SPICE file materializes real net objects
    # the NetlistComparer can traverse.
    ext_path = gds.with_suffix(".extracted.cir")
    ext_path.write_text(ext.netlist_text, encoding="utf-8")
    nl_a = db.Netlist()
    nl_a.read(str(ext_path), db.NetlistSpiceReader())

    ref_text = spice.read_text(encoding="utf-8", errors="replace")
    wrappers = lvs_device_wrappers(tech)
    ref_path = spice.with_suffix(".lvs.ref.spice")
    ref_path.write_text(ref_text + "\n" + wrappers + "\n", encoding="utf-8")
    nl_b = db.Netlist()
    try:
        nl_b.read(str(ref_path), db.NetlistSpiceReader())
    except RuntimeError as exc:
        return LVSResult("error", None, None, cell_name, None,
                         "", "", None, [f"reference netlist parse failed: {exc}"])

    def _flatten_to_top(nl, top_name):
        while True:
            subs = [c for c in nl.each_circuit()
                    if c.name.upper() != top_name.upper()]
            if not subs:
                break
            nl.flatten_circuit(subs[0])
        return next((c for c in nl.each_circuit()
                     if c.name.upper() == top_name.upper()), None)

    top_name = cell_name or _top_circuit_name(nl_a)
    ext_top = _flatten_to_top(nl_a, top_name) if top_name else None
    # The reference side picks the circuit named like the extracted top:
    # device-abstract wrappers appended above are unreferenced subcircuits,
    # so the unique-top heuristic fails on designs that instantiate none
    # of them (e.g. a connectivity-only guard ring).
    # The reference side may name its top subckt differently from the GDS
    # cell (block-level calls use the cell name, e.g. "diff_pair_f4_...",
    # while the emitter calls the subckt "diff_pair"). Prefer an exact
    # match on cell_name, then on the extracted top name, and fall back to
    # the unique-top heuristic — device-abstract wrappers are unreferenced
    # subcircuits, so ambiguity means there is no clear reference top.
    ref_name = None
    for cand in dict.fromkeys(x for x in (cell_name, top_name) if x):
        ref_name = next((c.name for c in nl_b.each_circuit()
                         if c.name.upper() == cand.upper()), None)
        if ref_name:
            break
    ref_name = ref_name or _ref_top_name(nl_b, top_name or cell_name)
    ref_top = _flatten_to_top(nl_b, ref_name) if ref_name else None
    if ext_top is None or ref_top is None:
        return LVSResult("error", None, None, cell_name, None, "", "", None,
                         [f"top circuit not found (ext={top_name} ref={ref_name})"])

    # Capacitor classes do not combine in parallel by default — enable it
    # so a unit-cap array folds into the single reference C like MOS
    # fingers fold into one device. C is compared with a 5% relative
    # tolerance: the extracted value tracks the drawn plate overlap,
    # which sits on the 5nm grid while the reference formula is exact.
    for nl in (nl_a, nl_b):
        for dc in nl.each_device_class():
            if "CAP" in dc.name.upper() or "MIM" in dc.name.upper():
                dc.supports_parallel_combination = True
                dc.equal_parameters = db.EqualDeviceParameters(
                    db.DeviceClassCapacitor.PARAM_C, 0.0, 0.05)
    nl_a.combine_devices()
    nl_b.combine_devices()
    # Compare W/L for real — geometry honours declared params, so a
    # width/length mismatch is a genuine failure. Only layout-derived
    # parasitics (AS/AD/PS/PD...) are exempted: the reference netlist
    # does not specify them.
    for nl in (nl_a, nl_b):
        for dc in nl.each_device_class():
            for pd in dc.parameter_definitions():
                if pd.name.upper() not in ("W", "L"):
                    pd.is_primary = False

    cmp = db.NetlistComparer()
    cmp.same_circuits(ext_top, ref_top)
    # Device class names differ across the two flows (extractor emits
    # "nfet_01v8", the reference uses "sky130_fd_pr__nfet_01v8") — pair
    # them explicitly by polarity or the comparer reports no devices.
    ext_classes = {dc.name.upper(): dc for dc in nl_a.each_device_class()}
    ref_classes = {dc.name.upper(): dc for dc in nl_b.each_device_class()}
    # reference class -> device count in the reference top (device-abstract
    # wrappers create empty classes; pairing against one yields nothing)
    def _dev_count(top, dc):
        return sum(1 for dev in top.each_device()
                   if dev.device_class() is dc or
                   dev.device_class().name.upper() == dc.name.upper())

    # Pair by device polarity, not by a literal family string: sky130
    # classes carry NFET/PFET while IHP uses NMOS/PMOS — keying on one
    # vocabulary silently pairs nothing on the other.
    def _pol(name: str) -> str:
        if "CAP" in name or "MIM" in name:
            return "CAP"
        if "PFET" in name or "PMOS" in name:
            return "P"
        if "NFET" in name or "NMOS" in name:
            return "N"
        return name

    for aname, adc in ext_classes.items():
        key = _pol(aname)
        best = None
        for bname, bdc in ref_classes.items():
            if _pol(bname) == key and _dev_count(ref_top, bdc) > 0:
                best = bdc
                break
        if best is not None:
            cmp.same_device_classes(adc, best)
    match = cmp.compare(nl_a, nl_b)
    net_diff = [] if match else _net_terminal_diff(ext_top, ref_top, _pol)

    if report:
        ext_count = len(list(ext_top.each_device()))
        ref_count = len(list(ref_top.each_device()))
        report.write_text(
            f"LVS (klayout-pya engine): {'MATCH' if match else 'MISMATCH'}\n"
            f"extracted devices: {ext_count}, reference devices: {ref_count}\n"
            f"extraction errors: {ext.errors}\n",
            encoding="utf-8",
        )
    status = "passed" if match else "failed"
    return LVSResult(
        status, match, report if report and report.exists() else None,
        ext_top.name, None,
        errors=[] if match else ["netlist topology mismatch"],
        unmatched_nets=net_diff or None,
    )


def _net_terminal_diff(ext_top, ref_top, class_key) -> list[str]:
    """Named nets whose terminal signature differs between the flattened
    tops — the actionable part of an LVS mismatch. Signature = multiset
    of (polarity-class, terminal) pairs hanging on the net. Unnamed $N
    nets are skipped: they have no user-visible handle."""

    def sig(circuit) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for dev in circuit.each_device():
            cls = class_key(dev.device_class().name.upper())
            for term in dev.device_class().terminal_definitions():
                net = dev.net_for_terminal(term.name)
                if net is None:
                    continue
                name = net.expanded_name() or net.name
                if name.startswith("$"):
                    continue
                out.setdefault(name.lower(), []).append(
                    f"{cls}.{term.name}")
        return {k: sorted(v) for k, v in out.items()}

    ext_sig, ref_sig = sig(ext_top), sig(ref_top)
    diff = []
    for name in sorted(set(ext_sig) | set(ref_sig)):
        if ext_sig.get(name) != ref_sig.get(name):
            diff.append(name)
    return diff


def _top_circuit_name(nl) -> str | None:
    referenced = set()
    for c in nl.each_circuit():
        for sc in c.each_subcircuit():
            referenced.add(sc.circuit_ref().name)
    tops = [c.name for c in nl.each_circuit() if c.name not in referenced]
    return tops[0] if len(tops) == 1 else None


def _ref_top_name(nl, hint: str | None) -> str | None:
    """Pick the reference top when several unreferenced circuits exist.

    Device-abstract wrappers appended to the reference file are unreferenced
    subcircuits, so the plain unique-top heuristic fails. Prefer a circuit
    whose name is contained in (or contains) the extracted top name —
    emitters name subckts after the block ("diff_pair") while cells carry
    parameter suffixes ("diff_pair_f4_w1.0_l0.15"). Otherwise take the
    unreferenced circuit with the most devices; wrappers have none.
    """
    referenced = set()
    for c in nl.each_circuit():
        for sc in c.each_subcircuit():
            referenced.add(sc.circuit_ref().name)
    tops = [c for c in nl.each_circuit() if c.name not in referenced]
    if len(tops) == 1:
        return tops[0].name
    if hint:
        h = hint.upper()
        named = [t.name for t in tops
                 if t.name.upper() in h or h in t.name.upper()]
        if len(named) == 1:
            return named[0]
    by_dev = sorted(tops,
                    key=lambda c: sum(1 for _ in c.each_device()),
                    reverse=True)
    if by_dev and sum(1 for _ in by_dev[0].each_device()) > 0:
        return by_dev[0].name
    return None


def _default_setup(tech: str) -> Path | None:
    value = os.environ.get("NETGEN_SETUP") or os.environ.get(f"{tech.upper()}_NETGEN_SETUP")
    return Path(value) if value else None


def _unavailable(report: Path | None, message: str) -> LVSResult:
    return LVSResult("unavailable", None,
                     report if report and report.exists() else None,
                     None, None, errors=[message])


def _text(value: Any) -> str:
    return value.decode(errors="replace") if isinstance(value, bytes) else (value or "")


def _check_match(report: str) -> bool | None:
    if "circuits match uniquely" in report.lower():
        return True
    if ("do not match" in report.lower() or "mismatch" in report.lower()
            or "net mismatch" in report.lower()):
        return False
    return None
