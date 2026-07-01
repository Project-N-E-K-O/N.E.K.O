# PNGTuber 轻量角色载体技术文档

本文是 PNGTuber 接入、第三方格式导入和前端 runtime 的当前维护基线。旧的阶段性计划、踩坑记录和 PR #1779 经验已经整理进对应章节；以后维护时以本文前半部分的“当前事实”和“验收标准”为准。

## 当前结论

PNGTuber 是现有 avatar 体系下的轻量渲染模式，在角色配置中通过模型类型枚举启用：

```json
{ "model_type": "pngtuber" }
```

它不是 Live2D 的子格式，也不是独立于角色 `_reserved.avatar` 之外的新配置体系；运行时不依赖 WebGL、Godot Engine、Godot CLI 或额外常驻进程。普通 PNGTuber 使用单图或多状态图片；第三方工程包会在上传阶段转换为项目统一模型包，然后由 `static/pngtuber-core.js` 的 `PNGTuberManager` 在前端运行。

当前支持的导入形态：

| 来源 | `source_format` | 当前状态 |
| --- | --- | --- |
| 原生 `model.json` 图片包 | `simple_package` | 直接加载，支持 idle/talking/drag/click 与轻量情绪图。 |
| PNGTuber-Plus `.save` | `pngtuber_plus_save` | 转换为 `layered_canvas_v1`，metadata `adapter_version: 2`；支持 costume、hotkey、toggle、说话/眨眼、多帧、Plus 节点树、矩形 clip 和近似物理。 |
| PNGTubeRemix `.pngRemix` | `pngtube_remix_pngremix` | 解析 Godot Variant 并转换为 `layered_canvas_v1`，metadata `adapter_version: 2`；支持 state/hotkey、emotion mapping、sprite sheet、父级继承 `z_index` 图层排序、Remix `physics_v2` 和可用 mesh deformation。 |
| veadotube `.veadomini/.veado` | `veadotube` | 识别但拒绝，等待真实样本适配。 |
| 只有图片无工程文件 | `image_pair_candidate` | 拒绝，提示使用双图导入或补 `model.json`。 |

公开 `pngtuber.adapter` 继续保持 `layered_canvas_v1`，这是兼容现有前端开关和旧模型的加载名；能力版本写在 metadata 的 `adapter_version` 与 `runtime_features` 中。

## 架构边界

后端职责：

- 管理用户 PNGTuber 资源目录和 `/user_pngtuber` 静态挂载。
- 校验原生 `model.json` 包。
- 识别并转换 `.save` / `.pngRemix` / `.veadomini` / `.veado`。
- 生成统一的 `model.json`、预览图、分层素材和 metadata。
- 保证导入失败返回明确 `source_format`，并清理临时目录。

前端职责：

- 加载 `model.json` 中的 PNGTuber 配置。
- 渲染普通 `<img>` 或 layered `<canvas>`。
- 处理说话、眨眼、mouth flap、One Bounce、拖拽、缩放、镜像和 debug state。
- 按 `source_format` 分流 Plus / Remix runtime，避免互相污染。
- 与 Live2D、VRM、MMD 保持运行态互斥。

非目标：

- 不直接运行第三方工程。
- 不引入 Godot Engine、Godot CLI、GDScript runtime、导出模板或 `project.godot`。
- 不复刻 PNGTuber-Plus / PNGTubeRemix 编辑器。
- 不做完整 Godot 物理或 collision polygon runtime。
- 不让 PNGTuber runtime 改写全局 `window.lanlan_config.model_type`。

## 模型配置

PNGTuber 配置保存到角色配置 `_reserved.avatar.pngtuber` 中。关键字段：

```json
{
  "idle_image": "/user_pngtuber/character_idle.png",
  "talking_image": "/user_pngtuber/character_talking.png",
  "drag_image": "",
  "click_image": "",
  "happy_image": "",
  "sad_image": "",
  "angry_image": "",
  "surprised_image": "",
  "scale": 1,
  "offset_x": 0,
  "offset_y": 0,
  "mobile_scale": 1,
  "mobile_offset_x": 0,
  "mobile_offset_y": 0,
  "mirror": false,
  "adapter": "layered_canvas_v1",
  "layered_metadata": "metadata.pngtuber-plus.json",
  "source_type": "pngtuber_plus_save",
  "source_format": "pngtuber_plus_save"
}
```

保存链路必须保留完整 config，尤其是：

- `adapter`
- `layered_metadata`
- `source_format`
- `source_type`
- `idle_image`
- `talking_image`
- `mobile_scale`
- `mobile_offset_x`
- `mobile_offset_y`

Plus / Remix 分层模型保存后，上述字段不能丢。Plus 还要保留 `plus_settings`、`runtime_features` 和 layer transform 字段；Remix 要保留 `runtime_features`、`state_catalog`、`emotion_mappings`、mesh/physics 能力标记。

## Runtime 行为

### 普通 PNGTuber

普通包使用单 `<img>` runtime：

- `idle` / `talking` 随语音切换。
- 长语音通过轻量 mouth flap 在 `idle` / `talking` 间循环，避免一直张嘴。
- `drag` / `click` 随指针交互短暂切换。
- `happy` / `sad` / `angry` / `surprised` 通过 `PNGTuberManager.setEmotion()` 或 `window.applyEmotion()` 切换。

### 分层 PNGTuber

导入 Plus 或 Remix 后，运行时使用 `<canvas>` 绘制分层图像。共同能力：

- 说话和眨眼层筛选。
- 随机 blink timer。
- speech bounce / One Bounce。
- layered state 切换。
- sprite sheet frame animation。
- `window.pngtuberManager.getDebugState()`。

`getDebugState()` 必须至少暴露：

- `sourceFormat`
- `adapterVersion`
- `metadataCapabilities`
- `runtimeFeatures`
- `unsupportedFeatures`
- `layerCount`
- `renderedIdleLayerCount`
- `renderedTalkingLayerCount`
- `renderedLayers`
- `meshMetadata`
- `meshRuntime`
- `physicsVersion`
- `layeredToggles`

### Plus runtime

Plus 由 `source_format: "pngtuber_plus_save"` 显式启用，不能套到 Remix 或旧 layered 模型。

当前能力：

- `costumeLayers` 归一化为 10 位，默认 costume 1。
- 生成 10 个 `Costume 1..10` state。
- 当前 costume 可见性继承父级：父层隐藏时子层也隐藏。
- 默认 costume hotkey 为 `1 2 3 4 5 6 7 8 9 0`。
- 可选读取 `settings.pngtp` 的 `costumeKeys`。
- `null`、空字符串、重复键不注册 hotkey。
- 每层 `toggle` 可按键翻转显示状态，并影响子树。
- 同一按键既是 costume hotkey 又是 toggle key 时，先切 costume，再执行 toggle。
- `showTalk/showBlink` 按官方 Plus 可见性表生效。
- 横向 sprite sheet 保留完整图片，`frames` 映射到 `hframes`。
- `animSpeed` 转换为前端 `animation_speed`，保留 `source_anim_speed`。
- 导入器输出 `local_position`、`node_origin`、`sprite_offset`、`draw_offset`、`parent_chain`、`plus_transform`。
- 前端走 `isLayeredPlusModel()`、`drawPlusLayerTree()`、`plusLayerPhysicsTransform()`。
- 父层 transform 影响子层。
- `clip_children` 以父 sprite 当前帧矩形裁剪子树。
- `settings.pngtp` 的 `blinkSpeed`、`blinkChance`、`bounceOnCostumeChange` 写入 runtime 配置。

Plus 边界：

- collision polygon / 透明区域命中仍只保留 metadata。
- `clip_children` 是矩形裁剪，不是 alpha mask。
- wobble / drag / rotationalDrag / stretch 是实用近似，不是 Godot 逐帧复刻。
- 不复刻 Plus 编辑器、拖拽碰撞、选中区域或完整 Godot 节点系统。

### Remix runtime

Remix 由 `source_format: "pngtube_remix_pngremix"` 显式启用。它不走 Plus 的节点树 runtime，保留扁平 layered canvas 绘制路径。

当前能力：

- 后端解析 `sprites_array`、`settings_dict`、`input_array`。
- 输出 `state_catalog`、`hotkeys`、`raw_hotkeys`。
- 输出 `emotion_mappings`。
- `handleLayeredHotkey()` 可切换 mapped state。
- `window.applyEmotion('happy')` 等轻量情绪路径可映射到 layered state。
- 5 个未命名 state 使用 neutral、happy、sad、angry、surprised 的兜底顺序。
- `stateFrameInfo()` 驱动 metadata 可表达的 sprite sheet。
- `stateHasRemixPhysics()`、`layeredPhysicsTransform()`、`physics_v2` 支持 follow mouse、drag、rotation、stretch、animate-to-mouse sheet 等近似行为。
- Remix 子图层的有效 `z_index` 需要沿父链累加后写入 layer 和 `states[]`，避免后发、辫子、饰品等子层使用局部 z 值导致扁平 canvas 排序错层。
- 当存在真实 vertices / triangles / UVs 时，`runtime_features.mesh_deformation` 为 true，`drawLayerMesh()` 启用 affine mesh triangle 绘制。
- 缺少真实几何时保留 `mesh_metadata: true`、`mesh_runtime: false`，并在 `unsupported_features` 中说明原因。

Remix 边界：

- 不运行 PNGTubeRemix / Godot runtime。
- 不复刻 Remix 编辑器交互。
- 不复用 Plus 的 `drawPlusLayerTree()`、`local_position` / `node_origin` transform stack。
- 如果未来要补 Remix 父子 transform 或更完整物理，应新增 Remix 专属 feature flag 或 adapter 语义，并先补浏览器视觉验收。

## 导入输出契约

### Plus `.save`

输出文件：

```text
model.json
idle.png
talking.png
source.save
layers/
metadata.pngtuber-plus.json
```

`metadata.pngtuber-plus.json` 必须包含：

- `adapter_version: 2`
- `runtime: "layered_canvas"`
- `source_format: "pngtuber_plus_save"`
- `state_count: 10`
- `settings.states`
- `hotkeys`
- `toggles`
- `plus_settings`
- `runtime_features.clip_children_rect`
- `layers`

Plus layer/state 必须包含：

- `costumeLayers`
- `showTalk`
- `showBlink`
- `toggle`
- `clipped`
- `parentId`
- `parent_chain`
- `local_position`
- `node_origin`
- `sprite_offset`
- `draw_offset`
- `plus_transform`
- `frames`
- `hframes`
- `animation_speed`
- `source_anim_speed`

多 `.save` 规则：

- 只有一个 `.save`：直接导入。
- 多个 `.save`：优先根目录中与上传文件夹/模型名同名的 `.save`。
- 仍不唯一：返回 `400`，`source_format: "pngtuber_plus_save"`，`warnings` 列出候选 `.save`。

### Remix `.pngRemix`

输出文件：

```text
model.json
idle.png
talking.png
source.pngRemix
layers/
metadata.pngtube-remix.json
```

`metadata.pngtube-remix.json` 必须包含：

- `adapter_version: 2`
- `runtime: "layered_canvas"`
- `source_format: "pngtube_remix_pngremix"`
- `layers`
- `capabilities.speech_layers`
- `capabilities.blink_layers`
- `capabilities.hotkeys`
- `capabilities.motion_layers`
- `capabilities.physics`
- `capabilities.mesh`
- `runtime_features.physics_v2`
- `runtime_features.mesh_deformation`
- `state_catalog`
- `emotion_mappings`
- `hotkeys`
- `raw_hotkeys`
- `settings`
- `raw_settings`
- 每个 layer 与 `states[]` 中用于运行时排序的有效 `z_index`，该值已经包含父级链上的 `z_index`。

失败规则：

- Variant 解析失败归类为 PNGTubeRemix 转换失败。
- 不回退为“缺少 model.json”。
- 导入失败必须清理临时目录。
- 成功导入后必须能通过 `/api/model/pngtuber/models` 列出。

## 模型管理器与页面互斥

PNGTuber 复用现有 `#model-select / live2dModelManager` 模型选择入口，不保留独立 `pngtuber-model-select`、`pngtuberModelManager`、`pngtuberModelDropdown`。

选择 PNGTuber 时：

- `modelSelect` 填充 `/api/model/pngtuber/models`。
- option 必须包含 `data-model-type="pngtuber"`、`data-folder`、`data-url`、`data-pngtuber`。
- PNGTuber change 分支直接调用 `loadSelectedPNGTuberOption(...)`。
- 自动恢复上次 PNGTuber 选择时，也直接 `await loadSelectedPNGTuberOption(...)`，不要只 dispatch suppressed change。
- 选中 PNGTuber 后立即启用保存按钮，不能依赖 Live2D 加载流程。

加载 PNGTuber 时必须多次压制其他 runtime：

```js
hideOtherAvatarRuntimesForPNGTuber();
await window.pngtuberManager.load(config || {});
hideOtherAvatarRuntimesForPNGTuber();
window.pngtuberManager.show();
hideOtherAvatarRuntimesForPNGTuber();
```

任意入口显示一个模型时，必须同步隐藏其他模型和对应按钮。重点入口：

- `static/js/index.js`
- `static/live2d-init.js`
- `static/pngtuber-core.js`
- `static/app-interpage.js`
- `static/app-ui.js`
- `static/app-character.js`

Live2D 防护：

- `showLive2d()` 在 PNGTuber 模式下必须直接 return，并隐藏 Live2D 残留。
- `initLive2DModel()` 在显示 Live2D 容器前必须检查 PNGTuber。
- `ensurePIXIReady()` 前也要再次检查，避免异步等待期间用户切换模型类型。
- 从 PNGTuber 切回 Live2D 时，必须重新触发 Live2D 模型加载链路，不能只恢复 UI。

透明容器交互约束：

- `#pngtuber-container` 应保持 `pointer-events: none`。
- `.pngtuber-image` 仅在可交互状态下 `pointer-events: auto`。
- 锁定时图片也不能接收 pointer events。
- 不要用全局点击拦截或 MutationObserver 去抢主页 UI 事件。

## 保存链路

模型管理页保存 PNGTuber 时，配置合并顺序必须固定：

1. 现有角色配置中的 `_reserved.avatar.pngtuber`
2. 当前模型 option 的 `data-pngtuber`
3. runtime 当前配置 `window.pngtuberManager.config`

runtime config 必须最后覆盖，否则拖拽、缩放、镜像和来源字段容易丢失。

保存 PNGTuber 时：

- `window.hasUnsavedChanges = true`
- `savePositionBtn.disabled = false`
- `markModelChangedForCardFacePrompt()`
- 只在 `window.lanlan_config.model_type === "pngtuber"` 时保存 PNGTuber 布局。
- 不在普通同步函数里写 `window.lanlan_config.model_type = "pngtuber"`。

手机 Web 布局：

- 桌面字段：`scale`、`offset_x`、`offset_y`
- 手机字段：`mobile_scale`、`mobile_offset_x`、`mobile_offset_y`
- 手机判断按 viewport 宽度，优先 `window.isMobileWidth()`，缺失时回退 `window.innerWidth <= 768`。
- 保存时必须携带两套布局字段，不能只提交当前 viewport 字段。

## 回归风险和踩坑记录

已经修复且必须防回归：

- PNGTuber 和 Live2D 同框显示。
- 从 PNGTuber 切回 Live2D 后模型不显示。
- 选择 PNGTuber 后保存按钮不可用。
- PNGTuber 预览位置套用了主页偏移。
- PNGTuber 容器挡住主页聊天框。
- `.pngRemix` 被误报为缺少 `model.json`。
- Plus / Remix 多状态图层重叠。
- One Bounce 失效或误用于所有模型。
- 自动切回上次 PNGTuber 模型时下拉显示正确但 runtime 未加载。
- 手机 Web 下 PNGTuber 继承桌面大偏移导致不可见。
- 主页返回后 PNGTuber 透明容器残留 pointer events。

经验原则：

- 第三方工程是导入源，不是运行源。
- Godot 只作为文件格式来源，不作为运行依赖。
- 新 runtime 能力必须 behind flag、`source_format` 分流或 adapter 版本演进。
- 任意新增 transform、physics、RAF 循环前，必须验证 `setSpeaking(true)`、mouth flap、One Bounce、hide/dispose 清理。
- Plus 专属 runtime 只能由 `source_format: "pngtuber_plus_save"` 启用。
- Remix 专属 physics / mesh 只能由 Remix metadata `runtime_features` 启用。
- 文档、测试和输出契约要同步更新。

## 验收标准

自动验证：

```powershell
node --check static\pngtuber-core.js
node --check static\app-buttons.js
node --check static\js\model_manager.js
.\.venv\Scripts\python.exe -m pytest tests\unit\test_pngtuber_plus_importer.py tests\unit\test_pngtuber_static_contracts.py tests\unit\test_pngtuber_router_delete.py tests\unit\test_model_manager_window_features.py
```

涉及 `.pngRemix` parser 时额外运行：

```powershell
python -m py_compile main_routers\pngtuber_importers\godot_variant.py main_routers\pngtuber_importers\pngtube_remix.py
```

前端或模板改动后：

```powershell
.\build_frontend.bat
```

手动验收：

1. 导入原生 simple package，确认旧格式不回归。
2. 导入 PNGTuber Plus `.save`，确认生成并显示角色。
3. Plus 模型确认 costume hotkey、toggle、说话/眨眼、多帧 sprite sheet、父子 transform、矩形 clip 行为稳定。
4. Plus 包含多个 `.save` 且无法唯一选择时，确认返回 `400` 和候选 `warnings`。
5. 导入 PNGTubeRemix `.pngRemix`，确认生成 `model.json`、`idle.png`、`talking.png`、`source.pngRemix`、`metadata.pngtube-remix.json`，并确认头发/饰品等子图层保留父级继承后的 z-order。
6. 多 state Remix 包确认 hotkey/state 和 `window.applyEmotion('happy')` 映射。
7. 播放 TTS，确认 mouth flap 与 One Bounce 不回归。
8. 检查 `window.pngtuberManager.getDebugState()` 中的 `sourceFormat`、`runtimeFeatures`、`meshRuntime`、`physicsVersion`。
9. PNGTuber -> Live2D -> PNGTuber 循环切换，不同框、不残留、不闪烁。
10. 拖拽、缩放、镜像后保存，刷新后确认 `_reserved.avatar.pngtuber` 保留来源字段和位置字段。
11. 桌面宽度加载 PNGTuber 使用桌面布局。
12. 手机宽度加载同一 PNGTuber 使用 `mobile_*` 布局，角色在视口内可见。
13. 主页返回/恢复 PNGTuber 后，聊天框和主页按钮仍可点击。

## 长期扩展方向

可考虑但不属于当前已完成范围：

- veadotube 真实样本适配。
- Reactive Images / OBS 插件类导入。
- Plus collision polygon / alpha mask clip runtime。
- Remix 父子 transform 专属 runtime。
- 更完整的 Remix / Plus 物理。
- 分层模型可视化调试面板。
- 分层状态按钮或轻量 UI 编辑器。
- PNGTuber 与角色市场、创作者分享、活动角色快速发布联动。
