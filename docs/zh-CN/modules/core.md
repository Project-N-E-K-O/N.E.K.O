# LLMSessionManager

**Package：** `main_logic/core/`

`LLMSessionManager` 是逐角色协调器，连接浏览器 WebSocket、一个活动模型客户端、记忆、工具、主动结果与语音输出。`app/main_server/character_runtime.py` 为每个角色创建并持有对应 manager。

类在 `manager.py` 中组装；`__init__` 是全部实例状态的唯一 owner，方法来自 `context_append`、`focus`、`tts_runtime`、`turn`、`tool_calling`、`lifecycle`、`proactive`、`greeting`、`streaming` 和 `notify` mixin。`main_logic.core` 只重导出原单体模块中继续受支持的兼容 surface。

## 会话选择与启动

```python
await manager.start_session(websocket, new=False, input_mode="audio")
```

`start_session()` 被串行化，并防止同模式重复启动或跨模式竞态。用户主动发起的请求可以替换另一个模式的 in-flight start；后台 greeting/proactive 启动不会反向覆盖用户请求。

启动阶段如下：

1. 绑定前端 WebSocket，重新加载运行时、角色、音色和 provider 设置，并通知前端进入准备状态；
2. 退出旧的活动/待切换会话，重置输入和 TTS 状态；
3. 从记忆服务器预取 `GET /new_dialog/{lanlan_name}`；
4. 所选路由需要外部 TTS 时启动其 worker；
5. 文本输入创建 `OmniOfflineClient`，音频输入创建 `OmniRealtimeClient`，绑定回调/工具，并使用渲染后的记忆上下文连接；
6. 把已连接客户端提升为 `self.session`，按需启动接收任务，刷新待处理上下文/输入并确认 ready。

记忆上下文是必需的启动依赖。`/new_dialog` 连接失败、超时或返回非 2xx 都会使会话启动失败；manager 不会使用空上下文静默继续。

连续启动失败会打开本地熔断，避免每个音频 chunk 都再次尝试连接；前端显式重试会重置熔断。

## 输入路由

```python
await manager.stream_data(message)
```

`stream_data()` 是 WebSocket router 的媒体入口。会话启动期间，它会缓存可缓存输入；消息类型需要另一模式时，也可以创建或重建客户端。

- 文本输入交给 `OmniOfflineClient.stream_text()`；
- 音频 bytes 通过有界音频队列交给 `OmniRealtimeClient.stream_audio()`；
- 截图、相机帧和其他受支持视觉输入经过处理后进入当前客户端的图片路径；
- 没有合适活动会话时，live vision frame 会被丢弃，不会隐式启动会话。

文本与音频会话是明确的二选一。Realtime 传输错误不会在没有新模式/启动决策时自动转成 Offline 会话。

## 输出与回合生命周期

模型客户端通过 manager 回调输出，而不是直接写前端：

| 回调 | 职责 |
|---|---|
| `handle_new_message()` | 在 realtime 用户回合边界打断过期语音、重置重采样/TTS 状态并轮换 `speech_id` |
| `handle_text_data()` | 把助手文本流式发送到 UI；启用外部 TTS 时也写入 TTS 请求队列 |
| `handle_audio_data()` | 转发模型原生 PCM；manager 把 24 kHz PCM 重采样为前端使用的 48 kHz PCM |
| `handle_input_transcript()` | 记录语音输入、更新活动/语言状态、发布用户上下文，并协调记忆/agent mirror |
| `handle_output_transcript()` | mirror 助手转写，并维护下游系统使用的回合文本 |
| `handle_response_complete()` | 结束 TTS，向 WebSocket 和 sync queue 发送 turn-end，再执行归档/预热及回调投递决策 |

每条回复都带 `speech_id`。打断和主动消息竞态 guard 使用它丢弃来自已被替换回合的迟到 TTS 或模型 chunk。

`end_session()` 关闭活动和 pending 客户端，取消接收/准备任务，重置流状态，并可完成已准备好的热切换。`cleanup(expected_websocket=...)` 增加 ownership guard，避免旧 WebSocket 的断开拆掉新连接。`shutdown()` 是 character runtime 替换非活动 manager 时，用于长期任务的同步最终清理。

## 热切换与上下文追加

热切换准备由回合/token 阈值、renew signal 或额外上下文触发，并不会在每次新会话启动时无条件执行。`_background_prepare_pending_session()` 创建已连接的 pending client；`_perform_final_swap_sequence()` 在锁保护下提升它，并保留过渡期间到达的输入/上下文。

`append_context()` 是持久上下文注入的公开路径。它会去重请求、投递到 active 和/或 pending client，并把暂时无法投递的内容排入下一次启动 prompt。模型可见的主动消息则使用 `submit_proactive_callback()` / `enqueue_agent_callback()`，由 `trigger_agent_callbacks()` 在回复与语音播放 gate 下投递，二者语义不同。

## 工具与记忆

Manager 拥有 `ToolRegistry`。`register_tool_and_sync()` 及对应 unregister/clear 方法会更新活动客户端的工具定义。`_register_builtin_tools()` 为 offline 和 realtime 会话注册内置 `recall_memory`。

记忆有两条面向模型的路径：

1. **自动新会话上下文** —— 启动和热切换获取 `GET /new_dialog/{lanlan_name}`。记忆服务器把 persona、可用反思、近期压缩历史与时间相关上下文渲染到初始 prompt。
2. **按需召回** —— `recall_memory` 把自然语言 `query`、`time` 表达式或两者一起发送到 `POST /query_memory/{lanlan_name}`。它搜索事实和反思；persona 因已自动渲染而不进入候选池。

对话持久化由 `main_logic/cross_server.py` 另行协调：增量内容走 `/cache`，回合/会话边界使用 `/process`、`/renew` 或 `/settle`。持久化事实与反思不会全部自动注入。

## 执行边界

```text
主服务器 asyncio loop
  ├─ 前端 WebSocket 收发
  ├─ LLMSessionManager lifecycle、客户端回调、工具/主动任务
  ├─ 活动客户端接收 task 与可选 pending-session 准备
  ├─ 逐角色 cross-server sync connector asyncio task
  └─ TTS response-handler asyncio task

专用 TTS worker 线程
  └─ 通过请求/响应队列运行同步 provider worker
```

异步锁保护会话替换、前端写入、缓存和媒体队列。TTS worker 是 manager 内的主要线程边界；阻塞队列等待以及部分文件系统/SDK 操作通过 `asyncio.to_thread()` 卸载。

## 主要调用方与依赖

- `main_routers/websocket_router.py` 调用 `start_session()`、`stream_data()`、`end_session()` 和带 guard 的 `cleanup()`。
- `app/main_server/character_runtime.py` 拥有逐角色 manager、WebSocket lock、sync queue 和 cross-server connector task。
- `ConfigManager` 提供角色、API、音色和回复长度设置。
- `OmniOfflineClient` / `OmniRealtimeClient` 提供模型传输。
- 记忆服务器是初始上下文的必需依赖；Agent bridge 与 proactive delivery 路径提供可选后台结果。
- `main_logic/tts_client/` 提供外部 TTS worker 函数；线程与队列由 manager 拥有。
