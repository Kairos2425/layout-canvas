# ADR 0001: Transactional edit session is the sole mutation boundary

Status: accepted · 2026-09-14

## Context

AI agents previously compiled Block IR in one shot (`compile_ir`): generate a
full document, hope it compiles. There was no way to iterate — read the
design, make a small change, re-check — which is the loop analog layout
actually requires (adjust margin → re-PPA → adjust again). Analog Canvas
solves this with a single edit-engine that both GUI and Agent must use
(its ADRs 0005/0007).

## Decision

`layout_canvas.engine.DesignSession` is the only path that mutates a Design
held in a session:

- `snapshot()` returns the full design plus the supported op vocabulary.
- `transact(edits, expected_revision, dry_run)` applies typed edits
  atomically on a deep copy; the result is re-validated through the pydantic
  model before commit. A failing transaction changes nothing.
- `expected_revision` is an optimistic lock; a stale agent is rejected with
  `revision-conflict` rather than silently overwriting unseen state.
- `undo()` pops the committed-history stack and bumps the revision.

Every response is an `Envelope {status, revision, diagnostics[], data}`;
diagnostics carry `code` + `object_id` so an agent can locate the offending
object without parsing prose (Analog Canvas ADR 0015).

The op vocabulary is deliberately small and model-level (set_placement,
set_params, add/remove instance/net/port/constraint, set_net_pins,
set_meta). Block-parameter range checks stay at compile time via
`resolve_params`; the session validates document structure, not PDK
semantics.

## Consequences

- MCP tools `open_design/snapshot/transact/undo/close_design` are thin
  adapters over the session; any future GUI must use the same class.
- `compile_ir` remains for one-shot compatibility but is no longer the
  recommended agent loop.
- Referential integrity is enforced, not guessed: removing a referenced
  instance is rejected with the referrers named.
