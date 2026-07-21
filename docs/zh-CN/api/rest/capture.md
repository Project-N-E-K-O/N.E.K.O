# 截图桥接 API

截图路由是 N.E.K.O. Electron 渲染器截图桥的 HTTP 入口。当原生截图后端无法读取其他应用窗口时（例如 Linux 纯 Wayland 环境），GalGame 插件会使用它。

> [!WARNING]
> 这是第一方本地桥接协议，不是通用截图 API。两个接口都只接受回环地址客户端，并且必须有已连接的 N.E.K.O. Electron 渲染器通过主 WebSocket 处理请求。非回环调用方会收到 HTTP `403`。

## 路由清单

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` | `/api/capture/health` | 检查是否有具备截图能力的渲染器连接 |
| `POST` | `/api/capture/screenshot` | 请求该渲染器截取指定桌面源 |

## 健康检查

渲染器已注册时：

```http
GET /api/capture/health
```

```json
{"success": true, "available": true}
```

没有渲染器时返回 HTTP `503`：

```json
{"success": false, "available": false, "error": "no_renderer"}
```

## 截取桌面源

```http
POST /api/capture/screenshot
Content-Type: application/json

{
  "target_id": "window:123456:0",
  "pid": 4242,
  "title": "Example Game"
}
```

| 字段 | 类型 | 要求 |
|---|---|---|
| `target_id` | integer 或 string | 必填；会标准化为字符串；长度至少为 1 且不超过桥接限制 |
| `pid` | integer | 必填且大于 0 |
| `title` | string | 可选，最长 512 个字符，默认为 `""` |

未知字段会被拒绝。`target_id` 可以是插件后端取得的原生窗口句柄，也可以是 Electron `desktopCapturer` 的 source ID。桌面源由渲染器解析；HTTP 路由本身不执行截图。

成功时返回图片 data URL 和可选的渲染器元数据：

```json
{
  "success": true,
  "image": "data:image/jpeg;base64,...",
  "width": 1920,
  "height": 1080,
  "source_id": "window:123456:0"
}
```

只有渲染器提供有效值时，才会包含 `width`、`height` 和 `source_id`。

## 错误响应

| 状态码 | `error` | 含义 |
|---:|---|---|
| `400` | `invalid_json` | 请求体不是有效 JSON |
| `403` | `loopback_only` | 调用方不是回环客户端 |
| `422` | `validation_error` | 请求字段或 `target_id` 长度无效 |
| `502` | `source_not_found` | 渲染器找不到指定桌面源 |
| `502` | `bridge_error` | 截图桥返回其他上游错误 |
| `502` | `empty_image` | 渲染器未返回可用图片 |
| `503` | `no_renderer` | 没有具备截图能力的渲染器连接 |
| `504` | `renderer_timeout` | 渲染器未在桥接超时前响应 |
| `500` | `internal_error` | 未预期的服务端错误 |

桥接会使用[消息类型](/zh-CN/api/websocket/message-types)中说明的 WebSocket `capture_bridge_status`、`capture_bridge_response` 和服务端截图请求消息。返回图片属于用户敏感的屏幕内容，请勿记录到日志。
