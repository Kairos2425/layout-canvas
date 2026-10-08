# External PDK descriptors (`*.pdk.json`)

A PDK descriptor is the process-data contract the compiler and tools
program against: layer/datatype map, pin-label datatype, extraction recipe,
DRC subset and model-deck pointer. Built-ins (`sky130`, `ihp_sg13g2`) ship
as code; **any other process — including commercial PDKs that must never
enter this repository — can join as a JSON file, with zero code changes.**

Point the loader at private paths with either (or both) environment
variables:

| Variable | Meaning |
|---|---|
| `LAYOUT_CANVAS_PDK_DIR` | a directory; every `*.json` inside is parsed |
| `LAYOUT_CANVAS_PDKS` | `os.pathsep`-separated list of descriptor files |

Loading is lazy (first `get_pdk`/`all_pdks` access) and idempotent — the
scan re-runs when the env spec changes. Diagnostics never kill the
registry: a malformed file lands in `external_pdk_errors()` (visible in
the MCP `probe_environment` result and `layout-canvas pdk list`), the
remaining files still register, and **a descriptor may not shadow a
built-in name** — a `*.pdk.json` claiming `sky130`/`ihp_sg13g2` is refused
with a diagnostic so a stray file can never swap a verified layer map.

```bash
export LAYOUT_CANVAS_PDK_DIR=/secure/foundry/pdks   # e.g. demo65.pdk.json
layout-canvas pdk list                             # what is registered + load errors
layout-canvas pdk dump sky130                      # a complete template to copy from
```

## What a descriptor unlocks (and what it does not)

| Capability | Descriptor-only PDK | Note |
|---|---|---|
| `import_gds` / web Import GDS | ✅ | pin labels read on `(layer, pin_purpose)` |
| `extract_netlist` (LVS extraction) | ✅ with `extract` section | planar-MOS fixed-role recipe |
| `run_lvs` (pya engine) | ✅ with `extract` section | `leaf_devices` generates the reference wrappers |
| `run_drc` (pya engine) | ✅ with `drc` section | min-width/min-space/via-enclosure subset |
| `run_simulation` model prelude | ✅ with `model_libs.spice_prelude_file` | resolved against the descriptor's directory |
| `verify_design` on imported cells | ✅ | same extract+DRC+LVS pipeline |
| **Parametric block generators** | ❌ — still Python | `blocks/<pdk>/` package + `base.register`; see below |
| Official foundry `.drc`/`.lvs` decks | external `klayout` binary | unchanged; pass `deck_path`/`setup_path` |

A `compile_ir`/`generate_block`/`verify_design` call that needs blocks a
descriptor-only PDK does not have fails with a named boundary —
`no blocks registered for pdk '<name>' — descriptor-only PDKs support
import_gds/DRC/LVS/sim` — never an opaque crash. The intended flow is:
`import_gds` your cells (with `spice_path` when you want LVS/sim to have a
reference), compose the top level in IR, then verify.

## JSON schema

```json
{
  "name": "demo65",                       // required, unique (built-ins reserved)
  "pin_purpose": 16,                      // pin-label datatype; default 16
  "layers": {"met1": [68, 20], "...": [L, DT]},
  "rules": {"default_routing_layer": "met2", "...": "free-form"},
  "model_libs": {"spice_prelude_file": "models/tt.lib"},
  "extract": {
    "roles": {"well_n": [64, 20], "...": null},
    "text_datatypes": [16],
    "leaf_devices": {"my_nch_lv": ["my_nch_dev", "nmos"],
                     "my_pch_lv": ["my_pch_dev", "pmos"]}
  },
  "drc": {
    "layers": {"rdl": [10, 0]},
    "rules": {"met1": [["width", 0.14], ["space", 0.14]]},
    "enclosure": [{"label": "via.m1", "cut": "via1",
                   "enclosed_by": ["met1"], "enc": 0.055}]
  }
}
```

- `layers` — `{name: [layer, datatype]}`; the drawing map used for pin
  discovery and routing-layer names.
- `rules` — free-form metadata (margins, preferred routing layer); merged
  into `pdk.rules`.
- `model_libs` — key/value pairs. `spice_prelude_file` is a model deck
  whose **contents** are injected as the simulation prelude; a relative
  path resolves against the directory containing the `*.pdk.json`
  (`base_dir`), so a commercial PDK ships `my.pdk.json` beside its
  `models/` directory.
- `extract.roles` — the **fixed role contract** below. `extract` present
  requires non-null `well_n`, `diff` and `poly` (the MOS derivation needs
  a well, an active and a gate); everything else may be `null`.
- `extract.text_datatypes` — datatypes whose texts become net labels;
  defaults to `[pin_purpose]`.
- `extract.leaf_devices` — `{subckt_name: [device_class, polarity]}`,
  `polarity` ∈ `nmos|pmos`. The keys are the leaf names your foundry
  netlists instantiate; the class names are what extraction emits and what
  the auto-generated LVS wrappers model. The first `nmos`/`pmos` entry
  supplies the device classes used by the MOS4 extractor.
- `drc.rules` — `{name: [["width"|"space", um], ...]}`. Keys resolve to a
  (layer, datatype) by checking, in order: `drc.layers` (extra DRC-only
  drawings you may declare there), `extract.roles` role names, `layers`
  drawing names, then a literal `"L/DT"` pair. An unknown name fails the
  file with the usable list in the message.
- `drc.enclosure` — list of `{label, cut, enclosed_by, enc}` (or the
  positional `[label, cut, [outers], enc]`): every `cut` shape must be
  covered by the union of `enclosed_by` shrunk by `enc` µm — the standard
  via/contact enclosure construction.
- `drc.layers` — optional named layers needed only for checks (marker
  layers that aren't part of `layers` or `extract.roles`).

## The fixed extraction-role contract

The extraction engine is a **planar-MOS template**: it derives
`p_active = diff & well & psdm`, gates = `active & poly`, S/D = `active −
gate`, bulk ties from `tap` (or implants), then connects
`poly/tap → licon → li1 → mcon → met1 → via1 → … → met5`, reads pin labels
and extracts MOS4 devices plus optional MIM caps. `extract.roles` keys are
therefore fixed and validated:

```
well_n diff tap poly licon li1 mcon
met1 met2 met3 met4 met5  via1 via2 via3 via4
nsdm psdm capm
```

Map each role to the process's (layer, datatype), or `null` where the
process lacks it (e.g. no `tap` layer → taps derive from implants; no
`capm` → MOS-only extraction). Processes that do not fit this template —
SOI, FinFET with multi-pattern gate cuts, BJT/hetero flows — need a code
adapter, not a descriptor: the honest failure mode is a rejected role key
or `unavailable`, never silently wrong extraction.

## Example

`examples`-style minimal descriptor (layer numbers here mirror sky130's so
a sky130-compiled GDS verifies under it — the pattern the test-suite's
`demo65.pdk.json` uses):

```json
{
  "name": "demo65",
  "pin_purpose": 16,
  "layers": {"nwell": [64, 20], "diff": [65, 20], "poly": [66, 20],
             "met1": [68, 20], "met2": [69, 20]},
  "model_libs": {"spice_prelude_file": "models/demo65_tt.lib"},
  "extract": {
    "roles": {"well_n": [64, 20], "diff": [65, 20], "tap": [65, 44],
              "poly": [66, 20], "licon": [66, 44], "li1": [67, 20],
              "mcon": [67, 44], "met1": [68, 20], "via1": [68, 44],
              "met2": [69, 20], "met5": null,
              "nsdm": [93, 44], "psdm": [94, 20], "capm": null},
    "leaf_devices": {"demo65_nch": ["demo65_nch", "nmos"],
                     "demo65_pch": ["demo65_pch", "pmos"]}
  },
  "drc": {"rules": {"diff": [["width", 0.15], ["space", 0.27]],
                    "met1": [["width", 0.14], ["space", 0.14]]}}
}
```

Verification under it is the same call, different name:
`extract_netlist(gds, tech="demo65")`, `run_drc(gds, tech="demo65")`,
`run_lvs(..., tech="demo65")` — missing sections report `unavailable`
instead of pretending a pass.

## Privacy

- **Keep commercial PDK files on a private path.** Put `*.pdk.json` and its
  model decks in a directory outside this repo (e.g. `~/pdk-private/` or a
  company fileshare path) and point `LAYOUT_CANVAS_PDK_DIR` at it — the
  repo's `.gitignore` covers `*.pdk.json`/`.tmp_*` accidents inside the
  tree, but the rule is: licensed process data never enters git.
- **`design.meta`/project files may reference your PDK name.** A `.lcproj`
  or gallery entry saying `"pdk": "demo65"` reveals only the name — whether
  even that is acceptable is the user's call for NDA'd processes; prefer
  neutral codenames in shared artifacts.
- `pdk dump <name>` prints *your* descriptor back — treat its output like
  the source file.
