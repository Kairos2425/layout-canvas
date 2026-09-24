"""StrongARM latch comparator (L2 block) — real striped implementation."""

from __future__ import annotations

import gdsfactory as gf

from layout_canvas.blocks.base import register
from layout_canvas.blocks.sky130 import layers
from layout_canvas.blocks.sky130.geom import add_port, rect, snap
from layout_canvas.ir.model import BlockSpec, ParamSpec, PortSpec


def register_strongarm() -> None:
    spec = BlockSpec(
        name="sky130.strongarm",
        pdk="sky130",
        level="L2",
        summary="StrongARM latch-based comparator with reset",
        params=[
            ParamSpec(
                name="fingers",
                type="int",
                default=4,
                min=2,
                max=16,
                description="Fingers per device side (ABBA pattern)",
            ),
            ParamSpec(
                name="width",
                type="float",
                default=1.5,
                unit="um",
                min=0.42,
                max=5.0,
                description="Channel width per finger",
            ),
        ],
        ports=[
            PortSpec(name="inp", layer="met1", direction="input", tap_layer="met1"),
            PortSpec(name="inn", layer="met1", direction="input", tap_layer="met1"),
            PortSpec(name="outp", layer="met1", direction="output", tap_layer="met1"),
            PortSpec(name="outn", layer="met1", direction="output", tap_layer="met1"),
            PortSpec(name="clk", layer="met1", direction="input", tap_layer="met1"),
            PortSpec(name="vdd", layer="met1", direction="supply", tap_layer="met1"),
        ],
        constraints=["symmetric_vertical", "cross_coupled_matching"],
        tags=["comparator", "strongarm", "digital"],
    )

    @register(spec, netlist=_netlist_strongarm)
    def _build(fingers: int, width: float) -> gf.Component:
        c = gf.Component(name=f"strongarm_f{fingers}_w{width}")
        length = 0.5
        pitch = snap(length + 1.2)
        half_l = length / 2
        diff_h = snap(width)
        pattern = ["A", "B", "B", "A"] * (fingers // 2)
        total_width = snap(pitch * len(pattern))

        def _segs_net(pat, src, da, db, x0=0.0):
            fxs = [snap(x0 + i * pitch + pitch / 2) for i in range(len(pat))]
            edges = [x0] + [x + half_l for x in fxs]
            starts = [x - half_l for x in fxs] + [x0 + pitch * len(pat)]
            segs = list(zip(edges, starts))
            nets = []
            for j in range(len(segs)):
                if j == 0:
                    nets.append(da if pat[0] == "A" else db)
                elif j == len(segs) - 1:
                    nets.append(da if pat[-1] == "A" else db)
                else:
                    nets.append(src if pat[j - 1] != pat[j]
                                else (da if pat[j] == "A" else db))
            return fxs, segs, nets

        def _sd_straps(y0, segs, nets, strap_ys):
            for (sx0, sx1), net in zip(segs, nets):
                if sx1 - sx0 < 0.4:
                    continue
                cx = snap((sx0 + sx1) / 2)
                y_top = strap_ys[net]
                n_con = max(1, int((sx1 - sx0 - 0.2) / 0.34) + 1)
                for k in range(n_con):
                    kx = snap(sx0 + 0.17 + (sx1 - sx0 - 0.34) * (k / max(1, n_con - 1)) if n_con > 1 else cx)
                    rect(c, layers.LICON, kx - 0.085, y0 + diff_h / 2 - 0.085,
                         kx + 0.085, y0 + diff_h / 2 + 0.085)
                rect(c, layers.LI, sx0 + 0.06, y0 + diff_h / 2 - 0.15,
                     sx1 - 0.06, y0 + diff_h / 2 + 0.15)
                rect(c, layers.LI, cx - 0.15, y0 + diff_h / 2, cx + 0.15, y_top)
                rect(c, layers.MCON, cx - 0.065, y_top - 0.065,
                     cx + 0.065, y_top + 0.065)
            for net, y in strap_ys.items():
                xs = [snap((s[0] + s[1]) / 2) for s, n in zip(segs, nets)
                      if n == net and s[1] - s[0] >= 0.4]
                if xs:
                    rect(c, layers.MET1, min(xs) - 0.2, y - 0.24,
                         max(xs) + 0.2, y + 0.24)

        def _gate_strap(y0, fxs, sides, strap_a_y, strap_b_y, tap_net_a=None):
            """Per-finger gate taps; A-side on li1 at strap_a_y, B-side
            li1-stub -> mcon -> met1 at strap_b_y (same recipe as
            diff_pair, proven not to short)."""
            tap_y = y0 - 0.15
            for fx, side in zip(fxs, sides):
                rect(c, layers.POLY, fx - 0.2, tap_y - 0.15, fx + 0.2, y0)
                rect(c, layers.LICON, fx - 0.085, tap_y - 0.085,
                     fx + 0.085, tap_y + 0.085)
                if side == "A":
                    rect(c, layers.LI, fx - 0.15, strap_a_y, fx + 0.15,
                         tap_y + 0.15)
                else:
                    rect(c, layers.LI, fx - 0.15, strap_a_y + 0.34,
                         fx + 0.15, tap_y + 0.15)
                    rect(c, layers.MCON, fx - 0.065, tap_y - 0.065,
                         fx + 0.065, tap_y + 0.065)
                    rect(c, layers.MET1, fx - 0.19, strap_b_y, fx + 0.19,
                         tap_y + 0.10)

        def _via1(x, y):
            rect(c, layers.VIA1, x - 0.13, y - 0.13, x + 0.13, y + 0.13)

        # ---------------- y map ----------------------------------------
        tail_y0 = 0.5
        tail_h = diff_h
        dp_y0 = tail_y0 + tail_h + 3.3
        nl_y0 = dp_y0 + diff_h + 4.6
        pl_y0 = nl_y0 + diff_h + 4.6

        # ---------------- tail switch (clk) -----------------------------
        tail_pitch = snap(length + 1.6)
        tail_x0 = snap(total_width / 2 - tail_pitch)
        tail_fx = [tail_x0 + tail_pitch / 2, tail_x0 + 1.5 * tail_pitch]
        rect(c, layers.DIFF, tail_x0, tail_y0, tail_x0 + 2 * tail_pitch,
             tail_y0 + tail_h)
        rect(c, layers.NSDM, tail_x0 - 0.1, tail_y0 - 0.1,
             tail_x0 + 2 * tail_pitch + 0.1, tail_y0 + tail_h + 0.1)
        for fx in tail_fx:
            rect(c, layers.POLY, fx - half_l, tail_y0 - 0.2,
                 fx + half_l, tail_y0 + tail_h + 0.2)
        t_edges = [tail_x0, tail_fx[0] + half_l, tail_fx[1] + half_l]
        t_starts = [tail_fx[0] - half_l, tail_fx[1] - half_l,
                    tail_x0 + 2 * tail_pitch]
        t_segs = list(zip(t_edges, t_starts))
        t_nets = ["vss", "tail", "vss"]
        tail_strap_y = tail_y0 + tail_h + 0.55
        for (sx0, sx1), net in zip(t_segs, t_nets):
            cx = snap((sx0 + sx1) / 2)
            rect(c, layers.LICON, cx - 0.085, tail_y0 + tail_h / 2 - 0.085,
                 cx + 0.085, tail_y0 + tail_h / 2 + 0.085)
            rect(c, layers.LI, sx0 + 0.06, tail_y0 + tail_h / 2 - 0.15,
                 sx1 - 0.06, tail_y0 + tail_h / 2 + 0.15)
            if net == "tail":
                rect(c, layers.LI, cx - 0.15, tail_y0 + tail_h / 2,
                     cx + 0.15, tail_strap_y)
                rect(c, layers.MCON, cx - 0.065, tail_strap_y - 0.065,
                     cx + 0.065, tail_strap_y + 0.065)
                rect(c, layers.MET1, cx - 0.24, tail_strap_y - 0.24,
                     cx + 0.24, tail_strap_y + 0.24)
            else:
                rect(c, layers.MCON, cx - 0.065, tail_y0 + tail_h / 2 - 0.065,
                     cx + 0.065, tail_y0 + tail_h / 2 + 0.065)
                rect(c, layers.MET1, cx - 0.15, -0.65,
                     cx + 0.15, tail_y0 + tail_h / 2)
        clk_strap_y = tail_y0 - 0.45
        for fx in tail_fx:
            rect(c, layers.POLY, fx - 0.2, clk_strap_y,
                 fx + 0.2, tail_y0 - 0.01)
            rect(c, layers.LICON, fx - 0.085, clk_strap_y + 0.2,
                 fx + 0.085, clk_strap_y + 0.37)
            rect(c, layers.LI, fx - 0.15, clk_strap_y,
                 fx + 0.15, clk_strap_y + 0.42)
        clk_px = tail_x0 + tail_pitch
        rect(c, layers.LI, tail_fx[0] - 0.15, clk_strap_y,
             tail_fx[1] + 0.15, clk_strap_y + 0.35)
        rect(c, layers.MCON, clk_px - 0.065, clk_strap_y + 0.2 - 0.065,
             clk_px + 0.065, clk_strap_y + 0.2 + 0.065)
        rect(c, layers.MET1, clk_px - 0.19, clk_strap_y + 0.2 - 0.19,
             clk_px + 0.19, clk_strap_y + 0.2 + 0.19)
        add_port(c, "clk", layers.MET1, (clk_px, clk_strap_y + 0.2), 0.6, 270)

        # ---------------- input pair (nmos) ------------------------------
        rect(c, layers.DIFF, 0, dp_y0, total_width, dp_y0 + diff_h)
        rect(c, layers.NSDM, -0.1, dp_y0 - 0.1, total_width + 0.1,
             dp_y0 + diff_h + 0.1)
        dp_fx, dp_segs, dp_nets = _segs_net(pattern, "tail", "d1", "d2")
        for fx in dp_fx:
            rect(c, layers.POLY, fx - half_l, dp_y0 - 0.2,
                 fx + half_l, dp_y0 + diff_h + 0.2)
        dp_strap = {"d1": dp_y0 + diff_h + 0.55,
                    "tail": dp_y0 + diff_h + 1.21,
                    "d2": dp_y0 + diff_h + 1.87}
        _sd_straps(dp_y0, dp_segs, dp_nets, dp_strap)
        _gate_strap(dp_y0, dp_fx, pattern, dp_y0 - 0.55, dp_y0 - 1.10)
        # inp/inn port risers (met1) — same recipe as diff_pair
        port_y = dp_y0 + diff_h / 2
        gy_a, gy_b = dp_y0 - 0.55, dp_y0 - 1.10
        gxs = {"A": [x for x, s in zip(dp_fx, pattern) if s == "A"],
               "B": [x for x, s in zip(dp_fx, pattern) if s == "B"]}
        px = {"A": total_width * 0.25, "B": total_width * 0.75 + 0.3}
        for side, xs in gxs.items():
            if not xs:
                continue
            if side == "A":
                rect(c, layers.LI, min(min(xs), px[side]) - 0.15, gy_a - 0.15,
                     max(max(xs), px[side]) + 0.15, gy_a + 0.15)
                rect(c, layers.MCON, px[side] - 0.065, gy_a - 0.065,
                     px[side] + 0.065, gy_a + 0.065)
                rect(c, layers.MET1, px[side] - 0.19, gy_a - 0.19,
                     px[side] + 0.19, gy_a + 0.19)
            else:
                rect(c, layers.MET1, min(min(xs), px[side]) - 0.19,
                     gy_b - 0.19, max(max(xs), px[side]) + 0.19, gy_b + 0.19)
            rect(c, layers.MET1, px[side] - 0.19,
                 gy_a if side == "A" else gy_b, px[side] + 0.19, port_y)

        # ---------------- nmos latch --------------------------------------
        rect(c, layers.DIFF, 0, nl_y0, total_width, nl_y0 + diff_h)
        rect(c, layers.NSDM, -0.1, nl_y0 - 0.1, total_width + 0.1,
             nl_y0 + diff_h + 0.1)
        nl_fx, nl_segs, nl_nets = _segs_net(pattern, "vss", "outn", "outp")
        for fx in nl_fx:
            rect(c, layers.POLY, fx - half_l, nl_y0 - 0.2,
                 fx + half_l, nl_y0 + diff_h + 0.2)
        nl_strap = {"outn": nl_y0 + diff_h + 0.55,
                    "vss": nl_y0 + diff_h + 1.21,
                    "outp": nl_y0 + diff_h + 1.87}
        _sd_straps(nl_y0, nl_segs, nl_nets, nl_strap)
        # nlatch shared source strap -> guard ring (vss)
        xs_vss = [snap((s[0] + s[1]) / 2) for s, n in zip(nl_segs, nl_nets)
                  if n == "vss" and s[1] - s[0] >= 0.4]
        if xs_vss:
            rect(c, layers.MET1, -0.65, nl_strap["vss"] - 0.24,
                 min(xs_vss), nl_strap["vss"] + 0.24)
        _gate_strap(nl_y0, nl_fx, pattern, nl_y0 - 0.55, nl_y0 - 1.10)
        # latch gates: A-side fingers -> d1, B-side -> d2 (straps joined
        # into the met2 risers below)
        nl_gy_a, nl_gy_b = nl_y0 - 0.55, nl_y0 - 1.10
        for side, xs in gxs.items():
            if not xs:
                continue
            if side == "A":
                rect(c, layers.LI, min(xs) - 0.15, nl_gy_a - 0.15,
                     max(xs) + 0.15, nl_gy_a + 0.15)
            else:
                rect(c, layers.MET1, min(xs) - 0.19, nl_gy_b - 0.19,
                     max(xs) + 0.19, nl_gy_b + 0.19)

        # ---------------- pmos latch --------------------------------------
        rect(c, layers.NWELL, -0.5, pl_y0 - 0.5, total_width + 0.5,
             pl_y0 + diff_h + 0.5)
        rect(c, layers.DIFF, 0, pl_y0, total_width, pl_y0 + diff_h)
        rect(c, layers.PSDM, -0.1, pl_y0 - 0.1, total_width + 0.1,
             pl_y0 + diff_h + 0.1)
        pl_fx, pl_segs, pl_nets = _segs_net(pattern, "vdd", "outn", "outp")
        for fx in pl_fx:
            rect(c, layers.POLY, fx - half_l, pl_y0 - 0.2,
                 fx + half_l, pl_y0 + diff_h + 0.2)
        pl_strap = {"outn": pl_y0 + diff_h + 0.55,
                    "vdd": pl_y0 + diff_h + 1.21,
                    "outp": pl_y0 + diff_h + 1.87}
        _sd_straps(pl_y0, pl_segs, pl_nets, pl_strap)
        _gate_strap(pl_y0, pl_fx, pattern, pl_y0 - 0.55, pl_y0 - 1.10)
        # cross-coupled gates: A-side fingers -> outp, B-side -> outn
        pl_gy_a, pl_gy_b = pl_y0 - 0.55, pl_y0 - 1.10
        for side, xs in gxs.items():
            if not xs:
                continue
            if side == "A":
                rect(c, layers.LI, min(xs) - 0.15, pl_gy_a - 0.15,
                     max(xs) + 0.15, pl_gy_a + 0.15)
            else:
                rect(c, layers.MET1, min(xs) - 0.19, pl_gy_b - 0.19,
                     max(xs) + 0.19, pl_gy_b + 0.19)
        # n-well bulk tie to vdd (source strap) via met2
        ntx = snap(total_width - 0.1)
        nty = pl_y0 - 0.35
        rect(c, layers.TAP, ntx - 0.15, nty - 0.15, ntx + 0.15, nty + 0.15)
        rect(c, layers.LICON, ntx - 0.085, nty - 0.085,
             ntx + 0.085, nty + 0.085)
        rect(c, layers.LI, ntx - 0.15, nty - 0.15, ntx + 0.15, nty + 0.15)
        rect(c, layers.MCON, ntx - 0.065, nty - 0.065,
             ntx + 0.065, nty + 0.065)
        rect(c, layers.MET1, ntx - 0.19, nty - 0.19, ntx + 0.19, nty + 0.19)
        xs_vdd = [snap((s[0] + s[1]) / 2) for s, n in zip(pl_segs, pl_nets)
                  if n == "vdd" and s[1] - s[0] >= 0.4]
        if xs_vdd:
            # strap must also cover the vdd port pad (0.5 * total_width)
            vdd_port_x = snap(total_width * 0.5)
            rect(c, layers.MET1,
                 min(min(xs_vdd) - 0.2, vdd_port_x - 0.24),
                 pl_strap["vdd"] - 0.24,
                 max(max(xs_vdd) + 0.2, ntx + 0.19, vdd_port_x + 0.24),
                 pl_strap["vdd"] + 0.24)

        # ---------------- interconnect (met2 risers + via1) --------------
        def _strap_via(x, y, on_li1):
            """via1 lands on met1 — an li1 strap needs mcon+met1 pad first."""
            if on_li1:
                rect(c, layers.MCON, x - 0.065, y - 0.065,
                     x + 0.065, y + 0.065)
                rect(c, layers.MET1, x - 0.19, y - 0.19, x + 0.19, y + 0.19)
            _via1(x, y)

        def _seg_xrange(segs, nets, name):
            xs = [snap((s[0] + s[1]) / 2) for s, n in zip(segs, nets)
                  if n == name and s[1] - s[0] >= 0.4]
            return (min(xs) - 0.2, max(xs) + 0.2) if xs else None

        def _gate_xrange(xs, half):
            return (min(xs) - half, max(xs) + half) if xs else None

        def _inter(*ranges):
            lo = max(r[0] for r in ranges if r)
            hi = min(r[1] for r in ranges if r)
            return (lo, hi) if lo < hi else None

        # Every riser must land on BOTH endpoint straps — compute x from
        # the actual segment/strap extents. A riser x outside the strap
        # leaves the net floating; a pad placed on another net's riser
        # shorts the two nets (observed: outp A-pad on a B-finger riser).
        gx_a = _gate_xrange(gxs["A"], 0.15)
        gx_b = _gate_xrange(gxs["B"], 0.19)

        def _pick_x(r, avoid=(), clear=0.38, lo_bias=0.0):
            """choose an x inside range r, >= clear away from avoided xs."""
            if r is None:
                return None
            lo, hi = r
            cand = snap(lo + 0.35) if lo_bias < 0 else snap((lo + hi) / 2)
            for dx in (0.0, -0.5, 0.5, -1.0, 1.0, -1.5, 1.5):
                x = snap(cand + dx)
                if lo <= x <= hi and not any(abs(x - a) < clear for a in avoid):
                    return x
            # fine sweep near the preferred x — never silently drop the
            # clearance (f4 fallback merged two met2 risers 0.12 apart)
            n = max(1, int((hi - lo) / 0.05) + 1)
            idx = sorted(range(n), key=lambda i: abs(snap(lo + i * 0.05) - cand))
            for i in idx:
                x = snap(lo + i * 0.05)
                if lo <= x <= hi and not any(abs(x - a) < clear for a in avoid):
                    return x
            return snap((lo + hi) / 2)

        b_fx = gxs["B"]

        # d2 first (narrow intersection), then d1 on a distinct channel —
        # two met2 risers sharing x and overlapping in y would merge.
        r = _inter(_seg_xrange(dp_segs, dp_nets, "d2"), gx_b)
        x_d2 = _pick_x(r) or snap(total_width * 0.75)
        r = _inter(_seg_xrange(dp_segs, dp_nets, "d1"), gx_a)
        x_d1 = _pick_x(r, list(b_fx) + [x_d2], clear=0.55) \
            or snap(total_width * 0.25)
        _via1(x_d1, dp_strap["d1"])
        _strap_via(x_d1, nl_gy_a, on_li1=True)
        rect(c, layers.MET2, x_d1 - 0.19, dp_strap["d1"],
             x_d1 + 0.19, nl_gy_a)
        _via1(x_d2, dp_strap["d2"])
        _strap_via(x_d2, nl_gy_b, on_li1=False)
        rect(c, layers.MET2, x_d2 - 0.19, dp_strap["d2"],
             x_d2 + 0.19, nl_gy_b)
        # outp riser: nlatch outp strap -> platch outp strap + platch A
        # gate strap. Then outn on a DISTINCT channel — overlapping risers
        # would merge the outputs.
        r = _inter(_seg_xrange(nl_segs, nl_nets, "outp"),
                   _seg_xrange(pl_segs, pl_nets, "outp"), gx_a)
        # output risers must also clear the d1/d2 risers — their y spans
        # (nl->pl vs dp->nl) overlap around the nlatch straps
        x_op = _pick_x(r, list(b_fx) + [x_d1, x_d2], clear=0.55) \
            or snap(total_width * 0.40)
        r = _inter(_seg_xrange(nl_segs, nl_nets, "outn"),
                   _seg_xrange(pl_segs, pl_nets, "outn"), gx_b)
        x_on = _pick_x(r, [x_d1, x_d2], clear=0.55, lo_bias=-1.0) \
            or snap(total_width * 0.60)
        if x_op is not None and abs(x_on - x_op) < 0.55:
            x_on = _pick_x(r, [x_op, x_d1, x_d2], clear=0.55) \
                or snap(total_width * 0.60)
        _via1(x_on, nl_strap["outn"])
        _via1(x_on, pl_strap["outn"])
        rect(c, layers.MET2, x_on - 0.19, nl_strap["outn"],
             x_on + 0.19, pl_strap["outn"])
        _strap_via(x_on, pl_gy_b, on_li1=False)
        # outp riser: nlatch outp strap -> platch outp strap + platch A
        # gate strap. Must use a DIFFERENT x than the outn riser —
        # overlapping risers would merge the outputs.
        r = _inter(_seg_xrange(nl_segs, nl_nets, "outp"),
                   _seg_xrange(pl_segs, pl_nets, "outp"), gx_a)
        x_op = _pick_x(r, list(b_fx) + [x_d1, x_d2, x_on], clear=0.55) \
            or snap(total_width * 0.40)
        _via1(x_op, nl_strap["outp"])
        _via1(x_op, pl_strap["outp"])
        rect(c, layers.MET2, x_op - 0.19, nl_strap["outp"],
             x_op + 0.19, pl_strap["outp"])
        _strap_via(x_op, pl_gy_a, on_li1=True)
        # tail: dp tail strap -> tail drain strap
        x_tail = snap(clk_px + tail_pitch / 2)
        _via1(x_tail, dp_strap["tail"])
        _via1(x_tail, tail_strap_y)
        rect(c, layers.MET1, x_tail - 0.19, tail_strap_y - 0.19,
             x_tail + 0.19, tail_strap_y + 0.19)
        rect(c, layers.MET2, x_tail - 0.19, tail_strap_y,
             x_tail + 0.19, dp_strap["tail"])
        rect(c, layers.MET1, tail_fx[1] - tail_pitch / 2 - 0.19,
             tail_strap_y - 0.19, x_tail + 0.19, tail_strap_y + 0.19)
        # nwell bulk -> vdd
        _via1(ntx, nty)
        _via1(ntx, pl_strap["vdd"])
        rect(c, layers.MET2, ntx - 0.19, nty, ntx + 0.19, pl_strap["vdd"])

        # ---------------- ports + guard ring -----------------------------
        add_port(c, "inp", layers.MET1, (px["A"], port_y), 0.8, 180)
        add_port(c, "inn", layers.MET1, (px["B"] - 0.3, port_y), 0.8, 0)
        add_port(c, "outp", layers.MET1, (x_op, pl_strap["outp"]), 0.8, 90)
        add_port(c, "outn", layers.MET1, (x_on, pl_strap["outn"]), 0.8, 90)
        add_port(c, "vdd", layers.MET1, (snap(total_width * 0.5), pl_strap["vdd"]), 0.8, 90)
        for px_, py_ in ((px["A"], port_y), (px["B"] - 0.3, port_y),
                         (x_op, pl_strap["outp"]), (x_on, pl_strap["outn"]),
                         (snap(total_width * 0.5), pl_strap["vdd"])):
            rect(c, layers.MET1, px_ - 0.24, py_ - 0.24, px_ + 0.24, py_ + 0.24)

        gx0, gx1 = -0.9, total_width + 0.9
        gy0, gy1 = -0.9, pl_strap["outp"] + 1.0
        rw = 0.5
        for lay in (layers.TAP, layers.LI, layers.MET1):
            rect(c, lay, gx0, gy0, gx0 + rw, gy1)
            rect(c, lay, gx1 - rw, gy0, gx1, gy1)
            rect(c, lay, gx0, gy0, gx1, gy0 + rw)
            rect(c, lay, gx0, gy1 - rw, gx1, gy1)

        def _ring_contacts(layer, half, pitch_):
            x = gx0 + rw / 2
            while x < gx1:
                for y in (gy0 + rw / 2, gy1 - rw / 2):
                    rect(c, layer, x - half, y - half, x + half, y + half)
                x += pitch_
            y = gy0 + rw / 2 + pitch_
            while y < gy1 - rw / 2:
                for x in (gx0 + rw / 2, gx1 - rw / 2):
                    rect(c, layer, x - half, y - half, x + half, y + half)
                y += pitch_
        _ring_contacts(layers.LICON, 0.085, 0.5)
        _ring_contacts(layers.MCON, 0.065, 0.5)

        return c


def _netlist_strongarm(fingers: int, width: float) -> str:
    length = 0.5
    pattern = ["A", "B", "B", "A"] * (fingers // 2)

    def seg_net(pat, j, src, da, db):
        n = len(pat) + 1
        if j == 0:
            return da if pat[0] == "A" else db
        if j == n - 1:
            return da if pat[-1] == "A" else db
        if pat[j - 1] != pat[j]:
            return src
        return da if pat[j] == "A" else db

    lines = [".subckt strongarm inp inn outp outn clk vdd"]
    # input pair: A->d1 gate inp, B->d2 gate inn
    for i, side in enumerate(pattern):
        g = "inp" if side == "A" else "inn"
        lines.append(
            f"Mi{i + 1} {seg_net(pattern, i + 1, 'tail', 'd1', 'd2')} {g} "
            f"{seg_net(pattern, i, 'tail', 'd1', 'd2')} vss "
            f"sky130_fd_pr__nfet_01v8 w={width}u l={length}u"
        )
    lines.append(
        f"Mt1 tail clk vss vss sky130_fd_pr__nfet_01v8 w={width}u l={length}u")
    lines.append(
        f"Mt2 vss clk tail vss sky130_fd_pr__nfet_01v8 w={width}u l={length}u")
    # nmos latch: A-side -> outn gate d1, B-side -> outp gate d2
    for i, side in enumerate(pattern):
        g = "d1" if side == "A" else "d2"
        lines.append(
            f"Ml{i + 1} {seg_net(pattern, i + 1, 'vss', 'outn', 'outp')} {g} "
            f"{seg_net(pattern, i, 'vss', 'outn', 'outp')} vss "
            f"sky130_fd_pr__nfet_01v8 w={width}u l={length}u"
        )
    # pmos latch cross-coupled: A-side -> outn gate outp, B-side -> outp gate outn
    for i, side in enumerate(pattern):
        g = "outp" if side == "A" else "outn"
        lines.append(
            f"Mp{i + 1} {seg_net(pattern, i + 1, 'vdd', 'outn', 'outp')} {g} "
            f"{seg_net(pattern, i, 'vdd', 'outn', 'outp')} vdd "
            f"sky130_fd_pr__pfet_01v8 w={width}u l={length}u"
        )
    lines.append(".ends")
    return "\n".join(lines)


register_strongarm()
