# 快速开始

本页将引导你在完成[开发环境搭建](./dev-setup)后首次运行 N.E.K.O.。

## 1. 启动服务器

```bash
# 在不同的终端中运行：
uv run python memory_server.py
uv run python main_server.py
```

## 2. 配置 API 提供商

访问 `http://localhost:48911/api_key`，至少配置**核心 API** 提供商。

如果想快速测试且无需 API 密钥，请选择 **Free** 作为核心 API 提供商。

## 3. 与默认角色互动

在浏览器中打开 `http://localhost:48911`。默认角色（"小天"）将加载一个 Live2D 模型。

**文字模式：** 在聊天输入框中输入消息并按回车键。

**语音模式：** 点击麦克风按钮开启语音会话。自然地说话 —— 系统使用服务端 VAD（语音活动检测）来判断你何时结束发言。

## 4. 自定义角色

访问 `http://localhost:48911/chara_manager` 可以：

- 修改角色的名字、性别、年龄和性格特征
- 设置自定义 Live2D 或 VRM 模型
- 克隆自定义声音（上传约 15 秒的干净音频样本）
- 编辑系统提示词以完全控制角色行为

## 5. 探索 Web UI 页面

| URL | 用途 |
|-----|------|
| `/` | 主聊天界面 |
| `/chara_manager` | 角色设置 |
| `/api_key` | API 密钥配置 |
| `/model_manager` | Live2D/VRM 模型管理 |
| `/live2d_emotion_manager` | 情绪到动画映射 |
| `/vrm_emotion_manager` | VRM 情绪映射 |
| `/voice_clone` | 语音克隆 |
| `/memory_browser` | 浏览和编辑记忆 |
| `/steam_workshop_manager` | Steam 创意工坊管理 |

## 下一步

- [项目结构](./project-structure) —— 了解代码库布局
- [架构概览](/zh-CN/architecture/) —— 三个服务器如何协作
- [API 参考](/zh-CN/api/) —— 所有 REST 和 WebSocket 端点
