# 新手教程框架重构方案

## 背景

当前新手教程系统分布在十几个 JS 文件中（总计约 1.5MB），核心逻辑集中在 `yui-guide-director.js`（657KB）和 `yui-guide-overlay.js`（91KB）。Ghost Cursor、高亮、对抗机制、场景生命周期等功能高度耦合，各天教程在 scene handler 中存在大量重复的时序编排代码。本重构的目标是将可复用的通用功能抽象为独立模块，使每日教程只需声明配置即可运行。

### 现状痛点

1. **Ghost Cursor 逻辑分散**：移动、点击、晃动、对抗回弹等方法散布在 `YuiGuideGhostCursor`（director 内部类）和 `YuiGuideOverlay` 两个类之间；调用方需要同时理解两层的 token 取消、duration 标准化、PC bridge 代理和 DOM fallback。
2. **高亮类型未统一抽象**：圆角矩形和圆形高亮的创建、切换、过渡和清理在每个 scene handler 中重复出现；circle vs rounded-rect 的判断依赖 CSS 选择器匹配，且 `variant`（猫耳、猫爪、plain-circle、thin）散落在各处配置。
3. **对抗机制与场景业务混合**：`TutorialInterruptController` 通过回调对象与 Director 交互，但「暂停当前演出 → 播放对抗台词 → 恢复演出」的完整暂停/恢复流程没有抽象成通用方法；高亮和侧边栏展开/收起也未接入暂停机制。
4. **PC 全局 Overlay 与 DOM 双路径**：每个视觉操作都有 `if (isPcOverlayActive())` 分支，导致逻辑翻倍；未来 PC overlay 成为唯一渲染层后，DOM 分支代码仍无法干净删除。
5. **场景生命周期重复**：`prepareAvatarFloatingScene()` → `playAvatarFloatingScene()` → `runAvatarFloatingSceneOperation()` → `cleanup` 的流水线在各天 scene 中以不同形式重复实现；cursor 锚点保存/读取、外置聊天窗同步、花瓣 cue 触发等通用流程没有统一封装。

---

## 重构目标

1. **Ghost Cursor 通用化**：抽象为独立的 `GhostCursorController`，对外暴露 `moveTo`、`click`、`wobble`、`hold`、`pause`、`resume` 等声明式 API，内部自动处理 PC overlay / DOM 切换、token 取消、锚点管理。
2. **高亮通用化**：抽象为 `SpotlightController`，统一圆角矩形和圆形高亮的创建、过渡、清理和暂停机制；对外只暴露 `highlight(target, options)` / `clearHighlight(target)` / `pauseHighlight()` / `resumeHighlight()`。
3. **对抗暂停通用化**：将「真实鼠标检测 → cursor 对抗位移 → 计数升级 → 暂停当前演出 → 播放打断台词 → 恢复演出」完整链路封装为 `ResistanceController`，所有需要暂停的元素（cursor、高亮、侧边栏展开、台词播放）通过统一的 `PauseToken` 机制协同。
4. **PC 全局透明 Overlay 唯一渲染层**：所有 Ghost Cursor 和高亮演出统一由 PC 全局透明 overlay 渲染；DOM fallback 仅作为 PC bridge 不可用时的降级路径，且由框架层自动处理，业务代码不感知渲染层差异。
5. **场景生命周期声明化**：每日教程只需声明 scene 配置（目标、cursor 动作、高亮、operation、情绪），框架自动编排时序（T+0 高光 → T+220ms cursor → 点击并行 operation → 台词结束 → 下一 scene）；不再需要各天手写 `playAvatarFloatingScene` 的 day-specific handler。

---

## 重复流程与可抽象通用方法全量分析

以下是对 `yui-guide-director.js`（15,467 行）和各天教程文件的逐模式扫描结果，按重复次数和抽象价值排序。

### A 类：高重复 / 完全相同的代码块

#### A1. Scene Preamble（台词/情绪/按钮准备）

**重复次数**：7+ 处完全相同

每个 `playDay4...Scene()`、`playDay2...Scene()` 等方法开头都有约 20 行一模一样的代码：

```javascript
const text = this.resolveAvatarFloatingSceneText(scene);
const voiceKey = scene.voiceKey || '';
const sceneButtons = this.getAvatarFloatingSceneButtons(scene);
const canHandleSceneButtons = sceneButtons.length > 0
    ? this.installGuideMessageActionHandler()
    : false;
const actionWaitPromise = canHandleSceneButtons
    ? this.beginGuideMessageActionWait(sceneButtons, 0)
    : null;
if (text) {
    this.appendGuideChatMessage(text, {
        textKey: scene.textKey || '',
        voiceKey: voiceKey,
        buttons: sceneButtons
    });
}
const sceneEmotion = this.resolveAvatarFloatingSceneEmotion(scene);
if (sceneEmotion) {
    this.applyGuideEmotion(sceneEmotion);
}
```

**变化点**：无，完全相同。

**抽象为**：`SceneOrchestrator.prepareNarration(scene)` → `{ text, voiceKey, actionWaitPromise, ... }`

---

#### A2. Scene Postamble（守卫 + 等待 + 延迟）

**重复次数**：7+ 处完全相同

每个 scene 方法尾部：

```javascript
if (canHandleSceneButtons && this.pendingGuideMessageAction) {
    this.armPendingGuideMessageActionTimeout(12000);
}
if (actionWaitPromise && sceneRunId === this.sceneRunId && !this.isStopping()) {
    await actionWaitPromise;
}
if (sceneRunId !== this.sceneRunId || this.isStopping()) {
    return false;
}
await this.waitForSceneDelay(index >= total - 1 ? 260 : 420);
return sceneRunId === this.sceneRunId && !this.isStopping();
```

**变化点**：仅 `index` / `total` 影响 260/420 延迟选择。

**抽象为**：`SceneOrchestrator.finalizeScene(sceneRunId, { canHandleSceneButtons, actionWaitPromise, index, total })`

---

#### A3. Guard / Run-ID 守卫检查

**重复次数**：274 处

`sceneRunId !== this.sceneRunId || this.isStopping()` 出现在几乎所有 `await` 之后。部分方法内部重复声明局部 `guardFailed` 闭包（如 line 7542、11632）。

**抽象为**：统一的 `isGuardFailed(runId)` 实例方法，替代所有局部重新声明和内联检查。

---

#### A4. Narration + Catch 模式

**重复次数**：7+ 处

```javascript
const narrationPromise = (text || voiceKey)
    ? this.speakGuideLine(text, {
        voiceKey: voiceKey,
        minDurationMs: 1800
    }).catch((error) => {
        console.warn('[YuiGuide] 悬浮窗教程旁白失败，继续流程:', scene.id, error);
    })
    : Promise.resolve();
```

**变化点**：仅 `minDurationMs`（通常为 1800）。

**抽象为**：`NarratorController.narrate(text, { voiceKey, minDurationMs })`，内部统一 catch。

---

#### A5. Overlay Clear 序列

**重复次数**：20+ 对

```javascript
this.overlay.clearActionSpotlight();
this.overlay.clearPersistentSpotlight();
// 常伴随:
this.overlay.hideBubble();
```

**抽象为**：`SpotlightController.clearAll()` 单一方法。

---

#### A6. Day Guide 样板代码（deepFreeze / registerGuide / zhAudio）

**重复次数**：3 个文件完全相同

`yui-guide-day1-home-guide.js`、`yui-guide-day4-companion-guide.js`、`yui-guide-day6-agent-guide.js` 各自重复定义：
- `deepFreeze()` — 完全相同
- `registerGuide()` — 完全相同
- `audioFilesForAllLocales()` / `zhAudio()` — 功能相同，名字不同

**抽象为**：共享 `yui-guide-common.js` 模块导出这三个工具函数。

---

### B 类：中等重复 / 可参数化的流程

#### B1. 花瓣转场生命周期

**重复次数**：5+ 处

花瓣转场有两条触发路径：
1. **台词进度触发**：`playAvatarFloatingPetalTransitionAtCue()` — 在 `petalTransition: true` 的 scene 中，约 70% 台词进度处触发。
2. **归还控制权触发**：`playReturnPetalTransition()` — Day 1 键鼠控制归还时触发。

两条路径共享：`loadReturnPetalSequence()` 加载序列、转场层 setup、overlay/PC bridge 渲染和清理逻辑。

**完整的花瓣转场通用流程**：
```
1. 检查 PC overlay 是否支持花瓣渲染
2. 加载花瓣序列资源
3. 在约 N% 台词进度处触发
4. 触发瞬间：同步隐藏 Ghost Cursor + 清理所有高光 + 启动花瓣层
5. PC overlay: 发送 petal patch
6. DOM fallback: 创建花瓣 DOM 元素
7. 花瓣动画播放完毕：写入完成态 + 清理
```

**变化点**：
- 触发时机（70% / 自定义百分比）
- 花瓣起始位置
- 是否写入完成态
- 是否播放模型渐隐

**抽象为**：`PetalTransitionController`：

```javascript
class PetalTransitionController {
  /** 在台词指定进度处触发花瓣转场 */
  playAtCue(narrationHandle, cuePercent: number, options?: PetalOptions): Promise<void>

  /** 立即播放花瓣转场（非台词触发场景） */
  playNow(options?: PetalOptions): Promise<void>

  /** 取消花瓣转场（skip/angry exit 时） */
  cancel(): void
}
```

---

#### B2. 模型替身图片演出生命周期

**重复次数**：Day 2-7 每日 2 次，但逻辑已由 `YuiGuideAvatarStandIn` 统一管理。

当前替身演出的完整流程：
```
1. Director 在 scene 台词开始后 ~900ms 查找替身 cue
2. 调用 overlay.showAvatarStandIn({ resource, position })
3. 同时临时隐藏教程模型
4. 5 秒后自动恢复模型并移除替身层
5. PC overlay: 发送 avatarStandIn patch
6. DOM fallback: 创建替身图片 DOM
```

**当前问题**：替身 cue 硬编码在 `YuiGuideAvatarStandIn` 的 `CUES` 映射表中，与 scene 配置分离。scene 配置中无法声明替身需求。

**优化为**：替身声明合并到 scene 配置中：

```javascript
{
  id: 'day2_intro_context',
  textKey: '...',
  voiceKey: '...',
  avatarStandIn: {
    resource: 'peek-head',          // 资源名
    position: 'bottom-right',       // 摆放位置
    delayMs: 900,                   // 台词开始后延迟
    durationMs: 5000,               // 持续时间
  },
}
```

`SceneOrchestrator` 在播放 scene 时自动读取 `avatarStandIn` 配置并触发演出，不再需要独立的硬编码映射表。

---

#### B3. 完整清理 / Teardown 序列

**重复次数**：15+ 处

```javascript
this.closeChatToolPopover();
this.clearExternalizedChatGuideTarget({ clearCursor: ... });
this.collapseAvatarFloatingSidePanelsExcept(null);
this.clearSceneExtraSpotlights();
this.clearRetainedExtraSpotlights();
this.clearSpotlightGeometryHints();
this.clearSpotlightVariantHints();
this.overlay.clearActionSpotlight();
await this.closeManagedPanels().catch(() => {});
this.collapseAgentSidePanel('agent-user-plugin');
this.collapseAgentSidePanel('agent-openclaw');
this.collapseCharacterSettingsSidePanel();
```

**变化点**：是否保留外置聊天窗状态、是否清除 cursor。

**抽象为**：`performFullCleanup({ preserveExternalized, clearCursor })` 统一方法。

---

#### B4. Ghost Cursor Look-At 生命周期

**重复次数**：16+ 对 start/stop

```javascript
// 启动
const handle = await this.startGhostCursorLookAtPerformance({ isCancelled: () => guardFailed() });
// ... 使用 ...
// 停止
await this.stopPersistentGhostCursorLookAtPerformance(reason);
```

有 4 种 start 变体和 2 种 stop 变体，本质上都是「启动模型目光追踪 → 使用 → 停止」。

**抽象为**：RAII 风格的 `withLookAt(options, async (handle) => { ... })` 包装器，自动清理。

---

#### B5. 外置聊天窗守卫 + 调用模式

**重复次数**：25+ 处原始调用

```javascript
if (this.interactionTakeover && typeof this.interactionTakeover.setExternalizedChatSpotlight === 'function') {
    this.interactionTakeover.setExternalizedChatSpotlight(...);
}
```

已有 `setExternalizedChatGuideTarget()` 和 `setExternalizedChatCursorEffect()` 部分抽象，但 25+ 处仍直接调用原始模式。

**优化为**：统一所有调用点使用已有包装方法；重构后将外置/内置模式差异封装到 `SpotlightController` 和 `GhostCursorController` 内部。

---

#### B6. Spotlight 几何设置 + 保留 + 移除三联操作

**重复次数**：10+ 处

```javascript
this.setSpotlightGeometryHint(element, { padding: 4, geometry: 'circle' });
this.addRetainedExtraSpotlight(element);
// ... 使用 ...
this.removeRetainedExtraSpotlight(element);
```

**抽象为**：`SpotlightController.highlightFloatingButton(element, options)` / `unhighlightFloatingButton(element)`，内部封装三步操作。

---

#### B7. `scaleSceneMs()` 时序缩放函数重复声明

**重复次数**：5 处局部重新声明

每个复杂接管序列方法都声明自己的 `scaleSceneMs(value, minValue, maxValue)`：

```javascript
const scaleSceneMs = (value, minValue, maxValue) => {
    const baseValue = Number.isFinite(value) ? value : 0;
    const scaledValue = Math.round(baseValue * timingScale);
    return clamp(scaledValue, ...);
};
```

**抽象为**：实例方法 `createSceneScaler(voiceKey)` 或 `scaleSceneMs(value, minValue, maxValue, voiceKey)`。

---

#### B8. Agent 面板打开 + Toggle 点击 + 状态验证序列

**重复次数**：6+ 处

```
1. 打开 Agent 面板
2. waitForElement(toggleId)
3. 高亮 toggle
4. Ghost Cursor 移到 toggle 并点击
5. waitForAgentToggleState(toggleId, checked)
```

已有 `performHighlightedApiClick()` 部分封装，但多个调用点仍手动编排。

**抽象为**：扩展 `OperationRegistry` 中的 `agent-toggle-click` 操作，统一处理面板打开、等待、高亮、点击和验证。

---

### C 类：架构级重复 / 策略模式

#### C1. 外置聊天窗 vs 本地 DOM 双路径分支

**重复次数**：50+ 处

```javascript
if (this.isHomeChatExternalized()) {
    // 通过 interactionTakeover 操作外置聊天窗
} else {
    // 直接操作本地 DOM
}
```

这是最根本的架构分支，几乎影响所有与聊天窗交互的方法。

**优化为**：引入 `ChatWindowAdapter` 策略接口：

```javascript
class ChatWindowAdapter {
  /** 获取输入区 rect */
  getInputRect(): ScreenRect
  /** 高亮聊天窗 */
  highlightSpotlight(kind: string, options): void
  /** 设置 cursor 目标 */
  setCursorTarget(kind: string): void
  /** 清除高亮 */
  clearSpotlight(): void
  /** 清除 cursor */
  clearCursor(): void
  /** 解析目标为 screen 坐标 */
  resolveTargetToScreenRect(target): ScreenRect
}

// 两个实现
class LocalChatWindowAdapter extends ChatWindowAdapter { ... }
class ExternalizedChatWindowAdapter extends ChatWindowAdapter { ... }
```

所有 Scene Handler 只与 `ChatWindowAdapter` 交互，不再写 `isHomeChatExternalized()` 分支。

---

#### C2. 每日 Round 启动前准备序列

**重复次数**：Day 1-7 每天都执行

```
1. resetHomeTutorialDay(day)
2. startAvatarFloatingGuideDay(day)
3. UniversalTutorialManager.startAvatarFloatingGuideRound(day)
4. 临时切到 yui-origin 模型
5. 确认模型可见
6. 等待 1500ms（不生成聊天头像截图、不播放 idle/sway、不套用常驻表情）
7. 禁用 proactive/Galgame/greeting
8. 启动 Ghost Cursor look-at
9. 开始播放每日 round
```

**抽象为**：`RoundPreludeController.play(day, roundConfig)` 统一执行 9 步准备序列。

---

#### C3. 每日收尾三句通用流程

**重复次数**：Day 1-7 每天都有

```
1. 关闭临时面板（设置弹窗、侧边栏、Agent 面板、工具菜单、HUD）
2. 清理所有非 persistent 高光
3. 圆角矩形高亮胶囊输入框
4. Ghost Cursor 平滑移动到胶囊输入框中心
5. 最终句约 70% 处触发花瓣 cue
6. 花瓣 cue 触发瞬间：隐藏 Ghost Cursor + 清理所有高光
7. 花瓣动画播放完毕：写入完成态
```

**变化点**：
- 收尾前关闭的临时面板类型不同（Day 2 关闭设置面板、Day 3 关闭工具菜单、Day 6 关闭 HUD）
- Ghost Cursor 移动到胶囊输入框的起点不同（从上一句锚点开始）

**抽象为**：`SceneOrchestrator.playWrapScene(cleanupTargets, cursorStartAnchor)` 通用收尾方法。

---

#### C4. 设置类 Scene 通用流程（打开设置 → 高亮入口 → 展开侧边栏 → 椭圆运动 → 收起）

**重复次数**：Day 2、Day 4、Day 5 各有 1-2 处

```
1. 圆形高亮设置按钮
2. Ghost Cursor 移到设置按钮并模拟点击
3. 并行调用打开设置 API
4. 设置按钮高光作为 persistent 保留
5. 圆角矩形高亮侧边栏入口按钮
6. Ghost Cursor 移到入口按钮并模拟点击
7. 展开侧边栏
8. 高光从入口按钮过渡到侧边栏容器
9. Ghost Cursor 在侧边栏内做椭圆运动
10. 台词播放完毕后收起侧边栏并清理高光
```

**变化点**：
- 设置按钮后的侧边栏类型不同（character-settings / chat-settings / animation-settings）
- 是否需要点击入口按钮（有时只需移动指认）

**抽象为**：`SettingsTourFlow.play(settingsButtonTarget, sidePanelType, options)` 通用方法：

```javascript
class SettingsTourFlow {
  constructor(options) {
    // options: { cursor, spotlight, sidebarPause, operationRegistry }
  }

  /** 完整设置巡游流程 */
  async play(config: {
    entryButton: string,           // 设置按钮选择器
    sidePanelType: string,         // 侧边栏类型
    clickEntry: boolean,           // 是否点击设置按钮（默认 true）
    clickSidePanelButton: boolean, // 是否点击侧边栏入口按钮
    doEllipse: boolean,            // 是否在侧边栏内做椭圆运动
    persistentEntry: boolean,      // 是否保持设置按钮 persistent 高光
    persistentUntilSceneId: string,// persistent 高光保持到哪个 scene
    pauseAware: boolean,           // 侧边栏展开是否配合对抗暂停
  }): Promise<void>

  /** 收起侧边栏并清理 */
  async teardown(): Promise<void>
}
```

---

#### C5. 模型切换 + 等待 + 恢复序列

**重复次数**：每日 Round 启动时执行，异常退出时恢复

```
启动：
1. TutorialAvatarReloadController.beginOverride() → 切到 yui-origin
2. 等待模型可见
3. 等待 1500ms
4. 开始 round

恢复（skip/angry exit/destroy/pagehide/handoff 失败）：
1. 停止所有教程动作
2. 清理替身
3. TutorialAvatarReloadController.requestRestore()
4. 恢复用户原模型、聊天头像身份
```

**抽象为**：`TutorialAvatarReloadController` 已存在且功能完整，但启动序列中的「切模 → 确认可见 → 等待 1500ms → 禁用 proactive」应组合为 `prepareRoundEnvironment(day)` 统一方法。

---

### 重复模式总表

| 编号 | 模式名称 | 重复次数 | 文件范围 | 抽象方式 |
|------|---------|---------|---------|---------|
| A1 | Scene Preamble | 7+ | director.js | `prepareNarration(scene)` |
| A2 | Scene Postamble | 7+ | director.js | `finalizeScene(runId, opts)` |
| A3 | Guard / Run-ID 检查 | 274 | director.js | `isGuardFailed(runId)` |
| A4 | Narration + Catch | 7+ | director.js | `NarratorController.narrate()` |
| A5 | Overlay Clear 序列 | 20+ | director.js | `SpotlightController.clearAll()` |
| A6 | Day Guide 样板代码 | 3 | day1/day4/day6 | `yui-guide-common.js` |
| B1 | 花瓣转场生命周期 | 5+ | director.js + overlay.js | `PetalTransitionController` |
| B2 | 模型替身演出 | 12 (Day2-7各2) | director.js + standin.js | 配置合并到 scene |
| B3 | 完整清理/Teardown | 15+ | director.js | `performFullCleanup(opts)` |
| B4 | Look-At 生命周期 | 16+ | director.js | `withLookAt()` RAII 包装 |
| B5 | 外置聊天窗守卫+调用 | 25+ | director.js | 统一使用包装方法 |
| B6 | Spotlight 几何三联操作 | 10+ | director.js | `highlightButton/unhighlightButton` |
| B7 | scaleSceneMs 重复声明 | 5 | director.js | 实例方法 |
| B8 | Agent Toggle 操作序列 | 6+ | director.js | 扩展 OperationRegistry |
| C1 | 外置/本地双路径分支 | 50+ | director.js | `ChatWindowAdapter` 策略 |
| C2 | Round 启动前准备序列 | 7 | director.js + manager.js | `RoundPreludeController` |
| C3 | 每日收尾三句流程 | 7 | director.js | `playWrapScene()` |
| C4 | 设置类 Scene 巡游流程 | 4+ | director.js | `SettingsTourFlow` |
| C5 | 模型切换+恢复序列 | 7+ | director.js + manager.js | `prepareRoundEnvironment()` |

---

## 补充模块（由重复流程分析驱动）

在前面核心 9 模块（GhostCursorController、SpotlightController、ResistanceController、TutorialOverlayRenderer、SceneOrchestrator、OperationRegistry、NarratorController、CursorAnchorStore、SidebarPauseController）的基础上，补充以下模块：

### 补充模块 1：`PetalTransitionController`

**来源**：B1 花瓣转场生命周期

**职责**：统一管理花瓣转场的完整生命周期——资源加载、进度 cue 监听、PC overlay / DOM 渲染和清理。

```javascript
class PetalTransitionController {
  constructor(options) {
    // options: { overlay: TutorialOverlayRenderer, narrator: NarratorController }
  }

  /** 在台词指定进度处触发花瓣转场（每日收尾场景使用） */
  playAtCue(narrationHandle, cuePercent: number, options?: {
    origin?: { x: number, y: number },  // 花瓣起始位置
    writeCompletionState?: string,       // 写入完成态的 key
    hideCursor?: boolean,                // 是否隐藏 Ghost Cursor
  }): Promise<void>

  /** 立即播放花瓣转场（Day 1 键鼠归还等非台词触发场景） */
  playNow(options?: PetalOptions): Promise<void>

  /** 取消花瓣转场（skip/angry exit 时） */
  cancel(): void

  /** 加载花瓣序列资源（预加载） */
  preloadSequence(): Promise<void>

  destroy(): void
}
```

**当前问题**：`playAvatarFloatingPetalTransitionAtCue()` 和 `playReturnPetalTransition()` 各自实现加载、渲染和清理，共享的 `loadReturnPetalSequence()` 被两者重复调用。

---

### 补充模块 2：`RoundPreludeController`

**来源**：C2 每日 Round 启动前准备序列 + C5 模型切换+恢复序列

**职责**：封装每日教程启动前的完整准备序列。

```javascript
class RoundPreludeController {
  constructor(options) {
    // options: {
    //   avatarReload: TutorialAvatarReloadController,
    //   featureController: NekoHomeTutorialFeatureController,
    //   cursor: GhostCursorController,
    //   overlay: TutorialOverlayRenderer,
    //   standIn: AvatarStandInController,
    // }
  }

  /** 执行每日 Round 启动前完整准备 */
  async prepare(day: number): Promise<void> {
    // 1. 切到 yui-origin 模型
    // 2. 等待模型可见
    // 3. 等待 1500ms（不生成聊天头像截图、不播放 idle/sway、不套用常驻表情）
    // 4. 禁用 proactive/Galgame/greeting
    // 5. 启动 Ghost Cursor look-at
    // 6. 通知 WebSocket home_tutorial_state 阻塞后端 greeting
  }

  /** 异常恢复（skip/angry exit/destroy/pagehide/handoff 失败） */
  async restore(): Promise<void> {
    // 1. 停止所有教程动作
    // 2. 清理替身
    // 3. 恢复用户原模型、聊天头像身份
    // 4. 恢复 proactive/Galgame/greeting
  }

  destroy(): void
}
```

---

### 补充模块 3：`ChatWindowAdapter`（策略接口）

**来源**：C1 外置聊天窗 vs 本地 DOM 双路径分支（50+ 处）

**职责**：将外置聊天窗和本地 DOM 的操作差异封装为统一接口，消除 `isHomeChatExternalized()` 分支。

```javascript
/** 策略接口 */
class ChatWindowAdapter {
  /** 获取聊天窗输入区 screen rect */
  getInputRect(): ScreenRect | null

  /** 获取聊天窗整体 screen rect */
  getWindowRect(): ScreenRect | null

  /** 解析目标元素为 screen rect */
  resolveTargetToScreenRect(target): ScreenRect | null

  /** 设置高亮 */
  setSpotlight(kind: string, rect: ScreenRect, options?: SpotlightOptions): void

  /** 清除高亮 */
  clearSpotlight(kind?: string): void

  /** 设置 cursor 目标 */
  setCursorTarget(kind: string, rect: ScreenRect): void

  /** 清除 cursor */
  clearCursor(): void

  /** 判断是否可用 */
  isAvailable(): boolean
}

/** 本地 DOM 实现 */
class LocalChatWindowAdapter extends ChatWindowAdapter { ... }

/** 外置聊天窗实现（通过 interactionTakeover + postMessage 通信） */
class ExternalizedChatWindowAdapter extends ChatWindowAdapter { ... }
```

**选择逻辑**：`SceneOrchestrator` 根据 `isHomeChatExternalized()` 在初始化时选择 adapter，之后所有 scene handler 只与 adapter 交互。

---

### 补充模块 4：`SettingsTourFlow`（设置巡游流程）

**来源**：C4 设置类 Scene 通用流程

**职责**：封装「打开设置 → 高亮入口 → 展开侧边栏 → 椭圆运动 → 收起」的完整设置巡游流程。

```javascript
class SettingsTourFlow {
  constructor(options) {
    // options: { cursor, spotlight, sidebarPause, operationRegistry }
  }

  /** 完整设置巡游流程 */
  async play(config: {
    entryButton: string,            // 设置按钮选择器
    sidePanelType: string,          // 侧边栏类型（'chat-settings' | 'animation-settings' | 'character-settings'）
    clickEntry: boolean,            // 是否点击设置按钮（默认 true）
    clickSidePanelButton: boolean,  // 是否点击侧边栏入口按钮
    doEllipse: boolean,             // 是否在侧边栏内做椭圆运动
    persistentEntry: boolean,       // 是否保持设置按钮 persistent 高光
    persistentUntilSceneId: string, // persistent 高光保持到哪个 scene
    pauseAware: boolean,            // 侧边栏展开是否配合对抗暂停
  }): Promise<void>

  /** 收起侧边栏并清理（台词播放完毕后调用） */
  async teardown(config: {
    closeSidePanel: boolean,        // 是否收起侧边栏
    closeSettings: boolean,         // 是否关闭整个设置弹窗
    clearPersistent: boolean,       // 是否清理 persistent 高光
  }): Promise<void>
}
```

**使用场景**：
- Day 2：`day2_personalization_space` + `day2_personalization_detail`（character-settings）
- Day 4：`day4_chat_settings`（chat-settings）+ `day4_model_behavior`（animation-settings）
- Day 5：`day5_character_settings`（character-settings）

---

### 补充模块 5：`SceneLifecycleHelpers`（场景生命周期辅助方法）

**来源**：A1 Scene Preamble + A2 Scene Postamble + A3 Guard 检查 + A4 Narration+Catch + A5 Overlay Clear + B3 清理序列 + B7 scaleSceneMs

**职责**：将 scene 播放中重复出现的小块逻辑封装为共享方法。

```javascript
/** 附加到 SceneOrchestrator 的辅助方法集 */
const SceneLifecycleHelpers = {
  /** A1: 准备台词、情绪、按钮 */
  prepareNarration(scene): NarrationContext,

  /** A2: 守卫检查 + 按钮等待 + 延迟 */
  finalizeScene(runId, opts): Promise<boolean>,

  /** A3: 统一守卫检查 */
  isGuardFailed(runId): boolean,

  /** A4: 创建台词播放 Promise（含 catch） */
  createNarrationPromise(text, voiceKey, minDurationMs): Promise<void>,

  /** A5: 清除所有高光 */
  clearAllSpotlights(): void,

  /** B3: 完整清理序列 */
  performFullCleanup(opts: { preserveExternalized?: boolean, clearCursor?: boolean }): Promise<void>,

  /** B7: 时序缩放 */
  scaleSceneMs(value, minValue, maxValue, voiceKey): number,

  /** B4: RAII 风格 Look-At */
  withLookAt(options, asyncFn): Promise<void>,
};
```

---

### 补充模块 6：`yui-guide-common.js`（Day Guide 共享工具）

**来源**：A6 Day Guide 样板代码

**职责**：抽取各天 guide 文件中完全相同的工具函数。

```javascript
// yui-guide-common.js
export function deepFreeze(value) { ... }
export function registerGuide(config) { ... }
export function audioFilesForAllLocales(fileName) { ... }
```

各天 guide 文件改为：
```javascript
import { deepFreeze, registerGuide, audioFilesForAllLocales } from './yui-guide-common.js';
```

---

### 更新后的完整模块依赖关系

```
SceneOrchestrator
  ├── GhostCursorController
  │     ├── CursorAnchorStore
  │     └── TutorialOverlayRenderer
  ├── SpotlightController
  │     └── TutorialOverlayRenderer
  ├── NarratorController
  ├── ResistanceController
  │     ├── GhostCursorController
  │     ├── SpotlightController
  │     ├── NarratorController
  │     └── PauseCoordinator
  ├── SidebarPauseController
  │     └── PauseCoordinator
  ├── PetalTransitionController      ← 新增
  │     └── TutorialOverlayRenderer
  ├── RoundPreludeController          ← 新增
  │     └── TutorialAvatarReloadController
  ├── ChatWindowAdapter               ← 新增
  │     ├── LocalChatWindowAdapter
  │     └── ExternalizedChatWindowAdapter
  ├── SettingsTourFlow                ← 新增
  │     ├── GhostCursorController
  │     ├── SpotlightController
  │     └── SidebarPauseController
  ├── SceneLifecycleHelpers           ← 新增
  ├── PauseCoordinator
  ├── OperationRegistry
  └── TutorialOverlayRenderer
```

---

### 更新后的文件结构规划

```
static/
  tutorial/
    ghost-cursor-controller.js          // GhostCursorController
    spotlight-controller.js             // SpotlightController
    resistance-controller.js            // ResistanceController
    pause-coordinator.js                // PauseCoordinator
    sidebar-pause-controller.js         // SidebarPauseController
    tutorial-overlay-renderer.js        // TutorialOverlayRenderer
    cursor-anchor-store.js             // CursorAnchorStore
    narrator-controller.js              // NarratorController
    operation-registry.js               // OperationRegistry
    scene-orchestrator.js               // SceneOrchestrator + SceneLifecycleHelpers
    petal-transition-controller.js      // PetalTransitionController        ← 新增
    round-prelude-controller.js         // RoundPreludeController           ← 新增
    chat-window-adapter.js             // ChatWindowAdapter 策略接口       ← 新增
    settings-tour-flow.js              // SettingsTourFlow                 ← 新增
    index.js                            // 统一导出
  yui-guide-common.js                   // Day Guide 共享工具              ← 新增
  yui-guide-director.js                 // 保留，瘦身为核心调度
  yui-guide-overlay.js                  // 保留，降级为 DOM fallback 渲染
  tutorial-interaction-takeover.js      // 保留，接入 PauseCoordinator
  tutorial-interrupt-controller.js      // Phase 4 删除
  tutorial-highlight-controller.js      // Phase 4 删除
  tutorial-skip-controller.js           // 保留
  tutorial-avatar-reload-controller.js  // 保留，被 RoundPreludeController 使用
  yui-guide-day*.js                     // 逐步迁移为声明式配置，移除样板代码
```

---

## 原模块划分（核心 9 模块）

以下为初始设计的 9 个核心模块，API 不变。

### 模块 1：`GhostCursorController`

**职责**：管理 Ghost Cursor 的完整生命周期，包括显示、移动、点击、晃动、对抗位移、椭圆轨道、暂停/恢复和锚点持久化。

**当前代码位置**：
- `YuiGuideGhostCursor` 类 — `yui-guide-director.js:2310+`
- `YuiGuideOverlay` 中的 cursor 相关方法 — `yui-guide-overlay.js:1840+`
- PC Overlay Bridge 的 cursor patch — `yui-guide-overlay.js:93+`

**重构后的 API**：

```javascript
class GhostCursorController {
  constructor(options) {
    // options: { overlayBridge, anchorStore, positionTracker }
  }

  // ─── 基础操作 ───

  /** 在指定位置显示 cursor（仅首次或被清除后使用） */
  showAt(x, y, options?: { immediate?: boolean }): Promise<void>

  /** 平滑移动到目标位置
   *  target 可以是 DOM 元素、screen rect、或逻辑目标名（如 'chat-capsule-input'）
   *  options.durationMs: 覆盖默认移动时长
   *  options.easing: 覆盖默认缓动曲线
   *  options.onSettled: 移动完成回调
   */
  moveTo(target, options?: MoveOptions): Promise<AnchorPoint>

  /** 在当前位置播放模拟点击动画
   *  options.on-clickStart: 点击动画开始时回调（用于并行触发真实 API/DOM 操作）
   *  options.durationMs: 点击动画时长
   */
  click(options?: ClickOptions): Promise<void>

  /** 在当前位置播放左右晃动动画 */
  wobble(durationMs?: number): Promise<void>

  /** 保持当前位置不变，不触发新动画 */
  hold(): void

  /** 隐藏 cursor（仅用于 skip/angry exit/destroy/收尾花瓣 cue） */
  hide(): void

  // ─── 暂停/恢复 ───

  /** 暂停当前动画，冻结在当前位置 */
  pause(): void

  /** 从暂停位置恢复之前的动画 */
  resume(): void

  /** 注册暂停令牌，用于与高亮、侧边栏等协同暂停 */
  getPauseToken(): PauseToken

  // ─── 对抗机制 ───

  /** 常驻轻微对抗：朝用户鼠标反方向做轻微位移后回弹
   *  由 ResistanceController 在每次真实鼠标移动时调用
   */
  reactToUserMotion(userX, userY, options?: { scale?: number }): void

  /** 轻微打断对抗：做一次更明显的反向位移 */
  resistTo(userX, userY, options?: ResistOptions): Promise<void>

  // ─── 锚点管理 ───

  /** 记录当前 scene 的 cursor 锚点 */
  saveAnchor(sceneId: string): void

  /** 读取指定 scene 的 cursor 锚点 */
  getAnchor(sceneId: string): AnchorPoint | null

  /** 失效指定 scene 的 cursor 锚点 */
  invalidateAnchor(sceneId: string): void

  /** 获取当前可见位置（用于模型 look-at） */
  getCurrentPosition(): { x: number, y: number }

  // ─── 椭圆轨道 ───

  /** 在指定区域内做椭圆运动
   *  自动响应 pause/resume
   */
  runEllipse(centerX, centerY, radiusX, radiusY, options?: EllipseOptions): Promise<void>

  // ─── 生命周期 ───

  destroy(): void
}
```

**核心设计原则**：

1. **单一渲染通道**：`GhostCursorController` 内部自动判断 PC overlay 是否可用，调用方无需关心渲染层差异。PC bridge 可用时所有动画指令发往 PC overlay；不可用时降级到 DOM 渲染。
2. **Token 取消**：所有长时间动画（move、ellipse）内部维护 `motionToken`，`pause()` 自增 token 使旧动画回调失效；`resume()` 启动新动画从暂停点继续。
3. **锚点自动管理**：每次 `moveTo` 成功完成后自动记录锚点；`moveTo` 在目标不可达时自动读取上一 scene 锚点作为起点，避免从默认点闪现。
4. **对抗与暂停解耦**：`reactToUserMotion` 和 `resistTo` 是纯视觉方法，不处理打断逻辑；打断计数和台词触发由 `ResistanceController` 统一管理。

---

### 模块 2：`SpotlightController`

**职责**：统一管理所有高亮（圆角矩形、圆形、union、persistent、secondary、extra），提供声明式 API 和暂停机制。

**当前代码位置**：
- `YuiGuideOverlay` 中的 spotlight 相关方法 — `yui-guide-overlay.js:592+`
- `TutorialHighlightController` — `tutorial-highlight-controller.js`

**重构后的 API**：

```javascript
class SpotlightController {
  constructor(options) {
    // options: { overlayBridge, selectorResolver }
  }

  // ─── 高亮操作 ───

  /** 高亮目标
   *  target: DOM 元素、CSS 选择器、或逻辑目标名
   *  options.shape: 'rounded-rect' | 'circle' | 'auto'（默认 auto，自动检测）
   *  options.variant: 'default' | 'plain-capsule' | 'plain-circle' | 'thin' | 'circle-image'
   *  options.tier: 'primary' | 'persistent' | 'secondary' | 'extra'（默认 primary）
   *  options.padding: { top, right, bottom, left }
   *  options.radius: 自定义圆角半径
   *  options.transition: 'smooth' | 'immediate'（默认 immediate）
   *  options.geometry: 'circle' | 'rounded-rect'（强制覆盖自动检测）
   */
  highlight(target, options?: SpotlightOptions): Promise<SpotlightRef>

  /** 平滑过渡：从当前高亮位置/大小动画过渡到新目标 */
  transitionTo(currentRef, newTarget, options?: SpotlightOptions): Promise<SpotlightRef>

  /** 清除指定高亮
   *  不指定 ref 时清除当前 scene 的所有非 persistent 高亮
   */
  clear(ref?: SpotlightRef): void

  /** 清除所有高亮（包括 persistent） */
  clearAll(): void

  // ─── 暂停/恢复 ───

  /** 暂停高亮动画（冻结在当前状态，对抗期间使用） */
  pause(): void

  /** 恢复高亮动画 */
  resume(): void

  /** 注册暂停令牌 */
  getPauseToken(): PauseToken

  // ─── 工具方法 ───

  /** 检测目标是否应为圆形高亮 */
  isCircularTarget(target): boolean

  /** 获取目标的 screen rect（供 cursor 锚点使用） */
  getTargetScreenRect(target): ScreenRect

  /** 刷新所有活跃高亮的目标位置（窗口大小变化、滚动时调用） */
  refreshPositions(): void

  // ─── 生命周期 ───

  destroy(): void
}
```

**核心设计原则**：

1. **自动形状检测**：`shape: 'auto'` 时通过目标元素的 CSS 类名、data 属性和尺寸比例自动判断圆形/圆角矩形；调用方不再需要手动指定。
2. **分层管理**：`tier` 参数区分 primary / persistent / secondary / extra；同一目标同一时刻只允许一套同 tier 高亮，避免重叠。
3. **平滑过渡**：`transitionTo` 方法在 spotlight 位置/尺寸变化时使用 CSS transition 或 PC overlay 动画平滑过渡，避免高亮闪烁。
4. **暂停协同**：`getPauseToken()` 返回的令牌与 GhostCursorController 的令牌共同注册到 `PauseCoordinator`，对抗期间同步暂停。

---

### 模块 3：`ResistanceController`

**职责**：封装完整的对抗机制链路——真实鼠标检测 → 轻微对抗 → 计数升级 → 暂停演出 → 播放打断台词 → 恢复演出 → 生气退出。

**当前代码位置**：
- `TutorialInterruptController` — `tutorial-interrupt-controller.js`
- `YuiGuideDirector` 中的 `interruptsEnabled`、`interruptQualifyingMoveStreak` — `yui-guide-director.js:2560+`

**重构后的 API**：

```javascript
class ResistanceController {
  constructor(options) {
    // options: {
    //   cursor: GhostCursorController,
    //   spotlight: SpotlightController,
    //   pauseCoordinator: PauseCoordinator,
    //   narrator: NarratorController,
    //   thresholds: { moveDistance, acceleration, qualifyingCount, angryExitCount }
    // }
  }

  // ─── 生命周期 ───

  /** 开始监听真实鼠标移动 */
  activate(): void

  /** 停止监听（skip/destroy/收尾时调用） */
  deactivate(): void

  /** 暂停对抗检测（教程场景间切换时短暂禁用） */
  pauseDetection(): void

  /** 恢复对抗检测 */
  resumeDetection(): void

  // ─── 配置 ───

  /** 设置打断台词列表（循环使用） */
  setResistanceDialogs(dialogs: ResistanceDialog[]): void

  /** 设置生气退出台词 */
  setAngryExitDialog(dialog: ResistanceDialog): void

  /** 设置回调 */
  onAngryExit(callback: () => Promise<void>): void

  // ─── 状态查询 ───

  /** 当前是否处于对抗演出中 */
  isActive(): boolean

  /** 当前累计有效对抗次数 */
  getQualifyingCount(): number

  // ─── 生命周期 ───

  destroy(): void
}
```

**对抗暂停流程**：

```
真实鼠标移动
  │
  ├─ 常驻轻微对抗：cursor.reactToUserMotion()
  │   不暂停任何演出，只做视觉位移
  │
  ├─ 移动距离/加速度超过阈值？
  │   └─ 是：累计 qualifyingCount++
  │
  ├─ qualifyingCount >= 3 且 < angryExitThreshold？
  │   └─ 轻微打断：
  │      1. pauseCoordinator.pause() — 同时暂停 cursor、高亮、侧边栏、台词
  │      2. cursor.resistTo() — 更明显的对抗位移
  │      3. narrator.playResistance() — 播放打断台词
  │      4. pauseCoordinator.resume() — 恢复所有暂停元素
  │
  └─ qualifyingCount >= angryExitThreshold（默认第 3 次）？
      └─ 生气退出：
         1. pauseCoordinator.pause()
         2. 清理所有高光和 cursor
         3. narrator.playAngryExit()
         4. 调用统一 skip/destroy 退出教程
```

**`PauseCoordinator` 协同暂停机制**：

```javascript
class PauseCoordinator {
  /** 注册可暂停组件 */
  register(name: string, token: PauseToken): void

  /** 暂停所有已注册组件 */
  pause(): void

  /** 恢复所有已注册组件 */
  resume(): void

  /** 暂停指定组件 */
  pauseOnly(names: string[]): void

  /** 恢复指定组件 */
  resumeOnly(names: string[]): void

  /** 清除所有注册 */
  destroy(): void
}
```

---

### 模块 4：`TutorialOverlayRenderer`（PC 全局透明 Overlay 统一渲染层）

**职责**：统一管理 PC 全局透明 overlay 的所有视觉输出（cursor、高亮、花瓣、模型替身），提供单一渲染通道。

**当前代码位置**：
- `createPcOverlayBridge()` — `yui-guide-overlay.js:93+`
- `N.E.K.O.-PC/src/avatar-tool-cursor-service.js`（PC 端渲染服务）

**重构后的 API**：

```javascript
class TutorialOverlayRenderer {
  constructor(options) {
    // options: { hostBridge, fallbackRenderer }
  }

  // ─── 连接管理 ───

  /** 检测 PC overlay 是否可用 */
  isAvailable(): boolean

  /** 初始化连接 */
  connect(): Promise<void>

  /** 断开连接 */
  disconnect(): void

  // ─── 批量更新 ───

  /** 开始一个渲染帧（内部积累状态变更） */
  beginFrame(): void

  /** 提交当前渲染帧（一次性发送所有状态变更到 overlay） */
  commitFrame(): void

  // ─── 状态更新（在 beginFrame/commitFrame 之间调用） ───

  /** 更新 cursor 状态 */
  setCursor(state: CursorState): void

  /** 更新 spotlight 列表 */
  setSpotlights(spotlights: SpotlightState[]): void

  /** 更新花瓣转场 */
  setPetal(state: PetalState): void

  /** 更新模型替身 */
  setAvatarStandIn(state: StandInState): void

  // ─── 完整状态包 ───

  /** 发送完整可见状态（确保 cursor + spotlight + petal + standIn 同时携带） */
  sendFullState(state: FullOverlayState): void

  // ─── 降级 ───

  /** 获取 DOM fallback 渲染器（PC bridge 不可用时） */
  getFallbackRenderer(): DomFallbackRenderer

  // ─── 生命周期 ───

  destroy(): void
}
```

**核心设计原则**：

1. **帧批处理**：`beginFrame()` / `commitFrame()` 确保同一次 update 携带完整可见状态，避免 cursor 和 spotlight 交替清空导致闪烁。
2. **完整状态包**：每次 update 必须包含所有活跃元素的完整状态；不允许只发 cursor 不发 spotlight，或只发 spotlight 不发 cursor。
3. **自动降级**：当 `hostBridge` 不可用或 IPC 超时时，自动切换到 `DomFallbackRenderer`，业务代码无感知。
4. **z-order 保活**：在 active run 期间持续 `moveTop()` reassert（≤160ms 间隔），确保 overlay 始终压过 Pet 主窗口。

---

### 模块 5：`SceneOrchestrator`（场景生命周期声明式编排）

**职责**：根据 scene 配置声明自动编排时序，替代当前各天手写的 `playAvatarFloatingScene` handler。

**当前代码位置**：
- `playAvatarFloatingScene()` — `yui-guide-director.js`
- `playAvatarFloatingRound()` — `yui-guide-director.js`
- 各天 scene handler（day1~day7 文件中的 day-specific logic）

**重构后的 scene 配置格式**：

```javascript
const day1Config = {
  day: 1,
  round: {
    scenes: [
      {
        id: 'day1_intro_greeting',
        text: '微风、阳光...',
        voiceKey: 'avatar_floating_day1_intro_greeting',
        emotion: 'happy',

        // 高亮配置
        spotlight: {
          target: 'chat-input',         // CSS 选择器或逻辑名
          shape: 'rounded-rect',        // 自动检测时用 'auto'
          variant: 'default',
          tier: 'primary',
        },

        // Cursor 配置
        cursor: {
          action: 'move',               // 'move' | 'click' | 'wobble' | 'hold' | 'ellipse'
          target: 'chat-capsule-input',  // 可与 spotlight.target 不同
          durationMs: 760,              // 覆盖默认时长
        },

        // 业务操作（Director API 调用）
        operations: [
          // 无额外操作
        ],

        // 生命周期钩子（仅在声明式配置无法满足时使用）
        onPrepare: null,
        onComplete: null,
      },
      {
        id: 'day1_history_handle',
        text: '戳一下聊天框上面的...',
        voiceKey: 'avatar_floating_day1_history_handle',
        emotion: 'happy',

        spotlight: null,  // 不高亮

        cursor: {
          action: 'click',
          target: '.compact-history-visibility-handle',
          durationMs: 520,
        },

        operations: [
          // 点击动画开始时并行调用 API
          { trigger: 'onClickStart', action: 'open-history' },
          // 台词结束后调用 API
          { trigger: 'onNarrationEnd', action: 'close-history' },
        ],
      },
      // ... 更多 scene
    ],

    // 收尾配置
    wrap: {
      spotlight: { target: 'chat-input', shape: 'rounded-rect' },
      cursor: { action: 'hold' },
      petalTransition: true,
      petalCueAt: 0.7,  // 70% 处触发
    },
  },
};
```

**自动编排时序**：

```
scene 进入
  │
  ├─ T+0ms：建立 spotlight（调用 SpotlightController.highlight）
  │          追加台词到聊天窗（调用 NarratorController.narrate）
  │          播放 emotion 动作
  │
  ├─ T+0ms ~ T+220ms：高光稳定期
  │
  ├─ T+220ms：
  │   cursor.action === 'move'  → cursor.moveTo(target)
  │   cursor.action === 'click' → cursor.moveTo(target).then(cursor.click)
  │   cursor.action === 'wobble'→ cursor.moveTo(target).then(cursor.wobble)
  │   cursor.action === 'hold'  → 不触发新 cursor 动画
  │   cursor.action === 'ellipse'→ cursor.runEllipse(target)
  │
  ├─ onClickStart（点击动画开始时）：
  │   并行触发 operations 中 trigger === 'onClickStart' 的操作
  │
  ├─ narration 结束后：
  │   触发 operations 中 trigger === 'onNarrationEnd' 的操作
  │   保存 cursor 锚点
  │   等待 260-420ms
  │
  └─ 进入下一 scene（cursor 锚点自动接续）
```

**重构后的 API**：

```javascript
class SceneOrchestrator {
  constructor(options) {
    // options: {
    //   cursor: GhostCursorController,
    //   spotlight: SpotlightController,
    //   narrator: NarratorController,
    //   resistance: ResistanceController,
    //   overlay: TutorialOverlayRenderer,
    //   pauseCoordinator: PauseCoordinator,
    //   operationRegistry: OperationRegistry,
    // }
  }

  /** 播放完整 round */
  playRound(roundConfig: RoundConfig): Promise<void>

  /** 播放单个 scene */
  playScene(sceneConfig: SceneConfig): Promise<void>

  /** 跳过当前 round */
  skip(): Promise<void>

  /** 销毁（清理所有状态） */
  destroy(): void

  /** 注册自定义 operation handler */
  registerOperation(actionName: string, handler: OperationHandler): void
}
```

---

### 模块 6：`OperationRegistry`（业务操作注册表）

**职责**：将教程中所有真实 UI 操作（打开设置、打开侧边栏、切换开关、打开 HUD 等）注册为命名操作，scene 配置通过名字引用。

**当前散布位置**：
- `runAvatarFloatingSceneOperation()` — `yui-guide-director.js`
- 各天 scene handler 中的内联操作调用

**重构后的 API**：

```javascript
class OperationRegistry {
  /** 注册操作 */
  register(name: string, handler: (context: OperationContext) => Promise<void>): void

  /** 执行操作 */
  execute(name: string, context: OperationContext): Promise<void>

  /** 检查操作是否存在 */
  has(name: string): boolean
}

// 注册示例
registry.register('open-settings', async (ctx) => {
  await ctx.api.openSettingsPanel();
});

registry.register('show-settings-sidepanel:chat-settings', async (ctx) => {
  await ctx.api.ensureAvatarFloatingSettingsSidePanel('chat-settings');
});

registry.register('close-settings-panel', async (ctx) => {
  await ctx.api.closeSettingsPanel();
});

registry.register('open-history', async (ctx) => {
  await ctx.api.openCompactHistory();
});

registry.register('close-history', async (ctx) => {
  await ctx.api.closeCompactHistory();
});

registry.register('cleanup', async (ctx) => {
  ctx.orchestrator.cleanupTempState();
});
```

---

### 模块 7：`NarratorController`（台词与情绪管理）

**职责**：统一管理台词追加、语音播放、情绪动作和打断台词。

**当前代码位置**：
- `resolveAvatarFloatingSceneText()` / `resolveAvatarFloatingSceneVoiceKey()` / `resolveAvatarFloatingSceneEmotion()` — `yui-guide-director.js`
- 语音播放和进度追踪 — Director 内部方法

**重构后的 API**：

```javascript
class NarratorController {
  constructor(options) {
    // options: { chatWindow, emotionController, audioPlayer }
  }

  /** 播放台词（追加到聊天窗 + 播放语音 + 触发情绪动作） */
  narrate(text: string, options?: NarrateOptions): Promise<NarrationHandle>

  /** 暂停当前台词播放 */
  pause(): void

  /** 恢复台词播放 */
  resume(): void

  /** 停止当前台词播放（skip/angry exit） */
  stop(): void

  /** 播放打断台词 */
  playResistance(dialog: ResistanceDialog): Promise<void>

  /** 播放生气退出台词 */
  playAngryExit(dialog: ResistanceDialog): Promise<void>

  /** 获取当前播放进度（0~1，用于花瓣 cue 判断） */
  getProgress(): number

  /** 在指定进度处触发回调（用于花瓣 cue） */
  onProgress(threshold: number, callback: () => void): void
}
```

---

### 模块 8：`CursorAnchorStore`（锚点持久化）

**职责**：管理 cursor 锚点的保存、读取、失效和跨窗口同步。

**当前代码位置**：
- `avatarFloatingSceneCursorAnchorPoints` — `yui-guide-director.js`
- `latestExternalizedChatCursorAnchorPoint` — `yui-guide-director.js`
- `neko:yui-guide:external-chat-cursor-anchor` 事件监听 — `yui-guide-director.js`

**重构后的 API**：

```javascript
class CursorAnchorStore {
  constructor(options) {
    // options: { maxAgeMs }
  }

  /** 保存锚点 */
  save(sceneId: string, point: AnchorPoint): void

  /** 读取锚点 */
  load(sceneId: string): AnchorPoint | null

  /** 失效锚点 */
  invalidate(sceneId: string): void

  /** 失效所有锚点 */
  invalidateAll(): void

  /** 同步外置聊天窗锚点 */
  syncFromExternalWindow(anchor: ExternalAnchor): void

  /** 获取最近的外置聊天窗锚点 */
  getLatestExternalAnchor(): AnchorPoint | null

  /** 清理过期锚点 */
  gc(): void
}

/** 锚点结构 */
interface AnchorPoint {
  x: number;
  y: number;
  sceneId: string;
  kind: string;          // 'local' | 'external-chat' | 'pc-overlay'
  settled: boolean;      // 移动是否已完成
  updatedAt: number;     // 时间戳
}
```

---

### 模块 9：`SidebarPauseController`（侧边栏展开暂停）

**职责**：教程期间，点击事件展开的侧边栏显示需要配合对抗机制有暂停效果。

**当前问题**：侧边栏展开由 React 状态驱动，教程通过 `ensureAvatarFloatingSettingsSidePanel()` 直接触发，没有与对抗暂停机制联动。对抗期间如果侧边栏正在展开动画中，不会暂停。

**重构后的设计**：

```javascript
class SidebarPauseController {
  constructor(options) {
    // options: { pauseCoordinator, spotlightController }
  }

  /** 打开侧边栏（配合对抗暂停） */
  open(panelType: string, options?: { animate?: boolean }): Promise<void>

  /** 关闭侧边栏（配合对抗暂停） */
  close(panelType: string): Promise<void>

  /** 暂停侧边栏展开/收起动画 */
  pause(): void

  /** 恢复侧边栏展开/收起动画 */
  resume(): void

  /** 注册到 PauseCoordinator */
  getPauseToken(): PauseToken
}
```

**暂停行为**：

1. 对抗暂停时，正在展开中的侧边栏动画冻结在当前帧。
2. 对抗台词播放结束后，侧边栏从冻结帧继续展开。
3. 如果对抗发生时侧边栏尚未开始展开，延迟到对抗结束后再开始。
4. 侧边栏展开动画使用 CSS `animation-play-state: paused` 或 React transition 控制。

---

## 迁移策略

### Phase 0：Day Guide 共享工具抽取

1. 创建 `yui-guide-common.js`，导出 `deepFreeze`、`registerGuide`、`audioFilesForAllLocales`。
2. 各天 guide 文件改为 import 共享工具。
3. 统一替身声明：将 `YuiGuideAvatarStandIn` 的硬编码 `CUES` 映射表改为从各天 scene 配置的 `avatarStandIn` 字段自动读取。

**验证**：Day 1-7 guide 注册和替身演出行为不变。

### Phase 1：基础模块抽取（不改变外部行为）

1. 从 `YuiGuideGhostCursor` 和 `YuiGuideOverlay` 中抽取 `GhostCursorController`。
2. 从 `YuiGuideOverlay` 和 `TutorialHighlightController` 中抽取 `SpotlightController`。
3. 从 `TutorialInterruptController` 中抽取 `ResistanceController` 和 `PauseCoordinator`。
4. 从 `YuiGuideOverlay.createPcOverlayBridge` 中抽取 `TutorialOverlayRenderer`。
5. 从花瓣相关方法中抽取 `PetalTransitionController`。
6. 从 Round 启动序列中抽取 `RoundPreludeController`。
7. 抽取 `SceneLifecycleHelpers`（`isGuardFailed`、`clearAllSpotlights`、`performFullCleanup`、`scaleSceneMs`、`withLookAt`）。
8. 所有新模块通过适配器模式与旧代码对接，确保行为不变。

**验证**：Day 1 现有主线场景在新模块上运行通过。

### Phase 2：场景编排统一 + ChatWindowAdapter

1. 抽取 `ChatWindowAdapter` 策略接口（`LocalChatWindowAdapter` + `ExternalizedChatWindowAdapter`）。
2. 抽取 `SceneOrchestrator`，实现声明式 scene 配置的自动时序编排；内置 `prepareNarration` / `finalizeScene`。
3. 抽取 `OperationRegistry`，注册所有已有 operation handler。
4. 抽取 `NarratorController` 和 `CursorAnchorStore`。
5. 抽取 `SettingsTourFlow`，统一 Day 2/4/5 的设置巡游流程。
6. 将 Day 1 配置从命令式 handler 迁移为声明式配置。
7. 其余 Day 2-7 逐步迁移。

**验证**：Day 1-7 所有主线场景在新编排器上运行通过；验收清单全部通过。

### Phase 3：侧边栏暂停与对抗协同

1. 实现 `SidebarPauseController`。
2. 将侧边栏展开/收起操作注册到 `OperationRegistry`。
3. 在 `PauseCoordinator` 中注册 cursor、spotlight、sidebar 三个暂停令牌。
4. `SettingsTourFlow` 接入 `SidebarPauseController`，实现侧边栏展开期间对抗暂停。
5. 验证对抗期间侧边栏动画正确冻结和恢复。

**验证**：Day 2 设置侧边栏、Day 4 对话设置/动画设置侧边栏在对抗暂停时正确行为。

### Phase 4：清理旧代码

1. 移除 `YuiGuideGhostCursor` 类（被 `GhostCursorController` 替代）。
2. 移除 `YuiGuideOverlay` 中的 cursor/highlight 方法（被 `SpotlightController` + `TutorialOverlayRenderer` 替代）。
3. 移除 `TutorialInterruptController`（被 `ResistanceController` 替代）。
4. 移除各天 scene handler 中的内联时序代码（被声明式配置 + `SceneOrchestrator` 替代）。
5. 移除 274 处内联 guard 检查（被 `isGuardFailed` + `finalizeScene` 替代）。
6. 移除 50+ 处 `isHomeChatExternalized()` 分支（被 `ChatWindowAdapter` 策略替代）。
7. 移除 25+ 处外置聊天窗守卫+调用原始模式（被 adapter 封装）。
8. 更新 `UniversalTutorialManager` 使用新的模块组合。

**预估体积变化**：
- `yui-guide-director.js`：657KB → ~200KB（移除内联 handler、guard、双路径分支、旧 cursor/高光逻辑）
- `yui-guide-overlay.js`：91KB → ~40KB（仅保留 DOM fallback 渲染）
- 各天 guide 文件：总计减少 ~30KB（移除样板代码）
- 新增独立模块总计：~250KB

---

## 各天教程迁移示例

### Day 1（声明式配置）

```javascript
window.YuiGuideDailyGuides[1] = {
  day: 1,
  round: {
    title: 'day1',
    scenes: [
      {
        id: 'day1_intro_activation',
        textKey: 'tutorial.avatarFloating.day1.introActivation',
        voiceKey: 'avatar_floating_day1_intro_activation',
        emotion: 'happy',
        spotlight: { target: 'chat-input', shape: 'auto', variant: 'default' },
        cursor: { action: 'wobble', target: 'chat-input' },
        operations: [],
        waitForUserAction: true, // 等待用户真实点击激活音频
      },
      {
        id: 'day1_intro_greeting',
        textKey: 'tutorial.avatarFloating.day1.introGreeting',
        voiceKey: 'avatar_floating_day1_intro_greeting',
        emotion: 'happy',
        spotlight: { target: 'chat-input', shape: 'auto', variant: 'default' },
        cursor: { action: 'hold' },
        operations: [
          { trigger: 'onNarrationEnd', action: 'clear-spotlight', target: 'chat-input' },
        ],
      },
      {
        id: 'day1_history_handle',
        textKey: 'tutorial.avatarFloating.day1.historyHandle',
        voiceKey: 'avatar_floating_day1_history_handle',
        emotion: 'happy',
        spotlight: null,
        cursor: { action: 'click', target: '.compact-history-visibility-handle' },
        operations: [
          { trigger: 'onClickStart', action: 'open-history' },
          { trigger: 'onNarrationEnd', action: 'close-history' },
        ],
      },
      // ... 其余 scene
      {
        id: 'day1_takeover_return_control',
        textKey: 'tutorial.avatarFloating.day1.takeoverReturnControl',
        voiceKey: 'avatar_floating_day1_takeover_return_control',
        emotion: 'happy',
        spotlight: { target: 'chat-input', shape: 'rounded-rect', variant: 'plain-capsule' },
        cursor: {
          action: 'move',
          target: 'chat-capsule-input',
          durationMs: 900,
          startFromAnchor: 'day1_takeover_capture_cursor', // 从上一句锚点开始
        },
        operations: [{ trigger: 'onNarrationEnd', action: 'cleanup' }],
        petalTransition: true,
        petalCueAt: 0.7,
      },
    ],
  },
};
```

### Day 2（设置侧边栏对抗暂停示例）

```javascript
{
  id: 'day2_personalization_detail',
  textKey: 'tutorial.avatarFloating.day2.personalizationDetail',
  voiceKey: 'avatar_floating_day2_personalization_detail',
  emotion: 'neutral',
  spotlight: [
    { target: '[data-neko-sidepanel-type="character-settings"]', shape: 'rounded-rect', tier: 'primary' },
    { target: '#${p}-btn-settings', shape: 'circle', tier: 'persistent' },
  ],
  cursor: { action: 'ellipse', target: 'settings-sidepanel:character-settings' },
  operations: [
    { trigger: 'onPrepare', action: 'show-settings-sidepanel:character-settings', pauseAware: true },
    { trigger: 'onNarrationEnd', action: 'close-sidepanel:character-settings' },
  ],
  sidebarPause: true, // 侧边栏展开配合对抗暂停
},
```

---

## 与现有模块的对应关系

| 现有代码 | 重构后模块 | 变化 |
|---|---|---|
| `YuiGuideGhostCursor` 类 | `GhostCursorController` | 独立文件，API 声明化，内部封装 PC overlay / DOM 切换 |
| `YuiGuideOverlay` cursor 方法 | `GhostCursorController` + `TutorialOverlayRenderer` | 拆分职责：控制器 vs 渲染 |
| `YuiGuideOverlay` spotlight 方法 | `SpotlightController` + `TutorialOverlayRenderer` | 拆分职责：控制器 vs 渲染 |
| `TutorialHighlightController` | `SpotlightController` | 合并为统一高亮管理 |
| `TutorialInterruptController` | `ResistanceController` + `PauseCoordinator` | 拆分对抗逻辑和暂停协调 |
| `TutorialInteractionTakeoverController` | 保留 + `ChatWindowAdapter` | 外置/本地差异封装到 adapter |
| `TutorialSkipController` | 保留，不变 | — |
| `TutorialAvatarReloadController` | `RoundPreludeController` 使用 | 组合切模+等待+恢复为统一方法 |
| `playAvatarFloatingScene()` | `SceneOrchestrator.playScene()` | 从命令式改为声明式 |
| `runAvatarFloatingSceneOperation()` | `OperationRegistry.execute()` | 注册式操作执行 |
| `avatarFloatingSceneCursorAnchorPoints` | `CursorAnchorStore` | 独立锚点管理 |
| `YuiGuideAvatarStandIn` (硬编码 CUES) | Scene 配置 `avatarStandIn` 字段 | 替身声明合并到 scene 配置 |
| `YuiGuidePageHandoff` | 保留，作为 `OperationRegistry` 中的操作 | — |
| `createPcOverlayBridge()` | `TutorialOverlayRenderer` | 独立渲染层，帧批处理 |
| `playAvatarFloatingPetalTransitionAtCue()` | `PetalTransitionController` | 统一两条花瓣触发路径 |
| `isHomeChatExternalized()` 50+ 分支 | `ChatWindowAdapter` 策略 | 分支封装到 adapter 内部 |
| `deepFreeze` / `registerGuide` / `zhAudio` × 3 | `yui-guide-common.js` | 共享工具模块 |
| 274 处 `sceneRunId !== this.sceneRunId` | `SceneLifecycleHelpers.isGuardFailed()` | 统一守卫方法 |
| 20+ 处 overlay clear 序列 | `SpotlightController.clearAll()` | 统一清理方法 |
| Day 2/4/5 设置巡游重复流程 | `SettingsTourFlow` | 声明式设置巡游 |

---

## 风险与缓解

| 风险 | 影响 | 缓解措施 |
|---|---|---|
| 旧代码深度耦合，适配层过多 | Phase 1 工期膨胀 | Phase 1 只做模块抽取+适配器，不改变任何外部行为；用现有 Day 1 测试覆盖 |
| 声明式配置无法覆盖所有边缘情况 | 部分场景仍需自定义 handler | 保留 `onPrepare` / `onComplete` 钩子作为逃生口；Day 3 轮盘旋转等复杂操作可走自定义 handler |
| PC overlay 渲染层切换导致视觉回归 | 高光/cursor 闪烁或消失 | Phase 1 保留 DOM fallback；每个 phase 结束后在 PC + 网页端各跑一遍验收清单 |
| 对抗暂停与 React 状态更新冲突 | 侧边栏展开/收起与暂停不同步 | `SidebarPauseController` 通过 React `useTransition` 或 CSS `animation-play-state` 控制，不直接操作 React 内部状态 |
| 各天教程迁移期间新旧代码共存 | 同一功能存在两套实现 | 使用 feature flag 控制新旧路径；每天一个 flag，迁移完一天删除旧代码 |

---

## 文件结构规划

（已在上文「补充模块」部分更新，此处为最终版汇总）

```
static/
  tutorial/
    ghost-cursor-controller.js          // GhostCursorController
    spotlight-controller.js             // SpotlightController
    resistance-controller.js            // ResistanceController
    pause-coordinator.js                // PauseCoordinator
    sidebar-pause-controller.js         // SidebarPauseController
    tutorial-overlay-renderer.js        // TutorialOverlayRenderer
    cursor-anchor-store.js             // CursorAnchorStore
    narrator-controller.js              // NarratorController
    operation-registry.js               // OperationRegistry
    scene-orchestrator.js               // SceneOrchestrator + SceneLifecycleHelpers
    petal-transition-controller.js      // PetalTransitionController
    round-prelude-controller.js         // RoundPreludeController
    chat-window-adapter.js             // ChatWindowAdapter 策略接口
    settings-tour-flow.js              // SettingsTourFlow
    index.js                            // 统一导出
  yui-guide-common.js                   // Day Guide 共享工具 (deepFreeze, registerGuide, zhAudio)
  yui-guide-director.js                 // 保留，瘦身为核心调度
  yui-guide-overlay.js                  // 保留，降级为 DOM fallback 渲染
  tutorial-interaction-takeover.js      // 保留，接入 PauseCoordinator
  tutorial-interrupt-controller.js      // Phase 4 删除
  tutorial-highlight-controller.js      // Phase 4 删除
  tutorial-skip-controller.js           // 保留
  tutorial-avatar-reload-controller.js  // 保留，被 RoundPreludeController 使用
  yui-guide-day*.js                     // 逐步迁移为声明式配置，移除样板代码
```

---

## 总结

本重构将新手教程系统从「命令式 + 分散实现」转变为「声明式配置 + 模块化通用组件」架构。

### 核心模块（15 个）

| 模块 | 职责 | 消除的重复 |
|------|------|-----------|
| `GhostCursorController` | cursor 移动/点击/晃动/对抗/椭圆/暂停 | cursor 逻辑从 2 个类中统一 |
| `SpotlightController` | 圆角矩形/圆形高亮统一管理 | 20+ 处 overlay clear 序列 |
| `ResistanceController` | 对抗检测/计数/暂停/台词/生气退出 | 打断逻辑从 Director 解耦 |
| `PauseCoordinator` | cursor/高亮/侧边栏统一暂停协调 | 暂停机制从无到有 |
| `SidebarPauseController` | 侧边栏展开配合对抗暂停 | 新能力 |
| `TutorialOverlayRenderer` | PC 全局 overlay 唯一渲染层+帧批处理 | 消除 if(PC) 双路径分支 |
| `SceneOrchestrator` | 声明式 scene 自动时序编排 | 7+ 处 preamble/postamble 重复 |
| `OperationRegistry` | 注册式业务操作 | 6+ 处 agent toggle 序列 |
| `NarratorController` | 台词/语音/情绪/打断台词 | 7+ 处 narration+catch 重复 |
| `CursorAnchorStore` | 锚点保存/读取/失效/跨窗口同步 | 从 Director 内部数据结构抽出 |
| `PetalTransitionController` | 花瓣转场统一生命周期 | 合并两条花瓣触发路径 |
| `RoundPreludeController` | 每日启动准备序列 | Day 1-7 相同的 9 步准备 |
| `ChatWindowAdapter` | 外置/本地聊天窗统一接口 | 50+ 处 isHomeChatExternalized 分支 |
| `SettingsTourFlow` | 设置巡游通用流程 | Day 2/4/5 相同的 10 步流程 |
| `SceneLifecycleHelpers` | guard/clear/scale/lookAt 辅助 | 274 处 guard + 25+ 处外置守卫 |

### 预估收益

- **`yui-guide-director.js`**：15,467 行 → ~4,500 行（移除内联 handler、guard、双路径分支、旧 cursor/高光/花瓣逻辑）
- **各天 guide 文件**：总计减少 ~30KB（移除 `deepFreeze`/`registerGuide`/`zhAudio` 样板代码）
- **新增独立模块**：~250KB，但每个模块职责单一、可独立测试
- **新增教程场景成本**：从「理解 15,000 行 Director + 手写 100+ 行 handler」降为「写 20 行声明式配置」
