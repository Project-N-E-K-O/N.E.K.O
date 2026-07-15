# Avatar 道具交互提示词设计规范

本文定义聊天框道具交互提示词的长期维护规则，适用于 `lollipop`、`fist`、`hammer` 以及后续新增道具。它关注的是模型收到的即时反应提示词，不覆盖前端动画、掉落物坐标、桌面端窗口形态或普通聊天主 prompt。

如果本文与当前代码、测试或真实生成结果冲突，以可复现证据和当前代码为准，并先更新本文再继续修改。

## 当前代码入口

主要文件：

1. `config/prompts/avatar_interaction_contract.py`
   - tool/action/intensity/special-field 和 payload normalizer。
2. `config/prompts/prompts_avatar_interaction.py`
   - prompt、reaction profile、memory 模板和 text-context sanitizer。

关键结构：

1. `AVATAR_INTERACTION_TOOL_CONTRACT`
2. `normalize_avatar_interaction_payload`
3. `_sanitize_avatar_interaction_text_context`
4. `_AVATAR_INTERACTION_REACTION_PROFILES`
5. `_AVATAR_INTERACTION_TOUCH_ZONE_FACTS`
6. `_build_avatar_interaction_instruction`

当前提示词只有一条直接生效的事件事实链路，结构是：

```text
客观事件事实
```

检查提示词时必须调用 `_build_avatar_interaction_instruction` 看最终字符串；不要从 payload 字段或 memory note 推测模型实际收到的内容。

## 核心目标

道具交互提示词要让角色像在聊天里自然接到一个刚发生的事件，而不是让模型写一段描写、模板台词或系统字段复述。

目标：

1. 每次只提供刚发生的客观事件。
2. 让回复保留当前角色人设和聊天上下文里的自然表达，不在道具提示词里重新规定固定语气。
3. 保留不同道具和不同子事件的辨识度。
4. 避免同一道具多轮点击后稳定坍缩成同一句式。
5. 8 个语言的事实结构一致，只做本地化表达，不额外加某个语言独有的规则。

## 非目标

不要把道具提示词写成完整角色设定、写作教程或输出审查器。

非目标：

1. 不在这里规定猫娘固定口头禅、固定语气词、固定称呼或固定撒娇方式。
2. 不用道具提示词修正所有普通聊天风格问题。
3. 不把前端动画、坐标、概率、payload、输入框草稿等实现细节暴露给模型。
4. 不把单次测试里出现的坏回复逐条塞进提示词禁止项。
5. 不把没有代码事实支持的设定写进事件，例如道具材质、真实疼痛程度、距离变化、关系推进、角色动作姿势。

## 事件事实写法

`reaction_focus` 只写事件事实。它应该回答四个问题：

1. 谁触发：使用 `{actor}`。它由代码从 `master_name` 解析而来；`master_name` 为空时会回落到本地化中性称呼。
2. 发生在谁身上：使用第二人称指向角色，例如中文的“你”。
3. 用了什么道具：明确写出棒棒糖、猫爪、锤子或新增道具名。
4. 做了什么：明确动作、次数、频率或附加结果。

推荐形态：

```text
{actor}刚刚用<道具>对你做了<客观动作>。
```

或：

```text
{actor}刚刚用<道具><频率/次数>地<客观动作>，并触发了<客观结果>。
```

不要在 `reaction_focus` 写这些内容：

1. 回复应该急、软、可爱、委屈、傲娇、自然口语等语气指导。
2. “不要模板化”“不要三段式”“不要复读”等输出诊断。
3. “像真人一样”“正常人会怎么说”等抽象表达要求。
4. 括号动作、舞台指令、旁白描述。
5. “别闹”“你又来”“哥哥坏”等固定台词方向。
6. 当前事件没有提供的主观判断或剧情补全。

## 当前道具事件边界

新增或修改时要保留同一工具内部的多事件差异，不要把一个道具压成单一事件。

| 道具 | action | intensity / flag | 事件语义 |
|---|---|---|---|
| `lollipop` | `offer` | `normal` | 对方把棒棒糖递到嘴边，角色吃了第一口 |
| `lollipop` | `tease` | `normal` | 同一支棒棒糖再次递到嘴边，角色吃了第二口 |
| `lollipop` | `tap_soft` | `rapid` | 棒棒糖一口接一口递到嘴边，角色连续吃了几口 |
| `lollipop` | `tap_soft` | `burst` | 短时间内连续递到嘴边，角色吃了好几口 |
| `fist` | `poke` | `normal` | 猫爪轻轻碰一次 |
| `fist` | `poke` | `rapid` | 猫爪连续轻轻碰几次 |
| `fist` | `poke` | `reward_drop=True` | 猫爪轻碰或连续轻碰时掉出奖励，不覆盖原 intensity 事实 |
| `hammer` | `bonk` | `normal` | 锤子敲中一次 |
| `hammer` | `bonk` | `rapid` | 短时间内又敲中一次 |
| `hammer` | `bonk` | `burst` | 锤子连续快速敲中好几次 |
| `hammer` | `bonk` | `easter_egg=True` 且 `intensity=easter_egg` | 放大彩蛋锤敲中一次 |

修改事件时，先确认前端 payload 和 `normalize_avatar_interaction_payload` 的归一逻辑。不要只改文案导致 action、intensity、flag 与事件事实互相打架。

## 多语言一致性

支持语言：

```text
zh / zh-TW / en / ja / ko / ru / es / pt
```

一致性要求：

1. 每个 locale 都要覆盖相同的工具、action、intensity 和附加结果。
2. 每个 locale 的 `reaction_focus` 要表达同一个客观事实，可以按语言习惯本地化，不要求逐字翻译。
3. 每个 locale 都应使用同样简短的直接事件事实结构。
4. 不要让某个语言额外加入口吻要求、禁令、前端字段或动作括号。
5. 事件事实使用 `{actor}`，不要直接把 `{master_name}` 写进运行时事件句；`{master_name}` 为空、纯空白或跨语言语法需要由 actor helper 统一处理。
6. 连接基础事实与位置事实时，中文、繁中、日文可不加空格；韩文、英文、俄文、西语、葡语使用自然空格。
7. 猫爪和锤子的 `touch_zone` 是事件事实：有值时必须在 prompt 和 memory 中保持 `ear/head/face/body` 的真实位置；无值时不补默认位置。棒棒糖不消费该字段。

检查时至少生成 8 个语言的代表样例，不能只读字典。

## 新增道具模板

新增道具时按这个顺序做，不要先写大段 prompt。

### 1. 定义 payload 事件

先写清楚事件矩阵：

| 字段 | 内容 |
|---|---|
| tool_id | 新道具 id |
| action_id | 有哪些动作 |
| intensity | 每个动作允许哪些强度 |
| flag | 是否有 reward、easter egg 或其它附加布尔结果 |
| target | 是否仍只作用于 `avatar` |
| touch_zone | 是否需要位置事实 |

只有前端实际会发送、后端会归一的字段，才能进入提示词事实。

### 2. 更新允许列表和归一逻辑

需要同步：

1. `config/prompts/avatar_interaction_contract.py` 的 `AVATAR_INTERACTION_TOOL_CONTRACT`，在对应 action 下声明允许的 intensity、touch zone 能力和特殊布尔字段。
2. `normalize_avatar_interaction_payload`，仅当 wire alias 或特殊归一规则确实变化时修改。
3. `static/app/app-buttons.js` 的 `AVATAR_INTERACTION_CONTRACT`，保持宿主发送契约与后端一致。

`normalize_avatar_interaction_payload` 是 Python 侧唯一归一入口；真实 greeting 和 testbench 都直接导入它，并显式注入 `_sanitize_avatar_interaction_text_context`。不要保留第二个 normalizer facade，也不要再派生第二份 allowed actions / intensities / combinations 配置。

不要把无法被归一验证的事件写进 `reaction_focus`。

### 3. 更新事件事实

需要同步 8 个 locale：

1. `_AVATAR_INTERACTION_REACTION_PROFILES` 中对应 action/intensity/flag 的 `reaction_focus`。
2. `_AVATAR_INTERACTION_TOUCH_ZONE_FACTS`，仅当该道具声明位置能力或位置表达变化时修改。

### 4. 编写 reaction profile

每个事件写一条客观事实：

```python
"new_tool": {
    "new_action": {
        "normal": {
            "reaction_focus": "{actor}刚刚用<新道具><客观动作>了你一次。",
        },
        "rapid": {
            "reaction_focus": "{actor}刚刚用<新道具>连续<客观动作>了你几次。",
        },
    },
}
```

`reaction_focus` 要像事件记录，不要像角色台词。角色怎么说交给模型根据当前人设和上下文生成。

### 5. 检查直接事件事实 prompt

除非有明确设计变更，新道具也应沿用：

```text
reaction_focus
```

当前运行时只发送事件事实，不再保留 wrapper、字段列表、reply line 或 requirements 兼容分支。只有在真实输出证明模型无法识别聊天互动、且有可复现证据时，才重新评估统一结构；不要为单个道具加入示例台词、专属禁令、“一句话”或“固定语气”等格式压力。

`text_context` 暂时保留在 payload normalizer 中，由真实 greeting 和 testbench 调用方注入同一个 sanitizer，供历史调用与诊断预览使用；当前事件事实 prompt 不把它发送给模型，也不依据它改变反应。

## 修改现有道具模板

修改前先回答：

1. 要修的是事件事实不准确，还是模型生成回复不好？
2. 问题是否由提示词里的事件事实、无关约束、口吻暗示、语言不一致导致？
3. 是否只需要删减提示词，而不是新增更多约束？
4. 是否会破坏同一道具内部多个事件的差异？
5. 是否会让三个道具重新变成同一种泛化互动？

优先操作顺序：

1. 删除会带歪的无关内容。
2. 把主观描述改成客观事件事实。
3. 补齐丢失的道具名、动作、次数、频率或附加结果。
4. 对齐 8 个语言。
5. 生成样例验证。

不要直接把坏回复里的词加入禁止列表。禁止列表很容易变成新的提醒，导致模型继续围绕坏词生成。

## 反模板化原则

反模板化不靠堆“要自然”这类要求，而靠减少提示词本身的模板压力。

有效做法：

1. 提示词只给事实，不给台词写法。
2. 同一道具不同事件的事实必须不同。
3. 不写固定情绪、不写固定称呼、不写固定语气词。
4. 不在 prompt 里同时给多个互相竞争的要求。
5. 不写示范台词，除非测试或人工评审明确需要临时对照，且示范不得进入运行时 prompt。

无效做法：

1. 反复要求“自然口语”。
2. 反复要求“不要重复”。
3. 把某次坏输出里的词逐个禁止。
4. 为了区分道具而编造额外剧情。
5. 用固定句式替换旧固定句式。

## 样例生成检查

修改后至少用脚本生成代表事件，人工扫一遍实际提示词。

```bash
uv run python - <<'PY'
from config.prompts.prompts_avatar_interaction import _build_avatar_interaction_instruction

base = {
    "target": "avatar",
    "interaction_id": "scan",
    "timestamp": 1,
    "text_context": "晚点再继续",
}

cases = [
    ("lollipop_first", {"tool_id": "lollipop", "action_id": "offer", "intensity": "normal"}),
    ("lollipop_repeat", {"tool_id": "lollipop", "action_id": "tap_soft", "intensity": "rapid"}),
    ("paw_reward", {"tool_id": "fist", "action_id": "poke", "intensity": "normal", "reward_drop": True, "touch_zone": "head"}),
    ("hammer_easter", {"tool_id": "hammer", "action_id": "bonk", "intensity": "easter_egg", "easter_egg": True, "touch_zone": "head"}),
]

for locale in ["zh", "zh-TW", "en", "ja", "ko", "ru", "es", "pt"]:
    print("\\n###", locale)
    for name, payload in cases:
        text = _build_avatar_interaction_instruction(locale, "YUI", "哥哥", {**base, **payload})
        print(f"{name}: {text}")
PY
```

人工检查重点：

1. 是否只出现事件事实。
2. 是否没有字段名、wrapper、payload、前端、坐标、草稿说明。
3. 是否没有括号动作、旁白、示例台词。
4. 是否没有某个语言额外多出一套约束。
5. 棒棒糖、猫爪、锤子是否一眼能看出不同。
6. 同一道具的不同事件是否能看出差异。
7. 新增或修改的事实是否完全来自 payload 和代码链路。

## 自动验证

提示词或 payload 归一逻辑修改后，至少运行：

```bash
uv run python -m compileall config/prompts/avatar_interaction_contract.py config/prompts/prompts_avatar_interaction.py
uv run pytest tests/unit/test_avatar_interaction_payload_contract.py tests/unit/test_avatar_interaction_memory_contract.py
node --test static/avatar-interaction-contract.test.cjs
```

如果新增了 tool、action、intensity、flag 或 locale 覆盖，优先补对应单元测试，至少覆盖：

1. payload 归一后事件进入正确 profile。
2. 缺失或非法 intensity 会被直接拒绝，不会回落到其它事件事实。
3. flag 与 intensity 冲突时直接拒绝，不改写事件事实。
4. 8 个 locale 都能生成非空 prompt。
5. 生成结果不包含运行时不应暴露的字段。

## 提交前清单

提交前确认：

1. `git status --short` 中除明确目标文件外，没有误带其它 tracked 修改。
2. 未跟踪 `.agent`、临时日志、测试产物没有被加入提交。
3. `config/prompts/prompts_avatar_interaction.py` 的实际生成结果已看过。
4. 8 个 locale 都已同步。
5. 多事件没有被简化成单一事件。
6. 没有新增固定语气、固定台词、括号动作或主观臆测。
7. 相关检查已运行，并在最终说明中写清楚。
