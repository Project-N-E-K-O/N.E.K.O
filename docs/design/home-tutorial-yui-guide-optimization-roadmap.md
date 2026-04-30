# 首页教程 Yui 入场苏醒交互优化路线图

## 1. 文档目的

本文描述“新手引导开始前，角色以苏醒仪式入场”的实现方案。

这里的目标不是重写首页教程，也不是新增一套独立启动页，而是在当前 N.E.K.O 已有的首页、Live2D、存储启动屏障和 Yui Guide 演出层之上，补一段短、稳、可跳过的角色入场交互，让第一次引导从“教程开始了”变成“她醒过来并看向我”。

本文只吸收需求里的体验思路，不采用其中示例代码和具体实现写法。最终实现必须以当前仓库真实代码为准。

---

## 2. 当前项目基线

### 2.1 首页脚本加载现实

首页入口是 [templates/index.html](../../templates/index.html)，不是 React/Vue 单页应用。当前关键加载顺序是：

- `static/app-storage-location.js` 最早加载，用于建立存储位置启动屏障；
- Live2D SDK 与 `static/live2d-core.js` / `static/live2d-emotion.js` / `static/live2d-model.js` / `static/live2d-init.js` 先加载；
- 主业务脚本和聊天窗脚本随后加载；
- 最后加载 `static/yui-guide-steps.js`、`static/yui-guide-overlay.js`、`static/yui-guide-page-handoff.js`、`static/yui-guide-director.js`、`static/universal-tutorial-manager.js`。

因此入场交互应挂在 Yui Guide 演出层启动阶段，而不是抢在存储位置屏障或 Live2D 初始化之前执行。

### 2.2 存储启动屏障

`static/live2d-init.js` 已经等待 `window.__nekoStorageLocationStartupBarrier`。这意味着首次存储位置选择、迁移、恢复期间，Live2D 不应提前苏醒。

入场交互必须遵守：

- 存储位置 overlay 未释放时不启动；
- `#storage-location-overlay` 可见时直接跳过 wakeup，不叠加第二层遮罩；
- 存储流程完成后，由正常首页教程生命周期触发入场。

当前方案明确撤销 M1 遮罩、毛玻璃和粒子层。存储流程结束后不做视觉承接遮罩，避免把安全启动闸门和情感演出耦合在一起，也避免 Live2D 动作被 blur / overlay 遮住。

### 2.3 Live2D 参数控制现实

当前 Live2D 管理代码已经存在参数覆盖经验：

- [static/live2d-emotion.js](../../static/live2d-emotion.js) 能读取和写入 `internalModel.coreModel` 参数；
- [static/live2d-model.js](../../static/live2d-model.js) 已经安装口型、眨眼、视线微动相关的 update 覆盖；
- [static/live2d-init.js](../../static/live2d-init.js) 在 `onModelReady` 后恢复待机动作。

所以“苏醒”不应再做零散的外部定时器强写参数，而应作为一个有生命周期的短期参数覆盖器，和现有口型/眨眼/视线机制明确避让、交接。

额外注意：当前 `YuiGuideDirector` 已有教程正脸锁机制，会通过 `window.nekoYuiGuideFaceForwardLock` 和 `window.mouseTrackingEnabled = false` 暂停 Live2D / VRM / MMD 的鼠标跟踪。苏醒交互必须复用这条锁。新手引导期间模型不跟踪真实鼠标，这是硬约束；苏醒阶段只允许使用脚本化的短期视线/头部参数，不允许重新开启鼠标追踪。

### 2.4 Yui Guide 现有边界

当前教程演出边界已经冻结：

- `UniversalTutorialManager` 负责教程骨架、步骤、高亮、跳过、完成状态；
- `YuiGuideDirector` 负责演出编排；
- `YuiGuideOverlay` 负责教程演出 DOM、遮罩、气泡、ghost cursor；
- `YuiGuideStepsRegistry` 负责场景注册。

入场交互应归 `YuiGuideDirector` 管，Live2D 参数动作归 `YuiGuideWakeup` 管。当前不新增 wakeup 表现 DOM，不把逻辑塞进 `UniversalTutorialManager`。

---

## 3. 体验目标

第一版入场体验建议控制在 3.4 到 4.2 秒内：

1. 首页主 UI 与 Live2D 模型保持可见，不加 wakeup 遮罩；
2. Live2D 模型短暂处于闭眼、低头、安静状态；
3. 模型缓慢睁眼、抬头，视线脚本化回到正面；
4. 在睁眼抬头完成后，再触发实际存在的右手打招呼参数；
5. 入场动作完整结束后，才交给 `intro_basic` 的气泡、语音和后续 Yui Guide 流程。

用户感知上，这不是“加载动画”，而是“角色刚醒来，注意到了我”。

---

## 4. 非目标与禁止项

第一版不做以下事情：

- 不控制系统真实鼠标；
- 不调用 Agent / Computer Use / `pyautogui`；
- 不在存储位置流程还未放行时启动；
- 不为默认模型硬编码独占动作文件；
- 不要求每个模型必须提供 `WakeUp.motion3.json`；
- 不把入场做成不可跳过的强制动画；
- 不新增 wakeup 专用遮罩、毛玻璃、粒子或扫描线；
- 不在 `UniversalTutorialManager` 中加入大量 Live2D 参数细节；
- 不影响 VRM/MMD 初始化、切换和已存在的待机恢复逻辑。

如果当前活跃模型不是 Live2D，第一版直接降级进入 Yui Guide 气泡，不播放 wakeup 视觉层。

---

## 5. 推荐实现结构

### 5.1 新增模块

建议新增：

- `static/yui-guide-wakeup.js`

不新增 `static/css/yui-guide-wakeup.css`，也不在 `static/css/yui-guide.css` 中加入 wakeup stage / backdrop / particle 样式。当前 wakeup 是纯脚本化 Live2D 参数 prelude。

全局导出建议：

```text
window.YuiGuideWakeup
```

最小职责：

- 判断当前是否支持 Live2D 苏醒；
- 安装/移除短期 Live2D 参数覆盖；
- 复用教程正脸锁，确保入场和后续引导期间模型不跟踪真实鼠标；
- 支持取消、跳过、异常恢复；
- 支持 Live2D 短等待后快速降级；
- 向 Director 返回成功、跳过、降级或失败结果。

### 5.2 Director 接入点

`YuiGuideDirector.startPrelude()` 是最合适的入口。

推荐顺序：

1. Director 收到 `startPrelude()`；
2. 检查 `intro_basic` 是否需要播放；
3. 启用或确认教程正脸锁，暂停 Live2D / VRM / MMD 鼠标跟踪；
4. 检查存储位置 overlay、reduced motion、Live2D 支持状态；
5. 调用 `YuiGuideWakeup.run()`；
6. 无论成功、降级或失败，都继续进入现有 `intro_basic` 演出；
7. 如果用户点击跳过，则 `skip()` 统一取消 wakeup、释放参数覆盖。

不要在 `live2d-init.js` 的 `onModelReady` 中直接自动播放完整苏醒。`onModelReady` 可以作为“模型已具备苏醒条件”的信号，但是否播放应由教程生命周期决定，否则非教程场景也会突然入场。

`YuiGuideWakeup.run()` 不能长时间等待 Live2D。超过短等待预算仍没有拿到可用 Live2D 模型或必要参数时，返回类似 `{ result: 'fallback', reason: 'live2d_unavailable' }`，让教程直接进入 `intro_basic`，不能让苏醒动画变成启动挂起点。

### 5.3 无遮罩表现边界

当前实现不创建以下节点：

```text
.yui-guide-wakeup-stage
.yui-guide-wakeup-backdrop
.yui-guide-wakeup-particles
.yui-guide-wakeup-vignette
```

原因是入场动作的主体是模型本身。任何 blur、毛玻璃或暗场遮罩都会降低“闭眼、抬头、挥手”这些细节的可见性，并且容易和现有存储 overlay、教程 overlay、Electron 透明窗口合成产生冲突。

`YuiGuideWakeup.run()` 开始前和运行期间需要主动移除阻挡性的旧节点或通用教程根层，包括 `#yui-guide-overlay`、`.yui-guide-wakeup-stage`、`.yui-guide-wakeup-backdrop` 和 `.yui-guide-wakeup-particles`，并清理 `body.yui-taking-over` / ghost cursor 状态。这样能保证苏醒阶段屏幕上只有真实模型动作，不被 M1 残留遮罩或通用教程 overlay 挡住。

后续如果要重新加入视觉包装，必须另开设计评审，不作为 M1/M2 默认范围。

---

## 6. Live2D 苏醒控制策略

### 6.1 参数候选

第一版只碰低风险参数：

- `ParamEyeLOpen`
- `ParamEyeROpen`
- `ParamAngleX`
- `ParamAngleY`
- `ParamAngleZ`
- `ParamEyeBallX`
- `ParamEyeBallY`

针对当前默认模型 `static/yui_default/yui_default.model3.json`，不能假设存在 motion 文件。该模型的 `FileReferences.Motions` 为空，打招呼必须优先基于实际存在的参数做短期覆盖：

- `Param75`：`【开关】右-挥手`
- `Param90`：`右小臂-动画`
- `Param92`：`右手-动画`
- `Param95`：`右手-摆手动画`

参数存在性必须运行时检测。不存在就跳过，不报错。

不要碰：

- 可见性/透明度参数；
- 嘴型参数，避免和 TTS 口型同步冲突；
- 用户保存的常驻表情参数；
- 物理参数或模型缩放/位置参数。

### 6.2 生命周期

推荐状态机：

```text
idle
  -> preparing
  -> holding_sleep_pose
  -> waking
  -> handoff_to_idle
  -> done
```

异常或跳过统一进入：

```text
cancelled -> restore -> done
```

关键要求：

- `run()` 开始前记录被接管参数的当前值；
- `run()` 开始前扫描参数存在性，并把扫描结果限定在本次 session 内使用；
- 每帧写入只发生在 wakeup active session 内；
- `destroy()` 必须恢复或平滑交还参数；
- 模型被切换或销毁时立即停止；
- Director `skip()`、`abortAsAngryExit()`、`destroy()` 都必须调用清理。
- 第三方模型缺少部分参数时静默跳过对应参数，不能报错阻断教程。

`handoff_to_idle` 阶段必须做权重衰减，不要直接停止参数覆盖。建议用 300 到 500ms 的 fade-out，把 wakeup 计算值按权重逐步衰减到当前 Live2D 自动更新后的值。这样可以避免苏醒结束瞬间眼睛、眼球或头部角度跳变。

### 6.3 和现有 update 覆盖的关系

当前 `static/live2d-model.js` 已经覆盖 `motionManager.update` 和 `coreModel.update`。新实现不要再直接替换这些方法。

实际实现应在 `Live2DManager` 上增加一个“临时姿态覆盖槽”，由现有 `coreModel.update` 覆盖在调用原始 update 之前的最后写入点执行。不要只依赖 wakeup 模块监听 PIXI ticker 逐帧写参数；ticker 写入如果发生在 `coreModel.update()` 之后，当前帧不会参与 Cubism 顶点计算，下一帧又可能被正脸锁或 idle 覆盖，视觉上就会表现为头和眼睛没有动。

必须满足：

- `YuiGuideWakeup` 不直接给 `coreModel.update = ...` 赋值；
- 不覆盖 `_origMotionManagerUpdate`、`_origCoreModelUpdate`；
- 不和 `_autoEyeBlinkEnabled`、`_updateRandomLookAt()` 长期竞争；
- 入场结束后恢复自动眨眼和视线微动。
- 入场和教程期间保持 `window.nekoYuiGuideFaceForwardLock === true`，不调用 `model.focus(pointer.x, pointer.y)`，也不恢复鼠标追踪。

wakeup 模块仍可使用 `requestAnimationFrame` 或 PIXI ticker 作为 session 计时器，但真实参数写入应通过临时姿态覆盖槽发生在渲染前。实现必须用 session id / source id 防止旧 session 写到新模型。

### 6.4 优先级

苏醒期间的参数优先级建议为：

1. 模型基础 motion 正常 update；
2. 已有口型/常驻表情逻辑正常执行；
3. 教程正脸锁暂停真实鼠标跟踪，并将外部 focus target 归零；
4. wakeup 对眼睛和头部做最后一层短期覆盖；
5. `handoff_to_idle` 用权重衰减交还参数；
6. wakeup 结束后恢复给 idle motion、自动眨眼、随机视线，但仍由 Director 决定教程正脸锁何时释放。

这样能保证苏醒看起来稳定，同时不会破坏模型本身的待机生命感。

---

## 7. 与教程流程的关系

### 7.1 场景注册

不建议把入场做成一个新的普通教程 step。它更像 `intro_basic` 的 prelude 动画。

可选做法：

- 在 `static/yui-guide-steps.js` 的 `intro_basic.performance` 中新增轻量标记，例如 `wakeup: true`；
- 或在 Director 内部约定 `startPrelude()` 首次进入 home 时尝试 wakeup。

如果要新增字段，需先更新冻结说明或补一段字段说明，避免破坏 `YuiGuideStep` 契约。

### 7.2 完成态

入场动画只应在首页教程真正要展示时播放：

- 自动首次教程：播放；
- 用户手动重新打开教程：可播放，但应允许后续关闭；
- 已完成教程的普通启动：不播放；
- 存储位置流程阻断期间：不播放。

不要把它绑定到“每次 Live2D 模型加载完成”。

多窗口场景需要避免重复苏醒。建议使用一个短 TTL 的 localStorage lease，例如 `neko_yui_guide_wakeup_lock`，记录播放窗口、时间戳和 session id。另一个窗口检测到有效 lease 时，应跳过 wakeup 或走快速降级，只进入教程气泡。窗口关闭、跳过或异常结束时清理 lease；lease 过期后允许下一次正常播放。

---

## 8. i18n 与用户可见内容

第一版入场动画可以没有文字。如果需要短句，例如“正在唤醒 Yui”或跳过提示，必须走 i18n。

当前项目实际 locale 是 8 个：

- `en`
- `es`
- `ja`
- `ko`
- `pt`
- `ru`
- `zh-CN`
- `zh-TW`

新增 key 必须同步所有 locale，并验证 JSON 可解析、key 集合一致。不要只在 JS 里写中文 fallback。

---

## 9. 可访问性与性能

### 9.1 reduced motion

检测 `prefers-reduced-motion: reduce` 时：

- 不创建 wakeup 视觉 DOM；
- Live2D 只做很短的睁眼/抬头或直接跳过参数动画；
- 总时长控制在 600ms 左右。

### 9.2 性能预算

建议预算：

- 入场总时长：3.4 到 4.2 秒；
- wakeup DOM 数量：0；
- 不使用 wakeup 专用 box-shadow、blur、backdrop-filter、粒子动画；
- 参数更新随现有渲染帧走，不额外开多个 interval。

### 9.3 失败降级

任何一步失败都不能阻断教程：

- Live2D 未就绪：直接进入 `intro_basic`；
- Live2D 超过短等待预算未 ready：返回 fallback 并继续 `intro_basic`；
- 参数不存在：跳过对应参数；
- 模型切换：取消入场并清理；
- 用户跳过：立即清理并标记教程结束；
- 存储 overlay 再次出现：取消入场。
- 另一个窗口持有有效 wakeup lease：跳过本窗口 wakeup，直接进入教程气泡。

---

## 10. 实施阶段

### M1：无 DOM prelude 接入

范围：

- 新增 `static/yui-guide-wakeup.js`；
- 在 Director `startPrelude()` 中调用 wakeup，并等待其返回后再进入 `intro_basic`；
- 支持跳过、销毁、reduced motion；
- 支持存储 overlay 可见时不启动；
- Live2D 不可用时快速 fallback；
- 不创建 wakeup overlay DOM；
- wakeup 期间主动移除 `#yui-guide-overlay` 和旧 wakeup stage，避免全屏层阻挡模型；
- 不碰 Live2D 参数。

验收：

- 首页首次教程前会经过 wakeup 生命周期，但无视觉遮罩；
- 存储位置 overlay 可见时直接 skipped；
- 跳过按钮能立即结束；
- wakeup 运行中 DOM 中没有 `#yui-guide-overlay` 或 `.yui-guide-wakeup-stage` 阻挡模型；
- `static/css/yui-guide.css` 中没有 wakeup stage / backdrop / particle 样式；
- Playwright 或浏览器 smoke 确认没有 `.yui-guide-wakeup-stage`。

### M2：Live2D 参数苏醒

范围：

- 接入当前 Live2D 模型参数检测；
- 实现短期眼睛/头部/视线覆盖；
- 基于 `yui_default.cdi3.json` 中真实存在的 `Param75` / `Param90` / `Param92` / `Param95` 做右手打招呼；
- 保证打招呼发生在睁眼、缓慢抬头之后；
- 入场结束后通过 300 到 500ms 权重衰减交回 idle、眨眼、视线微动；
- 复用教程正脸锁，整个新手引导期间不跟踪真实鼠标；
- 支持 Live2D ready 短等待超时 fallback；
- 模型切换和页面销毁时清理。

验收：

- 默认 Live2D 模型能闭眼低头后睁眼抬头；
- 默认 Live2D 模型在睁眼抬头后再右手打招呼；
- `intro_basic` 气泡和语音在上述动作结束后才开始；
- 苏醒结束时眼睛、眼球、头部无明显跳变；
- 不影响口型同步；
- 不影响用户保存的模型参数；
- 新手引导期间移动真实鼠标不会改变模型视线追踪；
- 连续刷新、跳过、切换模型无残留姿态。

### M3：体验打磨与指标

范围：

- 增加轻量体验指标记录，复用 `homeTutorialExperienceMetrics`；
- 记录 wakeup played/skipped/fallback/error；
- 增加多窗口 wakeup lease，避免多个首页同时播放苏醒；
- 根据真实体验调整时长、参数曲线和挥手幅度；
- 不重新引入 wakeup 遮罩，除非后续需求明确批准。

验收：

- 本地可导出入场事件；
- 异常路径有 console warning，但不刷屏；
- 多窗口同时打开时不会重复触发苏醒；
- 文档、测试和 i18n 状态一致。

---

## 11. 建议测试清单

### 单元/轻量测试

- `YuiGuideWakeup.isSupported()` 在无 Live2D、Live2D 未就绪、参数缺失时返回合理结果；
- `cancel()` 重复调用安全；
- session id 能阻止旧动画写入新模型；
- reduced motion 分支不创建 wakeup DOM；
- Live2D ready 短等待超时后返回 fallback；
- 参数扫描缺少眼睛或眼球参数时静默降级；
- wakeup lease 有效时跳过本窗口播放。

### 前端集成测试

建议新增或扩展 `tests/e2e/test_home_prompt_flow.py` / `tests/frontend` 相关测试：

- mock 首次首页教程，确认没有 wakeup stage；
- mock 存储阻断态，确认 wakeup 不出现；
- 点击跳过后 Live2D 参数 session 被取消；
- 移动真实鼠标时模型不执行鼠标追踪；
- 确认打招呼参数写入发生在睁眼抬头阶段之后；
- `intro_basic` 仍能正常进入。

### 手工验证

- macOS / Windows / Linux 至少各走一遍首页首次教程；
- Live2D / VRM / MMD 三种模型类型分别确认降级策略；
- 浏览器端和 Electron 分发端都确认窗口、鼠标穿透没有异常；
- 开启 `prefers-reduced-motion` 后确认动画明显减弱。
- 开两个首页窗口，确认不会重复播放苏醒。

---

## 12. 文件归属建议

主负责人：

- 更新冻结说明中的新增字段或生命周期约定；
- 审核 `UniversalTutorialManager` 与 `YuiGuideDirector` 的边界。

演出层负责人：

- `static/yui-guide-wakeup.js`
- `static/yui-guide-director.js` 中的接入调用。

Live2D 负责人：

- 如需在 `static/live2d-model.js` 增加短期覆盖槽，应由熟悉现有口型/眨眼覆盖的人收口；
- 不建议由教程层直接重写 Live2D update 函数。

测试负责人：

- 首页教程首次流程；
- 存储阻断流程；
- reduced motion；
- 跳过/销毁/模型切换回归。

---

## 13. 完成标准

第一版可合并标准：

- 入场只在首页 Yui Guide 开始时触发；
- 不创建 wakeup 遮罩、毛玻璃、粒子或扫描线；
- 存储位置选择、迁移、恢复不会被 wakeup 覆盖；
- 用户可随时跳过；
- Live2D 参数接管有完整清理；
- 默认 Yui 使用实际存在的参数完成闭眼、抬头、右手打招呼，不依赖不存在的 motion；
- 打招呼在睁眼抬头之后，`intro_basic` 在入场结束之后；
- `handoff_to_idle` 有权重衰减，结束时无明显参数跳变；
- 新手引导期间模型不跟踪真实鼠标；
- Live2D 未 ready 超过短等待预算会 fallback，不阻塞教程；
- VRM/MMD 或 Live2D 不可用时不会报错阻断教程；
- 新增用户可见文案完成 8 locale 同步；
- 至少完成一次浏览器端 Playwright 验证；
- Electron 端如无法自动化，需记录手工验证结果和剩余风险。
