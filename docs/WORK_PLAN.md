# Engineering handoff worklist

This worklist tracks the current handoff from architecture notes to a real,
repeatable engineering path. It is intentionally outcome-based: a green unit
test alone does not count as end-to-end completion.

## Current turn

- [x] Inspect repository state, recent commits, and architecture memo.
- [x] Run the test suite inside the project virtual environment.
- [ ] Make PPA extraction and optimizer behavior truthful and deterministic.
- [ ] Make DRC/LVS runners fail closed and expose actionable tool/config errors.
- [ ] Add a real Sky130 golden-flow smoke test or document the external-tool blocker.
- [ ] Verify MCP tools against the same compiler and verification contracts.
- [ ] Refresh architecture/memory status with actual evidence and remaining gaps.
- [ ] Run full tests, lint/type checks available in the environment, and inspect generated artifacts.

## Acceptance bar

The project is only reported as complete for this turn when the code path is
tested from Block IR through generated layout/netlist and verification result
handling. If KLayout, Netgen, or a Sky130 deck is unavailable, the result must
be an explicit, machine-readable `blocked`/`unavailable` outcome—not a false
clean result.

