# 主动搭话 API

**前缀：** `/api/proactive`

用于读取和修改主动搭话**模式**及其底层的主动搭话**设置**字段。所有写入都经过 `utils.preferences.save_global_conversation_settings`，因此字段白名单、类型校验和原子写逻辑都集中在一处维护。

这是四个第一方偏好设置路由（`/mode` 和 `/settings` 各有 `GET`/`POST`），不是公共调度 API。它们没有单独认证层，路径末尾没有 `/`。遗留应用错误会以 HTTP `200` 和 `success: false` 返回，调用方必须检查响应体。

::: info
本组接口与 `POST /api/proactive_chat`（参见[系统 API](./system.md)）不同，后者用于**生成**一条主动搭话消息。这里的接口只读取和更新主动搭话的配置。
:::

## 模式

模式是服务器端定义的一组主动搭话字段预设。可用预设为 `off`、`normal`、`focus`、`frequent`。当持久化的字段不匹配任何预设时，模式会被报告为 `custom`。

### `GET /api/proactive/mode`

读取当前模式以及当前的主动搭话字段。

**响应：**

```json
{
  "success": true,
  "mode": "normal",
  "available_modes": ["off", "normal", "focus", "frequent"],
  "settings": { "proactiveChatEnabled": true, "proactiveChatInterval": 15 }
}
```

`mode` 由持久化的字段反推得出，若与任何预设都不匹配则为 `custom`。`settings` 只包含与主动搭话相关的字段。

### `POST /api/proactive/mode`

应用一个预设模式。

**请求体：**

```json
{ "mode": "focus" }
```

`mode` 必须是 `off`、`normal`、`focus`、`frequent` 之一，未知值会被拒绝。

**响应：**

```json
{
  "success": true,
  "mode": "focus",
  "applied": { "proactiveChatEnabled": true }
}
```

`applied` 是保存后从磁盘回读的结果（按值且按类型的严格比对）。若有预设字段未能写入，还会返回一个 `rejected` 字段名数组。

::: info
切换模式绝不会改变 `proactiveVisionEnabled`（隐私模式开关）。预设刻意不包含该字段——是否允许角色查看屏幕由用户本人决定。
:::

## 设置

设置类接口直接读取和部分更新主动搭话字段，不经过预设。

### `GET /api/proactive/settings`

读取当前的主动搭话字段（对话设置中的白名单子集）。

**响应：**

```json
{
  "success": true,
  "settings": { "proactiveChatEnabled": true, "proactiveChatInterval": 15 }
}
```

### `POST /api/proactive/settings`

部分更新主动搭话字段。请求体只接受可写的主动搭话字段（例如 `proactiveChatEnabled`、`proactiveChatInterval`、`proactiveVisionInterval`）。无法识别的字段会被静默忽略，`save_global_conversation_settings` 还会再做一轮类型/范围校验。

**请求体：**

```json
{ "proactiveChatEnabled": true, "proactiveChatInterval": 30 }
```

**响应：**

```json
{
  "success": true,
  "applied": { "proactiveChatEnabled": true, "proactiveChatInterval": 30 }
}
```

`applied` 是保存后从磁盘回读的结果。值或类型未通过校验的字段会列在 `rejected` 数组中。若请求体包含 `proactiveVisionEnabled`，该字段会被拒绝并通过 `rejected_user_owned` 上报。

::: info
`proactiveVisionEnabled` 是用户专有字段（隐私模式开关的反面，涉及屏幕内容采集）。**主动搭话**这组端点不会修改它——会通过 `rejected_user_owned` 上报；它由主 conversation-settings 保存路径（UI 中的隐私模式开关）设置，是用户本人的选择。在此处发送该字段会让它出现在 `rejected_user_owned` 中而不被应用。
:::
