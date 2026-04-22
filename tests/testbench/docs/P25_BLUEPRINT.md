# P25 外部事件注入 · 新系统对话/记忆影响测试 — 蓝图

> **Single Source of Truth**. 本文件是 P25 阶段的**唯一权威规格**. `PLAN.md` 条目 `p25_external_events` 只是索引; `PROGRESS.md` 和 `AGENT_NOTES.md` 里的描述都应以本文件为准.
>
> **动因 (2026-04-22)**: P24 Day 9 主程序同步盘点时, 用户就道具交互 (PR #769 `4b504d4` `prompts_avatar_interaction.py` 1196 行) 与我二轮对齐 **testbench 的根本定位**:
> > *"相关系统对于记忆系统是有影响的. 我们的测试生态在这个问题上的任务是这样的, 我们需要模拟出道具交互的相关行为, 然后结合记忆系统, 评估 AI 模型的反应和道具交互相关系统对短期和长期记忆 (事实和反思) 的具体影响. 所以我们不需要把实时 LLM 流式会话这些系统做进来, 而是要做模型对话 prompt 方面的复现, 保证这个道具交互系统在模型回复和记忆系统处理上是稳健的, 这是我们作为测试生态的一个重要任务 (主程序新就位的相关系统对于对话和记忆会不会有影响, 和这些系统的交互如何?)"*
>
> 一轮评估定性 "三重架构不兼容 → explicit out-of-scope" 被**翻转**为: "**pure helper 层必须接入, 同族系统一并接入**". 工作量独立且复杂, 单开 P25 专门交付 (原 P25 README 顺延 P26).

---

## 1. 目标与边界

### 1.1 核心目标

让 testbench 成为**真正的 "主程序新就位系统对对话/记忆影响测试生态"**. 具体地, 回答**四个问题**:

1. **模型回复层**: 当主程序的"外部事件触发型 prompt" (avatar interaction instruction / agent callback notification / proactive chat prompt) 临时注入到系统消息时, 模型生成的回复是否稳健? 有没有违反 persona / 忽视 instruction / 幻觉等情况?
2. **短期记忆层**: 这些事件产出的 memory note (如 `[主人摸了摸你的头]`) 或 assistant 回复进入 recent history 后, `recent.compress` 的摘要是否合理地识别 "情感互动" vs "系统噪音"?
3. **长期记忆层**: `facts.extract` 是否从高频 avatar 互动中抽出合理的人设特征 (e.g. "主人喜欢摸头"), `reflect` 能否把多次互动概括成反思?
4. **去重/融合层**: avatar interaction 的 8000ms 去重窗口 + rank upgrade 策略 (1→2→3 升级合并为 `[主人连续摸了摸你的头]`) 在本地 cache 中是否正确实现?

### 1.2 范围内 (In-Scope, 3 类)

全部是**主程序中"运行时外部触发 + 临时 prompt 注入 + 写 memory"**模式的系统, **pure 部分可复用率接近 100%**:

| # | 系统 | 主程序触发 | 复用的主程序 pure 层 | 影响面 |
|---|---|---|---|---|
| **A** | Avatar Interaction | 前端 avatar 点击 (PR #769) | `config.prompts_avatar_interaction.py` 全部 9 helper + 7 常量表 + `main_logic.cross_server._should_persist_avatar_interaction_memory` | `[主人摸了摸你的头]` 作 role=user + LLM 反应作 role=assistant 成对入 recent |
| **B** | Agent Callback | 后台 agent 任务完成 | `config.prompts_sys.AGENT_CALLBACK_NOTIFICATION` 5 语言版本 + `main_logic.core.LLMSessionManager.drain_agent_callbacks_for_llm` 的拼接逻辑 | "======[系统通知：后台任务]" instruction 作临时 system 片段 + LLM 回复进 recent |
| **C** | Proactive Chat | 定时 / 队列空闲 | `config.prompts_proactive.py` 全部 5 个 `proactive_chat_prompt*` 变体 + dispatch table + memory/meme/music 子 getter | LLM 自发产出 assistant 回复 (或 `[PASS]` 跳过) 进 recent |

### 1.3 范围外 (Out-of-Scope, 明文约束)

**避免 P25 成为 "无限扩张筐" (与 P22/P22.1/P24 一致的哲学)**:

- **不实现实时流基础设施**: 不引入 `OmniOfflineClient.prompt_ephemeral` / 多进程 `sync_message_queue` / WebSocket `/avatar_interaction` / `_proactive_expected_sid` contextvar / `LLMSessionManager` 状态机 / SID race guard — 这些是"实时投递机制", 与"影响评估"测试目标**正交**, testbench 的同步 `ChatOpenAI.astream` HTTP+SSE 架构**不重现**.
- **不实现冷却计时器**: 主程序 `handle_avatar_interaction` 的 600ms / 1500ms 冷却窗口是给实时前端防抖用的, testbench 是 tester-driven 手动触发, **不需要**. dedupe 窗口 (8000ms) 是语义层的必须保留.
- **不实现 emotion 分析子系统**: `config.prompts_emotion.py` 是独立的情绪分析 prompt, 不走"运行时注入 + 写 memory"模式, 与 P25 无关.
- **不做多会话并发 avatar 事件隔离**: testbench 本身只有单活跃会话 (PR #769 的多会话 dedupe cache 隔离也只在多会话场景发生, testbench 天然单会话无需考虑).
- **不改动主程序**: 全部复用 `config/prompts_*.py` 与 `main_logic/cross_server.py` 的 pure 函数, 通过 `from ... import` 引入, **零侵入**.
- **不做性能压测**: 手动点击一次事件 → 跑一次 LLM → 写一对 message 的流程已足够覆盖"影响评估"目标, 不涉及吞吐/并发测试.

---

## 2. 设计原则

### 2.1 语义契约 vs 运行时机制

> **权威来源**: 本条原则已独立归档为跨项目方法论, 详见
> [`LESSONS_LEARNED §1.6`](LESSONS_LEARNED.md#16-语义契约-vs-运行时机制-测试生态-oos-判据) +
> 全局 cursor skill `semantic-contract-vs-runtime-mechanism`. 本阶段直接应用.

**本阶段最核心的设计原则**, 来自 P24 Day 9 的二轮评估教训:

- 主程序的"运行时 prompt 注入"系统 = **语义契约** (prompt 模板 + memory note 模板 + dedupe 策略) + **运行时机制** (实时流 / 多进程 queue / WebSocket / contextvar race guard).
- testbench 作为"影响评估测试生态", 只需要复现**语义契约**. 运行时机制是"投递方式", 与"影响是什么"**正交**.
- 复现语义契约 = import 主程序的 pure helper + 在 testbench 的同步架构里薄封装一层调用.

这条原则**超越本阶段**, 应作为未来**所有**"新主程序系统 → testbench 影响评估"对接的默认方法. 记入 AGENT_NOTES 和 cursor skill.

### 2.2 Tester-Driven, 不自动触发

**与 testbench 现有 `stage_coordinator` / `memory_runner` 哲学一致**: 永远只**提供**模拟入口, 永远**不自动**触发. Tester 明确点击 "模拟 avatar poke" / "模拟 agent callback done" 后, 系统才开始跑 LLM + 写 memory. 原因:

- testbench 是**离线测试工具**, 目标是让 tester "控制变量看结果", 不是"跑真实运行时".
- 自动触发会引入时间相关的不确定性 (定时器, 队列, 随机性), 破坏 "**同样输入 → 同样输出** " 的可复现测试性质.

### 2.3 Choke-point Consistency

所有"写 session.messages"的路径**必须**走 `pipeline/messages_writer.append_message(session, msg, on_violation=...)`. avatar 事件产出的 user note + assistant reply 也不例外 — 继承 Day 2 抽出的单调时间 guard 与 coerce/warn/raise 策略. 不允许绕过去直接 `session.messages.append(...)`.

### 2.4 Dual-Mode Memory Write

用户决策 `persist_strategy = dual_mode`: 默认只写 `session.messages`, **可选** mirror 到 `recent.json`.

- **默认 (session_only)**: 对齐 testbench 现有 chat_turn 哲学 — 不自动同步 memory, 由 tester 手动触发 `recent.compress` / `facts.extract` / `reflect` 才看下游影响.
- **可选 (mirror_recent)**: tester 在 UI 上勾选 "also mirror to recent.json", 事件直写 recent — 更接近主程序 `sync_connector_process` 的自动同步行为, 适合测 "高频 avatar → 立即触发 recent 阈值压缩" 这类场景.

两种模式都**必走** `append_message` choke-point + 虚拟时钟单调 guard.

### 2.5 LLM Invocation = Always

用户决策 `llm_mock_strategy = always_llm`: 每次模拟事件**都跑真实 LLM** 产出 assistant reply (使用 session 当前 `model_config.chat` 组).

- 对齐主程序行为 (主程序每次 avatar / agent_callback / proactive 都真的调 LLM).
- 让 "影响评估" 有真实数据 (否则只模拟 note 不模拟 reply, 无法评估 model 回复稳健性).
- 代价: 每次事件消耗真实 token. 不提供 "skip LLM" 开关 — 要省 token tester 自己调 `ChatMock` endpoint (已有基础设施).

### 2.6 三类一个端点

同模式, 同抽象:

```
POST /api/session/external-event
body: { kind: "avatar_interaction" | "agent_callback" | "proactive", payload: {...} }
```

内部按 `kind` 分派到三个 handler. 避免前端 / 后端各写三遍相似代码.

---

## 3. 分阶段交付

### Day 1 — 后端 handler + router 骨架 (~0.8 天)

**交付物**:
- `tests/testbench/pipeline/external_events.py` (~250 行):
  - `simulate_avatar_interaction(session, payload, mirror_recent=False) -> SimulationResult`
  - `simulate_agent_callback(session, payload, mirror_recent=False) -> SimulationResult`
  - `simulate_proactive(session, payload, mirror_recent=False) -> SimulationResult`
  - `SimulationResult` dataclass: `{accepted, reason, instruction, memory_pair, persisted, dedupe_info, assistant_reply, diagnostics_op}`
  - 内嵌 `_AvatarDedupeCache` (per-session dict, 8000ms 窗口 + rank upgrade, 复用 `main_logic.cross_server._should_persist_avatar_interaction_memory` 的**纯函数版本** — 如无纯函数则先 copy 到 `pipeline/avatar_dedupe.py` 并加文档说明)
  - 导入 `config.prompts_avatar_interaction` 全部 pure helper (`_normalize_avatar_interaction_payload`, `_build_avatar_interaction_instruction`, `_build_avatar_interaction_memory_meta`, etc.)
  - 三个 handler 共用的 LLM 调用脚手架 (复用 `chat_runner.resolve_group_config` + `create_chat_llm`)

- `tests/testbench/routers/external_event_router.py` (~90 行):
  - `POST /api/session/external-event`:
    - 请求 body 用 Pydantic `ExternalEventRequest{kind, payload, mirror_recent=False}`
    - SessionState.BUSY 短锁 (同 chat_turn)
    - 按 `kind` 分派, 返回 `SimulationResult` JSON
  - `GET /api/session/external-event/dedupe-info` — 可选调试端点, 查当前 session 的 avatar dedupe cache 状态 (哪些 key 还在窗口内)

- `tests/testbench/server.py` 注册新 router

- `tests/testbench/pipeline/diagnostics_ops.py` 新增 3 个 op:
  - `AVATAR_INTERACTION_SIMULATED` (level=info)
  - `AGENT_CALLBACK_SIMULATED` (level=info)
  - `PROACTIVE_SIMULATED` (level=info)
  - 每个事件 handler 成功后入 diagnostics ring

**验收点**:
- `curl POST /api/session/external-event` 三类 kind 各走一遍, 返回 200 + `SimulationResult` 有非空 instruction + memory_pair + assistant_reply
- Session messages 追加 2 条 (user note + assistant reply for avatar, system instruction + assistant reply for callback/proactive)
- Diagnostics ring 有对应 op entry
- 连续点 avatar fist poke 10 次, dedupe 内部 cache 只保留 1 个 key (rank 升级), session.messages 只追加 1 组 (后续被 dedupe 过滤), 响应 `persisted=false` + `dedupe_info={reason: "within_window", rank_upgraded: true, rank: 10}`

### Day 2 — 前端面板 + i18n + CSS (~1 天)

**交付物**:
- `tests/testbench/static/ui/chat/external_events_panel.js` (~300 行):
  - 侧边 / 抽屉式折叠面板, 3 个子表单 tab:
    - **Avatar**: tool_id (dropdown: hand/lollipop/fist/hammer) + action_id (dropdown 动态根据 tool_id 过滤合法 action) + intensity (dropdown: normal/rapid/burst/easter_egg, 根据 tool_id+action_id 过滤合法组合) + touch_zone (dropdown: ear/head/face/body, 仅 fist/hammer 启用) + easter_egg checkbox + text_context (文本框 max 80 字符)
    - **Agent Callback**: summary (多行) + detail (多行) + status (dropdown: completed/failed/partial)
    - **Proactive**: trigger_reason (dropdown: idle_timer/queue_empty/manual) + trending_content (JSON 编辑器) + use_session_memory_context (checkbox 默认勾选)
  - 每个表单底部:
    - "Invoke event" 按钮 (主操作)
    - "Mirror to recent.json" checkbox (默认不勾)
  - 提交后分 4 栏实时展示:
    1. **Instruction preview** (产出的 prompt 片段, 折叠式)
    2. **Memory pair preview** (`{role:user, content: "[主人摸了摸你的头]"}` + `{role:assistant, content: LLM 回复}`)
    3. **Persistence decision** (persisted=true/false + dedupe_info 详情, e.g. "8000ms 窗口内已存在 key `hand_touch_head_normal`, rank 升级 2→3")
    4. **LLM reply bubble** (直接渲染 assistant reply 的 markdown)
  - 面板右上角有 "Clear dedupe cache" 按钮 (调 `/api/session/external-event/dedupe-reset` 端点 — Day 1 追加)
  - Abort 支持 (api.js signal/abort, 中途取消 LLM 调用)

- `tests/testbench/static/core/i18n.js` 新增 25+ keys:
  - `external_events.panel.title`, `external_events.tab.avatar`, ...
  - 每个 tool_id / action_id / intensity / touch_zone / trigger_reason 的 label

- `tests/testbench/static/ui/app.css` 新增 `.external-events-panel` 样式 (折叠式 + 4 栏布局 + memory pair 的 user/assistant 颜色区分)

- Chat 工作区主布局 `ui/chat/layout.js` 加入 panel (收起状态不占空间)

**验收点**:
- 手动点 Avatar hand + touch + head + normal 提交, 看到完整 4 栏输出, assistant reply 是真实 LLM 内容
- 切到 Agent Callback, 填 summary "已完成下载", 提交, 看到 LLM 回复中自然提及下载完成
- 切到 Proactive, 填 trending_content `["某条热搜"]`, 提交, 看到 LLM 回复或 `[PASS]`
- 勾 "Mirror to recent.json" 提交一次 avatar poke, 跑一次 `recent.compress` trigger, preview 抽屉展示的摘要**包含** 该 poke 的系统 note
- 连点 10 次 avatar 同 payload, 面板显示 "dedupe window: 6.2s remaining, rank upgraded to 10", 只有第 1 次 persisted=true

### Day 3 — Smoke + 回归 + 文档 (~0.7 天)

**交付物**:
- `tests/testbench/smoke/p25_external_events_smoke.py` (~300 行):
  - **去重函数单测**: `_should_persist_avatar_interaction_memory` 的 matrix (首次落 / 窗口内同 key rank 升 / 窗口内同 key rank 降 / 窗口外同 key / 不同 key 不干扰 / 空 note 拒绝 / 非法 rank 默认 1)
  - **payload 校验单测**: `_normalize_avatar_interaction_payload` 的 matrix (所有合法 tool×action×intensity 组合 / 非法 intensity 被纠正 / 非法 touch_zone 对 hand/lollipop 被拒 / text_context 超 80 字符截断 / easter_egg 非 bool 默认 false)
  - **memory_meta 产出单测**: `_build_avatar_interaction_memory_meta` 对齐主程序 `tests/unit/test_avatar_interaction_memory_contract.py` 的期望 (5 语言 × 4 tool × rank 1/2/3/升级合并文案)
  - **三类 handler 闭环** (mock LLM, 断言 session.messages 追加正确 + diagnostics entry + 去重工作):
    - simulate_avatar_interaction 正常 path + 冲突 path + dedupe path + mirror_recent path
    - simulate_agent_callback 正常 path + 空 summary 拒绝 path
    - simulate_proactive 正常 path + `[PASS]` path (不写 message)
  - **router 集成**: TestClient POST `/api/session/external-event` 三类各一次 + 非法 kind 返回 400 + 无 session 返回 404

- 回归既有 smoke: `p21_*` / `p22_*` / `p23_*` / `p24_*` 全绿
- 跑用户手测一轮"全链路影响评估":
  1. 创建新 session + 导入一个基础 persona
  2. 点 10 次 avatar fist burst 到 head (应该最终只 persisted 1 条 rank=10)
  3. 点 agent callback "已完成播放列表整理" (应产生一条 assistant 自然提及)
  4. 点 proactive trigger_idle (应产生一条 assistant 自发搭话或 `[PASS]`)
  5. 手动 trigger `recent.compress`, 看摘要如何处理这些事件
  6. 手动 trigger `facts.extract`, 看是否抽出"主人喜欢摸头"类特征
  7. 手动 trigger `reflect`, 看反思层的概括
  8. 记录实测结果到 `p24_integration_report.md` 新增 §1.3 P25 外部事件 subsection (或单开 `p25_events_report.md`)

- `tests/testbench/docs/external_events_guide.md` (~200 行) tester 手册:
  - 每类事件的使用场景
  - payload 字段含义
  - dedupe 策略解读
  - 期望观察的影响 (recent / facts / reflection 三层)
  - 已知限制 (不触发实时流 / 不触发多会话隔离 / 冷却窗口不复现)

- ~~更新 `AGENT_NOTES.md` 加 L25 "语义契约 vs 运行时机制" 元教训~~ — **已于 P25 立项时完成**, 详见 [AGENT_NOTES §4.27 #108](AGENT_NOTES.md#108-2026-04-22-p24-day-8-9-完结--day-9-e-二轮翻转--p25-立项-用户手测验收--主程序同步五项盘点全零行动--道具交互翻转--三条新元教训-l23l24l25) (L23/L24/L25 三条) + [LESSONS_LEARNED §1.6](LESSONS_LEARNED.md#16-语义契约-vs-运行时机制-测试生态-oos-判据) + [LESSONS_LEARNED §2.9A](LESSONS_LEARNED.md#29a-property-动态计算-vs-直接赋值-路径字段的沙盒mock-友好设计) (@property 动态计算)
- ~~`~/.cursor/skills/testbench-external-system-adapter`~~ — **已于 P25 立项时完成**, 以 `semantic-contract-vs-runtime-mechanism` 命名归档 (放置于 `~/.cursor/skills/`, 不依赖本项目, 其它项目的测试生态对接方法论也适用)

**验收点**:
- 全部新 smoke + 回归 smoke 绿
- tester 手册可独立读懂
- 至少一组"10 次 avatar 事件 → recent.compress → facts.extract → reflect" 全链路跑通并记录影响观察

---

## 4. 关键技术决策

### 4.1 Dedupe cache 放在 session 还是 module level?

**决策: session 级** (`session._avatar_dedupe_cache: dict`).

- 主程序 `cross_server.py::avatar_interaction_memory_cache` 是 per-`lanlan_name` 的 (多会话就是多角色, 隔离自然).
- testbench 单会话模型, 但每次用户新建 / load session 要重置 cache (避免跨 session 污染).
- 放 session 级直接得到正确隔离语义 + 不需要额外的 clear 钩子.

### 4.2 handle_response_discarded 怎么办?

**决策: 不复现**.

- 主程序这个 handler 处理的是 "LLM 回复被 circuit breaker / rate limit 丢弃" 的场景, 需要 pending_turn_meta 状态机配合.
- testbench 的 LLM 调用是同步 await, 失败就抛异常 → handler 层捕获 → 返回 `SimulationResult{accepted=false, reason="llm_error: ...", ...}`. 不入 messages / 不入 dedupe cache.
- 等价语义: 主程序的 rollback dedupe entry 行为, 在 testbench = "失败就不进入 cache" (天然回滚).

### 4.3 Proactive 的 memory_context 从哪来?

**决策: 默认用 session 当前 recent**, 可选用户手填.

- 主程序 `proactive_chat_prompt` 的 `{memory_context}` 占位来自 `memory_server` 的 `/cache` 查询.
- testbench 直接走 `prompt_builder._build_memory_context_structured_with_clock` 取到当前 session 的 memory_context (复用 preview 逻辑).
- 前端给个 "override memory context" textarea (默认隐藏) 让 tester 手动指定 (for edge case 测试).

### 4.4 与 P24 Day 10 smoke 的边界?

**决策: 不合并**.

- P24 Day 10 `p24_integration_smoke.py` 焦点是"端到端核心流程" (Setup → Chat → Memory → Evaluation → Save/Load).
- P25 `p25_external_events_smoke.py` 焦点是 "外部事件独立闭环". 两者不耦合.
- 但 P25 完成后可以在 P24 integration 报告加一个 §1.3 "外部事件影响观察" 补录.

### 4.5 Avatar memory meta 的 5 语言 label 怎么测?

**决策: 只对**用户当前 session `persona.language` **测, 不矩阵 5 语言**.

- 主程序的 `tests/unit/test_avatar_interaction_memory_contract.py` 已经 matrix 过 5 语言.
- testbench smoke 只需验证 "当前 session 语言 → memory meta 正确" 即可 (等价于信任主程序的 unit 测试).
- 如果将来发现某语言有 bug 再回头加.

---

## 5. 风险与缓解

| 风险 | 可能性 | 影响 | 缓解 |
|---|---|---|---|
| 主程序 `prompts_avatar_interaction` 常量改动破坏 testbench | 低 | 中 | testbench 只 import helper function, 不 copy 常量; 主程序改动会**立即反映**到 testbench (保持同步) |
| `_should_persist_avatar_interaction_memory` 非公开 API 被主程序改签名 | 中 | 低 | 在 Day 1 把该函数**复制**到 `tests/testbench/pipeline/avatar_dedupe.py` (加"来自 cross_server.py, 保持同步"的文档), 主程序改动时 P24 同步盘点会捕获 |
| LLM 消耗 token 过多 | 中 | 中 | UI 面板默认禁用 "auto-trigger on event queue" (无此功能也是缓解); tester 有意识才点, 一次点一次跑 |
| 三类系统语义差异大导致抽象泄露 | 低 | 低 | 用 `kind` 字段分派, 三个 handler 各自独立, 公共部分只有"写 messages + diagnostics entry"这一薄层 |
| dedupe rank upgrade 文案与主程序不一致 | 中 | 低 | smoke 直接 import 主程序 `_build_avatar_interaction_memory_meta` 对齐, 不另写文案 |
| 前端面板复杂度高 | 中 | 中 | 沿用 `page_schemas.js` / `page_memory_facts.js` 现有 CRUD 面板样式, 不引入新组件库 |

---

## 6. 与 P24 / P26 的关系

### 6.1 依赖 P24

- 需要 P24 Day 8 Diagnostics ring buffer 稳定 (P25 新增 3 个 op 需要入 ring)
- 需要 P24 Day 9 主程序同步盘点已完成 (证明 `config.prompts_avatar_interaction` + `prompts_sys` + `prompts_proactive` 稳定无大改)
- 需要 P24 Day 10 smoke 稳定 (避免 P25 动代码时回归既有绿灯被误伤)

### 6.2 影响 P26

- P26 README 需覆盖 "外部事件模拟" 使用说明 + 每类事件的影响评估方法
- `external_events_guide.md` (P25 Day 3 交付) 会被 P26 README 整合或引用
- P26 里的 "限制声明" 需新增 "外部事件不复现实时流 / 不复现多会话隔离 / 冷却窗口不复现" 三条

### 6.3 与 P22 Autosave 的兼容

- P25 事件产出的 messages 走 `append_message` 后**自动**被 autosave 拿住 (autosave 订阅 messages 变动).
- dedupe cache (`session._avatar_dedupe_cache`) 是 in-memory, **不持久化** — load session 后 cache 清空 (符合直觉: load 后应该可以重新开始测试, 不继承上次 cache 状态).

---

## 7. 工作量估算 · 总计 2.5-3 天

| Day | 任务 | 人日 |
|---|---|---|
| 1 | 后端 handler + router + dedupe + diagnostics op | 0.8 |
| 2 | 前端 panel + i18n + CSS + layout 接入 | 1.0 |
| 3 | smoke + 回归 + tester 手册 + AGENT_NOTES + cursor skill + 手测一轮 | 0.7 |

**Buffer**: +0.5 天给 "用户手测反馈一轮" (P24 经验表明每轮用户手测都会出 2-3 个 UI 细节 bug).

**风险调整后估计**: 3-3.5 天.

---

## 8. 开工门禁

进入 P25 前必须满足:

- [x] P24 Day 1-9 完成并用户验收 (Day 1-8 已 ✅, Day 9 已 ✅ 2026-04-22)
- [ ] P24 Day 10 smoke 交付完成 + 回归绿
- [ ] 用户对 P25_BLUEPRINT 本文档审读通过 (or 提出修订)

满足以上 3 条后启动 P25 Day 1.

---

## 9. 交付后入档事项

P25 完成后必须在以下文档中同步:

- `PLAN.md` — todos 条目 `p25_external_events` 状态 done + 快照段更新进度
- `PROGRESS.md` — 阶段总览表 P25 done + 阶段详情展开 P25 实际交付 + changelog 条目
- `AGENT_NOTES.md` — L23/L24/L25 元教训已在 P25 立项时落地 (§4.27 #108), P25 交付时如有新踩坑追加到 §4.27
- ~~`.cursor/skills-cursor/testbench-external-system-adapter/SKILL.md` (新文件)~~ — **P25 立项时已完成**, 归档为 `~/.cursor/skills/semantic-contract-vs-runtime-mechanism/SKILL.md`
- `external_events_guide.md` (新文件) — tester 手册
- `p24_integration_report.md` §1.3 或新建 `p25_events_report.md` — 外部事件影响实测观察

---

**变更日志**:
- 2026-04-22 初版创建 (基于 P24 Day 9 二轮评估结论 + 用户 4 轮 AskQuestion 决策)
