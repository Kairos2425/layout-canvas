"""Session-aware PPA optimizer.

Each tuning iteration is committed through ``DesignSession.transact`` so
every step is revisioned, validated, and undoable — the optimizer is a
client of the same mutation boundary as any other agent, not a backdoor.
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
    max_iterations: int = 5,
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

        env = session.transact(edits)
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
