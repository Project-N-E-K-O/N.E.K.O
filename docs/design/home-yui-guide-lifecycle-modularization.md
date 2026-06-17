# 新手教程生命周期通用模块与专属适配说明

本文档说明已经抽出来的五类新手教程生命周期通用模块怎么接、怎么关，以及新增教程时哪些能力必须复用通用模块、哪些能力可以留在页面专属适配层。

当前已落地的五个通用模块：

1. `static/tutorial/core/interaction-takeover.js`
2. `static/tutorial/visual/highlight-controller.js`
3. `static/tutorial-interrupt-controller.js`
4. `static/tutorial/core/skip-controller.js`
5. `static/tutorial/avatar/reload-controller.js`

对应五类能力：

1. 全流程鼠标禁用 / 接管期交互白名单 / 正脸锁
2. 圆形高亮 / 矩形虚拟高亮 / 多目标合并高亮 / extra spotlight 清理
3. 轻微打断抵抗 / 生气退出分支 / 打断期间 presentation 暂停与恢复
4. 跳过按钮显示、点击、销毁
5. 教程期间临时切模、聊天头像覆盖、结束后恢复

## 目标

把“教程内容编排”与“教程运行时生命周期”拆开。

后续新增教程 scene 时：

1. Director 只决定什么时候进入接管、放行哪些按钮、何时结束 takeover，以及当前 scene 应该高亮哪个业务目标。
2. Manager 只决定什么时候显示 skip、什么时候开始临时切模、什么时候恢复。
3. 公共模块负责全局监听、高亮形态和属性生命周期、打断分支执行、幂等清理、异常结束闭环。

## 通用模块与专属适配

### 通用模块

通用模块是后续所有新手教程都应该优先复用的生命周期能力。只要教程运行在能加载 `static/` 脚本的页面里，就不应该再复制这些逻辑。

通用模块负责：

1. 交互接管、全局事件守卫和正脸锁生命周期
2. 圆形、矩形、合并区域、extra、precise 等高亮形态的创建和清理
3. 轻微打断和生气退出的标准分支语义
4. 跳过按钮的显示、点击和销毁
5. 教程临时模型切换、聊天头像覆盖和结束恢复

通用模块不允许写入：

1. 具体教程天数、scene 顺序、台词和剧情节奏
2. 具体业务 DOM selector
3. ghost cursor 的业务路径和真实 UI 点击顺序
4. 跨窗口 handoff payload、Electron IPC、页面构建产物路径
5. 某个页面专属的 fallback 或缓存处理

### 专属适配层

专属适配层是每个教程或页面自己的“胶水代码”。它负责把通用模块接到当前页面的真实 DOM、真实按钮和真实窗口环境上。

专属适配层可以保留：

1. 当前教程的 scene 顺序和台词选择
2. 当前页面的目标元素解析、白名单和真实 UI 操作
3. ghost cursor 的移动路径、点击节奏和页面内可见动画
4. 插件 dashboard、设置页、跨页面入口等 handoff 细节
5. Electron IPC、独立 Vue 页面运行时、前端产物重建要求

专属适配层必须遵守通用模块定义的语义。例如生气退出在任何页面都等同“语音后跳过”：触发时立即清理高亮和 ghost cursor，语音结束后走统一 skip 路径，不能走 done。

## 当前 owner

### 通用模块 owner

#### 1. 交互接管

- 模块文件：`static/tutorial/core/interaction-takeover.js`
- 当前接入方：`static/tutorial/yui-guide/director.js`

#### 2. 高亮控制

- 模块文件：`static/tutorial/visual/highlight-controller.js`
- 当前接入方：`static/tutorial/yui-guide/director.js`

#### 3. 打断分支

- 模块文件：`static/tutorial-interrupt-controller.js`
- 当前接入方：`static/tutorial/yui-guide/director.js`

#### 4. 跳过按钮

- 模块文件：`static/tutorial/core/skip-controller.js`
- 当前接入方：`static/tutorial/core/universal-manager.js`

#### 5. 模型重载

- 模块文件：`static/tutorial/avatar/reload-controller.js`
- 当前接入方：`static/tutorial/core/universal-manager.js`

### 专属适配 owner

1. `static/tutorial/yui-guide/director.js`
   - 首页 Yui 新手教程专属 Director。
   - 负责 scene 编排、目标 DOM 选择、ghost cursor 路径、真实 UI 点击、handoff payload。
   - 必须通过包装方法调用 `TutorialInteractionTakeover`、`TutorialHighlightController`、`TutorialInterruptController`，不能重新实现这些生命周期。
2. `static/tutorial/core/universal-manager.js`
   - 首页及普通模板教程的 Manager 适配层。
   - 负责启动条件、skip 按钮接入、临时模型切换接入、统一 destroy。
   - 必须通过 `TutorialSkipController` 和 `TutorialAvatarReloadController` 管对应生命周期。
3. `frontend/plugin-manager/src/yui-guide-runtime.ts`
   - 插件管理页专属运行时适配层。
   - 插件管理页是独立 Vue 前端产物，不直接加载首页全局模块。
   - 它拥有页面内 `main` spotlight、页面内 ghost cursor、桌面 IPC skip / interrupt bridge、插件页本地 skip 控件和 dist 产物重建要求。
   - 它必须遵守通用语义，但不能依赖首页模块替它清理页面内的高亮或 ghost cursor。
4. 后续 Day 2 到 Day 4 或新页面教程
   - 可以新增自己的 Director / Page Runtime。
   - 只能持有页面业务知识和页面适配逻辑。
   - 通用生命周期必须优先接入本文档列出的五个模块。

## 模板加载顺序

如果首页会加载 `tutorial/yui-guide/director.js`，脚本顺序应至少满足：

```html
<script src="/static/tutorial/yui-guide/steps.js"></script>
<script src="/static/tutorial/yui-guide/overlay.js"></script>
<script src="/static/tutorial/yui-guide/page-handoff.js"></script>
<script src="/static/avatar-performance-stage.js"></script>
<script src="/static/tutorial/avatar/yui-stage.js"></script>
<script src="/static/tutorial/yui-guide/wakeup.js"></script>
<script src="/static/tutorial/core/interaction-takeover.js"></script>
<script src="/static/tutorial/visual/highlight-controller.js"></script>
<script src="/static/tutorial-interrupt-controller.js"></script>
<script src="/static/tutorial/yui-guide/director.js"></script>
<script src="/static/tutorial/core/skip-controller.js"></script>
<script src="/static/tutorial/avatar/reload-controller.js"></script>
<script src="/static/tutorial/core/universal-manager.js"></script>
```

如果非首页页面也加载 `tutorial/yui-guide/director.js`，但不接入头像演出运行时，至少需要保证：

```html
<script src="/static/tutorial/yui-guide/steps.js"></script>
<script src="/static/tutorial/yui-guide/overlay.js"></script>
<script src="/static/tutorial/yui-guide/page-handoff.js"></script>
<script src="/static/tutorial/core/interaction-takeover.js"></script>
<script src="/static/tutorial/visual/highlight-controller.js"></script>
<script src="/static/tutorial-interrupt-controller.js"></script>
<script src="/static/tutorial/yui-guide/director.js"></script>
<script src="/static/tutorial/core/skip-controller.js"></script>
<script src="/static/tutorial/avatar/reload-controller.js"></script>
<script src="/static/tutorial/core/universal-manager.js"></script>
```

如果页面只加载 `tutorial/core/universal-manager.js`，至少需要：

```html
<script src="/static/tutorial/core/skip-controller.js"></script>
<script src="/static/tutorial/avatar/reload-controller.js"></script>
<script src="/static/tutorial/core/universal-manager.js"></script>
```

## 模块一：TutorialInteractionTakeover

### 职责

它负责：

1. 文档级交互守卫注册与销毁
2. `overlay.setTakingOver()` 生命周期
3. 首页接管期正脸锁 / 鼠标跟踪关闭与恢复
4. 外置聊天窗按钮禁用与 spotlight 同步
5. 触控 passthrough 特判

它不负责：

1. spotlight 画什么
2. ghost cursor 怎么走
3. 哪个 DOM 该放行
4. skip 逻辑

### 对外接口

模块通过全局对象暴露：

```js
window.TutorialInteractionTakeover.createController(options)
```

当前实际用到的 controller 方法：

```js
controller.setActive(active)
controller.enableFaceForwardLock()
controller.applyFaceForwardLock()
controller.releaseFaceForwardLock()
controller.setExternalizedChatButtonsDisabled(disabled)
controller.setExternalizedChatSpotlight(kind)
controller.clearExternalizedChatFx()
controller.onExternalChatReady()
controller.destroy()
```

### 推荐接法

在 Director 构造阶段创建：

```js
this.interactionTakeover = window.TutorialInteractionTakeover.createController({
  page: this.page,
  overlay: this.overlay,
  allowTarget: (target, event) => this.isAllowedTutorialInteractionTarget(target, event),
  isSystemDialogTarget: (target, event) => this.isSystemDialogInteractionTarget(target, event),
  allowTouchPassthrough: (event, controller) => {
    return !!(
      this.mobileTouchInteractionPassthrough &&
      controller &&
      controller.isTouchInteractionEvent(event) &&
      !this.awaitingIntroActivation &&
      !this.manualPluginDashboardOpenAllowed
    )
  },
  isDestroyed: () => this.destroyed,
  externalizedChatDetector: () => this.isHomeChatExternalized(),
  externalChatChannelProvider: () => window.appInterpage?.nekoBroadcastChannel || null,
})
```

如果页面处于旧缓存状态导致 `static/tutorial/core/interaction-takeover.js` 暂时没有加载，Director 只能降级交互接管与外置聊天窗同步能力，并保留 skip / angry exit 终止兜底，不能因为 takeover 模块缺失阻断教程结束链路。正常页面必须按上面的模板顺序加载该模块。

然后在 Director 内保留一层薄包装：

```js
setTutorialTakingOver(active) {
  this.interactionTakeover.setActive(active === true)
}
```

之后 scene 内统一调用：

```js
this.setTutorialTakingOver(true)
this.setTutorialTakingOver(false)
```

### 允许点击的目标应该放哪

白名单判断仍然留在 Director 的页面语义层，例如：

1. skip 按钮
2. 首页输入框激活
3. 手动打开插件管理面板入口
4. 系统弹窗

也就是：

```js
isAllowedTutorialInteractionTarget(target) { ... }
isSystemDialogInteractionTarget(target) { ... }
```

模块只消费这些判断，不拥有页面业务知识。

### 清理要求

教程销毁时必须调用：

```js
this.interactionTakeover.destroy()
```

这样会一起清掉：

1. document capture listeners
2. `yui-taking-over`
3. 外置聊天窗禁用态
4. 正脸锁

## 模块二：TutorialHighlightController

### 职责

它负责：

1. 创建、复用、移除矩形虚拟高亮节点
2. 为麦克风、猫爪、设置等悬浮圆形按钮写入圆形高亮提示
3. 把多个 DOM 合并成一个矩形高亮区域
4. 管理 precise highlight class、spotlight variant、spotlight geometry 属性
5. 同步 retained / scene extra spotlights 到 `YuiGuideOverlay`
6. 统一解析 selector、DOM、rect、elements 数组等高亮目标配置

它不负责：

1. overlay 的遮罩、描边、cutout 怎么画
2. scene 顺序和剧情节奏
3. 具体哪个业务按钮应该被高亮
4. ghost cursor 怎么移动或点击

### 对外接口

模块通过全局对象暴露：

```js
window.TutorialHighlightController.createController(options)
```

当前实际用到的 controller 方法：

```js
controller.getElementRect(element)
controller.createVirtualSpotlight(key, rect, options)
controller.createUnionSpotlight(key, elements, options)
controller.clearVirtualSpotlight(key)
controller.clearAllVirtualSpotlights()
controller.setPreciseHighlightTargets(elements)
controller.clearPreciseHighlights()
controller.setSpotlightGeometryHint(element, options)
controller.clearSpotlightGeometryHints()
controller.setSpotlightVariantHints(entries)
controller.clearSpotlightVariantHints()
controller.addRetainedExtraSpotlight(element)
controller.replaceRetainedExtraSpotlight(matcher, element)
controller.removeRetainedExtraSpotlight(matcher)
controller.clearRetainedExtraSpotlights()
controller.setSceneExtraSpotlights(elements)
controller.clearSceneExtraSpotlights()
controller.clearAllExtraSpotlights()
controller.getFloatingButtonShell(element)
controller.isCircularFloatingButtonSpotlight(element)
controller.applyCircularFloatingButtonSpotlightHint(element)
controller.normalizeHighlightTarget(target, fallbackKey)
controller.applyGuideHighlights(config)
controller.destroy()
```

### 推荐接法

在 Director 构造阶段创建：

```js
this.highlightController = window.TutorialHighlightController.createController({
  document,
  window,
  overlay: this.overlay,
  defaultPadding: DEFAULT_SPOTLIGHT_PADDING,
  resolveElement: (selector) => this.resolveElement(selector),
})
```

然后在 Director 内保留一层薄包装：

```js
createVirtualSpotlight(key, rect, options) {
  return this.highlightController.createVirtualSpotlight(key, rect, options)
}

applyCircularFloatingButtonSpotlightHint(element) {
  return this.highlightController.applyCircularFloatingButtonSpotlightHint(element)
}
```

之后 scene 内继续使用原来的 Director 方法名，不需要直接持有 controller。

### 圆形高亮和矩形高亮的边界

圆形高亮只负责给悬浮按钮目标写入：

```js
data-yui-guide-spotlight-padding="4"
data-yui-guide-spotlight-geometry="circle"
```

矩形高亮只负责创建不可点击的虚拟 DOM，并写入：

```js
data-yui-guide-virtual-spotlight="..."
data-yui-guide-spotlight-padding="..."
data-yui-guide-spotlight-radius="..."
```

真正的遮罩 cutout、圆形 frame、矩形 frame 仍由 `static/tutorial/yui-guide/overlay.js` 读取这些属性后统一渲染。

### 清理要求

教程销毁时必须调用：

```js
this.highlightController.destroy()
```

这样会一起清掉：

1. 虚拟高亮 DOM
2. precise highlight class
3. spotlight variant / geometry 属性
4. retained / scene extra spotlights

## 模块三：TutorialInterruptController

### 职责

它负责：

1. 执行轻微打断抵抗分支，也就是 `interrupt_resist_light`
2. 执行生气退出分支，也就是 `interrupt_angry_exit`
3. 打断期间暂停当前 scene presentation，并在轻微抵抗结束后恢复
4. 向聊天窗口追加打断台词，并控制打断语音、模型动作、ghost cursor 抵抗动作并行
5. 生气退出时进入 angry presentation，关闭当前 scene 动画，并在语音/演出结束后请求统一跳过
6. 被 skip / destroy 打断后停止接收晚到的打断回调

它不负责：

1. 判断用户鼠标移动是否达到打断条件
2. 维护打断次数、速度阈值、连续加速度阈值
3. 决定 angry exit 阈值是多少
4. scene 主线顺序和 takeover 业务阶段
5. 生气退出后 Manager 的 teardown 细节
6. 插件管理页内的本地 spotlight、ghost cursor 和 Electron IPC 通道

### 对外接口

模块通过全局对象暴露：

```js
window.TutorialInterruptController.createController(options)
```

当前实际用到的 controller 方法：

```js
controller.playLightResistance(x, y, options)
controller.abortAsAngryExit(source)
controller.destroy()
```

### 推荐接法

在 Director 构造阶段创建：

```js
this.interruptController = window.TutorialInterruptController.createController({
  overlay: this.overlay,
  cursor: this.cursor,
  callbacks: {
    getStep: (stepId) => this.getStep(stepId),
    getInterruptCount: () => this.interruptCount,
    isStopping: () => this.isStopping(),
    isResistancePaused: () => this.scenePausedForResistance,
    pauseCurrentSceneForResistance: () => this.pauseCurrentSceneForResistance(),
    resumeCurrentSceneAfterResistance: () => this.resumeCurrentSceneAfterResistance(),
    appendGuideChatMessage: (message, options) => this.appendGuideChatMessage(message, options),
    applyGuideEmotion: (emotion, options) => this.applyGuideEmotion(emotion, options),
    requestTermination: (reason, tutorialReason) => this.requestTermination(reason, tutorialReason),
  },
})
```

然后在 Director 内保留一层薄包装：

```js
playLightResistance(x, y, options) {
  return this.interruptController.playLightResistance(x, y, options)
}

abortAsAngryExit(source) {
  return this.interruptController.abortAsAngryExit(source)
}
```

### 打断判断应该放哪

打断判断仍然留在 Director 的页面语义层，例如：

1. `DEFAULT_INTERRUPT_DISTANCE`
2. `DEFAULT_INTERRUPT_SPEED_THRESHOLD`
3. `DEFAULT_INTERRUPT_ACCELERATION_THRESHOLD`
4. `DEFAULT_INTERRUPT_ACCELERATION_STREAK`
5. `performance.interrupts.threshold`

模块只消费“已经确定要轻微抵抗”或“已经确定要生气退出”的结果，不拥有鼠标事件和阈值判断。

### 生气退出语义

`interrupt_angry_exit` 的产品语义等价于“延迟执行跳过按钮”：

1. 触发瞬间，当前 scene 必须停止继续演示，清掉当前高亮、插件预览、普通气泡和 ghost cursor 动画。
2. 生气退出台词和模型演出必须完整播放。
3. 语音/演出结束后，才进入统一 skip / destroy 路径。
4. 这不是正常 scene done，不能把它当成教程完成或插件 dashboard 预览完成。

首页公共模块通过 `requestTermination(source || 'angry_exit', 'angry_exit')` 交给 Manager。Manager 会把 `angry_exit` 归一为 skip 结果，确保和跳过按钮一样不会标记为正常完成。

插件管理页的同等语义在 `frontend/plugin-manager/src/yui-guide-runtime.ts` 里实现：

1. 触发时调用 `stopGhostCursorAnimation()`、`clearSpotlight()` 和 `setAngryVisual(true)`。
2. 插件页本地 `main` 高亮和 ghost cursor 动画立即停止，后续预览动画用 `isCurrentWithoutAngryExit` 防止重启。
3. 通过桌面 bridge 或 opener 请求首页播放 `interrupt_angry_exit` 语音。
4. 首页语音结束后，插件页调用 `requestPluginDashboardSkip({ source: 'plugin_dashboard_angry_exit', reason: 'angry_exit' })`；首页 Director 会把这个 source 识别为明确 skip 请求，直接转入 `tutorialManager.handleTutorialSkipRequest()`，不要求携带屏幕坐标。
5. 插件页不再发送 `plugin-dashboard:done`，避免把生气退出误判成插件面板教程正常完成。

插件页 skip 还有两条入口：

1. 用户真实点击首页右上角 `#neko-tutorial-skip-btn` 的屏幕区域时，插件页 runtime 根据 handoff payload 里的 `skipButtonScreenRect` 做命中判断，发送带 `screenX/screenY` 的 `plugin-dashboard:skip-request`。首页 Director 重新读取当前 skip 按钮 rect 校验后调用 `skipButton.click()`，让 `TutorialSkipController` 处理真实首页按钮点击。
2. 用户点击插件页自己的桌面 skip 按钮时，插件页发送 `source: 'plugin_dashboard_button'`。首页 Director 同样把它视为明确 skip 请求并转入 Manager 统一 skip 入口，而不是走坐标转发。

### 清理要求

教程终止或销毁时必须调用：

```js
this.interruptController.destroy()
```

这样可以防止这些 late callback 重新写回 UI：

1. 轻微抵抗结束后的 scene presentation 恢复
2. 生气退出演出结束后的 termination request
3. 打断期间的模型动作、语音、ghost cursor Promise 回调

## 模块四：TutorialSkipController

### 职责

它负责：

1. 创建 `#neko-tutorial-skip-btn`
2. 统一绑定 `pointerdown / mousedown / touchstart / click`
3. 首次点击后禁用按钮，防止重复 skip
4. 按钮移除与幂等销毁

注意：这里描述的是首页和普通模板教程的全局 skip 按钮。插件管理页作为独立 Vue runtime，会创建自己的页面内 skip 控件；该控件不属于 `TutorialSkipController` 管理，但必须把 skip 转发回首页 Manager 的统一入口。

它不负责：

1. skip 以后具体要不要调用 Director
2. skip 失败后回退逻辑
3. tutorial-completed / skipped 事件派发

这些仍由 `UniversalTutorialManager` 决定。

### 对外接口

```js
window.TutorialSkipController.createController(options)
```

当前实际用到的方法：

```js
controller.show({
  label,
  onSkip,
})
controller.hide()
controller.destroy()
controller.getElement()
```

### 推荐接法

在 Manager 中懒创建：

```js
ensureTutorialSkipController() {
  if (!this._tutorialSkipController) {
    this._tutorialSkipController = window.TutorialSkipController.createController({
      document,
      buttonId: 'neko-tutorial-skip-btn',
    })
  }
  return this._tutorialSkipController
}
```

推荐再补一个统一业务入口：

```js
handleTutorialSkipRequest() {
  const director = this.isYuiGuideEnabledForPage(this.currentPage)
    ? this.ensureYuiGuideDirector()
    : null

  if (director && typeof director.skip === 'function') {
    return Promise.resolve(director.skip('skip', 'skip'))
      .then(() => {
        this.requestTutorialDestroy('skip')
      })
      .catch((error) => {
        console.warn('[Tutorial] Yui Guide skip 失败，回退到 requestTutorialDestroy:', error)
        this.requestTutorialDestroy('skip')
      })
  }

  this.requestTutorialDestroy('skip')
  return Promise.resolve()
}
```

然后让 `showSkipButton()` 只负责把按钮接到这个入口：

```js
controller.show({
  label: this.t('tutorial.buttons.skip', '跳过'),
  onSkip: () => this.handleTutorialSkipRequest(),
})
```

`hideSkipButton()` 统一调用：

```js
controller.hide()
```

### 使用边界

如果新增教程页面只想复用 skip 按钮，不需要接 Director。  
只要传自己的 `onSkip`，或者在 Manager 里复用 `handleTutorialSkipRequest()` 这种统一退出入口即可。

如果是跨页 handoff 子页面回传 skip，也建议优先转发回 Manager 的 `handleTutorialSkipRequest()`，不要在子链路里重新拼一份 `director.skip() + requestTutorialDestroy()`。

当前插件 dashboard 的实现细分为：

1. 坐标转发：插件页判断真实点击命中首页 skip 按钮屏幕区域后发送 `screenX/screenY`，首页 Director 校验当前按钮 rect 后调用 `#neko-tutorial-skip-btn.click()`。
2. 明确 skip：`source: 'plugin_dashboard_button'` 和 `source: 'plugin_dashboard_angry_exit'` 不依赖坐标，首页 Director 直接调用 `tutorialManager.handleTutorialSkipRequest()`。
3. 插件页本地 skip 控件必须继续拦截 pointer/mouse/touch/click 事件，即使已经触发过 skip，也不能 disabled 到让后续事件穿透到底层插件页面。

## 模块五：TutorialAvatarReloadController

### 职责

它负责：

1. 教程开始时临时切换到教程模型
2. 捕获并覆盖聊天头像 / 名称
3. 教程结束时恢复用户原模型
4. 处理 setup 期间的超时、取消、延迟恢复

它不负责：

1. 具体怎么 reload 模型
2. 怎么构造模型快照 payload
3. viewport placement 的具体算法
4. chat identity override 的渲染实现

这些都通过 callbacks 由 Manager 提供。

### 对外接口

```js
window.TutorialAvatarReloadController.createController(options)
```

当前实际用到的方法：

```js
controller.beginOverride()
controller.restoreOverride()
```

### 推荐接法

在 Manager 中懒创建：

```js
ensureTutorialAvatarReloadController() {
  if (!this._tutorialAvatarReloadController) {
    this._tutorialAvatarReloadController = window.TutorialAvatarReloadController.createController({
      host: this,
      timeoutMs: TUTORIAL_AVATAR_OVERRIDE_TIMEOUT_MS,
      tutorialModelName: TUTORIAL_YUI_LIVE2D_MODEL_NAME,
      resolveCurrentName: () => this.resolveCurrentTutorialCatgirlName(),
      fetchCharacters: () => this.fetchTutorialCharacters(),
      buildSnapshotPayload: (currentConfig) => this.buildTutorialModelSavePayload(currentConfig),
      reloadModel: (currentName, payload, options) => this.reloadTutorialModel(currentName, payload, options),
      setPreparing: (preparing) => this.setTutorialLive2dPreparing(preparing),
      revealPrepared: () => this.revealTutorialLive2dPrepared(),
      captureAvatarPreview: () => this.captureTutorialChatAvatarPreview(),
      applyIdentityOverride: (payload) => this.applyTutorialChatIdentityOverride(payload),
      sleep: (delayMs) => this.sleep(delayMs),
      clearViewportWatcher: () => this.clearTutorialLive2dViewportPlacementWatcher(),
    })
  }
  return this._tutorialAvatarReloadController
}
```

然后把旧入口保留成包装层：

```js
beginTutorialAvatarOverride() {
  return this.ensureTutorialAvatarReloadController().beginOverride()
}

restoreTutorialAvatarOverride() {
  return this.ensureTutorialAvatarReloadController().restoreOverride()
}
```

### 为什么保留旧方法名

因为当前业务流程已经到处在调用：

1. `beginTutorialAvatarOverride()`
2. `restoreTutorialAvatarOverride()`

保留这层包装，可以把 owner 换掉，但不要求 scene 编排层跟着大改。

### 异常路径

这个模块必须覆盖：

1. setup 超时
2. setup 中断
3. restoreRequested 晚到
4. destroy 期间重复 restore

因此不要在业务代码里直接改：

1. `controller.override`
2. `controller.overridePromise`

这些状态现在已经完全存在于 `TutorialAvatarReloadController` 内部，不再由 `UniversalTutorialManager` 代持。

## 新教程接入模板

如果后面新增任意新手教程，先判断它运行在哪一类页面里：

1. 能加载 `static/` 全局脚本的页面：必须直接接入通用模块。
2. 独立构建产物或独立窗口页面：可以写页面专属 runtime，但必须复刻通用语义，并在文档里标明哪些逻辑是专属适配。

新增首页教程变体的最小接入方式如下。

### Director 侧

1. 创建 `interactionTakeover`
2. 创建 `highlightController`
3. 创建 `interruptController`
4. 保留 `isAllowedTutorialInteractionTarget()` 作为页面白名单
5. 在 scene 切换时只调用 `setTutorialTakingOver(true/false)`
6. 需要圆形、矩形、合并区域、extra spotlight 时，继续通过 Director 包装方法调用 `highlightController`
7. 用户打断达到轻微抵抗或生气退出条件时，继续通过 Director 包装方法调用 `interruptController`

### Manager 侧

1. 用 `showSkipButton()` / `hideSkipButton()` 管 skip
2. 用 `beginTutorialAvatarOverride()` / `restoreTutorialAvatarOverride()` 管临时切模
3. teardown 时继续走统一 `_teardownTutorialUI()`

### 独立页面 runtime 侧

独立页面 runtime 不能直接使用首页 DOM 生命周期时，至少要实现同等语义：

1. 有自己的页面内 highlighter 时，必须在 skip / destroy / angry exit 触发瞬间清掉。
2. 有自己的 ghost cursor 时，必须提供等价 `stopGhostCursorAnimation()` 的停止和隐藏能力。
3. 有跨窗口 skip / interrupt 时，必须转发到统一教程 skip / interrupt 通道，不能在页面内伪造 done。
4. 生气退出必须等语音结束后再执行 skip；视觉清理必须在触发瞬间执行。
5. 页面内 skip 控件必须用本地事件守卫防穿透，并把业务结果回传统一 skip 入口；不要在独立 runtime 内直接拼首页 teardown。
6. 如果页面产物需要 build，修改 runtime 后必须重建产物。

## 该放在专属适配层的逻辑

这些不要塞进公共模块：

1. 具体 scene 顺序
2. 具体 bubble 文案
3. 哪个按钮什么时候允许手动点击
4. 当前 scene 具体应该高亮哪个业务目标
5. 鼠标移动是否构成打断、打断次数和 angry exit 阈值
6. plugin dashboard handoff payload 结构
7. 插件管理页的本地 spotlight / ghost cursor / IPC bridge / 本地 skip 控件适配逻辑
8. 业务锁与 tutorial prompt 状态机

尤其首页业务锁仍然由 `static/tutorial/core/app-prompt.js` 持有，模块化后也没有改 owner。

## 不允许放进专属适配层的逻辑

后续新增教程时，下面这些不能再复制一份：

1. 全局接管事件守卫
2. 圆形/矩形/extra/precise 高亮属性生命周期
3. 轻微打断和生气退出的通用语义
4. 跳过按钮生命周期
5. 临时模型切换和恢复生命周期

如果某个独立页面暂时无法直接加载通用模块，只能在该页面 runtime 中写“等价适配”，并且必须在本文件或对应设计文档中写清楚这是专属适配，不是新的通用模块。

## 收尾清单

新增教程内容时，至少检查这几项：

1. scene 进入 takeover 前是否真的需要 `setTutorialTakingOver(true)`
2. 允许点击的白名单是否都在 `isAllowedTutorialInteractionTarget()` 里
3. skip 后是否还能落到统一 `requestTutorialDestroy()`
4. destroy / pagehide / remote terminate 时是否会走到 `restoreTutorialAvatarOverride()`
5. 圆形 / 矩形 / virtual / extra / precise 高亮是否都能通过 `highlightController` 清理
6. skip / destroy / angry exit 时是否会调用 `interruptController.destroy()`
7. angry exit 是否在触发瞬间清掉高亮和 ghost cursor，并在语音结束后走 skip 而不是 done
8. 插件 dashboard skip 是否区分“坐标转发点击首页按钮”和“插件页按钮 / angry exit 明确 skip”，且两者最终都落到 Manager 统一 skip 入口
9. 插件管理页本地 skip 控件是否能拦截 pointer/mouse/touch/click，避免点击穿透到底层页面
10. 插件管理页是否同步重建 `frontend/plugin-manager/dist`，避免桌面端加载旧 runtime
11. 是否有外置聊天窗模式，需要同步按钮禁用或 spotlight

## 当前接入文件

### 通用模块文件

1. `static/tutorial/core/interaction-takeover.js`
2. `static/tutorial/visual/highlight-controller.js`
3. `static/tutorial-interrupt-controller.js`
4. `static/tutorial/core/skip-controller.js`
5. `static/tutorial/avatar/reload-controller.js`

### 专属适配接入文件

1. `static/tutorial/yui-guide/director.js`
2. `static/tutorial/core/universal-manager.js`
3. `frontend/plugin-manager/src/yui-guide-runtime.ts`（插件管理页独立适配层，不是公共模块接入方）
4. `templates/index.html`
5. `templates/memory_browser.html`
6. `templates/api_key_settings.html`
7. 其他只用 `UniversalTutorialManager` 的教程页面模板

如果后续某个新页面要复用其中任一模块，优先沿用这里的接法，不要再把通用生命周期逻辑直接复制回 Manager、Director 或页面 runtime。
