# ADR 0006: PDK descriptor + cell abstract are the scaling contracts

Status: accepted · 2026-09-14

## Context

Two scaling pressures: (a) Sky130 layer/rule constants are scattered through
block generators and were hardcoded in the compiler's LVS label injection;
(b) hierarchical reuse needs an upper-level view of a compiled cell without
re-reading full geometry (Analog Canvas ADR 0025, formal ports/hierarchy).

## Decision

- `pdk.PDK` is a frozen descriptor: name, layer map, `pin_purpose`, and a
  `rules` dict (margins, default routing layer, DRC deck name). A registry
  (`get_pdk`/`register_pdk`) makes additional PDKs a data problem, not a
  code fork. The compiler's pin-label layer now resolves through
  `pdk.pin_label_layer` instead of a hardcoded `(layer, 16)`.
- `compiler.hierarchy.cell_abstract(design)` returns the upper-level view:
  bbox, pin positions/layers, per-layer polygon counts, instance count.
  This is the contract a future hierarchical placer programs against.

## Consequences

- Adding a commercial PDK = register a descriptor + generator package; the
  compiler, verifiers and web canvas stay untouched.
- The abstract is derived data — never persisted as electrical truth; the IR
  remains authoritative.
- Repeated compiles of one design retire the previous top KCell
  (`_fresh_top`), since kfactory rejects duplicate cell names — required
  for the preview→PPA→abstract loop in one process.
