# LLM Prompt Budget 设计文档（输入侧）

**Owner**: Wave-of-Budget 重构（PR #976 起）
**对应代码**: `config/__init__.py` §3.7 LLM Context & Output Budget
**调试工具**: `NEKO_LLM_PROMPT_AUDIT=1` → `logs/llm_prompt_audit/YYYY-MM-DD.jsonl`

---

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
| `*_MAX_CHARS` | 字符数（仅遗留 char-based 流程） |
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
| 渲染主对话 prompt 的 persona 预算 | `PERSONA_RENDER_TOKEN_BUDGET` | 2000 |
| 渲染主对话 prompt 的 reflection 预算 | `REFLECTION_RENDER_TOKEN_BUDGET` | 2000 |
| promote-merge LLM 输入的同 entity 池 | `PERSONA_MERGE_POOL_MAX_TOKENS` | 4000 |
| corrections 单次 batch | `PERSONA_CORRECTION_BATCH_LIMIT` | 10 |

> **`PERSONA_RENDER_TOKEN_BUDGET` ≠ `PERSONA_MERGE_POOL_MAX_TOKENS`**：
> 前者是渲染给主对话用的（要省 token 给历史/任务），后者是 promotion-merge LLM 看的（要尽量看全才能合并判断）。

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
- 每条 fact 落盘时带 `signal_processed=False` 标记（[memory/facts.py](memory/facts.py)）
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
| Phase 1 候选话题总数 | `PROACTIVE_PHASE1_TOTAL_TOPICS` | 20 |
| 单条 web/news 内容 | `PROACTIVE_EXTERNAL_PER_ITEM_MAX_TOKENS` | 200 |
| 外部内容拼合总和 | `PROACTIVE_EXTERNAL_TOTAL_MAX_TOKENS` | 2000 |
| 主动搭话历史 deque | `PROACTIVE_CHAT_HISTORY_MAX` | 10 |
| 话题历史 deque | `PROACTIVE_TOPIC_HISTORY_MAX` | 100 |

### 4.6 Utils

| Component | 常量 | 默认 |
|---|---|---|
| 翻译短文本 chunk | `TRANSLATION_CHUNK_MAX_CHARS_SHORT` | 5000 chars |
| 翻译长文本 chunk | `TRANSLATION_CHUNK_MAX_CHARS_LONG` | 15000 chars |

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
4. 用 `NEKO_LLM_PROMPT_AUDIT=1` 跑一次冒烟，确认实际 prompt 没破。

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
  - `REFLECTION_RENDER_TOKEN_BUDGET` 1000 → 2000（与 `PERSONA_RENDER_TOKEN_BUDGET` 持平）
  - `EVIDENCE_DETECT_SIGNALS_MAX_OBSERVATIONS` 200 → 30（recall 后 LLM 看到的最终 budget）
  - `RECALL_CANDIDATES_TOTAL_MAX_TOKENS` 25000 → 15000；`EVIDENCE_OBSERVATIONS_TOTAL_MAX_TOKENS` 25000 → 15000
  - `TRANSLATION_OUTPUT_MAX_TOKENS` 2000 → 1000
  - 新增 `EVIDENCE_DETECT_SIGNALS_MAX_NEW_FACTS = 20` + drain 机制（fact `signal_processed` 字段 + `amark_signal_processed` helper）
  - `PROACTIVE_LLM_DEFAULT_MAX_TOKENS` (1536) → `PROACTIVE_PHASE2_GENERATE_MAX_TOKENS` (450 = abort fence × 1.5)
  - `PROACTIVE_LLM_RETRY_MAX_TOKENS` (1024) → `PROACTIVE_PHASE1_UNIFIED_MAX_TOKENS` (1024)（仅重命名，值不变）
