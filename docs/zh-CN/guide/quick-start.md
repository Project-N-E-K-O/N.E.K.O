# 快速开始

先完成[开发环境搭建](./dev-setup)。

```bash
uv run python launcher.py
```

launcher 启动协作服务并报告最终端口。使用它报告的主 URL；`http://127.0.0.1:48911` 只是首选默认值。

打开主 URL 的 `/api_key`，选择 Core Provider，按需选择 Assist Provider，填写对应凭据并运行连通性检查。Provider/模型列表随 revision 变化，不要照抄旧截图。

全新数据根从当前 locale 的角色默认值与 Yui Origin 初始化。角色 ID/显示名来自有效数据，旧文档固定写成“小天”已经失效。文字聊天使用共享 React 组件；语音取决于 core/TTS 路径和麦克风权限。

| 路由 | 用途 |
| --- | --- |
| `/character_card_manager` | 角色与形象选择 |
| `/model_manager` | 形象模型 |
| `/live2d_emotion_manager` | Live2D 情感映射 |
| `/vrm_emotion_manager` | VRM 情感映射 |
| `/voice_clone` | 依赖 Provider 的音色克隆 |
| `/memory_browser` | 记忆输出与处理状态 |
| `/api_key` | Provider 与凭据 |

不是每个 Provider/部署模式都支持全部功能。Agent 和托管插件还需要 agent/tool 服务。
