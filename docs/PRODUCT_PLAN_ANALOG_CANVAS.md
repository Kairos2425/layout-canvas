# Layout-Canvas 产品落地规划（对标 Analog Canvas）

> 日期：2026-09-14
> 参照对象：`analog-canvas.tokenzhang.com` = 开源仓库 `github.com/cascode-ai/analog-canvas`
> （内部名 interactive-circuit-maker，张智帅 Zhishuai Zhang / 清华 EE → ETH Zürich）
> 本地参照副本：`E:\analog-canvas-review-20260905`（全量 clone，含 ADR/spec/测试体系）

---

## 0. 对方产品拆解（从线上 bundle + 源码仓库核实）

Analog Canvas 是一个 **local-first、connectivity-aware 的模拟电路原理图编辑器**：

- **技术栈**：pnpm monorepo，React + SVG 编辑器（`apps/editor`，PWA 可安装离线用），
  TypeScript ESM-only，Zod 做模型校验，CodeMirror 做网表文本编辑，ngspice 做仿真，
  Cloudflare Worker + Durable Objects 做云端 gallery/账号/分析。
- **核心不变量**（这是真正值得抄的东西）：
  1. `.icproj.json` Project 文件是唯一权威事实源；浏览器恢复副本不权威。
  2. `@icm/edit-engine` 是**唯一变更边界**——GUI 和 Agent 走同一个事务化编辑引擎，
     dry-run → commit，带 expected-revision 乐观锁，谁都不许绕过电气/版本/锁不变量。
  3. 连接性是显式电气事实：net membership、Junction、terminal 都是数据，
     画线交叉不产生连接——**模糊交点直接拒绝，不猜**。
  4. Agent 协议 = `capabilities / snapshot / transact / render`，没有第二套命令语言。
  5. 仿真是产品的一部分（ADR 0055）：全晶体管级可解析才跑 ngspice，
     含抽象块就拒跑并点名哪个块——**fail closed，不报假绿**。
- **工程纪律**：ADR（57 篇）> spec > product-plan > 实现；生成物一律代码生成 + drift 门禁；
  每个 commit 带 `Test-Impact:` trailer；gate 分级（focused → affected → full-delivery）。
- **生态外围**：gallery 作品发布/审核/版本历史、访问分析页、bug-report 入口、
  Razavi 教材符号目录（视觉权威 manifest）、MCP server（`analog-canvas-mcp` stdio）。

**他的管线终点 = 我们的管线起点**：他输出确定性 design netlist（SPICE/Spectre），
layout-canvas 吃 netlist/IR 产 GDS + PPA。两者天然互补。

---

## 1. 产品定位

**一句话**：面向 AI Agent 的"电路 → 版图 → 验证"全链路编译工作台——
Agent 说一句"给我一个 5 管 OTA，面积 < 4000µm²，长宽比 ≈ 1"，
系统迭代产出 DRC/LVS 干净、PPA 达标的 GDS。

差异化：Analog Canvas 停在原理图 + 仿真；商用工具（Virtuoso + SKILL）贵且不对 Agent 友好；
layout-canvas 占据 **"Agent 原生的版图编译器"** 这个空位，PPA 量化反馈闭环是护城河。

---

## 2. 从他那里直接移植的架构改造（Phase A，本仓库内）

按优先级排序，全部可在现有 Python 代码上落地：

| # | 改造项 | 对应他的实现 | 落点 |
|---|---|---|---|
| A1 | **事务化编辑引擎**：现有 `compile` 是单次函数；改为 session 持有 `Design`，提供 `snapshot()` / `transact(edits, expected_revision)` / `undo`，dry-run 先返回诊断不落地 | `packages/edit-engine` | 新增 `src/layout_canvas/engine/session.py` |
| A2 | **连接性索引（纯读投影层）**：编译前给 Agent 返回每 instance 的 pin→net 解析表、未连接 pin、悬空 net——他管这个叫 `@icm/derived`，是"read electrical facts before drawing" | `packages/derived` | 新增 `src/layout_canvas/derived/connectivity.py` |
| A3 | **fail closed 的验证语义**：DRC/LVS 工具缺失时返回机器可读 `blocked`/`unavailable`，绝不报假 clean（你 WORK_PLAN 里已写了这条 acceptance bar，把它做成协议层硬约束） | ADR 0055 | `tools/drc.py` / `tools/lvs.py` + MCP 返回 envelope |
| A4 | **诊断 envelope 统一格式**：`{status, diagnostics[], object_locator, revision}`——Agent 每条响应都能定位到具体对象 | ADR 0015 | MCP `server.py` 响应结构 |
| A5 | **项目文件权威 + 迁移边界**：`.lcproj.json`（或现 IR JSON）加 `schema_version`，加载走单独 `protocol` 模块做 migrate + load diagnostics | `packages/project-protocol` | 新增 `src/layout_canvas/protocol/` |
| A6 | **MCP 工具对齐 Agent API 形态**：现有 5 个工具扩展为 `capabilities / open_design / snapshot / transact / preview / inspect_ppa / run_verification / export`——保留旧工具做兼容别名 | `apps/mcp-server` + `docs/agent/*.md` | `mcp/server.py` |
| A7 | **工程纪律移植**（成本最低收益最大）：`docs/adr/` 起步写 3 篇（会话事务边界、连接性显式化、验证 fail-closed）；commit 加 `Test-Impact:` trailer；spec 权威层级写进 CONTRIBUTING | `docs/adr/` + AGENTS.md | `docs/` + `CONTRIBUTING.md` |

---

## 3. 产品化路线（Phase B–D）

### Phase B：闭环可用（对标他的"simulation is part of the product"）

- **B1 仿真环**：编译产 netlist → ngspice 仿真（.op/.tran/.ac），结果进 PPA 报告。
  规则照抄他：**每个 instance 都能解析到 PDK 模型才跑，否则拒跑并点名未解析块**。
- **B2 Agent 迭代环完整跑通**：`snapshot → transact(调整 IR) → compile → inspect_ppa →
  run_verification` 的多轮 loop，配合 optimizer 做 Pareto 前沿（你已有 inspect_ppa 的种子）。
- **B3 KLayout Salt 插件打磨**：`mcp/bridge.py` 的 TCP 桥已是独有优势（他都没有），
  包装成一键安装的 Salt 包，做到"KLayout 里实时看 Agent 画版图"的 demo 效果。

### Phase C：从引擎到产品

- **C1 Web 画布**：不是抄他的原理图编辑器——做一个**版图 review 画布**：
  浏览器渲染编译出的 cell（SVG，颜色按 layer），叠 PPA 热图/DRC marker，
  人审 + Agent 改的同屏界面。技术栈可复用他的模式：React + SVG + local-first + PWA。
- **C2 层次化编译**：他 ADR 0025 的 formal ports/hierarchy 思路搬到版图侧——
  cell 编译一次产 abstract view（pin 位置 + 边界 + blockage），上层复用，
  这是"编译方法"的论文级卖点（他自己 ISCAS'25 就是 hierarchical compilation）。
- **C3 PDK 抽象层**：Sky130 → 可配置 PDK descriptor（layer map + design rules +
  device generators），为商用工艺扩展留口（对应你 ARCH_MEMORY 阶段二）。

### Phase D：商业化闭环

- **D1 Gallery/共享**（抄他的 Worker + DO 模式，也可先纯本地）：
  发布的 `.lcproj` + 渲染图 + PPA 卡片，社区可参考复用 topology。
- **D2 账号 + 云项目**：后做；先把 local-first 和 MCP 分发打透。
- **D3 收费点**：免费 = Sky130 + 基础 blocks + 本地验证；
  付费 = 商用 PDK generator、层次编译、云端协作/版本历史、批量 Pareto 扫参。

---

## 4. 立刻可执行的下一步（建议顺序）

1. `git status` 确认 worktree 干净；把 A1 session/transact 骨架建起来（带 revision 乐观锁）。
2. A3 fail-closed 验证语义 + A4 envelope——改动小、立刻提升 Agent 体验。
3. A2 connectivity 投影——让 Agent 在动 IR 之前能"读到电气事实"。
4. 写 3 篇 ADR（A7），把 A1/A2/A3 的契约冻结。
5. B1 ngspice 接入的最小路径：`.op` 冒烟 + 未解析 instance 拒跑。

每个目标走他自己的纪律：bounded target → 实现 → 风险成比例的验证 →
带 `Test-Impact:` 的 commit。

---

## 5. 参考锚点（读代码/文档的入口）

- 变更边界设计：`E:\analog-canvas-review-20260905\packages\edit-engine\`
- Agent 协议：`docs\specs\agent-api.md`、`docs\agent\workflow.md`（8 步 layout loop）
- 验证 fail-closed：`docs\adr\0055-simulation-is-part-of-the-product.md`
- 连接性哲学：`docs\specs\connectivity-and-routing.md`、ADR 0052
- 发布/gallery：`worker\`、`docs\deployment.md`、ADR 0057
- 文档权威层级：`docs\current\README.md`、`AGENTS.md`
