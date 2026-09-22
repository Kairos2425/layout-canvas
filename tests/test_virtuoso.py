"""Virtuoso bridge + gallery tests."""
from pathlib import Path

import pytest

import layout_canvas.blocks.sky130  # noqa: F401
from layout_canvas.compiler.compile import compile_design
from layout_canvas.compiler.netlist import compile_netlist
from layout_canvas.compiler.virtuoso import export_skill, export_spectre
from layout_canvas.ir.model import Design

pytest.importorskip("klayout.db", reason="klayout module required for SKILL export")


def _design() -> Design:
    return Design.model_validate({
        "name": "vt_test",
        "pdk": "sky130",
        "instances": [
            {"id": "cm", "block": "sky130.current_mirror", "params": {"fingers": 2}},
        ],
        "ports": [{"name": "IN", "pin": "cm.in", "direction": "input"}],
    })


def test_skill_rebuilds_hierarchy(tmp_path: Path):
    design = _design()
    comp = compile_design(design)
    gds = tmp_path / "vt.gds"
    comp.write_gds(str(gds))
    skill = export_skill(gds, "canvas_lib", "sky130")

    # child cellview created before the parent instantiates it
    child_pos = skill.index('dbOpenCellViewByType("canvas_lib" '
                            '"current_mirror_f2_w1_0_l0_15_nmos"')
    inst_pos = skill.index('dbCreateInst(cv master')
    assert 0 < child_pos < inst_pos
    # real geometry: mapped OA layer names, not raw numbers
    assert 'list("diff" "drawing")' in skill
    assert 'list("poly" "drawing")' in skill
    # pin labels carried over
    assert 'dbCreateLabel' in skill and '"IN"' in skill
    assert 'ddCreateLib("canvas_lib"' in skill


def test_spectre_dialect(tmp_path: Path):
    spice = compile_netlist(_design())
    scs = export_spectre(spice)
    assert scs.startswith("// generated")
    assert "simulator lang=spectre" in scs
    assert "subckt sky130_current_mirror__" in scs
    assert " ( in out gate )" in scs
    assert "X1 ( out gate in in ) sky130_fd_pr__nfet_01v8" in scs
    assert "ends vt_test" in scs
    assert ".subckt" not in scs and ".ends" not in scs


def test_gallery_publish_list_fork(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("LAYOUT_CANVAS_GALLERY", str(tmp_path / "gallery"))
    import layout_canvas.web.gallery as gallery
    from layout_canvas.web.app import _api

    res = _api("gallery/publish", {
        "ir_json": _design().model_dump(),
        "meta": {"author": "tester", "tags": ["mirror"]},
    })
    assert res["status"] == "ok"
    eid = res["data"]["id"]
    entry_dir = gallery.gallery_root() / eid
    assert (entry_dir / "design.json").is_file()
    assert (entry_dir / "preview.svg").read_text().startswith("<svg")

    res = _api("gallery/list", {})
    assert any(e["id"] == eid for e in res["data"]["entries"])

    res = _api("gallery/fork", {"id": eid})
    assert res["status"] == "ok"
    assert res["data"]["design"]["name"] == "vt_test"

    res = _api("gallery/get", {"id": "missing"})
    assert res["status"] == "error"


def test_gallery_git_sync_two_users(tmp_path: Path, monkeypatch):
    """Real co-building: two gallery roots sharing a bare remote exchange
    published designs via pull/push — no mocks, actual git."""
    import shutil
    import subprocess
    if shutil.which("git") is None:
        pytest.skip("git not on PATH")

    import layout_canvas.web.gallery as gallery

    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)

    # User A publishes and pushes.
    a = tmp_path / "alice"
    monkeypatch.setenv("LAYOUT_CANVAS_GALLERY", str(a))
    info = gallery.publish(_design(), {"author": "alice"})
    res = gallery.sync(str(remote))
    assert res["status"] == "ok", res

    # User B pulls and sees Alice's design — a real clone-free exchange.
    b = tmp_path / "bob"
    monkeypatch.setenv("LAYOUT_CANVAS_GALLERY", str(b))
    res = gallery.sync(str(remote))
    assert res["status"] == "ok", res
    entries = gallery.list_entries()
    assert any(e["id"] == info["id"] and e["author"] == "alice"
               for e in entries)

    # Bob forks it, publishes his own variant, Alice pulls it back.
    b_design = _design().model_copy(update={"name": "vt_test_bob"})
    info_b = gallery.publish(b_design, {"author": "bob"})
    res = gallery.sync(str(remote))
    assert res["status"] == "ok", res
    monkeypatch.setenv("LAYOUT_CANVAS_GALLERY", str(a))
    res = gallery.sync(str(remote))
    assert res["status"] == "ok", res
    assert any(e["id"] == info_b["id"] for e in gallery.list_entries())


def test_web_health_and_version():
    from layout_canvas.web.app import _version
    assert _version()["version"]  # SOURCE_COMMIT or git sha or "dev"


def test_web_virtuoso_action(tmp_path: Path):
    from layout_canvas.web.app import _api
    res = _api("virtuoso", {"ir_json": _design().model_dump(),
                            "library": "testlib"})
    assert res["status"] == "ok"
    assert 'ddCreateLib("testlib"' in res["data"]["skill"]
    assert 'dbCreateInst' in res["data"]["skill"]
    assert "simulator lang=spectre" in res["data"]["spectre"]
