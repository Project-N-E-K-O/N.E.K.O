# 主动搭话把内部标签当台词说出来

## 症状

- 主动搭话多出一个只有几个字的气泡，内容像小标题：「屏幕细节轻问」「回忆线索」「聊天中」，后面才是正常台词。
- 中文用户的台词开头冒出英文词：`focused_work`、`restricted_screen_only`、`hook`。

第一种看起来像「发了两条」，是因为模型把标签放在第一行，正文在第二行，前端按换行把一条回复拆成了多个气泡。

## 根因

Phase 2 的 prompt 里有很多「标签：说明」形态的内部助记，本意是让模型据此发挥，不是让它念出来：

- 口吻角度种子 `ACTIVITY_TONE_HINTS`、口吻质量要求 `ACTIVITY_TONE_QUALITY_BARS`（`config/prompts/prompts_activity.py`）。
- 记忆线索的引导语和逐条标签 `TOPIC_MEMORY_CUE_INTROS` / `TOPIC_MEMORY_CUE_LABELS`。
- 活动状态段的状态名、倾向名、各小节标题（`ACTIVITY_STATE_LABELS`、`ACTIVITY_PROPENSITY_DIRECTIVES`、`ACTIVITY_STATE_SECTION_LABELS`）。

弱一些的模型会把标签那半截原样抄到回复第一行。

为什么已有的防护漏掉它：这一轮没有网页、音乐、表情包这类带副作用的通道时，prompt 要求模型输出**不带来源标签的纯文本**，服务层的 `_expects_source_tag` 为 False，于是「缺少合法来源标签就丢弃或自救重写」那道闸不生效；而只剥方括号标签的清理（`[Screen]` 之类）又不认识裸的标签词。纯屏幕闲聊恰好走这条路径。

英文枚举泄漏有两种来源，排查时按这两种形态去 prompt 拼装代码里找：

1. 用 f-string 把枚举字段直接插进 prompt。
2. 二级回退 `表.get(语言, 英文表).get(key, key)`：某个语言缺这个 key 时，回退成了英文 key 本身。

## 现在的防护

两层都要有，缺一层都会复发。

**注入侧（治本）**：给模型看的状态名、小节名都渲染成当前语言的文案；回退到「空」而不是回退到 key。状态分数那一行的键是小模型按英文约定返回的，要在渲染处映射成本地化标签，不能原样拼进去。

**输出侧（兜底）**：

- `generation.py::_strip_proactive_screen_tag_leak`：剥掉开头误输出的屏幕类方括号标签（`_PROACTIVE_SCREEN_TAG_LEAKS`），后面紧跟合法来源标签时采用后者，否则按 `CHAT` 处理；同时处理 `/chat`、`标签/正文` 这类斜杠前缀。
- `generation.py::_strip_proactive_intent_label_leak`：剥掉开头的内部标签。标签集合由 `prompts_activity.py::get_proactive_intent_leak_labels()` 在运行时从上面那些 prompt 表**派生**，再并上少量实际观察到的前缀（`_PROACTIVE_OBSERVED_CONTEXT_PREFIX_LABELS`）。
- 两处调用点：Phase 2 流结束后、投递前；防复读重写的结果上再剥一次。

## 改这块的规则

- **名单从 prompt 表派生，不手写。** 往 `ACTIVITY_TONE_HINTS` 之类的表里加条目时不用动剥离代码；新增一张会进 Phase 2 prompt 的「标签：说明」表时，要把它接进 `get_proactive_intent_leak_labels()`。
- **只剥开头、只做整段精确匹配。** 只在三种形态下剥：第一行整行就是标签且后面还有正文；`标签：正文`；`标签/正文` 或 `/标签正文`。标签两侧的 markdown 装饰和括号先去掉再比。句子中间出现同样的词不动。
- **常用词不进名单。** `open`、`idle`、`gaming`、`chatting` 这类单词，以及各语言里本身就是日常用语的状态名（`_ACTIVITY_ENUM_COMMON_WORDS`、`_ACTIVITY_RENDERED_COMMON_LABELS_BY_LANG`）被排除，否则会误删正常台词的第一行。
- **冒号有全角和半角两种。** 中文、日文用全角，其它语言用半角，取「最早出现的那个冒号」做分隔，不要全角优先。
- **所有语言的标签取并集。** 泄漏的标签和正文可能不是同一种语言。
- 剥完变成空串就按「生成为空」放弃这一轮。

## 诊断时注意：语音和显示两条链路不对称

TTS 链路上的 `TtsBracketStripper`（`utils/frontend_utils.py`）会连括号带内容整段不念（书名号除外）；显示链路只有上面的名单。所以：

- 带方括号的泄漏只污染气泡，听不到。
- 不带括号的裸词，语音也会念出来。
- 「气泡第一行没有方括号」不能反推模型原始输出里没有括号。

## 不在这道防护范围内

模型自己用了不该用的称呼（把用户叫成物化的称呼）是另一类问题，输出侧没有防护；`tests/unit/test_proactive_text_does_not_dehumanize.py` 只检查 prompt 模板，不检查模型输出。

相关测试：`tests/unit/test_proactive_intent_label_leak.py`。
