# Engineering handoff worklist

This worklist tracks the current handoff from architecture notes to a real,
repeatable engineering path. It is intentionally outcome-based: a green unit
test alone does not count as end-to-end completion.

## Current turn (2026-09-14)

- [x] Commit pending PPA/optimizer/fail-closed work as clean baseline.
- [x] Transactional edit session (`engine/`) — snapshot/transact/undo +
      expected_revision optimistic lock + unified diagnostic envelope.
- [x] Connectivity projection (`derived/`) — pin→net, unconnected pins,
      degenerate-net diagnostics.
- [x] Project file protocol (`protocol/`) — versioned `.lcproj.json`,
      bare-IR v0 migration, structured load diagnostics.
- [x] PDK descriptor layer (`pdk/`) — compiler pin labels via descriptor.
- [x] ngspice runner (`tools/sim.py`) — fail-closed incl. `refused` with
      unresolved blocks named.
- [x] Cell abstract export (`compiler/hierarchy.py`) — bbox/pins/layer
      polygon counts for hierarchical reuse.
- [x] Local web review canvas (`web/`, `layout-canvas web`) — verified
      end-to-end over HTTP.
- [x] MCP tools: open_design/snapshot/transact/undo/inspect_connectivity/
      close_design/compile_session/export_abstract/run_simulation/
      save_project/load_project.
- [x] ADRs 0001–0006 freeze the new contracts; 69/69 tests pass.

## Remaining (next targets)

- [ ] Real ngspice smoke: needs ngspice binary + sky130 model deck on the
      host; verify `refused`/`unavailable` vs a real `.op` run.
- [ ] Real Sky130 DRC/LVS golden flow: same external-tool blocker.
- [x] Optimizer ↔ session integration: `engine/optimize.py` commits each
      iteration through `transact` — revisioned and undoable.
- [x] Hierarchy consumption: `blocks/cells.register_design_cell` +
      MCP `register_cell`; parents instantiate via `block: "<alias>"`.
      Cell sim stays fail-closed (blackbox for LVS, refused for ngspice).
- [x] KLayout Salt packaging: `pymacros/block_canvas.lym` autorun wrapper
      added next to `grain.xml`; real install still needs a KLayout host.
- [ ] Web canvas polish: layer-colored polygon rendering (currently port
      markers only), human click-to-edit feeding `transact`.
- [ ] Flattened hierarchical simulation (cells currently refuse by design).

## Acceptance bar

The project is only reported as complete for this turn when the code path is
tested from Block IR through generated layout/netlist and verification result
handling. If KLayout, Netgen, or a Sky130 deck is unavailable, the result must
be an explicit, machine-readable `blocked`/`unavailable` outcome—not a false
clean result.
