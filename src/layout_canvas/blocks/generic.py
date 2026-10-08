"""Descriptor-driven generic blocks — the last mile for external PDKs.

Every non-built-in PDK loaded from a ``*.pdk.json`` descriptor
(``LAYOUT_CANVAS_PDK_DIR`` / ``LAYOUT_CANVAS_PDKS``) gets up to three
parametric generators — ``<pdk>.gen_diff_pair``, ``<pdk>.gen_current_mirror``
and ``<pdk>.gen_guard_ring`` — built purely from the descriptor's
``extract.roles`` layer contract and ``drc.rules`` values. The ``gen_``
prefix keeps them clear of any future native blocks for the same PDK.

Registration is capability-gated, never aspirational: a block whose
required roles are absent is simply not registered, so the palette only
lists blocks that can actually build. Sub-capabilities drop individual
parameter choices or geometry instead — a missing ``well_n`` removes the
``pmos`` choice, a missing ``tap``/implant path drops the substrate ring —
and the degradation is named in ``spec.constraints``. When the descriptor
carries no ``drc`` section, parameter bounds fall back to conservative
0.5 µm width / 0.3 µm space defaults and the constraint list says the
rules are unavailable.

The geometry follows the proven sky130 recipes with every layer resolved
through the role contract and every size derived from descriptor rules
(falling back to the recipe constants, not sky130-specific values). Bulk
ties honour the same extraction contract the tools implement: a dedicated
``tap`` layer when present, else implant-derived taps
(``diff & nsdm`` inside the well / ``diff & psdm`` outside it).
"""

from __future__ import annotations

from typing import Any

import gdsfactory as gf

from layout_canvas.blocks import base
from layout_canvas.blocks.sky130.geom import rect, snap
from layout_canvas.ir.model import BlockSpec, ParamSpec, PortSpec
from layout_canvas.pdk.descriptor import PDK, builtin_pdk_names

# pdk name -> block names this module generated (for removal on env rescan)
_GENERATED: dict[str, set[str]] = {}
# pdk name -> registration count; makes regenerated cell names unique
_EPOCHS: dict[str, int] = {}
# pdk name -> last registration error; a broken generator must never kill
# PDK loading (same fail-closed discipline as the descriptor scan itself)
GENERATION_ERRORS: dict[str, str] = {}

def generated_block_names(pdk_name: str) -> tuple[str, ...]:
    """The ``gen_*`` blocks this module registered for one PDK — surfaced
    in ``pdk list``/``/api/pdks``/``probe_environment`` so a missing
    generator is visible, not silent."""
    return tuple(sorted(_GENERATED.get(pdk_name, ())))


def generation_errors() -> dict[str, str]:
    """per-PDK generator failures — same diagnostics discipline as
    ``external_pdk_errors()``."""
    return dict(GENERATION_ERRORS)


# Contact-stack + active roles a MOS cell cannot be built without.
_MOS_ROLES = ("diff", "poly", "licon", "li1", "mcon", "met1")
# The guard ring ties substrate/well through a dedicated tap layer.
_GUARD_RING_ROLES = ("tap", "licon", "li1", "mcon", "met1")


def _roles(pdk: PDK) -> dict[str, Any]:
    return (pdk.extract or {}).get("roles") or {}


def _has(pdk: PDK, *roles: str) -> bool:
    r = _roles(pdk)
    return all(r.get(role) for role in roles)


def _rule(pdk: PDK, layer: Any, kind: str, default: float) -> float:
    if layer is None:
        return default
    for lay, checks in (pdk.drc or {}).get("rules", {}).items():
        if tuple(lay) == tuple(layer):
            for k, v in checks:
                if k == kind:
                    return float(v)
    return default


def _enclosure(pdk: PDK, cut_layer: Any, default: float) -> float:
    if cut_layer is None:
        return default
    for _label, cut, _outers, enc in (pdk.drc or {}).get("enclosure", []):
        if tuple(cut) == tuple(cut_layer):
            return float(enc)
    return default


def _leaf_model(pdk: PDK, polarity: str) -> str | None:
    """First leaf subckt name of the given polarity — the SPICE model name."""
    leaf = (pdk.extract or {}).get("leaf_devices", {})
    return next(
        (name for name, (_cls, pol) in leaf.items() if pol == polarity), None
    )


def _can_ptap(pdk: PDK) -> bool:
    """A p-substrate tap is drawable: dedicated tap layer, or diff+psdm."""
    return _has(pdk, "tap") or _has(pdk, "diff", "psdm")


def _can_ntap(pdk: PDK) -> bool:
    """An n-well tie is drawable: dedicated tap layer, or diff+nsdm.

    The implant path additionally needs ``psdm`` — the extractor only
    derives implant taps once psdm shapes exist on the layout (the pmos
    devices themselves provide them).
    """
    return _has(pdk, "tap") or _has(pdk, "diff", "nsdm", "psdm")


def _can_pmos(pdk: PDK) -> bool:
    """PMOS devices need a well and a way to tie it."""
    return _has(pdk, "well_n") and _can_ntap(pdk)


class _D:
    """Rule-derived dimensions for one PDK — all units µm.

    Defaults are the reference-recipe values (the same numbers the verified
    sky130 generators use); descriptor rules override them. Conductor
    width/space lookups default to conservative 0.5/0.3 when the
    descriptor has no rule for that layer — flagged via ``no_rules`` on the
    spec constraints.
    """

    def __init__(self, pdk: PDK) -> None:
        r = _roles(pdk)
        self.no_rules = pdk.drc is None
        # bound-driving rules actually missing (partial drc sections count)
        self.missing_rules: set[str] = set()

        def rw(role: str, kind: str, default: float) -> float:
            lay = r.get(role)
            got = lay is not None and any(
                k == kind
                for other, checks in (pdk.drc or {}).get("rules", {}).items()
                if tuple(other) == tuple(lay)
                for k, _v in checks)
            if lay is not None and not got:
                self.missing_rules.add(f"{role}.{kind}")
            return _rule(pdk, lay, kind, default)

        # cut/contact sizes: manufactured fixed contacts; the recipe's own
        # sizes are the fallback (contact layers rarely carry width rules)
        self.licon = rw("licon", "width", 0.17)
        self.mcon = rw("mcon", "width", 0.13)
        # conductor rules
        self.li_w = rw("li1", "width", 0.5)
        self.li_s = rw("li1", "space", 0.3)
        self.met_w = rw("met1", "width", 0.5)
        self.met_s = rw("met1", "space", 0.3)
        self.diff_w = rw("diff", "width", 0.5)
        self.diff_s = rw("diff", "space", 0.3)
        self.poly_w = rw("poly", "width", 0.5)
        self.tap_w = rw("tap", "width", 0.5)
        # enclosure values from drc.enclosure (cut layer -> enclosing layer)
        self.li_enc = _enclosure(pdk, r.get("licon"), 0.06)
        self.mcon_enc = _enclosure(pdk, r.get("mcon"), 0.03)
        # registration epoch -> cell-name suffix; a descriptor rescan
        # produces a fresh Block while the old cell may still be referenced
        # by a live top, so regenerated cells get a unique name instead of
        # colliding in the process-wide KCLayout
        self.sfx = ""

        self.licon_h = self.licon / 2
        self.mcon_h = self.mcon / 2
        self.li_h = max(0.15, self.li_w / 2)
        self.met_h = max(0.19, self.met_w / 2)
        # S/D strap half-height + pitch (strap spacing clears the strap
        # height plus met1 min-space — the failure the recipe comments cite)
        self.strap_hh = max(0.24, self.met_w / 2)
        self.strap_pitch = 2 * self.strap_hh + self.met_s + 0.04
        # S/D segment width: must fit the contact column with margin
        self.seg_w = max(1.2, self.licon + 0.5)
        # fingered-rail minimum channel width: contact + 2 * (enc + margin)
        self.w_min = max(self.diff_w, self.licon + 2 * self.li_enc + 0.13)
        self.l_min = self.poly_w
        # contact pitch inside a segment / along a ring rail
        self.cont_pitch = 2 * self.licon + 0.02
        # guard-ring rail width
        self.ring_rw = max(0.5, self.tap_w,
                           self.licon + 2 * self.li_enc + 0.15)


def _port(c: gf.Component, pdk: PDK, name: str, layer: Any,
          center: tuple[float, float], width: float, orientation: int) -> None:
    """``add_port`` honouring the descriptor's ``pin_purpose`` datatype."""
    cx, cy = snap(center[0]), snap(center[1])
    c.add_port(
        name=name,
        center=(cx, cy),
        width=snap(width, 0.01),
        orientation=orientation,
        layer=layer,
        port_type="electrical",
    )
    try:
        c.add_label(text=name, position=(cx, cy),
                    layer=pdk.pin_label_layer(layer))
    except Exception:
        pass


def _constraints(pdk: PDK, base_list: list[str], d: _D,
                 extra: list[str] | None = None) -> list[str]:
    out = list(base_list) + ["generic_generator_descriptor"]
    if d.no_rules:
        out.append("rules unavailable — verify with DRC")
    elif d.missing_rules:
        out.append("partial rules unavailable — verify with DRC")
    out.extend(extra or [])
    return out


# ---------------------------------------------------------------------------
# gen_diff_pair — A/B interdigitated pair, ports: inp inn outp outn tail vss
# ---------------------------------------------------------------------------

def _diff_pair_nets(drain_a: str, drain_b: str, source: str,
                    pattern: list[str]) -> list[str]:
    """Segment ownership: between different-device fingers -> shared source,
    between same-device fingers -> that drain, ends -> boundary drain."""
    seg_net: list[str] = []
    for j in range(len(pattern) + 1):
        if j == 0:
            seg_net.append(drain_a if pattern[0] == "A" else drain_b)
        elif j == len(pattern):
            seg_net.append(drain_a if pattern[-1] == "A" else drain_b)
        else:
            seg_net.append(
                source if pattern[j - 1] != pattern[j]
                else (drain_a if pattern[j] == "A" else drain_b)
            )
    return seg_net


def _finger_rail(c: gf.Component, pdk: PDK, d: _D, *,
                 pattern: list[str], width: float, length: float,
                 drain_a: str, drain_b: str, source: str,
                 port_x: dict[str, float], type_: str,
                 strap_ext: dict[str, float] | None = None) -> dict[str, Any]:
    """Draw the shared fingered-transistor core both blocks reuse.

    Returns strap/segment geometry the caller finishes (gate straps, ring,
    ports) — identical structure to the verified sky130 recipe.
    """
    r = _roles(pdk)
    diff, poly = r["diff"], r["poly"]
    licon, li1, mcon, met1 = r["licon"], r["li1"], r["mcon"], r["met1"]
    implant = r.get("psdm") if type_ == "pmos" else r.get("nsdm")

    finger_pitch = snap(length + d.seg_w)
    total_width = finger_pitch * len(pattern)
    diff_h = snap(width)
    diff_bottom = 3.0

    if type_ == "pmos":
        # p-active = diff & well & psdm — no well, no device.
        rect(c, r["well_n"], -0.75, 2.4,
             total_width + 0.5, diff_bottom + diff_h + 0.5)
    rect(c, diff, 0, diff_bottom, total_width, diff_bottom + diff_h)
    if implant is not None:
        rect(c, implant, -0.1, diff_bottom - 0.1,
             total_width + 0.1, diff_bottom + diff_h + 0.1)

    half_l = length / 2
    poly_y0 = snap(diff_bottom - 0.2)
    poly_y1 = snap(diff_bottom + diff_h + 0.2)
    for i in range(len(pattern)):
        x = snap(i * finger_pitch + finger_pitch / 2)
        rect(c, poly, x - half_l, poly_y0, x + half_l, poly_y1)

    finger_x = [snap(i * finger_pitch + finger_pitch / 2)
                for i in range(len(pattern))]
    edges = [0.0] + [x + half_l for x in finger_x]
    starts = [x - half_l for x in finger_x] + [total_width]
    segs = list(zip(edges, starts))
    seg_net = _diff_pair_nets(drain_a, drain_b, source, pattern)

    # Per-segment contact column + li1 riser up to its met1 strap; straps
    # spaced by strap_pitch so neighbours never merge.
    strap_y = {drain_a: diff_bottom + diff_h + d.strap_hh + 0.31}
    strap_y[source] = strap_y[drain_a] + d.strap_pitch
    strap_y[drain_b] = strap_y[source] + d.strap_pitch
    seg_min = d.licon + 0.23  # skip slivers that cannot host a contact
    for (sx0, sx1), net in zip(segs, seg_net):
        if sx1 - sx0 < seg_min:
            continue
        cx = snap((sx0 + sx1) / 2)
        y_top = strap_y[net]
        n_con = max(1, int((sx1 - sx0 - 0.2) / d.cont_pitch) + 1)
        inset = d.licon_h + 0.125
        for k in range(n_con):
            kx = snap(
                sx0 + inset + (sx1 - sx0 - 2 * inset) * (k / max(1, n_con - 1))
                if n_con > 1 else cx
            )
            rect(c, licon, kx - d.licon_h, diff_bottom + diff_h / 2 - d.licon_h,
                 kx + d.licon_h, diff_bottom + diff_h / 2 + d.licon_h)
        rect(c, li1, sx0 + d.li_enc, diff_bottom + diff_h / 2 - d.li_h,
             sx1 - d.li_enc, diff_bottom + diff_h / 2 + d.li_h)
        rect(c, li1, cx - d.li_h, diff_bottom + diff_h / 2,
             cx + d.li_h, y_top + d.mcon_h + d.mcon_enc + 0.005)
        rect(c, mcon, cx - d.mcon_h, y_top - d.mcon_h,
             cx + d.mcon_h, y_top + d.mcon_h)

    # Horizontal met1 straps — each reaches its port pad so a single-segment
    # net never leaves the pin on a floating island. ``strap_ext`` grows a
    # strap's right edge continuously into a rail (the mirror's source
    # strap merges into the ring — a detached bridge would leave the
    # segments on a second, anonymous net).
    strap_ext = strap_ext or {}
    for net, y in strap_y.items():
        xs = [snap((s[0] + s[1]) / 2) for s, n in zip(segs, seg_net)
              if n == net and s[1] - s[0] >= seg_min]
        if xs:
            x0 = min(xs) - 0.2
            x1 = max(xs) + 0.2
            if net in port_x:
                x0 = min(x0, port_x[net] - d.strap_hh)
                x1 = max(x1, port_x[net] + d.strap_hh)
            if net in strap_ext:
                x1 = max(x1, strap_ext[net])
            rect(c, met1, x0, y - d.strap_hh, x1, y + d.strap_hh)

    return {
        "total_width": total_width,
        "diff_h": diff_h,
        "diff_bottom": diff_bottom,
        "finger_x": finger_x,
        "strap_y": strap_y,
        "finger_pitch": finger_pitch,
    }


def _tap_square(c: gf.Component, pdk: PDK, kind: str,
                cx: float, cy: float, half: float) -> None:
    """One bulk-tap square: ``kind`` 'p' (substrate) or 'n' (n-well tie).

    Dedicated tap layer when the descriptor has one, else implant-derived:
    ptap = diff & psdm outside the well; ntap = diff & nsdm inside it.
    """
    r = _roles(pdk)
    if r.get("tap") is not None:
        rect(c, r["tap"], cx - half, cy - half, cx + half, cy + half)
    else:
        implant = r["psdm"] if kind == "p" else r["nsdm"]
        rect(c, r["diff"], cx - half, cy - half, cx + half, cy + half)
        rect(c, implant, cx - half - 0.05, cy - half - 0.05,
             cx + half + 0.05, cy + half + 0.05)


def _ring(c: gf.Component, pdk: PDK, d: _D, *,
          gx0: float, gx1: float, gy0: float, gy1: float,
          kind: str, contacts: bool = True,
          metal_only: bool = False) -> None:
    """Rectangular guard ring: tap(+implant)/li1/met1 frame + contacts.

    ``metal_only`` draws the bare met1 frame (the pmos source-strap ring:
    a contacted tap ring would fuse the source to the substrate — the PMOS
    lesson from the sky130 mirror).
    """
    r = _roles(pdk)
    rw = d.ring_rw
    layers_to_draw = [r["met1"]] if metal_only else [r["li1"], r["met1"]]
    if not metal_only:
        if r.get("tap") is not None:
            layers_to_draw.insert(0, r["tap"])
        else:
            implant = r["psdm"] if kind == "p" else r["nsdm"]
            layers_to_draw.insert(0, r["diff"])
            # implant frame, slightly wider than the diff frame
            m = 0.08
            for x0, y0, x1, y1 in (
                (gx0 - m, gy0 - m, gx0 + rw + m, gy1 + m),
                (gx1 - rw - m, gy0 - m, gx1 + m, gy1 + m),
                (gx0 - m, gy0 - m, gx1 + m, gy0 + rw + m),
                (gx0 - m, gy1 - rw - m, gx1 + m, gy1 + m),
            ):
                rect(c, implant, x0, y0, x1, y1)
    for lay in layers_to_draw:
        rect(c, lay, gx0, gy0, gx0 + rw, gy1)          # left rail
        rect(c, lay, gx1 - rw, gy0, gx1, gy1)         # right rail
        rect(c, lay, gx0, gy0, gx1, gy0 + rw)         # bottom rail
        rect(c, lay, gx0, gy1 - rw, gx1, gy1)         # top rail
    if metal_only or not contacts:
        return
    pitch = max(0.5, d.cont_pitch)

    def _contacts(layer: Any, half: float) -> None:
        x = gx0 + rw / 2
        while x <= gx1 - rw:
            for y in (gy0 + rw / 2, gy1 - rw / 2):
                rect(c, layer, x - half, y - half, x + half, y + half)
            x += pitch
        y = gy0 + rw / 2 + pitch
        while y <= gy1 - rw:
            for x in (gx0 + rw / 2, gx1 - rw / 2):
                rect(c, layer, x - half, y - half, x + half, y + half)
            y += pitch

    _contacts(r["licon"], d.licon_h)
    _contacts(r["mcon"], d.mcon_h)


def _spec_diff_pair(pdk: PDK, d: _D, types: list[str],
                    extra_constraints: list[str]) -> BlockSpec:
    choices = types
    return BlockSpec(
        name=f"{pdk.name}.gen_diff_pair",
        pdk=pdk.name,
        level="L1",
        summary="Common-centroid differential pair — generic generator "
                "(descriptor-driven)",
        params=[
            ParamSpec(name="fingers", type="int", default=4, min=2, max=32,
                      description="Fingers per input transistor (ABBA pattern)"),
            ParamSpec(name="width", type="float",
                      default=round(max(2.0, d.w_min), 3), unit="um",
                      min=round(d.w_min, 3), max=10.0,
                      description="Input pair width per finger"),
            ParamSpec(name="length", type="float",
                      default=round(max(0.5, d.l_min), 3), unit="um",
                      min=round(d.l_min, 3), max=5.0,
                      description="Gate length"),
            ParamSpec(name="tail_width", type="float", default=4.0, unit="um",
                      min=1.0, max=20.0,
                      description="Tail current source width (informational — "
                                  "the tail device is a separate cell)"),
            ParamSpec(name="type", type="str", default=choices[0],
                      choices=choices,
                      description="Transistor type"),
        ],
        ports=[
            PortSpec(name="inp", layer="met1", direction="input",
                     tap_layer="met1"),
            PortSpec(name="inn", layer="met1", direction="input",
                     tap_layer="met1"),
            PortSpec(name="outp", layer="met1", direction="output",
                     tap_layer="met1"),
            PortSpec(name="outn", layer="met1", direction="output",
                     tap_layer="met1"),
            PortSpec(name="tail", layer="met1", direction="input",
                     tap_layer="met1"),
            PortSpec(name="vss", layer="met1", direction="inout",
                     tap_layer="met1"),
        ],
        constraints=_constraints(
            pdk,
            ["common_centroid_ABBA", "symmetric_vertical"],
            d, extra_constraints),
        tags=["analog", "diff_pair", "generic"],
    )


def _build_diff_pair(pdk: PDK, d: _D):
    r = _roles(pdk)
    met1 = r["met1"]

    def _build(fingers: int, width: float, length: float,
               tail_width: float, type: str) -> gf.Component:
        c = gf.Component(
            name=f"{pdk.name}_gen_diff_pair_{type}_f{fingers}"
                 f"_w{width}_l{length}{d.sfx}")
        pattern = ["A", "B", "B", "A"] * (fingers // 2)
        tw0 = snap(length + d.seg_w) * len(pattern)
        rail = _finger_rail(
            c, pdk, d, pattern=pattern, width=width, length=length,
            drain_a="outp", drain_b="outn", source="tail",
            port_x={"outp": tw0 * 0.25, "outn": tw0 * 0.75,
                    "tail": tw0 / 2},
            type_=type)
        tw = rail["total_width"]
        diff_h = rail["diff_h"]
        diff_bottom = rail["diff_bottom"]
        finger_x = rail["finger_x"]
        strap_y = rail["strap_y"]

        # Gate strapping: side A on li1, side B through mcon onto met1 so
        # the two inputs can never cross-connect.
        tap_y = diff_bottom - max(0.50 - diff_h / 2, d.li_s - 0.02)
        gap = 2 * max(d.li_h, d.met_h) + 0.17
        gy_a, gy_b = tap_y - gap, tap_y - 2 * gap
        port_y = diff_bottom + diff_h / 2
        gate_xs = {"A": [], "B": []}
        poly = r["poly"]
        for i, side in enumerate(pattern):
            fx = finger_x[i]
            rect(c, poly, fx - 0.2, tap_y - 0.15, fx + 0.2,
                 diff_bottom - 0.005)
            rect(c, r["licon"], fx - d.licon_h, tap_y - d.licon_h,
                 fx + d.licon_h, tap_y + d.licon_h)
            if side == "A":
                rect(c, r["li1"], fx - d.li_h, gy_a, fx + d.li_h,
                     tap_y + 0.15)
            else:
                rect(c, r["li1"], fx - d.li_h, tap_y - 0.15, fx + d.li_h,
                     tap_y + 0.15)
                rect(c, r["mcon"], fx - d.mcon_h, tap_y - d.mcon_h,
                     fx + d.mcon_h, tap_y + d.mcon_h)
                rect(c, met1, fx - d.met_h, gy_b, fx + d.met_h,
                     tap_y + 0.10)
            gate_xs[side].append(fx)
        px = {"A": tw * 0.25, "B": tw * 0.75 + 0.3}
        for side, xs in gate_xs.items():
            if not xs:
                continue
            if side == "A":
                rect(c, r["li1"],
                     min(min(xs), px[side]) - d.li_h, gy_a - d.li_h,
                     max(max(xs), px[side]) + d.li_h, gy_a + d.li_h)
                rect(c, r["mcon"], px[side] - d.mcon_h, gy_a - d.mcon_h,
                     px[side] + d.mcon_h, gy_a + d.mcon_h)
                rect(c, met1, px[side] - d.met_h, gy_a - d.met_h,
                     px[side] + d.met_h, gy_a + d.met_h)
            else:
                rect(c, met1,
                     min(min(xs), px[side]) - d.met_h, gy_b - d.met_h,
                     max(max(xs), px[side]) + d.met_h, gy_b + d.met_h)
            rect(c, met1, px[side] - d.met_h,
                 gy_a if side == "A" else gy_b,
                 px[side] + d.met_h, port_y)

        # Ports + in-cell met1 pads (labels only attach to same-cell metal).
        _port(c, pdk, "inp", met1, (tw * 0.25, port_y), 0.8, 180)
        _port(c, pdk, "inn", met1, (tw * 0.75, port_y), 0.8, 0)
        _port(c, pdk, "outp", met1, (tw * 0.25, strap_y["outp"]), 0.8, 90)
        _port(c, pdk, "outn", met1, (tw * 0.75, strap_y["outn"]), 0.8, 90)
        _port(c, pdk, "tail", met1, (tw / 2, strap_y["tail"]), 0.8, 90)
        for px_, py_ in (
            (tw * 0.25, port_y), (tw * 0.75, port_y),
            (tw * 0.25, strap_y["outp"]),
            (tw * 0.75, strap_y["outn"]),
            (tw / 2, strap_y["tail"]),
        ):
            rect(c, met1, px_ - d.strap_hh, py_ - d.strap_hh,
                 px_ + d.strap_hh, py_ + d.strap_hh)

        if type == "pmos":
            # Bulk = n-well, exported as the 'vss' pin (port-order compat —
            # for pmos it is the well-bias net, typically vdd). An n-tap
            # square inside the well, contacted up to a labeled met1 pad.
            ntx, nty = -0.45, diff_bottom + diff_h / 2
            _tap_square(c, pdk, "n", ntx, nty, 0.15)
            rect(c, r["licon"], ntx - d.licon_h, nty - d.licon_h,
                 ntx + d.licon_h, nty + d.licon_h)
            rect(c, r["li1"], ntx - 0.15, nty - 0.15, ntx + 0.15, nty + 0.15)
            rect(c, r["mcon"], ntx - d.mcon_h, nty - d.mcon_h,
                 ntx + d.mcon_h, nty + d.mcon_h)
            rect(c, met1, ntx - d.met_h, nty - d.met_h,
                 ntx + d.met_h, nty + d.met_h)
            _port(c, pdk, "vss", met1, (ntx, nty), 0.8, 180)
        elif _can_ptap(pdk):
            # nmos: p-substrate tap frame + contacted met1 ring -> 'vss'.
            gx0, gx1 = -0.9, tw + 0.9
            gy0 = min(0.9, gy_b - (d.met_h + d.ring_rw + d.met_s))
            gy1 = strap_y["outn"] + 1.0
            _ring(c, pdk, d, gx0=gx0, gx1=gx1, gy0=gy0, gy1=gy1, kind="p")
            _port(c, pdk, "vss", met1, (tw / 2, gy0 + d.ring_rw / 2),
                  0.8, 270)
        # else: no bulk path for the substrate pin — the spec says so in
        # constraints; 'vss' stays a declared port without geometry.

        return c

    return _build


def _netlist_diff_pair(pdk: PDK):
    def _nl(fingers: int, width: float, length: float,
            tail_width: float, type: str) -> str:
        model = _leaf_model(pdk, "nmos" if type == "nmos" else "pmos")
        pattern = ["A", "B", "B", "A"] * (fingers // 2)
        seg_net = _diff_pair_nets("outp", "outn", "tail", pattern)
        lines = [".subckt gen_diff_pair inp inn outp outn tail vss"]
        for i, side in enumerate(pattern):
            gate = "inp" if side == "A" else "inn"
            lines.append(
                f"M{i + 1} {seg_net[i + 1]} {gate} {seg_net[i]} vss "
                f"{model} w={width}u l={length}u")
        lines.append(".ends")
        return "\n".join(lines)

    return _nl


# ---------------------------------------------------------------------------
# gen_current_mirror — ports: in out gate vss
# ---------------------------------------------------------------------------

def _build_current_mirror(pdk: PDK, d: _D):
    r = _roles(pdk)
    met1 = r["met1"]

    def _build(fingers: int, width: float, length: float,
               type: str) -> gf.Component:
        c = gf.Component(
            name=f"{pdk.name}_gen_current_mirror_{type}_f{fingers}"
                 f"_w{width}_l{length}{d.sfx}")
        pattern = ["A", "B", "B", "A"] * (fingers // 2)
        tw0 = snap(length + d.seg_w) * len(pattern)
        rail = _finger_rail(
            c, pdk, d, pattern=pattern, width=width, length=length,
            drain_a="in", drain_b="out", source="vss",
            port_x={"in": tw0 * 0.25, "out": tw0 * 0.75},
            type_=type,
            # The source strap extends into the right ring rail so the
            # shared source net IS the vss net, not a floating island.
            strap_ext={"vss": tw0 + 0.65})
        tw = rail["total_width"]
        diff_h = rail["diff_h"]
        diff_bottom = rail["diff_bottom"]
        finger_x = rail["finger_x"]
        strap_y = rail["strap_y"]

        # Gate strap: ALL fingers share 'gate' — one li1 strap below the
        # rail ties every gate tap.
        tap_y = diff_bottom - max(0.50 - diff_h / 2, d.li_s - 0.02)
        gap = 2 * max(d.li_h, d.met_h) + 0.17
        gy = tap_y - gap
        port_y = diff_bottom + diff_h / 2
        px_gate = tw * 0.5
        poly = r["poly"]
        for fx in finger_x:
            rect(c, poly, fx - 0.2, tap_y - 0.15, fx + 0.2,
                 diff_bottom - 0.005)
            rect(c, r["licon"], fx - d.licon_h, tap_y - d.licon_h,
                 fx + d.licon_h, tap_y + d.licon_h)
            rect(c, r["li1"], fx - d.li_h, gy, fx + d.li_h, tap_y + 0.15)
        rect(c, r["li1"],
             min(min(finger_x), px_gate) - d.li_h, gy - d.li_h,
             max(max(finger_x), px_gate) + d.li_h, gy + d.li_h)
        rect(c, r["mcon"], px_gate - d.mcon_h, gy - d.mcon_h,
             px_gate + d.mcon_h, gy + d.mcon_h)
        rect(c, met1, px_gate - d.met_h, gy - d.met_h,
             px_gate + d.met_h, gy + d.met_h)
        rect(c, met1, px_gate - d.met_h, gy, px_gate + d.met_h, port_y)

        # Ports + in-cell met1 pads.
        _port(c, pdk, "in", met1, (tw * 0.25, strap_y["in"]), 0.8, 90)
        _port(c, pdk, "out", met1, (tw * 0.75, strap_y["out"]), 0.8, 90)
        _port(c, pdk, "gate", met1, (px_gate, port_y), 0.8, 180)
        for px_, py_ in (
            (tw * 0.25, strap_y["in"]),
            (tw * 0.75, strap_y["out"]),
            (px_gate, port_y),
        ):
            rect(c, met1, px_ - d.strap_hh, py_ - d.strap_hh,
                 px_ + d.strap_hh, py_ + d.strap_hh)

        if type == "pmos":
            # n-well bulk tie to the source ring: an n-tap inside the well
            # with a met1 jumper down to the bottom ring rail (which is the
            # source net itself — bare met1 frame, never a tap ring: a p-tap
            # frame would fuse the source to the substrate).
            ntx = snap(tw - 0.1)
            nty = 2.65
            _tap_square(c, pdk, "n", ntx, nty, 0.15)
            rect(c, r["licon"], ntx - d.licon_h, nty - d.licon_h,
                 ntx + d.licon_h, nty + d.licon_h)
            rect(c, r["li1"], ntx - 0.15, nty - 0.15, ntx + 0.15, nty + 0.15)
            rect(c, r["mcon"], ntx - d.mcon_h, nty - d.mcon_h,
                 ntx + d.mcon_h, nty + d.mcon_h)
            rect(c, met1, ntx - 0.15, 1.15, ntx + 0.15, nty + 0.15)

            gx0, gx1 = -0.9, tw + 0.9
            gy0, gy1 = 0.9, strap_y["out"] + 1.0
            _ring(c, pdk, d, gx0=gx0, gx1=gx1, gy0=gy0, gy1=gy1,
                  kind="n", metal_only=True)
            _port(c, pdk, "vss", met1, (tw / 2, gy0 + d.ring_rw / 2),
                  0.8, 270)
        else:
            gx0, gx1 = -0.9, tw + 0.9
            gy0, gy1 = 0.9, strap_y["out"] + 1.0
            _ring(c, pdk, d, gx0=gx0, gx1=gx1, gy0=gy0, gy1=gy1, kind="p")
            _port(c, pdk, "vss", met1, (tw / 2, gy0 + d.ring_rw / 2),
                  0.8, 270)

        return c

    return _build


def _netlist_current_mirror(pdk: PDK):
    def _nl(fingers: int, width: float, length: float, type: str) -> str:
        model = _leaf_model(pdk, "nmos" if type == "nmos" else "pmos")
        pattern = ["A", "B", "B", "A"] * (fingers // 2)
        seg_net = _diff_pair_nets("in", "out", "vss", pattern)
        lines = [".subckt gen_current_mirror in out gate vss"]
        for i, _side in enumerate(pattern):
            lines.append(
                f"M{i + 1} {seg_net[i + 1]} gate {seg_net[i]} vss "
                f"{model} w={width}u l={length}u")
        lines.append(".ends")
        return "\n".join(lines)

    return _nl


def _spec_current_mirror(pdk: PDK, d: _D, types: list[str],
                         extra_constraints: list[str]) -> BlockSpec:
    return BlockSpec(
        name=f"{pdk.name}.gen_current_mirror",
        pdk=pdk.name,
        level="L1",
        summary="Interdigitated current mirror — generic generator "
                "(descriptor-driven)",
        params=[
            ParamSpec(name="fingers", type="int", default=4, min=2, max=64,
                      description="Fingers per side (input + output interleaved)"),
            ParamSpec(name="width", type="float",
                      default=round(max(1.0, d.w_min), 3), unit="um",
                      min=round(d.w_min, 3), max=10.0,
                      description="Transistor width per finger"),
            ParamSpec(name="length", type="float",
                      default=round(max(0.15, d.l_min), 3), unit="um",
                      min=round(d.l_min, 3), max=10.0,
                      description="Gate length"),
            ParamSpec(name="type", type="str", default=types[0],
                      choices=types, description="Transistor type"),
        ],
        ports=[
            PortSpec(name="in", layer="met1", direction="input",
                     tap_layer="met1"),
            PortSpec(name="out", layer="met1", direction="output",
                     tap_layer="met1"),
            PortSpec(name="gate", layer="met1", direction="input",
                     tap_layer="met1"),
            PortSpec(name="vss", layer="met1", direction="inout",
                     tap_layer="met1"),
        ],
        constraints=_constraints(
            pdk, ["common_centroid", "matched_orientation"],
            d, extra_constraints),
        tags=["analog", "current_source", "generic"],
    )


# ---------------------------------------------------------------------------
# gen_guard_ring — rectangular tap ring, ports: tap + tap_n/s/w/e
# ---------------------------------------------------------------------------

def _build_guard_ring(pdk: PDK, d: _D):
    r = _roles(pdk)
    tap, diff, met1 = r["tap"], r.get("diff"), r["met1"]

    def _build(width: float, height: float, ring_width: float,
               ptype: str) -> gf.Component:
        c = gf.Component(
            name=f"{pdk.name}_gen_guard_ring_{ptype}"
                 f"_{width}x{height}{d.sfx}")
        w_in, h_in = snap(width), snap(height)
        rw = snap(ring_width)
        xi0, xi1 = -w_in / 2, w_in / 2
        yi0, yi1 = -h_in / 2, h_in / 2
        xo0, xo1 = xi0 - rw, xi1 + rw
        yo0, yo1 = yi0 - rw, yi1 + rw

        # Dedicated tap layer carries the contact when the descriptor has
        # one (never diff: a stray diff frame fuses with neighbouring
        # device active on touch — observed in the overlap smoke). Only the
        # implant-derived ring needs diff under the implant marker.
        rails = [r["li1"], met1]
        if tap is not None:
            rails.insert(0, tap)
        elif diff is not None:
            rails.insert(0, diff)
        for lay in rails:
            rect(c, lay, xo0, yo0, xo1, yi0)   # bottom
            rect(c, lay, xo0, yi1, xo1, yo1)   # top
            rect(c, lay, xo0, yi0, xi0, yi1)   # left
            rect(c, lay, xi1, yi0, xo1, yi1)   # right

        implant_margin = 0.12
        if ptype == "ntap":
            if r.get("nsdm") is not None:
                rect(c, r["nsdm"], xo0 - implant_margin, yo0 - implant_margin,
                     xo1 + implant_margin, yo1 + implant_margin)
            rect(c, r["well_n"], xo0 - 0.35, yo0 - 0.35,
                 xo1 + 0.35, yo1 + 0.35)
        elif r.get("psdm") is not None:
            rect(c, r["psdm"], xo0 - implant_margin, yo0 - implant_margin,
                 xo1 + implant_margin, yo1 + implant_margin)

        contact = d.licon
        pitch = max(0.36, d.cont_pitch)
        cy_off = rw / 2

        def contacts_h(x0: float, x1: float, y: float) -> None:
            cx = x0 + pitch / 2
            while cx + contact <= x1:
                rect(c, r["licon"], cx, y - contact / 2,
                     cx + contact, y + contact / 2)
                rect(c, r["mcon"], cx, y - contact / 2,
                     cx + contact, y + contact / 2)
                cx += pitch

        def contacts_v(y0: float, y1: float, x: float) -> None:
            cy = y0 + pitch / 2
            while cy + contact <= y1:
                rect(c, r["licon"], x - contact / 2, cy,
                     x + contact / 2, cy + contact)
                rect(c, r["mcon"], x - contact / 2, cy,
                     x + contact / 2, cy + contact)
                cy += pitch

        contacts_h(xo0, xo1, yo0 + cy_off)
        contacts_h(xo0, xo1, yo1 - cy_off)
        contacts_v(yi0, yi1, xo0 + cy_off)
        contacts_v(yi0, yi1, xo1 - cy_off)

        _port(c, pdk, "tap", met1, (0.0, yo0 + rw / 2), rw, 270)
        _port(c, pdk, "tap_s", met1, (0.0, yo0 + rw / 2), rw, 270)
        _port(c, pdk, "tap_n", met1, (0.0, yo1 - rw / 2), rw, 90)
        _port(c, pdk, "tap_w", met1, (xo0 + rw / 2, 0.0), rw, 180)
        _port(c, pdk, "tap_e", met1, (xo1 - rw / 2, 0.0), rw, 0)
        return c

    return _build


def _netlist_guard_ring(pdk: PDK):
    def _nl(width: float, height: float, ring_width: float,
            ptype: str) -> str:
        return ("* generic guard ring ({ptype})\n"
                ".subckt gen_guard_ring_{ptype} tap tap_n tap_s tap_w tap_e\n"
                ".ends\n").format(ptype=ptype)

    return _nl


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def _register_for_pdk(pdk: PDK) -> None:
    """(Re)generate the ``gen_*`` block set for one descriptor PDK."""
    name = pdk.name
    if name in builtin_pdk_names():
        return  # built-ins keep their hand-tuned generators
    # rebuild: drop anything previously generated for this name first
    for bname in _GENERATED.pop(name, set()):
        base.unregister(bname)
    made: set[str] = set()
    _GENERATED[name] = made
    if pdk.extract is None:
        return  # no role contract — nothing can be generated

    d = _D(pdk)
    epoch = _EPOCHS.get(name, 0)
    _EPOCHS[name] = epoch + 1
    d.sfx = "" if epoch == 0 else f"_g{epoch}"
    types = []
    if _leaf_model(pdk, "nmos") is not None:
        types.append("nmos")
    if _leaf_model(pdk, "pmos") is not None and _can_pmos(pdk):
        types.append("pmos")

    if _has(pdk, *_MOS_ROLES) and types:
        extra = []
        if not _can_ptap(pdk):
            extra.append("no tap/psdm bulk path — substrate ring omitted")
        spec = _spec_diff_pair(pdk, d, types, extra)
        base.register(spec, netlist=_netlist_diff_pair(pdk))(
            _build_diff_pair(pdk, d))
        made.add(spec.name)

        mirror_types = [t for t in types
                        if t == "pmos" or _can_ptap(pdk)]
        if mirror_types:
            mextra = list(extra)
            spec = _spec_current_mirror(pdk, d, mirror_types, mextra)
            base.register(spec, netlist=_netlist_current_mirror(pdk))(
                _build_current_mirror(pdk, d))
            made.add(spec.name)

    if _has(pdk, *_GUARD_RING_ROLES):
        ptypes = ["ptap"] + (["ntap"] if _has(pdk, "well_n") else [])
        spec = BlockSpec(
            name=f"{pdk.name}.gen_guard_ring",
            pdk=pdk.name,
            level="L1",
            summary="Parametric substrate/well guard ring — generic "
                    "generator (descriptor-driven)",
            params=[
                ParamSpec(name="width", type="float", default=10.0, unit="um",
                          min=1.0, max=500.0,
                          description="Inner enclosed cavity width"),
                ParamSpec(name="height", type="float", default=10.0, unit="um",
                          min=1.0, max=500.0,
                          description="Inner enclosed cavity height"),
                ParamSpec(name="ring_width", type="float", default=0.8,
                          unit="um",
                          min=round(
                              max(0.48, d.licon + 2 * d.li_enc + 0.1), 3),
                          max=10.0,
                          description="Width of the ring trace"),
                ParamSpec(name="ptype", type="str", default="ptap",
                          choices=ptypes,
                          description="ptap: P+ substrate tap; ntap: N+ tap "
                                      "in n-well"),
            ],
            ports=[
                PortSpec(name="tap", layer="met1", direction="inout",
                         tap_layer="tap"),
                PortSpec(name="tap_n", layer="met1", direction="inout",
                         tap_layer="tap"),
                PortSpec(name="tap_s", layer="met1", direction="inout",
                         tap_layer="tap"),
                PortSpec(name="tap_w", layer="met1", direction="inout",
                         tap_layer="tap"),
                PortSpec(name="tap_e", layer="met1", direction="inout",
                         tap_layer="tap"),
            ],
            constraints=_constraints(
                pdk, ["guard_ring_enclosure", "continuous_ring"], d),
            tags=["analog", "isolation", "guard_ring", "generic"],
        )
        base.register(spec, netlist=_netlist_guard_ring(pdk))(
            _build_guard_ring(pdk, d))
        made.add(spec.name)

    if not made:
        _GENERATED.pop(name, None)


def _on_pdk(pdk: PDK) -> None:
    try:
        _register_for_pdk(pdk)
    except Exception as exc:  # never let a generator kill PDK loading
        GENERATION_ERRORS[pdk.name] = str(exc)


def _on_pdk_removed(name: str) -> None:
    for bname in _GENERATED.pop(name, set()):
        base.unregister(bname)
    GENERATION_ERRORS.pop(name, None)


# Install on import: replays every registered PDK (load-order independent)
# and subscribes for future external loads / rescans.
base.on_pdk_registered(_on_pdk, on_removed=_on_pdk_removed)
