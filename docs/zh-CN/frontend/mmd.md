# MMD 模型

## 概述

N.E.K.O. 使用 Three.js 及其 MMD 加载器渲染 MMD（MikuMikuDance）模型。内置模型位于 `static/mmd/Miku/Miku.pmx`，默认加载。模型会根据对话中识别出的情绪做出反应，并通过 **morph-target（混合形状 / blendshape）** 权重驱动，而非 Live2D 那样的动作文件。

## 格式

| 类型 | 扩展名 | 说明 |
|------|--------|------|
| 模型 | `.pmx`、`.pmd` | 由 Three.js MMD 加载器加载 |
| 动画 | `.vmd` | 姿态 / 动作轨道（待机、口型同步等） |

::: info
上传上限为 **500 MB**（含纹理的 MMD 模型可能较大）。参见 `main_routers/mmd_router.py` 中的 `MAX_FILE_SIZE`（第 57–64 行）。ZIP 包还另有 2 GB 解压上限与 10000 个文件数上限，用于防范 zip bomb。
:::

## 模型来源

| 来源 | 位置 |
|------|------|
| 内置 | `/static/mmd`（如 `static/mmd/Miku/Miku.pmx`） |
| 用户导入的模型 | `/user_mmd` |
| 用户导入的动画 | `/user_mmd/animation` |
| Steam 创意工坊 | `/workshop/<item_id>/...`（自动挂载） |

按模型覆盖的情感映射存储在 `/user_mmd/emotion_config` 下，文件名为 `<model>.json`。

## 渲染与静态模块

查看器由 `static/` 下的 `mmd-*.js` 模块组成：

| 模块 | 用途 |
|------|------|
| `mmd-core.js` | Three.js 场景、渲染器与 MMD 模型加载 |
| `mmd-manager.js` | 顶层管理器（`window.mmdManager`），串联各子模块 |
| `mmd-init.js` | 启动 / 初始化 |
| `mmd-animation.js` | VMD 动画播放与口型同步取值 |
| `mmd-expression.js` | morph-target 控制与情感系统（`mmd-expression.js`） |
| `mmd-interaction.js` | 指针 / 交互处理 |
| `mmd-cursor-follow.js` | 光标跟随行为 |
| `mmd-ui-buttons.js` | MMD 专属控制按钮 |
| `mmd-ui-debug.js` | 调试浮层 |

## 情感映射

与 Live2D（将情绪映射到表情 + 动作文件）不同，MMD 的情绪以 **morph-target / blendshape** 权重的方式应用。`mmd-expression.js` 内置了一份从情绪标签到候选 morph 名称（日文 / 英文）的默认 `moodMap`，例如：

```javascript
{
  "happy":     ["笑い", "にやり", "にこり", "smile", "happy", "joy", "ワ"],
  "sad":       ["悲しい", "泣き", "sad", "sorrow", "しょんぼり"],
  "angry":     ["怒り", "angry", "anger", "むっ"],
  "surprised": ["驚き", "びっくり", "surprised", "shock", "おっ"],
  "relaxed":   ["穏やか", "relaxed", "calm", "微笑み"],
  "fear":      ["恐怖", "fear", "scared", "おびえ"]
}
```

设置情绪时，`MMDExpression.setEmotion(emotion)` 会查找候选 morph 名称，选取当前模型中第一个存在的名称，并将其权重设为 `1.0`（一段延迟后自动回归 neutral）。其应用方式为：

```javascript
window.mmdManager.expression.setEmotion('happy');
```

每个模型都可以覆盖默认映射。前端通过 `GET /api/model/mmd/emotion_mapping?model=<name>`（经由 `loadMoodMap()`）拉取并将返回的映射合并到默认值之上；编辑器通过 `POST /api/model/mmd/emotion_mapping` 保存覆盖。

## 模型管理页面

- `/model_manager` — 浏览、上传与删除 MMD 模型和动画
- `/mmd_emotion_manager` — 配置按模型的情绪到 morph 的映射

## REST API

**前缀：** `/api/model/mmd`

定义于 `main_routers/mmd_router.py`。所有成功响应均为带 `success` 布尔字段的 JSON；错误响应使用 `{ "success": false, "error": "..." }` 并附带相应状态码。

### `POST /api/model/mmd/upload`

上传 MMD 模型文件（`.pmx` / `.pmd`）。

**Body：** `multipart/form-data`，含模型 `file`。

**Response：** `{ success, message, model_name, model_url, file_size }`。文件以 1 MB 分块流式写入 `/user_mmd`；同名文件已存在时会被拒绝。

### `POST /api/model/mmd/upload_animation`

上传 `.vmd` 动画文件。

**Body：** `multipart/form-data`，含 `.vmd` 的 `file`。

**Response：** `{ success, message, filename, file_path }`。存储于 `/user_mmd/animation`。

### `POST /api/model/mmd/upload_zip`

上传 `.zip` 包（模型 `.pmx`/`.pmd` 加纹理），自动解压到子目录。

**Body：** `multipart/form-data`，含 `.zip` 的 `file`。

**Response：** `{ success, message, model_name, model_url, file_count, file_size }`。

::: info
由于 MMD 包常使用非 UTF-8 文件名，ZIP 文件名会经 CJK 感知探测（Shift-JIS / CP932、GBK、Big5、EUC-KR）解码。路径穿越、zip bomb 以及保留目录名（`animation`、`emotion_config`）都会被拒绝。
:::

### `GET /api/model/mmd/models`

列出 MMD 模型。会搜索内置 `static/mmd`、用户 `/user_mmd`（递归，跳过保留目录）以及已订阅的 Steam 创意工坊项目。

**Response：** `{ success, models: [...] }`。每个条目包含 `name`、`filename`、`url`、`rel_path`、`type`、`size` 和 `location`（`project`、`user` 或 `steam_workshop`）。没有有效模型文件的残留目录会带 `broken: true` 返回。

### `GET /api/model/mmd/animations`

列出内置 `static/mmd/animation` 与用户 `/user_mmd/animation` 下的 `.vmd` 动画。

**Response：** `{ success, animations: [...] }`，含 `name`、`filename`、`url`、`type`、`size`。

### `GET /api/model/mmd/config`

返回 MMD 路径配置。

**Response：** `{ success, paths: { user_mmd: "/user_mmd", static_mmd: "/static/mmd" } }`。

### `GET /api/model/mmd/emotion_mapping`

获取某个模型的情感映射。

**Query：** `model=<name>`。

**Response：** `{ success, mapping }`。未存储覆盖时返回空映射。模型名含路径分隔符会被拒绝。

### `POST /api/model/mmd/emotion_mapping`

更新某个模型的情感映射。

**Body：** JSON `{ "model": "<name>", "mapping": { ... } }`。

**Response：** `{ success, message }`。映射以原子写入方式保存到 `/user_mmd/emotion_config/<model>.json`。

### `DELETE /api/model/mmd/model`

删除用户导入的模型（及其目录内的关联资源）。

**Body：** JSON `{ "url": "/user_mmd/<...>" }`。

**Response：** `{ success, message, deleted_files }`。位于子目录中的模型会删除整个子目录；对应的 `emotion_config/<model>.json` 也会被删除。内置 `/static/mmd/` 模型不可删除。

### `GET /api/model/mmd/animations/list`

列出可删除的用户 `.vmd` 动画（来自 `/user_mmd/animation`）。

**Response：** `{ success, animations: [...] }`，含 `name`、`filename`、`url`、`path`。

### `DELETE /api/model/mmd/animation`

删除用户导入的 `.vmd` 动画。

**Body：** JSON `{ "url": "/user_mmd/animation/<file>.vmd" }`。

**Response：** `{ success, message }`。只能删除 `/user_mmd/animation` 下的 `.vmd` 文件。
