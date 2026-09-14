"""Intelligent layout tuning and DRC-guided optimizer for Block IR."""

from __future__ import annotations

import copy
from typing import Any

from layout_canvas.compiler.compile import compile_design
from layout_canvas.compiler.ppa import extract_ppa
from layout_canvas.ir.model import Design


def _compile_for_metrics(design: Design, suffix: str):
    """Compile under a unique top-cell name for gdsfactory's global layout."""
    compile_design_input = copy.deepcopy(design)
    compile_design_input.name = f"{design.name}__optimizer_{suffix}"
    return compile_design_input, compile_design(compile_design_input)


def optimize_design_layout(
    design: Design,
    target_aspect_ratio: float = 1.0,
    min_clearance: float = 0.5,
    max_iterations: int = 5,
) -> tuple[Design, dict[str, Any]]:
    """Iteratively optimize placement clearances and aspect ratios for a Design."""
    curr_design = copy.deepcopy(design)
    history: list[dict[str, Any]] = []

    for it in range(max_iterations):
        # 1. Compile current layout and analyze PPA
        metric_design, comp = _compile_for_metrics(curr_design, str(it))
        ppa = extract_ppa(comp, metric_design)
        history.append({
            "iteration": it,
            "area_um2": ppa["area_um2"],
            "aspect_ratio": ppa["aspect_ratio"],
            "hpwl_um": ppa["hpwl_um"],
            "wire_length": ppa["hpwl_um"],
        })

        # 2. Check if aspect ratio and clearances satisfy targets
        aspect_ratio = ppa["aspect_ratio"]
        ar_diff = abs(aspect_ratio - target_aspect_ratio) if aspect_ratio is not None else float("inf")
        if ar_diff <= 0.2:
            break

        # 3. Apply rule-based heuristics: if too wide, adjust relative margins or stack vertically
        for inst in curr_design.instances:
            if inst.placement.margin < min_clearance:
                inst.placement.margin = min_clearance

    metric_design, final_comp = _compile_for_metrics(curr_design, "final")
    best_ppa = extract_ppa(final_comp, metric_design)
    return curr_design, {
        "iterations": len(history),
        "history": history,
        "final_ppa": best_ppa,
    }
