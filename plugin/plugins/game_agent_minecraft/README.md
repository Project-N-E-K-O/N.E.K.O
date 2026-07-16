# Minecraft 游戏插件

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

`task_finished` 的 `status` 是一个字符串，插件**容忍任意取值**（当作不透明
串透传，不做枚举校验，未知值不会崩）。agent 目前会发这些：`ok`（真正完成）、
`interrupted`（被打断）、`superseded`（被更新的任务顶替而中止——中性的"没跑完"，
与 `interrupted` 同类，**不算失败**）、`failed`（真正执行失败）、`timeout`。
插件据此给对话 LLM 分档措辞：`ok`→成功、`interrupted`/`superseded`→中性"换/停"、
其余→失败。**约定**：失败绝不能报成 `ok`（否则猫娘会把翻车当成功叙述）。

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
3. 如需修改配置，编辑 `plugin.toml` 的 `[game_agent]` 节后调
   `game_agent_reload_config` entry 应用：
   - **非 transport 配置**（`task_timeout_seconds`、`system_prompt_interval_seconds`、
     `screenshot_cache_size`、各种开关等）—— 当场生效，**不重连** WebSocket。
   - **transport 配置**（`ws_url`、`reconnect_interval_seconds`）—— 会触发
     WebSocket 客户端 stop+start 切换到新地址；entry 返回值的
     `transport_restarted` 标志会反映这一点。

启用后无需任何 main_logic 代码改动 —— `minecraft_task` 工具会通过 SDK 的
`@llm_tool` 装饰器自动注册到 `main_server` 的统一工具表。

## 配置项

`plugin.toml` 的 `[game_agent]` 节：

| 字段 | 默认 | 含义 |
|------|------|------|
| `ws_url` | `ws://localhost:48909` | agent server WebSocket 地址 |
| `reconnect_interval_seconds` | `5.0` | WS 断开后等待多久重连 |
| `task_timeout_seconds` | `120.0` | `minecraft_task` 单次调用最长等待 agent 完成的秒数；超时返回 `{status: "timeout"}` 给 LLM。默认 120s 给 mine/craft 类多步动作留余量（实测 60–90s 较常见）；先前 25s 默认在挖矿场景下普遍误超时 |
| `system_prompt_interval_seconds` | `15.0` | 自动 nudge 循环的最小间隔（节流，降低对话密度）；不影响 `main_server` 自身的对话节奏控制 |
| `skip_system_prompt_if_busy` | `true` | 任务进行中跳过 nudge，避免堆叠 |
| `stream_screenshots_to_llm` | `true` | 收到 agent 截图就 push 到模型视觉上下文（`ai_behavior="read"`），按下面的最小间隔节流 |
| `screenshot_stream_min_interval_seconds` | `6.0` | 内联截图流 push 的最小间隔（节流，防止按 agent ~1Hz 速率灌爆模型视觉上下文）；窗口内的帧折叠为最新一张 |
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
| `{"status": "failed", "query": "...", "text": "..."}` | agent 报告任务真正执行失败 |
| `{"status": "superseded", "query": "...", "text": "..."}` | agent 因更新的任务顶替而中止本任务（中性非完成，与 `interrupted` 同类，不算失败） |
| `{"status": "timeout", "query": "...", "reason": "..."}` | `task_timeout_seconds` 内 agent 未完成 |
| `{"status": "interrupted", "query": "...", "reason": "..."}` | 被新任务（`overwrite=True`）抢占 |
| `{"result": "busy", "currently_executing": "...", "hint": "..."}` | 已有任务在跑且 `overwrite=False`，新任务被拒 |
| `{"output": {"error": "..."}, "is_error": true, "error": "AGENT_DISCONNECTED"}` | agent server 当前不可达 |

> `status` 直接透传 agent 的原值：`ok`/`interrupted` 之外，agent 现也可能发
> `failed`/`superseded`。插件对 `interrupted`、`superseded` 用中性"没跑完"措辞，
> 对 `failed`（及 `timeout`、断连）用失败措辞，只有 `ok` 判成功。

## 自动 nudge 循环

后台任务每 `system_prompt_interval_seconds` 秒检查一次：

- 如果 agent 有日志或截图缓存，且当前没有 pending 任务（或 `skip_system_prompt_if_busy=false`），
  就以 `ai_behavior="read"` push 一条 `GAME_SYSTEM | ...` 文本 + 最新截图，
  只补充游戏 awareness，不强制开启模型回合
- 只有当前真实派发的任务收到可关联的 `task_finished` 后，才会武装 `keep_going`；
  继续空闲 45 秒后以 `ai_behavior="respond"` 唤醒，之后仍空闲则每 45 秒节流触发
- 启动、发送失败、客户端超时、断连、陌生或迟到的完成帧都不会武装
  `keep_going`
- `minecraft_task` 只能依据 master 刚说完的最新一条消息中的新明确指令；不得从旧对话、
  旧 tool call、日志、截图或完成 cue 回捞任务，也不得重复派发已经下达过的命令
- 时机由 `main_server` 的 proactive_message handler 二次把关（用户/模型说话期间它不会真的打断），
  插件这层同时负责去重、节流和 busy awareness

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
