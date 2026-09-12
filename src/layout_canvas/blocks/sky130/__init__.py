"""Sky130 parametric blocks."""

from layout_canvas.blocks.sky130.cap_array import register_cap_array
from layout_canvas.blocks.sky130.current_mirror import register_current_mirror
from layout_canvas.blocks.sky130.diff_pair import register_diff_pair
from layout_canvas.blocks.sky130.guard_ring import register_guard_ring
from layout_canvas.blocks.sky130.ota_5t import register_ota_5t
from layout_canvas.blocks.sky130.strongarm import register_strongarm

# Auto-register all blocks
register_current_mirror()
register_diff_pair()
register_cap_array()
register_ota_5t()
register_strongarm()
register_guard_ring()

__all__ = [
    "register_current_mirror",
    "register_diff_pair",
    "register_cap_array",
    "register_ota_5t",
    "register_strongarm",
    "register_guard_ring",
]
