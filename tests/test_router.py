"""Tests for differential and symmetric analog router."""

from __future__ import annotations

import gdsfactory as gf
import pytest

from layout_canvas.compiler.router import route_differential_pair, route_symmetric_nets


def test_route_differential_pair():
    c = gf.Component("test_diff_route")
    net_p = ((-10.0, 0.0), (10.0, 0.0))
    net_n = ((-10.0, -2.0), (10.0, -2.0))

    route_differential_pair(
        c,
        p_start=net_p[0],
        p_end=net_p[1],
        n_start=net_n[0],
        n_end=net_n[1],
        width=0.4,
        spacing=0.6,
        shield=True,
    )
    assert c.bbox().width() > 0


def test_route_symmetric_nets():
    c = gf.Component("test_sym_route")
    route_symmetric_nets(
        c,
        p_route=[(-15.0, 5.0), (-5.0, 5.0), (-5.0, 10.0)],
        n_route=[(15.0, 5.0), (5.0, 5.0), (5.0, 10.0)],
        width=0.5,
    )
    assert c.bbox().width() > 0
