"""Tests for Sky130 guard ring generator."""

from __future__ import annotations

import pytest
import gdsfactory as gf

from layout_canvas.blocks.base import get
from layout_canvas.blocks.sky130.guard_ring import build_guard_ring


def test_guard_ring_registration():
    block = get("sky130.guard_ring")
    assert block.name == "sky130.guard_ring"
    assert block.spec.pdk == "sky130"
    ring_type_param = next(p for p in block.spec.params if p.name == "ring_type")
    assert "ptap" in ring_type_param.choices
    assert "ntap" in ring_type_param.choices


def test_build_ptap_guard_ring():
    comp = build_guard_ring(inner_width=20.0, inner_height=10.0, ring_type="ptap")
    assert isinstance(comp, gf.Component)
    assert "tap" in comp.ports
    assert comp.ports["tap"].center[0] == pytest.approx(0.0)


def test_build_ntap_guard_ring():
    comp = build_guard_ring(inner_width=15.0, inner_height=15.0, ring_type="ntap", ring_width=1.0)
    assert isinstance(comp, gf.Component)
    assert "tap" in comp.ports
