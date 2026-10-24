# sky130-imported-cell-lvs

`inputs/src_dp_sky130.gds` is an external cell delivered as a bare GDS
stream (e.g. a Virtuoso stream-out); `inputs/src_dp_sky130.spice` is its
golden subckt netlist. Import the cell as a block, then compile
`starter/design.json` — a parent design instantiating it beside a
`sky130.current_mirror` load — and verify the parent.

## Stated criteria

- The imported block exposes the six pin-label ports of the fixture.
- The parent design compiles and extracts the full device census.
- `ppa.area_um2` stays inside the declared bound.

## Evaluation clauses (anti-cheat)

- Unstated checks apply to the *parent* layout: `drc.total_violations == 0`
  and `lvs.match == true` — the imported cell must carry its SPICE
  emitter or LVS honestly fails (a layout-only black box cannot pass).
- The fixture files are inputs, not deliverables: their sha256 is
  recorded in the scorecard provenance; a modified fixture invalidates
  the comparison.
