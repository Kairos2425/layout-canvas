"""ESD dual-diode clamp: block, extraction, LVS, reliability verdicts.

Covers the sky130.esd_dual_diode generator (pw2nd + pd2nw under the
areaid.diode marker), opt-in diode extraction, and the connectivity-
based structural evaluation of the esd_hbm / esd_cdm reliability items.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

import layout_canvas.blocks.sky130  # noqa: F401 - registers sky130 blocks
from layout_canvas.compiler.compile import compile_design
from layout_canvas.compiler.netlist import compile_netlist
from layout_canvas.ir.model import Design
from layout_canvas.tools.drc import run_drc
from layout_canvas.tools.extract import extract_netlist
from layout_canvas.tools.lvs import run_lvs
from layout_canvas.tools.reliability import reliability_report
from layout_canvas.tools.verify import verify_design

pytest.importorskip("klayout.db", reason="klayout python module required")

REPO = Path(__file__).resolve().parent.parent
OTA_JSON = REPO / "examples" / "ota_lab.json"


def _gds_of(design: Design, tmp_path: Path) -> Path:
    comp = compile_design(design)
    out = tmp_path / f"{design.name}.gds"
    comp.write_gds(str(out))
    return out


def _item(report: dict, item_id: str) -> dict:
    return next(i for i in report["items"] if i["id"] == item_id)


def _ota() -> Design:
    return Design.from_json(OTA_JSON.read_text(encoding="utf-8"))


def _ota_with_clamps(*, drop_port: str | None = None,
                     at_x: dict[str, float] | None = None) -> Design:
    """ota_lab + one esd_dual_diode per external port, wired pad->port
    net, vdd/vss onto the supply nets. ``at_x`` overrides the absolute
    x placement for the named ports' clamps."""
    data = _ota().model_dump()
    insts = data["instances"]
    nets = {n["name"]: n for n in data["nets"]}
    pins_in_net = {p for n in data["nets"] for p in n["pins"]}
    for i, port in enumerate(data["ports"]):
        iid = f"ESD_{port['name'].upper()}"
        if port["name"] == drop_port:
            continue
        insts.append({
            "id": iid,
            "block": "sky130.esd_dual_diode",
            "params": {},
            "placement": {"x": (at_x or {}).get(port["name"],
                                               60.0 + 50.0 * i),
                          "y": -20.0, "rotation": 0, "mirror": False},
        })
        pin = port["pin"]
        if pin in pins_in_net:
            net = next(n for n in data["nets"] if pin in n["pins"])
            net["pins"].append(f"{iid}.pad")
        else:
            data["nets"].append({"name": port["name"],
                                 "pins": [pin, f"{iid}.pad"]})
        nets["vdd"]["pins"].append(f"{iid}.vdd")
        nets["vss"]["pins"].append(f"{iid}.vss")
    return Design.model_validate(data)


# --- block-level: geometry, extraction, LVS --------------------------------

def test_esd_block_drc_extract_lvs(tmp_path: Path):
    from layout_canvas.blocks import base

    comp = base.get("sky130.esd_dual_diode").component()
    gds = tmp_path / "esd.gds"
    comp.write_gds(str(gds))

    drc = run_drc(str(gds), tech="sky130", engine="pya")
    assert drc.status == "passed" and drc.total_violations == 0

    ext = extract_netlist(gds, "sky130")
    assert ext.status == "ok" and ext.errors == []
    assert ext.devices == 2
    assert "sky130_fd_pr__diode_pw2nd_05v5" in ext.netlist_text
    assert "sky130_fd_pr__diode_pd2nw_05v5" in ext.netlist_text
    assert not any(ln.startswith(("M", "X")) for ln in
                   ext.netlist_text.splitlines())

    design = Design.model_validate({
        "name": "esd_self", "pdk": "sky130",
        "instances": [{"id": "ESD", "block": "sky130.esd_dual_diode",
                       "params": {}}],
        "ports": [{"name": "pad", "pin": "ESD.pad", "direction": "inout"}],
    })
    gds2 = _gds_of(design, tmp_path)
    spice = tmp_path / "esd_self.spice"
    spice.write_text(compile_netlist(design))
    lvs = run_lvs(layout_path=str(gds2), schematic_path=str(spice),
                  cell_name="esd_self", tech="sky130", engine="pya")
    assert lvs.status == "passed" and lvs.match


# --- design-level: verified design, reliability verdicts --------------------

def test_ota_variant_verify_and_esd_structural():
    design = _ota_with_clamps()
    result = verify_design(design)
    assert result["passed"], result
    assert result["drc"]["total_violations"] == 0
    assert result["lvs"]["match"] is True
    # ota MOS devices + 8 diode junctions. The netlist writer lifts the
    # vss-anode (global-net) pw2nd diodes to the top circuit — one card
    # per clamp — while the four identical pd2nw stay in the shared
    # subckt, so the written card count is +4+1, not +8.
    ext = extract_netlist(
        _gds_of(design, Path(tempfile.mkdtemp())), "sky130")
    d_cards = [ln for ln in ext.netlist_text.splitlines()
               if ln.startswith("D")]
    assert len([ln for ln in d_cards
                if "diode_pw2nd" in ln]) == 4
    assert [ln for ln in d_cards if "diode_pd2nw" in ln]
    assert len([ln for ln in ext.netlist_text.splitlines()
                if ln.startswith("X") and "esd_dual_diode" in ln]) == 4

    report = reliability_report(design, run_sims=False)
    for item_id in ("esd_hbm", "esd_cdm"):
        it = _item(report, item_id)
        assert it["status"] == "structural", it
        assert it["evidence_level"] == "structural"
        assert all(e["clamp_instance"] for e in it["details"]["per_port"])


def test_ota_clamp_left_of_core_verify():
    """Regression: clamp at x<0 grazed foreign met1/met3 channel metal.

    With ESD_INP left of the OTA core, its vdd pin lands next to the
    mirrored tail mirror's riser column; the riser-channel allocator
    jogged sideways on met1 at the pin row and the jog slid to 0.13 um
    under the clamp's own pad strap (met1.s = 0.14), while neighbouring
    met3 risers were booked at a 0.20 edge gap (met3.s = 0.30).  The
    router must keep foreign-metal spacing on jogs/trunks and the met3
    riser spacing; a left-of-core clamp must stay DRC-clean *and* LVS-
    matching.
    """
    design = _ota_with_clamps(at_x={"inp": -80.0})
    result = verify_design(design)
    assert result["passed"], result
    assert result["drc"]["total_violations"] == 0
    assert result["lvs"]["match"] is True


def test_ota_variant_one_unprotected_port_fails():
    design = _ota_with_clamps(drop_port="outn")
    report = reliability_report(design, run_sims=False)
    for item_id in ("esd_hbm", "esd_cdm"):
        it = _item(report, item_id)
        assert it["status"] == "fail", it
        assert "outn" in it["reason"]
        assert it["details"]["unprotected_ports"] == ["outn"]


def test_ota_without_clamps_unavailable_reason():
    report = reliability_report(_ota(), run_sims=False)
    for item_id in ("esd_hbm", "esd_cdm"):
        it = _item(report, item_id)
        assert it["status"] == "unavailable"
        assert "sky130.esd_dual_diode" in it["reason"]
        assert "unprotected" in it["reason"]


# --- simulation: reverse-biased clamps are electrically transparent ---------

@pytest.mark.skipif(
    not os.environ.get("LAYOUT_CANVAS_NGSPICE"),
    reason="LAYOUT_CANVAS_NGSPICE not set")
def test_clamped_ota_sim_equivalent():
    from layout_canvas.tools.testbench import run_testbench

    exe = os.environ["LAYOUT_CANVAS_NGSPICE"]
    base_run = run_testbench(_ota(), "tb_op_schematic", executable=exe)
    assert base_run["status"] == "passed", base_run
    var_run = run_testbench(_ota_with_clamps(), "tb_op_schematic",
                            executable=exe)
    assert var_run["status"] == "passed", var_run
    # same specs pass, values moved < 1 mV (reverse-biased junctions)
    for key in base_run["waves"]:
        vb, vv = base_run["waves"][key], var_run["waves"].get(key)
        if vb and vv and key in var_run["waves"]:
            delta = abs(vb[-1] - vv[-1])
            if key.startswith(("outp", "outn", "inp", "inn")):
                assert delta < 1e-3, (key, delta)
    assert {e["name"]: e["status"] for e in var_run["specs"]} == \
        {e["name"]: e["status"] for e in base_run["specs"]}
    # supply current change < 1 uA — clamp leakage sanity
    for sig in var_run["waves"]:
        if sig.lower().startswith("i(v_vdd"):
            delta = abs(var_run["waves"][sig][-1]
                        - base_run["waves"].get(sig, [0])[-1])
            assert delta < 1e-6, (sig, delta)
    # post-layout path: extracted D-cards (KLayout A/P params translated
    # to ngspice area/pj) must simulate, not just parse
    pex = run_testbench(_ota_with_clamps(), "tb_op_pex", executable=exe)
    assert pex["status"] == "passed", pex.get("errors")
    assert pex["spec_status"] == "pass"
