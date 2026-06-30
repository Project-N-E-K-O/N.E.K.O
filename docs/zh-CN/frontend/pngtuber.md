# PNGTuber 模型

## 概述

N.E.K.O. 可以渲染轻量级的 2D 图片形象（"PNGTuber" 风格），作为 Live2D、MMD 或 VRM 模型之外的另一种选择。PNGTuber 形象由 `static/pngtuber-core.js`（`PNGTuberManager` 类）驱动，它会根据语音和指针交互在静态图片之间切换（对于导入的分层工程，则绘制一张层叠 canvas）。

与 3D/Live2D 形象不同，PNGTuber 包就是一个图片文件夹外加一个 `model.json` 描述文件——没有骨骼绑定，也不需要 Cubism 运行时。

## 包格式

PNGTuber 模型是一个包含 `model.json` 文件的文件夹，其 `model_type` 须设为 `pngtuber`。图片引用位于 `pngtuber` 对象下：

```json
{
  "name": "My Avatar",
  "model_type": "pngtuber",
  "pngtuber": {
    "idle_image": "idle.png",
    "talking_image": "talking.png",
    "drag_image": "drag.png",
    "click_image": "click.png",
    "happy_image": "happy.png",
    "sad_image": "sad.png",
    "angry_image": "angry.png",
    "surprised_image": "surprised.png"
  }
}
```

### 图片状态键

| 键 | 用途 |
|-----|---------|
| `idle_image` | **必填。** 默认的待机帧。 |
| `talking_image` | 助手说话时显示。 |
| `drag_image` | 拖拽形象时显示。 |
| `click_image` | 点击形象时短暂显示。 |
| `happy_image` / `sad_image` / `angry_image` / `surprised_image` | 情绪帧（见 [情绪状态](#情绪状态尚未由情绪分析驱动)）。 |

相对路径在包文件夹内解析；绝对路径（`/…`）与 `http(s)://` URL 原样保留。图片引用会在服务端规范化为 `/user_pngtuber/<folder>/<file>`。

### 允许的扩展名与大小限制

| 约束 | 取值 |
|------------|-------|
| 图片扩展名 | `.png`、`.gif`、`.jpg`、`.jpeg`、`.webp` |
| 单文件上限 | 50 MB |
| 整包上限 | 250 MB |

服务端会在接受包之前校验：`idle_image` 必须存在，且每个 `*_image` 引用都指向一个存在的、扩展名合法的文件。

## 情绪状态（尚未由情绪分析驱动）

::: warning 诚实说明
`happy_image` / `sad_image` / `angry_image` / `surprised_image` 这几个键属于包的 schema，且会经过**服务端校验**（上传时检查路径与扩展名），但 PNGTuber 运行时**尚未**根据情绪分析切换到它们。

`PNGTuberManager` 目前只驱动：

- `idle` ↔ `talking`，由助手**语音**开始/结束事件切换。
- `drag` 与 `click`，由指针**交互**切换。

它没有 Live2D / MMD / VRM 那样的 `setEmotion` 钩子，也没有专门的 PNGTuber 情绪管理页面。这四个情绪图片键会随包一起存储和分发，以便为将来的情绪驱动路径做好准备，但目前提供它们除了能通过校验外没有任何可见效果。
:::

## 导入格式

上传接口会检测包类型并就地规范化。检测出的类型会以 `source_format` 回传。

| 来源 | 检测方式 | 结果 |
|--------|-----------|--------|
| 原生 simple package | 文件夹根目录有 `model.json` | `source_format: simple_package`——直接使用。 |
| **PNGTuber-Plus** | 存在 `.save` 工程文件 | 转换为 `layered_canvas_v1` 适配器。 |
| **PNGTube-Remix** | 存在 `.pngremix` 工程文件 | 转换为 `layered_canvas_v1` 适配器。 |
| veadotube | 存在 `.veadomini` / `.veado` 文件 | **已识别但暂不支持**——上传会被拒绝并附说明性错误。 |

### 分层适配器（`layered_canvas_v1`）

导入 PNGTuber-Plus 或 PNGTube-Remix 工程时，转换器会生成一份分层元数据文件并把 `adapter` 设为 `layered_canvas_v1`。运行时 `PNGTuberManager` 会把各图层绘制到一张 `<canvas>` 上，而不是切换单个 `<img>`，并额外加入：

- **眨眼（blink）**——眼睛图层按随机定时器眨眼。
- **说话弹跳（speech bounce）**——说话时形象上下弹跳/挤压。

源工程中的热键、物理和多帧动画会保存在元数据中以备将来运行时支持，但目前尚未全部驱动。若元数据加载失败，运行时会回退到普通的单图模式。

## 静态服务

用户的 PNGTuber 包通过 `/user_pngtuber` 挂载提供，对应磁盘上配置的 PNGTuber 目录。模型文件引用形如 `/user_pngtuber/<folder>/model.json` 与 `/user_pngtuber/<folder>/<image>`。

## API 端点

**前缀：** `/api/model/pngtuber`

### `POST /upload_model`

以 multipart 文件列表上传一个 PNGTuber 包。每个文件的 `filename` 携带其在包内的相对路径；单一的共享顶层文件夹会被自动剥离。包会先暂存、检测、校验，并（对第三方工程）转换，然后才正式落地。

**Body**——`multipart/form-data`，含一个 `files` 字段（一个或多个 `UploadFile` 条目）。

**Response**（成功）

```json
{
  "success": true,
  "message": "...",
  "model_type": "pngtuber",
  "model_name": "My Avatar",
  "name": "My Avatar",
  "folder": "My_Avatar",
  "url": "/user_pngtuber/My_Avatar/model.json",
  "pngtuber": { "idle_image": "/user_pngtuber/My_Avatar/idle.png", "...": "..." },
  "source_format": "simple_package",
  "warnings": [],
  "file_size": 123456
}
```

失败时返回 `{ "success": false, "error": "..." }`，并附相应的 4xx/5xx 状态码。第三方导入错误还会附带 `source_format` 与 `warnings`。

### `GET /models`

列出所有已安装的用户 PNGTuber 包。

**Response**

```json
{
  "success": true,
  "models": [
    {
      "name": "My Avatar",
      "folder": "My_Avatar",
      "filename": "My_Avatar",
      "location": "user",
      "type": "pngtuber",
      "model_type": "pngtuber",
      "url": "/user_pngtuber/My_Avatar/model.json",
      "pngtuber": { "idle_image": "/user_pngtuber/My_Avatar/idle.png", "...": "..." },
      "source_format": "simple_package"
    }
  ]
}
```

没有合法 `model.json` 或 `model_type` 不是 `pngtuber` 的文件夹会被跳过。

### `DELETE /model`

删除一个已安装的 PNGTuber 包。

**Body**

```json
{ "folder": "My_Avatar" }
```

标识按**文件夹 slug** 解析（优先级 `folder` → `url` → `name`）：像 `/user_pngtuber/My_Avatar/model.json` 这样的 `model.json` URL 会被解析回其文件夹。建议用 `GET /models` 返回的 `folder` slug（或 `url`）——`name` 是给人看的显示名，可能与 slug 不一致，只有当它恰好等于文件夹名时按 `name` 删才生效。目标会被限制在 PNGTuber 目录内。

**Response**

```json
{ "success": true, "message": "PNGTuber model My_Avatar deleted" }
```

::: info
PNGTuber 的模型管理位于共享的 `/model_manager` 页面。没有单独的 PNGTuber 情绪管理页面；形象的设置菜单链接到角色卡管理、模型管理和声音克隆页面。
:::
