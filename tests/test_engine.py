"""Tests for the transactional edit engine and connectivity projection."""

import json

import pytest

from layout_canvas.derived.connectivity import inspect_connectivity
from layout_canvas.engine import DesignSession
from layout_canvas.ir.model import Design
from layout_canvas.mcp.server import LayoutCanvasMCPServer


def _design() -> Design:
    return Design.model_validate(
        {
            "name": "sess_test",
            "pdk": "sky130",
            "instances": [
                {
                    "id": "m1",
                    "block": "sky130.diff_pair",
                    "params": {},
                    "placement": {"x": 0.0, "y": 0.0},
                },
                {
                    "id": "m2",
                    "block": "sky130.current_mirror",
                    "params": {},
                    "placement": {
                        "relative_to": "m1",
                        "relation": "right_of",
                        "margin": 1.0,
                    },
                },
            ],
            "nets": [{"name": "tail", "pins": ["m1.tail", "m2.gate"]}],
            "ports": [{"name": "TAIL", "pin": "m1.tail", "direction": "input"}],
        }
    )


class TestSession:
    def test_snapshot_reports_revision_and_design(self):
        s = DesignSession(_design())
        snap = s.snapshot()
        assert snap.ok
        assert snap.revision == 0
        assert snap.data["design"]["name"] == "sess_test"
        assert "set_placement" in snap.data["supported_ops"]

    def test_transact_commits_and_bumps_revision(self):
        s = DesignSession(_design())
        env = s.transact([{"op": "set_placement", "instance": "m1", "x": 5.0}])
        assert env.ok
        assert env.revision == 1
        assert s.design.instance("m1").placement.x == 5.0

    def test_transact_is_atomic_on_failure(self):
        s = DesignSession(_design())
        env = s.transact(
            [
                {"op": "set_placement", "instance": "m1", "x": 5.0},
                {"op": "set_placement", "instance": "nope", "x": 1.0},
            ]
        )
        assert env.status == "rejected"
        assert env.diagnostics[0].code == "unknown-instance"
        assert s.revision == 0
        assert s.design.instance("m1").placement.x == 0.0

    def test_revision_conflict_rejects(self):
        s = DesignSession(_design())
        env = s.transact(
            [{"op": "set_meta", "key": "k", "value": 1}], expected_revision=7
        )
        assert env.status == "rejected"
        assert env.diagnostics[0].code == "revision-conflict"
        assert s.revision == 0

    def test_dry_run_does_not_commit(self):
        s = DesignSession(_design())
        env = s.transact(
            [{"op": "set_placement", "instance": "m1", "x": 9.0}], dry_run=True
        )
        assert env.ok and env.data["dry_run"] is True
        assert env.data["design"]["instances"][0]["placement"]["x"] == 9.0
        assert s.revision == 0
        assert s.design.instance("m1").placement.x == 0.0

    def test_undo_restores_previous_design(self):
        s = DesignSession(_design())
        s.transact([{"op": "set_placement", "instance": "m1", "x": 5.0}])
        env = s.undo()
        assert env.ok and env.revision == 2
        assert s.design.instance("m1").placement.x == 0.0

    def test_remove_referenced_instance_rejected(self):
        s = DesignSession(_design())
        env = s.transact([{"op": "remove_instance", "instance": "m1"}])
        assert env.status == "rejected"
        assert env.diagnostics[0].code == "instance-referenced"

    def test_unknown_op_rejected(self):
        s = DesignSession(_design())
        env = s.transact([{"op": "explode"}])
        assert env.status == "rejected"
        assert env.diagnostics[0].code == "unknown-op"

    def test_invalid_resulting_design_rejected(self):
        s = DesignSession(_design())
        env = s.transact(
            [{"op": "set_net_pins", "net": "tail", "pins": ["ghost.out"]}]
        )
        assert env.status == "rejected"
        assert env.diagnostics[0].code == "invalid-design"


class TestCompactSnapshot:
    def test_compact_collapses_runs_by_kind(self):
        s = DesignSession(_design())
        for _ in range(8):
            s.record_run("verify", {"passed": True, "lvs_status": "passed"})
        s.record_run("verify", {"passed": False, "lvs_status": "failed"})
        s.record_run("testbench", {"spec_status": ["pass"],
                                 "sim_status": ["passed"]})
        snap = s.snapshot(compact=True)
        assert snap.ok
        runs = snap.data["runs"]
        assert runs["verify"]["count"] == 9
        assert runs["verify"]["last"]["op"] == "verify"
        assert runs["verify"]["last"]["result_status"] == "failed"
        assert runs["testbench"]["count"] == 1
        assert snap.data["compact"] is True
        # the design itself is never compacted away — it is ground truth
        assert snap.data["design"]["name"] == "sess_test"

    def test_compact_is_smaller_on_the_wire(self):
        s = DesignSession(_design())
        for _ in range(10):
            s.record_run("verify", {"passed": True, "lvs_status": "passed",
                                    "drc_violations": 0,
                                    "quality_status": "pass"})
        s.record_run("testbench", {"spec_status": ["pass"],
                                   "sim_status": ["passed"]})
        s.record_run("reliability", {"coverage": 0.5,
                                   "statuses": {"htol": "pass"}})
        full = json.dumps(s.snapshot().to_dict())
        comp = json.dumps(s.snapshot(compact=True).to_dict())
        assert len(comp) < len(full)

    def test_full_snapshot_is_the_default(self):
        s = DesignSession(_design())
        s.record_run("verify", {"passed": True})
        snap = s.snapshot()
        assert isinstance(snap.data["runs"], list)
        assert snap.data["runs"][0]["passed"] is True
        assert "compact" not in snap.data


class TestDecisionPointConfirm:
    """High-impact edits never apply silently: the first call returns
    requires_confirmation with an impact report; only confirm=True commits."""

    def test_remove_net_requires_confirmation_then_applies(self):
        s = DesignSession(_design())
        edits = [{"op": "remove_net", "net": "tail"}]
        env = s.transact(edits, expected_revision=0)
        assert env.status == "requires_confirmation"
        assert env.revision == 0  # revision preserved
        assert s.revision == 0
        assert any(n.name == "tail" for n in s.design.nets)  # not applied
        impact = env.data["impact"]
        assert impact["high"] is True
        assert impact["affected_nets"] == ["tail"]
        assert "tail" in impact["summary"]
        # same edits + confirm=True commits at the preserved revision
        env2 = s.transact(edits, expected_revision=0, confirm=True)
        assert env2.ok and env2.revision == 1
        assert all(n.name != "tail" for n in s.design.nets)

    def test_remove_instance_requires_confirmation(self):
        s = DesignSession(_design())
        # m3 is unreferenced — add it, then removing it is high-impact
        s.transact([{"op": "add_instance", "instance": {
            "id": "m3", "block": "sky130.current_mirror", "params": {}}}])
        env = s.transact([{"op": "remove_instance", "instance": "m3"}],
                         expected_revision=1)
        assert env.status == "requires_confirmation"
        assert env.data["impact"]["affected_instances"] == ["m3"]
        assert s.design.instance("m3").id == "m3"  # not applied
        env2 = s.transact([{"op": "remove_instance", "instance": "m3"}],
                          expected_revision=1, confirm=True)
        assert env2.ok
        with pytest.raises(KeyError):
            s.design.instance("m3")

    def test_set_params_majority_requires_confirmation(self):
        s = DesignSession(_design())  # 2 instances: 2/2 > 50%
        edits = [
            {"op": "set_params", "instance": "m1", "params": {"fingers": 4}},
            {"op": "set_params", "instance": "m2", "params": {"fingers": 4}},
        ]
        env = s.transact(edits)
        assert env.status == "requires_confirmation"
        assert set(env.data["impact"]["affected_instances"]) == {"m1", "m2"}
        env2 = s.transact(edits, confirm=True)
        assert env2.ok

    def test_low_impact_edits_apply_directly(self):
        s = DesignSession(_design())
        # 1 of 2 instances is exactly 50% — not >50% — so it applies.
        env = s.transact(
            [{"op": "set_params", "instance": "m1",
              "params": {"fingers": 4}}])
        assert env.ok and s.design.instance("m1").params["fingers"] == 4
        env = s.transact([{"op": "set_placement", "instance": "m1", "x": 2.0}])
        assert env.ok

    def test_invalid_high_impact_edit_is_rejected_not_confirmed(self):
        # validation precedes the decision point — a broken edit gets
        # diagnostics, not a pointless confirmation prompt.
        s = DesignSession(_design())
        env = s.transact([{"op": "remove_net", "net": "ghost"}])
        assert env.status == "rejected"
        assert env.diagnostics[0].code == "unknown-net"

    def test_dry_run_needs_no_confirm(self):
        s = DesignSession(_design())
        env = s.transact([{"op": "remove_net", "net": "tail"}],
                         dry_run=True)
        assert env.ok and env.data["dry_run"] is True
        assert any(n.name == "tail" for n in s.design.nets)

    def test_mcp_envelope_and_compact_snapshot(self):
        server = LayoutCanvasMCPServer()
        opened = server.execute_tool(
            "open_design", {"ir_json": _design().model_dump()})
        sid = opened["session_id"]
        server.sessions[sid].record_run("verify", {"passed": True})

        res = server.execute_tool("transact", {
            "session_id": sid,
            "edits": [{"op": "remove_net", "net": "tail"}],
            "expected_revision": 0})
        assert res["status"] == "requires_confirmation"
        assert res["data"]["impact"]["high"] is True
        assert res["revision"] == 0
        res2 = server.execute_tool("transact", {
            "session_id": sid,
            "edits": [{"op": "remove_net", "net": "tail"}],
            "expected_revision": 0, "confirm": True})
        assert res2["status"] == "ok" and res2["revision"] == 1

        snap = server.execute_tool(
            "snapshot", {"session_id": sid, "compact": True})
        assert snap["data"]["compact"] is True
        assert snap["data"]["runs"]["verify"]["count"] == 1
        assert snap["data"]["runs"]["verify"]["last"][
            "result_status"] == "passed"


class TestConnectivity:
    def test_pin_net_resolution(self):
        report = inspect_connectivity(_design())
        assert report["instances"]["m1"]["pins"]["tail"]["net"] == "tail"
        assert report["instances"]["m1"]["pins"]["tail"]["is_port"] is True
        # diff_pair declares more pins than just tail — they show as unconnected
        assert "inp" in report["instances"]["m1"]["unconnected"]

    def test_degenerate_and_unknown_block_diagnostics(self):
        design = Design.model_validate(
            {
                "name": "diag_test",
                "pdk": "sky130",
                "instances": [{"id": "x1", "block": "sky130.nonexistent"}],
                "nets": [{"name": "lonely", "pins": ["x1.a"]}],
            }
        )
        report = inspect_connectivity(design)
        codes = {d["code"] for d in report["diagnostics"]}
        assert "degenerate-net" in codes
        assert "unknown-block" in codes
        assert "x1" in report["instances"]


class TestMCPSessionTools:
    def test_open_snapshot_transact_undo_close(self):
        server = LayoutCanvasMCPServer()
        opened = server.execute_tool("open_design", {"ir_json": _design().model_dump()})
        sid = opened["session_id"]
        assert opened["revision"] == 0

        snap = server.execute_tool("snapshot", {"session_id": sid})
        assert snap["status"] == "ok"
        assert snap["data"]["design"]["name"] == "sess_test"

        tx = server.execute_tool(
            "transact",
            {
                "session_id": sid,
                "edits": [{"op": "set_placement", "instance": "m1", "y": 3.0}],
                "expected_revision": 0,
            },
        )
        assert tx["status"] == "ok" and tx["revision"] == 1

        conn = server.execute_tool("inspect_connectivity", {"session_id": sid})
        assert conn["status"] == "ok"
        assert "m1" in conn["data"]["instances"]

        undone = server.execute_tool("undo", {"session_id": sid})
        assert undone["status"] == "ok"

        closed = server.execute_tool("close_design", {"session_id": sid})
        assert closed["closed"] == sid
        with pytest.raises(KeyError):
            server.execute_tool("snapshot", {"session_id": sid})

    def test_tools_listed(self):
        server = LayoutCanvasMCPServer()
        names = {t["name"] for t in server.get_tool_definitions()}
        for tool in (
            "open_design",
            "snapshot",
            "transact",
            "undo",
            "inspect_connectivity",
            "close_design",
            "compile_session",
            "export_abstract",
            "run_simulation",
            "save_project",
            "load_project",
        ):
            assert tool in names

    def test_save_load_project_roundtrip(self, tmp_path):
        server = LayoutCanvasMCPServer()
        opened = server.execute_tool("open_design", {"ir_json": _design().model_dump()})
        sid = opened["session_id"]
        target = tmp_path / "cell.lcproj.json"
        saved = server.execute_tool("save_project", {"session_id": sid, "path": str(target)})
        assert saved["path"] == str(target.resolve())

        loaded = server.execute_tool("load_project", {"path": str(target)})
        assert loaded["status"] == "ok"
        snap = server.execute_tool("snapshot", {"session_id": loaded["session_id"]})
        assert snap["data"]["design"]["name"] == "sess_test"

    def test_open_design_accepts_lcproj_path(self, tmp_path):
        from layout_canvas.protocol import save_project

        target = save_project(_design(), tmp_path / "c.lcproj.json")
        server = LayoutCanvasMCPServer()
        opened = server.execute_tool("open_design", {"path": str(target)})
        assert opened["design_name"] == "sess_test"

    def test_compile_session_writes_gds(self, tmp_path):
        server = LayoutCanvasMCPServer()
        opened = server.execute_tool("open_design", {"ir_json": _design().model_dump()})
        out = tmp_path / "cell.gds"
        res = server.execute_tool(
            "compile_session", {"session_id": opened["session_id"], "output_path": str(out)}
        )
        assert res["revision"] == 0
        assert out.is_file() and out.stat().st_size > 0

    def test_export_abstract(self):
        server = LayoutCanvasMCPServer()
        opened = server.execute_tool("open_design", {"ir_json": _design().model_dump()})
        res = server.execute_tool("export_abstract", {"session_id": opened["session_id"]})
        assert res["name"] == "sess_test"
        assert len(res["bbox"]) == 4
        assert res["instance_count"] == 2

    def test_run_simulation_fail_closed(self):
        server = LayoutCanvasMCPServer()
        opened = server.execute_tool("open_design", {"ir_json": _design().model_dump()})
        res = server.execute_tool(
            "run_simulation",
            {"session_id": opened["session_id"], "stimulus": ".op"},
        )
        # Environment decides: unavailable (no ngspice) or a real sim outcome —
        # but never a fabricated clean result.
        assert res["status"] in ("unavailable", "passed", "failed", "error")
