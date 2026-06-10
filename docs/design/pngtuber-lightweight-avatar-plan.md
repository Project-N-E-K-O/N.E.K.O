# PNGTuber 轻量角色载体接入计划

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
- `static/app-interpage.js`：模型管理器保存后的主页热重载、隐藏/显示主界面。
- `static/app-ui.js`：`showLive2d()`、`showCurrentModel()`、请她离开/回来流程。
- `static/app-character.js`：角色切换流程。

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
- `scale`：显示缩放。
- `offset_x`、`offset_y`：显示偏移。
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
- 支持 PNGTubeRemix `.pngRemix` 格式识别，并逐步实现转换。
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
- 保留图层位置、父子关系、zindex、眨眼、说话层、sprite sheet、摆动参数。
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

第一阶段先做格式识别和温和失败。

输入：

```text
*.pngRemix
```

处理策略：

- 检测 `.pngRemix` 文件。
- 扫描内嵌 PNG signature，尝试提取 sprites。
- 尝试识别 `sprites_array`、`mouth`、`position`、`scale`、`rotation` 等结构。
- 如果无法稳定还原 idle/talking，返回明确提示：已识别 PNGTubeRemix 模型，但当前版本只能识别格式，暂不能完整转换；请提供导出的静音/说话图，或等待 `.pngRemix` 转换支持。

第二阶段目标：

- 根据 PNGTubeRemix 源码确认数据结构。
- 实现 sprite 提取。
- 合成 idle/talking。
- 保存原始 `.pngRemix` 和转换 metadata。

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
- PNGTubeRemix `.pngRemix` 可识别但不可转换：`已识别 PNGTubeRemix 模型，但当前版本暂不能完整转换 .pngRemix。请使用 PNG/GIF 图组或等待兼容更新。`
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
- `.pngRemix` 能被识别为 PNGTubeRemix。
- 未完成转换的 `.pngRemix` 返回明确错误，不误报成普通无效包。
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
4. 导入 `.pngRemix`，确认错误提示准确，不说“缺少 model.json”。
5. 导入 veadotube 样本，确认识别路径和提示准确。
6. 从 PNGTuber 切回 Live2D，确认主页不残留 PNGTuber。
7. 从 Live2D 切回 PNGTuber，确认使用用户上次选择的 PNGTuber 模型。

验证命令：

```powershell
node --check static\js\model_manager.js
python -m py_compile main_routers\pngtuber_router.py
uv run pytest tests\unit\test_pngtuber_router.py tests\unit\test_pngtuber_static_contracts.py tests\unit\test_pngtuber_importers.py
```

前端 JS 或模板改动后运行：

```powershell
.\build_frontend.bat
```

### 默认假设

- PNGTuber 仍是独立 `model_type: "pngtuber"`。
- 现有 Simple Package 是项目内部统一落地格式，不废弃。
- 第三方格式第一阶段以“转换导入”为主，不直接运行原始工程。
- PNGTuber Plus `.save` 优先级最高，因为已有真实样本且 JSON 可解析。
- PNGTubeRemix `.pngRemix` 优先做识别和错误提示，再做完整转换。
- veadotube 需要真实样本确认 `.veadomini/.veado` 版本差异。
- Reactive Images / OBS 插件类不做复杂工程兼容，先作为两图导入场景处理。
