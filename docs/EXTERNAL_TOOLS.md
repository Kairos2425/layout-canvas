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

## Current host status (probed 2026-09-15)

All 10 adapters report `unavailable` — no EDA tools on PATH. The interface
layer is fully wired and tested fail-closed; installing any of the FOSS
tools (ngspice, KLayout, Netgen) immediately lights up the corresponding
loop with zero code changes.
