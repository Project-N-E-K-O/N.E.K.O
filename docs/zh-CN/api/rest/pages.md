# 页面路由

为 Web UI 提供 HTML 页面服务。所有页面使用 Jinja2 模板渲染。

## 路由

| 路径 | 模板 | 描述 |
|------|------|------|
| `/` | `index.html` | 主聊天界面 |
| `/model_manager` | `model_manager.html` | Live2D/VRM 模型管理 |
| `/live2d_parameter_editor` | `live2d_parameter_editor.html` | Live2D 参数微调 |
| `/live2d_emotion_manager` | `live2d_emotion_manager.html` | Live2D 表情-动画映射 |
| `/vrm_emotion_manager` | `vrm_emotion_manager.html` | VRM 表情-动画映射 |
| `/chara_manager` | `chara_manager.html` | 角色设置编辑器 |
| `/voice_clone` | `voice_clone.html` | 语音克隆界面 |
| `/api_key` | `api_key_settings.html` | API 密钥配置 |
| `/steam_workshop_manager` | `steam_workshop_manager.html` | Steam 创意工坊管理 |
| `/memory_browser` | `memory_browser.html` | 记忆浏览与编辑 |
| `/{lanlan_name}` | `index.html` | 角色专属聊天（通配路由） |

::: info
`/{lanlan_name}` 通配路由提供相同的主界面，但会预选特定角色。
:::
