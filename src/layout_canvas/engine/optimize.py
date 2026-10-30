"""Session-aware optimizers.

Each tuning iteration is committed through ``DesignSession.transact`` so
every step is revisioned, validated, and undoable — the optimizer is a
client of the same mutation boundary as any other agent, not a backdoor.

Two objectives:

- ``placement`` — the original PPA margin tightening loop.
- ``specs`` — coordinate descent over bounded numeric block params,
  scored by testbench spec pass count (tie-broken on smaller area).
  Fail-closed: no testbenches is an error, and an absent simulator is
  reported as ``unavailable``, never as a pass.
"""

from __future__ import annotations

from typing import Any

from layout_canvas.compiler.compile import compile_design
from layout_canvas.compiler.ppa import extract_ppa
from layout_canvas.engine.envelope import Diagnostic, Envelope
from layout_canvas.engine.session import DesignSession


def optimize_session(
    session: DesignSession,
    target_aspect_ratio: float = 1.0,
    min_clearance: float = 0.5,
    max_iterations: int | None = None,
    objective: str = "placement",
    testbench: str | None = None,
) -> Envelope:
    """Dispatch on ``objective``: ``placement`` (default, legacy margin
    tightening) or ``specs`` (coordinate descent on block params scored by
    testbench spec results). ``testbench`` restricts scoring to one named
    bench; ``None`` scores all of them."""
    if objective == "placement":
        return _optimize_placement(
            session, target_aspect_ratio, min_clearance, max_iterations or 5)
    if objective == "specs":
        return _optimize_specs(session, testbench, max_iterations or 6)
    return Envelope(
        "error",
        session.revision,
        [Diagnostic("error", "bad-objective",
                    f"unknown objective {objective!r}; known: placement, specs")],
    )


def _optimize_placement(
    session: DesignSession,
    target_aspect_ratio: float,
    min_clearance: float,
    max_iterations: int,
) -> Envelope:
    """Iteratively tighten placement margins toward the target aspect ratio.

    Per iteration: compile → measure PPA → commit a transact that bumps
    under-clearance margins. Stops early when the aspect ratio is within
    0.2 of target or a step commits nothing.
    """
    history: list[dict[str, Any]] = []

    for iteration in range(max_iterations):
        comp = compile_design(session.design)
        ppa = extract_ppa(comp, session.design)
        history.append(
            {
                "iteration": iteration,
                "revision": session.revision,
                "area_um2": ppa["area_um2"],
                "aspect_ratio": ppa["aspect_ratio"],
                "hpwl_um": ppa["hpwl_um"],
            }
        )

        aspect_ratio = ppa["aspect_ratio"]
        if aspect_ratio is not None and abs(aspect_ratio - target_aspect_ratio) <= 0.2:
            break

        edits = [
            {
                "op": "set_placement",
                "instance": inst.id,
                "margin": min_clearance,
            }
            for inst in session.design.instances
            if inst.placement.margin < min_clearance
        ]
        if not edits:
            break

        # confirm=True: the optimize call itself is the caller's decision
        # point — per-step margin bumps are its function, not a surprise.
        env = session.transact(edits, confirm=True)
        if not env.ok:
            return Envelope(
                "error",
                session.revision,
                env.diagnostics
                + [
                    Diagnostic(
                        "error",
                        "optimizer-step-failed",
                        f"iteration {iteration} transact was rejected",
                    )
                ],
                {"history": history},
            )

    comp = compile_design(session.design)
    final_ppa = extract_ppa(comp, session.design)
    return Envelope(
        "ok",
        session.revision,
        data={
            "iterations": len(history),
            "history": history,
            "final_ppa": final_ppa,
        },
    )


# --- Spec-driven coordinate descent -------------------------------------


def _tunable_params(session: DesignSession) -> list[dict[str, Any]]:
    """Per-instance numeric params with a declared [min, max] — the descent
    axes. Blocks missing from the registry contribute nothing."""
    from layout_canvas.blocks import base

    out: list[dict[str, Any]] = []
    for inst in session.design.instances:
        try:
            spec = base.get(inst.block).spec
        except KeyError:
            continue
        for p in spec.params:
            if p.type in ("int", "float") and p.min is not None and p.max is not None:
                try:
                    # current value: explicit param, else block default
                    cur = float(inst.params.get(p.name, p.default))
                except (TypeError, ValueError):
                    continue
                out.append(
                    {
                        "instance": inst.id,
                        "name": p.name,
                        "is_int": p.type == "int",
                        "min": float(p.min),
                        "max": float(p.max),
                        "default": p.default,
                        "value": cur,
                    }
                )
    return out


def _spec_margin(entry: dict[str, Any]) -> float:
    """Signed distance into a spec's bound, normalized and clipped to
    ``[-1, 0]``: 0 for a pass, negative for a measured fail, -1 when the
    spec cannot even be measured. This is the descent gradient a bare
    pass/fail count lacks — a candidate that moves a failing spec closer
    to its bound must score above one that moved it further away.
    """
    status = entry.get("status")
    if status == "pass":
        return 0.0
    if status != "fail":
        return -1.0
    value = entry.get("value")
    if value is None:
        return -1.0
    lo, hi = entry.get("min"), entry.get("max")
    scale = max(
        abs(lo) if lo is not None else 0.0,
        abs(hi) if hi is not None else 0.0,
        (hi - lo) if lo is not None and hi is not None else 0.0,
        1e-12,
    )
    if lo is not None and value < lo:
        dist = value - lo
    elif hi is not None and value > hi:
        dist = hi - value
    else:
        return 0.0
    return max(dist / scale, -1.0)


def _score(
    session: DesignSession,
    testbench: str | None,
) -> tuple[tuple[float, float, float, float], str]:
    """Score the design as ((feasible, n_passed, margin_sum, -area), status).

    Lexicographic: a physically broken candidate (compile/DRC/LVS fail)
    ranks below every feasible one; among feasible candidates more passing
    specs win, equal pass counts resolve on summed spec margin (the
    gradient term), and remaining ties go to the smaller layout. ``status``
    is the fail-closed spec rollup — ``unavailable`` when simulation
    cannot run, never faked.
    """
    from layout_canvas.tools.testbench import _spec_status, run_all, run_testbench
    from layout_canvas.tools.verify import verify_design

    design = session.design
    if testbench is not None:
        runs = [run_testbench(design, testbench)]
    else:
        runs = run_all(design)
    entries = [e for r in runs for e in r.get("specs", [])]
    n_passed = sum(1 for e in entries if e.get("status") == "pass")
    status = _spec_status(entries)
    try:
        area = extract_ppa(compile_design(design), design).get("area_um2")
    except Exception:
        area = None
    feasible = 0
    if area is not None:
        # Params that break extraction/DRC/LVS (e.g. a shrunk dimension
        # severing a net) must never outscore a physically valid layout.
        try:
            feasible = 1 if verify_design(design).get("passed") else 0
        except Exception:
            feasible = 0
    margin = sum(_spec_margin(e) for e in entries)
    # A design that no longer compiles also scores worst on the area axis.
    return (
        float(feasible),
        float(n_passed),
        margin,
        -(area if area is not None else 1e18),
    ), status


def _optimize_specs(
    session: DesignSession,
    testbench: str | None,
    max_iterations: int,
) -> Envelope:
    """Coordinate descent on block params toward all-specs-pass.

    Per parameter try value×(0.75, 0.9, 1.1, 1.33), clamped to [min, max]
    and rounded for ints. The coarse pair explores; the fine pair lands
    inside spec bands narrower than a param's coarse-step sensitivity —
    without them a descent can only straddle the band forever. Each
    candidate is committed through ``transact`` — kept when the
    lexicographic score (feasible, specs passed, margin, -area) improves,
    reverted via transact otherwise. Stops on all-pass, a full round
    without improvement, or ``max_iterations``.
    """
    design = session.design
    if not design.testbenches:
        return Envelope(
            "error",
            session.revision,
            [Diagnostic(
                "error", "no-testbenches",
                "spec-driven optimize needs design.testbenches; declare one "
                "first (set_testbenches op or the testbenches IR field)")],
        )
    names = {t.name for t in design.testbenches}
    if testbench is not None and testbench not in names:
        return Envelope(
            "error",
            session.revision,
            [Diagnostic(
                "error", "unknown-testbench",
                f"no testbench {testbench!r}; known: {sorted(names)}")],
        )
    tunables = _tunable_params(session)
    if not tunables:
        return Envelope(
            "error",
            session.revision,
            [Diagnostic(
                "error", "no-tunable-params",
                "no instance exposes a bounded numeric parameter "
                "(ParamSpec type int/float with min and max)")],
        )

    history: list[dict[str, Any]] = []
    best, best_status = _score(session, testbench)
    initial_score = best
    history.append({
        "iteration": 0,
        "revision": session.revision,
        "param": None,
        "value": None,
        "score": list(best),
        "spec_status": best_status,
    })
    if best_status == "unavailable":
        # Specs cannot be evaluated at all — there is no descent signal,
        # and the area tiebreak would just shrink the layout for nothing
        # (and can drift into LVS-invalid states). Fail closed: report and
        # stop rather than silently tuning a meaningless objective.
        summary = {
            "objective": "specs",
            "testbench": testbench,
            "iterations": 0,
            "evals": 0,
            "initial_score": list(initial_score),
            "final_score": list(best),
            "spec_status": "unavailable",
            "all_passed": False,
        }
        session.record_run("optimize", summary)
        return Envelope(
            "ok",
            session.revision,
            [Diagnostic(
                "warning", "specs-unavailable",
                "specs could not be evaluated (simulator or signals "
                "unavailable); no parameter tuning attempted")],
            {**summary, "history": history, "tunable_params": [
                {"instance": t["instance"], "name": t["name"],
                 "min": t["min"], "max": t["max"], "value": t["value"]}
                for t in tunables]},
        )

    iterations = 0
    for iteration in range(1, max_iterations + 1):
        if best_status == "pass":
            break
        iterations = iteration
        improved = False
        for t in tunables:
            # Candidates anchor on the param's value at the start of its
            # trial; ``kept`` tracks the best value committed so far and is
            # what a losing candidate reverts to.
            base_val = t["value"]
            kept = base_val
            for factor in (0.75, 0.9, 1.1, 1.33):
                cand = base_val * factor
                cand = min(max(cand, t["min"]), t["max"])
                if t["is_int"]:
                    cand = float(int(round(cand)))
                if cand == kept:
                    continue  # clamped/rounded onto the standing value
                # confirm=True: the caller asked for param tuning — that
                # *is* the decision point. Without it a ≤2-instance design
                # would gate every single-param candidate.
                env = session.transact(
                    [{
                        "op": "set_params",
                        "instance": t["instance"],
                        "params": {t["name"]: int(cand) if t["is_int"] else cand},
                    }],
                    expected_revision=session.revision,
                    confirm=True,
                )
                if not env.ok:
                    history.append({
                        "iteration": iteration,
                        "revision": session.revision,
                        "param": f"{t['instance']}.{t['name']}",
                        "value": cand,
                        "score": None,
                        "spec_status": "error",
                        "diagnostics": [d.to_dict() for d in env.diagnostics],
                    })
                    continue
                score, cur_status = _score(session, testbench)
                history.append({
                    "iteration": iteration,
                    "revision": session.revision,
                    "param": f"{t['instance']}.{t['name']}",
                    "value": int(cand) if t["is_int"] else cand,
                    "score": list(score),
                    "spec_status": cur_status,
                })
                if score > best:
                    best = score
                    best_status = cur_status
                    kept = cand
                    improved = True
                else:
                    # Not better: revert the param through transact so the
                    # whole exploration stays revisioned and undoable.
                    session.transact(
                        [{
                            "op": "set_params",
                            "instance": t["instance"],
                            "params": {
                                t["name"]: int(kept) if t["is_int"] else kept},
                        }],
                        expected_revision=session.revision,
                        confirm=True,
                    )
            t["value"] = kept
            if best_status == "pass":
                break
        if not improved or best_status == "pass":
            break

    all_passed = best_status == "pass"
    summary = {
        "objective": "specs",
        "testbench": testbench,
        "iterations": iterations,
        "evals": len(history) - 1,
        "initial_score": list(initial_score),
        "final_score": list(best),
        "spec_status": best_status,
        "all_passed": all_passed,
    }
    session.record_run("optimize", summary)
    return Envelope(
        "ok",
        session.revision,
        data={
            **summary,
            "history": history,
            "tunable_params": [
                {"instance": t["instance"], "name": t["name"],
                 "min": t["min"], "max": t["max"], "value": t["value"]}
                for t in tunables
            ],
        },
    )
