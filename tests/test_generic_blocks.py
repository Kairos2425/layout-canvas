"""Generic descriptor-driven ``gen_*`` blocks for external PDKs.

A ``*.pdk.json`` with an ``extract.roles`` contract gets up to three
parametric generators — ``<pdk>.gen_diff_pair``,
``<pdk>.gen_current_mirror``, ``<pdk>.gen_guard_ring`` — built purely from
descriptor data. These tests prove the generated cells actually work end
to end on the demo65 fixture: GDS -> extraction finds the descriptor leaf
devices -> LVS MATCH -> DRC clean. Fail-closed semantics hold throughout:
missing roles remove blocks or choices, params below the descriptor rules
are rejected, and cheating the bounds produces real DRC violations.
"""

import json

import pytest

import layout_canvas.blocks.generic  # noqa: F401  installs the PDK hook
import layout_canvas.blocks.sky130  # noqa: F401
from layout_canvas.blocks import base
from layout_canvas.ir.model import Design
from layout_canvas.pdk import all_pdks, external_pdk_errors, get_pdk
from layout_canvas.tools.drc import run_drc
from layout_canvas.tools.extract import extract_netlist
from layout_canvas.tools.lvs import run_lvs
from layout_canvas.tools.verify import verify_design

# Same layer contract as test_pdk_external's demo65 — sky130-style numbers
# so the generated geometry can be checked against known-good recipes.
_SKY130_LAYERS = {
    "diff": [65, 20], "tap": [65, 44], "nwell": [64, 20],
    "poly": [66, 20], "licon": [66, 44], "li1": [67, 20],
    "mcon": [67, 44], "met1": [68, 20], "via1": [68, 44],
    "met2": [69, 20], "via2": [69, 44], "met3": [70, 20],
    "via3": [70, 44], "met4": [71, 20], "via4": [71, 44],
    "met5": [72, 20], "nsdm": [93, 44], "psdm": [94, 20],
}


def _demo65(over: dict | None = None) -> dict:
    doc = {
        "name": "demo65",
        "pin_purpose": 16,
        "layers": dict(_SKY130_LAYERS),
        "rules": {"min_instance_margin_um": 0.5,
                  "default_routing_layer": "met2"},
        "extract": {
            "roles": {
                "well_n": [64, 20], "diff": [65, 20], "tap": [65, 44],
                "poly": [66, 20], "licon": [66, 44], "li1": [67, 20],
                "mcon": [67, 44], "met1": [68, 20], "via1": [68, 44],
                "met2": [69, 20], "via2": [69, 44], "met3": [70, 20],
                "via3": [70, 44], "met4": [71, 20], "via4": [71, 44],
                "met5": [72, 20], "nsdm": [93, 44], "psdm": [94, 20],
                "capm": [89, 44],
            },
            "text_datatypes": [16],
            "leaf_devices": {
                "demo65_nch": ["demo65_nch", "nmos"],
                "demo65_pch": ["demo65_pch", "pmos"],
            },
        },
        "drc": {
            "rules": {
                "well_n": [["width", 0.84], ["space", 1.27]],
                "diff": [["width", 0.15], ["space", 0.27]],
                "poly": [["width", 0.15], ["space", 0.21]],
                "li1": [["width", 0.17], ["space", 0.17]],
                "met1": [["width", 0.14], ["space", 0.14]],
                "met2": [["width", 0.14], ["space", 0.14]],
                "met3": [["width", 0.30], ["space", 0.30]],
                "met4": [["width", 0.30], ["space", 0.30]],
                "met5": [["width", 0.36], ["space", 0.34]],
                "nsdm": [["width", 0.38], ["space", 0.38]],
                "psdm": [["width", 0.38], ["space", 0.38]],
                "capm": [["width", 0.50]],
            },
            "enclosure": [
                {"label": "licon.li", "cut": "licon",
                 "enclosed_by": ["li1"], "enc": 0.06},
                {"label": "mcon.li", "cut": "mcon",
                 "enclosed_by": ["li1"], "enc": 0.03},
                {"label": "mcon.m1", "cut": "mcon",
                 "enclosed_by": ["met1"], "enc": 0.03},
                {"label": "via.m1", "cut": "via1",
                 "enclosed_by": ["met1"], "enc": 0.055},
                {"label": "via.m2", "cut": "via1",
                 "enclosed_by": ["met2"], "enc": 0.055},
                {"label": "via2.m2", "cut": "via2",
                 "enclosed_by": ["met2"], "enc": 0.065},
                {"label": "via2.m3", "cut": "via2",
                 "enclosed_by": ["met3"], "enc": 0.065},
            ],
        },
    }
    if over:
        doc.update(over)
    return doc


def _write_pdk(tmp_path, monkeypatch, doc: dict):
    pdk_dir = tmp_path / "pdks"
    pdk_dir.mkdir(exist_ok=True)
    (pdk_dir / f"{doc['name']}.pdk.json").write_text(json.dumps(doc))
    monkeypatch.setenv("LAYOUT_CANVAS_PDK_DIR", str(pdk_dir))
    return doc["name"]


@pytest.fixture
def demo65(tmp_path, monkeypatch):
    """A sky130-flavoured descriptor — every role present."""
    return _write_pdk(tmp_path, monkeypatch, _demo65())


def _component_and_verify(block_name: str, tech: str, tmp_path, **params):
    """Build a gen_* cell and run extract -> LVS -> DRC on it."""
    blk = base.get(block_name)
    comp = blk.component(**params)
    gds = tmp_path / f"{comp.name}.gds"
    comp.write_gds(str(gds))
    ext = extract_netlist(str(gds), tech=tech)
    ref = tmp_path / f"{comp.name}.cir"
    ref.write_text(blk.spice(**params))
    lvs = run_lvs(layout_path=gds, schematic_path=ref,
                  cell_name=comp.name, tech=tech, engine="pya")
    drc = run_drc(str(gds), tech=tech, engine="pya")
    return ext, lvs, drc


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def test_generic_blocks_registered(demo65):
    get_pdk("demo65")
    blocks = base.all_blocks()
    for name in ("demo65.gen_diff_pair", "demo65.gen_current_mirror",
                 "demo65.gen_guard_ring"):
        assert name in blocks, f"{name} missing from palette"
        spec = blocks[name].spec
        assert spec.pdk == "demo65"
        assert "generic generator (descriptor-driven)" in spec.summary

    dp = blocks["demo65.gen_diff_pair"].spec
    assert dp.level == "L1"
    assert [p.name for p in dp.ports] == [
        "inp", "inn", "outp", "outn", "tail", "vss"]
    assert {p.name for p in dp.params} >= {
        "fingers", "width", "length", "type"}

    cm = blocks["demo65.gen_current_mirror"].spec
    assert [p.name for p in cm.ports] == ["in", "out", "gate", "vss"]

    gr = blocks["demo65.gen_guard_ring"].spec
    assert [p.name for p in gr.ports] == [
        "tap", "tap_n", "tap_s", "tap_w", "tap_e"]
    ptype = next(p for p in gr.params if p.name == "ptype")
    assert ptype.choices == ["ptap", "ntap"]


def test_builtin_pdks_never_get_gen_blocks(demo65):
    base.all_blocks()  # trigger the external scan
    for pdk in ("sky130", "ihp_sg13g2"):
        for gen in ("gen_diff_pair", "gen_current_mirror", "gen_guard_ring"):
            assert f"{pdk}.{gen}" not in base.all_blocks()


def test_all_blocks_triggers_external_scan(tmp_path, monkeypatch):
    """Lazy loading: all_blocks() alone must surface the gen_* blocks —
    no prior get_pdk() call needed (load-order independence)."""
    _write_pdk(tmp_path, monkeypatch, _demo65())
    assert "demo65.gen_diff_pair" in base.all_blocks()


def test_env_rescan_removes_generated_blocks(tmp_path, monkeypatch):
    _write_pdk(tmp_path, monkeypatch, _demo65())
    assert "demo65.gen_diff_pair" in base.all_blocks()
    monkeypatch.delenv("LAYOUT_CANVAS_PDK_DIR")
    monkeypatch.delenv("LAYOUT_CANVAS_PDKS", raising=False)
    assert "demo65.gen_diff_pair" not in base.all_blocks()


def test_pdk_without_extract_registers_nothing(tmp_path, monkeypatch):
    doc = _demo65()
    doc["name"] = "bare99"
    del doc["extract"], doc["drc"]
    _write_pdk(tmp_path, monkeypatch, doc)
    get_pdk("bare99")
    names = [n for n in base.all_blocks() if n.startswith("bare99.")]
    assert names == []
    # the block-gap diagnostic still describes the descriptor-only PDK
    gap = base.describe_pdk_block_gap("bare99")
    assert gap is not None and "import_gds" in gap


def test_missing_roles_drop_blocks(tmp_path, monkeypatch):
    """Fail-closed registration: a role contract without the contact stack
    or any tap path must not produce blocks that cannot build."""
    doc = _demo65()
    doc["name"] = "weak65"
    roles = doc["extract"]["roles"]
    # well_n/diff/poly are mandatory for any extract section; cutting the
    # contact stack and every tap path must still leave a loadable PDK —
    # just no generatable blocks.
    del roles["mcon"], roles["tap"], roles["nsdm"], roles["psdm"]
    _write_pdk(tmp_path, monkeypatch, doc)
    get_pdk("weak65")
    names = {n for n in base.all_blocks() if n.startswith("weak65.")}
    assert names == set()  # no mcon -> no MOS stack; no tap -> no ring


def test_sub_capability_drops_choices(tmp_path, monkeypatch):
    """No tap AND no psdm: nmos diff_pair still registers (ring degraded)
    but the mirror needs the substrate ring and the pmos choice needs a
    well-tie path — both disappear."""
    doc = _demo65()
    doc["name"] = "notap65"
    roles = doc["extract"]["roles"]
    del roles["tap"], roles["psdm"]
    _write_pdk(tmp_path, monkeypatch, doc)
    get_pdk("notap65")
    names = {n for n in base.all_blocks() if n.startswith("notap65.")}
    assert "notap65.gen_diff_pair" in names
    assert "notap65.gen_current_mirror" not in names
    assert "notap65.gen_guard_ring" not in names
    dp = base.get("notap65.gen_diff_pair").spec
    t = next(p for p in dp.params if p.name == "type")
    assert t.choices == ["nmos"]
    assert any("bulk path" in c for c in dp.constraints)


def test_no_drc_rules_marks_constraints(tmp_path, monkeypatch):
    doc = _demo65()
    doc["name"] = "norules65"
    del doc["drc"]
    _write_pdk(tmp_path, monkeypatch, doc)
    get_pdk("norules65")
    spec = base.get("norules65.gen_diff_pair").spec
    assert "rules unavailable — verify with DRC" in spec.constraints
    # conservative bounds: the width min defaults to the 0.5 rule value
    w = next(p for p in spec.params if p.name == "width")
    assert w.min >= 0.5


# ---------------------------------------------------------------------------
# Generated cells: extract -> LVS -> DRC
# ---------------------------------------------------------------------------

def test_gen_diff_pair_nmos_extract_lvs_drc(demo65, tmp_path):
    ext, lvs, drc = _component_and_verify(
        "demo65.gen_diff_pair", "demo65", tmp_path,
        fingers=4, width=2.0, length=0.5)
    assert ext.status == "ok" and ext.devices == 8
    assert "demo65_nch" in ext.netlist_text
    assert lvs.status == "passed" and lvs.match, lvs.errors
    assert drc.status == "passed" and drc.total_violations == 0


def test_gen_diff_pair_pmos_extract_lvs_drc(demo65, tmp_path):
    ext, lvs, drc = _component_and_verify(
        "demo65.gen_diff_pair", "demo65", tmp_path,
        fingers=2, type="pmos")
    assert ext.status == "ok" and ext.devices == 4
    assert "demo65_pch" in ext.netlist_text
    assert lvs.status == "passed" and lvs.match, lvs.errors
    assert drc.status == "passed" and drc.total_violations == 0


def test_gen_current_mirror_extract_lvs_drc(demo65, tmp_path):
    for ptype, leaf, devs in (("nmos", "demo65_nch", 4),
                              ("pmos", "demo65_pch", 4)):
        ext, lvs, drc = _component_and_verify(
            "demo65.gen_current_mirror", "demo65", tmp_path,
            fingers=2, type=ptype)
        assert ext.status == "ok" and ext.devices == devs
        assert leaf in ext.netlist_text
        assert lvs.status == "passed" and lvs.match, (ptype, lvs.errors)
        assert drc.status == "passed" and drc.total_violations == 0


def test_gen_guard_ring_extract_lvs_drc(demo65, tmp_path):
    for ptype in ("ptap", "ntap"):
        ext, lvs, drc = _component_and_verify(
            "demo65.gen_guard_ring", "demo65", tmp_path,
            width=8.0, height=6.0, ptype=ptype)
        assert ext.status == "ok"
        assert lvs.status == "passed" and lvs.match, (ptype, lvs.errors)
        assert drc.status == "passed" and drc.total_violations == 0


# ---------------------------------------------------------------------------
# Fail-closed parameters
# ---------------------------------------------------------------------------

def test_params_below_descriptor_rules_rejected(demo65):
    get_pdk("demo65")
    blk = base.get("demo65.gen_diff_pair")
    # demo65 poly min-width rule is 0.15 -> length below it must be refused
    with pytest.raises(ValueError, match="below min"):
        blk.resolve_params({"length": 0.05})
    with pytest.raises(ValueError, match="below min"):
        blk.resolve_params({"width": 0.1})
    # and the bound is the rule-derived value, not a hardcoded one
    lp = next(p for p in blk.spec.params if p.name == "length")
    wp = next(p for p in blk.spec.params if p.name == "width")
    assert lp.min == pytest.approx(0.15)
    assert wp.min == pytest.approx(0.42, abs=0.01)


def test_below_rule_geometry_reports_real_drc_violation(demo65, tmp_path):
    """Cheating the param bounds via the raw build must surface as a real
    DRC failure — the check is the backstop, not a courtesy."""
    get_pdk("demo65")
    blk = base.get("demo65.gen_diff_pair")
    comp = blk.build(fingers=2, width=0.10, length=0.15,
                     tail_width=4.0, type="nmos")
    gds = tmp_path / "bad.gds"
    comp.write_gds(str(gds))
    drc = run_drc(str(gds), tech="demo65", engine="pya")
    assert drc.total_violations > 0
    # demo65 diff = layer 65/20 — its min-width rule must be the one hit
    assert any(v["rule"].startswith("65/20") for v in drc.violations)


# ---------------------------------------------------------------------------
# Real design smoke
# ---------------------------------------------------------------------------

def test_verify_design_demo65_smoke(demo65):
    """A real design on the descriptor-only PDK — gen_diff_pair plus a
    gen_guard_ring tied to the same substrate net — must verify."""
    design = Design.model_validate({
        "name": "demo65_smoke",
        "pdk": "demo65",
        "instances": [
            {"id": "dp", "block": "demo65.gen_diff_pair",
             "params": {"fingers": 4, "width": 2.0}},
            {"id": "gr", "block": "demo65.gen_guard_ring",
             "params": {"width": 12.0, "height": 8.0},
             "placement": {"relative_to": "dp", "relation": "below",
                           "margin": 4.0}},
        ],
        "nets": [{"name": "vss", "pins": ["dp.vss", "gr.tap"]}],
        "ports": [
            {"name": "INP", "pin": "dp.inp"},
            {"name": "INN", "pin": "dp.inn"},
            {"name": "OUTP", "pin": "dp.outp"},
            {"name": "OUTN", "pin": "dp.outn"},
            {"name": "TAIL", "pin": "dp.tail"},
        ],
    })
    res = verify_design(design)
    assert res["passed"], res
    assert res["extract"]["devices"] == 8
    assert res["drc"]["total_violations"] == 0
    assert res["lvs"]["match"]


def test_external_pdk_errors_unchanged(demo65):
    get_pdk("demo65")
    assert external_pdk_errors() == {}
    assert "demo65" in all_pdks()


# ---------------------------------------------------------------------------
# Web surface: /api/pdks + /api/blocks see the external PDK and its blocks
# ---------------------------------------------------------------------------

def test_api_blocks_and_pdks_show_external_pdk(demo65):
    from http.server import ThreadingHTTPServer
    from threading import Thread
    from urllib.request import urlopen

    from layout_canvas.web.app import _Handler

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    Thread(target=server.serve_forever, daemon=True).start()
    try:
        port = server.server_address[1]
        with urlopen(f"http://127.0.0.1:{port}/api/pdks") as r:
            pdks = json.loads(r.read())
        demo = next(p for p in pdks if p["name"] == "demo65")
        assert demo["extract"] is True and demo["drc"] is True
        assert demo["gen_blocks"] == 3
        with urlopen(f"http://127.0.0.1:{port}/api/blocks") as r:
            blocks = json.loads(r.read())
        names = {b["name"] for b in blocks}
        assert "demo65.gen_diff_pair" in names
        assert "demo65.gen_current_mirror" in names
        assert "demo65.gen_guard_ring" in names
    finally:
        server.shutdown()
        server.server_close()
