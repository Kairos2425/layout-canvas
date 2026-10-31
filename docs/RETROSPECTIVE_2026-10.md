# Phase Retrospective — October 2026

Project state checkpoint after the reliability/JEDEC and quality-checker phases.
All numbers below are measured, not aspirational.

## Capability map (what actually works today)

| Layer | State | Evidence |
|---|---|---|
| IR → GDS compile | Parametric blocks sky130/ihp + generic `gen_*` for descriptor-only PDKs | 9-task frozen bench, LVS match on all MOS tasks |
| Verification | DRC (KLayout subset), LVS (LayoutToNetlist + comparer), PEX sim | `verify` = extract/DRC/LVS/quality in one gate |
| Spec system | op/tran/ac/dc, settling/slew/overshoot/db/bw_3db, nominal+stress+drift | `flat-scalar-v2` scorecards |
| Reliability | 14-item JEDEC matrix: 6 pass-grade + antenna deterministic + latch/ESD structural, rest honestly unavailable | `layout-canvas reliability`, release `jesd-aligned-v1` |
| Optimization | spec-driven coordinate descent w/ margin gradient + feasibility gate | bench task `sky130-optimize-specs` |
| Candidates | K variants × full verify, fail-closed per candidate, web compare UI | bench task `sky130-ota-candidates` |
| Mixed-signal | Verilog RTL → vvp dump → PWL → ngspice tran | `Testbench.digital`, iverilog 12.0 via WSL |
| Quality | symmetric/match/order/align/abut checked on placed geometry | golden_diffpair: 5/5 constraints, 0.0000µm deviations |
| Ecosystem | Gallery (git remote, verify receipts), MCP 39 tools, Virtuoso .il/.scs export + dry-run acceptance | public repo Kairos2425/layout-canvas-gallery |
| Bench | frozen v5, 9 tasks, baseline regression gate | `bench --check-baseline` |
| Tests | 391 passed / ~4-5 env skips | ngspice 47 + klayout 0.30.12 + iverilog 12.0 |

## What worked (do again)

1. **The frozen bench pays rent immediately.** v1's optimize task exposed that
   pass/fail counting had no gradient → pure area shrinkage + LVS-illegal
   winners. The checker isn't overhead — it's the bug-finder. Same story when
   the matching checker went live: it instantly caught *wrong declarations in
   the shipped examples* (align top vs bottom). Every new verifier found real
   defects on day one.
2. **Fail-closed is load-bearing.** "unavailable" as a first-class verdict
   prevented three false-green classes: missing simulator, missing rule table
   (ihp antenna), missing model (ESD before clamps). The discipline cost is
   real (more code paths) but the alternative is fake numbers.
3. **Empirically checking vendor tool semantics before trusting them.**
   KLayout's `antenna_check` diode factor scales the gate-area *denominator*
   — vs the PDK's subtractive EGAR — and would false-pass protected nets.
   Only a synthetic truth table caught it. Rule: verify semantics on a
   controlled example before wiring any tool into a verdict.
4. **X-card parameters don't reach wrapper subckts.** `delvto`/`mulu0` did
   nothing until the model prelude's `.param` defaults were patched to forward
   into inner M-cards. Device-level aging now has real measured drift
   (op 0.61→0.78 V), not a parameter that silently goes nowhere.
5. **Declarations deserve validators.** The constraint declaration repairs
   (align top→bottom) were honest fixes — the checker judged the *intent*,
   found the declaration wrong, and the fix changed zero geometry.

## What didn't work (don't repeat)

1. **Parallel workstream collision.** A second agent committed `852a000`
   mid-task and swept a sibling feature's `cli.py` into its commit — code
   survived, history lies. Protocol now: worktree isolation or agreed file
   ownership before parallel sessions.
2. **Assumed coordinate semantics.** The router's "met1 at a pin's y belongs
   to this net" assumption produced spacing violations at x<0 placement;
   relative placement computed margins from cell *origin* instead of bbox
   edge. Both fixed, both were assumptions that survived until a real layout
   broke them. New rule: geometry invariants get a regression test when fixed.
3. **SPICE power rails were silently broken for months.** Subckt-internal
   nodes were local names; the "working" op points were resistor-divider
   artifacts. Root cause found only when AC gain was physically impossible
   (−122 dB). Lesson: a passing sim ≠ a powered sim — sanity-check an
   internal rail node, not just the output.
4. **Encoding/paper cuts on Windows.** GBK console × UTF-8 source caused a
   commit-message corruption, a `verify` crash on `µm`, and unreadable IR
   loads. Mitigations shipped (`errors="replace"`, explicit encodings) — but
   the pattern is "write tools defensively", not "remember UTF-8".
5. **Promotional numbers drifted from reality** until the count-sync rule
   (README/index.html/SKILL/test-count in the same commit) was institutionalized.

## Honest gaps (what we still do NOT do)

- **Silicon/foundry-grade verification**: DRC/LVS are the KLayout-pya subset,
  not foundry decks; aging is a parametric proxy, not `aging_model` or
  `silicon_calibrated`. THB/HAST/temp-cycle/FIT are out of domain by physics,
  not by missing code.
- **Virtuoso remote leg**: `.il`/`.scs` export + dry-run acceptance exists;
  real `virtuoso -nograph` execution is unverified pending an EDA host.
- **ESD strike simulation**: clamp presence + rail connectivity are structural
  evidence only; no HBM/CDM waveform sim (compact diode models aren't
  calibrated for strike currents; no rail clamp modelled).
- **Intra-cell quality**: finger interdigitation/ABBA order/dummy presence is
  generated but not machine-inspected inside cells.
- **Heterogeneous processes**: extraction recipes assume planar bulk MOS;
  SOI/FinFET need a recipe adapter.
- **Schematic editing**: we consume netlists/IR; the upstream-style schematic
  canvas doesn't exist (by design so far).

## Retrospective verdict

The phase delivered its thesis: *verdicts only with evidence*. Three kinds of
proof now travel together — physical (DRC/LVS/PEX), behavioral (spec matrix
incl. stress/device-stress), and declared-intent (matching/quality/intent) —
each refusing to fake the others' rungs. The biggest risks left are coverage
depth (foundry decks, real Virtuoso, intra-cell inspection) rather than
correctness of what exists.

## Next-phase candidates

- Remote Virtuoso acceptance on a real EDA host (one env var away).
- Antenna-style real-data expansion for IHP rules (needs verified numbers).
- Intra-cell pattern inspection (ABBA/dummy) to close the quality gap the
  checker already reports as uninspected.
- Mixed-signal loop closure: digital side measured on iverilog waveforms too
  (currently analog-only specs).
- SOI/FinFET recipe adapter research.
