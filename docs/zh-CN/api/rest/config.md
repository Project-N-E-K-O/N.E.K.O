# 配置 API

**前缀：** `/api/config`

管理 API 提供商配置、用户偏好设置和页面设置。

## 端点

### `GET /api/config/page_config`

获取页面配置（模型路径、模型类型）。

**查询参数：**

| 名称 | 类型 | 必填 | 描述 |
|------|------|------|------|
| `lanlan_name` | string | 否 | 角色名称 |

**响应：** 页面配置，包括 Live2D/VRM 模型路径和类型。

---

### `GET /api/config/preferences`

获取用户偏好设置（模型选择、显示设置）。

---

### `POST /api/config/preferences`

更新用户偏好设置。

**请求体：** 包含偏好键值对的 JSON 对象。

---

### `POST /api/config/preferences/set-preferred`

设置角色的首选模型。

**请求体：**

```json
{
  "model_name": "model_name_here",
  "model_path": "/path/to/model"
}
```

---

### `GET /api/config/steam_language`

获取 Steam 客户端的语言设置。用于自动语言检测。

---

### `GET /api/config/user_language`

获取用户配置的语言偏好。

---

### `GET /api/config/core_api`

获取当前核心 API 配置（提供商、模型、端点）。

::: warning
此端点不会暴露原始 API 密钥。密钥以掩码形式返回。
:::

---

### `POST /api/config/core_api`

更新核心 API 配置。

**请求体：**

```json
{
  "coreApiKey": "sk-xxxxx",
  "coreApi": "qwen",
  "assistApi": "qwen",
  "assistApiKeyQwen": "sk-xxxxx"
}
```

有关可用的提供商值，请参阅 [API 提供商](/zh-CN/config/api-providers)。

---

### `GET /api/config/api_providers`

获取所有可用 API 提供商及其配置的列表。

---

### `POST /api/config/gptsovits/list_voices`

列出本地 GPT-SoVITS 服务中可用的声音。

**请求体：** 语音服务连接设置。
