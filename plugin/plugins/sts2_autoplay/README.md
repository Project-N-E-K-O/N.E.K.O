# 尖塔自动游玩（sts2_autoplay）

这个插件用于把 `STS2 AI Agent` 暴露出来的本地尖塔状态接入到 N.E.K.O，并提供手动一步执行、自动游玩、状态查看，以及将自动游玩信息主动推送到前端对话框的能力。

## 这个插件依赖什么

它依赖MOD `STS2 AI Agent` 提供的本地 HTTP 服务：

- 游戏内 Mod：`STS2AIAgent`
- 默认本地接口地址：`http://127.0.0.1:8080`

也就是说，这个插件工作的前提是：

1. 你已经把 `STS2 AI Agent` 的 Mod 装进了《Slay the Spire 2》
2. 游戏启动后，`http://127.0.0.1:8080/health` 可以访问

## 如何使用 STS2 AI Agent

### STS2 AI Agent 是什么
``` 访问/下载/更新链接

git clone https://gitclone.com/github.com/CharTyr/STS2-Agent.git

```
`STS2 AI Agent` 是一个给《Slay the Spire 2》使用的游戏 Mod + MCP Server 组合：

- `STS2AIAgent`：把游戏状态和操作暴露为本地 HTTP API
- `mcp_server`：把这套本地 API 包装成 MCP Server，方便接入支持 MCP 的 AI 客户端

### 当前上游已提供的主要能力

当前版本的上游能力包括：

- 读取游戏状态
- 获取当前可执行动作
- 执行战斗、奖励、商店、地图、事件等常见操作
- 通过 SSE 事件减少高频轮询
- 以 `stdio` 或 HTTP 方式暴露 MCP
- 提供卡牌、遗物、敌人、药水、事件等打包元数据查询
- 支持 planner / combat 分层 handoff 流程

### 1. 安装 STS2 AI Agent Mod

将本文件夹的 `mods`里的文件复制到尖塔游戏目录的 `mods/` 下：

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

### 2. 启动游戏并确认本地接口正常

先正常启动一次游戏，让 Mod 随游戏加载。

然后访问：

```text
http://127.0.0.1:8080/health
```

只要能拿到返回结果，就说明 `STS2 AI Agent` 的本地 HTTP 服务已经启动成功。

### 3. MCP Server 是否必须启动

**不是必须。**

`sts2_autoplay` 这个插件当前直接连的是 `STS2 AI Agent` 暴露出的本地 HTTP API，默认地址是：

```text
http://127.0.0.1:8080
```

因此：

- 如果你只是想在 N.E.K.O 里使用这个自动游玩插件，通常只需要游戏内 Mod 正常启动即可
- 如果你还想把尖塔接入支持 MCP 的 AI 客户端，再另外启动 `mcp_server`

### 4. 如需单独使用 MCP Server

如果你还要把它接到 MCP 客户端，可按上游说明启动：

#### 运行环境

1. 安装 `Python 3.11+`
2. 安装 `uv`

Windows 安装 `uv`：

```powershell
powershell -ExecutionPolicy Bypass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

macOS：

```bash
brew install uv
```

#### 启动 stdio MCP

大多数桌面 AI 客户端更适合用 `stdio` 方式。

在 `mcp_server/` 工作目录下可使用：

```text
uv run sts2-mcp-server
```

如果你使用上游仓库脚本，也可以直接运行：

Windows：

```powershell
powershell -ExecutionPolicy Bypass -File ".\scripts\start-mcp-stdio.ps1"
```

macOS / Linux：

```bash
./scripts/start-mcp-stdio.sh
```

#### 启动网络版 MCP

如果你的客户端更适合连 HTTP，可使用上游脚本启动网络版：

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

## 本插件配置说明

配置文件：`plugin.toml`

当前 `[sts2]` 配置项：

- `base_url`：尖塔本地 Agent 地址，默认 `http://127.0.0.1:8080`
- `connect_timeout_seconds`：连接超时
- `request_timeout_seconds`：请求超时
- `poll_interval_idle_seconds`：空闲状态轮询间隔
- `poll_interval_active_seconds`：自动游玩运行时轮询间隔
- `action_interval_seconds`：每个动作之间的额外间隔
- `post_action_delay_seconds`：动作执行后等待局面稳定的间隔
- `autoplay_on_start`：插件启动后是否自动开始游玩
- `mode`：当前自动游玩模式，固定支持 `full-program` / `全程序`、`half-program` / `半程序`、`full-model` / `全模型`
  - `full-program`：纯程序启发式
  - `half-program`：单次模型决策 + 程序合法性校验/回退
  - `full-model`：两次模型调用（reasoning + final action）+ 中间程序检查 + 最终合法性验证
- `character_strategy`：角色策略名称。会按 `strategies/<name>.md` 在策略目录里查找对应文档，支持用户自定义扩展；例如 `defect` 会匹配 `strategies/defect.md`
- `max_consecutive_errors`：最大连续错误次数
- `push_notifications`：历史保留字段
- `event_stream_enabled`：预留字段，目前未实际启用
- `llm_frontend_output_enabled`：是否把自动游玩信息主动推送到前端对话框
- `llm_frontend_output_probability`：推送概率，范围会收敛到 `0.0 ~ 1.0`

## 插件入口（可直接在 N.E.K.O 中调用）

下面这些入口已经暴露给宿主，可直接调用：

### 1. `sts2_health_check`
检查本地尖塔 Agent 服务是否可用。

### 2. `sts2_refresh_state`
强制刷新一次当前尖塔状态。

### 3. `sts2_get_status`
获取连接状态、自动游玩状态、最近错误、最近动作等信息。

### 4. `sts2_get_snapshot`
获取最近缓存的游戏快照和当前可执行动作。

### 5. `sts2_step_once`
按当前策略执行一步。

### 6. `sts2_start_autoplay`
启动后台自动游玩循环。

### 7. `sts2_pause_autoplay`
暂停自动游玩。

### 8. `sts2_resume_autoplay`
恢复自动游玩。

### 9. `sts2_stop_autoplay`
停止自动游玩。

### 10. `sts2_get_history`
获取最近动作和状态历史。

参数：

- `limit`：返回条数，默认 `20`

### 11. `sts2_set_mode`
设置自动游玩模式。

参数：

- `mode`：支持 `full-program` / `全程序`、`half-program` / `半程序`、`full-model` / `全模型`

### 12. `sts2_set_character_strategy`
设置角色策略名称。

参数：

- `character_strategy`：会经过名称标准化后匹配 `strategies/<name>.md`；例如 `defect` 会匹配 `strategies/defect.md`

### 13. `sts2_set_speed`
设置速度参数，并写回本地 `plugin.toml`。

参数：

- `action_interval_seconds`
- `post_action_delay_seconds`
- `poll_interval_active_seconds`

## 自动推送到前端的内容

当开启 `llm_frontend_output_enabled` 后，本插件会通过 `proactive_notification` 向前端主动推送信息。

当前主要会在这些场景推送：

- 自动游玩成功执行动作后（按概率）
- 自动游玩出现错误时（启用后强制推送）

## 常见排查

### 1. 调用插件入口时报连接失败

先检查：

- 游戏是否已经启动
- `STS2 AI Agent` Mod 是否已正确放进游戏 `mods/`
- `http://127.0.0.1:8080/health` 是否可访问
- `plugin.toml` 里的 `base_url` 是否正确

### 2. 自动游玩能运行，但前端没有收到消息

检查：

- `llm_frontend_output_enabled` 是否为 `true`
- `llm_frontend_output_probability` 是否过低
- 联调时建议先设为 `1`
- 宿主前端是否已接收 `proactive_notification`

### 3. 事件房卡住

当前版本已经对事件、弹窗、过渡态做过处理，优先动作包含：

- `confirm_modal`
- `dismiss_modal`
- `choose_event_option`
- `proceed`

如果仍卡住，先用 `sts2_get_snapshot` 查看当前 `screen` 和 `available_actions`。

### 4. `http://127.0.0.1:8080/health` 打不开

优先检查：

1. 游戏是否真的已经启动
2. `STS2AIAgent.dll`、`STS2AIAgent.pck`、`mod_id.json` 是否都已复制到游戏目录 `mods/`
3. 文件名是否被系统改名、重复或放错目录
4. 你操作的是 Steam 游戏目录，而不是仓库目录
