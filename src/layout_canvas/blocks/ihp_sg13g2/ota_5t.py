"""IHP SG13G2 5-transistor OTA (L2 block 鈥?composed stripes).

Ported from the verified sky130 ota_5t. Stack mapping:
DIFF->ACTIV, POLY->GATPOLY, LICON->CONT, LI->METAL1, MCON->VIA1,
MET1->METAL2, VIA1->VIA2, MET2->METAL3.
"""

from __future__ import annotations

import gdsfactory as gf

from layout_canvas.blocks.base import register
from layout_canvas.blocks.ihp_sg13g2 import layers
from layout_canvas.blocks.ihp_sg13g2.geom import add_port, rect, snap
from layout_canvas.ir.model import BlockSpec, ParamSpec, PortSpec

NMOS = "sg13_lv_nmos"
PMOS = "sg13_lv_pmos"


def register_ota_5t() -> None:
    spec = BlockSpec(
        name="ihp_sg13g2.ota_5t",
        pdk="ihp_sg13g2",
        level="L2",
        summary="5-transistor OTA: diff pair + active load + tail current (sg13_lv_*)",
        params=[
            ParamSpec(name="diff_fingers", type="int", default=4, min=2, max=16,
                      description="Fingers per input transistor (ABBA)"),
            ParamSpec(name="load_fingers", type="int", default=4, min=2, max=16,
                      description="Fingers per load transistor (ABBA)"),
            ParamSpec(name="width", type="float", default=1.0, unit="um",
                      min=0.15, max=10.0, description="Channel width per finger"),
        ],
        ports=[
            PortSpec(name="inp", layer="metal2", direction="input", tap_layer="metal2"),
            PortSpec(name="inn", layer="metal2", direction="input", tap_layer="metal2"),
            PortSpec(name="out", layer="metal2", direction="output", tap_layer="metal2"),
            PortSpec(name="vdd", layer="metal2", direction="supply", tap_layer="metal2"),
            PortSpec(name="vss", layer="metal2", direction="supply", tap_layer="metal2"),
            PortSpec(name="vbias", layer="metal2", direction="input", tap_layer="metal2"),
        ],
        constraints=["symmetric_vertical", "compact_stacking"],
        tags=["analog", "opamp", "ota", "ihp"],
    )

    @register(spec, netlist=_netlist_ota_5t)
    def _build(diff_fingers: int, load_fingers: int, width: float) -> gf.Component:
        c = gf.Component(name=f"ihp_ota_5t_d{diff_fingers}_l{load_fingers}_w{width}")
        length = 0.34
        pitch = snap(length + 1.2)
        half_l = length / 2
        diff_h = snap(width)
        dp_pattern = ["A", "B", "B", "A"] * (diff_fingers // 2)
        ld_pattern = ["A", "B", "B", "A"] * (load_fingers // 2)
        total_width = snap(pitch * max(len(dp_pattern), len(ld_pattern)))

        tail_y0, tail_h = 0.5, diff_h
        dp_y0 = tail_y0 + tail_h + 3.3
        ld_y0 = dp_y0 + diff_h + 4.6

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
                n_con = max(1, int((sx1 - sx0 - 0.34) / 0.36) + 1)
                for k in range(n_con):
                    kx = snap(sx0 + 0.17 + (sx1 - sx0 - 0.34) * (k / max(1, n_con - 1)) if n_con > 1 else cx)
                    rect(c, layers.CONT, kx - 0.085, y0 + diff_h / 2 - 0.085,
                         kx + 0.085, y0 + diff_h / 2 + 0.085)
                rect(c, layers.METAL1, sx0 + 0.06, y0 + diff_h / 2 - 0.15,
                     sx1 - 0.06, y0 + diff_h / 2 + 0.15)
                rect(c, layers.METAL1, cx - 0.15, y0 + diff_h / 2,
                     cx + 0.15, y_top)
                rect(c, layers.VIA1, cx - 0.10, y_top - 0.10,
                     cx + 0.10, y_top + 0.10)
            for net, y in strap_ys.items():
                xs = [snap((s[0] + s[1]) / 2) for s, n in zip(segs, nets)
                      if n == net and s[1] - s[0] >= 0.4]
                if xs:
                    rect(c, layers.METAL2, min(xs) - 0.2, y - 0.24,
                         max(xs) + 0.2, y + 0.24)

        # ---------------- tail device (nmos, 2 fingers) ------------------
        tail_pitch = snap(length + 1.6)
        tail_x0 = snap(total_width / 2 - tail_pitch)
        tail_fx = [tail_x0 + tail_pitch / 2, tail_x0 + 1.5 * tail_pitch]
        rect(c, layers.ACTIV, tail_x0, tail_y0, tail_x0 + 2 * tail_pitch,
             tail_y0 + tail_h)
        rect(c, layers.NSDM, tail_x0 - 0.1, tail_y0 - 0.1,
             tail_x0 + 2 * tail_pitch + 0.1, tail_y0 + tail_h + 0.1)
        for fx in tail_fx:
            rect(c, layers.GATPOLY, fx - half_l, tail_y0 - 0.2,
                 fx + half_l, tail_y0 + tail_h + 0.2)
        t_edges = [tail_x0, tail_fx[0] + half_l, tail_fx[1] + half_l]
        t_starts = [tail_fx[0] - half_l, tail_fx[1] - half_l,
                    tail_x0 + 2 * tail_pitch]
        t_segs = list(zip(t_edges, t_starts))
        t_nets = ["vss", "tail", "vss"]
        tail_strap_y = tail_y0 + tail_h + 0.60
        for (sx0, sx1), net in zip(t_segs, t_nets):
            cx = snap((sx0 + sx1) / 2)
            rect(c, layers.CONT, cx - 0.085, tail_y0 + tail_h / 2 - 0.085,
                 cx + 0.085, tail_y0 + tail_h / 2 + 0.085)
            rect(c, layers.METAL1, sx0 + 0.06, tail_y0 + tail_h / 2 - 0.15,
                 sx1 - 0.06, tail_y0 + tail_h / 2 + 0.15)
            if net == "tail":
                rect(c, layers.METAL1, cx - 0.15, tail_y0 + tail_h / 2,
                     cx + 0.15, tail_strap_y)
                rect(c, layers.VIA1, cx - 0.10, tail_strap_y - 0.10,
                     cx + 0.10, tail_strap_y + 0.10)
                rect(c, layers.METAL2, cx - 0.24, tail_strap_y - 0.24,
                     cx + 0.24, tail_strap_y + 0.24)
            else:
                rect(c, layers.VIA1, cx - 0.10, tail_y0 + tail_h / 2 - 0.10,
                     cx + 0.10, tail_y0 + tail_h / 2 + 0.10)
                rect(c, layers.METAL2, cx - 0.15, -0.65,
                     cx + 0.15, tail_y0 + tail_h / 2)
        # tail gate strap: pads on the poly overhang BELOW the rail 鈥?        # a pad reaching into the diffusion lands the contact on the
        # channel (parasitic) and skews the extracted gate length.
        vbias_strap_y = tail_y0 - 0.45
        for fx in tail_fx:
            rect(c, layers.GATPOLY, fx - 0.2, vbias_strap_y,
                 fx + 0.2, tail_y0 - 0.01)
            rect(c, layers.CONT, fx - 0.085, vbias_strap_y + 0.2,
                 fx + 0.085, vbias_strap_y + 0.37)
            rect(c, layers.METAL1, fx - 0.15, vbias_strap_y,
                 fx + 0.15, vbias_strap_y + 0.42)
        vbias_px = tail_x0 + tail_pitch
        rect(c, layers.METAL1, tail_fx[0] - 0.15, vbias_strap_y,
             tail_fx[1] + 0.15, vbias_strap_y + 0.35)
        rect(c, layers.VIA1, vbias_px - 0.10, vbias_strap_y + 0.2 - 0.10,
             vbias_px + 0.10, vbias_strap_y + 0.2 + 0.10)
        rect(c, layers.METAL2, vbias_px - 0.15, vbias_strap_y + 0.2 - 0.15,
             vbias_px + 0.15, vbias_strap_y + 0.2 + 0.15)
        add_port(c, "vbias", layers.METAL2,
                 (vbias_px, vbias_strap_y + 0.2), 0.6, 270)

        # ---------------- diff pair stripe --------------------------------
        rect(c, layers.ACTIV, 0, dp_y0, total_width, dp_y0 + diff_h)
        rect(c, layers.NSDM, -0.1, dp_y0 - 0.1, total_width + 0.1,
             dp_y0 + diff_h + 0.1)
        dp_fx, dp_segs, dp_nets = _segs_net(dp_pattern, "tail", "out1", "out")
        for fx in dp_fx:
            rect(c, layers.GATPOLY, fx - half_l, dp_y0 - 0.2,
                 fx + half_l, dp_y0 + diff_h + 0.2)
        dp_strap = {"out1": dp_y0 + diff_h + 0.60,
                    "tail": dp_y0 + diff_h + 1.32,
                    "out": dp_y0 + diff_h + 2.04}
        _sd_straps(dp_y0, dp_segs, dp_nets, dp_strap)
        tap_y = dp_y0 - 0.15
        gy_a, gy_b = dp_y0 - 0.62, dp_y0 - 1.20
        port_y = dp_y0 + diff_h / 2
        gate_xs = {"A": [], "B": []}
        for i, side in enumerate(dp_pattern):
            fx = dp_fx[i]
            rect(c, layers.GATPOLY, fx - 0.2, tap_y - 0.15, fx + 0.2, dp_y0)
            rect(c, layers.CONT, fx - 0.085, tap_y - 0.085,
                 fx + 0.085, tap_y + 0.085)
            if side == "A":
                rect(c, layers.METAL1, fx - 0.15, gy_a, fx + 0.15,
                     tap_y + 0.15)
            else:
                rect(c, layers.METAL1, fx - 0.15, gy_a + 0.34, fx + 0.15,
                     tap_y + 0.15)
                rect(c, layers.VIA1, fx - 0.10, tap_y - 0.10,
                     fx + 0.10, tap_y + 0.10)
                rect(c, layers.METAL2, fx - 0.15, gy_b, fx + 0.15,
                     tap_y + 0.10)
            gate_xs[side].append(fx)
        px = {"A": total_width * 0.25, "B": total_width * 0.75}
        for side, xs in gate_xs.items():
            if not xs:
                continue
            if side == "A":
                rect(c, layers.METAL1, min(min(xs), px[side]) - 0.15,
                     gy_a - 0.15, max(max(xs), px[side]) + 0.15, gy_a + 0.15)
                rect(c, layers.VIA1, px[side] - 0.10, gy_a - 0.10,
                     px[side] + 0.10, gy_a + 0.10)
                rect(c, layers.METAL2, px[side] - 0.15, gy_a - 0.15,
                     px[side] + 0.15, gy_a + 0.15)
            else:
                rect(c, layers.METAL2, min(min(xs), px[side]) - 0.15,
                     gy_b - 0.15, max(max(xs), px[side]) + 0.15, gy_b + 0.15)
            rect(c, layers.METAL2, px[side] - 0.15,
                 gy_a if side == "A" else gy_b, px[side] + 0.15, port_y)

        # ---------------- pmos load stripe ---------------------------------
        ld_x0 = snap((total_width - pitch * len(ld_pattern)) / 2)
        rect(c, layers.NWELL, ld_x0 - 0.5, ld_y0 - 0.5,
             ld_x0 + pitch * len(ld_pattern) + 0.5, ld_y0 + diff_h + 0.5)
        rect(c, layers.ACTIV, ld_x0, ld_y0, ld_x0 + pitch * len(ld_pattern),
             ld_y0 + diff_h)
        rect(c, layers.PSDM, ld_x0 - 0.1, ld_y0 - 0.1,
             ld_x0 + pitch * len(ld_pattern) + 0.1, ld_y0 + diff_h + 0.1)
        ld_fx, ld_segs, ld_nets = _segs_net(ld_pattern, "vdd", "out1", "out",
                                          x0=ld_x0)
        for fx in ld_fx:
            rect(c, layers.GATPOLY, fx - half_l, ld_y0 - 0.2,
                 fx + half_l, ld_y0 + diff_h + 0.2)
        ld_strap = {"out1": ld_y0 + diff_h + 0.60,
                    "vdd": ld_y0 + diff_h + 1.32,
                    "out": ld_y0 + diff_h + 2.04}
        _sd_straps(ld_y0, ld_segs, ld_nets, ld_strap)
        ld_tap_y = ld_y0 - 0.15
        ld_gy = ld_y0 - 0.60
        for fx in ld_fx:
            rect(c, layers.GATPOLY, fx - 0.2, ld_tap_y - 0.15, fx + 0.2, ld_y0)
            rect(c, layers.CONT, fx - 0.085, ld_tap_y - 0.085,
                 fx + 0.085, ld_tap_y + 0.085)
            rect(c, layers.METAL1, fx - 0.15, ld_gy, fx + 0.15,
                 ld_tap_y + 0.15)
        rect(c, layers.METAL1, min(ld_fx) - 0.15, ld_gy - 0.15,
             max(ld_fx) + 0.15, ld_gy + 0.15)
        # n-well bulk tie -> vdd strap via METAL3 (crosses METAL2 straps)
        ntx = snap(ld_x0 + pitch * len(ld_pattern) - 0.1)
        nty = ld_y0 - 0.55
        rect(c, layers.ACTIV, ntx - 0.15, nty - 0.15, ntx + 0.15, nty + 0.15)
        rect(c, layers.NSDM, ntx - 0.17, nty - 0.17, ntx + 0.17, nty + 0.17)
        rect(c, layers.CONT, ntx - 0.085, nty - 0.085, ntx + 0.085, nty + 0.085)
        rect(c, layers.METAL1, ntx - 0.15, nty - 0.15, ntx + 0.15, nty + 0.15)
        rect(c, layers.VIA1, ntx - 0.10, nty - 0.10, ntx + 0.10, nty + 0.10)
        rect(c, layers.METAL2, ntx - 0.15, nty - 0.15, ntx + 0.15, nty + 0.15)
        xs_vdd = [snap((s[0] + s[1]) / 2) for s, n in zip(ld_segs, ld_nets)
                  if n == "vdd" and s[1] - s[0] >= 0.4]
        if xs_vdd:
            # strap must also reach the vdd port pad (0.4 * total_width)
            vdd_port_x = snap(total_width * 0.4)
            rect(c, layers.METAL2,
                 min(min(xs_vdd) - 0.2, vdd_port_x - 0.24),
                 ld_strap["vdd"] - 0.24,
                 max(max(xs_vdd) + 0.2, ntx + 0.15, vdd_port_x + 0.24),
                 ld_strap["vdd"] + 0.24)

        # ---------------- interconnect (METAL3 risers + VIA2) --------------
        def _via2(x, y):
            rect(c, layers.VIA2, x - 0.13, y - 0.13, x + 0.13, y + 0.13)

        # riser channels from actual strap extents + clearance, not fixed
        # fractions (colliding risers merged nets at non-default sizes)
        def _seg_xrange(segs, nets, name):
            xs = [snap((s[0] + s[1]) / 2) for s, n in zip(segs, nets)
                  if n == name and s[1] - s[0] >= 0.4]
            return (min(xs) - 0.1, max(xs) + 0.1) if xs else None

        def _inter(*ranges):
            r = None
            for x in ranges:
                if x is None:
                    return None
                r = x if r is None else (max(r[0], x[0]), min(r[1], x[1]))
            return r if r and r[0] <= r[1] else None

        def _pick_x(r, avoid=(), clear=0.60, prefer=None):
            if r is None:
                return None
            lo, hi = r
            cand = prefer if prefer is not None else snap((lo + hi) / 2)
            n = max(1, int((hi - lo) / 0.05) + 1)
            idx = sorted(range(n),
                         key=lambda i: abs(snap(lo + i * 0.05) - cand))
            for i in idx:
                x = snap(lo + i * 0.05)
                if lo <= x <= hi and not any(abs(x - a) < clear for a in avoid):
                    return x
            return snap((lo + hi) / 2)

        r1 = _inter(_seg_xrange(dp_segs, dp_nets, "out1"),
                    _seg_xrange(ld_segs, ld_nets, "out1"))
        x_out1 = _pick_x(r1, [ntx], prefer=snap(total_width * 0.25)) \
            or snap(total_width * 0.25)
        rect(c, layers.METAL2, x_out1 - 0.15, dp_strap["out1"] - 0.15,
             x_out1 + 0.15, dp_strap["out1"] + 0.15)
        _via2(x_out1, dp_strap["out1"])
        _via2(x_out1, ld_strap["out1"])
        rect(c, layers.METAL3, x_out1 - 0.15, dp_strap["out1"],
             x_out1 + 0.15, ld_strap["out1"])
        gx = snap((min(ld_fx) + max(ld_fx)) / 2)
        if abs(gx - x_out1) < 0.60:
            xs_g = [x for x in ld_fx if abs(x - x_out1) >= 0.60]
            if xs_g:
                gx = snap(min(xs_g, key=lambda x: abs(x - gx)))
        rect(c, layers.VIA1, gx - 0.10, ld_gy - 0.10,
             gx + 0.10, ld_gy + 0.10)
        rect(c, layers.METAL2, gx - 0.15, ld_gy - 0.15, gx + 0.15,
             ld_strap["out1"] + 0.15)
        r2 = _inter(_seg_xrange(dp_segs, dp_nets, "out"),
                    _seg_xrange(ld_segs, ld_nets, "out"))
        x_out = _pick_x(r2, [ntx, x_out1], prefer=snap(total_width * 0.75)) \
            or snap(total_width * 0.75)
        rect(c, layers.METAL2, x_out - 0.15, dp_strap["out"] - 0.15,
             x_out + 0.15, dp_strap["out"] + 0.15)
        _via2(x_out, dp_strap["out"])
        _via2(x_out, ld_strap["out"])
        rect(c, layers.METAL3, x_out - 0.15, dp_strap["out"],
             x_out + 0.15, ld_strap["out"])
        x_tail = snap(vbias_px + tail_pitch / 2)
        _via2(x_tail, dp_strap["tail"])
        _via2(x_tail, tail_strap_y)
        rect(c, layers.METAL2, x_tail - 0.15, tail_strap_y - 0.15,
             x_tail + 0.15, tail_strap_y + 0.15)
        rect(c, layers.METAL3, x_tail - 0.15, tail_strap_y,
             x_tail + 0.15, dp_strap["tail"])
        rect(c, layers.METAL2, tail_fx[1] - tail_pitch / 2 - 0.15,
             tail_strap_y - 0.15, x_tail + 0.15, tail_strap_y + 0.15)
        # nwell bulk -> vdd strap
        _via2(ntx, nty)
        _via2(ntx, ld_strap["vdd"])
        rect(c, layers.METAL3, ntx - 0.15, nty, ntx + 0.15, ld_strap["vdd"])

        # ---------------- ports + guard ring -------------------------------
        add_port(c, "inp", layers.METAL2, (px["A"], port_y), 0.8, 180)
        add_port(c, "inn", layers.METAL2, (px["B"], port_y), 0.8, 0)
        add_port(c, "out", layers.METAL2, (x_out, ld_strap["out"]), 0.8, 90)
        add_port(c, "vdd", layers.METAL2, (snap(total_width * 0.4), ld_strap["vdd"]), 0.8, 90)
        for px_, py_ in ((px["A"], port_y), (px["B"], port_y),
                         (x_out, ld_strap["out"]),
                         (snap(total_width * 0.4), ld_strap["vdd"])):
            rect(c, layers.METAL2, px_ - 0.24, py_ - 0.24, px_ + 0.24, py_ + 0.24)

        gx0, gx1 = -1.05, total_width + 1.05
        gy0, gy1 = -0.9, ld_strap["out"] + 1.0
        rw = 0.5
        for lay in (layers.ACTIV, layers.PSDM, layers.METAL1, layers.METAL2):
            rect(c, lay, gx0, gy0, gx0 + rw, gy1)
            rect(c, lay, gx1 - rw, gy0, gx1, gy1)
            rect(c, lay, gx0, gy0, gx1, gy0 + rw)
            rect(c, lay, gx0, gy1 - rw, gx1, gy1)

        # continuous contact bars inside each rail (discrete holes at a
        # pitch collide at corners and trip Cnt.b 0.18 spacing)
        def _ring_contacts(layer, half):
            for xa, ya, xb, yb in (
                (gx0 + 0.2, gy0 + rw / 2 - half, gx1 - 0.2, gy0 + rw / 2 + half),
                (gx0 + 0.2, gy1 - rw / 2 - half, gx1 - 0.2, gy1 - rw / 2 + half),
                (gx0 + rw / 2 - half, gy0 + 0.2, gx0 + rw / 2 + half, gy1 - 0.2),
                (gx1 - rw / 2 - half, gy0 + 0.2, gx1 - rw / 2 + half, gy1 - 0.2),
            ):
                rect(c, layer, xa, ya, xb, yb)
        _ring_contacts(layers.CONT, 0.085)
        _ring_contacts(layers.VIA1, 0.10)
        add_port(c, "vss", layers.METAL2, (total_width / 2, gy0 + rw / 2), 0.8, 270)

        return c


def _netlist_ota_5t(diff_fingers: int, load_fingers: int, width: float) -> str:
    length = 0.34
    dp_pattern = ["A", "B", "B", "A"] * (diff_fingers // 2)
    ld_pattern = ["A", "B", "B", "A"] * (load_fingers // 2)

    def seg_net(pattern, j, src, da, db):
        n = len(pattern) + 1
        if j == 0:
            return da if pattern[0] == "A" else db
        if j == n - 1:
            return da if pattern[-1] == "A" else db
        if pattern[j - 1] != pattern[j]:
            return src
        return da if pattern[j] == "A" else db

    lines = [".subckt ota_5t inp inn out vdd vss vbias"]
    for i, side in enumerate(dp_pattern):
        gate = "inp" if side == "A" else "inn"
        lines.append(
            f"Mn{i + 1} {seg_net(dp_pattern, i + 1, 'tail', 'out1', 'out')} "
            f"{gate} {seg_net(dp_pattern, i, 'tail', 'out1', 'out')} vss "
            f"{NMOS} w={width}u l={length}u"
        )
    lines.append(
        f"Mt1 tail vbias vss vss {NMOS} w={width}u l={length}u")
    lines.append(
        f"Mt2 vss vbias tail vss {NMOS} w={width}u l={length}u")
    for i, side in enumerate(ld_pattern):
        lines.append(
            f"Mp{i + 1} {seg_net(ld_pattern, i + 1, 'vdd', 'out1', 'out')} "
            f"out1 {seg_net(ld_pattern, i, 'vdd', 'out1', 'out')} vdd "
            f"{PMOS} w={width}u l={length}u"
        )
    lines.append(".ends")
    return "\n".join(lines)


register_ota_5t()
