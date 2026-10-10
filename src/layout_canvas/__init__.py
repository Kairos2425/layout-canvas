"""Package initialization."""

import gdsfactory as gf

# Ensure gdsfactory has an active generic PDK default for block generators and compilation
try:
    gf.get_active_pdk()
except Exception:
    gf.gpdk.PDK.activate()

from layout_canvas.blocks import base

__version__ = "0.1.0"
__all__ = ["base"]
