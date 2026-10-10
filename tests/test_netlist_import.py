"""Tests for compiler/netlist_import.py — upstream netlist → Block IR."""

import pytest

import layout_canvas.blocks  # noqa: F401 — populate registry
from layout_canvas.compiler.netlist_import import detect_dialect, import_netlist
from layout_canvas.ir.model import Design

SPICE_OTA = """\
* upstream schematic export (analog-canvas style)
.subckt current_mirror in out gate vss
M1 out in vss vss nch w=1u l=0.5u
M2 gate in vss vss nch w=1u l=0.5u
.ends current_mirror
.subckt diff_pair inp inn outp outn tail vss
M1 outp inp tail vss nch w=2u l=0.15u
M2 outn inn tail vss nch w=2u l=0.15u
.ends diff_pair
.subckt pmos_mirror in out gate vss
M1 out in vss vss pch w=1.5u l=0.5u
M2 gate in vss vss pch w=1.5u l=0.5u
.ends pmos_mirror
.subckt ota_top inp inn outp outn vdd vss vbias
XDP inp inn outp outn tail vss diff_pair nf=4 w=2u l=1u
XLOAD outp outn outp vdd pmos_mirror
XTAIL vbias tail vbias vss current_mirror
.ends ota_top
"""

SPECTRE_OTA = """\
// analog-canvas spectre export
subckt current_mirror ( in out gate vss )
    M1 ( out in vss vss ) nch w=1u l=0.5u
    M2 ( gate in vss vss ) nch w=1u l=0.5u
ends current_mirror
subckt diff_pair ( inp inn outp outn tail vss )
    M1 ( outp inp tail vss ) nch w=2u l=0.15u
    M2 ( outn inn tail vss ) nch w=2u l=0.15u
ends diff_pair
subckt ota_top ( inp inn outp outn vdd vss vbias )
    XDP ( inp inn outp outn tail vss ) diff_pair nf=4
    XCM ( vbias tail vbias vss ) current_mirror
ends ota_top
"""


class TestDialectDetect:
    def test_spice(self):
        assert detect_dialect(SPICE_OTA) == "spice"

    def test_spectre(self):
        assert detect_dialect(SPECTRE_OTA) == "spectre"


class TestSpiceImport:
    def test_ok_maps_all_instances(self):
        res = import_netlist(SPICE_OTA, pdk="sky130")
        assert res["status"] == "ok"
        blocks = {m["instance"]: m["block"] for m in res["mapped"]}
        assert blocks["XDP"] == "sky130.diff_pair"
        assert blocks["XTAIL"] == "sky130.current_mirror"
        # pin-set fallback: 'pmos_mirror' has no name match but its pins
        # equal the current_mirror signature
        assert blocks["XLOAD"] == "sky130.current_mirror"

    def test_polarity_inferred_from_subckt_devices(self):
        res = import_netlist(SPICE_OTA, pdk="sky130")
        by_id = {i["id"]: i["params"] for i in res["ir"]["instances"]}
        # pmos_mirror body is pch devices → type=pmos (its vss pin is the
        # interior source strap on vdd, not global substrate)
        assert by_id["XLOAD"]["type"] == "pmos"
        assert by_id["XTAIL"]["type"] == "nmos"

    def test_ir_validates_and_roundtrips(self):
        res = import_netlist(SPICE_OTA, pdk="sky130")
        d = Design.from_json(res["ir_text"])
        assert d.pdk == "sky130"
        assert len(d.instances) == 3
        netmap = {n.name: set(n.pins) for n in d.nets}
        assert netmap["tail"] == {"XDP.tail", "XTAIL.out"}
        port_pins = {p.name: p.pin for p in d.ports}
        assert port_pins["inp"] == "XDP.inp"
        assert port_pins["vdd"] in {"XLOAD.vss", "XTAIL.gate",
                                    "XTAIL.in"}
        # relative placement chain — imported rows never overlap at origin
        assert d.instances[1].placement.relative_to == "XDP"
        assert d.instances[1].placement.relation == "right_of"

    def test_param_aliases_and_units(self):
        res = import_netlist(SPICE_OTA, pdk="sky130")
        dp = next(i for i in res["ir"]["instances"] if i["id"] == "XDP")
        assert dp["params"] == {"fingers": 4, "width": 2.0, "length": 1.0}

    def test_out_of_range_param_dropped_not_emitted(self):
        bad = SPICE_OTA.replace("nf=4 w=2u l=1u", "nf=4 w=1p l=1u")
        res = import_netlist(bad, pdk="sky130")
        dp = next(i for i in res["ir"]["instances"] if i["id"] == "XDP")
        assert "width" not in dp["params"]
        assert any("w=1p" in d or "width" in d for d in res["diagnostics"])
        Design.from_json(res["ir_text"])  # stays compilable

    def test_m_multiplier_not_fingers(self):
        net = SPICE_OTA.replace("nf=4 w=2u l=1u", "m=4")
        res = import_netlist(net, pdk="sky130")
        dp = next(i for i in res["ir"]["instances"] if i["id"] == "XDP")
        assert dp["params"].get("fingers") is None
        assert any("m=" in d for d in res["diagnostics"])


class TestSpectreImport:
    def test_ok(self):
        res = import_netlist(SPECTRE_OTA, pdk="sky130")
        assert res["status"] == "ok", res["diagnostics"]
        assert res["dialect"] == "spectre"
        blocks = {m["instance"]: m["block"] for m in res["mapped"]}
        assert blocks["XDP"] == "sky130.diff_pair"
        d = Design.from_json(res["ir_text"])
        assert {n.name for n in d.nets} >= {"tail"}
        assert d.instances[0].params == {"fingers": 4}


class TestFailClosed:
    def test_unknown_subckt_named_unresolved(self):
        net = SPICE_OTA + "\n.subckt top2 inp out\nXU1 inp out mystery_cell\n.ends\n"
        res = import_netlist(net, pdk="sky130", top="top2")
        assert res["status"] == "failed"  # nothing mapped
        assert res["unresolved"][0]["subckt"] == "mystery_cell"
        assert "not in file" in res["unresolved"][0]["reason"]

    def test_partial_when_one_unresolved(self):
        net = SPICE_OTA.replace(
            "XTAIL vbias tail vbias vss current_mirror",
            "XTAIL vbias tail vbias vss undefined_block")
        res = import_netlist(net, pdk="sky130", top="ota_top")
        assert res["status"] == "partial"
        assert len(res["mapped"]) == 2
        assert res["unresolved"][0]["subckt"] == "undefined_block"

    def test_pin_mismatch_refused_not_guessed(self):
        net = SPICE_OTA.replace(
            ".subckt diff_pair inp inn outp outn tail vss",
            ".subckt diff_pair a b c d e f")
        res = import_netlist(net, pdk="sky130")
        assert res["status"] == "partial"
        unr = next(u for u in res["unresolved"] if u["instance"] == "XDP")
        assert "pins differ" in unr["reason"]

    def test_pin_count_mismatch_refused(self):
        net = SPICE_OTA.replace(
            "XDP inp inn outp outn tail vss diff_pair",
            "XDP inp inn outp diff_pair")
        res = import_netlist(net, pdk="sky130")
        unr = next(u for u in res["unresolved"] if u["instance"] == "XDP")
        assert "pin count mismatch" in unr["reason"]

    def test_ambiguous_top_fails_closed(self):
        net = SPICE_OTA + "\n.subckt another_top a b\nXU1 a b a a current_mirror\n.ends\n"
        res = import_netlist(net, pdk="sky130")
        assert res["status"] == "failed"
        assert any("ambiguous top" in d for d in res["diagnostics"])
        assert res["ir"] is None

    def test_explicit_top(self):
        net = SPICE_OTA + "\n.subckt another_top a b\nXU1 a b a a current_mirror\n.ends\n"
        res = import_netlist(net, pdk="sky130", top="ota_top")
        assert res["status"] == "ok"
        assert res["design_name"] == "ota_top"

    def test_missing_top_named(self):
        res = import_netlist(SPICE_OTA, pdk="sky130", top="nope")
        assert res["status"] == "failed"
        assert "nope" in res["diagnostics"][-1]

    def test_flat_top_devices_counted(self):
        net = "M1 d g s b nch w=1u l=0.15u\nM2 d2 g s b nch w=1u l=0.15u\n"
        res = import_netlist(net, pdk="sky130")
        assert res["status"] == "failed"
        assert res["top_level_devices_skipped"] == 2

    def test_wrong_pdk_reports_gap(self):
        res = import_netlist(SPICE_OTA, pdk="no_such_pdk")
        assert res["status"] == "failed"
        assert any("no blocks registered" in u["reason"]
                   for u in res["unresolved"])


class TestIdSanitise:
    def test_weird_instance_names(self):
        net = SPICE_OTA.replace("XDP ", "X.ODD ")
        res = import_netlist(net, pdk="sky130")
        ids = [i["id"] for i in res["ir"]["instances"]]
        assert all(i.replace("_", "").isalnum() for i in ids)
        Design.from_json(res["ir_text"])


class TestEndToEnd:
    def test_imported_ir_compiles(self):
        pytest.importorskip("gdsfactory")
        from layout_canvas.compiler.compile import compile_design

        res = import_netlist(SPICE_OTA, pdk="sky130")
        design = Design.from_json(res["ir_text"])
        comp = compile_design(design)
        assert comp is not None
        assert comp.bbox().area() > 0


class TestFlatNetlist:
    def test_flat_spice_x_instances_no_crash(self):
        # Regression: top-level X cards used to hit parsed.instances
        # (AttributeError) -- flat netlists must import via top_instances.
        net = """\
.subckt diff_pair inp inn outp outn tail vss
.ends
.subckt current_mirror iin iout vss
.ends
XDP inp inn outp outn tail vss diff_pair
XCM iin iout vss current_mirror
.end
"""
        res = import_netlist(net, pdk="sky130")
        assert res["status"] in ("ok", "partial")
        assert res["ir"] is not None
        assert any("flat" in d for d in res["diagnostics"])

    def test_misdetected_file_fails_closed_not_crashes(self):
        # Spectre-syntax headers w/o parens misdetected as spice must
        # degrade to unresolved diagnostics, never raise.
        weird = "subckt foo a b\nM1 (a b b b) nmos w=1u\nends foo\nX1 a b foo\n"
        res = import_netlist(weird, pdk="sky130")
        assert res["status"] in ("ok", "partial", "failed")


class TestRailPinAliases:
    SPECTRE_PMIRROR = """\
subckt pmos_mirror ( in out gate vdd )
    M1 (in in vdd vdd) pch w=2u l=0.5u
    M2 (out in vdd vdd) pch w=2u l=0.5u
ends pmos_mirror
subckt top ( in out gate vdd vss )
    XM (in out gate vdd) pmos_mirror
ends top
"""

    def test_pmos_mirror_maps_via_rail_alias(self):
        res = import_netlist(self.SPECTRE_PMIRROR, pdk="sky130", top="top")
        assert res["status"] == "ok"
        inst = res["ir"]["instances"][0]
        assert inst["block"] == "sky130.current_mirror"
        assert inst["params"].get("type") == "pmos"
        # the vdd pin binds to the block's 'vss' port, not left dangling
        port_vdd = next(p for p in res["ir"]["ports"] if p["name"] == "vdd")
        assert port_vdd["pin"] == "XM.vss"

    def test_rail_arity_still_guarded(self):
        # a 1-rail subckt must not alias onto a multi-pin block whose
        # rail count differs — arity survives canonicalisation
        net = ".subckt ota inp inn outp outn vdd\n.ends\nX1 a b c d vdd ota\n"
        res = import_netlist(net, pdk="sky130")
        assert res["status"] in ("partial", "failed")
        assert res["unresolved"]
