# Usage Guide

Concrete end-to-end usage: IR → GDS/SPICE → verify (DRC/LVS) → PEX sim →
Virtuoso/Spectre export, through CLI, Python API, web canvas, and MCP.

## 1. Install

```bash
pip install -e ".[dev]"
pip install klayout          # in-process engine: DRC + extraction + LVS
```

Optional external tools — probed, never required, env-var overrides:

| Env var | Points at | Needed for |
|---|---|---|
| `LAYOUT_CANVAS_NGSPICE` | ngspice binary | schematic sim + PEX |
| `LAYOUT_CANVAS_KLAYOUT` | klayout app binary | official foundry .drc/.lvs decks |
| `LAYOUT_CANVAS_NETGEN` | netgen binary | external LVS engine |
| `LAYOUT_CANVAS_IHP_MODELS` | `<IHP-Open-PDK>/ihp-sg13g2/libs.tech/ngspice/models` | IHP real-model sim |
| `LAYOUT_CANVAS_OSDI_DIR` | dir containing `psp103.osdi` | IHP PSP models |

Check what is live: `python -c "import json; from layout_canvas.tools.backends import probe_environment; print(json.dumps(probe_environment(), indent=1))"`
or MCP tool `probe_environment`.

## 2. The Design IR

One JSON object describes a design: block instances + params + placements +
nets + ports. Example (also `examples/diffamp.json`):

```json
{
  "name": "ota_biased",
  "pdk": "sky130",
  "instances": [
    {"id": "U1", "block": "sky130.ota_5t",
     "params": {"diff_fingers": 4, "load_fingers": 4, "width": 2.0},
     "placement": {"x": 0, "y": 0}},
    {"id": "U2", "block": "sky130.current_mirror",
     "params": {"fingers": 2, "width": 2.0, "length": 0.5, "type": "nmos"},
     "placement": {"x": 18, "y": 0}}
  ],
  "nets": [
    {"name": "vbias",   "pins": ["U1.vbias", "U2.out"]},
    {"name": "vdd",     "pins": ["U1.vdd"]},
    {"name": "vss",     "pins": ["U1.vss", "U2.vss"]},
    {"name": "bias_in", "pins": ["U2.in", "U2.gate"]}
  ],
  "ports": [
    {"name": "inp", "pin": "U1.inp"},
    {"name": "inn", "pin": "U1.inn"},
    {"name": "out", "pin": "U1.out"},
    {"name": "vdd", "pin": "U1.vdd"},
    {"name": "vss", "pin": "U1.vss"},
    {"name": "bias_in", "pin": "U2.in"}
  ]
}
```

Block names are `<pdk>.<block>`. Sky130: `diff_pair`, `current_mirror`,
`ota_5t`, `strongarm`, `cap_array`, `guard_ring`. IHP SG13G2: `diff_pair`,
`current_mirror`, `ota_5t`, `guard_ring`. Get each block's params/ports via
`layout-canvas` → MCP `list_blocks`, or `/api/blocks` on the web app.

## 3. CLI

```bash
layout-canvas compile design.json -o design.gds     # IR → GDS
layout-canvas compile design.json -o design.oas -f oas
layout-canvas netlist design.json -o design.sp      # IR → SPICE (LVS ref)
layout-canvas schema                                 # full IR JSON schema
layout-canvas web --port 8080                        # local canvas
layout-canvas mcp                                    # MCP stdio server
```

## 4. Web canvas

`layout-canvas web` serves `http://127.0.0.1:8080` (localhost-only):

- **Place/edit** — pick a block, set params, place instances, wire nets.
- **Verify** — extract + DRC + LVS in one shot. DRC violations draw red
  boxes at the violation bbox; LVS mismatched nets highlight in the canvas.
- **Simulate** — `source=schematic` runs the reference netlist;
  `source=extracted` runs PEX on the extracted netlist. Optional `probes`
  (internal nodes like `xd1.tail`), `analysis` (`op`/`tran`),
  `vdd`, `stimulus` (full custom testbench text).
- **Virtuoso** — returns SKILL + Spectre text for the compiled layout.
- **Projects** — save/load design JSON (gallery/session APIs included).

POST endpoints: `/api/preview`, `/api/connectivity`, `/api/netlist`,
`/api/simulate`, `/api/testbench`, `/api/drc`, `/api/verify`, `/api/virtuoso`,
`/api/ppa`, `/api/abstract`, `/api/import_gds`, plus `/api/session/*` and
`/api/gallery/*`. Body: `{"ir_json": {...design...}, ...action fields}`.

### Testbenches & specs

A design carries named simulation testbenches with measurable specs:

```json
"testbenches": [{
  "name": "tb_op_schematic",
  "source": "schematic",            // schematic | extracted (PEX)
  "analysis": "op",                 // op | tran
  "vdd": 1.8,
  "stimulus": null,                 // extracted only
  "probes": ["tail"],               // extracted only
  "specs": [
    {"name": "outp_op", "signal": "outp", "measure": "final",
     "min": 0.7, "max": 1.0, "unit": "V"},
    {"name": "tail_pp", "signal": "xd1.tail", "measure": "pp",
     "max": 0.1, "unit": "V"}
  ]
}]
```

`measure` ∈ `final | min | max | mean | pp` over the waveform samples; a
spec needs `min` and/or `max`. Validation is strict: schematic benches
reject `stimulus`/`probes` (the schematic path has no custom-bench
parameter), testbench and spec names must be unique.

Each spec resolves to `pass` / `fail` / `unavailable` with a `reason` —
fail-closed: a missing simulator or missing signal is `unavailable`
(with the available signal names listed), never a silent pass. Per-bench
rollup `spec_status`: `fail` > `unavailable` > `pass` > `no_specs`.

Run them via:

- **CLI** — `layout-canvas testbench design.json [--name tb]`: one line
  per spec, exit 0 only when every spec passed.
- **Web** — `POST /api/testbench {ir_json, name?}`; the canvas Bench tab
  lists benches with per-spec chips and sparklines.
- **MCP** — `run_testbench {session_id?|ir_json, name?}`.
- **Session ops** — `{"op": "set_testbenches", "testbenches": [...]}`
  replaces the list through the transactional boundary.

Runs are journaled on the session (`snapshot().data.runs`, FIFO 50):
testbench and verify calls record kind/UTC timestamp/revision/status
summaries — never waveforms.

### Gallery

Publishing records a content hash, an AI/authorship flag, and a
publish-time verification snapshot (`{status, drc_violations, lvs_match,
passed}` — a verification failure marks the entry, never blocks it).
Re-publishing identical content raises `duplicate of <id>` unless
`allow_duplicate` is set. `gallery/list` accepts `verified_only`, `pdk`
and `tag` filters; the web gallery tab exposes the same filters plus
PDK/DRC·LVS/AI chips on each card. MCP mirrors it via `gallery_list`,
`gallery_get`, `gallery_publish` (`ai_generated` defaults true for
agents) and `gallery_fork` (opens the entry as a session).

## 5. Python API

```python
from layout_canvas.ir.model import Design
from layout_canvas.compiler.compile import compile_design, export_gds
from layout_canvas.compiler.netlist import export_spice
from layout_canvas.tools.verify import verify_design
from layout_canvas.tools.sim import simulate_extracted, simulate_auto
import layout_canvas.blocks  # register generators

design = Design.model_validate({...})

comp = compile_design(design)            # gdsfactory component
export_gds(design, "design.gds")         # or comp.write_gds(...)
export_spice(design, "design.sp")        # reference netlist

r = verify_design(design)                # extract → DRC → LVS
# r = {"passed": bool, "extract": {...}, "drc": {...}, "lvs": {...}}

# Schematic-level sim (auto testbench)
res = simulate_auto(design, analysis="op", vdd=1.8)

# Post-layout sim on the EXTRACTED netlist
res = simulate_extracted(
    design,
    analysis="op", vdd=1.8,
    probes=["out", "tail"],              # top-level nets & internal nodes
    stimulus="v1 in 0 0.9 dc 0\n.tran 1n 10n",   # optional custom bench
    executable="ngspice",                # or full path
)
# res["waves"] -> {"out": [...], "xd1.tail": [...]}, res["status"]
```

`verify_design` is fail-closed: missing engines report
`unavailable`, mismatches report real bboxes / net diffs.

### Import external GDS (Virtuoso stream-out)

A foreign cell can be registered as an instantiable block. Pins are read
from the GDS's pin-layer labels (the PDK's pin-label datatype — sky130
`(layer,16)`, IHP `(layer,2)`); each label becomes an `inout` port on the
matching drawing layer.

```python
from layout_canvas.blocks.gds_cell import register_gds_cell

block = register_gds_cell(
    "ext.inv_v1",
    "inv_v1.gds",
    pdk="sky130",
    cell_name="inv_v1",          # optional unless the GDS has >1 top cell
    spice_text=open("inv_v1.sp").read(),  # optional; must contain '.subckt inv_v1' with a matching pin count
)
# afterwards: block "ext.inv_v1" is instantiable in any parent design
```

Web canvas: **Import GDS** button in the Blocks palette — pick a
`.gds`/`.oas` file, confirm the alias, and the new block appears in the
palette ready to place (POST `/api/import_gds` with
`{alias, pdk, gds_b64, cell_name?, spice_text?, format?}`). The format is
sniffed (`%SEMI-OASIS` magic) when `format` is omitted; payloads are
capped at 64 MiB decoded / 96 MB base64 and rejected above that. The
response includes a pya-DRC summary (`drc.status`, `drc.violations`).

MCP: `import_gds` tool — `{alias, path, pdk, cell_name?, spice_path?}`.

Two limitations:

- **Pin labels required** — the GDS must carry pin labels on the PDK's
  pin-label layer (e.g. a Virtuoso stream-out with `met*/pn` labels).
  Zero labels → the import refuses instead of guessing.
- **No netlist → layout-only** — without `spice_text`/`spice_path` the
  cell has no transistor-level emitter: `simulate_design` refuses by name
  and LVS compares against a black-box (never a false pass).

## 6. MCP — drive it with an agent

Register the stdio server in your MCP client config:

```json
{
  "mcpServers": {
    "layout-canvas": {
      "command": "layout-canvas",
      "args": ["mcp"]
    }
  }
}
```

33 tools, grouped:

| Intent | Tools |
|---|---|
| Environment | `probe_environment` |
| Blocks | `list_blocks`, `generate_block`, `register_cell`, `import_gds` |
| Session | `open_design`, `close_design`, `transact`, `snapshot`, `undo`, `load_project`, `save_project`, `get_active_layout_info` |
| Build | `insert_block_into_layout`, `compile_session`, `compile_ir`, `generate_netlist`, `optimize`, `inspect_connectivity`, `inspect_ppa`, `render_preview_svg` |
| Gallery | `gallery_list`, `gallery_get`, `gallery_publish`, `gallery_fork` |
| Verify | `run_drc`, `extract_netlist`, `run_lvs`, `verify_design`, `run_testbench` |
| Simulate | `run_simulation` — `source="schematic"` or `"extracted"` (PEX), `probes`, `stimulus`, `analysis`, `vdd`, `tran_stop`, `tran_step` |
| Export | `export_virtuoso` (SKILL + Spectre), `export_abstract` |

Typical agent loop: `open_design` → `insert_block_into_layout` /
`transact` → `verify_design` → `run_simulation(source="extracted")` →
`export_virtuoso` → `save_project`.

## 7. Deploy

```bash
cd deploy && docker compose up     # serves the web canvas in a container
```

Bind is localhost by default; expose behind your own proxy/auth if you put
it on a network. See `docs/DEPLOYMENT.md`.

## 8. What verification means here

- **DRC** = in-process KLayout `Region` checks: real min-width, min-spacing,
  and via/contact **enclosure** rules per PDK (Sky130 headline values +
  IHP values taken from the official `sg13g2_tech_default.json`). It is a
  representative subset, not tape-out sign-off — run the full foundry deck
  via the `klayout` app adapter for that.
- **LVS** = real `LayoutToNetlist` extraction from the generated GDS +
  `NetlistComparer` vs the design's own reference netlist. Passive blocks
  (guard_ring) are compared on pin/net connectivity semantics.
- **PEX** = the extracted netlist itself is simulated in ngspice, with
  device cards rewritten onto foundry wrapper subckts (Sky130 BSIM4 /
  IHP PSP103) — not the schematic.
