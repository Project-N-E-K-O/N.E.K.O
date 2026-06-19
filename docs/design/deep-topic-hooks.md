# 后台深话题 hook（background deep topic hooks）设计与维护指导

> 本文是 `feat/deep-topic-hooks` 分支深话题 hook 的主文档，必须自足。
> 后续维护时不要依赖 PR 讨论或临时 notes 作为事实来源。
> 如与当前代码、测试或真实运行结果冲突，以可复现证据和当前代码为准，并先更新本文再继续实施。
> #1847 后续章节只记录尚未替换的 research-agent 内核，其余机制描述均按当前代码事实维护。

## 文档定位

本文覆盖深话题 hook 当前已落地的长期事实和维护边界：

1. 这条管线「是什么、不是什么」，以及它和已有 proactive chat / 记忆系统的边界。
2. 从对话采集到投递的各阶段，以及每个阶段的物料契约（material contract）。
3. PR review 阶段敲定的设计决策（字段精简、打分、门控、隐私语义、去重、检索词来源等），含「为什么这么定」。
4. 投递内部机制（一次性、ACK future、撤回、inflight、no-op release）。
5. 已落地的投递前 deep/search prepare，以及 #1847 后续要替换的 multi-round research agent 内核。

本文不记录临时试错过程，也不把后续目标写成已落地事实。

## 核心目标

深话题 hook 是「10% 神明降临」那一侧的能力：低频地、像随口想起来一样，主动接住用户最近真正在意的一件事，而不是高频寒暄。

1. 从慢收集的对话证据里挑 1-2 个值得以后低频开口的深话题，**而不是**总结最近一句话。
2. 物料只是给角色的**信号**，不是开口台词；最终怎么开口由 Phase-2 角色模型决定。
3. 后台先只把话题备好；联网现实细节统一放到投递前 prepare，避免候选识别和联网阶段互相打断。
4. 投递全程尊重活动倾向门（propensity）和真实 delivery gate，宁可不开口也不硬凑；privacy mode 不参与 deep topic 链路。

## 非目标

1. 不接管长期记忆存储，也不重写 proactive chat 投递核心。
2. 不做高频触发；重点是关系深度，不是触发频率。
3. 不让小模型 author「该怎么聊」——它只识别话题与关键词，不写开场白、不写检索策略。
4. 不用 privacy mode 决定 deep topic 是否积累、抽取、prepare 或投递（见「隐私语义」）。

## 机制总览

进程内共享一个 `TopicHookPool`（`main_logic/topic/pipeline.py`），内部按角色分桶；activity tracker heartbeat 调用时只处理自己的角色，避免一个 open 角色替另一个 private/away 角色跑 candidate。管线分阶段：

1. **采集**：`note_user_message` / note AI 回合按 token 预算喂 `TopicSignalStore`，形成跨窗口（最多最近 60 条、最近 12 小时）的慢对话证据。全局池把这份 signal 持久化到 local state，短时间反复重启会合并处理；超过 12 小时的 signal 直接淘汰。持久化是后台合并 flush，不在每个聊天 turn 上同步 `atomic_write_json` / fsync；测试或显式清理可调用 `flush()`。池子不再单独保留「最近对话」缓冲——那和证据是同一批 turn，纯冗余。
2. **分析**：topic 不再维护自己的 45s sleep/debounce loop，也不等 candidate quiet。`main_logic/activity/tracker.py` 的 activity heartbeat 每 20s 调一次 `process_ready_topics(lanlan_name=当前角色)`；只要该角色 dirty、ready、且没有正在 pending 的 material，就调情绪档小模型 `call_topic_candidates`（`main_logic/activity/llm_enrichment.py`）。candidate 不看 privacy、gap、quota，也不看 delivery activity gate。无角色参数的 `process_ready_topics()` 会扫描 dirty + 已恢复的 signal names，用于启动后 re-arm；tracker heartbeat 一律带角色名，避免跨角色泄露。它每次读取的是 capped rolling evidence，不是「上次之后新增的增量」；最多 60 条会全部渲染给 prompt，不再有 `max_lines=40` 或 head/tail 采样。`_seq` 只保护 candidate LLM：分析期间来了新 turn，就丢弃这次候选结果，等下一轮基于完整窗口重算。它的**唯一对话输入就是 signal store 渲染的证据**（`global_signals`），prompt 里用中文水印 `======以下为最近对话(按时间顺序)======` / `======以上为最近对话(按时间顺序)======` 把这块围起来。
3. **打分 / 门控**：后端代码按 `relevance ≥ 70 且 risk ≤ 65` 过滤（`_material_is_ready`），不是 prompt 自己判阈值。
4. **pending material**：candidate 阶段不做联网增强，只把 `interest/keywords/relevance/risk` 过滤后的 material 放入 pending。
5. **投递前 prepare**：`_schedule_trigger` → `_run_trigger_when_available` 等投递窗口（`min_trigger_gap_seconds` + quota/activity gate）打开后，先 `await _deepen_material(...)`。这一步把 deep search query 衍生和联网补强合并为同一个 delivery-time prepare，并把 `material_hint` / `deep_query` 写回 live material。`deep_search_done` 保证同一份 material 只 prepare 一次；如果 prepare/投递阶段又来了新 turn，新的 dirty signal 会保留，但不会覆盖这份 pending material。
6. **投递**：prepare 完成后立刻重新检查投递窗口；窗口仍打开就调 `trigger_topic_hook_once`（`main_logic/topic/delivery.py`），把物料包成 callback、经 `ProactiveDeliveryManager` 一次性投给角色。窗口已关则保留 prepared material，reschedule 到下一个窗口直接投递，不重复 research。若 delivery bridge 返回 False（语音、活动门、unfinished thread、manager 暂不可用等），按 trigger retry delay 退避后再试，避免投递窗口已满足时 tight-loop。只有确认投递成功才命中日配额、记 used。

### 物料契约

小模型输出、经 `call_topic_candidates` 清洗后的物料字段（**仅这四个**）：

| 字段 | 含义 |
|---|---|
| `interest` | 一句话描述用户最近在意/纠结/计划/反复提的一件具体事（≤90 token 存储，prompt 要求 ≤30 字/词） |
| `keywords` | 3-6 个关键词；用于去重、投递前 research seed、筛选联网结果；锚定用户反复在意的稳定点 |
| `relevance` | 0-100，话题与用户的相关度 × 它是否在对话里反复出现 |
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

`keywords` 同时承担：① 去重主判据（关键词重合）；② 投递前 research seed；③ 联网结果相关性兜底过滤（`_is_related_link` 关键词子串匹配）。`_query_for_material` 在 delivery-time prepare 时优先用大模型衍生的 `deep_query`，没有 deep query 才回落到 keywords / interest。
**原本**小模型另外输出一个 `search_query`。**问题**：prompt 对 `search_query`（围绕用户反复在意的稳定点的查询词）和 `keywords`（同样锚定那个稳定点的关键词）的要求本质同一件事，重复，且让小模型构造检索词是它不擅长的 author how-to。
**结论**：删 `search_query`；candidate 小模型只给 keywords，投递前 prepare 再由更强模型衍生 `deep_query`，keywords 只作兜底和相关性锚点。详见 commit 「derive topic search query from keywords」。

### 去重：关键词重合为主，ngram veto 为兜底

`_topic_was_used_today` 主判据是最近 48 小时/当日已用话题的关键词重合。ngram veto 是**并行兜底**：要求 query/标题间相似度 ≥ 0.6 **且** 共享 ≥ 2 个 2-gram 单元（`_material_bigram_units`，丢弃单字 CJK 噪声）才算重复。
**理由**：用户不完全信任机械 ngram，但保留它以防关键词漏判；两个条件取「且」是为了让机械 veto 足够保守，不误杀。若日后 ngram 指标暴露严重问题，可单独摘掉这条兜底而不动主判据。

### 隐私语义：不参与 deep topic 链路

隐私模式不再 gate deep topic 的任一阶段：

- store：conversation turn sink 会把 raw turn 继续喂给 `TopicSignalStore`，不因 privacy redaction 清空或跳过。
- candidate：`process_ready_topics()` / `process_now()` 不读 `is_privacy_mode_enabled()`，privacy 在 analyzer await 期间切换也不会丢弃结果。
- prepare/query：delivery-time deep search 不查 privacy。
- delivery：`topic_hook_delivery_allowed()` 不查 privacy；是否投递只看真实 delivery gate（activity propensity、unfinished thread、manager/voice/proactive busy、gap、quota、dedup 等）。

显式的角色清理 / 已投递清理仍然存在，但不再由 privacy mode 自动触发。

### 活动倾向门：投递路径上守，retry 路径不守

深话题是最具打扰性的全新开场，必须和 `/api/proactive_chat` 同样的 propensity 门：propensity 为 `closed`（隐私黑名单）或 `restricted_screen_only`（游戏/专注）时不投，且**不**借用 proactive reminiscence 的 open-thread 例外。当前 snapshot 存在 `unfinished_thread` 时也不投全新的 deep topic hook；已经有未完成承诺/线程在桌面上时，应优先让原线程续上，而不是用新深话题抢话。
被 requeue 进 `pending_agent_callbacks` 的 hook 在 retry 时**不**重查这道门：retry 只在用户在场的回合 drain，propensity 实际为 open，且重查会把话题特定门控铺进通用投递核心。

### 语音会话不投深话题（只 defer 不 drop）

深话题是文本态开场白；在实时语音对话里注入会横切一段正在进行的口语交流，正是这个特性要避免的「硬凑打扰」。因此 `topic_hook_delivery_allowed`（`main_logic/core.py`）在语音路径可达时直接返回 False。

- **谓词是 `_voice_delivery_blocked()`（并集），与 voice 分支实际触发条件对齐**：`trigger_agent_callbacks` 按 `isinstance(self.session, OmniRealtimeClient)` 决定走 voice 分支，而 `_is_voice_session_active_or_starting()` 走的是 input-mode 标志。两者在切换窗口会错位，所以门取两者并集：
  - `isinstance(self.session, OmniRealtimeClient)`：当前 live session 是 realtime。覆盖 **audio→text** 拆除窗口——`start_session` 已把 input-mode 标志翻成 text，但旧 realtime session 还在 `self.session` 里待若干 await 步，voice 分支照样会注入旧语音会话。
  - `_is_voice_session_active_or_starting()`：语音活跃或正在启动。覆盖 **text→audio** 启动窗口——realtime client 还没装进 `self.session`、旧 `OmniOfflineClient` 仍在。
- **采集与 Phase-2 照常跑**：语音回合仍喂 `TopicHookPool`，投递窗口可用时仍会跑 `_deepen_material`。这是有意的——`TopicHookPool` 是进程级、按角色全局的，语音期间攒下的物料应当保留，等用户回到文本会话再投，而不是因为「这次是语音」就不积累（否则重度语音用户永远造不出深话题）。
- **defer 不是 drop**：返回 False 走的是和活动门同一条「撤回排队副本 + pool reschedule 重试」机制，物料留 pending，下一个文本会话窗口再投，不烧日配额。
- **两条投递门共用此收口**：提交门（`_topic_activity_gate_open`）和释放门（`_deliver_proactive_batch`）都查 `topic_hook_delivery_allowed`，不在投递核心里散落会话类型判断。
- **会话启动边界兜底 drain / already-pending / extras-only 三条绕过路径**：`trigger_agent_callbacks` 的 voice 分支与 hot-swap `prime_context` 都直接消费 `pending_agent_callbacks` / `pending_extra_replies`，**不**复查 `topic_hook_delivery_allowed`，所以那两道门管不到已进队列的 hook。`_reset_proactive_gate` 在 `_voice_delivery_blocked()` 时调 `_drop_pending_topic_hooks_for_voice`，一次扫干净：
  - `pending_agent_callbacks` 里 `channel == "topic_hook"` 的——含 `_reset_proactive_gate` 自己刚从 manager drain 进来的、以及释放门早先放进来但 defer 的（SM 忙/媒体流失败/无文本会话）。resolve ack False + retract，复用 `_purge_retracted_agent_callbacks` 同步清配对的 `pending_extra_replies`。
  - `pending_extra_replies` 里 `source_kind == "topic"` 的孤儿 extra——`drain_agent_callbacks_for_llm` 在文本回合清 `pending_agent_callbacks` 但留下 extra，cb 已投递+ack，这条只剩 extra 会被 hot-swap prime 在语音里重新引出，按 `source_kind` 单独扫掉。
- **文本投递点 delivery-point 复查兜底 in-flight snapshot**：topic hook 过了释放门后，`trigger_agent_callbacks` 会把它拷进局部 `callbacks_snapshot` 并从 `pending_agent_callbacks` 移除；若此 trigger 卡在 `try_start_proactive` / `_proactive_write_lock` 上、同时用户起 audio，这个 in-flight snapshot 不在任何队列里，会话启动 sweep 够不着，且释放门的判定已 stale。`_deliver_agent_callbacks_text` 因此在临界区内查**两次** `_voice_delivery_blocked()`：claim sid 前一次（便宜早退）、`prompt_ephemeral` 前一次（权威，兜住 CLAIM/PHASE2 两个 await 期间才发生的切换）——这和该方法本来就为同样的 await 做两道 retracted 过滤是对称的。命中就 `_retract_topic_hook_snapshots` 把 snapshot 里的 topic hook 标 retract + ack False，复用既有 retracted 过滤链丢弃（普通文本投递时是 no-op）。这是把门补到**实际投递点**，而非仅在 submit/release 阶段。
  - **空批兜底释放 inflight**：若 topic hook 被撤光导致 active 为空提前 return，`text_start`/`text_end` 不会跑，`ProactiveDeliveryManager` 的 inflight 槽不会被释放，后续 cue 会卡到超时。两处空批 return 都调 `release_inflight_noop()`（与 `_deliver_proactive_batch` 的空投兜底同款）。

### surfaced reflection id 只记真正渲染的

`_render_followup_topic_hooks`（`main_routers/system_router.py`）复刻 `_iter_followup_texts` 的空串/去重过滤后再收集 surfaced id，避免被 `build_topic_hook_prompt` 去掉的空/重复 followup 仍被 `/record_surfaced` 打进冷却。

### 输入预算按 token

慢对话证据按 token 截断（`utils/tokenize.py`，每条 500 token），不按字数。topic pipeline 的普通文本清洗、material interest、单个 keyword 也都用 token cap（分别 1000 / 90 / 30），字符 cap 不再参与 topic 语义预算。

### is_ready 门：数有信息量的发言，不做魔法打分

分析器只在 `TopicSignalStore.is_ready` 通过后才跑：默认攒够 8 条**有信息量的用户发言**（`_is_meaningful_turn`：非寒暄填充词、且有 ≥3 个信息字符）才算 ready。AI turn 会进入 candidate 输入上下文，但不计入 ready。早期版本用 signal_len / 信息密度 / 稳定度 一堆魔法数凑到 ≥80，对 AI 无 grounding、对维护是负担，已删；门本身（攒够信号再分析、防刚说一句就开聊）保留。`readiness_percent` 仅供日志。

### 联网与 i18n

- 联网检索：大陆用 baidu、非大陆用 DuckDuckGo（脚本化 Google 几乎必触发 429/sorry，见 `utils/web_scraper.py` 同款判断），baidu/DuckDuckGo 互为跨区兜底。
- prompt 8 语言（zh / zh-TW / en / ja / ko / es / pt / ru）；解析与投递都做 zh-family fallback（zh-* → zh → en，`_select_lang_template` / `_detail_template_for_lang`），保证 zh-TW 仍走中文。
- 投递时按 live tracker locale 重解析语言（`current_topic_language`），避免 candidate 之后 `set_user_language` 切到 zh-TW 后仍以旧 locale 开口。

## 投递内部机制

- 一次性：`trigger_topic_hook_once` 提交 callback 后等 `DELIVERY_ACK_FUTURE_KEY` future（120s 超时）；只有 `TopicHookPool` 能 retry 并记 used/配额，其余 False 路径都 `_remove_callback_from_manager` 撤掉排队副本，防止绕过一次性记账二次冒头。
- 撤回：`DELIVERY_RETRACTED_KEY` + manager.retract，并从 `pending_agent_callbacks` / `pending_extra_replies` 按对象 id 与 `_callback_delivery_id` 清除。
- inflight：释放时若一批 callback 全被门控丢弃（投了个空），`ProactiveDeliveryManager.release_inflight_noop()` 立即释放 inflight 槽，避免空投后下一条 cue 干等满 inflight 超时。

## Phase-2：投递前 deep/search prepare（已落地）

深话题的价值在于「先查再聊」，所以 deep search 是**开口前的后台准备步骤**，不是阻塞用户热路径的同步调用：

- 触发条件是真实 delivery gate：`topic_hook_delivery_allowed()`（语音、`closed`、`restricted_screen_only`、`unfinished_thread`）+ proactive busy / manager release gate + `min_trigger_gap_seconds` + daily quota + dedup。不再有 topic quiet window。
- `_run_trigger_when_available` 在调投递桥之前先 `await self._deepen_material(...)`：用更强档位（`summary` tier，`derive_deep_search_query`）从 interest + keywords（+ floor 线索 `online_angle`）衍生一条聚焦 deep query，再用它跑联网增强、写入 `material_hint`。这一步跑在 trigger task 里，**不阻塞用户对话**。
- `enable_online_enrichment=False` 是完整 offline kill switch：delivery-time prepare 不衍生 deep query，也不调用 `enrich_topic_materials_online()`。若只想关掉深搜但保留其他联网能力，用 `enable_deep_search=False`。
- **准备就绪后重新过门**：deep 跑完后重新检查 delivery window。条件仍满足就调 `trigger_topic_hook_once`；如果窗口已关，保留 prepared material，reschedule 等下个窗口直接投递。新 turn 不会打断这份 prepared/pending material。
- **floor 永远兜底**：deep query 衍生失败/超时，或 deep 检索没结果，都保留已有 keyword / floor hint。
- **缓存防重搜**：`deep_search_done` 写在 live material 上，reschedule 重试复用已备好的 deep 结果而不重复搜；它在衍生前就置位，避免 flaky 衍生每次 reschedule 都重试（代价：一次性失败后该物料本轮不再尝试 deep，但 floor 仍投递）。
- **query 来源对偶**：小模型只识别话题 + keywords；deep query 由大模型 author（`deep_query` 字段，`_query_for_material` 优先用它）——这正是当初从小模型拿掉 `search_query` 的原因。

旋钮：`TopicHookPool(enable_deep_search=...)` 默认开；衍生档位为 `summary`（与窗口搜索摘要同档，比 emotion 重、比 agent 轻），改档位是 `derive_deep_search_query` 里一行。

## #1847 后续：multi-round research agent

> 本节只描述 #1847 剩余的后续目标，不重复已经落地的 cadence / persistence / gate / prepare 接点。

当前已经完成的收口：

1. topic 私有 45s debounce loop 已移除，候选分析接入 activity heartbeat。
2. topic signal 已按角色持久化最近 12 小时，短重启会合并处理。
3. candidate 阶段只看对话证据，不做联网。
4. `_seq` 只保护 candidate LLM；pending 之后的 prepare / delivery 不再被新 turn 打断。
5. deep search 与联网补强已经合并到 delivery-time prepare 接点。
6. `topic_hook_delivery_allowed()` 已增加 `unfinished_thread` 门。

剩余要做的是把当前 `_deepen_material` 的「单 query 衍生 + 一次联网增强」替换成真正的 multi-round research agent：

1. pending material 等待 delivery window。
2. 窗口打开时执行一次 research prepare：规划查询、选择模态、联网/阅读/反思、合成 `material_hint` / `online_query` / `online_angle`。
3. prepare 完成后立刻重新检查 delivery window。
4. 如果窗口仍打开，立即投递。
5. 如果窗口已关闭，保留 prepared material，下一个窗口打开时直接投递，不重复 research。

research prepare 的失败语义仍是 floor-first：失败、超时或预算耗尽时，保留可用的 floor hint；没有 floor hint 时也不能让异常污染 pending material。

### 当前状态机

```text
collect persisted turns
  -> candidate ready (enough meaningful user turns)
  -> analyze capped 60-turn / 12h evidence
  -> pending material
  -> delivery window opens (gap/quota/activity/no unfinished thread)
  -> research prepare once
  -> if window still open: deliver
     else: keep prepared material
  -> mark used on confirmed delivery
```

## 测试覆盖

- `tests/unit/test_topic_pipeline.py`：采集、打分门控、去重（关键词 + ngram veto）、调度、`_deepen_material`（衍生 query 覆盖 floor / 无结果保 floor / 幂等 + 开关）。
- `tests/unit/test_topic_llm_enrichment.py`：物料契约解析、低相关/高风险跳过、语言模板 fallback、keywords、`derive_deep_search_query` 解析。
- `tests/unit/test_topic_materials.py`：deep_query 优先 / keywords 拼 query、联网相关性过滤、DuckDuckGo 分区、locale 透传。
- `tests/unit/test_topic_delivery.py`：投递路径、活动门、语言重解析、一次性记账。
- `tests/unit/test_proactive_delivery.py`：批量释放、inflight、no-op release。
- `tests/unit/test_system_router_topic_hooks.py`：followup 渲染与 surfaced id 精度、topic hook locale。
