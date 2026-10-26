"""Reliability matrix (tools/reliability.py) + device-stress plumbing tests.

Fail-closed contract under test:
- every one of the 14 JEDEC items is always present with a legal status;
- sim-backed items go ``unavailable`` (never fabricated) without a
  simulator or with ``run_sims=False``;
- the parametric-stress path only reports evidence when the injected
  params provably reach a device model (real ngspice op-point movement).
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

import layout_canvas.blocks.generic  # noqa: F401  populate block registry
import layout_canvas.blocks.sky130  # noqa: F401
from layout_canvas.ir.model import Design
from layout_canvas.tools.reliability import reliability_report

REPO = Path(__file__).resolve().parent.parent
OTA = REPO / "bench" / "tasks" / "sky130-ota-stress-corner" / "starter" / "design.json"

ITEM_IDS = [
    "htol", "nbti_aging", "hci_aging", "em_current", "latch_up",
    "antenna", "esd_hbm", "esd_cdm", "thb_hast", "temp_cycle", "htsl",
    "endurance", "fit_rate", "mismatch_aging",
]
LEGAL = {"pass", "fail", "unavailable", "not_applicable", "structural"}


def _ota() -> Design:
    return Design.from_json(OTA.read_text(encoding="utf-8"))


# --- Pure-text injection unit tests ----------------------------------------


def test_inject_device_stress_polarity():
    from layout_canvas.tools.sim import _inject_device_stress

    net = (
        "* c\n"
        ".subckt cell a b c d\n"
        "M1 d g s b sky130_fd_pr__nfet_01v8 w=1u l=1u\n"
        "M2 d2 g s2 b2 sky130_fd_pr__pfet_01v8 w=2u l=1u\n"
        "R1 a b 1k\n"
        ".ends\n"
    )
    out, models = _inject_device_stress(
        net, {"dvth_nmos": 0.03, "dvth_pmos": -0.04, "mu0_scale": 0.85})
    m1 = next(ln for ln in out.splitlines() if ln.startswith("M1"))
    m2 = next(ln for ln in out.splitlines() if ln.startswith("M2"))
    r1 = next(ln for ln in out.splitlines() if ln.startswith("R1"))
    assert "delvto=0.03" in m1 and "mulu0=0.85" in m1
    assert "delvto=-0.04" in m2 and "mulu0=0.85" in m2
    assert "delvto" not in r1 and "mulu0" not in r1
    assert models == {"sky130_fd_pr__nfet_01v8", "sky130_fd_pr__pfet_01v8"}


def test_inject_leaf_names_and_partial_keys():
    from layout_canvas.tools.sim import _inject_device_stress

    # extracted netlists name devices M$<n> with leaf model tokens
    net = "M$1 d g s b nfet_01v8 L=1u W=1u\n"
    out, models = _inject_device_stress(net, {"mu0_scale": 0.9})
    assert "mulu0=0.9" in out and "delvto" not in out
    assert models == {"nfet_01v8"}
    out2, _ = _inject_device_stress(
        "M$1 d g s b nfet_01v8 L=1u\nM$2 d2 g s b pfet_01v8 L=1u\n",
        {"dvth_nmos": 0.05})
    ln1, ln2 = out2.splitlines()
    assert "delvto=0.05" in ln1 and "delvto" not in ln2


def test_patch_prelude_forwards_params():
    from layout_canvas.tools.sim import _patch_prelude_for_device_stress

    prelude = (
        ".subckt sky130_fd_pr__nfet_01v8 d g s b\n"
        "+ w = {w} l = {l}\n"
        ".param l = 1u w = 1u nf = 1.0\n"
        "msky130_fd_pr__nfet_01v8 d g s b sky130_fd_pr__nfet_01v8__model "
        "l = {l} w = {w} nf = {nf}\n"
        ".model sky130_fd_pr__nfet_01v8__model.0 nmos\n"
        ".ends\n")
    out, patched = _patch_prelude_for_device_stress(
        prelude, {"sky130_fd_pr__nfet_01v8"})
    assert patched == {"sky130_fd_pr__nfet_01v8"}
    assert "delvto = 0" in out and "mulu0 = 1" in out
    assert "delvto = {delvto} mulu0 = {mulu0}" in out
    # a wrapper not in `wanted` is untouched
    out2, patched2 = _patch_prelude_for_device_stress(
        prelude, {"other_wrapper"})
    assert patched2 == set() and out2 == prelude


def test_device_stress_prelude_unapplied_without_models():
    """A model token that is a known wrapper but not in the prelude →
    the stress keys are honestly unapplied."""
    from layout_canvas.tools import sim

    text, rep = sim._device_stress_prelude(
        "sky130", {"sky130_fd_pr__nfet_01v8"})
    # bundled sky130 prelude exists → wrapper gets patched
    assert rep["unapplied"] == [] and text is not None
    assert "delvto" in text
    # a PDK with no resolvable prelude cannot wire anything
    text2, rep2 = sim._device_stress_prelude(
        "ihp_sg13g2", {"sg13_lv_nmos"})
    assert rep2["unapplied"] == ["sg13_lv_nmos"]


def test_supply_current_elements():
    from layout_canvas.tools.sim import _supply_current_elements

    stim = "\n".join([
        "V_VDD vdd 0 1.8",
        "V_VSS vss 0 0",
        "V_inp inp 0 0.9",
        "I_tail tail vss 10u",
        "V_ac_out outp 0 0 ac 1",
    ])
    assert _supply_current_elements(stim) == ["v_vdd", "v_vss"]


# --- Stress whitelist -------------------------------------------------------


def test_stress_whitelist_accepts_device_keys():
    from layout_canvas.ir.model import Testbench

    tb = Testbench(name="t", stress={
        "vdd": 1.98, "temp": 125.0,
        "dvth_nmos": 0.03, "dvth_pmos": -0.04, "mu0_scale": 0.85})
    assert tb.stress["mu0_scale"] == 0.85


def test_stress_whitelist_rejects_unknown_key():
    from layout_canvas.ir.model import Testbench

    with pytest.raises(Exception, match="unknown stress key"):
        Testbench(name="t", stress={"bogus": 1.0})


# --- Matrix completeness ----------------------------------------------------


def test_matrix_completeness_and_statuses():
    rep = reliability_report(_ota(), run_sims=False)
    assert rep["release"] == "jesd-aligned-v1"
    assert rep["coverage"]["total"] == len(ITEM_IDS)
    assert [i["id"] for i in rep["items"]] == ITEM_IDS
    for it in rep["items"]:
        assert it["status"] in LEGAL
        assert set(it) >= {"id", "title", "jedec", "evaluated_via",
                           "evidence_level", "status", "reason", "details"}


def test_run_sims_false_marks_sim_items_unavailable():
    rep = reliability_report(_ota(), run_sims=False)
    by = {i["id"]: i for i in rep["items"]}
    for sim_item in ("htol", "nbti_aging", "hci_aging", "em_current",
                     "mismatch_aging"):
        assert by[sim_item]["status"] == "unavailable"
        assert "run_sims=False" in by[sim_item]["reason"]
    # structural items still evaluate without a simulator
    assert by["latch_up"]["status"] == "structural"
    assert by["endurance"]["status"] == "not_applicable"
    # antenna is deterministic — evaluated even with run_sims=False
    assert by["antenna"]["status"] == "pass"
    assert by["antenna"]["evidence_level"] == "deterministic"
    # static domain-boundary items stay unavailable either way
    assert by["fit_rate"]["status"] == "unavailable"


def test_no_simulator_fails_closed():
    rep = reliability_report(
        _ota(), executable="definitely-not-ngspice-xyz", run_sims=True)
    by = {i["id"]: i for i in rep["items"]}
    # the stress-corner bench exists → runs attempted → sim unavailable
    assert by["htol"]["status"] == "unavailable"
    assert by["mismatch_aging"]["status"] == "unavailable"
    # missing declarations stay unavailable with guidance either way
    assert by["nbti_aging"]["status"] == "unavailable"
    assert "dvth_nmos" in by["nbti_aging"]["reason"]
    assert by["em_current"]["status"] == "unavailable"
    assert "current-budget" in by["em_current"]["reason"]
    # structural verdict is not gated on the simulator
    assert by["latch_up"]["status"] == "structural"


# --- Real ngspice -----------------------------------------------------------


def _ngspice() -> str | None:
    return os.environ.get("LAYOUT_CANVAS_NGSPICE") or shutil.which("ngspice")


_HAS_NGSPICE = _ngspice() is not None
_SKY130_TT_LIB = (
    REPO / "examples" / "models" / "sky130" / "sky130_tt.lib")


@pytest.mark.skipif(
    not _HAS_NGSPICE or not _SKY130_TT_LIB.is_file(),
    reason="ngspice or bundled sky130 TT models not present")
def test_delvto_moves_op_point_for_real(tmp_path):
    """Numeric proof: the same op run with/without device stress must
    produce *different* measured op values — string presence alone is
    never enough."""
    from layout_canvas.tools.sim import simulate_auto

    d = _ota()
    base = simulate_auto(d, executable=_ngspice(),
                         workdir=tmp_path / "nom")
    stressed = simulate_auto(
        d, executable=_ngspice(), workdir=tmp_path / "str",
        device_stress={"dvth_nmos": 0.3, "mu0_scale": 0.5})
    assert base["status"] == "passed", base["errors"]
    assert stressed["status"] == "passed", stressed["errors"]
    v0, v1 = base["waves"]["outp"][0], stressed["waves"]["outp"][0]
    assert v0 != pytest.approx(v1, abs=0.01)
    # the device-stress audit trail is honest about what was wired
    assert stressed["device_stress"]["unapplied"] == []
    assert (set(stressed["device_stress"]["patched_wrappers"])
            == {"sky130_fd_pr__nfet_01v8", "sky130_fd_pr__pfet_01v8"})


@pytest.mark.skipif(
    not _HAS_NGSPICE or not _SKY130_TT_LIB.is_file(),
    reason="ngspice or bundled sky130 TT models not present")
def test_supply_current_and_em_spec(tmp_path):
    """i(v_vdd) is captured on the auto stimulus path and a spec on it
    evaluates through the normal measure/bound machinery."""
    from layout_canvas.tools.testbench import run_testbench

    d = _ota().model_dump()
    d["testbenches"] = [{
        "name": "tb_em", "source": "schematic", "analysis": "op",
        "vdd": 1.8,
        "specs": [
            {"name": "idd_ok", "signal": "i(v_vdd)", "measure": "final",
             "max": 4e-4, "unit": "A"},
            {"name": "idd_too_tight", "signal": "i(v_vdd)",
             "measure": "final", "max": 1e-6, "unit": "A"}],
    }]
    res = run_testbench(Design.model_validate(d), "tb_em")
    assert res["status"] == "passed", res.get("errors")
    waves = res["waves"]
    assert "i(v_vdd)" in waves and waves["i(v_vdd)"][0] > 0
    specs = {s["name"]: s for s in res["specs"]}
    assert specs["idd_ok"]["status"] == "pass"
    assert specs["idd_too_tight"]["status"] == "fail"


@pytest.mark.skipif(
    not _HAS_NGSPICE or not _SKY130_TT_LIB.is_file(),
    reason="ngspice or bundled sky130 TT models not present")
def test_reliability_with_sims(tmp_path):
    """With a live simulator the OTA's stress bench lights htol up and
    the drift spec lights mismatch_aging; items needing declarations
    stay unavailable with honest guidance."""
    rep = reliability_report(_ota(), executable=_ngspice(), run_sims=True)
    by = {i["id"]: i for i in rep["items"]}
    assert by["htol"]["status"] == "pass"
    assert by["htol"]["evidence_level"] == "stress_corner"
    assert by["mismatch_aging"]["status"] == "pass"
    assert by["em_current"]["status"] == "unavailable"
    assert by["nbti_aging"]["status"] == "unavailable"
    assert rep["coverage"]["evaluated"] >= 4


@pytest.mark.skipif(
    not _HAS_NGSPICE or not _SKY130_TT_LIB.is_file(),
    reason="ngspice or bundled sky130 TT models not present")
def test_parametric_stress_item(tmp_path):
    """A testbench declaring device-stress keys activates nbti/hci at
    the parametric_stress rung."""
    d = _ota().model_dump()
    d["testbenches"].append({
        "name": "tb_aging", "source": "schematic", "analysis": "op",
        "vdd": 1.8,
        "stress": {"vdd": 1.98, "temp": 125.0,
                   "dvth_nmos": 0.03, "mu0_scale": 0.85},
        "specs": [{"name": "o", "signal": "outp", "measure": "final",
                   "on": "stress", "min": 0.3, "max": 1.2, "unit": "V"}],
    })
    rep = reliability_report(
        Design.model_validate(d), executable=_ngspice(), run_sims=True)
    by = {i["id"]: i for i in rep["items"]}
    assert by["nbti_aging"]["status"] == "pass"
    assert by["nbti_aging"]["evidence_level"] == "parametric_stress"
    assert by["hci_aging"]["status"] == "pass"
    assert by["em_current"]["status"] == "unavailable"  # no i() spec
