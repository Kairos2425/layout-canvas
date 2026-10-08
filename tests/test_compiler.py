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


def test_relative_placement_is_edge_to_edge():
    """margins and aligns are measured between instance bboxes, not
    origins — a cell whose geometry extends left of its origin must not
    eat the declared gap."""
    from layout_canvas.compiler.compile import _resolve_relative_placements

    # A block whose bbox runs -5..+2 in x and -1..+3 in y around its origin
    def _cell(name: str, x0: float, y0: float, x1: float, y1: float):
        c = gf.Component(name=name)
        c.add_polygon(
            [(x0, y0), (x1, y0), (x1, y1), (x0, y1)], layer=(1, 0))
        return c

    ref = _cell("ref_cell", -5.0, -1.0, 2.0, 3.0)
    cur = _cell("cur_cell", -1.0, -0.5, 3.0, 1.5)
    comp_map = {"a": ref, "b": cur}

    def _coords(relation=None, align=None, margin=0.0, dx=0.0, dy=0.0):
        design = Design(
            name="t", pdk="sky130",
            instances=[
                Instance(id="a", block="sky130.diff_pair"),
                Instance(id="b", block="sky130.diff_pair",
                         placement=Placement(
                             relative_to="a", relation=relation,
                             align=align, margin=margin, x=dx, y=dy)),
            ])
        return _resolve_relative_placements(design, comp_map)

    # right_of: b's left edge (calc_x + -1) lands 2µm past a's right edge
    x, _y = _coords(relation="right_of", margin=2.0)["b"]
    assert (x + -1.0) - (0.0 + 2.0) == pytest.approx(2.0)
    # left_of: b's right edge sits 1.5µm below a's left edge
    x, _y = _coords(relation="left_of", margin=1.5)["b"]
    assert (0.0 + -5.0) - (x + 3.0) == pytest.approx(1.5)
    # below / above mirror the same edge semantics on y
    _x, y = _coords(relation="below", margin=0.5)["b"]
    assert (0.0 + -1.0) - (y + 1.5) == pytest.approx(0.5)
    _x, y = _coords(relation="above", margin=0.0)["b"]
    assert (y + -0.5) - (0.0 + 3.0) == pytest.approx(0.0)
    # aligns: edges coincide; centers use the bbox midpoint
    assert _coords(align="bottom")["b"][1] == pytest.approx(-0.5)
    assert _coords(align="top")["b"][1] == pytest.approx(1.5)
    assert _coords(align="left")["b"][0] == pytest.approx(-4.0)
    assert _coords(align="right")["b"][0] == pytest.approx(-1.0)
    assert _coords(align="center_x")["b"][0] == pytest.approx(-2.5)
    assert _coords(align="center_y")["b"][1] == pytest.approx(0.5)
    # manual deltas still stack on top of the edge placement
    x, y = _coords(relation="right_of", margin=1.0, dx=0.3, dy=0.7)["b"]
    assert (x + -1.0) - 2.0 == pytest.approx(1.3)
    assert y == pytest.approx(0.7)


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
    assert ppa["net_hpwl_um"]["shared"] == pytest.approx(17.25, abs=0.001)
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
