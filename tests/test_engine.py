"""Tests for the transactional edit engine and connectivity projection."""

import pytest

from layout_canvas.engine import DesignSession
from layout_canvas.derived.connectivity import inspect_connectivity
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
        ):
            assert tool in names
