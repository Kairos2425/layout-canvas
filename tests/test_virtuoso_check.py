"""Virtuoso acceptance harness: static checks + remote-leg orchestration.

The remote tests never claim Cadence ran — they exercise our side of the
SSH contract (command construction, log parsing, census diffing) with a
stubbed ``_run``/``_scp`` and are labelled as such.
"""

import subprocess
from pathlib import Path

import pytest

import layout_canvas.blocks.sky130  # noqa: F401
from layout_canvas.compiler.compile import compile_design
from layout_canvas.compiler.netlist import compile_netlist
from layout_canvas.compiler.virtuoso import export_skill
from layout_canvas.ir.model import Design
from layout_canvas.tools import virtuoso_check

pytest.importorskip("klayout.db",
                    reason="klayout module required for SKILL export")

ROOT = Path(__file__).parent.parent
DIFFAMP = ROOT / "examples" / "diffamp.json"


def _design() -> Design:
    import json

    return Design.model_validate(
        json.loads(DIFFAMP.read_text(encoding="utf-8")))


def _compile(design: Design, tmp_path: Path) -> Path:
    gds = tmp_path / f"{design.name}.gds"
    compile_design(design).write_gds(str(gds))
    return gds


def _statuses(checks):
    return {c["name"].split(":")[0] if ":" in c["name"] else c["name"]: c
            for c in checks}


class TestStaticReport:
    def test_diffamp_static_ok(self, tmp_path):
        design = _design()
        gds = _compile(design, tmp_path)
        rep = virtuoso_check.static_report(
            gds, "sky130", spice_text=compile_netlist(design))
        assert rep["status"] == "static_ok", [
            c for c in rep["checks"] if c["status"] != "pass"]
        assert rep["cells"] >= 2  # top + leaf cells
        assert rep["unmapped_layers"] == []
        assert Path(rep["paths"]["il"]).is_file()
        assert Path(rep["paths"]["scs"]).is_file()
        # every check actually ran with real evidence
        names = {c["name"] for c in rep["checks"]}
        assert "skill_parens" in names
        assert "scs_subckt_ends" in names
        assert any(n.startswith("skill:") and n.endswith(":insts")
                   for n in names)

    def test_extracted_netlist_scs_when_no_spice_given(self, tmp_path):
        """Default path derives the .scs by real LVS extraction."""
        gds = _compile(_design(), tmp_path)
        rep = virtuoso_check.static_report(gds, "sky130")
        assert rep["status"] == "static_ok", [
            c for c in rep["checks"] if c["status"] != "pass"]
        assert Path(rep["paths"]["scs"]).is_file()
        assert "subckt" in Path(rep["paths"]["scs"]).read_text()

    def test_unmapped_layers_reported_not_failed(self, tmp_path):
        """A layer outside every map still reports static_ok, with the
        L*_D* fallback names listed under unmapped_layers."""
        design = _design()
        gds = _compile(design, tmp_path)
        rep = virtuoso_check.static_report(
            gds, "no_such_pdk", spice_text=compile_netlist(design))
        assert rep["status"] == "static_ok"
        assert rep["unmapped_layers"], "expect every layer unmapped"
        il = Path(rep["paths"]["il"]).read_text()
        assert 'list("L' in il  # fallback names emitted, nothing dropped

    def test_skill_check_detects_missing_cell(self, tmp_path):
        design = _design()
        gds = _compile(design, tmp_path)
        ly, cells, _ = virtuoso_check._layout_stats(gds)
        names = virtuoso_check._oa_cell_names(ly)
        il = export_skill(gds, "canvas_lib", "sky130")
        # amputate one emitted cell block -> coverage + counts must fail
        victim = next(iter(names.values()))
        import re
        broken = re.sub(
            rf'(?m)^\s*cv = dbOpenCellViewByType\("canvas_lib" "{victim}".*?'
            rf'dbClose\(cv\)\n',
            "", il, flags=re.DOTALL)
        assert broken != il
        checks = virtuoso_check._check_skill(broken, cells, names)
        st = _statuses(checks)
        assert st["skill_cells_covered"]["status"] == "fail"

    def test_skill_check_detects_paren_imbalance(self, tmp_path):
        gds = _compile(_design(), tmp_path)
        ly, cells, _ = virtuoso_check._layout_stats(gds)
        names = virtuoso_check._oa_cell_names(ly)
        broken = export_skill(gds, "canvas_lib", "sky130") + "("
        checks = virtuoso_check._check_skill(broken, cells, names)
        st = _statuses(checks)
        assert st["skill_parens"]["status"] == "fail"

    def test_spectre_structural_checks(self):
        good = (
            "simulator lang=spectre\n"
            "subckt leaf ( a b )\nends leaf\n"
            "subckt top ( x y )\n"
            "X1 ( x y ) leaf\n"
            "ends top\n")
        checks = {c["name"]: c for c in virtuoso_check._check_spectre(good)}
        assert all(c["status"] == "pass" for c in checks.values())

        missing = good + "X2 ( x y ) ghost\n"
        checks = {c["name"]: c for c in virtuoso_check._check_spectre(missing)}
        assert checks["scs_subckt_refs"]["status"] == "fail"
        assert "ghost" in checks["scs_subckt_refs"]["detail"]

        bad_ports = good.replace("X1 ( x y ) leaf", "X1 ( x ) leaf")
        checks = {c["name"]: c
                  for c in virtuoso_check._check_spectre(bad_ports)}
        assert checks["scs_port_counts"]["status"] == "fail"

        unbalanced = good.replace("ends top\n", "")
        checks = {c["name"]: c
                  for c in virtuoso_check._check_spectre(unbalanced)}
        assert checks["scs_subckt_ends"]["status"] == "fail"


class TestRemoteAcceptance:
    def test_no_host_is_unavailable(self, tmp_path, monkeypatch):
        monkeypatch.delenv(virtuoso_check.HOST_ENV, raising=False)
        monkeypatch.delenv(virtuoso_check.DIR_ENV, raising=False)
        res = virtuoso_check.remote_acceptance(
            None, None, tmp_path / "x.gds", "sky130")
        assert res["status"] == "unavailable"
        assert virtuoso_check.HOST_ENV in res["error"]
        assert virtuoso_check.DIR_ENV in res["setup"]

    def test_no_dir_is_unavailable(self, tmp_path, monkeypatch):
        monkeypatch.delenv(virtuoso_check.DIR_ENV, raising=False)
        res = virtuoso_check.remote_acceptance(
            "eda.example.com", None, tmp_path / "x.gds", "sky130")
        assert res["status"] == "unavailable"
        assert virtuoso_check.DIR_ENV in res["error"]

    def _fake_remote(self, monkeypatch, tmp_path, design,
                     mutate=None, drop_report=False):
        """Stub the SSH transport: remote workdir = local dir; virtuoso and
        spectre replays fabricate logs, and verify_report.txt is built from
        the *expected* census (a faithful remote — mutate/drop inject
        failures). Returns the remote dir."""
        remote_dir = tmp_path / "remote"
        remote_dir.mkdir()
        calls = []
        import re
        import shutil

        def _is_remote(arg: str) -> bool:
            # remote specs look like "host:path" — Windows local paths
            # "C:\..." also contain ':' and must not count as remote.
            return ":" in arg and not re.match(r"^[A-Za-z]:[\\/]", arg)

        def fake_run(cmd, timeout):
            calls.append(cmd)
            text = " ".join(map(str, cmd))
            if cmd[0] == "scp":
                args = cmd[4:]
                dst = args[-1]
                if _is_remote(dst):  # upload: locals -> remote_dir
                    for s in args[:-1]:
                        shutil.copy(str(s), remote_dir / Path(str(s)).name)
                    return subprocess.CompletedProcess(cmd, 0, "", "")
                src_remote = args[0]
                _, _, rpath = src_remote.partition(":")
                src = remote_dir / Path(rpath).name
                if src.is_file():
                    shutil.copy(src, dst)
                    return subprocess.CompletedProcess(cmd, 0, "", "")
                return subprocess.CompletedProcess(cmd, 1, "",
                                                   "no such file")
            assert cmd[0] == "ssh"
            if "mkdir -p" in text:
                remote_dir.mkdir(exist_ok=True)
                return subprocess.CompletedProcess(cmd, 0, "", "")
            if "virtuoso -nograph -replay verify.il" in text:
                (remote_dir / "ciw_verify.log").write_text(
                    "audit done\n", encoding="utf-8")
                if not drop_report:
                    _, cells, _ = virtuoso_check._layout_stats(
                        self._gds)
                    names = virtuoso_check._oa_cell_names(
                        virtuoso_check._layout_stats(self._gds)[0])
                    lines = []
                    for gds_cell, want in cells.items():
                        w = dict(want)
                        if mutate and gds_cell == mutate[0]:
                            w[mutate[1]] += mutate[2]
                        lines.append(
                            f"{names[gds_cell]} shapes="
                            f"{w['rects'] + w['polygons'] + w['labels']} "
                            f"rects={w['rects']} polygons={w['polygons']} "
                            f"labels={w['labels']} others=0 "
                            f"insts={w['insts']}")
                    (remote_dir / "verify_report.txt").write_text(
                        "\n".join(lines) + "\n", encoding="utf-8")
                return subprocess.CompletedProcess(cmd, 0, "", "")
            if "virtuoso -nograph -replay" in text:
                (remote_dir / "ciw.log").write_text(
                    "replay done\n", encoding="utf-8")
                return subprocess.CompletedProcess(cmd, 0, "", "")
            if "spectre" in text:
                (remote_dir / "spectre.log").write_text(
                    "Analysis `dc` succeeded\nspectre completes\n",
                    encoding="utf-8")
                return subprocess.CompletedProcess(cmd, 0, "", "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr(virtuoso_check, "_run", fake_run)
        return remote_dir, calls

    def test_remote_verified_on_faithful_remote(self, tmp_path, monkeypatch):
        design = _design()
        self._gds = _compile(design, tmp_path)
        spice = compile_netlist(design)
        remote_dir, calls = self._fake_remote(monkeypatch, tmp_path, design)
        monkeypatch.setenv(virtuoso_check.MODELS_ENV, "/pdk/tt.scs")
        res = virtuoso_check.remote_acceptance(
            "ssh eda@host", str(remote_dir).replace("\\", "/"),
            self._gds, "sky130", spice_text=spice)
        assert res["status"] == "verified", [
            c for c in res["checks"] if c["status"] != "pass"]
        assert res["spectre"]["status"] == "ok"
        # transport sanity: upload scp ran before the first ssh replay
        scp_idx = next(i for i, c in enumerate(calls) if c[0] == "scp")
        virt_idx = next(i for i, c in enumerate(calls)
                        if c[0] == "ssh" and "virtuoso" in c[-1])
        assert scp_idx < virt_idx
        assert Path(res["staging"]).is_dir()

    def test_remote_census_mismatch_fails(self, tmp_path, monkeypatch):
        design = _design()
        self._gds = _compile(design, tmp_path)
        ly, cells, _ = virtuoso_check._layout_stats(self._gds)
        victim = next(c for c in cells if cells[c]["rects"] > 0)
        remote_dir, _ = self._fake_remote(
            monkeypatch, tmp_path, design, mutate=(victim, "rects", -1))
        res = virtuoso_check.remote_acceptance(
            "eda@host", str(remote_dir), self._gds, "sky130")
        assert res["status"] == "failed"
        bad = [c for c in res["checks"]
               if c["status"] == "fail" and c["name"].startswith("oa:")]
        assert bad and bad[0]["name"].endswith(":rects")

    def test_remote_missing_report_fails(self, tmp_path, monkeypatch):
        design = _design()
        self._gds = _compile(design, tmp_path)
        remote_dir, _ = self._fake_remote(
            monkeypatch, tmp_path, design, drop_report=True)
        res = virtuoso_check.remote_acceptance(
            "eda@host", str(remote_dir), self._gds, "sky130")
        assert res["status"] == "failed"
        assert any(c["name"] == "verify_report"
                   and c["status"] == "fail" for c in res["checks"])

    def test_ssh_transport_failure_is_unavailable(self, tmp_path,
                                                  monkeypatch):
        def dead(cmd, timeout):
            if cmd[0] == "ssh":
                return subprocess.CompletedProcess(cmd, 255, "",
                                                   "Connection refused")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr(virtuoso_check, "_run", dead)
        gds = _compile(_design(), tmp_path)
        res = virtuoso_check.remote_acceptance(
            "eda@host", "/tmp/x", gds, "sky130")
        assert res["status"] == "unavailable"
        assert "Connection refused" in res["error"]

    def test_bad_gds_fails_not_crashes(self, tmp_path):
        res = virtuoso_check.remote_acceptance(
            "eda@host", "/tmp/x", tmp_path / "nope.gds", "sky130")
        assert res["status"] == "failed"
        assert "artifact" in res["error"]

    def test_spectre_unavailable_without_models(self, tmp_path, monkeypatch):
        """The spectre leg reports unavailable (not a fake green or a
        doomed run) when no model deck is configured."""
        design = _design()
        self._gds = _compile(design, tmp_path)
        remote_dir, _ = self._fake_remote(monkeypatch, tmp_path, design)
        monkeypatch.delenv(virtuoso_check.MODELS_ENV, raising=False)
        res = virtuoso_check.remote_acceptance(
            "eda@host", str(remote_dir), self._gds, "sky130")
        assert res["spectre"]["status"] == "unavailable"
        assert virtuoso_check.MODELS_ENV in res["spectre"]["reason"]
        # layout leg verified but the overall verdict cannot be green
        assert res["status"] == "unavailable"


class TestOaLayersDescriptor:
    def test_descriptor_oa_layers_win(self, tmp_path):
        from layout_canvas.pdk.descriptor import PDK

        pdk = PDK.from_dict({
            "name": "demo65",
            "layers": {"met1": [68, 20]},
            "oa_layers": {"68/20": "m1", "999/5": "rdl"},
        })
        assert pdk.oa_layers == {(68, 20): "m1", (999, 5): "rdl"}
        # round-trip
        back = PDK.from_dict(pdk.to_dict())
        assert back.oa_layers == pdk.oa_layers

    def test_oa_layers_validation(self):
        from layout_canvas.pdk.descriptor import PDK

        with pytest.raises(ValueError, match="oa_layers"):
            PDK.from_dict({"name": "x", "oa_layers": {"met1": "m1"}})
        with pytest.raises(ValueError, match="oa_layers"):
            PDK.from_dict({"name": "x", "oa_layers": {"1/0": 7}})

    def test_export_skill_uses_descriptor_map(self, tmp_path, monkeypatch):
        """A registered descriptor's oa_layers beat the built-in table."""
        from layout_canvas.pdk import descriptor
        from layout_canvas.pdk.descriptor import PDK

        gds = _compile(_design(), tmp_path)
        # built-in sky130 name untouched
        assert 'list("met1" "drawing")' in export_skill(
            gds, "lib", "sky130")
        # descriptor PDK with oa_layers override (registry slot restored by
        # monkeypatch — no leakage into other tests)
        monkeypatch.setitem(descriptor._REGISTRY, "demo65oa", PDK.from_dict({
            "name": "demo65oa",
            "layers": {"met1": [68, 20]},
            "oa_layers": {"68/20": "m1"},
        }))
        il = export_skill(gds, "lib", "demo65oa")
        assert 'list("m1" "drawing")' in il
        assert 'list("met1" "drawing")' not in il
        # and the override is per-key: a pair absent from oa_layers keeps
        # the L*_D* fallback (descriptor has no built-in table)
        assert 'list("L' in il


class TestCliDryRun:
    def test_virtuoso_accept_dry_run(self, capsys, monkeypatch):
        import sys

        monkeypatch.setattr(sys, "argv", [
            "layout-canvas", "virtuoso-accept", str(DIFFAMP), "--dry-run"])
        monkeypatch.delenv(virtuoso_check.HOST_ENV, raising=False)
        from layout_canvas.cli import main
        assert main() == 0
        out = capsys.readouterr().out
        assert "[static] status=static_ok" in out
        assert "--dry-run" in out

class TestTechLibAttach:
    """Real-Virtuoso LPP resolution: dbCreate* calls name layers like
    ("diff" "drawing") which only exist in a techfile — the emitted .il must
    attach the PDK's tech library, or every create call errors."""

    def test_default_techlib_attached(self, tmp_path):
        gds = _compile(_design(), tmp_path)
        il = export_skill(gds, "canvas_lib", "sky130")
        assert 'techSetTechLibName(libId "sky130_fd_pr")' in il
        assert 'ddGetObj("sky130_fd_pr")' in il  # guarded: visible only

    def test_arg_and_env_override(self, tmp_path, monkeypatch):
        gds = _compile(_design(), tmp_path)
        il = export_skill(gds, "lib", "sky130", tech_lib="my_tech")
        assert 'techSetTechLibName(libId "my_tech")' in il
        monkeypatch.setenv(virtuoso_check.TECHLIB_ENV, "env_tech")
        il = export_skill(gds, "lib", "sky130")
        assert 'techSetTechLibName(libId "env_tech")' in il

    def test_unknown_tech_warns_instead_of_attaching(self, tmp_path,
                                                     monkeypatch):
        monkeypatch.delenv(virtuoso_check.TECHLIB_ENV, raising=False)
        gds = _compile(_design(), tmp_path)
        il = export_skill(gds, "lib", "no_such_pdk")
        assert "techSetTechLibName" not in il
        assert "no tech library configured" in il

    def test_remote_reports_missing_techlib(self, tmp_path, monkeypatch):
        """An exotic tech with no known/default tech lib surfaces as an
        unavailable check, not a replay mysteriously full of *Error*s."""
        monkeypatch.delenv(virtuoso_check.TECHLIB_ENV, raising=False)
        gds = _compile(_design(), tmp_path)
        res = virtuoso_check.remote_acceptance(
            "eda@host", "/tmp/x", gds, "no_such_pdk")
        st = {c["name"]: c for c in res["checks"]}
        # ssh genuinely fails in CI — whatever leg ran, tech_lib is flagged
        assert st["tech_lib"]["status"] == "unavailable"


class TestInstNamingAndTransforms:
    def test_inst_names_unique_per_cellview(self, tmp_path):
        """Two instances of the same child cell must not both emit
        "<child>_I0_0" — Virtuoso errors on duplicate instance names."""
        import re

        import klayout.db as kdb

        ly = kdb.Layout()
        ly.dbu = 0.001
        top = ly.create_cell("top")
        leaf = ly.create_cell("leaf")
        li = ly.layer(65, 20)
        leaf.shapes(li).insert(kdb.DBox(0, 0, 1, 1))
        top.insert(kdb.DCellInstArray(
            leaf.cell_index(), kdb.DTrans(kdb.DVector(0, 0))))
        top.insert(kdb.DCellInstArray(
            leaf.cell_index(), kdb.DTrans(kdb.DVector(5, 0))))
        gds = tmp_path / "p.gds"
        ly.write(str(gds))
        il = export_skill(gds, "lib", "sky130")
        names = re.findall(r'dbCreateInst\(cv master "([^"]+)"', il)
        assert len(names) == 2 and len(set(names)) == 2

    def test_mirror_and_mag_survive(self, tmp_path):
        """GDS mirrored/magnified placements must become MY/MX* orients and
        a real magnification — silently dropping them redraws wrongly."""
        import re

        import klayout.db as kdb

        ly = kdb.Layout()
        ly.dbu = 0.001
        top = ly.create_cell("top")
        leaf = ly.create_cell("leaf")
        li = ly.layer(65, 20)
        leaf.shapes(li).insert(kdb.DBox(0, 0, 1, 1))
        top.insert(kdb.DCellInstArray(
            leaf.cell_index(),
            kdb.DCplxTrans(1.5, 0, True, kdb.DVector(5, 0))))
        gds = tmp_path / "p.gds"
        ly.write(str(gds))
        il = export_skill(gds, "lib", "sky130")
        m = re.search(r'dbCreateInst\(cv master "[^"]+" 5:0 "(\w+)" ([\d.]+)\)',
                      il)
        assert m and m.group(1) == "MX" and float(m.group(2)) == 1.5
