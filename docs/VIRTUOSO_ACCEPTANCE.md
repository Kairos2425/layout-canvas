# Virtuoso acceptance (`virtuoso-accept`)

Layout-canvas never claims a Cadence run it didn't do. The acceptance
channel has two layers, both fail-closed:

| Layer | Runs where | What it proves | Status |
|---|---|---|---|
| `static_report` | anywhere (needs only the `klayout` pip module) | the emitted `.il`/`.scs` faithfully describe the GDS: per-cell rect/polygon/label/instance counts, paren balance, cellview coverage + `dbSave`, subckt/ends balance, X-card refs and port counts | `static_ok` |
| `remote_acceptance` | a Linux EDA host over SSH | the `.il` actually replays in Virtuoso and the OA database contains the same shapes; the `.scs` elaborates in spectre against real models | `verified` |

```bash
# Cadence-free check — safe on any machine, no SSH touched
layout-canvas virtuoso-accept examples/diffamp.json --dry-run

# full acceptance against your EDA host
layout-canvas virtuoso-accept examples/diffamp.json --host eda01 --lib canvas_lib
```

Exit code is 0 only when every executed stage is green (`static_ok`, and
`verified` when the remote leg ran). `failed` *or* an explicitly-requested
leg that came back `unavailable` exits 1 — a check that could not run is
not a pass.

## Prerequisites

- A Linux host with **Cadence Virtuoso** (IC6.x / ICADVM18.x) and
  **spectre** on its `PATH` (licensed through your normal environment).
- **SSH reachability with key-based auth** — the harness always runs
  `ssh -o BatchMode=yes`, so password prompts are never attempted and
  never hang: a host that needs a password reports `unavailable`
  immediately. Set up keys once:

  ```bash
  ssh-keygen -t ed25519          # if you don't have a key yet
  ssh-copy-id user@eda-host      # installs your pubkey on the EDA host
  ssh user@eda-host true         # sanity: must succeed with no prompt
  ```

  `~/.ssh/config` aliases work too — `LAYOUT_CANVAS_VIRTUOSO_HOST` may be
  `user@host` or a `Host` alias name.

## Environment

| Variable | Meaning |
|---|---|
| `LAYOUT_CANVAS_VIRTUOSO_HOST` | ssh target: `user@eda-host` or an ssh-config alias (`--host` overrides) |
| `LAYOUT_CANVAS_VIRTUOSO_DIR` | remote working directory for the upload/replay/log artifacts |
| `LAYOUT_CANVAS_SPECTRE_MODELS` | remote path(s) to the PDK model deck (`include`d by the generated harness); unset ⇒ the spectre leg reports `unavailable` |

No credentials ever appear in code, config files or docs — authentication
is delegated to your ssh keys/agent.

## What the remote leg does

1. Generates `<design>.il` (SKILL replay), `<design>.scs` (Spectre
   netlist), `verify.il` (a read-only census script) and
   `<design>_harness.scs` (top-level spectre bench), then `scp`s them to
   `$LAYOUT_CANVAS_VIRTUOSO_DIR`.
2. `virtuoso -nograph -replay <design>.il -log ciw.log` rebuilds every
   cellview in the OA library.
3. `virtuoso -nograph -replay verify.il -log ciw_verify.log` opens each
   rebuilt cellview and dumps a shape/instance census to
   `verify_report.txt` (`foreach(sh cv~>shapes …)` per `objType`, plus
   `cv~>instances`).
4. `scp` pulls `ciw.log`, `ciw_verify.log`, `verify_report.txt` back; the
   census is diffed cell-by-cell against the GDS truth, and CIW `*Error*`
   / `*WARNING*` counts go into the result.
5. With `LAYOUT_CANVAS_SPECTRE_MODELS` set, `spectre <design>_harness.scs`
   runs a dc operating point — elaboration proves every subckt/model/port
   resolves against the real deck. Without models the leg is honestly
   `unavailable` (a device netlist cannot elaborate without its PDK).

Verdicts: `verified` = replay clean **and** census matches **and** spectre
ok · `failed` = a real mismatch or tool error (details in `checks[]` and
`spectre.errors`) · `unavailable` = host/dir unset, ssh unreachable,
`virtuoso`/`spectre` missing on the remote — with the setup recipe in
`result["setup"]`.

## PDK layer names — the real gotcha

`dbCreateRect` in the `.il` uses **OA layer names** (`("met1" "drawing")`),
not GDS numbers. The target OA library must be attached to a techfile that
*defines those names* — the sky130 OA PDK ships `met1`/`li1`/`poly`/… so
the built-in map just works, but a commercial PDK's techfile usually calls
its layers something else (`M1`, `metal1`, …).

Resolution order in `export_skill`/`static_report`/`remote_acceptance`:

1. the PDK descriptor's `oa_layers` section — `"L/DT" → oa layer name`,
   purpose always `"drawing"`:

   ```json
   {
     "name": "demo65",
     "layers": {"met1": [68, 20], "...": [0, 0]},
     "oa_layers": {"68/20": "M1", "65/20": "DIFF"}
   }
   ```

2. the built-in table for `sky130` / `ihp_sg13g2`;
3. the `L<layer>_D<dt>` fallback name (e.g. `L68_D20`) — nothing is
   dropped, but the OA techfile almost certainly doesn't define it.

**Workflow: always `--dry-run` first.** `unmapped_layers` lists every
`L/DT` pair that fell through to the fallback — fill those into your
descriptor's `oa_layers` before spending a remote run. Label purposes are
emitted as `pin` on the resolved drawing layer.

## Three ways to reach a Virtuoso

| Setup | How |
|---|---|
| Direct SSH | `LAYOUT_CANVAS_VIRTUOSO_HOST=user@eda01` + keys; the harness does the rest |
| Via [virtuoso-bridge-lite](https://pypi.org/project/virtuoso-bridge-lite/) (MIT) | run `virtuoso-bridge start` on the EDA host to get a local Virtuoso session API; feed our `.il` through its `load` path — useful when you already run the bridge and don't want a second SSH channel |
| Local Linux VM | WSL2/VirtualBox VM with Virtuoso installed; treat the VM as the EDA host |

## Result contract

```python
from layout_canvas.tools.virtuoso_check import static_report, remote_acceptance

rep = static_report("out.gds", "sky130", library="canvas_lib")
# rep = {"status": "static_ok" | "failed" | "unavailable",
#        "checks": [{"name", "status", "detail", ...}],
#        "unmapped_layers": ["L/DT", ...], "paths": {...}}

res = remote_acceptance(host, workdir, "out.gds", "sky130",
                        spectre_models="/pdk/spectre/tt.scs")
# res["status"] ∈ verified | failed | unavailable
# res["checks"]  per-cell census diffs + CIW *Error*/*WARNING* counts
# res["spectre"] {"status": ok|failed|unavailable, "errors", "log_tail"}
```

| Status | Means |
|---|---|
| `static_ok` | the artifacts are internally consistent with the GDS — Cadence never ran |
| `verified` | remote Virtuoso replay produced the same shapes; spectre elaborated the netlist |
| `failed` | a real mismatch/error — read `checks[]` and `spectre.errors` |
| `unavailable` | a precondition is missing (host, dir, models, klayout, ssh) — nothing was verified, nothing failed |
