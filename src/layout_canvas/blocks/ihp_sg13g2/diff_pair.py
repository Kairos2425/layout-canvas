"""IHP SG13G2 common-centroid differential pair (L1 block).

Real implementation ported from the verified sky130 recipe:
fingered rail + per-segment S/D straps + two-layer gate strapping +
p-tap guard ring. Layer stack mapping:

    sky130 DIFF/POLY/LICON/LI/MCON/MET1
    -> ihp  ACTIV/GATPOLY/CONT/METAL1/VIA1/METAL2
"""

from __future__ import annotations

import gdsfactory as gf

from layout_canvas.blocks.base import register
from layout_canvas.blocks.ihp_sg13g2 import layers
from layout_canvas.blocks.ihp_sg13g2.geom import add_port, rect, snap
from layout_canvas.ir.model import BlockSpec, ParamSpec, PortSpec

NMOS = "sg13_lv_nmos"
PMOS = "sg13_lv_pmos"


def register_diff_pair() -> None:
    spec = BlockSpec(
        name="ihp_sg13g2.diff_pair",
        pdk="ihp_sg13g2",
        level="L1",
        summary="Common-centroid differential pair (sg13_lv_nmos)",
        params=[
            ParamSpec(
                name="fingers", type="int", default=4, min=2, max=32,
                description="Fingers per input transistor (ABBA pattern)",
            ),
            ParamSpec(
                name="width", type="float", default=1.0, unit="um", min=0.15, max=10.0,
                description="Channel width per finger",
            ),
            ParamSpec(
                name="length", type="float", default=0.34, unit="um", min=0.13, max=5.0,
                description="Gate length (sg13_lv min 0.13um)",
            ),
        ],
        ports=[
            PortSpec(name="inp", layer="metal2", direction="input", tap_layer="metal2"),
            PortSpec(name="inn", layer="metal2", direction="input", tap_layer="metal2"),
            PortSpec(name="outp", layer="metal2", direction="output", tap_layer="metal2"),
            PortSpec(name="outn", layer="metal2", direction="output", tap_layer="metal2"),
            PortSpec(name="tail", layer="metal2", direction="input", tap_layer="metal2"),
            PortSpec(name="vss", layer="metal2", direction="inout", tap_layer="metal2"),
        ],
        constraints=["common_centroid_ABBA", "symmetric_vertical"],
        tags=["analog", "diff_pair", "opamp", "ihp"],
    )

    @register(spec, netlist=_netlist_diff_pair)
    def _build(fingers: int, width: float, length: float) -> gf.Component:
        c = gf.Component(name=f"ihp_diff_pair_f{fingers}_w{width}_l{length}")
        finger_pitch = snap(length + 1.2)
        pattern = ["A", "B", "B", "A"] * (fingers // 2)
        total_width = snap(finger_pitch * len(pattern))
        half_l = length / 2
        diff_h = snap(width)
        diff_bottom = 3.0

        # active rail + n-implant (nmos)
        rect(c, layers.ACTIV, 0, diff_bottom, total_width, diff_bottom + diff_h)
        rect(c, layers.NSDM, -0.1, diff_bottom - 0.1, total_width + 0.1,
             diff_bottom + diff_h + 0.1)
        for i in range(len(pattern)):
            fx = snap(i * finger_pitch + finger_pitch / 2)
            rect(c, layers.GATPOLY, fx - half_l, diff_bottom - 0.2,
                 fx + half_l, diff_bottom + diff_h + 0.2)

        # S/D segment ownership (same rule as sky130)
        finger_x = [snap(i * finger_pitch + finger_pitch / 2)
                    for i in range(len(pattern))]
        edges = [0.0] + [x + half_l for x in finger_x]
        starts = [x - half_l for x in finger_x] + [total_width]
        segs = list(zip(edges, starts))
        seg_net = []
        for j in range(len(segs)):
            if j == 0:
                seg_net.append("outp" if pattern[0] == "A" else "outn")
            elif j == len(segs) - 1:
                seg_net.append("outp" if pattern[-1] == "A" else "outn")
            else:
                seg_net.append("tail" if pattern[j - 1] != pattern[j]
                               else ("outp" if pattern[j] == "A" else "outn"))

        # per-segment CONT + METAL1 stub + riser + VIA1 to METAL2 strap
        strap_y = {"outp": diff_bottom + diff_h + 0.60,
                   "tail": diff_bottom + diff_h + 1.32,
                   "outn": diff_bottom + diff_h + 2.04}
        for (sx0, sx1), net in zip(segs, seg_net):
            if sx1 - sx0 < 0.4:
                continue
            cx = snap((sx0 + sx1) / 2)
            y_top = strap_y[net]
            # CONT pitch must keep 0.18 edge spacing (0.17 hole + 0.18)
            n_con = max(1, int((sx1 - sx0 - 0.34) / 0.36) + 1)
            for k in range(n_con):
                kx = snap(sx0 + 0.17 + (sx1 - sx0 - 0.34) * (k / max(1, n_con - 1)) if n_con > 1 else cx)
                rect(c, layers.CONT, kx - 0.085, diff_bottom + diff_h / 2 - 0.085,
                     kx + 0.085, diff_bottom + diff_h / 2 + 0.085)
            rect(c, layers.METAL1, sx0 + 0.06, diff_bottom + diff_h / 2 - 0.15,
                 sx1 - 0.06, diff_bottom + diff_h / 2 + 0.15)
            rect(c, layers.METAL1, cx - 0.15, diff_bottom + diff_h / 2,
                 cx + 0.15, y_top)
            rect(c, layers.VIA1, cx - 0.10, y_top - 0.10,
                     cx + 0.10, y_top + 0.10)
        # every strap must reach its port pad 鈥?a single-segment net
        # (outn at fingers=2) otherwise leaves the pad floating
        port_x = {"outp": total_width * 0.25, "outn": total_width * 0.75,
                  "tail": total_width / 2}
        for net, y in strap_y.items():
            xs = [snap((s[0] + s[1]) / 2) for s, n in zip(segs, seg_net)
                  if n == net and s[1] - s[0] >= 0.4]
            if xs:
                x0 = min(xs) - 0.2
                x1 = max(xs) + 0.2
                if net in port_x:
                    x0 = min(x0, port_x[net] - 0.24)
                    x1 = max(x1, port_x[net] + 0.24)
                rect(c, layers.METAL2, x0, y - 0.24, x1, y + 0.24)

        # gate strapping: A on METAL1, B stub->VIA1->METAL2 riser+strap
        tap_y = diff_bottom - 0.15
        gy_a, gy_b = diff_bottom - 0.62, diff_bottom - 1.20
        port_y = diff_bottom + diff_h / 2
        gate_xs = {"A": [], "B": []}
        for i, side in enumerate(pattern):
            fx = finger_x[i]
            rect(c, layers.GATPOLY, fx - 0.2, tap_y - 0.15, fx + 0.2, diff_bottom)
            rect(c, layers.CONT, fx - 0.085, tap_y - 0.085,
                 fx + 0.085, tap_y + 0.085)
            if side == "A":
                rect(c, layers.METAL1, fx - 0.15, gy_a, fx + 0.15, tap_y + 0.15)
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

        # ports on METAL2 (the strap layer) + in-cell pads for labels
        add_port(c, "inp", layers.METAL2, (total_width * 0.25, port_y), 0.8, 180)
        add_port(c, "inn", layers.METAL2, (total_width * 0.75, port_y), 0.8, 0)
        add_port(c, "outp", layers.METAL2, (total_width * 0.25, strap_y["outp"]), 0.8, 90)
        add_port(c, "outn", layers.METAL2, (total_width * 0.75, strap_y["outn"]), 0.8, 90)
        add_port(c, "tail", layers.METAL2, (total_width / 2, strap_y["tail"]), 0.8, 90)
        for px_, py_ in (
            (total_width * 0.25, port_y),
            (total_width * 0.75, port_y),
            (total_width * 0.25, strap_y["outp"]),
            (total_width * 0.75, strap_y["outn"]),
            (total_width / 2, strap_y["tail"]),
        ):
            rect(c, layers.METAL2, px_ - 0.24, py_ - 0.24, px_ + 0.24, py_ + 0.24)

        # p-tap guard ring: ACTIV+PSDM rails + CONT + METAL1 + VIA1 + METAL2
        gx0, gx1 = -0.9, total_width + 0.9
        gy0, gy1 = 0.9, strap_y["outn"] + 1.0
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


def _netlist_diff_pair(fingers: int, width: float, length: float) -> str:
    pattern = ["A", "B", "B", "A"] * (fingers // 2)

    def seg_net(j: int) -> str:
        n = len(pattern) + 1
        if j == 0:
            return "outp" if pattern[0] == "A" else "outn"
        if j == n - 1:
            return "outp" if pattern[-1] == "A" else "outn"
        if pattern[j - 1] != pattern[j]:
            return "tail"
        return "outp" if pattern[j] == "A" else "outn"

    lines = [".subckt diff_pair inp inn outp outn tail vss"]
    for i, side in enumerate(pattern):
        gate = "inp" if side == "A" else "inn"
        lines.append(
            f"M{i + 1} {seg_net(i + 1)} {gate} {seg_net(i)} vss "
            f"{NMOS} w={width}u l={length}u"
        )
    lines.append(".ends")
    return "\n".join(lines)


register_diff_pair()
