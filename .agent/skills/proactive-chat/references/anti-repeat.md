# 防复读：覆盖范围、空转与死锁、素材豁免

## 哪条路径有什么防护

「防复读」不是一个东西，是几套互相独立的机制，覆盖面各不相同。回答「为什么这句没被拦」之前先对这张表。

| 机制 | 覆盖的路径 | 判据 | 处置 |
|---|---|---|---|
| 套话改写 `utils/slop_filter.py`（规则在 `config/prompts/prompts_slop.py`） | 文字会话（`OmniOfflineClient`）。语音不经过 | 人工维护的正则；同一条规则在历史里命中达到 `SLOP_REPEAT_THRESHOLD` 次起 | 只改写「喂给模型的历史副本」，存档和用户看到的原文不动 |
| 近期话题软提示 `AntiRepeatCorpus.top_recent_topics` | 会话启动时注入一次（`main_logic/core/notify.py` 的 `_build_initial_prompt`） | 前景窗里得分最高的几个 ngram | system prompt 里提醒「最近聊过这些」，不拦截 |
| 惯用语回避清单 `memory/habitual_phrases.py` | 会话启动时读文件 | 同一说法出现在多个会话里，且模型判定是口癖而不是话题；用户可以在记忆浏览器里放行或压制 | system prompt 里的回避清单，不拦截 |
| 用户禁令 `memory/user_directives.py` | 会话启动注入；主动搭话 Phase 2 软注入加出口硬闸 | 用户明确说过「别再提 X」 | 见 `user-directives.md` |
| 字面查重 `state.py::_find_similar_recent_proactive_chat` | 只有主动搭话 Phase 2 | 和一小时内已投递的搭话 `SequenceMatcher` 相似度 ≥ `_PROACTIVE_SIMILARITY_THRESHOLD` | 丢弃 |
| BM25 硬闸 `AntiRepeatCorpus.score_draft` | 只有主动搭话 Phase 2 | 得分 ≥ `ANTI_REPEAT_REGEN_THRESHOLD` 重写一次；重写后仍 ≥ `ANTI_REPEAT_DROP_THRESHOLD` 丢弃 | 重写 / 丢弃 |
| 未回应长窗 `score_unanswered_proactive_draft` | 主动搭话 Phase 2、休息提醒 | 用户持续没互动期间，草稿和至少 `ANTI_REPEAT_UNANSWERED_MIN_MATCHES` 条已发出的主动内容高度相似 | 重写一次；仍相似则丢弃 |
| 复读兜底 `_check_repetition` | 文字会话和语音会话 | 连续第三轮和前两轮整条相似度都 ≥ 0.8 | 文字会话清空对话历史（保留 system）；宿主清凝神状态并给前端发 `repetition_warning` |

要点：

- **用户发起的普通回复没有「打分—拦截」闸。** 打分只在 `main_logic/proactive_chat/generation.py` 里调用。普通回复只靠 prompt 里的软提示、回避清单和禁令。
- **防复读语料只有三处写入**：`finish_proactive_delivery`（主动搭话成功投递）、开屏问候提交（`greeting.py`）、`prompt_ephemeral(completion_mode="response", persist_response=True)`。`stream_text` 的普通回复不写语料。所以 BM25 比的是「她最近主动说过什么」。
- 阈值、窗口都在 `config/session_settings.py`，每个常量的注释写了取值理由。

### BM25 抓不住已经固化的口癖（设计如此）

IDF 在最近 `ANTI_REPEAT_BG_WINDOW` 条的背景窗上算。一个说法铺满背景窗之后 IDF 趋近 0，就不再被打分提名。BM25 的目标是「这几轮突然开始反复说 X」，对「一直都在说 X」无效，越顽固越沉默。

例子：每条回复结尾都挂同一句话。普通回复没有硬闸；复读兜底比的是整条回复，一句尾巴占比太小过不了 0.8；BM25 软提示头几轮会提名，之后不再提名。这类问题现在由惯用语回避清单处理（按跨会话出现次数判，不看 IDF），或者用户自己说「别再提」，或者往 `SLOP_RULES` 加规则。别去调 BM25 阈值。

## Phase 2 出口的检测顺序

`generation.py::_guard_phase2_output` 的顺序是定死的：

1. 算这一轮的素材豁免（见下）。
2. 用户禁令硬闸。命中直接丢弃，不重写。
3. 字面查重。命中直接丢弃。
4. 未回应长窗和 BM25。任一触发就带着「避开这些词」的提示重写一次。
5. 对重写后的草稿再来一遍：禁令 → BM25 → 长窗 → 字面，任一不过就丢弃。
6. 音乐的数据级锁。

⚠️ 禁令闸必须在 3、4、5 的检测之前。那些检测命中时会调 `record_anti_repeat_decision`，把草稿片段写进 `anti_repeat_effects.json`。禁令闸放在后面的话，只拦住了投递，含禁令词的片段已经落盘。

其它约定：

- 重写结果由哪个检测器拦下，就按哪个检测器、用重写后草稿的片段记账，不能沿用初稿的原因和片段。
- 禁令命中不调 `record_anti_repeat_decision`：那是另一套系统提前终止了草稿，防复读没有做决定。
- 重写前后各比一次「用户沉默起点」，期间用户有过互动就放弃投递。
- 被丢弃的草稿不写语料、不写搭话历史。

## ⚠️ 空闲死锁：丢弃不推进语料

症状：主动搭话「再也不说话」，每一轮都是 `PASS_DUPLICATE`，日志里 BM25 分数远超丢弃阈值且每轮几乎一样。

根因是结构性的：

- 主动搭话只在用户空闲时触发；被丢弃的草稿不写语料；语料只在成功投递时前进。于是前景窗冻结在最后几条同话题的内容上。
- 屏幕没变，下一轮草稿还是在说同一个屏幕，分数每轮都一样高。重写只避开几个词，又拿到同一张截图，分数不降。
- BM25 总分是 Σ(IDF×TF)，没有上界。话题词在背景窗里稀有（IDF 高），在前景窗的几条里反复出现（TF 叠加），十来个 ngram 就能到阈值的好几倍。

现在的解法是前景窗带时间上限 `ANTI_REPEAT_FG_TTL_SECONDS`（`memory/anti_repeat.py::_split_fg_bg`）：只有这个时间内的条目参与 TF，背景窗不过期。空闲一段时间后前景窗自然变空，`score_draft` 返回 0，草稿放行。语义上也说得通——防的是连着说同一件事，不是隔一阵子再提。

以后改这块要记住：

- 「连续丢弃 N 次后强行投递一次」救不了死锁。实测强投几十次分数也只是缓慢下降，因为每次只挤掉前景窗里的一条。有效的只有让旧条目过期或清窗。
- 任何「只在成功时推进」的打分窗口，配上「只在空闲时触发」的调用方，都会有同样的死锁。新增类似机制时先想清楚窗口靠什么前进。
- 开屏问候没有做「相似就拦下重写」，只用 prompt 引导和角度轮换，就是为了不再造一个同类死锁。
- Phase 2 超时是独立的偶发失败，设计上就是放弃这一轮等下一轮，不是死锁。

## 素材豁免：推歌、推图按素材去重，不按台词

推歌和推图的开场白天生模板化（「换首歌」「看这个」），台词长得一样而素材不同。按台词打分的后果是：连推几首之后前景窗被开场白占满，之后她自发的推歌、推图全被丢弃，表现为「放音乐的频率极低」。用户点歌走的是另一条路径，不受影响，所以容易误判成「没问题」。

规则（`ANTI_REPEAT_EXEMPT_SOURCE_TAGS`，目前是 `MUSIC` 和 `MEME`）：

- 这一轮的素材和最近一小时投递过的不同 → 跳过字面查重、BM25、长窗，直接放行。素材相同 → 回到普通的台词检测（台词不重复照样能发）。
- 素材标识由 `state.py::_proactive_material_key` 算：音乐是 `标题|歌手`；表情包是**搜索关键词**，不是图片。
- 表情包走随机热词兜底时关键词为空 → 标识为空 → 永远算新素材。因为 Phase 2 看不到图片，这种情况下可能一字不差地重复配文，所以 `ANTI_REPEAT_VERBATIM_GUARD_SOURCE_TAGS`（目前只有 `MEME`）保留一道「去掉空白和标点后完全相同」的检查，命中重写一次，仍然相同才丢弃。
- 豁免的台词**不进**防复读语料（`finish_proactive_delivery` 里按 `source_tag` 跳过），否则模板开场白会抬高其它通道的基线。但它们仍然进搭话历史 `_proactive_chat_history`，继续出现在 prompt 的「最近主动说过的话」里。软提示保留，只去掉硬拦截，两者不要混。
- 素材豁免不豁免用户禁令。豁免的理由是「素材新鲜就别拿台词复读度卡它」，和用户禁掉的话题无关。

两个容易改错的点：

- 表情包的标识读 `meme_content['keyword']`。这是 `generation.py::_fetch_phase1_followups` 包装之后的字段，值是 Phase 1 模型给的关键词（随机热词兜底时为空），不是抓取函数原始返回里的字段。在消费侧改成读原始返回的字段名会让标识恒为空，去重失效。
- 记账用**实际投递的通道**（`delivery.py` 里的 `delivered_tag`），不是模型输出的标签。Phase 1 只有音乐时模型可能输出 `[CHAT]`，但随后仍会附上曲目，实际投递的是音乐；按 `CHAT` 记会让模板开场白进语料、曲目标识不被记录。出口处用于判断豁免的 `dedup_tag` 也是同样的考虑。

相关测试：`tests/unit/test_proactive_material_dedup.py`、`tests/unit/test_proactive_unanswered_repeat.py`、`tests/unit/test_anti_repeat.py`。
