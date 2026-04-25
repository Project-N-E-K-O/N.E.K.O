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

| # | 系统 | 主程序触发 | 复用的主程序 pure 层 | 影响面 (testbench 复现) |
|---|---|---|---|---|
| **A** | Avatar Interaction | 前端 avatar 点击 (PR #769) | `config.prompts_avatar_interaction.py` 全部 9 helper + 7 常量表 + `main_logic.cross_server._should_persist_avatar_interaction_memory` (按 L30 **copy** 到 `pipeline/avatar_dedupe.py` + drift smoke, 不跨 package import — 详见 §4.5 + §A.8 #1) | `[主人摸了摸你的头]` 作 role=user + LLM 反应作 role=assistant 成对产出; **默认** session_only = 仅入 `session.messages` (tester 可观察事件在对话视图的直接效果), **opt-in** mirror_to_recent = 复现主程序独立 memory path 语义额外入 `memory/recent.json` |
| **B** | Agent Callback | 后台 agent 任务完成 | `config.prompts_sys.AGENT_CALLBACK_NOTIFICATION` 5 语言版本 + `drain_agent_callbacks_for_llm` 的拼接逻辑 (拼接策略按语义契约层复用, 主程序多进程 queue 不复现) | **Instruction 只入本次 LLM wire 不入 session.messages** (对齐主程序 `prompt_ephemeral(persist_response=False)` 语义); LLM 回复经 `append_message` 写入 `session.messages`; **默认** session_only, **opt-in** mirror_to_recent |
| **C** | Proactive Chat | 定时 / 队列空闲 | `config.prompts_proactive.py` 全部 5 个 `proactive_chat_prompt*` 变体 + dispatch table + memory/meme/music 子 getter | LLM 自发产出 assistant 回复 (或 `[PASS]` 跳过); **Instruction 只入 wire**, **非 [PASS] 回复**经 `append_message` 写入 `session.messages`; **默认** session_only, **opt-in** mirror_to_recent |

**关键语义锚点** (三类一致, §2.6 保持):

- Instruction 片段 **永远不入** `session.messages` (只出现在本次 LLM 调用 wire, 对齐主程序 `prompt_ephemeral` 不持久化指令); `SimulationResult.instruction` 字段仅作 UI preview 展示.
- Assistant reply **必经** `pipeline/messages_writer.append_message` choke-point 写入 `session.messages` (§2.3 + §3A A17).
- Avatar 语义层还有独立的 "user-role memory note + assistant reply **成对**" 概念 (主程序 `cross_server.py` L474-497 `/cache` 独立路径). 在 testbench 里, 此成对结构的承载主体**仍是** `session.messages` (默认 session_only), 仅在 opt-in mirror_to_recent 开关下额外写一份到 `memory/recent.json`.
- **三类默认一致**: default = session_only; opt-in = mirror_to_recent. 不按主程序运行时行为 (是否走 `/cache` / 是否走 `had_user_input_this_turn`) 在默认值上分化三类, 保 §2.6 "三类一个抽象".

### 1.3 范围外 (Out-of-Scope, 明文约束)

**避免 P25 成为 "无限扩张筐" (与 P22/P22.1/P24 一致的哲学)**:

- **不实现实时流基础设施**: 不引入 `OmniOfflineClient.prompt_ephemeral` / 多进程 `sync_message_queue` / WebSocket `/avatar_interaction` / `_proactive_expected_sid` contextvar / `LLMSessionManager` 状态机 / SID race guard — 这些是"实时投递机制", 与"影响评估"测试目标**正交**, testbench 的同步 `ChatOpenAI.astream` HTTP+SSE 架构**不重现**.
- **冷却三分类明示** (L29 候选元教训, R1a 落地): 主程序存在三种"N 秒内不复触发"行为, testbench 按类分别处置 —
  - (a) **实时流抖动冷却** (`handle_avatar_interaction` 600ms / 1500ms 帧合并窗口): 运行时机制, **不复现** (tester-driven 无需防抖).
  - (b) **语义去重冷却** (`_should_persist_avatar_interaction_memory` 8000ms 窗口 + rank upgrade): **语义契约, 必保** (独立实现在 `pipeline/avatar_dedupe.py`, 见 §4.5).
  - (c) **N 秒窗口禁复触发** (`prepare_proactive_delivery` `min_idle_secs` 阈值): 运行时机制, **不复现** (上游 2026-04-22 merge 把阈值从 30s 改到 10s, 与 testbench 无关).
- **不复现连续主动搭话合并** (`merge_unsynced_tail_assistants`, cross_server.py L80-118 "精简 N 条连续未同步主动搭话, 仅保留最后一条"): 是主程序实时流场景的运行时优化, 触发前提是"多条 proactive/agent_callback assistant reply 未被任何 user turn 打断". testbench 里 tester 手工点击间隔天然 ≥ 人类反应时间, 视同 "user 在中间 interleave 了操作", 不满足前提; 且 merge 本身是运行时机制 (§2.1 判定), 不复现 — 每次 `simulate_*` 成功都是独立 turn, session.messages 逐条追加 (R9 归并自此).
- **不实现 emotion 分析子系统**: `config.prompts_emotion.py` 是独立的情绪分析 prompt, 不走"运行时注入 + 写 memory"模式, 与 P25 无关.
- **不做多会话并发 avatar 事件隔离**: testbench 本身只有单活跃会话 (PR #769 的多会话 dedupe cache 隔离也只在多会话场景发生, testbench 天然单会话无需考虑).
- **不改动主程序**: 全部复用 `config/prompts_*.py` 的 pure 函数, 通过 `from ... import` 引入; `main_logic.cross_server` 的 `_should_persist_avatar_interaction_memory` 按 L30 **copy 而非 import** (`pipeline/avatar_dedupe.py`, 顶部标注来源 + Day 3 `p25_avatar_dedupe_drift_smoke.py` 字节级 hash compare 守漂移). testbench 不 import `main_logic/*`, 保持历史边界纪律.
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

用户决策 `persist_strategy = dual_mode`: **三类一致** — 默认 `session_only`, **可选** `mirror_to_recent`. (此处"三类一致"守 §2.6 "三类一个抽象", 不按主程序 runtime 行为 `had_user_input_this_turn` 把三类默认值拆开. 详细漂移诊断见 §A.7.)

- **默认 (session_only)**: 对齐 testbench 现有 chat_turn 哲学 — Assistant reply 经 `append_message` 仅写 `session.messages`, 不自动同步到 `memory/recent.json`. 由 tester 手动触发 `recent.compress` / `facts.extract` / `reflect` 才看下游影响. 此默认的**初衷是 testbench 工作流** (tester 先在 session.messages 看 note + reply, 再手动 trigger compress/extract/reflect 观察下游), **不是**对齐主程序 `/cache` 是否自动写.
- **可选 (mirror_to_recent)**: tester 在 UI 上勾选 "also mirror to recent.json", 对 avatar 额外复现主程序独立 memory path 语义 (`cross_server.py` L474-497 /cache 独立路径); 对 agent_callback / proactive 复现 "下一轮 user turn 顺带 /cache" 行为. 适合测 "高频 avatar → 立即触发 recent 阈值压缩" 这类场景.

**两种模式都**必走** `append_message` choke-point + 虚拟时钟单调 guard**. Mirror_to_recent 分支额外调 `memory_writer` (复用 P24 `CompressedRecentHistoryManager.update_history(compress=False)` 语义, 与主程序 `/cache` `recent_history_manager.update_history(..., compress=False)` 对齐, 见 R8 细节).

**Feature flag 三元组 surfacing** (L17 + §A.8 观察点): `SimulationResult.mirror_to_recent_info = {requested: bool, applied: bool, fallback_reason: str|None}`. 如 tester 勾选 mirror_to_recent 但遇到 "recent.json 写入失败 / 达 max size" 类降级, 必须 `applied=False + fallback_reason` 而非 silent drop.

### 2.5 LLM Invocation = Always

用户决策 `llm_mock_strategy = always_llm`: 每次模拟事件**都跑真实 LLM** 产出 assistant reply (使用 session 当前 `model_config.chat` 组).

- 对齐主程序行为 (主程序每次 avatar / agent_callback / proactive 都真的调 LLM).
- 让 "影响评估" 有真实数据 (否则只模拟 note 不模拟 reply, 无法评估 model 回复稳健性).
- 代价: 每次事件消耗真实 token. 不提供 "skip LLM" 开关 — 要省 token tester 自己调 `ChatMock` endpoint (已有基础设施).

### 2.6 三类一个抽象 (端点 + 响应结构 + 默认值)

同模式, 同抽象:

```
POST /api/session/external-event
body: { kind: "avatar_interaction" | "agent_callback" | "proactive", payload: {...}, mirror_to_recent: false }
```

内部按 `kind` 分派到三个 handler. 避免前端 / 后端各写三遍相似代码.

**三"一致"** (§A.7 漂移诊断锚点, 任何矫正不得违反):

1. **一个端点** — `POST /api/session/external-event`, 不按 `kind` 拆三个路径.
2. **一个响应结构** — `SimulationResult` dataclass 三类共用 (见 §3 Day 1), UI 前端按字段渲染不按 kind 分支.
3. **一个默认值** — `mirror_to_recent` 默认 `False` 三类一致 (§2.4). 不按主程序运行时行为 (avatar 走独立 /cache / agent_callback + proactive 不走 /cache) 在默认值上分化 — 那是运行时机制, §2.1 OOS.

### 2.7 agent_callback 触发路径的 testbench 对应

主程序 agent_callback 在代码里有两条触发路径 (`core.py` L3623-3631 的"顺带 inject 到 user turn 前"路径 A / L3051-3063 的"独立 proactive turn"路径 B), 两条都调同一 `prompt_ephemeral(AGENT_CALLBACK_NOTIFICATION + ctx)` instruction. **testbench 不选其一复现** — 触发层一律是 "tester 手工点 [Invoke event]", 对应 §2.2 tester-driven 原则. 主程序两条路径的区别只在 runtime 时机, testbench 关心的"影响评估"(§1.1 四问) 在两条路径上产出一致的语义契约 (同 instruction / 同 reply / 同 memory 写入), 故 **testbench 视图 = tester 手动 click, 不映射路径 A 或 B** (R13 漂移撤回后的收敛).

---

## 3. 分阶段交付

### Day 1 — 后端 handler + router 骨架 (~0.8 天) ✅ **已交付 (2026-04-23, Subagent 并行开发模式首次应用)**

**交付实况** (对照下文"交付物"清单): 5 新文件 + 2 改文件全部按 §A.8 清单落地, **11/11 smoke 全绿** (9 历史 + P25 新 2 个). 主 agent + 4 subagent 并行 (A 抄 dedupe / B 扩 diagnostics_ops / C 写 TC smoke / D 写 drift smoke), 主 agent 写 external_events.py + router 主干. Subagent C **自诊出主 agent 写的 bug**: `meta.get("dedupe_key")` 实际主程序返回键是 `memory_dedupe_key`, 主 agent 5 分钟修 + 把 B2 smoke 从 record-and-continue 升级为 strict assert. 详情见 `AGENT_NOTES.md §4.27 #109` + `PROGRESS.md 2026-04-23 变更日志` + `LESSONS_LEARNED §7.A L33 候选`. 本 Day 派生 **L33 "Subagent 并行开发 + 主 agent 三段式 review" 范式**.

**交付物**:

- `tests/testbench/pipeline/avatar_dedupe.py` (~140 行, **新文件**, R4 + L30 配):
  - 顶部 docstring 明文 "**copy from main_logic/cross_server.py, 2026-04-23 快照, 主程序该函数发生签名变更时本文件与 `p25_avatar_dedupe_drift_smoke.py` 同步更新**".
  - `AVATAR_INTERACTION_MEMORY_DEDUPE_WINDOW_MS = 8000` 常量.
  - `_should_persist_avatar_interaction_memory(cache_entry, note, rank, now_ms) -> (persist: bool, info: dict)` 纯函数, 函数体 **byte-for-byte** 同步自主程序 (drift smoke 字节级 hash compare 守漂移).
  - `_AvatarDedupeCache` class: per-session LRU dict.
    - `_MAX_ENTRIES = 100` soft cap (R3 + L27 资源上限四问: (a) 上限 100 / (b) 达上限 LRU evict 最旧 + 入 ring / (c) `AVATAR_DEDUPE_CACHE_FULL` DiagnosticsOp once-notice / (d) "Clear dedupe cache" UI 按钮 actionable).
    - TTL 扫描: `evict_expired(now_ms)` 把 `now_ms - entry.last_persisted_ms > AVATAR_INTERACTION_MEMORY_DEDUPE_WINDOW_MS` 的条目移除.
    - `peek_state() -> list[dict]` 返回当前 key / rank / last_persisted_ms / remaining_ms, 给 `/dedupe-info` 端点 + UI preview 用.
    - 挂到 `session._avatar_dedupe_cache` (§A.2 D3 白名单, session 销毁自动清).

- `tests/testbench/pipeline/external_events.py` (~280 行):
  - `SimulationResult` dataclass:
    ```python
    @dataclass
    class CoerceEntry:
        field: str           # intensity / touch_zone / text_context / pointer / timestamp
        original: Any
        coerced: Any
        reason: str          # "intensity_out_of_whitelist" / "text_sanitized" / ...

    @dataclass
    class MirrorToRecentInfo:
        requested: bool
        applied: bool
        fallback_reason: str | None

    @dataclass
    class SimulationResult:
        accepted: bool
        reason: str | None           # 见下文"reason 复现表"
        kind: Literal["avatar_interaction", "agent_callback", "proactive"]
        instruction: str | None      # **仅 UI preview, 不入 session.messages**
        memory_pair: dict | None     # avatar 专属 {user_note, assistant_reply} 语义预览
        persisted: bool              # 是否入了 session.messages / recent
        dedupe_info: dict | None     # {reason, rank, rank_upgraded, remaining_ms, key}
        assistant_reply: str | None  # LLM 产出
        coerce_info: list[CoerceEntry]      # R5 + L14 surface
        mirror_to_recent_info: MirrorToRecentInfo  # §2.4 feature flag 三元组
        diagnostics_op: str          # 入 ring 的 op 名
    ```
  - Three handlers (共用 LLM 调用脚手架, 复用 `chat_runner.resolve_group_config` + `create_chat_llm`):
    - `async def simulate_avatar_interaction(session, payload, mirror_to_recent=False) -> SimulationResult`
    - `async def simulate_agent_callback(session, payload, mirror_to_recent=False) -> SimulationResult`
    - `async def simulate_proactive(session, payload, mirror_to_recent=False) -> SimulationResult`
  - **Pure helper 来源**:
    - Avatar: `from config.prompts_avatar_interaction import _normalize_avatar_interaction_payload, _build_avatar_interaction_instruction, _build_avatar_interaction_memory_meta, _sanitize_avatar_interaction_text_context, ...` (9 helper 全 import, 零 copy).
    - Agent Callback: `from config.prompts_sys import AGENT_CALLBACK_NOTIFICATION, _loc`.
    - Proactive: `from config.prompts_proactive import proactive_chat_prompt*, _normalize_prompt_language, ...`.
    - Avatar dedupe: **不 import main_logic**, 一律走 `from tests.testbench.pipeline.avatar_dedupe import _should_persist_avatar_interaction_memory, _AvatarDedupeCache`.
  - **Coerce surface 覆盖 5 处 silent coerce** (R5 + R11 + R12):
    - `intensity` 非白名单 → `_normalize_avatar_interaction_intensity` 纠正; coerce_info 追加 `{field: "intensity", original, coerced, reason: "out_of_whitelist"}`.
    - `touch_zone` 对 hand/lollipop 非白名单 → 空串; 追加 `{field: "touch_zone", ...}`.
    - `text_context` 超 80 字符 / 非 printable / 合并空白 → `_sanitize_avatar_interaction_text_context` 清洗; per-field 合并一条 `{field: "text_context", reason: "text_sanitized", original, coerced}` 不展开子步骤.
    - `pointer` 非法 → None; 追加一条.
    - `timestamp` 非法 → `time.time()*1000` fallback; 追加一条.
  - **Instruction 只入 wire 不入 messages** (R1b 配, §A.8 #2): handler 构造 `instruction_text` → 作为 `system` 消息临时拼到**本次 LLM 调用 的 prompt list**, 不调用 `append_message`. `SimulationResult.instruction = instruction_text` 仅供 UI preview.
  - **Assistant reply 走 append_message** (A17 + §2.3): LLM 产出非 `[PASS]` 的 reply 经 `messages_writer.append_message(session, {role:assistant, content: reply}, on_violation="warn")` 写入; 若触发 coerce 追加到 coerce_info.
  - **Avatar user-role memory note 成对结构**:
    - **默认 session_only**: `append_message(session, {role:user, content: memory_note})` + `append_message(session, {role:assistant, content: reply})` 两条入 `session.messages` (tester 在 Chat 视图直接看到 pair).
    - **mirror_to_recent=True**: 额外走 `memory_writer` 把同一对 `{user_note, assistant_reply}` 写入 `memory/recent.json` (复现主程序 `/cache` 独立路径语义).
  - **Agent Callback / Proactive**:
    - 默认 session_only: 仅 reply 入 session.messages (instruction 不入 messages).
    - mirror_to_recent=True: reply 同时入 recent.json.
    - Proactive LLM 返 `[PASS]` → `accepted=True, persisted=False, reason="proactive_pass"`, 不追加任何 message.
  - **Dedupe rollback 保护** (L20 配): handler 开头 dedupe 先占位 (预写 entry + rank), 若后续 LLM 调用抛异常 → 回滚 dedupe entry (删除或还原 rank - 1), 避免"失败的事件占 dedupe 位"问题. 对应主程序 `cross_server.py` L480-484 `dedupe_prior_entry` 保护.
  - **SimulationResult.reason 复现表** (R10 配, §2.1 直接应用):
    - **复现 (语义契约层)**: `invalid_payload` / `duplicate` / `busy` / `llm_error` / `proactive_pass` / `missing_fields`.
    - **不复现 (运行时机制层, testbench 不会产出这些 reason)**: `cooldown` (600/1500ms) / `speak_cooldown` / `voice_session_active` / `no_websocket` / `session_start_failed` / `not_text_session`. 源码注释明示.

- `tests/testbench/routers/external_event_router.py` (~100 行):
  - `POST /api/session/external-event`:
    - Pydantic `ExternalEventRequest{kind: Literal[...], payload: dict, mirror_to_recent: bool = False}`.
    - SessionState.BUSY 短锁 (同 chat_turn pattern).
    - 按 `kind` 分派到三 handler, 返回 `SimulationResult` JSON.
    - Body 缺字段 / kind 非法 → 400 `HTTPException(detail={code, message})` (§3A A1 + A12).
    - 无 active session → 404.
    - **不** 用 SSE 流式 (Day 1 一律请求-响应 async def, L23 类别 1).
  - `GET /api/session/external-event/dedupe-info`: 查当前 session 的 avatar dedupe cache 状态 (哪些 key 还在窗口内, remaining_ms).
  - `POST /api/session/external-event/dedupe-reset`: 清空 session avatar dedupe cache (tester "重新开始测试" 快捷).
  - **Mutation 严禁 AbortController** (L19 + R6 配): Day 2 前端 "Invoke event" 按钮只用 `disabled + spinner + toast`, **不** 传 signal. `/dedupe-info` (GET) 和 `/dedupe-reset` (幂等) 允许 signal/abort.

- `tests/testbench/server.py` 注册新 router.

- `tests/testbench/pipeline/diagnostics_ops.py` 新增 4 个 op:
  - `AVATAR_INTERACTION_SIMULATED` (level=info) — handler 成功后入 ring.
  - `AGENT_CALLBACK_SIMULATED` (level=info).
  - `PROACTIVE_SIMULATED` (level=info).
  - `AVATAR_DEDUPE_CACHE_FULL` (level=info, **once-notice** per session lifetime, R3 + L27) — LRU evict 触发时首次入一条, 后续 silent 不 spam.

**验收点**:
- `curl POST /api/session/external-event` 三类 kind 各走一遍, 返回 200 + `SimulationResult` 有非空 instruction + assistant_reply.
- **Avatar 默认**: session.messages 追加 user memory_note + assistant reply 两条; `memory/recent.json` **不** 自动变化.
- **Agent callback / Proactive 默认**: session.messages 只追加 assistant reply 一条; instruction 不在 messages 里出现.
- **mirror_to_recent=True 一轮**: `memory/recent.json` 对应条目追加, `SimulationResult.mirror_to_recent_info = {requested: true, applied: true, fallback_reason: null}`.
- Diagnostics ring 有对应 op entry.
- 连续点 avatar fist poke 10 次, dedupe cache 只保留 1 个 key (rank 升级到 10), session.messages 只追加 1 组 (后续被 dedupe 过滤), 响应 `persisted=false + dedupe_info={reason:"within_window", rank_upgraded:true, rank:10, remaining_ms:xxxx}`.
- **Coerce surface**: 发 payload `intensity="totally-invalid"` → `accepted=true, coerce_info=[{field:"intensity", original:"totally-invalid", coerced:"normal", reason:"out_of_whitelist"}]`, 不 silent drop.
- **Dedupe cap**: 用脚本连发 101 个不同 key, 第 101 个时 `AVATAR_DEDUPE_CACHE_FULL` op 入 ring, 最老那条被 LRU evict. 第 102 个**不再**入 op (once-notice).
- **Reason 复现表**: 故意发非法 payload (缺 tool_id) → 返 `accepted=false, reason="invalid_payload"`; **不会** 返 `reason="cooldown"` / `"voice_session_active"` 等运行时层 reason.

### Day 2 — 前端面板 + i18n + CSS (~1 天)

**交付物**:
- `tests/testbench/static/ui/chat/external_events_panel.js` (~300 行):
  - 侧边 / 抽屉式折叠面板, 3 个子表单 tab:
    - **Avatar**: tool_id (dropdown: hand/lollipop/fist/hammer) + action_id (dropdown 动态根据 tool_id 过滤合法 action) + intensity (dropdown: normal/rapid/burst/easter_egg, 根据 tool_id+action_id 过滤合法组合) + touch_zone (dropdown: ear/head/face/body, 仅 fist/hammer 启用) + easter_egg checkbox + text_context (文本框 max 80 字符)
    - **Agent Callback**: summary (多行) + detail (多行) + status (dropdown: completed/failed/partial)
    - **Proactive**: trigger_reason (dropdown: idle_timer/queue_empty/manual) + trending_content (JSON 编辑器) + use_session_memory_context (checkbox 默认勾选)
  - 每个表单底部:
    - "Invoke event" 按钮 (主操作)
    - "mirror_to_recent" checkbox (默认不勾; 勾上则把 reply/memory pair 额外写入 `memory/recent.json`, 对应 §2.4 opt-in; 三类一致)
  - 提交后分 5 栏实时展示:
    1. **Instruction preview** (产出的 prompt 片段, 折叠式; 注明 "仅 preview, 未入 session.messages", 对齐 §1.2 语义锚点)
    2. **Memory pair preview** (`{role:user, content: "[主人摸了摸你的头]"}` + `{role:assistant, content: LLM 回复}`; 仅 avatar 显示此栏, 其它两类隐藏或空)
    3. **Persistence decision** (persisted=true/false + dedupe_info 详情, e.g. "8000ms 窗口内已存在 key `hand_touch_head_normal`, rank 升级 2→3"; 含 `mirror_to_recent_info` 三元组 requested/applied/fallback_reason 的黄色 warning badge, 若 requested=true && applied=false)
    4. **Payload coerce** (仅 `coerce_info` 非空时显示; 黄色 warning badge + 每条 coerce `{field, original → coerced, reason}`, R5 + L14 surface)
    5. **LLM reply bubble** (直接渲染 assistant reply 的 markdown)
  - 面板右上角有 "Clear dedupe cache" 按钮 (调 `/api/session/external-event/dedupe-reset` 端点 — 幂等操作**允许** signal/abort)
  - **[Invoke event] 按钮是 mutation, 严禁 AbortController** (L19 + R6 配): in-flight 期间按钮 `disabled=true` + 文本换成 spinner + toast "事件正在投递, 请稍候"; 用户若要中止, 必须等 api.js 默认 90s 超时或手动刷新页面. 理由: 事件 handler 是**四处副作用**(写 messages + 可选写 recent + 写 dedupe cache + 写 diagnostics ring), 中途 abort 会留"部分落地"状态, 无法回滚.

- `tests/testbench/static/core/i18n.js` 新增 25+ keys:
  - `external_events.panel.title`, `external_events.tab.avatar`, ...
  - 每个 tool_id / action_id / intensity / touch_zone / trigger_reason 的 label

- `tests/testbench/static/ui/app.css` 新增 `.external-events-panel` 样式 (折叠式 + 4 栏布局 + memory pair 的 user/assistant 颜色区分)

- Chat 工作区主布局 `ui/chat/layout.js` 加入 panel (收起状态不占空间)

**验收点**:
- 手动点 Avatar hand + touch + head + normal 提交, 看到完整 4 栏输出, assistant reply 是真实 LLM 内容
- 切到 Agent Callback, 填 summary "已完成下载", 提交, 看到 LLM 回复中自然提及下载完成
- 切到 Proactive, 填 trending_content `["某条热搜"]`, 提交, 看到 LLM 回复或 `[PASS]`
- 勾 "mirror_to_recent" 提交一次 avatar poke, 跑一次 `recent.compress` trigger, preview 抽屉展示的摘要**包含** 该 poke 的系统 note
- 连点 10 次 avatar 同 payload, 面板显示 "dedupe window: 6.2s remaining, rank upgraded to 10", 只有第 1 次 persisted=true

### Day 3 — Smoke + 回归 + 文档 (~0.7 天)

**交付物**:
- `tests/testbench/smoke/p25_external_events_smoke.py` (~320 行):
  - **去重函数单测**: `_should_persist_avatar_interaction_memory` 的 matrix (首次落 / 窗口内同 key rank 升 / 窗口内同 key rank 降 / 窗口外同 key / 不同 key 不干扰 / 空 note 拒绝 / 非法 rank 默认 1)
  - **payload 校验单测**: `_normalize_avatar_interaction_payload` 的 matrix (所有合法 tool×action×intensity 组合 / 非法 intensity 被纠正 / 非法 touch_zone 对 hand/lollipop 被拒 / text_context 超 80 字符截断 / easter_egg 非 bool 默认 false); 同时断言 `SimulationResult.coerce_info` 正确 surface (R5 + L14).
  - **memory_meta 产出单测**: `_build_avatar_interaction_memory_meta` 对齐主程序 `tests/unit/test_avatar_interaction_memory_contract.py` 的期望 (当前 session.persona.language × 4 tool × rank 1/2/3/升级合并文案; 5 语言矩阵信任主程序 unit 测, §4.5 决策)
  - **Dedupe cache cap + LRU**: 连发 101 个不同 key, 断言第 100 之后 LRU evict 发生, `AVATAR_DEDUPE_CACHE_FULL` DiagnosticsOp once-notice (R3 + L27)
  - **Dedupe rollback**: handler 开头 dedupe 占位 → LLM 抛异常 → 断言 dedupe entry 已被回滚 (下次同 key 仍 persisted=True, L20 配)
  - **三类 handler 闭环** (mock LLM, 断言 session.messages 追加正确 + diagnostics entry + 去重工作):
    - simulate_avatar_interaction 正常 path + dedupe within-window path + mirror_to_recent path
    - simulate_agent_callback 正常 path + 空 summary 拒绝 path; 断言 **instruction 不入 session.messages** (R1b 配), 只 reply 入
    - simulate_proactive 正常 path + `[PASS]` path (不写 message); 断言 instruction 不入 session.messages
  - **Persona language 静默回退** (上游 delta): 设 `persona.language=es` → 跑 simulate_agent_callback, 断言 instruction 文本是**英文** (prompts_sys._loc + `_SILENT_FALLBACK`); 设 `persona.language=pt` → 跑 simulate_proactive, 断言 prompt 是**英文** (prompts_proactive._normalize_prompt_language 回退).
  - **Reason 复现表** (R10): 断言所有产出的 reason 只来自复现集 (invalid_payload / duplicate / busy / llm_error / proactive_pass / missing_fields); 任何 reason 含 "cooldown" / "voice" / "websocket" / "not_text_session" 应 FAIL.
  - **router 集成**: TestClient POST `/api/session/external-event` 三类各一次 + 非法 kind 返回 400 + 无 session 返回 404 + mirror_to_recent=True 一次断言 recent.json 变化.

- `tests/testbench/smoke/p25_avatar_dedupe_drift_smoke.py` (~80 行, R4 + L30 配):
  - **`from main_logic.cross_server import _should_persist_avatar_interaction_memory as _upstream`** (**整个 testbench 仅此 smoke 允许 import main_logic**, L30 约束).
  - `from tests.testbench.pipeline.avatar_dedupe import _should_persist_avatar_interaction_memory as _local`.
  - 断言两者的**函数体字节级等价**: `hashlib.sha256(inspect.getsource(_upstream).encode()).hexdigest() == ...(local)`. 若文档块 / 空白不同但语义等价, 用 `ast.unparse(ast.parse(src))` 规范化后再 hash.
  - 另附行为等价 matrix: 在 20 组随机 (note, rank, now_ms) 样本上断言 `_upstream(...) == _local(...)`.
  - Drift 发生时 smoke FAIL + 错误消息明示 "主程序 _should_persist_avatar_interaction_memory 签名/行为已变, 请同步更新 `tests/testbench/pipeline/avatar_dedupe.py`".

- 回归既有 smoke: `p21_*` / `p22_*` / `p23_*` / `p24_*` 全绿

- 跑用户手测一轮"全链路影响评估":
  1. 创建新 session + 导入一个基础 persona
  2. 点 10 次 avatar fist burst 到 head (应该最终只 persisted 1 条 rank=10)
  3. 点 agent callback "已完成播放列表整理" (应产生一条 assistant 自然提及)
  4. 点 proactive trigger_idle (应产生一条 assistant 自发搭话或 `[PASS]`)
  5. 手动 trigger `recent.compress`, 看摘要如何处理这些事件
  6. 手动 trigger `facts.extract`, 看是否抽出"主人喜欢摸头"类特征 — **明示**: avatar memory pair 在默认 session_only 模式下**不**自动触发 facts.extract (对齐主程序 `/cache` 不触发 `/process` 的语义, R8 细节); tester **必须**手动 trigger 才能观察长期记忆影响
  7. 手动 trigger `reflect`, 看反思层的概括
  8. 记录实测结果到 `p24_integration_report.md` 新增 §1.3 P25 外部事件 subsection (或单开 `p25_events_report.md`)

- `tests/testbench/docs/external_events_guide.md` (~220 行) tester 手册:
  - 每类事件的使用场景
  - payload 字段含义
  - dedupe 策略解读 (8000ms 窗口 + rank upgrade + LRU cap=100)
  - 期望观察的影响 (recent / facts / reflection 三层)
  - **默认 session_only / opt-in mirror_to_recent 的行为区别** (§2.4)
  - **Payload coerce 展示位置与解读** (R5 + L14, 5 个 silent coerce 字段)
  - **SimulationResult.reason 复现表 / 不复现表** (R10)
  - 已知限制 (**冷却三分类 OOS**: 实时流抖动 600/1500ms / `min_idle_secs=10s` / merge_unsynced_tail_assistants 连续主动搭话合并 — 全部不复现; 不触发实时流; 不触发多会话隔离)

- ~~更新 `AGENT_NOTES.md` 加 L25 "语义契约 vs 运行时机制" 元教训~~ — **已于 P25 立项时完成**, 详见 [AGENT_NOTES §4.27 #108](AGENT_NOTES.md#108-2026-04-22-p24-day-8-9-完结--day-9-e-二轮翻转--p25-立项-用户手测验收--主程序同步五项盘点全零行动--道具交互翻转--三条新元教训-l23l24l25) (L23/L24/L25 三条) + [LESSONS_LEARNED §1.6](LESSONS_LEARNED.md#16-语义契约-vs-运行时机制-测试生态-oos-判据) + [LESSONS_LEARNED §2.9A](LESSONS_LEARNED.md#29a-property-动态计算-vs-直接赋值-路径字段的沙盒mock-友好设计) (@property 动态计算)
- ~~`~/.cursor/skills/testbench-external-system-adapter`~~ — **已于 P25 立项时完成**, 以 `semantic-contract-vs-runtime-mechanism` 命名归档 (放置于 `~/.cursor/skills/`, 不依赖本项目, 其它项目的测试生态对接方法论也适用)

**验收点**:
- 全部新 smoke (p25_external_events_smoke + p25_avatar_dedupe_drift_smoke) + 回归 smoke 全绿 (baseline 要 10/10 smoke 绿, 若含 p24_sandbox_attrs_sync_smoke 等则按实际数字计)
- `p25_avatar_dedupe_drift_smoke` 对主程序 `_should_persist_avatar_interaction_memory` 的字节/行为双等价守住
- tester 手册可独立读懂
- 至少一组"10 次 avatar 事件 → recent.compress → facts.extract → reflect" 全链路跑通并记录影响观察
- `persona.language=es/pt` 两语言场景 agent_callback + proactive instruction 断言英文静默回退生效

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

### 4.5 Avatar dedupe pure helper 跨 package: copy + drift smoke, 不 import (R4 + L30 配)

**决策**: **不** `from main_logic.cross_server import _should_persist_avatar_interaction_memory`; 改为**字节级 copy** 到 `tests/testbench/pipeline/avatar_dedupe.py`, 同时写 `tests/testbench/smoke/p25_avatar_dedupe_drift_smoke.py` 字节 + 行为双等价断言守漂移.

**理由**:

- `main_logic/cross_server.py` 模块级 `import main_logic.agent_event_bus / aiohttp / ssl / asyncio.Queue` 全部有重副作用; testbench 从 P00 起就没有 `import main_logic`, 一旦引入这条依赖等于把整套 runtime 副作用带进测试生态, 破坏**历史边界纪律**.
- L30 铁律: copy 和 drift smoke **必须配对**, 缺一则失效 — 前者无 smoke 会"半年后主程序改签名, testbench 默默走老版本, 语义偷偷背离"; 后者无 copy 则会 import error.
- `p25_avatar_dedupe_drift_smoke.py` 是**整个 testbench 里唯一允许 import `main_logic`** 的文件 (顶部 docstring 明示); 其它任何 pipeline / router / smoke 都不许.
- Day 1 implementation: 把 `_should_persist_avatar_interaction_memory` 函数体 + `AVATAR_INTERACTION_MEMORY_DEDUPE_WINDOW_MS` 常量 copy, 顶部 docstring "copy from main_logic/cross_server.py @ 2026-04-23 snapshot, 主程序该函数发生签名变更时本文件与 `p25_avatar_dedupe_drift_smoke.py` 同步更新".

**候选 §3A 新原则 (交付后观察)**: H3 "外部系统 pure helper cross-package copy > import (有重副作用时)" — L30 的铁律化.

### 4.6 merge_unsynced_tail_assistants 不复现 (§1.3 OOS 合并, R9 归并后)

**决策**: **不复现**. 已在 §1.3 OOS 列出, 此处不重复; 仅记录决策理由汇总:

- 主程序 `cross_server.py::merge_unsynced_tail_assistants` (L80-118) 的触发前提是"tail 连续 ≥2 条 assistant 且都在 last_synced_index 之后, 丢弃前 N-1 条仅保留最后一条".
- testbench 里 tester 手工点击 `simulate_*` 间隔 ≥ 人类反应时间, 视同 "user 在中间 interleave 了操作", 不满足主程序 merge 触发前提.
- 且 merge 本身是**实时流场景的运行时优化** (连续主动搭话未被 user turn 打断 = runtime 现象), §2.1 判定 OOS.
- 每次 `simulate_*` 成功 → 独立 turn → `session.messages` 逐条追加.

### 4.7 SimulationResult.reason 复现表 (R10 配, §2.1 直接应用)

**复现 (语义契约层, testbench 真会产出)**:

- `invalid_payload` — Pydantic 校验失败 / 字段缺失但 payload 形式合规 (两种情形).
- `missing_fields` — payload 顶层缺 `kind` 或 `payload` 字段 (`invalid_payload` 的细分, 也可合并).
- `duplicate` — avatar 8000ms 窗口内 + rank 未升级 (语义去重, §1.3 (b) 类必保).
- `busy` — SessionState 已被 chat_turn / reset / 其它 event 占用.
- `llm_error` — LLM 调用抛异常 (timeout / RateLimitError / API key 错 / ...); handler 捕获后 rollback dedupe 然后返此 reason.
- `proactive_pass` — proactive 唯一专属, LLM 返 `[PASS]` 时不写 message 的成功语义 (`accepted=True, persisted=False, reason="proactive_pass"`).

**不复现 (运行时机制层, testbench **不会**产出这些 reason, 源码注释明示)**:

- `cooldown` — 600ms 帧合并窗口, testbench tester-driven 无需防抖.
- `speak_cooldown` — 1500ms speak 冷却, 同上.
- `voice_session_active` — OmniRealtimeClient 状态, testbench 无实时流.
- `no_websocket` — WebSocket 连接状态, testbench 无.
- `session_start_failed` — 自启实时 session, testbench 无.
- `not_text_session` — 区分 realtime / text session, testbench 永远 text.

Smoke `p25_external_events_smoke.py` 用"所有产出 reason ∈ 复现集" 断言守此边界 (Day 3).

### 4.8 Avatar memory meta 的 5 语言 label 怎么测?

**决策: 只对**用户当前 session `persona.language` **测, 不矩阵 5 语言**.

- 主程序的 `tests/unit/test_avatar_interaction_memory_contract.py` 已经 matrix 过 5 语言.
- testbench smoke 只需验证 "当前 session 语言 → memory meta 正确" 即可 (等价于信任主程序的 unit 测试).
- 如果将来发现某语言有 bug 再回头加.
- **例外**: 上游 delta `_SILENT_FALLBACK={'es','pt'}` 必须在 smoke 里验一次英文静默回退 (见 Day 3 验收点).

---

## 5. 风险与缓解

| 风险 | 可能性 | 影响 | 缓解 |
|---|---|---|---|
| 主程序 `prompts_avatar_interaction` 常量改动破坏 testbench | 低 | 中 | testbench 只 import helper function, 不 copy 常量; 主程序改动会**立即反映**到 testbench (保持同步); `p25_avatar_dedupe_drift_smoke` 对 `_should_persist_avatar_interaction_memory` 字节/行为双等价守漂移 |
| `_should_persist_avatar_interaction_memory` 被主程序改签名/行为 | 中 | 中 | Day 1 把该函数 **copy** 到 `pipeline/avatar_dedupe.py` (L30 + §4.5) + Day 3 `p25_avatar_dedupe_drift_smoke` 字节级 hash compare + 行为 matrix 守漂移. 漂移即 FAIL, 强制本阶段 agent 同步更新 |
| **高频 tester 点爆 dedupe cache (R3 + L27 派生)** | 低 | 低 | `_AvatarDedupeCache._MAX_ENTRIES=100` soft cap + LRU evict + `AVATAR_DEDUPE_CACHE_FULL` DiagnosticsOp once-notice + 面板 "Clear dedupe cache" 按钮 actionable. 即使 tester 在 8s 内连点 1000 次不同 key 也不会 OOM / 不会 silent drop |
| LLM 消耗 token 过多 | 中 | 中 | UI 面板默认禁用 "auto-trigger on event queue" (无此功能也是缓解); tester 有意识才点, 一次点一次跑 |
| 三类系统语义差异大导致抽象泄露 | 低 | 低 | 用 `kind` 字段分派, 三个 handler 各自独立, 公共部分只有"写 messages + diagnostics entry + coerce surface"这一薄层; §2.6 "三类一个抽象" 守 |
| dedupe rank upgrade 文案与主程序不一致 | 中 | 低 | smoke 直接 import 主程序 `_build_avatar_interaction_memory_meta` 对齐, 不另写文案 |
| 前端面板复杂度高 | 中 | 中 | 沿用 `page_schemas.js` / `page_memory_facts.js` 现有 CRUD 面板样式, 不引入新组件库 |
| Feature flag `mirror_to_recent` silent fallback (L17 派生) | 低 | 中 | `SimulationResult.mirror_to_recent_info = {requested, applied, fallback_reason}` 三元组 surface; UI 在 requested=true && applied=false 时必 toast warning |

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
- [x] P24 Day 10-12 全交付 + 用户 "不要留尾巴" 指示后 Day 12 欠账清返全清 (commit `62844c7` + push `d0fdf72..62844c7 main -> main`, 2026-04-23), **9/9 全量 smoke 全绿 baseline 锁定**
- [x] **§A 开工前设计层回顾完成** (本次新加的 gate 条件, 2026-04-23): 六轮元审核 + 第七轮 self-audit + **第八轮漂移诊断** + 24 元教训/57 原则框架回顾 + 27 条上游 merge delta 三方交叉核查 + §A 收工整理 UTF-8 字节损坏事件就地修复 (冗余登记 3 处 + `git checkout HEAD` + 纯 UTF-8 重写, 0 数据损失). 最终开工清单收敛到 **§A.8**: **8 条有效矫正 + 3 条细节补完 + 1 条观察** (§3/§4/§5 回写) + 3 条 Day 3 新 smoke + 3 条 §1.3 OOS 扩 + 5 条派生元教训候选 L28-L32 (漂移演化: 原 7 条 → 第七轮 11 条 → 第八轮撤回 R7 R13 完全 + R1c 部分 + R9 合并 + R1b 降级 → 8 条; L32 "PowerShell Set-Content UTF-8 陷阱" 来自 §A 收工整理 UTF-8 修复事件)
- [x] **用户对 P25_BLUEPRINT 本文档审读通过** (2026-04-23, 用户指示 "按照 A.8 开工清单开工"; §A.8 是最终核对的开工权威清单, §A.1 / §A.5 为历史留痕, §A.7 记录漂移诊断过程)
- [x] **§1.2 / §1.3 / §2.4 / §2.6 / §2.7 / §3 Day 1-3 / §4.5-4.8 / §5 已按 §A.8 回写完成** (2026-04-23 本 agent 开工前完成, 小改动无新决策; 回写痕迹: avatar dedupe copy + drift smoke / Dual-Mode 三类一致 session_only 默认 / coerce_info + mirror_to_recent_info surface / Instruction 不入 messages / reason 复现表 / 冷却三分类 OOS / merge 不复现)

全部满足. **P25 Day 1 启动**.

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
- 2026-04-23 §A 开工前设计层回顾新增 (P24 Day 12 欠账清返 done 后, 按用户 "开工前先研究设计草案, 从架构上理清内容 + 预防高危 bug 点" 要求, 参照 P24 开工期六轮 meta 审核节奏做的 design review gate). Git: `d6a22c2` [`docs(testbench): P25 开工前 · §A 设计层回顾 gate (元审核 + 框架回顾 + 上游 delta 三方交叉核查)`] pushed 成功.
- 2026-04-23 §A 第七轮 self-audit 补丁 (用户"再审一轮"指示后, 回头审视 §A 本身交叉对照主程序 core.py / cross_server.py / memory_server.py / prompts_avatar_interaction.py 代码, 追加 R7/R9/R10/R13 四条针对 §A 自身的不精确/遗漏矫正, R8/R11/R12 三条细节补完). 矫正总数 7 → 11, §3 Day 1 必改 4 → 6 处, §2.4 从 "一刀切反转" 精确到 "三类分别说明", §4 补 4.6/4.7 两条决策. Git: `e2dc82c` [`docs(testbench): P25 §A 第七轮 self-audit 补丁 (R7/R9/R10/R13 + R8/R11/R12)`] pushed 成功.
- 2026-04-23 §A 第八轮漂移诊断 (用户"保持设计思想连贯性, 防语义漂移"指示后, 回头诊断前七轮的 11 条矫正是否守住原设计初衷 §1.1 §2.1 §2.2 §2.4 §2.6). 发现 R7 / R13 是目标漂移产物 (把 "复现主程序 runtime 行为"悄悄引入目标, 违反 §2.1 语义契约 vs 运行时机制), R1c 默认反转部分漂移, R9 归属错, R1b 严重度高估. 处置: R7 R13 完全撤回, R1c 部分撤回 (默认反转 → 保留观察), R9 降到 §1.3 OOS 合并, R1b ❗ → ⚠ 降级. 收敛到 §A.8 = 8 条有效矫正 + 3 条细节 + 1 条观察, 新 §A.7 漂移诊断小节 + 新元教训候选 L31 "审查时必须持续锚定设计初衷". 本批修改还包括: 从 `Set-Content -NoNewline` 操作造成的 UTF-8 字节损坏 (3280 处中文末字节丢失) 中恢复 — `git checkout HEAD` 重置后按骨架 ASCII 参考 + AGENT_NOTES §4.27 #108 + LESSONS §7.A 语义源重写 §A.7 / §A.8 / §A.9, 同步加 cursor skill 候选 `powershell-set-content-utf8-trap`. Git: *pending* (按用户 "无需 commit" 指示, 未入 git; 待 P25 Day 1 开工时合并或独立 `docs(testbench): P25 §A 第八轮漂移诊断 + L31 归档 + UTF-8 修复` commit).

---

## §A 开工前设计层回顾 (Design Review Gate, 2026-04-23)

> **权威性**: 本节是 **P25 Day 1 开工前的 design review gate**. 本节列出的 "设计层矫正" 必须落到 §3 Day-by-Day / §4 关键技术决策 / §5 风险表 后才能开工. Agent 在读本节时若发现 §3 / §4 / §5 已更新对齐, 说明矫正已落地; 若未对齐, 必须先改 §3 / §4 / §5 再开工.
>
> **开工唯一权威清单 = §A.8 (漂移修正后最终开工清单)**. §A.1 表 / §A.5 清单为**历史留痕**, 记录六轮 meta + 第七轮 self-audit + 第八轮漂移诊断的完整演化, 不是开工依据.
>
> **产出方式**: 按 P24 开工期六轮 meta 审核方法论 ([P24_BLUEPRINT §14 meta-audit](P24_BLUEPRINT.md#14-p24-方案元审)), 对本蓝图 §1-§9 做六轮思想实验 + 对 LESSONS §7 24 条元教训 + §3A 57 条设计原则 + 2026-04-22 git merge 27 条上游 delta 做三方交叉核查; 用户要求"再审一轮"后再跑**第七轮 self-audit** (回头审视前六轮产出的 §A 本身, 交叉对照主程序 `core.py` / `cross_server.py` / `memory_server.py` / `prompts_avatar_interaction.py` 的实际代码行为, 发现 §A 自身的不精确/遗漏点 4 条: R7/R9/R10/R13). 用户进一步要求 "审查时保持设计思想连贯性 + 防语义漂移" 后再跑**第八轮漂移诊断** (§A.7), 回头审视前七轮的 11 条矫正是否守住原设计初衷 (§1.1 四问 / §2.1 语义契约 vs 运行时机制 / §2.2 Tester-Driven / §2.4 默认 session_only / §2.6 三类一个抽象), 发现 R7 / R13 是目标漂移产物 (完全撤回), R1c 默认反转部分目标漂移 (部分撤回), R9 归属错 (合并到 §1.3 OOS), R1b 严重度被高估 (降级). 用 P24 Day 12 欠账清返 (`fix(testbench): 62844c7`) 留下的工具 (semantic-contract-vs-runtime-mechanism skill + async-lazy-init-promise-cache skill + render_drift_detector 骨架) 辅助审查.

### §A.1 六轮元审核 + 第七轮 self-audit (发现的设计层问题)

下表列出审核发现的 **11 条设计层矫正** (原 6 轮 7 条 + 第七轮 self-audit 追加 4 条), 必须在 Day 1 开工前先回写到 §3 / §4 / §5:

| # | 审核维度 | 问题原文 | 正确语义 | 落地处置 | 严重度 |
|---|---|---|---|---|---|
| **R1a** | L26 生成器三分类延伸到"冷却三分类" | §1.3 OOS 说"不冷却窗口 (600ms/1500ms)", §3 Day 1 说"dedupe 8000ms 窗口" 两者混在一起, 没明确区分 | 主程序三种冷却: **(a)** 实时流抖动 (600ms/1500ms, 运行时 OOS) / **(b)** 语义去重 (8000ms rank upgrade, 语义**必保**) / **(c)** N 秒窗口内禁复触发 (未在 avatar 场景出现, 但 proactive `min_idle_secs`=10s 属于此类, 也是运行时 OOS) | §1.3 OOS 扩成"冷却三分类", 明示 (b) 保, (a)(c) 不保. 派生候选元教训 **L29 "冷却语义三分类"** (L26 在时序维度的延伸) | ⚠ 中 |
| **R1b** | 临时 instruction 不入 history | §3 Day 1 Acceptance 第 2 条 "agent_callback 注入时 system instruction + assistant reply 都入 session.messages" | 实证主程序 `core.py::handle_avatar_interaction` L2876 调用 `prompt_ephemeral(instruction, persist_response=False)` 以及 L3624-3631 agent_callback 调用 `prompt_ephemeral(_loc(AGENT_CALLBACK_NOTIFICATION, lang) + ctx)` 注释 "**指令不持久化, 只保留 AI 回复**". 即 **instruction 从来不入持久 history, 只 reply 入**. proactive 同理. | §3 Day 1 Acceptance 改: instruction 只出现在**本次 LLM 调用的 wire**, **不**调 `append_message`; assistant reply **才**走 `append_message` choke-point. `SimulationResult.instruction` 字段作 UI preview 用, 不代表"已入 messages" | ❗ 高 (语义错位) |
| **R1c** | Avatar memory pair "独立 path" 概念 | §2.4 "Dual-Mode Memory Write" 描述为"默认 session_only, 可选 mirror_recent", 但没澄清 session.messages 和 recent 的关系 | 实证主程序 `cross_server.py` L474-497: avatar_interaction 产出 `{role:user, content:[{type:text, text:memory_note}]} + {role:assistant, content:[{type:text, text:assistant_text}]}` **直接 POST `/cache`** (独立 memory path), **不**走 conversation history. 即 avatar memory pair 对应的是 testbench 的 `memory/recent.json` (独立 memory), 不是 testbench 的 `session.messages` (UI 对话视图) | §2.4 增补一段 "testbench 的 session.messages ≈ 主程序 UI conversation history 视图; memory/recent.json ≈ 主程序 /cache memory path, 两者互斥". Dual-Mode 改写: **默认 (recent_only)** = 仅走 memory_writer 写 recent.json, 对齐主程序真实语义; **可选 (mirror_ui)** = 额外 mirror 一份到 session.messages 让 tester 在 Chat 视图直接看到 note + reply. (这是语义反转: 原 "默认 session_only" 与主程序不一致). | ❗ 高 (默认选择错) |
| **R2** | B14 emit/on 双向一致性 | Day 1 新增 3 个 DiagnosticsOp (`AVATAR_INTERACTION_SIMULATED` / `AGENT_CALLBACK_SIMULATED` / `PROACTIVE_SIMULATED`) 全 info 级 | 实证 `static/core/errors_bus.js` 仅拉 level in ('error', 'warning') 入 toast 徽章, info 级不会"漂移"到 errors toast (✅). 但**若** Day 2 面板需要 "面板右上角显示最近 N 次事件摘要" 小组件, 就要新订阅 `diagnostics:op_pushed` (目前不存在). | §A.2 Day 2 UI 建议项加一条: 面板右上角的 "最近 N 次事件摘要" 视情况决定 (若做, 必须同步加 emit 侧: diagnostics_store 每 push info 级 op 时 emit 一次事件, 前端订阅 state.on). **不做则 ✅ 当前 B14 合规**. | ℹ 低 |
| **R3** | L27 资源上限 UX 降级四问 应用到 dedupe cache | §2.x dedupe cache 有 8000ms TTL 但**无条目数 soft cap**. tester 在 8s 内连点 1000 次**不同 key** 会累积 1000 条. | 四问: (a) **上限?** 无; (b) **达上限行为?** 无; (c) **用户可见?** 无; (d) **actionable 操作?** 手动 "Clear dedupe cache" 按钮 (部分). 需补齐: soft cap=**100** 条 + LRU evict + 新 `AVATAR_DEDUPE_CACHE_FULL` DiagnosticsOp (level=info, 仅首次满记录不 spam) | §3 Day 1 pipeline/avatar_dedupe.py (见 R4) 加 `_MAX_ENTRIES=100` + LRU + 溢出 once-notice. §3 Day 1 diagnostics_ops.py 新增 `AVATAR_DEDUPE_CACHE_FULL`. §5 风险表补一条 "高频事件 tester 点爆 cache". | ⚠ 中 |
| **R4** | main_logic 边界破坏 | §3 Day 1 + §5 风险表 2 "If 主程序改签名... copy 到 pipeline/avatar_dedupe.py". 含蓄表达"先 import 再 fallback copy" | 实证 `main_logic/cross_server.py` 模块级 `import main_logic.agent_event_bus / aiohttp / ssl / asyncio.Queue` 有重副作用, testbench **从未 import main_logic**, 边界历史上就没打破过. 接受 cross_server import 等于打破"testbench 只 import config/ 和 memory/" 的历史默契, 且带入大量不必要依赖. | **Day 1 必做, 不走 fallback**: 把 `_should_persist_avatar_interaction_memory` 及 `AVATAR_INTERACTION_MEMORY_DEDUPE_WINDOW_MS` 常量**显式 copy** 到 `tests/testbench/pipeline/avatar_dedupe.py`, 顶部 docstring 明文 "copy from main_logic/cross_server.py, 2026-04-23 快照, 主程序该函数发生签名变更时本文件与新 smoke 同步更新". Day 3 新 smoke `p25_avatar_dedupe_drift_smoke.py` **从主程序 import** 原函数 + testbench copy 对比**字节级相等的纯函数 body** (hash compare), 漂移即 FAIL — 这样 "复用主程序 pure helper" 的承诺在"不 import main_logic"前提下仍然落地. | ❗ 高 (边界纪律) |
| **R5** | L14 coerce 必须 surface | `_normalize_avatar_interaction_payload` 对非法 intensity / touch_zone / text_context>80字符 **静默 coerce**, Day 1 handler 未 surface | P24 Day 2 `messages_writer.append_message` 已踩过一次, 当时教训是"coerce 必须返 caller + caller surface". 当前 P25 Day 1 `SimulationResult` 只有 `accepted/reason/instruction/memory_pair/persisted/dedupe_info/assistant_reply`, **没** `coerce_info` 字段, 等于又一次"coerce = silent". | `SimulationResult` 加 `coerce_info: list[CoerceEntry]` 字段, `CoerceEntry = {field, original, coerced, reason}`; handler 从 `_normalize_avatar_interaction_payload` 返回值提取 coerce diff (如需 helper 改签名返三元组 则本阶段做). Day 2 UI 面板在 "Persistence decision" 栏下加一个 "Payload coerce" 子区 (若 coerce_info 非空才显示), 显示黄色 warning badge. | ❗ 高 (L14 重踩点) |
| **R6** | L19 mutation 不能 abort | §3 Day 2 "Abort 支持 (api.js signal/abort, 中途取消 LLM 调用)" | L19 明言 "严禁给 mutations (POST/PUT/DELETE) 用 AbortController — 中途 abort 会让服务端状态模糊 (commit 了还是没 commit?)". `POST /api/session/external-event` 是 mutation (写 messages + 写 recent + 写 dedupe cache + 写 diagnostics ring, 四处副作用). abort 中途等于部分副作用落地部分没落地, 无法回滚. | §3 Day 2 改: "**不**用 AbortController; [Invoke event] 按钮在 in-flight 期间 `disabled=true` + 显示 spinner + toast 'event 正在投递, 请稍候'; 若用户真要中止必须等当前 LLM 调用超时 (api.js 默认 90s) 或手动刷新页面". 面板 [Clear dedupe cache] 按钮**可以**用 signal/abort (是 GET-like 幂等调用). | ❗ 高 (L19 重踩点) |

**第七轮 self-audit (2026-04-23 §A commit 后, 追加 4 条针对 §A 自身的复审矫正)**:

| # | 审核维度 | 问题原文 (§A 原表述) | 正确语义 (基于主程序代码复核) | 落地处置 | 严重度 |
|---|---|---|---|---|---|
| **R7** | Dual-Mode 默认反转**仅适用于 avatar**, 不是"一刀切" | R1c "§2.4 默认反转 recent_only" 被当成三类系统都适用 | 主程序 `cross_server.py` L571 `if had_user_input_this_turn and ... /cache`: **只有**带用户输入的 turn 才写 /cache. agent_callback/proactive 自身不写 /cache, 等下一轮 user turn 一起入. 所以 **avatar = 独立 memory path (recent_only 默认对) / agent_callback + proactive = 入 chat_history 但不自动入 recent (session_only 默认对)**. 两类默认**不同方向**. | §A.5 R1c 精确化: 默认反转**仅对 avatar**; agent_callback/proactive 保持原 session_only 默认. §2.4 按"三类分别说明"重写. 派生 §4 新决策 "三类 persist default 表". | ❗ 高 (语义精度) |
| **R9** | merge_unsynced_tail_assistants 连续 proactive/agent_callback reply 合并仅保留最后一条 | §A 未覆盖, §3 Day 1 也未明示 | 主程序 `cross_server.py::merge_unsynced_tail_assistants` (L80-118): tail 如果连续 ≥2 条 assistant 且都在 last_synced_index 之后, **丢弃前 N-1 条, 只保留最后一条** ("精简了 N 条未同步的连续主动搭话消息, 仅保留最后一条"). 这是**语义契约**级别的行为 (不是运行时机制), 对"连点 3 次 proactive 应该看到几条 reply" 的直觉有实质影响. | testbench 决策: **不**复现 merge (复杂度 ↑ + 破坏"每次点击=一条 turn"的直觉; tester 手工点击间隔天然 ≥ 人类反应时间, 等价 "user 在中间 interleave 了操作", 不满足主程序 merge 触发前提 "连续未被 user turn 打断"). §4 新加决策 "4.6 merge_unsynced_tail 不复现 (OOS 理由)". | ⚠ 中 |
| **R10** | SimulationResult.reason 候选值不明 | §3 Day 1 `SimulationResult` 字段仅列出 `reason` 字段名, 未明示哪些 reason 值是"语义契约"层面需要复现 | 主程序 `handle_avatar_interaction` 有 12 种 reject reason: `invalid_payload` / `duplicate` / `cooldown` (600ms 运行时窗口) / `voice_session_active` (OmniRealtimeClient) / `no_websocket` (连接状态) / `session_start_failed` (自启失败) / `not_text_session` (模式校验) / `busy` (text session locked) / `speak_cooldown` (另一个运行时窗口) / `error` (prompt_ephemeral 异常) / 以及 agent_callback/proactive 各自的少量对应 | §4 新加表 "4.7 SimulationResult.reason 复现表". **复现的 (语义契约)**: `invalid_payload` / `duplicate` / `busy` / `llm_error`. **不复现的 (运行时机制)**: `cooldown` (600ms) / `speak_cooldown` / `voice_session_active` / `no_websocket` / `session_start_failed` / `not_text_session`. | ⚠ 中 |
| **R13** | agent_callback 触发路径不明 | §3 Day 1 未说明复现路径 A 还是 B | 主程序 agent_callback 有**两条触发路径**: **路径 A** = `core.py` L3623-3631, 在 user turn 发送**前**顺带 inject (`prompt_ephemeral(_loc(AGENT_CALLBACK_NOTIFICATION,...) + ctx)`); **路径 B** = `_deliver_agent_callbacks_text` L3051-3063, 作为**独立 proactive turn** 触发 (定时/idle). 两条路径都是同一 instruction + `prompt_ephemeral` 调用, 但触发时机不同. | testbench 决策: **复现路径 B** (独立 turn, tester 手动 click). **不复现路径 A** (会要求 chain 到 user text send, 破坏"简单事件驱动"哲学). §2 新加小节 "2.7 agent_callback 路径选择" 或在 §3 Day 1 交付物描述里明示 simulate_agent_callback 对应路径 B. | ❗ 高 (路径歧义) |

**矫正 11 条 (原 7 + self-audit 补 4) 的分级** (*第八轮漂移诊断前*的快照, 留痕):
- ❗ 高 (7 条): R1b / R1c / R4 / R5 / R6 / R7 / R13 — 都是"语义错位 / 边界破坏 / 会重踩历史教训 / 路径歧义"类, 不改开工就错.
- ⚠ 中 (4 条): R1a / R3 / R9 / R10 — 分类清晰化 + 资源 cap 补齐 + 语义契约边界明示.
- ℹ 低 (1 条): R2 — 条件满足, 本地 ✅ 合规; 只在 UI 扩"事件摘要"视组件时再做.

> **⚠ 第八轮漂移诊断 (2026-04-23)**: 上述 11 条在用户指示 "审查时必须持续锚定设计初衷" 后作 drift 重审, 发现 **3 条是目标漂移产物要撤回 + 1 条降级 + 1 条合并不独立**. 见 §A.7. **开工请以 §A.8 为准**, 不是本 §A.1 表 + 本分级.

**R8/R11/R12 (细节补完, 不独立成矫正点)**:
- **R8** `/cache` endpoint 调用 `recent_history_manager.update_history(..., compress=False)`, 语义等价 testbench `CompressedRecentHistoryManager.update_history(compress=False)` ✅. 需在 §3 Day 3 验收点明示: avatar memory pair 入 recent.json **不**自动触发 facts.extract (对应主程序 /cache 不触发 /process), 要测长期记忆必须 tester 手动 trigger facts.extract / reflect. (已在 Day 3 步骤 6-7 隐含, 明写一下更清晰).
- **R11** R5 的 coerce surface 至少应覆盖 5 处 silent coerce: `intensity` (`_normalize_avatar_interaction_intensity` 纠正) / `touch_zone` (非白名单→空串) / `text_context` (`_sanitize_avatar_interaction_text_context`) / `pointer` (非法→None) / `timestamp` (非法→`time.time()*1000` fallback). §A.5 R5 文案完善补此列举.
- **R12** `_sanitize_avatar_interaction_text_context` 做大量 silent 清洗 (换行归一化 / 非 printable→空格 / 去 list 前缀 / 合并连续空白 / 80 字符截断). coerce_info 为避免爆表, **per-field 合并记一条** `{field: "text_context", reason: "text_sanitized", original: "...", coerced: "..."}`, 不展开子步骤.

### §A.2 框架回顾 (24 元教训 + 57 原则中 P25 必读子集)

从 `LESSONS_LEARNED §7` 24 条元教训筛出 **P25 直接相关 8 条 / 间接相关 3 条 / 暂不相关 13 条**:

#### 直接相关 (Day 1 编码时要查的)

| # | 元教训 | P25 具体落地点 |
|---|---|---|
| **L6** | 多源写入 choke-point (单源 vs 多源是纸面原则成败分水岭) | session.messages 的所有入口 (包括 avatar memory pair 的 mirror_ui 分支) 必须走 `pipeline/messages_writer.append_message`. 裸 `.append` 禁用. P24 Day 11 已加 §3A A17. |
| **L14** | Coerce 必须 surface (silent coerce = silent fallback) | R5 矫正直接对应. `SimulationResult.coerce_info` 字段必加. |
| **L17** | Feature flag "requested/applied/fallback_reason" 三元组 | dual-mode memory write 的 `mirror_ui` 开关是 feature flag. 若 tester 勾选 mirror_ui 但遇到"session.messages 已达 max_messages 拒写" 之类降级场景, 必须返 `{requested: true, applied: false, fallback_reason: "max_messages_reached"}` 三元组, 不能 silent drop. |
| **L19** | Last-click-wins vs mutation | R6 矫正直接对应. [Invoke event] 不用 AbortController. |
| **L20** | 同族架构空白 sweep | P25 新增 `POST /api/session/external-event` mutation. 三问: (a) 会不会触发 session:change 级联风暴? — 不会 (event 只变 messages, 不动 session.name/id/model). (b) 高频连点会不会像 New Session 卡死? — 不会 (按钮 disabled 兜住). (c) error 路径有没有清残留 meta? — `SimulationResult.accepted=false` 时 dedupe cache 必须回滚 (主程序 cross_server.py L480-484 `dedupe_prior_entry` 保护机制). 照抄. |
| **L23** | 生成器三分类 (请求-响应 / 真 generator / Template Method) | 三类 handler (avatar / agent_callback / proactive) 都是"请求-响应 async def" 类型 (返 `SimulationResult`), 不是 generator. A6 不适用, A5 不强制 (不走 SSE). A9 不适用 (非 Template Method). 但若未来 P25.1 扩 "批量事件注入 SSE 流式预览", 会升级为"真 async generator" 要补 A5/A6. |
| **L24** | 资源上限 UX 降级四问 | R3 矫正直接对应. dedupe cache 加 soft cap=100. |
| **L25** | 语义契约 vs 运行时机制 (P25 本阶段元教训) | P25 §2.1 原则. R1a/R1b/R1c 都是此原则的具体应用深度展开. |

#### 间接相关 (审查时辅助参考)

| # | 元教训 | P25 参考价值 |
|---|---|---|
| **L9** | X 受 Y 守护→先怀疑 Y 漏守 | 若 Day 3 smoke 发现"dedupe 没生效" bug, 先怀疑 `_should_persist_avatar_interaction_memory` 的调用入口漏守, 而不是去质疑 main_logic 实现. |
| **L11** | 方法论立即扩大 | R1a "冷却三分类" 派生 L29 候选元教训, 本阶段立即派生立即记录, 不推 P26. |
| **L18** | innerHTML 清不了 state listener | Day 2 前端面板的 state.on 订阅 (如订阅 `session:change` 切换 session 清 dedupe cache UI) 必须有 `host.__offSessionChange` teardown. 在新面板 mount 时立即加. |

#### 暂不相关 (P25 范围外, 但交付后 P26 README 时再审)

L1-L5 (整体方法论, 已内化) / L7 (纸面原则静态守) / L8 (合规率 KPI) / L10 (决策树) / L12 (RAG 灯) / L13 (sweep checklist) / L15 (restore 保留主键) / L16 (前后端 shape 双端一致) / L21 (hidden 属性) / L22 (opts 覆盖陷阱) / L26/L27 (P24 Day 10 派生, 已在 R1a/R3 应用).

#### §3A 57 原则中 P25 直接相关子集

| 组 | 编号 | 原则 | P25 应用点 |
|---|---|---|---|
| A | **A1** | 软错 / 硬错分离 | `SimulationResult.accepted=false` = 软错 (200 OK + reason); payload 格式根本非法 = 硬错 400. |
| A | **A5** | SSE 先 yield error 帧 | Day 1 不走 SSE, 不适用 (但若未来 P25.1 做流式预览要加). |
| A | **A7** / **A17** | messages 单 choke-point | 已覆盖, R1b 之后 instruction **不**走 choke-point (因为不入), reply 走. |
| A | **A10** | JSON body bool/num 归一化 | `mirror_ui: bool` + dedupe rank int 必须走归一化 helper. |
| A | **A12** | HTTPException detail = dict | `400 {detail: {code: "PAYLOAD_INVALID", message: "..."}}` 形式, 不用裸 str. |
| A | **A15** | api_key 从对外序列化 redact | `SimulationResult` 不含 model_config (model_config 在 session 里, 不在返回体). ✅ 天然合规. |
| A | **A18** | Choke-point 必配静态验证 | `p24_lint_drift_smoke.py` 的 `single-append-message` 规则自动守 R1b 矫正 (reply 走 append_message). ✅ 已存在的 cursor rule 自动覆盖本期. |
| B | **B1** | state.X 变 → renderAll | Day 2 面板每次 Invoke event 后 state.set(...) → renderAll. 新面板的 mount 必须接 `session:change` / `model_config:change` 事件. |
| B | **B3** | 数据子集订阅 | 面板 mount 后订阅 `session:change` 用于重置 dedupe cache 视图. |
| B | **B7** | 跨 workspace 导航 force-remount | 面板在 Chat workspace, 切走 → 切回 必须按 B7 force-remount. |
| B | **B12** / **B14** | emit 前 grep + 双向一致 | R2 矫正直接对应. |
| B | **B13** | 清零类必 reload | 不适用 (面板无"清零"按钮; Clear dedupe cache 只清 session._avatar_dedupe_cache, 非清零语义). |
| B | **B15** | placeholder 条件渲染 | "未触发任何事件" 空态必须 renderAll 条件 append, 不 `display:none`. |
| C | **C3** | `append(null)` | Day 2 面板 renderAll 若拼 `host.append(maybeNullEl)` 必防御. |
| C | **C5** | min-width:0 父链 | 面板嵌 Chat workspace grid, 父链所有层要 `min-width: 0`. |
| D | **D1** | Promise cache lazy init | 面板任何 lazy load (如 lazy load prompt_proactive.py 的 dispatch table metadata) 必走 Promise cache (P24 Day 12 欠账清返刚用过的 skill). |
| D | **D3** | 临时运行态挂 session + 白名单 filter | `session._avatar_dedupe_cache` 必进 Session `__post_init__` + `describe()` / `dict()` 的白名单黑名单 (走 Day 6A 定的白名单). session 销毁自动清, 不加 cleanup 钩子. |
| E | **E1** | jsdom mount smoke | Day 3 必跑面板 jsdom mount smoke (虽然 Day 3 p25_external_events_smoke 已含 router 集成, 但 mount smoke 要额外跑, 参照 p21_3 / p22 pattern). |
| E | **E3** | 阶段开工前 Full-Repo Pre-Sweep | Day 1 开工第一步: 跑全量 9/9 smoke + `p24_lint_drift` 作 baseline. 当前状态: 已在本 agent 欠账清返 done 后跑过一轮全绿 (9/9 + lint drift), 可直接开工不必再跑. |
| F | **F7** | Fail-loud 不 silent fallback | R5 矫正直接对应. |
| G | **G1** | Testbench "检测不改"边界 | P25 不改主程序 prompts, 不改 memory 写入语义, ✅ 合规. |

### §A.3 上游 Merge Delta (27 条对 P25 的影响点)

2026-04-22 merge commit `cb394ab` 并入上游 27 条, 与 P25 相关文件的精确 delta:

| 文件 | Delta | 对 P25 的影响 | 需在 P25 做什么 |
|---|---|---|---|
| `config/prompts_avatar_interaction.py` | **零变更** ✅ | 9 个 helper + 7 个常量表全稳定 | 直接 import 复用, 无风险 |
| `main_logic/cross_server.py` | **零变更** ✅ | `_should_persist_avatar_interaction_memory` 函数体稳定 | R4 矫正: copy 到 testbench, 不 import (边界纪律) |
| `config/prompts_sys.py` | **+34 / -1** | `AGENT_CALLBACK_NOTIFICATION` 5 语言表**未扩 es/pt**, 只在 TRANSLATION_INSTRUCTION / TRANSLATION_REQUIREMENTS / TRANSLATION_LANG_NAMES / MEMORY_MEMO_WITH_SUMMARY 等加了 es/pt; `_loc()` 新增 `_SILENT_FALLBACK = {'es', 'pt'}` — **当 session.persona.language ∈ {es, pt} 时 AGENT_CALLBACK_NOTIFICATION 静默回退到 en, 不打 WARNING** | Day 3 smoke 加 **`persona.language=es` → agent_callback instruction 应是英文**的断言 (验静默回退路径). 不是 bug (LLM 能理解英文), 但 tester 需知悉 |
| `config/prompts_proactive.py` | **+4 / -0** | `_normalize_prompt_language()` 加 4 行 es/pt 回退到 en 分支 | 同上, smoke 补 `persona.language=es` → proactive prompt 应是英文 |
| `main_logic/core.py` | **+1 / -1** | `prepare_proactive_delivery(min_idle_secs=30.0)` 改 `=10.0` | testbench `simulate_proactive` OOS 不复现实时流, **无影响**. §1.3 OOS 可补"`min_idle_secs` 阈值 OOS". |
| `memory/recent.py` | **+5 / -5** | `json.loads` / `messages_from_dict` / `messages_to_dict` 改走 `asyncio.to_thread`. 性能/不阻塞事件循环改进, **API 签名不变** | 无影响, 继续复用 |
| `memory/persona.py` | **+82 / -20** | `PersonaManager.aensure_persona / aadd_fact / arecord_mentions / aupdate_suppressions` 全部加 **per-character `asyncio.Lock`** 串行化, 解决 P2.a.2 的 persona.json 竞写 | testbench **单会话单 event loop**, 天然零竞争, 零影响 ✅. 但 Day 3 smoke 若 **asyncio.gather 并发** 跑 aadd_fact 会测到新的串行行为, 原期望"并发全成功"要改为"**按 lock 序列成功**"(实际可能这么测的 smoke 都没有, 故此风险极低) |
| `memory/reflection.py` | **+143 / -28** | `ReflectionEngine` 同样加 per-character asyncio.Lock. 同时新增 `_reflection_id_from_facts()` 确定性 id 生成 (sha256 前 16 hex = 64 bit), `save_reflections + mark_absorbed` 可基于 id 幂等去重 | 零影响 (testbench 调 synthesize_reflection 时 lock 自动生效). Day 3 可尝试验 "同 facts 两次合成产出相同 reflection_id" 的幂等性, 属于 memory_runner smoke 的 bonus 点, 不强制 |
| `memory_server.py` | **+262 / -20** | 新增 `cursor_store` + `outbox` 全局 singleton, 引入 `from memory.cursors import CursorStore` / `from memory.outbox import Outbox` | testbench **不 import memory_server**, 只 `from memory import (CompressedRecentHistoryManager, ..., ReflectionEngine)` 等类, **无直接影响**. 但 Day 3 跑"端到端 recent.compress → facts.extract → reflect" 时若主程序的 memory 路径现在走 outbox 双写 (event_log + cache), testbench 复现的只是 cache 侧, 不含 outbox — 符合语义契约 vs 运行时机制原则 (outbox 是可靠性机制, 非语义). ✅ |
| `memory/cursors.py` (NEW) | +126 | `CursorStore` 单文件 cursor 持久化 (P0 resilience) | testbench 不用, 无影响 |
| `memory/outbox.py` (NEW) | +239 | `Outbox` 可靠事件投递 (P1 resilience) | testbench 不用, 无影响 |
| `memory/event_log.py` (NEW) | +549 | `EventLog / Reconciler` (P2.a resilience, memory_evidence RFC) | testbench 不用, 无影响. **但**若未来 P27+ 需要测 "memory-event-log 写语义", 需独立新 phase |

**上游 delta 总结**: 对 P25 **零阻塞 + 1 个 Day 3 smoke 补测点** (`persona.language=es/pt` 静默回退) + **1 个 OOS 扩充** (`min_idle_secs=10s` 阈值). 主程序新 memory resilience (outbox / event_log / cursors) 不在 P25 scope.

### §A.4 派生的新元教训候选 (L28-L30)

- **L28 "跨阶段推迟项必须双向回扫"** (P24 Day 12 欠账清返派生, 已在 AGENT_NOTES §4.27 #108 Day 12 段 + PROGRESS §P24 Day 12 欠账清返段登记). 每阶段收尾跑 `rg "推迟至 Day X"` 双向核对. P25 Day 3 收尾必跑.
- **L29 "冷却语义三分类"** (R1a 派生, 候选, 待 P25 落地后观察是否稳定抽象). 任何"N 秒内不复触发" 行为必须先分类: (a) 实时流抖动 / (b) 语义去重 / (c) N 秒窗口禁复触发, 再决定 testbench 是否复现. L26 "yield 型 API 三分类" 在时序维度的延伸.
- **L30 "外部系统 pure helper 跨 package copy + drift smoke 而非 import"** (R4 派生, 候选). 当 pure helper 所在 package 有重副作用 (aiohttp/ssl/event_bus) 时, 跨 package **import** 不如 **copy + drift smoke** — 前者带入副作用, 后者边界清晰 + hash compare 自动守漂移. 适用场景: 测试生态 / adapter 层 / plugin 沙盒.

### §A.5 Design Review Gate 结论 (第七轮快照 / *historical*)

> ⚠ **此节为第七轮结束时的快照, 保留留痕**. 第八轮漂移诊断 (§A.7) 已撤回 R7 / R13, 部分撤回 R1c, 降级 R1b, 合并 R9. **开工请以 §A.8 为准** (§A.7 后新加), 而不是本 §A.5.

本轮 design review gate 输出以下**开工前必做清单** (原 7 条 + 第七轮 self-audit 补 4 条 = 总计 11 条矫正 + 3 处细节补完):

1. **§3 Day 1 必改 6 处**:
   - Acceptance 第 2 条改: instruction 只入 wire, 不入 session.messages. **三类 reply 的去向分别明示**: avatar=独立 memory path 不入 session.messages; agent_callback/proactive=reply 入 session.messages 但不自动 /cache (对齐主程序 "had_user_input_this_turn") (R1b 精确化 + R7)
   - 新增 `pipeline/avatar_dedupe.py` (copy `_should_persist_avatar_interaction_memory` + `AVATAR_INTERACTION_MEMORY_DEDUPE_WINDOW_MS` 常量) (R4)
   - `_AvatarDedupeCache` 加 `_MAX_ENTRIES=100` + LRU + 溢出 once-notice (R3)
   - `SimulationResult` 加 `coerce_info: list[CoerceEntry]` 字段, handler 覆盖 5 处 silent coerce (intensity / touch_zone / text_context / pointer / timestamp, per-field 合并一条) (R5 + R11 + R12)
   - `simulate_agent_callback` 交付物明示对应**主程序路径 B** (独立 proactive turn), 不复现路径 A (顺带 inject 到 user turn) (R13)
   - `SimulationResult.reason` 字段注释明示"复现的是语义契约层 reason: invalid_payload/duplicate/busy/llm_error; cooldown/voice_session_active 等运行时层 reason 不复现" (R10)
2. **§2.4 Dual-Mode Memory Write 默认值 — 三类分别说明 (不是一刀切)**:
   - **avatar**: 默认 = **recent_only** (独立 memory path, 仅写 recent.json, 对齐主程序 `/cache` 独立路径); 可选 = **mirror_ui** (额外 mirror 到 session.messages 让 tester 在 Chat 视图看到 memory pair) (R1c + R7)
   - **agent_callback / proactive**: 默认 = **session_only** (reply 入 session.messages, 不自动入 recent, 对齐主程序 `had_user_input_this_turn` 条件); 可选 = **mirror_recent** (显式模拟 "下一轮 user turn 顺带 /cache" 行为) (R7)
3. **§3 Day 2 必改 1 处**:
   - 删 "Abort 支持 (api.js signal/abort, 中途取消 LLM 调用)"
   - 改为 "[Invoke event] 按钮 in-flight 期间 disabled=true + spinner + toast '事件正在投递, 请稍候'" (R6)
4. **§3 Day 3 必加 3 条 smoke + 1 条验收点说明**:
   - `p25_avatar_dedupe_drift_smoke.py` — from 主程序 import `_should_persist_avatar_interaction_memory` vs testbench copy hash compare (R4)
   - `persona.language=es/pt` 断言 → agent_callback instruction 是英文 (上游 delta)
   - `persona.language=es/pt` 断言 → proactive prompt 是英文 (上游 delta)
   - 验收点步骤 6 明示: avatar memory pair 入 recent.json **不**自动触发 facts.extract (主程序 /cache 语义), tester 必须手动 trigger 才能观察长期记忆影响 (R8)
5. **§1.3 OOS 扩 2 条**:
   - 冷却三分类明示 (R1a)
   - `min_idle_secs=10s` 阈值 OOS (上游 core.py 的 prepare_proactive_delivery)
6. **§4 关键技术决策必补 2 条**:
   - **4.6 merge_unsynced_tail_assistants 不复现 (OOS)**: 主程序连续 proactive/agent_callback reply 丢弃前 N-1 条仅保留最后一条, testbench **不复现** (tester 手工点击间隔 ≥ 人类反应时间, 等价 "user 在中间 interleave 了操作", 不满足主程序 merge 触发前提); 换言之, testbench 每次 `simulate_*` 都是独立 turn, session.messages 逐条追加 (R9)
   - **4.7 SimulationResult.reason 复现表**: 复现 (`invalid_payload` / `duplicate` / `busy` / `llm_error`) vs 不复现 (`cooldown` / `speak_cooldown` / `voice_session_active` / `no_websocket` / `session_start_failed` / `not_text_session`) 明示 (R10)
7. **§5 风险表补 1 条**:
   - "高频事件 tester 点爆 dedupe cache" (R3 已处置, 记录到风险表)
8. **§3A 可能新增原则 (待 P25 交付后观察)**:
   - 候选 H3 "外部系统 pure helper cross-package copy > import (有重副作用时)" (L30 的铁律化)
   - 候选 A19 "SimulationResult-like 响应对象必须含 coerce_info / fallback_reason 字段族" (L14/L17 的 request-response 扩展)

### §A.6 开工门禁重核 (第七轮快照 / *historical*)

> ⚠ 第七轮时基于 §A.5 的 11 条设过 §8 门禁; 第八轮漂移诊断后门禁条件变更, 最终版见 **§A.9** (本节后面). 本节仅保留历史痕迹, 不是当前门禁.

(第七轮此处曾要求 "用户审读 11 条矫正 + 3 条补完", 其中 R7 / R13 已在 §A.7 判定为目标漂移撤回, R1c 部分撤回, R9 合并, R1b 降级 → 该门禁已被 §A.9 取代)

### §A.7 第八轮漂移诊断 (2026-04-23)

> **触发事件**: 用户 dev_note 指示 — "**AI 的代码设计思想真的有延续性吗? 改着改着语义不会漂移了吧. 我应该要求 AI 保持设计思想、设计目的和语义的一致性, 这样出了问题也更好排查, 要求 AI 保持语义描述的精确性和详细性, 并且保证设计思想的连贯性 (这个模块设计出来是干什么的? 有什么功能和目的?)**". 本节回头诊断 §A.1 11 条矫正中哪些是**正当的精度提升**, 哪些是**目标漂移产物** (审查时把"对齐主程序运行时行为"悄悄引入了设计目标, 违反了 P25 §2.1 "语义契约 vs 运行时机制" 的初衷).

#### §A.7.1 设计初衷锚点 (任何矫正必须以此为准)

P25 原版 §1.1 §2.1 §2.2 §2.4 §2.6 的设计思想**必须**保持连贯, 任何矫正不得违反:

| 锚点 | 原文出处 | 不得漂移的方向 |
|---|---|---|
| **四个问题** | §1.1 | 模型回复稳健 / recent 压缩识别 / facts.extract 抽出 / dedupe 实现 — 这四件事就是 testbench 要回答的**全部**问题; 不引入第五个"复现主程序 runtime 行为"的目标 |
| **语义契约 vs 运行时机制** | §2.1 | testbench 只复现**影响是什么**, 不复现**怎么投递**. 主程序 /cache 写不写 / session.messages 合不合并 / 两条触发路径选哪条 — 这些是**运行时机制**, OOS |
| **Tester-Driven** | §2.2 | testbench 的触发来源是 **tester 手动点按钮**, 不是"复现主程序的 A 路径或 B 路径". "testbench 触发路径" 本身就等于 "tester 手动" 一条 |
| **Dual-Mode 默认 session_only** | §2.4 | 默认 session_only 的**初衷是 testbench 工作流** ("tester 先看 session.messages 里事件的 user note + reply 各是什么, 再手动 trigger compress/extract/reflect 观察下游"), **不是** "对齐主程序是否自动写 /cache" |
| **三类一个抽象** | §1.2 + §2.6 | 三类 (avatar / agent_callback / proactive) 用同一 `POST /api/session/external-event` + 同一 `SimulationResult` dataclass; 不按"主程序实际行为差异"把三类分派到不同模式 |

#### §A.7.2 11 条矫正的漂移度重审

| # | 原标 | 重审结论 | 原因 | 处置 |
|---|---|---|---|---|
| R1a | ⚠ 中 | **保留 ⚠ 中** | 精度提升. §1.3 原本含糊提到"不冷却窗口 600/1500ms", 三分类明示让 OOS 边界清楚, 不漂移. | 保留, §1.3 OOS 扩 |
| R1b | ❗ 高 | **降级 ❗→⚠** | 原版 §1.2 已明示 "memory pair 入 recent" 等, 并未要求 instruction 入 session.messages. R1b 消除的是**不存在的歧义**, 不是高危 bug. 方向对, 严重度被高估. | 保留, 降级为 ⚠ |
| **R1c** | ❗ 高 | **部分撤回** | "avatar 有独立 memory path" 的观察**对**; 但我把这个观察用于**反转 §2.4 默认值**, 是目标漂移. §2.4 默认 session_only 是 testbench 工作流决定, 不应因 "主程序 /cache 独立 path"而反转, 违反 §2.1 + §2.4. | **撤回"默认反转"部分**, 保留"avatar 走独立路径"的观察作为 §3 Day 1 实现细节提示 (memory_writer 对 avatar 可有独立的 mirror-to-recent 参数对齐主程序独立 path 语义, 但**默认值**仍按 §2.4 原版 session_only) |
| R2 | ℹ 低 | **保留 ℹ 低** | 仅观察, 无动作, 不漂移. | 保留 |
| R3 | ⚠ 中 | **保留 ⚠ 中** | 精度提升. L27 资源上限四问正当应用. 不漂移. | 保留 |
| R4 | ❗ 高 | **保留 ❗ 高** | 消除**原版自身歧义** (§1.2 暗示 import vs §5 风 copy). 选定 copy + drift smoke, 是架构纪律落地, 不漂移. | 保留 |
| R5 | ❗ 高 | **保留 ❗ 高** | L14 "coerce 必须 surface" 合规, 不漂移. | 保留 |
| R6 | ❗ 高 | **保留 ❗ 高** | L19 "mutation 不能 abort" 合规, 不漂移. | 保留 |
| **R7** | ❗ 高 | **撤回** | 在 R1c 目标漂移基础上**加错**: 把 三类 default 应不同 引入 P25. 原版 §1.2 + §2.4 + §2.6 明确要求三类**共用一个 default**, 理由是 testbench 工作流的**一致性** (tester 不需要记忆 哪一类走什么默认). 我用"主程序 had_user_input_this_turn 条件"推导出 三类默认不同, 是**复现主程序 runtime** 的目标漂移. | **撤回**. 三类统一按 §2.4 原版默认 session_only, UI 面板文案描述保持一致. |
| R9 | ⚠ 中 | **不独立成 §4.6, 合并入 §1.3 OOS** | 结论对 (merge 不复现), 但它是 §1.3 "不实现实时流基础设施" 的自然推论 (merge 是连续自动触发场景的 runtime 优化), 不需单独占 §4 决策. 独立出来会让 §4 膨胀 + 把 runtime 行为讨论带进 §4. | 降为 §1.3 OOS 补一行 "不复现连续主动搭话合并 (merge_unsynced_tail_assistants 是实时流场景优化)" |
| R10 | ⚠ 中 | **保留 ⚠ 中** | 精度提升. 明示 "哪些 reason 是语义契约层 / 哪些是 runtime 层" 恰好是 §2.1 的直接应用. 不漂移. | 保留 |
| **R13** | ❗ 高 | **撤回** | 原版 §1.2 行 B 行明示复现 `drain_agent_callbacks_for_llm` 的**拼接逻辑**; §2.2 明示触发 = tester 手动. 原版根本不存在 testbench 复现路径 A 还是 B"的选择题. R13 是 AI 审查时的过拟合 — 把主程序看到的两条路径当成 testbench 需要选一条, 生造出歧义再自己解决, 违反 §1.2 + §2.2. | **撤回**. 在 §3 Day 1 `simulate_agent_callback` docstring 里加一句 "触发 = tester 手动点, 不对应主程序何种触发时机" 即可 |

**净结果**: 11 条中, **6 条正当的精度提升** (R1a / R3 / R4 / R5 / R6 / R10) + **1 条降级** (R1b ❗→⚠) + **1 条部分撤回** (R1c 保留观察 / 撤回默认反转) + **1 条合并不独立** (R9 入 §1.3) + **2 条完全撤回** (R7 / R13) + **1 条仅观察** (R2).

#### §A.7.3 漂移根因 (为什么会发生?)

1. **审查时工具选择偏向 "代码比对"**: 我在第七轮 self-audit 时用 grep + read 把主程序 `core.py` / `cross_server.py` / `memory_server.py` 各挖了一遍, 发现"主程序这里这样, 主程序那里那样" — 工具给出的信息**全是 runtime 实现细节**, 容易让我把 testbench 应该跟主程序一致 当成默认假设.
2. **缺少"设计初衷锚点" 的 guard**: 第七轮每发现一条主程序行为差异, 没有回过头问 "这对 P25 §1.1 的四个问题有影响吗? 违反 §2.1 语义契约 vs 运行时机制吗?". 只问了 "主程序是什么样, testbench 对不上吗?". 方向错.
3. **KPI 错位: 追求"发现问题数" 而非"守住初衷数"**: 第七轮结束时我得意地宣布"追加 4 条矫正", 但这 4 条里 2 条 (R7 / R13) 其实是**目标漂移产物**. 没有"保持初衷不漂移"这个质量指标, 就只会在"发现了多少问题"上堆数字.

#### §A.7.4 派生新元教训候选 L31

**L31 (候选) "审查时必须持续锚定设计初衷, 不得悄悄引入新目标"**:

- **场景**: 在 设计草案审查 阶段 (meta-audit / self-audit / design review), 当 AI 用 grep/read 挖主程序代码时.
- **失败模式**: 审查工具返回大量"主程序 runtime 实现细节", AI 不自觉地把 testbench 应该跟主程序 runtime 一致 引入矫正清单, 实际违反原设计的"语义契约 vs 运行时机制"边界.
- **症状**: 矫正清单看起来越来越精细, 但**与原设计初衷 §1 §2 的连贯性逐步断裂**. 某条矫正单独看都对 (主程序确实那样), 但**组合起来**会把原设计目标改掉.
- **防御规则**:
  1. 每轮审查开头先明写"**本轮不得引入的新目标**" (如 P25 = "不得把 '复现主程序 runtime 行为' 引入目标"). 列在审查笔记顶部.
  2. 每条候选矫正**必问三问**: (a) 这条是在回答 §1 的哪个目标问题? (b) 违反了 §2 哪条原则吗? (c) 如果原版 §1 §2 的作者在场, 他会说 这是精度提升 还是 你改了我的目标?
  3. 审查产物的 KPI 是 **守住初衷的同时, 发现多少真正的精度缺口**, 不是 **发现了多少问题**.
- **来源**: 本次 P25 §A 第八轮漂移诊断 (R1c 部分撤回 / R7 / R13 完全撤回) 派生.
- **关联**: L25 (语义契约 vs 运行时机制) 在审查流程维度的延伸 — L25 管 什么该复现, L31 管 审查时怎么不丢掉 L25.

正式归档到 `LESSONS_LEARNED §7` 待 P25 Day 3 交付时一并落.

### §A.8 Design Review Gate 结论 (漂移修正后 · 最终开工清单)

> 本节是第八轮漂移诊断后的**最终开工清单**, 取代 §A.5 作为 §8 开工门禁的校验依据. 漂移痕迹保留在 §A.1 / §A.5 / §A.7, 方便后续复盘 "审查时目标漂移" 现象.

**开工前必做**: **8 条有效矫正** + **3 条细节补完** + **1 条观察性提示**, 按原设计思想 (§1.1 四问 / §2.1 语义契约 vs 运行时机制 / §2.2 tester-driven / §2.4 默认 session_only / §2.6 三类一个抽象) 收敛:

1. **§3 Day 1 必改 4 处**:
   - 新增 `pipeline/avatar_dedupe.py` (copy `_should_persist_avatar_interaction_memory` + `AVATAR_INTERACTION_MEMORY_DEDUPE_WINDOW_MS` 常量); 主程序 `_should_persist_avatar_interaction_memory` 是 pure helper, 其所在 package 有重副作用, 按 L30 "copy + drift smoke" 而非 import (R4 配)
   - `_AvatarDedupeCache` 加 `_MAX_ENTRIES=100` + LRU + 溢出 once-notice (L27 资源上限四问) (R3 配)
   - `SimulationResult` 加 `coerce_info: list[CoerceEntry]` 字段, handler 覆盖 5 处 silent coerce (intensity / touch_zone / text_context / pointer / timestamp, per-field 合并一条); `coerce_info` 总条数入 ring (L14 "coerce 必须 surface") (R5 配 + R11/R12 细节)
   - `SimulationResult.reason` 字段注释明示: **复现** (语义契约层) = `invalid_payload` / `duplicate` / `busy` / `llm_error`; **不复现** (运行时机制层) = `cooldown` / `speak_cooldown` / `voice_session_active` / `no_websocket` / `session_start_failed` / `not_text_session` (§2.1 直接应用) (R10 配)

2. **§1.2 Acceptance 第 2 条精确化** (降级自 ❗, 仅消除潜在歧义):
   - 明示 "instruction 只入 wire 给 LLM, **不**入 session.messages". 原版已隐含此点 (§1.2 行 B/C 行), 第八轮确认后作为显式澄清 (R1b 配, ❗→⚠)
   - 三类 reply 去向**仍按原版**: avatar reply 走独立 memory path (入 recent.json); agent_callback / proactive reply 入 session.messages; 这是**原版设计**, 不是新增决策 (R1c 的观察部分保留, 但不改 §2.4 默认值)

3. **§2.4 Dual-Mode Memory Write** (**原版设计**, 不按第七轮反转):
   - 默认 = **session_only** (**三类一致**, 理由: testbench 工作流要 tester 先在 session.messages 看事件效果, 再手动触发 compress/extract) — 此条**原**版, 第七轮 R1c 的 反转默认值 已撤回 (§A.7 记录)
   - 可选模式 = **mirror_to_recent** (三类一致的 opt-in, 让 tester 可选复现主程序 "avatar 独立 /cache 路径" 语义) — 仅一个可选模式, **不**分三类分别不同的默认 (R7 撤回, §2.6 "三类一个抽象" 守住)
   - **Day 1 实现细节提示**: `memory_writer` 对 avatar 行为可参数化 (因为主程序对 avatar 走独立 path), 但**参数化的默认值仍是 session_only 三类一致**, tester 可通过 UI 切到 mirror_to_recent 观察 avatar 独立 path 语义

4. **§3 Day 2 必改 1 处**:
   - 删 "Abort 支持 (api.js signal/abort, 中途取消 LLM 调用)"
   - 改为 "[Invoke event] 按钮 in-flight 期间 disabled=true + spinner + toast '事件正在投递, 请稍候'" (L19 "mutation 不能 abort") (R6 配)

5. **§3 Day 3 必加 3 条 smoke + 1 条验收点说明**:
   - `p25_avatar_dedupe_drift_smoke.py` — from 主程序 import `_should_persist_avatar_interaction_memory` vs testbench copy hash compare (R4 配套)
   - `persona.language=es/pt` 断言 → agent_callback instruction 是英文 (上游 delta)
   - `persona.language=es/pt` 断言 → proactive prompt 是英文 (上游 delta)
   - 验收点步骤 6 明示: avatar memory pair 入 recent.json **不**自动触发 facts.extract (主程序 /cache 语义), tester 必须手动 trigger 才能观察长期记忆影响 (R8 细节)

6. **§1.3 OOS 扩 3 条** (R1a + R9 合并 + 上游 delta):
   - 冷却三分类明示 (a 实时流抖动 OOS / b 语义去重 IN-SCOPE / c N 秒窗口 OOS) (R1a 配)
   - `min_idle_secs=10s` 阈值 OOS (上游 core.py 的 prepare_proactive_delivery)
   - **不复现**连续主动搭话 `merge_unsynced_tail_assistants` 合并 (tester 手工点击间隔 ≥ 人类反应时间, 不满足主程序 merge 触发前提; 且 merge 是实时流场景的运行时优化, §2.1 判定 OOS) (R9 配, 合并自原第七轮 §4.6 提案)

7. **§5 风险表补 1 条**:
   - "高频事件 tester 点爆 dedupe cache" (R3 已处置, 补记录到风险表)

8. **§3A 可能新增原则 (待 P25 交付后观察)**:
   - 候选 H3 "外部系统 pure helper cross-package copy > import (有重副作用时)" (L30 的铁律化)
   - 候选 A19 "SimulationResult-like 响应对象必须含 coerce_info / fallback_reason 字段族" (L14/L17 的 request-response 扩展)

**观察性提示** (不开工前必做):
- R2 (ℹ 低): avatar 事件摘要视组件条件满足, 仅观察, 不列入本清单.

**已撤回 / 调整** (详见 §A.7):
- **R7 完全撤回**: "三类 default 应不同" 违反 §2.6 "三类一个抽象" + §2.1 "不复现 runtime", 属于目标漂移.
- **R13 完全撤回**: "simulate_agent_callback 对应路径 A 还是 B" 是 AI 过拟合生造的歧义, 原版 §1.2 + §2.2 已明示 = tester 手动, 无选择题.
- **R1c 部分撤回**: "avatar 独立 path" 观察对 (保留为 Day 1 实现细节提示), 但用作 反转 §2.4 默认值 是漂移 (撤回).
- **R9 降为合并**: 结论对但归属 §1.3 OOS 而非独立 §4 决策.
- **R1b 降级**: ❗→⚠, 消除的是原版已隐含的歧义而非高危 bug.

### §A.9 开工门禁重核 (§8)

**§8 开工门禁三条 (本轮新加的第 3 条指向 §A.8)**:
- [x] P24 Day 1-9 完成并用户验收 ✅ (Day 1-12 全 + Day 12 欠账清返 全绿)
- [x] **P24 Day 10 smoke 交付完成 + 回归绿** ✅ (9/9 smoke 全绿, 最后一次 2026-04-23 欠账清返 commit `62844c7` 后跑的)
- [ ] 用户对 P25_BLUEPRINT 本文档审读通过 (**以 §A.8 为开工权威清单**: 8 条有效矫正 + 3 条细节补完 + 1 条观察; 已在 §A.7 记录: R7 / R13 完全撤回, R1c 部分撤回, R9 合并, R1b 降级) **← 本轮新加的 gate 条件**

**满足判据**: 用户阅读 §A.7 (漂移诊断) + §A.8 (最终开工清单) + 回复 "开工" 或 "修订 §A 再开工" 之一, 再进 Day 1. **不**允许按第七轮 §A.5 的 11 条直接开工 (会把 R7 R13 的 "复现主程序 runtime 行为" 引入 testbench, 违反 §2.1 语义契约 vs 运行时机制).
