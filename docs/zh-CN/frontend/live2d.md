# Live2D 模型

## 运行时

Live2D 在主页面的 `#live2d-canvas` 上渲染。当前实现拆分为：

- `static/live2d/live2d-core.js`
- `static/live2d/live2d-model.js`
- `static/live2d/live2d-emotion.js`
- `static/live2d/live2d-interaction.js`
- `static/live2d/live2d-init.js`
- `static/live2d/live2d-ui-buttons.js`

`live2d-init.js` 创建 `window.live2dManager` 并暴露共享的 `window.LanLan1` 兼容方法。模型管理器为预览加载同一套渲染模块；不存在另一套旧版 Live2D 页面实现。

## 模型与来源

模型通过 Cubism `.model3.json` 文件及其引用的 `.moc3`、纹理、动作、表情和可选物理文件发现。必须保持模型的相对目录结构。

`GET /api/live2d/models` 会合并：

- 项目静态模型目录中的内置模型；
- 通过 `/user_live2d` 提供的用户模型（配置时还可能使用可写的 `/user_live2d_local` 影子目录）；
- 通过 `/workshop/{item_id}/...` 提供的已安装 Steam 创意工坊模型。

应使用 API 返回的 URL，不要根据绝对文件系统路径拼接 URL。

## 情绪映射

编辑器和运行时使用以下逻辑结构：

```json
{
  "motions": { "happy": ["motions/happy.motion3.json"] },
  "expressions": { "happy": ["expressions/happy.exp3.json"] }
}
```

存在 `EmotionMapping` 时服务器优先读取；否则会根据 `FileReferences.Motions` 与表情名称前缀推导分组。保存时写入标准 Cubism `FileReferences.Motions` 与 `FileReferences.Expressions` 结构；动作和表情路径必须保持相对路径，且不能越出模型目录。

`window.LanLan1.setEmotion(name)` 会委派给当前渲染器。对于 Live2D，管理器会应用已配置的表情与动作；其中一项缺失时会安全降级。特殊 `常驻` 组只能配置表情。

## 管理页面

- `/model_manager` 选择、导入、预览和删除模型。
- `/live2d_emotion_manager` 把情绪组映射到动作与表情。
- `/live2d_parameter_editor` 编辑保存的模型布局/参数设置。

## API 摘要

| 方法 | 端点 | 用途 |
| --- | --- | --- |
| `GET` | `/api/live2d/models` | 列出本地与创意工坊模型；`?simple=true` 只返回名称 |
| `GET`、`POST` | `/api/live2d/model_config/{model_name}` | 读取 Cubism 配置；只更新动作与表情 |
| `GET`、`POST` | `/api/live2d/emotion_mapping/{model_name}` | 读取或保存情绪组 |
| `GET` | `/api/live2d/model_files/{model_name}` | 列出已验证的模型资源 |
| `GET` | `/api/live2d/model_parameters/{model_name}` | 检查 Cubism 参数元数据 |
| `GET`、`POST` | `/api/live2d/load_model_parameters/{model_name}`、`/api/live2d/save_model_parameters/{model_name}` | 加载或保存参数设置 |
| `POST` | `/api/live2d/upload_model` | 导入多文件模型包 |
| `POST` | `/api/live2d/upload_file/{model_name}` | 添加动作或表情文件；上限 50 MB |
| `DELETE` | `/api/live2d/model/{model_name}` | 删除用户模型 |

ID 版本（`model_config_by_id` 与 `model_files_by_id`）用于以发布物品 ID 作为稳定标识的创意工坊模型。

## 宿主边界

Live2D 资源与初始化运行在 `index.html` 中，也包括加载该模板的 Electron 桌宠窗口。独立 `/chat` 与 `/subtitle` 页面不会渲染第二个角色；跨窗口命令应转发给主页面，而不是再初始化一个 Live2D 管理器。
