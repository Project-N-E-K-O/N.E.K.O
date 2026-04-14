# 首页教程替换方案：Yui 引导演出架构设计

## 1. 文档目的

本文档用于描述“以 Yui 角色演出替换当前首页教程”的后续开发方案。

目标不是单纯重写文案，而是把设想中的：

- 开场对话
- 带情绪的气泡提示
- 伪鼠标演出
- 页面内自动点击与高亮
- 跨页面接力引导
- 右上角跳过

整理成一套可在当前 N.E.K.O 项目中逐步落地的架构、状态机和模块需求。

本文以当前仓库代码为基准，基准时间为 `2026-04-14`。

---

## 2. 背景与结论

### 2.1 需求背景

当前首页已经存在一套基于 `driver.js` 的通用教程系统，能完成：

- 主页步骤编排
- 高亮元素
- 自动展开部分菜单
- 右上角跳过按钮
- 页面级首次进入判定与已读持久化

但它偏向传统“功能导览”，缺少明显的角色感、叙事感和演出感。

后续希望把首页教程替换为“Yui 主导的强角色化引导”，让新用户第一次进入时感受到：

- 是 Yui 在主动带路
- 她会说话、会吐槽、会阻止某些危险操作
- 教程不是冷冰冰的高亮框，而是一段角色演出

### 2.2 核心结论

推荐采用：

**保留现有教程系统作为步骤与高亮骨架，在其上增加 Yui 演出层，而不是另起一套完整教程引擎。**

不推荐采用：

- 彻底废弃 `static/universal-tutorial-manager.js` 后重写一整套教程框架
- 直接控制系统真实鼠标
- 让“教程演出”复用现有 `avatar_interaction` 聊天业务链路
- 把插件管理、设置页、桌面自动化混成一个无法回退的大状态机

### 2.3 推荐方案一句话概括

首页教程替换为：

**`UniversalTutorialManager` 负责“步骤、定位、高亮、已读、跳过”，`YuiGuideDirector` 负责“配音、气泡、表情、伪鼠标、页面接力、演出节奏”。**

---

## 3. 当前代码现实基础

## 3.1 已有教程系统

当前项目已有通用教程系统：

- 入口脚本：`static/universal-tutorial-manager.js`
- 首页已加载：`templates/index.html`
- 设置页已加载：`templates/api_key_settings.html`
- 创意工坊页已加载：`templates/steam_workshop_manager.html`

它当前已经具备：

- `driver.js` 步骤驱动
- 页面类型检测
- 每页首次启动判定
- 右上角跳过按钮
- 高亮时自动滚动、弹窗拖动、视口钳制

因此“步骤管理”和“跳过按钮”不是空白能力，不需要从零实现。

## 3.2 首页现有入口结构

首页主 UI 是原生 JS + Jinja2 模板，不是 React/Vue 单页应用。

- 主页面：`templates/index.html`
- 浮动按钮与设置弹层：`static/avatar-ui-popup.js`
- 聊天相关逻辑：`static/app-buttons.js`
- 页面间通信：`static/app-interpage.js`

这意味着新教程如果发生在首页，最佳实现位置仍是传统前端层，而不是 React 聊天窗层。

## 3.3 设置菜单现实入口

首页设置菜单当前稳定可见的入口主要是：

- 角色设置
- API 密钥
- 记忆浏览
- 创意工坊

这些项目位于 `static/avatar-ui-popup.js` 的设置菜单构造逻辑中。

## 3.4 插件管理现实入口

插件管理不是首页设置菜单里的一个现成项。

当前插件管理面板是独立 Vue 应用：

- 源码：`frontend/plugin-manager/`
- 服务路径：插件服务器 `/ui/`
- 现有入口：`/api/agent/user_plugin/dashboard` 重定向到 `/ui`

因此“演示插件管理”不能假设它和首页设置菜单在同一个页面层里。

## 3.5 桌面端现实限制

本项目运行在桌面环境，但当前前端暴露出来的稳定能力主要是：

- 透明窗口与鼠标穿透切换
- 独立窗口 `setBounds`
- 屏幕信息访问
- Electron 桥接的窗口辅助能力

当前代码里没有一个专门给教程使用的“真实鼠标锁定/强制拉回”接口。

仓库中确实存在 `pyautogui` 路径，但那是 Agent 电脑控制链路，不适合作为首页教程演出方案。

所以本方案明确约束：

- **不控制系统真实鼠标**
- **只使用页面内 ghost cursor 作为伪鼠标演出**
- **接管演出期间仅在当前页面隐藏系统光标视觉，不尝试把真实鼠标拖到 ghost cursor 位置**

---

## 4. 设计目标

### 4.1 体验目标

- 教程由 Yui 主导，而不是普通提示框主导
- 角色语气、动作、气泡、表情与步骤一致
- 用户明确知道当前发生的是“教程演出”
- 即使跳过或中断，也不会感到界面失控

### 4.2 工程目标

- 尽可能复用现有教程系统
- 首页替换后，不破坏其他页面原有教程
- 演出层与步骤层解耦，便于后续改文案、改节奏、换角色
- 支持分阶段上线，不要求一次做完跨页面全自动

### 4.3 安全与可控目标

- 不抢真实鼠标
- 任何时刻都可以右上角跳过
- 用户强行移动鼠标/按键时，演出应优雅降级或进入可控中止
- 不影响已有聊天会话、Agent 任务、窗口拖动、鼠标穿透等核心逻辑

---

## 5. 非目标

以下内容不属于第一阶段目标：

- 真正调用系统 API 强制锁定鼠标
- 复用 Agent 的 `computer_use` 或 `pyautogui` 执行教程
- 把教程事件投递给 LLM 生成实时自由回复
- 在所有页面同时重做教程系统
- 做成不可恢复、不可跳过、会让业务状态失真的敌对惩罚机制

---

## 6. 推荐总体架构

## 6.1 分层

推荐拆为三层：

### A. 步骤骨架层

复用现有 `UniversalTutorialManager`，负责：

- 场景/步骤顺序
- 锚点元素查找
- 高亮与遮罩
- 页面完成状态持久化
- 跳过与销毁

### B. 演出编排层

新增 `YuiGuideDirector`，负责：

- 根据当前步骤播放对应演出
- 管理气泡、配音、表情
- 管理 ghost cursor
- 管理页面接力与恢复
- 管理中断和降级策略

### C. 表现组件层

新增若干轻量组件或 DOM 管理器，负责：

- `YuiGuideBubble`
- `YuiGuideVoiceQueue`
- `YuiGuideEmotionBridge`
- `YuiGuideGhostCursor`
- `YuiGuidePageHandoff`

## 6.2 推荐关系图

```text
templates/index.html
  └─ UniversalTutorialManager
       ├─ tutorial step registry
       ├─ highlight / skip / storage
       └─ YuiGuideDirector
            ├─ Bubble manager
            ├─ Voice queue
            ├─ Emotion bridge
            ├─ Ghost cursor overlay
            └─ Page handoff coordinator
```

---

## 7. 页面范围与分阶段实现

## 7.1 Phase 1：仅替换首页教程

范围：

- 替换首页已有教程文案与表现形式
- 复用首页现有步骤点位
- 加入气泡、配音、表情、伪鼠标
- 保留右上角跳过

不包含：

- 继续接力到插件管理页
- 继续接力到设置页独立窗口

这是最安全、最容易先上线的版本。

## 7.2 Phase 2：首页 + 设置菜单内引导

范围：

- 首页内打开设置弹层
- 在首页内继续引导 API 密钥、记忆浏览、创意工坊等菜单项
- 仍然停留在首页页内，不跨独立窗口

## 7.3 Phase 3：跨页面接力

范围：

- 从首页打开某个独立页面
- 通过 handoff token 告知新页面继续教程
- 新页面教程结束后返回首页或完成整个流程

建议优先支持：

- `/api_key`
- `/memory_browser`
- `/steam_workshop_manager`

插件管理 `/ui/` 放到更后面，因为它是单独的 Vue 面板，目前也没有接入通用教程系统。

## 7.4 Phase 4：插件管理页专用教程

范围：

- 为 `/ui/` 单独接入一个轻量 handoff bootstrap
- 在 Vue 面板里实现自己的引导桥
- 与首页演出逻辑对接，但不直接复用 DOM 级高亮实现

---

## 8. 状态机设计

## 8.1 顶层状态

先区分两类概念：

- **运行时主状态**：建议真正落到代码枚举里的状态
- **伴随态 / 并行监听**：附着在某些主状态上的运行机制，不一定要单独做成主状态枚举值

```text
IDLE
  -> PRELUDE
  -> HOME_GUIDE
  -> OPENING_TARGET_PAGE
  -> WAITING_HANDOFF
  -> RESUMED_GUIDE
  -> COMPLETED
  -> SKIPPED
  -> ANGRY_EXIT
  -> CANCELLED
```

### 状态说明

- `IDLE`
  - 未开始
- `PRELUDE`
  - 角色开场白、气泡、表情、小幅伪鼠标演出
- `HOME_GUIDE`
  - 首页步骤引导主过程
- `OPENING_TARGET_PAGE`
  - 正在打开新页面或新窗口
- `WAITING_HANDOFF`
  - 当前页等待目标页接棒
- `RESUMED_GUIDE`
  - 新页面拿到 token 后继续步骤
- `ANGRY_EXIT`
  - Yui 因连续有效打断而触发的戏剧性离场
  - 内部属于“受控中止”，后续应落到 `SKIPPED`
- `COMPLETED`
  - 教程完整结束
- `SKIPPED`
  - 用户主动跳过
  - 或进入 `ANGRY_EXIT` 后按“特殊跳过”收尾
- `CANCELLED`
  - 因页面销毁、超时或异常中止

### 伴随态 / 并行监听

- `INTERRUPT_MONITORING`
  - 与 `PRELUDE / HOME_GUIDE / RESUMED_GUIDE` 中的强演出步骤并行存在的监听伴随态
  - 只在允许较劲的步骤中开启，对 `pointermove / mousemove / pointerdown / keydown` 做节流判定
  - 建议在实现里作为 Director 内部标志或子状态处理，而不是强制写进顶层主状态枚举

## 8.2 演出子状态

每一个教程步骤内部可再有演出子状态：

```text
STEP_ENTER
  -> SPEAKING
  -> CURSOR_ACTING
       |-- [parallel throttled input monitor]
       |-- (valid interrupt, count < threshold) --> CURSOR_RESISTING --> CURSOR_ACTING
       |-- (interruptCount >= threshold) -------> ANGRY_EXIT
  -> HIGHLIGHTING
  -> WAITING_NEXT
  -> STEP_LEAVE
```

### 说明

- `SPEAKING`
  - 播放配音，显示气泡
- `CURSOR_ACTING`
  - ghost cursor 做移动、点击、轻晃等演出
- `CURSOR_RESISTING`
  - 用户触发有效打断后，ghost cursor 产生“被拉拽又弹回”的较劲动画
  - 可随机播放一条轻微反抗语音，然后恢复主轨迹
- `HIGHLIGHTING`
  - 交给 `driver.js` 高亮当前元素
- `WAITING_NEXT`
  - 等待自动推进或用户点击下一步

补充约束：

- 并行监听不是全程开启，只在当前步骤 `performance.interruptible !== false` 且存在强演出时开启
- `CURSOR_RESISTING` 只属于演出子状态，不单独破坏主状态机；只有累计阈值达到后才升级到顶层 `ANGRY_EXIT`
- `performance.interruptible` 决定“这一小步能不能较劲”
- `interrupts.*` 决定“如果能较劲，具体使用什么阈值、节流和升级策略”

---

## 9. 中断与跳过策略

## 9.1 跳过

必须保留右上角跳过按钮。

行为要求：

- 首页教程期间始终可见
- 点击后立即结束当前教程
- 清理气泡、配音、ghost cursor、临时状态
- 标记教程已结束或按策略记录为已跳过

补充约束：

- `ANGRY_EXIT` 不是异常崩溃，而是一个内部等价于“特殊跳过”的状态
- 不管是右上角跳过还是 `ANGRY_EXIT`，都应尽量复用同一条清理路径
- 如果产品层需要表现“Yui 被气跑了”，可以在清理前追加一小段怒气演出，但最终控制权交还必须和普通跳过一样稳定

## 9.2 用户操作中断

用户移动鼠标、点击、按键是正常行为，但本方案允许在工程绝对安全的前提下，恢复一条**安全可控的戏剧性中止（Theatrical Abort）** 分支。

### 核心原则

- “Yui 被气跑了”在实现上不是系统异常，而是 `ANGRY_EXIT -> SKIPPED`
- 任何时候右上角跳过优先级最高
- 不抢真实系统鼠标，不锁死输入，不制造无法恢复的状态
- 中断监听只影响演出层，不直接破坏 `UniversalTutorialManager` 的步骤骨架

### 推荐中断分级

#### 轻度打断

触发示例：

- ghost cursor 演出期间出现一次明显鼠标位移
- 用户点击了非目标区域
- 用户按下任意键，表达“我想自己操作”

处理建议：

- 停止当前 ghost cursor 动画
- 允许 Yui 播放一句短促的“别乱动啦”式语音
- 执行一次轻微拉扯/回弹动画后回到当前步骤
- 该拉扯动画建议通过 `CURSOR_RESISTING` 子状态实现，而不是直接在 `mousemove` 回调里堆逻辑
- 若当前步骤演出过重，可直接降级为普通高亮，不必强撑完整表演

#### 连续有效打断

新增运行时状态：

```ts
let interruptCount = 0;
```

推荐判定方式：

- 只在 `PRELUDE / HOME_GUIDE / RESUMED_GUIDE` 期间开启监听
- 监听逻辑以 `pointermove` 为主，必要时兼容 `mousemove`
- 只在当前步骤存在 ghost cursor 或强演出时累计计数
- 对 `mousemove / pointerdown / keydown` 做节流，建议 `400-600ms` 内最多记一次
- 鼠标位移需超过阈值后才算一次有效打断，避免轻微抖动误伤
- 步骤成功推进后重置 `interruptCount`
- 连续安静一段时间后可衰减或重置 `interruptCount`

这样可以保留戏剧效果，同时避免桌面端用户随手晃一下鼠标就直接把 Yui “气跑”。

### `ANGRY_EXIT` 触发

当 `interruptCount >= 3` 时，进入 `ANGRY_EXIT`。

推荐执行顺序：

1. 冻结后续 step 自动推进，防止演出和清理并发。
2. 立即停止当前配音队列、气泡更新和 ghost cursor 动画。
3. 播放一次短促的生气语音，并显示怒气符号/表情特效。
4. 将当前退出原因标记为 `angry_exit`。
5. 进入与右上角跳过等价的统一退出路径，调用 `YuiGuideDirector.skip('angry_exit')` 或等价封装；`skip / abortAsAngryExit / destroy` 以及更高层调用者必须共享一个原子 `isTerminating`（或等价 termination promise / compare-and-set 标记），只有首个进入者负责调度统一 `finally / cleanup`，并在该清理例程内最终且仅最终调用一次 `destroy()` 清理临时 DOM、监听器和状态，后续并发触发应立即 early-return。
6. 如果产品希望 Yui 真的“离开首页”，可在清理尾声复用现有“请她离开”业务链路。

### 与现有业务代码的衔接建议

当前首页已存在“请她离开 / 请她回来”链路：

- `templates/index.html` 中的 `#resetSessionButton`
- `static/app-ui.js` 中的 `live2d-goodbye-click`
- `static/app-buttons.js` 中 `resetSessionButton.click()` 后续结束会话逻辑

因此更合适的实现不是让教程代码直接拼业务细节，而是补一个受控包装，例如：

```ts
window.triggerYuiGoodbyeFromGuide?.({ reason: 'angry_exit' });
```

过渡阶段也可以内部复用 `live2d-goodbye-click` 或 `resetSessionButton.click()`，但文档上建议尽量封成明确 API，避免教程层长期依赖 DOM 模拟点击。

### 结果预期

- 用户感知：是“我把 Yui 惹生气了，她跑掉了”
- 工程语义：是一次带演出的 `skip`
- 系统结果：教程安全终结，控制权立即归还，不留下脏状态

## 9.3 异常中断

以下情况需要自动终止演出并清理：

- 页面卸载
- 目标锚点不存在且超过超时
- 新窗口打开失败
- handoff token 超时
- 音频系统初始化失败且当前步骤依赖音频

---

## 10. 核心模块设计

## 10.1 `YuiGuideDirector`

职责：

- 绑定到 `UniversalTutorialManager`
- 接收当前 step 变化
- 执行与 step 对应的角色演出
- 管理手动/自动推进逻辑
- 管理接管期间的全局 `body` 光标隐藏类

建议文件：

- `static/yui-guide-director.js`

核心接口示意：

```ts
type YuiGuideDirectorOptions = {
  tutorialManager: UniversalTutorialManager;
  page: string;
  roleId?: string;
};

interface YuiGuideDirector {
  startPrelude(): Promise<void>;
  enterStep(stepId: string, context: GuideStepContext): Promise<void>;
  leaveStep(stepId: string): Promise<void>;
  handleInterrupt(event: GuideInterruptEvent): void;
  skip(reason?: string): void;
  abortAsAngryExit(source?: string): Promise<void>;
  destroy(): void;
}
```

补充建议：

- `skip(reason)` 是外部可调用的统一退出入口
- `skip(reason)` 负责写入教程结束原因，并在内部最终调用一次 `destroy()`
- `destroy()` 只负责最终清理，不承担业务判定，也不建议在正常退出流程里被外部重复调用
- `abortAsAngryExit()` 负责怒气演出和“特殊跳过”编排，避免把该逻辑散落在多个 `step` 回调里
- `skip / abortAsAngryExit / destroy` 应共享 `isTerminating`（或等价原子终止标记）与单一 `finalizeTermination()` 例程；首个入口负责设置标记并执行清理，重复触发只复用已有终止流程，不得重复跑半套清理
- 更高层调用方优先走 `skip()` 或 `abortAsAngryExit()`，不要在业务代码里直接多处调用 `destroy()`；`destroy()` 只允许被统一清理例程最终落点调用一次
- `handleInterrupt()` 内部应只做“状态迁移 + 节流判定 + 调度”，不要把弹簧动画细节都堆在 Director 本体

## 10.2 `YuiGuideBubble`

职责：

- 显示角色气泡
- 支持标题、正文、强调语句
- 支持跟随角色位置或固定在教程区域

建议能力：

- `show(text, options)`
- `update(text)`
- `hide()`

## 10.3 `YuiGuideVoiceQueue`

职责：

- 顺序播放教程语音
- 提供 `onEnded`
- 允许跳过时立即停止

建议实现：

- 第一阶段支持预录音频
- 第二阶段可接现有 TTS 体系

不建议：

- 直接复用聊天会话的流式 TTS 作为教程播报主链路

因为教程语音需要更确定的时序，不适合依赖会话状态。

## 10.4 `YuiGuideEmotionBridge`

职责：

- 给当前模型施加教程时的表情和动作
- 在教程结束时恢复原状态

建议复用：

- 已有 `applyEmotion` 能力
- Live2D / VRM / MMD 各自现有情感接口

## 10.5 `YuiGuideGhostCursor`

职责：

- 在当前页面渲染假光标
- 从当前位置平滑移动到目标元素
- 执行点击、停顿、晃动、回弹等动画
- 保存当前承诺轨迹，在用户打断时做“拉扯后回弹”

约束：

- 只渲染在当前页面
- 不修改真实系统鼠标位置
- 不依赖 Agent 自动化

建议能力：

```ts
moveTo(targetRect, options)
resistTo(userRealX, userRealY, options?)
click(options)
wobble(options)
setSpeedMultiplier(speed)
hide()
showAt(x, y)
cancel()
```

补充说明：

- `resistTo(userRealX, userRealY)`：
  - 根据 ghost cursor 当前坐标与用户真实鼠标事件坐标，计算一个朝用户事件方向的短距离拉拽向量
  - 先被“拖过去一点”，再用 `ease-out-elastic` 或等价弹簧曲线回到原定轨迹
  - 这是“Yui 紧紧抓住鼠标不放”的核心视觉 API，建议与 `CURSOR_RESISTING` 子状态一一对应
- `setSpeedMultiplier(speed)`：
  - 用于动态调整巡航速度
  - 适合“发现用户在看修改记忆后突然冲去点右上角关闭”这种情绪化加速演出
- 建议 `moveTo()` 内部保留最近一次目标轨迹信息，供 `resistTo()` 回弹后续航

## 10.6 `YuiGuidePageHandoff`

职责：

- 跨页面继续教程
- 保存当前 scene / token / 来源页 / 返回策略
- 目标页读取 token 后恢复指定 scene

建议存储：

- `sessionStorage` 优先
- 必要时 `localStorage`

建议数据：

```json
{
  "token": "uuid",
  "token_version": 1,
  "flow_id": "home_yui_guide_v1",
  "source_page": "home",
  "source_origin": "app://home",
  "target_page": "api_key",
  "resume_scene": "api_keys_intro",
  "nonce": "uuid",
  "created_at": 1770000000000,
  "expires_at": 1770000005000,
  "consumed_at": null,
  "signature": "base64(hmac-sha256(payload))"
}
```

其中 `signature` 仅表示“由后端 / 主进程使用仅服务端持有的 HMAC key 对 payload 生成的签名”。前端绝不能生成签名，也绝不能持有或推导出签名 key。

补充要求：

- token 必须具备单次消费语义，但浏览器侧 Web Storage 不能提供可靠的“原子单次消费”保证。推荐在记录中增加 `consumed_at` / `used`，或等价的一次性 nonce 状态，并由后端 / 主进程 / 中央 authority 通过 compare-and-set 或事务更新来完成“检查并标记已消费”；前端只负责发起请求和展示结果
- 恢复时必须校验来源绑定字段（至少 `source_page`，更推荐额外校验 `source_origin` / `source_id`）；若当前请求来源与 token 记录不一致，应拒绝恢复
- 推荐加入 `token_version` 与 `signature`（例如 HMAC）校验，避免旧格式 token 或被篡改 payload 被重放；签名生成与校验只能由后端 / 主进程负责，HMAC key 只能保存在服务端边界内，前端不得持有 key material，也不得尝试客户端签名
- 消费流程建议固定为：前端提交 token 给后端 / 主进程 → 后端 / 主进程在同一次 compare-and-set / 事务里检查 `expires_at` / `consumed_at` / `signature` / 来源绑定 / `token_version` → 校验通过后立即写入 `consumed_at` → 再返回允许恢复的结果，由前端继续恢复 `resume_scene`
- 错误处理上，`expired / used / signature_invalid / source_mismatch` 都应直接拒绝恢复并清理本地 token；只有在“后端成功写入 `consumed_at` 之前就发生存储或通信失败”时才允许重试。前端本地存储只能用于暂存展示态或待提交数据，不能依赖它提供单次消费保证

---

## 11. 步骤数据结构建议

推荐把“教程步骤”和“演出信息”放在同一份配置里，但按字段分层。

示例：

```ts
type YuiGuideStep = {
  id: string;
  page: 'home' | 'api_key' | 'memory_browser' | 'steam_workshop' | 'plugin_dashboard';
  anchor: string;
  tutorial: {
    title: string;
    description: string;
    autoAdvance?: boolean;
    allowUserInteraction?: boolean;
  };
  performance?: {
    bubbleText?: string;
    voiceKey?: string;
    emotion?: string;
    cursorAction?: 'move' | 'click' | 'wobble' | 'none';
    cursorTarget?: string;
    cursorSpeedMultiplier?: number;
    delayMs?: number;
    interruptible?: boolean;
    resistanceVoices?: string[];
  };
  navigation?: {
    openUrl?: string;
    windowName?: string;
    resumeScene?: string;
  };
  interrupts?: {
    mode?: 'ignore' | 'degrade' | 'theatrical_abort';
    threshold?: number;
    throttleMs?: number;
    resetOnStepAdvance?: boolean;
  };
};
```

### 字段设计原则

- `tutorial` 提供给高亮系统
- `performance` 提供给演出系统
- `navigation` 提供给跨页面系统
- `interrupts` 提供给打断分级和 `ANGRY_EXIT` 策略

补充建议：

- `performance.interruptible`
  - 决定当前步骤是否允许进入“较劲”表演
- `performance.resistanceVoices`
  - 提供轻度打断时可随机抽取的短语音池
- `interrupts`
  - 更偏向运行策略，如节流、阈值、是否在步骤推进时重置
- 优先级建议：
  - 若 `performance.interruptible === false`，则本步骤直接禁用较劲和 `ANGRY_EXIT`
  - 只有在 `performance.interruptible !== false` 时，`interrupts.mode / threshold / throttleMs` 才生效

这样未来改文案、改表情、改页面跳转时互不干扰。

---

## 12. 首页替换方案

## 12.1 推荐替换方式

不建议删除 `getHomeSteps()` 这一层入口。

推荐方式：

1. 保留 `UniversalTutorialManager` 的首页入口判断
2. 将首页原有步骤配置迁移到新的 `YuiGuideStep` 配置
3. `getHomeSteps()` 输出基础高亮步骤
4. `YuiGuideDirector` 根据 step id 追加 Yui 演出

## 12.2 为什么不建议完全重写首页教程引擎

因为当前系统已经处理了很多边角问题：

- 首次启动判定
- 页面延迟加载
- 浮动按钮等待
- 视口内定位
- 跳过按钮
- 拖动 popover

这些全都重写，收益不高，风险很大。

---

## 13. 插件管理相关设计

## 13.1 当前现实

插件管理当前是独立 Vue 面板，不在首页设置菜单内。

因此文档中的“展示插件管理”需要拆成两个层次：

- 逻辑上展示“插件能力”
- 技术上打开独立插件面板

## 13.2 推荐落地策略

下面的 A/B/C 是**插件能力这一条支线的子阶段**，用于说明插件相关能力怎么演进，不等同于第 7 节的全局 `Phase 1-4`。

### 插件子阶段 A

对应全局 `Phase 1`。

首页仍然锚定真实存在的“插件能力入口”或“Agent / 猫爪面板”，但点击后不强行跨到 Vue 面板。

推荐采用一层 **Smoke & Mirrors** 的演出代理：

- 在 `index.html` 当前页遮罩层上显示一个“插件列表预演层”
- 该层可使用高质量透明 `WebP`、短 `WebM`，或由 DOM + CSS 拼出来的假面板
- 预演层中可以包含“插件列表上下滑动”的视觉素材
- ghost cursor 在预演层表面完成点击、悬停、滑动演出
- 演出结束后立刻收起预演层，把话题引回首页设置或下一步

这样可以在 Phase 1 保留原剧本里的“Yui 很熟练地翻插件列表”的观感，同时不需要过早打通 `/ui/` 的跨技术栈教程。

### 插件子阶段 B

对应全局 `Phase 3` 之后的跨页能力。

从首页打开 `/api/agent/user_plugin/dashboard`。

### 插件子阶段 C

对应全局 `Phase 4`。

为 `/ui/` 新增一个轻量引导接入层，再继续讲解插件管理界面本身。

## 13.3 不建议的实现

不建议把插件管理伪装成首页设置菜单里的一个假按钮，然后在教程代码里偷偷跳到别处。

可以接受的“视觉欺骗”边界是：

- 锚点必须是真实存在的首页元素
- 假的是“二级内容预演层”，不是“一级入口本身”
- 预演层只承担演出，不承诺真实可操作能力
- 用户一旦跳过、关闭或打断，应立即回到真实页面结构

原因：

- 入口不真实
- 后续维护混乱
- 用户会形成错误认知

---

## 14. 音频、表情与动作需求

## 14.1 音频

建议支持两种来源：

- 预录语音资源
- 后续可选 TTS

第一阶段优先预录音频，理由：

- 节奏稳定
- 不依赖模型
- 不受网络波动影响

## 14.2 表情

教程演出期间建议使用有限表情集：

- `neutral`
- `happy`
- `surprised`
- `angry`
- `embarrassed` 或等价替代

优先使用项目当前已稳定存在的情绪能力，不为教程单独发明一整套表情协议。

## 14.3 动作

动作建议分三层：

- 轻动作：气泡、表情、视线变化
- 中动作：ghost cursor 点击、高亮切换
- 重动作：打开菜单、打开页面、显著 UI 演出

第一阶段避免高度依赖模型动作文件，先保证功能稳定。

---

## 15. 桌面端交互约束

## 15.1 必须遵守的限制

- 不修改真实系统鼠标位置
- 不锁定系统输入
- 不调用 Agent 自动化执行教程
- 不影响现有窗口拖动、缩放、穿透逻辑
- 不依赖操作系统层面的鼠标隐藏或抢占

## 15.2 允许的桌面增强

- 在透明窗口或当前页内绘制假光标
- 在教程接管期间为当前页面注入 `body.yui-taking-over`，用 CSS 隐藏系统光标视觉
- 利用现有窗口 API 实现独立窗口打开和聚焦
- 在必要时临时关闭某个浮层的穿透，保证用户能点击跳过

推荐样式：

```css
body.yui-taking-over,
body.yui-taking-over [data-yui-cursor-hidden='true'] {
  cursor: none !important;
}

body.yui-taking-over :is(
  [data-yui-skip-control],
  [data-yui-emergency-exit],
  button,
  [href],
  input,
  select,
  textarea,
  summary,
  [role='button'],
  [role='link'],
  [tabindex]:not([tabindex='-1'])
) {
  cursor: auto !important;
}

body.yui-taking-over :is(
  [data-yui-skip-control],
  [data-yui-emergency-exit],
  button,
  [href],
  input,
  select,
  textarea,
  summary,
  [role='button'],
  [role='link'],
  [tabindex]:not([tabindex='-1'])
):focus-visible {
  outline: 2px solid var(--yui-focus-ring, #fff);
  outline-offset: 3px;
}
```

说明：

- 这只是页面级视觉隐藏，不等于控制真实鼠标位置
- 不要对“跳过按钮 / 紧急退出控件 / 其他可交互控件”应用 `cursor: none`；若产品希望接管阶段统一隐藏光标，只应对明确标记为演出层或非交互层的元素（如 `data-yui-cursor-hidden="true"`）执行隐藏
- 接管场景必须提供键盘兜底退出，例如把 `Esc` 绑定到与跳过按钮相同的统一退出路径
- 接管期间的跳过 / 紧急退出控件必须保留可见 `:focus-visible` 样式，确保用户能通过键盘恢复常规交互
- `SKIPPED / ANGRY_EXIT / CANCELLED / COMPLETED` 时都必须移除该类
- 若遇到系统级菜单、原生窗口边框或浏览器外区域，真实鼠标仍可能可见，这属于可接受边界

---

## 16. 持久化与版本策略

## 16.1 已读键

由于首页教程将被替换，建议给新教程使用新版本键，避免沿用旧教程的已读状态。

建议：

- 旧键继续保留兼容
- 新键例如：
  - `neko_tutorial_home_yui_v1`

## 16.2 版本升级策略

当新教程内容发生大改时，可通过升级版本号强制重新展示：

- `home_yui_v1`
- `home_yui_v2`

---

## 17. 建议代码落点

建议新增或修改以下文件：

### 新增

- `static/yui-guide-director.js`
- `static/yui-guide-overlay.js`
- `static/yui-guide-steps.js`
- `static/css/yui-guide.css`
- `static/assets/tutorial/` 或等价目录
  - 用于存放插件预演层的 `WebP / WebM` 资源

### 修改

- `static/universal-tutorial-manager.js`
  - 增加与 `YuiGuideDirector` 的挂接点
- `templates/index.html`
  - 加载新脚本与样式
- `static/app-interpage.js`
  - 追加教程 handoff 事件处理
- `static/app-ui.js` / `static/app-buttons.js`
  - 视情况补出可复用的“教程触发请她离开”包装 API，避免教程层长期依赖按钮点击

### 后续可能新增

- `frontend/plugin-manager/src/...`
  - 插件管理页引导桥接

---

## 18. 实施顺序建议

### Milestone 1

- 建立 `YuiGuideDirector`
- 在首页接管现有教程的 step change 生命周期
- 加入气泡和表情

### Milestone 2

- 加入 ghost cursor
- 打通首页内菜单演出
- 保证跳过和中断稳定

### Milestone 3

- 加入页面 handoff
- 打通 `api_key / memory_browser / steam_workshop_manager`

### Milestone 4

- 评估 `/ui/` 插件管理页接入方式
- 为 Vue 面板补轻量教程桥

---

## 19. 测试要求

## 19.1 功能测试

- 首次启动会进入新教程
- 已完成后不会重复进入
- 跳过后状态正确写入
- 连续三次有效打断会进入 `ANGRY_EXIT`，并最终按 `skip` 语义稳定结束
- 轻度打断会进入 `CURSOR_RESISTING`，随后恢复到 `CURSOR_ACTING`
- ghost cursor 不影响真实鼠标
- 插件预演层能正确显示、自动收起，并在素材缺失时回退到简化 DOM 版本或直接跳过该演出
- `body.yui-taking-over` 会在接管阶段正确添加，并在结束时可靠移除
- 教程结束后所有临时 DOM 被清理

## 19.2 回归测试

- 首页已有浮动按钮逻辑不受影响
- 设置弹层正常打开关闭
- 聊天功能、语音、截图、Agent HUD 不受影响
- 独立聊天窗口、字幕窗口、Toast 不受影响

## 19.3 桌面端专项测试

- 透明窗口下跳过按钮始终可点击
- 多显示器环境下不出现错误定位
- 鼠标穿透开启/关闭时教程元素不失效

---

## 20. 风险与应对

## 20.1 风险：演出层和教程层耦合过深

应对：

- 演出信息独立配置
- `driver.js` 只管步骤，不管角色演出

## 20.2 风险：跨页面 handoff 容易丢状态

应对：

- token 化
- 加过期时间
- 失败时回退到普通页面教程

## 20.3 风险：桌面端窗口与穿透逻辑复杂

应对：

- 第一阶段不跨窗口
- 第一阶段不碰真实鼠标

## 20.4 风险：插件管理页技术栈不同

应对：

- 单独分阶段处理
- 不强行共用首页 DOM 教程实现

## 20.5 风险：`ANGRY_EXIT` 误触发导致体验突兀

应对：

- 中断计数必须做节流和位移阈值过滤
- 步骤切换后重置计数
- 优先在强演出步骤启用，普通高亮步骤可直接降级而不是累计惩罚
- `ANGRY_EXIT` 内部仍然复用 `skip` 清理路径，保证即使误触发也不会造成业务异常

## 20.6 风险：插件预演层过于“假”，用户认知错位

应对：

- 只把它用作 Phase 1 的短时演出代理，不冒充真实插件页完成实际任务
- 入口锚点必须真实存在
- 预演素材时长要短，并在结束后快速回到真实页面
- 等 `/ui/` 接入引导后，再逐步把该障眼法降级为可选表现层

## 20.7 风险：`body.yui-taking-over` 未清理，导致页面持续隐藏光标

应对：

- 光标类的增删必须集中在 `YuiGuideDirector` 的统一生命周期中管理
- `skip / destroy / abortAsAngryExit / page unload` 都要走同一套 `finally` 清理，并由共享的 `isTerminating` / termination promise 防止重复清理
- 开发阶段加入 watchdog 日志或断言，便于快速发现“教程结束但光标仍隐藏”的问题

---

## 21. 最终建议

后续开发时，请按以下原则执行：

1. 先把“角色演出层”建立在现有教程系统之上，不要直接推翻现有框架。
2. 首页替换优先，跨页面接力延后。
3. 伪鼠标只做视觉演出，不做真实系统控制。
4. 跳过永远优先，用户感知上的可控性高于戏剧效果。
5. 插件管理单独看作一个独立页面系统，不要强行塞进首页设置菜单模型里。

如果后续开始进入实现阶段，建议下一份开发文档继续细化：

- `YuiGuideStep` 配置格式
- `UniversalTutorialManager` 挂接点
- ghost cursor 动画协议
- handoff token 协议
- 首页替换的最小可运行版本任务拆分
