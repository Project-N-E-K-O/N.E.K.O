# Avatar 道具交互设计与维护说明

本文是三种 Avatar 道具的当前设计入口。它同时约束网页端、宿主/后端与 NEKO-PC 桌面端；Full Chat 与 Compact Chat 已共用同一套道具运行时、注册表和提交语义，只保留布局与选择入口的呈现差异。

若本文与代码、测试或真实运行结果冲突，以可复现证据和当前代码为准，并在继续扩展前修正文档。

## 当前范围

本链路只包含三种已实装道具：

| 道具 | tool id | action | intensity | 特殊事实 |
|---|---|---|---|---|
| 棒棒糖 | `lollipop` | `offer` / `tease` / `tap_soft` | `normal` / `rapid` / `burst` | 无 `touchZone` |
| 猫爪 | `fist` | `poke` | `normal` / `rapid` | `touchZone`、可选 `rewardDrop` |
| 锤子 | `hammer` | `bonk` | `normal` / `rapid` / `burst` / `easter_egg` | `touchZone`、可选 `easterEgg` |

本次不包含：

1. 猜拳或其它新道具。
2. 为 Full Chat 或 Compact Chat 重新建立独立的道具状态机、声音、效果、命中或提交链路。
3. 背包、购买、消耗、解锁或拖拽投放即触发。
4. 非 Avatar 目标交互。
5. 改变 `avatar_interaction` 作为轻量临时互动的产品语义。

新增道具必须另行确认产品事件和跨端能力，不能落入任一旧道具的 fallback。

## 用户体验与不可破坏规则

用户路径：

1. 在 Full Chat 或 Compact Chat 的 Avatar tools 入口打开道具选择。
2. 从当前界面的选择入口选择一种道具。
3. 道具进入持续跟随状态，直到用户取消、切换、道具被移出快捷栏或页面失去可交互能力。
4. 离开 Avatar 范围时显示范围外小图标；靠近 Avatar 时三种道具都显示大图标。
5. 按下只给本地按压反馈；在同一 session 内有效松开后才提交一次交互。
6. 本地声音和动效立即播放，不等待模型回复或后端 ack。

必须一直成立：

1. 三种道具靠近 Avatar 都放大；棒棒糖不放大是旧实现错误。
2. `pointerdown` 不提交后端。
3. `pointerup` 必须匹配同一 pointer、同一 session、未超过移动阈值，并重新确认当前 Avatar 命中和 UI 排除。
4. `pointercancel`、窗口失焦、页面隐藏、教程接管、输入区禁用、切换或销毁都只取消，不提交。
5. 同一次 press 最多产生一个 commit。
6. 本地表现、Host payload、后端 prompt 和 memory 从同一个 commit 事实派生。
7. UI、聊天窗口和其它桌面管理窗口不能误触发道具，也不能被透明 overlay 抢走点击。
8. 猫爪/锤子的 `touchZone` 必须保持真实的 `ear` / `head` / `face` / `body`，不能统一写成头部。
9. Avatar Tool 链路始终显示系统光标；道具图片只跟随在系统光标旁边，不隐藏、不替换系统光标。

## 职责与事实源

### NEKO React

| 位置 | 职责 |
|---|---|
| `frontend/react-neko-chat/src/App.tsx`、`FullChatSurface.tsx` | Full/Compact 的界面适配、选择状态接线、调用共享 runtime 和渲染共享视觉层；只保留布局与入口差异。 |
| `frontend/react-neko-chat/src/avatarTools.ts` | 从共享定义投影 UI 目录、资源路径、热点、默认槽位和持久化。 |
| `frontend/react-neko-chat/src/avatar-tools/catalog.ts`、`tools/*` | 唯一道具注册表；三个道具各自声明 definition 与 handlers，统一登记资源、视觉、声音、效果和 interaction profile。 |
| `frontend/react-neko-chat/src/avatar-tools/interactionContract.ts`、`message-schema.ts` | 从注册表派生 interaction payload 类型与运行时校验；`message-schema.ts` 只重导出该 interaction schema。 |
| `frontend/react-neko-chat/src/avatar-tools/useAvatarToolRuntime.ts`、`interactionEngine.ts` | 唯一活动 session、pointer 周期、范围状态、命令/commit 分发、Host 发布和统一销毁。 |
| `frontend/react-neko-chat/src/avatar-tools/hitTesting.ts`、`interactionPolicy.ts` | 页面 bounds/UI exclusion 适配，以及网页与桌面共享的范围、hold、press/release 和缓存策略。 |
| `frontend/react-neko-chat/src/avatar-tools/feedback.ts`、`visualState.ts`、`AvatarToolVisuals.tsx` | disposer、sound/effect execution、视觉状态派生和稳定展示层；不重新定义命中或提交规则。 |
| `frontend/react-neko-chat/src/avatar-tools/payload.ts`、`desktopContract.ts`、`desktopContractSchema.ts` | 生成 interaction/state payload，并把同一注册定义与 runtime policy 投影、校验为 PC 可消费契约。 |

`catalog.ts` 是 React 道具注册的单一事实源；`avatarTools.ts`、interaction schema、runtime、视觉层和桌面 contract 都消费它。`App.tsx`、`FullChatSurface.tsx`、quickbar 和 manager 不维护道具 timer、burst、press、声音或 tool-id 业务分支。

Runtime 依赖可替换 provider：

1. bounds provider。
2. UI exclusion provider。
3. clock / monotonic clock。
4. random source。
5. Host state/interaction callbacks。

默认 provider 可以读取当前页面的 Live2D、VRM、MMD 或桌面注入 bounds；道具规则模块不能直接读取模型 manager、DOM、Host 或 IPC。

### Host 与 Python 后端

| 位置 | 职责 |
|---|---|
| `static/app/app-react-chat-window/*` | 接收 React 的 interaction/state callback 并派发宿主事件；状态、消息处理和公开 API 按上游 parts 分层维护。 |
| `static/app/app-buttons.js` | wire payload 归一、Host 冷却、发送、文本输入延后和 ack/turn 生命周期。 |
| `static/app/app-websocket.js` | 把 `avatar_interaction_ack` 转成宿主生命周期事件。 |
| `main_routers/websocket_router.py` | 把 `avatar_interaction` 转给当前 session manager。 |
| `main_logic/core/greeting.py` | 去重、后端冷却、会话守卫、临时 prompt、turn meta 和最终 ack。 |
| `config/prompts/avatar_interaction_contract.py` | Python 侧唯一 tool/action/intensity/special-field 契约和 wire normalizer。 |
| `config/prompts/prompts_avatar_interaction.py` | 直接事件事实 prompt、reaction profile、touch zone 事实、memory 和 text-context sanitizer。 |
| `main_logic/cross_server.py` | avatar interaction memory 隔离、去重与持久化。 |

Host 与 Python 都接受顶层 snake_case / camelCase 输入。Host 的 websocket wire 顶层字段使用 snake_case，但嵌套 `pointer` 仍使用 `{clientX, clientY}`；Python 同时接受嵌套 camelCase / snake_case，并归一为 `{client_x, client_y}`。两端必须用完整行为 parity 测试约束，不能只比较允许值表。

Python 调用方直接使用 `normalize_avatar_interaction_payload`，并显式注入 `_sanitize_avatar_interaction_text_context`；不保留第二个 normalizer facade。Host 不维护 tool/action/intensity 到模型 emotion 的 seed 表，也不直接调用模型 emotion API；即时反馈属于 React/PC 的道具视觉、声音和效果，模型情绪与动作继续由既有 assistant 响应链路决定。

### NEKO-PC

桌面端分为三层：

| 层 | 位置 | 职责 |
|---|---|---|
| Chat descriptor publisher | `src/preload/bridges/chat-avatar-tool-bridge.js`、`chat-compact-window-surface-bridge.js`、`chat-full-window-surface-bridge.js` | 只由当前可见且选中的 surface 发布 descriptor；不转发 pointer，不渲染桌面道具跟随视觉。 |
| Main pointer/visual coordinator | `src/main.js`、`src/avatar-tool-visual-overlay-service.js`、`src/main/avatar-tool-visual-ownership.js`、`src/main/cursor-display-ipc.js` | 读取全局坐标、识别窗口上下文、管理视觉所有权、桌面 overlay 和 move 通道；不隐藏或替换系统光标。 |
| Pet interaction adapter | `src/preload/bridges/pet-input-region-bridge.js`、`src/preload/bridges/pet-avatar-tool-adapter.js` | Bridge 提供模型 bounds、平台坐标和输入穿透能力；adapter 接入真实 down/up/cancel，执行桌面视觉、声音、效果并提交一次 interaction。 |

`src/desktop-avatar-tools/*` 保存可测试的 domain helper 和薄边界服务：

1. `contract-validator.js`、`contract.js`：严格校验、decode、能力协商、规范化和 fingerprint。
2. `pointer-range.js`：bounds、range、touch zone、pointer context 和视觉 hold。
3. `interaction-engine.js`：active tool、press snapshot、移动阈值、release guard、tool profile 执行和 commit。
4. `effects.js`、`interaction-output.js`：纯 effect execution、payload 构造、Host dispatch 与 sound/effect 输出顺序。
5. `runtime.js`：唯一 session、range、outside feedback、effect lock/timeline 和 generation 所有者。
6. `visual-state.js`：从 selection/range/effect 派生可差量桌面道具视觉状态。
7. `surface-lifecycle.js`：Full/Compact/Pet 的 ownership、handoff、reload replay 和 renderer 守卫。

PC 不再复制 `tools/*` 或 tool-id 业务表；每个道具的独立定义来自 NEKO 投影的桌面 contract，PC domain 只执行声明式 profile。`pet-avatar-tool-adapter.js` 可以操作 DOM、Audio、窗口和 IPC，并使用 preload scope 做资源清理，但不能创建第二套 session/range/effect 生命周期。

桌面多窗口模式下，Full/Compact 都从同一 NEKO runtime 发布真实选择得到的 descriptor，但只有当前可见且选中的 surface 拥有发布权。切换 surface 时 ownership 必须原子交接，隐藏或失活 surface 的迟到状态必须被拒绝；Pet reload/ready 后重放当前 owner 的最新 descriptor。隐藏窗口即使残留旧 shape 元数据，也不能参与 Host 命中。

Avatar Tool 链路始终不调用 `system-cursor-visibility-service.js`；该服务只属于新手教程 Ghost Cursor 生命周期。Niri 的全屏道具视觉 overlay 只负责视觉跟随并始终保持 passthrough；真实 down/up/cancel 只能来自 Pet 的精确输入区域。不得用 overlay 接管桌面其它应用的点击来模拟全局交互。

`wireVersion`、`definitionVersion`、`policyVersion` 的值 `1`，以及 `*-v1` interaction/effect `kind`，是已序列化的协议判别值，必须由生产者和消费者共同校验。它们不用于公开文件、模块、factory 或 API 命名；当前实现不保留名为 `V1`、`contract-v1` 的兼容层。

## Runtime 生命周期

```text
inactive
  -> active.outside
  -> active.in_range
  -> active.pressing
  -> active.committing
  -> active.settling
  -> active.outside / active.in_range / inactive
```

### 创建与切换

1. 切换前先 destroy 旧 session。
2. 新 session 获得新的 generation。
3. 初始化当前道具变体、burst history 和 disposer。
4. 发布 active state；桌面多窗口只发布 descriptor，不发布 Chat pointer。
5. 旧 timer、audio、effect 或 Promise 回调必须通过 generation/disposer 失效。

### 高频移动

1. 原始坐标保存在 ref/session，不直接进入 React render。
2. 网页端用一个 RAF 合并 move；每帧最多计算一次 bounds/UI exclusion 并发布一次有效 state diff。
3. 道具跟随视觉节点保持稳定，只更新 `transform`；不得在 move 中创建 DOM 或 Audio。
4. PC main 只轮询 move；不得合成 down/up/cancel 或提交 interaction。
5. overlay/poller 只在支持桌面视觉的 active tool 下运行。

### Press 与 release

`pointerdown` 保存：

1. session generation。
2. tool id 与 pointer id。
3. 起点和移动阈值状态。
4. 当前本地按压反馈所需状态。

`pointerup` 重新读取当前 bounds 和 UI exclusion。只有当前命中有效时才由道具模块返回 commit。release 的 `touchZone` 取当前可靠命中，不以 press 旧 bounds 兜底。

### 销毁

统一销毁必须：

1. 先让 generation 失效。
2. 清 press、timer、RAF、audio、effect 和 tool lock。
3. 确认系统光标始终可见，并恢复 passthrough。
4. 停止 PC overlay/poller。
5. 发布 inactive state。
6. 幂等；重复销毁不抛错、不重复提交。

## 命中与范围稳定性

Avatar bounds 的默认优先来源：

1. 桌面注入 bounds。
2. MMD manager。
3. VRM manager。
4. Live2D manager。

页面命中使用矩形快速过滤和中心椭圆，并使用进入/离开不同 padding 与短 hold，减少模型轻微变化或边缘移动造成的范围外/范围内视觉抖动。

UI exclusion 至少覆盖：

1. composer、工具轮盘、quickbar、manager、发送按钮和消息操作按钮。
2. 窗口拖拽/缩放层。
3. 教程 interaction shield。
4. Live2D / VRM / MMD 浮动按钮、返回、锁定和弹窗。
5. sidepanel、历史区和桌面浮动交互控件。

桌面全局上下文必须区分：

1. `overPetWindow`：指针在 Pet 自身；这是 Pet 交互候选，不是 Host exclusion。
2. `overChatWindow`：指针在聊天窗口；必须排除并取消 press。
3. `insideHostWindow`：指针在字幕、字幕设置、Agent HUD、jukebox、toast 等其它管理窗口；必须排除并取消 press。

禁止把 `overPetWindow` 合并进 `insideHostWindow`，否则 Pet 原生 move 与 main 轮询会交替写道具视觉，并在按下期间取消 session。

## 三种道具规则

### 棒棒糖

1. 第一阶段 `offer`，第二阶段 `tease`，后续 `tap_soft`。
2. `tap_soft` 可按连续点击升级为 `rapid/burst`。
3. 不带 `touchZone`、`rewardDrop` 或 `easterEgg`。
4. commit 后播放咬食音效并生成爱心。
5. 靠近 Avatar 使用大图标。

### 猫爪

1. `pointerdown` 只切按下变体；有效 release 才提交 `poke`。
2. 连续点击只使用 `normal/rapid`；不要从旧草稿误加 `burst`。
3. commit 使用 release 时的真实 `touchZone`。
4. `rewardDrop` 只属于猫爪；命中后可播放金币音和掉落效果。
5. 靠近 Avatar 使用大图标。

### 锤子

1. Avatar 外按下只允许短暂本地反馈，不提交、不累计 burst。
2. 有效 release 才提交 `bonk`。
3. commit 后执行 `windup -> swing -> impact -> recover -> idle`。
4. 动画锁只属于当前 hammer session；动画未结束时不接受重叠提交。
5. `easterEgg` 只属于锤子，并与 `intensity=easter_egg` 保持同一事实。
6. 挥动期间保持大形态或显式隐藏基础道具视觉，不能在移出 Avatar 时重新出现小锤。

## Host/后端回应周期

本地动效不等待后端。普通文本输入只在角色回应周期内延后。

当前真实顺序：

```text
interaction sent
  -> awaiting_result
  -> matching assistant turn active
  -> matching turn ended / awaiting_final_ack
  -> delivered/rejected ack or grace timeout
  -> release deferred text
```

规则：

1. 不使用旧的 8 秒 ack timeout 提前释放；模型回复可能更慢。
2. 长时间完全没有 turn/ack 时才使用总结果 timeout 兜底。
3. assistant turn 事件只按后端既有 `meta.kind=avatar_interaction` 与匹配的
   `meta.interaction_id` 关联；无标记、类型不同或 interaction 不同的 turn
   都不得推进或结束本次延后。
4. turn 结束后等待最终 ack；ack 丢失时用短 grace 收尾。
5. late ack、reject、duplicate、busy、cooldown 和 error 都必须幂等收尾。
6. ack 不回滚已经播放的本地点击效果。

## 扩展与维护边界

新增或修改道具前先写清：

1. 选择后的范围外小图标和范围内大图标。
2. pointerdown 本地反馈、有效 release 条件和 outside 行为。
3. action/intensity、touch zone、概率字段与 effect/sound。
4. 网页与桌面 capability；未适配桌面时必须明确 `desktopVisual=false`，不能 fallback。
5. Host/Python prompt、memory 和 ack 语义。

同步入口：

1. React catalog/interaction contract、runtime tool module 和测试。
2. `static/app/app-buttons.js` Host contract。
3. `config/prompts/avatar_interaction_contract.py` Python contract。
4. `config/prompts/prompts_avatar_interaction.py` prompt/memory。
5. PC desktop capability、纯 tool rule 和测试。
6. 8 locale 用户可见文案与静态资源预加载。

不要：

1. 在 `App.tsx` 或 Pet preload 重新加大段 tool-id 分支。
2. 复制新的 timer/audio/effect lifecycle。
3. 让 quickbar/manager 承担道具业务。
4. 让 Chat preload 回灌 pointer 或桌面道具视觉状态。
5. 让 main poller 合成点击。
6. 用 press 旧命中代替 release 当前命中。

## 验证清单

网页端至少覆盖：

1. 三种道具选择、取消、切换和槽位移除。
2. 所有道具范围内大图标、范围外小图标稳定，边缘不抖动；系统光标始终可见。
3. down 不提交；有效 up 单次提交。
4. drag-out、移动超阈值、release 到 UI、cancel、blur、教程 shield 均不提交。
5. release 重新读取 bounds/touch zone。
6. timer、audio、effect 和旧 generation 不残留。
7. RAF 合并和 state diff 不重复上报。

Host/后端至少覆盖：

1. Host/Python contract 与 normalizer 行为 parity。
2. 三种道具的 action/intensity/special fields。
3. 8 locale prompt/memory 和四种 touch zone。
4. ack/reject、慢回复、匹配/不匹配 turn、late ack 和 timeout。

桌面端至少覆盖：

1. 同一 Pet 点经 Pet 原生 pointer 与 main poll 后 imageKind/尺寸稳定。
2. main poll 不取消 Pet press；down -> poll -> up 只 commit 一次。
3. Chat 和其它管理窗口仍正确排除。
4. Niri 初始坐标、overlay 持续视觉跟随与全程 passthrough；Pet 精确区域完成真实 down/up/cancel，第三方窗口点击不被抢走。
5. Compact 与 Full 双向切换时原子交接 descriptor ownership，选择持续、旧 surface 迟到状态被拒绝，并且始终只有一个发布者；隐藏窗口的旧 shape 不影响命中。
6. Pet reload/crash 后 overlay、道具视觉和 selection 能恢复，系统光标仍保持可见。
7. hammer 动画移出 Avatar 时不出现第二把小锤。
8. macOS / Windows / Wayland 的 passthrough、截图暂停、多屏变化不被破坏。

修改后运行与范围匹配的 typecheck、Vitest、Node contract、Python 单测、PC unit/contract，并以隔离数据目录启动真实桌面端复测。截图只能辅助观察，不能替代连续移动、按下、松开、取消和跨窗口的真实输入验证。
