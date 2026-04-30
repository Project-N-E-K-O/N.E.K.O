# Minecraft 游戏代理插件

把本地 Minecraft Agent (WebSocket) 桥接到 N.E.K.O 的实时 LLM 会话上：模型可以
通过 `minecraft_task` 工具下发指令，agent 执行过程中的截图实时推到模型的视觉
上下文里，让模型一边玩一边解说。

## 协议

agent server 与本插件通过单一 WebSocket 连接通信，JSON 帧格式：

| 方向 | `type` | 字段 |
|------|--------|------|
| → agent | `task` | `{"type": "task", "task": "<目标>", "task_id": "<uuid>"}` |
| ← agent | `log` | `{"type": "log", "text": "..."}` |
| ← agent | `screenshot` | `{"type": "screenshot", "image": "<base64>", "encoding": "png"\|"jpeg"}` |
| ← agent | `task_finished` | `{"type": "task_finished", "status": "ok", "text": "...", "task_id": "<uuid>"}` |
| ← agent | `agent_status` | informational, ignored by callbacks |

`task_id` 是**可选的**显式关联字段。插件每次发 `task` 帧都会带一个新生成
的 UUID；agent 如果在对应的 `task_finished` 帧里把这个 UUID 原样回传，插件
就用 ID 严格匹配 pending 任务，跳过 FIFO 的 stale-frame 启发式。**不回传
也兼容**——插件会回退到按完成顺序匹配（见下方"已知限制"）。建议有内部并发
的 agent 实现一定带上 `task_id` 以避免 out-of-order 完成被错误归属。

agent 端启动方式由你自己决定（一般是 `node minecraft-agent/index.js --port 48909`
或类似命令），插件只负责连进去。

## 启用步骤

1. 启动 Minecraft agent server，记下监听端口（默认 `48909`）
2. 在 N.E.K.O 插件管理界面启用 `game_agent_minecraft` 插件
3. 如需修改 agent 地址或超时，编辑 `plugin.toml` 的 `[game_agent]` 节，然后调
   `game_agent_reload_config` entry 即可热更新（无需重启 WebSocket 连接）

启用后无需任何 main_logic 代码改动 —— `minecraft_task` 工具会通过 SDK 的
`@llm_tool` 装饰器自动注册到 `main_server` 的统一工具表。

## 配置项

`plugin.toml` 的 `[game_agent]` 节：

| 字段 | 默认 | 含义 |
|------|------|------|
| `ws_url` | `ws://localhost:48909` | agent server WebSocket 地址 |
| `reconnect_interval_seconds` | `5.0` | WS 断开后等待多久重连 |
| `task_timeout_seconds` | `25.0` | `minecraft_task` 单次调用最长等待 agent 完成的秒数；超时返回 `{status: "timeout"}` 给 LLM |
| `system_prompt_interval_seconds` | `5.0` | 自动 nudge 循环的最小间隔；不影响 `main_server` 自身的对话节奏控制 |
| `skip_system_prompt_if_busy` | `true` | 任务进行中跳过 nudge，避免堆叠 |
| `stream_screenshots_to_llm` | `true` | 收到 agent 截图就立即 push 到模型视觉上下文（`ai_behavior="read"`） |
| `screenshot_cache_size` | `3` | 内存里保留的最近 N 张截图，nudge 时一并发出 |

## LLM 工具：`minecraft_task`

```json
{
  "name": "minecraft_task",
  "description": "Send a task to the Minecraft game system ...",
  "parameters": {
    "type": "object",
    "properties": {
      "task":      {"type": "string"},
      "overwrite": {"type": "boolean"}
    },
    "required": ["task"]
  }
}
```

返回值（LLM 看到的）：

| 形态 | 时机 |
|------|------|
| `{"status": "ok", "query": "..."}` | agent 正常完成 |
| `{"status": "timeout", "query": "...", "reason": "..."}` | `task_timeout_seconds` 内 agent 未完成 |
| `{"status": "interrupted", "query": "...", "reason": "..."}` | 被新任务（`overwrite=True`）抢占 |
| `{"result": "busy", "currently_executing": "...", "hint": "..."}` | 已有任务在跑且 `overwrite=False`，新任务被拒 |
| `{"output": {"error": "..."}, "is_error": true, "error": "AGENT_DISCONNECTED"}` | agent server 当前不可达 |

## 自动 nudge 循环

后台任务每 `system_prompt_interval_seconds` 秒检查一次：

- 如果 agent 有日志或截图缓存，且当前没有 pending 任务（或 `skip_system_prompt_if_busy=false`），
  就 push_message 一条 `GAME_SYSTEM | ...` 文本 + 缓存里的截图给 LLM
- LLM 收到后通常会决定下一个 `minecraft_task`，或者只解说不下指令
- 时机由 `main_server` 的 proactive_message handler 二次把关（用户/模型说话期间它不会真的打断），
  插件这层只负责"别 5 秒里发 10 次"

## 与原版 (`feat/game-agent-integration`) 的差别

原方案把所有逻辑硬编码进 `main_logic/core.py` 和
`main_logic/game_agent_client.py`，与 PR #1035 的统一 `ToolRegistry` 强冲突。
这版完全是一个独立插件：

| 项 | 原版 | 现版 |
|----|------|------|
| 工具注册 | 直接拼 Gemini 的 `function_declarations` | `@llm_tool` 装饰器，走统一 `ToolRegistry`，全 provider 通用（OpenAI / Gemini / GLM / Qwen Omni / StepFun ...） |
| 截图入栈 | `session.send_media_input(bytes, mime)` 直调 | `push_message v2` `parts=[{"type":"image","data":...,"mime":...}]`，main_server 自动 `stream_image` |
| 异步 task 完成回填 | 自维护 `_game_pending_tool_id` + `session.send_tool_response` | `asyncio.Event` 在 `@llm_tool` handler 里 await，task_finished 回调 set 事件 |
| 自动 nudge | `session.create_response(prompt)` 直调 | `push_message v2` `ai_behavior="respond"` |
| 启用方式 | 改 `core.py` 里两个属性 | 在插件管理界面打开开关 |

迁移指南：原 `core.game_agent_enabled = True` + `core.game_agent_url = ...` 的两行代码不再需要；
直接启用本插件并按需调整 `plugin.toml`。

## 已知限制

**FIFO 回退模式下的 out-of-order completion**：如果 agent 没有在
`task_finished` 帧里回传 `task_id`，插件就退化到按完成顺序匹配 pending
任务的 stale-frame drop 计数器。假设 agent 按完成顺序发帧——这对顺序处理
的 agent（mineflayer 系等）成立，但若 agent 内部有并发使得"被 overwrite
的 A 在替换者 B 之后才完成"（帧顺序 B-then-A），filter 会吞掉 B 的真完成、
把 A 的延迟帧当成 B 的——当前 `minecraft_task` 调用会卡到 `task_timeout_seconds`。

**修复方法**：在 agent 端把 task 帧里收到的 `task_id` 原样回传到对应的
`task_finished` 帧上即可。插件会自动切到显式 ID 匹配模式，不再依赖完成
顺序。

## 文件分工

| 文件 | 作用 |
|------|------|
| `__init__.py` | 插件 facade —— 生命周期 hook、`@llm_tool` 装饰、状态查询 entries |
| `service.py` | `GameAgentService` —— 跨回调状态、`asyncio.Event` 桥接、自动 nudge 循环 |
| `client.py` | `GameAgentClient` —— WebSocket 连接 + 自动重连 + 帧分发 |
| `plugin.toml` | 插件清单 + `[game_agent]` 配置 |
