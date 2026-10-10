"""External PDK descriptors — ``*.pdk.json`` loading + tool-chain coverage.

Covers the contract documented in ``docs/PDK_DESCRIPTORS.md``: a commercial
process arrives as a JSON descriptor under ``LAYOUT_CANVAS_PDK_DIR`` (or as
a file listed in ``LAYOUT_CANVAS_PDKS``) and immediately serves the
import_gds → DRC-subset → LVS-extraction → model-prelude chain — no code
needed. Malformed descriptors are diagnostics, never fatal; built-ins can
never be shadowed; missing sections report ``unavailable``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

import layout_canvas.blocks.sky130  # noqa: F401 - registers sky130 generators
from layout_canvas.ir.model import Design
from layout_canvas.pdk import (
    all_pdks,
    external_pdk_errors,
    external_pdks,
    get_pdk,
)

try:
    import klayout.db  # noqa: F401
    HAS_KLAYOUT = True
except ImportError:
    HAS_KLAYOUT = False

needs_klayout = pytest.mark.skipif(
    not HAS_KLAYOUT, reason="klayout python module required")

# A "demo65" commercial-ish PDK: sky130 layer numbers/rules so a compiled
# sky130 GDS verifies under it, with leaf devices renamed to prove the
# descriptor (not the built-in tables) drove extraction.
_SKY130_LAYERS = {
    "diff": [65, 20], "tap": [65, 44], "nwell": [64, 20],
    "poly": [66, 20], "licon": [66, 44], "li1": [67, 20],
    "mcon": [67, 44], "met1": [68, 20], "via1": [68, 44],
    "met2": [69, 20], "via2": [69, 44], "met3": [70, 20],
    "via3": [70, 44], "met4": [71, 20], "via4": [71, 44],
    "met5": [72, 20], "nsdm": [93, 44], "psdm": [94, 20],
}

_DEMO65 = {
    "name": "demo65",
    "pin_purpose": 16,
    "layers": _SKY130_LAYERS,
    "rules": {
        "min_instance_margin_um": 0.5,
        "default_routing_layer": "met2",
    },
    "model_libs": {"spice_prelude_file": "models/demo65_tt.lib"},
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
            # well_n exercises role-name resolution (the drawing is "nwell"),
            # capm exercises a role with no entry in "layers" at all.
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

# Same layers, absurd met1 width: any real layout violates it — proves the
# descriptor's drc.rules ran instead of the built-in table.
_TIGHT99 = {
    "name": "tight99",
    "layers": _SKY130_LAYERS,
    "drc": {"rules": {"met1": [["width", 100.0]]}},
}


def _write(directory: Path, name: str, payload) -> Path:
    path = directory / name
    path.write_text(
        payload if isinstance(payload, str) else json.dumps(payload),
        encoding="utf-8")
    return path


@pytest.fixture
def pdk_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A private PDK directory wired through LAYOUT_CANVAS_PDK_DIR."""
    directory = tmp_path / "pdks"
    (directory / "models").mkdir(parents=True)
    (directory / "models" / "demo65_tt.lib").write_text(
        "* demo65 TT corner stub\n.param demo65_vth=0.5\n", encoding="utf-8")
    monkeypatch.setenv("LAYOUT_CANVAS_PDK_DIR", str(directory))
    monkeypatch.delenv("LAYOUT_CANVAS_PDKS", raising=False)
    return directory


def _mirror_design() -> Design:
    return Design.model_validate({
        "name": "ext_mirror",
        "pdk": "sky130",
        "instances": [{"id": "cm", "block": "sky130.current_mirror",
                       "params": {"fingers": 2}}],
        "ports": [{"name": "IN", "pin": "cm.in", "direction": "input"}],
    })


def _mirror_gds(design: Design, path: Path) -> Path:
    from layout_canvas.compiler.compile import compile_design

    out = path / f"{design.name}.gds"
    compile_design(design).write_gds(str(out))
    return out


# --- Descriptor loading -------------------------------------------------

def test_descriptor_registers_and_roundtrips(pdk_dir: Path):
    _write(pdk_dir, "demo65.pdk.json", _DEMO65)
    pdk = get_pdk("demo65")
    assert pdk.source.endswith("demo65.pdk.json")
    assert pdk.base_dir == str(pdk_dir)
    assert pdk.layer("met1") == (68, 20)
    assert pdk.pin_label_layer((69, 20)) == (69, 16)
    assert "demo65" in all_pdks()
    assert external_pdks()["demo65"] is pdk
    assert pdk.extract["roles"]["capm"] == (89, 44)
    # drc resolved names -> (layer, datatype) at load time
    assert pdk.drc["rules"][(64, 20)] == [("width", 0.84), ("space", 1.27)]
    assert pdk.drc["rules"][(89, 44)] == [("width", 0.5)]
    assert pdk.drc["enclosure"][0] == ("licon.li", (66, 44), [(67, 20)], 0.06)

    # to_dict is a faithful inverse: re-parse the dump and compare sections
    from layout_canvas.pdk.descriptor import PDK

    clone = PDK.from_dict(pdk.to_dict())
    assert clone.extract == pdk.extract
    assert clone.drc == pdk.drc
    assert clone.layers == pdk.layers


def test_pdks_list_env_loads_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    target = _write(tmp_path, "demo65.pdk.json", _DEMO65)
    monkeypatch.delenv("LAYOUT_CANVAS_PDK_DIR", raising=False)
    monkeypatch.setenv("LAYOUT_CANVAS_PDKS", str(target))
    assert get_pdk("demo65").source == str(target)


def test_bad_descriptor_is_diagnostic_not_fatal(pdk_dir: Path):
    bad = _write(pdk_dir, "broken.pdk.json", "{ this is not json")
    _write(pdk_dir, "demo65.pdk.json", _DEMO65)
    errors = external_pdk_errors()
    assert str(bad) in errors
    # the bad file did not stop the good one, nor the built-ins
    assert "demo65" in external_pdks()
    assert get_pdk("sky130").layer("met1") == (68, 20)


def test_builtin_name_shadowing_refused(pdk_dir: Path):
    evil = _write(pdk_dir, "fake.pdk.json",
                  {"name": "sky130", "layers": {"met1": [1, 0]}})
    assert str(evil) in external_pdk_errors()
    pdk = get_pdk("sky130")
    assert pdk.layer("met1") == (68, 20)  # built-in map intact
    assert pdk.source is None


def test_unknown_extract_role_and_polarity_rejected(pdk_dir: Path):
    payload = json.loads(json.dumps(_DEMO65))
    payload["extract"]["roles"] = dict(payload["extract"]["roles"],
                                       bogus_layer=[1, 2])
    bad = _write(pdk_dir, "demo65.pdk.json", payload)
    errors = external_pdk_errors()
    assert "bogus_layer" in errors[str(bad)]
    assert "demo65" not in external_pdks()

    payload2 = json.loads(json.dumps(_DEMO65))
    payload2["name"] = "demo66"
    payload2["extract"]["leaf_devices"] = {"demo66_x": ["demo66_x", "bipolar"]}
    bad2 = _write(pdk_dir, "demo66.pdk.json", payload2)
    # a new file dropped into an already-scanned dir needs an explicit
    # rescan — load_external_pdks() is that hook (env change rescans too)
    from layout_canvas.pdk import load_external_pdks

    errors = load_external_pdks()
    assert "bipolar" in errors[str(bad2)]


def test_unknown_drc_name_lists_choices(pdk_dir: Path):
    payload = {"name": "demo66", "layers": _SKY130_LAYERS,
               "drc": {"rules": {"metalll": [["width", 0.2]]}}}
    bad = _write(pdk_dir, "demo66.pdk.json", payload)
    err = external_pdk_errors()[str(bad)]
    assert "metalll" in err and "met1" in err  # names the fix


@needs_klayout
def test_missing_extract_section_is_unavailable(pdk_dir: Path, tmp_path: Path):
    _write(pdk_dir, "bare99.pdk.json",
           {"name": "bare99", "layers": _SKY130_LAYERS})
    pdk = get_pdk("bare99")
    assert pdk.extract is None and pdk.drc is None
    gds = _mirror_gds(_mirror_design(), tmp_path)

    from layout_canvas.tools.drc import run_drc
    from layout_canvas.tools.extract import extract_netlist

    assert extract_netlist(gds, tech="bare99").status == "unavailable"
    assert run_drc(str(gds), tech="bare99", engine="pya").status == "unavailable"


# --- Tool chain on the descriptor --------------------------------------

@needs_klayout
def test_extraction_uses_descriptor_leaf_names(pdk_dir: Path, tmp_path: Path):
    _write(pdk_dir, "demo65.pdk.json", _DEMO65)
    gds = _mirror_gds(_mirror_design(), tmp_path)

    from layout_canvas.tools.extract import extract_netlist

    ext = extract_netlist(gds, tech="demo65")
    assert ext.status == "ok", ext.errors
    assert ext.devices == 4  # f2 mirror -> ABBA -> 4 fingers
    # the descriptor's class names — not the built-in nfet_01v8 — prove the
    # extract.roles + leaf_devices came from the *.pdk.json
    assert "demo65_nch" in ext.netlist_text
    assert "nfet_01v8" not in ext.netlist_text


@needs_klayout
def test_lvs_under_descriptor_pdk(pdk_dir: Path, tmp_path: Path):
    _write(pdk_dir, "demo65.pdk.json", _DEMO65)
    design = _mirror_design()
    gds = _mirror_gds(design, tmp_path)

    from layout_canvas.compiler.netlist import compile_netlist
    from layout_canvas.tools.lvs import run_lvs

    # The golden netlist speaks the PDK's own leaf names (as a foundry
    # LVS netlist would): swap the sky130 wrapper names the emitters use.
    ref = tmp_path / "ref.spice"
    ref.write_text(
        compile_netlist(design)
        .replace("sky130_fd_pr__nfet_01v8", "demo65_nch")
        .replace("sky130_fd_pr__pfet_01v8", "demo65_pch"),
        encoding="utf-8")
    result = run_lvs(layout_path=gds, schematic_path=ref,
                     cell_name=design.name, tech="demo65", engine="pya")
    assert result.status == "passed"
    assert result.match is True
    assert "MATCH" in result.report_path.read_text()


@needs_klayout
def test_drc_uses_descriptor_rules(pdk_dir: Path, tmp_path: Path):
    _write(pdk_dir, "demo65.pdk.json", _DEMO65)
    _write(pdk_dir, "tight99.pdk.json", _TIGHT99)
    gds = _mirror_gds(_mirror_design(), tmp_path)

    from layout_canvas.tools.drc import run_drc

    assert run_drc(str(gds), tech="demo65", engine="pya").status == "passed"
    # same GDS, same engine — only the descriptor's rules changed
    tight = run_drc(str(gds), tech="tight99", engine="pya")
    assert tight.status == "failed"
    assert any(v["rule"] == "68/20.w" for v in tight.violations)
    assert run_drc(str(gds), tech="sky130", engine="pya").status == "passed"


@needs_klayout
def test_import_gds_on_descriptor_pdk(pdk_dir: Path, tmp_path: Path):
    _write(pdk_dir, "demo65.pdk.json", _DEMO65)
    gds = _mirror_gds(_mirror_design(), tmp_path)

    from layout_canvas.blocks.gds_cell import register_gds_cell

    block = register_gds_cell("demo65.mirror_cell", str(gds), pdk="demo65")
    assert block.spec.pdk == "demo65"
    assert "IN" in [p.name for p in block.spec.ports]


def test_model_prelude_resolves_descriptor_dir(pdk_dir: Path):
    _write(pdk_dir, "demo65.pdk.json", _DEMO65)

    from layout_canvas.tools.sim import default_model_prelude

    prelude = default_model_prelude("demo65")
    assert "demo65_vth" in prelude


# --- Boundaries and introspection ---------------------------------------

def test_no_blocks_error_is_named(pdk_dir: Path):
    # a descriptor-only PDK nothing ever registers blocks for
    _write(pdk_dir, "gap77.pdk.json",
           {"name": "gap77", "layers": _SKY130_LAYERS})

    from layout_canvas.compiler.compile import compile_design
    from layout_canvas.mcp.server import LayoutCanvasMCPServer

    design = Design.model_validate({
        "name": "needs_block", "pdk": "gap77",
        "instances": [{"id": "u1", "block": "gap77.diff_pair"}]})
    with pytest.raises(ValueError, match="no blocks registered for pdk 'gap77'"):
        compile_design(design)

    server = LayoutCanvasMCPServer()
    with pytest.raises(ValueError, match="no blocks registered for pdk 'gap77'"):
        server.execute_tool("generate_block", {"name": "gap77.diff_pair"})
    # unknown PDKs keep the plain unknown-block error
    with pytest.raises(KeyError, match="unknown block"):
        server.execute_tool("generate_block", {"name": "tsmc5.ota"})


def test_probe_environment_reports_external_pdks(pdk_dir: Path):
    _write(pdk_dir, "demo65.pdk.json", _DEMO65)
    _write(pdk_dir, "broken.pdk.json", "{ nope")

    from layout_canvas.tools.backends import probe_environment

    report = probe_environment()
    ext = report["external_pdks"]["demo65"]
    assert ext["extract"] is True and ext["drc"] is True
    assert ext["source"].endswith("demo65.pdk.json")
    assert ext["leaf_devices"] == 2
    assert any("broken.pdk.json" in k for k in report["external_pdk_errors"])
    # generated-block surface is visible too: names and failures
    assert set(ext["gen_blocks"]) >= {"demo65.gen_diff_pair"}
    assert "block_generation_errors" in report


def test_cli_pdk_list_and_dump(pdk_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys):
    _write(pdk_dir, "demo65.pdk.json", _DEMO65)

    from layout_canvas.cli import main

    monkeypatch.setattr(sys, "argv", ["layout-canvas", "pdk", "list"])
    assert main() == 0
    listed = json.loads(capsys.readouterr().out)
    names = {p["name"] for p in listed["pdks"]}
    assert {"sky130", "ihp_sg13g2", "demo65"} <= names
    demo = next(p for p in listed["pdks"] if p["name"] == "demo65")
    assert set(demo["gen_blocks"]) == {
        "demo65.gen_diff_pair", "demo65.gen_current_mirror",
        "demo65.gen_guard_ring"}
    assert listed["generation_errors"] == {}
    entry = next(p for p in listed["pdks"] if p["name"] == "demo65")
    assert entry["extract"] is True and entry["drc"] is True
    assert entry["source"].endswith("demo65.pdk.json")

    monkeypatch.setattr(sys, "argv", ["layout-canvas", "pdk", "dump", "demo65"])
    assert main() == 0
    dumped = json.loads(capsys.readouterr().out)
    assert dumped["extract"]["roles"]["met1"] == [68, 20]
    assert dumped["extract"]["leaf_devices"]["demo65_nch"] == ["demo65_nch", "nmos"]
    assert dumped["drc"]["rules"]["met1"] == [["width", 0.14], ["space", 0.14]]
    assert dumped["drc"]["enclosure"][0]["cut"] == "licon"

    # built-ins dump a complete template — the commercial-PDK starting point
    monkeypatch.setattr(sys, "argv", ["layout-canvas", "pdk", "dump", "sky130"])
    assert main() == 0
    tpl = json.loads(capsys.readouterr().out)
    assert tpl["extract"]["roles"]["capm"] == [89, 44]  # beyond the layers map
    assert tpl["extract"]["leaf_devices"]["sky130_fd_pr__nfet_01v8"] == [
        "nfet_01v8", "nmos"]
    assert tpl["drc"]["rules"]["met5"] == [["width", 0.36], ["space", 0.34]]

    # a dumped template is itself a valid descriptor
    from layout_canvas.pdk.descriptor import PDK

    reparsed = PDK.from_dict(tpl)
    assert reparsed.name == "sky130"
    assert reparsed.drc["rules"][(72, 20)] == [("width", 0.36), ("space", 0.34)]

def test_cli_pdk_check_validates_and_reports(tmp_path: Path,
                                             monkeypatch: pytest.MonkeyPatch,
                                             capsys):
    """`pdk check FILE` is the commercial-PDK onramp: offline validation
    plus a capability report (roles/gen blocks/warnings) — no env needed."""
    from layout_canvas.cli import main

    target = _write(tmp_path, "demo65.pdk.json", _DEMO65)
    monkeypatch.setattr(
        sys, "argv", ["layout-canvas", "pdk", "check", str(target)])
    assert main() == 0
    rep = json.loads(capsys.readouterr().out)
    assert rep["status"] == "ok"
    assert rep["pdk"] == "demo65"
    assert rep["drc_rules"] == 12
    assert rep["drc_enclosures"] == 7
    assert set(rep["gen_blocks"]) == {
        "demo65.gen_diff_pair",
        "demo65.gen_current_mirror",
        "demo65.gen_guard_ring",
    }
    assert "capm" in rep["extract_roles"]["covered"]

    # a descriptor missing the MOS derivation contract is rejected
    gutted = dict(_DEMO65)
    gutted["name"] = "gut65"
    gutted["extract"] = {"roles": {"diff": [65, 20]}}
    bad = _write(tmp_path, "gut65.pdk.json", gutted)
    monkeypatch.setattr(
        sys, "argv", ["layout-canvas", "pdk", "check", str(bad)])
    assert main() == 2
    assert "rejected" in capsys.readouterr().err

    # and invalid JSON is a diagnostic, not a traceback
    broken = _write(tmp_path, "broken.pdk.json", "{ not json")
    monkeypatch.setattr(
        sys, "argv", ["layout-canvas", "pdk", "check", str(broken)])
    assert main() == 2
    assert "invalid JSON" in capsys.readouterr().err


class TestExtendsInheritance:
    def test_extends_inherits_and_overrides(self):
        # {extends: base} deep-merges: omitted sections inherit, given
        # sections override entry-wise. This is the minutes-level
        # commercial-PDK onramp.
        from layout_canvas.pdk.descriptor import PDK

        doc = {
            "name": "acme28",
            "extends": "sky130",
            "extract": {
                "leaf_devices": {
                    "acme28_nch": ["acme28_nch", "nmos"],
                    "acme28_pch": ["acme28_pch", "pmos"],
                }
            },
        }
        pdk = PDK.from_dict(doc)
        base = get_pdk("sky130")
        assert pdk.layers == base.layers            # inherited untouched
        # sky130's extraction truth lives in the tool tables — extends
        # inherits the merged view, so roles come through populated.
        roles = pdk.extract["roles"]
        assert roles["diff"] and roles["well_n"] and roles["met1"]
        leaf = set(pdk.extract["leaf_devices"])
        assert {"acme28_nch", "acme28_pch"} <= leaf   # added
        assert "sky130_fd_pr__nfet_01v8" in leaf      # inherited entries stay
        with pytest.raises(ValueError, match="unknown base"):
            PDK.from_dict({"name": "x", "extends": "no_such_pdk"})

    def test_cli_pdk_init_scaffolds_extends(self, tmp_path: Path,
                                          monkeypatch: pytest.MonkeyPatch,
                                          capsys):
        from layout_canvas.cli import main

        out = tmp_path / "acme28.pdk.json"
        monkeypatch.setattr(
            sys, "argv",
            ["layout-canvas", "pdk", "init", "acme28", "--extends", "sky130",
             "-o", str(out)])
        assert main() == 0
        monkeypatch.setattr(
            sys, "argv", ["layout-canvas", "pdk", "check", str(out)])
        assert main() == 0
        rep = json.loads(capsys.readouterr().out)
        assert rep["status"] == "ok"
        assert rep["extends"] == "sky130"
        assert rep["gen_blocks"]  # inherits everything needed to unlock
