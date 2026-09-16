# ADR 0007: Cell blocks consume compiled designs; netlists flatten for simulation

Status: accepted · 2026-09-15 · amended 2026-09-16

## Context

ADR 0006 exported the cell *abstract* (the upper-level view). This ADR
closes the loop: a compiled design must be instantiable by a parent design,
which is the difference between a flat compiler and a hierarchical one
(Analog Canvas ADR 0025 / ISCAS'25 hierarchical compilation).

## Decision

`blocks/cells.register_design_cell(alias, design)` registers a compiled
Design in the normal block registry:

- The cell's `PortSpec` contract is derived from the child's formal
  `ports` — pin names parents route against are the child's declared
  interface, never its internals.
- `build` recompiles the child on demand; the shared component cache
  deduplicates repeated instantiations.
- `netlist` is a *flattening* emitter (amended): it inlines the child's
  complete hierarchical netlist — every primitive `.subckt` plus the child
  top retargeted to the alias — so `simulate_design` runs real device-level
  simulation. Flattening is explicit here, not silent: the cell contract is
  that the registered design's netlist *is* its verified behavior model.
  `_rename_subckt` locates the emitter's self-declared top by name rather
  than blindly rewriting the first `.subckt`, which keeps multi-subckt
  flattened sources intact.

## Consequences

- `register_cell` MCP tool takes a live `session_id` or a `.lcproj` path —
  "compile once, instantiate everywhere" without GDS round-trips.
- Hierarchical simulation works end-to-end: verified with real ngspice +
  real sky130 BSIM4 models (`test_cell_flattened_real_simulation`).
- Nested cells flatten recursively; a known limitation is that two cells
  sharing an inner primitive emit duplicate identical `.subckt` defs —
  harmless (identical content, simulator keeps the last) but noted.
- The optimizer now runs *through* `transact` (`engine/optimize.py`), so
  tuning steps are revisioned and undoable like any other agent edit.
