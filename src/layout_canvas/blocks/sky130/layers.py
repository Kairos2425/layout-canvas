"""Sky130 PDK layer definitions."""

from __future__ import annotations

# Sky130 layers (layer, datatype)
DIFF = (65, 20)  # nwell/pwell diffusion
TAP = (65, 44)   # substrate/well tap
NWELL = (64, 20)
POLY = (66, 20)
LICON = (66, 44)  # local interconnect contact
LI = (67, 20)     # local interconnect metal
MCON = (67, 44)   # metal1 contact
MET1 = (68, 20)
VIA1 = (68, 44)
MET2 = (69, 20)
VIA2 = (69, 44)
MET3 = (70, 20)
VIA3 = (70, 44)
MET4 = (71, 20)
VIA4 = (71, 44)
MET5 = (72, 20)

# Implants
NSDM = (93, 44)  # N+ source/drain implant
PSDM = (94, 20)  # P+ source/drain implant

# High-voltage
HVTP = (78, 44)  # HV thick-oxide PMOS
HVTR = (18, 20)  # HV thick-oxide NMOS
