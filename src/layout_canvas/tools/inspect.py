"""Read-only geometry inspection over a compiled GDS — the agent-side
"dimension picker".

Num-Layout-style micro-operations assume a human can hover and read edge
lengths and spacings; an agent needs the same facts as data.  This module
answers three questions against any GDS via the in-process KLayout engine:

* what is in the layout (top cell, hierarchy, bbox, per-layer shape/area
  census — recursive, so subcell contents count),
* how big is a feature (optional ``box_um`` query returns every shape
  intersecting the box with its own bbox dimensions — the measure use
  case),
* how far apart are things (optional ``spacing_layers`` pair returns the
  minimum edge-to-edge gap measured by KLayout ``Region`` booleans).

Everything is fail-closed: a missing klayout module or unreadable file
reports ``unavailable``/``error`` — never a fabricated clean report.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def _load_layout(gds_path: str):
    """Return (layout, top_cell, dbu) or raise."""
    import klayout.db as db

    ly = db.Layout()
    ly.read(str(gds_path))
    return ly, ly.top_cell(), ly.dbu


def inspect_geometry(
    gds_path: str,
    layers: list[tuple[int, int]] | None = None,
    box_um: list[float] | None = None,
    spacing_layers: list[tuple[int, int]] | None = None,
) -> dict[str, Any]:
    """Inspect a GDS file's geometry.

    Args:
        gds_path: path to a GDS/OAS file.
        layers: optional ``[[layer,datatype],...]`` filter for the census
            and box query; ``None`` inspects every layer present.
        box_um: optional ``[x1, y1, x2, y2]`` region (microns).  When
            given, every shape intersecting the box is reported with its
            own bbox in microns — the "measure" primitive.
        spacing_layers: optional ``[[l1,d1],[l2,d2]]`` pair; when given,
            the minimum edge-to-edge spacing between the two layers is
            measured over the whole top cell.

    Returns a dict with ``status`` (``ok``/``unavailable``/``error``),
    top-cell and hierarchy facts, a per-layer census, optional ``shapes``
    (box query) and optional ``spacing`` (pair query).
    """
    try:
        import klayout.db as db
    except ImportError:
        return {"status": "unavailable",
                "reason": "klayout python module not installed"}
    gds = Path(gds_path)
    if not gds.is_file():
        return {"status": "error", "reason": f"no such file: {gds}"}
    try:
        ly, top, dbu = _load_layout(gds)
    except Exception as exc:
        return {"status": "error", "reason": f"cannot read {gds.name}: {exc}"}

    # dbu is microns per database unit → db_units × dbu = microns
    um = float(ly.dbu)
    layer_filter = {tuple(x) for x in layers} if layers else None

    # One recursive pass over the whole hierarchy (absolute coords) feeds
    # both the census and the optional box query — top.shapes() alone
    # misses everything inside instantiated subcells.
    census: dict[str, dict[str, Any]] = {}
    measured: list[dict[str, Any]] = []
    q = None
    if box_um is not None:
        if len(box_um) != 4:
            return {"status": "error",
                    "reason": "box_um must be [x1, y1, x2, y2]"}
        x1, y1, x2, y2 = [v / um for v in box_um]  # µm → db units
        q = db.Box(x1, y1, x2, y2)

    it = db.RecursiveShapeIterator(ly, top, list(ly.layer_indexes()))
    for _ in it.each():
        li = it.layer()
        info = ly.get_info(li)
        key = (info.layer, info.datatype)
        if layer_filter is not None and key not in layer_filter:
            continue
        s = it.shape()
        tag = f"{info.layer}/{info.datatype}"
        c = census.get(tag)
        if c is None:
            c = census[tag] = {"shapes": 0, "area_db": 0.0, "_bb": None}
        c["shapes"] += 1
        c["area_db"] += _shape_area_db(s, db)
        b = s.bbox()
        if not b.empty():
            if c["_bb"] is None:
                c["_bb"] = db.Box(b.left, b.bottom, b.right, b.top)
            else:
                c["_bb"] = db.Box(min(c["_bb"].left, b.left),
                                  min(c["_bb"].bottom, b.bottom),
                                  max(c["_bb"].right, b.right),
                                  max(c["_bb"].top, b.top))
        if q is not None:
            sb = s.bbox()
            if not sb.empty() and sb.overlaps(q):
                measured.append({
                    "layer": tag,
                    "bbox_um": [
                        round(sb.left * um, 4), round(sb.bottom * um, 4),
                        round(sb.right * um, 4), round(sb.top * um, 4)],
                    "w_um": round((sb.right - sb.left) * um, 4),
                    "h_um": round((sb.top - sb.bottom) * um, 4),
                    "area_um2": round(_shape_area_db(s, db) * um * um, 4),
                })

    layers_out: dict[str, dict[str, Any]] = {}
    for tag, c in census.items():
        bb = c.pop("_bb")
        layers_out[tag] = {
            "shapes": c["shapes"],
            "area_um2": round(c["area_db"] * um * um, 4),
            "bbox_um": _box_um(bb, um),
        }

    out: dict[str, Any] = {
        "status": "ok",
        "gds": str(gds),
        "top_cell": top.name,
        "cell_count": ly.cells(),
        "dbu_um": dbu,
        "layers": layers_out,
    }

    if box_um is not None:
        measured.sort(key=lambda d: (d["layer"], d["bbox_um"]))
        out["shapes"] = measured[:500]
        out["shapes_truncated"] = len(measured) > 500

    # --- spacing query: min edge-to-edge gap between two layers -----------
    if spacing_layers is not None:
        if len(spacing_layers) != 2:
            return {**out, "status": "error",
                    "reason": "spacing_layers must be [[l1,d1],[l2,d2]]"}
        (l1, d1), (l2, d2) = spacing_layers
        li1 = ly.find_layer(l1, d1)
        li2 = ly.find_layer(l2, d2)
        if li1 is None or li2 is None:
            missing = []
            if li1 is None:
                missing.append(f"{l1}/{d1}")
            if li2 is None:
                missing.append(f"{l2}/{d2}")
            out["spacing"] = {"status": "unavailable",
                              "reason": f"layer(s) not in layout: {missing}"}
        else:
            r1 = db.Region(top.begin_shapes_rec(li1))
            r2 = db.Region(top.begin_shapes_rec(li2))
            r1.merge()
            r2.merge()
            # separation_check(other, d): EdgePairs of edges closer than d;
            # EdgePair.distance() is the exact edge-to-edge gap in dbu.
            # Probe with the layout diagonal so every approach is captured.
            bb = top.bbox()
            diag = int(((bb.right - bb.left) ** 2
                        + (bb.top - bb.bottom) ** 2) ** 0.5) + 1
            eps = r1.separation_check(r2, diag)
            dists = [p.distance() for p in eps] if len(eps) else []
            out["spacing"] = {
                "layers": [f"{l1}/{d1}", f"{l2}/{d2}"],
                "pairs_within_diag": len(dists),
                "min_gap_um": round(min(dists) * um, 4) if dists else None,
                "note": "min edge-to-edge distance over whole top cell",
            }

    return out


def _shape_area_db(s, db) -> float:
    """Area of any klayout Shape in database units (0 for text/inst)."""
    try:
        if s.is_polygon() and s.polygon is not None:
            return float(s.polygon.area())
        if s.is_box():
            b = s.box  # property, not a method
            return float((b.right - b.left) * (b.top - b.bottom))
        if s.is_path():
            path = s.path
            poly = getattr(path, "polygon", None)
            poly = poly() if callable(poly) else poly
            if poly is not None:
                return float(poly.area())
    except Exception:
        return 0.0
    return 0.0


def _box_um(bb, um: float) -> list[float] | None:
    if bb is None or bb.empty():
        return None
    return [round(bb.left * um, 4), round(bb.bottom * um, 4),
            round(bb.right * um, 4), round(bb.top * um, 4)]
