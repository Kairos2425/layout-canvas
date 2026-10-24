"""Structured testbenches: IR validation, spec scoring, and real sim runs."""

import os
import shutil
from pathlib import Path

import pytest

import layout_canvas.blocks.sky130  # noqa: F401
from layout_canvas.engine import DesignSession
from layout_canvas.ir.model import Design
from layout_canvas.tools.testbench import (
    evaluate_specs,
    measure_signal,
    run_all,
    run_testbench,
)

ROOT = Path(__file__).parent.parent
LAB = ROOT / "examples" / "ota_lab.json"


def _design(**over) -> Design:
    payload = {
        "name": "tb_test",
        "pdk": "sky130",
        "instances": [{"id": "dp", "block": "sky130.diff_pair", "params": {}}],
    }
    payload.update(over)
    return Design.model_validate(payload)


class TestIRValidation:
    def test_schematic_rejects_stimulus_and_probes(self):
        with pytest.raises(Exception, match="stimulus"):
            _design(testbenches=[{
                "name": "tb", "source": "schematic", "stimulus": "V1 a 0 1"}])
        with pytest.raises(Exception, match="probes"):
            _design(testbenches=[{
                "name": "tb", "source": "schematic", "probes": ["x"]}])

    def test_extracted_accepts_stimulus_and_probes(self):
        d = _design(testbenches=[{
            "name": "tb", "source": "extracted",
            "stimulus": "V1 a 0 1", "probes": ["tail"]}])
        assert d.testbenches[0].probes == ["tail"]

    def test_spec_needs_a_bound_and_ordered_bounds(self):
        with pytest.raises(Exception, match="min and/or max"):
            _design(testbenches=[{"name": "tb", "specs": [
                {"name": "s", "signal": "outp"}]}])
        with pytest.raises(Exception, match="min"):
            _design(testbenches=[{"name": "tb", "specs": [
                {"name": "s", "signal": "outp", "min": 2.0, "max": 1.0}]}])

    def test_duplicate_testbench_and_spec_names_rejected(self):
        tb = {"name": "tb", "specs": [
            {"name": "s", "signal": "a", "min": 0},
            {"name": "s", "signal": "b", "min": 0}]}
        with pytest.raises(Exception, match="duplicate spec"):
            _design(testbenches=[tb])
        with pytest.raises(Exception, match="duplicate testbench"):
            _design(testbenches=[{"name": "tb"}, {"name": "tb"}])


class TestMeasureAndEvaluate:
    def test_measure_signal(self):
        xs = [1.0, 3.0, 2.0]
        assert measure_signal(xs, "final") == 2.0
        assert measure_signal(xs, "min") == 1.0
        assert measure_signal(xs, "max") == 3.0
        assert measure_signal(xs, "mean") == 2.0
        assert measure_signal(xs, "pp") == 2.0
        with pytest.raises(ValueError):
            measure_signal([], "final")

    def test_measure_db_and_bw_3db(self):
        import math

        # db = 20*log10 of the first sample (low-freq ac gain)
        assert measure_signal([0.5, 2.0], "db") == pytest.approx(
            20 * math.log10(0.5))
        with pytest.raises(ValueError, match="positive"):
            measure_signal([0.0, 1.0], "db")
        # bw_3db = sweep value at the first drop below |v0|/sqrt(2)
        sweep = [1e3, 1e4, 1e5, 1e6]
        mag = [1.0, 0.9, 0.6, 0.5]
        assert measure_signal(mag, "bw_3db", sweep) == 1e5
        with pytest.raises(ValueError, match="never"):
            measure_signal([1.0, 0.99], "bw_3db", [1e3, 1e4])
        with pytest.raises(ValueError, match="sweep"):
            measure_signal([1.0], "bw_3db")

    def test_evaluate_specs_bw_3db_uses_result_sweep(self):
        res = {"status": "passed",
               "sweep": [1e3, 1e4, 1e5],
               "waves": {"out": [1.0, 0.9, 0.5]}}
        out = evaluate_specs(res, [self._spec(
            measure="bw_3db", signal="out", min=9e4, max=None, unit="Hz")])
        assert out[0]["status"] == "pass" and out[0]["value"] == 1e5
        # monotone-rising response never crosses -3dB -> unavailable
        res["waves"]["out"] = [1.0, 1.1, 1.2]
        out = evaluate_specs(res, [self._spec(
            measure="bw_3db", signal="out", min=9e4, max=None, unit="Hz")])
        assert out[0]["status"] == "unavailable"
        assert "never" in out[0]["reason"]

    def _spec(self, **kw):
        from layout_canvas.ir.model import Spec
        base = {"name": "s", "signal": "out", "min": 0.5, "max": 1.5}
        base.update(kw)
        return Spec.model_validate(base)

    def test_pass(self):
        res = {"status": "passed", "waves": {"out": [1.0]}}
        out = evaluate_specs(res, [self._spec()])
        assert out[0]["status"] == "pass" and out[0]["value"] == 1.0
        assert out[0]["reason"] == ""

    def test_fail_with_reason(self):
        res = {"status": "passed", "waves": {"out": [0.2]}}
        out = evaluate_specs(res, [self._spec()])
        assert out[0]["status"] == "fail"
        assert "0.2" in out[0]["reason"] and "min" in out[0]["reason"]

    def test_missing_signal_lists_available(self):
        res = {"status": "passed", "waves": {"a": [1.0], "b": [2.0]}}
        out = evaluate_specs(res, [self._spec(signal="zzz")])
        assert out[0]["status"] == "unavailable"
        assert "available: a, b" in out[0]["reason"]

    def test_sim_not_ok_makes_everything_unavailable(self):
        res = {"status": "refused",
               "errors": ["blocks without transistor-level emitters: x"]}
        specs = [self._spec(name="s1"), self._spec(name="s2")]
        out = evaluate_specs(res, specs)
        assert all(e["status"] == "unavailable" for e in out)
        assert "refused" in out[0]["reason"]


class TestTransientMeasures:
    """settling / slew / overshoot — tran-only, sweep-axis measures."""

    def _spec(self, **kw):
        from layout_canvas.ir.model import Spec
        base = {"name": "s", "signal": "out", "min": 0.5, "max": 1.5}
        base.update(kw)
        return Spec.model_validate(base)

    def test_settling_finds_last_band_exit(self):
        # ramp 0.9 -> 1.3 between 50 and 60 ns; final=1.3, band ±0.026.
        sw = [0.0, 25e-9, 50e-9, 55e-9, 58e-9, 59e-9, 59.5e-9, 60e-9, 1e-6]
        xs = [0.9, 0.9, 0.9, 1.0, 1.2, 1.24, 1.28, 1.3, 1.3]
        assert measure_signal(xs, "settling", sw) == pytest.approx(59.5e-9)
        # tighter tol -> later settle (band ±0.013: 1.287 needed)
        assert measure_signal(xs, "settling", sw, tol=0.01) == \
            pytest.approx(60e-9)
        # already inside the band for the whole sweep -> settles at t0
        assert measure_signal([1.3] * len(sw), "settling", sw) == 0.0

    def test_settling_never_is_value_error(self):
        # only the last sample is inside the band — "settled" at the very
        # end cannot prove it stays, so it reports never-settled.
        sw = [0.0, 1e-9, 2e-9, 3e-9]
        with pytest.raises(ValueError, match="never"):
            measure_signal([0.5, 0.5, 1.5, 1.0], "settling", sw, tol=0.1)
        with pytest.raises(ValueError, match="sweep"):
            measure_signal([0.5, 1.0], "settling")

    def test_slew_and_overshoot(self):
        sw = [0.0, 1e-9, 2e-9, 3e-9, 4e-9, 5e-9]
        xs = [0.0, 1.2, 1.05, 1.0, 1.0, 1.0]
        # 1.2 V in 1 ns = 1.2e9 V/s
        assert measure_signal(xs, "slew", sw) == pytest.approx(1.2e9)
        # peak 1.2 vs final 1.0 -> 20 %
        assert measure_signal(xs, "overshoot", sw) == pytest.approx(20.0)
        # undershoot-only wave reports 0 %, not a negative number
        assert measure_signal([1.0, 0.4, 1.0], "overshoot",
                              [0.0, 1e-9, 2e-9]) == 0.0
        with pytest.raises(ValueError, match="sweep"):
            measure_signal(xs, "slew")
        with pytest.raises(ValueError, match="nonzero final"):
            measure_signal([0.0, 0.5, 0.0], "overshoot", [0, 1, 2])
        with pytest.raises(ValueError, match="increasing"):
            measure_signal([1.0, 1.5], "slew", [1e-9, 1e-9])

    def test_transient_measure_on_op_is_unavailable(self):
        res = {"status": "passed", "waves": {"out": [0.9, 1.0]}}
        spec = self._spec(measure="settling", signal="out",
                          min=None, max=1e-6)
        out = evaluate_specs(res, [spec], analysis="op")
        assert out[0]["status"] == "unavailable"
        assert "transient-only" in out[0]["reason"]
        # same spec on a tran run without a sweep axis -> unavailable too
        out = evaluate_specs(res, [spec], analysis="tran")
        assert out[0]["status"] == "unavailable"
        assert "sweep" in out[0]["reason"]

    def test_spec_tol_validation(self):
        from layout_canvas.ir.model import Spec
        with pytest.raises(Exception, match="tol"):
            Spec.model_validate({"name": "s", "signal": "x",
                                 "measure": "settling", "max": 1, "tol": 0})
        ok = Spec.model_validate({"name": "s", "signal": "x",
                                  "measure": "settling", "max": 1,
                                  "tol": 0.05})
        assert ok.tol == 0.05

    def test_tiny_measure_values_not_rounded_to_zero(self):
        """round(6e-8 s, 6) would read 0 — spec values keep 6 sig digits."""
        res = {"status": "passed",
               "sweep": [0.0, 1e-9, 2e-9, 3e-9],
               "waves": {"out": [0.0, 1.3, 1.0, 1.0]}}
        spec = self._spec(measure="settling", signal="out",
                          min=None, max=1e-6)
        out = evaluate_specs(res, [spec], analysis="tran")
        assert out[0]["status"] == "pass"
        assert out[0]["value"] == pytest.approx(2e-9)
        assert out[0]["value"] != 0.0


class TestRunTestbench:
    def test_unknown_name_raises_keyerror(self):
        with pytest.raises(KeyError):
            run_testbench(_design(), "nope")

    def test_run_all_returns_per_bench_results(self):
        d = _design(testbenches=[{"name": "t1"}, {"name": "t2"}])
        runs = run_all(d)
        assert [r["testbench"] for r in runs] == ["t1", "t2"]
        assert all(r["spec_status"] == "no_specs" for r in runs)

    def test_unavailable_sim_fail_closed(self):
        d = _design(testbenches=[{"name": "t1", "specs": [
            {"name": "s", "signal": "outp", "min": 0}]}])
        r = run_testbench(d, "t1", executable="definitely_not_ngspice_xyz")
        assert r["status"] == "unavailable"
        assert r["specs"][0]["status"] == "unavailable"
        assert r["spec_status"] == "unavailable"

    def test_transact_set_testbenches_then_run(self):
        """set_testbenches goes through the session op boundary."""
        session = DesignSession(_design())
        env = session.transact([{
            "op": "set_testbenches",
            "testbenches": [{"name": "tb_sess", "specs": [
                {"name": "s", "signal": "outp", "min": 0, "max": 5}]}],
        }])
        assert env.status == "ok"
        assert session.design.testbenches[0].name == "tb_sess"
        r = run_testbench(session.design, "tb_sess",
                         executable="definitely_not_ngspice_xyz")
        assert r["testbench"] == "tb_sess"
        assert r["status"] == "unavailable"
        # invalid payload is rejected, not half-applied
        bad = session.transact([{
            "op": "set_testbenches",
            "testbenches": [{"name": "bad", "source": "schematic",
                             "probes": ["x"]}],
        }])
        assert bad.status == "rejected"
        assert bad.diagnostics[0].code == "invalid-testbench"
        assert session.design.testbenches[0].name == "tb_sess"


_NGSPICE = os.environ.get("LAYOUT_CANVAS_NGSPICE") or shutil.which("ngspice")


class TestStressCorner:
    """Accelerated stress corner: IR validation, drift math, spec routing."""

    def _design(self, tbs) -> Design:
        payload = {
            "name": "tb_stress",
            "pdk": "sky130",
            "instances": [{"id": "dp", "block": "sky130.diff_pair",
                           "params": {}}],
            "testbenches": tbs,
        }
        return Design.model_validate(payload)

    def test_stress_key_whitelist(self):
        with pytest.raises(Exception, match="allowed"):
            self._design([{"name": "tb", "stress": {"humidity": 50.0}}])
        with pytest.raises(Exception, match="empty"):
            self._design([{"name": "tb", "stress": {}}])
        ok = self._design([{"name": "tb", "stress": {"vdd": 2.0}}])
        assert ok.testbenches[0].stress == {"vdd": 2.0}

    def test_spec_on_literal_and_defaults(self):
        from layout_canvas.ir.model import Spec

        with pytest.raises(Exception, match="nominal"):
            Spec.model_validate({"name": "s", "signal": "x",
                                 "min": 0, "on": "corner"})
        ok = Spec.model_validate({"name": "s", "signal": "x", "min": 0})
        assert ok.on == "nominal"
        d = self._design([{"name": "tb"}])
        assert d.testbenches[0].temp is None
        assert d.testbenches[0].stress is None

    def test_drift_measure_math(self):
        from layout_canvas.ir.model import Spec

        nom = {"status": "passed", "waves": {"outp": [0.61]},
               "sweep": [0.0]}
        st = {"status": "passed", "waves": {"outp": [0.75]},
              "sweep": [0.0]}
        spec = Spec.model_validate({"name": "d", "signal": "outp",
                                    "measure": "final", "on": "drift",
                                    "max": 0.2, "unit": "V"})
        e = evaluate_specs(nom, [spec], stress=st)[0]
        assert e["status"] == "pass"
        assert e["value"] == pytest.approx(0.14)
        assert e["nominal_value"] == 0.61 and e["stress_value"] == 0.75
        # symmetric: |Δ| does not depend on which dict is "the" run
        e2 = evaluate_specs(st, [spec], stress=nom)[0]
        assert e2["value"] == pytest.approx(0.14)
        tight = Spec.model_validate({"name": "d", "signal": "outp",
                                     "measure": "final", "on": "drift",
                                     "max": 0.1})
        e3 = evaluate_specs(nom, [tight], stress=st)[0]
        assert e3["status"] == "fail" and "max" in e3["reason"]

    def test_drift_and_stress_unavailable_without_corner(self):
        from layout_canvas.ir.model import Spec

        res = {"status": "passed", "waves": {"outp": [0.6]}}
        specs = [
            Spec.model_validate({"name": "s", "signal": "outp",
                                 "min": 0, "on": "stress"}),
            Spec.model_validate({"name": "d", "signal": "outp",
                                 "max": 1, "on": "drift"}),
        ]
        out = evaluate_specs(res, specs)
        assert [e["status"] for e in out] == ["unavailable", "unavailable"]
        assert "Testbench.stress" in out[0]["reason"]
        assert "drift" in out[1]["reason"]

    def test_drift_unavailable_when_a_run_failed(self):
        from layout_canvas.ir.model import Spec

        nom = {"status": "passed", "waves": {"outp": [0.6]}}
        st = {"status": "failed", "errors": ["analysis failed"]}
        spec = Spec.model_validate({"name": "d", "signal": "outp",
                                    "on": "drift", "max": 1})
        e = evaluate_specs(nom, [spec], stress=st)[0]
        assert e["status"] == "unavailable"
        assert "stress" in e["reason"]

    def test_run_testbench_double_run_and_routing(self, monkeypatch,
                                                  tmp_path):
        import layout_canvas.tools.sim as sim

        calls = []

        def fake(design, *, analysis="op", vdd=1.8, temp=None, stimulus=None,
                 probes=None, simulator="auto", executable=None, timeout=120,
                 workdir=None, device_stress=None):
            calls.append({"vdd": vdd, "temp": temp,
                          "device_stress": device_stress,
                          "workdir": Path(workdir).name if workdir else None})
            v = 0.61 if vdd <= 1.8 else 0.75
            return {"status": "passed",
                    "waves": {"outp": [v], "outn": [0.6]},
                    "sweep": [0.0], "ports": [],
                    "log_path": None, "deck_path": None}

        monkeypatch.setattr(sim, "simulate_auto", fake)
        d = self._design([{
            "name": "tb", "source": "schematic", "analysis": "op",
            "vdd": 1.8, "stress": {"vdd": 1.98, "temp": 125.0},
            "specs": [
                {"name": "n_outp", "signal": "outp", "min": 0.5,
                 "on": "nominal"},
                {"name": "s_outp", "signal": "outp", "min": 0.4,
                 "max": 0.9, "on": "stress"},
                {"name": "d_outp", "signal": "outp", "max": 0.2,
                 "on": "drift"},
            ]}])
        r = run_testbench(d, "tb", workdir=tmp_path)
        assert len(calls) == 2
        assert calls[0]["vdd"] == 1.8 and calls[0]["temp"] is None
        assert calls[1]["vdd"] == 1.98 and calls[1]["temp"] == 125.0
        # separate subdirs so deck.cir/waves.dat never clobber each other
        assert calls[0]["workdir"] == "nominal"
        assert calls[1]["workdir"] == "stress"
        # no device-stress keys declared → both legs run unstressed
        assert calls[0]["device_stress"] is None
        assert calls[1]["device_stress"] is None
        assert r["stress"]["status"] == "passed"
        assert r["stress"]["corner"] == {"vdd": 1.98, "temp": 125.0}
        names = {e["name"]: e for e in r["specs"]}
        assert names["n_outp"]["value"] == 0.61   # nominal run
        assert names["s_outp"]["value"] == 0.75   # stress run
        assert names["d_outp"]["value"] == pytest.approx(0.14)
        assert r["spec_status"] == "pass"
        # the stress sub-table carries only the corner-routed specs
        assert {e["name"] for e in r["stress"]["specs"]} == {
            "s_outp", "d_outp"}
        assert r["stress"]["spec_status"] == "pass"

    def test_device_stress_keys_route_to_stress_leg_only(
            self, monkeypatch):
        """dvth_*/mu0_scale become device_stress on the stress run; the
        nominal run and the reported corner stay auditable."""
        import layout_canvas.tools.sim as sim

        calls = []

        def fake(design, **kw):
            calls.append(kw)
            return {"status": "passed", "waves": {"outp": [0.6]},
                    "sweep": [0.0]}

        monkeypatch.setattr(sim, "simulate_auto", fake)
        d = self._design([{
            "name": "tb", "vdd": 1.8,
            "stress": {"vdd": 1.98, "temp": 125.0,
                       "dvth_nmos": 0.03, "mu0_scale": 0.85}}])
        r = run_testbench(d, "tb")
        assert calls[0]["device_stress"] is None      # nominal: never
        assert calls[1]["device_stress"] == {          # stress leg only
            "dvth_nmos": 0.03, "mu0_scale": 0.85}
        # device keys recorded on the reported corner for audit
        assert r["stress"]["corner"] == {
            "vdd": 1.98, "temp": 125.0,
            "dvth_nmos": 0.03, "mu0_scale": 0.85}

    def test_stress_falls_back_per_key(self, monkeypatch):
        import layout_canvas.tools.sim as sim

        calls = []

        def fake(design, *, analysis="op", vdd=1.8, temp=None, **kw):
            calls.append((vdd, temp))
            return {"status": "passed", "waves": {"outp": [0.6]},
                    "sweep": [0.0]}

        monkeypatch.setattr(sim, "simulate_auto", fake)
        d = self._design([{"name": "tb", "vdd": 1.7, "temp": 40.0,
                           "stress": {"vdd": 2.0}}])
        run_testbench(d, "tb")
        # stress.vdd overrides; temp falls back to the nominal tb.temp
        assert calls == [(1.7, 40.0), (2.0, 40.0)]

    def test_no_stress_means_single_run(self, monkeypatch):
        import layout_canvas.tools.sim as sim

        calls = []

        def fake(design, **kw):
            calls.append(kw)
            return {"status": "passed", "waves": {"outp": [0.6]},
                    "sweep": [0.0]}

        monkeypatch.setattr(sim, "simulate_auto", fake)
        d = self._design([{"name": "tb", "specs": [
            {"name": "s", "signal": "outp", "min": 0, "on": "stress"}]}])
        r = run_testbench(d, "tb")
        assert len(calls) == 1
        assert "stress" not in r
        assert r["specs"][0]["status"] == "unavailable"
        assert r["spec_status"] == "unavailable"


@pytest.mark.skipif(_NGSPICE is None, reason="ngspice not installed")
def test_ota_stress_corner_real_run(tmp_path):
    """Real double run: nominal vs 1.98 V / 125 °C on the OTA op point.

    Measured on ngspice_con-47 against the bundled sky130 TT models:
    nominal outp_op 0.6098 V, stressed outp_op 0.4658 V, drift 0.144 V.
    """
    starter = (ROOT / "bench" / "tasks" / "sky130-ota-stress-corner"
               / "starter" / "design.json")
    d = Design.from_json(starter.read_text(encoding="utf-8"))
    r = run_testbench(d, "tb_op_stress", executable=_NGSPICE,
                      workdir=tmp_path)
    assert r["status"] == "passed", r.get("errors")
    assert r["stress"]["status"] == "passed", r["stress"].get("errors")
    specs = {s["name"]: s for s in r["specs"]}
    assert specs["outp_stress"]["status"] == "pass"
    assert specs["outp_stress"]["value"] == pytest.approx(0.4658, abs=0.02)
    assert specs["outp_drift"]["status"] == "pass"
    assert specs["outp_drift"]["nominal_value"] == pytest.approx(
        0.6098, abs=0.02)
    assert specs["outp_drift"]["stress_value"] == pytest.approx(
        0.4658, abs=0.02)
    assert specs["outp_drift"]["value"] == pytest.approx(0.144, abs=0.02)
    # the stress deck really carried a .temp card; nominal did not
    stress_deck = Path(r["stress"]["deck_path"]).read_text(
        encoding="utf-8", errors="replace")
    nom_deck = Path(r["deck_path"]).read_text(
        encoding="utf-8", errors="replace")
    assert ".temp 125" in stress_deck
    assert ".temp" not in nom_deck
    # split workdirs — both decks kept on disk
    assert (tmp_path / "nominal" / "deck.cir").is_file()
    assert (tmp_path / "stress" / "deck.cir").is_file()


class TestAcAnalysis:
    """ac analysis: stimulus shape, complex wrdata parsing, spec measures."""

    def test_ac_stimulus_deck(self):
        from layout_canvas.tools.sim import default_stimulus

        d = Design.from_json(LAB.read_text())
        stim = default_stimulus(d, analysis="ac")
        assert "ac dec 10 1k 1G" in stim
        # the first input-direction port carries the ac 1 source
        assert "V_inp inp 0 0.9 ac 1" in stim
        assert "V_inn inn 0 0.9" in stim
        assert " ac 1" not in stim.split("V_inn")[1].splitlines()[0]

    def test_dc_stimulus_deck(self):
        from layout_canvas.tools.sim import default_stimulus

        stim = default_stimulus(
            Design.from_json(LAB.read_text()), analysis="dc")
        assert "dc V_inp 0 1.8" in stim

    def test_extracted_auto_ac_stimulus(self):
        """The auto-bias PEX path attaches ac to a surviving input port."""
        from layout_canvas.tools import sim

        lines = []
        for p in ("inp", "outp"):
            lines.append(sim._bias_line(p, 1.8))
        d = Design.from_json(LAB.read_text())
        drive = sim._drive_port(d, ["outp", "inp"])
        assert drive == "inp"
        lines, src = sim._apply_drive(lines, drive, 1.8, "ac")
        assert src == "V_inp"
        assert any(ln.endswith("ac 1") for ln in lines)


@pytest.mark.skipif(_NGSPICE is None, reason="ngspice not installed")
def test_ota_lab_real_run():
    d = Design.from_json(LAB.read_text())
    runs = run_all(d, executable=_NGSPICE)
    assert len(runs) == 3
    by_name = {r["testbench"]: r for r in runs}
    for name in ("tb_op_schematic", "tb_op_pex"):
        r = by_name[name]
        assert r["status"] == "passed", r.get("errors")
        assert r["spec_status"] == "pass"
        assert all(s["status"] == "pass" for s in r["specs"])
    # ac bench on the real (powered) 5-T OTA: gain_db is a genuine positive
    # pass and bw_3db finds a real -3dB crossing around 0.5 GHz.
    ac = by_name["tb_ac"]
    assert ac["status"] == "passed", ac.get("errors")
    assert ac["ac_source"] == "inp"
    assert len(ac["waves"]["outp"]) > 0
    specs = {s["name"]: s for s in ac["specs"]}
    assert specs["gain_db"]["status"] == "pass"
    assert specs["gain_db"]["value"] == pytest.approx(14.03, abs=0.5)
    assert specs["bw_3db"]["status"] == "pass"
    assert specs["bw_3db"]["value"] == pytest.approx(5.0e8, rel=0.2)


@pytest.mark.skipif(_NGSPICE is None, reason="ngspice not installed")
def test_ota_lab_ac_run_direct(tmp_path):
    """simulate_auto analysis='ac' gives magnitude + __db waves."""
    from layout_canvas.tools.sim import simulate_auto

    d = Design.from_json(LAB.read_text())
    r = simulate_auto(d, analysis="ac", vdd=1.8, executable=_NGSPICE,
                      workdir=tmp_path)
    assert r["status"] == "passed"
    assert r["ac_source"] == "inp"
    assert len(r["sweep"]) == 61
    # |v(inp)| = 1 across the band: the driven source itself
    assert all(abs(v - 1.0) < 1e-9 for v in r["waves"]["inp"])
    assert len(r["waves"]["outp__db"]) == len(r["sweep"])


@pytest.mark.skipif(_NGSPICE is None, reason="ngspice not installed")
def test_diffamp_tran_end_to_end(tmp_path):
    """Real extracted-tran run exercising settling/slew/overshoot.

    diffamp.json's tb_tran steps inp 0.9→1.3 V over 10 ns (PWL) on the
    extracted netlist; the specs below were measured against
    ngspice_con-47 (inp settle ~60 ns into the ±2 % band, slew 4e7 V/s
    from the 0.4 V/10 ns edge, 0 % overshoot on a monotone ramp).
    """
    d = Design.from_json(
        (ROOT / "examples" / "diffamp.json").read_text(encoding="utf-8"))
    r = run_testbench(d, "tb_tran", executable=_NGSPICE, workdir=tmp_path)
    assert r["status"] == "passed", r.get("errors")
    assert r["spec_status"] == "pass"
    specs = {s["name"]: s for s in r["specs"]}
    assert specs["inp_settle"]["status"] == "pass"
    assert 0 < specs["inp_settle"]["value"] < 1e-7  # ~6e-8 s, real time
    assert specs["inp_slew"]["value"] == pytest.approx(4e7, rel=0.2)
    assert specs["inp_overshoot"]["value"] == pytest.approx(0.0)
    assert specs["outp_final"]["status"] == "pass"


@pytest.mark.skipif(_NGSPICE is None, reason="ngspice not installed")
def test_out_of_bounds_spec_fails(tmp_path):
    """A deliberately tight bound must report fail with a real reason."""
    import json

    payload = json.loads(LAB.read_text())
    payload["testbenches"] = [{
        "name": "tb_tight",
        "source": "schematic",
        "analysis": "op",
        "vdd": 1.8,
        "specs": [{"name": "too_tight", "signal": "outp",
                   "measure": "final", "min": 1.9, "max": 2.0, "unit": "V"}],
    }]
    d = Design.model_validate(payload)
    r = run_testbench(d, "tb_tight", executable=_NGSPICE, workdir=tmp_path)
    assert r["status"] == "passed"
    assert r["spec_status"] == "fail"
    spec = r["specs"][0]
    assert spec["status"] == "fail"
    assert "< min" in spec["reason"]
