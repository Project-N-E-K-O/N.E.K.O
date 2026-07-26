# GalGame API

**前缀：** `/api`

GalGame 模式接口。根据最近的对话，为玩家生成三条分支回复候选，类似视觉小说的选项菜单供玩家挑选。当 GalGame 模式开关打开时，React 聊天窗口会在猫娘完成一轮发言后调用该接口。

这是一个第一方路由，不是独立公共文本生成 API。它会把近期对话文本发送给已配置的摘要模型提供商，因此调用方必须遵循聊天 UI 相同的用户同意、隐私和提供商额度边界。路径末尾没有 `/`。

## 回复选项

### `POST /api/galgame/options`

为玩家生成三条回复候选，每种风格各一条：

- **A** —— 认真而踏实：紧扣话题、如实作答，不做角色扮演。
- **B** —— 温柔而充满爱意：语气柔软、流露关心。
- **C** —— 天马行空：俏皮的假设、奇幻设定、古灵精怪的幽默。

**请求体：**

```json
{
  "messages": [
    { "role": "user", "text": "..." },
    { "role": "assistant", "text": "..." }
  ],
  "language": "en",
  "lanlan_name": "character_name",
  "master_name": "player_name"
}
```

- `messages` —— 最近的对话。每条包含 `role`（`"assistant"` 或 `"user"`）和 `text` 字符串。只保留最近的若干轮，且**最后一轮必须来自 assistant**，否则请求会被拒绝。
- `language` —— 可选。短语言代码（如 `"en"`、`"zh"`、`"ja"`）。省略时会从最新一条消息自动检测语言。
- `lanlan_name` —— 可选。角色名。缺省时回退到已配置的角色，再回退到默认占位符。
- `master_name` —— 可选。玩家名。缺省时回退到已配置的值，再回退到默认占位符。

**响应：**

```json
{
  "success": true,
  "options": [
    { "label": "A", "text": "..." },
    { "label": "B", "text": "..." },
    { "label": "C", "text": "..." }
  ]
}
```

::: info
该接口设计为永不硬失败。在生成超时、模型/解析错误，或未配置 summary 模型时，它仍会返回 `success: true`，附带预置的兜底选项以及 `"fallback": true` 标记。如果模型只返回了 A/B/C 中的部分风格，缺失的项会用兜底内容补齐，响应会带上 `"partial": true` 以及 `missing_labels` 列表。
:::

::: info
回复生成运行在 **summary** 模型档位上。若未配置 summary 模型，则立即返回兜底选项。当会话被接管时（如语音输入被改路由进游戏逻辑），生成会被跳过，并返回带 `"reason": "session_takeover"` 的兜底选项。
:::

在以下情况下，请求会以 HTTP `400` 被拒绝：请求体不是合法 JSON（`"error": "invalid_json"`）、不是对象（`"error": "invalid_payload"`），或没有可供回复的 assistant 轮次（`"error": "no_assistant_turn"`）。
