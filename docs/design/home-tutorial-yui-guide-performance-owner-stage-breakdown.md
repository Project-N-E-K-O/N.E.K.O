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

---

## 15. 当前统一实现口径（2026-04-16）

本节用于覆盖最近一轮联调后已经确认的“当前按什么做”的实现口径。

如果本节与前文某些阶段性示意冲突，以本节为准。

### 15.1 技术基线

- 教程播报优先播放 `static/assets/tutorial/guide-audio/{locale}/` 下的预录音频，播放失败时回退到浏览器原生 `SpeechSynthesis` API
- 所有教程文本同时进入对话窗
- 高亮统一使用”遮罩挖洞 + 独立描边框”的方式实现，不能靠提升真实业务元素层级来伪装
- Ghost Cursor 只负责演出层视觉和轨迹，不控制真实系统鼠标
- 所有”模拟点击”都必须同时满足两件事：
  - 前端出现 Ghost Cursor 平滑移动、按钮高亮、点击反馈
  - 后端或业务 API 真正执行对应动作
- 真实鼠标可见性规则：阶段一至三可见，阶段四起隐藏（`yui-taking-over`），对抗机制触发时临时显示 3 秒（`yui-resistance-cursor-reveal`）
- 阶段一启动时因浏览器 autoplay 限制，先引导用户点击输入框获取交互手势后再播放音频

### 15.2 总流程状态机

当前首页新手引导按下面 7 个编号阶段推进，其中阶段二 / 阶段三为互斥分支，最后再进入退出前播报：

1. 初始化与首次问候
2. 傲娇催促
3. 初始回复
4. 夺取鼠标控制权
5. 系统设置自动化演示
6. 跨页演出与插件展示
7. 角色设置演示

其中唯一的前置分支只有：

- 用户点击【暂时不聊天】按钮：进入阶段二，再进入阶段三
- 用户主动输入文本并成功发送：跳过阶段二，直接进入阶段三

### 15.3 阶段一：初始化与首次问候

启动项目后立即执行：

- 开启全屏遮罩
- 只高亮底部聊天输入框
- Ghost Cursor 立即出现在输入框位置并晃动
- 输入框旁出现气泡提示：`点一下这里，我就能开始说话啦～`
- 等待用户点击输入框（解锁浏览器 autoplay 限制）

用户点击输入框后：

- 气泡消失
- 发送并播报：
  - `想要找我的时候，随时在这里打字或者发语音都能召唤本喵哦！`（7秒）

这句语音播放完成后：

- 高亮区域从”输入框”平滑过渡到”整个对话窗”
- 对话窗保持高亮

随后发送并播报：

- `现在你可以试试跟我说说话啦，看看我们是不是超有默契的喵～`（6秒）

同时：

- 在该条消息下出现【暂时不聊天】和【你好】分流按钮
- 引导进入等待分流状态

### 15.4 阶段二：傲娇催促

仅当用户点击【暂时不聊天】时触发：

- 发送并播报：
  - `可恶，居然敢无视本大小姐嘛！要说你一直没理我，我可是会主动跑出来咬你的哦～（哈！！）`（9秒）
- 这句语音结束后，Ghost Cursor 首次出现，并全局隐藏用户鼠标直到新手教程结束或用户点击跳过，或者触发用户争抢鼠标事件提前结束教程
- 完成后直接进入阶段四

### 15.5 阶段三：初始回复

仅当用户点击【你好】时触发：

- 发送并播报：
  - `我是你的专属猫娘，从今天起就由我来陪伴主人咯。无论是想要聊天解闷、一起玩耍，还是需要我帮忙做些什么，我都会乖乖陪在主人身边的喵。以后请多多指教啦，最喜欢主人了~！`（16秒）
- 这句语音结束后，Ghost Cursor 首次出现
- 完成后直接进入阶段四


### 15.6 阶段四：夺取鼠标控制权

- **从此阶段起真实用户鼠标隐藏**（通过 `yui-taking-over` CSS class），直至教程结束或触发对抗机制

发送并播报：

- `好啦！不说废话了喵——你看到那个可爱的‘猫爪’了吗，准备好了吗？让我借用一下你的鼠标吧！`（9秒）

然后在该语音播放第6秒执行预热动作：

- Ghost Cursor 持续轻微移动 3 秒

然后发送并播报：

- `嘿咻！可算逮住你的鼠标了喵～`（4秒）

### 15.7 阶段五：系统设置自动化演示

发送并播放：

- `还没完呢！你快看快看，这里还有超～～多好玩的插件呢！`(7秒)

同时窗内执行首页自动化链路，总时长控制在 7 秒：

1. 平滑移动到 `alt='猫爪'` 的按钮
2. 先高亮该按钮
3. 模拟点击该按钮，并同步调用对应 API 打开猫爪面板
4. 平滑移动到【猫爪总开关】
5. 先高亮该按钮
6. 模拟打开，并同步调用开关 API
7. 平滑移动到【用户插件】开关
8. 先高亮该按钮
9. 模拟打开，并同步调用开关 API
10. 让悬停动作把【管理面板】按钮显现出来
11. 先高亮【管理面板】
12. Ghost Cursor 平滑移动到【管理面板】
13. 模拟点击【管理面板】，并同步调用真实打开页面 API

### 15.8 阶段六：跨页演出与插件展示

等待页面：

- `${UI_ORIGIN}/ui/`

其中 `UI_ORIGIN` 为环境可配置的前端页面源地址，文档只约定路由 `/ui/`，不写死 `127.0.0.1`。

完全加载后，执行 `/ui/` 页面演出：

- 给 `/ui` 页面添加全屏遮罩
- 高亮【插件管理】按钮
- Ghost Cursor 移动过去并模拟点击，同时走对应业务动作
- 发送并播报：
  - `有了它们，我不光能看 B 站弹幕，还能帮你关灯开空调…… 本喵就是无所不能的超级猫猫神！哼哼～`（9秒）

然后在播报期间执行并行动画：

- Ghost Cursor 移动到右侧 `<main>` 元素，并先高亮 `<main>`
- 前 2 秒：
  - 向下滚动 `150px`
  - 再向上滚动 `150px`
- 后 7 秒：
  - Ghost Cursor 在 `<main>` 区域做横向椭圆轨迹移动

本段语音播放完后：

- 自动关闭 `/ui` 页面
- 返回首页继续后续阶段

### 15.9 阶段七：角色设置演示

返回首页后，发送并播报：

- `当然啦，如果你想让本喵多和你聊聊天也不是不行啦，给我多准备点小鱼干吧，嘿嘿，好了不逗你啦，设置都在这个齿轮里。`(12秒)`你看，这里可以穿我的新衣服、给我换一个好听的声音……换一个猫娘或是修改记忆？等一下！你在干嘛？该不会是想把我换掉吧？啊啊啊不行！快关掉快关掉！`（14秒）

前 9 秒 ghost cursor 只显示不执行动作，9 到 12 秒执行以下 1 到 3 点，12 到 14 秒执行以下 4 到 9 点：

1. Ghost Cursor 平滑移动到 `alt='设置'` 的按钮
2. 先高亮【设置】按钮
3. 模拟点击【设置】，并同步调用打开设置面板 API
4. 高亮【角色设置】按钮
5. Ghost Cursor 平滑移动到 【角色设置】按钮
6. 模拟点击【角色设置】，并保持该入口高亮（12秒运行到这）
7. 同时高亮以下 2 个目标：
   - 【角色外形】
   - 【声音克隆】
8. Ghost Cursor 平滑移动到【角色外形】和【声音克隆】的高亮区域
9. Ghost Cursor 在【角色外形】和【声音克隆】的高亮区域做椭圆轨迹移动，持续到阶段七语音播报结束。

### 15.10 退出前播报

`好啦好啦，不霸占你的电脑啦～控制权还给你了喵！可不许趁我不注意乱点奇怪的设置哦！之后的日子也请你多多关照了喵～`(12秒)

本段语音全部播放完毕后：

- 收束所有高亮与演出状态

### 15.11 对抗机制
当前代码里的“用户抢鼠标中断”不是按简单的“移动次数”判断，而是只在首页接管态下，对高强度鼠标争抢做分级响应。

触发前提：

- 只在 `interruptsEnabled === true` 时监听
- 只在 `body.yui-taking-over` 存在时生效
- 当前实际会开启该机制的步骤是：
  - `takeover_capture_cursor`
  - `takeover_plugin_preview`
  - `takeover_settings_peek`
- 监听源为：
  - `mousemove`：用于真正判断是否发生“争抢”
  - `mousedown`：只用于刷新上一帧鼠标基准点，不直接计数

1. 被动回弹（不计入打断次数）

当用户只是轻微挪动鼠标时，先触发“被动回弹”：

- 条件：
  - 单次位移距离 `>= 10px`
  - 当前速度 `>= 0.2 px/ms`
  - 与上一次被动回弹的间隔 `>= 140ms`
- 行为：
  - Ghost Cursor 做一次朝用户反方向的轻微反应，再回到原位置
  - 只做视觉“较劲”，不播放台词
  - 不增加 `interruptCount`

2. 有效打断（计入打断次数）

只有用户出现明显“抢夺鼠标”的动作，才算一次有效打断。代码当前判定条件为：

- 当前位移距离 `>= 32px`
- 当前速度 `>= 1.8 px/ms`
- 当前加速度 `>= 0.09`
- 上述强动作需要连续命中 `3` 次采样（`DEFAULT_INTERRUPT_ACCELERATION_STREAK = 3`）
- 相邻两次有效打断之间还要满足节流间隔 `>= 500ms`

达到一次有效打断后：

- `interruptCount += 1`
- 教程会先进入“轻微抵抗”分支，而不是立刻退出
- 当前三个接管步骤都配置了 `resetOnStepAdvance = false`，所以 `interruptCount` 会跨阶段累计，不会在 `takeover_capture_cursor -> takeover_plugin_preview -> takeover_settings_peek` 之间自动清零

3. 轻微抵抗（第 1 次 / 第 2 次有效打断）

当 `interruptCount < 3` 时，进入 `interrupt_resist_light`：

- **真实用户鼠标临时显示 3 秒**（通过 `yui-resistance-cursor-reveal` CSS class 覆盖 `cursor: none`），3 秒后自动恢复隐藏
- 当前语音按次数轮播：
  - 第 1 次：`喂！不要拽我啦，还没轮到你的回合呢！`
  - 第 2 次：`等一下啦！还没结束呢，不要随便打断我啦！`
  - 仅提供两次抵抗语音，不设置第三次
- 同时行为：
  - 中断当前旁白播放并记录恢复点
  - 对话窗追加抵抗台词
  - Ghost Cursor 执行一次 `resistTo(x, y)` 拉扯回弹
  - 若用户停止继续争抢，原本被打断的教程旁白会在短暂静止后自动续播

4. 生气退出（第 3 次有效打断）

当 `interruptCount >= 3` 时，直接进入 `interrupt_angry_exit`：

- 关闭当前步骤计时器并停止继续累计打断
- 画面进入 angry 状态：
  - `overlay.setTakingOver(true)`
  - `overlay.setAngry(true)`
- 播放生气退出台词：
  - `人类~~~~！你真的很没礼貌喵！既然你这么想自己操作，那你就自己对着冰冷的屏幕玩去吧！哼！`
- 台词结束后统一走：
  - `requestTermination('pointer_interrupt', 'angry_exit')`

5. 结论

因此，当前代码语义更准确的描述是：

- 轻微晃动鼠标：只会触发 Ghost Cursor 的被动回弹，不算一次打断
- 连续高强度抢夺鼠标：会累计有效打断次数
- 前两次有效打断：进入“轻微抵抗”并尝试恢复教程
- 第三次有效打断：进入 angry exit，教程直接终止并走统一清理


### 15.12 退出与清理要求

以下任一情况触发后，都必须完整收尾：

- 用户点击【跳过】
- 用户主动退出教程
- 教程正常播放完成
- 教程进入 angry exit

收尾时必须保证：

- 全屏遮罩消失
- 所有高亮框消失
- Ghost Cursor 消失
- 对话窗恢复正常交互
- 首页真实猫娘仍然保留，不能因为教程退出把主界面一起隐藏
- 若教程期间打开过子页面，则关闭或恢复主界面显示状态，不能留下“主界面被隐藏但教程已结束”的脏状态

### 15.13 实现建议

为避免后续继续把状态写散，当前建议把实现拆成 4 条明确链路：

- `NarrationRunner`
  - 负责教程文本进入对话窗
  - 负责 `SpeechSynthesis` Promise 封装
  - 后续替换为预录音频时只换这一层
- `HighlightManager`
  - 负责遮罩挖洞
  - 负责多目标同时高亮
  - 负责输入框 -> 对话窗的平滑过渡
- `GhostCursorDirector`
  - 负责轨迹、点击动画、滚动、椭圆运动
  - 不直接承担业务动作
- `HomeInteractionApi / CrossPageHandoff`
  - 负责真实 API 调用
  - 负责打开与关闭页面
  - 负责和 Ghost Cursor 演出动作做时序编排
