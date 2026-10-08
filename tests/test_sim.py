"""Tests for the fail-closed ngspice runner."""

import os
import re
import shutil
from pathlib import Path

import gdsfactory as gf
import pytest

from layout_canvas.blocks import base
from layout_canvas.blocks.base import Block
from layout_canvas.ir.model import BlockSpec, Design
from layout_canvas.tools.sim import run_netlist, simulate_design


def _design_with(block_name: str) -> Design:
    return Design.model_validate(
        {
            "name": "sim_test",
            "pdk": "sky130",
            "instances": [{"id": "x1", "block": block_name, "params": {}}],
        }
    )


def test_unresolvable_block_refused_and_named():
    base._REGISTRY["test.blackbox"] = Block(
        spec=BlockSpec(
            name="test.blackbox",
            pdk="sky130",
            level="L0",
            summary="no emitter",
            params=[],
            ports=[],
        ),
        build=lambda: gf.Component(),
        netlist=None,
    )
    try:
        result = simulate_design(_design_with("test.blackbox"), "V1 a 0 1")
        assert result.status == "refused"
        assert "test.blackbox" in result.unresolved_blocks
        assert result.log_path is None
    finally:
        del base._REGISTRY["test.blackbox"]


def test_missing_ngspice_is_unavailable():
    result = run_netlist("V1 a 0 1\n.op\n.end", executable="definitely-not-ngspice-xyz")
    assert result.status == "unavailable"
    assert "definitely-not-ngspice-xyz" in result.errors[0]


def test_error_scanner_catches_ngspice_failures(tmp_path):
    from layout_canvas.tools.sim import _find_errors

    log = "some output\nError: no such model nfet_01v8\nmore output"
    assert _find_errors(log) == ["Error: no such model nfet_01v8"]
    assert _find_errors("all good") == []


def _ngspice_binary() -> str | None:
    return os.environ.get("LAYOUT_CANVAS_NGSPICE") or shutil.which("ngspice")


@pytest.mark.skipif(_ngspice_binary() is None, reason="ngspice not installed")
def test_real_ngspice_op_smoke(tmp_path):
    """End-to-end: compile a diff_pair, bias it, solve the DC op for real."""
    import layout_canvas.blocks.sky130  # noqa: F401
    from layout_canvas.compiler.netlist import compile_netlist

    design = Design.model_validate(
        {
            "name": "ngspice_smoke",
            "pdk": "sky130",
            "instances": [{"id": "dp", "block": "sky130.diff_pair", "params": {}}],
        }
    )
    variant = re.search(r"\.subckt (sky130_diff_pair__\w+)", compile_netlist(design)).group(1)
    lib = Path(__file__).parent.parent / "examples" / "models" / "illustrative_mos.lib"
    stimulus = f"""
VDD vdd 0 1.8
VSS vss 0 0
VINP inp 0 0.95
VINN inn 0 0.9
IT tail vss 10u
RDP vdd outp 10k
RDN vdd outn 10k
X1 inp inn outp outn tail vss {variant}
.op
"""
    result = simulate_design(
        design,
        stimulus,
        includes=[str(lib)],
        executable=_ngspice_binary(),
        workdir=tmp_path,
    )
    # Illustrative level-1 models, real ngspice 鈥?asserts the interface works;
    # says nothing about silicon (per ADR 0005 / illustrative-model rule).
    assert result.status == "passed", result.errors
    assert result.log_path and result.log_path.is_file()


_SKY130_TT_LIB = (
    Path(__file__).parent.parent / "examples" / "models" / "sky130" / "sky130_tt.lib"
)


@pytest.mark.skipif(
    _ngspice_binary() is None or not _SKY130_TT_LIB.is_file(),
    reason="ngspice or bundled sky130 TT models not present",
)
def test_real_sky130_tt_op_smoke(tmp_path):
    """Real SkyWater BSIM4 TT models + real ngspice: DC op of a diff pair."""
    import layout_canvas.blocks.sky130  # noqa: F401
    from layout_canvas.compiler.netlist import compile_netlist

    design = Design.model_validate(
        {
            "name": "sky130_tt_smoke",
            "pdk": "sky130",
            "instances": [{"id": "dp", "block": "sky130.diff_pair", "params": {}}],
        }
    )
    variant = re.search(r"\.subckt (sky130_diff_pair__\w+)", compile_netlist(design)).group(1)
    stimulus = f"""
VDD vdd 0 1.8
VSS vss 0 0
VINP inp 0 0.95
VINN inn 0 0.9
IT tail vss 10u
RDP vdd outp 10k
RDN vdd outn 10k
X1 inp inn outp outn tail vss {variant}
.op
"""
    result = simulate_design(
        design,
        stimulus,
        includes=[str(_SKY130_TT_LIB)],
        executable=_ngspice_binary(),
        workdir=tmp_path,
    )
    assert result.status == "passed", result.errors
    assert result.log_path and result.log_path.is_file()


def test_deck_builder_includes_stimulus_and_end():
    from layout_canvas.tools.sim import _build_deck

    deck = _build_deck(".subckt a x\n.ends", "X1 x a\n.op", ["/models/sky130.lib"])
    assert '.include "/models/sky130.lib"' in deck
    assert deck.rstrip().endswith(".end")


class TestParseWrdata:
    """ngspice wrdata emits (scale, re) pairs for real analyses and
    (scale, re, im) triples for ac — the parser splits on that."""

    def test_real_pairs(self, tmp_path):
        from layout_canvas.tools.sim import parse_wrdata

        f = tmp_path / "op.dat"
        f.write_text(" 0.0 0.9  0.0 0.8\n", encoding="utf-8")
        out = parse_wrdata(f)
        assert out["v0"] == [0.0]
        assert out["v1"] == [0.9] and out["v1i"] == [0.0]
        assert out["v2"] == [0.8] and out["v2i"] == [0.0]

    def test_complex_triples_keep_imag(self, tmp_path):
        from layout_canvas.tools.sim import parse_wrdata

        f = tmp_path / "ac.dat"
        f.write_text(
            " 1e3 1.0 0.0  1e3 0.5 0.25\n"
            " 2e3 1.0 0.0  2e3 0.4 0.20\n",
            encoding="utf-8")
        out = parse_wrdata(f)
        assert out["v0"] == [1e3, 2e3]
        assert out["v1"] == [1.0, 1.0] and out["v1i"] == [0.0, 0.0]
        # v2 keeps real AND imaginary samples under v2 / v2i
        assert out["v2"] == [0.5, 0.4]
        assert out["v2i"] == [0.25, 0.20]

    def test_ac_magnitude_waves(self):
        from layout_canvas.tools.sim import _ac_waves

        waves = _ac_waves(
            ["a", "b"],
            {"v1": [3.0, 1.0], "v1i": [4.0, 0.0],
             "v2": [0.0, -2.0], "v2i": [0.0, 0.0]})
        assert waves["a"] == [5.0, 1.0]
        assert waves["b"] == [0.0, 2.0]
        # 20*log10(5) = 13.979 dB
        assert waves["a__db"][0] == pytest.approx(13.979, abs=1e-3)
