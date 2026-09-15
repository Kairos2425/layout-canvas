"""IHP SG13G2 5-transistor OTA (L2 block)."""

from __future__ import annotations

import gdsfactory as gf

from layout_canvas.blocks.base import register
from layout_canvas.blocks.ihp_sg13g2 import layers
from layout_canvas.blocks.ihp_sg13g2.geom import add_port, rect
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
                      description="Differential pair fingers"),
            ParamSpec(name="load_fingers", type="int", default=4, min=2, max=16,
                      description="Active load fingers"),
            ParamSpec(name="width", type="float", default=1.0, unit="um", min=0.15, max=10.0,
                      description="Transistor width"),
        ],
        ports=[
            PortSpec(name="inp", layer="metal1", direction="input"),
            PortSpec(name="inn", layer="metal1", direction="input"),
            PortSpec(name="out", layer="metal1", direction="output"),
            PortSpec(name="vdd", layer="metal1", direction="supply"),
            PortSpec(name="vss", layer="metal1", direction="supply"),
        ],
        constraints=["symmetric_vertical", "compact_stacking"],
        tags=["analog", "opamp", "ota", "ihp"],
    )

    @register(spec, netlist=_netlist_ota_5t)
    def _build(diff_fingers: int, load_fingers: int, width: float) -> gf.Component:
        c = gf.Component(name=f"ihp_ota_5t_d{diff_fingers}_l{load_fingers}")

        total_w = 16.0
        load_h = 4.0
        diff_h = 5.0
        tail_h = 3.0

        rect(c, layers.ACTIV, 0, 12, total_w, 12 + load_h)
        rect(c, layers.NWELL, -0.31, 11.7, total_w + 0.31, 16.3)
        rect(c, layers.ACTIV, 0, 6, total_w, 6 + diff_h)
        rect(c, layers.ACTIV, 6, 0, 10, tail_h)

        add_port(c, "inp", layers.METAL1, (4, 8.5), 0.5, 180)
        add_port(c, "inn", layers.METAL1, (12, 8.5), 0.5, 0)
        add_port(c, "out", layers.METAL1, (8, 14), 0.5, 90)
        add_port(c, "vdd", layers.METAL1, (8, 15.5), 0.6, 90)
        add_port(c, "vss", layers.METAL1, (8, 1), 0.6, 270)

        return c


def _netlist_ota_5t(diff_fingers: int, load_fingers: int, width: float) -> str:
    return f""".subckt ota_5t inp inn out vdd vss
* Diff pair
X1 out1 inp tail vss {NMOS} w={width}u l=0.34u ng={diff_fingers} m=1
X2 out inn tail vss {NMOS} w={width}u l=0.34u ng={diff_fingers} m=1
* Tail
X3 tail vbias vss vss {NMOS} w={width * 2}u l=0.34u m=1
* Active load
X4 out1 out1 vdd vdd {PMOS} w={width}u l=0.34u ng={load_fingers} m=1
X5 out out1 vdd vdd {PMOS} w={width}u l=0.34u ng={load_fingers} m=1
.ends"""


register_ota_5t()
