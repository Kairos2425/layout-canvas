"""Structured simulation testbenches: named setup + spec checking.

A ``Testbench`` on a Design is a declarative sim recipe (schematic or
extracted source, op/tran, vdd, optional stimulus/probes) plus ``Spec``
records — each spec measures one waveform key and reports a real
pass/fail/unavailable verdict with a reason. A testbench may declare a
``stress`` corner (accelerated vdd/temp — an HTOL-style proxy, not an
aging model); it then runs nominal + stress, and a spec's ``on`` field
routes it to the nominal run, the stress run, or the |stress − nominal|
drift between them. Fail-closed throughout: a missing simulator, a
missing signal or a missing declared corner reports ``unavailable``,
never a fabricated pass.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from layout_canvas.ir.model import Design, Spec, Testbench

_MEASURES = ("final", "min", "max", "mean", "pp", "db", "bw_3db",
             "settling", "slew", "overshoot")

# Measures that only make sense on a transient waveform — they reduce the
# sample series along the sweep axis, so an ac/op result (no time axis or a
# frequency axis) reports ``unavailable``, never a number from the wrong axis.
_TRANSIENT_MEASURES = ("settling", "slew", "overshoot")


def _require_sweep(measure: str, sweep: list[float] | None, n: int) -> list[float]:
    if sweep is None or len(sweep) != n:
        raise ValueError(
            f"{measure} needs a sweep axis matching the waveform "
            "(transient-only measure)")
    return [float(t) for t in sweep]


def measure_signal(
    samples: list[float],
    measure: str,
    sweep: list[float] | None = None,
    tol: float | None = None,
) -> float:
    """Reduce a waveform to a scalar per ``measure``.

    ``db`` is ``20*log10`` of the first sample — the low-frequency
    magnitude of an ac sweep. ``bw_3db`` is the sweep frequency where the
    magnitude first falls below ``|v[0]|/sqrt(2)`` (needs ``sweep``).
    Transient measures (need ``sweep`` as the time axis):
    ``settling`` — the earliest time after which the waveform stays inside
    ±``tol``·|final| (``tol`` defaults to 0.02; ``final`` = last sample);
    ``slew`` — max |dv/dt| over adjacent samples; ``overshoot`` —
    ``max(0, (peak-final)/|final|*100)`` in percent.
    """
    import math

    if not samples:
        raise ValueError("empty waveform")
    xs = [float(v) for v in samples]
    if measure == "final":
        return xs[-1]
    if measure == "min":
        return min(xs)
    if measure == "max":
        return max(xs)
    if measure == "mean":
        return sum(xs) / len(xs)
    if measure == "pp":
        return max(xs) - min(xs)
    if measure == "db":
        if xs[0] <= 0:
            raise ValueError(f"db needs a positive first sample, got {xs[0]:g}")
        return 20.0 * math.log10(xs[0])
    if measure == "bw_3db":
        if sweep is None or len(sweep) != len(xs):
            raise ValueError("bw_3db needs a sweep axis matching the waveform")
        threshold = xs[0] / math.sqrt(2.0)
        for x, f in zip(xs, sweep, strict=False):
            if x < threshold:
                return float(f)
        raise ValueError(
            f"magnitude never falls below |v0|/sqrt(2)={threshold:g} "
            f"(last={xs[-1]:g})")
    if measure == "settling":
        ts = _require_sweep(measure, sweep, len(xs))
        frac = tol if tol is not None else 0.02
        final = xs[-1]
        band = frac * abs(final) or frac  # final==0 → absolute ±frac band
        idx = None
        for i in range(len(xs) - 1, -1, -1):
            if abs(xs[i] - final) > band:
                idx = i + 1
                break
        if idx is None:
            return float(ts[0])
        if idx >= len(xs) - 1:
            # Entering the band only at the last sample cannot demonstrate
            # it *stays* there — report never-settled instead of returning
            # the sweep endpoint as if it were a measured settle time.
            raise ValueError(
                f"settling: waveform never stays within ±{frac:g} of "
                f"final={final:g} before the sweep ends "
                f"(last out-of-band excursion at t={ts[idx - 1]:g})")
        return float(ts[idx])
    if measure == "slew":
        ts = _require_sweep(measure, sweep, len(xs))
        best = 0.0
        for i in range(1, len(xs)):
            dt = ts[i] - ts[i - 1]
            if dt <= 0:
                raise ValueError(
                    "slew needs a strictly increasing sweep axis "
                    f"(t[{i - 1}]={ts[i - 1]:g} >= t[{i}]={ts[i]:g})")
            best = max(best, abs(xs[i] - xs[i - 1]) / dt)
        return best
    if measure == "overshoot":
        _require_sweep(measure, sweep, len(xs))
        final = xs[-1]
        if final == 0:
            raise ValueError("overshoot needs a nonzero final value")
        return max(0.0, (max(xs) - final) / abs(final) * 100.0)
    raise ValueError(f"unknown measure {measure!r}; known: {_MEASURES}")


def _fmt(value: float | None, unit: str) -> str:
    return f"{value:g} {unit}".rstrip()


def _measure_one(
    result: dict[str, Any],
    spec: Spec,
    *,
    analysis: str | None = None,
) -> tuple[float | None, str | None]:
    """Reduce ``spec.signal`` in one sim-result dict to a scalar.

    Returns ``(value, None)`` on success or ``(None, reason)`` — the
    reason only explains why no scalar exists; it never asserts a verdict.
    ``analysis`` is the testbench's analysis kind: transient-only measures
    (``settling``/``slew``/``overshoot``) on anything else fail with a
    plain reason instead of a bare axis error.
    """
    status = str(result.get("status", ""))
    if status not in ("ok", "passed"):
        errors = result.get("errors") or []
        return None, (
            f"simulation {status or 'unknown'}"
            + (": " + "; ".join(str(e) for e in errors) if errors else "")
        )
    if (spec.measure in _TRANSIENT_MEASURES
            and analysis is not None and analysis != "tran"):
        return None, (
            f"measure {spec.measure!r} is transient-only; testbench "
            f"analysis is {analysis!r}")
    samples = (result.get("waves") or {}).get(spec.signal)
    if not samples:
        available = ", ".join(sorted(result.get("waves") or {})) or "none"
        return None, (
            f"signal {spec.signal!r} not in waveforms; available: {available}")
    try:
        # 6 significant digits, not 6 decimals — transient measures
        # produce nanosecond / V-per-second magnitudes that a fixed
        # round(…, 6) would silently collapse to 0.
        measured = measure_signal(
            list(samples), spec.measure,
            sweep=result.get("sweep"), tol=spec.tol)
        return float(f"{measured:.6g}"), None
    except ValueError as exc:
        return None, str(exc)


def _score_value(entry: dict[str, Any], spec: Spec, value: float) -> None:
    """Apply the spec's min/max bounds to a reduced scalar."""
    entry["value"] = value
    if spec.min is not None and value < spec.min:
        entry["status"] = "fail"
        entry["reason"] = f"{_fmt(value, spec.unit)} < min {_fmt(spec.min, spec.unit)}"
    elif spec.max is not None and value > spec.max:
        entry["status"] = "fail"
        entry["reason"] = f"{_fmt(value, spec.unit)} > max {_fmt(spec.max, spec.unit)}"
    else:
        entry["status"] = "pass"


_NO_CORNER_REASON = (
    "needs a declared stress corner (Testbench.stress) — "
    "the testbench declared none")


def _run_entry_status(result: dict[str, Any]) -> str:
    """Spec-entry status when a run dict cannot produce a measurement:
    ``error`` for infrastructure-level failures (runner crash, a digital
    stimulus that never built), ``unavailable`` otherwise."""
    return "error" if str(result.get("status", "")) == "error" \
        else "unavailable"


def evaluate_specs(
    result: dict[str, Any],
    specs: list[Spec],
    *,
    analysis: str | None = None,
    stress: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Score each spec against a sim-result dict (``simulate_auto`` shape).

    ``stress`` is the optional second sim-result dict from the same
    testbench run at its declared stress corner (same analysis/probes,
    stress vdd/temp). Spec routing by ``spec.on``:

    - ``"nominal"`` (default) reduces the signal on ``result``;
    - ``"stress"`` reduces the same signal/measure on ``stress``;
    - ``"drift"`` reduces it on both runs and bounds the magnitude
      ``|stress − nominal|`` in ``value``, with the two scalars exposed as
      ``nominal_value``/``stress_value``.

    ``stress=None`` means no corner was declared or run — ``stress`` and
    ``drift`` specs report ``unavailable`` with the reason, never a
    nominal-only stand-in. A spec on a run whose simulation itself was
    not ``ok``/``passed`` reports ``unavailable`` too — fail-closed.
    """
    out = []
    for spec in specs:
        entry = {
            "name": spec.name,
            "signal": spec.signal,
            "measure": spec.measure,
            "unit": spec.unit,
            "min": spec.min,
            "max": spec.max,
            "on": spec.on,
            "value": None,
            "status": "unavailable",
            "reason": "",
        }
        if spec.on == "stress":
            if stress is None:
                entry["reason"] = f"spec on='stress' {_NO_CORNER_REASON}"
                out.append(entry)
                continue
            value, reason = _measure_one(stress, spec, analysis=analysis)
            if reason is not None:
                entry["status"] = _run_entry_status(stress)
                entry["reason"] = f"stress run: {reason}"
                out.append(entry)
                continue
            _score_value(entry, spec, value)
        elif spec.on == "drift":
            if stress is None:
                entry["reason"] = (
                    f"spec on='drift' bounds |stress − nominal| and "
                    f"{_NO_CORNER_REASON}")
                out.append(entry)
                continue
            n_val, n_reason = _measure_one(result, spec, analysis=analysis)
            s_val, s_reason = _measure_one(stress, spec, analysis=analysis)
            if n_reason is not None or s_reason is not None:
                entry["status"] = (
                    "error" if "error" in (
                        _run_entry_status(result),
                        _run_entry_status(stress))
                    else "unavailable")
                entry["reason"] = "; ".join(
                    f"{label}: {r}" for label, r in
                    (("nominal", n_reason), ("stress", s_reason)) if r)
                out.append(entry)
                continue
            entry["nominal_value"] = n_val
            entry["stress_value"] = s_val
            _score_value(entry, spec, float(f"{abs(s_val - n_val):.6g}"))
        else:
            value, reason = _measure_one(result, spec, analysis=analysis)
            if reason is not None:
                entry["status"] = _run_entry_status(result)
                entry["reason"] = reason
                out.append(entry)
                continue
            _score_value(entry, spec, value)
        out.append(entry)
    return out


def _spec_status(results: list[dict[str, Any]]) -> str:
    if not results:
        return "no_specs"
    statuses = {r["status"] for r in results}
    if "error" in statuses:
        return "error"
    if "fail" in statuses:
        return "fail"
    if "unavailable" in statuses:
        return "unavailable"
    return "pass"


def _testbench(design: Design, name: str) -> Testbench:
    for tb in design.testbenches:
        if tb.name == name:
            return tb
    raise KeyError(
        f"unknown testbench {name!r}; known: {sorted(t.name for t in design.testbenches)}"
    )


def run_testbench(
    design: Design,
    name: str,
    *,
    executable: str | None = None,
    workdir: str | Path | None = None,
    iverilog: str | None = None,
) -> dict[str, Any]:
    """Run one named testbench and evaluate its specs.

    A testbench with a non-empty ``stress`` table runs the same
    analysis/probes twice: nominal (``tb.vdd``/``tb.temp``) and the
    accelerated stress corner (``stress["vdd"]``/``stress["temp"]``, each
    falling back to the nominal value). The device-proxy keys
    ``dvth_nmos``/``dvth_pmos``/``mu0_scale`` additionally inject BSIM4
    ``delvto``/``mulu0`` onto the stress leg's MOS cards (a parametric
    aging *proxy*, not a foundry aging model); if the PDK's wrappers
    cannot forward them the stress run reports ``unavailable`` and every
    ``stress``/``drift`` spec follows it. The stress run lands under
    ``result["stress"]`` with the same waves/status/spec_status shape plus
    the applied ``corner``; its ``specs`` list covers only the
    ``on="stress"``/``"drift"`` entries. The top-level ``specs`` list
    carries every spec evaluated on its routed run and ``spec_status``
    rolls up both corners. No simulator → every entry ``unavailable``.

    A testbench carrying ``digital`` (mixed-signal stimulus — see
    ``tools/mixed_signal.py``) applies to ``analysis="tran"`` only: on
    ``op``/``ac``/``dc`` every spec reports ``unavailable`` ("digital
    stimulus only applies to tran"). On tran the Verilog runs first —
    once, shared by nominal and stress legs — and its PWL sources replace
    the auto-bias on their nodes inside ``simulate_auto``/
    ``simulate_extracted``. Fail-closed: no iverilog → run ``unavailable``
    and every spec ``unavailable``; a compile/vvp failure or a listed
    net that never reached the dump → run ``error`` and every spec
    ``error`` with the tool's own message. ``iverilog`` overrides the
    runner the way ``executable`` overrides ngspice (else
    ``LAYOUT_CANVAS_IVERILOG``/PATH/WSL via ``tools/verilog.py``).
    """
    from layout_canvas.tools.sim import (
        DEVICE_STRESS_KEYS,
        simulate_auto,
        simulate_extracted,
    )

    tb = _testbench(design, name)
    exe = executable or os.environ.get("LAYOUT_CANVAS_NGSPICE")

    digital_lines: dict[str, str] | None = None
    digital_info: dict[str, Any] | None = None
    if tb.digital is not None:
        if tb.analysis != "tran":
            result = {
                "status": "unavailable",
                "errors": [
                    f"testbench {tb.name}: digital stimulus only applies "
                    f"to tran (analysis={tb.analysis!r})"],
                "testbench": tb.name,
                "log_path": None, "deck_path": None,
                "waves": {}, "sweep": []}
            specs = evaluate_specs(result, tb.specs, analysis=tb.analysis)
            result["specs"] = specs
            result["spec_status"] = _spec_status(specs)
            return result
        from layout_canvas.tools.mixed_signal import digital_stimulus
        dig_dir = Path(workdir) / "digital" if workdir else None
        dig = digital_stimulus(tb, executable=iverilog, workdir=dig_dir)
        if dig["status"] != "ok":
            result = {
                "status": dig["status"],  # "unavailable" | "error"
                "errors": [dig["reason"]],
                "testbench": tb.name,
                "log_path": None, "deck_path": None,
                "waves": {}, "sweep": [],
                "digital": dig}
            specs = evaluate_specs(result, tb.specs, analysis=tb.analysis)
            result["specs"] = specs
            result["spec_status"] = _spec_status(specs)
            return result
        digital_lines = dig["lines"]
        digital_info = dig

    # Only keyword the digital lines when a digital bench actually ran —
    # the plain call shape is unchanged for every other testbench.
    _dig_kw = {"digital_sources": digital_lines} if digital_lines else {}

    def _run(vdd: float, temp: float | None, wdir: Any,
             device_stress: dict[str, float] | None = None) -> dict[str, Any]:
        if tb.source == "extracted":
            return simulate_extracted(
                design,
                analysis=tb.analysis,
                vdd=vdd,
                temp=temp,
                stimulus=tb.stimulus,
                probes=tb.probes or None,
                executable=exe,
                workdir=wdir,
                device_stress=device_stress,
                **_dig_kw,
            )
        return simulate_auto(
            design,
            analysis=tb.analysis,
            vdd=vdd,
            temp=temp,
            executable=exe,
            workdir=wdir,
            device_stress=device_stress,
            **_dig_kw,
        )

    stressed = bool(tb.stress)
    # Two runs sharing one workdir would clobber each other's
    # deck.cir/waves.dat — split them into named subdirectories.
    nom_dir = Path(workdir) / "nominal" if stressed and workdir else workdir
    str_dir = Path(workdir) / "stress" if stressed and workdir else None
    result = _run(tb.vdd, tb.temp, nom_dir)
    stress_res: dict[str, Any] | None = None
    corner: dict[str, float | None] | None = None
    if stressed:
        assert tb.stress is not None
        corner = {
            "vdd": float(tb.stress.get("vdd", tb.vdd)),
            "temp": tb.stress.get("temp", tb.temp),
        }
        if corner["temp"] is not None:
            corner["temp"] = float(corner["temp"])
        # The device-level proxy keys (dvth_*/mu0_scale) ride the stress
        # leg only — a nominal run is never device-stressed, so drift =
        # |stressed − nominal| stays meaningful. They are recorded on the
        # reported corner for auditability.
        device_stress = {
            k: float(tb.stress[k]) for k in DEVICE_STRESS_KEYS
            if k in tb.stress}
        corner.update(device_stress)
        stress_res = _run(corner["vdd"], corner["temp"], str_dir,
                          device_stress=device_stress or None)
    specs = evaluate_specs(result, tb.specs, analysis=tb.analysis,
                           stress=stress_res)
    for key in ("log_path", "deck_path"):
        if result.get(key):
            result[key] = str(result[key])
    result["testbench"] = tb.name
    if digital_info is not None:
        # audit trail: the emitted PWL lines + parsed transition series
        result["digital"] = digital_info
    result["specs"] = specs
    result["spec_status"] = _spec_status(specs)
    if stress_res is not None:
        for key in ("log_path", "deck_path"):
            if stress_res.get(key):
                stress_res[key] = str(stress_res[key])
        corner_specs = [e for e in specs if e["on"] in ("stress", "drift")]
        stress_res["corner"] = corner
        stress_res["testbench"] = tb.name
        stress_res["specs"] = corner_specs
        stress_res["spec_status"] = _spec_status(corner_specs)
        result["stress"] = stress_res
    return result


def run_all(design: Design, **kwargs: Any) -> list[dict[str, Any]]:
    """Run every testbench on the design, in declaration order."""
    return [run_testbench(design, tb.name, **kwargs) for tb in design.testbenches]
