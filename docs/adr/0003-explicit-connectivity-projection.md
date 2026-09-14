# ADR 0003: Connectivity is explicit data, exposed as a read-only projection

Status: accepted · 2026-09-14

## Context

Analog Canvas's core invariant: net membership is an electrical fact stored
in the model; drawing geometry never silently creates a connection, and
ambiguous cases are rejected rather than guessed. Its agent workflow's step 2
is "read electrical facts before drawing". Our IR already stores explicit
`Net.pins` (good), but agents had no way to *query* connectivity — they had
to cross-reference nets, ports, and block specs by hand.

## Decision

`layout_canvas.derived.inspect_connectivity(design)` is a pure function
returning:

- per-instance pin → net resolution, with `is_port` flags;
- `unconnected` pin lists derived from each block's declared `PortSpec`s
  (unknown blocks degrade to "referenced pins only" plus an
  `unknown-block` diagnostic);
- degenerate-net (`<2` pins) and pin-multi-driven diagnostics.

The projection is read-only and deterministic; it lives outside the model
(derived, not stored) so it can never drift from the document.

## Consequences

- MCP `inspect_connectivity` accepts a live `session_id` or an ad-hoc
  `ir_json`; agents are expected to call it before composing `transact`
  edits that touch nets.
- Geometry produced by the compiler/router may *render* connections but the
  IR `Net` list remains the only electrical authority.
