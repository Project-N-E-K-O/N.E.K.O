# PNGTuber API

**Prefix:** `/api/model/pngtuber`

管理 PNGTuber 形象——基于 2D 图片的虚拟形象，通过切换图片状态（待机、说话、互动反应）来驱动外观。接口涵盖模型包上传、列出与删除。

## 模型包

PNGTuber 模型是一个文件夹（以多文件包形式上传），其中包含一个 `model.json`，且 `model_type` 为 `"pngtuber"`。`pngtuber` 配置块将各形象状态映射到对应图片文件。`idle_image` 为必填，其余状态均为可选。

支持的图片状态：

- `idle_image`（**必填**）
- `talking_image`
- `drag_image`
- `click_image`
- `happy_image`
- `sad_image`
- `angry_image`
- `surprised_image`

支持的图片扩展名：`.png`、`.gif`、`.jpg`、`.jpeg`、`.webp`。

::: info
体积限制：单个文件最大 **50 MB**，整个模型包最大 **250 MB**。
:::

## 上传

### `POST /api/model/pngtuber/upload_model`

以多文件 `multipart/form-data` 请求上传一个 PNGTuber 模型包。每个分段是一个文件，其 `filename` 携带它在包内的相对路径（公共的顶层文件夹会被自动剥除）。文件会被流式写入暂存目录，随后对模型包进行识别与规范化、校验，再提交到用户模型目录。

**Body:** `multipart/form-data`，包含一个或多个 `files` 分段。模型包必须包含根目录下的 `model.json`（`model_type: "pngtuber"`），或一个可识别的第三方工程文件（见下方“导入适配器”）。

**Response（成功）:**

```json
{
  "success": true,
  "message": "...",
  "model_type": "pngtuber",
  "model_name": "...",
  "name": "...",
  "folder": "...",
  "url": "/user_pngtuber/<folder>/model.json",
  "pngtuber": { },
  "source_format": "simple_package",
  "warnings": [],
  "file_size": 0
}
```

`pngtuber` 对象是规范化后的配置：图片状态路径被重写到 `/user_pngtuber/<folder>/...` 之下，并附带布局字段（`scale`、`offset_x`、`offset_y`、`mobile_scale`、`mobile_offset_x`、`mobile_offset_y`、`mirror`），以及 `adapter`、`layered_metadata`、`source_type`、`source_format`。

出错时返回 `{ "success": false, "error": "..." }`（已识别但导入失败时还会附带 `source_format` 与 `warnings`）。

::: info
校验要求 `model_type` 为 `"pngtuber"` 且 `idle_image` 非空。每个相对的 `*_image` 路径都必须使用受支持的扩展名，并指向包内确实存在的文件。
:::

#### 导入适配器

当模型包本身还不是原生 `model.json` 时，上传流程会识别来源格式并就地转换。检测出的格式会以 `source_format` 回传：

- `source_format: "simple_package"` —— 原生 N.E.K.O 模型包：根目录下的 `model.json`，`model_type: "pngtuber"`。直接按原样使用；驱动 idle/talking/drag/click 与轻量情绪图。
- `source_format: "pngtuber_plus_save"` —— PNGTuber-Plus（`.save`），经由 **`layered_canvas_v1`** 适配器转换（`adapter_version: 2`）：支持 costume、toggle、说话/眨眼、sprite sheet 多帧、Plus 节点树、矩形 clip 与近似物理。
- `source_format: "pngtube_remix_pngremix"` —— PNGTube-Remix（`.pngRemix`），经由 **`layered_canvas_v1`** 适配器转换（`adapter_version: 2`）：支持 state 切换、`emotion_mappings`、sprite sheet、`effective_z_index` 排序、`physics_v2` 与可用的 mesh 变形。
- `source_format: "veadotube"` —— veadotube（`.veadomini` / `.veado`）；可识别但**暂未支持**，上传会被拒绝并请求提供样本以便适配。
- `source_format: "image_pair_candidate"` —— 只有图片、没有 `model.json` 或工程文件；上传被拒绝，提示改用双图导入。

#### 能力与失败矩阵

`window.pngtuberManager.getDebugState()` 会按 `source_format` 报告当前启用的能力。情绪由 `window.applyEmotion('happy')` 驱动，对 `pngtuber` 模型会路由到 `pngtuberManager.setEmotion`。

| 能力 | `simple_package` | `pngtuber_plus_save` | `pngtube_remix_pngremix` |
|------|:----------------:|:--------------------:|:------------------------:|
| idle / talking 切换 | ✅ | ✅ | ✅ |
| 情绪 `window.applyEmotion('happy')` | ✅ 切图 | ✅ 切 state | ✅ 切 state |
| 眨眼 + 说话弹跳 | —— | ✅ | ✅ |
| costume 热键 / toggle | —— | ✅ | —— |
| sprite sheet 多帧 | —— | ✅ | ✅ |
| `physics_v2` | —— | 近似 | ✅ |
| mesh 变形（`meshRuntime`） | —— | —— | ✅ 存在真实几何时 |

只有当 Remix 工程带有真实的 vertices / triangles / UVs 时，debug state 里的 `meshRuntime` 才会为 `true`；否则 `meshMetadata` 保持 `true`、`meshRuntime` 保持 `false`，并在 `unsupportedFeatures` 中说明原因。

失败响应：

- `source_format: "veadotube"` → 可识别但被拒绝，等待真实样本。
- `source_format: "image_pair_candidate"` → 被拒绝；改用双图导入或补 `model.json`。
- 多个无法唯一确定的 `.save` → 返回 HTTP 400，附 `source_format: "pngtuber_plus_save"` 与 `warnings` 中的候选列表。
- 无法解析的 `.pngRemix` → 归类为 PNGTube-Remix 转换失败（`source_format: "pngtube_remix_pngremix"`），绝不退化为“缺少 `model.json`”错误。

#### 验收检查

```powershell
node --check static\pngtuber-core.js
node --check static\app-buttons.js
uv run pytest tests\unit\test_pngtuber_static_contracts.py tests\unit\test_card_maker_static_contracts.py tests\unit\test_pngtuber_router_delete.py tests\unit\test_model_manager_window_features.py
```

## 列出

### `GET /api/model/pngtuber/models`

列出所有已导入的 PNGTuber 模型。每条记录读取自模型包的 `model.json`（仅包含 `model.json` 中 `model_type: "pngtuber"` 的文件夹；无效模型包会被跳过）。

**Response:**

```json
{
  "success": true,
  "models": [
    {
      "name": "...",
      "folder": "...",
      "filename": "...",
      "location": "user",
      "type": "pngtuber",
      "model_type": "pngtuber",
      "url": "/user_pngtuber/<folder>/model.json",
      "pngtuber": { },
      "source_format": "simple_package"
    }
  ]
}
```

## 删除

### `DELETE /api/model/pngtuber/model`

删除一个 PNGTuber 模型包及其全部文件。

**Body:**

```json
{ "folder": "<folder>" }
```

目标按**文件夹 slug** 解析：处理器读取 `folder`，其次回退到 `url`，再回退到 `name`。无论传入哪个值，都会被当作文件夹 slug 处理（指向 `.../<folder>/model.json` 的 `url` 会被解析回其 `<folder>`），而不会去匹配人类可读的显示名称。

建议使用 `GET /models` 返回的 `folder` slug，或 model.json 的 `url` 来删除。不要依赖 `name`：`GET /models` 返回的 `name` 是显示名称，`folder` 才是磁盘上的 slug，二者可能不同——传入显示用的 `name` 仅在它恰好等于文件夹 slug 时才有效，因此只能作为可能存在歧义的最后兜底手段。解析出的路径必须仍位于 PNGTuber 目录之内。

**Response:** `{ "success": true, "message": "..." }`。缺少标识或路径越界返回 `400`；模型不存在返回 `404`。
