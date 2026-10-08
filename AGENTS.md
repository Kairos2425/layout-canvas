# Agent entry

**If you are an AI agent**, read `skills/layout-canvas/SKILL.md` first — it is
the operating manual for driving this repository's layout compiler through MCP
(`layout-canvas mcp`), including the 8-step design loop, the typed edit ops,
and how to read fail-closed results.

Working in this repository as a contributor:

- Follow `CONTRIBUTING.md`; commits use `feat:` / `fix:` / `docs:` / `test:` /
  `chore:` prefixes.
- Verification is fail-closed by contract (ADR 0002, 0005): never make a DRC,
  LVS or simulation path report a pass when a tool, model or signal is
  missing, and never loosen a rule value to make a check green.
- Machine-specific paths go through environment variables
  (`docs/EXTERNAL_TOOLS.md`); private PDKs and run products never enter git.
- Run `.venv\Scripts\python.exe -m pytest tests -q` before committing; set
  `LAYOUT_CANVAS_NGSPICE` to a console ngspice to exercise the simulation
  tests instead of skipping them.
