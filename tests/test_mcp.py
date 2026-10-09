"""Test Layout Canvas MCP Server and Bridge functionality."""

from __future__ import annotations

import json
import pytest
from pathlib import Path

from layout_canvas.mcp.server import LayoutCanvasMCPServer
from layout_canvas.mcp.bridge import KLayoutBridgeServer, BridgeClient


def test_mcp_initialize():
    server = LayoutCanvasMCPServer()
    req = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {}
    }
    resp = server.handle_request(req)
    assert resp["jsonrpc"] == "2.0"
    assert resp["id"] == 1
    assert "protocolVersion" in resp["result"]
    assert resp["result"]["serverInfo"]["name"] == "layout-canvas-mcp"


def test_mcp_tools_list():
    server = LayoutCanvasMCPServer()
    req = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/list",
        "params": {}
    }
    resp = server.handle_request(req)
    assert "tools" in resp["result"]
    tool_names = [t["name"] for t in resp["result"]["tools"]]
    assert len(tool_names) == 35
    for name in ("run_testbench", "gallery_list", "gallery_publish",
                 "gallery_fork", "gallery_stats", "import_gds",
                 "import_netlist"):
        assert name in tool_names
    assert "list_blocks" in tool_names
    assert "generate_block" in tool_names
    assert "compile_ir" in tool_names
    assert "run_drc" in tool_names
    assert "get_active_layout_info" in tool_names
    assert "insert_block_into_layout" in tool_names


def test_mcp_execute_list_blocks():
    # Make sure sky130 blocks are loaded
    import layout_canvas.blocks.sky130  # noqa: F401
    server = LayoutCanvasMCPServer()

    req = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {
            "name": "list_blocks",
            "arguments": {"pdk": "sky130"}
        }
    }
    resp = server.handle_request(req)
    assert resp["result"]["isError"] is False
    content_text = resp["result"]["content"][0]["text"]
    blocks_data = json.loads(content_text)
    assert isinstance(blocks_data, list)
    names = [b["name"] for b in blocks_data]
    assert "sky130.diff_pair" in names
    assert "sky130.current_mirror" in names


def test_mcp_execute_generate_block(tmp_path: Path):
    import layout_canvas.blocks.sky130  # noqa: F401
    server = LayoutCanvasMCPServer()
    out_gds = tmp_path / "test_diff_pair.gds"

    req = {
        "jsonrpc": "2.0",
        "id": 4,
        "method": "tools/call",
        "params": {
            "name": "generate_block",
            "arguments": {
                "name": "sky130.diff_pair",
                "params": {"width": 3.0, "length": 0.5, "fingers": 2},
                "output_path": str(out_gds),
                "format": "gds"
            }
        }
    }
    resp = server.handle_request(req)
    assert resp["result"]["isError"] is False
    res = json.loads(resp["result"]["content"][0]["text"])
    assert res["block"] == "sky130.diff_pair"
    assert out_gds.exists()
    assert out_gds.stat().st_size > 0


def test_mcp_run_testbench_and_gallery_smoke(tmp_path, monkeypatch):
    """run_testbench + gallery_* tools over a session; sim is fail-closed."""
    import layout_canvas.blocks.sky130  # noqa: F401
    monkeypatch.setenv("LAYOUT_CANVAS_GALLERY", str(tmp_path / "gal"))
    server = LayoutCanvasMCPServer()

    ir = {
        "name": "tb_mcp",
        "pdk": "sky130",
        "instances": [{"id": "dp", "block": "sky130.diff_pair", "params": {}}],
        "testbenches": [{"name": "tb1", "source": "schematic", "specs": [
            {"name": "s", "signal": "outp", "min": 0, "max": 5}]}],
    }
    opened = server.execute_tool("open_design", {"ir_json": ir})
    sid = opened["session_id"]

    res = server.execute_tool("run_testbench",
                              {"session_id": sid, "name": "tb1"})
    assert res["runs"][0]["testbench"] == "tb1"
    # run recorded on the session with a status summary
    snap = server.execute_tool("snapshot", {"session_id": sid})
    assert snap["data"]["runs"][0]["kind"] == "testbench"
    assert snap["data"]["runs"][0]["testbenches"] == ["tb1"]
    with pytest.raises(Exception, match="unknown testbench"):
        server.execute_tool("run_testbench",
                            {"session_id": sid, "name": "nope"})

    pub = server.execute_tool("gallery_publish", {"session_id": sid})
    assert pub["ai_generated"] is True
    assert pub["verification"]["status"] in ("ok", "unavailable")

    listed = server.execute_tool("gallery_list", {"verified_only": True})
    assert any(e["id"] == pub["id"] for e in listed["entries"])
    # agent re-publishing identical content hits the dedup
    dup = server.execute_tool("gallery_publish", {"session_id": sid})
    assert dup["status"] == "error" and "duplicate of" in dup["error"]

    forked = server.execute_tool("gallery_fork", {"id": pub["id"]})
    assert forked["session_id"] != sid
    assert forked["design"]["name"] == "tb_mcp"
    assert server.execute_tool("snapshot",
                               {"session_id": forked["session_id"]})


def test_mcp_bridge_roundtrip():
    """Test KLayoutBridgeServer and BridgeClient socket communication without GUI."""
    def dummy_handler(method: str, params: dict) -> dict:
        if method == "get_active_layout_info":
            return {"active": True, "cell_name": "TOP", "layers": ["68/20", "65/20"]}
        return {"error": {"code": -32601, "message": "not found"}}

    # Bind to random ephemeral port or high port
    port = 9188
    server = KLayoutBridgeServer(port=port, handler=dummy_handler)
    assert server.start() is True

    try:
        client = BridgeClient(port=port, timeout=2.0)
        res = client.send_request("get_active_layout_info")
        assert "result" in res
        assert res["result"]["active"] is True
        assert res["result"]["cell_name"] == "TOP"
    finally:
        server.stop()
