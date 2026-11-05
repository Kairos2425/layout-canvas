"""Intra-cell finger-pattern inspection tests (tools/patterns.py).

``inspect_intracell`` opens the cell boundary the constraint checker
cannot see through: on the canonical LayoutToNetlist extraction it
collects every ``poly ∩ diff`` gate shape, resolves each gate's flat
cluster (net) and reports the interdigitation pattern, the common-
centroid deviation and edge-dummy evidence per instance.

Every number asserted here comes from the real compiled GDS + the real
KLayout extraction — no fabricated fixtures.
"""
from pathlib import Path

import pytest

pytest.importorskip("klayout.db", reason="klayout python module required")

import gdsfactory as gf

import layout_canvas.blocks.generic  # noqa: F401 - registers generic blocks
import layout_canvas.blocks.sky130  # noqa: F401 - registers sky130 blocks
from layout_canvas.blocks import base
from layout_canvas.blocks.sky130 import layers
from layout_canvas.blocks.sky130.geom import add_port, rect, snap
from layout_canvas.compiler.compile import compile_design
from layout_canvas.ir.model import BlockSpec, Design, PortSpec
from layout_canvas.tools.matching import check_constraints
from layout_canvas.tools.patterns import inspect_intracell

_GOLDEN = (Path(__file__).resolve().parents[1]
           / "examples" / "golden_diffpair.json")


def _golden() -> Design:
    return Design.from_json(_GOLDEN.read_text(encoding="utf-8"))


def _finger_cell(name: str, pattern: list[str]) -> gf.Component:
    """Minimal multi-net fingered cell — one poly gate bar per finger,
    each gate stub landed on the li1 strap row dedicated to its net.

    ``pattern`` maps finger index → strap key; each distinct strap is an
    independent li1 row, so the straps extract as separate clusters.
    A met1 pad + port per strap lets a parent design label the net
    (``vss`` reads as a supply/bulk net; unnamed straps stay unlabeled).
    """
    c = gf.Component(name)
    pitch, half_l = 1.7, 0.25
    diff_y0, diff_h = 3.0, 2.0
    sides = list(dict.fromkeys(pattern))
    strap_y = {s: snap(1.95 - 0.7 * k) for k, s in enumerate(sides)}
    total_w = pitch * len(pattern)
    rect(c, layers.DIFF, 0, diff_y0, total_w, diff_y0 + diff_h)
    for i, side in enumerate(pattern):
        fx = snap(i * pitch + pitch / 2)
        sy = strap_y[side]
        # gate bar + poly stub down to this net's strap row
        rect(c, layers.POLY, fx - half_l, 2.8, fx + half_l,
             diff_y0 + diff_h + 0.2)
        rect(c, layers.POLY, fx - 0.2, sy, fx + 0.2, 2.995)
        rect(c, layers.LICON, fx - 0.085, sy + 0.065,
             fx + 0.085, sy + 0.235)
        rect(c, layers.LI, fx - 0.15, sy - 0.05, fx + 0.15, sy + 0.35)
    for side in dict.fromkeys(pattern):
        sy = strap_y[side]
        xs = [snap(i * pitch + pitch / 2)
              for i, s in enumerate(pattern) if s == side]
        rect(c, layers.LI, min(xs) - 0.15, sy, max(xs) + 0.15, sy + 0.3)
        px = snap(sum(xs) / len(xs))
        rect(c, layers.MCON, px - 0.065, sy + 0.085,
             px + 0.065, sy + 0.215)
        rect(c, layers.MET1, px - 0.24, sy - 0.05, px + 0.24, sy + 0.35)
        add_port(c, side, layers.MET1, (px, sy + 0.15), 0.4, 0)
    return c


def _register(alias: str, pattern: list[str]) -> None:
    comp = _finger_cell(f"cell_{alias.replace('.', '_')}", pattern)
    spec = BlockSpec(
        name=alias, pdk="sky130", level="L0",
        summary="synthetic fingered cell (test fixture)",
        params=[],
        ports=[PortSpec(name=s, layer="met1", direction="inout",
                        tap_layer="met1")
               for s in dict.fromkeys(pattern)],
        constraints=[], tags=["test"],
    )
    base._REGISTRY[alias] = base.Block(
        spec=spec, build=lambda **_: comp, netlist=None)


def _unregister(*aliases: str) -> None:
    for a in aliases:
        base._REGISTRY.pop(a, None)


def test_golden_abba_pattern_and_centroid():
    """golden_diffpair's DP_L/DP_R must measure as ABBA with ~0 centroid
    deviation — the real numbers, not the claimed ones."""
    d = _golden()
    res = inspect_intracell(d, component=compile_design(d))
    assert res["status"] == "ok"
    assert res["unassigned_fingers"] == 0

    for iid, expected_cx in (("DP_L", -8.2), ("DP_R", 8.2)):
        e = res["instances"][iid]
        assert e["status"] == "pass"
        assert e["fingers"] == 8
        assert e["axis"] == "x"
        assert e["pattern"] == "ABBAABBA"
        assert e["centroid_deviation_um"] == pytest.approx(0.0, abs=1e-3)
        nets = {n["net"]: n for n in e["nets"]}
        assert set(nets) == {"inp", "inn"}
        assert nets["inp"]["finger_count"] == 4
        assert nets["inn"]["finger_count"] == 4
        assert nets["inp"]["centroid_um"] == pytest.approx(expected_cx,
                                                         abs=1e-3)
        assert nets["inn"]["centroid_um"] == pytest.approx(expected_cx,
                                                         abs=1e-3)
        # edge fingers are on the signal nets — no dummies detected
        assert e["dummy_count"] == 0
        assert len(e["dummy_edges"]) == 2
        assert all(not ed["is_dummy"] for ed in e["dummy_edges"])

    # Mirror cells tie ALL gates to one net — a single-net pattern can
    # never be scored for common centroid: structural, honestly noted.
    for iid in ("LOAD", "TAIL"):
        e = res["instances"][iid]
        assert e["status"] == "structural"
        assert e["pattern"] == "AAAAAAAA"
        assert e["centroid_deviation_um"] is None
        assert "no centroid pair" in e["note"]

    # Guard ring carries no MOS gates: unavailable, never a pass.
    assert res["instances"]["GR"]["status"] == "unavailable"
    assert res["instances"]["GR"]["fingers"] == 0


def test_synthetic_non_common_centroid_deviation():
    """AABB finger order is NOT common centroid — the measured deviation
    must surface, and a declared match constraint must fail on it."""
    _register("test.aabb", ["ga", "ga", "gb", "gb"])
    try:
        d = Design.model_validate({
            "name": "aabb_test", "pdk": "sky130",
            "instances": [
                {"id": "u1", "block": "test.aabb", "params": {},
                 "placement": {"x": 0, "y": 0}},
                {"id": "u2", "block": "test.aabb", "params": {},
                 "placement": {"x": 10, "y": 0}},
            ],
            "constraints": [{"type": "match",
                             "instances": ["u1", "u2"]}],
        })
        res = inspect_intracell(d, component=compile_design(d))
        e = res["instances"]["u1"]
        assert e["fingers"] == 4
        assert e["pattern"] == "AABB"
        # ga fingers at x=0.85/2.55 (centroid 1.7), gb at 4.25/5.95
        # (centroid 5.1) → 3.4 µm measured spread.
        assert e["centroid_deviation_um"] == pytest.approx(3.4, abs=0.05)
        assert e["status"] == "structural"
        assert "EXCEEDS" in e["note"]

        # Declared match: measured centroid mismatch is a real defect —
        # the check fails and carries the evidence in warnings + detail.
        res2 = check_constraints(d, component=compile_design(d))
        chk = res2["checks"][0]
        assert chk["type"] == "match" and chk["status"] == "fail"
        assert chk["intracell"]["warnings"]
        assert "3.4" in chk["note"]
        assert res2["status"] == "fail"
    finally:
        _unregister("test.aabb")


def test_cell_without_gates_reports_unavailable():
    """A cell with no poly∩diff is ``unavailable`` — never a pass."""
    d = Design.model_validate({
        "name": "nogates", "pdk": "sky130",
        "instances": [
            {"id": "gr", "block": "sky130.guard_ring",
             "params": {"inner_width": 10.0, "inner_height": 10.0}},
        ],
    })
    res = inspect_intracell(d, component=compile_design(d))
    # The whole layout has no MOS gate → the inspection itself is
    # unavailable and every instance reports it.
    assert res["status"] == "unavailable"
    e = res["instances"]["gr"]
    assert e["status"] == "unavailable"
    assert e["fingers"] == 0
    assert e["pattern"] is None


def test_quality_section_carries_intracell_detail():
    """The old caveat 'intra-cell … not inspected' is now measured data:
    the match/symmetric notes must carry the detected pattern + dev."""
    d = _golden()
    res = check_constraints(d, component=compile_design(d))
    assert res["status"] == "pass"
    by_type = {c["type"]: c for c in res["checks"]}

    match = by_type["match"]
    assert "intracell" in match
    assert "not inspected" not in match["note"]
    assert "intra-cell:" in match["note"]
    assert "ABBAABBA" in match["note"]
    assert "dev=0.0000" in match["note"]
    assert match["intracell"]["warnings"] == []
    assert (match["intracell"]["instances"]["DP_L"]["pattern"]
            == "ABBAABBA")
    assert (match["intracell"]["instances"]["DP_L"]
            ["centroid_deviation_um"] == pytest.approx(0.0, abs=1e-3))

    sym = by_type["symmetric"]
    assert "intracell" in sym
    assert "intra-cell:" in sym["note"]
    assert "ABBAABBA" in sym["note"]


def test_supply_edge_dummies_detected_and_limits_noted():
    """Edge fingers on a supply-named net ARE detectable (and reported);
    the note still bounds what can't be detected (signal-net-strapped
    dummies, unlabeled edge nets)."""
    _register("test.dabbad", ["vss", "ga", "gb", "gb", "ga", "vss"])
    try:
        d = Design.model_validate({
            "name": "dabbad_test", "pdk": "sky130",
            "instances": [
                {"id": "u1", "block": "test.dabbad", "params": {},
                 "placement": {"x": 0, "y": 0}},
            ],
            "ports": [{"name": "vss", "pin": "u1.vss",
                       "direction": "supply"}],
        })
        res = inspect_intracell(d, component=compile_design(d))
        e = res["instances"]["u1"]
        assert e["fingers"] == 6
        # vss→A, ga→B, gb→C → "ABCCBA"; ga/gb centroids coincide at 5.1.
        assert e["pattern"] == "ABCCBA"
        assert e["centroid_deviation_um"] == pytest.approx(0.0, abs=1e-3)
        assert e["status"] == "pass"
        # both edge fingers sit on the vss strap → detected dummies
        assert e["dummy_count"] == 2
        assert all(ed["is_dummy"] and ed["kind"] == "supply_or_bulk"
                   for ed in e["dummy_edges"])
        assert "edge dummies detected" in e["note"]
        # ...but a dummy sharing a signal gate net is undetectable and
        # the note says so instead of claiming coverage.
        assert "indistinguishable" in e["note"]
        assert "not detected" in e["note"]
    finally:
        _unregister("test.dabbad")


def test_unlabeled_edge_net_is_undetermined_not_guessed():
    """An edge finger on an unlabeled net is reported 'unlabeled' —
    dummy status undetermined — and never folded into the centroid."""
    _register("test.uaabb", ["dd", "ga", "gb", "gb", "ga", "dd"])
    try:
        d = Design.model_validate({
            "name": "uaabb_test", "pdk": "sky130",
            "instances": [
                {"id": "u1", "block": "test.uaabb", "params": {},
                 "placement": {"x": 0, "y": 0}},
            ],
        })
        res = inspect_intracell(d, component=compile_design(d))
        e = res["instances"]["u1"]
        assert e["fingers"] == 6
        # dd→A, ga→B, gb→C → "ABCCBA"; ga/gb centroids coincide at 5.1.
        assert e["pattern"] == "ABCCBA"
        assert e["centroid_deviation_um"] == pytest.approx(0.0, abs=1e-3)
        assert e["status"] == "pass"
        # the dd net only ever sits at the row edges → reported
        # 'unlabeled' + excluded from the centroid figure of merit —
        # undetermined, never guessed as signal or dummy.
        edge_kinds = {ed["side"]: ed["kind"] for ed in e["dummy_edges"]}
        assert edge_kinds == {"left": "unlabeled", "right": "unlabeled"}
        assert e["dummy_count"] == 0
        assert "undetermined" in e["note"]
        uedge = next(n for n in e["nets"] if n["kind"] == "unlabeled")
        assert uedge["eligible"] is False
        assert uedge["edge_only"] is True
    finally:
        _unregister("test.uaabb")
