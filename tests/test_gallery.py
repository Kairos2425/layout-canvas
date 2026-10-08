"""Gallery publish/list: content-hash dedup, verification record, filters."""

import pytest

import layout_canvas.blocks.sky130  # noqa: F401
from layout_canvas.ir.model import Design
from layout_canvas.web import gallery


@pytest.fixture()
def gal(tmp_path, monkeypatch):
    monkeypatch.setenv("LAYOUT_CANVAS_GALLERY", str(tmp_path / "gal"))
    return gallery


def _design(name="gal_dp", pdk="sky130", block="sky130.diff_pair") -> Design:
    return Design.model_validate({
        "name": name,
        "pdk": pdk,
        "instances": [{"id": "dp", "block": block, "params": {"fingers": 2}}],
    })


def test_publish_records_hash_verification_and_ai_flag(gal):
    info = gal.publish(_design(), {"author": "tester", "tags": ["analog"],
                                   "ai_generated": True})
    assert info["design_hash"]
    assert info["ai_generated"] is True
    ver = info["verification"]
    assert ver["status"] == "ok"
    # the publish-time check really ran: clean design reports real numbers
    assert ver["drc_violations"] == 0
    assert ver["lvs_match"] is True
    assert ver["passed"] is True


def test_duplicate_publish_rejected_then_allowed(gal):
    gal.publish(_design())
    with pytest.raises(ValueError, match="duplicate of"):
        gal.publish(_design())
    # same content under a different name is still the same design
    with pytest.raises(ValueError, match="duplicate of"):
        gal.publish(_design(name="renamed"))
    ok = gal.publish(_design(), {"allow_duplicate": True})
    assert ok["id"]


def test_list_filters(gal):
    gal.publish(_design(), {"tags": ["analog"], "allow_duplicate": True})
    gal.publish(_design(name="other", block="sky130.current_mirror"),
                {"tags": ["digital"]})

    assert len(gal.list_entries()) == 2
    assert len(gal.list_entries(verified_only=True)) == 2
    assert [e["pdk"] for e in gal.list_entries(pdk="ihp_sg13g2")] == []
    assert {e["name"] for e in gal.list_entries(tag="analog")} == {"gal_dp"}
    assert {e["name"] for e in gal.list_entries(
        verified_only=True, tag="digital")} == {"other"}


def test_web_gallery_api_passthrough(gal):
    from layout_canvas.web.app import _api

    gal.publish(_design(), {"tags": ["analog"]})
    res = _api("gallery/list", {"tag": "analog"})
    assert res["status"] == "ok"
    assert len(res["data"]["entries"]) == 1
    res = _api("gallery/list", {"verified_only": True, "pdk": "nope"})
    assert res["data"]["entries"] == []
