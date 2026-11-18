# 反套话改写（界面上叫「自然表达」）

作用：构建发给对话模型的请求时，把**历史里角色自己说过的套话**（「心脏疯狂跳动」「嘴角勾起一抹弧度」这类）换成同义的别的说法，打断模型对自己文风的模仿。

候选短语的离线挖掘工具另见 `docs/contributing/natural-expression-candidate-miner.md`；它只产出待人工审核的候选，不生成规则。

## 核心前提：只改发出去的副本

- 引擎 `utils/slop_filter.py::apply_slop_reduction(messages, lang)` 是纯函数，返回新列表，不改传入的消息对象。持久化的对话历史、用户看到的原文都不动。
- 因此误改的危害被限制在「模型看到的历史和原文略有出入」，开关关掉就完全恢复。这是它能默认开启的理由，任何改动都不能破坏这个前提（比如把改写结果写回 `_conversation_history`）。

## 结构

| 部分 | 位置 |
|---|---|
| 引擎 | `utils/slop_filter.py` |
| 规则表 | `config/prompts/prompts_slop.py::SLOP_RULES`，键是 `zh` / `zh-TW` / `en` / `ja` / `ko` / `ru` / `es` / `pt` |
| 开关 | 对话设置里的 `slopFilterEnabled`，默认开；读取在 `is_slop_filter_enabled()`，读失败按开处理 |
| 武装 | `main_logic/omni_offline_client/_lifecycle.py::_with_dialog_slop`，装饰 `stream_text` 和 `prompt_ephemeral`，按轮设置 / 复位 contextvar `_dialog_slop_lang` |
| 语言解析 | `resolve_dialog_slop_lang(user_language_provider)`；manager 建文字客户端时传 `lambda: self.user_language` |
| 测试 | `tests/unit/test_slop_filter.py` |

引擎行为：

- 只改**已完成的 assistant 轮**。system 和 user 消息原样通过。带工具调用的在途 assistant 轮（OpenAI 的 `tool_calls`、Anthropic 的 `tool_use` 块）不改：那是工具循环刚回灌的前缀，改了会让模型从用户没见过的措辞接着写。
- 同一条规则在历史里第 `SLOP_REPEAT_THRESHOLD`（现在是 2）次及以后的命中才改写，第一次原样保留。这是引擎常量，不是用户设置。
- 替换项的选择由规则和命中位置的稳定坐标推出，不用全局随机：重发同一请求、或者历史后面追加了新内容，旧位置的改写结果不变。改了匹配或替换池、想让结果重新洗牌时，递增 `SLOP_RULESET_VERSION`。
- 围栏代码、行内代码、URL 不参与计数也不改写。
- 语言没有规则集就整轮跳过，**不回落到别的语言**的规则。
- 繁体中文单独一套（`zh-TW`），并且要在「归一成短码」之前分流：短码归一会把 zh-TW / zh-Hant / zh-HK 都压成 `zh`。反过来也不能把整个函数改成用完整语言码，那样简体会变成 `zh-CN`，而简体规则集的键是 `zh`，会被空规则保护整轮跳过。
- 每条规则单独容错；编译失败的规则只报一次并永久跳过。
- 命中统计走 logger（只有规则 id 和数量）；涉及回复原文的调试输出只走 `print`，由 `NEKO_SLOP_DEBUG` 控制。

## 接入点必须三处对偶

改写挂在「归一化后的消息副本」上，每种后端一处，漏一处那种后端就静默失效：

- `utils/llm_client/openai_client.py::ChatOpenAI._params`
- `utils/llm_client/anthropic_client.py`（构建 payload 处）
- 原生 Gemini SDK 路径：`_slop_reduced_for_genai`（`_genai_support.py` 在转换成 contents 之前调用）

为什么不切在文字客户端的工具循环顶部：工具循环直接往 `_conversation_history` 本体追加工具轮来持久化，换成副本会破坏它。

## ⚠️ contextvar 不能漏进工具 handler

装饰器给整个回合武装了 `_dialog_slop_lang`。工具循环在它还非空时去 `await` 工具 handler；handler（插件、task agent 的工具）如果在同一个 task 或它派生的 task 里调 `utils.llm_client`，它那些与对话无关的 few-shot / 历史会被当成角色历史改写。

- 做法：执行工具 handler 的那一处用 `_suspend_dialog_slop()` 包住（把 contextvar 置空，退出时恢复），见 `_tools.py`。
- 新增任何「在对话回合内部 `await` 外部代码」的位置时，同样要挂起。
- 装饰器总是 set / reset（包括值为 `None` 时），用来清掉从父 task 继承的值。
- 守卫：`test_suspend_dialog_slop_hides_lang_from_nested_tool_calls`（同 task、嵌套 task、handler 抛异常三条路径）。

范围：只有文字模式（`OmniOfflineClient`）。realtime 语音不经过这条链路，是已知边界。

## 写规则、改规则时的检查清单

规则格式 `{id, name, find, replace, flags}`：`find` 是 Python `re`（不是 JS），`replace` 是替换池，可以用 `\1`、`\g<name>` 把捕获组带过去（走 `re.Match.expand`）。

静态正则改写自然语言句子中间的片段，有一类固有风险：`find` 匹配得比 `replace` 能正确表达的范围宽。下面每一条都是出过的问题类型：

1. **`find` 跨人称 / 性别 / 动词形态匹配，`replace` 却写死一种。** 屈折语（es / pt / ru / ko 等）尤其容易。做法：把 `find` 收窄到 `replace` 能覆盖的形态（例如只匹配第三人称），或者把「主语加限定动词」整段捕获成 `\1` 让替换项复用。
2. **体貌、时态被改。** `find` 混进了未完成体（持续的背景动作），替换池全是完成体（一次性事件）。做法：`find` 只留和替换池同体貌的分支。
3. **宾语、所有者被吞掉不还原。** `find` 把「瞥了**他**一眼」「揉了**我的**头」里的宾语或所有者匹配进去，替换后丢了是谁。做法：把它捕获成组，整个替换池每一条都带上；或者不匹配进去，留在外面。
4. **所有者留在匹配外面时，替换池必须以名词开头。** 「他的」后面接一条以动词或副词开头的替换项，会拼出不通的句子。优先「所有者留在外面、替换池全部名词开头」，而不是把所有者匹配进去丢掉。
5. **替换项注入了原文没有的细节。** 中性的动作被换成带敌意的；多出视线对象、耳语对象、地点、身体部位、原因。做法：替换池保持中性；或者把原文的那个细节捕获复用；或者收窄 `find`，让带特殊色彩的原文不被匹配（留着不改，比改错好）。
6. **一词两义的要保持情感中性。** 「心跳加快」可以是害怕也可以是心动，替换池不能全押一种。
7. **`find` 末尾的自由词捕获越界。** 末尾一个无界的「若干词」会跨子句吞词。做法：惰性量词加子句边界的前瞻。被后续固定 token 夹住的捕获没有这个问题。
8. **两个槽位必须是同一个人**（「他捏了捏**自己的**鼻梁」对「他捏了捏**她的**鼻子」）。正则没法直接约束两个组语义一致，用配对枚举的分支；各分支的组号不同，替换项里把所有分支的组拼在一起（没命中的组展开成空串）。
9. **句首大写。** 以小写代词开头的英文规则要加 `re.IGNORECASE`；加了之后，捕获到的句首大写代词如果被替换项放到句子中间，会出现错误的大写，所以替换项要以该捕获组开头。lookbehind 同样受 `IGNORECASE` 影响。
10. **CJK 规则不用 `\b`**（这些语言不靠空格分词）。
11. **含反向引用的字符串必须是 raw 字符串。** 非 raw 的 `"\1"` 是八进制转义，变成一个控制字符；模块 docstring 里举正则例子也要用 `r"""`，否则 `\b` 会静默变成退格符，`\g` 在新版 Python 里是语法警告。
12. 模块和函数的 docstring 用英文写（仓库有 docstring 禁 CJK 的守卫）。

非中英文的规则，写和审都要找母语水平的审校逐条看整个语种，不要只修评审点到的那一条：同一类问题在同语种的兄弟规则里通常成片出现。让自动化工具做这类审校时，明确它只返回修改建议、不直接改文件，再由人核对改动范围。

接受的残留：静态正则改写句中片段不可能做到零误伤。因为只影响模型看到的副本，这类残留是低危害、可接受的；发现一类就按上面的做法把整类收窄，不追求逐例兜底。
