# ihp-mirror-verify

Compile `starter/design.json` — a single IHP SG13G2 `current_mirror`
(fingers=2, nmos) with top-level `IN`/`OUT`/`GATE`/`VSS` ports — and
verify the resulting layout under the `ihp_sg13g2` technology.

## Stated criteria

- The IR compiles to GDS without errors.
- Extraction reports the device census declared in `task.toml`.
- `ppa.area_um2` stays inside the declared bound.

## Evaluation clauses (anti-cheat)

- The evaluator adds **unstated checks**: `drc.total_violations == 0`
  under the SG13G2 rule subset and `lvs.match == true` against the
  IR-derived reference netlist — both on the in-process engine.
- Cross-PDK discipline: layers/datatypes differ from sky130 (labels ride
  datatype 2). A design that only verifies under sky130 assumptions is
  not a solution.
- `unavailable` is never a pass; scores only count against the frozen
  `release_id`.
