# ADR 0005: Simulation is part of the product and fails closed

Status: accepted · 2026-09-14

## Context

ADR 0002 fixed DRC/LVS to four fail-closed states. Simulation needs one more:
a *design-level* refusal. Borrowed directly from Analog Canvas ADR 0055 —
simulate only what is fully described at transistor level; anything else is
refused with the offending blocks named.

## Decision

`tools/sim.py` (`simulate_design`, `run_netlist`) adds a fifth status:

- `refused` — a design instance resolves to a block that is unregistered or
  has no transistor-level netlist emitter; `unresolved_blocks` names them.
  Checked *before* the simulator binary so design faults beat environment
  faults.
- `unavailable` — ngspice binary missing.
- `passed` / `failed` / `error` — as in ADR 0002 (`failed` includes ngspice
  error lines parsed from the run log, e.g. undefined device models).

The caller supplies stimulus (sources, top `X` instantiation, analyses) and
`includes` (model decks). The engine assembles the deck; it does not invent
testbenches or substitute illustrative device models for foundry data.

## Consequences

- MCP `run_simulation` accepts `session_id`/`ir_json` (compiled design) or a
  verbatim `deck`.
- `power`/`performance` in the PPA report stay `unavailable` until a real
  simulation flow feeds them — no fabricated numbers.
