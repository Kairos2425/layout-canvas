# Engineering handoff worklist

This worklist tracks the current handoff from architecture notes to a real,
repeatable engineering path. It is intentionally outcome-based: a green unit
test alone does not count as end-to-end completion.

## Current turn (2026-09-14)

- [x] Commit pending PPA/optimizer/fail-closed work as clean baseline.
- [x] Transactional edit session (`engine/`) — snapshot/transact/undo +
      expected_revision optimistic lock + unified diagnostic envelope.
- [x] Connectivity projection (`derived/`) — pin→net, unconnected pins,
      degenerate-net diagnostics.
- [x] Project file protocol (`protocol/`) — versioned `.lcproj.json`,
      bare-IR v0 migration, structured load diagnostics.
- [x] PDK descriptor layer (`pdk/`) — compiler pin labels via descriptor.
- [x] ngspice runner (`tools/sim.py`) — fail-closed incl. `refused` with
      unresolved blocks named.
- [x] Cell abstract export (`compiler/hierarchy.py`) — bbox/pins/layer
      polygon counts for hierarchical reuse.
- [x] Local web review canvas (`web/`, `layout-canvas web`) — verified
      end-to-end over HTTP.
- [x] MCP tools: open_design/snapshot/transact/undo/inspect_connectivity/
      close_design/compile_session/export_abstract/run_simulation/
      save_project/load_project.
- [x] ADRs 0001–0006 freeze the new contracts; 69/69 tests pass.

## Verified real-tool status (2026-09-15, this host)

- [x] Real ngspice `.op` on all three decks: illustrative level-1,
      bundled **real SkyWater BSIM4 TT** (`examples/models/sky130/
      sky130_tt.lib`, upstream files verbatim), and **real IHP SG13G2
      PSP 103.6** via `pre_osdi` (see EXTERNAL_TOOLS.md recipe).
- [x] `blocks/ihp_sg13g2/` — second PDK generator library (diff_pair,
      current_mirror, ota_5t, guard_ring) on the verified SG13G2 layer map.
- [x] MCP full-loop e2e over real stdio JSON-RPC
      (`tests/test_mcp_e2e.py`): open → connectivity → transact →
      PPA → compile → netlist → real sim → abstract → project
      round-trip → register_cell → close.
- [x] Web review canvas: all `/api/*` endpoints exercised over HTTP
      (preview/ppa/connectivity/netlist/abstract/sample/blocks).
- [x] Web canvas polish: `render_svg` now draws real per-layer polygons
      (y-flipped, deterministic layer palette, polygon budget), and the
      page has session mode — click selects an instance (hit-test on
      per-instance bboxes), second click commits `set_placement` through
      `session/edit` (revision-locked, undoable). Verified over HTTP.
- [x] In-process KLayout engine (`klayout-pya`, pip `klayout` in `.venv`):
      real verification with no external binary — `tools/extract.py`
      (LayoutToNetlist device extraction, official-deck recipe), pya DRC
      subset (real width/space checks + violation bboxes), pya LVS
      (extraction + NetlistComparer topology compare). Verified in
      `tests/test_pya_verification.py` on compiled GDS.
- [ ] LVS-clean generators: extracted layouts reveal our blocks have no
      internal wiring (terminals float) — schematic-vs-layout mismatches are
      honest findings until blocks emit contacts/metal for S/D and gates.
- [ ] Foundry-deck DRC/LVS: full `sky130A.drc` / `sg13g2.lvs` coverage still
      needs the klayout app binary (Ruby DSL, not in the pip module); IHP PDK
      ships decks + golden testcases on disk for when a binary exists.
- [ ] Remaining Sky130 corner/device coverage beyond nfet_01v8+pfet_01v8
      (lvt, 3v3, 5V, caps/resistors — fetch on demand).

## Remaining (next targets)

- [ ] Block internal wiring (licon/li1/met1) so extracted topology can
      match schematic — the gate to a real LVS `passed` result.
- [ ] Real Sky130 DRC/LVS golden flow: needs klayout/netgen binaries;
      in-process engine covers the subset meanwhile.
- [x] Optimizer ↔ session integration: `engine/optimize.py` commits each
      iteration through `transact` — revisioned and undoable.
- [x] Hierarchy consumption: `blocks/cells.register_design_cell` +
      MCP `register_cell`; parents instantiate via `block: "<alias>"`.
      Cell sim stays fail-closed (blackbox for LVS, refused for ngspice).
- [x] KLayout Salt packaging: `pymacros/block_canvas.lym` autorun wrapper
      added next to `grain.xml`; real install still needs a KLayout host.
- [x] Flattened hierarchical simulation: cell netlist emitters inline the
      child's full hierarchy (ADR 0007 amended); verified with real ngspice
      + sky130 BSIM4 in `test_cell_flattened_real_simulation`.

## Acceptance bar

The project is only reported as complete for this turn when the code path is
tested from Block IR through generated layout/netlist and verification result
handling. If KLayout, Netgen, or a Sky130 deck is unavailable, the result must
be an explicit, machine-readable `blocked`/`unavailable` outcome—not a false
clean result.

## Queued tasks (ViraStack对比-20261016)

自审依据：`E:/Devin_work/docs/VIRASTACK_COMPARISON_20261016_ZH.md` +
layout-canvas 专项笔记（`E:/Devin_work/layout-canvas/research/`）。Cadence
ViraStack Custom Layout 第三方实测翻车点——DRC/LVS/ERC 全过但无共心匹配、
器件分散、保护环半空——正是我们 IR 约束一等公民设计对应的场景；短板是
验收判据缺失，不是模型笨。

- [x] **P0 匹配/对称质量判据 checker**（差异化最强项）。IR `symmetric`/
      `match`/`order` 约束目前只声明未几何验证。补 `tools/matching.py`（或
      `verify_design` 的 `quality` 段）：指叉 pattern 校验（ABBA/共心）、
      对称轴偏差、dummy 完整性、质心偏移、guard-ring 连续性——全部机器
      可判、fail-closed；golden 样例把"匹配结构存在且正确"写成检查项。
      对标结论："先把质量编码成判据，再谈自动化"。（已落本轮）
- [ ] **P1 多候选生成+并排比较**（抄 Layout Prototypes 形态）。optimize/
      generate 出 K 个参数/摆放变体 → 并排 preview + per-candidate 摘要
      （area/hpwl/DRC/spec_status）；也是 gallery 卡片素材。
- [ ] **P2 agent 协议瘦身 + 决策点确认**。snapshot 紧凑模式（摘要化历史、
      契约引用而非全文）；高影响 transact 前 decision-point 确认（对标其
      "跑方案前先确认 5 个决策点"的交互）。
- [ ] **P1.5 黄金对照样例**（待观察#3 的反超素材 + 对外叙事硬通货）。做一个
      common-centroid diff pair + guard ring 的 sky130 golden 样例，
      `verify_design` 输出携带 matching/quality 判据证据（对称轴偏差、
      质心偏移、dummy/护环完整性逐项 pass），gallery 卡片带验证凭据——
      直接对照 ViraStack tsmc65 翻车案例的"质量维度"。
      来源：ViraStack对比-20261016 专项笔记 §MVP含义-2/待观察-3。
- [ ] **P2 设计意图载体**（Mental Model 同构；服务 token 压缩与未来工艺
      迁移 agent）。IR `meta` 或新 `intent` 通道显式携带：匹配对（哪些
      instance 须成对）、敏感 net（高阻/低阻节点）、参数取值理由；
      import_netlist 已能推断 polarity/拓扑，结论落进 intent 而非散落
      diagnostics。来源：ViraStack对比-20261016 总报告 §五-2。
