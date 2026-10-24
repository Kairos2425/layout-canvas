# sky130-diffamp-verify

Compile `starter/design.json` — a Sky130 differential amplifier at the
`examples/diffamp.json` level (diff pair + two PMOS mirror loads, matched
constraints, top-level ports) — and verify the resulting layout.

## Stated criteria

- The IR compiles to GDS without errors.
- Extraction finds the device census declared by the design.
- `ppa.area_um2` stays inside the declared bound in `task.toml`.

## Evaluation clauses (anti-cheat)

- The evaluator runs **additional checks not stated here**: any compiled
  submission must also produce `drc.total_violations == 0` and
  `lvs.match == true` on the in-process KLayout engine. Do not tune the
  design toward the stated bound while breaking connectivity.
- A missing engine reports `unavailable` — never a pass. An empty or
  black-boxed layout cannot satisfy the hidden LVS leg.
- Scores only count against the frozen `release_id` in
  `bench/benchmark.toml`; the frozen reference for this task lives in
  `results/reference.json`.
