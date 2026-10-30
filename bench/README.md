# layout-canvas bench — frozen evaluation set for the layout compiler

A small, frozen benchmark suite for the layout-canvas compiler, modeled on
the upstream **analog-design-bench** (Arcadia-1 "Long-Horizon Analog Design
Bench") methodology and adapted to layout compilation. Fail-closed
throughout: a missing tool, model or capability reports `unavailable` —
counted in coverage, never as a pass.

```bash
layout-canvas bench                          # run all tasks
layout-canvas bench --task ihp-mirror-verify # run one task
layout-canvas bench --check-baseline         # regression gate vs frozen baselines
layout-canvas bench --freeze                 # re-freeze results/reference.json
python bench/run.py --check-baseline         # same runner, no CLI indirection
```

Each run writes `bench/runs/<UTC ts>/scorecard.json` (`metric_schema:
"flat-scalar-v2"`) with per-task checks/metrics, coverage and provenance
(tool versions, git sha, per-task input sha256, platform, timestamp).

## Methodology — what we internalized from upstream

1. **Scores only exist against a frozen release.**
   `benchmark.toml` carries `release_id` + `state = "frozen"` and the
   ordered task-slot table. Change any bound, starter, input or slot and
   the release is a different benchmark: bump `release_id` and re-freeze
   every `tasks/*/results/reference.json` in the same change.

2. **Task directories are self-describing.**
   Each `tasks/<id>/` holds `task.toml` (schema_version, id, deliverable,
   type, bounds), `instruction.md` (the spec in measurable language plus
   anti-cheat clauses), `starter/` (public starting point),
   `solution/` (reference), `inputs/` (fixtures for import/descriptor
   tasks) and `results/reference.json` (frozen golden baseline).

3. **The public starter is not the eval.**
   `instruction.md` states only the spec surface. Every task that
   compiles additionally faces the *hidden checks* —
   `drc.total_violations == 0` and `lvs.match == true` on the in-process
   KLayout engine — whether or not the instruction mentions them. This
   is our equivalent of upstream's hidden test set: optimizing toward
   the declared bound while breaking connectivity still fails.

4. **Worst-case annotation.**
   Spec tasks report which criterion sits closest to its bound
   (`specs.worst_case` / `specs.worst_margin_frac`) — our analogue of
   upstream's PVT-corner annotation, expressed over the task's parameter
   variants rather than process corners.

5. **`flat-scalar-v2` metric schema.**
   Every task result is `checks[]` + `metrics[]` where checks carry
   `{id, measured_value, spec, status, unit, message, deterministic,
   tol, tol_rel, capability, evidence_level}`; the scorecard adds
   `coverage`, `evidence_complete` and `failure`. Deterministic checks
   compare exactly against baseline; sim-derived checks inside their
   recorded tolerance.

6. **Provenance is part of the score.**
   Every scorecard records ngspice/KLayout versions, git sha, the
   sha256 of every file the task consumed, platform and UTC timestamp.
   A number without provenance is not a baseline.

7. **Baselines are gates, not references to copy.**
   `results/reference.json` is checked in per task; `--check-baseline`
   diffs a live run against it — deterministic metrics must match
   *exactly*, sim values must stay within the task's declared
   `tolerances`. A regression exits non-zero listing every diff.

## Reliability evaluation ladder

Every check carries `capability`/`deterministic` as before; checks that
touch measured or simulated evidence additionally carry an
`evidence_level` field naming the rung of the evidence ladder that
produced it, surfaced verbatim into `scorecard.json`:

1. **`deterministic`** — structural truth from in-process engines:
   DRC violations, LVS match, extracted device/net counts, PPA bounds.
   Exact-equality baseline gates.
2. **`solver_verified`** — a real simulator produced the number on the
   schematic netlist (`Spec.on="nominal"`). `source:"extracted"` benches
   sit one notch higher on the same rung (solver-verified *with
   parasitics*) — still not silicon.
3. **`stress_corner`** — the same analysis re-run at an accelerated
   voltage/temperature corner (`Testbench.stress`, HTOL-style proxy).
   `Spec.on="stress"` bounds the stressed scalar; `on="drift"` bounds
   `|stress − nominal|`. **Still solver-verified evidence — never an
   aging model.**
4. **`parametric_stress`** *(this release)* — a stress corner that
   additionally injects device-level BSIM4 parameters
   (`stress.dvth_nmos`/`dvth_pmos` → `delvto`, `stress.mu0_scale` →
   `mulu0` on the MOS instance cards, forwarded through the foundry
   wrapper subcircuits). A transparent NBTI/HCI-style drift *proxy* —
   **no time evolution, no wearout statistics, not an aging model.** If
   the PDK's wrappers cannot forward the params the run reports
   `unavailable` rather than silently running un-stressed.
5. **`aging_model`** — time-evolving degradation (BTI/HCI/TDDB) from a
   foundry aging deck. Not implemented; no check may claim it.
6. **`silicon_calibrated`** — models fitted to measured silicon. This
   repository never claims it, by construction.

An `unavailable` verdict means the rung's capability was missing — the
check reports that honestly rather than substituting a weaker rung.

### JEDEC coverage matrix

`layout-canvas reliability <design.json>` (CLI), `reliability_report`
(MCP) and `layout_canvas.tools.reliability.reliability_report(design)`
report the release `jesd-aligned-v1` matrix: 14 JEDEC/JEP items — htol
(JESD22-A108), nbti_aging / hci_aging (JEP122), em_current (JEP122
Black), latch_up (JESD78), antenna, esd_hbm (JS-001), esd_cdm (JS-002),
thb_hast (A110/A101), temp_cycle (A104), htsl (A103), endurance (A117),
fit_rate (JESD85), mismatch_aging (JEP122) — each with
`id/jedec/evaluated_via/evidence_level/status/reason/details` and
`status ∈ {pass, fail, unavailable, not_applicable, structural}`.
Items the tool cannot evaluate (e.g. humidity chamber tests,
FIT extrapolation, missing spec declarations or a missing
simulator) are `unavailable` with the missing capability named — never
fabricated as passes. `latch_up` passes only at structural grade
(block-level self-protection heuristic — not physical latch-up
verification). `esd_hbm`/`esd_cdm` are structural too: a per-port
connectivity scan checks that each external port shares its net with
the `pad` pin of a rail-connected `sky130.esd_dual_diode` clamp
(structural evidence only — no HBM/CDM strike simulation); an
unprotected port is `fail`, no clamp at all is `unavailable`.

## Task types

| `type` | Runner semantics |
|---|---|
| `verify` | load starter IR → compile → extract + DRC + LVS → bounds |
| `spec` | verify pipeline + run every declared testbench; each spec entry is its own check |
| `optimize` | `DesignSession` → `optimize_session(objective="specs")` inside `run.max_iterations`; score = `specs_passed` + `iterations` + `final_area_um2`; the tuned layout is re-verified |
| `import` | `register_gds_cell` from `inputs/` (gds + optional spice emitter) → instantiate in parent starter → verify; the alias is unregistered afterwards |
| `candidates` | load starter IR + `starter/variants.json` (the exact `{name, edits}` list `eval_candidates` takes) → multi-candidate sweep: each variant through transact → compile → verify → schematic benches; per-candidate `status` gate (`[candidates.expect]` pins designed-fail variants — a rejected edit list is a real `failed` verdict, not noise), spec pass counts as capability checks, mechanical ranking |

All types get the hidden DRC/LVS checks once compilation succeeds — for
`candidates`, once per candidate that reaches verification.

## The nine tasks

| id | capability surface |
|---|---|
| `sky130-diffamp-verify` | baseline compile: sky130 diffamp IR → GDS → DRC=0 → LVS match |
| `ihp-mirror-verify` | cross-PDK compile + verify (ihp_sg13g2 mirror) |
| `sky130-ota-lab-specs` | spec judgement: 3 testbenches, 7 spec entries, area bound, worst-case spec |
| `sky130-imported-cell-lvs` | external asset reuse: fixture GDS import → parent verify |
| `demo65-descriptor-pdk` | process externalized: `inputs/demo65.pdk.json` drives `gen_*` blocks + verify |
| `sky130-optimize-specs` | bounded spec-driven tuning: must reach all-specs-pass ≤ 6 rounds |
| `sky130-ota-stress-corner` | accelerated V/T corner: OTA op re-run at 1.98 V/125 °C, `on:"stress"` + `on:"drift"` specs (HTOL-style proxy, not an aging model) |
| `sky130-ota-aging-proxy` | parametric device stress: BSIM4 `delvto`/`mulu0` injected on the stress corner (NBTI/HCI proxy) + drift + `i(v_vdd)` supply-current EM budget |
| `sky130-ota-candidates` | multi-candidate sweep: 4 `{name, edits}` variants on the OTA tail width — 3 pass end-to-end, 1 designed-fail variant pins the fail-closed transact rejection; per-candidate hidden checks + spec counts + ranking |

## `task.toml` schema (schema_version = 1)

```toml
id = "my-task"                    # must equal the directory name
type = "verify"                   # verify | spec | optimize | import | candidates
title = "…"
deliverable = "starter/design.json"
pdk = "sky130"

[env]                             # optional env vars for the run,
LAYOUT_CANVAS_PDKS = "inputs/demo65.pdk.json"   # paths resolve vs task dir

[run]                             # optimize tasks
max_iterations = 6

[import]                          # import tasks
alias = "bench.imported_dp"; gds = "inputs/cell.gds"; spice = "inputs/cell.spice"

[candidates]                      # candidates tasks
variants = "starter/variants.json"  # optional default; {name, edits} list
                                  # fed verbatim to eval_candidates
[candidates.expect]               # name -> designed status (default "passed")
ghost = "failed"
[candidates.expect_specs]         # name -> designed spec pass count
w080 = 3                          #   (unlisted names expect all specs to pass)

[tolerances]                      # baseline-compare tolerances for sim values
sim_rel = 0.05; sim_abs = 0.0

[bounds]                          # declared checks: metric -> spec
"extract.devices" = { equals = 16, deterministic = true, unit = "count" }
"ppa.area_um2" = { max = 1600.0, unit = "um2" }
```

A malformed `task.toml` is a `TaskError` naming the file and the field —
never a silent zero.

## Solver-visible surface (exposure contract)

A solver — human or agent — may receive **only**:

- `tasks/<id>/instruction.md` (the spec surface),
- `tasks/<id>/starter/` and `tasks/<id>/inputs/` (public fixtures).

Everything else is evaluator material and must not reach the solver:
`solution/`, `results/reference.json`, `task.toml`'s `[bounds]` table,
the hidden-check list, and `bench/run.py` internals. This is the same
contract razavi-bench uses ("do not expose golden_solution or the
rubric"): a solver that saw the bounds table could tune to the number
instead of solving the task.

When a real agent solves these tasks, `skills/layout-canvas/SKILL.md`
is part of the treatment — give it to the solver exactly as shipped;
there is intentionally no per-task routing table. The agent decides
which tool is relevant, the evaluator decides whether the result counts.

## Adding a task

1. `mkdir bench/tasks/<id>` with `task.toml`, `instruction.md`,
   `starter/design.json`, `solution/design.json` (+ `inputs/` for
   import/descriptor fixtures).
2. Add a `[[tasks]]` slot in `benchmark.toml` describing its surface.
3. Run `layout-canvas bench --task <id>` until green.
4. `layout-canvas bench --task <id> --freeze` to write its baseline —
   review the diff carefully; a frozen reference is a contract.

## Release & freeze protocol

- `state = "frozen"`: bounds, starters, inputs and the task list are
  immutable. Any change → bump `release_id` and re-freeze every
  reference in the same change.
- Freeze with the real toolchain you want the gate to mean
  (`LAYOUT_CANVAS_NGSPICE` for sim tasks). Freezing without a
  capability records honest `unavailable` checks; a later
  capability-equipped run reports them as *evidence gained* notes, not
  silent passes.
- `bench/runs/` is gitignored; `results/reference.json` is checked in.
- Never edit a reference to make a diff green — investigate the
  regression or bump the release.

## Anti-cheat clauses (what the runner enforces)

- Hidden `drc_clean` + `lvs_match` checks on every compiled task.
- `capability` semantics: spec/sim checks are capability-gated — a
  missing ngspice yields `unavailable` task status (coverage shows it),
  never a fabricated pass and never a fake fail of unrelated checks.
- Deterministic baseline gate: `extract.devices`, `drc.total_violations`,
  `lvs.match`, declared `deterministic` bounds must match the frozen
  reference exactly.
- Input integrity: per-task sha256 of starter+inputs in every
  scorecard's provenance.
- The optimizer only moves params through `DesignSession.transact` —
  revisions are recorded; there is no out-of-band edit path to fake.
