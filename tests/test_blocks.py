"""Test block registry and parameter validation."""

import pytest

from layout_canvas.blocks.base import get
from layout_canvas.blocks.sky130.current_mirror import register_current_mirror


def test_block_registration():
    """Test block can be registered and retrieved."""
    register_current_mirror()
    block = get("sky130.current_mirror")
    assert block.name == "sky130.current_mirror"
    assert block.spec.pdk == "sky130"


def test_param_defaults():
    """Test parameter defaults."""
    register_current_mirror()
    block = get("sky130.current_mirror")
    defaults = block.defaults()
    assert defaults["fingers"] == 4
    assert defaults["width"] == 1.0


def test_param_coercion():
    """Test parameter type coercion and validation."""
    register_current_mirror()
    block = get("sky130.current_mirror")

    # Valid params
    resolved = block.resolve_params({"fingers": 8, "width": 2.5})
    assert resolved["fingers"] == 8
    assert resolved["width"] == 2.5

    # Type coercion
    resolved = block.resolve_params({"fingers": "8", "width": "2.5"})
    assert resolved["fingers"] == 8
    assert isinstance(resolved["width"], float)


def test_param_range_check():
    """Test parameter range validation."""
    register_current_mirror()
    block = get("sky130.current_mirror")

    # Below min
    with pytest.raises(ValueError, match="below min"):
        block.resolve_params({"fingers": 1})

    # Above max
    with pytest.raises(ValueError, match="above max"):
        block.resolve_params({"width": 20.0})


def test_param_choices():
    """Test parameter choice validation."""
    register_current_mirror()
    block = get("sky130.current_mirror")

    # Valid choice
    block.resolve_params({"type": "pmos"})

    # Invalid choice
    with pytest.raises(ValueError, match="not in"):
        block.resolve_params({"type": "jfet"})


def test_unknown_param():
    """Test unknown parameter rejection."""
    register_current_mirror()
    block = get("sky130.current_mirror")

    with pytest.raises(ValueError, match="unknown params"):
        block.resolve_params({"nonexistent": 42})
