# 负反馈：用户说「别再提 X」之后发生什么

## 三条互不相通的通路

用户表达负面反馈后，有三套机制各自运行，改其中一套不会影响另外两套。动手前先确认要改的是哪一条。

| 通路 | 怎么检测 | 在哪里生效 | 多快 |
|---|---|---|---|
| 话题禁令 `memory/user_directives.py` | 正则抽取（`config/prompts/prompts_directives.py::extract_directives`），和用户消息同一回合落盘 | 会话启动时的 system prompt；主动搭话 Phase 2 的 prompt 和出口硬闸 | 主动搭话立刻生效；普通对话靠当前上下文里用户的原话，到下次会话重建才进 system prompt |
| 负面关键词 → 争议信号 `app/memory_server/signal_extraction.py::_amaybe_trigger_negative_keyword_hook` | 关键词预筛，命中后一次小模型调用找出用户否定的是哪条记忆 | 给对应的反思/人设条目加争议分 | 回合结束后；要累计到阈值并持续一段时间才归档（`config/memory_settings.py` 的 `EVIDENCE_ARCHIVE_*`） |
| 反驳检查循环 `app/memory_server/evidence_loops.py` | 定时用模型检查用户有没有反驳已经提起过的反思 | 反思标成被否定 | 一个检查周期内（`REBUTTAL_CHECK_INTERVAL`） |

后两条属于记忆系统的证据机制，细节看 `docs/design/memory-evidence-rfc.md`。下面只讲第一条和它在主动搭话里的用法。

## 话题禁令的存储规则

- 去重键是 `(kind, term.casefold())`。同一条再说一次：`hit_count + 1`、刷新 `last_seen_at`，有效期按 `min(基础 TTL × hit_count, 上限)` 重新算（`USER_DIRECTIVE_TTL_SECONDS`、`USER_DIRECTIVE_TTL_MAX_SECONDS`）。只有再次命中才续期，沉默不续。
- 落盘条数上限 `USER_DIRECTIVE_MAX_STORED`，超出时先清过期的，再按 `last_seen_at` 从旧到新淘汰；注入上限 `USER_DIRECTIVE_MAX_ACTIVE` 是另一个量。
- ⚠️ 新增和刷新两条分支都要淘汰，并且共用 `_report_if_kept` 判断「刚写的这条有没有被淘汰掉」。只在新增分支淘汰的话，只重复旧指令的用户永远走不到那里，超限的旧文件收不回上限；淘汰后无条件报告成功的话，时钟回拨时上游会去渲染一条已经不存在的禁令。
- 没有宾语的「闭嘴」「换个话题」不存；「我不喜欢西瓜」这类陈述不算禁令。

## 禁令进 prompt 的三个入口

1. **会话启动**：`main_logic/core/notify.py` 的 `_build_initial_prompt` 把全部有效禁令拼进 system prompt。
2. **写进「下一个会话」的缓存**：`_inject_pending_user_directives`。system prompt 每个会话只建一次，热切换时新会话的 prompt 在后台提前建好，这之后才说的禁令会错过新会话。`memory` 层不能反向 import `main_logic`，所以由存储侧置一个待办标记（`mark_pending_injection`），`main_logic` 在用户发言入口取走标记后调 `append_context(..., lifetime="next_session")`。
   - ⚠️ **不注入当前会话**，这是有意的。当前会话里用户的原话还在上下文中；再塞一条会以用户消息的形态进历史，模型会去回应它（「好的我不会再提了」）。实时语音会话更不行：往进行中的会话推上下文会让这一轮的语音输出被吞掉，角色在用户刚说完禁令时沉默。
   - 渲染的是**完整的有效清单**，不是只渲染新增的那条，和会话启动时的注入保持一致。
   - 去重指纹用「排序后的 casefold 词集合」的 JSON 序列化算。不能用渲染后的文本块（块内按 `last_seen_at` 排序，重复一条旧禁令会改变顺序，产生新指纹，同一份清单被追加两次）；也不能用分隔符拼接（词里可以含分隔符，会碰撞）。
   - 去重窗口沿用 `append_context` 自己的 `_CONTEXT_APPEND_DEDUP_TTL_SECONDS`，不是「每个词集合永久只注入一次」。连着说两遍算一次；隔半小时再说，重新注入来强化是对的。
3. **主动搭话 Phase 2**：`generation.py::_append_directives_section` 把同一个禁令块拼到记忆上下文后面。Phase 2 的 prompt 是自己拼的，不走 `_build_initial_prompt`，没有这一步的话，「她自己挑起话头」这条最容易惹人烦的路径反而看不到禁令。

## 主动搭话出口的硬闸

`generation.py::_proactive_directive_hits`：草稿里字面出现了有效禁令词就丢弃这一轮（`PASS_USER_DIRECTIVE`）。初稿查一次，防复读重写之后再查一次（重写的提示只管避开重复的词，可能把禁令话题带回来）。

为什么是丢弃而不是重写：重写是一次真实的模型调用，而走到这里的前提已经是「模型看过禁令还是提了」。丢弃不花钱，代价只是少说一句。

匹配规则，每一条都对应一个踩过的坑：

- **拉丁、西里尔、希腊文字的词按词边界匹配；中日韩文字按子串匹配。** 禁了 `it` 之后按子串匹配会命中 `favorite`、`waiting`，主动搭话整体静默。
- ⚠️ **词边界不能用 `\b`。** Python 的 `\w` 包含汉字，`今天work很累` 里 `work` 两侧都是 `\w`，`\bwork\b` 匹配不上；中英混说是明确要支持的场景。做法是用「同一文字族的字符」做前后断言（`_LATIN_WORD_CHAR`）。
- **两侧都折叠繁简（`memory.script_fold.fold_script`）并做 Unicode NFC 归一。** 用户用繁体说的禁令要能拦住简体草稿；带重音的西语、葡语词可能以分解形式存下来，不归一就永远匹配不上。折叠不可用时退回不折叠的匹配，不能整道闸放弃。
- **纯指代词（「这个」`this` `it` 等，表在 `prompts_directives.py`）跳过硬闸，但保留在 prompt 的软约束里。** 抽取侧会存下它们是既有行为；按子串硬拦一个最常见的词，会让主动搭话在禁令有效期内全部静默。
- **首字母或全部大写、而小写形式落在指代词表里的词（`US`、`Her`）是专名**：不豁免，并且按大小写敏感匹配。⚠️ 这两半必须一起改。只取消豁免而仍然不分大小写匹配，`US` 会命中每一个普通的 `us`，等于把「全体静默」的问题造回来。只对「和指代词表撞车」的词分大小写，不要扩大到所有大写词（否则 `Work` 匹配不上 `work`）。
- 词尾是标点的词（抽取放得宽）不做边界断言，退回子串匹配，宁可多拦。
- 素材豁免（推歌、推图）不豁免禁令。

## 隐私：禁令词是全流程最敏感的文本

禁令里常见的是前任、病名、逝者的名字。规则：

- 禁令词不进 `logger`，不进 `/api/proactive_chat` 的响应体，只报命中条数。
- Phase 2 的调试输出不打印 prompt 正文（禁令注入 prompt 之后，原来打印 prompt 的调试行就成了泄漏点）。
- 自检「有没有把禁令词写出去」时，不能只 grep 直接引用禁令变量的地方，还要查把它拼进去的 prompt、响应体、被淘汰时的告警这些间接出口。
- 拦截闸排在任何会落盘草稿片段的检测之前（见 `anti-repeat.md` 的出口顺序）。

## 召回侧：负分过滤要带「确实被争议过」

`memory/recall.py::_hard_filter` 丢弃 `score < 0` 的条目，但反思落盘时没有 `score` 这个键（证据是 `reinforcement` / `disputation` 两个累加器，分数读的时候现算）。`memory/hybrid_recall.py::_tag_tier` 负责在过滤前补上分数，并且只给 `disputation > 0` 的条目补。

- 理由：净分转负不只一种原因。用户对提起过的反思没接话，会被记成一次轻微的负强化；一次提起好几条、用户没回应，这几条净分都是负的。沉默不等于反对，按净分一刀切会把之后该被召回的内容抹掉。
- ⚠️ 测试里手工给条目塞 `score` 键能让过滤测试一直绿，却证明不了真实数据上过滤生效。写这类测试要用真实的落盘形状。

## 评审意见怎么判

- 修「不修就比改动前更糟」的问题；「覆盖面还能更大」的意见（比如把禁令扩到更多发声路径）另开 issue，不在同一个 PR 里扩。
- 同一条被驳回过的意见再出现时要重新判：对方有新论点就按新论点判，只是重复就维持原判。
- 已知的覆盖缺口记在 #3013：休息提醒、小游戏邀请、语音提醒不过禁令闸；附带素材的标题（歌名、新闻标题）不在出口闸的扫描范围内。

相关测试：`tests/unit/test_user_directives.py`、`tests/unit/test_user_directives_midsession_inject.py`、`tests/unit/test_proactive_user_directives.py`。
