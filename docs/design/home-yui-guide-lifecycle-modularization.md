# 首页 Yui 新手教程生命周期模块使用说明

本文档说明已经抽出来的三类新手教程生命周期模块怎么接、怎么关、以及新增教程时应该把什么留在业务层、什么交给公共运行时。

当前已落地的三个模块：

1. `static/tutorial-interaction-takeover.js`
2. `static/tutorial-skip-controller.js`
3. `static/tutorial-avatar-reload-controller.js`

对应三类能力：

1. 全流程鼠标禁用 / 接管期交互白名单 / 正脸锁
2. 跳过按钮显示、点击、销毁
3. 教程期间临时切模、聊天头像覆盖、结束后恢复

## 目标

把“教程内容编排”与“教程运行时生命周期”拆开。

后续新增教程 scene 时：

1. Director 只决定什么时候进入接管、放行哪些按钮、何时结束 takeover。
2. Manager 只决定什么时候显示 skip、什么时候开始临时切模、什么时候恢复。
3. 公共模块负责全局监听、幂等清理、异常结束闭环。

## 当前 owner

### 1. 交互接管

- 模块文件：`static/tutorial-interaction-takeover.js`
- 当前接入方：`static/yui-guide-director.js`

### 2. 跳过按钮

- 模块文件：`static/tutorial-skip-controller.js`
- 当前接入方：`static/universal-tutorial-manager.js`

### 3. 模型重载

- 模块文件：`static/tutorial-avatar-reload-controller.js`
- 当前接入方：`static/universal-tutorial-manager.js`

## 模板加载顺序

如果页面会加载 `yui-guide-director.js`，脚本顺序应至少满足：

```html
<script src="/static/yui-guide-overlay.js"></script>
<script src="/static/yui-guide-page-handoff.js"></script>
<script src="/static/tutorial-interaction-takeover.js"></script>
<script src="/static/avatar-performance-stage.js"></script>
<script src="/static/yui-guide-avatar-stage.js"></script>
<script src="/static/yui-guide-wakeup.js"></script>
<script src="/static/yui-guide-director.js"></script>
<script src="/static/tutorial-skip-controller.js"></script>
<script src="/static/tutorial-avatar-reload-controller.js"></script>
<script src="/static/universal-tutorial-manager.js"></script>
```

如果页面只加载 `universal-tutorial-manager.js`，至少需要：

```html
<script src="/static/tutorial-skip-controller.js"></script>
<script src="/static/tutorial-avatar-reload-controller.js"></script>
<script src="/static/universal-tutorial-manager.js"></script>
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

## 模块二：TutorialSkipController

### 职责

它负责：

1. 创建 `#neko-tutorial-skip-btn`
2. 统一绑定 `pointerdown / mousedown / touchstart / click`
3. 首次点击后禁用按钮，防止重复 skip
4. 按钮移除与幂等销毁

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

## 模块三：TutorialAvatarReloadController

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

如果后面新增一个首页教程变体，最小接入方式如下。

### Director 侧

1. 创建 `interactionTakeover`
2. 保留 `isAllowedTutorialInteractionTarget()` 作为页面白名单
3. 在 scene 切换时只调用 `setTutorialTakingOver(true/false)`

### Manager 侧

1. 用 `showSkipButton()` / `hideSkipButton()` 管 skip
2. 用 `beginTutorialAvatarOverride()` / `restoreTutorialAvatarOverride()` 管临时切模
3. teardown 时继续走统一 `_teardownTutorialUI()`

## 该放在业务层的逻辑

这些不要塞进公共模块：

1. 具体 scene 顺序
2. 具体 bubble 文案
3. 哪个按钮什么时候允许手动点击
4. plugin dashboard handoff payload 结构
5. 业务锁与 tutorial prompt 状态机

尤其首页业务锁仍然由 `static/app-tutorial-prompt.js` 持有，模块化后也没有改 owner。

## 收尾清单

新增教程内容时，至少检查这几项：

1. scene 进入 takeover 前是否真的需要 `setTutorialTakingOver(true)`
2. 允许点击的白名单是否都在 `isAllowedTutorialInteractionTarget()` 里
3. skip 后是否还能落到统一 `requestTutorialDestroy()`
4. destroy / pagehide / remote terminate 时是否会走到 `restoreTutorialAvatarOverride()`
5. 是否有外置聊天窗模式，需要同步按钮禁用或 spotlight

## 当前接入文件

实际已接到这些文件：

1. `static/yui-guide-director.js`
2. `static/universal-tutorial-manager.js`
3. `templates/index.html`
4. `templates/memory_browser.html`
5. `templates/api_key_settings.html`
6. 其他只用 `UniversalTutorialManager` 的教程页面模板

如果后续某个新页面要复用其中任一模块，优先沿用这里的接法，不要再把生命周期逻辑直接复制回 Manager 或 Director。
