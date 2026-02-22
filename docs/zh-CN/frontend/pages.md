# 页面与模板

## 模板渲染

页面使用 Jinja2 在服务端渲染。模板位于 `templates/` 目录中。

## 页面列表

| 路径 | 模板 | 描述 |
|------|------|------|
| `/` | `index.html` | 带有 Live2D/VRM 渲染的主聊天界面 |
| `/chara_manager` | `chara_manager.html` | 角色性格和设置编辑器 |
| `/api_key` | `api_key_settings.html` | API 密钥配置面板 |
| `/model_manager` | `model_manager.html` | 模型浏览和管理 |
| `/live2d_parameter_editor` | `live2d_parameter_editor.html` | Live2D 模型参数微调 |
| `/live2d_emotion_manager` | `live2d_emotion_manager.html` | Live2D 情感映射 |
| `/vrm_emotion_manager` | `vrm_emotion_manager.html` | VRM 情感映射 |
| `/voice_clone` | `voice_clone.html` | 语音克隆界面 |
| `/memory_browser` | `memory_browser.html` | 记忆浏览和编辑 |
| `/steam_workshop_manager` | `steam_workshop_manager.html` | Steam 创意工坊管理 |

## 深色模式

深色模式由 `static/theme-manager.js` 管理：

- 通过 UI 按钮切换
- 保存在 `localStorage` 中
- CSS 变量定义在 `static/css/dark-mode.css` 中
- 遵循系统偏好（`prefers-color-scheme`）

## 静态文件服务

| 挂载点 | 目录 | 内容 |
|--------|------|------|
| `/static` | `static/` | JS、CSS、图片、语言文件 |
| `/user_live2d` | 用户文档 | 用户导入的 Live2D 模型 |
| `/user_vrm` | 用户文档 | 用户导入的 VRM 模型 |
| `/workshop` | Steam 创意工坊 | 工坊订阅的模型 |
