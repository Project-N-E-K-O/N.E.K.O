# 首页教程 Yui 引导演出层负责人开发文档

## 1. 文档目的

本文档用于指导“开发 B：演出层负责人”在当前 N.E.K.O 仓库里正式开工。

它解决的不是“Yui 要不要演”，而是下面这些更具体的问题：

- 在现有代码已经有教程骨架和共享场景注册表的前提下，开发 B 现在到底该从哪里开始接
- 演出层哪些内容归开发 B 独占，哪些只能通过接口挂接，不能反向侵入主骨架
- 开场三句、接管主流程、对抗流程、跨页恢复分别要做到什么程度
- 每个阶段交什么、怎么验收、怎么避免和主负责人及开发 C 冲突

本文基准时间为 `2026-04-15`。

---

## 2. 本文档依据

本文基于以下设计文档与当前仓库代码现实编写：

- [home-tutorial-yui-guide-architecture.md](./home-tutorial-yui-guide-architecture.md)
- [home-tutorial-yui-guide-three-person-collaboration.md](./home-tutorial-yui-guide-three-person-collaboration.md)
- [home-tutorial-yui-guide-preparation-freeze.md](./home-tutorial-yui-guide-preparation-freeze.md)
- [home-tutorial-yui-guide-main-owner-stage-breakdown.md](./home-tutorial-yui-guide-main-owner-stage-breakdown.md)
- `windows-mcp-uiautomation-integration.md`

同时还核对了当前代码中的真实锚点与运行时状态：

- [static/universal-tutorial-manager.js](../../static/universal-tutorial-manager.js)
- [static/yui-guide-steps.js](../../static/yui-guide-steps.js)
- `templates/index.html`
- [static/avatar-ui-popup.js](../../static/avatar-ui-popup.js)
- [static/app-interpage.js](../../static/app-interpage.js)
- [static/live2d-emotion.js](../../static/live2d-emotion.js)
- `templates/viewer.html`

---

## 3. 先说当前代码现实

在 `2026-04-15` 这个时间点，开发 B 不是从零开始。

当前已经成立的前置条件有：

- 首页已经加载了 [static/yui-guide-steps.js](../../static/yui-guide-steps.js) 和 [static/universal-tutorial-manager.js](../../static/universal-tutorial-manager.js)
- `UniversalTutorialManager` 已经具备 `Yui Guide` 运行时桥接能力
- 首页旧教程 step 已经补了首批 `yuiGuideSceneId`
- 共享场景注册表已经存在，并且首页 `sceneOrder.home` 已冻结
- `prelude-start / step-enter / step-leave / tutorial-end` 事件已经会通过 `window` 广播
- `createYuiGuideDirector(options)` 已经成为演出层的标准挂接入口

当时待补齐、因此正是开发 B 主战场的部分有：

- `static/yui-guide-director.js`
- `static/yui-guide-overlay.js`
- `static/css/yui-guide.css`
- `static/assets/tutorial/`
- 演出层自己的统一终止和清理实现
- ghost cursor 本体
- 气泡、语音、表情桥的稳定最小闭环

这意味着开发 B 当前的任务不是“先去改教程管理器”，而是：

- 围绕已经存在的生命周期挂接点，把演出层真正接起来
- 围绕已经冻结的 `YuiGuideStep.performance` 字段，把剧本变成运行时行为

---

## 4. 开发 B 的职责边界

### 4.1 你负责的事情

- 实现 `YuiGuideDirector`
- 实现 `YuiGuideBubble`
- 实现 `YuiGuideVoiceQueue`
- 实现 `YuiGuideEmotionBridge`
- 实现 `YuiGuideGhostCursor`
- 实现首页接管时的演出层 DOM 与样式
- 实现 `interrupt_resist_light` 与 `interrupt_angry_exit` 的演出编排
- 补齐和维护 `performance` 配置
- 保证 `skip / abortAsAngryExit / destroy` 可安全停机

### 4.2 你不负责的事情

- 不负责教程 step 骨架本体
- 不负责首页真实入口的 DOM 结构调整
- 不负责页面打开、窗口命名、跨页 token 发送与恢复
- 不负责 `/ui` Vue 面板内部的桥接
- 不负责真实系统鼠标控制
- 不负责把 Agent 自动化、Computer Use 或 Windows UIAutomation 混进教程演出

这里要特别强调：

- `windows-mcp-uiautomation-integration.md` 描述的是 Agent 桌面自动化快车道
- 它不是 Yui 首页引导的执行器
- 演出层只能做页面内 ghost cursor 和页面级视觉接管，不能借题发挥成真实桌面控制

---

## 5. 文件 owner 与写入纪律

### 5.1 开发 B 独占文件

- `static/yui-guide-director.js`
- `static/yui-guide-overlay.js`
- `static/css/yui-guide.css`
- `static/assets/tutorial/`

### 5.2 可参与但不应主改

- [static/yui-guide-steps.js](../../static/yui-guide-steps.js)

开发 B 在这个文件里主要补：

- `performance.bubbleText`
- `performance.voiceKey`
- `performance.emotion`
- `performance.cursorAction`
- `performance.cursorTarget`
- `performance.cursorSpeedMultiplier`
- `performance.delayMs`
- `performance.interruptible`
- `performance.resistanceVoices`
- `interrupts` 既定形状内的策略值

### 5.3 不应直接长期占用

- [static/universal-tutorial-manager.js](../../static/universal-tutorial-manager.js)
- `templates/index.html`
- [static/app-ui.js](../../static/app-ui.js)
- [static/app-buttons.js](../../static/app-buttons.js)
- [static/avatar-ui-popup.js](../../static/avatar-ui-popup.js)
- [static/app-interpage.js](../../static/app-interpage.js)

如果需要这些文件提供新挂接点，先提接口需求，再由主负责人或开发 C 收口。

---

## 6. 当前可直接依赖的运行时契约

### 6.1 Director 挂接入口

开发 B 的主模块必须向全局提供：

```ts
window.createYuiGuideDirector = function createYuiGuideDirector(options) {}
```

`options` 当前至少可依赖：

```ts
{
  tutorialManager,
  page,
  registry
}
```

### 6.2 Director 最小接口

开发 B 必须实现以下方法，并保持与冻结说明一致：

```ts
interface YuiGuideDirector {
  startPrelude(): Promise<void>;
  enterStep(stepId: string, context: unknown): Promise<void>;
  leaveStep(stepId: string): Promise<void>;
  handleInterrupt(event: Event): void;
  skip(reason?: string): void;
  abortAsAngryExit(source?: string): Promise<void>;
  destroy(): void;
}
```

### 6.3 可旁路调试的事件

当前 `UniversalTutorialManager` 已经会广播：

- `neko:yui-guide:prelude-start`
- `neko:yui-guide:step-enter`
- `neko:yui-guide:step-leave`
- `neko:yui-guide:tutorial-end`

这意味着开发 B 可以先通过事件旁路验证演出逻辑，再把它收口进 Director。

### 6.4 当前共享场景注册表

开发 B 当前直接可用的首页场景包括：

- `intro_basic`
- `takeover_capture_cursor`
- `takeover_plugin_preview`
- `takeover_settings_peek`
- `takeover_return_control`
- `interrupt_resist_light`
- `interrupt_angry_exit`

`handoff_*` 场景暂时主要由开发 C 负责导航，但开发 B 需要为后续跨页节奏预留表现位。

---

## 7. 建议模块拆分

建议开发 B 按下列结构落地，而不是把演出逻辑塞进一个大文件：

### 7.1 `static/yui-guide-director.js`

职责：

- 接住 `startPrelude / enterStep / leaveStep / skip / abortAsAngryExit / destroy`
- 读取注册表中的 `performance`
- 管理统一终止态
- 协调 overlay、bubble、voice、emotion、ghost cursor

### 7.2 `static/yui-guide-overlay.js`

职责：

- 挂载教程演出根节点
- 承载气泡层、ghost cursor 层、插件预演层
- 提供 DOM 创建、显示、隐藏、销毁能力

### 7.3 `static/css/yui-guide.css`

职责：

- `body.yui-taking-over` 页面级接管样式
- 气泡、光标、怒气态、预演层样式
- 跳过与紧急退出控件的可见性保护

### 7.4 `static/assets/tutorial/`

职责：

- 插件预演层短时素材
- 可选的预录语音资源

第一阶段素材策略建议：

- 有素材则播放
- 缺素材则自动回退到简化 DOM 预演，不阻塞主流程

---

## 8. 场景到演出责任的落地表

| 场景 ID | 开发 B 需要交付什么 |
|---|---|
| `intro_basic` | 气泡、语音、表情；通过 `startPrelude()` 承接，不重复挂进旧 step |
| `takeover_capture_cursor` | ghost cursor 初次出现、轻晃、接管开始样式 |
| `takeover_plugin_preview` | 点击猫爪后的预演层、插件展示节奏、可中断清理 |
| `takeover_settings_peek` | 进入设置一瞥时的台词、光标点击与情绪转折 |
| `takeover_return_control` | 结束台词、光标归中、接管状态收束 |
| `interrupt_resist_light` | 拉扯、回弹、随机抵抗语音 |
| `interrupt_angry_exit` | 怒气表情、退出台词、进入统一终止通道 |

---

## 9. 分阶段开发说明

## 9.1 准备阶段

目标不是写效果，而是先把演出层骨架搭对。

开发 B 需要先完成：

- 明确 Director 内部的状态机最小形状
- 约定 overlay 根节点命名和销毁方式
- 约定 bubble、voice、emotion、cursor 的编排顺序
- 确认所有退出路径最终都汇聚到一次 `destroy()`
- 审视 [static/yui-guide-steps.js](../../static/yui-guide-steps.js) 里的首版 `performance` 是否足够驱动第一阶段

此阶段不要做的事情：

- 不要先写跨页 handoff
- 不要先写 `/ui` 桥接
- 不要为了演出方便去反向改教程骨架

### 推荐内部状态

建议至少区分：

- `IDLE`
- `PRELUDE_PLAYING`
- `STEP_PLAYING`
- `CURSOR_ACTING`
- `CURSOR_RESISTING`
- `TERMINATING`
- `DESTROYED`

---

## 9.2 Milestone 1：开场三句闭环

这一阶段的目标是让首页“像 Yui 在说话”，而不是先追求复杂接管。

开发 B 必须完成：

- `createYuiGuideDirector()` 可被首页稳定创建
- `startPrelude()` 能只处理 `intro_basic`
- `enterStep()` 能处理接管、插件、设置与归还控制权场景
- `leaveStep()` 不留下脏气泡、脏音频、脏计时器
- 气泡和表情桥有第一版最小闭环
- `performance` 首批配置被补齐并可驱动运行

这一阶段推荐优先级：

1. 先把 bubble 跑起来
2. 再接 emotion
3. 再接稳定语音
4. 最后再打磨节奏和细节

阶段完成标准：

- 首页教程启动后，`intro_basic` 会在 prelude 阶段出现
- 开场旁白只保留当前流程实际使用的自我介绍与 `intro_basic`
- 跳过时不会残留气泡、定时器、表情占用

---

## 9.3 Milestone 2：接管主流程与对抗流程

这是开发 B 的主战役。

开发 B 必须完成：

- `YuiGuideGhostCursor` 第一版
- `body.yui-taking-over` 生命周期管理
- 动作一到动作四的演出闭环
- 轻微抵抗与怒退流程
- 统一中断节流与阈值判断
- `skip / abortAsAngryExit / destroy` 共用终止通道

这一阶段的实现重点不是“动画多炫”，而是“无论哪条退出路径都不会把页面弄脏”。

建议动作拆法：

1. `takeover_capture_cursor`
   - ghost cursor 出现
   - 轻微晃动
   - 接管样式打开
2. `takeover_plugin_preview`
   - 光标点击猫爪
   - 播放插件预演层
   - 素材缺失时自动降级
3. `takeover_settings_peek`
   - 光标切向设置
   - 配合开发 C 打开的真实设置弹层做演出
4. `takeover_return_control`
   - 结束台词
   - 光标归中
   - 所有演出态回收

对抗流程要求：

- 只有 `performance.interruptible !== false` 的强演出步骤才累计抵抗
- 轻微抵抗只做页面内拉扯和回弹
- 连续达到阈值后触发 `interrupt_angry_exit`
- `interrupt_angry_exit` 最后必须仍然走统一清理路径

阶段完成标准：

- 首页可以跑通接管主流程
- 有效打断能进入抵抗，再进入怒退
- 退出后页面不残留隐藏光标、overlay、音频、监听器

---

## 9.4 Milestone 3：跨页前后演出节奏

这一阶段开发 B 不主导导航，但要主导“跨页前后像同一段演出”。

开发 B 需要完成：

- opening / waiting / resumed 三类表现位
- 目标页恢复时重新建立 bubble / voice / emotion
- 首页预演层与真实跨页版本的台词一致性调整

此阶段重点不是新增很多动画，而是避免体验断层。

阶段完成标准：

- 从首页跳向目标页前后，Yui 的语气和状态连续
- 目标页恢复后不出现“上一页演出残影”
- 首页预演版和跨页版的台词不互相打架

---

## 9.5 Milestone 4：`/ui` 接入后的演出补位

这一阶段是否进入范围由主负责人决定。

如果进入，开发 B 只负责：

- 让首页插件预演层与真实 `/ui` 面板之间体验连续
- 让 `/ui` 恢复后的 bubble / voice / emotion 风格保持一致

开发 B 不负责：

- `/ui` 页面真实路由桥接
- Vue 教程桥的主实现

---

## 10. 具体实现建议

## 10.1 Bubble

建议第一版支持：

- `show(text, options)`
- `update(text)`
- `hide()`

第一阶段只要求：

- 文本正确
- 时序稳定
- 跳过可立即隐藏

## 10.2 Voice

建议第一阶段优先预录或稳定本地资源，不要直接复用聊天 TTS 主链路。

原因：

- 教程节奏必须确定
- 聊天音频队列受会话状态影响更大
- 演出层更需要“可立即停、可预测结束”

如果第一阶段拿不到预录资源，也应提供无音频回退，不阻塞上线。

## 10.3 Emotion

从当前仓库看，首页已经加载了 [static/live2d-emotion.js](../../static/live2d-emotion.js)，而视图侧已有现成情感调用入口。

因此建议开发 B：

- 优先复用现有模型情感能力
- 只桥接 `neutral / happy / surprised / angry / embarrassed`
- 不为教程单独再发明一套新情绪协议

这里的结论是基于当前模板加载和 viewer 情感调用路径做出的工程推断，后续若主负责人发现更稳定的统一入口，以主负责人收口方案为准。

## 10.4 Ghost Cursor

第一版只要支持：

- `showAt(x, y)`
- `moveTo(targetRect, options)`
- `click(options)`
- `wobble(options)`
- `resistTo(userRealX, userRealY, options)`
- `cancel()`
- `hide()`

实现要求：

- 不改真实鼠标
- 回弹后能续航原轨迹
- 被跳过后立即停止

## 10.5 终止与清理

开发 B 必须把下面几种结束方式统一收口：

- 正常完成
- 右上角跳过
- `abortAsAngryExit()`
- 页面卸载
- Director 重复创建失败后的自清理

建议只保留一个统一终止例程，例如：

- `finalizeTermination(reason)`

并确保：

- 首个进入者负责清理
- 后续重复调用直接复用终止态
- `destroy()` 只做最终销毁，不做业务判断

---

## 11. 与其他两位的协作接口

### 11.1 和主负责人协作时

你要向主负责人要的不是“帮我改功能”，而是：

- 生命周期挂接点是否足够
- `templates/index.html` 是否需要补脚本与样式装载
- `static/yui-guide-steps.js` 某些字段是否需要主负责人先冻结后再扩

### 11.2 和开发 C 协作时

你要对齐的是：

- 哪些真实入口何时可见
- 设置弹层何时打开
- 哪些页面入口是预演，哪些是实际跳转
- 跨页恢复点何时触发

你不应该替开发 C 解决：

- 页面打开逻辑
- 路由恢复逻辑
- 菜单 DOM 结构问题

---

## 12. 验收与回归清单

### 12.1 开发 B 完成标志

- 演出层是模块化的，不是散落回调
- Director 可被首页稳定创建和销毁
- `performance` 配置能驱动真实演出
- 接管流程和打断流程都能跑通
- 所有退出路径都能清干净

### 12.2 必测项

- 首次进入首页时，Yui 演出会正常开始
- `intro_basic` 只在 prelude 播放一次
- 跳过后 overlay、气泡、音频、监听器都被清理
- ghost cursor 不影响真实鼠标
- 轻微打断会拉扯并回弹
- 达到阈值后进入 angry exit
- angry exit 结束后页面可正常继续使用
- 素材缺失时插件预演层能降级

### 12.3 回归项

- 首页浮动按钮仍可正常工作
- 设置弹层可正常开关
- 聊天输入、语音入口、主动能力开关不受影响
- 透明窗口场景里跳过按钮仍可点击

---

## 13. 推荐开工顺序

如果现在立刻开工，建议按这个顺序推进：

1. 新建 `static/yui-guide-director.js`，只做空壳 Director 与统一终止态
2. 新建 `static/yui-guide-overlay.js`，先把根节点、bubble 容器、cursor 容器立起来
3. 新建 `static/css/yui-guide.css`，先实现最小可见样式和 `body.yui-taking-over`
4. 跑通 `intro_basic`
5. 跑通 `takeover_capture_cursor`
6. 跑通 `takeover_plugin_preview / takeover_settings_peek / takeover_return_control`
7. 最后补 `interrupt_resist_light / interrupt_angry_exit`

这条顺序的核心思想是：

- 先把“能接、能退、能清理”做对
- 再把“会动、会演、会生气”做漂亮

---

## 14. 最终结论

开发 B 当前最重要的任务，不是去定义更多接口，而是把已经存在的：

- 首页教程骨架
- Yui Guide 生命周期
- 场景注册表
- 首页真实锚点

真正接成一套可运行、可中断、可清理的演出系统。

一句话总结：

开发 B 交付的应该是一套“可被挂接、可被终止、可被联调”的 Yui 演出层，而不是若干散落效果。

---

## 15. 当前统一实现口径（2026-04-28）

本节用于覆盖最近几轮联调后已经确认的“当前代码真实怎么跑”的实现口径。

如果本节与前文其他阶段性示意冲突，以本节为准。

### 15.1 技术基线

- 教程文本统一进入对话窗。
- 首页内嵌聊天直接 append 到 React chat。
- N.E.K.O.-PC 的外置 `/chat` 窗口通过 BroadcastChannel 注入教程消息。
- 只有外置聊天窗通信失败时，首页才退回 overlay 气泡兜底。
- 教程语音统一优先使用 `static/assets/tutorial/guide-audio/{locale}/` 下的预录 `.mp3`。
- 当前已落地的语音目录为 `zh`、`en`、`ja`、`ko`、`ru`。
- `zh-TW` 文本走 i18n，语音复用 `zh/` 目录，不单独拆新音频桶。
- 预录音频播放失败时只做静默等待，避免浏览器 TTS 串音。
- 普通教程语音播放时只启动教程专用的简单嘴部开合动画，不接入全局 TTS / WebSocket / 音频分析链路；`interrupt_*` 对抗机制语音不驱动嘴部动作。
- 全新手教程已经去掉遮罩。
- 首页与 `/ui/` 页面只保留 spotlight、precise highlight 和 ghost cursor。
- Ghost Cursor 只负责页面内演出，不控制真实系统鼠标。
- Ghost Cursor 所有可见状态下的位置切换必须平滑移动，不能从一个目标点突变到另一个目标点。
- 首页和 `/ui/` 页面进入椭圆轨迹前，必须先平滑移动到轨迹起点，再开始轨迹动画。
- 所有“模拟点击”都必须同时满足两件事：
  - 前端出现高亮、Ghost Cursor 平滑移动和点击反馈。
  - 对应真实业务 API 或业务动作被实际调用。
- 圆形按钮高亮统一走 `circle-highlight.png`。
- 当前明确按圆形图渲染的入口包括 `alt='语音控制'`、`alt='猫爪'`、`alt='设置'`。
- 这些圆形入口不附带 `cat-paw.png`、`left-cat-ear.png`、`right-cat-ear.png`。
- 普通矩形高亮继续沿用统一的放大缩小 pulse 动效。
- 首页普通模式下，用户首次点击聊天输入框后立即进入 `body.yui-taking-over`，真实鼠标全局隐藏。
- N.E.K.O.-PC 外置 `/chat` 模式没有“输入框点击后再隐藏鼠标”这一步，而是由首页主流程直接接管。
- 新手教程期间会把当前角色模型临时切到 `yui_default`，并按模型管理页保存 / 重载流程生效；教程结束、跳过或异常收尾时恢复用户原模型。
- 教程期间模型容器使用相对视口定位到屏幕中间偏右：`left: 55%`、`top: 50%`、`transform: translate(-50%, -50%) translateZ(0)`，容器尺寸保持 `width: 100%`、`height: 100%`。
- 教程结束时必须恢复模型容器原始 `left/top/right/bottom/width/height/transform` 内联样式。

### 15.2 总流程状态机

当前首页主流程为下面这条固定链路：

1. `intro_greeting_reply`
2. `intro_basic`
3. `takeover_capture_cursor`
4. `takeover_plugin_preview`
5. `takeover_settings_peek`
6. `takeover_return_control`

补充说明：

- `intro_greeting_reply` 是前奏辅助段，不是 `yui-guide-steps.js` 里的正式 step。
- 当前首页正式 scene 顺序仍以 `static/yui-guide-steps.js` 中的 `sceneOrder.home` 为准。
- `intro_proactive` 与 `intro_cat_paw` 已从当前流程和 scene 注册表中移除，不再有单独台词、语音或步骤映射。
- `interrupt_resist_light` 与 `interrupt_angry_exit` 作为中断场景，可在主流程中途插入。

### 15.3 开场前奏与阶段一：聊天入口 + 语音控制按钮

首页普通模式当前实现如下：

1. 确保对话窗可见。
2. 高亮聊天输入区。
3. Ghost Cursor 出现在输入区附近并晃动。
4. 输入框上方出现气泡：`点一下这里，我就能开始说话啦～`
5. 除【跳过】和这一次真实输入框点击外，其余首页点击都被 `interactionGuard` 拦截。
6. 等待用户点击输入框，用于解锁浏览器 autoplay。
7. 点击后气泡消失，并立即进入 `yui-taking-over`。

补充说明：

- Ghost Cursor 初次出现允许直接落在输入框附近；之后只要已经可见，所有位置更新都必须通过平滑移动完成。

点击输入框后，前奏继续执行：

1. 向对话窗发送并播放自我介绍：
   `欢迎回家，喵~ 外面的世界很辛苦吧？在这个专属我们的小窝里，你可以放下所有的烦恼哦。我是林悠怡，接下来的熟悉过程请放心交给我，我会一步步牵着您的手慢慢来的。`
   表情轨道：`sbx -> xxy`
2. 随后向对话窗发送并播放 `intro_basic`：
   `这里有一个神奇的按钮！只要点击它，就可以直接和我聊天啦！想跟我分享今天的新鲜事吗？或者只是叫叫我的名字？快来试试嘛，我已经迫不及待想听到你的声音啦！喵！`
   表情轨道：`swz`
3. 对话窗作为 persistent spotlight 保持高亮。
4. `alt='语音控制'` 按钮作为 action spotlight 用圆形图高亮。
5. Ghost Cursor 在语音播报期间从输入区移动到 `alt='语音控制'` 按钮。

本阶段结束后不会再进入旧的 `intro_proactive` 或 `intro_cat_paw`，而是直接进入 `takeover_capture_cursor`。

N.E.K.O.-PC 外置 `/chat` 模式当前实现如下：

- 跳过“等待输入框点击解锁 autoplay”和提示气泡这一步。
- 同样会发送并播放自我介绍和 `intro_basic` 两段内容。
- 教程消息通过 BroadcastChannel 注入到 `/chat` 对话窗，而不是停留在首页气泡层。

### 15.4 阶段二：键鼠控制接管（`takeover_capture_cursor`）

本段文本与语音为：

- `超级魔法按钮出现！只要点一下这里，我就可以把小爪子伸到你的键盘和鼠标上啦！我会帮你打字，帮你点开网页……不过，要是那个鼠标指针动来动去的话，我可能也会忍不住扑上去抓它哦！准备好迎接我的捣乱……啊不，是帮忙了吗？喵！`
- 表情轨道：`szhs -> syhs`

当前实现是“边播边推进”，而不是等整句播完后再统一动作。

动作顺序如下：

1. `alt='猫爪'` 按钮用 `circle-highlight.png` 高亮。
2. Ghost Cursor 移动到 `alt='猫爪'` 按钮并模拟点击。
3. 同步调用真实业务动作打开猫爪面板。
4. 高亮【猫爪总开关】并调用 `setAgentMasterEnabled(true)`。
5. 高亮【键鼠控制】并调用 `setAgentFlagEnabled('computer_use_enabled', true)`。

补充说明：

- 本段以及后续接管段的 `interruptCount` 会跨 scene 累计。
- 对应 step 的 `resetOnStepAdvance = false`，不会在 scene 切换时自动清零。
- 【猫爪总开关】到【键鼠控制】的 Ghost Cursor 移动使用明显较短时长，避免相邻开关间慢速移动时出现抖动感。

### 15.5 阶段三：插件面板联动（`takeover_plugin_preview` + `/ui/`）

首页第一句文本与语音为：

- `还没完呢！你快看快看，这里还有超～～多好玩的插件呢！`
- 表情：`by`

首页本段动作顺序如下：

1. 打开猫爪面板。
2. 高亮【用户插件】开关并调用 `setAgentFlagEnabled('user_plugin_enabled', true)`。
3. 通过悬停让【管理面板】按钮显现出来。
4. 用虚拟 spotlight 高亮【管理面板】按钮区域。
5. Ghost Cursor 移动到【管理面板】并模拟点击。
6. 同步打开真实 `/ui/` 页面。

当前管理面板入口说明：

- 用户手动点击首页猫爪侧边【管理面板】时，入口 URL 直接指向 `http://127.0.0.1:48916/ui`，并继续追加 `v=` 缓存刷新参数。
- 教程跨页演出仍由 `static/yui-guide-page-handoff.js` 负责打开 `/ui/` 并进行 handoff。

跨页切换后的统一口径如下：

- `alt='猫爪'`、【猫爪总开关】、【用户插件】、【管理面板】这些首页遗留高亮会在跨页演出前清掉，不会持续到设置阶段。
- `/ui/` 打开后，首页 persistent spotlight 与 action spotlight 会清空。
- `/ui/` 打开后，首页 Ghost Cursor 隐藏，直到 `/ui/` 页面关闭后才恢复。
- 第二句文本与语音：
  `有了它们，我不光能看 B 站弹幕，还能帮你关灯开空调…… 本喵就是无所不能的超级猫猫神！哼哼～`
  仍然在首页播放和输出，不在 `/ui/` 子页本地 TTS。
- 表情：`syhs`

`/ui/` 页面当前演出如下：

1. 不加遮罩。
2. 高亮【插件管理】按钮。
3. Ghost Cursor 移动过去并模拟点击。
4. 高亮右侧 `<main>`。
5. 先向下滚动 `150px`。
6. 再向上滚动 `150px`。
7. 然后在 `<main>` 区域做椭圆轨迹移动。

补充说明：

- `/ui/` 内 Ghost Cursor 初次出现后，如果需要重定位到插件按钮附近，会先平滑移动再开始后续步骤。
- 进入 `<main>` 椭圆轨迹前，会先平滑移动到椭圆起点；这段预移动从椭圆总预算中拆出较大的时间片，避免从按钮或中心点突变到轨迹边缘，也避免额外拉长阶段时长。

`/ui/` 与首页当前通过握手机制同步：

- 首页向 `/ui/` 发送 `neko:yui-guide:plugin-dashboard:start` 启动演出。
- `/ui/` ready 后回传 `neko:yui-guide:plugin-dashboard:ready`。
- 首页语音真正结束时再向 `/ui/` 发送 `neko:yui-guide:plugin-dashboard:narration-finished`。
- `/ui/` 页面收到这个消息后再收束高亮并结束演出。

这样做的原因是：

- 高亮收束跟随真实音频结束，而不是写死某个语种延迟。
- 被对抗机制打断后，语音暂停 / 恢复也不会导致 `/ui/` 侧时序漂移。

本段结束后：

- 如果 `/ui/` 窗口是教程自己创建的，则由教程关闭。
- 猫爪总开关、键鼠控制、用户插件开关会回滚到接管前快照。
- 首页 UI 和首页 Ghost Cursor 恢复，继续进入设置阶段；恢复时如果 Ghost Cursor 已有位置，必须平滑移动到后续目标，不允许瞬移。

### 15.6 阶段四：设置一瞥（`takeover_settings_peek`）

本段第一句文本与语音为：

- `当然啦，如果你想让本喵多和你聊聊天也不是不行啦，给我多准备点小鱼干吧，嘿嘿，好了不逗你啦，设置都在这个齿轮里。`
- 表情：`xxy`

本段第二句文本与语音为：

- `你看，这里可以穿我的新衣服、给我换一个好听的声音……换一个猫娘或是修改记忆？等一下！你在干嘛？该不会是想把我换掉吧？啊啊啊不行！快关掉快关掉！`
- 表情：`sbx`

当前动作口径如下：

1. 先关闭猫爪面板，恢复首页主 UI。
2. 对话窗继续保持为讲述承载区。
3. `alt='设置'` 按钮用 `circle-highlight.png` 高亮。
4. 第一段语音开始播放后，仍然保留一个 cue 点：只有到了 `openSettingsPanel` 这个 cue，才点击设置按钮并打开设置面板。
5. 这个 cue 点不是按每个语种手写延迟，而是按真实音频时长做比例映射。
6. 第一段语音播完后，定位并高亮【角色设置】入口。
7. 展开角色设置侧面板。
8. 找到【角色外形】和【声音克隆】。
9. 第二段语音一开始就直接进入动作推进。
10. Ghost Cursor 移动到角色设置区域中心，并在该区域做椭圆轨迹移动，持续到第二段语音结束。

本段的高亮策略为下面这组三层组合：

- 设置按钮本体。
- 【角色设置】菜单入口。
- 角色设置子区域打包后的联合 spotlight。

本段结束后：

- 收掉角色设置侧面板。
- 关闭设置面板。
- 清理所有 settings 相关 spotlight。

### 15.7 阶段五：归还控制权（`takeover_return_control`）

本段文本与语音为：

- `好啦好啦，不霸占你的电脑啦～控制权还给你了喵！可不许趁我不注意乱点奇怪的设置哦！之后的日子也请你多多关照了喵～`
- 本段不使用教程表情轨道。

当前实现口径如下：

- 进入本段前会先清掉 persistent spotlight 和 action spotlight。
- 因此这里不应该再出现“全桌面高亮”。
- 本段播报完成后，Ghost Cursor 回到视口中心，晃动一次，然后隐藏。
- 回到视口中心也走平滑移动；只有最终隐藏动作会直接收起 Ghost Cursor。
- 随后禁用中断监听，并以 `complete` 结束教程。

### 15.8 对抗机制

当前对抗机制贯穿整个新手教程主流程。

首页当前会开启对抗机制的阶段包括：

- `intro_basic`
- `takeover_capture_cursor`
- `takeover_plugin_preview`
- `takeover_settings_peek`
- `takeover_return_control`

`/ui/` 页面也会开启本地对抗检测，但“语音播放 / 暂停 / 续播 / angry exit”控制权仍在首页。

首页检测口径如下：

- 只在 `interruptsEnabled === true` 时监听。
- 只在 `body.yui-taking-over` 存在时生效。
- 监听源为：
  - `mousemove`：用于真正判断是否发生争抢。
  - `mousedown`：只用于刷新上一帧基准点，不直接计数。

`/ui/` 页面检测口径如下：

- 使用 `pointermove` / `pointerdown` 采样，而不是只看鼠标事件。
- 同时拦截 `touchstart`、`touchmove`、`touchend`、`wheel`、`click` 等交互，避免移动端或触控板永远攒不到打断次数。
- 当 `/ui/` Ghost Cursor 正处于脚本驱动运动时，允许用更低连续命中数触发有效打断，确保用户能抢回插件页光标。

1. 被动回弹（不计入打断次数）

当用户只是轻微挪动鼠标时，先触发被动回弹：

- 单次位移距离 `>= 10px`
- 当前速度 `>= 0.2 px/ms`
- 与上一次被动回弹的间隔 `>= 140ms`
- 行为是 Ghost Cursor 做一次轻微反应后回位，不播放台词，也不增加 `interruptCount`

2. 首页有效打断（计入打断次数）

首页当前判定条件为：

- 当前位移距离 `>= 32px`
- 当前速度 `>= 1.8 px/ms`
- 当前加速度 `>= 0.09`
- 连续命中 `3` 次采样
- 相邻两次有效打断之间节流 `>= 500ms`

3. `/ui/` 页面有效打断（计入打断次数）

`/ui/` 页面基础阈值与首页一致：

- 当前位移距离 `>= 32px`
- 当前速度 `>= 1.8 px/ms`
- 相邻两次有效打断之间节流 `>= 500ms`

差异在于：

- 普通情况下仍要求加速度阈值和连续 `3` 次命中。
- 如果当前是脚本驱动运动中的 Ghost Cursor，则把连续命中数降到 `2`。

4. 轻微抵抗（第 1 次 / 第 2 次有效打断）

当 `interruptCount < 3` 时：

- 首页会中断当前旁白并记录恢复音频偏移。
- 对话窗追加抵抗台词。
- 当前只提供两句抵抗语音：
  - 第 1 次：`喂！不要拽我啦，还没轮到你的回合呢！`
  - 第 2 次：`等一下啦！还没结束呢，不要随便打断我啦！`
- 首页会临时显示真实鼠标 3 秒，然后再自动恢复隐藏。
- 首页场景会执行 `cursor.resistTo(x, y)`。
- `/ui/` 页面会把抵抗请求传回首页，由首页负责播报；子页自己负责本地 Ghost Cursor 的拉扯回弹。
- 用户停止继续争抢后，原本被中断的语音会从记录偏移点续播。
- 抵抗台词表情随机使用 `z2` / `z3` 其中一个。

5. 生气退出（第 3 次有效打断）

当 `interruptCount >= 3` 时：

- 首页或 `/ui/` 页面都会统一走 angry exit。
- `/ui/` 页面只负责把 angry exit 请求传回首页。
- 首页负责追加文本、播放：
  `人类~~~~！你真的很没礼貌喵！既然你这么想自己操作，那你就自己对着冰冷的屏幕玩去吧！哼！`
- 生气退出会直接应用 `z3` 生气表情；因为此时已经进入终止态，不再依赖普通语音表情轨道。
- 台词结束后统一走：
  `requestTermination('pointer_interrupt', 'angry_exit')`

### 15.9 对话窗与交互限制

首页内嵌聊天模式当前限制如下：

- 教程期间除【跳过】和普通模式下首个输入框激活点击外，其余首页点击会被 `interactionGuard` 拦截。
- 这意味着教程进行中不能随意点击首页其他按钮或面板。

N.E.K.O.-PC 外置 `/chat` 模式当前限制如下：

- 教程开始后会广播 `yui_guide_set_chat_buttons_disabled`。
- 禁用对话窗按钮点击。
- 禁用输入框点击和输入。
- 禁用 `contenteditable` 编辑。
- 禁用右下角 resize handle 和 resize edge。
- 如果当前焦点已经落在输入框，会主动 `blur()`。

### 15.10 退出与清理要求

以下任一情况触发后，都必须完整收尾：

- 用户点击【跳过】
- 教程正常播放完成
- 教程进入 angry exit
- 页面关闭或父教程销毁

收尾时必须保证：

- 所有 spotlight 和 precise highlight 消失。
- Ghost Cursor 消失。
- `yui-taking-over`、`yui-resistance-cursor-reveal`、`yui-guide-plugin-dashboard-running` 等接管态 class 被移除。
- 对话窗恢复正常交互。
- 如果是 N.E.K.O.-PC 外置 `/chat`，必须解除 `yui-guide-chat-buttons-disabled`。
- 首页真实猫娘和主界面继续保留，不能因为教程结束把主 UI 一起隐藏。
- 若教程期间打开过 `/ui/` 页面，则在需要时关闭它并恢复首页可见状态。
- 由教程临时创建的 return button 等演出遗留 DOM 必须清理。

### 15.11 当前代码落点

当前这套统一口径对应的主要实现文件如下：

- `static/yui-guide-steps.js`
  - scene 顺序
  - 文本 key
  - `interruptible` / `resetOnStepAdvance` 等策略
- `static/yui-guide-director.js`
  - 首页主流程
  - 对话窗写入
  - 本地音频播放
  - 旁白暂停 / 续播
  - 首页 Ghost Cursor 与高亮编排
- `static/yui-guide-overlay.js`
  - spotlight / circle-highlight DOM
  - 首页 Ghost Cursor 平滑移动、椭圆轨迹起点预移动
- `static/css/yui-guide.css`
  - 所有 spotlight、circle-highlight、Ghost Cursor、接管态样式
- `static/yui-guide-page-handoff.js`
  - 首页真实面板打开 / 关闭
  - `/ui/` 打开与回收
  - 跨页 handoff
- `frontend/plugin-manager/src/yui-guide-runtime.ts`
  - `/ui/` 子页 Ghost Cursor
  - `/ui/` 子页 Ghost Cursor 平滑重定位与椭圆轨迹起点预移动
  - `/ui/` spotlight
  - `/ui/` 页面中断检测与首页握手
- `static/universal-tutorial-manager.js`
  - 教程期间模型强制切换到 `yui_default`
  - 教程模型容器相对视口定位与结束恢复
- `static/app-interpage.js`
  - 教程消息向外置 `/chat` 注入
  - N.E.K.O.-PC 对话窗锁定状态广播接收
- `templates/chat.html`
  - 外置 `/chat` 被锁定时的按钮、输入框、缩放禁用样式
