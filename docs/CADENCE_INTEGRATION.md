# Cadence Integration Guide

For teams that already have **Virtuoso + Spectre licenses and a real PDK**:
this is the bring-up recipe that takes layout-canvas from "installed" to
"rebuilding verified cellviews in your OA database". Everything here is
fail-closed — a missing precondition is reported `unavailable` with the
reason, never a green-looking guess.

Estimated bring-up: ~30 minutes on an EDA host you can already SSH into.

## What you get at the end

```text
Block IR ──compile──► GDS ──┬── .il replay ──► OA cellviews (real shapes)
                            ├── .scs ────────► spectre -harness elaboration
                            └── census diff ─► verified | failed | unavailable
```

`layout-canvas virtuoso-accept` runs static (Cadence-free) checks anywhere,
then the SSH leg on your EDA host: upload artifacts → `virtuoso -nograph
-replay` → read-only census of every rebuilt cellview → diff vs GDS truth →
optional `spectre` dc-op elaboration with your model deck.

## Step 1 — install (5 min)

```bash
pip install git+https://github.com/Kairos2425/layout-canvas.git
# or: git clone … && pip install -e ".[dev]"
layout-canvas --help
```

No Cadence needed yet — the static leg runs with just `pip install klayout`.

## Step 2 — describe your PDK (15 min)

Commercial PDKs attach as a JSON descriptor that stays on private paths —
**never in git** (`*.pdk.json` is gitignored).

```bash
layout-canvas pdk dump sky130 > myprocess.pdk.json   # complete template
# edit: layer/datatype map, pin_label datatype, extract.roles,
#       leaf_devices, drc subset, oa_layers, model_libs.spice_prelude_file
layout-canvas pdk check myprocess.pdk.json           # offline validation +
                                                     # capability report
export LAYOUT_CANVAS_PDK_DIR=/secure/pdks            # directory of *.pdk.json
```

`pdk check` tells you exactly what the descriptor unlocks — which
`extract.roles` are covered, which `gen_*` generators register, which DRC
subset rules exist, whether `oa_layers` are mapped — before anything touches
a layout. A rejected descriptor exits non-zero with the specific contract
violation.

| Section | Unlocks |
|---|---|
| `layers` + `pin_purpose` | GDS compile, pin labels |
| `extract.roles` + `leaf_devices` | netlist extraction, LVS, `gen_*` blocks |
| `drc.rules`/`enclosure` | in-process DRC subset (not sign-off) |
| `oa_layers` | `.il` replay with real OA layer names |
| `model_libs.spice_prelude_file` | simulations with foundry models |

Schema + privacy rules: `docs/PDK_DESCRIPTORS.md`.

## Step 3 — remote acceptance on the EDA host (10 min)

```bash
export LAYOUT_CANVAS_VIRTUOSO_HOST=user@eda01        # ssh key auth only
export LAYOUT_CANVAS_VIRTUOSO_DIR=~/lc_accept
export LAYOUT_CANVAS_VIRTUOSO_TECHLIB=my_pdk_tech    # OA tech library name
export LAYOUT_CANVAS_SPECTRE_MODELS=/pdk/corners/tt.scs   # optional

layout-canvas virtuoso-accept design.ir.json
```

Why `TECHLIB` matters: the replay calls `dbCreateRect(cv list("diff"
"drawing") …)` — LPP names resolve only while the design library is attached
to a tech library that defines them. The emitted `.il` does this itself:

```skill
if(libId && ddGetObj("my_pdk_tech") && getd(quote(techSetTechLibName)) then
    techSetTechLibName(libId "my_pdk_tech")
else
    printf("layout-canvas: tech library … not visible …"))
```

Defaults exist for `sky130` (`sky130_fd_pr`) and `ihp_sg13g2` (`SG13G2`); a
commercial PDK needs your tech lib name once via `TECHLIB`/`--tech-lib`.
Run Virtuoso from a directory whose `cds.lib` can see the PDK — standard EDA
practice, nothing canvas-specific.

SSH is key-auth only (`BatchMode=yes`): a host that needs a password reports
`unavailable`, never a hang. Full harness anatomy:
`docs/VIRTUOSO_ACCEPTANCE.md`.

## Honest status and limits

- **Remote replay leg**: the harness is complete and every check is real,
  but it has not yet run against a live Virtuoso install. Expect a
  first-bring-up iteration loop; the census diff and CIW `*Error*` capture
  are built to surface exactly where it breaks.
- **DRC** is a representative min-width/space/enclosure subset per tech —
  not a foundry sign-off deck. Run the official `.drc` through the KLayout
  binary adapter (`run_drc(deck_path=...)`) or Calibre for tape-out.
- **LVS** on blocks without internal wiring reports honest topology
  mismatches by design (wiring is a roadmap item), not a silent pass.
- **Generated SKILL** draws vias as shapes rather than `dbCreateVia`, and
  there are no dummy-edge devices yet — both matter for production-grade
  matching, neither changes the verification contract.
- Everything else — session transactions, spec evaluation, optimizers —
  is identical with or without Cadence; the host only changes where
  acceptance evidence comes from.
