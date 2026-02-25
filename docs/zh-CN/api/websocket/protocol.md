# WebSocket 协议

## 连接

连接到 WebSocket 端点：

```
ws://localhost:48911/ws/{lanlan_name}
```

其中 `{lanlan_name}` 是角色标识符（例如 `小天`）。

### 连接流程

1. 客户端打开 WebSocket 连接
2. 服务器分配会话 ID（UUID）并验证角色是否存在
3. 客户端发送 `start_session` 以初始化 LLM 会话
4. 客户端发送 `stream_data` 消息，携带音频或文本输入
5. 服务器流式返回响应（文本、音频、状态、情感）
6. 客户端发送 `end_session` 以关闭会话
7. 服务器执行热替换，切换到预热的会话

### 保活机制

发送周期性的 `ping` 消息以防止连接超时：

```json
{ "action": "ping" }
```

服务器响应：

```json
{ "type": "pong" }
```

## 会话管理

### 启动会话

```json
{
  "action": "start_session",
  "input_type": "audio",
  "new_session": true
}
```

| 字段 | 类型 | 描述 |
|------|------|------|
| `action` | `"start_session"` | 必填 |
| `input_type` | `"audio"` \| `"text"` | 输入模式 |
| `new_session` | boolean | 是否创建全新会话 |

### 结束会话

```json
{ "action": "end_session" }
```

触发热替换机制：当前 LLM 会话被关闭并替换为预热的会话。

### 暂停会话

```json
{ "action": "pause_session" }
```

保持 WebSocket 连接但暂停 LLM 处理。

## 错误处理

如果角色名称无效，服务器将以适当的关闭码关闭 WebSocket。如果服务端切换了角色，服务器会发送 `catgirl_switched` 消息，指示客户端重新连接。
