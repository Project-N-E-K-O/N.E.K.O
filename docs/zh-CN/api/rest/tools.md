# 运行时工具 API

运行时工具 API 允许本地插件或伴随进程把 HTTP 回调注册为模型可调用的工具。主服务器在 `/api/tools` 提供这些接口；注册会写入当前各角色的会话管理器，并在修改接口返回前同步到活动模型会话。

> [!IMPORTANT]
> 这是本地集成协议，不是远程管理 API。所有接口只接受回环地址客户端。回调 URL 也只能使用 HTTP 或 HTTPS，并指向 `localhost`、`127.0.0.0/8`、`::1` 或 IPv4 映射的回环地址。其他调用方收到 HTTP `403`；非回环回调地址会因校验失败收到 HTTP `422`。

注册项只存在于运行时，服务器重启后不会保留。全局注册只应用于请求处理时已经存在的角色会话管理器。

## 路由清单

| 方法 | 路径 | 用途 |
|---|---|---|
| `POST` | `/api/tools/register` | 注册或替换一个远程模型工具 |
| `POST` | `/api/tools/unregister` | 按名称移除工具 |
| `POST` | `/api/tools/clear` | 移除具有指定 `source` 标签的工具 |
| `GET` | `/api/tools` | 列出当前注册项 |

## 注册

```http
POST /api/tools/register
Content-Type: application/json

{
  "name": "get_weather",
  "description": "Get weather for a location.",
  "parameters": {
    "type": "object",
    "properties": {
      "location": {"type": "string"}
    },
    "required": ["location"]
  },
  "callback_url": "http://127.0.0.1:9333/tools/get_weather",
  "role": null,
  "source": "plugin:weather",
  "timeout_seconds": 30
}
```

| 字段 | 要求 | 默认值 |
|---|---|---|
| `name` | 字符串，1–64 个字符 | 必填 |
| `description` | 展示给模型的工具描述 | `""` |
| `parameters` | 工具参数的 JSON Schema 对象 | 空对象 schema |
| `callback_url` | 回环地址上的 HTTP/HTTPS URL | 必填 |
| `role` | 已存在的角色名；`null` 表示所有当前角色 | `null` |
| `source` | 供 `clear` 使用的生命周期标签；插件应使用 `plugin:<id>` | `"external"` |
| `timeout_seconds` | 大于 0 且不超过 300 | `30` |

注册会替换每个目标 registry 中的同名工具。非空 `role` 不存在时返回 HTTP `404`；字段校验失败返回 HTTP `422`。

响应会区分全部成功、部分成功和全部失败：

```json
{
  "ok": true,
  "registered": "get_weather",
  "affected_roles": ["Lanlan"],
  "failed_roles": []
}
```

没有任何角色接受注册时，`ok` 为 false。部分成功时保留 `ok: true`，并在 `failed_roles` 中描述同步失败。

## 回调协议

模型调用远程工具时，主服务器发送：

```json
{
  "name": "get_weather",
  "arguments": {"location": "Shanghai"},
  "call_id": "call_123",
  "raw_arguments": "{\"location\":\"Shanghai\"}"
}
```

成功回调可通过 `output` 返回任意 JSON 值：

```json
{"output": {"temperature_c": 28}, "is_error": false}
```

要返回工具级错误而不让 HTTP 请求失败：

```json
{"error": "location not found", "is_error": true}
```

调度器会把 HTTP `4xx`/`5xx` 视为工具错误；非 JSON 响应会作为文本输出。请求超时或连接失败也会转换为模型可见的工具错误。

对于以 `plugin:` 开头的 source，同一回调 origin 连续发生三次连接级失败后，系统会从所有角色 registry 中自动移除该 origin 的工具，不会误删同一插件其他 origin 的工具。读取超时、HTTP 错误响应和工具级 `is_error` 不计入驱逐。

回调载荷可能包含由用户对话派生的模型参数。回调必须保持本地，并遵守插件其他用户数据通路的隐私处理要求。

## 注销与清理

从所有角色移除一个工具名：

```http
POST /api/tools/unregister
Content-Type: application/json

{"name": "get_weather", "role": null}
```

移除一个 source 注册的全部工具：

```http
POST /api/tools/clear
Content-Type: application/json

{"source": "plugin:weather", "role": null}
```

两个接口都可用 `role` 指定一个已存在角色。响应包含 `affected_roles` 和 `failed_roles`；`unregister` 的 `removed` 是布尔值，`clear` 的 `removed` 是移除数量。

## 列表

`GET /api/tools` 列出全部当前角色；`GET /api/tools?role=Lanlan` 只选择一个角色，角色不存在时返回 HTTP `404`。

```json
{
  "ok": true,
  "tools_by_role": {
    "Lanlan": [
      {
        "name": "get_weather",
        "description": "Get weather for a location.",
        "source": "plugin:weather",
        "callback_url": "http://127.0.0.1:9333/tools/get_weather",
        "is_remote": true
      }
    ]
  }
}
```
