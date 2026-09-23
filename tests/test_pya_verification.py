"""In-process KLayout engine tests: real extraction, DRC, and LVS.

These exercise the installed ``klayout`` python module — the same engine the
klayout application uses — against real compiled GDS. No external binary.
"""
from pathlib import Path

import pytest

import layout_canvas.blocks.sky130  # noqa: F401 - registers sky130 blocks
from layout_canvas.compiler.compile import compile_design
from layout_canvas.compiler.netlist import compile_netlist
from layout_canvas.ir.model import Design
from layout_canvas.tools.drc import run_drc
from layout_canvas.tools.extract import extract_netlist
from layout_canvas.tools.lvs import run_lvs

pytest.importorskip("klayout.db", reason="klayout python module required")


def _mirror_design() -> Design:
    return Design.model_validate({
        "name": "pya_mirror",
        "pdk": "sky130",
        "instances": [{"id": "cm", "block": "sky130.current_mirror",
                       "params": {"fingers": 2}}],
        "ports": [{"name": "IN", "pin": "cm.in", "direction": "input"}],
    })


def _gds(design: Design, path: Path) -> Path:
    comp = compile_design(design)
    out = path / f"{design.name}.gds"
    comp.write_gds(str(out))
    return out


def test_extract_devices_from_real_gds(tmp_path: Path):
    gds = _gds(_mirror_design(), tmp_path)
    result = extract_netlist(gds, tech="sky130")
    assert result.status == "ok"
    assert result.errors == []
    # current_mirror f2 -> ABBA pattern -> 4 physical finger devices
    assert result.devices == 4
    assert "nfet_01v8" in result.netlist_text
    # geometry honours the width param (diff height = channel width)
    assert "W=1U" in result.netlist_text
    # the pin label surfaces as a named net in the extracted netlist
    assert "IN" in result.netlist_text


def test_pya_drc_catches_real_violation(tmp_path: Path):
    import klayout.db as db

    ly = db.Layout()
    ly.dbu = 0.001
    top = ly.create_cell("bad")
    top.shapes(ly.layer(66, 20)).insert(db.Box(0, 0, 100, 2000))  # 0.1um poly
    gds = tmp_path / "bad.gds"
    ly.write(str(gds))

    result = run_drc(str(gds), tech="sky130", engine="pya")
    assert result.status == "failed"
    assert result.clean is False
    assert result.total_violations >= 1
    assert any(v["rule"] == "66/20.w" for v in result.violations)


def test_pya_drc_passes_compliant_layout(tmp_path: Path):
    gds = _gds(_mirror_design(), tmp_path)
    result = run_drc(str(gds), tech="sky130", engine="pya")
    assert result.status == "passed"
    assert result.clean is True


def test_pya_lvs_compares_real_netlists(tmp_path: Path):
    design = _mirror_design()
    gds = _gds(design, tmp_path)
    spice = tmp_path / "ref.spice"
    spice.write_text(compile_netlist(design))

    result = run_lvs(layout_path=gds, schematic_path=spice,
                     cell_name=design.name, engine="pya")
    # The mirror draws real strapped geometry now — extraction yields the
    # full in/out/gate/vss topology and LVS must match the reference.
    assert result.status == "passed"
    assert result.match is True
    assert result.report_path and result.report_path.is_file()
    text = result.report_path.read_text()
    assert "MATCH" in text
    assert "extracted devices:" in text


def test_pya_lvs_self_match(tmp_path: Path):
    """Round-tripped extracted netlist vs itself must match — this validates
    the whole extract -> SPICE -> compare path end to end."""
    import klayout.db as db
    from layout_canvas.tools.extract import extract_netlist

    gds = _gds(_mirror_design(), tmp_path)
    ext = extract_netlist(gds, tech="sky130")
    assert ext.status == "ok"

    ext_path = tmp_path / "extracted.cir"
    ext_path.write_text(ext.netlist_text)
    nl_a = db.Netlist()
    nl_a.read(str(ext_path), db.NetlistSpiceReader())
    nl_b = db.Netlist()
    nl_b.read(str(ext_path), db.NetlistSpiceReader())

    def top(nl):
        while True:
            subs = [c for c in nl.each_circuit()
                    if c.name.upper() != "PYA_MIRROR"]
            if not subs:
                break
            nl.flatten_circuit(subs[0])
        return next(c for c in nl.each_circuit()
                    if c.name.upper() == "PYA_MIRROR")

    a, b = top(nl_a), top(nl_b)
    cmp = db.NetlistComparer()
    cmp.same_circuits(a, b)
    assert cmp.compare(nl_a, nl_b) is True


def test_probe_reports_pya_engine():
    from layout_canvas.tools.backends import probe_environment
    report = probe_environment()
    pya = next(t for t in report["in_process_engines"]
               if t["name"] == "klayout-pya")
    assert pya["status"] == "available"
    assert "klayout-pya" in report["verification"]["available"]
