# sky130-ota-candidates

`starter/design.json` is the `examples/ota_lab.json` OTA — the same
proven design the other OTA tasks compile (diff pair + PMOS mirror load
+ NMOS tail pull-down), carrying `tb_op_schematic` (schematic op),
`tb_op_pex` (post-layout op, probes the internal `tail` node) and
`tb_ac` (schematic ac).

`starter/variants.json` declares four candidates in the exact
`{name, edits}` list format the `candidates_eval` /
`eval_candidates` tool takes:

- `w080` / `w100` / `w120` — `set_params` sweeps of the `TAIL` mirror
  width (2.0 µm → 1.6 / 2.0 / 2.4 µm).
- `ghost` — an edit targeting a non-existent instance, designed to be
  rejected by the transact validation boundary.

## Stated criteria

- Run the multi-candidate evaluation on design + variants: each
  variant's edits are applied to a fresh copy through the same transact
  validation the session uses, then compiled, PPA-measured, physically
  verified (extract/DRC/LVS) and simulated on the `source="schematic"`
  testbenches (`tb_op_pex` is post-layout — skipped during sweeps, run
  it on the chosen candidate).
- `w080`, `w100`, `w120` report `status: "passed"`; `ghost` reports
  `status: "failed"` — a rejected edit list fails that one candidate
  and never poisons the sweep (`report.status` stays `ok`).
- Per-candidate spec pass counts come from real simulator runs; the
  width sweep changes the circuit, so the counts differ per candidate.
- `ranking` is the mechanical sort — verify passed → specs passed →
  smaller area. It is an ordering of measured numbers, not a quality
  judgment.

## Evaluation clauses (anti-cheat)

- Unstated checks apply: every candidate that reaches verification must
  produce `drc.total_violations == 0` and `lvs.match == true`.
- The designed-fail variant must report `failed` with the real transact
  diagnostics — reporting it as passed is a fail, and a candidate that
  cannot be evaluated at all reports `unavailable`, never a fabricated
  verdict.
- The spec verdicts come from a real simulator — a missing ngspice
  reports `unavailable` (counted in coverage, never as a pass).
- Deterministic metrics (per-candidate areas, status tallies, ranking
  order) compare exactly against the frozen reference; sim-derived
  spec pass counts compare inside the declared `tolerances`.
