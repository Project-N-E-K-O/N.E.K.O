# 模型配置

N.E.K.O. 按**角色**解析模型，而不是只读一个全局模型名。选中的 Provider profile 提供默认值，`core_config.json` 中受支持的值可覆盖单个角色。

| 角色 | 字段 |
| --- | --- |
| Core | `CORE_MODEL` |
| 会话 | `CONVERSATION_MODEL` |
| 摘要 | `SUMMARY_MODEL` |
| 纠错 | `CORRECTION_MODEL` |
| 情感 | `EMOTION_MODEL` |
| 视觉 | `VISION_MODEL` |
| Agent | `AGENT_MODEL` |
| 实时 | `REALTIME_MODEL` |
| TTS | `TTS_MODEL` |

推荐在 Web UI 选择 Core/Assist Provider，填写相应凭据并运行连通性检查，再按需设置受支持的角色 model/URL/key。已保存的端点只有在仍属于当前 profile 候选列表时才会复用。

模型 ID、端点、thinking 参数、token 限制和语音目录都易变。应查看运行 revision 对应的 Web UI 和 `config/api_providers.json`，不要把文档示例当兼容性承诺。

新增角色或字段时必须同步更新 loader、config manager、router/UI、测试及全部 8 个 locale。
