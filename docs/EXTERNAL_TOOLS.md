# External Tool Environment

Layout-canvas treats every external EDA tool as a probed adapter
(`tools/backends.py`). Missing tools are `unavailable`, license-gated tools
are detected and still attempted (license failure = real `failed` result),
and nothing reports a fabricated clean result.

Quick check on any host: MCP tool `probe_environment`, or

```bash
python -c "from layout_canvas.tools.backends import probe_environment; import json; print(json.dumps(probe_environment(), indent=1))"
```

## Simulators (SPICE decks from `compile_netlist` + stimulus)

| Tool | Kind | License | Status | Command wired |
|---|---|---|---|---|
| ngspice | SPICE sim | free/open | ✅ runner | `ngspice -b -o log deck.cir` |
| Xyce | SPICE sim | free/open | ✅ runner | `Xyce -o log deck.cir` |
| LTspice | SPICE sim | free (closed) | ✅ runner | `ltspice -b deck.cir` → `deck.log` |
| Spectre | SPICE sim | Cadence license | ✅ runner | `spectre +spice -log log deck.cir` |
| HSPICE | SPICE sim | Synopsys license | ✅ runner | `hspice deck.cir -o out` → `.lis` |
| Eldo | SPICE sim | Siemens license | ✅ runner | `eldo deck.cir` |

Selection: `run_simulation(simulator="auto")` probes FOSS first; name a tool
to force it. Blocks lacking transistor-level emitters → `refused` (named).

## Verification

| Tool | Kind | License | Status |
|---|---|---|---|
| **klayout-pya** (pip `klayout` module) | DRC + LVS | free/open | ✅ **in-process engine, verified on real GDS** (`tools/extract.py`, `tools/drc.py::run_pya_drc`, `tools/lvs.py::engine="pya"`) |
| KLayout (app binary) | DRC | free/open | ✅ runner (`tools/drc.py`) — needed for full foundry .drc/.lvs decks |
| Netgen | LVS | free/open | ✅ runner (`tools/lvs.py`) |
| Magic | DRC/extract | free/open | probe only — runner not wired |
| Calibre | sign-off DRC/LVS | Siemens license | probe only — reserved |

## Cadence Virtuoso / Spectre (remote acceptance)

Virtuoso runs on a licensed Linux EDA host, never locally. The
`virtuoso-accept` CLI / `tools/virtuoso_check.py` harness reaches it over
**SSH key auth** (always `BatchMode=yes` — a host that needs a password
reports `unavailable`, never a hang):

| Env var | Points at |
|---|---|
| `LAYOUT_CANVAS_VIRTUOSO_HOST` | ssh target (`user@host` or ssh-config alias) |
| `LAYOUT_CANVAS_VIRTUOSO_DIR` | remote working directory for upload/replay/logs |
| `LAYOUT_CANVAS_SPECTRE_MODELS` | remote spectre model deck (`include`d by the harness) |

`virtuoso-accept --dry-run` needs none of these — it validates the
generated `.il`/`.scs` locally against the GDS. Full setup:
`docs/VIRTUOSO_ACCEPTANCE.md`.

### In-process KLayout engine (`klayout-pya`)

The pip `klayout` package exposes the same geometry/netlist engine the
application runs — `Region` boolean/check ops, `LayoutToNetlist` device
extraction, `NetlistComparer` — with no external binary:

- `tools/extract.py::extract_netlist(gds, tech)` — real netlist extraction:
  MOS4 device recognition (official IHP-deck recipe: active−gate SD regions,
  `tS/tD/tG` terminal annotation layers, derived regions registered into
  connectivity), labels → net names, hierarchy preserved. Truthful: unwired
  layouts extract floating terminals as separate nets.
- `run_drc(engine="pya")` — representative min-width/min-spacing subset per
  tech (`_PYA_RULES`), real `width_check`/`space_check` with violation bboxes.
  **Not** a foundry-deck substitute — the app binary is still required for
  the full `sky130A.drc` / `sg13g2.drc` rule coverage.
- `run_lvs(engine="pya")` — extraction + SPICE round-trip + `NetlistComparer`
  topology compare. Reference netlists get auto-generated device-abstract
  wrappers (`lvs_device_wrappers`) so leaf X-cards resolve to single MOS4
  devices. A `failed` result is a real topology mismatch — current generated
  blocks have no internal wiring, so schematic-vs-layout mismatches are
  honest findings (wiring blocks is the roadmap item for LVS-clean layouts).
- `engine="auto"`: prefers external binaries when present, falls back to the
  in-process engine. `probe_environment` reports it under
  `in_process_engines` + `verification.available`.
- Official `.drc`/`.lvs` Ruby decks still need the `klayout` executable —
  the pip module has no DSL interpreter.

## PDK / model decks (data, not tools)

- **Sky130 models**: `sky130A` device libs (e.g. `sky130.lib.spice` from
  skywater-pdk) — pass via `run_simulation(includes=[...])`.
- **DRC deck**: `sky130A.drc` KLayout deck — pass `deck_path` to `run_drc`.
- **LVS setup**: netgen `sky130A_setup.tcl` — `setup_path` or
  `NETGEN_SETUP` / `SKY130_NETGEN_SETUP` env vars.
- **External PDK descriptors**: `LAYOUT_CANVAS_PDK_DIR` (directory scanned
  for `*.pdk.json`) and `LAYOUT_CANVAS_PDKS` (pathsep-separated file list)
  register additional PDKs with no code — layer map, pin-label datatype,
  extraction recipe, DRC subset and `model_libs.spice_prelude_file` all
  come from the JSON. Loaded PDKs and per-file diagnostics surface in
  `probe_environment` under `external_pdks`/`external_pdk_errors`; see
  `docs/PDK_DESCRIPTORS.md` for the schema and the privacy rules
  (commercial PDK material stays on private paths, never in git).

## Binary overrides

Adapters check `LAYOUT_CANVAS_<TOOL>` env vars before PATH, e.g.
`LAYOUT_CANVAS_NGSPICE=/opt/ngspice/bin/ngspice`. Use this for tools that are
installed but not on PATH.

## Host setup (environment variables)

- **ngspice**: install ngspice ≥40 and put it on PATH, or set
  `LAYOUT_CANVAS_NGSPICE` to the binary. The real-model smoke test
  (`tests/test_sim.py::test_real_ngspice_op_smoke`) auto-skips without one.
- **IHP SG13G2 open PDK**: clone `IHP-GmbH/IHP-Open-PDK` and set
  `LAYOUT_CANVAS_IHP_MODELS=<repo>/ihp-sg13g2/libs.tech/ngspice/models`
  (provides `cornerMOSlv.lib`) and
  `LAYOUT_CANVAS_OSDI_DIR=<dir containing psp103.osdi>` (usually the
  ngspice `lib/ngspice` directory). Registered as PDK descriptor
  `ihp_sg13g2` with verified layer map, plus a real block package
  (`blocks/ihp_sg13g2/`: diff_pair, current_mirror, ota_5t, guard_ring)
  emitting `X`-instantiated `sg13_lv_nmos/pmos` PSP wrappers.

## IHP SG13G2 simulation recipe (verified)

SG13G2 MOS devices are PSP 103.6 subckt wrappers, not BSIM models. A working
deck needs all of the following — verified against ngspice-47:

```spice
.control
pre_osdi <osdi_dir>/psp103.osdi
pre_osdi <osdi_dir>/psp103_nqs.osdi
.endc
.lib "<ihp_models>/cornerMOSlv.lib" mos_tt
.param pre_layout=1
```

- `pre_osdi` loads the PSP Verilog-A binaries shipped inside ngspice
  (`lib/ngspice/*.osdi`). Without them ngspice reports
  `Unknown model type psp103va`. Use **forward slashes, no quotes** —
  backslashes are stripped and quotes leak into the filename.
- `.lib ... mos_tt` pulls the corner's global `.param`s (e.g.
  `sg13g2_lv_nmos_vfbo`); including `sg13g2_moslv_mod.lib` alone fails with
  `Undefined parameter`.
- `.param pre_layout=1` is required — the wrappers `.if`-branch on it.
- `.include` paths with spaces must be quoted (the runner does this now).

Test: `tests/test_ihp_sg13g2.py::test_real_ihp_psp_op_smoke` (skips when
binary/models absent).
- **Sky130 models**: bundled under `examples/models/sky130/` — upstream
  `skywater-pdk-libs-sky130_fd_pr` files verbatim (Apache-2.0): TT corner
  bins + pm3 model cards for `nfet_01v8`/`pfet_01v8` plus the `invariant`/
  `lod` parameter sets, wired by `sky130_tt.lib`. Block emitters
  instantiate the canonical `sky130_fd_pr__nfet_01v8`/`pfet_01v8` subckts
  (X-cards, `nf=` fingers). **Real foundry BSIM4 `.op` verified**
  (`tests/test_sim.py::test_real_sky130_tt_op_smoke`, VDD current = tail
  current as expected).
- **In-process verification**: `klayout==0.30.12` (pip, in `.venv`) gives real
  DRC/LVS without external binaries — device extraction, geometry checks and
  `NetlistComparer` all verified on compiled GDS
  (`tests/test_pya_verification.py`, 6 tests).
- **Still missing**: KLayout/Netgen/Magic binaries (needed for official
  foundry .drc/.lvs decks); Spectre/HSPICE/Eldo/Calibre installs
  (license-gated — adapters will report them the moment they exist).
