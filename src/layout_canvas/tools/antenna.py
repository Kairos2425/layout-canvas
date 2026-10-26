"""Process antenna (plasma damage) check 鈥?per-net gate-area ratio.

Uses KLayout's ``LayoutToNetlist`` purely for connectivity: for every
rule layer X a fresh netlist is extracted with connectivity truncated at
X in the stack order (poly鈫抣icon鈫抣i1鈫抦con鈫抦et1鈫抳ia1鈫掆€︹啋met5), matching
the process reality that X's plasma etch happens before upper layers
exist.

The verdict is computed directly from the PDK rule

    EGAR = EA / A_gate 鈭?K路A_diode          (diode bonus NOT applied)

where EA is the effective antenna area of layer X on a gate-bearing net
(vertical layers: perimeter 脳 film thickness FLT; horizontal layers:
cut area), ``A_gate`` is the poly鈭ヾiff area on the same net, and
``A_diode`` is the AntennaDiode area ``(diff|tap) 鈭?poly 鈭?pshort``
connected to the net. Nets touching ``pshort`` (a substrate tap outside
the well 鈥?rule .ar.1 "antenna short") are exempt and counted.

``antenna_check`` is additionally run **without** diode specs as a
cross-check: on nets carrying neither diode nor pshort shapes its
violation set must equal the computed violation set, otherwise the layer
reports an internal error rather than a silently chosen verdict. Its
diode-factor semantics (``K路A_diode`` scales the gate-area denominator,
verified empirically 鈥?see ``tests/test_antenna.py``) do not implement
the subtractive PDK form and are therefore not used for the verdict.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from layout_canvas.tools.extract import _descriptor, _recipe_for

# Conductor stack in process order. Adjacent entries connect; the check
# for layer X builds connectivity only up to and including X.
_STACK = (
    "poly", "licon", "li1", "mcon", "met1", "via1", "met2", "via2",
    "met3", "via3", "met4", "via4", "met5",
)
# Needed for gate/diode/pshort derivation but not part of the etch-order
# connectivity (well_n feeds the pshort derivation only).
_DERIVATION_ROLES = ("diff", "tap", "well_n")

# SkyWater sky130 PDK docs, "Antenna Rules", Table Ig (S8P12-10R*,
# S8PIR-10R, S8PF-10R*):
# https://skywater-pdk.readthedocs.io/en/main/rules/antenna.html
# Diode bonus (2200 for metals) intentionally omitted 鈥?conservative.
_BUILTIN_ANTENNA: dict[str, dict[str, Any]] = {
    "sky130": {
        "source": "SkyWater sky130 antenna rules, Table Ig "
                  "(skywater-pdk.readthedocs.io/en/main/rules/antenna.html)",
        "layers": {
            "poly": {"type": "vertical", "thickness": 0.180,
                     "max_ratio": 50, "k": None},
            "licon": {"type": "horizontal", "max_ratio": 3, "k": None},
            "li1": {"type": "vertical", "thickness": 0.100,
                    "max_ratio": 75, "k": 450},
            "mcon": {"type": "horizontal", "max_ratio": 3, "k": 18},
            "met1": {"type": "vertical", "thickness": 0.350,
                     "max_ratio": 400, "k": 400},
            "via1": {"type": "horizontal", "max_ratio": 6, "k": 36},
            "met2": {"type": "vertical", "thickness": 0.350,
                     "max_ratio": 400, "k": 400},
            "via2": {"type": "horizontal", "max_ratio": 6, "k": 36},
            "met3": {"type": "vertical", "thickness": 0.800,
                     "max_ratio": 400, "k": 400},
            "via3": {"type": "horizontal", "max_ratio": 6, "k": 36},
            "met4": {"type": "vertical", "thickness": 0.800,
                     "max_ratio": 400, "k": 400},
            "via4": {"type": "horizontal", "max_ratio": 6, "k": 36},
            "met5": {"type": "vertical", "thickness": 1.200,
                     "max_ratio": 400, "k": 400},
        },
    },
}


def _rules_for(tech: str) -> dict[str, Any] | None:
    """Antenna rule table 鈥?descriptor ``antenna`` section first, then
    the built-in table. ``None`` = no table: callers report
    ``unavailable`` rather than guessing numbers."""
    pdk = _descriptor(tech)
    if pdk is not None and pdk.antenna is not None:
        return pdk.antenna
    return _BUILTIN_ANTENNA.get(tech)


def _result(status: str, reason: str = "",
            rule_source: str | None = None,
            layers: list | None = None, violations_total: int = 0,
            exempt_nets: int = 0, notes: list | None = None
            ) -> dict[str, Any]:
    return {
        "status": status,
        "reason": reason,
        "rule_source": rule_source,
        "layers": layers or [],
        "violations_total": violations_total,
        "exempt_nets": exempt_nets,
        "notes": notes or [],
    }


def _build_l2n(db, ly, top, recipe: dict[str, Any], upto: int):
    """Fresh LayoutToNetlist with connectivity up to ``_STACK[upto]``.

    Returns ``(l2n, idx)`` where ``idx`` maps region name 鈫?internal
    layer index for ``polygons_of_net`` / ``antenna_check``.
    """
    l2n = db.LayoutToNetlist(db.RecursiveShapeIterator(ly, top, []))
    present: dict[str, Any] = {}
    deriv: dict[str, Any] = {}
    names: list[str] = []

    def region(role: str):
        spec = recipe.get(role)
        if not spec:
            return None
        names.append(role)
        return l2n.make_layer(ly.layer(spec[0], spec[1]), role)

    for role in _STACK[: upto + 1]:
        r = region(role)
        if r is not None:
            present[role] = r
    for role in _DERIVATION_ROLES:
        if role not in present:
            deriv[role] = region(role)
    # NB: an empty Region is falsy -- never select these with "or".
    rpoly = present.get("poly")
    rdiff = deriv.get("diff", present.get("diff"))
    rtap = deriv.get("tap", present.get("tap"))
    rnwell = deriv.get("well_n", present.get("well_n"))

    # Derived regions BEFORE connects 鈥?booleans must see raw layers.
    rgate = rpoly & rdiff
    l2n.register(rgate, "gate")
    names.append("gate")
    rpshort = ((rtap - rpoly) - rnwell
               if rtap is not None and rnwell is not None
               else db.Region())
    # pshort registers BEFORE diode: the lazy boolean must see
    # the registered region, not an unregistered expression.
    l2n.register(rpshort, "pshort")
    names.append("pshort")
    rdiode = (rdiff | rtap if rtap is not None else rdiff) - rpoly - rpshort
    l2n.register(rdiode, "diode")
    names.append("diode")

    for r in present.values():
        l2n.connect(r, r)
    roles = [r for r in _STACK[: upto + 1] if r in present]
    for a, b in zip(roles, roles[1:], strict=False):
        l2n.connect(present[a], present[b])
    # gate rides on the poly net; diode/pshort join through the contact.
    # Raw diff is deliberately NOT connected to licon 鈥?it would bridge
    # S/D segments that the contact was never meant to merge.
    if "poly" in present:
        l2n.connect(rgate, present["poly"])
    if "licon" in present:
        l2n.connect(rdiode, present["licon"])
        l2n.connect(rpshort, present["licon"])

    l2n.extract_netlist()
    idx = {n: l2n.layer_index(n) for n in names}
    return l2n, idx


def _cluster_representatives(nl) -> list[Any]:
    """One ``Net`` per physical cluster.

    A cluster spanning hierarchy appears as a net in several circuits;
    the top-circuit member is preferred, falling back to a member with
    no upward pins (fully internal cluster)."""
    circuits = []
    try:
        top = nl.top_circuit()
    except Exception:
        top = None
    for c in nl.each_circuit():
        if top is None or c.cell_index != top.cell_index:
            circuits.append(c)
    if top is not None:
        circuits.insert(0, top)
    by_cluster: dict[Any, Any] = {}
    for circ in circuits:
        for net in circ.each_net():
            cid = net.cluster_id
            rep = by_cluster.get(cid)
            if rep is None or (rep.pin_count() > 0
                               and net.pin_count() == 0):
                by_cluster[cid] = net
    return list(by_cluster.values())


def _check_layer(db, l2n, idx, spec: dict[str, Any],
                 dbu: float) -> tuple[dict[str, Any], str | None]:
    """Evaluate one rule layer; returns ``(entry, error_reason)``."""
    name = spec["name"]
    vertical = spec["type"] == "vertical"
    max_ratio = spec["max_ratio"]
    k = spec.get("k")
    factor = spec["thickness"] if vertical else 1.0  # perim / area
    entry = {"layer": name, "type": spec["type"], "max_ratio": max_ratio,
             "k": k, "worst_ratio": None, "violations": 0,
             "exempt_nets": 0, "checked": False, "note": ""}

    x_idx = idx.get(name)
    if x_idx is None:
        entry["note"] = "layer not in extraction recipe"
        return entry, None

    nl = l2n.netlist()
    gate_idx, diode_idx, pshort_idx = (
        idx.get("gate"), idx.get("diode"), idx.get("pshort"))

    # (net, A_gate, exempt, A_diode, raw_ratio, violating, region_on_x)
    nets: list[tuple[Any, float, bool, float, float, bool, Any]] = []
    any_x = False
    for net in _cluster_representatives(nl):
        g = l2n.polygons_of_net(net, gate_idx, True)
        if g is None or g.is_empty():
            continue
        a_gate = g.area() * dbu * dbu
        ps = (l2n.polygons_of_net(net, pshort_idx, True)
              if pshort_idx is not None else None)
        exempt = bool(ps is not None and not ps.is_empty())
        d = l2n.polygons_of_net(net, diode_idx, True)
        a_diode = (d.area() * dbu * dbu) if d is not None else 0.0
        xr = l2n.polygons_of_net(net, x_idx, True)
        if xr is None or xr.is_empty():
            eff, reg_x = 0.0, None
        else:
            any_x = True
            eff = (xr.perimeter() * dbu * factor if vertical
                   else xr.area() * dbu * dbu * factor)
            reg_x = xr
        raw = eff / a_gate
        egar = raw - (k or 0.0) * a_diode
        nets.append((net, a_gate, exempt, a_diode, raw,
                     (not exempt) and egar > max_ratio, reg_x))

    if not any_x:
        entry["note"] = "no shapes on this layer"
        return entry, None
    entry["checked"] = True
    if not nets:
        entry["note"] = "no gate-bearing nets"
        return entry, None

    active = [n for n in nets if not n[2]]
    worst = max((n[4] for n in active), default=0.0)
    entry["worst_ratio"] = float(f"{worst:.4g}")
    entry["violations"] = sum(1 for n in active if n[5])
    entry["exempt_nets"] = sum(1 for n in nets if n[2])

    # Cross-check against antenna_check WITHOUT diode specs: on nets
    # carrying neither diode nor pshort shapes the violating-net sets
    # must agree; a disagreement is reported, never silently resolved.
    a_f, p_f = (0.0, factor) if vertical else (factor, 0.0)
    kl_region = l2n.antenna_check(
        l2n.polygons_by_index(gate_idx), 1.0, 0.0,
        l2n.polygons_by_index(x_idx), a_f, p_f, max_ratio, [])
    kl_clean: set[Any] = set()
    unmapped = 0
    for sh in kl_region.each():
        probe = db.Region()
        probe.insert(sh)
        hit = next((n for n in nets
                    if n[6] is not None and not (n[6] & probe).is_empty()),
                   None)
        if hit is None:
            unmapped += 1
        elif not hit[2] and hit[3] == 0.0:
            kl_clean.add(hit[0].cluster_id)
    my_clean = {n[0].cluster_id for n in nets
                if not n[2] and n[3] == 0.0 and n[5]}
    err = None
    if unmapped or kl_clean != my_clean:
        err = (f"{name}: antenna_check cross-check disagrees "
               f"(klayout={len(kl_clean)} viol nets +{unmapped} unmapped "
               f"shapes, computed={len(my_clean)})")
    return entry, err


def antenna_check(gds_path: str | Path, tech: str = "sky130"
                  ) -> dict[str, Any]:
    """Run the per-layer antenna-ratio check on ``gds_path``.

    Returns ``{status, reason, rule_source, layers, violations_total,
    exempt_nets, notes}``; status 鈭?pass | fail | unavailable |
    not_applicable | error. Never reports pass without evidence.
    """
    try:
        import klayout.db as db
    except ImportError:
        return _result("unavailable", "klayout python module not installed")
    rules = _rules_for(tech)
    if rules is None:
        return _result("unavailable",
                       f"no antenna rule table for tech {tech!r}")
    recipe = _recipe_for(tech)
    if recipe is None:
        return _result("unavailable",
                       f"no extraction recipe for tech {tech!r}")
    if not recipe.get("poly") or not recipe.get("diff"):
        return _result("unavailable",
                       f"extraction recipe for {tech!r} lacks "
                       "poly/diff 鈥?cannot derive MOS gates")
    gds = Path(gds_path)
    if not gds.is_file():
        return _result("unavailable", f"GDS not found: {gds}")

    ly = db.Layout()
    try:
        ly.read(str(gds))
    except Exception as exc:
        return _result("error", f"cannot read {gds.name}: {exc}")
    tops = list(ly.top_cells())
    if not tops:
        return _result("error", "GDS has no top cell")
    top = tops[0]
    dbu = ly.dbu

    # Gate = poly & diff 鈥?one global check decides applicability.
    poly_idx = ly.layer(*recipe["poly"])
    diff_idx = ly.layer(*recipe["diff"])
    gate_area = db.Region(db.RecursiveShapeIterator(
        ly, top, [poly_idx])) & db.Region(
        db.RecursiveShapeIterator(ly, top, [diff_idx]))
    if gate_area.is_empty():
        return _result("not_applicable",
                       "no MOS gates (poly鈭ヾiff) 鈥?nothing to check",
                       rule_source=rules["source"])

    notes = [
        "diode bonus (2200 for metals) not applied 鈥?conservative",
        "KLayout antenna_check diode factor scales the gate-area "
        "denominator, not the PDK's subtractive EGAR term; the verdict "
        "uses EGAR = EA/A_gate 鈭?K路A_diode computed per net and "
        "antenna_check only cross-checks diode-free nets",
    ]
    layers_out: list[dict[str, Any]] = []
    errors: list[str] = []
    violations_total = 0
    exempt_nets = 0

    seen = set()
    for name in [r for r in _STACK if r in rules["layers"]]:
        seen.add(name)
        spec = rules["layers"][name]
        if recipe.get(name) is None:
            layers_out.append({
                "layer": name, "type": spec["type"],
                "max_ratio": spec["max_ratio"], "k": spec.get("k"),
                "worst_ratio": None, "violations": 0, "exempt_nets": 0,
                "checked": False,
                "note": "layer not in extraction recipe"})
            continue
        l2n, idx = _build_l2n(db, ly, top, recipe, _STACK.index(name))
        entry, err = _check_layer(
            db, l2n, idx, {"name": name, **spec}, dbu)
        layers_out.append(entry)
        violations_total += entry["violations"]
        exempt_nets = max(exempt_nets, entry.get("exempt_nets", 0))
        if err:
            errors.append(err)
    for name in rules["layers"]:
        if name not in seen:
            layers_out.append({
                "layer": name,
                "type": rules["layers"][name].get("type"),
                "max_ratio": rules["layers"][name].get("max_ratio"),
                "k": rules["layers"][name].get("k"),
                "worst_ratio": None, "violations": 0, "exempt_nets": 0,
                "checked": False,
                "note": "layer not in the conductor stack order"})

    if errors:
        return _result("error", "; ".join(errors),
                       rule_source=rules["source"], layers=layers_out,
                       violations_total=violations_total,
                       exempt_nets=exempt_nets, notes=notes)
    if violations_total:
        worst = max(layers_out, key=lambda e: e["violations"])
        return _result(
            "fail",
            f"{worst['layer']}: worst EA/A_gate "
            f"{worst['worst_ratio']} > {worst['max_ratio']} "
            f"({violations_total} violating net/layer pair(s))",
            rule_source=rules["source"], layers=layers_out,
            violations_total=violations_total, exempt_nets=exempt_nets,
            notes=notes)
    return _result("pass", "", rule_source=rules["source"],
                   layers=layers_out, violations_total=0,
                   exempt_nets=exempt_nets, notes=notes)
