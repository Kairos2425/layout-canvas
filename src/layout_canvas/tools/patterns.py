"""Intra-cell finger-pattern inspection — gate interdigitation measured
on the compiled layout, through the real ``LayoutToNetlist`` connectivity.

``check_constraints`` (tools/matching.py) judges *placed* instance
bboxes; this module opens the cell boundary. On the same canonical
layout + netlist extraction that LVS runs it collects every
``poly ∩ diff`` gate shape, resolves which flat connectivity cluster
(net) each gate rides on and assigns fingers to design instances by
placed bbox.  Per instance it reports:

- ``fingers`` — count of gate shapes inside the placed bbox;
- ``axis`` — the finger-array direction (``"x"``/``"y"``: the axis with
  the larger centroid spread; a non-collinear spread is noted);
- ``pattern`` — one letter per distinct gate net in first-appearance
  order along the axis, e.g. ``ABBAABBA`` — the interdigitation order
  verbatim, as drawn;
- ``nets`` — per gate net: name, finger count, area-weighted centroid
  along the axis and a classification (``signal`` / ``supply_or_bulk`` /
  ``unlabeled``);
- ``centroid_deviation_um`` — max pairwise spread of the *eligible*
  gate nets' centroids (the common-centroid figure of merit); ``None``
  when fewer than two eligible nets exist.  Eligible = labeled
  non-supply nets, plus unlabeled nets contributing an interior finger;
  supply-named nets and unlabeled edge-only clusters are presumed-dummy
  candidates, never folded into the figure of merit;
- ``dummy_edges`` — the two extreme fingers' net + classification.  Only
  a supply/bulk-named edge net counts as a detected dummy; an unlabeled
  edge-only net is reported ``unlabeled`` (undetermined, not guessed),
  and a dummy strapped onto a *signal* gate net is geometrically
  indistinguishable from an active finger — detecting it would be
  inference, not inspection, and is explicitly not claimed.

Per-instance ``status``: ``pass`` when ≥2 eligible gate nets exist and
their centroid deviation is within ``tolerance_um``; ``structural`` when
the pattern was measured but no common-centroid verdict exists (a single
gate net — mirror cells tie all gates together — or a deviation over
tolerance, which the note names); ``unavailable`` when no gate shapes
exist inside the instance or extraction could not run.  A *measured*
over-tolerance deviation is real evidence — the constraint checker
escalates it; ``structural``/``unavailable`` never invent a verdict.

Fail-closed contract: missing klayout, a missing extraction recipe, a
failed netlist extraction, an unpairible component or an unnamed top
cell all report ``unavailable`` with the reason — the inspection is
never silently skipped.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from layout_canvas.compiler.compile import compile_design
from layout_canvas.ir.model import Design
from layout_canvas.tools.extract import _recipe_for

# Snap-grid tolerance — the same physical 0.005 µm grid the placement
# checker (tools/matching.TOL_UM) uses; deviations below it are "on
# grid".  Kept a parameter so callers can tighten/loosen deliberately.
TOL_UM = 0.005

# Gate-net classifications.
_SUPPLY_NET_NAMES = {
    "vss", "vssa", "vssd", "vdd", "vdda", "vddd", "vcc", "vccd",
    "gnd", "gnda", "vsub", "vbb", "vssx", "avss", "avdd", "dvss",
    "dvdd", "sub", "substrate", "psub", "nsub", "bulk", "vss!", "vdd!",
    "gnd!",
}

_EDGE_DUMMY_CAVEAT = (
    "dummies sharing a signal gate net are geometrically "
    "indistinguishable from active fingers — not detected; unlabeled "
    "edge nets are reported 'unlabeled' rather than guessed"
)


def _resolve_bboxes(design: Design, component: Any) -> dict[str, list[float]]:
    """Map instance ids to placed ``[left, bottom, right, top]`` in µm.

    Same positional pairing as ``web/app.py::_instance_bboxes``:
    ``compile_design`` inserts one ref per design instance in order, so
    ``component.insts`` aligns 1:1.  A component that does not carry
    exactly that many refs is not this design's — refuse rather than pair
    wrongly.  (Canonical home is this module; ``tools.matching`` re-exports
    it.)
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


def _poly_centroid(poly: Any) -> tuple[float, float]:
    """True area centroid of a ``db.Polygon`` (shoelace), in its units."""
    pts = list(poly.each_point_hull())
    a2 = cx = cy = 0.0
    for p0, p1 in zip(pts, pts[1:] + pts[:1], strict=False):
        cr = p0.x * p1.y - p1.x * p0.y
        a2 += cr
        cx += (p0.x + p1.x) * cr
        cy += (p0.y + p1.y) * cr
    if abs(a2) < 1e-12:
        b = poly.bbox()
        return (b.left + b.right) / 2.0, (b.bottom + b.top) / 2.0
    return cx / (3.0 * a2), cy / (3.0 * a2)


def _net_kind(name: str, labeled: bool) -> str:
    """Classify a gate net for centroid/dummy purposes."""
    if not labeled:
        return "unlabeled"
    parts = {p.strip().lower() for p in name.split(",") if p.strip()}
    if parts and parts <= _SUPPLY_NET_NAMES:
        return "supply_or_bulk"
    return "signal"


def _letter(index: int) -> str:
    return chr(ord("A") + index) if index < 26 else f"N{index}"


def _instance_report(iid: str, frag_rows: list[dict[str, Any]],
                     tolerance_um: float) -> dict[str, Any]:
    """Build the per-instance intracell entry from its gate fragments.

    ``frag_rows`` are ``{x, y, area, cluster, net, kind}`` in µm.
    """
    entry: dict[str, Any] = {
        "instance": iid,
        "fingers": len(frag_rows),
        "axis": None,
        "pattern": None,
        "nets": [],
        "centroid_deviation_um": None,
        "dummy_edges": [],
        "status": "unavailable",
        "note": "no poly∩diff gate shapes inside the placed bbox",
    }
    if not frag_rows:
        return entry

    xs = [f["x"] for f in frag_rows]
    ys = [f["y"] for f in frag_rows]
    spread_x = max(xs) - min(xs)
    spread_y = max(ys) - min(ys)
    axis = "x" if spread_x >= spread_y else "y"
    entry["axis"] = axis
    if axis == "x":
        frag_rows.sort(key=lambda f: (f["x"], f["y"]))
    else:
        frag_rows.sort(key=lambda f: (f["y"], f["x"]))

    # Pattern letters: one per distinct cluster, first-appearance order.
    letter_of: dict[Any, str] = {}
    letters = []
    for f in frag_rows:
        if f["cluster"] not in letter_of:
            letter_of[f["cluster"]] = _letter(len(letter_of))
        letters.append(letter_of[f["cluster"]])
    pattern = "".join(letters)
    entry["pattern"] = pattern

    # Per-net stats, in first-appearance (letter) order.  A net is a
    # centroid *candidate* when it is a labeled non-supply net, or an
    # unlabeled net contributing at least one *interior* finger — an
    # unlabeled cluster that only ever sits at a row edge is the exact
    # signature of a floating/intended dummy and is excluded (its dummy
    # status stays "undetermined", never guessed).
    edge_idx = {0, len(frag_rows) - 1}
    nets: list[dict[str, Any]] = []
    for cid, letter in letter_of.items():
        idx = [i for i, f in enumerate(frag_rows) if f["cluster"] == cid]
        rows = [frag_rows[i] for i in idx]
        coord = [f["x"] if axis == "x" else f["y"] for f in rows]
        weight = [f["area"] for f in rows]
        cent = (sum(c * w for c, w in zip(coord, weight, strict=True))
                / sum(weight))
        kind = rows[0]["kind"]
        edge_only = all(i in edge_idx for i in idx)
        eligible = (kind == "signal"
                    or (kind == "unlabeled" and not edge_only))
        nets.append({
            "net": rows[0]["net"],
            "letter": letter,
            "kind": kind,
            "finger_count": len(rows),
            "centroid_um": round(cent, 4),
            "eligible": eligible,
            "edge_only": edge_only,
        })
    entry["nets"] = nets

    # Common-centroid figure of merit over the eligible gate nets:
    # supply/bulk-named nets are presumed-dummy fingers — folding them
    # into the centroid would penalise a correct dummy scheme.
    candidates = [n for n in nets if n["eligible"]]
    if len(candidates) >= 2:
        cents = [n["centroid_um"] for n in candidates]
        dev = round(max(cents) - min(cents), 4)
        entry["centroid_deviation_um"] = dev
        entry["status"] = "pass" if dev <= tolerance_um else "structural"
    else:
        entry["status"] = "structural"

    # Edge fingers: extreme positions along the finger axis.
    lo_side, hi_side = ("left", "right") if axis == "x" else ("bottom", "top")
    edges = [(lo_side, frag_rows[0]), (hi_side, frag_rows[-1])]
    entry["dummy_edges"] = [
        {"side": side,
         "net": f["net"],
         "kind": f["kind"],
         "is_dummy": f["kind"] == "supply_or_bulk"}
        for side, f in edges
    ]
    dummy_count = sum(1 for e in entry["dummy_edges"] if e["is_dummy"])
    entry["dummy_count"] = dummy_count

    # Honest note.
    names = "/".join(f"{n['net']}×{n['finger_count']}" for n in nets)
    bits = [f"{len(frag_rows)} finger(s) on gate net(s) {names}"]
    if entry["centroid_deviation_um"] is None:
        if len(candidates) == 1:
            bits.append(
                f"pattern {pattern} — single centroid-eligible gate net "
                f"({candidates[0]['net']!r}): no centroid pair to compare "
                "(e.g. mirror cells tie all gates together; S/D-segment "
                "interdigitation is a separate axis, not scored here)")
        else:
            bits.append(f"pattern {pattern} — no ≥2 centroid-eligible "
                        "gate nets: no pair to compare")
    else:
        dev = entry["centroid_deviation_um"]
        if dev <= tolerance_um:
            bits.append(f"pattern {pattern}, centroid deviation "
                        f"{dev:.4f}µm within tol {tolerance_um}µm — "
                        "common-centroid holds")
        else:
            bits.append(f"pattern {pattern}, centroid deviation "
                        f"{dev:.4f}µm EXCEEDS tol {tolerance_um}µm — "
                        "common-centroid does NOT hold")
    if dummy_count:
        sides = [e["side"] for e in entry["dummy_edges"] if e["is_dummy"]]
        bits.append(f"edge dummies detected on {'/'.join(sides)} edge(s) "
                    f"(net {entry['dummy_edges'][0]['net']!r})")
    undetermined = [n["net"] for n in nets
                    if n["kind"] == "unlabeled" and n["edge_only"]]
    if undetermined:
        bits.append(f"edge-only unlabeled net(s) {undetermined} — "
                    "dummy status undetermined")
    bits.append(_EDGE_DUMMY_CAVEAT)
    if min(spread_x, spread_y) > tolerance_um and len(frag_rows) > 1:
        bits.append(
            f"finger centroids not collinear (x-spread {spread_x:.4f}µm, "
            f"y-spread {spread_y:.4f}µm) — pattern sorted along {axis}")
    entry["note"] = "; ".join(bits)
    return entry


def inspect_intracell(design: Design, component: Any | None = None,
                      *, extraction: Any | None = None,
                      tolerance_um: float = TOL_UM) -> dict[str, Any]:
    """Inspect intra-cell finger interdigitation on the compiled layout.

    Returns ``{status, reason, instances, unassigned_fingers}`` where
    ``instances`` maps every design instance id to its per-instance
    entry.  ``status`` is ``ok`` (inspection ran), ``unavailable``
    (nothing determinable — the reason names why) or ``error``.
    ``extraction`` may carry a prior ``ExtractionResult`` for the same
    compiled component (``verify_design`` reuses its own — one extraction
    serves LVS, antenna *and* this inspection).
    """
    out: dict[str, Any] = {"status": "unavailable", "reason": "",
                           "instances": {}, "unassigned_fingers": 0}

    def _unavail(reason: str) -> dict[str, Any]:
        out["status"] = "unavailable"
        out["reason"] = reason
        out["instances"] = {
            i.id: {
                "instance": i.id, "fingers": 0, "axis": None,
                "pattern": None, "nets": [], "centroid_deviation_um": None,
                "dummy_edges": [], "dummy_count": 0,
                "status": "unavailable",
                "note": f"intra-cell not determinable: {reason}",
            } for i in design.instances
        }
        return out

    try:
        import klayout.db as db
    except ImportError:
        return _unavail("klayout python module not installed")

    try:
        if component is None:
            component = compile_design(design)
        boxes = _resolve_bboxes(design, component)
    except Exception as exc:
        return _unavail(f"cannot resolve placed geometry: {exc}")

    recipe = _recipe_for(design.pdk)
    if recipe is None or not recipe.get("poly") or not recipe.get("diff"):
        return _unavail(f"extraction recipe for {design.pdk!r} lacks "
                        "poly/diff — cannot derive MOS gates")

    ext = extraction
    if ext is None:
        tmp = Path(tempfile.mkdtemp())
        gds = tmp / f"{design.name}.gds"
        try:
            component.write_gds(str(gds))
        except Exception as exc:
            return _unavail(f"cannot write GDS for extraction: {exc}")
        from layout_canvas.tools.extract import extract_netlist
        ext = extract_netlist(str(gds), design.pdk)
    if getattr(ext, "status", None) != "ok" or ext.netlist_object is None:
        detail = "; ".join(getattr(ext, "errors", None) or [])
        return _unavail(f"netlist extraction "
                        f"{getattr(ext, 'status', 'missing')}"
                        + (f": {detail}" if detail else ""))

    l2n, ly = ext._owners
    top = ly.cell(design.name)
    if top is None:
        return _unavail(f"extracted layout has no cell named "
                        f"{design.name!r} — not this design's geometry")
    dbu = ly.dbu
    nl = ext.netlist_object
    try:
        tc = nl.top_circuit()
    except Exception:
        tc = None
    if tc is None:
        return _unavail("extracted netlist has no top circuit")

    # Gate shapes = poly ∩ diff, flat in the canonical layout's top space.
    gate = db.Region(db.RecursiveShapeIterator(
        ly, top, [ly.layer(*recipe["poly"])])) & db.Region(
        db.RecursiveShapeIterator(ly, top, [ly.layer(*recipe["diff"])]))
    if gate.is_empty():
        return _unavail("no MOS gates (poly∩diff) in the whole layout — "
                        "nothing to inspect")

    try:
        pidx = l2n.layer_index("poly")
    except Exception as exc:
        return _unavail(f"extracted netlist exposes no 'poly' layer: {exc}")

    # Every flat physical cluster surfaces as exactly one top-circuit net;
    # polygons_of_net(net, poly_idx, recursive=True) returns that
    # cluster's poly shapes in top-cell coordinates.
    frag_rows: list[dict[str, Any]] = []
    for net in tc.each_net():
        pr = l2n.polygons_of_net(net, pidx, True)
        if pr is None or pr.is_empty():
            continue
        frag = pr & gate
        if frag.is_empty():
            continue
        name = (net.name or "").strip()
        labeled = bool(name)
        kind = _net_kind(name, labeled)
        for poly in frag.each():
            cx, cy = _poly_centroid(poly)
            frag_rows.append({
                "x": cx * dbu, "y": cy * dbu, "area": poly.area(),
                "cluster": net.cluster_id,
                "net": name or f"net#{net.cluster_id}",
                "kind": kind,
            })

    per_inst: dict[str, list[dict[str, Any]]] = {
        iid: [] for iid in boxes}
    unassigned = 0
    eps = tolerance_um  # boundary slop — half-grid is fine, never worse
    # Smallest containing bbox owns a finger — an enclosing cell (guard
    # ring, a hierarchy wrapper) must not steal fingers from the real
    # inner instance.
    owners = sorted(boxes.items(),
                    key=lambda kv: (kv[1][2] - kv[1][0])
                    * (kv[1][3] - kv[1][1]))
    for f in frag_rows:
        for iid, (x0, y0, x1, y1) in owners:
            if (x0 - eps <= f["x"] <= x1 + eps
                    and y0 - eps <= f["y"] <= y1 + eps):
                per_inst[iid].append(f)
                break
        else:
            unassigned += 1

    out["instances"] = {
        iid: _instance_report(iid, rows, tolerance_um)
        for iid, rows in per_inst.items()
    }
    out["unassigned_fingers"] = unassigned
    out["status"] = "ok"
    if unassigned:
        out["reason"] = (f"{unassigned} gate shape(s) lie outside every "
                         "instance bbox — top-level-drawn devices are "
                         "not attributed to any instance")
    return out
