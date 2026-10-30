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

from .edits import OPS, apply_edits, classify_impact
from .envelope import Diagnostic, Envelope, failed, rejected


def _run_result_status(record: dict[str, Any]) -> Any:
    """The one status word a compact run summary keeps."""
    if "status" in record:
        return record["status"]
    if "passed" in record:
        return "passed" if record["passed"] else "failed"
    for key in ("spec_status", "lvs_status", "quality_status", "statuses",
                "coverage", "sim_status"):
        if key in record:
            return record[key]
    return "recorded"


def _compact_runs(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """Collapse the runs journal by kind: ``{kind: {count, last: {op, at,
    result_status}}}`` — the compact stand-in for the full payload list."""
    out: dict[str, Any] = {}
    for record in runs:
        kind = str(record.get("kind", "run"))
        entry = out.setdefault(kind, {"count": 0, "last": None})
        entry["count"] += 1
        entry["last"] = {
            "op": kind,
            "at": record.get("ts"),
            "revision": record.get("revision"),
            "result_status": _run_result_status(record),
        }
    return out


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

    def snapshot(self, compact: bool = False) -> Envelope:
        """Complete read of design state — the agent's ground truth.

        ``compact=True`` keeps the full design but collapses the runs
        journal into ``{kind: {count, last: {op, at, result_status}}}`` —
        agents that only need the current state opt into the smaller
        payload; the default stays the full journal.
        """
        data = {
            "design": self._design.model_dump(),
            "ir_version": self._design.ir_version,
            "supported_ops": list(OPS),
            "runs": (_compact_runs(self.runs) if compact
                     else list(self.runs)),
        }
        if compact:
            data["compact"] = True
        return Envelope("ok", self._revision, data=data)

    def transact(
        self,
        edits: list[dict[str, Any]],
        expected_revision: int | None = None,
        dry_run: bool = False,
        confirm: bool = False,
    ) -> Envelope:
        """Validate and atomically commit typed edits.

        ``expected_revision`` is an optimistic lock: a mismatch rejects the
        whole transaction so a stale agent never overwrites unseen changes.

        ``confirm`` is the decision-point gate. An edit list classified
        high-impact (``edits.classify_impact``: removes instances/nets or
        retunes params on more than half the design) is *not* applied when
        ``confirm`` is false — the call returns
        ``status="requires_confirmation"`` with an ``impact`` report and
        the revision untouched; resubmitting the same edits with
        ``confirm=True`` commits them. ``dry_run`` needs no confirm — it
        never commits.
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
        impact = classify_impact(self._design, edits)
        if impact["high"] and not confirm:
            return Envelope(
                "requires_confirmation",
                self._revision,
                diagnostics=[
                    Diagnostic(
                        "warning",
                        "high-impact-edits",
                        impact["summary"]
                        + " — nothing applied; resubmit with confirm=true",
                    )
                ],
                data={"impact": impact, "edits_count": len(edits)},
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
