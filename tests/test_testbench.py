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


@pytest.mark.skipif(_NGSPICE is None, reason="ngspice not installed")
def test_ota_lab_real_run():
    d = Design.from_json(LAB.read_text())
    runs = run_all(d, executable=_NGSPICE)
    assert len(runs) == 2
    for r in runs:
        assert r["status"] == "passed", r.get("errors")
        assert r["spec_status"] == "pass"
        assert all(s["status"] == "pass" for s in r["specs"])


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
