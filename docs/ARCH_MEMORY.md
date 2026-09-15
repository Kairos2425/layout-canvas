# Layout-Canvas 研发档案与产品架构备忘 (Project Memory & Architecture)

> 记录日期：2026-09-12  
> 项目定位：面向生成式 AI Agent 的原生模拟版图中间表示 (Block IR) 与可验证编译器 (Layout Compiler)

---

## 1. 核心架构与模块能力地图

| 层次 | 核心代码路径 | 职责与能力 | 核心对外接口 / 产物 |
|---|---|---|---|
| **中间表示 (IR)** | `src/layout_canvas/ir/model.py` | 声明式、类型安全的 Pydantic v2 抽象数据结构（支持相对放置关系拓扑、对称匹配、护环约束、走线网络） | `Design`, `Instance`, `Placement`, `Constraint`, `Net` |
| **事务引擎 (Engine)** | `src/layout_canvas/engine/` | 唯一变更边界：`DesignSession` 持有 design，typed edits 原子提交 + `expected_revision` 乐观锁 + undo 历史；统一诊断 envelope（status/revision/diagnostics/object_id） | `snapshot`, `transact`, `undo`；ops: set_placement/set_params/add_remove instance/net/port/constraint/set_meta |
| **派生投影 (Derived)** | `src/layout_canvas/derived/connectivity.py` | 纯只读电气事实投影：pin→net 解析、未连接 pin、退化 net/多重驱动诊断——Agent 动手前先读电气事实 | `inspect_connectivity` |
| **项目协议 (Protocol)** | `src/layout_canvas/protocol/project.py` | `.lcproj.json` 版本化权威文件边界：schema_version + migrate + 结构化 load diagnostics；裸 IR 按 v0 迁移 | `load_project`, `save_project` |
| **PDK 描述层** | `src/layout_canvas/pdk/descriptor.py` | 工艺数据化：layer map + pin purpose + 规则字典 + registry；编译器 pin label 经 `pin_label_layer` 解析 | `PDK`, `get_pdk`, `register_pdk`（内置 sky130） |
| **发生器库 (Sky130)** | `src/layout_canvas/blocks/sky130/` | Sky130 标准模拟单元参数化发生器（五管OTA、交叉耦合差分对、共源共栅电流镜、电容阵列、StrongARM、P/N护环） | `ota_5t`, `diff_pair`, `current_mirror`, `cap_array`, `strongarm`, `guard_ring` |
| **编译器 (Compiler)** | `src/layout_canvas/compiler/` | 1. 相对拓扑与几何变换求解器 (`compile.py`，`_fresh_top` 重编译安全)<br>2. 障碍物感知曼哈顿与对称差分走线器 (`router.py`)<br>3. SPICE/CDL 子电路网表生成器 (`netlist.py`)<br>4. 版图 PPA 特性抽取与优化器 (`ppa.py`, `optimizer.py`)<br>5. 层次化 cell abstract 导出 (`hierarchy.py`)<br>6. SVG 预览渲染 (`render.py`) | GDSII, OASIS, SPICE Netlist, PPA Report, Cell Abstract |
| **验证闭环 (Tools)** | `src/layout_canvas/tools/` | KLayout Batch DRC + Netgen LVS + ngspice 仿真，全部 fail-closed 四/五态契约（passed/failed/unavailable/error/refused） | `run_drc`, `run_lvs`, `simulate_design`, `run_netlist` |
| **AI 交互层 (MCP & Bridge)** | `src/layout_canvas/mcp/` | 1. JSON-RPC 2.0 stdio MCP 服务 (`server.py`)<br>2. KLayout Qt DockWidget 双向 TCP 实时通信桥 (`bridge.py`) | session 工具组 `open_design/snapshot/transact/undo/close_design/compile_session` + `inspect_connectivity/export_abstract/run_simulation/save_project/load_project` + 旧有 `generate_block/compile_ir/run_drc/run_lvs/generate_netlist/render_preview_svg/inspect_ppa` |
| **本地画布 (Web)** | `src/layout_canvas/web/` | 零依赖 stdlib HTTP review 画布：IR 编辑 → Preview/PPA/Connectivity/Abstract/Netlist 一页式检视，`layout-canvas web` 启动 | http://127.0.0.1:8080 |

---

## 2. 核心技术突破与最新交付特性

1. **GDS 端口 Text Label 物理注入**：
   - 解决纯几何导出导致 Netgen 无法提取真实端点的问题；在 `compile.py` 中自动将 `Port` 和模块引脚在对应的 `MET1_PIN (68, 16)` / `MET2_PIN (69, 16)` 打标，实现从 Block IR 到 GDS 再到 LVS 的全流程闭环。
2. **相对拓扑布局求解器 (Relative Placement)**：
   - 引入 `right_of` / `left_of` / `above` / `below` 及 `margin`、`align`，避免 LLM 手算绝对坐标导致短路重叠。
3. **敏感区域避障走线 (Obstacle-Aware Router)**：
   - 自动收集已放置器件的有源区与栅极作为障碍物边界，布线干线自动弯折规避核心区域。
4. **多模态版图预览 (Multi-Modal Layout Preview)**：
   - MCP 工具 `preview_layout` 直接生成 Base64 编码的 SVG/PNG 向量图形，供多模态大模型直接“看”到版图布局与护环结构。
5. **PPA 指标抽取与自适应优化 (PPA & Optimizer)**：
   - `inspect_ppa` 自动测量芯片面积、利用率、长宽比与总布线长度，配合 `optimizer.py` 实现闭环迭代。
6. **事务化编辑会话 (Transactional Session, 2026-09-14)**：
   - Agent 工作流从「一把生成」升级为「读快照→typed edits→原子提交」多轮迭代；
     revision 乐观锁防止陈旧 Agent 覆盖新状态，dry_run 支持不落地的预检。
7. **Fail-Closed 仿真 (ngspice)**：
   - 无晶体管级 emitter 的 block → `refused` 并点名；无 ngspice → `unavailable`；
     跑挂 → 解析日志报真实错误。绝不给假绿。
8. **契约文档化 (ADR)**：
   - `docs/adr/0001-0006` 冻结：事务边界、验证 fail-closed、显式连接性、
     项目文件协议、仿真契约、PDK 描述层与 cell abstract。
   - 对标参照：E:\analog-canvas-review-20260905（开源 analog-canvas 全量 clone）；
     落地规划见 docs/PRODUCT_PLAN_ANALOG_CANVAS.md。

---

## 3. 后续演进路线规划 (Roadmap)

- **阶段一 (进行中)**：完善全套 Sky130 基础模拟单元库，打磨开箱即用的 KLayout Salt 插件。
- **阶段二**：适配商用制程（如 TSMC 65nm / 40nm PDK 规范抽象），推出企业级 PDK Generator 插件。
- **阶段三**：云端多人协作与强化学习布局优化器，实现指标约束下的全自动多物理场协同收敛。
