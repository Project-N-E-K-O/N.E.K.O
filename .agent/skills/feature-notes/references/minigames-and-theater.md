# 小游戏、小剧场、卡片锻造

## 1. 小游戏的 prompt 模块怎么分

`config/prompts/` 里和小游戏有关的文件：

- **具体游戏各一个文件**：`prompts_soccer.py`、`prompts_badminton.py`、`prompts_drawing_guess.py`、`prompts_watch_together.py`。某个游戏的系统 prompt、quick-lines、开局上下文都收在它自己的文件里。
- **跨游戏的按层分**：编排用 `prompts_minigame_route.py`，共享 helper 用 `prompts_minigame_common.py`（`_normalize_prompt_lang`、`_localized_template`、`PREGAME_CONTEXT_INPUT_WATERMARK`）。
- galgame 是对话 / 剧情模式，不算小游戏，不共用这套 helper（`prompts_galgame.py` 的 docstring 有说明）。
- **不建纯转发的门面文件。** 拆模块或改名时直接改调用方的 import。
- 跨模块的 prompt 不能写具体游戏的名词，见 `.agent/rules/neko-guide.md` 的「跨模块 prompt 不能写死特定游戏 / 功能」。

### ⚠️ 两套 locale 键有意并存

- 羽毛球 quick-lines 的表用**完整 locale** 做键，简体是 `zh-CN`，归一函数是 `normalize_badminton_prompt_locale`。
- 足球和所有系统 / 开局 prompt 的表用**短码**做键，简体是 `zh`，归一函数是 `prompts_minigame_common._normalize_prompt_lang`。
- 两套现在都保留 `zh-TW`，区别只在简体怎么拼。不要统一：两族表的键不一样，改任何一边的拼法只会让它自己的表查不到。
- `_normalize_prompt_lang` 的默认值是 `zh` 而不是 `en`，是有意的（这个模块里有写死中文标点的 helper）。
- ⚠️ 有几张表是用 `.get(key) or table["en"]` 读的，不经过 `_loc`。这种表缺 `zh-TW` 行会直接掉到英文，不会回落简体。往这里加表必须带 `zh-TW` 行。

### quick-lines 的写法

- 每个必需 key 配两条示例，外面包一对 `======以下为必需 keys======` / `======以上为必需 keys======`。这对标记是水印，每种语言都保留中文原样，标记里面不要出现冒号和破折号。
- ⚠️ 示例里不能有裸的 `{` 或 `}`：这段 prompt 会被 `.format(name=..., personality=...)` 处理。

## 2. 斜杠快捷指令

文本框整条消息只有 `/别名`（半角或全角斜杠）时，直接打开对应小游戏，不经过模型，也不走邀请状态机。

- 别名表：`config/prompts/prompts_proactive.py` 的 `MINI_GAME_MAGIC_COMMANDS`。各语言的别名一起匹配，因为用户打字的语言不一定和界面语言一致。
- 识别位置：`main_logic/core/turn.py` 的 `_maybe_handle_mini_game_magic_command`，在任何「会话是否就绪」的检查之前。打开游戏不需要模型会话，所以会话建不起来不能把指令吞掉，语音会话里打指令也不能把语音会话拆掉。
- **既定规则：识别放在后端，不改成前端识别。** 「语音会话里打斜杠指令会被切成文字会话」这类意见不成立；再有人提把识别挪到前端，按此驳回。
- 这条指令只丢弃**它自己这次请求**带的附件。待发图片的队列是整个会话共用的、不带请求身份，整个清空会把前一条消息还没发出去的图也清掉。

## 3. 小游戏的宿主路由和 SDK

SDK 的用法和状态机见 `static/game/sdk/README.md`。README 之外需要知道的：

- 每个游戏的静态资源放在 `static/game/games/<游戏名>/` 下。体量大的游戏用自己独立的资源版本号做缓存键（例子：`main_routers/pages_router.py` 里空气投篮的版本号），不并进全局的静态资源版本。
- 同一个角色同一时间只允许一条活动的游戏路由；新开一局由 `/route/start` 里的「顶替」逻辑收掉旧的（`main_routers/game_router/runtime.py` 的 `game_route_start`）。
- `/route/start` 只检查角色名非空，**不校验角色是否存在**。要校验得在游戏前端用 SDK 的只读接口 `game.avatar.getCharacter()` 先查。
- ⚠️ 后端结束一条游戏路由时会无条件取消这条路由的语音，并经 WebSocket 发 `game_route_speech_cancel`，把浏览器里**正在播的那句**掐掉（`main_routers/game_router/postgame.py`、`route_lifecycle.py`）。所以「结算台词」要等这句真正播完再结束路由，并设一个等待上限。
- 小游戏相关的会话池在区域没落定时是放行并打警告（主会话是不放行），见 `references/region-telemetry-survey.md`。

## 4. 小剧场

设计和决策记录在 `docs/design/neko-theater-architecture.md`、`neko-theater-decisions.md`、`neko-theater-market-integration.md`。下面是写测试、查问题时容易踩的。

- ⚠️ **发给模型的上下文不是 JSON。** `services/theater/numeric_v2_context.py` 的 `render_prompt_context` 产出的是若干个 `======以下为X======` / `======以上为X======` 包起来的块。对整段内容 `json.loads` 一定抛 `JSONDecodeError`；如果外层有 `except Exception`，会被收成「不可用」状态，看起来像是角色设定缺失。读回某个块用 `services/theater/prompt_data.py` 的 `prompt_data_block_payload(text, label)`，它和写入用的 `prompt_data_block` 成对，也处理了分隔符转义和 CRLF。
- 这些 `======…======` 标记同时是发给模型的水印，不要翻译、不要改写。
- 现状：三部内置剧本（`assets/theater/`）的 `meta.language` 都是 `zh-CN`；`main_routers/theater_market_router.py` 不带语言参数。也就是说任何界面语言的用户拿到的剧本正文都是中文，没有语言闸。做剧场多语言时这是起点。
- 工坊生成新剧本用的指令（`config/prompts/prompts_theater_pipeline.py` 的 `_FRAME_PROMPT`、`_STATE_PROMPT` 等）是纯中文、没有分语言的表，而且每一条都对应生成结果的一项校验。翻译这些指令如果有偏移，生成结果会过不了校验，所以每种语言都要实际跑一遍生成来验证，不能只做文本翻译。
- 前端测试不要拿页面 `title` 当稳定契约：`/theater` 带市场参数时会自己跳到外部市场站，`/chat` 的标题会被页面脚本改成角色名（实测观察）。
- `window.open('', name)` 不会清空已经存在的同名窗口（实测观察）。所以「已有窗口不是 `about:blank`」可以用来判断这个窗口是不是本次新建的；只对本次新建的空白窗口写占位内容或关闭它。
- 在 Windows 上读内置剧本 JSON 必须显式 `encoding='utf-8'`，否则按系统默认编码读会失败。

## 5. 卡片锻造（记忆候选）

- 本仓库这侧的入口是 `main_logic/card_forge_facts.py` 的 `build_forge_facts_payload`，经 `main_routers/card_drop_router.py` 的 `/api/card-drop/facts` 和 `/api/card-drop/facts/query` 暴露给社区端页面。
- 「记忆够不够锻造」的预检在社区端，不在本仓库。它读返回里的 `totalMemoryCount`（没有时回落 `rawCount`）和 `facts` 列表里有效候选的条数。`fallbackReason`、`excludedCount` 目前社区端界面不读（消费方不在本仓库，以上是对当时社区端代码的观察）。**改这几个字段的计数或过滤语义之前，先确认消费方的用法。**
- **不按记忆来源禁止锻造**，包括 AI 披露类的记忆。要让某类内容不可锻造（比如应用内道具互动、音乐回调产生的记录），做法是写入时带结构化标记，不要按来源或正文关键词过滤。
- 机器日志式的行不作候选、也不计入总数：`_looks_like_machine_log` 认正文里的 UUID 和 ISO 日期时间。⚠️ 这类正则的边界用前后字符断言，不用 `\b`：中文字符在正则里算单词字符，`\b` 在中文和数字之间不成立。
