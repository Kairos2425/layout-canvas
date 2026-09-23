"""IHP SG13G2 layer definitions — verified against libs.tech/klayout/tech/sg13g2.lyt."""

from __future__ import annotations

# Drawing layers (layer, datatype)
ACTIV = (1, 0)
GATPOLY = (5, 0)
CONT = (6, 0)
METAL1 = (8, 0)
VIA1 = (19, 0)
METAL2 = (10, 0)
VIA2 = (29, 0)
METAL3 = (30, 0)
VIA3 = (49, 0)
METAL4 = (50, 0)
VIA4 = (66, 0)
METAL5 = (67, 0)
TOPVIA1 = (125, 0)
TOPMETAL1 = (126, 0)
TOPVIA2 = (133, 0)
TOPMETAL2 = (134, 0)
SALBLOCK = (28, 0)
NWELL = (31, 0)

# Implant layers (verified in extract recipe: nsdm=(7,0), psdm=(14,0))
NSDM = (7, 0)   # n+ source/drain implant
PSDM = (14, 0)  # p+ source/drain implant

# SG13G2 pin/text convention: (drawing layer, datatype 2)
ACTIV_PIN = (1, 2)
GATPOLY_PIN = (5, 2)
METAL1_PIN = (8, 2)
METAL2_PIN = (10, 2)

PIN_PURPOSE = 2
