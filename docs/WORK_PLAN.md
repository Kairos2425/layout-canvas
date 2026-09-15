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

## Verified real-tool status (2026-09-15, this host)

- [x] Real ngspice `.op` on all three decks: illustrative level-1,
      bundled **real SkyWater BSIM4 TT** (`examples/models/sky130/
      sky130_tt.lib`, upstream files verbatim), and **real IHP SG13G2
      PSP 103.6** via `pre_osdi` (see EXTERNAL_TOOLS.md recipe).
- [x] `blocks/ihp_sg13g2/` — second PDK generator library (diff_pair,
      current_mirror, ota_5t, guard_ring) on the verified SG13G2 layer map.
- [x] MCP full-loop e2e over real stdio JSON-RPC
      (`tests/test_mcp_e2e.py`): open → connectivity → transact →
      PPA → compile → netlist → real sim → abstract → project
      round-trip → register_cell → close.
- [x] Web review canvas: all `/api/*` endpoints exercised over HTTP
      (preview/ppa/connectivity/netlist/abstract/sample/blocks).
- [x] Web canvas polish: `render_svg` now draws real per-layer polygons
      (y-flipped, deterministic layer palette, polygon budget), and the
      page has session mode — click selects an instance (hit-test on
      per-instance bboxes), second click commits `set_placement` through
      `session/edit` (revision-locked, undoable). Verified over HTTP.
- [ ] KLayout DRC / Netgen LVS: binaries absent on this host; download
      attempt throttled by network. Runners are fail-closed and light up
      automatically via PATH or `LAYOUT_CANVAS_KLAYOUT`/`_NETGEN`.
- [ ] Remaining Sky130 corner/device coverage beyond nfet_01v8+pfet_01v8
      (lvt, 3v3, 5V, caps/resistors — fetch on demand).

## Remaining (next targets)

- [ ] Real Sky130 DRC/LVS golden flow: same external-tool blocker.
- [x] Optimizer ↔ session integration: `engine/optimize.py` commits each
      iteration through `transact` — revisioned and undoable.
- [x] Hierarchy consumption: `blocks/cells.register_design_cell` +
      MCP `register_cell`; parents instantiate via `block: "<alias>"`.
      Cell sim stays fail-closed (blackbox for LVS, refused for ngspice).
- [x] KLayout Salt packaging: `pymacros/block_canvas.lym` autorun wrapper
      added next to `grain.xml`; real install still needs a KLayout host.
- [ ] Flattened hierarchical simulation (cells currently refuse by design).

## Acceptance bar

The project is only reported as complete for this turn when the code path is
tested from Block IR through generated layout/netlist and verification result
handling. If KLayout, Netgen, or a Sky130 deck is unavailable, the result must
be an explicit, machine-readable `blocked`/`unavailable` outcome—not a false
clean result.
