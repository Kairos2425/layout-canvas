"""Block package initialization."""

# Importing the package activates the descriptor-driven ``gen_*``
# generators: ``generic`` installs its PDK-registration hook on import and
# replays every PDK already in the registry, so block availability is
# load-order independent.
from layout_canvas.blocks import generic  # noqa: F401
from layout_canvas.blocks.base import (
    Block,
    all_blocks,
    get,
    on_pdk_registered,
    register,
    unregister,
)

__all__ = [
    "Block",
    "get",
    "register",
    "unregister",
    "all_blocks",
    "on_pdk_registered",
]
