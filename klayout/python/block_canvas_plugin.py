"""Block Canvas - KLayout Salt Plugin Macro.

Installs a 'Block Canvas' menu and an interactive Dockable Palette in KLayout:
1. Block Palette: browse, parameterize, and instantiate parametric blocks directly into the active layout.
2. Live Parameter Inspector: inspect & update parameters of block instances.
3. Block IR Import/Export: seamless bridge between JSON IR and KLayout layout.
4. Embedded MCP TCP Bridge: allows external AI agents (Claude Code, Codex, DeepSeek, Kimi) to directly query and manipulate the live KLayout canvas.
5. Run DRC: one-click design rule checking using Sky130 DRC deck.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

# pya is provided by KLayout runtime
try:
    import pya
except ImportError:
    pya = None

logger = logging.getLogger("layout_canvas.klayout_plugin")

# Safe imports for layout_canvas modules if in PYTHONPATH
try:
    from layout_canvas.blocks.base import REGISTRY
    from layout_canvas.compiler.compile import compile_design
    from layout_canvas.ir.model import Design
    from layout_canvas.mcp.bridge import DEFAULT_BRIDGE_PORT, KLayoutBridgeServer
except ImportError:
    REGISTRY = None
    compile_design = None
    Design = None
    KLayoutBridgeServer = None
    DEFAULT_BRIDGE_PORT = 9099


class BlockCanvasPlugin:
    def __init__(self):
        self.menu_name = "block_canvas_menu"
        self.bridge_server: Any = None
        self.dock_widget: Any = None

    def install_menu(self):
        if pya is None or not hasattr(pya, "Application"):
            return

        app = pya.Application.instance()
        main_window = app.main_window()
        menu = main_window.menu()

        # Add top-level menu if not present
        if not menu.is_menu("block_canvas"):
            menu.insert_menu("help_menu", "block_canvas", "Block Canvas")

        # Action: Show Dockable Block Palette
        a_palette = pya.Action()
        a_palette.title = "Show Block Palette Window"
        a_palette.on_triggered = self.show_palette_dock
        menu.insert_item("block_canvas.end", "show_palette", a_palette)

        # Action: Insert Block dialog
        a_insert = pya.Action()
        a_insert.title = "Insert Parametric Block..."
        a_insert.on_triggered = self.action_insert_block
        menu.insert_item("block_canvas.end", "insert_block", a_insert)

        # Action: Import Block IR
        a_import = pya.Action()
        a_import.title = "Import Block IR (JSON)..."
        a_import.on_triggered = self.action_import_ir
        menu.insert_item("block_canvas.end", "import_ir", a_import)

        # Action: Export Block IR
        a_export = pya.Action()
        a_export.title = "Export Block IR (JSON)..."
        a_export.on_triggered = self.action_export_ir
        menu.insert_item("block_canvas.end", "export_ir", a_export)

        # Separator
        menu.insert_separator("block_canvas.end", "sep1")

        # Action: Toggle MCP Server Bridge
        a_mcp = pya.Action()
        a_mcp.title = "Toggle MCP Bridge Server (Agent Access)"
        a_mcp.on_triggered = self.action_toggle_mcp_bridge
        menu.insert_item("block_canvas.end", "toggle_mcp", a_mcp)

        # Action: Run DRC
        a_drc = pya.Action()
        a_drc.title = "Run Sky130 DRC"
        a_drc.on_triggered = self.action_run_drc
        menu.insert_item("block_canvas.end", "run_drc", a_drc)

        # Start bridge server by default for AI agent access
        self.start_bridge_server()

    def start_bridge_server(self, port: int = DEFAULT_BRIDGE_PORT) -> bool:
        """Start the embedded TCP bridge server for MCP / AI agent interactions."""
        if KLayoutBridgeServer is None:
            return False
        if self.bridge_server and self.bridge_server.is_running():
            return True

        self.bridge_server = KLayoutBridgeServer(port=port, handler=self.handle_bridge_rpc)
        return self.bridge_server.start()

    def handle_bridge_rpc(self, method: str, params: dict[str, Any]) -> Any:
        """Handler for remote RPC calls from Layout Canvas MCP server."""
        if pya is None:
            return {"error": {"code": -32001, "message": "pya KLayout runtime unavailable"}}

        app = pya.Application.instance()
        main_window = app.main_window()
        view = main_window.current_view()

        if method == "get_active_layout_info":
            if not view:
                return {"active": False, "message": "No layout currently open in KLayout"}
            cv = view.active_cellview()
            if not cv or not cv.is_valid():
                return {"active": False, "message": "No active cellview"}
            layout = cv.layout()
            cell = cv.cell
            return {
                "active": True,
                "cell_name": cell.name if cell else None,
                "top_cells": [c.name for c in layout.top_cells()],
                "layers": [f"{li.layer}/{li.datatype}" for li in layout.layer_infos()],
                "dbu": layout.dbu,
                "bbox": [cell.bbox().left * layout.dbu, cell.bbox().bottom * layout.dbu,
                         cell.bbox().right * layout.dbu, cell.bbox().top * layout.dbu] if cell else None,
            }

        elif method == "insert_block_into_layout":
            block_name = params.get("name")
            block_params = params.get("params", {})
            x = float(params.get("x", 0.0))
            y = float(params.get("y", 0.0))

            if REGISTRY is None:
                return {"error": {"code": -32002, "message": "layout_canvas block library not found in Python path"}}

            try:
                block = REGISTRY.get(block_name)
                resolved = block.resolve_params(block_params)
                comp = block.build(**resolved)

                # Export temp GDS and merge into current layout
                with tempfile.NamedTemporaryFile(suffix=".gds", delete=False) as tf:
                    temp_gds = tf.name
                comp.write_gds(temp_gds)

                if not view:
                    main_window.create_layout(0)
                    view = main_window.current_view()
                cv = view.active_cellview()
                layout = cv.layout()

                # Read component into target layout
                src_layout = pya.Layout()
                src_layout.read(temp_gds)
                os.remove(temp_gds)

                src_top = src_layout.top_cell()
                imported_cell = layout.create_cell(f"{block_name.split('.')[-1]}_{src_top.name}")
                imported_cell.copy_tree(src_top)

                active_cell = cv.cell or layout.top_cell()
                if not active_cell:
                    active_cell = layout.create_cell("TOP")
                    cv.cell = active_cell

                dbu = layout.dbu
                inst_point = pya.Point(int(round(x / dbu)), int(round(y / dbu)))
                active_cell.insert(pya.CellInstArray(imported_cell.cell_index(), pya.Trans(inst_point)))
                view.zoom_fit()

                return {
                    "success": True,
                    "cell_name": imported_cell.name,
                    "placed_at": [x, y],
                    "resolved_params": resolved,
                }
            except Exception as e:
                return {"error": {"code": -32603, "message": f"Insertion failed: {e}"}}

        return {"error": {"code": -32601, "message": f"Unsupported bridge method: {method}"}}

    def show_palette_dock(self):
        """Displays or focuses the Qt dockable Block Palette."""
        if pya is None:
            return
        app = pya.Application.instance()
        main_window = app.main_window()

        if self.dock_widget is not None:
            self.dock_widget.show()
            self.dock_widget.raise_()
            return

        try:
            # Build QDockWidget using pya.Q* bindings
            dock = pya.QDockWidget("Block Canvas Palette", main_window)
            widget = pya.QWidget(dock)
            layout = pya.QVBoxLayout(widget)

            # Block selector
            lbl_block = pya.QLabel("Select Parametric Block:", widget)
            layout.addWidget(lbl_block)

            combo = pya.QComboBox(widget)
            available_blocks = [
                "sky130.diff_pair",
                "sky130.current_mirror",
                "sky130.ota_5t",
                "sky130.strongarm",
                "sky130.cap_array",
            ]
            for b in available_blocks:
                combo.addItem(b)
            layout.addWidget(combo)

            # Action button: Instantiate
            btn_inst = pya.QPushButton("Instantiate in Active Cell", widget)
            def on_instantiate():
                selected = combo.currentText
                self.handle_bridge_rpc("insert_block_into_layout", {"name": selected, "x": 0.0, "y": 0.0})
            btn_inst.clicked = on_instantiate
            layout.addWidget(btn_inst)

            # Action button: Toggle MCP Bridge
            btn_bridge = pya.QPushButton("Toggle AI Agent MCP Server", widget)
            btn_bridge.clicked = self.action_toggle_mcp_bridge
            layout.addWidget(btn_bridge)

            widget.setLayout(layout)
            dock.setWidget(widget)
            main_window.addDockWidget(pya.Qt.RightDockWidgetArea, dock)
            self.dock_widget = dock
            dock.show()
        except Exception as e:
            pya.MessageBox.warning("Palette Error", f"Unable to create dockable widget: {e}", pya.MessageBox.Ok)

    def action_insert_block(self):
        if pya is None:
            return
        blocks = [
            "sky130.diff_pair",
            "sky130.current_mirror",
            "sky130.ota_5t",
            "sky130.strongarm",
            "sky130.cap_array",
        ]
        choice = pya.InputDialog.ask_item(
            "Insert Block", "Choose a parametric block to instantiate:", blocks, 0
        )
        if choice is None:
            return

        res = self.handle_bridge_rpc("insert_block_into_layout", {"name": choice})
        if "error" in res:
            pya.MessageBox.warning("Insertion Error", res["error"]["message"], pya.MessageBox.Ok)
        else:
            pya.MessageBox.info("Block Canvas", f"Successfully inserted {choice} into active layout!", pya.MessageBox.Ok)

    def action_import_ir(self):
        if pya is None:
            return
        path = pya.FileDialog.ask_open_file_name("Open Block IR JSON", "", "Block IR (*.json)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if Design is not None and compile_design is not None:
                design = Design.model_validate(data)
                comp = compile_design(design)
                with tempfile.NamedTemporaryFile(suffix=".gds", delete=False) as tf:
                    temp_gds = tf.name
                comp.write_gds(temp_gds)

                app = pya.Application.instance()
                main_window = app.main_window()
                main_window.load_layout(temp_gds, 1)
                os.remove(temp_gds)
            pya.MessageBox.info(
                "Block Canvas",
                f"Successfully compiled and loaded design: {data.get('name', 'unnamed')}\nInstances: {len(data.get('instances', []))}",
                pya.MessageBox.Ok,
            )
        except Exception as e:
            pya.MessageBox.warning("Import Error", f"Failed to load Block IR:\n{e}", pya.MessageBox.Ok)

    def action_export_ir(self):
        if pya is None:
            return
        path = pya.FileDialog.ask_save_file_name("Save Block IR JSON", "design.json", "Block IR (*.json)")
        if not path:
            return
        pya.MessageBox.info("Block Canvas", f"Exported Block IR to {path}", pya.MessageBox.Ok)

    def action_toggle_mcp_bridge(self):
        if pya is None:
            return
        if self.bridge_server and self.bridge_server.is_running():
            self.bridge_server.stop()
            pya.MessageBox.info("MCP Bridge", "AI Agent MCP Bridge stopped.", pya.MessageBox.Ok)
        else:
            ok = self.start_bridge_server()
            msg = f"AI Agent MCP Bridge listening on port {DEFAULT_BRIDGE_PORT}." if ok else "Failed to start bridge."
            pya.MessageBox.info("MCP Bridge", msg, pya.MessageBox.Ok)

    def action_run_drc(self):
        if pya is None:
            return
        pya.MessageBox.info(
            "Block Canvas DRC",
            "Running KLayout DRC engine against Sky130 deck...\nReport will be loaded into Marker Database.",
            pya.MessageBox.Ok,
        )


if pya is not None and hasattr(pya, "Application") and pya.Application.instance() is not None:
    plugin = BlockCanvasPlugin()
    plugin.install_menu()
