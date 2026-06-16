# 后台深话题 hook（background deep topic hooks）设计与维护指导

> 本文是 `feat/deep-topic-hooks` 分支深话题 hook 的主文档，必须自足。
> 后续维护时不要依赖 PR 讨论或临时 notes 作为事实来源。
> 如与当前代码、测试或真实运行结果冲突，以可复现证据和当前代码为准，并先更新本文再继续实施。
> 标「规划」的章节是尚未落地的设计，不得当成已完成事实引用。

## 文档定位

本文覆盖深话题 hook 当前已落地的长期事实和维护边界：

1. 这条管线「是什么、不是什么」，以及它和已有 proactive chat / 记忆系统的边界。
2. 从对话采集到投递的各阶段，以及每个阶段的物料契约（material contract）。
3. PR review 阶段敲定的设计决策（字段精简、打分、门控、隐私语义、去重、检索词来源等），含「为什么这么定」。
4. 投递内部机制（一次性、ACK future、撤回、inflight、no-op release）。
5. 规划中的 Phase-2：投递时大模型 deep search。

本文不记录临时试错过程，也不把规划写成现实。

## 核心目标

深话题 hook 是「10% 神明降临」那一侧的能力：低频地、像随口想起来一样，主动接住用户最近真正在意的一件事，而不是高频寒暄。

1. 从「慢收集的全局证据 + 最近对话」里挑 1-2 个值得以后低频开口的深话题，**而不是**总结最近一句话。
2. 物料只是给角色的**信号**，不是开口台词；最终怎么开口由 Phase-2 角色模型决定。
3. 后台预先把话题备好（含可选的联网现实细节），让 proactive chat 投递时不必同步拉取原始对话。
4. 全程尊重活动倾向门（propensity）与隐私模式，宁可不开口也不硬凑。

## 非目标

1. 不接管长期记忆存储，也不重写 proactive chat 投递核心。
2. 不做高频触发；重点是关系深度，不是触发频率。
3. 不让小模型 author「该怎么聊」——它只识别话题与关键词，不写开场白、不写检索策略。
4. 不在隐私模式下继续积累话题（见「隐私语义」）。

## 机制总览

每个角色一个进程内的 `TopicHookPool`（`main_logic/topic/pipeline.py`）。管线分阶段：

1. **采集**：`note_user_message` / note AI 回合把最近对话按 token 预算存进池子；同时喂 `TopicSignalStore` 形成慢全局证据。
2. **分析**：debounce + quiet-window 后调情绪档小模型 `call_topic_candidates`（`main_logic/activity/llm_enrichment.py`），从「全局证据 + 最近对话」产出候选物料。
3. **打分 / 门控**：后端代码按 `relevance ≥ 70 且 risk ≤ 65` 过滤（`_material_is_ready`），不是 prompt 自己判阈值。
4. **联网增强（可选）**：`enrich_topic_materials_online`（`main_logic/topic/materials.py`）拿物料的检索词做一次轻量联网，把现实细节烘进 `material_hint`。
5. **投递**：到达触发条件后 `trigger_topic_hook_once`（`main_logic/topic/delivery.py`）把物料包成 callback，经 `ProactiveDeliveryManager` 一次性投给角色，命中日配额、一次性 used 记账。

### 物料契约

小模型输出、经 `call_topic_candidates` 清洗后的物料字段（**仅这四个**）：

| 字段 | 含义 |
|---|---|
| `interest` | 一句话描述用户最近在意/纠结/计划/反复提的一件具体事（≤90 字符存储，prompt 要求 ≤30 字） |
| `keywords` | 3-6 个关系点关键词；用于去重、筛选联网结果，并直接作为联网查询词 |
| `relevance` | 0-100，话题与用户的相关度 × 证据稳定度 |
| `risk` | 0-100，主动提起的打扰/冒犯/硬凑风险 |

## 设计决策（review 敲定，含理由）

### 字段精简：5 分 + 6 文本 → interest/keywords/relevance/risk

**原本**小模型要输出一堆分数和 hook/opening_intent/deepening_hint/why_now 等文本字段。
**问题**：① 情绪档小模型 follow 不了这么多出参；② 让小模型写「该怎么开口/为什么现在」是 author how-to，越权且质量差；③ `why_now` 在生成时算出来，等真投递时早已过期，无用。
**结论**：砍到 interest + keywords + relevance + risk。开口怎么写、为什么现在，留给投递时的 Phase-2 角色模型。

### hook 是信号不是指令

`build_topic_hook_callback` 只把话题（+ 有联网时的一条具体事实）交给角色模型，**不**下发小模型写的 angle/opening/deepening 文本。开口的语气、时机、是否最终放弃，都由 Phase-2 决定。

### 打分是 rubric，不泄阈值；门控在后端

prompt 只给「明显反复出现 → 高分，顺口一提 → 低分；如实打分别为被采用而虚高」这类 rubric，**不**告诉模型 70/65 这些阈值（否则模型会贴着线刷分）。真正的 `relevance ≥ 70 且 risk ≤ 65` 过滤写在代码 `_material_is_ready`。

### keywords 多用途；search_query 已删（A）

`keywords` 同时承担：① 去重主判据（关键词重合）；② 联网结果相关性兜底过滤（`_is_related_link` 关键词子串匹配）；③ **联网查询词**（`_query_for_material` 把 keywords 拼成 query）。
**原本**小模型另外输出一个 `search_query`。**问题**：prompt 对 `search_query`（围绕最稳定关系点的查询词）和 `keywords`（关系点关键词）的要求本质同一件事，重复，且让小模型构造检索词是它不擅长的 author how-to。
**结论**：删 `search_query`，后台预取的查询词改为 keywords 拼接，interest/hook 作兜底。详见 commit 「derive topic search query from keywords」。

### 去重：关键词重合为主，ngram veto 为兜底

`_topic_was_used_today` 主判据是当日已用话题的关键词重合。ngram veto 是**并行兜底**：要求 query/标题间相似度 ≥ 0.6 **且** 共享 ≥ 2 个 2-gram 单元（`_material_bigram_units`，丢弃单字 CJK 噪声）才算重复。
**理由**：用户不完全信任机械 ngram，但保留它以防关键词漏判；两个条件取「且」是为了让机械 veto 足够保守，不误杀。若日后 ngram 指标暴露严重问题，可单独摘掉这条兜底而不动主判据。

### 隐私语义：只管积累，不管投递

隐私模式开启时，`enrich_topic_pool`（pipeline.py）**早退并清空整个池**（user/ai 回合、signal store、materials 全清）。因此隐私态下造不出新的敏感话题。
**结论**：投递阶段**不**再查隐私。已经排队的 hook 是隐私前快照造的，晚一点投出去可接受。`topic_hook_delivery_allowed`（`main_logic/core.py`）只保留活动倾向门，不查 `is_privacy_mode_enabled()`。详见 commit「drop redundant privacy recheck」。

### 活动倾向门：投递路径上守，retry 路径不守

深话题是最具打扰性的全新开场，必须和 `/api/proactive_chat` 同样的 propensity 门：propensity 为 `closed`（隐私黑名单）或 `restricted_screen_only`（游戏/专注）时不投，且**不**借用 proactive reminiscence 的 open-thread 例外。
被 requeue 进 `pending_agent_callbacks` 的 hook 在 retry 时**不**重查这道门：retry 只在用户在场的回合 drain，propensity 实际为 open，且重查会把话题特定门控铺进通用投递核心。

### surfaced reflection id 只记真正渲染的

`_render_followup_topic_hooks`（`main_routers/system_router.py`）复刻 `_iter_followup_texts` 的空串/去重过滤后再收集 surfaced id，避免被 `build_topic_hook_prompt` 去掉的空/重复 followup 仍被 `/record_surfaced` 打进冷却。

### 输入预算按 token

对话与全局证据都按 token 截断（`utils/tokenize.py`），不按字数。对话路单条上限与全局证据统一。

### 联网与 i18n

- 联网检索：大陆用 baidu、非大陆用 DuckDuckGo（脚本化 Google 几乎必触发 429/sorry，见 `utils/web_scraper.py` 同款判断），baidu/DuckDuckGo 互为跨区兜底。
- prompt 8 语言（zh / zh-TW / en / ja / ko / es / pt / ru）；解析与投递都做 zh-family fallback（zh-* → zh → en，`_select_lang_template` / `_detail_template_for_lang`），保证 zh-TW 仍走中文。
- 投递时按 live tracker locale 重解析语言（`current_topic_language`），避免 quiet window 内 `set_user_language` 切到 zh-TW 后仍以旧 locale 开口。

## 投递内部机制

- 一次性：`trigger_topic_hook_once` 提交 callback 后等 `DELIVERY_ACK_FUTURE_KEY` future（120s 超时）；只有 `TopicHookPool` 能 retry 并记 used/配额，其余 False 路径都 `_remove_callback_from_manager` 撤掉排队副本，防止绕过一次性记账二次冒头。
- 撤回：`DELIVERY_RETRACTED_KEY` + manager.retract，并从 `pending_agent_callbacks` / `pending_extra_replies` 按对象 id 与 `_callback_delivery_id` 清除。
- inflight：释放时若一批 callback 全被门控丢弃（投了个空），`ProactiveDeliveryManager.release_inflight_noop()` 立即释放 inflight 槽，避免空投后下一条 cue 干等满 inflight 超时。

## Phase-2：投递时大模型 deep search（已落地）

深话题的价值在于「先查再聊」，所以 deep search 是**开口前的后台准备步骤**，不是阻塞用户热路径的同步调用：

- 触发条件与现有开口完全相同（pool 的 debounce + quiet window + gap + quota + privacy + seq）。
- `_run_trigger` 在调投递桥之前先 `await self._deepen_material(...)`：用更强档位（`summary` tier，`derive_deep_search_query`）从 interest + keywords（+ A 的便宜 floor 线索 `online_angle`）衍生一条聚焦 deep query，再用它重跑联网增强、覆盖 floor 的 `material_hint`。这一步跑在 trigger task 里，**不阻塞用户对话**。
- **准备就绪后重新过开口门**：deep 跑完才调 `trigger_topic_hook_once`，其顶部的 `topic_hook_delivery_allowed()` 就是 prepare 之后的再校验——条件仍满足就开口，否则返回 False，pool reschedule 等下个窗口。
- **floor 永远兜底**：deep query 衍生失败/超时，或 deep 检索没结果，都回落到 A 的 keyword 兜底 `material_hint`。
- **缓存防重搜**：`deep_search_done` 写在 live material 上，reschedule 重试复用已备好的 deep 结果而不重复搜；它在衍生前就置位，避免 flaky 衍生每次 reschedule 都重试（代价：一次性失败后该物料本轮不再尝试 deep，但 floor 仍投递）。
- **query 来源对偶**：小模型只识别话题 + keywords；deep query 由大模型 author（`deep_query` 字段，`_query_for_material` 优先用它）——这正是当初从小模型拿掉 `search_query` 的原因。

旋钮：`TopicHookPool(enable_deep_search=...)` 默认开；衍生档位为 `summary`（与窗口搜索摘要同档，比 emotion 重、比 agent 轻），改档位是 `derive_deep_search_query` 里一行。

## 测试覆盖

- `tests/unit/test_topic_pipeline.py`：采集、打分门控、去重（关键词 + ngram veto）、调度、`_deepen_material`（衍生 query 覆盖 floor / 无结果保 floor / 幂等 + 开关）。
- `tests/unit/test_topic_llm_enrichment.py`：物料契约解析、低相关/高风险跳过、语言模板 fallback、keywords、`derive_deep_search_query` 解析。
- `tests/unit/test_topic_materials.py`：deep_query 优先 / keywords 拼 query、联网相关性过滤、DuckDuckGo 分区、locale 透传。
- `tests/unit/test_topic_delivery.py`：投递路径、活动门、语言重解析、一次性记账。
- `tests/unit/test_proactive_delivery.py`：批量释放、inflight、no-op release。
- `tests/unit/test_system_router_topic_hooks.py`：followup 渲染与 surfaced id 精度、topic hook locale。
