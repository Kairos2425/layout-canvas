"""Matching/symmetry quality checker — verifies declared IR ``Constraint``s
against the *placed* geometry.

A green DRC/LVS verdict says the layout is physically legal and
topologically correct; it says nothing about whether the designer's
declared matching intent actually holds on the canvas. ``check_constraints``
closes that gap for the constraint semantics expressible over placed
instance bounding boxes:

- ``symmetric`` — members mirror about the group's centre axis.  Vertical
  axis ⇒ equal |dx| to the group centre-x and identical y-span; horizontal
  swaps the roles.  An even member count needs every member paired; an odd
  count needs exactly one centroid on the axis.
- ``match`` — same block with identical resolved params (a), congruent
  placed bboxes (b) and uniform adjacent-centroid pitch (c).
- ``order`` — bbox-edge order equals the listed instance order.
- ``align`` — the declared edge/centre coordinate is equal across members.
- ``abut`` — consecutive members' facing edges touch within tolerance.

Everything is measured in top-cell micrometres on the compiled component,
reusing the positional ``design.instances`` ↔ ``component.insts`` pairing
that ``web/app.py::_instance_bboxes`` documents (``compile_design`` inserts
refs in design order).  The tolerance is the layout snap grid
(``blocks/*/geom.snap`` default 0.005 µm — one grid step); real deviations
are always reported, never silently accepted.

What this does NOT inspect: intra-cell geometry — finger
interdigitation/ABBA patterns, dummy devices, shared-centroid merges,
parasitic or routing symmetry, guard-ring continuity.  Those are
block-internal or net-level properties, not instance-bbox facts.

Fail-closed contract: a check that cannot be evaluated (missing axis,
unresolved block, unpaired component) reports ``unavailable`` with the
reason — never a guessed pass.  A declared constraint that is measurable
and violated reports ``fail`` naming the members.  A design with no
constraints reports ``not_applicable``.
"""

from __future__ import annotations

from typing import Any

from layout_canvas.blocks import base
from layout_canvas.compiler.compile import compile_design
from layout_canvas.ir.model import Constraint, ConstraintType, Design

# Placement/geometry is snapped to the 0.005 µm grid (blocks/*/geom.snap,
# PDK descriptor ``grid_um``) — deviations below one grid step are "on
# grid"; larger ones are real and reported.
TOL_UM = 0.005

_NOT_CHECKED_NOTE = (
    "intra-cell finger interdigitation/dummy/ABBA order is block-internal"
    " — not inspected (structural-level check)"
)


def _result(check_id: str, ctype: str, instances: list[str], status: str,
            measured: dict[str, Any], note: str) -> dict[str, Any]:
    return {"id": check_id, "type": ctype, "instances": instances,
            "status": status, "measured": measured, "note": note}


def _resolve_bboxes(design: Design, component: Any) -> dict[str, list[float]]:
    """Map instance ids to placed ``[left, bottom, right, top]`` in µm.

    Same positional pairing as ``web/app.py::_instance_bboxes``:
    ``compile_design`` inserts one ref per design instance in order, so
    ``component.insts`` aligns 1:1.  A component that does not carry
    exactly that many refs is not this design's — refuse rather than pair
    wrongly.
    """
    if component is None:
        component = compile_design(design)
    insts = list(component.insts)
    if len(insts) != len(design.instances):
        raise ValueError(
            f"component has {len(insts)} refs but design lists "
            f"{len(design.instances)} instances — cannot pair bboxes")
    boxes: dict[str, list[float]] = {}
    for inst, ref in zip(design.instances, insts, strict=True):
        bb = ref.bbox()
        boxes[inst.id] = [float(bb.left), float(bb.bottom),
                          float(bb.right), float(bb.top)]
    return boxes


# ---------------------------------------------------------------------------
# Per-type evaluators.  Each returns a ``_result`` dict; each receives the
# Constraint, the placed bboxes and the design's instance map.
# ---------------------------------------------------------------------------


def _check_symmetric(c: Constraint, check_id: str,
                     boxes: dict[str, list[float]],
                     _inst_map: dict[str, Any]) -> dict[str, Any]:
    insts = list(c.instances)
    if c.axis is None:
        return _result(check_id, "symmetric", insts, "unavailable", {},
                       "symmetric constraint needs 'axis' "
                       "(vertical|horizontal) — cannot evaluate")
    vertical = c.axis == "vertical"
    members: list[dict[str, Any]] = []
    for iid in insts:
        x0, y0, x1, y1 = boxes[iid]
        members.append({
            "id": iid, "l": x0, "b": y0, "r": x1, "t": y1,
            "cx": (x0 + x1) / 2.0, "cy": (y0 + y1) / 2.0,
        })
    if vertical:
        axis_um = (min(m["l"] for m in members)
                   + max(m["r"] for m in members)) / 2.0
    else:
        axis_um = (min(m["b"] for m in members)
                   + max(m["t"] for m in members)) / 2.0
    for m in members:
        m["d"] = (m["cx"] - axis_um) if vertical else (m["cy"] - axis_um)
        m["lo"], m["hi"] = ((m["b"], m["t"]) if vertical
                            else (m["l"], m["r"]))

    def span_dev(i: int, j: int) -> float:
        return max(abs(members[i]["lo"] - members[j]["lo"]),
                   abs(members[i]["hi"] - members[j]["hi"]))

    def resid(i: int, j: int) -> float:
        # 0 for a perfect mirror pair (equal |d|, opposite signs)
        return abs(members[i]["d"] + members[j]["d"])

    # Greedy mirror pairing, walking members in distance order.
    remaining = sorted(range(len(members)),
                       key=lambda k: members[k]["d"])
    partner: dict[int, int] = {}
    while remaining:
        i = remaining.pop(0)
        best_j, best_score = None, None
        for j in remaining:
            r_ij, s_ij = resid(i, j), span_dev(i, j)
            if r_ij <= TOL_UM and s_ij <= TOL_UM:
                score = r_ij + s_ij
                if best_score is None or score < best_score:
                    best_j, best_score = j, score
        if best_j is not None:
            partner[i], partner[best_j] = best_j, i
            remaining.remove(best_j)
    unpaired = [k for k in range(len(members)) if k not in partner]
    odd = len(members) % 2 == 1

    def nearest(i: int) -> int | None:
        """Closest would-be partner — for honest deviation reporting on
        unpaired members."""
        cands = [j for j in range(len(members)) if j != i]
        if not cands:
            return None
        return min(cands, key=lambda j: resid(i, j) + span_dev(i, j))

    members_out: list[dict[str, Any]] = []
    for i, m in enumerate(members):
        j = partner.get(i)
        if j is not None:
            r_ij, s_ij = resid(i, j), span_dev(i, j)
            members_out.append({
                "instance": m["id"], "mirror_partner": members[j]["id"],
                "on_axis": False,
                "dx_deviation_um": round(r_ij if vertical else s_ij, 4),
                "dy_deviation_um": round(s_ij if vertical else r_ij, 4)})
        elif abs(m["d"]) <= TOL_UM:
            members_out.append({
                "instance": m["id"], "mirror_partner": None,
                "on_axis": True,
                "dx_deviation_um": round(abs(m["d"]) if vertical else 0.0, 4),
                "dy_deviation_um": round(0.0 if vertical else abs(m["d"]), 4)})
        else:
            j = nearest(i)
            r_ij = resid(i, j) if j is not None else abs(m["d"])
            s_ij = span_dev(i, j) if j is not None else 0.0
            members_out.append({
                "instance": m["id"], "mirror_partner": None,
                "on_axis": False,
                "dx_deviation_um": round(r_ij if vertical else s_ij, 4),
                "dy_deviation_um": round(s_ij if vertical else r_ij, 4)})

    axis_name = "x" if vertical else "y"
    measured = {"axis": c.axis, "axis_um": round(axis_um, 4),
                "members": members_out}
    if odd:
        on_axis = [members[i]["id"] for i in unpaired
                   if abs(members[i]["d"]) <= TOL_UM]
        ok = len(unpaired) == 1 and len(on_axis) == 1
        if ok:
            note = (f"{len(members)} members: middle {on_axis[0]!r} on "
                    f"{axis_name}={axis_um}µm, rest mirrored")
        elif len(unpaired) != 1:
            names = [members[i]["id"] for i in unpaired]
            note = (f"odd group needs one member on the axis and the rest "
                    f"paired — unpaired: {names} (axis {axis_name}="
                    f"{axis_um}µm)")
        else:
            i = unpaired[0]
            note = (f"middle member {members[i]['id']!r} is "
                    f"{abs(members[i]['d']):.4f}µm off the axis "
                    f"({axis_name}={axis_um}µm)")
    else:
        ok = not unpaired
        if ok:
            note = (f"{len(members)} members mirrored about "
                    f"{axis_name}={axis_um}µm")
        else:
            names = [members[i]["id"] for i in unpaired]
            note = (f"no mirror partner for member(s) {names} "
                    f"(axis {axis_name}={axis_um}µm)")
    if c.note:
        note = f"{note}; declared note: {c.note}"
    return _result(check_id, "symmetric", insts,
                   "pass" if ok else "fail", measured, note)


def _check_match(c: Constraint, check_id: str,
                 boxes: dict[str, list[float]],
                 inst_map: dict[str, Any]) -> dict[str, Any]:
    insts = list(c.instances)
    blocks: dict[str, str] = {}
    resolved: dict[str, dict[str, Any]] = {}
    for iid in insts:
        inst = inst_map[iid]
        blocks[iid] = inst.block
        try:
            blk = base.get(inst.block)
        except KeyError:
            return _result(check_id, "match", insts, "unavailable", {},
                           f"unknown block {inst.block!r} — cannot "
                           "compare params")
        try:
            resolved[iid] = blk.resolve_params(inst.params)
        except ValueError as exc:
            return _result(check_id, "match", insts, "unavailable", {},
                           f"{iid}: params do not resolve — {exc}")

    ref = insts[0]
    differing: set[str] = set()
    same_bp = True
    for iid in insts[1:]:
        if blocks[iid] != blocks[ref]:
            same_bp = False
        for k in set(resolved[ref]) | set(resolved[iid]):
            if resolved[ref].get(k) != resolved[iid].get(k):
                same_bp = False
                differing.add(k)

    x0, y0, x1, y1 = boxes[ref]
    w0, h0 = x1 - x0, y1 - y0
    wh: dict[str, list[float]] = {}
    max_dim_dev = 0.0
    cents: dict[str, list[float]] = {}
    for iid in insts:
        x0, y0, x1, y1 = boxes[iid]
        w, h = x1 - x0, y1 - y0
        wh[iid] = [round(w, 4), round(h, 4)]
        cents[iid] = [(x0 + x1) / 2.0, (y0 + y1) / 2.0]
        max_dim_dev = max(max_dim_dev, abs(w - w0), abs(h - h0))
    congruent = max_dim_dev <= TOL_UM

    pitch = [[round(cents[b_][0] - cents[a_][0], 4),
              round(cents[b_][1] - cents[a_][1], 4)]
             for a_, b_ in zip(insts, insts[1:], strict=False)]
    max_pitch_dev = 0.0
    for v in pitch[1:]:
        max_pitch_dev = max(max_pitch_dev,
                            abs(v[0] - pitch[0][0]),
                            abs(v[1] - pitch[0][1]))
    uniform = max_pitch_dev <= TOL_UM

    measured = {
        "blocks": blocks,
        "resolved_params": resolved,
        "differing_params": sorted(differing),
        "bbox_wh_um": wh,
        "centroids_um": {k: [round(x, 4) for x in v]
                         for k, v in cents.items()},
        "pitch_um": pitch,
        "same_block_and_params": same_bp,
        "congruent_bbox": congruent,
        "uniform_pitch": uniform,
        "max_dim_deviation_um": round(max_dim_dev, 4),
        "max_pitch_deviation_um": round(max_pitch_dev, 4),
    }
    failed = []
    if not same_bp:
        detail = (f"differing params {sorted(differing)}"
                  if differing
                  else f"blocks differ: {sorted(set(blocks.values()))}")
        failed.append(f"(a) block/params — {detail}")
    if not congruent:
        failed.append(f"(b) congruent bboxes — max dim deviation "
                      f"{max_dim_dev:.4f}µm")
    if not uniform:
        failed.append(f"(c) uniform pitch — max deviation "
                      f"{max_pitch_dev:.4f}µm")
    if failed:
        note = "; ".join(failed)
        status = "fail"
    else:
        note = f"{len(insts)}× {blocks[ref]} identical, uniform pitch"
        status = "pass"
    note = f"{note}; {_NOT_CHECKED_NOTE}"
    if c.note:
        note = f"{note}; declared note: {c.note}"
    return _result(check_id, "match", insts, status, measured, note)


def _check_order(c: Constraint, check_id: str,
                 boxes: dict[str, list[float]],
                 _inst_map: dict[str, Any]) -> dict[str, Any]:
    insts = list(c.instances)
    if c.direction is None:
        return _result(check_id, "order", insts, "unavailable", {},
                       "order constraint needs 'direction' "
                       "(left_to_right|bottom_to_top) — cannot evaluate")
    key_idx = 0 if c.direction == "left_to_right" else 1  # left | bottom
    edges = {iid: boxes[iid][key_idx] for iid in insts}
    # Tolerant non-decreasing check in the listed order — edges within one
    # grid step count as equal, never as inverted.
    inversions = [(insts[k], insts[k + 1],
                   round(edges[insts[k]], 4), round(edges[insts[k + 1]], 4))
                  for k in range(len(insts) - 1)
                  if edges[insts[k + 1]] < edges[insts[k]] - TOL_UM]
    actual = sorted(insts, key=lambda iid: edges[iid])  # stable ties
    measured = {"direction": c.direction,
                "edge_um": {iid: round(v, 4) for iid, v in edges.items()},
                "expected_order": insts,
                "actual_order": actual}
    if inversions:
        note = (f"listed order {insts} is inverted at "
                f"{[f'{a}→{b}' for a, b, _, _ in inversions]}; "
                f"placed order is {actual}")
        status = "fail"
    else:
        note = f"{c.direction} order matches {insts}"
        status = "pass"
    if c.note:
        note = f"{note}; declared note: {c.note}"
    return _result(check_id, "order", insts, status, measured, note)


_ALIGN_VALS = {
    "left": lambda bb: bb[0],
    "bottom": lambda bb: bb[1],
    "right": lambda bb: bb[2],
    "top": lambda bb: bb[3],
    "center_x": lambda bb: (bb[0] + bb[2]) / 2.0,
    "center_y": lambda bb: (bb[1] + bb[3]) / 2.0,
}


def _check_align(c: Constraint, check_id: str,
                 boxes: dict[str, list[float]],
                 _inst_map: dict[str, Any]) -> dict[str, Any]:
    insts = list(c.instances)
    if c.edge is None:
        return _result(check_id, "align", insts, "unavailable", {},
                       "align constraint needs 'edge' (left|right|top|"
                       "bottom|center_x|center_y) — cannot evaluate")
    val = _ALIGN_VALS[c.edge]
    ref = val(boxes[insts[0]])
    members = [{
        "instance": iid,
        "value_um": round(val(boxes[iid]), 4),
        "deviation_um": round(val(boxes[iid]) - ref, 4),
    } for iid in insts]
    max_dev = max(abs(m["deviation_um"]) for m in members)
    ok = max_dev <= TOL_UM
    measured = {"edge": c.edge, "reference_instance": insts[0],
                "reference_um": round(ref, 4), "members": members}
    if ok:
        note = (f"all {len(insts)} members share {c.edge}≈{ref:.4f}µm "
                f"(max |dev| {max_dev:.4f}µm, ref {insts[0]!r})")
        status = "pass"
    else:
        worst = max(members, key=lambda m: abs(m["deviation_um"]))
        note = (f"{c.edge} deviates up to {worst['deviation_um']:+.4f}µm "
                f"on {worst['instance']!r} (ref {insts[0]!r} @ "
                f"{ref:.4f}µm)")
        status = "fail"
    if c.note:
        note = f"{note}; declared note: {c.note}"
    return _result(check_id, "align", insts, status, measured, note)


def _check_abut(c: Constraint, check_id: str,
                boxes: dict[str, list[float]],
                _inst_map: dict[str, Any]) -> dict[str, Any]:
    insts = list(c.instances)
    # Which way do members face? direction wins, then edge, then axis.
    axis_dir = None
    if c.direction is not None:
        axis_dir = "x" if c.direction == "left_to_right" else "y"
    elif c.edge in ("left", "right", "center_x"):
        axis_dir = "x"
    elif c.edge in ("top", "bottom", "center_y"):
        axis_dir = "y"
    elif c.axis == "vertical":
        axis_dir = "x"      # vertical axis ⇒ members side by side
    elif c.axis == "horizontal":
        axis_dir = "y"
    if axis_dir is None:
        return _result(check_id, "abut", insts, "unavailable", {},
                       "abut needs 'direction', 'edge' or 'axis' to pick "
                       "the facing edges — cannot evaluate")
    pairs = []
    for a, b_ in zip(insts, insts[1:], strict=False):
        gap = (boxes[b_][0] - boxes[a][2] if axis_dir == "x"
               else boxes[b_][1] - boxes[a][3])
        pairs.append({"pair": [a, b_], "gap_um": round(gap, 4)})
    max_gap = max((abs(p["gap_um"]) for p in pairs), default=0.0)
    ok = max_gap <= TOL_UM
    direction = "left_to_right" if axis_dir == "x" else "bottom_to_top"
    measured = {"direction": direction, "pairs": pairs,
                "max_gap_um": round(max_gap, 4)}
    if ok:
        note = (f"{len(pairs)} consecutive pair(s) abut within "
                f"{TOL_UM}µm ({direction})")
        status = "pass"
    else:
        bad = [f"{p['pair'][0]}→{p['pair'][1]} {p['gap_um']:+.4f}µm"
               for p in pairs if abs(p["gap_um"]) > TOL_UM]
        note = f"facing edges do not touch: {'; '.join(bad)}"
        status = "fail"
    if c.note:
        note = f"{note}; declared note: {c.note}"
    return _result(check_id, "abut", insts, status, measured, note)


_EVALUATORS = {
    ConstraintType.symmetric: _check_symmetric,
    ConstraintType.match: _check_match,
    ConstraintType.order: _check_order,
    ConstraintType.align: _check_align,
    ConstraintType.abut: _check_abut,
}


def check_constraints(design: Design, component: Any | None = None
                      ) -> dict[str, Any]:
    """Evaluate every declared ``Constraint`` on the placed geometry.

    ``component`` may carry the already-compiled ``gf.Component`` (as
    ``verify_design`` does) — when omitted the design is compiled once
    here.  Returns ``{status, checks, tolerance_um, note}`` with status ∈
    pass | fail | unavailable | not_applicable.  ``not_applicable`` means
    the design declared nothing — it is *not* a pass.
    """
    if not design.constraints:
        return {"status": "not_applicable", "tolerance_um": TOL_UM,
                "checks": [],
                "note": "no constraints declared — nothing to check"}

    checks: list[dict[str, Any]] = []
    try:
        boxes = _resolve_bboxes(design, component)
    except Exception as exc:
        for i, c in enumerate(design.constraints):
            checks.append(_result(
                f"{c.type}[{i}]", str(c.type), list(c.instances),
                "unavailable", {},
                f"cannot resolve placed geometry: {exc}"))
        return {"status": "unavailable", "tolerance_um": TOL_UM,
                "checks": checks,
                "note": f"placed geometry unavailable: {exc}"}

    inst_map = {i.id: i for i in design.instances}
    for i, c in enumerate(design.constraints):
        check_id = f"{c.type}[{i}]"
        missing = [iid for iid in c.instances if iid not in boxes]
        if missing:
            checks.append(_result(
                check_id, str(c.type), list(c.instances), "fail",
                {"missing_instances": missing},
                f"unknown instance(s) {missing} — not in the design"))
            continue
        evaluator = _EVALUATORS.get(c.type)
        if evaluator is None:  # pragma: no cover - enum is exhaustive
            checks.append(_result(
                check_id, str(c.type), list(c.instances), "unavailable",
                {}, f"no evaluator for constraint type {c.type!r}"))
            continue
        try:
            checks.append(evaluator(c, check_id, boxes, inst_map))
        except Exception as exc:
            checks.append(_result(
                check_id, str(c.type), list(c.instances), "unavailable",
                {}, f"evaluation error: {exc}"))

    statuses = {chk["status"] for chk in checks}
    if "fail" in statuses:
        status = "fail"
    elif "unavailable" in statuses:
        status = "unavailable"
    else:
        status = "pass"
    n = len(checks)
    note = (f"{sum(1 for c_ in checks if c_['status'] == 'pass')}/{n} "
            f"declared constraints verified"
            if status == "pass"
            else f"{n} declared constraints: "
                 + ", ".join(f"{s}×{sum(1 for c_ in checks if c_['status'] == s)}"
                             for s in sorted(statuses)))
    return {"status": status, "tolerance_um": TOL_UM,
            "checks": checks, "note": note}
