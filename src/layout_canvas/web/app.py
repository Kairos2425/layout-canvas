"""Local-first layout review canvas — zero-dependency stdlib web app.

Serves a single page where a human (or an agent driving HTTP) can paste a
Block IR document and review the compiled layout preview, PPA metrics,
connectivity projection, and generated netlist. Local-first: no accounts,
no cloud, loopback only by default.
"""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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
from layout_canvas.web.page import PAGE

# Single-user local canvas: one live session shared by the page. Session-mode
# edits go through DesignSession.transact — the same boundary the MCP agent
# uses — so revision locking and diagnostics apply to human clicks too.
_SESSION: DesignSession | None = None

_SAMPLE: dict[str, Any] = {
    "name": "sample",
    "pdk": "sky130",
    "instances": [
        {"id": "dp", "block": "sky130.diff_pair", "params": {}},
        {
            "id": "cm",
            "block": "sky130.current_mirror",
            "params": {},
            "placement": {"relative_to": "dp", "relation": "right_of", "margin": 2.0},
        },
    ],
    "nets": [{"name": "tail", "pins": ["dp.tail", "cm.in"]}],
    "ports": [{"name": "TAIL", "pin": "dp.tail", "direction": "input"}],
}


def _instance_bboxes(design: Design, comp: Any) -> dict[str, list[float]]:
    """Map design instance ids to placed bboxes in the compiled cell.

    compile_design inserts refs in design.instances order, so positional
    pairing is stable.
    """
    boxes: dict[str, list[float]] = {}
    for inst, ref in zip(design.instances, comp.insts):
        bb = ref.bbox()
        boxes[inst.id] = [float(bb.left), float(bb.bottom), float(bb.right), float(bb.top)]
    return boxes


def _session_api(action: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Session-mode actions — human edits through the transactional engine."""
    global _SESSION
    if action == "open":
        raw = payload.get("ir_json")
        try:
            design = Design.model_validate_json(raw) if isinstance(raw, str) else Design.model_validate(raw)
        except Exception as exc:
            return {"status": "error", "error": f"invalid IR: {exc}"}
        _SESSION = DesignSession(design)
        return {"status": "ok", "revision": 0, "data": _SESSION.snapshot().data}
    if _SESSION is None:
        return {"status": "error", "error": "no session open"}
    if action == "state":
        return {"status": "ok", "revision": _SESSION.revision, "data": _SESSION.snapshot().data}
    if action == "edit":
        env = _SESSION.transact(
            payload.get("edits", []),
            expected_revision=payload.get("expected_revision"),
        )
        return {
            "status": env.status,
            "revision": env.revision,
            "diagnostics": [d.to_dict() for d in env.diagnostics],
            "data": {"design": _SESSION.design.model_dump()},
        }
    if action == "undo":
        env = _SESSION.undo()
        return {
            "status": env.status,
            "revision": env.revision,
            "diagnostics": [d.to_dict() for d in env.diagnostics],
            "data": {"design": _SESSION.design.model_dump()},
        }
    if action == "close":
        _SESSION = None
        return {"status": "ok"}
    return {"status": "error", "error": f"unknown session action {action!r}"}


def _gallery_api(action: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Community gallery — publish / browse / open shared designs."""
    global _SESSION
    from layout_canvas.web import gallery
    if action == "list":
        return {"status": "ok", "data": {"entries": gallery.list_entries()}}
    if action == "publish":
        if _SESSION is not None:
            design = _SESSION.design
        else:
            raw = payload.get("ir_json")
            try:
                design = (Design.model_validate_json(raw) if isinstance(raw, str)
                          else Design.model_validate(raw))
            except Exception as exc:
                return {"status": "error", "error": f"invalid IR: {exc}"}
        try:
            info = gallery.publish(design, payload.get("meta"))
        except Exception as exc:
            return {"status": "error", "error": str(exc)}
        return {"status": "ok", "data": info}
    if action == "sync":
        return {"status": "ok", "data": gallery.sync(payload.get("remote"))}
    if action in ("get", "fork"):
        entry = gallery.get_entry(str(payload.get("id", "")))
        if entry is None:
            return {"status": "error", "error": "gallery entry not found"}
        if action == "fork":
            _SESSION = DesignSession(Design.model_validate(entry["design"]))
            return {"status": "ok", "revision": 0,
                    "data": {"design": _SESSION.design.model_dump(),
                             "meta": entry["meta"]}}
        return {"status": "ok", "data": entry}
    return {"status": "error", "error": f"unknown gallery action {action!r}"}


def _api(action: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Handle one API call; errors come back structured, never as a 500."""
    if action.startswith("session/"):
        return _session_api(action.split("/", 1)[1], payload)
    if action.startswith("gallery/"):
        return _gallery_api(action.split("/", 1)[1], payload)
    raw = payload.get("ir_json")
    try:
        design = Design.model_validate_json(raw) if isinstance(raw, str) else Design.model_validate(raw)
    except Exception as exc:
        return {"status": "error", "error": f"invalid IR: {exc}"}
    try:
        if action == "connectivity":
            return {"status": "ok", "data": inspect_connectivity(design)}
        if action == "netlist":
            return {"status": "ok", "data": {"spice": compile_netlist(design)}}
        comp = compile_design(design)
        if action == "preview":
            data = render_svg(comp)
            data["instances"] = _instance_bboxes(design, comp)
            return {"status": "ok", "data": data}
        if action == "ppa":
            return {"status": "ok", "data": extract_ppa(comp, design)}
        if action == "abstract":
            from layout_canvas.compiler.hierarchy import cell_abstract

            return {"status": "ok", "data": cell_abstract(design, comp)}
        if action == "virtuoso":
            import tempfile
            from layout_canvas.compiler.virtuoso import export_skill, export_spectre

            gds = Path(tempfile.mkdtemp()) / f"{design.name}.gds"
            comp.write_gds(str(gds))
            return {"status": "ok", "data": {
                "skill": export_skill(gds, payload.get("library", "canvas_lib"),
                                      design.pdk),
                "spectre": export_spectre(compile_netlist(design)),
            }}
        return {"status": "error", "error": f"unknown action {action!r}"}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args: Any) -> None:  # quiet per-request logging
        pass

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
        elif self.path == "/api/health":
            self._send(200, json.dumps({"status": "ok"}).encode(), "application/json")
        elif self.path == "/api/version":
            self._send(200, json.dumps(_version()).encode(), "application/json")
        elif self.path == "/manifest.json":
            self._send(200, json.dumps({
                "name": "Layout Canvas",
                "short_name": "LayoutCanvas",
                "start_url": "/",
                "display": "standalone",
                "background_color": "#1e1e28",
                "theme_color": "#2d6cdf",
                "description": "Block-level, AI-native analog layout canvas.",
                "icons": [],
            }).encode(), "application/manifest+json")
        elif self.path == "/sw.js":
            self._send(200, _SW.encode("utf-8"), "application/javascript; charset=utf-8")
        elif self.path == "/api/sample":
            self._send(200, json.dumps(_SAMPLE).encode(), "application/json")
        elif self.path == "/api/blocks":
            blocks = [b.spec.model_dump() for b in base.all_blocks().values()]
            self._send(200, json.dumps(blocks).encode(), "application/json")
        else:
            self._send(404, b'{"error": "not found"}', "application/json")

    def do_POST(self) -> None:
        if not self.path.startswith("/api/"):
            self._send(404, b'{"error": "not found"}', "application/json")
            return
        length = int(self.headers.get("Content-Length", "0") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError as exc:
            self._send(400, json.dumps({"error": str(exc)}).encode(), "application/json")
            return
        result = _api(self.path.removeprefix("/api/"), payload)
        self._send(200, json.dumps(result).encode(), "application/json")


def _version() -> dict[str, Any]:
    """Deployment verification surface — the served bytes must answer with
    the commit they were built from (SOURCE_COMMIT file or git checkout)."""
    import subprocess
    root = Path(__file__).resolve().parents[2]
    src_commit = root / "SOURCE_COMMIT"
    if src_commit.is_file():
        return {"version": src_commit.read_text(encoding="utf-8").strip()}
    try:
        sha = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, check=True).stdout.strip()
        return {"version": sha}
    except Exception:
        return {"version": "dev"}


_SW = """\
const CACHE = "layout-canvas-v1";
self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(["/"])));
  self.skipWaiting();
});
self.addEventListener("activate", (e) => {
  e.waitUntil(clients.claim());
});
self.addEventListener("fetch", (e) => {
  if (e.request.method !== "GET" || e.request.url.includes("/api/")) return;
  e.respondWith(fetch(e.request).catch(() => caches.match(e.request)));
});
"""


def run(host: str = "127.0.0.1", port: int = 8080) -> None:
    import layout_canvas.blocks.sky130  # noqa: F401  populate block registry
    try:
        import layout_canvas.blocks.ihp_sg13g2  # noqa: F401
    except Exception:
        pass

    remote = os.environ.get("LAYOUT_CANVAS_GALLERY_REMOTE")
    if remote:
        from layout_canvas.web import gallery
        print(f"gallery sync target: {remote} -> {gallery.sync(remote)['status']}")

    server = ThreadingHTTPServer((host, port), _Handler)
    print(f"layout-canvas web canvas: http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
