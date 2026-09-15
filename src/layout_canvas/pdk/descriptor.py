"""PDK descriptor — process data as data, not scattered constants.

A descriptor names the drawing/pin layer map and the few process rules the
compiler and tools actually consult. Block generators keep their existing
`blocks/sky130/layers.py` constants; the descriptor is the contract the
compiler, verifiers and future commercial-PDK adapters program against.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

Layer = tuple[int, int]


@dataclass(frozen=True)
class PDK:
    name: str
    layers: dict[str, Layer]
    pin_purpose: int = 16
    rules: dict[str, Any] = field(default_factory=dict)
    model_libs: dict[str, str] = field(default_factory=dict)

    def layer(self, name: str) -> Layer:
        try:
            return self.layers[name]
        except KeyError:
            raise KeyError(
                f"pdk {self.name!r}: unknown layer {name!r}; known: {sorted(self.layers)}"
            ) from None

    def pin_label_layer(self, drawing_layer: Any) -> Layer:
        """Map a port's drawing layer to its LVS pin-label layer.

        Accepts a (layer, datatype) tuple or a bare layer number; the label
        always lands on ``(layer, pin_purpose)``.
        """
        if isinstance(drawing_layer, (tuple, list)) and drawing_layer:
            number = int(drawing_layer[0])
        else:
            number = int(drawing_layer)
        return (number, self.pin_purpose)


_REGISTRY: dict[str, PDK] = {}


def register_pdk(pdk: PDK) -> PDK:
    _REGISTRY[pdk.name] = pdk
    return pdk


def get_pdk(name: str) -> PDK:
    try:
        return _REGISTRY[name]
    except KeyError:
        raise KeyError(f"unknown pdk {name!r}; known: {sorted(_REGISTRY)}") from None


def all_pdks() -> dict[str, PDK]:
    return dict(_REGISTRY)


def _build_sky130() -> PDK:
    return PDK(
        name="sky130",
        layers={
            "diff": (65, 20),
            "tap": (65, 44),
            "nwell": (64, 20),
            "poly": (66, 20),
            "licon": (66, 44),
            "li1": (67, 20),
            "mcon": (67, 44),
            "met1": (68, 20),
            "via1": (68, 44),
            "met2": (69, 20),
            "via2": (69, 44),
            "met3": (70, 20),
            "via3": (70, 44),
            "met4": (71, 20),
            "via4": (71, 44),
            "met5": (72, 20),
            "nsdm": (93, 44),
            "psdm": (94, 20),
        },
        rules={
            "min_instance_margin_um": 0.5,
            "default_routing_layer": "met2",
            "drc_deck": "sky130A.drc",
        },
    )


def _build_ihp_sg13g2() -> PDK:
    # Layer map verified against libs.tech/klayout/tech/sg13g2.lyt symbols.
    return PDK(
        name="ihp_sg13g2",
        layers={
            "activ": (1, 0),
            "gatpoly": (5, 0),
            "cont": (6, 0),
            "metal1": (8, 0),
            "via1": (19, 0),
            "metal2": (10, 0),
            "via2": (29, 0),
            "metal3": (30, 0),
            "via3": (49, 0),
            "metal4": (50, 0),
            "via4": (66, 0),
            "metal5": (67, 0),
            "topvia1": (125, 0),
            "topmetal1": (126, 0),
            "topvia2": (133, 0),
            "topmetal2": (134, 0),
            "salblock": (28, 0),
            "nwell": (31, 0),
        },
        pin_purpose=2,
        rules={
            "grid_um": 0.005,
            "default_routing_layer": "metal2",
        },
        model_libs={
            # ngspice corner decks ship inside the open PDK.
            "ngspice_dir": "libs.tech/ngspice/models",
            "mos_corner": "cornerMOSlv.lib",
            "cap_corner": "cornerCAP.lib",
            "res_corner": "cornerRES.lib",
            "dio_corner": "cornerDIO.lib",
            "hbt_corner": "cornerHBT.lib",
        },
    )


register_pdk(_build_sky130())
register_pdk(_build_ihp_sg13g2())
