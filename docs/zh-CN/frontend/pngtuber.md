# PNGTuber 模型

## 运行时

PNGTuber 是由 `static/pngtuber-core.js` 渲染的图片状态角色。`PNGTuberManager` 支持简单图片切换，也支持通过 `layered_canvas_v1` 适配器运行规范化分层模型。它与 Live2D、VRM、MMD 一样接入主页面的角色选择与 `window.LanLan1.setEmotion()` 契约。

## 规范化模型包

每个导入模型都保存在配置的用户 PNGTuber 目录，并通过 `/user_pngtuber/{folder}/model.json` 提供。最小简单包如下：

```json
{
  "model_type": "pngtuber",
  "name": "Example",
  "pngtuber": {
    "idle_image": "idle.png",
    "talking_image": "talking.png"
  }
}
```

`model_type` 必须为 `pngtuber`，`idle_image` 必填。相对图片路径必须留在模型包内。支持 `.png`、`.gif`、`.jpg`、`.jpeg` 与 `.webp`。

可选图片状态键包括 `talking_image`、`drag_image`、`click_image`、`happy_image`、`sad_image`、`angry_image` 与 `surprised_image`。运行时会为缺失状态回退，例如 talking 可使用 idle，因此只有 `idle_image` 强制要求。

布局键包括 `scale`、`offset_x`、`offset_y`、移动端专用缩放/偏移以及 `mirror`。分层导入还使用 `adapter: "layered_canvas_v1"` 与 `layered_metadata`。

## 导入格式

导入器按以下顺序检测格式：

1. 根目录含 `model.json` 的原生简单包；
2. PNGTuber Plus `.save` 工程；
3. PNGTube Remix `.pngRemix` 工程；
4. veadotube `.veadomini` 或 `.veado` 文件。

PNGTuber Plus 与 PNGTube Remix 会转换为规范化模型包，并可能产生分层元数据与警告。veadotube 当前只会被识别，随后以不支持格式拒绝。只有图片的文件夹也会被模型包端点拒绝；应使用模型管理器的图片对导入流程，或提供有效 `model.json`。

请求可以上传文件夹树。服务器会移除一个共享顶层目录、验证每条相对路径、先写入临时目录，并仅在导入成功后把模型包改名到目标位置。已有模型目录不会被覆盖。

限制为单文件 50 MB、完整模型包 250 MB。

## 状态与情绪行为

基础状态为 idle、talking、drag 与 click。语义情绪使用四个可选的 `happy`、`sad`、`angry`、`surprised` 图片或等价分层状态。源导入器支持时，分层适配器可保留第三方可见性状态、热键、开关、精灵图、眨眼与物理元数据。

规范化 `source_format` 说明模型包如何生成；客户端只应把它用于诊断，不应根据它选择渲染行为。渲染行为由规范化 `pngtuber` 对象与适配器元数据决定。

## API 摘要

所有端点都使用 `/api/model/pngtuber` 前缀。

| 方法 | 端点 | 用途 |
| --- | --- | --- |
| `POST` | `/api/model/pngtuber/upload_model` | 上传并规范化文件夹/模型包 |
| `GET` | `/api/model/pngtuber/models` | 列出有效用户模型包 |
| `DELETE` | `/api/model/pngtuber/model` | 按 `folder`、`url` 或 `name` 删除模型包 |

成功上传响应包含规范化模型、公共 URL、`source_format`、警告与上传总大小。列表端点会跳过没有有效 PNGTuber `model.json` 的目录。删除只接受直接模型包文件夹或 `/user_pngtuber/{folder}/model.json`；嵌套路径与路径穿越会被拒绝。

## 宿主边界

PNGTuber 在主页面和使用 `index.html` 的 Electron 桌宠窗口中渲染。`/chat` 与 `/subtitle` 不会初始化另一个 PNGTuber 管理器；需要跨窗口反映角色状态时，应与主窗口通信。
