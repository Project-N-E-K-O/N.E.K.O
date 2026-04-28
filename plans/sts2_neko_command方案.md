# 杀戮尖塔自然语言总入口方案

## 1. 背景

当前 `sts2_autoplay` 插件已经具备比较完整的底层能力，包括状态检查、快照读取、单步执行、单卡推荐、单卡执行、后台自动游玩、暂停、恢复、停止、软指导、模式设置、角色策略设置等。

这些能力作为底层工具是合理的，但如果直接暴露给普通用户或让上层模型在大量入口之间自行选择，就容易出现以下问题：

- 用户不知道该说什么、该用哪个入口。
- 模型可能把“给建议”误判成“实际操作”。
- 自动游玩、软指导、单步执行、单卡执行的边界过细，使用成本高。
- 入口越多，提示词越复杂，误调用概率越高。

因此建议采用“底层细、顶层粗”的设计：保留现有底层入口，但新增一个人工友好的自然语言总入口，由总入口内部完成意图识别、默认值补全、安全拦截和分流。

## 2. 目标

新增一个统一入口，让用户只需要自然表达意图，不需要记住具体工具名。

用户可以直接说：

- “这回合怎么打”
- “帮我打一张牌”
- “帮我打一步”
- “帮我打这一关”
- “先防一下”
- “暂停一下”
- “继续”
- “停了吧”

总入口负责判断应该执行以下哪类行为：

- 只查看状态。
- 只推荐，不操作。
- 实际打出一张牌。
- 执行一步合法动作。
- 启动半自动游玩。
- 暂停、恢复或停止自动游玩。
- 在自动游玩中发送软指导。
- 无法判断时返回澄清，不执行危险动作。

## 3. 总体原则

### 3.1 控制优先

暂停、停止、恢复这类控制命令优先级最高。

用户说“暂停一下”“停了吧”“别打了”时，应立即进入控制分流，不应再分析成战术建议。

### 3.2 咨询不动

用户只是问“怎么打”“打哪张好”“帮我看看”时，默认只给建议，不执行任何游戏动作。

### 3.3 授权才动

只有用户明确说“帮我打”“执行”“出一张”“自动打”“托管”时，才允许执行实际动作。

### 3.4 托管限范围

自动游玩必须有停止条件。

默认建议：

- “打这一关”“打一层”：`current_floor`
- “打完这场战斗”：`current_combat`
- “一直托管”：`manual`，但建议先确认

不建议在用户没有明确范围时默认无限托管。

### 3.5 运行中指导入队

如果后台自动游玩正在运行，用户说“先防一下”“别贪输出”“优先保命”等战术话术，应转成软指导，而不是立即打断或执行单步。

### 3.6 模糊表达不执行危险动作

如果无法判断用户是否授权实际操作，应返回澄清或保守给建议。

## 4. 新增入口设计

建议新增入口：

```python
sts2_neko_command(command: str, scope: str = "auto", confirm: bool = False)
```

### 4.1 参数

| 参数 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `command` | string | 必填 | 用户原话，例如“这回合怎么打” |
| `scope` | string | `auto` | 可选意图提示，可为 `auto`、`status`、`advice`、`one_card`、`one_action`、`autoplay`、`control`、`guidance` |
| `confirm` | boolean | `false` | 是否由上层明确确认可执行实际操作 |

### 4.2 返回结构

建议统一返回：

```json
{
  "status": "ok",
  "intent": "recommend_card",
  "action": "recommend_one_card",
  "executed": false,
  "needs_confirmation": false,
  "summary": "我建议这回合优先打……",
  "result": {}
}
```

字段说明：

| 字段 | 说明 |
| --- | --- |
| `status` | 处理状态 |
| `intent` | 识别出的用户意图 |
| `action` | 实际分流到的内部动作 |
| `executed` | 是否执行了游戏操作 |
| `needs_confirmation` | 是否需要用户确认 |
| `summary` | 给用户看的摘要 |
| `result` | 底层入口返回的原始结果 |

## 5. 分流规则

总入口内部按以下优先级判断。

### 5.1 控制类

用户话术：

- “暂停”
- “先停一下”
- “等一下”
- “别动”
- “继续”
- “恢复”
- “接着打”
- “停止”
- “停了吧”
- “别打了”
- “结束托管”

分流动作：

| 意图 | 底层动作 |
| --- | --- |
| 暂停 | `pause_autoplay` |
| 恢复 | `resume_autoplay` |
| 停止 | `stop_autoplay` |

控制类属于降低风险操作，不需要额外确认。

### 5.2 状态类

用户话术：

- “尖塔连上了吗”
- “现在什么情况”
- “刷新一下状态”
- “看看当前局面”
- “当前有哪些合法动作”

分流动作：

| 意图 | 底层动作 |
| --- | --- |
| 健康检查 | `health_check` |
| 获取状态 | `get_status` |
| 获取快照 | `get_snapshot` |
| 刷新状态 | `refresh_state` |

状态类只读，不执行任何游戏操作。

### 5.3 咨询类

用户话术：

- “这回合怎么打”
- “打哪张牌好”
- “帮我看看出牌”
- “给个建议”
- “分析一下局面”

分流动作：

- 优先调用单卡推荐能力。
- 不打牌，不结束回合，不选奖励，不走地图。

关键规则：只要没有明确授权词，就只推荐。

### 5.4 单卡授权类

用户话术：

- “帮我打一张牌”
- “帮我选一张牌打出去”
- “直接出一张”
- “这张你来打”

分流动作：

- 调用单卡执行能力。
- 只允许从当前可执行的 `play_card` 动作中选择。
- 执行前仍需重新校验动作合法性。

### 5.5 单步授权类

用户话术：

- “帮我打一步”
- “执行一步”
- “你操作一下”

分流动作：

- 调用单步执行能力。

注意：单步执行比单卡执行更宽，可能包含结束回合、选奖励、走地图等。因此只有明确出现“打一步”“执行一步”时才执行。

### 5.6 自动游玩类

用户话术：

- “帮我打这一关”
- “帮我打一层”
- “打完这场战斗”
- “自动打一下”
- “托管一下”

分流动作：

- 调用自动游玩启动能力。
- 自动推断 `stop_condition`。

推荐映射：

| 用户说法 | `stop_condition` |
| --- | --- |
| “打一关”“打一层” | `current_floor` |
| “打完这场战斗”“这场打完” | `current_combat` |
| “一直打”“持续托管” | `manual`，建议确认 |
| 无明确范围 | `current_floor` |

### 5.7 软指导类

如果自动游玩正在运行，用户说：

- “先防一下”
- “别贪输出”
- “优先保命”
- “能斩就斩”
- “省点资源”
- “别乱花钱”

分流动作：

- 调用软指导能力。
- 指导内容原样进入队列。
- 下一轮 LLM 决策参考该指导。

如果自动游玩没有运行，则降级为咨询类建议。

## 6. 关键词表

| 意图 | 关键词 |
| --- | --- |
| 暂停 | 暂停、先停、等一下、别动 |
| 恢复 | 继续、恢复、接着打 |
| 停止 | 停止、停了吧、别打了、结束托管 |
| 状态 | 状态、情况、局面、快照、连上、健康、合法动作 |
| 咨询 | 怎么打、打哪张、建议、看看、分析 |
| 单卡执行 | 打一张牌、出一张、选一张牌打出去 |
| 单步执行 | 打一步、执行一步、操作一下 |
| 自动游玩 | 打这一关、打一层、打完这场、自动打、托管 |
| 软指导 | 先防、保命、别贪、优先输出、能斩就斩、省资源 |

## 7. 安全策略

### 7.1 默认安全行为

| 场景 | 行为 |
| --- | --- |
| 用户只是问建议 | 只推荐，不操作 |
| 用户表达模糊 | 不执行危险动作 |
| 用户要求自动打但没说范围 | 默认 `current_floor` |
| 用户要求无限托管 | 返回确认或要求 `confirm=true` |
| 当前状态不可用 | 先返回连接或状态错误 |
| 低血量或高风险局面 | 复用已有低血量暂停、危险攻击减速逻辑 |

### 7.2 授权判断

建议把用户表达分为三档：

| 档位 | 示例 | 是否执行 |
| --- | --- | --- |
| 咨询 | “这回合怎么打” | 否 |
| 明确授权 | “帮我打一张牌” | 是 |
| 模糊授权 | “你看着办” | 否，先推荐或澄清 |

### 7.3 危险动作确认

以下动作建议要求更强授权：

- 无限托管。
- 低血量继续自动游玩。
- Boss 战中恢复高速自动游玩。
- 非战斗界面的关键选择，例如删牌、买遗物、路线选择。

第一版可以先不强制做复杂确认，但总入口要预留 `needs_confirmation` 字段。

## 8. 用户体验文案

建议在文档或前端中展示：

> 你不用记尖塔指令，直接说想让我做什么。问“这回合怎么打”我只会给建议；说“帮我打一张牌”我才会实际出牌；说“帮我打这一关”我会开始半自动；中途可以说“先防一下”“暂停”“继续”“停了吧”。

## 9. 实现计划

### P0：新增自然语言总入口

在插件入口层新增 `sts2_neko_command`。

入口描述应明确：

- 这是杀戮尖塔普通用户自然语言总入口。
- 当用户没有明确指定底层工具时，优先调用该入口。
- 该入口会内部判断推荐、执行、自动游玩、暂停、恢复、停止或软指导。

### P1：服务层新增分流方法

在服务层新增 `neko_command` 方法，负责：

1. 标准化 `command`。
2. 获取当前状态和自动游玩状态。
3. 识别意图。
4. 调用现有服务方法。
5. 返回统一结构。

### P2：更新 README

在 `README.md` 中新增“普通用户推荐说法”章节，并把底层入口标为开发者接口。

### P3：新增单元测试

建议新增测试文件：`tests/unit/test_sts2_autoplay_neko_command.py`。

覆盖：

- “这回合怎么打”只推荐，不执行。
- “帮我打一张牌”实际出牌。
- “帮我打一步”执行一步。
- “帮我打这一关”启动自动游玩。
- “先防一下”在自动游玩中进入软指导。
- “暂停一下”暂停自动游玩。
- 模糊说法不会执行危险动作。

## 10. 第一版伪代码

```python
async def neko_command(self, command: str, scope: str = "auto", confirm: bool = False) -> dict:
    text = normalize_text(command)
    status = await self.get_status()
    autoplay_state = status.get("autoplay", {}).get("state")

    if is_stop_command(text):
        return wrap("stop", await self.stop_autoplay(), executed=False)

    if is_pause_command(text):
        return wrap("pause", await self.pause_autoplay(), executed=False)

    if is_resume_command(text):
        return wrap("resume", await self.resume_autoplay(), executed=False)

    if is_status_command(text):
        return wrap("status", await self.get_snapshot(), executed=False)

    if autoplay_state == "running" and is_guidance_command(text):
        return wrap("guidance", await self.send_neko_guidance({"content": command}), executed=False)

    if is_advice_command(text):
        return wrap("recommend_card", await self.recommend_one_card_by_neko(objective=command), executed=False)

    if is_play_one_card_command(text):
        return wrap("play_one_card", await self.play_one_card_by_neko(objective=command), executed=True)

    if is_step_once_command(text):
        return wrap("step_once", await self.step_once(), executed=True)

    if is_autoplay_command(text):
        stop_condition = infer_stop_condition(text)
        if stop_condition == "manual" and not confirm:
            return need_confirmation("manual_autoplay", "你要我持续托管吗？")
        return wrap("start_autoplay", await self.start_autoplay(objective=command, stop_condition=stop_condition), executed=True)

    return {
        "status": "clarify",
        "intent": "unknown",
        "executed": False,
        "needs_confirmation": True,
        "summary": "我不确定你是想让我只给建议，还是实际操作。为了安全，我先不动牌。"
    }
```

## 11. 最终结论

建议保留现有底层工具，不继续增加更多细碎用户入口；新增一个统一的“尖塔猫娘自然语言总入口”。

最终形态是：

- 用户只面对一个入口。
- 入口内部做人工友好的判断。
- 咨询默认不动。
- 授权才操作。
- 自动游玩必须有范围。
- 运行中战术话术转软指导。
- 控制命令优先。
- 模糊表达保守处理。

这样既能保留当前复杂策略和自动化能力，又能显著降低用户使用门槛。