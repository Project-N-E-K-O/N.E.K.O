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
| `happy_image` / `sad_image` / `angry_image` / `surprised_image` | 情绪帧（见 [情绪状态](#情绪状态)）。 |

相对路径在包文件夹内解析；绝对路径（`/…`）与 `http(s)://` URL 原样保留。图片引用会在服务端规范化为 `/user_pngtuber/<folder>/<file>`。

### 允许的扩展名与大小限制

| 约束 | 取值 |
|------------|-------|
| 图片扩展名 | `.png`、`.gif`、`.jpg`、`.jpeg`、`.webp` |
| 单文件上限 | 50 MB |
| 整包上限 | 250 MB |

服务端会在接受包之前校验：`idle_image` 必须存在，且每个 `*_image` 引用都指向一个存在的、扩展名合法的文件。

## 情绪状态

`window.applyEmotion('happy')` 在运行时驱动 PNGTuber 的情绪。`applyEmotion`（位于 `static/app-buttons.js`）会在当前 `model_type` 为 `pngtuber` 时，先路由到 `window.pngtuberManager.setEmotion(emotion)`，再决定是否回退到 Live2D 路径。

- **simple package** —— `setEmotion('happy')` 切换到 `happy_image` / `sad_image` / `angry_image` / `surprised_image`，默认保持 5 秒后回到 idle。中性值（`neutral`、`idle`、`default`、`none`、`clear` 或空字符串）会立即清除情绪。
- **分层包** —— `setEmotion` 通过 `setLayeredEmotion` 把情绪映射到某个 layered state。Remix 的 `emotion_mappings` 优先；否则在模型至少暴露 5 个 state 时，采用 happy → 1、sad → 2、angry → 3、surprised → 4 的兜底顺序。

`idle` ↔ `talking` 仍由助手**语音**开始/结束事件切换，`drag` / `click` 仍由指针**交互**切换。若没有匹配的情绪图片或 state，`setEmotion` 会直接返回、不改变画面，并打印 `[PNGTuber] emotion unavailable`。

## 导入格式

上传接口会检测包类型并就地规范化。检测出的类型会以 `source_format` 回传。

| 来源 | 检测方式 | `source_format` |
|--------|-----------|-----------------|
| 原生 simple package | 文件夹根目录有 `model.json` | `source_format: "simple_package"` |
| PNGTuber-Plus | 存在 `.save` 工程文件 | `source_format: "pngtuber_plus_save"` |
| PNGTube-Remix | 存在 `.pngRemix` 工程文件 | `source_format: "pngtube_remix_pngremix"` |
| veadotube | 存在 `.veadomini` / `.veado` 文件 | `source_format: "veadotube"` |
| 只有图片、无工程文件 | 有图片但无 `model.json` | `source_format: "image_pair_candidate"` |

### 分层适配器（`layered_canvas_v1`）

导入 PNGTuber-Plus 或 PNGTube-Remix 工程时，转换器会生成一份分层元数据文件（`adapter_version: 2`）并把 `adapter` 设为 `layered_canvas_v1`。运行时 `PNGTuberManager` 会把各图层绘制到一张 `<canvas>` 上，而不是切换单个 `<img>`。Plus 与 Remix 按 `source_format` 分流，运行时互不污染。两者都会加入随机眨眼定时器和说话弹跳；若元数据加载失败，运行时会回退到普通的单图模式。

## 能力表

`window.pngtuberManager.getDebugState()` 会报告当前模型启用了哪些能力。

| 能力 | `simple_package` | `pngtuber_plus_save` | `pngtube_remix_pngremix` |
|------|:----------------:|:--------------------:|:------------------------:|
| idle / talking 切换 | ✅ | ✅ | ✅ |
| 情绪 `window.applyEmotion('happy')` | ✅ 切图 | ✅ 切 state | ✅ 切 state |
| 眨眼 + 说话弹跳 | —— | ✅ | ✅ |
| costume 热键 / toggle | —— | ✅ | —— |
| sprite sheet 多帧 | —— | ✅ | ✅ |
| `physics_v2` | —— | 近似 | ✅ |
| mesh 变形 | —— | —— | ✅ 存在真实几何时 |

只有当 Remix 工程带有真实的 vertices / triangles / UVs 时，debug state 里的 `meshRuntime` 才会为 `true`；否则 `meshMetadata` 保持 `true`、`meshRuntime` 保持 `false`，并在 `unsupportedFeatures` 中说明原因。

### 失败提示

- **veadotube**（`.veadomini` / `.veado`）→ `source_format: "veadotube"`；上传被拒绝，并请求提供真实样本以便适配。
- **只有图片** → `source_format: "image_pair_candidate"`；上传被拒绝，提示改用双图导入或补 `model.json`。
- **多个无法唯一确定的 `.save`** → 返回 HTTP 400，附 `source_format: "pngtuber_plus_save"` 与 `warnings` 中的候选列表。
- **无法解析的 `.pngRemix`** → 归类为 PNGTube-Remix 转换失败（`source_format: "pngtube_remix_pngremix"`），绝不退化为“缺少 `model.json`”错误。

## 验收清单

静态契约（在仓库根目录执行）：

```powershell
node --check static\pngtuber-core.js
node --check static\app-buttons.js
uv run pytest tests\unit\test_pngtuber_static_contracts.py tests\unit\test_card_maker_static_contracts.py tests\unit\test_pngtuber_router_delete.py tests\unit\test_model_manager_window_features.py
```

手动验收：

- 导入 PNGTuber-Plus `.save`，确认 costume、toggle、说话/眨眼、sprite sheet 多帧、父子 transform 与矩形 clip 均正常渲染。
- 导入多 state 的 PNGTube-Remix `.pngRemix`，确认 `window.applyEmotion('happy')` 映射到正确的 state。
- 检查 `window.pngtuberManager.getDebugState()` 中的 `sourceFormat`、`adapterVersion`、`runtimeFeatures`、`meshRuntime` 与 `physicsVersion`。

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
