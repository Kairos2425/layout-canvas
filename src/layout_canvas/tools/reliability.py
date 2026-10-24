"""JEDEC-aligned reliability evaluation matrix (release ``jesd-aligned-v1``).

One entry per JEDEC test method the tool can speak about — every item is
present in every report (coverage is complete by construction) and carries
an honest verdict:

- ``pass`` / ``fail`` — evaluated against real evidence;
- ``structural`` — passed at *structural* evidence grade only (block-level
  heuristic, not physical verification);
- ``not_applicable`` — adjudicated: the method does not apply to this
  design (e.g. no NVM);
- ``unavailable`` — the capability or declaration needed to evaluate is
  missing; ``reason`` names exactly what is missing and how to activate it.

``evidence_level`` mirrors the bench reliability ladder:
``deterministic`` < ``solver_verified`` < ``stress_corner`` <
``parametric_stress`` (device-parameter injection — a transparent
BSIM4 ``delvto``/``mulu0`` proxy, **not** a foundry aging model) <
``aging_model`` (never claimed) < ``silicon_calibrated`` (never claimed).

Fail-closed throughout: no simulator → every sim-backed item is
``unavailable``; ``run_sims=False`` evaluates only the structural items.
"""

from __future__ import annotations

import os
import re
from typing import Any

from layout_canvas.blocks import base
from layout_canvas.ir.model import Design

RELEASE = "jesd-aligned-v1"

_DEVICE_VTH_KEYS = ("dvth_nmos", "dvth_pmos")
_DEVICE_MU_KEYS = ("mu0_scale",)
DEVICE_STRESS_KEYS = _DEVICE_VTH_KEYS + _DEVICE_MU_KEYS

_CURRENT_SIGNAL = re.compile(r"^i\(v_\w+\)$")

# Latch-up structural scan (JESD78): block generators in this allowlist
# build their own substrate taps / well contacts; ``gen_*`` blocks do the
# same by construction; anything else MOS-bearing (sky130.strongarm,
# import_gds external cells, unregistered blocks) is needs-review. This is
# a block-level heuristic — structural-grade evidence, NOT a physical
# latch-up verification.
_LATCHUP_ALLOWLIST = ("diff_pair", "current_mirror", "ota_5t", "guard_ring")

_ESD_TOKENS = ("esd", "clamp")
_NVM_TOKENS = ("nvm", "flash", "efuse", "otp", "fetmos", "fgmos")

_STATUSES = ("pass", "fail", "unavailable", "not_applicable", "structural")


def _item(item_id: str, title: str, jedec: str, via: str, level: str,
          status: str, reason: str = "",
          details: dict[str, Any] | None = None) -> dict[str, Any]:
    assert status in _STATUSES, status
    return {
        "id": item_id,
        "title": title,
        "jedec": jedec,
        "evaluated_via": via,
        "evidence_level": level,
        "status": status,
        "reason": reason,
        "details": details or {},
    }


def _roll(entries: list[dict[str, Any]]) -> str:
    """Roll spec-entry verdicts up to an item status."""
    if not entries:
        return "unavailable"
    st = {e["status"] for e in entries}
    if "fail" in st:
        return "fail"
    if "unavailable" in st:
        return "unavailable"
    return "pass"


def _spec_entries(run: dict[str, Any] | None,
                  on: tuple[str, ...] | None = None) -> list[dict[str, Any]]:
    if not run:
        return []
    return [e for e in run.get("specs", [])
            if on is None or e.get("on") in on]


def _is_parametric(tb) -> bool:
    return bool(tb.stress) and any(k in tb.stress for k in DEVICE_STRESS_KEYS)


def _stress_evidence_level(tb) -> str:
    return "parametric_stress" if _is_parametric(tb) else "stress_corner"


# ---------------------------------------------------------------------------
# Sim-backed items
# ---------------------------------------------------------------------------

def _htol(design: Design, tb_run, run_sims: bool) -> dict[str, Any]:
    """JESD22-A108 — testbenches declaring a stress corner are the
    HTOL-style accelerated operating-life proxy."""
    benches = [tb for tb in design.testbenches if tb.stress]
    via = "stress_corner testbench (nominal + accelerated V/T run)"
    details: dict[str, Any] = {
        "stress_testbenches": [t.name for t in benches]}
    if not run_sims:
        return _item("htol", "High-Temperature Operating Life",
                     "JESD22-A108", via, "stress_corner", "unavailable",
                     "needs live simulation runs (run_sims=False)",
                     details)
    if not benches:
        return _item("htol", "High-Temperature Operating Life",
                     "JESD22-A108", via, "stress_corner", "unavailable",
                     "no testbench declares a stress corner — add "
                     "Testbench.stress {vdd, temp} to activate HTOL "
                     "evaluation", details)
    entries: list[dict[str, Any]] = []
    per_tb: dict[str, str] = {}
    for tb in benches:
        run = tb_run(tb.name)
        es = _spec_entries(run)
        entries.extend(es)
        per_tb[tb.name] = run.get("spec_status") or "no_specs"
    details["bench_status"] = per_tb
    if not entries:
        return _item("htol", "High-Temperature Operating Life",
                     "JESD22-A108", via, "stress_corner", "unavailable",
                     "stress testbenches carry no specs to evaluate",
                     details)
    status = _roll(entries)
    reason = ("" if status == "pass" else
              f"specs: {sum(1 for e in entries if e['status'] == 'fail')} "
              f"failed / {len(entries)}")
    return _item("htol", "High-Temperature Operating Life",
                 "JESD22-A108", via, "stress_corner", status, reason,
                 details | {"specs": entries})


def _parametric(design: Design, tb_run, run_sims: bool, *,
                item_id: str, title: str, keys: tuple[str, ...],
                guide: str) -> dict[str, Any]:
    """JEP122 NBTI/HCI — a stress corner that also injects the device-level
    proxy keys is a transparent parametric-stress evaluation."""
    benches = [tb for tb in design.testbenches
               if tb.stress and any(k in tb.stress for k in keys)]
    via = "parametric device stress (BSIM4 delvto/mulu0 injection)"
    details: dict[str, Any] = {
        "proxy_keys": list(keys),
        "parametric_testbenches": [t.name for t in benches],
    }
    if not run_sims:
        return _item(item_id, title, "JEP122", via, "parametric_stress",
                     "unavailable",
                     "needs live simulation runs (run_sims=False)",
                     details)
    if not benches:
        return _item(item_id, title, "JEP122", via, "parametric_stress",
                     "unavailable", guide, details)
    entries: list[dict[str, Any]] = []
    per_tb: dict[str, str] = {}
    for tb in benches:
        run = tb_run(tb.name)
        es = _spec_entries(run, on=("stress", "drift"))
        entries.extend(es)
        per_tb[tb.name] = run.get("spec_status") or "no_specs"
    details["bench_status"] = per_tb
    if not entries:
        return _item(item_id, title, "JEP122", via, "parametric_stress",
                     "unavailable",
                     "parametric-stress testbenches carry no "
                     "on='stress'/'drift' specs to evaluate", details)
    status = _roll(entries)
    return _item(item_id, title, "JEP122", via, "parametric_stress",
                 status, "" if status == "pass" else
                 f"{sum(1 for e in entries if e['status'] == 'fail')} "
                 f"failed / {len(entries)} stress-routed specs",
                 details | {"specs": entries})


def _em_current(design: Design, tb_run, run_sims: bool) -> dict[str, Any]:
    """JEP122 (Black) — a declared ``i(v_*)`` spec is the electromigration
    current budget checked on a real solver run."""
    via = "supply-branch current i(v_*) spec on a live sim run"
    spec_sites = [(tb, s) for tb in design.testbenches for s in tb.specs
                  if _CURRENT_SIGNAL.match(s.signal)]
    details: dict[str, Any] = {
        "current_specs": [
            {"testbench": tb.name, "spec": s.name, "signal": s.signal}
            for tb, s in spec_sites]}
    if not run_sims:
        return _item("em_current", "Electromigration current budget",
                     "JEP122 (Black)", via, "solver_verified", "unavailable",
                     "needs live simulation runs (run_sims=False)", details)
    if not spec_sites:
        return _item(
            "em_current", "Electromigration current budget",
            "JEP122 (Black)", via, "solver_verified", "unavailable",
            "no declared current-budget spec — add a spec with "
            "signal \"i(v_<supply-source>)\" (e.g. i(v_vdd)), measure "
            "final, and a max bound; auto stimulus captures supply "
            "branch currents automatically", details)
    entries: list[dict[str, Any]] = []
    per_tb: dict[str, str] = {}
    for tb, s in spec_sites:
        run = tb_run(tb.name)
        per_tb[tb.name] = run.get("spec_status") or "no_specs"
        for e in run.get("specs", []):
            if e["name"] == s.name:
                entries.append(e)
    details["bench_status"] = per_tb
    status = _roll(entries)
    return _item(
        "em_current", "Electromigration current budget",
        "JEP122 (Black)", via, "solver_verified", status,
        "" if status == "pass" else "current-budget spec(s) not all pass",
        details | {"specs": entries})


def _mismatch_aging(design: Design, tb_run,
                  run_sims: bool) -> dict[str, Any]:
    """JEP122 mismatch/drift — a declared ``on=\"drift\"`` spec is the
    |stress − nominal| evaluation; its rung is parametric_stress when the
    corner also injects device keys, stress_corner otherwise."""
    drift_sites = [(tb, s) for tb in design.testbenches for s in tb.specs
                   if s.on == "drift"]
    details: dict[str, Any] = {
        "drift_specs": [
            {"testbench": tb.name, "spec": s.name, "signal": s.signal}
            for tb, s in drift_sites]}
    levels = {_stress_evidence_level(tb) for tb, _ in drift_sites}
    level = ("parametric_stress" if levels and levels == {"parametric_stress"}
             else "stress_corner")
    via = "drift spec |stress − nominal| on declared corners"
    if not run_sims:
        return _item("mismatch_aging", "Mismatch / drift under aging",
                     "JEP122", via, level, "unavailable",
                     "needs live simulation runs (run_sims=False)", details)
    if not drift_sites:
        return _item(
            "mismatch_aging", "Mismatch / drift under aging",
            "JEP122", via, level, "unavailable",
            "no drift spec declared — add a spec with on=\"drift\" on a "
            "testbench carrying Testbench.stress (vdd/temp corner; add "
            "dvth_*/mu0_scale for the parametric-stress rung)", details)
    entries: list[dict[str, Any]] = []
    per_tb: dict[str, str] = {}
    for tb, s in drift_sites:
        run = tb_run(tb.name)
        per_tb[tb.name] = run.get("spec_status") or "no_specs"
        entries.extend(e for e in run.get("specs", [])
                       if e["name"] == s.name)
    details["bench_status"] = per_tb
    status = _roll(entries)
    return _item("mismatch_aging", "Mismatch / drift under aging",
                 "JEP122", via, level, status,
                 "" if status == "pass" else "drift spec(s) not all pass",
                 details | {"specs": entries})


# ---------------------------------------------------------------------------
# Structural / static items
# ---------------------------------------------------------------------------

def _block_has_mos(inst, blk) -> bool | None:
    """Does this instance's emitted netlist contain MOS cards?
    ``None`` = cannot determine (no emitter / emit raised) — needs-review."""
    if blk.netlist is None:
        return None
    try:
        text = blk.spice(**inst.params)
    except Exception:
        return None
    return bool(re.search(r"(?mi)^m\S+\s", text))


def _latch_up(design: Design) -> dict[str, Any]:
    """JESD78 — structural scan: every MOS-bearing instance's block is
    either self-protecting (allowlist / gen_*) or needs-review; a
    guard-ring instance covers the design."""
    covered: list[str] = []
    uncovered: list[dict[str, str]] = []
    passive: list[str] = []
    guard_ring = False
    for inst in design.instances:
        leaf = inst.block.rsplit(".", 1)[-1]
        if leaf.endswith("guard_ring") or leaf == "guard_ring":
            guard_ring = True
            covered.append(inst.id)
            continue
        try:
            blk = base.get(inst.block)
        except KeyError:
            uncovered.append({"instance": inst.id, "block": inst.block,
                              "note": "unregistered block — needs-review"})
            continue
        tags = {t.lower() for t in (blk.spec.tags or [])}
        has_mos = _block_has_mos(inst, blk)
        if has_mos is False:
            passive.append(inst.id)
            continue
        if has_mos is None or {"external", "gds"} & tags:
            uncovered.append({
                "instance": inst.id, "block": inst.block,
                "note": ("external/imported cell — needs-review"
                         if {"external", "gds"} & tags
                         else "cannot determine MOS content — needs-review")})
            continue
        if leaf in _LATCHUP_ALLOWLIST or leaf.startswith("gen_"):
            covered.append(inst.id)
        else:
            uncovered.append({"instance": inst.id, "block": inst.block,
                              "note": "MOS block outside the self-protection "
                                      "allowlist — needs-review"})
    details = {"covered_instances": covered,
               "needs_review": uncovered,
               "passive_instances": passive,
               "guard_ring_present": guard_ring,
               "allowlist": list(_LATCHUP_ALLOWLIST) + ["gen_*"]}
    via = ("block-level self-protection heuristic (substrate taps/guard "
           "ring built into the generator); NOT a physical latch-up "
           "verification")
    if not covered and not uncovered and not passive:
        return _item("latch_up", "Latch-up (JESD78)", "JESD78", via,
                     "structural", "not_applicable",
                     "design has no instances", details)
    if not covered and not uncovered:
        return _item("latch_up", "Latch-up (JESD78)", "JESD78", via,
                     "structural", "not_applicable",
                     "no MOS-bearing instances — nothing that can latch",
                     details)
    if guard_ring:
        return _item("latch_up", "Latch-up (JESD78)", "JESD78", via,
                     "structural", "structural",
                     "guard-ring instance present; block-level heuristic "
                     "only — not physical latch-up verification", details)
    if not uncovered:
        return _item("latch_up", "Latch-up (JESD78)", "JESD78", via,
                     "structural", "structural",
                     "every MOS-bearing instance is a self-protecting "
                     "generator block — structural evidence only", details)
    names = ", ".join(u["instance"] for u in uncovered)
    return _item("latch_up", "Latch-up (JESD78)", "JESD78", via,
                 "structural", "fail",
                 f"{len(uncovered)} MOS-bearing instance(s) outside the "
                 f"self-protection allowlist: {names}", details)


def _esd(design: Design, item_id: str, title: str, jedec: str) -> dict[str, Any]:
    """JS-001/JS-002 — palette scan for ESD clamp cells; the honest
    verdict lists the ports left unprotected."""
    blocks = base.all_blocks()
    palette_cells = sorted(
        n for n, b in blocks.items()
        if any(t in n.lower() for t in _ESD_TOKENS)
        or any(any(t in str(tag).lower() for t in _ESD_TOKENS)
               for tag in (b.spec.tags or [])))
    instanced = [i.id for i in design.instances
                 if any(t in i.block.lower() for t in _ESD_TOKENS)]
    ports = [p.name for p in design.ports]
    via = "structural palette scan for ESD clamp cells"
    details = {"esd_cells_in_palette": palette_cells,
               "esd_instances": instanced,
               "unprotected_ports": [p for p in ports]}
    if not ports:
        return _item(item_id, title, jedec, via, "structural",
                     "not_applicable",
                     "design exposes no ports — nothing to protect",
                     details)
    if instanced:
        return _item(item_id, title, jedec, via, "structural",
                     "structural",
                     "design instances ESD-clamp block(s); clamp presence "
                     "is structural evidence only — no HBM/CDM strike "
                     "simulation", details)
    return _item(item_id, title, jedec, via, "structural", "unavailable",
                 f"palette has no ESD clamp cell — {len(ports)} external "
                 f"port(s) unprotected: {', '.join(ports) or 'none'}",
                 details)


def _endurance(design: Design) -> dict[str, Any]:
    """JESD22-A117 — NVM cycling only applies when the design actually
    contains non-volatile storage; the palette ships none."""
    nvm_blocks = [n for n in base.all_blocks()
                  if any(t in n.lower() for t in _NVM_TOKENS)]
    nvm_insts = [i.id for i in design.instances
                 if any(t in i.block.lower() for t in _NVM_TOKENS)]
    via = "non-volatile memory presence scan"
    details = {"nvm_blocks_in_palette": nvm_blocks,
               "nvm_instances": nvm_insts}
    if not nvm_insts:
        return _item("endurance", "NVM endurance cycling", "JESD22-A117",
                     via, "structural", "not_applicable",
                     "no non-volatile memory devices in the design (the "
                     "palette ships no NVM/flash cell)", details)
    return _item("endurance", "NVM endurance cycling", "JESD22-A117", via,
                 "structural", "unavailable",
                 "NVM instances present but no endurance model/cycling "
                 "spec — evaluate externally", details)


# ---------------------------------------------------------------------------
# Statically-unavailable items (honest domain boundary)
# ---------------------------------------------------------------------------

_STATIC_UNAVAILABLE = [
    ("antenna", "Process antenna (plasma damage)",
     "PDK antenna rules (JESD-like)", "per-net antenna-ratio extraction",
     "deterministic",
     "unavailable — needs per-net metal/gate area extraction (antenna "
     "ratio per connected gate); the layout compiler does not extract "
     "antenna areas yet"),
    ("thb_hast", "Temperature-humidity bias / HAST",
     "JESD22-A110 / JESD22-A101", "package-level chamber test",
     "out_of_domain",
     "unavailable — humidity is not a simulation parameter; THB/HAST is a "
     "package-level chamber test outside the layout+SPICE domain"),
    ("temp_cycle", "Temperature cycling",
     "JESD22-A104", "thermo-mechanical fatigue analysis",
     "out_of_domain",
     "unavailable — thermo-mechanical fatigue (bump/BEOL/package) is "
     "outside the layout+SPICE domain; needs mechanical/thermal FEA"),
    ("htsl", "High-temperature storage life",
     "JESD22-A103", "storage-node retention evaluation",
     "out_of_domain",
     "unavailable — storage-life test; a design with floating-gate or "
     "storage nodes needs a dedicated retention evaluation, which no "
     "block currently provides"),
    ("fit_rate", "Failure-rate (FIT) estimation",
     "JESD85", "failure-rate extrapolation from models/field data",
     "out_of_domain",
     "unavailable — FIT needs failure-rate extrapolation data (foundry "
     "aging models or field returns); a layout tool cannot produce it"),
]


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def reliability_report(
    design: Design,
    *,
    executable: str | None = None,
    run_sims: bool = True,
) -> dict[str, Any]:
    """Build the JEDEC coverage matrix for ``design``.

    ``executable`` selects the ngspice binary (default
    ``LAYOUT_CANVAS_NGSPICE`` / PATH probe). ``run_sims=False`` evaluates
    only the structural/static items — every sim-backed item reports
    ``unavailable`` instead of blocking.
    """
    from layout_canvas.tools.testbench import run_testbench

    exe = executable or os.environ.get("LAYOUT_CANVAS_NGSPICE")
    runs: dict[str, dict[str, Any]] = {}

    def tb_run(tb_name: str) -> dict[str, Any]:
        if tb_name not in runs:
            runs[tb_name] = run_testbench(design, tb_name, executable=exe)
        return runs[tb_name]

    items: list[dict[str, Any]] = [
        _htol(design, tb_run, run_sims),
        _parametric(
            design, tb_run, run_sims, item_id="nbti_aging",
            title="NBTI aging (Vth drift proxy)", keys=_DEVICE_VTH_KEYS,
            guide="no testbench injects dvth_nmos/dvth_pmos — declare "
                  "stress.dvth_nmos (and/or stress.dvth_pmos, negative "
                  "for the pfet |Vth| increase) to activate the NBTI "
                  "parametric proxy"),
        _parametric(
            design, tb_run, run_sims, item_id="hci_aging",
            title="Hot-carrier injection (mobility-drift proxy)",
            keys=_DEVICE_MU_KEYS,
            guide="no testbench injects mu0_scale — declare "
                  "stress.mu0_scale (e.g. 0.85) on a high-V/T stress "
                  "corner to activate the HCI parametric proxy"),
        _em_current(design, tb_run, run_sims),
        _latch_up(design),
        _esd(design, "esd_hbm", "ESD — human body model", "JS-001"),
        _esd(design, "esd_cdm", "ESD — charged device model", "JS-002"),
        *[_item(i, t, j, via, lvl, "unavailable", r)
          for i, t, j, via, lvl, r in _STATIC_UNAVAILABLE],
        _endurance(design),
        _mismatch_aging(design, tb_run, run_sims),
    ]
    # Item order for readability: antenna/esd are interleaved above only
    # to keep builder order; re-sort into the canonical matrix order.
    order = ["htol", "nbti_aging", "hci_aging", "em_current", "latch_up",
             "antenna", "esd_hbm", "esd_cdm", "thb_hast", "temp_cycle",
             "htsl", "endurance", "fit_rate", "mismatch_aging"]
    by_id = {i["id"]: i for i in items}
    items = [by_id[i] for i in order]

    by_status: dict[str, int] = {}
    for it in items:
        by_status[it["status"]] = by_status.get(it["status"], 0) + 1
    evaluated = sum(v for k, v in by_status.items() if k != "unavailable")
    return {
        "design": design.name,
        "release": RELEASE,
        "run_sims": run_sims,
        "coverage": {"evaluated": evaluated, "total": len(items),
                     "by_status": by_status},
        "items": items,
        "sim_runs": {
            name: {"status": r.get("status"),
                   "spec_status": r.get("spec_status")}
            for name, r in runs.items()},
    }
