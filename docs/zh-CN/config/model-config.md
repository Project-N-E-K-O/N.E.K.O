# 模型配置

N.E.K.O. 针对不同任务使用不同的 AI 模型，每个模型都可以单独配置。

## 模型角色

| 角色 | 默认值 | 环境变量 | 用途 |
|------|--------|----------|------|
| 对话 | `qwen-max` | - | 角色聊天（离线模式） |
| 摘要 | `qwen-plus` | `NEKO_SUMMARY_MODEL` | 对话摘要 |
| 纠错 | `qwen-max` | `NEKO_CORRECTION_MODEL` | 文本纠错 |
| 情感 | `qwen-flash` | `NEKO_EMOTION_MODEL` | 表情的情感分析 |
| 视觉 | `qwen3-vl-plus-2025-09-23` | `NEKO_VISION_MODEL` | 图像/截图理解 |
| Agent | `qwen3.5-plus` | `NEKO_AGENT_MODEL` | Agent 任务执行 |
| 路由 | `qwen-plus` | `NEKO_ROUTER_MODEL` | 记忆路由决策 |
| 语义 | `text-embedding-v4` | `NEKO_SEMANTIC_MODEL` | 记忆文本向量嵌入 |
| 重排 | `qwen-plus` | `NEKO_RERANKER_MODEL` | 搜索结果重排 |
| 设定提议 | `qwen-max` | `NEKO_SETTING_PROPOSER_MODEL` | 提议设定更新 |
| 设定验证 | `qwen-max` | `NEKO_SETTING_VERIFIER_MODEL` | 验证设定更新 |

## 自定义模型端点

每个模型角色都可以使用自定义 API 端点。可以通过 `core_config.json` 或 Web UI 进行配置：

```json
{
  "conversationModel": "custom-model-name",
  "conversationModelUrl": "https://custom-api.example.com/v1",
  "conversationModelApiKey": "sk-xxxxx"
}
```

当设置了自定义 URL/密钥时，它会覆盖该特定角色的全局 Assist API 提供商。

## Computer Use 模型

Computer Use 需要两个视觉模型：

| 角色 | 默认值 | 用途 |
|------|--------|------|
| 规划模型 | `qwen3-vl-plus-2025-09-23` | 分析截图并规划操作 |
| 定位模型 | `qwen3-vl-plus-2025-09-23` | 定位 UI 元素以进行点击 |

通过 `core_config.json` 配置：

```json
{
  "computerUseModel": "qwen3-vl-plus-2025-09-23",
  "computerUseModelUrl": "https://dashscope.aliyuncs.com/compatible-mode/v1",
  "computerUseModelApiKey": "sk-xxxxx",
  "computerUseGroundModel": "qwen3-vl-plus-2025-09-23",
  "computerUseGroundUrl": "https://dashscope.aliyuncs.com/compatible-mode/v1",
  "computerUseGroundApiKey": "sk-xxxxx"
}
```

## 思考模式配置

部分模型支持"思考"或"扩展推理"模式。N.E.K.O. 默认禁用这些模式以获得更快的响应速度。禁用格式因提供商而异：

| 提供商 | 禁用格式 |
|--------|----------|
| Qwen、Step、DeepSeek | `{"enable_thinking": false}` |
| GLM | `{"thinking": {"type": "disabled"}}` |
| Gemini 2.x | `{"thinking_config": {"thinking_budget": 0}}` |
| Gemini 3.x | `{"thinking_config": {"thinking_level": "low"}}` |

此功能在 `config/__init__.py` 中根据模型名称自动处理。

## 图像速率限制

| 设置 | 默认值 | 说明 |
|------|--------|------|
| `NATIVE_IMAGE_MIN_INTERVAL` | 1.5 秒 | 屏幕截图的最小间隔 |
| `IMAGE_IDLE_RATE_MULTIPLIER` | 5 倍 | 无语音活动时的倍数 |
