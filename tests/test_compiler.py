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


def test_ppa_extraction_and_optimizer():
    """Test PPA metric extraction and optimizer iteration."""
    from layout_canvas.compiler.optimizer import optimize_design_layout
    from layout_canvas.compiler.ppa import extract_ppa

    design = Design(
        name="test_ppa_design",
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
                    margin=2.0,
                ),
            ),
        ],
        nets=[Net(name="shared", pins=["I0.inp", "I1.in"])],
        ports=[Port(name="input", pin="I0.inp")],
    )

    comp = compile_design(design)
    ppa = extract_ppa(comp, design)
    assert ppa["area_um2"] > 0
    assert ppa["instance_count"] == 2
    assert ppa["net_count"] == 1
    assert ppa["port_count"] == 1
    assert ppa["hpwl_um"] > 0
    assert ppa["net_hpwl_um"]["shared"] == pytest.approx(24.175, abs=0.001)
    assert ppa["estimated_wire_length_um"] == ppa["hpwl_um"]
    assert ppa["power"] is None and ppa["power_status"] == "unavailable"
    assert ppa["performance"] is None and ppa["performance_status"] == "unavailable"

    opt_design, info = optimize_design_layout(design, target_aspect_ratio=1.0)
    assert info["iterations"] >= 1
    assert "final_ppa" in info


def test_ppa_hpwl_is_zero_for_single_pin_or_unresolved_net():
    """A net without two resolved physical pins must not invent a wire span."""
    from layout_canvas.compiler.ppa import extract_ppa

    design = Design(
        name="test_ppa_single_pin",
        pdk="sky130",
        instances=[Instance(id="I0", block="sky130.current_mirror")],
        nets=[Net(name="one_pin", pins=["I0.in"])],
    )
    ppa = extract_ppa(compile_design(design), design)
    assert ppa["net_count"] == 1
    assert ppa["net_hpwl_um"] == {"one_pin": 0.0}
    assert ppa["hpwl_um"] == 0.0
