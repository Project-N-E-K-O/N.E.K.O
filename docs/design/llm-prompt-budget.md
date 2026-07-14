# LLM Prompt Budget 设计文档（输入侧）

**Owner**: Wave-of-Budget 重构（PR #976 起）
**对应代码**: `config/__init__.py` §3.7 LLM Context & Output Budget
**调试工具**: `NEKO_LLM_PROMPT_AUDIT=1` → `logs/llm_prompt_audit/YYYY-MM-DD.jsonl`
**CI 守门**: `scripts/check_llm_budget.py`（`.github/workflows/analyze.yml` → python-lint job）

---

## 0. 硬性要求（全仓 CI 守门）

> 每个 LLM 调用都必须被"框死"。`scripts/check_llm_budget.py` 全仓强制（除 `plugin/` `tests/` `frontend/` `config/` 等，见脚本 `EXCLUDE_DIRS`），违规 CI 直接 fail。

| 维度 | 要求 | lint code | 强制点 |
|---|---|---|---|
| **输出 budget** | 每个 `create_chat_llm()` / `ChatOpenAI()` 构造必须带 `max_completion_tokens=` 或 `max_tokens=` | `LLM_OUTPUT_BUDGET` | 构造点 |
| **输出 timeout** | 同一构造必须带 `timeout=`（或 `request_timeout=`） | `LLM_OUTPUT_BUDGET` | 构造点 |
| **输入 budget** | 每个带动态输入的 LLM 调用所在函数必须有 token-budget 痕迹（`truncate_to_tokens` / `truncate_head_tail_tokens` / `*_MAX_TOKENS` 常量） | `LLM_INPUT_BUDGET` | 调用点（启发式） |

**为什么放在构造点**：`ChatOpenAI._params(**overrides)` 允许同一 client 在 **调用时** 逐次设 budget/timeout（避免并发踩 instance 属性）。这类站点构造行可以不带，用 `# noqa: LLM_OUTPUT_BUDGET` + 一行理由放行。同理裸 `_client.chat.completions.create(...)`（CUA engine）、`asyncio.timeout()/wait_for()` 包裹（llm_enrichment / proactive）也走 noqa。

**输入 budget 是启发式**：它只能判断"函数里有没有 budget 痕迹"，不能证明某条字符串真被截。故意保守 → 会有误报。确实不该 cap 的（§6 "咎由自取"清单：用户原话 / 用户配置 / OS 窗口标题等）用 `# noqa: LLM_INPUT_BUDGET` + 理由放行。

**抑制写法**：`# noqa: LLM_OUTPUT_BUDGET` / `# noqa: LLM_INPUT_BUDGET`，可加 `# noqa: CODE  # 理由` 把理由写在第二个 `#` 之后（解析器在第二个 `#` 处停止读 code）。

**变量级共识**：
- 变长结构化 JSON 输出（memory reflection/recall/persona/facts/refine、deduper、card-assist、window-title 关键词等）没有紧的 task-specific budget——硬 cap 会在 thinking 模型上把 reasoning token 一起算进去、截断 JSON 中段导致 `robust_json_loads` 静默失败。这类站点统一用 **`LLM_OUTPUT_GUARD_MAX_TOKENS`（4096，runaway guard）** 作 `max_completion_tokens`：取值刻意大，正常输出不会被截，只挡模型失控；靠 guard + timeout + 输入 budget 三层兜底。
- 真正在 **调用时** 设 budget 的站点用 `# noqa: LLM_OUTPUT_BUDGET`：CUA engine（裸 `_client.chat.completions.create(max_completion_tokens=...)`）、`computer_use`（`invoke_raw(max_completion_tokens=...)`，ping vs 主调用预算不同）。
- 主对话流式 client（`OmniOfflineClient`）的 `timeout` 用 `DIALOG_LLM_STREAM_TIMEOUT_SECONDS`（180s，取大只作 hang-guard，不误截正常长回复）。

## 1. 背景

PR #967 之前，全仓字符长度限制散落在各模块的 magic number 里（200、500、1000 chars 等）。PR #967 把"内部摘要 / 单条 reflection / proactive abort fence"这些**已有的限制**统一切到 tiktoken token。但仍有大量"输入侧 prompt component"完全裸奔——任何来自用户、外部 API、plugin manifest 的字符串可以直接拼进 messages，理论上能塞 N MB。

本文档**只管输入侧**：每条会被拼进 LLM `messages` 的字符串都应该有明确的 budget（或显式列入"咎由自取"清单），并在 `config/__init__.py:843` §3.7 集中维护。

输出侧 `max_tokens` / `max_completion_tokens` 也在 §3.7 里维护，但路由由 `utils/llm_client.py` `ChatOpenAI._params()` 自动按 base_url 选字段名（Anthropic → `max_tokens`，其他 → `max_completion_tokens`），caller 无需关心。

## 2. 设计原则

### 2.1 三层防护

```text
单条 cap (per-item)  →  总和 cap (total)  →  会话级触发归档 (session)
   ↓                          ↓                          ↓
防长贴 / 异常输入      防大量短条累加          防长会话上下文滚雪球
```

每一层都不可缺；只做单条 cap 时 200 条 × 200 token = 40k 仍能撑爆，只做总和 cap 时单条异常长会挤掉其他重要内容。

### 2.2 单位约定

| 后缀 | 含义 |
|---|---|
| `*_MAX_TOKENS` | tiktoken `o200k_base` token 数（CJK ≈ 1.3-1.5 token/char，EN ≈ 0.25 token/char） |
| `*_TRIGGER_TOKENS` | 触发某个动作的阈值（不是硬上限） |
| `*_MAX_ITEMS` / `*_MAX` | 条数（消息条 / deque maxlen / list[-N:]） |
| `*_MAX_CHARS` | 字符数（仅非 prompt-facing 的 UI / payload 防爆流程使用，不作为 LLM input budget 证据） |
| `*_BYTES` | 字节 |
| `*_MS` | 毫秒 |

### 2.3 截断策略

- **平直截断** (`truncate_to_tokens`)：默认。candidate / observation / detail / 工具结果都用这个——丢尾不丢头。
- **头尾保留** (`truncate_head_tail_tokens`)：会话流水里的单条 user/assistant 长 message。开头是问候/话题、结尾是问题/总结，都重要。中段用 `…[省略中段]…` 替换。
- **数量截断** (`[:N]`)：列表型 component（unabsorbed facts、corrections queue、observations）按 score 排序后取前 N。

### 2.4 session 级归档

- **OmniOfflineClient**：累计 token > `SESSION_ARCHIVE_TRIGGER_TOKENS` (5000) **OR** 用户输入轮次 > `SESSION_TURN_THRESHOLD` (10) 触发新会话准备 + 记忆压缩 + 历史归档。两条件 OR。
- **OmniRealtimeClient**：不维护 server 侧 history，由 Gemini Live API 自己管 session 上限——本地不参与 token / turn 触发。
- 注：纯时间触发（uptime ≥ 40s）已**移除**。长时间发呆不再强制归档；归档由 turn / token 真实驱动。

## 3. 数据假设（配 budget 用）

| 角色 | 单轮 token 量级 | 备注 |
|---|---|---|
| 用户输入 | 30-500（平均 ~150） | 超长贴文走单条头尾截断 |
| AI 回复 | ~400 | TTS 限制下不会太长 |
| 一轮往返 | ~550 | 用户+AI |
| 触发归档 | ~9 轮 | 5000 / 550 ≈ 9，与 `SESSION_TURN_THRESHOLD = 10` 对齐 |

## 4. 输入侧 component 清单（按调用域分组）

每条 component 在 §3.7 里都有对应 docstring 注释，下表只做索引。

### 4.1 主对话流（omni_offline / omni_realtime / core）

| Component | 常量 | 默认 | 说明 |
|---|---|---|---|
| 会话归档 token 阈值 | `SESSION_ARCHIVE_TRIGGER_TOKENS` | 5000 | 仅 offline，与 turn 阈值 OR |
| 会话归档轮次阈值 (用户输入) | `SESSION_TURN_THRESHOLD` | 10 | 仅 offline |
| omni 最近回复缓存（重复检测） | `OMNI_RECENT_RESPONSES_MAX` | 3 轮 | |
| WS 帧大小 | `OMNI_WS_FRAME_LIMIT_BYTES` | 250000 | 字节，非 token |
| pending 用户图片 | `PENDING_USER_IMAGES_MAX` | 3 | |
| avatar 交互文本上下文 | `AVATAR_INTERACTION_CONTEXT_MAX_TOKENS` | 80 | |
| avatar 交互去重队列 | `AVATAR_INTERACTION_DEDUPE_MAX_ITEMS` | 32 条 | |
| avatar 交互去重时间窗 | `AVATAR_INTERACTION_DEDUPE_WINDOW_MS` | 8000 ms | |

### 4.2 记忆系统（memory/）

#### 4.2.1 Recent history compression

| Component | 常量 | 默认 |
|---|---|---|
| 压缩后保留条数 | `RECENT_HISTORY_MAX_ITEMS` | 10 |
| 触发压缩条数 | `RECENT_COMPRESS_THRESHOLD_ITEMS` | 15 |
| Stage-1 摘要 token 上限（触发 Stage-2） | `RECENT_SUMMARY_MAX_TOKENS` | 1000 |
| Stage-2 LLM `max_completion_tokens` | (`RECENT_SUMMARY_MAX_TOKENS+100`) | 1100 |
| Stage-2 prompt 字数自约束 | (prompt 内文案) | 700 字/words (CJK 1050 tok / EN 933 tok 都安全 < 1100) |
| Stage-2 输出后句末标点回溯截断 | `truncate_to_last_sentence_end` | (helper) |
| 单条 message 头尾保留截断 | `RECENT_PER_MESSAGE_MAX_TOKENS` | 500（head=tail=250） |

#### 4.2.2 Reflection

| Component | 常量 | 默认 |
|---|---|---|
| 单条反思 soft cap | `REFLECTION_TEXT_MAX_TOKENS` | 150 |
| surfacing top-K | `REFLECTION_SURFACE_TOP_K` | 3 |
| 单次 synthesis 最多带入的 unabsorbed fact 数 | `REFLECTION_SYNTHESIS_FACTS_MAX` | 20 |

#### 4.2.3 Persona

| Component | 常量 | 默认 |
|---|---|---|
| 渲染主对话 prompt 的 persona 预算 | `PERSONA_RENDER_MAX_TOKENS` | 2000 |
| 渲染主对话 prompt 的 reflection 预算 | `REFLECTION_RENDER_MAX_TOKENS` | 2000 |
| promote-merge LLM 输入的同 entity 池 | `PERSONA_MERGE_POOL_MAX_TOKENS` | 4000 |
| corrections 单次 batch | `PERSONA_CORRECTION_BATCH_LIMIT` | 10 |

> **`PERSONA_RENDER_MAX_TOKENS` ≠ `PERSONA_MERGE_POOL_MAX_TOKENS`**：
> 前者是渲染给主对话用的（要省 token 给历史/任务），后者是 promotion-merge LLM 看的（要尽量看全才能合并判断）。

##### 4.2.3.1 外部记忆导入（persona LLM 融合）

| Component | 常量 | 默认 |
|---|---|---|
| 融合后 neko(SOUL.md) persona 预算 | `EXTERNAL_IMPORT_PERSONA_NEKO_MAX_TOKENS` | 1000 |
| 融合后 master(USER.md) persona 预算 | `EXTERNAL_IMPORT_PERSONA_MASTER_MAX_TOKENS` | 600 |
| 融合 LLM 输入的候选素材总和 | `EXTERNAL_IMPORT_FUSION_INPUT_MAX_TOKENS` | 6000 |
| 融合产出单条 soft cap | `EXTERNAL_IMPORT_FUSION_ENTRY_MAX_TOKENS` | 200 |

> **为什么 persona 导入必须 LLM 融合、不能逐条追加**：
> OpenClaw/Hermes 的 `USER.md` / `SOUL.md` 是几十行自由 Markdown。persona 渲染进主对话
> prompt 时所有 non-protected 条目共抢 `PERSONA_RENDER_MAX_TOKENS` 这个池；若原样逐条
> 追加，导入素材会迅速把池撑爆，挤掉角色在对话里自然积累的印象。所以导入侧先经一次
> LLM 融合（归纳／合并／去重／消歧／按重要度排序），把内容压进上表的 per-entity 预算
> 再落盘为 non-protected persona 条目。融合失败**不降级**成逐条追加（那会绕开预算），
> 而是保留原始素材让用户重试（同指纹整批 skip、变更 replace-then-fuse，幂等）。
> facts 侧没有这个渲染硬上限，检索时才召回，所以走纯追加。代码见
> `memory/persona/fusion.py` 与 `app/memory_server/routes.py::import_external_markdown`。

#### 4.2.4 Recall

| Component | 常量 | 默认 |
|---|---|---|
| coarse-rank 过采样倍数 | `RECALL_COARSE_OVERSAMPLE` | 3 |
| 单条 candidate 截断 | `RECALL_PER_CANDIDATE_MAX_TOKENS` | 200 |
| candidates 总和兜底 | `RECALL_CANDIDATES_TOTAL_MAX_TOKENS` | 15000 |

#### 4.2.5 Evidence signal detection

| Component | 常量 | 默认 |
|---|---|---|
| Stage-2 候选 observation 条数上限 (LLM rerank 后) | `EVIDENCE_DETECT_SIGNALS_MAX_OBSERVATIONS` | 30 |
| Stage-2 单批 new_facts 上限 (drain) | `EVIDENCE_DETECT_SIGNALS_MAX_NEW_FACTS` | 20 |
| 单条 observation 截断 | `EVIDENCE_PER_OBSERVATION_MAX_TOKENS` | 200 |
| observations 总和兜底 | `EVIDENCE_OBSERVATIONS_TOTAL_MAX_TOKENS` | 15000 |
| 负面关键词检查 user 上下文条数 | `NEGATIVE_KEYWORD_CHECK_CONTEXT_ITEMS` | 3 |

**Drain 机制（Stage-2 单批 cap）**：
- 每条 fact 落盘时带 `signal_processed=False` 标记（[memory/facts.py](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/memory/facts.py)）
- `aextract_facts_and_detect_signals` 拉**全部** unprocessed facts，按 importance DESC 取前 N=20，调 Stage-2
- 成功 → `amark_signal_processed(ids)` 标记 True；失败 → 不动，下轮 idle tick 重试同一批
- 多余的 fact（>20）留 `False`，下次 idle 自然 drain
- 兼容性：老 facts.json 没此字段时 default=True，避免升级后把历史几百条 fact 重跑 Stage-2
- 与 `FACT_DEDUP_BATCH_LIMIT=20` 同口径（LLM 在 N×M 配对决策的舒适 batch ~20 条）

### 4.3 Agent / Brain（task_executor / computer_use / openclaw / cua）

#### 4.3.1 任务历史 + 结果回流

| Component | 常量 | 默认 |
|---|---|---|
| `messages[-N:]` 历史窗口 | `AGENT_HISTORY_TURNS` | 10 |
| 任务 detail 字段统一档 | `TASK_DETAIL_MAX_TOKENS` | 200 |
| 任务 summary 字段 | `TASK_SUMMARY_MAX_TOKENS` | 400 |
| 任务大 detail（HUD） | `TASK_LARGE_DETAIL_MAX_TOKENS` | 1000 |
| 任务 error | `TASK_ERROR_MAX_TOKENS` | 350 |
| AgentTaskTracker 记录数 | `AGENT_TASK_TRACKER_MAX_RECORDS` | 50 |

#### 4.3.1.1 Agent callback 注入（task_result + push_message/proactive 事件流）

callback（`enqueue_agent_callback`）会经 `_build_callback_instruction`（文本轮 system_prefix）和 `_render_pending_extra_replies_by_origin`（语音 hot-swap final_prime_text）拼进**本轮对话** LLM。task_result 类已被 `TASK_*_MAX_TOKENS` 截过，但 **push_message / proactive_message** 这条 plugin 事件流（proactive_bridge 聚合 text parts → summary/detail）此前完全裸奔，是真正的漏口。

| Component | 常量 | 默认 |
|---|---|---|
| 单条 callback summary/detail | `AGENT_CALLBACK_TEXT_MAX_TOKENS` | 1000 |
| 一次注入的 callback 指令总和 | `AGENT_CALLBACK_TOTAL_MAX_TOKENS` | 3000 |
| pending callback 队列长度 | `AGENT_CALLBACK_QUEUE_MAX_ITEMS` | 50 |

三层一致（per-item / total / 队列容量），在 `enqueue_agent_callback`（per-item + 队列）和两个 render helper（total）落实。

#### 4.3.2 Recent context buffer

| Component | 常量 | 默认 |
|---|---|---|
| 单条 message | `AGENT_RECENT_CTX_PER_ITEM_TOKENS` | 400 |
| 总和 | `AGENT_RECENT_CTX_TOTAL_TOKENS` | 1000 |

#### 4.3.3 Plugin pipeline

| Component | 常量 | 默认 |
|---|---|---|
| `_ensure_short_descriptions` 输入的原始 desc | `PLUGIN_INPUT_DESC_MAX_TOKENS` | 1000 |
| BM25 + LLM 并行触发阈值 | `AGENT_PLUGIN_DESC_BM25_THRESHOLD` | 3000 |

### 4.4 Plugin platform

| Component | 常量 | 默认 |
|---|---|---|
| 用户上下文 deque maxlen | `PLUGIN_USER_CONTEXT_MAX_ITEMS` | 200 |
| MCP 工具结果回流 | `MCP_TOOL_RESULT_MAX_TOKENS` | 1000 |

### 4.5 Proactive

| Component | 常量 | 默认 |
|---|---|---|
| Phase 1 每源抓取条数 | `PROACTIVE_PHASE1_FETCH_PER_SOURCE` | 10 |
| Phase 1 候选话题总数 | `PROACTIVE_PHASE1_TOTAL_TOPICS` | 12 |
| 单条 web/news 内容 | `PROACTIVE_EXTERNAL_PER_ITEM_MAX_TOKENS` | 200 |
| 外部内容拼合总和 | `PROACTIVE_EXTERNAL_TOTAL_MAX_TOKENS` | 1500 |
| 主动搭话历史 deque | `PROACTIVE_CHAT_HISTORY_MAX` | 10 |
| Source 衰减硬窗口 | `PROACTIVE_SOURCE_HARD_SKIP_SECONDS` | 5h |
| Source 衰减半衰期（web/image / music） | `PROACTIVE_SOURCE_HALF_LIFE_BY_KIND` | 3d / 1d |
| Source 遗忘阈值 | `PROACTIVE_SOURCE_FORGET_P` | 0.05 |

### 4.6 Utils

| Component | 常量 | 默认 |
|---|---|---|
| 翻译短文本 chunk | `TRANSLATION_CHUNK_MAX_TOKENS_SHORT` | 2000 |
| 翻译长文本 chunk | `TRANSLATION_CHUNK_MAX_TOKENS_LONG` | 5000 |

## 5. 输出侧 `max_completion_tokens` 索引

> 路由：caller 传 `max_completion_tokens=N`，`utils/llm_client.py` `ChatOpenAI._params()` 按 `base_url` 决定写进请求体的字段名（Anthropic → `max_tokens`，其他 → `max_completion_tokens`）。

| 调用 | 常量 | 默认 |
|---|---|---|
| LLM 健康检查 | `LLM_PING_MAX_TOKENS` | 5 |
| 连通性测试 | `CONNECTIVITY_TEST_MAX_TOKENS` | 1 |
| Emotion 分析 | `EMOTION_ANALYSIS_MAX_TOKENS` | 40 |
| OpenClaw magic intent 分类 | `OPENCLAW_MAGIC_INTENT_MAX_TOKENS` | 80 |
| 插件短描述生成 | `AGENT_PLUGIN_SHORTDESC_MAX_TOKENS` | 150 |
| 插件粗筛 | `AGENT_PLUGIN_COARSE_MAX_TOKENS` | 300 |
| Proactive Phase 2 abort fence | `PROACTIVE_PHASE2_OUTPUT_MAX_TOKENS` | 300 |
| 视觉/截图分析 | `VISION_ANALYSIS_MAX_TOKENS` | 500 |
| 插件完整评估 | `AGENT_PLUGIN_FULL_MAX_TOKENS` | 500 |
| Unified channel 评估 | `AGENT_UNIFIED_ASSESS_MAX_TOKENS` | 600 |
| Proactive Phase 1 unified | `PROACTIVE_PHASE1_UNIFIED_MAX_TOKENS` | 1024 |
| Proactive Phase 2 SDK 端 | `PROACTIVE_PHASE2_GENERATE_MAX_TOKENS` | 450 (=abort fence × 1.5) |
| 翻译输出 | `TRANSLATION_OUTPUT_MAX_TOKENS` | 1000 |
| ComputerUse 主调用 | `COMPUTER_USE_MAX_TOKENS` | 6000 |
| 变长输出 runaway guard | `LLM_OUTPUT_GUARD_MAX_TOKENS` | 4096 |
| 凝神 thinking 额外头寸（叠加，非上限） | `FOCUS_THINKING_EXTRA_TOKENS` | 800 |

> 注：变长结构化 JSON 输出（memory reflection/recall/persona/facts/refine、deduper、card-assist 等）没有紧的 task-specific budget，统一用 `LLM_OUTPUT_GUARD_MAX_TOKENS`（generous runaway guard，不会误截）作上限。见 §0。
>
> 注：`FOCUS_THINKING_EXTRA_TOKENS` 不是一个独立 cap，而是**叠加值**——仅在凝神**且该 provider 真正被翻开思考**的轮次，把主对话当前的 `max_completion_tokens`（`max_response_length + slack`，含 summary-mode 抬升）再 +800，给 reasoning 链单独留头寸。thinking 模型（Qwen / GLM / Kimi / Doubao / OpenRouter）的 reasoning token 与正式回复共用同一预算池（见 §0），不留头寸会把回复挤短。是否"真正开思考"用 `focus_extra_body(model) != get_extra_body(model)` 判：相等即 focus 形态与常规（关思考）形态一致（Claude 保持 disabled、未知模型为 None、非 thinking provider 只是保留自身非思考 extra），此时**不加** +800（避免给没有 reasoning 的轮次白抬上限、逼近 provider 输出上限）。作为 per-call override 经 `_focus_stream_overrides` → `astream` → `_params` 透传（与 `extra_body` 同一条路），不改 `self.llm` 实例属性；base 为 `None`（无限制预算）时省略字段。Python-side 长度 guard 仍按 `max_response_length` 收口可见回复。原生 Gemini（genai SDK 路径）的 `thinking_budget` 是独立字段、不占 `max_output_tokens`，没有"共享池挤占"问题，故该路径不参与本叠加（且 focus extra_body 在 genai 路径本就被忽略，是另一独立话题）。

**timeout 索引（部分）**：

| 调用 | 常量 / 值 | 默认 |
|---|---|---|
| memory 后台 LLM 硬上限 | `MEMORY_LLM_HARD_TIMEOUT_SECONDS` | 110 |
| 主对话流式 client（hang-guard） | `DIALOG_LLM_STREAM_TIMEOUT_SECONDS` | 180 |

## 6. 已知不 cap 项（NOT capped by design）

下列 component 故意不 cap，原因和"咎由自取"逻辑：

| Component | 位置 | 不 cap 原因 |
|---|---|---|
| 用户原话直拼 HumanMessage | `main_logic/omni_offline_client.py:413` | 用户故意贴 1MB 文本攻击自己的会话——咎由自取；session-level 5000 token 触发归档兜底 |
| OpenClaw `_classify_magic_intent_with_llm` user_text | `brain/openclaw_adapter.py:363` | 同上；用 1MB 文本做 80-token 分类是用户的事 |
| Emotion 分析 user text | `main_routers/system_router.py:1757` | 同上 |
| Bilibili `knowledge_context` | `plugin/plugins/bilibili_danmaku/llm_client.py:130` | 用户配置的知识库；用户自己写多大就多大 |
| sts2_autoplay `strategy_prompt` | `plugin/plugins/sts2_autoplay/service.py` | 用户写的 `.md` 策略文件，配置项 |
| 角色 `character_prompt` / world building | persona / setting | 用户配置的人物设定；同上 |
| OS `window_title` | screenshot / web_scraper | 系统活跃窗口标题，OS 层面通常 < 256 字符，且不是攻击面 |

## 7. 调试：临时 prompt 审计日志

```bash
NEKO_LLM_PROMPT_AUDIT=1 ./run.sh   # 启用
```

输出：`logs/llm_prompt_audit/YYYY-MM-DD.jsonl`，每行一条：

```json
{
  "ts": "2026-04-27T...",
  "call_type": "memory_recall_rerank",
  "model": "qwen-plus",
  "base_url": "https://...",
  "limit_field": "max_completion_tokens",
  "limit_value": 600,
  "tokens_total": 3421,
  "tokens_by_role": {"system": 1200, "user": 2100, "assistant": 121},
  "messages": [{"idx": 0, "role": "system", "tokens": 1200, "preview": "..."}]
}
```

实现：`utils/llm_prompt_audit.py` + `utils/llm_client.py` `ChatOpenAI._params()` 末尾 hook。

**测试结束删除清单**：
1. `utils/llm_prompt_audit.py`
2. `utils/llm_client.py:_params()` 末尾的 try/import block
3. `logs/llm_prompt_audit/`

## 8. 修改 budget 的流程

1. 在 `config/__init__.py` §3.7 改常量值，**同步更新 docstring**。
2. 如果是新加的 component：
   - 加常量到 §3.7（带完整 docstring）
   - 加进 `__all__`
   - 在调用点用 `truncate_to_tokens` / `truncate_head_tail_tokens` 真去截
   - 更新本文档 §4 / §5
3. 跑 `uv run pytest tests/unit -q` 确认现有测试通过。
4. 跑 `python scripts/check_llm_budget.py` 确认没有新的 `LLM_OUTPUT_BUDGET` / `LLM_INPUT_BUDGET` 违规（§0 硬性要求）。
5. 用 `NEKO_LLM_PROMPT_AUDIT=1` 跑一次冒烟，确认实际 prompt 没破。

## 9. 历史变更

- **PR #967**：字符长度统一切到 tiktoken token；引入 `utils/tokenize.py` 的 `count_tokens` / `truncate_to_tokens` / `atruncate_to_tokens`。
- **PR #976 (本次)**：
  - 新增 §3.7 LLM Context & Output Budget 集中区
  - 47 个原硬编码常量化 + 11 个新 component 补 budget
  - `ChatOpenAI._params()` 加 provider 自动路由 + 新增 `invoke_raw` / `ainvoke_raw`
  - `_params()` / `invoke` / `ainvoke` 加 `**overrides` 支持 per-call 参数（避免并发踩 instance 属性）
  - 修复 cua/engine.py 6 处非 Anthropic provider 错用 `max_tokens`
  - 修复 brain/computer_use.py 两处 ping/正式调用绕过 `_params()` 的 raw SDK 调用
  - 新增 `utils/tokenize.py:truncate_head_tail_tokens` 头尾保留截断
  - 临时 `NEKO_LLM_PROMPT_AUDIT` 审计日志（测完即删）
- **PR #976 (Wave 2 — budget 微调 + drain)**：
  - 删 session 归档的纯时间触发（`_elapsed >= 40s` 分支），保留 turn / token；turn 抽常量 `SESSION_TURN_THRESHOLD = 10`
  - Stage-2 摘要 prompt 字数 500 → 800；新增 `max_completion_tokens = RECENT_SUMMARY_MAX_TOKENS + 100 = 1100` SDK 端硬限；输出后用 `truncate_to_last_sentence_end` 句末标点回溯防止中段截断
  - `REFLECTION_SURFACE_TOP_K` 2 → 3
  - `REFLECTION_SYNTHESIS_FACTS_MAX` 30 → 20
  - `REFLECTION_RENDER_MAX_TOKENS` 1000 → 2000（与 `PERSONA_RENDER_MAX_TOKENS` 持平）
  - `EVIDENCE_DETECT_SIGNALS_MAX_OBSERVATIONS` 200 → 30（recall 后 LLM 看到的最终 budget）
  - `RECALL_CANDIDATES_TOTAL_MAX_TOKENS` 25000 → 15000；`EVIDENCE_OBSERVATIONS_TOTAL_MAX_TOKENS` 25000 → 15000
  - `TRANSLATION_OUTPUT_MAX_TOKENS` 2000 → 1000
  - 新增 `EVIDENCE_DETECT_SIGNALS_MAX_NEW_FACTS = 20` + drain 机制（fact `signal_processed` 字段 + `amark_signal_processed` helper）
  - `PROACTIVE_LLM_DEFAULT_MAX_TOKENS` (1536) → `PROACTIVE_PHASE2_GENERATE_MAX_TOKENS` (450 = abort fence × 1.5)
  - `PROACTIVE_LLM_RETRY_MAX_TOKENS` (1024) → `PROACTIVE_PHASE1_UNIFIED_MAX_TOKENS` (1024)（仅重命名，值不变）
