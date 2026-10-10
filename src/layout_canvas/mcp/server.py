"""Model Context Protocol (MCP) JSON-RPC 2.0 Server for Layout Canvas.

Exposes IC layout automation, parametric block synthesis, DRC checking,
and live KLayout integration tools to AI Agents (Claude Code, Codex, DeepSeek, Kimi, etc.).
"""

from __future__ import annotations

import json
import logging
import sys
import uuid
from pathlib import Path
from typing import Any

from layout_canvas.blocks import base
from layout_canvas.compiler.compile import compile_design
from layout_canvas.compiler.netlist import compile_netlist
from layout_canvas.compiler.ppa import extract_ppa
from layout_canvas.compiler.render import render_svg
from layout_canvas.derived.connectivity import inspect_connectivity
from layout_canvas.engine.session import DesignSession
from layout_canvas.ir.model import Design
from layout_canvas.mcp.bridge import BridgeClient
from layout_canvas.pdk import all_pdks
from layout_canvas.protocol.project import load_project, save_project
from layout_canvas.tools.drc import run_drc
from layout_canvas.tools.lvs import run_lvs
from layout_canvas.tools.sim import run_netlist, simulate_design

logger = logging.getLogger("layout_canvas.mcp.server")

SERVER_NAME = "layout-canvas-mcp"
SERVER_VERSION = "0.1.0"
PROTOCOL_VERSION = "2024-11-05"


class LayoutCanvasMCPServer:
    """Standard MCP server supporting Block IR compilation, block generators,
    and KLayout GUI interaction."""

    def __init__(self, bridge_client: BridgeClient | None = None):
        self.bridge_client = bridge_client or BridgeClient()
        self.sessions: dict[str, DesignSession] = {}

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        """Return MCP tool schemas."""
        return [
            {
                "name": "list_blocks",
                "description": "List all available parametric analog/mixed-signal blocks with "
                               "their specifications, parameters, ports, and PDKs.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "pdk": {
                            "type": "string",
                            "description": "Filter blocks by PDK name (e.g. 'sky130'). If "
                                           "omitted, returns all blocks.",
                        }
                    },
                },
            },
            {
                "name": "generate_block",
                "description": "Generate a standalone layout for a parametric block (e.g., "
                               "diff_pair, current_mirror, ota_5t, strongarm, cap_array) and save "
                               "as GDS or OASIS.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Full name of the block (e.g. 'sky130.diff_pair', "
                                           "'sky130.ota_5t')",
                        },
                        "params": {
                            "type": "object",
                            "description": "Key-value dictionary of block parameters (e.g. "
                                           "{'width': 2.0, 'length': 0.5, 'fingers': 4})",
                        },
                        "format": {
                            "type": "string",
                            "enum": ["gds", "oas"],
                            "default": "gds",
                            "description": "Output format: 'gds' (default) or 'oas'",
                        },
                        "output_path": {
                            "type": "string",
                            "description": "Optional output file path. If omitted, saves to a "
                                           "temporary file.",
                        },
                    },
                    "required": ["name"],
                },
            },
            {
                "name": "compile_ir",
                "description": "Compile a Block IR JSON document (declarative layout "
                               "specification) into a complete multi-instance routed GDS/OASIS "
                               "layout.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "ir_json": {
                            "type": ["string", "object"],
                            "description": "Block IR design content as either a JSON string or a "
                                           "JSON object dict.",
                        },
                        "output_path": {
                            "type": "string",
                            "description": "Destination path for the compiled GDS/OASIS layout "
                                           "file.",
                        },
                    },
                    "required": ["ir_json", "output_path"],
                },
            },
            {
                "name": "run_drc",
                "description": "Run Design Rule Checking on a layout file. Engine 'pya' runs an "
                               "in-process KLayout geometry-check subset (no external binary "
                               "needed); engine 'klayout' runs a full foundry .drc deck via the "
                               "KLayout executable.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "gds_path": {
                            "type": "string",
                            "description": "Path to the GDS layout file to check.",
                        },
                        "deck_path": {
                            "type": "string",
                            "description": "Optional path to custom DRC deck file (requires "
                                           "engine 'klayout').",
                        },
                        "tech": {"type": "string", "default": "sky130"},
                        "engine": {"type": "string", "enum": ["auto", "pya", "klayout"],
                                   "default": "auto"},
                    },
                    "required": ["gds_path"],
                },
            },
            {
                "name": "extract_netlist",
                "description": "Extract a device-level SPICE netlist from a GDS layout using the "
                               "in-process KLayout engine (LayoutToNetlist). Reports real "
                               "extraction: device count, named nets, hierarchy, and extraction "
                               "errors.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "gds_path": {"type": "string",
                                     "description": "Path to the GDS layout file."},
                        "tech": {"type": "string", "enum": sorted(all_pdks()), "default": "sky130",
                                 "description": "PDK name — built-ins plus descriptor PDKs "
                                                "from LAYOUT_CANVAS_PDK_DIR/LAYOUT_CANVAS_PDKS."},
                        "output_path": {"type": "string",
                                        "description": "Optional path to write the "
                                                       "extracted SPICE."},
                    },
                    "required": ["gds_path"],
                },
            },
            {
                "name": "generate_netlist",
                "description": "Generate a golden SPICE/CDL netlist from a Block IR JSON "
                               "specification for LVS verification.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "ir_json": {
                            "type": ["string", "object"],
                            "description": "Block IR design content as either a JSON string or a "
                                           "JSON object dict.",
                        },
                        "output_path": {
                            "type": "string",
                            "description": "Optional destination path for the generated SPICE "
                                           "netlist.",
                        },
                    },
                    "required": ["ir_json"],
                },
            },
            {
                "name": "run_lvs",
                "description": "Run Layout vs Schematic (LVS) verification comparing layout "
                               "against SPICE netlist.",
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
                        "cell_name": {"type": "string",
                                      "description": "Top cell name (defaults to layout "
                                                     "filename stem)."},
                        "setup_path": {"type": "string", "description": "Netgen setup Tcl file."},
                        "tech": {"type": "string", "default": "sky130"},
                        "engine": {"type": "string", "enum": ["auto", "netgen", "pya"],
                                   "default": "auto",
                                   "description": "'pya' runs in-process KLayout extraction + "
                                                  "NetlistComparer; 'netgen' uses the external "
                                                  "binary."},
                    },
                    "required": ["layout_path", "schematic_path"],
                },
            },
            {
                "name": "verify_design",
                "description": "One-shot physical verification of a design or session: compiles "
                               "to GDS, extracts devices, runs DRC, and LVS-compares against the "
                               "design's own reference netlist. Returns extract/drc/lvs sections "
                               "plus an overall 'passed' flag. Fail-closed: unavailable engines "
                               "report as such, never as a pass.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "session_id": {"type": "string", "description": "Open session to verify."},
                        "ir_json": {
                            "type": ["string", "object"],
                            "description": "Block IR design content (JSON string or object) when "
                                           "no session is used.",
                        },
                    },
                },
            },
            {
                "name": "get_active_layout_info",
                "description": "Query the currently open active layout inside the running KLayout "
                               "GUI instance (active cell, cell hierarchy, bounding box, layer "
                               "count).",
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                },
            },
            {
                "name": "render_preview_svg",
                "description": "Render a Block IR or parametric block into an SVG string for "
                               "visual layout inspection and multimodal AI agent preview.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "ir_json": {
                            "type": ["string", "object"],
                            "description": "Optional Block IR design to compile and render",
                        },
                        "block_name": {
                            "type": "string",
                            "description": "Optional block name to render directly (e.g. "
                                           "'sky130.guard_ring')",
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
                "description": "Instantiate and place a parametric block directly into the "
                               "currently active layout inside the running KLayout GUI instance.",
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
                "description": "Open a Block IR design into a transactional editing session. "
                               "Returns a session_id and revision used by snapshot/transact/undo.",
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
                "description": "Read the complete current design state and revision of a session "
                               "— the agent's ground truth before editing.",
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
                "description": "Apply typed edits to a session's design atomically. Carries an "
                               "expected_revision optimistic lock; use dry_run to validate "
                               "without committing. Ops: set_placement, set_params, add_instance, "
                               "remove_instance, add_net, remove_net, set_net_pins, add_port, "
                               "remove_port, add_constraint, remove_constraint, set_meta.",
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
                            "description": "Optimistic lock: reject if the session revision has "
                                           "moved on.",
                        },
                        "dry_run": {
                            "type": "boolean",
                            "default": False,
                            "description": "Validate and return the resulting design without "
                                           "committing.",
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
                "description": "Read electrical facts — pin-to-net resolution, unconnected pins, "
                               "degenerate nets — before submitting edits. Accepts a session_id "
                               "or an ad-hoc ir_json.",
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
                "name": "compile_session",
                "description": "Compile a session's current design to a GDS/OASIS file at "
                               "output_path.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "session_id": {"type": "string"},
                        "output_path": {"type": "string"},
                    },
                    "required": ["session_id", "output_path"],
                },
            },
            {
                "name": "export_abstract",
                "description": "Export the hierarchical cell abstract (bbox, pin positions, "
                               "per-layer polygon counts) of a session design or ad-hoc ir_json.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "session_id": {"type": "string"},
                        "ir_json": {"type": ["string", "object"]},
                    },
                },
            },
            {
                "name": "export_virtuoso",
                "description": "Export a session design or ad-hoc ir_json for Cadence Virtuoso: a "
                               "SKILL replay script that rebuilds the layout hierarchy in OA "
                               "(dbCreateRect/Polygon/Label/Inst) plus a Spectre .scs design "
                               "netlist.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "session_id": {"type": "string"},
                        "ir_json": {"type": ["string", "object"]},
                        "library": {"type": "string", "default": "canvas_lib",
                                    "description": "Target OA library name in Virtuoso."},
                        "tech_lib": {"type": "string",
                                     "description": "OA tech library the replay attaches to "
                                                    "so LPP names resolve (default: "
                                                    "LAYOUT_CANVAS_VIRTUOSO_TECHLIB env or "
                                                    "per-PDK default)."},
                        "output_dir": {"type": "string",
                                       "description": "Optional dir to write cell.il "
                                                      "and design.scs."},
                    },
                },
            },
            {
                "name": "run_simulation",
                "description": "Simulate a design with ngspice (fail-closed). Provide session_id "
                               "or ir_json plus 'stimulus' (sources, top X instantiation, "
                               "analyses) and optional 'includes' model decks. Blocks without "
                               "transistor-level emitters cause a named refusal.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "session_id": {"type": "string"},
                        "ir_json": {"type": ["string", "object"]},
                        "stimulus": {
                            "type": "string",
                            "description": "SPICE lines appended after the compiled netlist: "
                                           "sources, X top instance, .op/.tran/.ac etc.",
                        },
                        "includes": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Model deck paths to .include (e.g. sky130 device "
                                           "models).",
                        },
                        "deck": {
                            "type": "string",
                            "description": "Run a complete caller-provided SPICE deck verbatim "
                                           "instead of compiling a design.",
                        },
                        "source": {
                            "type": "string",
                            "enum": ["schematic", "extracted"],
                            "description": "'extracted' runs post-layout simulation: compile -> "
                                           "GDS -> KLayout extract -> foundry models. Default is "
                                           "the golden netlist.",
                        },
                        "probes": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Extra nets to record for source=extracted — block pin "
                                           "names resolve hierarchically (e.g. 'tail' -> "
                                           "xd1.tail).",
                        },
                        "analysis": {
                            "type": "string",
                            "enum": ["op", "tran", "ac", "dc"],
                            "description": "Analysis for source=extracted decks (auto-bias path); "
                                           "'ac'/'dc' add a sweep drive on an input port.",
                        },
                        "simulator": {
                            "type": "string",
                            "default": "auto",
                            "description": "Backend: auto | ngspice | xyce | ltspice | spectre | "
                                           "hspice | eldo. 'auto' picks the first "
                                           "probed-available simulator.",
                        },
                    },
                },
            },
            {
                "name": "optimize",
                "description": "Optimize a session design. objective='placement' (default) "
                               "tightens placement margins toward an aspect ratio; "
                               "objective='specs' runs coordinate descent over bounded numeric "
                               "block params scored by testbench spec pass count (needs "
                               "design.testbenches plus a simulator — ngspice — or every "
                               "candidate reports unavailable). Each candidate is committed "
                               "through transact (revisioned, undoable).",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "session_id": {"type": "string"},
                        "objective": {
                            "type": "string",
                            "enum": ["placement", "specs"],
                            "default": "placement",
                            "description": "'placement' = PPA margin tightening; 'specs' = "
                                           "spec-pass-count coordinate descent (requires "
                                           "testbenches on the design).",
                        },
                        "testbench": {
                            "type": "string",
                            "description": "objective='specs' only: restrict scoring to this "
                                           "testbench name; omitted = all design testbenches.",
                        },
                        "target_aspect_ratio": {"type": "number", "default": 1.0},
                        "min_clearance": {"type": "number", "default": 0.5},
                        "max_iterations": {
                            "type": "integer",
                            "description": "Iteration cap; defaults 5 for placement, 6 for specs.",
                        },
                    },
                    "required": ["session_id"],
                },
            },
            {
                "name": "register_cell",
                "description": "Register a compiled design as a reusable cell block under "
                               "'alias'. Afterwards parent IR may instantiate it via "
                               "block='<alias>'. Source: session_id or a .lcproj/IR file path.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "alias": {
                            "type": "string",
                            "description": "Block name parents will reference, e.g. 'cells.ota_v1'",
                        },
                        "session_id": {"type": "string"},
                        "path": {"type": "string"},
                    },
                    "required": ["alias"],
                },
            },
            {
                "name": "import_gds",
                "description": "Import a Virtuoso stream-out GDS as an instantiable cell block "
                               "registered under 'alias'. Pins are discovered from pin-layer "
                               "labels (the GDS must carry pin labels, e.g. met*/pn texts). "
                               "spice_path is optional — without it the cell is layout-only and "
                               "simulation/LVS stay fail-closed.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "alias": {
                            "type": "string",
                            "description": "Block name parents will reference, e.g. 'ext.inv_v1'",
                        },
                        "path": {
                            "type": "string",
                            "description": "Path to the GDS/OASIS file on disk.",
                        },
                        "pdk": {
                            "type": "string",
                            "default": "sky130",
                            "description": "PDK name used to resolve pin-label layers (e.g. "
                                           "'sky130', 'ihp_sg13g2').",
                        },
                        "cell_name": {
                            "type": "string",
                            "description": "Top cell to import; required when the GDS has "
                                           "multiple top cells.",
                        },
                        "spice_path": {
                            "type": "string",
                            "description": "Optional SPICE netlist containing '.subckt "
                                           "<cell_name>' with matching pin count; enables "
                                           "simulation/LVS.",
                        },
                    },
                    "required": ["alias", "path"],
                },
            },
            {
                "name": "import_netlist",
                "description": "Import an upstream SPICE/Spectre netlist (e.g. an analog-canvas "
                               "export) into a Block IR draft. Each instantiated subckt is "
                               "matched to a parametric block by name + pin-name signature; "
                               "unmappable instances are named in 'unresolved' and dropped — "
                               "never guessed. Pass the returned 'ir' to open_design, then refine "
                               "params/placement via transact. Provide 'path' or 'text', plus "
                               "'pdk' (default sky130) and 'top' when the top subckt is "
                               "ambiguous.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Path to a .sp/.cir/.scs netlist on disk.",
                        },
                        "text": {
                            "type": "string",
                            "description": "Raw netlist text (SPICE .subckt/X or Spectre "
                                           "subckt/instance dialects; auto-detected).",
                        },
                        "pdk": {
                            "type": "string",
                            "default": "sky130",
                            "description": "Target PDK whose block registry the subckts map onto.",
                        },
                        "top": {
                            "type": "string",
                            "description": "Top subckt name; required when several subckts are "
                                           "uninstantiated.",
                        },
                        "name": {
                            "type": "string",
                            "description": "Design name override (default: top subckt name).",
                        },
                    },
                },
            },
            {
                "name": "run_testbench",
                "description": "Run a structured simulation testbench declared on the design "
                               "(testbenches[].name). Evaluates each spec and reports per-spec "
                               "pass/fail/unavailable with reasons. Provide 'name' for one bench, "
                               "omit it to run all. Source: session_id or ir_json.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "session_id": {"type": "string"},
                        "ir_json": {
                            "type": ["string", "object"],
                            "description": "Block IR design content when no session is used.",
                        },
                        "name": {
                            "type": "string",
                            "description": "Testbench name from design.testbenches; omitted = run "
                                           "all.",
                        },
                    },
                },
            },
            {
                "name": "gallery_list",
                "description": "List gallery entries with optional filters: verified_only (only "
                               "entries whose publish-time verification passed), pdk, tag.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "verified_only": {"type": "boolean", "default": False},
                        "pdk": {"type": "string"},
                        "tag": {"type": "string"},
                    },
                },
            },
            {
                "name": "gallery_publish",
                "description": "Publish a design (session_id or ir_json) to the local gallery "
                               "with a preview, content hash dedup and publish-time DRC/LVS "
                               "verification record. Sets ai_generated=true by default "
                               "(agent-published); pass ai_generated=false for human-authored "
                               "designs. allow_duplicate bypasses the content-hash dedup.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "session_id": {"type": "string"},
                        "ir_json": {"type": ["string", "object"]},
                        "author": {"type": "string", "default": "agent"},
                        "description": {"type": "string", "default": ""},
                        "tags": {"type": "array", "items": {"type": "string"}},
                        "ai_generated": {"type": "boolean", "default": True},
                        "allow_duplicate": {"type": "boolean", "default": False},
                    },
                },
            },
            {
                "name": "gallery_stats",
                "description": "Gallery contributor leaderboard: {authors: [{author, count, "
                               "verified}], total, verified_count} — verified counts entries "
                               "whose publish-time verification passed.",
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                },
            },
            {
                "name": "gallery_get",
                "description": "Fetch a gallery entry by id — design JSON, meta "
                               "(author/tags/design_hash/verification) and preview SVG — without "
                               "opening a session.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "Gallery entry id."},
                    },
                    "required": ["id"],
                },
            },
            {
                "name": "gallery_fork",
                "description": "Open a gallery entry as a new editing session — returns "
                               "session_id, revision 0, the design and its meta.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "Gallery entry id."},
                    },
                    "required": ["id"],
                },
            },
            {
                "name": "probe_environment",
                "description": "Probe the host for EDA tools: simulators "
                               "(ngspice/Xyce/LTspice/Spectre/HSPICE/Eldo), DRC "
                               "(KLayout/Magic/Calibre), LVS (Netgen), plus external PDK "
                               "descriptors loaded from LAYOUT_CANVAS_PDK_DIR/LAYOUT_CANVAS_PDKS. "
                               "Returns availability, version, and license gating per tool — all "
                               "detection, no verdicts.",
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                },
            },
            {
                "name": "save_project",
                "description": "Save a session's design as a versioned .lcproj.json project file.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "session_id": {"type": "string"},
                        "path": {"type": "string"},
                    },
                    "required": ["session_id", "path"],
                },
            },
            {
                "name": "load_project",
                "description": "Load a .lcproj.json (or bare Block IR JSON) into a new session. "
                               "Returns session_id, load status and diagnostics.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "text": {"type": "string", "description": "Raw project JSON content."},
                    },
                },
            },
            {
                "name": "inspect_ppa",
                "description": "Extract PPA (Power, Performance, Area, Wirelength) metrics from a "
                               "Block IR design or generated layout.",
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
                                "text": (content if isinstance(content, str)
                                         else json.dumps(content, indent=2)),
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
        return (Design.model_validate_json(raw_ir) if isinstance(raw_ir, str)
                else Design.model_validate(raw_ir))

    def execute_tool(self, name: str, args: dict[str, Any]) -> Any:
        """Execute the specified tool logic."""
        if name == "open_design":
            if args.get("path"):
                result = load_project(Path(args["path"]))
                if not result.ok or result.design is None:
                    raise ValueError(
                        "cannot load design: "
                        + "; ".join(d.message for d in result.diagnostics)
                    )
                design = result.design
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

        elif name == "compile_session":
            session = self._get_session(args.get("session_id"))
            dest = Path(args["output_path"]).resolve()
            dest.parent.mkdir(parents=True, exist_ok=True)
            top = compile_design(session.design)
            if dest.suffix.lower() == ".oas":
                top.write_oas(dest)
            else:
                top.write_gds(dest)
            return {
                "output_path": str(dest),
                "revision": session.revision,
                "design_name": session.design.name,
            }

        elif name == "export_abstract":
            from layout_canvas.compiler.hierarchy import cell_abstract

            if args.get("session_id"):
                session = self._get_session(args["session_id"])
                return cell_abstract(session.design)
            if args.get("ir_json") is not None:
                return cell_abstract(self._parse_design(args["ir_json"]))
            raise ValueError("export_abstract needs 'session_id' or 'ir_json'")

        elif name == "export_virtuoso":
            import tempfile

            from layout_canvas.compiler.virtuoso import export_skill, export_spectre
            if args.get("session_id"):
                design = self._get_session(args["session_id"]).design
            elif args.get("ir_json") is not None:
                design = self._parse_design(args["ir_json"])
            else:
                raise ValueError("export_virtuoso needs 'session_id' or 'ir_json'")
            comp = compile_design(design)
            out_dir = Path(args.get("output_dir") or tempfile.mkdtemp())
            out_dir.mkdir(parents=True, exist_ok=True)
            gds = out_dir / f"{design.name}.gds"
            comp.write_gds(str(gds))
            skill = export_skill(gds, args.get("library", "canvas_lib"),
                                 design.pdk, tech_lib=args.get("tech_lib"))
            spectre = export_spectre(compile_netlist(design))
            (out_dir / f"{design.name}.il").write_text(skill, encoding="utf-8")
            (out_dir / f"{design.name}.scs").write_text(spectre, encoding="utf-8")
            return {"output_dir": str(out_dir), "gds": str(gds),
                    "skill_path": str(out_dir / f"{design.name}.il"),
                    "spectre_path": str(out_dir / f"{design.name}.scs"),
                    "skill": skill, "spectre": spectre}

        elif name == "run_simulation":
            if args.get("deck"):
                return run_netlist(
                    args["deck"], simulator=args.get("simulator", "auto")
                ).to_dict()
            if args.get("session_id"):
                design = self._get_session(args["session_id"]).design
            elif args.get("ir_json") is not None:
                design = self._parse_design(args["ir_json"])
            else:
                raise ValueError("run_simulation needs 'session_id', 'ir_json', or 'deck'")
            if args.get("source") == "extracted":
                # Post-layout path: compile -> GDS -> extract -> simulate the
                # extracted netlist under foundry wrapper models.
                from layout_canvas.tools.sim import simulate_extracted
                return simulate_extracted(
                    design,
                    analysis=args.get("analysis", "op"),
                    stimulus=args.get("stimulus"),
                    probes=args.get("probes"),
                    simulator=args.get("simulator", "auto"),
                )
            return simulate_design(
                design,
                stimulus=args.get("stimulus", ""),
                includes=args.get("includes"),
                simulator=args.get("simulator", "auto"),
            ).to_dict()

        elif name == "verify_design":
            from layout_canvas.tools.verify import verify_design
            if args.get("session_id"):
                design = self._get_session(args["session_id"]).design
            elif args.get("ir_json") is not None:
                design = self._parse_design(args["ir_json"])
            else:
                raise ValueError("verify_design needs 'session_id' or 'ir_json'")
            res = verify_design(design)
            if args.get("session_id"):
                self._get_session(args["session_id"]).record_run("verify", {
                    "passed": res["passed"],
                    "lvs_status": res["lvs"].get("status"),
                    "drc_violations": res["drc"].get("total_violations"),
                })
            return res

        elif name == "probe_environment":
            from layout_canvas.tools.backends import probe_environment

            return probe_environment()

        elif name == "optimize":
            from layout_canvas.engine.optimize import optimize_session

            session = self._get_session(args.get("session_id"))
            return optimize_session(
                session,
                target_aspect_ratio=float(args.get("target_aspect_ratio", 1.0)),
                min_clearance=float(args.get("min_clearance", 0.5)),
                max_iterations=(
                    int(args["max_iterations"])
                    if args.get("max_iterations") is not None else None
                ),
                objective=str(args.get("objective", "placement")),
                testbench=args.get("testbench"),
            ).to_dict()

        elif name == "register_cell":
            from layout_canvas.blocks.cells import register_design_cell

            alias = args["alias"]
            if args.get("session_id"):
                source = self._get_session(args["session_id"]).design
            elif args.get("path"):
                result = load_project(Path(args["path"]))
                if not result.ok or result.design is None:
                    raise ValueError(
                        "cannot load cell source: "
                        + "; ".join(d.message for d in result.diagnostics)
                    )
                source = result.design
            else:
                raise ValueError("register_cell needs 'session_id' or 'path'")
            block = register_design_cell(alias, source)
            return {
                "alias": alias,
                "ports": [p.name for p in block.spec.ports],
                "pdk": block.spec.pdk,
            }

        elif name == "run_testbench":
            from layout_canvas.tools.testbench import run_all, run_testbench

            if args.get("session_id"):
                design = self._get_session(args["session_id"]).design
            elif args.get("ir_json") is not None:
                design = self._parse_design(args["ir_json"])
            else:
                raise ValueError("run_testbench needs 'session_id' or 'ir_json'")
            name = args.get("name")
            if name:
                runs = [run_testbench(design, str(name))]
            else:
                runs = run_all(design)
            if args.get("session_id"):
                self._get_session(args["session_id"]).record_run("testbench", {
                    "testbenches": [r.get("testbench") for r in runs],
                    "spec_status": [r.get("spec_status") for r in runs],
                    "sim_status": [r.get("status") for r in runs],
                    "spec_counts": [
                        {s: sum(1 for e in r.get("specs", []) if e["status"] == s)
                         for s in ("pass", "fail", "unavailable")}
                        for r in runs
                    ],
                })
            return {"runs": runs}

        elif name == "gallery_list":
            from layout_canvas.web import gallery

            return {"entries": gallery.list_entries(
                verified_only=bool(args.get("verified_only")),
                pdk=args.get("pdk") or None,
                tag=args.get("tag") or None,
            )}

        elif name == "gallery_publish":
            from layout_canvas.web import gallery

            if args.get("session_id"):
                design = self._get_session(args["session_id"]).design
            elif args.get("ir_json") is not None:
                design = self._parse_design(args["ir_json"])
            else:
                raise ValueError("gallery_publish needs 'session_id' or 'ir_json'")
            try:
                return gallery.publish(design, {
                    "author": args.get("author", "agent"),
                    "description": args.get("description", ""),
                    "tags": args.get("tags") or [],
                    "ai_generated": bool(args.get("ai_generated", True)),
                    "allow_duplicate": bool(args.get("allow_duplicate")),
                })
            except ValueError as exc:
                return {"status": "error", "error": str(exc)}

        elif name == "gallery_stats":
            from layout_canvas.web import gallery

            return gallery.stats()

        elif name == "gallery_get":
            from layout_canvas.web import gallery

            entry = gallery.get_entry(str(args.get("id", "")))
            if entry is None:
                raise ValueError("gallery entry not found")
            return entry

        elif name == "gallery_fork":
            from layout_canvas.web import gallery

            entry = gallery.get_entry(str(args.get("id", "")))
            if entry is None:
                raise ValueError("gallery entry not found")
            design = Design.model_validate(entry["design"])
            session_id = uuid.uuid4().hex[:12]
            self.sessions[session_id] = DesignSession(design)
            return {
                "session_id": session_id,
                "revision": 0,
                "design": design.model_dump(),
                "meta": entry["meta"],
            }

        elif name == "import_gds":
            from layout_canvas.blocks.gds_cell import import_summary, register_gds_cell

            spice_text = None
            if args.get("spice_path"):
                spice_text = Path(args["spice_path"]).read_text(encoding="utf-8")
            block = register_gds_cell(
                args["alias"],
                args["path"],
                pdk=args.get("pdk", "sky130"),
                cell_name=args.get("cell_name"),
                spice_text=spice_text,
            )
            return import_summary(block)

        elif name == "import_netlist":
            from layout_canvas.compiler.netlist_import import import_netlist

            if args.get("path"):
                text = Path(args["path"]).read_text(encoding="utf-8")
            elif args.get("text") is not None:
                text = args["text"]
            else:
                raise ValueError("import_netlist needs 'path' or 'text'")
            return import_netlist(
                text, pdk=args.get("pdk", "sky130"), top=args.get("top"),
                design_name=args.get("name"))

        elif name == "save_project":
            session = self._get_session(args.get("session_id"))
            p = save_project(session.design, args["path"])
            return {"path": str(p), "revision": session.revision}

        elif name == "load_project":
            source: Any = args.get("path") if args.get("path") else args.get("text")
            if source is None:
                raise ValueError("load_project needs 'path' or 'text'")
            result = load_project(Path(source) if args.get("path") else str(source))
            out = result.to_dict()
            if result.ok and result.design is not None:
                session_id = uuid.uuid4().hex[:12]
                self.sessions[session_id] = DesignSession(result.design)
                out["session_id"] = session_id
            return out

        elif name == "list_blocks":
            pdk_filter = args.get("pdk")
            blocks = list(base.all_blocks().values())
            if pdk_filter:
                blocks = [b for b in blocks if b.spec.pdk == pdk_filter]
            return [b.spec.model_dump() for b in blocks]

        elif name == "generate_block":
            block_name = args["name"]
            try:
                block = base.get(block_name)
            except KeyError:
                # '<pdk>.<block>' on a descriptor-only PDK gets the honest
                # boundary message instead of an opaque KeyError.
                gap = base.describe_pdk_block_gap(str(block_name).split(".", 1)[0])
                if gap is not None:
                    raise ValueError(gap) from None
                raise
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

            bbox = (component.bbox()
                    if hasattr(component, "bbox") and callable(component.bbox)
                    else getattr(component, "bbox", None))
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
            result = run_drc(
                gds_path,
                tech=args.get("tech", "sky130"),
                deck_path=deck_path,
                engine=args.get("engine", "auto"),
            )
            return result.to_dict()

        elif name == "extract_netlist":
            from layout_canvas.tools.extract import extract_netlist
            result = extract_netlist(args["gds_path"], tech=args.get("tech", "sky130"))
            if result.status == "ok" and args.get("output_path"):
                Path(args["output_path"]).write_text(result.netlist_text, encoding="utf-8")
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
                design = (Design.model_validate_json(raw_ir) if isinstance(raw_ir, str)
                          else Design.model_validate(raw_ir))
                comp = compile_design(design)
            elif "block_name" in args and args["block_name"]:
                b_name = args["block_name"]
                block = base.get(b_name)
                comp = block.component(**args.get("params", {}))
            else:
                raise ValueError("Must provide either 'ir_json' or 'block_name'")

            return render_svg(comp)

        elif name == "inspect_ppa":
            raw_ir = args["ir_json"]
            design = (Design.model_validate_json(raw_ir) if isinstance(raw_ir, str)
                      else Design.model_validate(raw_ir))
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
                tech=args.get("tech", "sky130"),
                engine=args.get("engine", "auto"),
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
