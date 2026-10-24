# sky130-ota-lab-specs

`starter/design.json` is the `examples/ota_lab.json` OTA: a diff pair
with a PMOS mirror load and an NMOS tail pull-down, carrying three
declared testbenches — `tb_op_schematic` (schematic op),
`tb_op_pex` (post-layout op, probes the internal `tail` node), and
`tb_ac` (schematic ac, gain/bandwidth specs).

## Stated criteria

- All seven spec entries across the three testbenches evaluate `pass`
  (each spec is an individual check; partial credit is a task fail).
- `ppa.area_um2` stays inside the declared bound.
- The scorecard annotates the **worst-case spec** — the entry nearest
  its bound — as `specs.worst_case` / `specs.worst_margin_frac`.

## Evaluation clauses (anti-cheat)

- Unstated checks apply: the compiled layout must additionally produce
  `drc.total_violations == 0` and `lvs.match == true`.
- The spec verdicts come from a real simulator run — a missing ngspice
  reports `unavailable` (counts in coverage, never as a pass); editing
  `starter/design.json`'s spec bounds looser is a baseline regression,
  not a solution.
- Spec values are compared against the frozen reference within the
  declared `tolerances`; deterministic metrics compare exactly.
