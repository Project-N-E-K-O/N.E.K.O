# WebSocket 协议

主服务只有一个应用 WebSocket 路由：

```text
ws://127.0.0.1:48911/ws/{lanlan_name}
```

`{lanlan_name}` 需要 URL 编码。通过可信反向代理终止 TLS 时使用 `wss://`。这是随项目发布的 UI 协议，不是独立版本化的公共 wire 标准。

## 连接接受

1. 服务端接受 WebSocket，并用进程内 session manager 校验路径中的角色。
2. 角色不存在时，服务端可能先发送带有效回退角色的 `catgirl_switched`，随后关闭；不保证自定义 close code。
3. 角色有效时，连接会安装到该角色的 session manager。服务端会生成内部 UUID，但不会发送专门的连接 ack。
4. 客户端可立即发送 `greeting_check` 等控制 action；对话媒体必须先 `start_session`。

Router 中只有角色最新连接 UUID 是权威连接。旧连接再次发送数据时会收到 `CHARACTER_SWITCHING_TERMINAL` 并关闭。因此第一方多窗口依赖项目的跨页代理/同步层，而不是多个相互竞争的主 socket。

## 帧模型

- 客户端命令是顶层 `action` 的 UTF-8 JSON 文本帧。
- 服务端事件通常是顶层 `type` 的 UTF-8 JSON 文本帧。
- TTS 音频例外：先发 `audio_chunk` JSON 头，再发一个二进制帧。
- 服务端目前用 `receive_text()` 接收客户端数据；不要发送客户端二进制音频帧。
- 任意客户端 JSON 都可携带 `language`；router 会先更新角色 UI 语言，再分发 `action`。

非法 JSON 是连接级错误：处理器会尽力发送 `SERVER_ERROR` 状态，然后退出接收循环并清理当前会话。

## 会话生命周期

应用 WebSocket 与 provider session 生命周期相互独立：

```text
socket open
    │
    ├─ start_session ─> session_preparing ─> session_started
    │                                      └> session_failed
    │
    ├─ stream_data / avatar_interaction / control events
    │
    ├─ pause_session 或 end_session ─> provider session 结束
    │                                     socket 保持连接
    │
    └─ socket close ─> 清理当前 provider session 和路由状态
```

### 启动

```json
{
  "action": "start_session",
  "input_type": "audio",
  "new_session": false,
  "language": "zh-CN"
}
```

有效 `input_type` 为 `audio`、`screen`、`camera`、`text`、`avatar_drop_image`、`user_image`。`text` 和两种一次性图片使用文本/离线模式；`audio`、`screen`、`camera` 选择实时/音频模式。`new_session` 是 provider session 提示，不是连接 UUID。

启动是异步任务。发送麦克风样本前要等待匹配的 `session_started.input_mode`。`session_preparing` 只表示进度；`session_failed` 表示请求模式未启动。失败前通常会先发含机器可读原因的 `status`。

游戏路由激活时，文本/图片可能由游戏控制器确认或接管；音频会话可作为游戏的实时 STT provider。这属于第一方游戏集成。

### 暂停与结束

```json
{ "action": "pause_session" }
```

`pause_session` 会把 manager 标为空闲并结束当前 provider session，并不会保留一个可恢复的上游暂停流。

```json
{ "action": "end_session", "reason": "user_stop" }
```

`end_session` 安排 provider 清理，并保留应用 WebSocket 供之后重新启动。可选 `goodbye_active: true` 或 `reason: "goodbye"` 还会启用静默告别 gate。两者都不承诺存在预热替换会话。

上游断连、配置变化或超时时，服务端也可主动发送 `session_ended_by_server`。

## 保活

协议支持应用层心跳：

```json
{ "action": "ping" }
```

```json
{ "type": "pong" }
```

间隔应根据客户端/代理超时选择；后端 handler 本身没有强制固定间隔。

## 状态与错误

由于历史前端兼容，状态使用嵌套 JSON 信封：

```json
{
  "type": "status",
  "message": "{\"code\":\"INVALID_INPUT_TYPE\",\"details\":{\"input_type\":\"file\"}}"
}
```

需要对 `message` 再做一次 JSON 解析。已知示例包括 `INVALID_INPUT_TYPE`、`UNKNOWN_ACTION`、`SERVER_ERROR`、provider/认证/配额错误以及 `CHARACTER_SWITCHING_TERMINAL`。集合会扩展，客户端必须为未知 code 提供通用回退。

未知 action 不关闭 socket，只产生 `UNKNOWN_ACTION`。非法 JSON、被替代连接、角色删除/重命名或网络断开则会清理连接。

## 安全边界

该路由没有独立认证握手。应用假设本地可信 UI，并接收用户可控的文本、图片、telemetry 和截图元数据。端口 48911 应保持回环监听，或置于带认证和 Origin 限制的代理后。不要信任 telemetry 维度、本地截图响应或角色名。

另见[消息类型](./message-types)和[音频流](./audio-streaming)。
