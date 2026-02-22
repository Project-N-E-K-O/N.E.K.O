# API 参考

N.E.K.O. 通过 FastAPI 提供了全面的 API 接口。所有端点均由主服务器提供服务（默认 `http://localhost:48911`）。

## 基础 URL

```
http://localhost:48911
```

## 认证

本地访问无需认证。LLM 提供商的 API 密钥通过[配置](/zh-CN/config/)系统单独管理。

## REST 端点

| 路由 | 前缀 | 描述 |
|------|------|------|
| [配置](/zh-CN/api/rest/config) | `/api/config` | API 密钥、偏好设置、提供商配置 |
| [角色](/zh-CN/api/rest/characters) | `/api/characters` | 角色增删改查、语音设置、麦克风 |
| [Live2D](/zh-CN/api/rest/live2d) | `/api/live2d` | Live2D 模型管理、表情映射 |
| [VRM](/zh-CN/api/rest/vrm) | `/api/model/vrm` | VRM 模型管理、动画 |
| [记忆](/zh-CN/api/rest/memory) | `/api/memory` | 记忆文件、回顾配置 |
| [智能体](/zh-CN/api/rest/agent) | `/api/agent` | 智能体标志位、任务、健康检查 |
| [创意工坊](/zh-CN/api/rest/workshop) | `/api/steam/workshop` | Steam 创意工坊物品、发布 |
| [系统](/zh-CN/api/rest/system) | `/api` | 情感分析、截图、实用工具 |

## WebSocket

| 端点 | 描述 |
|------|------|
| [协议](/zh-CN/api/websocket/protocol) | 连接生命周期与会话管理 |
| [消息类型](/zh-CN/api/websocket/message-types) | 所有客户端→服务器和服务器→客户端的消息格式 |
| [音频流](/zh-CN/api/websocket/audio-streaming) | 二进制音频格式、中断、重采样 |

## 内部 API

以下为服务间 API，不面向外部使用：

| 服务器 | 描述 |
|--------|------|
| [记忆服务器](/zh-CN/api/memory-server) | 记忆存储与检索（端口 48912） |
| [智能体服务器](/zh-CN/api/agent-server) | 智能体任务执行（端口 48915） |

## 响应格式

所有 REST 端点返回 JSON。成功响应通常直接包含数据。错误响应遵循 FastAPI 的默认格式：

```json
{
  "detail": "Error message describing what went wrong"
}
```

## 内容类型

- `application/json` — 大多数端点
- `multipart/form-data` — 文件上传（模型、语音样本）
- `audio/*` — 语音预览响应
