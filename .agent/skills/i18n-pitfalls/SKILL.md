---
name: i18n-pitfalls
description: 多语言相关的既定规则和坑：locale 文件同步门怎么判、什么时候红着由维护者手工批准、碰 static/locales 的分支合 main 必冲突的三处和解法、提示词的 zh-TW 机制、对用户输入做中文关键词匹配的判据、禁用物化称呼。改 static/locales、config/prompts 的多语言表、或任何按语言分支的后端代码之前读。
---

# i18n 的规则和坑

## 和已有文档的分工

- `.agent/skills/i18n/`：前端硬编码中文的检测、替换成 i18n 标记的写法。
- `.agent/rules/neko-guide.md`：八种语言必须同步；后端多语言字符串（含嵌套 dict）一律放 `config/prompts/prompts_*.py`；跨模块 prompt 不能写具体游戏 / 功能的名词；翻译系统提示词要保留水印。这几条那边写全了，这里不重复，只补充下面几点：
  - 嵌套的语言表（外层或内层按语言分）整张都要搬进 `config/prompts/`，内层的查表键不算翻译，但它的值是。搬的时候顺手改成公开标识符（去掉模块私有的下划线前缀）。`scripts/check_prompt_hygiene.py` 只抓平铺结构，没被抓到不代表合规：规则的目的是加新语言时扫一遍 `config/` 就能补全。
  - 「写进普通记忆的字符串」后面会被复查用的 prompt 读到，等同于 prompt，也要用通用层的说法。

## 规则速查

### 1. `static/locales` 的同步门（`scripts/check_i18n_sync.py`）

- 门比的是「分支相对 merge-base 的 diff」：只要动了任何一个 locale 文件，八个文件都必须动，而且每个 diff 块的行范围必须一致。八个 JSON 约定同一个 key 在同一行。
- 所以：
  - **纯改文案**（每个 key 在原行内替换、不增删行、八种语言都改）天然对齐，门是绿的。
  - **增删 key** 必须八种语言同一行一起增删。数组内联成一行，一个 key 一行。
  - ⚠️ **只修部分语言的文案也会红**（比如只有西语和葡语译错了）。不要为了让门变绿去别的语言里塞假改动。
- 门没有行级豁免。**一次性的结构重排、或只修部分语言这类必然过不了门的改动：让这项检查红着，在 PR 描述里写明「i18n-sync 预期红，请维护者手工批准」，由维护者手工批准合并。不要去改脚本里的 `GROUPS`**，即使脚本的 docstring 提到过这种做法。改守门脚本会多出「合并后再恢复」的一步，还会招来对「放宽守门」的连环评审意见，得不偿失。
- 细节（怎么自查对齐、三点 diff、先提交再跑）见 `references/locale-files-and-merge.md`。

### 2. 改了 locale 的 key 结构就要换 `LOCALE_VERSION`

- 客户端按 `static/i18n-i18next.js` 的 `LOCALE_VERSION` 缓存语言包。加了 key 不换版本，缓存着旧包的客户端会把新 key 当字面量显示出来。
- `tests/unit/test_locale_cache_bust_contract.py` 盯着三样东西：`LOCALE_VERSION`、`RETIRED_LOCALE_VERSIONS`（用过的版本号）、`LOCALE_KEY_SIGNATURE`（八语 key 结构的指纹）。key 结构一变指纹就红，按失败信息做：换一个新的 `YYYY-MM-DD-slug`，旧值追加进退役表，更新指纹。
- 版本号用描述性的 slug，不加数字后缀。只改文案不改 key 的 PR 不用换。
- ⚠️ 分支和 main 都改过 locale 时，合并必然在这三处冲突，解法固定，见 `references/locale-files-and-merge.md`。

### 3. 禁用物化称呼

项目反对把 AI 放在附属、被支配的位置。**提示词模板、写进对话历史或记忆的说明文字、界面文案、占位符的回退文本里，都不能出现「主人 / master / ご主人 / 주인 / хозяин」以及其它语言里对等的称呼。** 这是项目的核心立场，不是文风偏好。

- 指人一律用占位符（各模块里叫 `{master}`、`{master_name}` 或 `{MASTER_NAME}`），由调用方填入用户设置的名字。
- 名字缺失时回退到**中性词**，不回退到物化称呼。仓库里现成的各语言写法：

  | 语言 | 作宾语 / 泛指 | 作主语 |
  |---|---|---|
  | zh | 对方 | 对方 |
  | zh-TW | 對方 | 對方 |
  | en | them | they / The other person |
  | ja | 相手 | 相手 |
  | ko | 상대 | 상대가 |
  | ru | собеседника | собеседник |
  | es | la otra persona / esa persona | esa persona |
  | pt | a outra pessoa / essa pessoa | a outra pessoa |

  出处：`config/prompts/prompts_directives.py` 的 `_DEFAULT_ADDRESSEE`、`prompts_avatar_interaction.py` 的两张 `*_FALLBACK` 表、`prompts_proactive.py` 的 `PROACTIVE_ACTION_NOTE_PLACEHOLDERS`。俄语等有格变化的语言，回退词要按它在句子里的位置选格。
- 八种语言的禁词表可以直接用 `config/prompts/prompts_visit.py` 的 `VISIT_FORBIDDEN_TERMS`（拉丁和西里尔文字按整词匹配，中日韩按子串；俄语的变格逐个列出）。新写一组模板时配一个断言不命中禁词的测试，现成例子：`tests/unit/test_behavior_notes.py`、`tests/unit/test_avatar_interaction_memory_contract.py`。
- ⚠️ 仓库里还残留少量这样的字面量（比如 `prompts_minigame_common.py` 里宿主固定上下文的中文行）。那是历史遗留，不是范本，不要照着写。
- 例外只有「数据」：记忆事实里的 `entity: "master"` 这类字段值、用户自己说的原话、禁词表本身。
- 涉及身份、人格、关系称谓、性别的字面量，下笔前先全仓搜一下现有写法；拿不准就问维护者。

### 4. 提示词的 zh-TW

- `config/prompts/` 下每张按语言分的表都必须有 `'zh-TW'` 行。门禁 `scripts/check_prompt_zh_tw.py` 不让缺口变多，`tests/unit/test_check_prompt_zh_tw.py` 把缺口数钉在 0。
- ⚠️ `_loc`（`config/prompts/prompts_sys.py`）缺键时是**静默回落**：中文变体落到简体，其它语言落到英文，只打印一行 WARNING。少做一步归一化的后果是「字形错了」而不是报错，没有任何信号。
- 传给 `_loc` 的语言码必须先过 `config/prompts/_locale.py` 的 `normalize_prompt_locale`（或各模块包好的归一函数）。直接把完整 locale（`zh-CN`）塞给以短码 `zh` 为键的表会走回落。
- `======…======` 的框是不是水印要**逐个位置判**：非中文语言全都写同一串中文的是水印，zh-TW 行照抄简体；其它语言各翻各的是正文，zh-TW 行必须是繁体。
- 详见 `references/prompt-locale-zh-tw.md`。

### 5. 对用户打的字做关键词 / 正则匹配

- 被全语种扫描的关键词表（否定词、别名、触发词）缺繁体就是 **0 命中**，不是降级。
- 窗口标题、进程名这类关键词表**不补繁体词条**，匹配两侧一起折叠成简体（`config/activity_keywords.py` 的 `fold_script`）。往表里加了带新汉字的中文别名后要重跑生成脚本。
- 补繁体会同时带来日文误命中（汉字同形）和「把简体一直存在的误报原样送给繁体用户」两类问题。
- 判据设计（闭集还是开集、往哪一侧收、正则会不会回溯爆炸）见 `references/zh-input-matching.md`。

## references 索引

| 文件 | 什么时候读 |
|---|---|
| `references/locale-files-and-merge.md` | 改 `static/locales/*.json`、合并 main 时 locale 相关文件冲突、同步门红了要判断该修还是该请维护者批准时 |
| `references/prompt-locale-zh-tw.md` | 在 `config/prompts/` 加或改按语言分的表、改调用方传的语言码、给插件加多语言时 |
| `references/zh-input-matching.md` | 改「从用户输入里识别指令 / 情绪 / 话题」的正则和词表（`prompts_directives.py`、`prompts_emotion.py`、`card_assist_router.py`、`activity_keywords.py` 等）之前 |
