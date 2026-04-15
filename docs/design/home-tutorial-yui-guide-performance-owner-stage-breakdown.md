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
- [windows-mcp-uiautomation-integration.md](./windows-mcp-uiautomation-integration.md)

同时还核对了当前代码中的真实锚点与运行时状态：

- [static/universal-tutorial-manager.js](../../static/universal-tutorial-manager.js)
- [static/yui-guide-steps.js](../../static/yui-guide-steps.js)
- [templates/index.html](../../templates/index.html)
- [static/avatar-ui-popup.js](../../static/avatar-ui-popup.js)
- [static/app-interpage.js](../../static/app-interpage.js)
- [static/live2d-emotion.js](../../static/live2d-emotion.js)
- [templates/viewer.html](../../templates/viewer.html)

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

当前还没有落地、因此正是开发 B 主战场的部分有：

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

- [windows-mcp-uiautomation-integration.md](./windows-mcp-uiautomation-integration.md) 描述的是 Agent 桌面自动化快车道
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
- [templates/index.html](../../templates/index.html)
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
- `intro_proactive`
- `intro_cat_paw`
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
| `intro_proactive` | 进入 step 时的台词、表情、基础节奏 |
| `intro_cat_paw` | 第三句开场白、进入接管前的情绪抬升 |
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
- `enterStep()` 能处理 `intro_proactive` 与 `intro_cat_paw`
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
- `intro_proactive` 与 `intro_cat_paw` 不会被重复播放
- 三句开场白与场景 ID 一一对应
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
- `intro_proactive` 与 `intro_cat_paw` 随 step 进入
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
4. 跑通 `intro_basic / intro_proactive / intro_cat_paw`
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
