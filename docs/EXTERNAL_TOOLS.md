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
| KLayout | DRC | free/open | ✅ runner (`tools/drc.py`) |
| Netgen | LVS | free/open | ✅ runner (`tools/lvs.py`) |
| Magic | DRC/extract | free/open | probe only — runner not wired |
| Calibre | sign-off DRC/LVS | Siemens license | probe only — reserved |

## PDK / model decks (data, not tools)

- **Sky130 models**: `sky130A` device libs (e.g. `sky130.lib.spice` from
  skywater-pdk) — pass via `run_simulation(includes=[...])`.
- **DRC deck**: `sky130A.drc` KLayout deck — pass `deck_path` to `run_drc`.
- **LVS setup**: netgen `sky130A_setup.tcl` — `setup_path` or
  `NETGEN_SETUP` / `SKY130_NETGEN_SETUP` env vars.

## Binary overrides

Adapters check `LAYOUT_CANVAS_<TOOL>` env vars before PATH, e.g.
`LAYOUT_CANVAS_NGSPICE=E:\...\ngspice.exe`. Use this for tools that are
installed but not on PATH.

## Current host status (verified 2026-09-15)

- **ngspice-47**: `E:\Reliability-PINN-Lab\.tmp\ngspice47\Spice64\bin\ngspice.exe`
  (not on PATH — set `LAYOUT_CANVAS_NGSPICE` or add `...\bin` to PATH).
  Real `.op` smoke verified end-to-end: diff_pair compiled → illustrative
  model deck → ngspice batch → DC solution (`tests/test_sim.py::
  test_real_ngspice_op_smoke`, auto-skips when no binary).
- **IHP SG13G2 open PDK**: `E:\Agentic TCAD\PDK\official_sources\IHP-Open-PDK\
  ihp-sg13g2` — real ngspice corner decks (`libs.tech/ngspice/models/
  cornerMOSlv.lib` etc.), KLayout tech, netgen and magic setups. Registered
  as PDK descriptor `ihp_sg13g2` with verified layer map, plus a real block
  package (`blocks/ihp_sg13g2/`: diff_pair, current_mirror, ota_5t,
  guard_ring) emitting `X`-instantiated `sg13_lv_nmos/pmos` PSP wrappers.
  **Real foundry-model `.op` verified end-to-end** (see recipe below).

## IHP SG13G2 simulation recipe (verified)

SG13G2 MOS devices are PSP 103.6 subckt wrappers, not BSIM models. A working
deck needs all of the following — verified 2026-09-15 against ngspice-47:

```spice
.control
pre_osdi E:/Reliability-PINN-Lab/.tmp/ngspice47/Spice64/lib/ngspice/psp103.osdi
pre_osdi E:/Reliability-PINN-Lab/.tmp/ngspice47/Spice64/lib/ngspice/psp103_nqs.osdi
.endc
.lib "E:/Agentic TCAD/PDK/.../ngspice/models/cornerMOSlv.lib" mos_tt
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
  current as expected). The on-disk `skywater-pdk` repo has empty library
  submodules; the bundled files remove that dependency for MOS decks.
- **Commercial PDKs on disk** (Spectre `.scs` decks, need license + Spectre):
  SMIC 0.18 (`PDK\installed\smic018mmrf`, `smic18eeprom`), TSMC 0.18
  (`PDK\TSMC180\...\tsmc18_models\models\spectre\`).
- **Still missing**: KLayout, Netgen, Magic binaries; Spectre/HSPICE/Eldo/
  Calibre installs (license-gated — adapters will report them the moment
  they exist).
