"""Bounded .lcproj.json load/migrate/save compatibility boundary.

Modeled on Analog Canvas's ``@icm/project-protocol``: the project file is the
canonical artifact; loading goes through one boundary that reports schema
version, applies migrations, and returns diagnostics instead of raising bare
exceptions at agents.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from layout_canvas.engine.envelope import Diagnostic
from layout_canvas.ir.model import Design

PROJECT_SCHEMA_VERSION = 1
PROJECT_KIND = "lcproj"


@dataclass
class LoadResult:
    status: str  # ok | migrated | error
    design: Design | None
    source_version: int | None
    diagnostics: list[Diagnostic] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.design is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "source_version": self.source_version,
            "diagnostics": [d.to_dict() for d in self.diagnostics],
            "design": self.design.model_dump() if self.design else None,
        }


def serialize_project(design: Design) -> str:
    payload = {
        "kind": PROJECT_KIND,
        "schema_version": PROJECT_SCHEMA_VERSION,
        "design": design.model_dump(),
    }
    return json.dumps(payload, indent=2)


def save_project(design: Design, path: str | Path) -> Path:
    p = Path(path).resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(serialize_project(design), encoding="utf-8")
    return p


def load_project(source: str | Path) -> LoadResult:
    """Load a project from a path or raw JSON text.

    Accepts both the versioned ``.lcproj`` envelope and a bare Block IR
    document (treated as schema v0 and migrated in place).
    """
    try:
        text = _read(source)
    except OSError as exc:
        return LoadResult("error", None, None, [Diagnostic("error", "io-error", str(exc))])
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        return LoadResult(
            "error", None, None, [Diagnostic("error", "invalid-json", str(exc))]
        )
    if not isinstance(payload, dict):
        return LoadResult(
            "error", None, None, [Diagnostic("error", "invalid-json", "project is not an object")]
        )

    if payload.get("kind") == PROJECT_KIND or "schema_version" in payload:
        return _load_versioned(payload)
    return _load_bare_ir(payload)


def _load_versioned(payload: dict[str, Any]) -> LoadResult:
    version = payload.get("schema_version")
    if not isinstance(version, int):
        return LoadResult(
            "error",
            None,
            None,
            [Diagnostic("error", "bad-version", f"schema_version must be an int, got {version!r}")],
        )
    if version > PROJECT_SCHEMA_VERSION:
        return LoadResult(
            "error",
            None,
            version,
            [
                Diagnostic(
                    "error",
                    "unsupported-version",
                    f"project schema v{version} is newer than supported "
                    f"v{PROJECT_SCHEMA_VERSION}; upgrade layout-canvas",
                )
            ],
        )
    body = payload.get("design")
    if not isinstance(body, dict):
        return LoadResult(
            "error", None, version, [Diagnostic("error", "missing-design", "no 'design' object")]
        )
    # v1 is the current shape; no migrations needed yet. New versions add
    # steps here — never rewrite the document silently.
    return _validate(body, version, migrated=False)


def _load_bare_ir(payload: dict[str, Any]) -> LoadResult:
    return _validate(
        payload,
        source_version=0,
        migrated=True,
        extra=[
            Diagnostic(
                "info",
                "migrated",
                "bare Block IR document loaded as schema v0; "
                "save through save_project to persist the versioned envelope",
            )
        ],
    )


def _validate(
    body: dict[str, Any],
    source_version: int,
    migrated: bool,
    extra: list[Diagnostic] | None = None,
) -> LoadResult:
    try:
        design = Design.model_validate(body)
    except Exception as exc:
        return LoadResult(
            "error",
            None,
            source_version,
            [Diagnostic("error", "invalid-design", str(exc))],
        )
    diags = list(extra or [])
    return LoadResult("migrated" if migrated else "ok", design, source_version, diags)


def _read(source: str | Path) -> str:
    text = str(source)
    if isinstance(source, Path) or not text.lstrip().startswith("{"):
        return Path(source).read_text(encoding="utf-8")
    return text
