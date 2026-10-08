"""Structured simulation testbenches: named setup + spec checking.

A ``Testbench`` on a Design is a declarative sim recipe (schematic or
extracted source, op/tran, vdd, optional stimulus/probes) plus ``Spec``
records — each spec measures one waveform key and reports a real
pass/fail/unavailable verdict with a reason. Fail-closed throughout: a
missing simulator or a missing signal reports ``unavailable``, never a
fabricated pass.
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
        for x, f in zip(xs, sweep):
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


def evaluate_specs(
    result: dict[str, Any],
    specs: list[Spec],
    *,
    analysis: str | None = None,
) -> list[dict[str, Any]]:
    """Score each spec against a sim-result dict (``simulate_auto`` shape).

    ``analysis`` is the testbench's analysis kind — transient-only
    measures (``settling``/``slew``/``overshoot``) on anything else report
    ``unavailable`` with a plain reason instead of a bare axis error.
    """
    status = str(result.get("status", ""))
    waves = result.get("waves") or {}
    out = []
    for spec in specs:
        entry = {
            "name": spec.name,
            "signal": spec.signal,
            "measure": spec.measure,
            "unit": spec.unit,
            "min": spec.min,
            "max": spec.max,
            "value": None,
            "status": "unavailable",
            "reason": "",
        }
        if status not in ("ok", "passed"):
            errors = result.get("errors") or []
            entry["reason"] = (
                f"simulation {status or 'unknown'}"
                + (": " + "; ".join(str(e) for e in errors) if errors else "")
            )
            out.append(entry)
            continue
        if (spec.measure in _TRANSIENT_MEASURES
                and analysis is not None and analysis != "tran"):
            entry["reason"] = (
                f"measure {spec.measure!r} is transient-only; testbench "
                f"analysis is {analysis!r}")
            out.append(entry)
            continue
        samples = waves.get(spec.signal)
        if not samples:
            available = ", ".join(sorted(waves)) or "none"
            entry["reason"] = (
                f"signal {spec.signal!r} not in waveforms; available: {available}"
            )
            out.append(entry)
            continue
        try:
            # 6 significant digits, not 6 decimals — transient measures
            # produce nanosecond / V-per-second magnitudes that a fixed
            # round(…, 6) would silently collapse to 0.
            measured = measure_signal(
                list(samples), spec.measure,
                sweep=result.get("sweep"), tol=spec.tol)
            value = float(f"{measured:.6g}")
        except ValueError as exc:
            entry["reason"] = str(exc)
            out.append(entry)
            continue
        entry["value"] = value
        if spec.min is not None and value < spec.min:
            entry["status"] = "fail"
            entry["reason"] = f"{_fmt(value, spec.unit)} < min {_fmt(spec.min, spec.unit)}"
        elif spec.max is not None and value > spec.max:
            entry["status"] = "fail"
            entry["reason"] = f"{_fmt(value, spec.unit)} > max {_fmt(spec.max, spec.unit)}"
        else:
            entry["status"] = "pass"
        out.append(entry)
    return out


def _spec_status(results: list[dict[str, Any]]) -> str:
    if not results:
        return "no_specs"
    statuses = {r["status"] for r in results}
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
) -> dict[str, Any]:
    """Run one named testbench and evaluate its specs."""
    from layout_canvas.tools.sim import simulate_auto, simulate_extracted

    tb = _testbench(design, name)
    exe = executable or os.environ.get("LAYOUT_CANVAS_NGSPICE")
    if tb.source == "extracted":
        result = simulate_extracted(
            design,
            analysis=tb.analysis,
            vdd=tb.vdd,
            stimulus=tb.stimulus,
            probes=tb.probes or None,
            executable=exe,
            workdir=workdir,
        )
    else:
        result = simulate_auto(
            design,
            analysis=tb.analysis,
            vdd=tb.vdd,
            executable=exe,
            workdir=workdir,
        )
    for key in ("log_path", "deck_path"):
        if result.get(key):
            result[key] = str(result[key])
    specs = evaluate_specs(result, tb.specs, analysis=tb.analysis)
    result["testbench"] = tb.name
    result["specs"] = specs
    result["spec_status"] = _spec_status(specs)
    return result


def run_all(design: Design, **kwargs: Any) -> list[dict[str, Any]]:
    """Run every testbench on the design, in declaration order."""
    return [run_testbench(design, tb.name, **kwargs) for tb in design.testbenches]
