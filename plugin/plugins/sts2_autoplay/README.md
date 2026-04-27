# 尖塔自动游玩（sts2_autoplay）

`sts2_autoplay` 用于把 `STS2 AI Agent` 暴露出来的本地《Slay the Spire 2》状态接入到 N.E.K.O。插件可以读取局面、执行合法动作、按策略自动游玩、让猫娘选择单张牌、向前端推送观察信息，并允许猫娘在后台任务中发送软指导来影响下一轮决策。

## 功能概览

- 连接本地 `STS2 AI Agent` HTTP 服务并读取游戏状态。
- 支持手动执行一步、后台半自动游玩、暂停、恢复和停止。
- 支持三种决策模式：`full-program`、`half-program`、`full-model`。
- 支持按角色加载策略文档，策略文件位于 `strategies/`。
- 支持猫娘单次选牌：只从当前可打出的 `play_card` 动作中选择一张牌，先推送原因，再执行。
- 支持猫娘软指导：用户或猫娘可以发送自然语言指导，下一轮 LLM 决策会参考。
- 支持后台观察汇报：把当前楼层、战斗、手牌、敌人意图、LLM 理由等推送给前端。
- 支持安全保护：低血量暂停、Boss/危险攻击减速、血量恢复后自动恢复、残血求生策略、收益最大化和连携评分。

## 依赖

本插件依赖上游 Mod `STS2 AI Agent` 提供的本地 HTTP 服务：

- 游戏内 Mod：`STS2AIAgent`
- 默认本地接口地址：`http://127.0.0.1:8080`
- 健康检查地址：`http://127.0.0.1:8080/health`

也就是说，这个插件工作的前提是：

1. 已经把 `STS2 AI Agent` 的 Mod 安装进《Slay the Spire 2》。
2. 游戏启动后，`http://127.0.0.1:8080/health` 可以访问。
3. N.E.K.O 中启用了 `sts2_autoplay` 插件。

## 安装 STS2 AI Agent

### 获取上游项目

```text
git clone https://gitclone.com/github.com/CharTyr/STS2-Agent.git
```

`STS2 AI Agent` 是给《Slay the Spire 2》使用的游戏 Mod + MCP Server 组合：

- `STS2AIAgent`：把游戏状态和操作暴露为本地 HTTP API。
- `mcp_server`：把本地 API 包装成 MCP Server，方便接入支持 MCP 的 AI 客户端。

本插件当前直接连接本地 HTTP API，不要求启动 MCP Server。

### 安装游戏 Mod

将上游项目中 `mods` 目录里的文件复制到尖塔游戏目录的 `mods/` 下：

```text
STS2AIAgent.dll
STS2AIAgent.pck
mod_id.json
```

Steam 默认游戏目录通常类似：

```text
C:\Program Files (x86)\Steam\steamapps\common\Slay the Spire 2
```

安装完成后目录应类似：

```text
Slay the Spire 2/
  mods/
    STS2AIAgent.dll
    STS2AIAgent.pck
    mod_id.json
```

### 启动游戏并确认接口

先正常启动游戏，让 Mod 随游戏加载。然后访问：

```text
http://127.0.0.1:8080/health
```

只要能拿到返回结果，就说明 `STS2 AI Agent` 的本地 HTTP 服务已经启动成功。

## MCP Server 说明

`sts2_autoplay` 不需要 MCP Server。它直接访问：

```text
http://127.0.0.1:8080
```

只有当你还想把尖塔接入其他支持 MCP 的 AI 客户端时，才需要单独启动上游的 `mcp_server`。

### 可选：启动 stdio MCP

运行环境：

1. `Python 3.11+`
2. `uv`

Windows 安装 `uv`：

```powershell
powershell -ExecutionPolicy Bypass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

macOS：

```bash
brew install uv
```

在上游 `mcp_server/` 工作目录下运行：

```text
uv run sts2-mcp-server
```

也可以使用上游脚本：

Windows：

```powershell
powershell -ExecutionPolicy Bypass -File ".\scripts\start-mcp-stdio.ps1"
```

macOS / Linux：

```bash
./scripts/start-mcp-stdio.sh
```

### 可选：启动网络版 MCP

Windows：

```powershell
powershell -ExecutionPolicy Bypass -File ".\scripts\start-mcp-network.ps1"
```

macOS / Linux：

```bash
./scripts/start-mcp-network.sh
```

默认 MCP 地址：

```text
http://127.0.0.1:8765/mcp
```

## 本插件配置

配置文件：`plugin.toml`

### 基础配置

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `base_url` | `http://127.0.0.1:8080` | 尖塔本地 Agent 地址。 |
| `connect_timeout_seconds` | `5` | 连接超时秒数。 |
| `request_timeout_seconds` | `15` | 请求超时秒数。 |
| `poll_interval_idle_seconds` | `3` | 空闲状态轮询间隔。 |
| `poll_interval_active_seconds` | `1` | 自动游玩运行时轮询间隔。 |
| `action_interval_seconds` | `1.5` | 每个动作之间的额外间隔。 |
| `post_action_delay_seconds` | `0.5` | 动作执行后等待局面稳定的间隔。 |
| `autoplay_on_start` | `false` | 插件启动后是否自动开始游玩。 |
| `semi_auto_autoplay` | `true` | 启动自动游玩时是否创建半自动任务上下文。 |
| `mode` | `half-program` | 当前自动游玩模式。 |
| `character_strategy` | `defect` | 角色策略名称，对应 `strategies/<name>.md`。 |
| `max_consecutive_errors` | `3` | 最大连续错误次数，超过后视为断开。 |
| `push_notifications` | `true` | 历史保留字段。 |
| `event_stream_enabled` | `false` | 预留字段，目前未实际启用。 |

### 决策模式

`mode` 支持以下值，也支持对应中文别名：

| 模式 | 中文别名 | 说明 |
| --- | --- | --- |
| `full-program` | `全程序` | 纯程序启发式，不调用模型。 |
| `half-program` | `半程序` | 先进行程序预检查，再调用一次模型决策，并做合法性校验/回退。 |
| `full-model` | `全模型` | 两次模型调用：先 reasoning，再 final action；中间进行程序检查，最终再做合法性验证。 |

### 角色策略

`character_strategy` 会按 `strategies/<name>.md` 查找策略文档。当前内置策略：

- `defect`
- `ironclad`
- `silent_hunter`
- `necrobinder`
- `regent`

你可以在 `strategies/` 中新增 Markdown 文件扩展策略。例如：

```text
strategies/my_strategy.md
```

然后把配置或入口参数设置为：

```text
my_strategy
```

### 前端推送与猫娘观察

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `llm_frontend_output_enabled` | `true` | 是否把自动游玩动作/错误主动推送到前端。 |
| `llm_frontend_output_probability` | `0.15` | 普通动作推送概率，范围会收敛到 `0.0 ~ 1.0`。错误会强制推送。 |
| `neko_reporting_enabled` | `true` | 是否推送更完整的猫娘观察报告。 |
| `neko_report_interval_steps` | `1` | 预留的汇报步数间隔配置。 |
| `neko_guidance_max_queue` | `50` | 猫娘软指导队列最大长度。 |

猫娘观察报告会携带 `report`、`neko_context`、`task` 等 metadata，供前端或对话逻辑判断这是“过程观察”，不是任务完成通知。

### 安全保护与自主动作

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `neko_auto_low_hp_threshold` | `0.3` | 当前血量比例低于该值时，后台自动游玩会自主暂停。 |
| `neko_auto_safe_hp_threshold` | `0.5` | 血量恢复到该比例后，可自动恢复。 |
| `neko_auto_dangerous_attack_threshold` | `20` | 敌人来袭伤害达到该值且会破防时，自动减速。 |
| `neko_auto_resume_after_low_hp` | `true` | 低血量暂停后是否允许血量恢复时自动恢复。 |
| `neko_desperate_enabled` | `true` | 是否启用残血求生策略。 |
| `neko_desperate_hp_threshold` | `0.2` | 触发残血求生策略的血量比例。 |
| `neko_maximize_enabled` | `true` | 是否启用收益最大化出牌选择。 |
| `neko_synergy_enabled` | `true` | 是否启用连携/协同评分。 |

当前自主动作包括：

- `pause`：低血量时暂停，等待用户或猫娘指令。
- `slow_down`：Boss 战或危险攻击时把动作间隔临时调慢。
- `resume`：满足安全血量条件后恢复。

## 插件入口

下面这些入口已经暴露给宿主，可直接在 N.E.K.O 中调用。

### `sts2_health_check`

检查本地尖塔 Agent 服务是否可用。

### `sts2_refresh_state`

强制刷新一次当前尖塔状态。

### `sts2_get_status`

获取连接状态、自动游玩状态、当前模式、角色策略、半自动任务、最近错误、最近动作等信息。

### `sts2_get_snapshot`

获取最近缓存的游戏快照和当前可执行动作。

### `sts2_step_once`

按当前策略执行一步。

### `sts2_play_one_card_by_neko`

让猫娘选择并打出一张牌。

参数：

- `objective`：可选，用户授权目标。例如：`帮我选一张牌打出去`。

行为：

1. 读取当前玩家、手牌、敌人和合法动作。
2. 只保留 `play_card` 动作。
3. 让当前模式/策略选择一张牌。
4. 先向前端推送“准备打出哪张牌和原因”。
5. 重新校验动作仍然合法。
6. 执行出牌并推送完成观察。

如果当前没有可打出的牌，会返回 `idle`，并推送失败原因。

### `sts2_start_autoplay`

启动后台半自动游玩循环。

参数：

- `objective`：可选，用户授权目标。例如：`帮我打这一关`。
- `stop_condition`：停止条件，默认 `current_floor`。

`stop_condition` 支持：

- `current_floor`：当前楼层完成或进入下一层后结束。
- `current_combat` / `combat`：如果从战斗开始，则当前战斗结束后结束。
- `manual` / `none`：不自动完成，需要手动停止。

启动后插件会创建半自动任务上下文，并向前端推送任务开始事件。任务完成时会推送 `semi_auto_task_completed`。

### `sts2_pause_autoplay`

暂停自动游玩。

### `sts2_resume_autoplay`

恢复已暂停的自动游玩。如果后台任务已经不存在，会重新启动自动游玩。

### `sts2_stop_autoplay`

停止自动游玩并清除半自动任务上下文。

### `sts2_get_history`

获取最近动作和状态历史。

参数：

- `limit`：返回条数，默认 `20`，范围会限制在 `1 ~ 100`。

### `sts2_send_neko_guidance`

向后台自动游玩发送猫娘软指导。指导会进入队列，并在下一轮 LLM 决策时注入上下文。

参数：

- `content`：必填，自然语言指导内容。例如：`先防一下，别急着输出`。
- `step`：可选，对应步数。
- `type`：可选，默认 `soft_guidance`。

### `sts2_set_mode`

设置自动游玩模式。

参数：

- `mode`：支持 `full-program` / `全程序`、`half-program` / `半程序`、`full-model` / `全模型`。

### `sts2_set_character_strategy`

设置角色策略名称。

参数：

- `character_strategy`：会经过名称标准化后匹配 `strategies/<name>.md`。例如 `defect` 会匹配 `strategies/defect.md`。

### `sts2_set_speed`

设置速度参数，并写回本地 `plugin.toml`。

参数：

- `action_interval_seconds`
- `post_action_delay_seconds`
- `poll_interval_active_seconds`

## 典型使用方式

### 检查连接

1. 启动《Slay the Spire 2》。
2. 确认 `http://127.0.0.1:8080/health` 可访问。
3. 在 N.E.K.O 中调用 `sts2_health_check`。

### 手动执行一步

调用：

```text
sts2_step_once
```

插件会根据当前 `mode` 和 `character_strategy` 选择一个合法动作并执行。

### 让猫娘打一张牌

用户可以对猫娘说类似：

```text
帮我选一张牌打出去
```

宿主应调用：

```text
sts2_play_one_card_by_neko
```

插件会只从当前可打出的卡牌中选择，不会选择结束回合、地图、奖励或其他动作。

### 让猫娘帮忙打一关

用户可以说：

```text
帮我打这一关
```

宿主应调用：

```text
sts2_start_autoplay
```

推荐参数：

```json
{
  "objective": "帮我打这一关",
  "stop_condition": "current_floor"
}
```

任务运行期间，观察事件只是过程汇报，不代表完成。只有收到半自动任务完成事件时，才应告诉用户这一关完成。

### 中途指导

自动游玩中，用户或猫娘可以发送指导：

```text
先防一下吧，别吃太多伤害
```

宿主应调用：

```text
sts2_send_neko_guidance
```

推荐参数：

```json
{
  "content": "先防一下吧，别吃太多伤害",
  "type": "soft_guidance"
}
```

指导会在下一轮 LLM 决策时被参考。`full-program` 模式不依赖模型，软指导影响有限。

## 前端推送事件

插件会通过宿主的消息通道推送以下几类事件：

| 事件类型 | 说明 |
| --- | --- |
| `action` | 普通自动游玩动作观察，受概率控制。 |
| `error` | 自动游玩错误，强制推送。 |
| `neko_report` | 完整猫娘观察报告，包含当前局面、手牌、敌人、战术摘要和模型理由。 |
| `neko_card_task_planned` | 猫娘单卡任务计划打出某张牌。 |
| `neko_card_task_completed` | 猫娘单卡任务已执行。 |
| `neko_card_task_failed` | 猫娘单卡任务无法执行。 |
| `semi_auto_task_started` | 半自动任务开始。 |
| `semi_auto_task_completed` | 半自动任务完成。 |
| `neko_autonomous_action` | 系统自主暂停、减速或恢复。 |

注意：`neko_report` 是过程观察，不是任务完成通知。前端或对话逻辑不应把单步动作、出牌、结束回合或状态刷新说成“任务完成”“打完 Boss”“战斗结束”或“通关”。

## 常见排查

### 调用插件入口时报连接失败

先检查：

- 游戏是否已经启动。
- `STS2 AI Agent` Mod 是否已正确放进游戏 `mods/`。
- `http://127.0.0.1:8080/health` 是否可访问。
- `plugin.toml` 里的 `base_url` 是否正确。

### `http://127.0.0.1:8080/health` 打不开

优先检查：

1. 游戏是否真的已经启动。
2. `STS2AIAgent.dll`、`STS2AIAgent.pck`、`mod_id.json` 是否都已复制到游戏目录 `mods/`。
3. 文件名是否被系统改名、重复或放错目录。
4. 你操作的是 Steam 游戏目录，而不是上游仓库目录。
5. 是否有防火墙或安全软件阻止本地端口。

### 自动游玩能运行，但前端没有收到消息

检查：

- `llm_frontend_output_enabled` 是否为 `true`。
- `llm_frontend_output_probability` 是否过低。
- `neko_reporting_enabled` 是否为 `true`。
- 联调时可先把 `llm_frontend_output_probability` 设为 `1`。
- 宿主前端是否已接收插件推送消息。

### 猫娘中途指导没有明显效果

检查：

- 当前模式是否为 `half-program` 或 `full-model`。
- `sts2_send_neko_guidance` 是否返回 `ok`。
- 指导内容是否足够具体，例如“优先防御”“先打最低血敌人”“保留药水”。
- 当前合法动作是否真的能满足指导。

### 半自动任务迟迟不完成

检查 `stop_condition`：

- 如果是 `manual` / `none`，任务不会自动完成，需要调用 `sts2_stop_autoplay`。
- 如果是 `current_combat`，只有从战斗界面启动并离开战斗后才会完成。
- 如果是 `current_floor`，通常在当前楼层完成或进入下一层后完成。

可以调用 `sts2_get_status` 查看 `autoplay.task`。

### 事件房、弹窗或过渡态卡住

当前版本已经对事件、弹窗、过渡态做过处理，优先动作包含：

- `confirm_modal`
- `dismiss_modal`
- `choose_event_option`
- `proceed`

如果仍卡住，先用 `sts2_get_snapshot` 查看当前 `screen` 和 `available_actions`。

### 自动游玩突然暂停或变慢

可能触发了安全保护：

- 血量比例低于 `neko_auto_low_hp_threshold` 时会暂停。
- Boss 战或危险攻击时会减速。
- 若 `neko_auto_resume_after_low_hp` 为 `true`，血量恢复到 `neko_auto_safe_hp_threshold` 后可能自动恢复。

可调用 `sts2_get_status` 查看状态，或调用 `sts2_resume_autoplay` / `sts2_stop_autoplay` 处理。
