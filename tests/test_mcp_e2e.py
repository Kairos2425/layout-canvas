"""End-to-end MCP walkthrough over real stdio JSON-RPC.

Spawns the actual server subprocess and drives the full agent loop:
open -> snapshot -> connectivity -> transact -> ppa -> compile -> netlist
-> abstract -> save/load -> close. The real-simulation step is included
only when an ngspice binary is available (LAYOUT_CANVAS_NGSPICE/PATH).
"""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
SKY_LIB = ROOT / "examples" / "models" / "sky130" / "sky130_tt.lib"


class McpClient:
    def __init__(self, proc):
        self.proc = proc
        self._id = 0

    def call(self, method, params=None):
        self._id += 1
        req = {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params or {}}
        self.proc.stdin.write(json.dumps(req) + "\n")
        self.proc.stdin.flush()
        return json.loads(self.proc.stdout.readline())

    def tool(self, name, args=None):
        resp = self.call("tools/call", {"name": name, "arguments": args or {}})
        result = resp["result"]
        text = result["content"][0]["text"]
        try:
            data = json.loads(text)
        except Exception:
            data = text
        return result["isError"], data


@pytest.fixture()
def mcp():
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src")
    proc = subprocess.Popen(
        [sys.executable, "-m", "layout_canvas.mcp.server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        cwd=str(ROOT),
        env=env,
    )
    yield McpClient(proc)
    proc.kill()


def _design():
    return {
        "name": "e2e_dp",
        "pdk": "sky130",
        "instances": [
            {
                "id": "dp",
                "block": "sky130.diff_pair",
                "params": {"fingers": 4, "width": 2.0, "length": 0.5, "tail_width": 4.0},
                "placement": {"x": 0, "y": 0},
            }
        ],
        "nets": [{"name": "n_inp", "pins": ["dp.inp"]}],
    }


def test_full_agent_loop(mcp, tmp_path):
    # handshake + tool surface
    assert mcp.call("initialize")["result"]["serverInfo"]["name"] == "layout-canvas-mcp"
    tools = {t["name"] for t in mcp.call("tools/list")["result"]["tools"]}
    for t in ("open_design", "snapshot", "transact", "undo", "inspect_connectivity",
              "compile_session", "inspect_ppa", "run_simulation", "save_project",
              "load_project", "export_abstract", "probe_environment", "optimize",
              "register_cell", "close_design"):
        assert t in tools, t

    # open + snapshot
    err, opened = mcp.tool("open_design", {"ir_json": _design()})
    assert not err and opened["revision"] == 0
    sid = opened["session_id"]

    err, conn = mcp.tool("inspect_connectivity", {"session_id": sid})
    assert conn["status"] == "ok"
    assert "tail" in conn["data"]["instances"]["dp"]["unconnected"]

    # revision-locked transaction
    err, tx = mcp.tool("transact", {
        "session_id": sid,
        "edits": [{"op": "set_params", "instance": "dp", "params": {"fingers": 8}}],
        "expected_revision": 0,
    })
    assert tx["status"] == "ok" and tx["revision"] == 1

    # stale-revision conflict must fail closed
    err, stale = mcp.tool("transact", {
        "session_id": sid,
        "edits": [{"op": "set_params", "instance": "dp", "params": {"fingers": 2}}],
        "expected_revision": 0,
    })
    assert stale["status"] in ("conflict", "error", "rejected")

    # compile to a real GDS
    gds = tmp_path / "e2e_dp.gds"
    err, comp = mcp.tool("compile_session", {"session_id": sid, "output_path": str(gds)})
    assert not err and gds.is_file() and gds.stat().st_size > 0

    # netlist carries the committed params and canonical model cells
    err, snap = mcp.tool("snapshot", {"session_id": sid})
    design_now = snap["data"]["design"]
    err, nl = mcp.tool("generate_netlist", {"ir_json": design_now})
    assert "sky130_fd_pr__nfet_01v8" in nl["spice"]
    assert "nf=8" in nl["spice"]
    variant = re.search(r"\.subckt (sky130_diff_pair__\w+)", nl["spice"]).group(1)

    # abstract + PPA
    err, absr = mcp.tool("export_abstract", {"session_id": sid})
    assert absr["bbox"][2] > absr["bbox"][0]
    err, ppa = mcp.tool("inspect_ppa", {"ir_json": design_now})
    assert ppa["area_um2"] > 0

    # project round-trip
    proj = tmp_path / "e2e_dp.lcproj.json"
    err, _ = mcp.tool("save_project", {"session_id": sid, "path": str(proj)})
    err, loaded = mcp.tool("load_project", {"path": str(proj)})
    assert loaded.get("session_id") and loaded["session_id"] != sid

    # cell registration + close
    err, cell = mcp.tool("register_cell", {"alias": "cells.e2e_dp", "session_id": sid})
    assert cell["alias"] == "cells.e2e_dp"
    err, closed = mcp.tool("close_design", {"session_id": sid})
    assert closed["closed"] == sid

    ngspice = os.environ.get("LAYOUT_CANVAS_NGSPICE") or shutil.which("ngspice")
    if ngspice and SKY_LIB.is_file():
        stimulus = f"""
VDD vdd 0 1.8
VSS vss 0 0
VINP inp 0 0.95
VINN inn 0 0.9
IT tail vss 10u
RDP vdd outp 10k
RDN vdd outn 10k
X1 inp inn outp outn tail {variant}
.op
"""
        err, sim = mcp.tool("run_simulation", {
            "ir_json": design_now,
            "stimulus": stimulus,
            "includes": [str(SKY_LIB)],
            "simulator": "ngspice",
        })
        assert sim["status"] == "passed", sim.get("errors")
