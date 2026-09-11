# Layout Canvas

**Block-level, AI-native IC layout editor** — parametric block library + constraint-aware placement + AI copilot, built on gdsfactory.

## Architecture

```
Block IR (JSON) ──compile──> gdsfactory ──export──> GDS/OASIS
     │                             │
     └─ port contract             └─ DRC/LVS
     └─ constraints
     └─ metadata
```

## Why Block IR?

Existing tools (gdsfactory, KLayout, Virtuoso) model layouts as **shapes/cells**. Block IR adds:

- **Parametric instances** with typed parameters, ranges, defaults
- **Port contracts** (layer, direction, width, position) for auto-routing
- **Constraint metadata** (symmetry, matching, alignment) for placement engines
- **Netlist emission** for LVS/SPICE co-design

This layer is our **differentiation** and **moat** — borrowed schema patterns from VLSIR (BSD), compiled down to gdsfactory, bridged up to Virtuoso SKILL / PyAether.

## Status (P0)

- [x] Block IR v0 schema (Pydantic + JSON Schema)
- [ ] IR → GDS compiler
- [ ] Sky130 L0-L2 blocks (target 5: current mirror, diff pair, cap array, 5T-OTA, StrongArm comparator)
- [ ] DRC runner (KLayout batch)
- [ ] LVS runner (netgen)
- [ ] CI badges
- [ ] KLayout Salt plugin (block palette + parameter panel)

## License

- **Core IR + compiler + block library**: Apache-2.0 (open source, can embed in closed products)
- **KLayout plugin**: Proprietary (KLayout Python scripts can self-license per upstream clarification)

## Quick Start

```bash
pip install -e ".[dev]"
pytest
```

## Roadmap

1. **P0** (now): IR + 5 Sky130 blocks + DRC/LVS + KLayout plugin skeleton
2. **Stage A**: KLayout Salt package with MCP server (Claude Code integration)
3. **Stage B**: Public block gallery + CI badges
4. **Stage C**: Web canvas (read-only → editable)
5. **Stage D**: Virtuoso/Aether bridge for enterprise
6. **Stage E**: AI netlist→constraint inference, ALIGN integration, AI block code generation

## Commercial Model

- **T0** (free): Public blocks + limited AI quota → community funnel
- **T1** (Pro ¥99-299/mo): Private projects, more AI, cloud DRC
- **T2** (Team): Collaboration, private block libs, Agent API
- **T3** (Enterprise): On-prem + commercial PDK plugins + EDA bridges
- **T4** (Ecosystem): Block marketplace revenue share

## Prior Art & License Audit

| Tool | License | Commercial Use |
|------|---------|----------------|
| gdsfactory, kfactory | MIT | ✅ Can embed |
| Glayout (OpenFASOC) | Apache-2.0 | ✅ Can use |
| ALIGN, Layout21, BAG | BSD-3 | ✅ Can use |
| Sky130/GF180/IHP PDK | Apache-2.0 | ✅ Can distribute |
| KLayout (desktop) | GPL-2/3 | ⚠️ Use as external process |
| KLayout PyPI | GPL-3 + author exemption | ✅ Scripts not derivative |
| netgen, Xyce | GPL | ⚠️ External process only |

**Conclusion**: No hard licensing blockers. GPL tools isolated via process boundaries.

## References

- [gdsfactory](https://github.com/gdsfactory/gdsfactory) - Python IC layout framework
- [Glayout](https://github.com/idea-fasoc/glayout) - OpenFASOC layout generators
- [ALIGN](https://github.com/ALIGN-analoglayout/ALIGN) - Analog layout from netlists
- [Layout21](https://github.com/dan-fritchman/Layout21) - Rust layout IR
- [VLSIR](https://github.com/Vlsir/Vlsir) - Silicon interchange format
- [KLayout](https://www.klayout.de/) - Mask layout viewer/editor
- [Sky130 PDK](https://github.com/google/skywater-pdk) - Open-source 130nm PDK
