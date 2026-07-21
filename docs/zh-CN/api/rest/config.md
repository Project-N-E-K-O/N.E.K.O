# 配置 API

**前缀：** `/api/config`

该 router 提供第一方前端所需的配置接口：provider 设置、连通性测试、模型显示偏好、语言提示、对话设置、GPT-SoVITS 发现以及运行时代理切换。

::: warning 内部配置面
这些路由面向随项目发布的 UI，并不是带版本承诺的公共配置 SDK。JSON 字段会随 provider 和前端功能扩展。除非自行加入鉴权，否则服务应仅监听回环地址。
:::

## Provider 配置与探测

### `POST /api/config/test_connectivity`

执行设置页使用的 provider 探测。Pydantic 请求体支持两种模式：

```json
{
  "provider_key": "openai",
  "provider_scope": "core",
  "api_key": "..."
}
```

或自定义端点：

```json
{
  "url": "https://example.test/v1",
  "api_key": "...",
  "model": "model-name",
  "provider_type": "openai_compatible",
  "sub_type": "",
  "voice_id": "",
  "is_free": false
}
```

响应含 `success`，按情况附带 `error`、`error_code`、`resolved_url`。Pydantic 类型错误返回 `422`；网络、认证或模型错误通常以 HTTP `200` 加 `success: false` 返回，供设置页显示分类错误。

### 核心 provider

| 方法和路径 | 用途 |
|---|---|
| `GET /api/config/core_api` | 读取 core/assist/audio provider 的有效配置；存储的密钥会先脱敏。 |
| `POST /api/config/core_api` | 合并校验后的 provider 设置，并通知/重启受影响会话；UI 回传的掩码密钥不会覆盖真实密钥。 |
| `GET /api/config/api_providers` | 返回运行时 provider 配置中的目录和前端元数据。 |

POST 请求体使用第一方设置页返回的字段名，例如 `coreApi`、`coreApiKey`、`assistApi` 及各 provider 专属字段；它是可扩展 JSON 对象，不是固定 Pydantic schema。

### GPT-SoVITS

| 方法和路径 | 用途 |
|---|---|
| `POST /api/config/gptsovits/list_voices` | 校验 HTTP base URL 并代理声音列表请求。 |
| `POST /api/config/gptsovits/test_connectivity` | 运行 WebSocket 初始化、ready 和合成流程，但不播放音频。 |

两者都使用设置页的连接参数并返回 `success` 信封。上游校验或连接错误会按阶段使用 `400`、`502` 或 `504`。

## 偏好与对话设置

| 方法和路径 | 用途 |
|---|---|
| `GET /api/config/preferences` | 读取各模型显示偏好。 |
| `POST /api/config/preferences` | 保存必填 `model_path`、`position`、`scale`，以及可选 `parameters`、`display`、`rotation`、`viewport`、`camera_position`。 |
| `POST /api/config/preferences/set-preferred` | 将必填 `model_path` 移到偏好顺序最前。 |
| `GET /api/config/conversation-settings` | 读取全局对话设置和首启默认值所用 telemetry 分支。 |
| `POST /api/config/conversation-settings` | 保存全局对话设置；更新 `noiseReductionEnabled` 会应用到兼容的活动会话。 |

偏好校验失败通常表示为 `{ "success": false, "error": "..." }`；存储维护模式可改为 HTTP 服务不可用响应。

## 页面与语言数据

| 方法和路径 | 用途 |
|---|---|
| `GET /api/config/page_config` | 解析指定/当前角色及 Live2D、VRM、MMD、PNGTuber 模型路径；查询参数 `lanlan_name` 可选，响应为 `no-store`。 |
| `GET /api/config/character_reserved_fields` | 返回前后端共用的角色档案保留字段配置。 |
| `GET /api/config/steam_language` | 返回 Steam locale 和可用时的 GeoIP 语言提示。 |
| `GET /api/config/user_language` | 返回前端/字幕使用的用户语言。 |

## 代理模式

### `POST /api/config/set_proxy_mode`

热切换当前进程的代理环境变量：

```json
{ "direct": true }
```

`direct: true` 会快照并移除代理变量，同时设置 `NO_PROXY=*`；`false` 恢复快照。响应中的 `proxies_after` 已移除凭据。该操作只影响当前进程。

## 经实现核对的路由清单

```text
POST /api/config/test_connectivity
GET  /api/config/core_api
POST /api/config/core_api
GET  /api/config/api_providers
POST /api/config/gptsovits/list_voices
POST /api/config/gptsovits/test_connectivity
GET  /api/config/steam_language
GET  /api/config/user_language
GET  /api/config/character_reserved_fields
GET  /api/config/page_config
GET  /api/config/preferences
POST /api/config/preferences
POST /api/config/preferences/set-preferred
GET  /api/config/conversation-settings
POST /api/config/conversation-settings
POST /api/config/set_proxy_mode
```
