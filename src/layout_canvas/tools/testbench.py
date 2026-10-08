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

_MEASURES = ("final", "min", "max", "mean", "pp")


def measure_signal(samples: list[float], measure: str) -> float:
    """Reduce a waveform to a scalar per ``measure``."""
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
    raise ValueError(f"unknown measure {measure!r}; known: {_MEASURES}")


def _fmt(value: float | None, unit: str) -> str:
    return f"{value:g} {unit}".rstrip()


def evaluate_specs(result: dict[str, Any], specs: list[Spec]) -> list[dict[str, Any]]:
    """Score each spec against a sim-result dict (``simulate_auto`` shape)."""
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
        samples = waves.get(spec.signal)
        if not samples:
            available = ", ".join(sorted(waves)) or "none"
            entry["reason"] = (
                f"signal {spec.signal!r} not in waveforms; available: {available}"
            )
            out.append(entry)
            continue
        try:
            value = round(measure_signal(list(samples), spec.measure), 6)
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
    specs = evaluate_specs(result, tb.specs)
    result["testbench"] = tb.name
    result["specs"] = specs
    result["spec_status"] = _spec_status(specs)
    return result


def run_all(design: Design, **kwargs: Any) -> list[dict[str, Any]]:
    """Run every testbench on the design, in declaration order."""
    return [run_testbench(design, tb.name, **kwargs) for tb in design.testbenches]
