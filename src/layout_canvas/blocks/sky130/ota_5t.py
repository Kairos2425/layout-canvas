"""5-transistor OTA (L2 block - composed stripes with real interconnect)."""

from __future__ import annotations

import gdsfactory as gf

from layout_canvas.blocks.base import register
from layout_canvas.blocks.sky130 import layers
from layout_canvas.blocks.sky130.geom import add_port, rect, snap
from layout_canvas.ir.model import BlockSpec, ParamSpec, PortSpec


def register_ota_5t() -> None:
    spec = BlockSpec(
        name="sky130.ota_5t",
        pdk="sky130",
        level="L2",
        summary="5-transistor OTA: diff pair + active load + tail current",
        params=[
            ParamSpec(
                name="diff_fingers",
                type="int",
                default=4,
                min=2,
                max=16,
                description="Fingers per input transistor (ABBA pattern)",
            ),
            ParamSpec(
                name="load_fingers",
                type="int",
                default=4,
                min=2,
                max=16,
                description="Fingers per load transistor (ABBA pattern)",
            ),
            ParamSpec(
                name="width",
                type="float",
                default=2.0,
                unit="um",
                min=0.42,
                max=10.0,
                description="Channel width per finger",
            ),
        ],
        ports=[
            PortSpec(name="inp", layer="met1", direction="input", tap_layer="met1"),
            PortSpec(name="inn", layer="met1", direction="input", tap_layer="met1"),
            PortSpec(name="out", layer="met1", direction="output", tap_layer="met1"),
            PortSpec(name="vdd", layer="met1", direction="supply", tap_layer="met1"),
            PortSpec(name="vss", layer="met1", direction="supply", tap_layer="met1"),
            PortSpec(name="vbias", layer="met1", direction="input", tap_layer="met1"),
        ],
        constraints=["symmetric_vertical", "compact_stacking"],
        tags=["analog", "opamp", "ota"],
    )

    @register(spec, netlist=_netlist_ota_5t)
    def _build(diff_fingers: int, load_fingers: int, width: float) -> gf.Component:
        c = gf.Component(name=f"ota_5t_d{diff_fingers}_l{load_fingers}_w{width}")
        length = 0.5
        finger_pitch = snap(length + 1.2)
        dp_pattern = ["A", "B", "B", "A"] * (diff_fingers // 2)
        ld_pattern = ["A", "B", "B", "A"] * (load_fingers // 2)
        total_width = snap(finger_pitch * max(len(dp_pattern), len(ld_pattern)))
        half_l = length / 2
        diff_h = snap(width)

        # y-map: tail rail | dp stripes | load stripe | straps between
        tail_y0, tail_h = 0.5, diff_h            # tail channel = width
        dp_y0 = tail_y0 + tail_h + 3.3           # room for tail straps
        ld_y0 = dp_y0 + diff_h + 4.6             # room for dp straps
        ring_top_pad = 1.0

        # ---------------- stripes --------------------------------------
        def _fingers(y0: float, xs_pitch: float, n: int) -> list[float]:
            fxs = []
            for i in range(n):
                fx = snap(i * xs_pitch + xs_pitch / 2)
                fxs.append(fx)
                rect(c, layers.POLY, fx - half_l, y0 - 0.2,
                     fx + half_l, y0 + diff_h + 0.2)
            return fxs

        def _seg_nets(pattern: list[str], src_net: str,
                      drain_a: str, drain_b: str) -> list[str]:
            edges_ = [0.0] + [x + half_l for x in
                              [snap(i * finger_pitch + finger_pitch / 2)
                               for i in range(len(pattern))]]
            starts_ = [x - half_l for x in
                       [snap(i * finger_pitch + finger_pitch / 2)
                        for i in range(len(pattern))]] + [finger_pitch * len(pattern)]
            segs_ = list(zip(edges_, starts_))
            nets_ = []
            for j in range(len(segs_)):
                if j == 0:
                    nets_.append(drain_a if pattern[0] == "A" else drain_b)
                elif j == len(segs_) - 1:
                    nets_.append(drain_a if pattern[-1] == "A" else drain_b)
                else:
                    nets_.append(src_net if pattern[j - 1] != pattern[j]
                                 else (drain_a if pattern[j] == "A" else drain_b))
            return segs_, nets_

        def _sd_straps(y0: float, segs: list, nets: list,
                       strap_ys: dict[str, float]) -> None:
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

        # --- tail device (nmos, 2 fingers, source to ring) --------------
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
        # tail segments: left/right source -> vss, middle -> tail net
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
                # riser up to the tail strap
                rect(c, layers.LI, cx - 0.15, tail_y0 + tail_h / 2,
                     cx + 0.15, tail_strap_y)
                rect(c, layers.MCON, cx - 0.065, tail_strap_y - 0.065,
                     cx + 0.065, tail_strap_y + 0.065)
                rect(c, layers.MET1, cx - 0.24, tail_strap_y - 0.24,
                     cx + 0.24, tail_strap_y + 0.24)
            else:
                # source stubs drop into the bottom ring rail on met1
                rect(c, layers.MCON, cx - 0.065, tail_y0 + tail_h / 2 - 0.065,
                     cx + 0.065, tail_y0 + tail_h / 2 + 0.065)
                rect(c, layers.MET1, cx - 0.15, -0.65,
                     cx + 0.15, tail_y0 + tail_h / 2)
        # tail gate strap: pads on the poly overhang, per-finger li1 stubs
        # plus a horizontal strap joining them (without it the two gates
        # stay separate nets), then an mcon -> met1 port riser.
        vbias_strap_y = tail_y0 - 0.45
        for fx in tail_fx:
            rect(c, layers.POLY, fx - 0.2, vbias_strap_y + 0.55,
                 fx + 0.2, tail_y0)
            rect(c, layers.LICON, fx - 0.085, vbias_strap_y + 0.5,
                 fx + 0.085, vbias_strap_y + 0.67)
            rect(c, layers.LI, fx - 0.15, vbias_strap_y,
                 fx + 0.15, vbias_strap_y + 0.7)
        vbias_px = tail_x0 + tail_pitch
        rect(c, layers.LI, tail_fx[0] - 0.15, vbias_strap_y,
             tail_fx[1] + 0.15, vbias_strap_y + 0.35)
        rect(c, layers.MCON, vbias_px - 0.065, vbias_strap_y + 0.2 - 0.065,
             vbias_px + 0.065, vbias_strap_y + 0.2 + 0.065)
        rect(c, layers.MET1, vbias_px - 0.19, vbias_strap_y + 0.2 - 0.19,
             vbias_px + 0.19, vbias_strap_y + 0.2 + 0.19)
        add_port(c, "vbias", layers.MET1,
                 (vbias_px, vbias_strap_y + 0.2), 0.6, 270)

        # --- diff pair stripe (nmos) ------------------------------------
        rect(c, layers.DIFF, 0, dp_y0, total_width, dp_y0 + diff_h)
        rect(c, layers.NSDM, -0.1, dp_y0 - 0.1, total_width + 0.1,
             dp_y0 + diff_h + 0.1)
        dp_fx = _fingers(dp_y0, finger_pitch, len(dp_pattern))
        dp_segs, dp_nets = _seg_nets(dp_pattern, "tail", "out1", "out")
        dp_strap = {"out1": dp_y0 + diff_h + 0.55,
                    "tail": dp_y0 + diff_h + 1.21,
                    "out": dp_y0 + diff_h + 1.87}
        _sd_straps(dp_y0, dp_segs, dp_nets, dp_strap)
        # gate strapping: A on li1, B on met1 (same recipe as diff_pair)
        tap_y = dp_y0 - 0.15
        gy_a, gy_b = dp_y0 - 0.55, dp_y0 - 1.10
        port_y = dp_y0 + diff_h / 2
        gate_xs = {"A": [], "B": []}
        for i, side in enumerate(dp_pattern):
            fx = dp_fx[i]
            rect(c, layers.POLY, fx - 0.2, tap_y - 0.15, fx + 0.2, dp_y0)
            rect(c, layers.LICON, fx - 0.085, tap_y - 0.085,
                 fx + 0.085, tap_y + 0.085)
            if side == "A":
                rect(c, layers.LI, fx - 0.15, gy_a, fx + 0.15, tap_y + 0.15)
            else:
                rect(c, layers.LI, fx - 0.15, gy_a + 0.34, fx + 0.15,
                     tap_y + 0.15)
                rect(c, layers.MCON, fx - 0.065, tap_y - 0.065,
                     fx + 0.065, tap_y + 0.065)
                rect(c, layers.MET1, fx - 0.19, gy_b, fx + 0.19,
                     tap_y + 0.10)
            gate_xs[side].append(fx)
        px = {"A": total_width * 0.25, "B": total_width * 0.75 + 0.3}
        for side, xs in gate_xs.items():
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

        # --- pmos load stripe -------------------------------------------
        ld_pitch = snap(length + 1.2)
        ld_x0 = snap((total_width - ld_pitch * len(ld_pattern)) / 2)
        ld_n = len(ld_pattern)
        rect(c, layers.NWELL, ld_x0 - 0.5, ld_y0 - 0.5,
             ld_x0 + ld_pitch * ld_n + 0.5, ld_y0 + diff_h + 0.5)
        rect(c, layers.DIFF, ld_x0, ld_y0, ld_x0 + ld_pitch * ld_n,
             ld_y0 + diff_h)
        rect(c, layers.PSDM, ld_x0 - 0.1, ld_y0 - 0.1,
             ld_x0 + ld_pitch * ld_n + 0.1, ld_y0 + diff_h + 0.1)
        for i in range(ld_n):
            fx = snap(ld_x0 + i * ld_pitch + ld_pitch / 2)
            rect(c, layers.POLY, fx - half_l, ld_y0 - 0.2,
                 fx + half_l, ld_y0 + diff_h + 0.2)
        ld_fx = [snap(ld_x0 + i * ld_pitch + ld_pitch / 2) for i in range(ld_n)]
        # shift segment math into the stripe's own x-origin
        ld_edges = [ld_x0] + [x + half_l for x in ld_fx]
        ld_starts = [x - half_l for x in ld_fx] + [ld_x0 + ld_pitch * ld_n]
        ld_segs = list(zip(ld_edges, ld_starts))
        ld_nets = []
        for j in range(len(ld_segs)):
            if j == 0:
                ld_nets.append("out1" if ld_pattern[0] == "A" else "out")
            elif j == len(ld_segs) - 1:
                ld_nets.append("out1" if ld_pattern[-1] == "A" else "out")
            else:
                ld_nets.append("vdd" if ld_pattern[j - 1] != ld_pattern[j]
                               else ("out1" if ld_pattern[j] == "A" else "out"))
        ld_strap = {"out1": ld_y0 + diff_h + 0.55,
                    "vdd": ld_y0 + diff_h + 1.21,
                    "out": ld_y0 + diff_h + 1.87}
        _sd_straps(ld_y0, ld_segs, ld_nets, ld_strap)
        # load gate strap: shared 'out1' (diode-connected input side)
        ld_tap_y = ld_y0 - 0.15
        ld_gy = ld_y0 - 0.55
        for fx in ld_fx:
            rect(c, layers.POLY, fx - 0.2, ld_tap_y - 0.15, fx + 0.2, ld_y0)
            rect(c, layers.LICON, fx - 0.085, ld_tap_y - 0.085,
                 fx + 0.085, ld_tap_y + 0.085)
            rect(c, layers.LI, fx - 0.15, ld_gy, fx + 0.15, ld_tap_y + 0.15)
        rect(c, layers.LI, min(ld_fx) - 0.15, ld_gy - 0.15,
             max(ld_fx) + 0.15, ld_gy + 0.15)
        # n-well bulk tie to source: n-tap at the stripe's right edge,
        # then via1 -> met2 up to the vdd strap. met2 crosses the
        # out1/out straps without connecting — a met1 jumper would short
        # straight through them.
        ntx = snap(ld_x0 + ld_pitch * ld_n - 0.1)
        nty = ld_y0 - 0.35
        rect(c, layers.TAP, ntx - 0.15, nty - 0.15, ntx + 0.15, nty + 0.15)
        rect(c, layers.LICON, ntx - 0.085, nty - 0.085,
             ntx + 0.085, nty + 0.085)
        rect(c, layers.LI, ntx - 0.15, nty - 0.15, ntx + 0.15, nty + 0.15)
        rect(c, layers.MCON, ntx - 0.065, nty - 0.065,
             ntx + 0.065, nty + 0.065)
        rect(c, layers.MET1, ntx - 0.19, nty - 0.19, ntx + 0.19, nty + 0.19)
        # extend the vdd strap past the tap so the upper via lands on it
        xs_vdd = [snap((s[0] + s[1]) / 2) for s, n in zip(ld_segs, ld_nets)
                  if n == "vdd" and s[1] - s[0] >= 0.4]
        if xs_vdd:
            rect(c, layers.MET1, min(xs_vdd) - 0.2, ld_strap["vdd"] - 0.24,
                 max(max(xs_vdd) + 0.2, ntx + 0.19), ld_strap["vdd"] + 0.24)

        # ---------------- inter-stripe routing (met2 verticals) ---------
        def _via1(x: float, y: float) -> None:
            rect(c, layers.VIA1, x - 0.13, y - 0.13, x + 0.13, y + 0.13)

        # out1: dp outp strap -> load 'in' strap + load gate strap
        x_out1 = snap(total_width * 0.25)
        rect(c, layers.MET1, x_out1 - 0.19, dp_strap["out1"] - 0.19,
             x_out1 + 0.19, dp_strap["out1"] + 0.19)
        _via1(x_out1, dp_strap["out1"])
        _via1(x_out1, ld_strap["out1"])
        rect(c, layers.MET2, x_out1 - 0.19, dp_strap["out1"],
             x_out1 + 0.19, ld_strap["out1"])
        # load gate strap joins out1 through a via into the same riser
        gx = snap((min(ld_fx) + max(ld_fx)) / 2)
        rect(c, layers.MCON, gx - 0.065, ld_gy - 0.065,
             gx + 0.065, ld_gy + 0.065)
        rect(c, layers.MET1, gx - 0.19, ld_gy - 0.19, gx + 0.19,
             ld_strap["out1"] + 0.19)
        _via1(gx, ld_strap["out1"])
        # out: dp outn strap -> load 'out' strap
        x_out = snap(total_width * 0.75)
        rect(c, layers.MET1, x_out - 0.19, dp_strap["out"] - 0.19,
             x_out + 0.19, dp_strap["out"] + 0.19)
        _via1(x_out, dp_strap["out"])
        _via1(x_out, ld_strap["out"])
        rect(c, layers.MET2, x_out - 0.19, dp_strap["out"],
             x_out + 0.19, ld_strap["out"])
        # n-well bulk: tap pad -> via -> met2 -> via -> vdd strap
        _via1(ntx, nty)
        _via1(ntx, ld_strap["vdd"])
        rect(c, layers.MET2, ntx - 0.19, nty, ntx + 0.19, ld_strap["vdd"])
        # tail: dp tail strap -> tail drain strap (down, on met2)
        x_tail = snap(vbias_px + tail_pitch / 2)
        _via1(x_tail, dp_strap["tail"])
        _via1(x_tail, tail_strap_y)
        rect(c, layers.MET1, x_tail - 0.19, tail_strap_y - 0.19,
             x_tail + 0.19, tail_strap_y + 0.19)
        rect(c, layers.MET2, x_tail - 0.19, tail_strap_y,
             x_tail + 0.19, dp_strap["tail"])
        rect(c, layers.MET1, tail_fx[1] - tail_pitch / 2 - 0.19,
             tail_strap_y - 0.19, x_tail + 0.19, tail_strap_y + 0.19)

        # ---------------- ports + guard ring ----------------------------
        add_port(c, "inp", layers.MET1, (px["A"], port_y), 0.8, 180)
        add_port(c, "inn", layers.MET1, (px["B"] - 0.3, port_y), 0.8, 0)
        add_port(c, "out", layers.MET1, (x_out, ld_strap["out"]), 0.8, 90)
        add_port(c, "vdd", layers.MET1, (snap(total_width * 0.4), ld_strap["vdd"]), 0.8, 90)
        for px_, py_ in ((px["A"], port_y), (px["B"] - 0.3, port_y),
                         (x_out, ld_strap["out"]),
                         (snap(total_width * 0.4), ld_strap["vdd"])):
            rect(c, layers.MET1, px_ - 0.24, py_ - 0.24, px_ + 0.24, py_ + 0.24)

        gx0, gx1 = -0.9, total_width + 0.9
        gy0, gy1 = -0.9, ld_strap["out"] + ring_top_pad
        rw = 0.5
        for lay in (layers.TAP, layers.LI, layers.MET1):
            rect(c, lay, gx0, gy0, gx0 + rw, gy1)
            rect(c, lay, gx1 - rw, gy0, gx1, gy1)
            rect(c, lay, gx0, gy0, gx1, gy0 + rw)
            rect(c, lay, gx0, gy1 - rw, gx1, gy1)

        def _ring_contacts(layer, half, pitch):
            x = gx0 + rw / 2
            while x < gx1:
                for y in (gy0 + rw / 2, gy1 - rw / 2):
                    rect(c, layer, x - half, y - half, x + half, y + half)
                x += pitch
            y = gy0 + rw / 2 + pitch
            while y < gy1 - rw / 2:
                for x in (gx0 + rw / 2, gx1 - rw / 2):
                    rect(c, layer, x - half, y - half, x + half, y + half)
                y += pitch
        _ring_contacts(layers.LICON, 0.085, 0.5)
        _ring_contacts(layers.MCON, 0.065, 0.5)
        add_port(c, "vss", layers.MET1, (total_width / 2, gy0 + rw / 2), 0.8, 270)

        return c


def _netlist_ota_5t(diff_fingers: int, load_fingers: int, width: float) -> str:
    length = 0.5
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
            f"sky130_fd_pr__nfet_01v8 w={width}u l={length}u"
        )
    # tail device: two fingers, drains on 'tail', sources on vss
    lines.append(
        f"Mt1 tail vbias vss vss sky130_fd_pr__nfet_01v8 w={width}u l={length}u")
    lines.append(
        f"Mt2 vss vbias tail vss sky130_fd_pr__nfet_01v8 w={width}u l={length}u")
    for i, side in enumerate(ld_pattern):
        lines.append(
            f"Mp{i + 1} {seg_net(ld_pattern, i + 1, 'vdd', 'out1', 'out')} "
            f"out1 {seg_net(ld_pattern, i, 'vdd', 'out1', 'out')} vdd "
            f"sky130_fd_pr__pfet_01v8 w={width}u l={length}u"
        )
    lines.append(".ends")
    return "\n".join(lines)


register_ota_5t()
