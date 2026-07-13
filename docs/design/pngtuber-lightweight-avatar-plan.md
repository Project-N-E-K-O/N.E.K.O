# PNGTuber 轻量角色载体技术文档

本文记录 PNGTuber 轻量角色载体的设计、实现经验与当前技术基线。前半部分保留历史方案和踩坑记录，后半部分以 PR #1779 为当前事实来源，整理 PNGTubeRemix `.pngRemix` 转换导入、Godot Variant 解析、依赖边界、包体影响和验收标准。

## PNGTubeRemix runtime 阶段经验补充

### 当前验收基线

当前稳定基线先以“能说话、能弹跳、不破坏 Live2D/VRM/MMD”为准。

已经验证的能力：

- PNGTuber 普通两图模型可以随语音在 `idle` / `talking` 之间切换。
- layered canvas PNGTuber 可以随语音切换闭嘴层和张嘴层。
- 长语音不应一直停在张嘴状态，而应通过轻量 mouth flap 在 `idle` / `talking` 之间循环。
- PNGTubeRemix metadata 中 `current_mo_anim: "One Bounce"` 可以作为说话开口时触发整体弹跳的依据。
- One Bounce 只在 metadata 明确包含 bounce 语义时启用，不应强制应用到所有 PNGTuber 模型。
- PNGTubeRemix layered runtime 支持 `Alt+1` 循环切换表情状态，支持 `Alt+2` 切换当前导入的主 asset action。
- 原 PNGTubeRemix 状态热键（例如 `Ctrl+1/2/3`）只作为 `state_hotkeys` 来源信息保存在 metadata 中，不作为项目运行时热键绑定。
- PNGTubeRemix asset action 的原始 F5/F6 信息保存在 `asset_actions` 中，但项目运行时只用统一的 `Alt+2` 触发。
- `.pngRemix` 导入必须保留任一状态可见的图层，不能只按默认状态过滤；否则表情专用脸部层会缺失。
- layered canvas 绘制顺序必须使用父子链累加后的 `effective_z_index`，而不是只用子图层本地 `z_index`。
- Live2D、VRM、MMD 与 PNGTuber 的显示互斥仍是最高优先级，任何 runtime 动画都不能破坏模型切换和主页返回逻辑。

本轮自动验证结果：

```bash
node --check static/pngtuber-core.js
python -m py_compile main_routers/pngtuber_importers/pngtube_remix.py
pytest tests/unit/test_pngtuber_static_contracts.py tests/unit/test_pngtube_remix_importer.py tests/unit/test_pngtuber_router_delete.py tests/unit/test_config_router_pngtuber_paths.py
```

结果：`35 passed`，仅有外部依赖 deprecation warning。

### PNGTubeRemix 动画来源判断

对真实橘雪梨 `.pngRemix` 样本和导入后的 metadata 观察后，结论是：PNGTubeRemix 模型并不是简单自带一段完整跳跃 GIF 或逐帧跳跃动画。模型文件主要保存三类信息：

- 图层素材：身体、头发、眼睛、嘴巴、手、道具等 PNG 图层。
- 状态配置：不同表情状态下的图层可见性、位置、缩放、旋转、父子关系、嘴巴开闭、眨眼等。
- runtime 参数：例如 `current_mo_anim`、`current_mc_anim`、`bounceGravity`、`bounceSlider`、`xAmp`、`xFrq`、`yAmp`、`yFrq`、`physics`、`stretchAmount`、`rdragStr` 等。

因此，演示中的 `duang~duang~` 更接近“软件 runtime 根据配置实时计算出的效果”，而不是模型包里存在一个可直接播放的完整跳跃动画文件。

已观察到的关键字段：

```json
{
  "current_mo_anim": "One Bounce",
  "current_mc_anim": "Idle",
  "bounceGravity": 575,
  "bounceSlider": 250,
  "xAmp": 5,
  "xFrq": 0.3,
  "yAmp": 5,
  "yFrq": 0.4
}
```

语义判断：

- `current_mo_anim` 可理解为 mouth open animation。`One Bounce` 表示嘴巴打开时触发一次整体弹跳。
- `current_mc_anim` 可理解为 mouth closed 或 character idle animation。`Idle` 表示非说话或常态下的角色动画模式。
- `bounceGravity` 和 `bounceSlider` 更像软件 runtime 的弹跳物理参数，不是图层自身的坐标。
- `xAmp/xFrq/yAmp/yFrq` 可能表示全局或局部待机运动参数，但直接加入全局 transform 曾干扰说话和弹跳主链路，暂不纳入稳定基线。

### 当前 runtime 子集

当前项目只把 PNGTubeRemix runtime 落地为一个保守子集：

- 图层渲染：Canvas layered runtime 根据 metadata 中的 layer、state、zindex、position、scale、rotation 绘制。
- 图层互斥：支持父级可见性继承、说话层继承、眨眼层继承、asset 显隐覆盖，避免多个动作/表情图层重叠。
- 图层层级：导入时计算 `effective_z_index`，运行时优先按 `effective_z_index` 排序；旧 metadata 没有该字段时，前端会按 `parent_id + z_index` 做兼容回退。
- 表情状态：导入时保留任一状态可见的图层，运行时用 `layeredStateIndex` 切换每层对应 state，`Alt+1` 循环切换。
- 动作/资产：导入 `saved_event`、`saved_disappear` 为 `asset_actions`，运行时用 `Alt+2` 切换主 asset action。
- 热键策略：`metadata.hotkeys` 为空，原文件状态热键只保存在 `state_hotkeys` 中，不参与键盘事件绑定。
- 口型：语音开始后启动 mouth flap 定时器，在 `idle` / `talking` 间轻量切换。
- One Bounce：当当前状态的 `current_mo_anim` 包含 `Bounce` 时，在张嘴瞬间触发一次整体 `translateY + scaleX/scaleY` 回弹。
- 清理：语音结束、隐藏 PNGTuber、dispose 时必须清理 mouth timer 和 bounce animation frame。

当前暂缓的能力：

- 全局 Idle motion：读取 `settings.xAmp/xFrq/yAmp/yFrq` 让整个角色持续漂动。曾导致说话和弹跳失效，需在有浏览器运行态调试面板后再恢复。
- 完整物理：`rdragStr`、`dragSpeed`、`stretchAmount`、`bounceGravity` 的完整 Godot/Remix 物理还原。
- mesh：当前不支持 mesh 变形。
- sprite sheet 多帧：运行时已有 `sprite_sheet_animation` feature flag 和帧计算逻辑，但默认不作为所有导入模型的稳定能力打开。
- 原版状态热键 UI：当前产品决策是不接入原版 `Ctrl+数字` 热键；只保留 `Alt+1` / `Alt+2` 两个统一入口。

### 重要回归教训

不要一次性把所有 Remix runtime 参数都接进主渲染链路。

实际发生过一次回归：在已经可用的 mouth flap + One Bounce 基础上，直接加入全局 Idle motion 后，用户观察到“不会开口说话，跳一跳也没了”。随后撤回全局 Idle motion，保留 mouth flap 和 One Bounce，主链路恢复。

因此后续策略必须是：

- 先固化“说话 + One Bounce + 互斥显示”作为稳定基线。
- 新增 runtime 能力必须有开关，默认不影响已稳定能力。
- 每新增一个 runtime 参数，只做一个最小可验证行为。
- 每次新增后都要验证 `setSpeaking(true)`、mouth flap、One Bounce、hide/dispose 清理。
- 不要让全局 transform、RAF 循环或状态重绘影响 `setState('talking')` 的图层筛选。

### 后续 runtime v2 建议

下一阶段不要直接继续叠动画，而是先做可观测性：

- 在开发模式下暴露 `window.pngtuberManager.getDebugState()`。
- 返回当前 `state`、`layeredStateIndex`、`isSpeaking`、`speakingMouthOpen`、`speakingBounceStart`、当前 `current_mo_anim`、当前 `current_mc_anim`。
- 增加只读调试日志或页面调试面板，显示当前渲染层数量、被 ancestor hidden 的层数量、当前 talking/idle 可见层差异。
- 用 Playwright 实际触发 `window.pngtuberManager.setSpeaking(true)`，观察 1-2 秒内 `state` 是否在 `idle/talking` 间变化。
- 再逐步恢复全局 Idle motion，并且必须提供开关，例如 `runtime_features.global_idle_motion`。

建议 runtime v2 的实现顺序：

1. Debug state API。
2. Playwright 运行态验收脚本。
3. 全局 Idle motion behind flag。
4. sprite sheet frame animation。
5. 状态调试/预览 UI；不要恢复原版状态热键绑定。
6. 更完整的 Remix physics。

## 实施经验与问题复盘摘要

本节汇总 PNGTuber 接入过程中的实际经验。PNGTuber 看起来只是图片/GIF 播放，但它进入的是一个已经存在 Live2D、VRM、MMD、模型管理器、多窗口通信、主页返回动画、悬浮按钮和角色配置接口的系统。因此稳定性关键不在单个 `<img>` 能否显示，而在前后端配置一致、运行时互斥、跨页面状态同步和重复 reload 防护。

### 核心结论

- PNGTuber 必须是独立 `model_type: "pngtuber"`，不能作为 Live2D 子格式处理。
- UI 可以复用现有 Live2D 模型选择入口，但逻辑分支必须和 Live2D 完全分开。
- 后端保存正确不代表前端运行时正确；切换问题必须同时查 `page_config`、`window.lanlan_config` 和 DOM computed style。
- 任何入口显示一个模型时，必须同步隐藏其他所有模型及其悬浮按钮。
- PNGTuber 的 runtime 不能污染 `window.lanlan_config.model_type`，否则切回 Live2D 后会被再次改回 PNGTuber。
- 模型管理器保存后可能通过 BroadcastChannel 和 postMessage 同时触发 `reload_model`，必须去重，否则 Live2D 会重复加载并闪烁。

### 模型选择器经验

PNGTuber 最终接入方式是复用现有 `#live2d-model-group / #model-select / live2dModelManager`，不再保留独立的 `pngtuber-model-select`。

约束：

- Live2D 模式下，`modelSelect` 填充 `/api/live2d/models`。
- PNGTuber 模式下，`modelSelect` 填充 `/api/model/pngtuber/models`。
- PNGTuber option 必须包含 `data-model-type="pngtuber"`、`data-folder`、`data-url`、`data-pngtuber`。
- `modelSelect.change` 必须先判断 `currentModelType === "pngtuber"`。
- 命中 PNGTuber 后，只更新 `currentModelInfo`、调用 `window.loadPNGTuberAvatar(config)`、显示 PNGTuber 容器，并启用保存按钮；不能继续落入 Live2D 的模型文件、动作、表情加载流程。

踩坑：如果 PNGTuber 选择后仍走 Live2D 后续流程，会造成保存按钮不可用、预览状态错乱、模型类型回退或同框。

### 前后端一致性经验

一次实际排查中，后端 `/api/config/page_config` 已经返回 Live2D：

```json
{
  "model_type": "live2d",
  "model_path": "/user_live2d/.../model.model3.json"
}
```

但主页仍同时显示 PNGTuber 和 Live2D。这个现象证明保存接口和后端配置已经正确，问题在前端运行时残留。

因此排查顺序应固定为：

1. 查保存请求体是否为预期模型类型。
2. 查角色配置 `_reserved.avatar.model_type`。
3. 查 `/api/config/page_config` 返回的 `model_type`。
4. 查 `window.lanlan_config.model_type`。
5. 查 `#live2d-container`、`#live2d-canvas`、`#pngtuber-container`、`.pngtuber-image` 的 computed style。
6. 查对应 floating buttons、lock icon、return button 是否残留。

### 运行时互斥经验

互斥不能只写在一个地方。以下入口都可能显示或恢复模型：

- `static/js/index.js`：主页初始配置加载。
- `static/live2d-init.js`：Live2D 自动初始化。
- `static/pngtuber-core.js`：PNGTuber 加载、显示、拖动、保存。
- `static/app/app-interpage.js`：模型管理器保存后的主页热重载、隐藏/显示主界面。
- `static/app/app-ui.js`：`showLive2d()`、`showCurrentModel()`、请她离开/回来流程。
- `static/app/app-character.js`：角色切换流程。

经验规则：每个“显示当前模型”的入口都必须同时“隐藏其他模型”。不要假设调用者已经处理过互斥。

### Live2D 入口防护

`showLive2d()` 是共享函数，PNGTuber 模式下如果被其他模块误调用，会重新显示 Live2D。必须加闸门：

```js
if ((window.lanlan_config?.model_type || '').toLowerCase() === 'pngtuber') {
  // hide live2d-container / live2d-canvas / live2d floating UI
  return;
}
```

`initLive2DModel()` 也必须在任何 `live2dContainer.style.display = 'block'` 之前检查 PNGTuber。否则 Live2D 自动初始化异步晚到时，会把已经隐藏的 Live2D 容器重新显示出来。

还需要在 `ensurePIXIReady()` 前再次检查一次，因为用户可能在异步等待期间切换了模型类型。

### PNGTuber 加载防护

只在 `loadPNGTuberAvatar()` 开始时隐藏 Live2D 不够。Live2D/VRM/MMD 的异步初始化或 UI 恢复可能晚到，所以 PNGTuber 加载应多次压制其他运行时：

```js
hideOtherAvatarRuntimesForPNGTuber();
await window.pngtuberManager.load(config || {});
hideOtherAvatarRuntimesForPNGTuber();
window.pngtuberManager.show();
hideOtherAvatarRuntimesForPNGTuber();
```

这能防止 Live2D 初始化、VRM/MMD 渲染或悬浮按钮恢复在中途插队。

### 模型管理器返回主页的关键漏网入口

实际出现过的问题：切换到 PNGTuber 后再切回 Live2D，返回主页时后端配置已经是 Live2D，但页面仍残留 PNGTuber。

根因在 `handleShowMainUI()`：它原先只负责显示当前模型，没有隐藏非当前模型。Live2D 分支只显示 `#live2d-container` 和 `#live2d-canvas`，没有同步隐藏 `#pngtuber-container`、`.pngtuber-image`、`#pngtuber-floating-buttons`、`#pngtuber-lock-icon`、`#pngtuber-return-button-container`。后面的悬浮按钮恢复逻辑还会遍历所有类型，把 PNGTuber 按钮也恢复出来。

修复约束：

- `handleShowMainUI()` 必须计算唯一活跃 UI 前缀：`live2d`、`vrm`、`mmd`、`pngtuber`。
- 显示主页前，先隐藏所有非当前运行时。
- 恢复悬浮按钮时只恢复当前前缀对应的按钮。
- 非当前类型的 return button 必须保持隐藏。

### 重复 reload 防护

模型管理器保存后，主页可能同时收到 BroadcastChannel 和 postMessage fallback 两条 `reload_model`。旧逻辑在 reload 已进行中时，会等待当前 reload 完成，然后递归再执行一次 `handleModelReload()`。

结果是 Live2D 被完整加载两次，产生多次闪烁。

修复约束：

- 为 reload 请求生成 `reloadKey`。
- 如果相同 `reloadKey` 正在进行中，直接返回当前 `window._modelReloadPromise`。
- 不要递归执行同一个 `handleModelReload(targetLanlanName, reloadOptions)`。
- 如果是不同请求，记录为 pending，并在当前 reload 完成后带原参数执行。
- pending reload 必须保留 `targetLanlanName` 和 `reloadOptions`，不能退化成无参数 reload。

### 全局配置污染防护

PNGTuber runtime 不应在普通同步函数中写：

```js
window.lanlan_config.model_type = 'pngtuber'
```

经验：

- PNGTuber 拖动、缩放、状态保存可以保存 `pngtuber` 配置，但不能在非 PNGTuber 当前态下改全局模型类型。
- PNGTuber 自动保存前必须确认 `window.lanlan_config.model_type === 'pngtuber'`。
- 否则切回 Live2D 后，PNG 操作、按钮状态同步或定时保存可能再次污染全局类型。

### 主页交互防护

PNGTuber 容器是全屏 fixed，如果容器本身接收 pointer events，会挡住主页聊天框和按钮。

约束：

- `#pngtuber-container` 应为 `pointer-events: none`。
- `.pngtuber-image` 才设置 `pointer-events: auto`。
- 锁定时图片也要禁用 pointer events。
- 不要用 MutationObserver 或全局点击拦截去抢主 UI 事件。

### 预览位置经验

模型管理器预览和主页展示是不同语义：

- 模型管理器：预览场景，应居中显示。
- 主页：桌宠场景，应按右下角、用户拖动偏移和缩放显示。

runtime 应识别 model manager 页面，例如 `document.body.classList.contains('model-manager-page')`，并使用独立默认定位。不要把主页拖动后的 offset 默认套到模型管理器预览。

### 保存按钮经验

选择 PNGTuber 模型后，保存按钮必须立即可用。PNGTuber 没有 Live2D 的动作、表情、参数加载流程，所以不能依赖 Live2D 加载成功来启用保存。

PNGTuber 分支应执行：

- `window.hasUnsavedChanges = true`
- `savePositionBtn.disabled = false`
- `markModelChangedForCardFacePrompt()`

### DOM 可见性验收清单

切到 PNGTuber 后：

- `#pngtuber-container` 可见。
- `#pngtuber-container .pngtuber-image` 可见且有有效 `src`。
- `#live2d-container` 不可见。
- `#live2d-canvas` 为 `visibility: hidden` 或不能交互。
- VRM/MMD 容器不可见。
- 只显示 PNGTuber 的 floating buttons。

切回 Live2D 后：

- `#live2d-container` 可见。
- `#live2d-canvas` 可见。
- `#pngtuber-container` 为 `display: none` 或 `visibility: hidden`，并带 `hidden`。
- `.pngtuber-image` 不可见或不能接收 pointer events。
- `#pngtuber-floating-buttons`、`#pngtuber-lock-icon`、`#pngtuber-return-button-container` 不显示。
- `window.lanlan_config.model_type === "live2d"`。

### 回归测试必须覆盖

静态契约测试至少覆盖：

- `showLive2d()` 在 PNGTuber 模式下直接 return，并隐藏 Live2D 残留。
- `initLive2DModel()` 在显示 Live2D 容器前检查 PNGTuber。
- `loadPNGTuberAvatar()` 至少在加载前、加载后、显示后调用互斥隐藏逻辑。
- `handleModelReload()` 对相同 in-flight reload 复用 promise，不递归重跑。
- `handleShowMainUI()` 隐藏非当前运行时，只恢复当前模型类型的悬浮按钮。
- PNGTuber runtime 不写 `window.lanlan_config.model_type = 'pngtuber'`。
- 模型管理器不再引用废弃的 `pngtuberModelSelect`、`pngtuberModelManager`、`pngtuberModelDropdown`。

### 实际验证流程建议

每次修复后建议按以下顺序验证：

1. 读取 `/api/config/page_config`，确认后端当前模型类型。
2. 打开主页，读取 `window.lanlan_config.model_type`。
3. 检查 `#live2d-container`、`#live2d-canvas`、`#pngtuber-container`、`.pngtuber-image` 的 computed style。
4. 在模型管理器切换到 PNGTuber，选择模型，保存。
5. 返回主页，确认只显示 PNGTuber。
6. 再切回 Live2D，保存。
7. 返回主页，确认只显示 Live2D，没有 PNGTuber 图片和按钮残留。
8. 观察 Live2D 是否只加载一次，没有多次闪烁。
9. 检查控制台是否出现重复 `reload_model`、重复 `loadModel` 或错误 fallback。

### 最终原则

PNGTuber 的工程风险不在图片渲染，而在和现有高表现力模型系统共存。后续实现必须遵守三个原则：

- 单一事实源：后端 `model_type`、`page_config`、`window.lanlan_config`、DOM 当前可见运行时必须一致。
- 强互斥：任何入口显示一个模型时，必须隐藏其他所有模型及其按钮。
- 防重复：模型保存、跨窗口广播、热重载和返回主页流程必须去重，避免同一模型被连续加载多次。

## 背景

当前项目已经支持 Live2D、MMD、VRM 三类高表现力头像模型。这些模型效果好，但制作和接入成本较高：Live2D 需要模型文件和参数绑定，VRM 需要 3D 模型，MMD 需要模型、材质和动作资源。

维护者希望长期增加一种低成本 PNGTuber 形式，让用户在没有高成本模型资源时，也能用几张截图、立绘、宣传图、PNG 或 GIF 快速创建角色。尤其是最新游戏、番剧或活动角色刚出现时，社区往往还没有 Live2D/VRM/MMD 模型，但用户通常能拿到截图或宣传图。

PNGTuber 的核心价值不是单纯播放 PNG/GIF，而是成为项目里的轻量角色载体：让没有模型的角色也能进入系统，先可用、可聊天、可说话、可分享，后续再升级到高表现力模型。

## 产品定位

PNGTuber 用于覆盖“快速可用”的角色接入场景。

它的作用包括：

- 扩大角色供给：让最新游戏角色、番剧角色、活动角色能用截图或立绘快速进入系统。
- 降低创作门槛：最低只需要一张图片即可上屏，两张图片即可实现说话切换。
- 快速角色原型：用户可以先验证角色人格、声音、提示词和互动体验，之后有模型资源再升级。
- 轻量角色包生态：角色包可以由图片、角色设定、声音配置、提示词、可选表情组成，比完整模型包更容易分享和维护。
- 与高表现力模型互补：PNGTuber 不替代 Live2D/MMD/VRM，而是覆盖低成本、快速创建、快速传播的阶段。

## 模型类型与配置

新增第四类头像类型：

```json
{
  "model_type": "pngtuber"
}
```

PNGTuber 配置保存到角色配置的 `_reserved.avatar.pngtuber` 中：

```json
{
  "_reserved": {
    "avatar": {
      "pngtuber": {
        "idle_image": "/user_pngtuber/character_idle.png",
        "talking_image": "/user_pngtuber/character_talking.png",
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
        "source_type": "screenshot"
      }
    }
  }
}
```

字段说明：

- `idle_image`：默认图，必需。
- `talking_image`：说话图，可选。
- `happy_image`、`sad_image`、`angry_image`、`surprised_image`：预留表情图，可选。
- `scale`：桌面 Web 显示缩放。
- `offset_x`、`offset_y`：桌面 Web 显示偏移。
- `mobile_scale`：手机 Web 显示缩放。旧配置缺失时默认 `min(scale, 1)`，避免桌面大缩放把角色推到手机视口外。
- `mobile_offset_x`、`mobile_offset_y`：手机 Web 显示偏移。旧配置缺失时默认 `0`，以右下锚点作为安全默认位置。
- `mirror`：是否镜像。
- `source_type`：素材来源，可为 `screenshot`、`transparent_asset` 或 `gif`。

## 支持素材

第一阶段支持以下素材格式：

- `.png`
- `.gif`
- `.jpg`
- `.jpeg`
- `.webp`

素材策略：

- 透明 PNG 是推荐格式。
- GIF 可用于待机循环动画或说话动画。
- 普通截图允许裁剪后直接作为 PNGTuber 图使用。
- MVP 不强制自动抠图，先保证“几张截图就能上”跑通。
- 自动抠图、背景去除、AI 生成 talking 图属于后续阶段。

## 资源目录与静态挂载

新增用户 PNGTuber 资源目录：

```text
用户数据目录/pngtuber
```

新增 Web 静态访问前缀：

```text
/user_pngtuber
```

后端职责：

- 创建 PNGTuber 用户资源目录。
- 挂载 `/user_pngtuber` 静态目录。
- 保存和读取角色配置。
- 允许 PNGTuber 相关字段通过角色配置接口。
- 在模型或素材导入校验中允许 PNG/GIF/JPG/JPEG/WebP。

## 运行进程

PNGTuber 不新增独立后端进程。

PNGTuber 不新增常驻 AI 进程。

PNGTuber 不新增渲染子进程。

运行方式：

- PNGTuber 运行在现有前端页面进程中。
- 后端只负责资源目录、静态挂载、配置保存与读取。
- 前端负责图片渲染、状态切换、截图裁剪 UI、角色切换时的显示互斥。
- PNGTuber runtime 是前端内存对象，不是系统进程。

核心前端对象：

```js
window.PNGTuberManager
```

建议方法：

```js
load(config)
setState(state)
show()
hide()
dispose()
```

DOM 容器：

```html
<div id="pngtuber-container"></div>
```

该容器与 Live2D、VRM、MMD 容器同级。

## 负载说明

PNGTuber 的目标是低负载运行。

CPU 负载：

- 空闲时接近 0，只显示静态图片。
- 说话时只切换图片状态。
- 不运行骨骼计算。
- 不运行物理模拟。
- 不运行 Three.js 或 MMD 渲染循环。
- 不需要持续 `requestAnimationFrame`。

GPU 负载：

- 静态 PNG 主要是浏览器合成开销。
- GIF 有浏览器解码和合成开销。
- 不使用 WebGL。
- 不占用 VRM/MMD 的 3D 渲染资源。

内存负载：

- 主要来自图片解码。
- 一张 1024x1024 RGBA 图片解码后约 4MB。
- idle + talking 两张 1024x1024 图片约 8MB。
- 多表情图片按数量线性增加。
- GIF 内存取决于帧数和浏览器解码策略，但总体远低于完整 3D 模型。

启动负载：

- 首次切换到 PNGTuber 时加载 1-2 张图片。
- 可预加载 talking 图，避免首次说话闪烁。
- 不需要初始化 Pixi、Three.js、MMD loader 或 VRM loader。

清理负载：

- 切出 PNGTuber 时隐藏容器。
- 清除状态定时器。
- 释放当前图片引用。
- 不需要 dispose WebGL renderer、mixer、texture、geometry 等重资源。

## 运行时行为

角色切换流程：

1. 读取角色配置。
2. 如果 `model_type === "pngtuber"`，进入 PNGTuber 分支。
3. 隐藏或暂停 Live2D、VRM、MMD 容器。
4. 显示 `#pngtuber-container`。
5. 加载 `idle_image`。
6. 预加载 `talking_image`。
7. 默认进入 idle 状态。

说话状态流程：

1. 默认显示 `idle_image`。
2. 检测到 TTS、assistant speech 或语音活动时切换到 `talking_image`。
3. 语音结束后延迟 120-200ms 回到 idle。
4. 如果没有配置 `talking_image`，保持 `idle_image`。
5. 如果 talking 图无效，回退 idle。

路径处理：

- Windows 本地路径转换为 `/user_pngtuber/<filename>`。
- 以 `/` 或 `http` 开头的路径保持原样。
- 空字符串、`undefined`、`null`、缺失文件视为无效路径。

失败兜底：

- `idle_image` 无效时显示默认 PNGTuber 占位图。
- `talking_image` 无效时回退 `idle_image`。
- PNGTuber 加载失败不能影响 Live2D、MMD、VRM 原有逻辑。

## 模型管理器工作流

模型管理器新增 `PNGTuber` 类型。

提供两个导入入口：

- 从截图创建
- 导入透明图/GIF

从截图创建的 MVP 流程：

1. 用户选择一张或多张图片。
2. 前端显示裁剪界面。
3. 用户框选角色区域。
4. 用户调整缩放、偏移、镜像。
5. 第一张图默认保存为 `idle_image`。
6. 第二张图默认保存为 `talking_image`。
7. 更多图片可手动分配到 happy/sad/angry/surprised。
8. 保存角色配置和图片资源路径。

导入透明图/GIF 的 MVP 流程：

1. 用户选择 PNG 或 GIF。
2. 如果只有一张图，设为 idle。
3. 如果有两张图，默认第一张 idle、第二张 talking。
4. 保存角色配置。
5. 在预览区展示效果。

## 分阶段实现

### Phase 1：MVP

目标是稳定实现“几张截图就能上”。

包含：

- 独立 `pngtuber` 模型类型。
- `/user_pngtuber` 资源挂载。
- idle/talking 图片配置。
- DOM `<img>` 渲染。
- 语音状态切图。
- 模型管理器基础导入。
- 截图裁剪、缩放、偏移、镜像。
- 与 Live2D/VRM/MMD 的切换互斥。
- 失败兜底与默认占位图。

### Phase 2：体验增强

包含：

- 自动抠图或背景去除作为可选能力。
- 更多表情状态。
- 情绪事件到表情图的映射。
- PNGTuber 角色包导入/导出。
- 从 PNGTuber 升级到 Live2D/VRM/MMD 时保留人格、声音、记忆、提示词配置。

### Phase 3：生态化

包含：

- 轻量角色包规范。
- 社区分享入口。
- 热门新角色快速创建模板。
- 可选 AI 辅助生成 talking 图或表情图。
- PNGTuber 与角色市场、创作者分享、活动角色快速发布联动。

## 测试计划

静态契约测试：

- 检查 `model_type: "pngtuber"` 分支存在。
- 检查 `PNGTuberManager` 存在。
- 检查 `#pngtuber-container` 存在。
- 检查 `/user_pngtuber` 静态挂载存在。
- 检查 PNG/GIF/JPG/JPEG/WebP 导入格式允许。

配置测试：

- 只有 `idle_image` 的角色能保存、读取、显示。
- `idle_image + talking_image` 能保存、读取、切换。
- 普通截图资源可以作为 PNGTuber 图片路径保存。
- 非法路径不会导致白屏或中断角色切换。
- 缺失 talking 图时回退 idle。

前端行为测试：

- Live2D -> PNGTuber -> Live2D 切换正常。
- VRM -> PNGTuber -> VRM 切换正常。
- MMD -> PNGTuber -> MMD 切换正常。
- TTS 播放时从 idle 切到 talking。
- TTS 结束后回 idle。
- GIF 能正常显示和循环播放。
- 裁剪后的截图按 scale、offset、mirror 显示。

负载测试：

- PNGTuber 空闲状态无持续 `requestAnimationFrame` 渲染循环。
- 切换到 PNGTuber 后不启动 WebGL renderer。
- 切出 PNGTuber 后旧图片引用和状态定时器被清理。
- 与 Live2D/VRM/MMD 相比，CPU 和 GPU 使用明显更低。

手动验收：

- 用一张游戏截图裁剪创建角色，能显示。
- 用两张截图分别作为 idle/talking，语音播放时能切图。
- 用透明 PNG 创建角色，背景透明显示正常。
- 用 GIF 创建角色，GIF 能动。
- 原有 Live2D、VRM、MMD 角色不受影响。

## 默认假设

- PNGTuber 是独立模型类型，不作为 Live2D 的子格式。
- 第一阶段重点是低成本快速上角色，不追求高表现力动画。
- MVP 不引入自动抠图作为硬依赖。
- 自动背景去除、AI 抠图、自动生成 talking 图属于第二阶段。
- 最低可用标准是一张截图能上屏，两张截图能随语音切换。
- PNGTuber 不新增独立进程，不依赖 WebGL。
## 第三方 PNGTuber 格式兼容计划

### 目标

将 PNGTuber 支持从“项目自定义简化图片包”扩展为三层兼容体系：简单图片包、状态图配置包、分层工程包。这样既保留“几张截图就能上”的低成本 MVP，也能逐步导入社区常见 PNGTuber Plus、PNGTubeRemix、veadotube mini 等真实模型格式。

优先级按已有用户样本和生态常见度排序：

- 保持现有 `model.json + idle/talking` 简化包稳定。
- 优先支持 PNGTuber Plus `.save` 转换导入。
- 支持 PNGTubeRemix `.pngRemix` 格式转换导入，并保留无法解析样本的明确错误。
- 预留 veadotube mini `.veadomini/.veado` 导入。
- 对 Reactive Images / OBS 插件类做“两图导入”，不在第一阶段做完整工程兼容。

### 格式分层

#### Tier 1：Simple PNGTuber Package

继续使用当前项目内部格式：

```json
{
  "model_type": "pngtuber",
  "pngtuber": {
    "idle_image": "idle.png",
    "talking_image": "talking.png"
  }
}
```

用途：

- 几张截图快速创建角色。
- 第三方格式转换后的统一落地格式。
- 当前前端 runtime 的基础输入。

要求：

- 继续支持 `.png`、`.jpg`、`.jpeg`、`.webp`、`.gif`。
- 继续要求根目录存在 `model.json`。
- 继续通过 `/api/model/pngtuber/models` 返回统一 `pngtuber` 配置。
- 不改变现有主页和模型管理器保存逻辑。

#### Tier 2：State-Based PNGTuber

覆盖按“状态图”组织的工具或生态：

- veadotube mini `.veadomini`
- veadotube `.veado`
- Discord Reactive Images 图组
- 普通 idle/talking 图片组

目标能力：

- 识别静音图、说话图、可选眨眼/表情图。
- 转换成项目内部 `model.json`。
- 第一阶段不实现复杂物理、拖拽、骨骼或分层。

导入结果统一生成：

```text
/user_pngtuber/<model_name>/
├─ model.json
├─ idle.png
├─ talking.png
└─ optional expression images
```

#### Tier 3：Layered PNGTuber Project

覆盖分层工程文件：

- PNGTuber Plus `.save`
- PNGTubeRemix `.pngRemix`

这类模型不是两张图，而是多个图层组合：

```text
body / face / eyes / mouth / hair / accessories
position / zindex / parent / showTalk / showBlink / frames
```

短期策略：

- 做转换导入器。
- 合成 `idle.png` 和 `talking.png`。
- 输出现有 Simple Package。
- 保证模型能导入、能显示、能随语音切图。

长期策略：

- 做分层 PNGTuber runtime。
- 前端用 Canvas 或 DOM layer 渲染图层。
- 保留图层位置、父子关系、有效 z、眨眼、说话层、sprite sheet、状态来源信息、asset action 和摆动参数。
- 让 PNGTuber Plus / PNGTubeRemix 的表现力尽量保留。

### 后端导入管线

新增 PNGTuber 导入识别层，不再只判断根目录是否有 `model.json`。

导入入口仍使用现有接口：

```text
POST /api/model/pngtuber/upload_model
```

格式探测顺序：

1. 如果根目录有 `model.json` 且 `model_type === "pngtuber"`，按现有 Simple Package 导入。
2. 如果存在 `.save`，按 PNGTuber Plus 导入器处理。
3. 如果存在 `.pngRemix`，按 PNGTubeRemix 导入器处理。
4. 如果存在 `.veadomini` 或 `.veado`，按 veadotube 导入器处理。
5. 如果只有图片文件，引导为“两图导入”或返回可操作错误。

建议新增内部模块：

```text
main_routers/pngtuber_importers/
├─ __init__.py
├─ simple_package.py
├─ pngtuber_plus.py
├─ pngtube_remix.py
├─ veadotube.py
└─ image_pair.py
```

统一 importer 返回结构：

```python
{
    "success": True,
    "source_format": "pngtuber_plus_save",
    "model_name": "...",
    "output_dir": Path(...),
    "model_json": {...},
    "warnings": []
}
```

后端仍只对外暴露统一模型列表，不把第三方格式细节泄漏给前端主流程。

### PNGTuber Plus `.save` 导入器

第一阶段实现可用转换器。

输入：

```text
*.save
相关 PNG 文件，可选
```

解析规则：

- `.save` 是 JSON。
- 顶层数字 key 视为 layer。
- 读取 `imageData`，base64 解码为 PNG。
- 若 `imageData` 缺失，则尝试读取 `path` 对应外部 PNG。
- 读取 `pos`、`zindex`、`parentId`、`showTalk`、`showBlink`、`frames`。
- 第一阶段只做静态合成，不做完整动画。

合成规则：

- `idle.png`：合成 `showTalk === 0` 和 `showTalk === 1` 的可见层。
- `talking.png`：合成 `showTalk === 0` 和 `showTalk === 2` 的可见层。
- 层排序按 `zindex`，同级保持原始 layer 顺序。
- `parentId/pos` 第一阶段至少支持相对位置累加。
- `showBlink` 第一阶段不进入合成主图，可作为 metadata 保存。
- `frames > 1` 第一阶段取第一帧；后续再支持 sprite sheet 动画。

输出：

```text
model.json
idle.png
talking.png
source.save
metadata.pngtuber-plus.json
```

`metadata.pngtuber-plus.json` 保存原始层信息，方便后续升级到分层 runtime。

### PNGTubeRemix `.pngRemix` 导入器

当前实现已支持可转换导入。`.pngRemix` 本质上是 Godot Variant 二进制数据，后端通过项目内置的轻量 Variant reader 解析出 `sprites_array`、`settings_dict`、`input_array` 等结构，再转换为项目统一的 layered canvas PNGTuber 模型。

输入：

```text
*.pngRemix
```

处理策略：

- 检测 `.pngRemix` 文件，调用 `main_routers/pngtuber_importers/godot_variant.py` 读取 Godot Variant。
- 从 Variant 数据中读取 sprites、image 数据、状态、层级、位置、缩放、旋转、翻转、z_index、嘴巴/眨眼开关、asset toggle 事件和状态 remap 输入信息。
- 只要某个 sprite 在任一 state 中可见，就必须导出该图层；不能只按第 0 状态过滤，否则半睁眼、腮红等表情专用脸部层会丢失。
- 对每个 layer/state 计算 `effective_z_index`。PNGTubeRemix/Godot 的子图层 z 默认相对父级，导出和运行时排序都应优先使用父子链累加后的有效 z。
- 使用 Pillow 解码每层图片并合成 `idle.png`、`talking.png`。
- 导出 `layers/` 下的分层 PNG，写入 `metadata.pngtube-remix.json`，保留状态、`state_hotkeys`、`asset_actions`、settings、motion/physics 标记和原始层信息。
- `metadata.hotkeys` 必须为空；原 PNGTubeRemix 状态热键只进入 `state_hotkeys`，不绑定运行时键盘事件。
- `asset_actions` 由 `saved_event` / `saved_disappear` 转换而来，运行时由项目统一的 `Alt+2` 触发，不直接绑定原 F5/F6。
- 复制原始工程为 `source.pngRemix`，生成统一的 `model.json`，并将 `pngtuber.adapter` 写为 `layered_canvas_v1`。
- 如果 Variant 解析或图层合成失败，返回明确的 PNGTubeRemix 转换错误，不退化成“缺少 model.json”的普通无效包。

Godot 依赖边界：

- 当前 PR 不引入 Godot Engine、Godot 可执行文件、GDScript 运行时或项目级 `project.godot` 依赖。
- Godot 相关代码仅限纯 Python 二进制格式解析，依赖 Python 标准库 `struct`。
- 图片解码、合成和导出依赖项目既有 Python 依赖 `pillow`。
- `.pngRemix` 中的 Godot/Remix 物理、mesh 和复杂运动参数只保存在 metadata 中；运行时仍只启用已验证的 layered canvas、speech layer、blink layer 和 One Bounce 子集。
- 后续如果要恢复完整 Godot/Remix 物理，应继续作为前端 layered runtime 能力增量实现，不应把 Godot Engine 作为桌面端启动或导入的硬依赖。

包体负担与工程标准评估：

- 包体负担很低：新增的是 `godot_variant.py` 这一类纯 Python 解析代码，不新增 Godot 安装包、动态库、可执行文件、导出模板或额外前端 runtime。
- 依赖负担可控：Godot Variant 解析只用 Python 标准库；图片处理复用项目已有 `pillow`，不会改变 `pyproject.toml` 的主依赖集合。
- 启动负担可控：解析器只在上传/导入 `.pngRemix` 时按需执行，不进入桌面端常驻启动链路，也不增加后台进程。
- 打包标准符合当前工程：该实现符合“转换导入第三方格式，最终落地为项目统一 `model.json + assets + metadata`”的既有策略，比直接运行第三方工程更稳定，也更容易测试和回归。
- 风险边界必须保持：后续不能因为文件格式源自 Godot 就引入 Godot Engine 作为硬依赖；如果确需完整物理或 mesh，还应优先在现有 layered canvas runtime 中做可开关的增量实现。

### veadotube 导入器

第一阶段做预留和样本驱动实现。

支持目标：

- `.veadomini`
- `.veado`
- `.zip + yaml + images`

处理策略：

- 如果 `.veadomini` 是 zip，解压到临时目录。
- 查找 `.yaml` 和图片资源。
- 从状态配置里识别 idle/talking/blink/expression。
- 转换成 Simple Package。
- 如果是旧版二进制 `.veadomini` 或未知 `.veado`，返回清晰错误，并提示需要样本继续适配。

### 前端导入体验

模型管理器 PNGTuber 导入入口保持一个，不新增多个按钮。

导入时根据后端返回展示不同提示：

- Simple Package：`PNGTuber 模型导入成功`
- PNGTuber Plus `.save`：`已从 PNGTuber Plus 工程转换导入。当前版本已合成静音/说话图，分层动画将在后续支持。`
- PNGTubeRemix `.pngRemix`：`PNGTubeRemix model imported with layered adapter v1. Speech, blink layers, states, and asset actions are enabled; source state hotkeys are preserved as metadata only.`
- veadotube 未知版本：`已识别 veadotube 模型文件，但该版本格式暂未支持。请提供样本用于适配。`

导入成功后仍走现有流程：

```text
刷新 /api/model/pngtuber/models
选中新导入模型
调用 window.loadPNGTuberAvatar(config)
启用保存设置按钮
```

### 兼容测试计划

新增或扩展：

```text
tests/unit/test_pngtuber_router.py
tests/unit/test_pngtuber_static_contracts.py
tests/unit/test_pngtuber_importers.py
```

单元测试场景：

- Simple Package 原有导入不回归。
- 没有 `model.json` 但有 `.save` 时进入 PNGTuber Plus importer。
- PNGTuber Plus `.save` 能生成 `model.json`、`idle.png`、`talking.png`。
- `.save` 中 `imageData` 可解码。
- `.save` 中缺失外部 PNG 时优先使用 `imageData`。
- `showTalk = 1` 进入 idle 合成。
- `showTalk = 2` 进入 talking 合成。
- `.pngRemix` 能通过 Godot Variant reader 解析并进入 PNGTubeRemix importer。
- PNGTubeRemix `.pngRemix` 能生成 `model.json`、`idle.png`、`talking.png`、`source.pngRemix` 和 `metadata.pngtube-remix.json`。
- PNGTubeRemix metadata 保留 `layers`、`state_hotkeys`、`asset_actions`、`settings`、`capabilities.motion_layers`、`capabilities.physics` 和 `capabilities.mesh`。
- PNGTubeRemix metadata 的 `hotkeys` 为空，`capabilities.hotkeys` 为 `false`；原版 `Ctrl+数字` 不应触发项目运行时表情切换。
- 第 0 状态隐藏、后续状态可见的图层必须被导出到 `layers/`，并在对应 state 中正确显示。
- 每个 state 应包含 `effective_z_index`，合成预览和运行时绘制按有效 z 排序。
- `saved_event` / `saved_disappear` 应转换为 `asset_actions`，并允许 `Alt+2` 触发主动作切换。
- 转换失败的 `.pngRemix` 返回明确 PNGTubeRemix 转换错误，不误报成普通无效包。
- `.veadomini/.veado` 能被识别，未知版本返回明确错误。
- 导入失败时临时目录被清理。
- 所有成功导入最终都能被 `/api/model/pngtuber/models` 列出。

集成测试使用真实样本或脱敏 fixture：

```text
fixtures/pngtuber_plus/夏凌岚.save
fixtures/pngtube_remix/sample.pngRemix
fixtures/simple_pngtuber/model.json
```

手动验收顺序：

1. 导入现有 Simple Package，确认不回归。
2. 导入 `夏凌岚.save` 文件夹，确认能生成并显示角色。
3. 播放 TTS，确认 idle/talking 能切换。
4. 导入 `.pngRemix`，确认能生成并显示角色；如果样本转换失败，错误提示必须指向 PNGTubeRemix 转换问题，不说“缺少 model.json”。
5. `.pngRemix` layered runtime 中按 `Alt+1` 循环表情，按 `Alt+2` 切换动作；`Ctrl+1/2/3` 不应触发表情切换。
6. 导入 veadotube 样本，确认识别路径和提示准确。
7. 从 PNGTuber 切回 Live2D，确认主页不残留 PNGTuber。
8. 从 Live2D 切回 PNGTuber，确认使用用户上次选择的 PNGTuber 模型。

验证命令：

```powershell
node --check static\js\model_manager.js
python -m py_compile main_routers\pngtuber_router.py
python -m py_compile main_routers\pngtuber_importers\godot_variant.py main_routers\pngtuber_importers\pngtube_remix.py
uv run pytest tests\unit\test_pngtuber_router.py tests\unit\test_pngtuber_static_contracts.py tests\unit\test_pngtuber_importers.py
```

前端 JS 或模板改动后运行：

```powershell
.\build_frontend.bat
```

## 当前已完成能力与稳定基线

本节记录 PNGTuber 接入到当前阶段已经实际完成、验证过或需要长期保留的实现经验。文档前半部分仍可作为原始设计计划参考，本节作为后续维护时的当前事实基线。

### 已完成能力

- `model_type: "pngtuber"` 已作为独立头像类型接入，不能作为 Live2D 子格式处理。
- PNGTuber 已复用现有 `#model-select / live2dModelManager` 模型选择入口，不再保留独立的 `pngtuber-model-select`、`pngtuberModelManager`、`pngtuberModelDropdown`。
- PNGTuber 模型列表由 `/api/model/pngtuber/models` 填充到现有 `modelSelect`，option 必须包含 `data-model-type="pngtuber"`、`data-folder`、`data-url`、`data-pngtuber`。
- Simple Package、PNGTuber Plus `.save`、PNGTubeRemix `.pngRemix` 已进入导入/转换体系。Simple Package 仍是项目内部统一落地格式。
- PNGTubeRemix `.pngRemix` 当前通过内置 Godot Variant reader 解析并转换为 `layered_canvas_v1`，不需要安装或启动 Godot Engine。
- 模型管理页已有轻量 PNGTuber 预览控件：`测试说话` 按钮和 `状态预览` 下拉。该面板是导入验收工具，不是完整编辑器，也不接入 Live2D motion/expression 系统。
- PNGTuber runtime 已支持 idle/talking、mouth flap、One Bounce、layered canvas、拖拽/缩放、浮动按钮、锁定/返回按钮和 debug state。
- PNGTubeRemix runtime 已支持 `Alt+1` 循环表情状态和 `Alt+2` 切换主 asset action。
- PNGTubeRemix source state hotkeys 只保存在 `state_hotkeys`，不会绑定原版 `Ctrl+1/2/3`。
- PNGTubeRemix importer 已补齐第 0 状态隐藏但后续状态可见的表情层，避免半睁眼、腮红等脸部层缺失。
- PNGTubeRemix importer 和 runtime 已按 `effective_z_index` 处理父子层级，修正表情/动作切换后的图层顺序。
- `window.pngtuberManager.getDebugState()` 已存在，不要重复设计第二套 debug API。

### 当前稳定验收基线

最近一次相关自动验证基线包括：

```powershell
node --check static\js\model_manager.js
node --check static\pngtuber-core.js
uv run pytest tests\unit\test_pngtuber_static_contracts.py
uv run pytest tests\unit\test_pngtube_remix_importer.py
python -m py_compile main_routers\pngtuber_importers\godot_variant.py main_routers\pngtuber_importers\pngtube_remix.py
```

前端 JS 或模板改动后必须运行：

```powershell
.\build_frontend.bat
```

注意：普通权限构建可能因 `static/yui-origin` 权限失败，需要提升权限重跑。构建输出中的 npm audit 和 chunk size warning 是既有警告，不代表 PNGTuber 修复失败。

## 最新实施经验补充

### 模型管理页自动加载经验

PNGTuber 自动切换到上次选择的模型时，不能只设置下拉值，也不能只依赖 synthetic `change` 事件。实际发生过的问题是：下拉已经显示橘雪莉，但 runtime 没有真正加载，用户必须手动重新选择一次模型才会显示正确状态。

最终约束：

- 自动切到 PNGTuber 时，优先选择用户上次记住的 PNGTuber 模型；没有历史选择时选择第一个可用 PNGTuber option。
- 自动选择后必须直接 `await loadSelectedPNGTuberOption(selectedOption, { markDirty: false })`。
- 手动选择 PNGTuber 时也复用 `loadSelectedPNGTuberOption(...)`，但传入 `markDirty: true` 或等价逻辑。
- `loadSelectedPNGTuberOption(...)` 必须直接 `await window.loadPNGTuberAvatar(pngtuberConfig)`，并 `await loadPNGTuberPreviewControls(pngtuberConfig)`。
- 自动恢复上次选择不标记未保存；用户手动选择模型才标记未保存并启用保存按钮。

该经验的核心是：下拉选中状态和 runtime 加载状态必须由同一条可等待的函数链路维护，不能分散在多个事件副作用里。

### 保存链路经验

PNGTuber 显示偏好保存在角色配置 `_reserved.avatar.pngtuber` 中，不进入 Live2D preferences。后端会整体写回 PNGTuber 配置，因此前端保存请求必须携带完整 PNGTuber config。

模型管理页保存 PNGTuber 时，配置合并顺序必须固定为：

1. 当前 option 的 `data-pngtuber`
2. `currentModelInfo.pngtuber`
3. `window.pngtuberManager.config`

`window.pngtuberManager.config` 必须最后覆盖，保证用户在模型管理页拖拽、缩放、镜像后的 `offset_x / offset_y / scale / mirror` 能保存为最新值。

手机 Web 适配后，PNGTuber 位置和缩放必须分成两套布局字段：

- 桌面 Web 使用 `scale / offset_x / offset_y`。
- 手机 Web 使用 `mobile_scale / mobile_offset_x / mobile_offset_y`。
- `window.pngtuberManager.config` 保存时必须携带完整 PNGTuber config，不能只提交当前布局字段，否则从手机切回桌面或从桌面切回手机会丢失另一套位置。
- 拖拽、滚轮缩放、双指缩放和自动保存都必须写入当前 active layout 对应字段。
- 模型管理页是预览环境，不进入手机布局；即使浏览器宽度很窄，也应继续使用居中预览逻辑。

同时必须保留以下字段，避免 `.save/.pngRemix` 分层模型保存后丢失来源和 metadata：

- `adapter`
- `layered_metadata`
- `source_format`
- `source_type`
- `idle_image`
- `talking_image`
- `drag_image`
- `click_image`

模型管理页不能依赖 `static/pngtuber-core.js::saveCurrentConfig()` 完成偏好保存。runtime 自动保存主要服务主页/运行态交互；模型管理页的“保存设置”按钮必须主动读取 runtime 当前 config。

### 运行态互斥经验

PNGTuber 与 Live2D/VRM/MMD 的互斥是最高优先级。实际排查中出现过后端配置已经是 Live2D，但主页仍残留 PNGTuber 的情况，根因在前端运行态和 DOM 残留，而不是后端保存失败。

约束：

- `loadPNGTuberAvatar()` 应在加载前、加载后、显示后多次隐藏 Live2D/VRM/MMD，防止异步初始化或 UI 恢复插队。
- Live2D 在 PNGTuber 模式下必须跳过默认 fallback 加载，避免 PNGTuber 模式又把 Live2D 重新拉起。
- 从 PNGTuber 切回 Live2D 时，不能只恢复 UI 和 PIXI canvas，必须重新触发 Live2D 模型加载链路。
- `handleShowMainUI()` 必须只恢复当前模型类型的容器和 floating buttons，不能遍历恢复所有类型按钮。
- BroadcastChannel 与 postMessage 可能重复触发 `reload_model`，必须做 in-flight reload 去重，避免 Live2D 重复加载和闪烁。
- PNGTuber runtime 不应在普通同步函数中写 `window.lanlan_config.model_type = 'pngtuber'`，否则切回 Live2D 后可能再次污染全局模型类型。

### 主页面交互经验

PNGTuber 容器是全局 fixed 容器，必须避免挡住主页聊天框和其他按钮。

约束：

- `#pngtuber-container` 应使用 `pointer-events: none`。
- 只有 `.pngtuber-image` 在可交互状态下使用 `pointer-events: auto`。
- 锁定状态下图片也应禁用交互。
- 不要使用全局点击拦截或 MutationObserver 去抢主页 UI 事件。

### 手机 Web 布局经验

PNGTuber 的“手机页面”定义必须跟项目现有 Web 口径一致：按窄 viewport 判断，不按 UA 或物理设备判断。运行时应优先使用 `window.isMobileWidth()`，缺失时才回退到 `window.innerWidth <= 768`。

手机布局只适用于普通 Web 主页，必须排除以下环境：

- 模型管理页：始终作为预览环境，保持居中显示，不使用 `mobile_*` 字段。
- Electron 聊天窗口：不走手机 Web 布局。
- Electron pet 模式：不走手机 Web 布局。

布局读取和写入规则：

- `applyTransform()` 只能读取 active layout 字段，不能固定读取桌面 `scale / offset_x / offset_y`。
- 桌面 active layout 是 `scale / offset_x / offset_y`。
- 手机 active layout 是 `mobile_scale / mobile_offset_x / mobile_offset_y`。
- 旧配置没有 `mobile_*` 时，`mobile_offset_x = 0`、`mobile_offset_y = 0`、`mobile_scale = min(scale, 1)`。
- `resize` 和 `orientationchange` 后必须重新应用 transform，保证从桌面宽度切到手机宽度时立即切换到手机布局。
- 手机拖动或缩放后刷新，应恢复手机位置；再切回桌面刷新，应恢复桌面位置，不能互相污染。

### 调试经验

`window.pngtuberManager.getDebugState()` 是当前统一运行态观察入口。排查时优先查看：

- `modelType`
- `state`
- `isSpeaking`
- `speakingMouthOpen`
- `layeredStateIndex`
- `layerCount`
- `renderedIdleLayerCount`
- `renderedTalkingLayerCount`
- `currentMoAnim`
- `currentMcAnim`
- `bounceActive`
- `timers`

不要在没有运行态证据时继续猜测模型是否加载、是否说话、是否有图层在线。优先用 debug state、DOM computed style 和实际浏览器截图验证。

## 已修复问题与防回归要求

### 已修复问题

- PNGTuber 和 Live2D 同框显示：通过加强运行态互斥、主页恢复逻辑和 Live2D PNGTuber 模式跳过加载修复。
- 从 PNGTuber 切回 Live2D 后模型不显示：通过 Live2D 回切后重新触发 Live2D 模型加载链路修复。
- 选择 PNGTuber 后保存提示不一致：保存成功提示已对齐 Live2D 的“位置和模型设置保存成功”。
- PNGTuber 模型管理页预览位置不对：模型管理页预览和主页展示语义分离，预览页应居中，主页按用户偏好显示。
- PNGTuber 模式主页聊天框不可用：通过容器 pointer events 约束避免 PNGTuber 容器挡住聊天框。
- PNGTuber 导入第三方格式后图层/动作重叠：通过 layered canvas runtime 的可见性、状态和继承规则约束减少多状态叠加。
- PNGTubeRemix `.pngRemix` 误判为缺少 `model.json`：通过 Godot Variant reader 进入专用转换器，成功时生成 `idle.png`、`talking.png`、`metadata.pngtube-remix.json`，失败时返回 PNGTubeRemix 转换错误。
- PNGTubeRemix 表情状态缺脸部层：旧 importer 只导出第 0 状态可见图层，导致第 1/第 2 表情才显示的脸部层缺失；现按任一状态可见导出。
- PNGTubeRemix 表情/动作切换后层级不对：旧排序只看子层本地 `z_index`；现导入和运行时都按父子链累加的 `effective_z_index` 排序，旧 metadata 运行时可回退计算。
- PNGTubeRemix 原版状态热键误绑定：当前产品决策只用 `Alt+1`/`Alt+2`，现 `metadata.hotkeys` 为空，原 `Ctrl+1/2/3` 只保存在 `state_hotkeys` 中作为来源记录。
- PNGTubeRemix F5/F6 action 与项目热键冲突：原 F5/F6 只保存在 `asset_actions` 的来源数据里，运行时统一用 `Alt+2` 切换主 action。
- 长语音导致一直张嘴：通过 mouth flap 在 `idle/talking` 之间轻量切换，避免长语音一直停留在 talking 图。
- One Bounce 失效或误用：仅在 metadata 明确包含 bounce 语义时启用，不强制应用到所有 PNGTuber 模型。
- PNGTuber 偏好保存丢失：模型管理页保存时使用 runtime config 最后覆盖，确保拖拽/缩放/镜像偏好写入 `_reserved.avatar.pngtuber`。
- 切换到 PNGTuber 后下拉显示上次模型但没有自动加载：通过 `loadSelectedPNGTuberOption(...)` 直接 await runtime 加载修复，不再只依赖 suppressed `change` 事件。
- 手机 Web 页面 PNGTuber 可能不可见或只露出一角：通过独立 `mobile_scale / mobile_offset_x / mobile_offset_y` 字段让手机布局不继承桌面大偏移。
- 主页返回/恢复后 PNGTuber 透明容器挡住聊天框和按钮：恢复路径保持 `#pngtuber-container` 为 `pointer-events: none`，只让 `.pngtuber-image` 在可交互状态下接收 pointer events。

### 防回归要求

静态契约测试至少覆盖：

- PNGTuber 不再引用废弃的 `pngtuberModelSelect`、`pngtuberModelManager`、`pngtuberModelDropdown`。
- PNGTuber option 写入 `data-model-type="pngtuber"` 和 `data-pngtuber`。
- `modelSelect` 的 PNGTuber change 分支调用 `loadSelectedPNGTuberOption(...)`。
- 自动回切 PNGTuber 时直接 `await loadSelectedPNGTuberOption(...)`，不要只 dispatch suppressed change。
- PNGTuber 保存分支读取 `window.pngtuberManager.config`，且 runtime config 合并顺序在最后。
- `loadPNGTuberAvatar()` 包含运行态互斥隐藏逻辑。
- Live2D 在 PNGTuber 模式下不会 fallback 加载默认模型。
- 手机 Web 布局使用 `window.isMobileWidth()` 判定，并排除模型管理页、Electron 聊天窗口和 Electron pet 模式。
- `applyTransform()`、拖拽、滚轮缩放、双指缩放和保存逻辑使用 active layout 字段，不固定读写桌面 `scale / offset_x / offset_y`。
- 角色配置接口和 PNGTuber 模型列表接口保留 `mobile_scale / mobile_offset_x / mobile_offset_y`。
- PNGTuber 容器恢复路径不能把 `#pngtuber-container` 改回 `pointer-events: auto`。
- `getDebugState()` 保持可用。
- PNGTubeRemix importer 必须保留任一 state 可见的图层，不得只按默认 state 过滤。
- PNGTubeRemix importer 必须输出 `effective_z_index` / `effective_zindex`，运行时绘制必须优先使用有效 z。
- PNGTubeRemix importer 输出的 `metadata.hotkeys` 必须为空，`state_hotkeys` 可记录原版状态名和原版按键。
- PNGTubeRemix runtime 键盘事件只处理 `Alt+1` 和 `Alt+2`，不得匹配 `metadata.hotkeys` 中的原版状态热键。
- PNGTubeRemix `asset_actions` 必须保留 show/hide sprite ids，`Alt+2` 应切换主 action。

手动验收至少覆盖：

1. 切到 PNGTuber 后自动加载上次选中的橘雪莉，不需要手动重选。
2. 手动选择另一个 PNGTuber 后，切走再切回会自动加载新选择的模型。
3. 拖拽/缩放 PNGTuber 后点击保存，刷新后仍保持位置和大小。
4. `.save/.pngRemix` 分层模型保存后状态预览仍可用，metadata 不丢。
5. `.pngRemix` 模型按 `Alt+1` 循环表情，脸部表情层完整且层级正确；按 `Ctrl+1/2/3` 不切换。
6. `.pngRemix` 模型按 `Alt+2` 切换动作，动作层显隐和层级正确。
7. Live2D -> PNGTuber -> Live2D 不同框、不残留、不异常闪烁。
8. PNGTuber -> Live2D -> PNGTuber 不出现旧 PNGTuber 或旧 Live2D 残留。
9. 桌面宽度加载 PNGTuber，继续使用桌面位置和缩放。
10. 手机宽度加载同一 PNGTuber，默认使用 `mobile_*` 位置和缩放，角色应在视口内可见。
11. 手机拖动/缩放后刷新，恢复手机位置；切回桌面刷新，恢复桌面位置。
12. 主页返回/恢复 PNGTuber 后，聊天框和主页按钮仍可点击，透明容器不能挡住交互。

推荐验证命令：

```powershell
node --check static\js\model_manager.js
node --check static\pngtuber-core.js
uv run pytest tests\unit\test_pngtuber_static_contracts.py
uv run pytest tests\unit\test_pngtube_remix_importer.py
uv run pytest tests\unit\test_pngtuber_router.py tests\unit\test_pngtuber_importers.py
uv run pytest tests\unit\test_pngtuber_router_delete.py tests\unit\test_characters_router_model_settings.py
uv run pytest tests\unit\test_avatar_return_button_idle_tiers_static.py::test_pngtuber_return_restores_pointer_events
python -m py_compile main_routers\pngtuber_importers\godot_variant.py main_routers\pngtuber_importers\pngtube_remix.py
.\build_frontend.bat
```

### 默认假设

- PNGTuber 仍是独立 `model_type: "pngtuber"`。
- 现有 Simple Package 是项目内部统一落地格式，不废弃。
- 第三方格式第一阶段以“转换导入”为主，不直接运行原始工程。
- PNGTuber Plus `.save` 优先级最高，因为已有真实样本且 JSON 可解析。
- PNGTubeRemix `.pngRemix` 已支持转换导入，但仍只落地稳定 layered canvas 子集。
- Godot 只作为 `.pngRemix` 文件格式来源被解析；项目不新增 Godot Engine、Godot CLI 或 GDScript 硬依赖。
- veadotube 需要真实样本确认 `.veadomini/.veado` 版本差异。
- Reactive Images / OBS 插件类不做复杂工程兼容，先作为两图导入场景处理。

## PR #1779 技术文档整理版

### 变更摘要

PR #1779 将 PNGTuber 第三方工程导入从“简单包 + PNGTuber Plus `.save`”扩展到 PNGTubeRemix `.pngRemix`。核心做法不是运行 Godot 工程，而是在后端导入阶段解析 `.pngRemix` 的 Godot Variant 二进制数据，并转换为项目内部统一 PNGTuber 模型格式。

当前结论：

- `.pngRemix` 已支持转换导入，成功后可生成可运行的 PNGTuber 模型。
- Godot 只作为文件格式来源，不作为运行时、打包或启动依赖。
- 输出模型统一落地为 `model.json`、`idle.png`、`talking.png`、`layers/`、`source.pngRemix` 和 `metadata.pngtube-remix.json`。
- 运行时仍使用项目现有 `layered_canvas_v1`，不直接执行 PNGTubeRemix/Godot runtime。

### 目标与非目标

目标：

- 让用户可以上传 PNGTubeRemix `.pngRemix` 工程并导入为 PNGTuber。
- 保留图层、状态、`state_hotkeys`、`asset_actions`、settings、motion/physics 标记等后续可升级信息。
- 项目运行时只绑定统一的 `Alt+1` 表情循环和 `Alt+2` 主动作切换，不绑定原 PNGTubeRemix 状态热键。
- 保持 Live2D、VRM、MMD 与 PNGTuber 的运行时互斥稳定。
- 避免新增重量级依赖，尤其避免把 Godot Engine 带进桌面端包体。

非目标：

- 不支持直接运行 Godot 工程。
- 不引入 Godot Engine、Godot CLI、GDScript runtime、导出模板或 `project.godot`。
- 不在本阶段还原完整 Remix 物理、mesh 变形或复杂运动链路。
- 不改变项目主依赖集合，不为 `.pngRemix` 单独增加 native/runtime 依赖。

### 模块边界

后端导入模块：

- `main_routers/pngtuber_importers/__init__.py`：统一识别第三方 PNGTuber 包格式。
- `main_routers/pngtuber_importers/godot_variant.py`：轻量 Godot Variant 二进制 reader。
- `main_routers/pngtuber_importers/pngtube_remix.py`：`.pngRemix` 转换器，负责解析、合成、导出 metadata。
- `main_routers/pngtuber_importers/pngtuber_plus.py`：PNGTuber Plus `.save` 转换器，作为同类 layered import 参考。

前端运行模块：

- `static/pngtuber-core.js`：统一 PNGTuber runtime，承载 `layered_canvas_v1`、mouth flap、One Bounce、debug state。
- `static/js/model_manager.js`：模型管理页选择、预览、保存和导入后的加载链路。
- `static/app/app-interpage.js`、`static/app/app-ui.js`、`static/live2d-init.js`：跨页面 reload 与 Live2D/PNGTuber 互斥边界。

### 导入数据流

```mermaid
flowchart TD
  A["用户上传 PNGTuber 包"] --> B["解压到临时目录"]
  B --> C{"识别包格式"}
  C -->|"model.json"| D["Simple Package 导入"]
  C -->|".save"| E["PNGTuber Plus importer"]
  C -->|".pngRemix"| F["PNGTubeRemix importer"]
  F --> G["GodotVariantReader 解析 Variant"]
  G --> H["读取 sprites / states / settings / state hotkeys / asset events"]
  H --> I["Pillow 解码图层并合成 idle/talking"]
  I --> J["导出 layers/ 与 metadata.pngtube-remix.json"]
  J --> K["生成 model.json"]
  K --> L["/api/model/pngtuber/models 可列出"]
  L --> M["前端 loadPNGTuberAvatar 使用 layered_canvas_v1"]
```

### `.pngRemix` 输出契约

成功转换后，目标模型目录至少包含：

```text
model.json
idle.png
talking.png
source.pngRemix
metadata.pngtube-remix.json
layers/
```

`model.json` 关键字段：

```json
{
  "model_type": "pngtuber",
  "source_format": "pngtube_remix_pngremix",
  "pngtuber": {
    "idle_image": "idle.png",
    "talking_image": "talking.png",
    "layered_metadata": "metadata.pngtube-remix.json",
    "adapter": "layered_canvas_v1",
    "source_type": "pngtube_remix_pngremix"
  }
}
```

`metadata.pngtube-remix.json` 至少应表达：

- `source_format: "pngtube_remix_pngremix"`
- `runtime: "layered_canvas"`
- `capabilities.speech_layers`
- `capabilities.blink_layers`
- `capabilities.hotkeys: false`
- `capabilities.motion_layers`
- `capabilities.physics`
- `capabilities.mesh`
- `layers`
- `hotkeys: []`
- `state_hotkeys`
- `asset_actions`
- `settings`
- 每层或每个 state 的 `effective_z_index`，用于还原 PNGTubeRemix/Godot 父子相对 z 顺序。
- 任一 state 可见的图层都应存在于 `layers/`，即使默认 state 不显示。

### Godot 依赖评估

本 PR 引入的是 Godot Variant 文件格式解析，不是 Godot 引擎接入。

包体影响：

- 不新增 Godot 安装包。
- 不新增 Godot 可执行文件。
- 不新增 Godot native 动态库。
- 不新增 GDScript runtime。
- 不新增导出模板。
- 不新增前端渲染 runtime。

依赖影响：

- `godot_variant.py` 仅依赖 Python 标准库 `struct`。
- `.pngRemix` 图片解码与合成复用项目已有 `pillow`。
- 不需要修改 `pyproject.toml` 的主依赖集合。

启动影响：

- Variant reader 只在上传/导入 `.pngRemix` 时按需运行。
- 不进入应用启动链路。
- 不创建后台进程。
- 不影响 Live2D、VRM、MMD 的默认加载路径。

工程标准结论：

- 符合项目“转换导入第三方格式，统一落地内部模型格式”的工程策略。
- 符合包体控制标准，因为没有引入重量级 runtime 或 native 依赖。
- 符合可维护性标准，因为解析、转换、运行时显示分层清晰，可通过单元测试覆盖。
- 后续如需完整物理或 mesh，应继续在 `layered_canvas_v1` 或后续 adapter 中做可开关增量，不应把 Godot Engine 升级为硬依赖。

### 运行时能力边界

已启用：

- idle/talking 切换。
- speech layer / blink layer。
- mouth flap。
- One Bounce。
- layered canvas 渲染。
- 模型管理页状态预览。
- `Alt+1` 循环 PNGTubeRemix states。
- `Alt+2` 切换主 `asset_actions`。
- `effective_z_index` 层级排序。
- `window.pngtuberManager.getDebugState()`。

保留但暂不完整执行：

- source state hotkeys：仅作为 `state_hotkeys` 元数据保留，不作为运行时键盘绑定。
- motion layer 参数。
- physics 标记。
- mesh 标记。
- sprite sheet 多帧动画。
- 完整 Godot/Remix 物理参数。

### 错误处理标准

- `.pngRemix` 解析失败时，错误应归类为 PNGTubeRemix 转换失败。
- 不应回退为“缺少 model.json”。
- 导入失败应清理临时目录。
- 成功导入后，模型必须能通过 `/api/model/pngtuber/models` 列出。
- 保存 `.pngRemix` 分层模型后，`adapter`、`layered_metadata`、`source_format`、`source_type`、`idle_image`、`talking_image` 不应丢失。

### 验收标准

自动验证：

```powershell
python -m py_compile main_routers\pngtuber_importers\godot_variant.py main_routers\pngtuber_importers\pngtube_remix.py
node --check static\pngtuber-core.js
node --check static\js\model_manager.js
uv run pytest tests\unit\test_pngtuber_router.py tests\unit\test_pngtuber_importers.py tests\unit\test_pngtuber_static_contracts.py
```

前端或模板改动后：

```powershell
.\build_frontend.bat
```

手动验收：

1. 导入 Simple Package，确认旧格式不回归。
2. 导入 PNGTuber Plus `.save`，确认仍能生成并显示角色。
3. 导入 PNGTubeRemix `.pngRemix`，确认生成 `model.json`、`idle.png`、`talking.png`、`metadata.pngtube-remix.json`。
4. `.pngRemix` metadata 的 `hotkeys` 为空，`state_hotkeys` 只记录来源状态热键；按 `Ctrl+1/2/3` 不应切换状态。
5. 在模型管理页切换 `idle/talking` 和状态预览，确认 layered metadata 生效。
6. 在运行态按 `Alt+1` 循环表情，按 `Alt+2` 切换动作，表情层和动作层层级正确。
7. 播放 TTS，确认 mouth flap 与 One Bounce 不回归。
8. PNGTuber -> Live2D -> PNGTuber 循环切换，不同框、不残留、不闪烁。
9. 拖拽、缩放、镜像后保存，刷新后确认 `_reserved.avatar.pngtuber` 保留来源字段和位置字段。

### 维护原则

- 继续把第三方工程当作导入源，不当作运行源。
- 继续把 Godot 限定为 Variant 文件格式解析，不扩展为引擎依赖。
- 新 runtime 能力必须 behind flag 或 adapter 版本演进，不直接影响稳定基线。
- 任意新增 transform、physics、RAF 循环前，必须验证 `setSpeaking(true)`、mouth flap、One Bounce、hide/dispose 清理。
- 文档、测试和输出契约要同步更新，避免实现状态和维护说明脱节。
