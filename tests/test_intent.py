"""Design-intent channel tests (work-plan P2).

``design.intent`` carries *why* as structured data — matched_groups,
sensitive_nets, param_rationale, notes — validated like every other IR
reference and cross-reported (never gated) by the quality stage as
``intent_coverage``.
"""

from pathlib import Path

import pytest

import layout_canvas.blocks  # noqa: F401 — populate registry
from layout_canvas.compiler.compile import compile_design
from layout_canvas.compiler.netlist_import import import_netlist
from layout_canvas.ir.model import Design
from layout_canvas.tools.matching import check_constraints


def _design(**extra) -> dict:
    d = {
        "name": "intent_test",
        "pdk": "sky130",
        "instances": [
            {"id": "a", "block": "sky130.diff_pair", "params": {}},
            {"id": "b", "block": "sky130.diff_pair", "params": {}},
        ],
        "nets": [{"name": "tail", "pins": ["a.tail", "b.tail"]}],
        "ports": [],
    }
    d.update(extra)
    return d


_INTENT = {
    "matched_groups": [["a", "b"]],
    "sensitive_nets": [
        {"net": "tail", "kind": "critical_bias", "reason": "tail node"},
    ],
    "param_rationale": [
        {"target": "a.width", "note": "gm sized for bias current"},
    ],
    "notes": ["halves must stay matched and mirrored"],
}


class TestIntentModel:
    def test_intent_loads_and_roundtrips(self):
        d = Design.model_validate(_design(intent=_INTENT))
        assert d.intent is not None
        assert d.intent.matched_groups == [["a", "b"]]
        assert d.intent.sensitive_nets[0].kind == "critical_bias"
        assert d.intent.param_rationale[0].target == "a.width"
        d2 = Design.from_json(d.to_json())
        assert d2.intent == d.intent

    def test_absent_intent_stays_none(self):
        d = Design.model_validate(_design())
        assert d.intent is None
        assert Design.from_json(d.to_json()).intent is None

    def test_strict_intent_rejects_extra_keys(self):
        bad = dict(_INTENT, bogus=1)
        with pytest.raises(ValueError):
            Design.model_validate(_design(intent=bad))

    @pytest.mark.parametrize("bad_intent, frag", [
        (dict(_INTENT, matched_groups=[["a", "ghost"]]), "unknown instance"),
        (dict(_INTENT, matched_groups=[[]]), "empty group"),
        (dict(_INTENT, sensitive_nets=[
            {"net": "nope", "kind": "other", "reason": "x"}]),
         "unknown net"),
        (dict(_INTENT, sensitive_nets=[
            {"net": "tail", "kind": "vibes", "reason": "x"}]), "kind"),
        (dict(_INTENT, param_rationale=[
            {"target": "ghost.width", "note": "x"}]), "bad target"),
        (dict(_INTENT, param_rationale=[
            {"target": "noDotsHere", "note": "x"}]), "bad target"),
    ])
    def test_bad_refs_fail_validation(self, bad_intent, frag):
        with pytest.raises(ValueError, match=frag):
            Design.model_validate(_design(intent=bad_intent))


class TestIntentCoverage:
    """``check_constraints`` cross-reports declaration consistency —
    geometry verdicts stay with the constraint checks; this is about the
    declared intent agreeing with the declared checks."""

    def test_covered_group_reports_clean(self):
        d = Design.model_validate(_design(
            intent=_INTENT,
            constraints=[
                {"type": "match", "instances": ["a", "b"]},
                {"type": "symmetric", "instances": ["a", "b"],
                 "axis": "vertical"},
            ]))
        res = check_constraints(d, component=compile_design(d))
        cov = res["intent_coverage"]
        assert cov["declared_groups"] == [["a", "b"]]
        assert cov["groups_without_constraint"] == []
        assert cov["constraints_without_intent"] == []
        assert "covered" in cov["note"]

    def test_group_without_constraint_is_a_note_not_a_gate(self):
        # intent declares a matched group but nothing checks it — a note,
        # never a fabricated fail on geometry that was never asked for.
        d = Design.model_validate(_design(intent=_INTENT))
        res = check_constraints(d, component=compile_design(d))
        assert res["status"] == "not_applicable"
        cov = res["intent_coverage"]
        assert cov["groups_without_constraint"] == [["a", "b"]]

    def test_constraint_without_intent_is_a_note_not_a_gate(self):
        d = Design.model_validate(_design(
            constraints=[{"type": "match", "instances": ["a", "b"]}]))
        res = check_constraints(d, component=compile_design(d))
        assert res["intent_coverage"] is None  # no intent declared
        # and a constraint wider than the declared group surfaces the
        # members the intent never claimed
        d2 = Design.model_validate(_design(
            intent={
                "matched_groups": [["a"]],
            },
            constraints=[{"type": "match", "instances": ["a", "b"]}]))
        res2 = check_constraints(d2, component=compile_design(d2))
        cov2 = res2["intent_coverage"]
        # {"a"} ⊆ {"a","b"} — the group is covered; 'b' sits outside intent
        assert cov2["groups_without_constraint"] == []
        assert cov2["constraints_without_intent"][0]["outside_intent"] == ["b"]
        assert res2["status"] == "pass"  # geometry still decides


class TestImportNetlistIntent:
    SPICE_PAIRS = """\
.subckt current_mirror in out gate vss
M1 out in vss vss nch w=1u l=0.5u
M2 gate in vss vss nch w=1u l=0.5u
.ends current_mirror
.subckt diff_pair inp inn outp outn tail vss
M1 outp inp tail vss nch w=2u l=0.15u
M2 outn inn tail vss nch w=2u l=0.15u
.ends diff_pair
.subckt top inp inn outp outn vbias vref vss
XDP inp inn outp outn tail vss diff_pair
XBIAS vbias tail vbias vss current_mirror
XREF vref vref vref vss current_mirror
.ends top
"""

    def test_import_populates_intent(self):
        res = import_netlist(self.SPICE_PAIRS, pdk="sky130", top="top")
        assert res["status"] == "ok"
        d = Design.from_json(res["ir_text"])
        assert d.intent is not None
        # two identical current_mirror instances on the same subckt
        # master → one declared matched group
        assert d.intent.matched_groups == [["XBIAS", "XREF"]]
        nets = {s.net: s for s in d.intent.sensitive_nets}
        # 'tail' touches XDP.tail → critical_bias; 'vbias' joins
        # XBIAS.in+XBIAS.gate → diode bias node.
        assert nets["tail"].kind == "critical_bias"
        assert nets["vbias"].kind == "critical_bias"
        targets = {p.target for p in d.intent.param_rationale}
        assert "XBIAS.type" in targets  # nch devices → type=nmos

    def test_no_inference_means_no_fabricated_intent(self):
        net = (".subckt current_mirror in out gate vss\n"
               "M1 out in vss vss nch w=1u\n.ends\n"
               ".subckt top a b\nXU1 a b b vss current_mirror\n.ends\n")
        res = import_netlist(net, pdk="sky130", top="top")
        d = Design.from_json(res["ir_text"])
        # single instance, no tail/diode net → nothing was inferred;
        # type IS inferred (nch body) so rationale may exist — but
        # matched_groups and sensitive_nets stay empty.
        if d.intent is not None:
            assert d.intent.matched_groups == []
            assert d.intent.sensitive_nets == []


_GOLDEN = Path(__file__).resolve().parents[1] / "examples" / "golden_diffpair.json"


class TestGoldenIntent:
    def test_golden_carries_real_intent(self):
        pytest.importorskip("klayout.db")
        d = Design.from_json(_GOLDEN.read_text(encoding="utf-8"))
        assert d.intent is not None
        assert d.intent.matched_groups == [["DP_L", "DP_R"]]
        kinds = {s.net: s.kind for s in d.intent.sensitive_nets}
        assert kinds["tail"] == "critical_bias"
        assert kinds["vbn"] == "critical_bias"
        assert all(p.target.split(".")[0] in
                   {i.id for i in d.instances}
                   for p in d.intent.param_rationale)

    def test_golden_intent_coverage_is_clean(self):
        pytest.importorskip("klayout.db")
        d = Design.from_json(_GOLDEN.read_text(encoding="utf-8"))
        cov = check_constraints(d, component=compile_design(d))[
            "intent_coverage"]
        assert cov["groups_without_constraint"] == []
        assert cov["constraints_without_intent"] == []
