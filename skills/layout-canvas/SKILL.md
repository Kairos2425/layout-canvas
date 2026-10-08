---
name: layout-canvas
description: Drive the layout-canvas analog layout compiler through its MCP server — compile Block IR into GDS, run DRC/LVS/PEX, evaluate testbench specs, export to Virtuoso, and publish to the gallery. Use whenever a task involves generating, verifying or iterating on analog layout in Sky130 or IHP SG13G2.
---

# layout-canvas agent skill

layout-canvas turns a declarative **Block IR** (instances of parametric analog
blocks + nets + ports + constraints + testbenches) into a routed GDS, then
verifies it physically (DRC, LVS, PEX) and electrically (ngspice specs).
Every result is **fail-closed**: a missing tool, model or signal is reported as
`unavailable`/`refused` with the reason — never as a pass. Treat any status
other than `ok`/`pass` as "not verified", and say so to the user.

## Start the server

```bash
pip install -e .            # once, inside the repo
layout-canvas mcp           # stdio MCP server; point your MCP client at it
```

Optional environment (only needed for the features they unlock):

| Variable | Unlocks |
|---|---|
| `LAYOUT_CANVAS_NGSPICE` | path to `ngspice_con`/`ngspice` for simulation + testbench specs |
| `LAYOUT_CANVAS_IHP_MODELS`, `LAYOUT_CANVAS_OSDI_DIR` | IHP SG13G2 PSP103 models |
| `LAYOUT_CANVAS_GALLERY` | gallery directory (default `~/.layout_canvas/gallery`) |
| `LAYOUT_CANVAS_PDK_DIR`, `LAYOUT_CANVAS_PDKS` | external `*.pdk.json` PDK descriptors (import_gds + DRC/LVS + model prelude; no block generators) |

Call `probe_environment` first when you are unsure what is installed.

## The loop (8 steps)

1. `list_blocks` — read the palette: block names, params with min/max, ports.
   Never invent a block or parameter name.
2. `open_design` with a Block IR document (or `load_project`). You get a
   `session_id` and `revision`.
3. `inspect_connectivity` — read the electrical facts (pin→net table,
   unconnected pins, floating nets) **before** editing.
4. `transact` with typed edits and `expected_revision`. Known ops:
   `set_placement`, `set_params`, `add_instance`, `remove_instance`,
   `add_net`, `remove_net`, `set_net_pins`, `add_port`, `remove_port`,
   `add_constraint`, `remove_constraint`, `set_meta`, `set_testbenches`.
   A rejected transaction returns `diagnostics[]` naming the edit index and
   the object — fix and resubmit; do not retry blindly. `undo` is available.
5. `compile_session` / `render_preview_svg` — look at the layout; `inspect_ppa`
   for area, bbox, device count.
6. `verify_design` — extract + DRC + LVS in one call. Read `drc.violations`
   (each has a layer, rule and bbox) and `lvs.unmatched_nets`; `passed` is
   true only when DRC is 0 and LVS matches.
7. `run_testbench` — runs the design's `testbenches[]` (schematic or
   post-layout `extracted`) and evaluates `specs[]`. `spec_status` is
   `pass` / `fail` / `unavailable` / `no_specs`; every spec carries a
   human-readable `reason`. `run_simulation` with `source:"extracted"` and
   `probes:["tail"]` gives raw waveforms, including internal nodes
   (`xd1.tail`). An `analysis:"ac"` testbench drives an `ac 1` source on
   an input port and measures `db` (low-frequency gain) / `bw_3db` specs;
   `optimize` with `objective:"specs"` coordinate-descends bounded numeric
   params toward all-specs-pass (needs a live simulator).
8. Deliver: `export_virtuoso` (SKILL `.il` + Spectre `.scs`), `save_project`,
   or `gallery_publish` (defaults to `ai_generated: true` — keep it that way
   when the design was produced by you).

## Block IR shape

```json
{
  "name": "diffamp", "pdk": "sky130",
  "instances": [
    {"id": "dp", "block": "sky130.diff_pair", "params": {"width": 2.0, "fingers": 2}},
    {"id": "cm", "block": "sky130.current_mirror", "params": {},
     "placement": {"relative_to": "dp", "relation": "right_of", "margin": 2.0}}
  ],
  "nets": [{"name": "tail", "pins": ["dp.tail", "cm.in"]}],
  "ports": [{"name": "TAIL", "pin": "dp.tail", "direction": "input"}],
  "testbenches": [
    {"name": "tb_op", "source": "extracted", "analysis": "op", "vdd": 1.8,
     "probes": ["tail"],
     "specs": [{"name": "tail_bias", "signal": "xd1.tail", "measure": "final",
                "min": 0.45, "max": 0.75, "unit": "V"}]}
  ]
}
```

Rules the engine enforces (do not fight them):

- Pin references are `<instance_id>.<port>`; both must exist.
- `source: "schematic"` testbenches cannot carry `probes` or a custom
  `stimulus` — those are post-layout (`extracted`) features.
- A `Spec` needs `min` and/or `max`.
- Imported external GDS cells (`import_gds`) without a SPICE `.subckt` have no
  netlist: simulation is `refused` and LVS cannot pass. Say so instead of
  working around it.

## Reading results honestly

| Field | Meaning |
|---|---|
| `status: "refused"` | the design contains a block without a transistor-level emitter — name it |
| `status: "unavailable"` | tool or model missing — report what is missing, do not substitute |
| `drc.total_violations > 0` | real geometry violations; move/resize via `transact`, re-verify |
| `lvs.match: false` | inspect `unmatched_nets` — they are highlighted in the web canvas too |
| `spec_status: "unavailable"` | the simulation did not produce the signal — check `reason` |

## Pitfalls

- Parameter bounds are physical (e.g. IHP MOS width ≥ 0.32 µm is set by the
  contact + enclosure rule). Out-of-range params are rejected, not clamped.
- Placement is relative or absolute in micrometres; overlaps are not
  auto-resolved — run `verify_design` after moving things.
- The web canvas and MCP share one `DesignSession` boundary; a human may be
  editing at the same time. Always pass `expected_revision`.
- Descriptor-only PDKs (`*.pdk.json` via `LAYOUT_CANVAS_PDK_DIR`/`PDKS`,
  listed by `probe_environment.external_pdks` or `layout-canvas pdk list`)
  have **no block generators** — `generate_block`/`compile_ir` on them
  refuse by name. The supported flow is `import_gds` → IR composition →
  `verify_design`/`run_simulation`.
