# demo65-descriptor-pdk

`inputs/demo65.pdk.json` is a complete **external PDK descriptor** — a
synthetic "demo65" commercial-style process shipping with the task. The
descriptor alone (no code) must supply the layer map, extraction recipe,
DRC subset and leaf-device names; the generic generators then provide
`demo65.gen_diff_pair` and `demo65.gen_guard_ring`.

`starter/design.json` composes a gen diff pair inside a gen guard ring
on `pdk: "demo65"`.

## Stated criteria

- The descriptor loads and the `gen_*` blocks register.
- The design compiles and extraction yields the declared device census —
  devices named `demo65_nch`, proving the descriptor (not the sky130
  built-ins) drove extraction.
- `ppa.area_um2` stays inside the declared bound.

## Evaluation clauses (anti-cheat)

- Unstated checks apply: `drc.total_violations == 0` under the
  *descriptor's* drc.rules and `lvs.match == true` against the
  descriptor's leaf-device wrappers.
- The descriptor file's sha256 is recorded in provenance — swapping it
  for a different layer map invalidates the baseline.
- A descriptor-only PDK with no `extract` section reports `unavailable`,
  never a vacuous pass.
