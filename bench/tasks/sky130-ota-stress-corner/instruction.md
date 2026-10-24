# sky130-ota-stress-corner

`starter/design.json` is the `examples/ota_lab.json` OTA: a diff pair
with a PMOS mirror load and an NMOS tail pull-down. It carries the three
lab testbenches — `tb_op_schematic` (schematic op), `tb_op_pex`
(post-layout op, probes the internal `tail` node), `tb_ac` (schematic ac)
— plus `tb_op_stress`, the **accelerated stress corner**.

`tb_op_stress` declares `stress = {vdd = 1.98, temp = 125}`: the same op
analysis is run twice — nominal (1.8 V, simulator-default temperature)
and at the stress corner (+10 % overvoltage at 125 °C, an HTOL-style
operating-point proxy). Two specs ride on it:

- `outp_stress` (`on = "stress"`): `outp`'s op value measured on the
  **stressed** run must sit inside [0.4, 0.9] V — the circuit still
  converges to a sane output point under stress.
- `outp_drift` (`on = "drift"`): `|stress − nominal|` of `outp`'s op
  value must stay ≤ 0.15 V — the output point is robust to the
  accelerated V/T corner.

This is a reliability *evidence* rung: an elevated-voltage/temperature
corner is a stress proxy, **not an aging model** — it says nothing about
time-dependent degradation (BTI/HCI/TDDB).

## Stated criteria

- All nine spec entries across the four testbenches evaluate `pass`
  (each spec is an individual check; partial credit is a task fail).
- `ppa.area_um2` stays inside the declared bound.
- The scorecard annotates the **worst-case spec** — the entry nearest
  its bound — as `specs.worst_case` / `specs.worst_margin_frac`.

## Evaluation clauses (anti-cheat)

- Unstated checks apply: the compiled layout must additionally produce
  `drc.total_violations == 0` and `lvs.match == true`.
- The spec verdicts — nominal, stress and drift — come from real
  simulator runs; a missing ngspice reports `unavailable` (counts in
  coverage, never as a pass). A stress/drift spec on a testbench without
  a declared `stress` corner is `unavailable`, not skipped.
- Editing `starter/design.json`'s spec bounds looser is a baseline
  regression, not a solution. Spec values are compared against the
  frozen reference within the declared `tolerances`; deterministic
  metrics compare exactly.
