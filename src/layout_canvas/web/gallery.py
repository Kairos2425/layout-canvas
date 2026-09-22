"""Local-first design gallery — the community-upload foundation.

Entries live under a shared directory (``LAYOUT_CANVAS_GALLERY`` env var or
``~/.layout_canvas/gallery``). Each entry is a folder with ``design.json``,
``meta.json`` and ``preview.svg`` — plain files, so a shared drive, git repo
or a future remote sync layer can turn this into a real community store
without changing the API.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

from layout_canvas.compiler.compile import compile_design
from layout_canvas.compiler.render import render_svg
from layout_canvas.ir.model import Design


def gallery_root() -> Path:
    env = os.environ.get("LAYOUT_CANVAS_GALLERY")
    root = Path(env) if env else Path.home() / ".layout_canvas" / "gallery"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _entry_id(name: str) -> str:
    slug = re.sub(r"[^a-z0-9_-]+", "-", name.lower()).strip("-") or "design"
    return f"{slug}-{int(time.time() * 1000) % 10_000_000:07d}"


def publish(
    design: Design,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compile, render a preview, and store a shareable gallery entry."""
    comp = compile_design(design)
    svg = render_svg(comp)["svg"]
    eid = _entry_id(design.name)
    entry_dir = gallery_root() / eid
    entry_dir.mkdir(parents=True, exist_ok=False)
    info = {
        "id": eid,
        "name": design.name,
        "pdk": design.pdk,
        "author": (meta or {}).get("author", "local"),
        "description": (meta or {}).get("description", ""),
        "tags": (meta or {}).get("tags", []),
        "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "instances": len(design.instances),
        "blocks": sorted({i.block for i in design.instances}),
    }
    (entry_dir / "design.json").write_text(
        design.model_dump_json(indent=2), encoding="utf-8")
    (entry_dir / "meta.json").write_text(
        json.dumps(info, indent=2), encoding="utf-8")
    (entry_dir / "preview.svg").write_text(svg, encoding="utf-8")
    return info


def list_entries() -> list[dict[str, Any]]:
    out = []
    for d in sorted(gallery_root().iterdir()):
        meta = d / "meta.json"
        if meta.is_file():
            try:
                out.append(json.loads(meta.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                continue
    return out


def get_entry(entry_id: str) -> dict[str, Any] | None:
    entry_dir = gallery_root() / entry_id
    meta = entry_dir / "meta.json"
    design = entry_dir / "design.json"
    if not meta.is_file() or not design.is_file():
        return None
    svg = entry_dir / "preview.svg"
    return {
        "meta": json.loads(meta.read_text(encoding="utf-8")),
        "design": json.loads(design.read_text(encoding="utf-8")),
        "preview_svg": svg.read_text(encoding="utf-8") if svg.is_file() else "",
    }
