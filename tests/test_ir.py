"""Test Block IR model and validation."""

import pytest

from layout_canvas.ir.model import (
    Constraint,
    ConstraintType,
    Design,
    Instance,
    Net,
    Placement,
    Port,
)


def test_minimal_design():
    """Minimal valid design with one instance."""
    d = Design(
        name="test_chip",
        pdk="sky130",
        instances=[
            Instance(id="M1", block="sky130.current_mirror", params={"fingers": 4}),
        ],
    )
    assert d.name == "test_chip"
    assert len(d.instances) == 1
    json = d.to_json()
    d2 = Design.from_json(json)
    assert d2.name == d.name


def test_duplicate_instance_id():
    """Duplicate instance IDs should fail."""
    with pytest.raises(ValueError, match="duplicate"):
        Design(
            name="bad",
            pdk="sky130",
            instances=[
                Instance(id="M1", block="sky130.current_mirror"),
                Instance(id="M1", block="sky130.diff_pair"),
            ],
        )


def test_net_bad_pin_ref():
    """Net referencing non-existent instance should fail."""
    with pytest.raises(ValueError, match="bad pin reference"):
        Design(
            name="bad",
            pdk="sky130",
            instances=[Instance(id="M1", block="sky130.current_mirror")],
            nets=[Net(name="vdd", pins=["M1.in", "M2.out"])],
        )


def test_port_bad_pin_ref():
    """Port referencing non-existent instance should fail."""
    with pytest.raises(ValueError, match="bad pin reference"):
        Design(
            name="bad",
            pdk="sky130",
            instances=[Instance(id="M1", block="sky130.current_mirror")],
            ports=[Port(name="out", pin="M2.out")],
        )


def test_constraint_bad_instance():
    """Constraint referencing non-existent instance should fail."""
    with pytest.raises(ValueError, match="unknown instance"):
        Design(
            name="bad",
            pdk="sky130",
            instances=[Instance(id="M1", block="sky130.current_mirror")],
            constraints=[Constraint(type=ConstraintType.symmetric, instances=["M1", "M2"])],
        )


def test_placement_rotations():
    """Test placement rotation values."""
    p = Placement(x=1.0, y=2.0, rotation=90)
    assert p.rotation == 90

    with pytest.raises(ValueError):
        Placement(rotation=45)  # Only 0/90/180/270 allowed


def test_relative_placement_schema():
    """Test relative placement attributes in Placement model."""
    p = Placement(
        relative_to="inst_a",
        relation="right_of",
        align="bottom",
        margin=2.5,
    )
    assert p.relative_to == "inst_a"
    assert p.relation == "right_of"
    assert p.margin == 2.5


def test_constraint_can_name_routing_nets_and_reject_unknown_net():
    d = Design(
        name="routing_constraints",
        pdk="sky130",
        instances=[
            Instance(id="A", block="sky130.current_mirror"),
            Instance(id="B", block="sky130.current_mirror"),
        ],
        nets=[Net(name="p", pins=["A.out", "B.in"]), Net(name="n", pins=["A.in", "B.out"])],
        constraints=[Constraint(type=ConstraintType.symmetric,
                                instances=["A", "B"], nets=["p", "n"])],
    )
    assert d.constraints[0].nets == ["p", "n"]
    with pytest.raises(ValueError, match="unknown net"):
        Design(
            name="bad_routing_constraints",
            pdk="sky130",
            instances=[Instance(id="A", block="sky130.current_mirror")],
            constraints=[Constraint(type=ConstraintType.symmetric,
                                    instances=["A"], nets=["p", "n"])],
        )
