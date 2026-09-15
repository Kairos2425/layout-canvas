"""IHP SG13G2 parametric blocks — real open-PDK models (sg13_lv_* PSP wrappers)."""

from layout_canvas.blocks.ihp_sg13g2.current_mirror import register_current_mirror
from layout_canvas.blocks.ihp_sg13g2.diff_pair import register_diff_pair
from layout_canvas.blocks.ihp_sg13g2.guard_ring import register_guard_ring
from layout_canvas.blocks.ihp_sg13g2.ota_5t import register_ota_5t

register_current_mirror()
register_diff_pair()
register_guard_ring()

__all__ = [
    "register_current_mirror",
    "register_diff_pair",
    "register_guard_ring",
    "register_ota_5t",
]
