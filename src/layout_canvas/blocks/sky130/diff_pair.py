"""Common-centroid differential pair (L1 block)."""

from __future__ import annotations

import gdsfactory as gf

from layout_canvas.blocks.base import register
from layout_canvas.blocks.sky130 import layers
from layout_canvas.blocks.sky130.geom import add_port, rect, snap
from layout_canvas.ir.model import BlockSpec, ParamSpec, PortSpec


def register_diff_pair() -> None:
    spec = BlockSpec(
        name="sky130.diff_pair",
        pdk="sky130",
        level="L1",
        summary="Common-centroid differential pair with tail current source",
        params=[
            ParamSpec(
                name="fingers",
                type="int",
                default=4,
                min=2,
                max=32,
                description="Fingers per input transistor (ABBA pattern)",
            ),
            ParamSpec(
                name="width",
                type="float",
                default=2.0,
                unit="um",
                min=0.42,
                max=10.0,
                description="Input pair width per finger",
            ),
            ParamSpec(
                name="length",
                type="float",
                default=0.5,
                unit="um",
                min=0.15,
                max=5.0,
                description="Gate length",
            ),
            ParamSpec(
                name="tail_width",
                type="float",
                default=4.0,
                unit="um",
                min=1.0,
                max=20.0,
                description="Tail current source width",
            ),
        ],
        ports=[
            PortSpec(name="inp", layer="met1", direction="input", tap_layer="met1"),
            PortSpec(name="inn", layer="met1", direction="input", tap_layer="met1"),
            PortSpec(name="outp", layer="met1", direction="output", tap_layer="met1"),
            PortSpec(name="outn", layer="met1", direction="output", tap_layer="met1"),
            PortSpec(name="tail", layer="met1", direction="input", tap_layer="met1"),
            PortSpec(name="vss", layer="met1", direction="inout", tap_layer="met1"),
        ],
        constraints=["common_centroid_ABBA", "symmetric_vertical"],
        tags=["analog", "diff_pair", "opamp"],
    )

    @register(spec, netlist=_netlist_diff_pair)
    def _build(fingers: int, width: float, length: float, tail_width: float) -> gf.Component:
        c = gf.Component(name=f"diff_pair_f{fingers}_w{width}_l{length}")

        # ABBA interdigitation for common-centroid. Geometry honours the
        # params: finger poly width = gate length, diffusion height =
        # channel width, x-pitch = gate length + S/D segment width.
        finger_pitch = snap(length + 1.2)
        pattern = ["A", "B", "B", "A"] * (fingers // 2)
        total_width = finger_pitch * len(pattern)

        # Input pair active region
        diff_h = snap(width)
        rect(c, layers.DIFF, 0, 3, total_width, 3 + diff_h)
        rect(c, layers.NSDM, -0.1, 2.9, total_width + 0.1, 3 + diff_h + 0.1)

        # Poly gates — finger width in x is the channel LENGTH
        half_l = length / 2
        poly_y0 = snap(2.8)
        poly_y1 = snap(3 + diff_h + 0.2)
        for i, side in enumerate(pattern):
            x = snap(i * finger_pitch + finger_pitch / 2)
            rect(c, layers.POLY, x - half_l, poly_y0, x + half_l, poly_y1)

        # The tail current source is a separate device (reference X3) — not
        # part of this cell. The 'tail' pin is the shared-source net itself
        # and is exported on the tail strap like the two drains.

        # --- intra-cell interconnect: real S/D and gate strapping ----------
        # A bare fingered rail is eight electrically separate transistors;
        # real cells contact every S/D segment and strap segments to their
        # net, and strap the gates of each device to its input.
        #
        # Segment ownership (alternating-orientation interdigitation):
        #   - segment between two DIFFERENT-device fingers -> shared source
        #   - segment between two SAME-device fingers      -> that drain
        #   - end segments                                 -> boundary drain
        finger_x = [snap(i * finger_pitch + finger_pitch / 2)
                    for i in range(len(pattern))]
        edges = [0.0] + [x + half_l for x in finger_x]
        starts = [x - half_l for x in finger_x] + [total_width]
        segs = list(zip(edges, starts))  # (x0, x1) per S/D segment
        seg_net: list[str] = []
        for j in range(len(segs)):
            if j == 0:
                seg_net.append("outp" if pattern[0] == "A" else "outn")
            elif j == len(segs) - 1:
                seg_net.append("outp" if pattern[-1] == "A" else "outn")
            else:
                seg_net.append("tail" if pattern[j - 1] != pattern[j]
                               else ("outp" if pattern[j] == "A" else "outn"))

        # Per-segment contact column + li1 riser up to its met1 strap.
        # Strap spacing must clear the strap height (0.48) plus margin —
        # 0.4 pitch made neighbouring straps physically overlap (real short,
        # only visible once same-layer connectivity is honoured).
        # Strap spacing must clear the strap height (0.48) plus met1
        # min-space (0.14): 0.66 pitch. Tighter pitches caused both real
        # overlap (merged nets) and DRC min_space violations.
        strap_y = {"outp": 3 + diff_h + 0.55, "tail": 3 + diff_h + 1.21,
                   "outn": 3 + diff_h + 1.87}
        for (sx0, sx1), net in zip(segs, seg_net):
            if sx1 - sx0 < 0.4:
                continue
            cx = snap((sx0 + sx1) / 2)
            y_top = strap_y[net]
            # contact array on the segment (licon -> li1 -> mcon -> met1)
            n_con = max(1, int((sx1 - sx0 - 0.2) / 0.34) + 1)
            for k in range(n_con):
                kx = snap(sx0 + 0.17 + (sx1 - sx0 - 0.34) * (k / max(1, n_con - 1)) if n_con > 1 else cx)
                rect(c, layers.LICON, kx - 0.085, 3 + diff_h / 2 - 0.085,
                     kx + 0.085, 3 + diff_h / 2 + 0.085)
            rect(c, layers.LI, sx0 + 0.06, 3 + diff_h / 2 - 0.15,
                 sx1 - 0.06, 3 + diff_h / 2 + 0.15)
            # li1 riser from segment to the strap
            rect(c, layers.LI, cx - 0.15, 3 + diff_h / 2, cx + 0.15, y_top)
            rect(c, layers.MCON, cx - 0.065, y_top - 0.065,
                 cx + 0.065, y_top + 0.065)
        # three horizontal met1 straps
        for net, y in strap_y.items():
            xs = [snap((s[0] + s[1]) / 2) for s, n in zip(segs, seg_net)
                  if n == net and s[1] - s[0] >= 0.4]
            if xs:
                rect(c, layers.MET1, min(xs) - 0.2, y - 0.24,
                     max(xs) + 0.2, y + 0.24)

        # Gate strapping. Both inputs strap below the rail on DIFFERENT
        # layers so they can never cross: side A straps on li1, side B
        # transitions li1->mcon->met1 at each tap and straps on met1
        # (met1 passes over the A li1 strap without connecting).
        # Tap pads stay inside the poly overhang — poly over diffusion
        # would create parasitic channels.
        diff_bottom = 3.0
        tap_y = diff_bottom - 0.15                    # 2.85, inside overhang
        gy_a, gy_b = 2.45, 1.90                       # li1 strap / met1 strap
        port_y = 3 + diff_h / 2
        gate_xs = {"A": [], "B": []}
        for i, side in enumerate(pattern):
            fx = finger_x[i]
            rect(c, layers.POLY, fx - 0.2, tap_y - 0.15, fx + 0.2, diff_bottom)
            rect(c, layers.LICON, fx - 0.085, tap_y - 0.085,
                 fx + 0.085, tap_y + 0.085)
            if side == "A":
                # li1 riser straight into the A strap
                rect(c, layers.LI, fx - 0.15, gy_a, fx + 0.15, tap_y + 0.15)
            else:
                # li1 stub at tap -> mcon -> met1 riser down to the B strap.
                # Stub bottom clears the A li1 strap (top 2.6) by li1
                # min-space 0.17+margin.
                rect(c, layers.LI, fx - 0.15, 2.79, fx + 0.15, tap_y + 0.15)
                rect(c, layers.MCON, fx - 0.065, tap_y - 0.065,
                     fx + 0.065, tap_y + 0.065)
                rect(c, layers.MET1, fx - 0.19, gy_b, fx + 0.19, tap_y + 0.10)
            gate_xs[side].append(fx)
        # Gate port risers both run on met1: an li1 riser routed up through
        # the diff area overlaps the S/D segment li stubs and shorts the
        # input to a drain — observed as inp|outp merging once same-layer
        # connectivity is honoured. The A strap is li1, so it transitions
        # through mcon at the tap point; the B strap is already met1.
        px = {"A": total_width * 0.25, "B": total_width * 0.75 + 0.3}
        for side, xs in gate_xs.items():
            if not xs:
                continue
            if side == "A":
                rect(c, layers.LI,
                     min(min(xs), px[side]) - 0.15, gy_a - 0.15,
                     max(max(xs), px[side]) + 0.15, gy_a + 0.15)
                rect(c, layers.MCON, px[side] - 0.065, gy_a - 0.065,
                     px[side] + 0.065, gy_a + 0.065)
                rect(c, layers.MET1, px[side] - 0.19, gy_a - 0.19,
                     px[side] + 0.19, gy_a + 0.19)
            else:
                rect(c, layers.MET1,
                     min(min(xs), px[side]) - 0.19, gy_b - 0.19,
                     max(max(xs), px[side]) + 0.19, gy_b + 0.19)
            rect(c, layers.MET1, px[side] - 0.19, gy_a if side == "A" else gy_b,
                 px[side] + 0.19, port_y)

        # Ports — positioned over the geometry that carries each net so pin
        # labels and access stacks land on the right strap, never a
        # neighbouring one. Labels only attach to same-cell conductor
        # shapes, so every label gets an in-cell met1 pad that overlaps the
        # net's own routing (verified: a label on a top-level pad alone does
        # not name the net).
        add_port(c, "inp", layers.MET1, (total_width * 0.25, 3 + diff_h / 2), 0.8, 180)
        add_port(c, "inn", layers.MET1, (total_width * 0.75, 3 + diff_h / 2), 0.8, 0)
        add_port(c, "outp", layers.MET1, (total_width * 0.25, strap_y["outp"]), 0.8, 90)
        add_port(c, "outn", layers.MET1, (total_width * 0.75, strap_y["outn"]), 0.8, 90)
        add_port(c, "tail", layers.MET1, (total_width / 2, strap_y["tail"]), 0.8, 90)
        for px_, py_ in (
            (total_width * 0.25, 3 + diff_h / 2),
            (total_width * 0.75, 3 + diff_h / 2),
            (total_width * 0.25, strap_y["outp"]),
            (total_width * 0.75, strap_y["outn"]),
            (total_width / 2, strap_y["tail"]),
        ):
            rect(c, layers.MET1, px_ - 0.24, py_ - 0.24, px_ + 0.24, py_ + 0.24)

        # --- guard ring: p-substrate tap frame with a contacted met1 ring --
        # Real cells tie the bulk somewhere physical: the tap frame joins
        # the global psub net in extraction (connect(rpsub, ptap)) and the
        # met1 ring exports it as the 'vss' pin.
        gx0, gx1 = -0.9, total_width + 0.9
        gy0, gy1 = 0.9, strap_y["outn"] + 1.0
        rw = 0.5
        for lay in (layers.TAP, layers.LI, layers.MET1):
            rect(c, lay, gx0, gy0, gx0 + rw, gy1)              # left rail
            rect(c, lay, gx1 - rw, gy0, gx1, gy1)             # right rail
            rect(c, lay, gx0, gy0, gx1, gy0 + rw)             # bottom rail
            rect(c, lay, gx0, gy1 - rw, gx1, gy1)             # top rail
        # contact arrays along each rail (licon on tap, mcon on li1->met1)
        def _ring_contacts(layer: tuple[int, int], half: float, pitch: float) -> None:
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


def _netlist_diff_pair(fingers: int, width: float, length: float, tail_width: float) -> str:
    # Finger-level reference: the layout draws every finger as a physical
    # device, so the schematic is written at finger granularity (extraction
    # yields one device per finger — comparing logical nf= devices would
    # need device combination and hides real connectivity faults).
    # Segment ownership mirrors the layout: segment between different-device
    # fingers is the shared source, between same-device fingers the drain.
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
        # extraction assigns D to the right-hand segment, S to the left
        lines.append(
            f"M{i + 1} {seg_net(i + 1)} {gate} {seg_net(i)} vss "
            f"sky130_fd_pr__nfet_01v8 w={width}u l={length}u"
        )
    lines.append(".ends")
    return "\n".join(lines)
