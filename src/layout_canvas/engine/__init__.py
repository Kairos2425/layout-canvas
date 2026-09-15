"""Transactional edit engine — the sole mutation boundary for Block IR."""

from layout_canvas.engine.edits import OPS, apply_edits
from layout_canvas.engine.envelope import Diagnostic, Envelope
from layout_canvas.engine.optimize import optimize_session
from layout_canvas.engine.session import DesignSession

__all__ = [
    "OPS",
    "DesignSession",
    "Diagnostic",
    "Envelope",
    "apply_edits",
    "optimize_session",
]
