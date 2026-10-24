"""Minimal stdio MCP walkthrough for layout-canvas.

Reference client for agents: spawns ``layout-canvas mcp`` and drives the
8-step design loop end to end — open_design, inspect_connectivity,
transact, compile_session, verify_design, run_testbench. It exists so a
solver can see the exact JSON-RPC envelope shape and the order of calls
without reading server internals. Copy and adapt; do not treat it as a
test harness (the real tests live in tests/).

Run: ``python mcp_walkthrough.py`` from anywhere with layout-canvas
installed (or PYTHONPATH=src).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys

IR = {
    "ir_version": "0.1",
    "name": "walkthrough",
    "pdk": "sky130",
    # One block, every pin a port — the smallest IR that verifies clean.
    "instances": [
        {"id": "dp", "block": "sky130.diff_pair",
         "params": {"width": 2.0, "fingers": 2},
         "placement": {"x": 0.0, "y": 0.0}},
    ],
    "nets": [],
    "ports": [
        {"name": p, "pin": f"dp.{p}", "direction": d}
        for p, d in (("inp", "input"), ("inn", "input"),
                     ("outp", "output"), ("outn", "output"),
                     ("tail", "input"), ("vss", "inout"))
    ],
}


class Mcp:
    """One JSON-RPC message per line over stdio, as the server expects."""

    def __init__(self) -> None:
        exe = shutil.which("layout-canvas") or sys.executable
        argv = [exe, "mcp"] if exe != sys.executable else [
            sys.executable, "-m", "layout_canvas.cli", "mcp"]
        self.proc = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            text=True, encoding="utf-8")
        self._id = 0

    def call(self, method: str, params: dict) -> dict:
        self._id += 1
        self.proc.stdin.write(
            json.dumps({"jsonrpc": "2.0", "id": self._id,
                        "method": method, "params": params}) + "\n")
        self.proc.stdin.flush()
        while True:
            msg = json.loads(self.proc.stdout.readline())
            if msg.get("id") == self._id:
                return msg.get("result", msg)

    def tool(self, name: str, arguments: dict) -> dict:
        res = self.call("tools/call", {"name": name, "arguments": arguments})
        text = "".join(c.get("text", "") for c in res.get("content", []))
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"raw": text}


def main() -> None:
    m = Mcp()
    m.call("initialize", {"protocolVersion": "2024-11-05",
                          "capabilities": {},
                          "clientInfo": {"name": "walkthrough",
                                         "version": "0"}})
    m.call("notifications/initialized", {})

    # step 1+2: palette (optional) then open the IR document
    blocks = m.tool("list_blocks", {})
    names = [b.get("name") for b in blocks] if isinstance(blocks, list) \
        else [b.get("name") for b in blocks.get("blocks", [])]
    print("blocks:", names[:6], "...")
    opened = m.tool("open_design", {"ir_json": IR})
    sid, rev = opened["session_id"], opened["revision"]
    print("session:", sid, "rev:", rev)

    # step 3: read the electrical facts before editing
    print("conn:", m.tool("inspect_connectivity", {"session_id": sid}))

    # step 4: every edit goes through transact with expected_revision
    env = m.tool("transact", {
        "session_id": sid, "expected_revision": rev,
        "edits": [{"op": "set_params", "instance": "dp",
                   "params": {"fingers": 4}}]})
    rev = env.get("revision", rev)
    print("transact ok:", env.get("status"), "rev:", rev)

    # step 5+6: compile, then the one-call physical gate. Result shapes
    # vary per tool — read the fields you need, don't assume an Envelope.
    comp = m.tool("compile_session", {"session_id": sid,
                                      "output_path": "walkthrough.gds"})
    print("compiled ->", comp.get("output_path"))
    v = m.tool("verify_design", {"session_id": sid})
    d = v.get("data") or v
    print("verify passed:", d.get("passed"),
          "drc:", (d.get("drc") or {}).get("total_violations"),
          "lvs.match:", (d.get("lvs") or {}).get("match"))

    # step 7: electrical evidence — needs LAYOUT_CANVAS_NGSPICE and a
    # declared testbench; this IR has none, so `runs` comes back empty —
    # honest "nothing was simulated", not a pass.
    tb = m.tool("run_testbench", {"session_id": sid})
    print("testbench runs:", len(tb.get("runs", [])),
          "(declared testbenches: none)")

    m.proc.stdin.close()
    m.proc.wait(timeout=10)


if __name__ == "__main__":
    main()
