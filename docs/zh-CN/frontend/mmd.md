# MMD 模型

## 运行时与格式

MMD 渲染器接受 PMX/PMD 模型和 VMD 动画。Three.js 加载模型，`@moeru/three-mmd-physics-ammo` 与 Ammo 提供可选的刚体物理。

实现拆分在 `static/mmd/` 下：`mmd-core.js`、`mmd-manager.js`、`mmd-init.js`、`mmd-animation.js`、`mmd-expression.js`、`mmd-interaction.js`、`mmd-cursor-follow.js` 及 UI 模块。只有当前角色使用 MMD 形象时，`mmd-init.js` 才会创建 `window.mmdManager` 并初始化 `#mmd-canvas`。

## 模型与动画来源

`GET /api/model/mmd/models` 递归合并：

- `static/mmd/` 下的内置 PMX/PMD 文件；
- `/user_mmd` 下的用户模型；
- `/workshop/{item_id}/...` 下的已安装创意工坊模型。

VMD 动画来自 `static/mmd/animation/` 与 `/user_mmd/animation/`。模型包应保持 PMX/PMD 文件与其引用纹理的相对布局。

可以直接上传 PMX/PMD 与 VMD。ZIP 导入用于包含模型与纹理的完整包：它选择第一个 PMX/PMD，保留已有的单一顶层目录，修正常见的日文和 CJK 文件名编码，拒绝绝对路径与父目录穿越，并解压到用户模型子目录。

限制为：单个上传文件 500 MB、ZIP 解压总量 2 GB、ZIP 条目 10,000 个。

## 情绪映射

MMD 情绪把语义名称映射到一个或多个 morph 名称：

```json
{
  "neutral": ["default", "ニュートラル"],
  "happy": ["笑い", "smile"]
}
```

映射按模型保存在用户 MMD 目录的 `emotion_config` 下。运行时 `mmd-expression.js` 把它合并到内置候选项之上，选取已加载模型中存在的第一个 morph，并在配置的延时后让非 neutral 表情回到 neutral。编辑器暴露 `neutral`、`happy`、`relaxed`、`sad`、`angry`、`surprised` 与 `fear` 分组。

使用 `/mmd_emotion_manager` 检查实际 morph 名称并保存映射。MMD 激活时，`window.LanLan1.setEmotion(name)` 委派给 `window.mmdManager.expression.setEmotion(name)`。

## 管理行为

`/model_manager` 导入模型与动画、预览选中的形象，并删除用户内容。内置与创意工坊资源在这些端点中只读。删除包子目录中的用户模型时，会删除顶层包目录，避免留下模型引用的纹理；对应情绪映射也会一并删除。

## API 摘要

| 方法 | 端点 | 用途 |
| --- | --- | --- |
| `POST` | `/api/model/mmd/upload` | 上传一个 `.pmx` 或 `.pmd` 文件 |
| `POST` | `/api/model/mmd/upload_animation` | 上传一个 `.vmd` 文件 |
| `POST` | `/api/model/mmd/upload_zip` | 导入模型包 |
| `GET` | `/api/model/mmd/models` | 列出内置、用户与创意工坊模型 |
| `GET` | `/api/model/mmd/animations` | 列出内置与用户 VMD 动画 |
| `GET` | `/api/model/mmd/config` | 返回公共 MMD URL 前缀 |
| `GET`、`POST` | `/api/model/mmd/emotion_mapping` | 读取或保存按模型的 morph 映射 |
| `DELETE` | `/api/model/mmd/model` | 按公共 URL 删除用户模型/模型包 |
| `GET` | `/api/model/mmd/animations/list` | 列出可删除的用户动画 |
| `DELETE` | `/api/model/mmd/animation` | 按公共 URL 删除用户 VMD 动画 |

## 宿主边界

MMD 只在主页面角色界面渲染，也包括加载 `/` 的 Electron 桌宠窗口。`/chat` 与 `/subtitle` 是独立窗口，应与主页面通信，而不是创建另一个 MMD 场景。
