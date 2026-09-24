"""IHP SG13G2 interdigitated current mirror (L0 block).

Real implementation ported from the verified sky130 recipe. Stack:
ACTIV/GATPOLY/CONT/METAL1/VIA1/METAL2.
"""

from __future__ import annotations

import gdsfactory as gf

from layout_canvas.blocks.base import register
from layout_canvas.blocks.ihp_sg13g2 import layers
from layout_canvas.blocks.ihp_sg13g2.geom import add_port, rect, snap
from layout_canvas.ir.model import BlockSpec, ParamSpec, PortSpec

NMOS = "sg13_lv_nmos"
PMOS = "sg13_lv_pmos"


def register_current_mirror() -> None:
    spec = BlockSpec(
        name="ihp_sg13g2.current_mirror",
        pdk="ihp_sg13g2",
        level="L0",
        summary="Interdigitated NMOS/PMOS current mirror (sg13_lv_*)",
        params=[
            ParamSpec(name="fingers", type="int", default=4, min=2, max=64,
                      description="Fingers per side (input + output interleaved)"),
            ParamSpec(name="width", type="float", default=0.5, unit="um", min=0.15, max=10.0,
                      description="Channel width per finger"),
            ParamSpec(name="length", type="float", default=0.13, unit="um", min=0.13, max=10.0,
                      description="Gate length"),
            ParamSpec(name="type", type="str", default="nmos", choices=["nmos", "pmos"],
                      description="Transistor type"),
        ],
        ports=[
            PortSpec(name="in", layer="metal2", direction="input", tap_layer="metal2"),
            PortSpec(name="out", layer="metal2", direction="output", tap_layer="metal2"),
            PortSpec(name="gate", layer="metal2", direction="input", tap_layer="metal2"),
            PortSpec(name="vss", layer="metal2", direction="inout", tap_layer="metal2"),
        ],
        constraints=["common_centroid", "matched_orientation"],
        tags=["analog", "current_source", "ihp"],
    )

    @register(spec, netlist=_netlist_current_mirror)
    def _build(fingers: int, width: float, length: float, type: str) -> gf.Component:
        c = gf.Component(name=f"ihp_current_mirror_f{fingers}_w{width}_l{length}_{type}")
        finger_pitch = snap(length + 1.2)
        pattern = ["A", "B", "B", "A"] * (fingers // 2)
        total_width = snap(finger_pitch * len(pattern))
        diff_h = snap(width)
        diff_bottom = 3.0
        implant = layers.NSDM if type == "nmos" else layers.PSDM
        if type == "pmos":
            rect(c, layers.NWELL, -0.5, 1.5, total_width + 0.5,
                 diff_bottom + diff_h + 0.5)
        rect(c, layers.ACTIV, 0, diff_bottom, total_width, diff_bottom + diff_h)
        rect(c, implant, -0.1, diff_bottom - 0.1, total_width + 0.1,
             diff_bottom + diff_h + 0.1)

        half_l = length / 2
        for i in range(len(pattern)):
            fx = snap(i * finger_pitch + finger_pitch / 2)
            rect(c, layers.GATPOLY, fx - half_l, diff_bottom - 0.2,
                 fx + half_l, diff_bottom + diff_h + 0.2)

        finger_x = [snap(i * finger_pitch + finger_pitch / 2)
                    for i in range(len(pattern))]
        edges = [0.0] + [x + half_l for x in finger_x]
        starts = [x - half_l for x in finger_x] + [total_width]
        segs = list(zip(edges, starts))
        seg_net: list[str] = []
        for j in range(len(segs)):
            if j == 0:
                seg_net.append("in" if pattern[0] == "A" else "out")
            elif j == len(segs) - 1:
                seg_net.append("in" if pattern[-1] == "A" else "out")
            else:
                seg_net.append("vss" if pattern[j - 1] != pattern[j]
                               else ("in" if pattern[j] == "A" else "out"))

        strap_y = {"in": diff_bottom + diff_h + 0.60,
                   "vss": diff_bottom + diff_h + 1.32,
                   "out": diff_bottom + diff_h + 2.04}
        for (sx0, sx1), net in zip(segs, seg_net):
            if sx1 - sx0 < 0.4:
                continue
            cx = snap((sx0 + sx1) / 2)
            y_top = strap_y[net]
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
        # (out at fingers=2) otherwise leaves the pad floating
        port_x = {"in": total_width * 0.25, "out": total_width * 0.75}
        for net, y in strap_y.items():
            xs = [snap((s[0] + s[1]) / 2) for s, n in zip(segs, seg_net)
                  if n == net and s[1] - s[0] >= 0.4]
            if xs:
                x0 = min(xs) - 0.2
                x1 = max(xs) + 0.2
                if net in port_x:
                    x0 = min(x0, port_x[net] - 0.24)
                    x1 = max(x1, port_x[net] + 0.24)
                if net == "vss":
                    x1 = total_width + 0.65  # extend into the right ring rail
                rect(c, layers.METAL2, x0, y - 0.24, x1, y + 0.24)

        if type == "pmos":
            # n-well bulk tie: n+ tap (activ+nsdm inside nwell) -> cont ->
            # metal1 -> via1 -> metal2 jumper down to the ring rail (vss)
            # the tap sits in the clear strip below the gate-tap row —
            # vertically separated from diff/gate CONTs, and the met2
            # jumper down to the bottom rail crosses only empty metal.
            ntx = snap(total_width - 0.5)
            nty = 1.8
            rect(c, layers.ACTIV, ntx - 0.15, nty - 0.15,
                 ntx + 0.15, nty + 0.15)
            rect(c, layers.NSDM, ntx - 0.17, nty - 0.17,
                 ntx + 0.17, nty + 0.17)
            rect(c, layers.CONT, ntx - 0.085, nty - 0.085,
                 ntx + 0.085, nty + 0.085)
            rect(c, layers.METAL1, ntx - 0.15, nty - 0.15,
                 ntx + 0.15, nty + 0.15)
            rect(c, layers.VIA1, ntx - 0.10, nty - 0.10,
                 ntx + 0.10, nty + 0.10)
            rect(c, layers.METAL2, ntx - 0.15, 1.15, ntx + 0.15, nty + 0.15)

        # gate strapping: all fingers share 'gate' 鈥?one METAL1 strap
        tap_y = diff_bottom - 0.15
        gy = diff_bottom - 0.55
        port_y = diff_bottom + diff_h / 2
        px_gate = total_width * 0.5
        for fx in finger_x:
            rect(c, layers.GATPOLY, fx - 0.2, tap_y - 0.15, fx + 0.2,
                 diff_bottom)
            rect(c, layers.CONT, fx - 0.085, tap_y - 0.085,
                 fx + 0.085, tap_y + 0.085)
            rect(c, layers.METAL1, fx - 0.15, gy, fx + 0.15, tap_y + 0.15)
        rect(c, layers.METAL1,
             min(min(finger_x), px_gate) - 0.15, gy - 0.15,
             max(max(finger_x), px_gate) + 0.15, gy + 0.15)
        rect(c, layers.VIA1, px_gate - 0.10, gy - 0.10,
             px_gate + 0.10, gy + 0.10)
        rect(c, layers.METAL2, px_gate - 0.15, gy - 0.15,
             px_gate + 0.15, gy + 0.15)
        rect(c, layers.METAL2, px_gate - 0.15, gy, px_gate + 0.15, port_y)

        add_port(c, "in", layers.METAL2, (total_width * 0.25, strap_y["in"]), 0.8, 90)
        add_port(c, "out", layers.METAL2, (total_width * 0.75, strap_y["out"]), 0.8, 90)
        add_port(c, "gate", layers.METAL2, (px_gate, port_y), 0.8, 180)
        for px_, py_ in (
            (total_width * 0.25, strap_y["in"]),
            (total_width * 0.75, strap_y["out"]),
            (px_gate, port_y),
        ):
            rect(c, layers.METAL2, px_ - 0.24, py_ - 0.24, px_ + 0.24, py_ + 0.24)

        # p-tap guard ring: ACTIV+PSDM rails + CONT + METAL1 + VIA1 + METAL2
        gx0, gx1 = -1.05, total_width + 1.05
        gy0, gy1 = 0.9, strap_y["out"] + 1.0
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


def _netlist_current_mirror(fingers: int, width: float, length: float, type: str) -> str:
    model = NMOS if type == "nmos" else PMOS
    pattern = ["A", "B", "B", "A"] * (fingers // 2)

    def seg_net(j: int) -> str:
        n = len(pattern) + 1
        if j == 0:
            return "in" if pattern[0] == "A" else "out"
        if j == n - 1:
            return "in" if pattern[-1] == "A" else "out"
        if pattern[j - 1] != pattern[j]:
            return "vss"
        return "in" if pattern[j] == "A" else "out"

    lines = [".subckt current_mirror in out gate vss"]
    for i, side in enumerate(pattern):
        lines.append(
            f"M{i + 1} {seg_net(i + 1)} gate {seg_net(i)} vss "
            f"{model} w={width}u l={length}u"
        )
    lines.append(".ends")
    return "\n".join(lines)


register_current_mirror()
