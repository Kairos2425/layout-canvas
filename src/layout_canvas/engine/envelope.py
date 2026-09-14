"""Diagnostic envelope shared by every engine/agent mutation surface.

Modeled on Analog Canvas ADR 0015: an agent never receives a bare exception
or a bare value; every response carries a status, the design revision the
response was computed against, and structured diagnostics that locate the
offending object.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Severity = Literal["error", "warning", "info"]
Status = Literal["ok", "rejected", "error"]


@dataclass
class Diagnostic:
    severity: Severity
    code: str
    message: str
    object_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
        }
        if self.object_id is not None:
            out["object_id"] = self.object_id
        return out


@dataclass
class Envelope:
    status: Status
    revision: int
    diagnostics: list[Diagnostic] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "revision": self.revision,
            "diagnostics": [d.to_dict() for d in self.diagnostics],
            "data": self.data,
        }


def rejected(revision: int, diagnostics: list[Diagnostic], **data: Any) -> Envelope:
    return Envelope("rejected", revision, diagnostics, data)


def failed(revision: int, message: str, code: str = "internal-error") -> Envelope:
    return Envelope("error", revision, [Diagnostic("error", code, message)])
