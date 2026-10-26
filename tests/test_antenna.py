"""Antenna check — per-net EGAR on synthetic + real layouts.

The synthetic fixture is one MOS gate (poly∩diff 0.15×0.42) whose gate
runs over licon→li1→mcon→met1 into a 0.14×200 µm strap:

    A_gate = 0.063 µm²,  EA_met1 ≈ P·FLT ≈ 140 µm²,  raw ratio ≈ 2230

It also empirically pins down the reason this tool computes EGAR itself:
KLayout's ``antenna_check`` diode factor scales the gate-area
*denominator* (``A_gate + K·A_diode``), which under-reports violations
versus the PDK's subtractive ``EA/A_gate − K·A_diode`` form.
"""

import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("klayout.db")

import layout_canvas.blocks.generic  # noqa: F401  populate block registry
import layout_canvas.blocks.sky130  # noqa: F401
from layout_canvas.compiler.compile import compile_design
from layout_canvas.ir.model import Design
from layout_canvas.tools.antenna import antenna_check
from layout_canvas.tools.extract import _recipe_for

REPO = Path(__file__).resolve().parent.parent
OTA_LAB = REPO / "examples" / "ota_lab.json"


def _synth_gds(tmp_path: Path, diode: float = 0.0, ptap: bool = False
               ) -> Path:
    """Gate + contact chain + met1 strap; optional contacted diff diode
    and a substrate tap (pAntennaShort → exempt net)."""
    import klayout.db as db

    r = _recipe_for("sky130")
    ly = db.Layout()
    ly.dbu = 0.001
    cell = ly.create_cell("T")
    L = {k: ly.layer(*v) for k, v in r.items()
         if isinstance(v, tuple) and len(v) == 2}

    def rect(role, x0, y0, x1, y1):
        cell.shapes(L[role]).insert(db.Box(
            int(x0 * 1000), int(y0 * 1000), int(x1 * 1000), int(y1 * 1000)))

    rect("diff", 0, 0, 2, 0.42)
    rect("poly", 0.5, -0.3, 0.65, 1.0)        # gate finger
    rect("poly", 0.5, 0.5, 2.2, 0.7)          # run stays clear of diff
    rect("licon", 2.0, 0.52, 2.17, 0.68)
    rect("li1", 1.9, 0.4, 2.3, 0.8)
    rect("mcon", 2.02, 0.54, 2.15, 0.66)
    rect("met1", 1.9, 0.4, 2.3, 0.8)
    rect("met1", 2.23, 0.53, 202.3, 0.67)     # 0.14 x 200 antenna strap
    if diode:
        rect("diff", 4.0, 1.0, 4.0 + diode, 1.0 + diode)
        rect("licon", 4.5, 1.5, 4.67, 1.66)
        rect("li1", 4.4, 1.4, 4.8, 1.8)
        rect("mcon", 4.52, 1.52, 4.65, 1.64)
        rect("met1", 4.3, 0.4, 4.9, 1.6)
    if ptap:
        rect("tap", 8.0, 1.0, 9.0, 2.0)       # p-tap, no nwell -> short
        rect("licon", 8.4, 1.4, 8.57, 1.56)
        rect("li1", 8.3, 1.3, 8.7, 1.7)
        rect("mcon", 8.42, 1.42, 8.55, 1.54)
        rect("met1", 8.2, 0.4, 8.8, 1.6)
    gds = tmp_path / "synth.gds"
    ly.write(str(gds))
    return gds


def _by_layer(result: dict) -> dict:
    return {e["layer"]: e for e in result["layers"]}


def test_ota_lab_passes(tmp_path):
    design = Design.model_validate(
        json.loads(OTA_LAB.read_text(encoding="utf-8")))
    gds = tmp_path / "ota_lab.gds"
    compile_design(design).write_gds(str(gds))
    r = antenna_check(str(gds), "sky130")
    assert r["status"] == "pass", r
    assert r["violations_total"] == 0
    layers = _by_layer(r)
    for name in ("poly", "li1", "met1"):
        assert layers[name]["checked"], name
        assert layers[name]["worst_ratio"] > 0, name


def test_bare_strap_violates_met1_only(tmp_path):
    r = antenna_check(str(_synth_gds(tmp_path)), "sky130")
    assert r["status"] == "fail"
    layers = _by_layer(r)
    assert layers["met1"]["violations"] == 1
    assert layers["met1"]["worst_ratio"] > 2000  # ~2231
    for name, entry in layers.items():
        if name != "met1":
            assert entry["violations"] == 0, name


def test_diode_relief_pdk_subtractive_form(tmp_path):
    """4x4 diode (A=16, K=400 → relief 6400) saves the net; a 0.5x0.5
    diode (relief 100 → EGAR ≈ 2130 > 400) still fails. Under KLayout's
    denominator-scaling the small diode would pass — these assertions
    prove the implemented formula is the PDK's."""
    big = antenna_check(str(_synth_gds(tmp_path, diode=4.0)), "sky130")
    assert big["status"] == "pass", big["reason"]
    small = antenna_check(str(_synth_gds(tmp_path, diode=0.5)),
                          "sky130")
    assert small["status"] == "fail"
    assert _by_layer(small)["met1"]["violations"] == 1


def test_antenna_short_exempts_net(tmp_path):
    r = antenna_check(str(_synth_gds(tmp_path, ptap=True)), "sky130")
    assert r["status"] == "pass", r["reason"]
    assert r["exempt_nets"] == 1
    assert _by_layer(r)["met1"]["violations"] == 0


def test_ihp_unavailable_no_rule_table(tmp_path):
    r = antenna_check(str(_synth_gds(tmp_path)), "ihp_sg13g2")
    assert r["status"] == "unavailable"
    assert "no antenna rule table" in r["reason"]


def test_missing_klayout_unavailable(monkeypatch):
    monkeypatch.setitem(sys.modules, "klayout.db", None)
    r = antenna_check("does-not-matter.gds", "sky130")
    assert r["status"] == "unavailable"
    assert "klayout" in r["reason"]


def test_missing_gds_unavailable():
    r = antenna_check("does-not-exist-xyz.gds", "sky130")
    assert r["status"] == "unavailable"
    assert "GDS not found" in r["reason"]


def test_reliability_item_deterministic():
    design = Design.model_validate(
        json.loads(OTA_LAB.read_text(encoding="utf-8")))
    from layout_canvas.tools.reliability import reliability_report

    rep = reliability_report(design, run_sims=False)
    ant = next(i for i in rep["items"] if i["id"] == "antenna")
    assert ant["evidence_level"] == "deterministic"
    assert ant["status"] == "pass", ant["reason"]
    assert ant["details"]["violations_total"] == 0
    assert any(e["checked"] for e in ant["details"]["layers"])
