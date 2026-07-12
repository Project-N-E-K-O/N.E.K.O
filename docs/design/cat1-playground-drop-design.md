# CAT1 Playground Drop Design

## 1. 功能结论

CAT1 问号方块点击后，进入一个新的长生命周期形态：`playground drop`。

这个形态是后续小游戏入口的前置阶段，不是普通 idle 动作，也不是 10 秒临时演出。进入后，猫 gif 和当前关联目标先掉到屏幕底部；之后停留在 playground 形态里。用户点击猫 gif 会触发“变回模型”并结束 playground；当前代码还实现了非拖拽点击 playground 问号方块的独立结束路径：派发问号方块点击事件、恢复猫/毛线球起始位置，然后 release playground，但不触发“变回模型”。

核心规则：

- 入口只来自问号方块点击事件。
- 进入后生命周期很长，不自动恢复 idle 或模型。
- 生命周期期间 CAT1 相关状态切换默认全部禁用。
- 只有 playground 模块自己的白名单行为可以运行。
- 模块内有独立拖拽和重力物理表现。
- 点击猫 gif 是正常“变回模型”结束路径；点击 playground 问号方块是当前已实现的非模型 release 路径；页面销毁、切出 CAT1 等只作为兜底清理。

当前实现事实：

- 主项目提交：`fd9c39c9 fix(avatar): refine idle cat1 playground gates`
- 桌面端提交：`b4e339a fix(desktop): restore cat playground bridge readiness`
- 网页端 playground entry/drop 统一 gate 已收敛到 capability 维度：`_isNekoIdleCat1PlaygroundEntryOrDropActive(button, capability)` 只组合 pending entry 和 `_isNekoIdleCat1PlaygroundCapabilityBlocked(button, capability)`。
- 桌面端 active 生命周期锁现在覆盖 React Chat ready 和 compact ball ready/recreate 后的补发路径，避免长生命周期中窗口或 preload 重建后漏锁。
- 桌面端 React Chat 自最小化毛线球参与 playground 时，使用实际视觉球尺寸归一化，不再把 carrier 尺寸当作毛线球可见尺寸。
- 当前工作区已在主项目加入轻量 `PhysicsBody` 工厂式物理层：`_createNekoIdleCat1PlaygroundPhysicsBody(id, element, options = {})`，猫、毛线球和问号方块都通过同一工厂注册到 `state.bodies`。
- 当前工作区已加入物理体质量配置：猫 `2`，毛线球 `0.65`，问号方块 `5`；碰撞解析按质量 / inverseMass 分配位移和速度响应。
- 当前工作区已加入可见内容 inset 配置，用于按图片可见区域做碰撞矩形和底部/左右边界调整；不是运行时逐像素扫描。
- 当前工作区已加入平台底部基准：优先参考 `window.electronScreen.getCurrentDisplay().workArea`，并夹在当前 `window.innerHeight` 内，适配 macOS / 桌面工作区差异。
- 当前工作区已实现“问号方块作为第三物理体”：点击入口时创建新的 `question-block` DOM 元素，原问号触发状态仍清理，`question-block` 作为独立 body 参与重力、拖拽和碰撞。
- 当前工作区已实现 `question-block` 旋转物理：拖拽松手时根据指针速度和抓取偏移计算角速度，角速度上限为 `14rad/s`；落地后按 `Math.PI / 2` 步进吸附到最近的 90 度朝向。旋转能力通过 body options 开启，目前只给 `question-block` 使用，不让猫和毛线球跟着旋转。
- 当前工作区的 `question-block` 非拖拽点击会派发 `neko:idle-cat1-playground-question-block-click`，随后停止 playground 私有物理、恢复 `cat` / `yarn` / `desktop-yarn` 的进入前位置，并以 `question-block-click` release 生命周期；该路径不会派发 return-click，也不会触发猫变回模型。
- 当前工作区的桌面最小化毛线球区分“可见球矩形”和“Electron 原生窗口载体”：物理层只使用可见 `51px` 毛线球矩形；桌面端接收 pair move 回传时优先按当前 collapsed window bounds 反推透明 carrier，拿不到时才 fallback 到现有 `83px` 原生窗口 bounds。
- 当前工作区已实现桌面 overlay 问号点击保留：PC 侧只在入口 payload 中携带点击瞬间的 `questionBlockScreenRect`，Pet 页面基于该矩形创建 `question-block`，桌面端不实现问号物理。
- 当前工作区已补齐 pending entry 取消时的问号克隆清理：页面销毁或 pending 期间切出 CAT1 会调用 `_clearNekoIdleCat1PlaygroundQuestionBlockClone(button)`，避免残留 DOM。
- `.agent` 文档只记录设计和接手事实，不参与功能提交。

## 2. 范围

### 本阶段要做

- 消费 `neko:idle-cat1-playground-entry-request` 事件。
- 点击问号后清理原问号触发状态，并把用户可见问号方块转成新的 `question-block` 物理体进入 playground drop。
- 猫 gif 和关联目标竖直掉到屏幕底部。
- 初始投放、拖拽离地和松手飞行都共用同一套物理模拟。
- 空中阶段猫 gif 使用 `static/assets/neko-idle/cat-idle-cat-move-2.gif`。
- 落到底部后猫 gif 切回 CAT1 第一形态默认态。
- playground 形态期间提供独立拖拽。
- 猫 gif、毛线球和问号方块受重力影响。
- 松手后按速度和方向产生上抛、下坠、抛物线和减速运动。
- 生命周期期间统一禁用和恢复其他 CAT1 相关能力。
- 点击问号进入 playground 后，用户可见的问号方块不直接消失，而是作为第三个独立 playground 物理体参与重力、拖拽和碰撞。

### 本阶段不做

- 小游戏本体。
- 掉到底部后的完整玩法规则。
- 新音效、表情、奖励、分数、道具。
- 自动恢复到 idle。
- 自动变回模型。
- 新增随机触发入口。
- 改动键盘序列触发条件。
- 设计小游戏里的胜负、碰撞、控制方式。

## 3. 入口事件

现有入口分两段：

1. 键盘隐藏序列触发问号方块出现。
2. 点击问号方块派发 `neko:idle-cat1-playground-entry-request`。

本设计只消费第二段事件，不重新设计键盘序列，也不改变问号 10 秒显示逻辑。

入口事件语义：

- 事件名：`neko:idle-cat1-playground-entry-request`
- `trigger`: `cat1-question-mark`
- 当前来源：
  - 网页端问号 DOM 点击：`source: 'question-mark'`
  - 桌面端 overlay 点击：`source: 'desktop-question-mark'`

事件只表示“请求进入 playground drop”。桌面端 overlay 只负责把入口事件回传给 Pet 页面，不在主进程里提前理解小游戏语义。

入口事件边界：

- 入口事件只负责进入 playground drop，不承载后续物理体控制语义。
- 桌面端 overlay 不应因为问号方块扩展而新增长期物理、拖拽、bounds 跟随或碰撞逻辑。
- 若桌面 overlay 点击后需要在 Pet 页面生成问号物理体，只允许把点击瞬间的 overlay screenRect 随入口请求传回 Pet 页面；后续物理状态仍只在主项目页面层计算。

## 4. 用户可见流程

1. 用户通过键盘序列召唤问号方块。
2. 用户点击问号方块。
3. 原问号触发状态被清理，同时创建新的用户可见 `question-block`，作为 playground 第三物理体继续存在。
4. 如果聊天框当前不是毛线球 / minimized 态，先请求现有聊天框收缩链路把它变成毛线球。
5. 毛线球目标可用后，CAT1 进入 playground drop 长生命周期。
6. 猫 gif、当前关联目标和问号方块物理体一起进入同一套物理循环。
7. 掉落期间猫 gif 显示 `cat-idle-cat-move-2.gif`。
8. 落到底部后猫 gif 切回 CAT1 第一形态默认态。
9. 用户可以拖拽猫 gif、毛线球和问号方块物理体。
10. 松手后，被拖拽物体根据松手速度和方向进入重力物理运动。
11. 用户非拖拽点击猫 gif 后，退出 playground drop 并走“变回模型”流程。
12. 用户非拖拽点击 playground 问号方块后，派发独立问号方块点击事件，恢复猫/毛线球到进入前位置，并 release playground；该路径不走“变回模型”流程。

当前关联目标：

- 网页内毛线球 / compact surface DOM：移动对应 DOM。
- 桌面最小化聊天框：作为独立物理体参与主项目页面层的物理模拟，再通过现有桌面同步位置链路移动窗口。
- 问号方块：点击入口后新增为 playground 内第三个独立物理体，不复用原问号触发状态，不属于猫 gif，也不属于毛线球。
- 没有可移动目标：猫 gif 可独自下落到底部，这是兜底，不影响生命周期锁定。

## 5. 状态模型

建议使用独立状态对象：

- `button.__nekoIdleCat1PlaygroundDropState`

核心字段：

- `active`: 是否处于 playground drop 生命周期。
- `phase`: 当前阶段。
- `token`: 生命周期唯一令牌，用于阻止旧 RAF、timer、pointer 回调写回。
- `frame`: requestAnimationFrame id。
- `button`: 当前猫按钮。
- `container`: 猫 gif 外层容器。
- `targetMode`: `dom`、`desktop` 或 `none`。
- `targetElement`: DOM 目标，仅 `dom` 模式使用。
- `targetScreenRect`: 桌面目标，仅 `desktop` 模式使用。
- `start`: 猫和目标的起点位置。
- `end`: 猫和目标的底部目标位置。
- `lastTickAt`: 上一次物理帧时间。
- `bodies`: playground 内受重力影响的物理体，包括猫 gif、毛线球、桌面最小化聊天框和问号方块；所有物理体都进入同一 `bodies` 集合。
- `draggingBodyId`: 当前被 playground 独立拖拽接管的物理体。
- `lastPointerSamples`: 最近若干个 pointer 位置和时间，用于计算松手速度。
- `gravityPxPerSecond2`: 重力加速度。
- `floorY`: 当前屏幕底部碰撞线。
- `wallLeft` / `wallRight`: 当前屏幕左右碰撞线。
- `maxDeltaMs`: 单帧物理更新时间上限，防止后台或卡顿后瞬移。
- `disabledCapabilities`: 生命周期期间由模块统一禁用的能力集合。
- `restoreSnapshot`: 进入前需要恢复的监听、timer、pointer capture、hover playback 等状态。
- `releaseReason`: 退出原因，例如 `cat-click`、`tier-change`、`page-destroy`。
- `previousArt`: 进入前猫 gif 图片，仅用于调试或异常兜底；正常点击退出不恢复 idle 图源。

阶段：

1. `inactive`
   未进入模块。

2. `dropping`
   原问号触发状态清理，猫、目标和用户可见问号方块作为独立物理体进入初始投放。猫图源是 `cat-idle-cat-move-2.gif`。

3. `settled`
   猫和目标已到底部；猫图源切回 CAT1 第一形态默认态；等待拖拽或点击退出。

4. `dragging`
   用户正在拖动 playground 内的猫 gif、毛线球或已实现的问号方块物理体；被拖动物体由指针位置控制，同时记录速度样本。

5. `ballistic`
   用户松手后，被拖动物体按初速度和重力运动；猫 gif 空中保持 `cat-idle-cat-move-2.gif`，落地后切回默认态。

6. `exiting`
   用户点击猫 gif，走变回模型流程；模块清理自己的状态、物理帧和锁。

## 6. 统一生命周期管理

playground drop 必须由统一生命周期管理器负责进入、禁用、运行、恢复和清理。不要把禁用和恢复散落写在拖拽、随机动作、键盘监听、hover 或桌面同步的局部函数里。

### 核心原则

生命周期 active 后采用“默认全禁，白名单放行”：

- CAT1 相关状态切换默认禁用。
- CAT1 相关动作入口默认禁用。
- 非 playground 模块的图源写入默认禁用。
- 非 playground 模块的位置写入默认禁用。
- 新增 CAT1 动作或状态切换时，默认也被禁用，除非明确加入白名单。

建议核心方法：

- `_acquireNekoIdleCat1PlaygroundDropLifecycle(button, entryDetail)`
- `_releaseNekoIdleCat1PlaygroundDropLifecycle(button, reason)`
- `_isNekoIdleCat1PlaygroundDropActive(button)`
- `_isNekoIdleCat1PlaygroundEntryOrDropActive(button, capability)`
- `_isNekoIdleCat1PlaygroundCapabilityBlocked(button, capability)`
- `_registerNekoIdleCat1PlaygroundCleanup(button, cleanup)`

当前实现要求：

- 业务入口仍可调用 `_isNekoIdleCat1PlaygroundEntryOrDropActive(button)` 取得“是否有 entry/drop 占用”的旧语义。
- 需要按能力判断时传入 `capability`，由 `_isNekoIdleCat1PlaygroundCapabilityBlocked(button, capability)` 执行白名单判定。
- `_isNekoIdleCat1PlaygroundEntryOrDropActive(button, capability)` 不直接读取 drop active 作为最终结论，避免绕过统一 capability gate。

### 进入时统一执行

- 创建唯一 `token`。
- 写入 `active: true` 和初始 `phase`。
- 建立 `disabledCapabilities`。
- 保存 `restoreSnapshot`，只记录确实需要恢复的监听、timer、pointer 和临时样式。
- 取消或暂停会抢写表现层的当前动作。
- 注册 cleanup，包括 RAF、pointer listener、resize listener、desktop bounds 同步尾帧等。

不要把 idle 基线或模型状态写成 playground 私有恢复目标。

### 生命周期期间默认禁用

以下是重点清单，不代表只有这些需要禁用：

- CAT1 形态切换入口。
- 普通 idle 拖拽入口。
- 原生/网页 return ball drag 入口。
- 随机 pair move、吃、玩、journey、walk、compact top edge follow。
- hover 点击态图源播放。
- edge peek、reclamp 等会移动猫位置的链路。
- 键盘隐藏序列监听或触发结果。
- 问号方块再次出现或再次点击进入。
- 普通最小化、恢复、dock、return、goodbye 等会接管猫/聊天框位置或模型状态的表现层动作，除非它们是用户明确触发的退出流程。
- 教程、idle、随机 timer、窗口 resize/reclamp、compact 跟随、跨窗口同步触发的自动状态切换。
- 任何非 playground 模块发起的 `_setNekoIdleReturnArtSource`。
- 任何非 playground 模块发起的 `container.style.left/top` 或等价位置写入。

### 生命周期期间允许

白名单行为：

- playground 初始物理投放。
- playground 内部独立拖拽。
- playground 物理帧更新猫 gif 和毛线球位置。
- playground 为桌面最小化聊天框发送 pair move bounds。
- 非拖拽点击猫 gif 触发退出并变回模型。
- 非拖拽点击 playground 问号方块触发独立点击事件，并通过 release 结束 playground，但不变回模型。
- 明确属于 release 流程的模型恢复、监听恢复和临时状态清理。
- 页面销毁、切出 CAT1 等外部兜底清理。

### 退出时统一恢复

只允许 `_releaseNekoIdleCat1PlaygroundDropLifecycle` 清理 active 锁和恢复能力。

release 负责：

- 取消所有 RAF、timer、pointer listener、resize listener。
- 释放 pointer capture。
- 清空 `draggingBodyId` 和 pointer samples。
- 清理 playground 物理体和临时样式引用。
- 恢复被暂停的键盘序列监听资格，但不补发期间漏掉的按键。
- 恢复随机动作调度资格，但不立刻补触发期间被拦截的随机动作。
- 恢复 hover、edge peek、drag 等入口资格。

不同退出原因的处理：

- `cat-click`: 不恢复 idle 图源和位置，因为后续进入“变回模型”流程。
- `question-block-click`: 先恢复 `cat` / `yarn` / `desktop-yarn` 的进入前位置，把猫图源切回 CAT1 第一形态默认态，再 release；不触发“变回模型”。
- `tier-change` / `page-destroy` / 异常兜底：只做清理和解锁，不主动启动新动作。

release 必须幂等：

- 重复调用 release 不重复触发模型切换。
- 重复调用 release 不重复派发事件。
- 重复调用 release 不补跑随机动作。
- 旧 token 的 RAF、timer、pointer 回调在 release 后必须直接 return。
- 局部模块发现 playground active 时只负责拒绝或停止自身动作，不负责解除 playground 锁。

## 7. 进入流程

收到 `neko:idle-cat1-playground-entry-request` 后：

1. 找到当前 CAT1 return button。
2. 若当前不是 CAT1，忽略。
3. 若已处于 playground drop，忽略重复入口；最多只清理问号，不重启物理流程。
4. 清理原问号触发状态，同时保留/创建用户可见的 `question-block` 物理体。
5. 若当前没有可用的毛线球 / minimized 聊天框目标，派发 `neko:idle-cat1-playground-yarn-request`，由聊天框复用现有 `setChatSurfaceMode('minimized')` 收缩为毛线球。
6. 收缩等待期间记录 pending entry，并把键盘触发、普通拖拽、随机动作、journey / pair move 等入口视为已占用，避免在正式 drop 前插入其它 CAT1 行为。
7. 毛线球目标可用后继续；若超时仍没有目标，只允许猫 gif 独自进入兜底下落。
8. 取消当前会竞争的短期动作：
   - `_cancelNekoIdleCat1Journey(button, { resetArt: false, preserveObservers: true })`
   - `_cancelNekoIdleCat1EatAction(button, { restoreArt: false })`
   - `_cancelNekoIdleCat1PlayAction(button, { restoreArt: false })`
   - `_finishNekoIdleReturnDragAction(button, { restoreArt: false })`
   - 清理 hover playback。
9. 调用 `_acquireNekoIdleCat1PlaygroundDropLifecycle(button, detail)`。
10. 设置猫 gif 图源为 `/static/assets/neko-idle/cat-idle-cat-move-2.gif`。
11. 计算猫和关联物理目标的起点、底部/左右边界，并立即注册初始物理体。
12. 启动 requestAnimationFrame 物理循环。

初始投放落地后：

1. 将猫和关联物理目标标记为 `grounded`。
2. 猫图源从 `cat-idle-cat-move-2.gif` 切回 CAT1 第一形态默认态。
3. 开启 playground 独立 pointer 拖拽监听。
4. 保持 active 状态，等待拖拽、物理运动或点击猫 gif 退出。

## 8. 物理位置与初始投放

初始下落和拖拽松手后的飞行/下落共用同一套物理循环，不写两套动画。区别只在于物理体的初始速度来源：

- 初始投放：`vx = 0`，`vy = 0`，由重力自然带到底部。
- 拖拽松手：`vx`、`vy` 来自最近 pointer samples。

第一版采用稳定简单规则：

- X 方向不变。
- 猫和关联物理目标各自受重力影响，最终落到屏幕底部上方。
- 目标 top：`window.innerHeight - element.height`
- 猫 top：`window.innerHeight - cat.height`
- 左右边界：
  - `wallLeft = 0`
  - `wallRight = window.innerWidth - body.width`

如果猫和关联物理目标都存在：

- 两者同步进度。
- 两者保持各自 left 不变。
- 不要求最终 top 相同。
- 两者进入同一个 physics tick，保证体感上一起掉下去。

如果未来需要猫落在目标旁边或目标上方，再单独扩展底部布局规则。当前不要提前设计小游戏站位。

## 9. 物理循环参考

本模块不需要第一版就接入完整物理引擎。当前实现中 `cat` / `yarn` / `desktop-yarn` / `question-block` 都通过同一轻量 `PhysicsBody` 模拟层运行。

### 当前轻量 PhysicsBody 架构

当前代码没有独立的 JS `class extends` 物理类体系；实际可复用边界是“通用工厂 + options 配置”的轻量 `PhysicsBody` 模拟层：

- 通用工厂：`_createNekoIdleCat1PlaygroundPhysicsBody(id, element, options = {})`
- 统一集合：`state.bodies = new Map()`
- 统一字段：`id`、`element`、`desktop`、`mass`、`inverseMass`、`visibleInsetRatios`、`x/y`、`vx/vy`、`dragging`、`grounded`、`floorY`、`wallLeft`、`wallRight`；支持旋转的 body 还带 `rotationEnabled`、`rotation`、`angularVelocity`、`angularDamping`、`angularGroundDamping`、`settleRotationWhenGrounded`、`restRotationStepRad`、`restRotationOffsetRad`、`rotationSettling`、`rotationSettleTarget`、`rotationSettleSpeed`
- 统一更新：重力、边界、拖拽、碰撞、旋转和位置写回都走 playground 物理循环。

因此新增物理体时，必须通过同一工厂和同一 `state.bodies` 集合接入；不能在业务流程里绕过工厂拼 ad hoc 对象，也不能把某个物理体挂到猫 gif、问号触发状态或桌面 overlay 生命周期上。

当前已确认的 body 配置：

- `cat`: 主项目猫 gif 容器，`mass = 2`。
- `yarn`: 网页内毛线球 / compact surface DOM，`mass = 0.65`。
- `desktop-yarn`: 桌面最小化聊天框在 Pet 页面层的镜像 body，`mass = 0.65`；Pet 页面物理体使用可见 `51px` 毛线球矩形，桌面端只接收 pair move 后按当前 carrier 换算出的原生窗口 bounds。
- `question-block`: 从点击瞬间的问号视觉矩形创建的新 DOM 元素，`mass = 5`，大于猫 `2`；当前唯一启用旋转的 body。

`question-block` 当前实现边界：

- 和 `cat` / `yarn` 一样通过 `_createNekoIdleCat1PlaygroundPhysicsBody(...)` 注册。
- 独立存在于 `state.bodies`，不是猫 body 的子元素、子状态或特殊拖拽分支。
- 具有自己的 `visibleInsetRatios`、质量和点击策略；这些配置作为 body options / 注册配置进入，不污染猫和毛线球。
- 具有自己的旋转 options：`rotationEnabled: true`、`settleRotationWhenGrounded: true`、`restRotationStepRad: Math.PI / 2`、`restRotationOffsetRad: 0`。默认角阻尼为 `0.986`，落地角阻尼为 `0.88`，回正速度为 `8`，停止阈值为 `0.02rad/s`，吸附误差为 `0.01rad`。
- 点击事件为 `neko:idle-cat1-playground-question-block-click`。当前实际代码中，非拖拽点击 `question-block` 不触发原入口事件，也不触发猫变回模型；它会停止 playground 物理、恢复猫/毛线球进入前位置，并以 `question-block-click` release 生命周期。

参考方向：

- MDN `requestAnimationFrame`：用浏览器帧循环读取时间差、更新状态、写回位置。
  - https://developer.mozilla.org/en-US/docs/Web/API/Window/requestAnimationFrame
- MDN game loop：参考输入、更新状态、渲染的主循环组织方式。
  - https://developer.mozilla.org/en-US/docs/Games/Anatomy
- MDN Pointer Events / pointer capture：拖拽时统一处理鼠标、触控和笔输入，并确保指针离开元素后仍能收到 move/up。
  - https://developer.mozilla.org/en-US/docs/Web/API/Pointer_events
  - https://developer.mozilla.org/en-US/docs/Web/API/Element/setPointerCapture
- Matter.js：参考 body、velocity、gravity、MouseConstraint 的概念，但不直接引入完整引擎。
  - https://brm.io/matter-js/docs/classes/Engine.html
  - https://brm.io/matter-js/docs/classes/Body.html
  - https://brm.io/matter-js/docs/classes/MouseConstraint.html
- Box2D：参考 body 拥有 position、velocity、force/gravity、static/dynamic 类型的建模方式。
  - https://box2d.org/documentation/md_simulation.html

核心公式：

```js
dt = Math.min(now - lastTickAt, maxDeltaMs) / 1000;

vy += gravity * dt;
x += vx * dt;
y += vy * dt;
vx *= damping;

if (x <= wallLeft) {
  x = wallLeft;
  vx = Math.abs(vx) * wallRestitution;
}

if (x >= wallRight) {
  x = wallRight;
  vx = -Math.abs(vx) * wallRestitution;
}

if (y >= floorY) {
  y = floorY;
  vy = 0;
  grounded = true;
}
```

同一套公式用于：

- 点击问号后的初始投放。
- 拖拽松手后的上抛。
- 拖拽松手后的侧向抛物线。
- 拖拽松手后的下坠。
- 窗口尺寸变化后重新受重力约束到底部。

### 旋转与落地回正

当前旋转是轻量 `PhysicsBody` 的可选能力，不是独立物理引擎，也不是 `question-block` 专用 tick 分支。实际边界：

- 只有注册时传入 `rotationEnabled: true` 的 body 才参与旋转；当前只有 `question-block` 开启。
- pointer down 时，如果 body 支持旋转，会清零 `angularVelocity` 并取消 `rotationSettling`，避免拖拽开始时继续残留自转或回正。
- pointer up 时，通过 `_getNekoIdleCat1PlaygroundThrowAngularVelocity(body, velocity, state)` 根据松手线速度和抓取点相对中心的偏移计算角速度。
- 如果叉乘得到的角速度过小，使用水平速度兜底生成轻微旋转体感。
- 角速度通过 `_clampNekoIdleCat1PlaygroundAngularVelocity(...)` 限制在 `-14rad/s` 到 `14rad/s` 之间。
- 空中和未进入回正阶段时，`rotation += angularVelocity * dt`，角速度按阻尼衰减。
- 落地且没有被拖拽时，如果开启 `settleRotationWhenGrounded`，会吸附到最近的 `restRotationStepRad` 角度；当前 `question-block` 是每 `Math.PI / 2` 一档，也就是 90 度回正。
- 回正使用弹簧式角速度推进和指数阻尼，不依赖地面线性停止阈值；回正完成后把 `rotation` 固定到目标角度并把 `angularVelocity` 清零。
- 位置写回后统一调用 `_applyNekoIdleCat1PlaygroundBodyRotation(body)`，实际 DOM 使用 `transform: rotate(<rad>rad)`。
- 不要在 physics tick 里写 `body.id === 'question-block'` 的特殊旋转分支；新增旋转体时应继续通过 body options 配置。

物理帧安全规则：

- `dt` 必须设置上限，例如 `maxDeltaMs = 32` 或 `50`。
- RAF 因后台、卡顿或系统休眠产生过大时间差时，只按上限推进一帧。
- 左右边界采用轻微反弹，不直接消失或飞出屏幕。
- 底部第一版仍以落地停住为主，避免过度弹跳影响后续小游戏设计。

## 10. 独立拖拽与重力

playground drop active 后，普通拖拽系统被锁住，但模块内部提供自己的拖拽能力。第一版可复制现有拖拽的 pointer capture、坐标计算和移动写入方式，但必须接入独立状态和物理更新，不复用普通拖拽的结束恢复逻辑。

### 可拖拽物体

- 猫 gif。
- 毛线球 / 当前网页内关联目标。
- 桌面最小化聊天框。
- 问号方块 `question-block`。

这些对象都是独立物理体，独立参与重力和拖拽。桌面最小化聊天框的物理状态仍在主项目页面层计算；桌面端只接收 bounds 同步并移动窗口，不在主进程里实现 playground 物理语义。

### 拖拽期间

- 被拖动物体跟随指针。
- 记录最近几个 pointer sample：`x`、`y`、`timestamp`。
- 被拖动物体暂时不受重力积分影响，避免指针拖拽和物理帧抢写。
- 如果被拖动物体支持旋转，拖拽开始时清零角速度并退出落地回正状态。
- 如果被拖动物体是猫 gif，猫图源切为 `static/assets/neko-idle/cat-idle-cat-move-2.gif`。
- 如果猫 gif 离开底部地面线，也保持 `cat-idle-cat-move-2.gif`。

### 松手后

- 根据最近 pointer sample 计算初速度 `vx`、`vy`。
- 将该物体从 `dragging` 切到 `ballistic`。
- 如果该物体支持旋转，按最近 pointer sample 的线速度和抓取偏移计算 `angularVelocity`，让松手产生自转。
- 物理帧按速度、方向和重力更新位置：
  - `x += vx * dt`
  - `y += vy * dt`
  - `vy += gravity * dt`
  - `vx` 可加入轻微阻尼，形成减速体感。

表现结果：

- 初速度向上时，上升后下落。
- 初速度向侧方时，形成抛物线。
- 初速度向下时，加速下坠。
- 问号方块松手后会根据角速度旋转，落地后逐步吸附到最近的 90 度朝向。

### 落地规则

- `floorY` 使用当前视口底部减去物体高度。
- `wallLeft` / `wallRight` 使用当前视口左右边界减去物体宽度。
- 当物体触碰左右边界时，按当前 `vx` 方向做轻微反弹，并施加水平阻尼。
- 当物体 `y >= floorY` 时钳制到 `floorY`。
- 第一版先采用“落地即停”或极轻微反弹后二次停住。
- 猫 gif 落地后切回 CAT1 第一形态默认态。
- 猫 gif 在任意空中阶段都保持 `cat-idle-cat-move-2.gif`，包括初始下落、拖拽离地、松手飞行、上抛和下坠。
- 支持旋转的 body 落地后仍可能继续进行角度回正；不要因为线性速度已停止就提前停掉 RAF。

重力影响范围：

- 猫 gif、毛线球和桌面最小化聊天框都是受重力影响的 playground 物理体。
- 未被拖拽的物理体如果已经稳定落地，不需要持续做无意义计算。
- 窗口尺寸变化导致底部线变化时，应重新计算 `floorY`，让物体继续受重力约束并最终停在新的底部。
- 窗口尺寸变化导致左右边界变化时，应重新计算 `wallLeft` / `wallRight`，避免物理体停在屏幕外。

### 点击与拖拽优先级

- 点击猫 gif 用于退出该长生命周期。
- 如果用户正在拖动猫 gif，pointer move/up 属于拖拽，不触发退出。
- 只有非拖拽点击猫 gif 才进入“变回模型”流程。

### 问号方块第三物理体扩展

这是点击问号进入 playground 后的已实现扩展。历史 13:45 边界内尚未包含该扩展；当前工作区已重新按本文边界实现。

用户可见目标：

- 正常问号出现、10 秒自动消失、键盘序列触发流程不变。
- 只有点击问号进入 playground 后，用户可见问号方块才不直接消失，而是作为 playground 内的第三个独立物理体下落。
- 问号方块可拖拽，受重力、底部、左右边界和其他物理体碰撞影响。
- 问号方块比猫重；当前质量为 `5`，猫为 `2`。
- 问号方块拖拽松手后会按松手速度和抓取偏移产生旋转，落地后吸附到最近的 90 度朝向；猫和毛线球不启用旋转。
- 非拖拽点击问号方块不触发原入口事件，也不触发猫变回模型；当前代码会触发新的独立事件 `neko:idle-cat1-playground-question-block-click`，随后恢复猫/毛线球进入前位置并 release playground。

实现边界：

- 不复用原问号 DOM / timer / keyboard state 作为长期物理体状态。
- 当前做法是：点击时读取问号视觉矩形，创建一个新的 `question-block` DOM 元素交给 playground 物理系统；原问号触发状态仍按入口流程清理。
- `question-block` 必须通过 `_createNekoIdleCat1PlaygroundPhysicsBody(...)` 创建，进入 `state.bodies`。
- `question-block` 必须是和 `cat`、`yarn` 并列的独立 body，不属于猫容器，不挂在毛线球目标，也不复用桌面 overlay。
- `question-block` 的质量、可见 inset、点击策略都应是自身配置；不能为了它修改猫或毛线球的质量、尺寸、拖拽、点击退出或桌面同步语义。
- `question-block` 的拖拽应复用 playground 通用 pointer / physics 机制；不要新建第二套拖拽系统。

桌面端边界：

- 桌面问号 overlay 仍只负责显示和回传入口事件。
- 桌面问号 overlay 不参与 playground 物理，不保留为长期窗口，不接收每帧 bounds，不处理拖拽和碰撞。
- 不新增 question-block 专用桌面 bounds IPC。
- 不扩展桌面毛线球 pointer bridge 来服务问号方块；`desktop-yarn` 仍是该 bridge 的目标。
- 桌面 overlay 点击后在 Pet 页面同位置生成 `question-block` 时，只允许在入口 payload 中携带点击瞬间的 overlay screenRect；当前字段为 `questionBlockScreenRect`，由 Pet 页面创建 DOM 物理体。

禁止事项：

- 不把问号方块和猫绑定成同一个 body。
- 不把问号方块注册成猫 body 的子元素后依赖猫移动。
- 不在 PC 主进程实现问号方块物理语义。
- 不让原问号点击监听在 playground active 后再次派发 `neko:idle-cat1-playground-entry-request`。
- 不因为问号扩展修改已存在的 `cat`、`yarn`、`desktop-yarn` 注册顺序、质量、尺寸来源或桌面 bounds 同步方式，除非另有明确需求。

## 11. 目标移动

DOM 目标：

- 使用现有直接定位工具，例如 `_setNekoIdleCat1PairMoveChatPosition(shell, left, top)`。
- 只写目标元素的 `left/top/right/bottom/transform`，不改结构。

桌面最小化聊天框：

- 作为独立物理体参与 playground 物理循环。
- 主项目页面层根据该物理体计算 screenRect。
- 复用现有 pair move 桌面同步链路发送位置：`_dispatchNekoIdleDesktopChatPairMoveBounds(screenRect, { force: true })`。
- Linux 桌面端普通 pair move 仍可跳过 native bounds sync，但 playground 物理同步使用 `force: true` 时必须允许最终位置写回。
- 每帧或节流后发送目标 screenRect；落地、反弹或退出前必须 force 一次，确保最终位置准确。
- 桌面主进程只移动窗口，不计算速度、重力或碰撞。
- React Chat 自最小化毛线球的 screenRect 必须归一化到实际可见球尺寸，不能使用外层 carrier 尺寸参与 playground 物理体计算。

猫 gif 容器：

- 使用现有容器定位工具，例如 `_setNekoIdleCat1ContainerPosition(container, left, top)`。

## 12. 退出流程

变回模型退出只来自非拖拽点击猫 gif：

1. 如果 playground drop active，允许这次点击继续走“猫变回模型”流程。
2. 点击前取消 playground drop 物理帧。
3. 调用 `_releaseNekoIdleCat1PlaygroundDropLifecycle(button, 'cat-click')`。
4. 不恢复 idle 图源，因为页面会进入模型形态。
5. 不触发随机动作、pair move 或 journey 重启。

退出顺序：

1. 先停止 playground 私有物理帧、pointer listener、resize listener 和 desktop bounds 同步。
2. 再 release playground 生命周期锁。
3. 然后进入“变回模型”流程。
4. 普通 CAT1 能力恢复后，不能在模型切换完成前立刻触发随机动作或其他自动状态切换。

注意：

- 普通点击猫 gif 在非 playground drop 状态下继续保持原有行为。
- playground drop 状态下，非拖拽点击猫 gif 是唯一会进入“变回模型”的退出交互。
- playground drop 状态下，非拖拽点击问号方块是当前已实现的非模型退出交互：派发 `neko:idle-cat1-playground-question-block-click`，恢复 `cat` / `yarn` / `desktop-yarn` 起始位置，release playground，不派发 return-click。
- 切出 CAT1 或页面销毁也必须调用同一个 release 方法清理状态，但这是外部生命周期兜底，不是正常结束路径。

## 13. 与现有功能的关系

问号触发：

- 键盘序列只负责让问号出现。
- 问号点击只负责进入 playground drop。
- 问号显示仍保留 10 秒自动清理。
- playground drop active 时键盘识别关闭或直接忽略。
- 退出时恢复键盘识别资格，但不回放生命周期期间的键盘输入。

拖拽：

- playground drop active 时禁止普通 idle 拖拽启动。
- playground 内部独立拖拽只由 playground 物理模块接管。
- 如果进入前正在拖拽，应先结束拖拽动作并停止拖拽图源抢写。

随机动作：

- pair move、吃、玩都不得在 playground drop 期间触发。
- 已排队的 timer / ready flag 应在进入时清理或在触发处被 gate 拦截。
- 退出时只恢复随机调度资格，不立即补跑被禁用期间跳过的随机动作。

compact top edge / 边缘探头：

- playground drop 期间不允许 compact top edge follow、edge peek、reclamp 等链路写猫位置。

桌面 overlay：

- 桌面问号 overlay 点击后只负责把入口事件回传给 Pet 页面。
- playground drop 的实际表现和状态仍在主项目页面层消费。

桌面生命周期桥：

- Pet 页面通过 `neko:idle-cat1-playground-state` 告知桌面端 active/inactive。
- active 时桌面端对 compact ball / React Chat 发送 `LOCK_PLAYGROUND_INTERACTION`，让它们进入 playground pointer bridge，而不是普通拖拽/恢复逻辑。
- React Chat `dom-ready` 后，如果 playground bridge 仍 active，必须补发 lock。
- compact ball ready / recreate / appear done 后，如果 playground bridge 仍 active，必须补发 lock。
- ready 补发只恢复锁定状态，不重新进入 playground，不重启物理，不改 Pet 页面生命周期。

## 14. 验收标准

基础行为：

- 点击问号后原问号触发状态被清理，用户可见问号方块转为第三独立物理体，不再表现为直接消失。
- 非拖拽点击 playground 问号方块会派发 `neko:idle-cat1-playground-question-block-click`，恢复猫/毛线球进入前位置，并 release playground；不会触发猫变回模型。
- 猫 gif 切换为 `cat-idle-cat-move-2.gif`。
- 猫、当前目标和问号方块一起进入同一物理循环并受重力影响。
- 初始投放由同一套 physics tick 驱动，不使用独立 tween 或第二套动画。
- 到底后猫 gif 切回 CAT1 第一形态默认态并停留。
- 不自动恢复模型。
- 点击猫 gif 后走变回模型流程，并结束 playground drop。

独立拖拽行为：

- playground drop 期间普通 idle 拖拽不启动。
- playground 内猫 gif 可以被拖拽。
- playground 内毛线球可以被拖拽。
- playground 内问号方块可以被拖拽。
- 拖拽松手后，根据松手方向和速度产生上抛、下坠、抛物线或减速运动。
- 猫 gif 空中阶段始终显示 `cat-idle-cat-move-2.gif`。
- 猫 gif 落到底部后显示 CAT1 第一形态默认态。
- 猫 gif 和毛线球最终都受底部地面线约束，不掉出屏幕。
- 猫 gif、毛线球、桌面最小化聊天框和问号方块都是同一体系下的独立物理体。
- 这些物理体都受重力、底部边界和左右边界影响。
- 物理体触碰左右边界时会轻微反弹，不飞出屏幕。
- 问号方块支持拖拽松手后的旋转和落地 90 度回正；猫、毛线球和桌面最小化聊天框不应因为该扩展获得旋转表现。

隔离行为：

- playground drop 期间采用默认全禁策略，CAT1 相关状态切换默认不执行。
- 只有 playground 独立拖拽、物理帧、桌面 bounds 同步、点击猫退出和外部兜底清理属于白名单。
- playground drop 期间吃/玩不触发。
- playground drop 期间 pair move 不触发。
- playground drop 期间 journey / compact follow 不写回猫位置。
- playground drop 期间键盘序列不再触发问号。
- playground drop 期间普通最小化、恢复、dock、return、goodbye、教程插播、窗口 resize/reclamp 等自动接管不覆盖 playground 状态。
- 10 秒问号 timer 不影响已经进入的 playground drop。

生命周期恢复行为：

- 进入 playground drop 时，禁用能力由统一 lifecycle acquire 建立，不由各功能局部散写。
- 退出 playground drop 时，恢复能力由统一 lifecycle release 执行。
- 点击猫 gif 退出后，键盘识别、随机动作资格、hover、edge peek 和普通拖拽入口恢复可用。
- 退出时不补发期间漏掉的键盘输入，也不立即补跑期间跳过的随机动作。
- `cat-click` 退出时先停止 playground 私有物理和监听，再 release 生命周期锁，最后进入模型切换。
- `question-block-click` 退出时先停止 playground 私有物理和监听，恢复 `cat` / `yarn` / `desktop-yarn` 起始位置，再 release 生命周期锁，不进入模型切换。
- 普通 CAT1 能力恢复后，不会在模型切换完成前抢跑随机动作。
- 重复 release、旧 RAF、旧 timer 或旧 pointer 回调不会重新触发模型切换或写回位置。
- 外部兜底退出，例如切出 CAT1 或页面销毁，也走同一个 release 清理路径。

桌面行为：

- 网页端问号点击可进入 playground drop。
- 桌面 overlay 问号点击也可进入 playground drop。
- 桌面 overlay 问号点击后不在桌面端实现问号物理；保留问号方块时，由 Pet 页面基于入口时的 `questionBlockScreenRect` 创建 `question-block` body。
- 桌面最小化聊天框模式下，聊天框作为独立物理体参与，位置同步通过现有 desktop pair move bounds 广播实现。
- React Chat 或 compact ball 在 active 期间 ready/recreate 后仍保持 playground lock。
- React Chat 自最小化毛线球在 playground 中显示为实际视觉球尺寸，不应变成 carrier 大小。

## 15. 建议测试

主项目静态测试与命令：

- 存在 playground drop state / active gate。
- `neko:idle-cat1-playground-entry-request` 有 listener。
- 非毛线球聊天框会先收到 `neko:idle-cat1-playground-yarn-request` 并收缩成 minimized 毛线球，再开始 drop。
- 收缩等待期间存在 pending entry gate，不允许键盘序列、普通拖拽、随机动作或 journey 插队。
- 入口进入时清理原问号触发状态，创建独立 `question-block` body，并设置猫图源为 `cat-idle-cat-move-2.gif`。
- 进入时立即注册猫、毛线球、桌面最小化聊天框对应的物理体。
- 验证 `question-block` 通过 `_createNekoIdleCat1PlaygroundPhysicsBody(...)` 注册到 `state.bodies`，并拥有独立质量配置。
- 验证 `question-block` 通过 body options 启用旋转：`rotationEnabled: true`、`settleRotationWhenGrounded: true`、`restRotationStepRad: Math.PI / 2`，且旋转配置不扩散到 `cat` / `yarn` / `desktop-yarn`。
- 验证 `question-block` 不改变 `cat`、`yarn`、`desktop-yarn` 的质量、尺寸来源、拖拽入口和桌面同步。
- 验证 pending entry 取消、页面销毁或切出 CAT1 时会清理尚未进入 physics lifecycle 的问号克隆，避免残留 DOM。
- 初始投放由 physics tick 驱动，不存在独立 tween 下落实现。
- 初始投放落地后存在切回 CAT1 第一形态默认态的逻辑。
- 进入时通过 `_acquireNekoIdleCat1PlaygroundDropLifecycle` 建立统一状态、禁用能力和 cleanup。
- 退出时通过 `_releaseNekoIdleCat1PlaygroundDropLifecycle` 统一清理状态、恢复能力和取消回调。
- capability 判断采用白名单模型：未知 CAT1 状态切换在 playground drop active 时默认 blocked。
- release 是幂等的，重复调用不会重复派发事件、切模型或恢复随机动作。
- 旧 token 的 RAF、timer、pointer 回调在 release 后不会继续写位置或图源。
- 键盘和随机动作退出后只恢复资格，不回放或补触发生命周期期间的事件。
- playground 独立拖拽状态不调用普通 idle 拖拽结束恢复。
- 松手时根据 pointer sample 计算初速度。
- 松手时支持旋转的 body 根据 pointer sample 和抓取偏移计算 `angularVelocity`，并通过最大角速度限制裁剪。
- 物理更新包含重力、`dt` 上限、水平阻尼、底部 floor clamp 和左右边界反弹。
- 物理更新包含旋转积分、角阻尼、落地 90 度回正和 angular active 判断；线性速度停止但角度仍在回正时 RAF 不能提前停止。
- 旋转逻辑走通用 `rotationEnabled` 判断，physics tick 不应出现 `body.id === 'question-block'` 的旋转特判。
- 桌面最小化聊天框的速度、重力和碰撞由主项目页面层物理体计算；物理层尺寸使用可见 `51px` 球，桌面端回传移动时优先按当前 collapsed carrier 换算为 Electron 原生窗口 bounds。
- `cat-click` 退出顺序先停止 playground 私有物理/监听，再 release，最后进入模型切换。
- `question-block-click` 路径会派发独立事件、恢复 `cat` / `yarn` / `desktop-yarn` 起始位置、release playground，且不派发 return-click。
- 进入时取消 journey、pair move、吃、玩、拖拽抢占。
- 拖拽、吃、玩、pair move、journey 入口都检查 playground drop active。
- 猫点击回模型路径会清理 playground drop 状态。
- `_isNekoIdleCat1PlaygroundEntryOrDropActive(button, capability)` 委托 capability gate，不绕过 `_isNekoIdleCat1PlaygroundCapabilityBlocked(button, capability)`。
- Linux pair move 普通路径可跳过 native bounds sync，但 `force: true` 路径必须保留。

桌面端契约测试：

- overlay 点击仍只派发 `neko:idle-cat1-playground-entry-request` 回 Pet 页面。
- 桌面端不直接实现 playground drop 业务语义。
- 桌面端仍不新增 question-block 专用物理、拖拽、bounds IPC 或 pointer bridge；验证入口 payload 可携带 overlay 点击瞬间 `questionBlockScreenRect`。
- React Chat ready listener 存在，并在 playground active 时补发 lock。
- compact ball ready listener 存在，并在 playground active 时补发 lock，同时继续同步 compact ball minimized/effective state。
- React Chat 自最小化毛线球 screenRect 使用视觉尺寸归一化，避免 carrier 尺寸污染 playground 物理体。

主项目运行验证：

- `node --check static/avatar/avatar-ui-buttons.js`
- `.venv/bin/python -m pytest tests/unit/test_avatar_return_button_idle_tiers_static.py -q`
- `.venv/bin/python -m pytest tests/unit/test_avatar_return_button_idle_tiers_static.py tests/unit/test_react_chat_idle_dock_static.py::test_cat1_desktop_pair_move_skips_linux_runtime_native_bounds_sync -q`

桌面端运行验证：

- `node --check src/main.js && node --check src/window-manager.js && node --check src/preload-chat-react.js && node --check src/preload-compact-chat-ball.js`
- `node --test test/idle-cat-question-mark-layer-contract.test.js test/react-chat-compact-surface-drag-contract.test.js test/desktop-compact-layout-contract.test.js`

通用验证：

- `git diff --check`

## 16. 后续待设计

后续小游戏还未设计，暂不实现：

- 猫掉到底部后的具体待机姿态。
- 小游戏区域、边界、碰撞和控制方式。
- 毛线球/聊天框在小游戏中的角色。
- 游戏结束后是否回到猫形态、模型形态或其他形态。
- 多屏幕底部选择规则。
- 音效、奖励、分数、失败/成功状态。
