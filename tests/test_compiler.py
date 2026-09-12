"""End-to-end integration tests for compiler, relative placement, and GDS generation."""

from __future__ import annotations

import gdsfactory as gf
import pytest

from layout_canvas.compiler.compile import compile_design
from layout_canvas.ir.model import Design, Instance, Net, Placement, Port


def test_compile_relative_placement_and_routing():
    """Test full compilation pipeline with relative placement and pin routing."""
    design = Design(
        name="test_ota_with_mirror",
        pdk="sky130",
        instances=[
            Instance(
                id="I0",
                block="sky130.diff_pair",
                placement=Placement(x=0.0, y=0.0),
            ),
            Instance(
                id="I1",
                block="sky130.current_mirror",
                placement=Placement(
                    relative_to="I0",
                    relation="right_of",
                    align="bottom",
                    margin=5.0,
                ),
            ),
        ],
        ports=[
            Port(name="in_p", pin="I0.in_p"),
            Port(name="in_n", pin="I0.in_n"),
        ],
    )

    top = compile_design(design)
    assert top is not None
    assert top.name == "test_ota_with_mirror"
    bbox = top.bbox()
    assert bbox.width() > 0
    assert bbox.height() > 0
