# ADR 0002: Verification results are fail-closed and machine-readable

Status: accepted · 2026-09-14

## Context

DRC/LVS depend on external tools (KLayout, Netgen) and PDK decks that may be
absent. The worst outcome for an agent-driven flow is a false clean result:
the agent proceeds on a lie. Analog Canvas ADR 0055 applies the same rule to
simulation: refuse and name the blocker rather than emit a result.

## Decision

`tools/drc.py` and `tools/lvs.py` return a stable contract with four states:

- `passed` — tool ran, report parsed, zero violations / circuits match.
- `failed` — tool ran and reported real violations or mismatched nets.
- `unavailable` — a prerequisite is missing (tool binary, deck, setup file,
  input file). `clean`/`match` is `null`, never `true`.
- `error` — tool ran but output is unparseable, timed out, or crashed.

No path maps a missing tool or missing report to `passed`. Reasons are
returned in `errors[]` as actionable messages (which file/tool is missing),
so an agent or a human can fix the environment rather than guess.

## Consequences

- Callers (MCP `run_drc`/`run_lvs`, tests, future cloud workers) must treat
  `unavailable`/`error` as *not verified*, distinct from *verified clean*.
- Adding a new checker requires the same four-state contract.
