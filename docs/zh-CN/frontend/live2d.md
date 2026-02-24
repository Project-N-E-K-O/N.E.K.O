# Live2D 集成

## 概述

N.E.K.O. 使用 Cubism SDK 通过 Pixi.js 渲染 Live2D 模型。模型显示在主聊天界面中，并根据对话中检测到的情感做出响应。

## 模型来源

| 来源 | 位置 |
|------|------|
| 内置 | `static/` 目录 |
| 用户导入 | `user_live2d/` 目录 |
| Steam 创意工坊 | `workshop/` 目录（自动挂载） |

## 情感映射

每个 Live2D 模型可以定义从情感标签到表情和动作的映射：

```json
{
  "happy": { "expression": "f01", "motion": "idle_01" },
  "sad": { "expression": "f03", "motion": "idle_02" },
  "angry": { "expression": "f05", "motion": "idle_03" }
}
```

情感由后端（`/api/analyze_emotion`）检测，并通过 WebSocket 发送到前端。

## UI 组件

| 模块 | 用途 |
|------|------|
| `live2d-ui-buttons.js` | 控制按钮（模型切换、设置） |
| `live2d-ui-drag.js` | 模型位置的拖拽和缩放 |
| `live2d-ui-hud.js` | 平视显示叠加层 |
| `live2d-ui-popup.js` | 弹出对话框和菜单 |

## 模型管理页面

- `/model_manager` — 浏览、上传和删除模型
- `/live2d_parameter_editor` — 微调模型参数
- `/live2d_emotion_manager` — 配置情感到动画的映射

## API 端点

请参阅 [Live2D API](/api/rest/live2d) 获取完整的 REST 端点参考。
