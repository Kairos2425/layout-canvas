"""Design editing session — the sole mutation boundary for Block IR.

Modeled on Analog Canvas's edit-engine / ADR 0007 (snapshot-driven agent
workflow): an agent reads a complete snapshot, submits typed edits with an
expected revision, and gets an atomic commit or a rejection with located
diagnostics. There is no second mutation path — MCP tools, a future GUI,
and batch scripts all go through ``DesignSession.transact``.
"""

from __future__ import annotations

import time
from typing import Any

from layout_canvas.derived.connectivity import inspect_connectivity
from layout_canvas.ir.model import Design

from .edits import OPS, apply_edits
from .envelope import Diagnostic, Envelope, failed, rejected


class DesignSession:
    """Holds one mutable Design with revisioned, transactional edits."""

    def __init__(self, design: Design):
        self._design = design
        self._revision = 0
        self._history: list[Design] = []
        # Verification/simulation run log — small summaries only (statuses
        # and counts), never waveforms or decks. FIFO-capped.
        self.runs: list[dict[str, Any]] = []

    def record_run(self, kind: str, summary: dict[str, Any]) -> dict[str, Any]:
        """Append a run record stamped with UTC time and the live revision."""
        record = {
            "kind": kind,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "revision": self._revision,
            **summary,
        }
        self.runs.append(record)
        if len(self.runs) > 50:
            del self.runs[: len(self.runs) - 50]
        return record

    @property
    def design(self) -> Design:
        return self._design

    @property
    def revision(self) -> int:
        return self._revision

    def snapshot(self) -> Envelope:
        """Complete read of design state — the agent's ground truth."""
        return Envelope(
            "ok",
            self._revision,
            data={
                "design": self._design.model_dump(),
                "ir_version": self._design.ir_version,
                "supported_ops": list(OPS),
                "runs": list(self.runs),
            },
        )

    def transact(
        self,
        edits: list[dict[str, Any]],
        expected_revision: int | None = None,
        dry_run: bool = False,
    ) -> Envelope:
        """Validate and atomically commit typed edits.

        ``expected_revision`` is an optimistic lock: a mismatch rejects the
        whole transaction so a stale agent never overwrites unseen changes.
        """
        if not isinstance(edits, list):
            return failed(self._revision, "'edits' must be a list", "bad-request")
        if expected_revision is not None and expected_revision != self._revision:
            return rejected(
                self._revision,
                [
                    Diagnostic(
                        "error",
                        "revision-conflict",
                        f"expected revision {expected_revision}, current is "
                        f"{self._revision}; re-snapshot before editing",
                    )
                ],
            )
        candidate, diagnostics = apply_edits(self._design, edits)
        if candidate is None:
            return rejected(self._revision, diagnostics)
        if dry_run:
            return Envelope(
                "ok",
                self._revision,
                data={"dry_run": True, "design": candidate.model_dump()},
            )
        self._history.append(self._design)
        self._design = candidate
        self._revision += 1
        return Envelope("ok", self._revision, data={"committed": len(edits)})

    def undo(self) -> Envelope:
        if not self._history:
            return rejected(
                self._revision,
                [Diagnostic("error", "nothing-to-undo", "history is empty")],
            )
        self._design = self._history.pop()
        self._revision += 1
        return Envelope("ok", self._revision)

    def connectivity(self) -> Envelope:
        return Envelope("ok", self._revision, data=inspect_connectivity(self._design))
