"""TCP Bridge protocol between Layout Canvas MCP server and live KLayout GUI instances.

Enables secure cross-process or remote agent execution inside KLayout
while maintaining compliance with corporate on-premise isolation policies.
"""

from __future__ import annotations

import json
import logging
import socket
import threading
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("layout_canvas.mcp.bridge")

DEFAULT_BRIDGE_HOST = "127.0.0.1"
DEFAULT_BRIDGE_PORT = 9099


class BridgeClient:
    """Client for connecting from MCP server to KLayout plugin TCP listener."""

    def __init__(self, host: str = DEFAULT_BRIDGE_HOST,
                 port: int = DEFAULT_BRIDGE_PORT, timeout: float = 10.0):
        self.host = host
        self.port = port
        self.timeout = timeout

    def send_request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Send a JSON-RPC request to the KLayout instance."""
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params or {},
        }
        raw = json.dumps(payload).encode("utf-8") + b"\n"

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        try:
            sock.connect((self.host, self.port))
            sock.sendall(raw)
            buffer = bytearray()
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                buffer.extend(chunk)
                if b"\n" in chunk:
                    break
            if not buffer:
                return {"error": {"code": -32603, "message": "Empty response from KLayout bridge"}}
            line = buffer.decode("utf-8").strip()
            return json.loads(line)
        except ConnectionRefusedError:
            return {"error": {"code": -32000, "message":
                              f"KLayout bridge not reachable at {self.host}:{self.port}. "
                              "Ensure KLayout is running with Block Canvas plugin enabled."}}
        except Exception as e:
            return {"error": {"code": -32603, "message": f"Bridge communication error: {e}"}}
        finally:
            try:
                sock.close()
            except Exception:
                pass


class KLayoutBridgeServer:
    """TCP Server that runs inside KLayout Python macro environment."""

    def __init__(self, host: str = DEFAULT_BRIDGE_HOST,
                 port: int = DEFAULT_BRIDGE_PORT,
                 handler: Callable[[str, dict[str, Any]], Any] | None = None):
        self.host = host
        self.port = port
        self.handler = handler
        self._server_sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._running = False

    def start(self) -> bool:
        if self._running:
            return True
        try:
            self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._server_sock.bind((self.host, self.port))
            self._server_sock.listen(5)
            self._server_sock.settimeout(1.0)
            self._running = True
            self._thread = threading.Thread(
                target=self._serve, daemon=True, name="KLayoutBridgeServer")
            self._thread.start()
            logger.info("KLayout Bridge Server listening on %s:%d", self.host, self.port)
            return True
        except Exception as e:
            logger.error("Failed to start KLayout Bridge Server: %s", e)
            self._running = False
            return False

    def stop(self) -> None:
        self._running = False
        if self._server_sock:
            try:
                self._server_sock.close()
            except Exception:
                pass
            self._server_sock = None
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
            self._thread = None

    def is_running(self) -> bool:
        return self._running

    def _serve(self) -> None:
        while self._running:
            try:
                if self._server_sock is None:
                    break
                conn, _ = self._server_sock.accept()
            except TimeoutError:
                continue
            except Exception:
                break

            try:
                conn.settimeout(5.0)
                buffer = bytearray()
                while self._running:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    buffer.extend(chunk)
                    if b"\n" in chunk:
                        break

                if not buffer:
                    conn.close()
                    continue

                line = buffer.decode("utf-8").strip()
                response = self._process_request(line)
                conn.sendall(json.dumps(response).encode("utf-8") + b"\n")
            except Exception as e:
                err_resp = {"jsonrpc": "2.0", "id": None,
                            "error": {"code": -32603, "message": str(e)}}
                try:
                    conn.sendall(json.dumps(err_resp).encode("utf-8") + b"\n")
                except Exception:
                    pass
            finally:
                try:
                    conn.close()
                except Exception:
                    pass

    def _process_request(self, raw_str: str) -> dict[str, Any]:
        try:
            req = json.loads(raw_str)
            req_id = req.get("id")
            method = req.get("method", "")
            params = req.get("params", {})

            if not self.handler:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32601, "message": "No request handler configured in KLayout"}
                }

            result = self.handler(method, params)
            if isinstance(result, dict) and "error" in result:
                return {"jsonrpc": "2.0", "id": req_id, "error": result["error"]}
            return {"jsonrpc": "2.0", "id": req_id, "result": result}
        except Exception as e:
            return {"jsonrpc": "2.0", "id": None,
                    "error": {"code": -32700, "message": f"Parse error: {e}"}}
