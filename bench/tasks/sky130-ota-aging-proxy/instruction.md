# sky130-ota-aging-proxy

`starter/design.json` is the OTA lab design (diff pair + PMOS mirror load
+ NMOS tail pull-down) carrying the four testbenches of
`sky130-ota-stress-corner` **plus `tb_aging`**, the parametric
device-stress corner.

`tb_aging` declares
`stress = {vdd = 1.98, temp = 125, dvth_nmos = 0.03, mu0_scale = 0.85}`:
the stress run is not only the accelerated voltage/temperature corner —
the device keys inject BSIM4 instance parameters onto the netlist's MOS
cards (`delvto = 0.03` on every nmos, `mulu0 = 0.85` on every MOS), and
the model prelude is inlined and patched so the foundry wrapper
subcircuits forward those params onto their inner BSIM4 devices. This is
a **transparent parametric proxy** for NBTI/HCI-style threshold/mobility
drift — it is *not* a foundry aging model: no time evolution, no
wearout statistics. Three specs ride on it:

- `outp_aging` (`on = "stress"`): `outp`'s op on the **device-stressed**
  corner must still converge to a sane point.
- `outp_aging_drift` (`on = "drift"`): `|stressed − nominal|` of `outp`
  must stay ≤ 0.25 V — the drift budget under combined V/T + device
  stress.
- `i_vdd_budget` (`on = "stress"`, `signal = "i(v_vdd)"`): the supply
  current the vdd source delivers on the stressed corner (positive =
  sourcing) must stay ≤ 400 µA — an electromigration-style current
  budget check on a real measured branch current.

If a device-stress key cannot reach a device model (the PDK's wrapper
subcircuits lack a `delvto`/`mulu0` forwarding path), the stress run and
every `stress`/`drift` spec on it report `unavailable` — the tool never
silently runs an un-stressed "stress" corner.

## Stated criteria

- All twelve spec entries across the five testbenches evaluate `pass`.
- `ppa.area_um2` stays inside the declared bound.
- Worst-case spec annotation as in the sibling spec tasks.

## Evaluation clauses (anti-cheat)

- Unstated checks apply: `drc.total_violations == 0` and
  `lvs.match == true` on the compiled layout.
- Spec verdicts come from real simulator runs; a missing ngspice
  reports `unavailable` (counts in coverage, never as a pass).
- Editing `starter/design.json`'s spec bounds looser is a baseline
  regression, not a solution. Spec values are compared against the
  frozen reference within the declared `tolerances`; deterministic
  metrics compare exactly.
