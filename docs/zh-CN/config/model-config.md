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

自定义 API 中某个角色的模型 ID 留空时，按该角色实际指向的 Provider 取同档默认模型：「跟随辅助 API」取辅助 Provider 的默认，「跟随核心 API」取核心 Provider 的默认，具名 Provider 取它自己的默认。免费版与固定模型的 Provider（如 Kimi Code）始终使用自身模型，忽略已保存的模型 ID。「自定义」端点没有 Provider 默认值，留空时沿用辅助 API 当前的模型名，建议显式填写。

Web UI 中每个模型 ID 输入框旁都有「拉取模型」按钮，可列出上游端点提供的模型（`POST /api/config/list_models`），并按输入内容筛选；留空时输入框以灰色占位文字显示当前实际使用的模型。填了也不会生效的位置按钮不可用：免费版与固定模型的 Provider、镜像对话/摘要的小游戏槽，以及实时、TTS、小游戏槽的跟随模式。

模型 ID、端点、thinking 参数、token 限制和语音目录都易变。应查看运行 revision 对应的 Web UI 和 `config/api_providers.json`，不要把文档示例当兼容性承诺。

新增角色或字段时必须同步更新 loader、config manager、router/UI、测试及全部 8 个 locale。
