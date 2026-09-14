"""Model Context Protocol (MCP) JSON-RPC 2.0 Server for Layout Canvas.

Exposes IC layout automation, parametric block synthesis, DRC checking,
and live KLayout integration tools to AI Agents (Claude Code, Codex, DeepSeek, Kimi, etc.).
"""

from __future__ import annotations

import json
import logging
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

from layout_canvas.blocks import base
from layout_canvas.compiler.compile import compile_design
from layout_canvas.compiler.netlist import compile_netlist
from layout_canvas.compiler.ppa import extract_ppa
from layout_canvas.derived.connectivity import inspect_connectivity
from layout_canvas.engine.session import DesignSession
from layout_canvas.ir.model import Design
from layout_canvas.mcp.bridge import BridgeClient
from layout_canvas.tools.drc import run_klayout_drc
from layout_canvas.tools.lvs import run_lvs

logger = logging.getLogger("layout_canvas.mcp.server")

SERVER_NAME = "layout-canvas-mcp"
SERVER_VERSION = "0.1.0"
PROTOCOL_VERSION = "2024-11-05"


class LayoutCanvasMCPServer:
    """Standard MCP server supporting Block IR compilation, block generators, and KLayout GUI interaction."""

    def __init__(self, bridge_client: BridgeClient | None = None):
        self.bridge_client = bridge_client or BridgeClient()
        self.sessions: dict[str, DesignSession] = {}

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
                        "tech": {"type": "string", "default": "sky130"},
                    },
                    "required": ["gds_path"],
                },
            },
            {
                "name": "generate_netlist",
                "description": "Generate a golden SPICE/CDL netlist from a Block IR JSON specification for LVS verification.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "ir_json": {
                            "type": ["string", "object"],
                            "description": "Block IR design content as either a JSON string or a JSON object dict.",
                        },
                        "output_path": {
                            "type": "string",
                            "description": "Optional destination path for the generated SPICE netlist.",
                        },
                    },
                    "required": ["ir_json"],
                },
            },
            {
                "name": "run_lvs",
                "description": "Run Layout vs Schematic (LVS) verification comparing layout against SPICE netlist.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "layout_path": {
                            "type": "string",
                            "description": "Path to layout file (GDS).",
                        },
                        "schematic_path": {
                            "type": "string",
                            "description": "Path to golden schematic SPICE netlist.",
                        },
                        "cell_name": {"type": "string", "description": "Top cell name (defaults to layout filename stem)."},
                        "setup_path": {"type": "string", "description": "Netgen setup Tcl file."},
                    },
                    "required": ["layout_path", "schematic_path"],
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
                "name": "render_preview_svg",
                "description": "Render a Block IR or parametric block into an SVG string for visual layout inspection and multimodal AI agent preview.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "ir_json": {
                            "type": ["string", "object"],
                            "description": "Optional Block IR design to compile and render",
                        },
                        "block_name": {
                            "type": "string",
                            "description": "Optional block name to render directly (e.g. 'sky130.guard_ring')",
                        },
                        "params": {
                            "type": "object",
                            "description": "Optional parameters if block_name is specified",
                        },
                    },
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
            {
                "name": "open_design",
                "description": "Open a Block IR design into a transactional editing session. Returns a session_id and revision used by snapshot/transact/undo.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "ir_json": {
                            "type": ["string", "object"],
                            "description": "Block IR design content as a JSON string or object.",
                        },
                        "path": {
                            "type": "string",
                            "description": "Path to a Block IR JSON file on disk.",
                        },
                    },
                },
            },
            {
                "name": "snapshot",
                "description": "Read the complete current design state and revision of a session — the agent's ground truth before editing.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "session_id": {"type": "string"},
                    },
                    "required": ["session_id"],
                },
            },
            {
                "name": "transact",
                "description": "Apply typed edits to a session's design atomically. Carries an expected_revision optimistic lock; use dry_run to validate without committing. Ops: set_placement, set_params, add_instance, remove_instance, add_net, remove_net, set_net_pins, add_port, remove_port, add_constraint, remove_constraint, set_meta.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "session_id": {"type": "string"},
                        "edits": {
                            "type": "array",
                            "items": {"type": "object"},
                            "description": "List of edit objects, each with an 'op' field.",
                        },
                        "expected_revision": {
                            "type": "integer",
                            "description": "Optimistic lock: reject if the session revision has moved on.",
                        },
                        "dry_run": {
                            "type": "boolean",
                            "default": False,
                            "description": "Validate and return the resulting design without committing.",
                        },
                    },
                    "required": ["session_id", "edits"],
                },
            },
            {
                "name": "undo",
                "description": "Revert the last committed transaction in a session.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "session_id": {"type": "string"},
                    },
                    "required": ["session_id"],
                },
            },
            {
                "name": "inspect_connectivity",
                "description": "Read electrical facts — pin-to-net resolution, unconnected pins, degenerate nets — before submitting edits. Accepts a session_id or an ad-hoc ir_json.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "session_id": {"type": "string"},
                        "ir_json": {
                            "type": ["string", "object"],
                            "description": "Block IR design to inspect without opening a session.",
                        },
                    },
                },
            },
            {
                "name": "close_design",
                "description": "Close a session and release its design state.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "session_id": {"type": "string"},
                    },
                    "required": ["session_id"],
                },
            },
            {
                "name": "inspect_ppa",
                "description": "Extract PPA (Power, Performance, Area, Wirelength) metrics from a Block IR design or generated layout.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "ir_json": {
                            "type": ["string", "object"],
                            "description": "Block IR design JSON",
                        },
                    },
                    "required": ["ir_json"],
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

    def _get_session(self, session_id: Any) -> DesignSession:
        if not isinstance(session_id, str) or session_id not in self.sessions:
            raise KeyError(f"unknown session_id {session_id!r}; call open_design first")
        return self.sessions[session_id]

    @staticmethod
    def _parse_design(raw_ir: Any) -> Design:
        return Design.model_validate_json(raw_ir) if isinstance(raw_ir, str) else Design.model_validate(raw_ir)

    def execute_tool(self, name: str, args: dict[str, Any]) -> Any:
        """Execute the specified tool logic."""
        if name == "open_design":
            if args.get("path"):
                design = Design.from_json(Path(args["path"]).read_text(encoding="utf-8"))
            elif args.get("ir_json") is not None:
                design = self._parse_design(args["ir_json"])
            else:
                raise ValueError("open_design needs 'ir_json' or 'path'")
            session_id = uuid.uuid4().hex[:12]
            self.sessions[session_id] = DesignSession(design)
            return {
                "session_id": session_id,
                "revision": 0,
                "design_name": design.name,
                "pdk": design.pdk,
            }

        elif name == "snapshot":
            return self._get_session(args.get("session_id")).snapshot().to_dict()

        elif name == "transact":
            session = self._get_session(args.get("session_id"))
            return session.transact(
                edits=args.get("edits"),
                expected_revision=args.get("expected_revision"),
                dry_run=bool(args.get("dry_run", False)),
            ).to_dict()

        elif name == "undo":
            return self._get_session(args.get("session_id")).undo().to_dict()

        elif name == "inspect_connectivity":
            if args.get("session_id"):
                return self._get_session(args["session_id"]).connectivity().to_dict()
            if args.get("ir_json") is not None:
                return {
                    "status": "ok",
                    "data": inspect_connectivity(self._parse_design(args["ir_json"])),
                }
            raise ValueError("inspect_connectivity needs 'session_id' or 'ir_json'")

        elif name == "close_design":
            session_id = args.get("session_id")
            self._get_session(session_id)
            del self.sessions[session_id]
            return {"closed": session_id}

        elif name == "list_blocks":
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
            if bbox is not None:
                if hasattr(bbox, "left"):
                    bbox_coords = [bbox.left, bbox.bottom, bbox.right, bbox.top]
                else:
                    try:
                        bbox_coords = [list(pt) for pt in bbox]
                    except Exception:
                        bbox_coords = str(bbox)
            else:
                bbox_coords = None

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
                tech=args.get("tech", "sky130"),
            )
            return result.to_dict()

        elif name == "generate_netlist":
            raw_ir = args["ir_json"]
            output_path = args.get("output_path")
            if isinstance(raw_ir, str):
                design = Design.model_validate_json(raw_ir)
            else:
                design = Design.model_validate(raw_ir)
            spice_code = compile_netlist(design)
            if output_path:
                dest = Path(output_path).resolve()
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(spice_code, encoding="utf-8")
            return {
                "design_name": design.name,
                "spice": spice_code,
                "output_path": str(output_path) if output_path else None,
            }

        elif name == "render_preview_svg":
            if "ir_json" in args and args["ir_json"]:
                raw_ir = args["ir_json"]
                design = Design.model_validate_json(raw_ir) if isinstance(raw_ir, str) else Design.model_validate(raw_ir)
                comp = compile_design(design)
            elif "block_name" in args and args["block_name"]:
                b_name = args["block_name"]
                block = base.get(b_name)
                comp = block.component(**args.get("params", {}))
            else:
                raise ValueError("Must provide either 'ir_json' or 'block_name'")

            bb = comp.bbox()
            left = float(bb.left) if hasattr(bb, "left") else -10.0
            bottom = float(bb.bottom) if hasattr(bb, "bottom") else -10.0
            width = float(bb.width()) if hasattr(bb, "width") else 20.0
            height = float(bb.height()) if hasattr(bb, "height") else 20.0

            # Generate lightweight geometric SVG preview
            svg_lines = [
                f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{left - 1.0} {bottom - 1.0} {width + 2.0} {height + 2.0}" width="600" height="600">',
                f'  <rect x="{left}" y="{bottom}" width="{width}" height="{height}" fill="#1e1e1e" stroke="#555" stroke-width="0.1"/>',
            ]
            for p in comp.ports.values():
                px, py = float(p.center[0]), float(p.center[1])
                svg_lines.append(f'  <circle cx="{px}" cy="{py}" r="0.2" fill="#ff4444" />')
                svg_lines.append(f'  <text x="{px + 0.3}" y="{py}" font-size="0.4" fill="#ffffff">{p.name}</text>')
            svg_lines.append('</svg>')

            return {
                "format": "svg",
                "bbox": [left, bottom, left + width, bottom + height],
                "svg": "\n".join(svg_lines),
            }

        elif name == "inspect_ppa":
            raw_ir = args["ir_json"]
            design = Design.model_validate_json(raw_ir) if isinstance(raw_ir, str) else Design.model_validate(raw_ir)
            comp = compile_design(design)
            return extract_ppa(comp, design)

        elif name == "run_lvs":
            layout_path = Path(args["layout_path"])
            schematic_path = Path(args["schematic_path"])
            result = run_lvs(
                layout_path=layout_path,
                schematic_path=schematic_path,
                cell_name=args.get("cell_name"),
                setup_path=args.get("setup_path"),
            )
            return result.to_dict()

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
