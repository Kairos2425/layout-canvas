# ADR 0007: Cell blocks consume compiled designs; simulation stays fail-closed

Status: accepted · 2026-09-15

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
- `netlist=None` by design: `compile_netlist` emits a blackbox `.subckt`
  shell (correct for hierarchical LVS), while `simulate_design` **refuses**
  and names the cell — a macro has no transistor-level emitter of its own
  and silently flattening it would simulate unverified geometry.

## Consequences

- `register_cell` MCP tool takes a live `session_id` or a `.lcproj` path —
  "compile once, instantiate everywhere" without GDS round-trips.
- True flattened simulation of hierarchies is future work; until then the
  refusal is the contract, not a gap.
- The optimizer now runs *through* `transact` (`engine/optimize.py`), so
  tuning steps are revisioned and undoable like any other agent edit.
