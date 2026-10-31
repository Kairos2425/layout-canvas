"""Mixed-signal stimulus: Verilog -> PWL sources -> ngspice tran.

The digital leg is fail-closed like everything else: no iverilog → the
bench reports ``unavailable``; a compile/vvp failure or a listed net that
never reaches the dump → ``error`` naming it. Real-tool tests skip when
iverilog (env/PATH/wsl) or ngspice is absent.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

import layout_canvas.blocks.sky130  # noqa: F401 - registers sky130 blocks
from layout_canvas.ir.model import Design
from layout_canvas.tools import mixed_signal, verilog
from layout_canvas.tools.mixed_signal import digital_stimulus
from layout_canvas.tools.testbench import run_testbench

ROOT = Path(__file__).parent.parent
OTA_DIGITAL = ROOT / "examples" / "ota_digital.json"
OTA_LAB = ROOT / "examples" / "ota_lab.json"

COUNTER_RTL = """
module counter3;
    reg clk;
    reg [2:0] count;
    initial begin
        clk = 0; count = 0;
        forever #10 clk = ~clk;
    end
    always @(posedge clk) count <= count + 1;
    initial begin #170; $finish; end
endmodule
"""


def _iverilog_available() -> bool:
    cands, _ = verilog._runner_candidates(None)
    return bool(cands)


def _ngspice() -> str | None:
    return os.environ.get("LAYOUT_CANVAS_NGSPICE") or shutil.which("ngspice")


needs_iverilog = pytest.mark.skipif(
    not _iverilog_available(),
    reason="no iverilog found (LAYOUT_CANVAS_IVERILOG, PATH, wsl)")
needs_ngspice = pytest.mark.skipif(
    _ngspice() is None, reason="no ngspice (LAYOUT_CANVAS_NGSPICE)")


def _tb(**over):
    payload = {
        "name": "tb_dig",
        "analysis": "tran",
        "digital": {
            "rtl": COUNTER_RTL,
            "signals": [
                {"verilog_net": "count[0]", "node": "inp",
                 "vhi": 1.8, "vlo": 0.0},
                {"verilog_net": "count[2]", "node": "inn",
                 "vhi": 1.8, "vlo": 0.0},
            ],
            "timescale": "1ns/1ps",
        },
        "specs": [
            {"name": "outp_pp", "signal": "outp", "measure": "pp",
             "min": 0.0, "max": 2.0, "unit": "V"},
        ],
    }
    payload.update(over)
    from layout_canvas.ir.model import Testbench
    return Testbench.model_validate(payload)


def _design(**tb_over) -> Design:
    d = Design.from_json(OTA_DIGITAL.read_text(encoding="utf-8"))
    if tb_over:
        d.testbenches = [_tb(**tb_over)]
    return d


class TestDigitalValidation:
    def test_signals_must_be_non_empty(self):
        with pytest.raises(ValueError):
            _tb(digital={"rtl": "module m; endmodule", "signals": []})

    def test_node_must_be_non_empty(self):
        with pytest.raises(ValueError):
            _tb(digital={"rtl": "module m; endmodule",
                         "signals": [{"verilog_net": "q", "node": ""}]})

    def test_duplicate_nodes_rejected(self):
        with pytest.raises(ValueError, match="duplicate digital node"):
            _tb(digital={"rtl": "module m; endmodule", "signals": [
                {"verilog_net": "a", "node": "inp"},
                {"verilog_net": "b", "node": "inp"}]})

    def test_bad_timescale_and_unknown_keys_rejected(self):
        with pytest.raises(ValueError):
            _tb(digital={"rtl": "module m; endmodule",
                         "signals": [{"verilog_net": "q", "node": "inp"}],
                         "timescale": "bogus"})
        with pytest.raises(ValueError):
            _tb(digital={"rtl": "module m; endmodule",
                         "signals": [{"verilog_net": "q", "node": "inp"}],
                         "extra": 1})

    def test_defaults(self):
        tb = _tb(digital={"rtl": "module m; endmodule",
                          "signals": [{"verilog_net": "q", "node": "inp"}]})
        sig = tb.digital.signals[0]
        assert (sig.vhi, sig.vlo, sig.delay) == (1.8, 0.0, 0.0)
        assert tb.digital.timescale == "1ns/1ps"


class TestPwlHelpers:
    """Pure-Python series -> PWL conversion (no tools needed)."""

    def test_step_edges_and_end_extension(self):
        pts, unknown = mixed_signal._pwl_pairs(
            [(0.0, 0), (10e-9, 1), (20e-9, 0)], 1.8, 0.0, 0.0, 30e-9)
        assert unknown == 0
        assert pts == [(0.0, 0.0), (10e-9, 0.0), (10e-9, 1.8),
                       (20e-9, 1.8), (20e-9, 0.0), (30e-9, 0.0)]

    def test_delay_shifts_all_times(self):
        pts, _ = mixed_signal._pwl_pairs(
            [(0.0, 1)], 1.8, 0.0, 5e-9, None)
        assert pts == [(5e-9, 1.8)]

    def test_xz_hold_last_level_and_count(self):
        pts, unknown = mixed_signal._pwl_pairs(
            [(0.0, "x"), (1e-9, 1), (2e-9, "z")], 1.8, 0.0, 0.0, None)
        assert unknown == 2
        # x at t=0 -> vlo; the z at 2ns holds the 1.8 level (merged point)
        assert pts == [(0.0, 0.0), (1e-9, 0.0), (2e-9, 1.8)]

    def test_constant_waveform_emits_dc(self):
        line = mixed_signal._source_line(
            "inp", [(0.0, 0.9), (1e-9, 0.9)])
        assert "PWL" not in line
        assert line == "V_dig_inp inp 0 0.9"

    def test_parse_dump_dedupes_and_ignores_user_output(self):
        series, end_t, n = mixed_signal._parse_dump(
            "user print\nLC_DUMP 0 q 0\nLC_DUMP 0 q 0\n"
            "LC_DUMP 10 q 1\nLC_END 100\n", 1e-9)
        assert n == 3
        assert end_t == pytest.approx(100e-9)
        assert series["q"] == [(0.0, 0), (10e-9, 1)]


class TestDigitalStimulus:
    @needs_iverilog
    def test_counter_dump_series_and_sources(self):
        res = digital_stimulus(_tb())
        assert res["status"] == "ok", res
        assert res["module"] == "counter3"
        assert res["stopped_by"] == "rtl"
        assert res["raw_dump_lines"] > 0
        s0 = res["series"]["count[0]"]
        assert s0[0] == (0.0, 0)
        assert len(s0) >= 5
        times = [t for t, _ in s0]
        assert times == sorted(times)
        # bit0 of a /20ns counter toggles every 10ns after t=0
        assert s0[1] == (10e-9, 1)
        assert s0[2][0] == pytest.approx(30e-9)
        s2 = res["series"]["count[2]"]
        assert s2[1] == (pytest.approx(70e-9), 1)
        assert set(res["lines"]) == {"inp", "inn"}
        for node in ("inp", "inn"):
            assert res["lines"][node].startswith(f"V_dig_{node} {node} 0 PWL(")
            assert res["lines"][node].rstrip().endswith("r 0)")
            assert res["sources"][node][0] == (0.0, 0.0)
            assert res["end_time"] == pytest.approx(170e-9)

    @needs_iverilog
    def test_rtl_syntax_error_reports_compile_failure(self):
        tb = _tb(digital={
            "rtl": "module broken( ; this is not verilog",
            "signals": [{"verilog_net": "q", "node": "inp"}]})
        res = digital_stimulus(tb)
        assert res["status"] == "error"
        assert "compile" in res["reason"].lower()
        assert res["reason"].strip() != ""

    @needs_iverilog
    def test_listed_signal_not_in_rtl_names_it(self):
        """A verilog_net that does not exist under the top fails
        elaboration — the iverilog message names it."""
        tb = _tb(digital={
            "rtl": COUNTER_RTL,
            "signals": [{"verilog_net": "no_such_net", "node": "inp"}]})
        res = digital_stimulus(tb)
        assert res["status"] == "error"
        assert "no_such_net" in res["reason"]

    def test_signal_missing_from_dump_names_it(self, monkeypatch):
        """A net that compiles but produces no dump lines → error naming
        it (a source must never drive nothing silently)."""
        monkeypatch.setattr(
            verilog, "_runner_candidates",
            lambda exe: ([{"argv": ["fake"], "vvp": ["fake"],
                           "wsl": False, "label": "fake"}], []))
        monkeypatch.setattr(
            verilog, "_compile_and_run",
            lambda cand, srcs, out, timeout: {
                "compile_rc": 0, "compile_ok": True, "compile_output": "",
                "vvp_rc": 0, "vvp_ok": True,
                "vvp_output": "LC_DUMP 0 present 0\n"})
        tb = _tb(digital={
            "rtl": "module m; reg present; endmodule",
            "signals": [{"verilog_net": "present", "node": "inp"},
                        {"verilog_net": "absent", "node": "inn"}]})
        res = digital_stimulus(tb)
        assert res["status"] == "error"
        assert "absent" in res["reason"]
        assert "present" not in res["reason"]

    def test_no_iverilog_unavailable(self, monkeypatch):
        monkeypatch.setattr(
            verilog, "_runner_candidates",
            lambda exe: ([], ["forced-missing"]))
        res = digital_stimulus(_tb())
        assert res["status"] == "unavailable"
        assert "verilog simulator" in res["reason"]


class TestRunTestbench:
    def test_digital_on_op_is_unavailable(self):
        d = _design(analysis="op")
        res = run_testbench(d, "tb_dig")
        assert res["status"] == "unavailable"
        assert res["spec_status"] == "unavailable"
        assert all(s["status"] == "unavailable" for s in res["specs"])
        assert "only applies to tran" in res["specs"][0]["reason"]

    def test_no_iverilog_makes_every_spec_unavailable(self, monkeypatch):
        monkeypatch.setattr(
            verilog, "_runner_candidates",
            lambda exe: ([], ["forced-missing"]))
        res = run_testbench(_design(), "tb_tran_digital")
        assert res["status"] == "unavailable"
        assert res["spec_status"] == "unavailable"
        assert res["specs"][0]["status"] == "unavailable"
        assert "verilog simulator" in res["specs"][0]["reason"]

    @needs_iverilog
    def test_rtl_error_makes_every_spec_error(self):
        d = _design(digital={
            "rtl": "module broken( ; not verilog at all",
            "signals": [{"verilog_net": "q", "node": "inp"}]})
        res = run_testbench(d, "tb_dig")
        assert res["status"] == "error"
        assert res["spec_status"] == "error"
        assert all(s["status"] == "error" for s in res["specs"])
        assert "compile" in res["specs"][0]["reason"].lower()

    @needs_iverilog
    def test_digital_node_not_a_port_is_error(self):
        """Schematic decks can only drive ports/rails — a digital node
        that is neither errors out instead of silently dangling."""
        d = _design(digital={
            "rtl": COUNTER_RTL,
            "signals": [{"verilog_net": "count[0]", "node": "notaport"}]})
        res = run_testbench(d, "tb_dig")
        assert res["status"] == "error"
        assert "notaport" in "; ".join(res.get("errors", []))

    @needs_iverilog
    @needs_ngspice
    def test_real_tran_run_with_pwl_override(self, tmp_path):
        """The fixture bench: counter drives inp/inn; the deck carries
        the PWL cards and no duplicate auto-bias on those ports."""
        d = Design.from_json(OTA_DIGITAL.read_text(encoding="utf-8"))
        res = run_testbench(d, "tb_tran_digital", workdir=tmp_path)
        assert res["status"] == "passed", res.get("errors")
        assert res["spec_status"] == "pass"
        assert res["digital"]["status"] == "ok"
        deck = Path(res["deck_path"]).read_text(encoding="utf-8")
        assert "V_dig_inp inp 0 PWL(" in deck
        assert "V_dig_inn inn 0 PWL(" in deck
        # the auto-bias for the driven ports is gone — no double drive
        assert "V_inp inp" not in deck
        assert "V_inn inn" not in deck
        # the digital edges really moved the analog output
        names = {s["name"]: s for s in res["specs"]}
        assert names["outp_pp"]["status"] == "pass"
        assert names["outp_pp"]["value"] > 0.5
        assert names["inp_reach_vhi"]["value"] == pytest.approx(1.8)

    @needs_iverilog
    @needs_ngspice
    def test_extracted_digital_node_and_dead_node(self, tmp_path):
        """Extracted deck: 'tail' is a real top-level extracted net — a
        digital source may drive it; a phantom net errors."""
        d = Design.from_json(OTA_LAB.read_text(encoding="utf-8"))
        d.testbenches = [_tb(
            name="tb_x", source="extracted",
            digital={"rtl": COUNTER_RTL, "signals": [
                {"verilog_net": "count[0]", "node": "ghostnet"}]},
            probes=["tail"])]
        res = run_testbench(d, "tb_x", workdir=tmp_path / "dead")
        assert res["status"] == "error"
        assert "ghostnet" in "; ".join(res.get("errors", []))
        assert res["spec_status"] == "error"
