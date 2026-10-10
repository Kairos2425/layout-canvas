# Commercial & Licensing

## License model

The core repository is **MIT** — see `LICENSE`. That includes the Block IR,
session engine, parametric blocks, compiler, verification adapters
(DRC/LVS/PEX/specs), Virtuoso/Spectre bridge, MCP server, web canvas and
gallery protocol. Use it commercially, fork it, embed it — MIT permits all
of that.

Two carve-outs live in `NOTICE`:

- `examples/models/sky130/` stays **Apache-2.0** (upstream SkyWater files).
- The **"Layout Canvas" name is reserved** — MIT does not license marks.
  Forks and redistributed products should use a different name.

## What stays private, by design

The architecture is deliberately local-first so the commercial-sensitive
parts never touch this repository:

| Asset | Where it lives |
|---|---|
| Commercial/foundry PDKs | `*.pdk.json` descriptors on private paths (`LAYOUT_CANVAS_PDK_DIR`), gitignored |
| Foundry model decks, DRC decks | Local files referenced by path/env only |
| Customer designs, run products, GDS | Local; `verify_report` records carry verdicts, not geometry |
| Investor/financing documents | Out of git entirely |

A `*.pdk.json` descriptor contains layer numbers, rule values and OA names
you choose to encode — it is **your** file about **your** PDK; we never
receive a copy. Check what a descriptor exposes with `layout-canvas pdk
check <file>`.

## Commercial arrangements

The open core stays MIT permanently. Offerings that may carry commercial
terms:

- **Private PDK enablement** — descriptor authoring, extraction recipes and
  block generators for foundry-proprietary processes under NDA.
- **Enterprise deployment** — private gallery registry, SSO/policy controls,
  air-gapped installs.
- **Support & verification** — real-PDK bring-up (see
  `docs/CADENCE_INTEGRATION.md`), flow audits, priority fixes.

Contact: open a GitHub issue marked "commercial" or reach the maintainers
directly.

## Contributions

Contributions are MIT, under the DCO-style certification in
`CONTRIBUTING.md`. This keeps the core clean to re-license for enterprise
bundles while remaining irrevocably open.
