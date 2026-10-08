# Layout Canvas

**Block-level, AI-native analog layout canvas** — parametric block library, typed
Block IR, deterministic layout compiler, in-process DRC/LVS/PEX verification, and
a local-first web canvas that AI agents can drive through MCP.

**Live demo → https://kairos2425.github.io/layout-canvas/**

```
Block IR (JSON) ── compile ──▶ gdsfactory ──▶ GDS
      │                            │
      │                            └──▶ SPICE netlist ──▶ LVS vs extracted netlist
      │                            └──▶ KLayout pya ──▶ DRC subset + extraction
      │                            └──▶ ngspice ──▶ schematic & post-layout sim
      │
      └── constraints · ports · metadata ──▶ MCP tools for AI agents
```

## What it does

| Stage | Capability |
|---|---|
| **IR** | Pydantic-typed Block IR: instances, params, placements, nets, ports |
| **Blocks** | Sky130: diff_pair · current_mirror · ota_5t · strongarm · cap_array · guard_ring — IHP SG13G2: diff_pair · current_mirror · ota_5t · guard_ring |
| **Compile** | Design → hierarchical GDS + SPICE; top-level router with keep-outs, risers, via stacks |
| **DRC** | In-process KLayout engine: real width / spacing / via-enclosure subset with violation bboxes drawn back on the canvas (RVE-style) |
| **LVS** | `LayoutToNetlist` extraction + `NetlistComparer` topology match; passive blocks get connectivity-semantics comparison; mismatched nets are localized and highlighted |
| **PEX sim** | Extracted netlist → BSIM4/PSP foundry models via ngspice; internal-node probes (`xd1.tail`), custom testbenches |
| **Testbenches** | Named sim setups on the IR (schematic/extracted, op/tran, probes) + per-spec pass/fail/unavailable with reasons; CLI exit codes, session run history |
| **Export** | Virtuoso SKILL (shapes/instances) + Spectre netlist, GDS/OASIS, SPICE |
| **Interfaces** | CLI · local web canvas · MCP stdio server (34 tools) |

## Install

```bash
git clone https://github.com/Kairos2425/layout-canvas
cd layout-canvas
pip install -e ".[dev]"
pip install klayout        # in-process DRC/LVS/extraction engine
pytest                     # 159 tests; tool-dependent tests auto-skip
```

Optional tools (probed adapters — absent tools report `unavailable`, never fake results):

- **ngspice** — schematic & post-layout simulation (`LAYOUT_CANVAS_NGSPICE=<path>`)
- **KLayout app / netgen / magic** — full foundry decks (`LAYOUT_CANVAS_KLAYOUT`, …)
- **IHP SG13G2 models** — `LAYOUT_CANVAS_IHP_MODELS`, `LAYOUT_CANVAS_OSDI_DIR`
  (see `docs/EXTERNAL_TOOLS.md`)

## Quick start

### CLI

```bash
layout-canvas compile examples/diffamp.json -o diffamp.gds    # IR → GDS
layout-canvas netlist examples/diffamp.json -o diffamp.sp    # IR → SPICE
layout-canvas schema                                         # Block IR JSON schema
layout-canvas web --port 8080                                # local canvas UI
layout-canvas mcp                                            # MCP stdio server
```

### Web canvas

`layout-canvas web` opens the editor at `http://127.0.0.1:8080`: place blocks,
edit params, wire nets, then **Verify** (extract → DRC → LVS, violations painted
on the layout) and **PEX** (post-layout ngspice on extracted netlist with
waveform sparklines). Projects save/load as JSON — local-first, nothing leaves
the machine.

### Python API

```python
from layout_canvas.ir.model import Design
from layout_canvas.tools.verify import verify_design
import layout_canvas.blocks  # registers all block generators

d = Design.model_validate({
    "name": "diffamp", "pdk": "sky130",
    "instances": [{"id": "U1", "block": "sky130.diff_pair",
                   "params": {"fingers": 4, "width": 2.0},
                   "placement": {"x": 0, "y": 0}}],
    "nets": [], "ports": [{"name": "inp", "pin": "U1.inp"}],
})
r = verify_design(d)          # extract → DRC → LVS in one call
r["passed"], r["drc"]["total_violations"], r["lvs"]["match"]
```

Post-layout simulation on the extracted netlist:

```python
from layout_canvas.tools.sim import simulate_extracted
res = simulate_extracted(d, probes=["out", "tail"], vdd=1.8)
# res["status"], res["waves"]  →  per-probe waveform vectors
```

### MCP (AI agents)

Point any MCP client at `layout-canvas mcp` (stdio). Agents get the full loop:
`open_design` → `insert_block_into_layout` / `transact` → `verify_design` →
`run_simulation(source="extracted", probes=[...])` → `export_virtuoso` /
`save_project`. `probe_environment` reports which EDA tools are live.

## Repository layout

```
src/layout_canvas/
  ir/          Block IR schema (Pydantic)
  blocks/      parametric generators (sky130/, ihp_sg13g2/)
  compiler/    GDS/SPICE/Skill compile, router, Virtuoso export, PPA
  engine/      transactional edit session (snapshot/undo)
  tools/       drc, extract, lvs, sim, verify, backends
  web/         local canvas app + gallery
  mcp/         stdio MCP server
tests/         159 tests incl. real-ngspice / real-PDK smokes
docs/          architecture, ADRs 0001-0009, external-tool setup
deploy/        Dockerfile + compose for the web app
```

## Security & privacy

- **Local-first by design.** The web app binds `127.0.0.1`; project files,
  GDS output and simulation decks stay on your machine. No telemetry, no
  cloud calls, no analytics.
- **No secrets in-repo.** PDK model paths, tool binaries and simulator paths
  are supplied via `LAYOUT_CANVAS_*` environment variables — see
  `docs/EXTERNAL_TOOLS.md`. Proprietary PDKs are never shipped or required.
- **Fail-closed verification.** DRC/LVS/sim report real tool results;
  missing tools return `unavailable`/`refused`, never fabricated green.
- **Process isolation.** GPL tools (KLayout app, netgen) run as external
  processes or the pip `klayout` module — no GPL code is linked in.
- Report vulnerabilities by opening a GitHub issue marked `security`, or
  contact the maintainer directly. Please do not include proprietary PDK
  material or NDA'd data in public reports.

## License

MIT — see `LICENSE`. Bundled Sky130 model files under
`examples/models/sky130/` are upstream SkyWater files and remain Apache-2.0.

## Prior art & references

Independently implemented; product-direction inspiration from Analog Canvas
(github.com/cascode-ai/analog-canvas). Built on
[gdsfactory](https://github.com/gdsfactory/gdsfactory),
[KLayout](https://www.klayout.de/), ngspice, and the Sky130 / IHP SG13G2
open PDKs. Related: Glayout (OpenFASOC), ALIGN, Layout21, BAG, VLSIR.
