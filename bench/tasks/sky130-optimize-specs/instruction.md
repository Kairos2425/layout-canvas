# sky130-optimize-specs

`starter/design.json` is the ota_lab OTA with a deliberately narrowed
spec: `tb_op_schematic.outp_op` requires `v(outp)` inside **[0.55, 0.575] V**
while the shipped parameters measure ≈0.61 V — the design must be
**retuned**, not merely re-verified.

## Stated criteria

- Run bounded spec-driven optimization (`objective="specs"`, coordinate
  descent over the bounded numeric block params) within
  `run.max_iterations = 6` rounds.
- The score is the triple `specs_passed` + `iterations` + `final_area_um2`;
  `optimize.all_passed` must be true inside the budget.
- Every trial commits through the transactional edit boundary (revisions
  are recorded; losing candidates are reverted, never patched around).

## Evaluation clauses (anti-cheat)

- The tuned layout faces the same unstated `drc.total_violations == 0` /
  `lvs.match == true` checks as every compiled task — a param set that
  passes spec but breaks the layout is a fail.
- A missing simulator reports `unavailable` and zero score; hand-editing
  the spec band in `starter/design.json` is a baseline regression, not a
  solution.
