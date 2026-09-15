# ADR 0008: External tools are probed adapters, not hardcoded binaries

Status: accepted · 2026-09-15

## Context

The product must run wherever an engineer sits: a laptop with only ngspice,
a foundry VDI with Spectre+Calibre and license servers, or a CI box with
nothing. Hardcoding one binary per capability makes every other environment
a code change. ADRs 0002/0005 already fixed the fail-closed contract; this
fixes *which* tool fills it.

## Decision

`tools/backends.py` registers one `ToolAdapter` per tool: executable
candidates, version probe, license gating, capability kind, and whether a
runner is wired.

- `probe_environment()` reports facts (status/version/license), never
  verdicts. Agents call it before promising verification.
- `run_netlist`/`simulate_design` take `simulator="auto"|<name>`; auto
  prefers FOSS. A named-but-absent tool → `unavailable`; a non-simulator
  (e.g. klayout) named for simulation → `unavailable` with the mismatch
  stated.
- Explicit `executable=` still overrides everything for custom installs.
- License-gated tools are attempted when installed; a failed checkout
  surfaces through the normal log-error scan (license patterns included),
  i.e. honest `failed`, never guessed.

Runner coverage: ngspice, Xyce, LTspice, Spectre, HSPICE, Eldo wired for
simulation; KLayout DRC and Netgen LVS already wired; Magic and Calibre are
probe-only until their runners land.

## Consequences

- Adding a tool = one adapter entry + (optionally) a command builder in
  `_SIM_RUNNERS` — no changes in callers or MCP schema.
- `docs/EXTERNAL_TOOLS.md` is the human-facing mirror of the registry.
