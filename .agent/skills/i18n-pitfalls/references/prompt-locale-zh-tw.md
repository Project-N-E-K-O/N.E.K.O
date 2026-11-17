# 提示词的语言选择和 zh-TW

适用范围：`config/prompts/prompts_*.py` 里按语言分的表，以及把语言码传进这些表的调用方。

## 1. 语言码怎么流到表里

- **表的键有两种拼法。** 多数表简体用短码 `zh`；少数表（比如羽毛球 quick-lines）用完整的 `zh-CN`。繁体统一是 `zh-TW`。
- **唯一的归一函数**是 `config/prompts/_locale.py` 的 `normalize_prompt_locale(language, default=, simplified=, keep_traditional=)`。各模块包了一层自己的写法（`prompts_sys.normalize_sys_prompt_locale`、`prompts_minigame_common._normalize_prompt_lang`、`prompts_badminton.normalize_badminton_prompt_locale` 等），区别只在这三个参数。它不依赖运行时注入，裸 import 和运行中的应用行为一致。输出只会是 `en / es / ja / ko / pt / ru`、简体键、`zh-TW` 之一。
- **取值函数** `prompts_sys._loc(d, lang)`：键在表里就返回；不在就打印一行 WARNING 然后回落，中文变体回落简体（先 `zh` 后 `zh-CN`），其它回落 `en`。`prompts_memory._loc`、`prompts_visit._loc` 是在它外面先做一次本模块归一的薄包装。

### 容易错的地方

- ⚠️ `utils/language_utils.py` 的 `normalize_language_code(x, format='full')` 对简体返回 `zh-CN`，而多数提示词表的简体键是 `zh`。把 `get_global_language_full()` 的结果直接塞给 `_loc`，简体用户会走回落（结果碰巧还是简体，但会刷 WARNING）；塞给 `table.get(k) or table["en"]` 这种写法的表就直接掉英文。**进表之前一律先过 `normalize_prompt_locale` 或模块自己的归一函数。**
- ⚠️ `table.get(key) or table["en"]` 这种读法不走 `_loc`，缺 `zh-TW` 行时掉的是**英文**，不是简体。
- ⚠️ `config/_runtime.py` 的 `normalize_language_code` 是运行时注入的转发器，没绑定时原样返回入参。提示词层如果靠它归一，裸 import（比如单元测试）和运行中的应用会是两种行为。
- ⚠️ `detect_language(text)` 只报文字系统，繁体和简体都是 `zh`。「从用户文本检测语言」的地方要用 `detect_prompt_language(text, default, ui_language=...)`，它在检测到中文时用界面语言区分繁简。`ui_language` 传**会话的 `user_language`**（前端 i18n 的真值），不要用进程级的 `get_global_language_full()`（来自 Steam 或系统语言）：用户在应用里切过语言时两者不一致。
- ⚠️ `if short in ("zh", "ja")` 这类分支（选中文引号、顿号之类）在调用方改传完整 locale 之后会漏掉 `zh-TW`，繁体用户掉进 ASCII 分支。改传参方式时把这类分支一起搜出来。
- **「是否保留繁体」的开关必须和调用点改传完整 locale 放在同一次改动里。** 只翻开关：调用点传的短码早已把繁简压成一样，等于没改。只改调用点：归一函数又把繁体压回去。
- 有些地方有意不改成完整 locale，理由写在各自的注释里，不要顺手「统一」：对外 API 契约里的短码；`prompts_directives._trim_term`（它选的是助词表的族，繁简共用一份）。

## 2. 两类「按语言分的表」，坏法不一样

| | 模板表 | 全语种扫描的词表 |
|---|---|---|
| 怎么用 | 经 `_loc` 按当前语言取一条 | 被 `*_flat` 之类的 helper 把所有语言的值拍平，对用户打的字做子串匹配 |
| 缺 zh-TW 的后果 | 换成简体或英文模板（降级） | 繁体输入 **0 命中**（功能失效） |
| 补上之后 | 还要调用方解析得出 `zh-TW` 才生效 | 立刻生效，不用改调用方 |

- 判断一张表属于哪类：看消费方是不是遍历 `TABLE.values()`。⚠️ 有假阳性：有的表遍历 `.values()` 只是为了派生一个排除清单，主要消费仍然按语言取。
- ⚠️ 繁体词条进了全语种扫描表，**对日文输入也生效**。和日文汉字同形的词会误命中日文句子，选词时挑日文里字形不同的写法。
- ⚠️ 给「标签别名表」加繁体词条，必须同时给对应的「否定词表」加繁体。只加前者，繁体的「没有 + 情绪词」会命中情绪词却识别不到否定，把意思读反，比不加更糟。
- 这类词表的更多判据见 `zh-input-matching.md`。

## 3. `======…======` 的框：水印还是正文

判据只有一条：**非中文语言是不是全都写同一串中文。**

- 全都一样：这是水印，是让模型在每种语言里看到同一个边界标记。zh-TW 行**逐字照抄简体**，不要转成繁体。
- ja / ko / ru 等各翻各的：这是正文。zh-TW 行**必须是繁体**，否则繁体用户在一段繁体提示词里读到一行简体。
- 同一张表里两种可以并存，所以要**逐个位置判**，不能整张表一刀切。
- 守卫：`tests/unit/test_prompt_watermark_locale_policy.py`（按位置比较，各语言标记数量一致的表才比）。
- 另一类有意保持简体的内容：角色配置里的字段键名。它们是识别码，转成繁体后模型回传的键就对不上了。

发给免费线路的提示词必须保留水印，否则请求会被拒；翻译或重排系统提示词时不要动 `======以上为` 这类标记（见 `.agent/rules/neko-guide.md`）。

## 4. 门禁和它查不到的东西

- `scripts/check_prompt_zh_tw.py`：`config/prompts/` 下同时有 `en` 和简体键的表必须有 `zh-TW` 键。CI 上按「缺口总数不许比 merge-base 多」来判；`tests/unit/test_check_prompt_zh_tw.py` 另外把总数钉在 0。看缺口用 `uv run python scripts/check_prompt_zh_tw.py --full`。
- 门禁只认几种规范写法（字面量、`dict()`、`|`、推导式、键值对序列、`dict.fromkeys`，以及紧跟的补键语句）。**不要为了让门禁认出一种新写法去扩它的解析器**：以前加过又撤掉，每次都引入新缺陷。证明不了的表，要么改成规范写法，要么挂 `# noqa: PROMPT_ZH_TW`。
- ⚠️ 不要用空表占位（`'zh-TW': []`）骗过门禁。确实不需要繁体行的表挂 `# noqa: PROMPT_ZH_TW`，这是一条能搜到的「有人判断过」的记录。
- 门禁**只查键在不在**，不查内容是不是繁体，也不查运行时能不能走到。成批补表之后另外做一次探针：导入所有模块，对每张表断言用 `zh-TW` 取值时没有走回落。
- 门禁扫不到 `config/prompts/` 以外的地方：插件自带的提示词文件、后端别处的零散文案。这些要自己查。

## 5. 写繁体内容

- 机器转换用 OpenCC 的 `s2tw`，不用 `s2t`（后者会出港式或古字写法）。即便是 `s2tw` 也要手工排除几个过度转换（比如把「台」转成「臺」）。OpenCC 只在开发时用，运行时不依赖，也不在项目依赖里。
- 台湾用词不是逐字转换能得到的，要按词替换。常见的几组：置信度→信賴度、搜索→搜尋、实时→即時、设备→裝置、运行→執行、返回→回傳、信号→訊號。
- 用脚本往带中日韩文字的 `.py` 文件里按位置插入内容时注意：`ast` 给的 `col_offset` 是 **UTF-8 字节偏移**，不是字符偏移。要把源码编码成字节后在字节串上拼接。
- 「差分结果是 0」不能直接当证据，先做一次变异验证（故意改坏一处看它报不报）。

## 6. 插件这一侧

- ⚠️ 插件里查 `config/prompts` 的表，**不要直接用 `get_global_language_full()` 的返回值当键**：简体会变成 `zh-CN`，在短码键的表里查不到、掉英文，比不做本地化更糟。统一走 `prompts_sys.normalize_sys_prompt_locale`。现成例子：`plugin/plugins/game_agent_minecraft/prompts.py` 的 `user_lang()`。
- ⚠️ **这类改动的测试必须同时覆盖简体和繁体。** 只测繁体的话，「把简体打到英文」的错误修法照样全绿。
- 插件自带的多语言表要加繁体时，三处一起改：表里加 `zh-TW` 行、支持语言清单加上 `zh-TW`、取用户语言的函数改成返回完整 locale。缺一处都不生效。
- `plugin/sdk/shared/i18n.py` 的 `PluginI18n` 的默认语言是构造时给的固定值，不跟着用户语言变。`t(key)` 不传 locale 时永远查默认语言那一本。
- 「编辑器按前端语言存、读取侧按短码候选链找」的配置（比如按语言存的提示词覆盖），读写两侧的语言码要在同一次改动里一起改，否则存进去的 `zh-TW` 读取侧找不到。
