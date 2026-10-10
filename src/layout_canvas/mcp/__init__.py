"""Model Context Protocol (MCP) server for Layout Canvas.

Provides standard JSON-RPC 2.0 tools for querying parametric blocks,
compiling Block IR designs, running DRC, and interacting with KLayout instances.
Designed to be compatible with Claude Code, Codex, DeepSeek, Kimi, and internal
corporate deployments.
"""

from __future__ import annotations

from layout_canvas.mcp.server import LayoutCanvasMCPServer, run_stdio_server

__all__ = ["LayoutCanvasMCPServer", "run_stdio_server"]
