"""Verilog blackbox stub + Icarus lint (fail-closed).

Covers the pure-Python stub contract (port list == Design.ports with the
input/output/inout direction mapping — no tool needed) and the iverilog
lint path, which skips when no runner (env/PATH/wsl) exists.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import layout_canvas.blocks.sky130  # noqa: F401 - registers sky130 blocks
from layout_canvas.ir.model import Design
from layout_canvas.tools import verilog
from layout_canvas.tools.verilog import verilog_check, verilog_stub

REPO = Path(__file__).resolve().parent.parent
OTA_JSON = REPO / "examples" / "ota_lab.json"


def _ota() -> Design:
    return Design.from_json(OTA_JSON.read_text(encoding="utf-8"))


def _iverilog_available() -> bool:
    cands, _ = verilog._runner_candidates(None)
    return bool(cands)


needs_iverilog = pytest.mark.skipif(
    not _iverilog_available(),
    reason="no iverilog found (LAYOUT_CANVAS_IVERILOG, PATH, wsl)")


# --- pure-Python stub contract --------------------------------------------


def test_stub_port_list_matches_design_ports():
    design = _ota()
    text = verilog_stub(design)
    assert design.name == "ota_lab"
    assert "module ota_lab (" in text
    # every declared port, exactly once, with the direction mapping
    for p in design.ports:
        want = {"input": "input", "output": "output"}.get(
            p.direction, "inout")
        decl = f"{want} wire {p.name}"
        assert text.count(decl) == 1, (p.name, text)
    assert text.count("input wire") == 2     # inp, inn
    assert text.count("output wire") == 2    # outp, outn
    assert "assign" not in text and "always" not in text
    assert text.rstrip().endswith("endmodule")


def test_stub_maps_other_directions_to_inout():
    design = Design.model_validate({
        "name": "esd_bb", "pdk": "sky130",
        "instances": [{"id": "u1", "block": "sky130.esd_dual_diode",
                       "params": {}}],
        "ports": [
            {"name": "pad", "pin": "u1.pad", "direction": "inout"},
            {"name": "vdd", "pin": "u1.vdd", "direction": "supply"},
            {"name": "vss", "pin": "u1.vss", "direction": "supply"},
        ],
    })
    text = verilog_stub(design)
    for name in ("pad", "vdd", "vss"):
        assert f"inout wire {name}" in text
    assert "supply" not in text


def test_stub_header_names_design_and_generator():
    text = verilog_stub(_ota())
    assert "ota_lab" in text.splitlines()[0]
    assert "layout-canvas" in text


# --- iverilog lint (real tool; skips when absent) --------------------------


@needs_iverilog
def test_lint_ota_stub_passes():
    res = verilog_check(_ota())
    assert res["status"] == "pass", res


@needs_iverilog
def test_lint_broken_stub_fails(monkeypatch):
    """Dropping a port decl while the bench still hooks it up must fail."""
    broken = verilog_stub(_ota()).replace("input wire inn,", "")
    assert "inn" not in broken
    # the tb is generated from design.ports, so .inn() remains while the
    # module loses the port -> iverilog reports a port mismatch
    monkeypatch.setattr(verilog, "verilog_stub",
                        lambda design, **kw: broken)
    res = verilog_check(_ota())
    assert res["status"] == "fail", res
    assert res["output"]


def test_lint_unavailable_when_no_tool(monkeypatch, tmp_path):
    monkeypatch.setenv(verilog.IVERILOG_ENV,
                       str(tmp_path / "nope" / "iverilog"))
    monkeypatch.delenv(verilog.WSL_DISTRO_ENV, raising=False)
    monkeypatch.setattr(verilog.shutil, "which", lambda name: None)
    res = verilog_check(_ota())
    assert res["status"] == "unavailable"
    assert res["reason"]
    assert "tried" in res["reason"]
