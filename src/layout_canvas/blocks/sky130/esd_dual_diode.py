"""Sky130 dual-diode ESD clamp cell.

A pad sees two reverse-biased diodes in normal operation:
``sky130_fd_pr__diode_pw2nd_05v5`` (N+ diffusion in p-substrate, anode on
``vss``) clamps negative strikes to ground, and
``sky130_fd_pr__diode_pd2nw_05v5`` (P+ diffusion in n-well, cathode on
``vdd``) clamps positive strikes to the rail.  Both diffusions carry the
``areaid.diode`` marker (81/23) so the extraction recipe recognises them
as diode devices instead of orphan S/D regions.
"""

from __future__ import annotations

import gdsfactory as gf

from layout_canvas.blocks.base import get, register
from layout_canvas.blocks.sky130 import layers
from layout_canvas.blocks.sky130.geom import (
    add_port,
    centered_positions,
    rect,
    snap,
)
from layout_canvas.ir.model import BlockSpec, ParamSpec, PortSpec

# sky130 areaid.diode marker (81/23) — gates diode extraction (opt-in).
DIODE_MARKER = (81, 23)

_MODEL_N = "sky130_fd_pr__diode_pw2nd_05v5"  # n+ diff in psub, anode=vss
_MODEL_P = "sky130_fd_pr__diode_pd2nw_05v5"  # p+ diff in nwell, cathode=vdd


def build_esd_dual_diode(
    w: float = 10.0,
    l: float = 2.0,  # noqa: E741 - PDK pin name
) -> gf.Component:
    """Standalone builder."""
    return get("sky130.esd_dual_diode").component(w=w, l=l)


def register_esd_dual_diode() -> None:
    spec = BlockSpec(
        name="sky130.esd_dual_diode",
        pdk="sky130",
        level="L0",
        summary="Dual-diode ESD clamp: pw2nd to VSS and pd2nw to VDD on a pad",
        params=[
            ParamSpec(
                name="w",
                type="float",
                default=10.0,
                unit="um",
                min=0.5,
                max=200.0,
                description="Diode diffusion width (per junction)",
            ),
            ParamSpec(
                name="l",
                type="float",
                default=2.0,
                unit="um",
                min=0.5,
                max=50.0,
                description="Diode diffusion length (per junction)",
            ),
        ],
        ports=[
            PortSpec(name="pad", layer="met1", direction="inout"),
            PortSpec(name="vdd", layer="met1", direction="supply"),
            PortSpec(name="vss", layer="met1", direction="supply"),
        ],
        constraints=["diode_marker_required", "well_taps_required"],
        tags=["esd", "clamp", "protection", "io", "diode"],
    )

    @register(spec, netlist=_netlist_esd_dual_diode)
    def _build(w: float, l: float) -> gf.Component:  # noqa: E741 - PDK pin name
        c = gf.Component(name=f"esd_dual_diode_w{w}_l{l}")

        w_d = snap(w)
        l_d = snap(l)
        # x-offset of the pd2nw group: keeps nwell-to-nsdm spacing far
        # beyond the 1.27um headline rule.
        xoff = w_d + 8.0
        contact = 0.17
        pitch = 0.36
        li_margin = 0.06
        m1_margin = 0.03

        def _contact_row(x0: float, x1: float, y_c: float,
                         with_mcon: bool = True) -> None:
            xs = centered_positions(
                (x0 + x1) / 2,
                max(1, int((x1 - x0 - contact) // pitch) + 1), pitch)
            for x in xs:
                rect(c, layers.LICON, x - contact / 2, y_c - contact / 2,
                     x + contact / 2, y_c + contact / 2)
                if with_mcon:
                    rect(c, layers.MCON, x - contact / 2,
                         y_c - contact / 2, x + contact / 2,
                         y_c + contact / 2)

        # --- pw2nd diode (n+ diff in p-substrate, p-tap ring to vss) ---
        rect(c, layers.DIFF, 0.0, 0.0, w_d, l_d)
        rect(c, layers.NSDM, -0.25, -0.25, w_d + 0.25, l_d + 0.25)
        rect(c, DIODE_MARKER, -0.45, -0.45, w_d + 0.45, l_d + 0.45)
        rect(c, layers.LI, -li_margin, -li_margin,
             w_d + li_margin, l_d + li_margin)
        _contact_row(0.0, w_d, l_d / 2)

        # p-tap ring around the n+ diode diff, tied to vss. The ring's
        # top segment carries no met1/mcon — the pad strap crosses it on
        # met1 (li1<->met1 needs mcon, so no accidental pad-vss short).
        ring_gap = 1.4
        rw = 0.8
        rx0, ry0 = -ring_gap - rw, -ring_gap - rw
        rx1, ry1 = w_d + ring_gap + rw, l_d + ring_gap + rw
        ix0, iy0 = -ring_gap, -ring_gap
        ix1, iy1 = w_d + ring_gap, l_d + ring_gap
        for x0, y0, x1, y1, top in (
            (rx0, ry0, rx1, iy0, False),   # bottom
            (rx0, iy1, rx1, ry1, True),    # top (no mcon/met1)
            (rx0, iy0, ix0, iy1, False),   # left
            (ix1, iy0, rx1, iy1, False),   # right
        ):
            for lyr in (layers.DIFF, layers.TAP, layers.PSDM, layers.LI):
                rect(c, lyr, x0, y0, x1, y1)
            if not top:
                rect(c, layers.MET1, x0, y0, x1, y1)
            if y1 - y0 < x1 - x0:
                _contact_row(x0, x1, (y0 + y1) / 2, with_mcon=not top)
            else:
                xs = centered_positions((x0 + x1) / 2, 1, pitch)
                ys = centered_positions(
                    (y0 + y1) / 2,
                    max(1, int((y1 - y0 - contact) // pitch) + 1), pitch)
                for y in ys:
                    rect(c, layers.LICON, xs[0] - contact / 2,
                         y - contact / 2, xs[0] + contact / 2,
                         y + contact / 2)
                    if not top:
                        rect(c, layers.MCON, xs[0] - contact / 2,
                             y - contact / 2, xs[0] + contact / 2,
                             y + contact / 2)

        # pad strap on met1: diff -> through the met1-free ring top -> bus
        pad_y0 = ry1 + 0.3
        pad_y1 = pad_y0 + 0.6
        rect(c, layers.MET1, -m1_margin, -m1_margin, w_d + m1_margin, pad_y1)
        rect(c, layers.MET1, -m1_margin, pad_y0,
             xoff + w_d + m1_margin, pad_y1)

        # --- pd2nw diode (p+ diff inside nwell, n-tap bar to vdd) ---
        tap_w = 0.8
        tap_x0 = xoff + w_d + 1.4
        tap_x1 = tap_x0 + tap_w
        nw_x0, nw_y0 = xoff - 0.6, -0.6
        nw_x1, nw_y1 = tap_x1 + 0.6, l_d + 0.6
        rect(c, layers.NWELL, nw_x0, nw_y0, nw_x1, nw_y1)
        rect(c, layers.DIFF, xoff, 0.0, xoff + w_d, l_d)
        rect(c, layers.PSDM, xoff - 0.25, -0.25,
             xoff + w_d + 0.25, l_d + 0.25)
        rect(c, DIODE_MARKER, xoff - 0.45, -0.45,
             xoff + w_d + 0.45, l_d + 0.45)
        rect(c, layers.LI, xoff - li_margin, -li_margin,
             xoff + w_d + li_margin, l_d + li_margin)
        _contact_row(xoff, xoff + w_d, l_d / 2)
        # pd2nw met1 strap joins the pad bus above the ring
        rect(c, layers.MET1, xoff - m1_margin, -m1_margin,
             xoff + w_d + m1_margin, pad_y1)

        # n-tap bar inside the nwell, strapped down to vdd (outside the
        # pw2nd ring — the ring only encloses the left group)
        for lyr in (layers.DIFF, layers.TAP, layers.NSDM,
                    layers.LI, layers.MET1):
            rect(c, lyr, tap_x0, 0.0, tap_x1, l_d)
        _contact_row(tap_x0, tap_x1, l_d / 2)
        vdd_y = ry0 - 0.6
        rect(c, layers.MET1, tap_x0, vdd_y, tap_x1, 0.0)

        # vss strap below the ring's bottom segment
        rect(c, layers.MET1, w_d / 2 - 0.4, vdd_y, w_d / 2 + 0.4, iy0)

        # --- ports (met1 pins + labels for LVS) ---
        # The rail-side ports sit at the TOP of their straps: the router
        # draws its horizontal net rail flush with the strap tops, so a
        # pin placed down in the stub leaves a sub-rule sliver between
        # the router's pad square and its rail (observed 0.06 < 0.14).
        add_port(c, "pad", layers.MET1,
                 ((0.0 + xoff + w_d) / 2, pad_y1 - 0.3),
                 xoff + w_d + 0.06, 90)
        add_port(c, "vdd", layers.MET1,
                 ((tap_x0 + tap_x1) / 2, -0.4), tap_w, 270)
        add_port(c, "vss", layers.MET1,
                 (w_d / 2, iy0 - 0.3), rw, 270)
        return c


def _netlist_esd_dual_diode(w: float, l: float) -> str:  # noqa: E741
    area_m2 = snap(w) * snap(l) * 1e-12       # um^2 -> m^2
    pj_m = 2 * (snap(w) + snap(l)) * 1e-6     # um -> m
    return f"""* Sky130 dual-diode ESD clamp ({_MODEL_N} + {_MODEL_P})
* node order anode/cathode: Dn clamps vss->pad (pw2nd), Dp clamps pad->vdd (pd2nw)
.subckt esd_dual_diode pad vdd vss
Dn vss pad {_MODEL_N} area={area_m2:.4e} pj={pj_m:.4e}
Dp pad vdd {_MODEL_P} area={area_m2:.4e} pj={pj_m:.4e}
.ends
"""
