"""Model Context Protocol (MCP) JSON-RPC 2.0 Server for Layout Canvas.

Exposes IC layout automation, parametric block synthesis, DRC checking,
and live KLayout integration tools to AI Agents (Claude Code, Codex, DeepSeek, Kimi, etc.).
"""

from __future__ import annotations

import json
import logging
import sys
import tempfile
from pathlib import Path
from typing import Any

from layout_canvas.blocks import base
from layout_canvas.compiler.compile import compile_design
from layout_canvas.ir.model import Design
from layout_canvas.mcp.bridge import BridgeClient
from layout_canvas.tools.drc import run_klayout_drc

logger = logging.getLogger("layout_canvas.mcp.server")

SERVER_NAME = "layout-canvas-mcp"
SERVER_VERSION = "0.1.0"
PROTOCOL_VERSION = "2024-11-05"


class LayoutCanvasMCPServer:
    """Standard MCP server supporting Block IR compilation, block generators, and KLayout GUI interaction."""

    def __init__(self, bridge_client: BridgeClient | None = None):
        self.bridge_client = bridge_client or BridgeClient()

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        """Return MCP tool schemas."""
        return [
            {
                "name": "list_blocks",
                "description": "List all available parametric analog/mixed-signal blocks with their specifications, parameters, ports, and PDKs.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "pdk": {
                            "type": "string",
                            "description": "Filter blocks by PDK name (e.g. 'sky130'). If omitted, returns all blocks.",
                        }
                    },
                },
            },
            {
                "name": "generate_block",
                "description": "Generate a standalone layout for a parametric block (e.g., diff_pair, current_mirror, ota_5t, strongarm, cap_array) and save as GDS or OASIS.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Full name of the block (e.g. 'sky130.diff_pair', 'sky130.ota_5t')",
                        },
                        "params": {
                            "type": "object",
                            "description": "Key-value dictionary of block parameters (e.g. {'width': 2.0, 'length': 0.5, 'fingers': 4})",
                        },
                        "format": {
                            "type": "string",
                            "enum": ["gds", "oas"],
                            "default": "gds",
                            "description": "Output format: 'gds' (default) or 'oas'",
                        },
                        "output_path": {
                            "type": "string",
                            "description": "Optional output file path. If omitted, saves to a temporary file.",
                        },
                    },
                    "required": ["name"],
                },
            },
            {
                "name": "compile_ir",
                "description": "Compile a Block IR JSON document (declarative layout specification) into a complete multi-instance routed GDS/OASIS layout.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "ir_json": {
                            "type": ["string", "object"],
                            "description": "Block IR design content as either a JSON string or a JSON object dict.",
                        },
                        "output_path": {
                            "type": "string",
                            "description": "Destination path for the compiled GDS/OASIS layout file.",
                        },
                    },
                    "required": ["ir_json", "output_path"],
                },
            },
            {
                "name": "run_drc",
                "description": "Run Sky130 Design Rule Checking (DRC) on a layout file using KLayout's DRC engine.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "gds_path": {
                            "type": "string",
                            "description": "Path to the GDS layout file to check.",
                        },
                        "deck_path": {
                            "type": "string",
                            "description": "Optional path to custom DRC deck file. Defaults to bundled sky130A.drc.",
                        },
                    },
                    "required": ["gds_path"],
                },
            },
            {
                "name": "get_active_layout_info",
                "description": "Query the currently open active layout inside the running KLayout GUI instance (active cell, cell hierarchy, bounding box, layer count).",
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                },
            },
            {
                "name": "insert_block_into_layout",
                "description": "Instantiate and place a parametric block directly into the currently active layout inside the running KLayout GUI instance.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Block name (e.g. 'sky130.diff_pair')",
                        },
                        "params": {
                            "type": "object",
                            "description": "Block parameter overrides",
                        },
                        "cell_name": {
                            "type": "string",
                            "description": "Optional custom cell name to create in KLayout",
                        },
                        "x": {
                            "type": "number",
                            "default": 0.0,
                            "description": "Placement X coordinate in micrometers",
                        },
                        "y": {
                            "type": "number",
                            "default": 0.0,
                            "description": "Placement Y coordinate in micrometers",
                        },
                        "rotation": {
                            "type": "integer",
                            "enum": [0, 90, 180, 270],
                            "default": 0,
                            "description": "Rotation in degrees (0, 90, 180, 270)",
                        },
                    },
                    "required": ["name"],
                },
            },
        ]

    def handle_request(self, req: dict[str, Any]) -> dict[str, Any]:
        """Handle a single JSON-RPC 2.0 MCP request."""
        req_id = req.get("id")
        method = req.get("method")
        params = req.get("params", {})

        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {
                        "tools": {"listChanged": False},
                    },
                    "serverInfo": {
                        "name": SERVER_NAME,
                        "version": SERVER_VERSION,
                    },
                },
            }

        elif method == "notifications/initialized" or method == "initialized":
            return {"jsonrpc": "2.0", "id": req_id, "result": {}}

        elif method == "ping":
            return {"jsonrpc": "2.0", "id": req_id, "result": {}}

        elif method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"tools": self.get_tool_definitions()},
            }

        elif method == "tools/call":
            tool_name = params.get("name")
            arguments = params.get("arguments", {})
            try:
                content = self.execute_tool(tool_name, arguments)
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": content if isinstance(content, str) else json.dumps(content, indent=2),
                            }
                        ],
                        "isError": False,
                    },
                }
            except Exception as e:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": f"Tool execution failed: {e}"}],
                        "isError": True,
                    },
                }

        else:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            }

    def execute_tool(self, name: str, args: dict[str, Any]) -> Any:
        """Execute the specified tool logic."""
        if name == "list_blocks":
            pdk_filter = args.get("pdk")
            blocks = list(base.all_blocks().values())
            if pdk_filter:
                blocks = [b for b in blocks if b.spec.pdk == pdk_filter]
            return [b.spec.model_dump() for b in blocks]

        elif name == "generate_block":
            block_name = args["name"]
            block = base.get(block_name)
            params = args.get("params", {})
            fmt = args.get("format", "gds").lower()
            output_path = args.get("output_path")

            resolved = block.resolve_params(params)
            component = block.component(**resolved)

            if not output_path:
                suffix = f".{fmt}"
                temp_file = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
                output_path = temp_file.name
                temp_file.close()

            p = Path(output_path).resolve()
            p.parent.mkdir(parents=True, exist_ok=True)

            if fmt == "oas":
                component.write_oas(p)
            else:
                component.write_gds(p)

            bbox = component.bbox() if hasattr(component, "bbox") and callable(component.bbox) else getattr(component, "bbox", None)
            bbox_coords = [list(pt) for pt in bbox] if bbox is not None else None

            return {
                "block": block_name,
                "output_path": str(p),
                "format": fmt,
                "resolved_params": resolved,
                "bbox": bbox_coords,
                "ports": [p.name for p in component.ports] if hasattr(component, "ports") else [],
            }

        elif name == "compile_ir":
            raw_ir = args["ir_json"]
            output_path = args["output_path"]
            if isinstance(raw_ir, str):
                design = Design.model_validate_json(raw_ir)
            else:
                design = Design.model_validate(raw_ir)

            dest = Path(output_path).resolve()
            dest.parent.mkdir(parents=True, exist_ok=True)

            top = compile_design(design)
            if dest.suffix.lower() == ".oas":
                top.write_oas(dest)
            else:
                top.write_gds(dest)

            return {
                "design_name": design.name,
                "pdk": design.pdk,
                "output_path": str(dest),
                "instances": [inst.id for inst in design.instances],
                "nets_count": len(design.nets),
            }

        elif name == "run_drc":
            gds_path = args["gds_path"]
            deck_path = args.get("deck_path")
            result = run_klayout_drc(
                gds_path=Path(gds_path),
                deck_path=Path(deck_path) if deck_path else None,
            )
            return {
                "clean": result.clean,
                "violations": result.violations,
                "total_violations": result.total_violations,
                "report_path": str(result.report_path) if result.report_path else None,
            }

        elif name in ("get_active_layout_info", "insert_block_into_layout"):
            resp = self.bridge_client.send_request(name, args)
            if "error" in resp:
                raise RuntimeError(resp["error"].get("message", "Bridge communication error"))
            return resp.get("result", {})

        else:
            raise ValueError(f"Unknown tool: {name}")


def run_stdio_server():
    """Run standard STDIO JSON-RPC server loop."""
    server = LayoutCanvasMCPServer()
    logger.info("Starting Layout Canvas MCP stdio server...")

    while True:
        try:
            line = sys.stdin.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue

            # Support Content-Length header or direct JSON
            if line.startswith("Content-Length:"):
                length = int(line.split(":")[1].strip())
                sys.stdin.readline()  # empty separator line
                content = sys.stdin.read(length)
                req = json.loads(content)
            else:
                req = json.loads(line)

            resp = server.handle_request(req)
            if resp:
                raw_out = json.dumps(resp)
                sys.stdout.write(raw_out + "\n")
                sys.stdout.flush()
        except KeyboardInterrupt:
            break
        except Exception as e:
            logger.error("Server loop error: %s", e)
            err_resp = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": f"Parse or processing error: {e}"},
            }
            sys.stdout.write(json.dumps(err_resp) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_stdio_server()
