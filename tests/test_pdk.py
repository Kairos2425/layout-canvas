"""Tests for the PDK descriptor layer."""

import pytest

from layout_canvas.pdk import all_pdks, get_pdk


def test_sky130_registered():
    pdk = get_pdk("sky130")
    assert pdk.layer("met1") == (68, 20)
    assert pdk.name in all_pdks()


def test_pin_label_layer_maps_purpose():
    pdk = get_pdk("sky130")
    assert pdk.pin_label_layer((68, 20)) == (68, 16)
    assert pdk.pin_label_layer(69) == (69, 16)
    assert pdk.pin_label_layer([70, 20]) == (70, 16)


def test_unknown_pdk_and_layer_named():
    with pytest.raises(KeyError, match="unknown pdk"):
        get_pdk("tsmc65")
    with pytest.raises(KeyError, match="unknown layer"):
        get_pdk("sky130").layer("met99")
