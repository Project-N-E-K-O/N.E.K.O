# Avatar 道具交互设计与维护规范

本文是当前 Avatar 道具交互的长期维护入口，覆盖 NEKO 网页聊天、Host/Python 事件链和 NEKO-PC 桌面链路。它只描述已经注册并实际运行的能力，以及新增道具必须遵守的结构和验证规则。

若文档与代码、测试或真实运行结果冲突，以可复现证据和当前代码为准，并先修正文档。未注册的方案、资源或实验代码不属于当前能力，不能据此增加兼容分支或 fallback。

## 当前已注册道具

`AVATAR_TOOL_DEFINITION_IDS` 和 `AVATAR_TOOL_REGISTRY` 当前包含以下四种道具：

| 道具 | tool id | interaction profile | effect recipe | 合法 action/intensity |
|---|---|---|---|---|
| 棒棒糖 | `lollipop` | `progressive-release` | `fixed-particles` | `offer/normal`、`tease/normal`、`tap_soft/rapid`、`tap_soft/burst` |
| 猫爪 | `fist` | `press-release` | `random-scatter` | `poke/normal`、`poke/rapid` |
| 锤子 | `hammer` | `locked-impact` | `hammer-swing` | `bonk/normal`、`bonk/rapid`、`bonk/burst`、`bonk/easter_egg` |
| 猜拳 | `rps` | `round-choice` | 无 | 第二步仍为本地确认，不生成 Host action/intensity |

当前特殊事实：

- 棒棒糖的注册 profile 不声明 `touchZone`、`rewardDrop` 或 `easterEgg`，规范 producer 不得生成这些字段。
- 猫爪必须携带 `touchZone`，并可携带 `rewardDrop`。
- 锤子必须携带 `touchZone`，并可携带 `easterEgg`；`easterEgg=true` 必须与 `intensity=easter_egg` 同时成立。
- 猜拳当前第二步在合法松开后冻结用户手势、等概率确定当前猫娘手势并播放声明的确认声；双方手势只确定一次，不生成 Host/Python interaction payload。
- 猜拳准备手势只由真实有效范围驱动；按下期间当前猫娘仍继续出拳。普通离开范围不截断已确认画面，失焦、页面隐藏、surface/道具切换和销毁会立即清理。
- 猜拳只进入当前 Compact 道具入口；Full 继续保留原来的三个道具，不为猜拳适配或增加维护范围。
- `touchZone` 只允许 `ear`、`head`、`face`、`body`。

## 设计原则

1. **注册表驱动**：道具视觉、声音、效果、交互 profile 和桌面能力从 `catalog.ts` 的 definition 投影；页面和 PC 不再维护独立的 tool-id 规则表。
2. **声明与执行分离**：definition 只声明数据；Web profile interpreter 和 PC runtime 解释声明。新增道具不能在页面或 preload 复制一套 handler。
3. **一次输入链、至多一次输出**：按下只冻结本地状态；同一 session 内通过校验的松开最多生成一次 profile 声明的输出。输出可以是本地确认，也可以是 Host commit；轮询、视觉 overlay 和 Host 边界事件都不能合成第二次输出。
4. **commit 是事件事实**：需要 Host 回应的道具，其本地表现、Host payload、Python prompt 和 memory 从同一个 commit 派生，不得在下游重新猜测 action、intensity、位置或特殊结果。
5. **声明字段严格验证**：缺失或非法的 action/intensity、必需位置和已声明特殊结果直接拒绝，不以默认 intensity、默认位置、未知 tool fallback 或宽松布尔值继续执行。producer 不生成未声明字段，下游也不把额外字段解释成事件事实。
6. **跨端语义一致、适配层独立**：Web 和 PC 共用 profile/effect 语义，但各自负责 DOM、窗口、坐标和平台输入适配；不能为了代码表面一致而复制平台兼容逻辑。
7. **表现不拥有输入**：道具图片和效果只展示状态；系统光标始终可见，透明 overlay 始终 passthrough。

## 事实源与数据流

### 两条数据流

道具选择与真实交互是两条职责不同的流：

```text
选择/定义流
NEKO catalog
  -> Web registration/runtime
  -> desktopContract descriptor
  -> NEKO-PC surface owner
  -> Pet desktop runtime

交互事实流
Web pointer 或 Pet pointer
  -> profile interpreter/runtime
  -> 单次 profile 输出
     -> 本地确认与表现
     -> 需要回应时：Host normalizer -> Python normalizer -> prompt / memory / ack lifecycle
```

Chat descriptor 只传当前选择和桌面契约，不传 Avatar pointer。桌面真实 `down/up/cancel` 只由 Pet 精确输入区域提供；main 的全局采样和 Chat host-boundary 事件只服务视觉、范围采样、所有权交接或取消。

### NEKO React

| 模块 | 唯一职责 |
|---|---|
| `frontend/react-neko-chat/src/avatar-tools/catalog.ts` | 已注册 ID、definition、视觉资源、声音、effect recipe、interaction profile、capability 和 definition 校验。 |
| `frontend/react-neko-chat/src/avatar-tools/profileInterpreter.ts` | 解释当前支持的 profile kind，生成通用 handlers；不按 tool id 分支。 |
| `frontend/react-neko-chat/src/avatar-tools/interaction.ts` | bounds、范围、UI exclusion、touch zone、press/release guard 和共享 runtime policy。 |
| `frontend/react-neko-chat/src/avatar-tools/protocol.ts` | interaction/state payload schema、类型和构建器。 |
| `frontend/react-neko-chat/src/avatar-tools/desktopContract.ts` | 把已注册 definition 严格投影为 PC descriptor contract。 |
| `frontend/react-neko-chat/src/avatar-tools/runtime.ts` | 当前页面唯一活动 session、pointer 生命周期、命令/commit 分发和销毁。 |
| `frontend/react-neko-chat/src/avatar-tools/presentation.tsx` | 稳定渲染道具、声音和 effect，并统一清理副作用。 |
| `frontend/react-neko-chat/src/avatarTools.ts` | 从注册表投影菜单、快捷栏、资源路径和持久化信息。 |
| `App.tsx`、`FullChatSurface.tsx` | Full/Compact 页面接线和布局适配；不承载道具规则。 |

`message-schema.ts` 消费并重导出道具 schema，用它校验窗口 callback 参数；不得再复制协议定义。quickbar、manager 和页面组件只消费注册结果，不维护 timer、burst、声音、effect 或 tool-id 业务分支。

### Host 与 Python

| 模块 | 唯一职责 |
|---|---|
| `static/app/app-react-chat-window/*` | 接收 React callback 并派发 Host 事件。 |
| `static/app/app-buttons.js` | Host wire normalizer、冷却、发送、文本延后和 ack/turn 生命周期。 |
| `static/app/app-websocket.js` | 接收并派发 `avatar_interaction_ack`。 |
| `main_routers/websocket_router.py` | 把事件路由到当前 session manager。 |
| `main_logic/core/greeting.py` | 去重、后端冷却、会话守卫、临时 prompt、turn meta 和最终 ack。 |
| `config/prompts/avatar_interaction_contract.py` | Python 唯一公开 payload normalizer 和 tool/action/intensity/special-field 契约。 |
| `config/prompts/prompts_avatar_interaction.py` | 事件事实、位置事实、memory 和 text-context sanitizer。 |
| `main_logic/cross_server.py` | interaction memory 的隔离、去重和持久化。 |

Host 与 Python 因跨语言边界各自保留契约实现，但必须由 parity 测试约束完整行为，不能只比较允许值列表。Python 调用方统一使用 `normalize_avatar_interaction_payload`；不得恢复私有 normalizer、facade alias 或第二套宽松归一入口。

提示词的事实写法、多语言要求和样例验证由 `docs/design/avatar-tool-prompt-guidelines.md` 维护；本文不重复定义角色口吻或事件文案。

### NEKO-PC

桌面外壳分为四个职责面：

| 职责面 | 主要位置 | 边界 |
|---|---|---|
| Chat descriptor publisher | `src/preload/bridges/chat-avatar-tool-bridge.js`、Full/Compact surface bridges | 只发布当前 owner 的 descriptor，不传 pointer、不执行 profile。 |
| Chat host-boundary bridge | `src/preload/bridges/chat-avatar-tool-host-boundary-bridge.js` | 只在需要的平台报告 Chat 物理边界和 pointer 生命周期，用于交接/取消。 |
| Main coordinator | `src/main.js`、visual ownership/overlay/handoff、`src/main/window-host-ipc.js` | 管理全局采样、视觉 owner、overlay、平台 handoff 和原生输入写入，不解释 profile。 |
| Pet adapter | `src/preload/bridges/pet-input-region-bridge.js`、`pet-avatar-tool-adapter.js` | 连接真实 Pet pointer、模型 bounds、桌面 runtime、Audio/DOM 和 interaction IPC。 |

`src/desktop-avatar-tools/*` 是无窗口依赖的桌面领域层：

- `contract.js`：validate/decode、能力协商、规范化和 fingerprint。
- `runtime.js`：range、touch zone、press/release、profile、generation、burst 和 effect lock。
- `interaction-output.js`：视觉状态、effect plan、sound/effect 顺序和 interaction payload。
- `surface-lifecycle.js`：descriptor ownership、handoff、reload replay 和 renderer guard。

NEKO producer 只允许已注册 tool id；PC consumer 对 tool id 使用通用小写 identifier，并按支持的 profile/effect kind 解码。这是有意的边界：复用现有 kind 的新道具不需要 PC tool-id 分支；只有协议版本、profile kind、effect kind 或 capability 语义变化时才扩展 PC consumer。

## 契约规则

### Interaction payload

需要提交 Host 的 profile runtime commit 必须包含：

- `toolId`
- `actionId`
- 该 action 明确允许的 `intensity`
- 有效的 `clientX` / `clientY`
- 猫爪和锤子的当前 `touchZone`

payload builder 再补充 `interactionId`、`target=avatar`、`timestamp`，并把坐标包装为 `pointer`。Host/Python 的 wire normalizer 为兼容历史调用允许 pointer 缺失或无效并归一为无坐标；因此坐标是当前 producer 的必需输出，不是后端事件事实的拒绝条件。

归一规则：

1. Host 可接收顶层 camelCase 或 snake_case，并发送 snake_case websocket 字段；嵌套 pointer 保持 `{clientX, clientY}`。Python 归一为 snake_case。
2. 当前 tool 声明的 `rewardDrop` / `easterEgg` 缺失时为 `false`；该字段存在时只接受两端共同支持的显式布尔表示：布尔值、数字 `0/1`、字符串 `"true"/"false"/"1"/"0"`。
3. 已声明布尔字段存在但无法解析时拒绝整个 payload，不静默降级。无关额外字段不构成事件事实；规范 producer 会由 strict schema 提前拒绝它们。
4. 不支持 touch zone 的道具只要携带该字段，即使值为 `null`，也应拒绝。
5. `text_context` 只用于历史 payload 兼容和诊断预览；normalizer 会清洗它，但当前事件事实 instruction 不发送或消费它。

### Definition 与 descriptor

Definition 和桌面 producer 必须同时保证：

1. tool id 来自注册集合；action、sound/effect resource id 使用合法的小写 wire identifier。chance field 使用合法 camelCase payload field，并且不能占用 canonical/reserved payload 字段。
2. sound/effect id 唯一，profile 的所有引用都能找到资源。当前三个已注册 legacy profile 仍允许 source definition 声明未引用的额外资源，桌面 projection 会过滤它们；`round-choice` 等要求唯一资源闭包的新 profile 必须在 catalog validator 直接拒绝多余声明。desktop contract schema 对所有 profile 都拒绝 descriptor 中的未引用资源。新增 definition 应通过直接校验/测试保证声明闭合，不能把非法或冗余资源交给 PC 猜测处理。
3. 概率、尺寸、时长、粒子数、glyph、路径长度和整数阈值满足 schema 上限；整数使用 safe integer。
4. `clientX`、`clientY` 等 canonical payload 字段不能被 chance field 占用。
5. `desktopInteraction=true` 必须以 `desktopVisual=true` 为前提；关闭的 capability 在 descriptor 中投影为 `null`，不能伪装成受支持。
6. `wireVersion`、`definitionVersion`、`policyVersion` 是结构版本；profile/effect kind 是不带版本后缀的语义判别值。生产者和消费者必须共同校验；只有对应公共结构发生不兼容变化时才提升数值版本。

校验应在 producer 构建 descriptor 时失败，不能把非法 definition 交给 PC 猜测修复。

资源按 canonical tool id 归档：图片位于 `static/assets/avatar-tools/<tool-id>/`，声音位于 `static/sounds/avatar-tools/<tool-id>/`。图片文件使用 `*-icon.png` / `*-pointer.png`，不能再混用 `sugar`、`claw`、`chat_*`、`cat_*` 或 `*_cursor.png` 等旧术语；系统 cursor 始终是独立输入表现，不属于道具图片。

## Runtime 与输入规则

### 选择、重放和销毁

Web 切换道具时销毁旧 session，再创建新的 generation、variant、burst history 和 disposer。PC descriptor 可能因 surface、reload 或 metadata 重放，因此按以下语义处理：

| 变化 | PC runtime 行为 |
|---|---|
| 只变 surface lease、desktop generation 或 timestamp | metadata update；不重建 interaction generation。 |
| 同 tool/contract，只变 descriptor 中的 range/outside variant | metadata update；descriptor variant 只作新 activation 的初始种子，replay 不覆盖 PC runtime 独占的实时 variant，并保留 interaction generation、burst history、press、timer 和 active effect。 |
| tool id 或 contract fingerprint 变化 | 新 interaction generation；清理旧 press、history、variant、timer 和 effect owner。 |
| inactive | 清空选择、pointer、timer、effect 和范围状态。 |

活动选择和 replay 必须同步启动 pointer tracking，不能等下一次 pointer move 才补建命中链。clock、scheduler 和 random source 等可注入依赖必须传到实际 range/interaction engine，使测试和重放结果可确定。

统一销毁必须幂等，并清理 pointer、RAF、timer、Audio、effect、interaction lock、overlay/poller 和迟到的异步回调。音频正常 `ended` 只 release；`error` 或 `play()` reject 必须 stop 并完整清理 session。

### 命中、范围和 UI exclusion

1. `pointerdown` 只保存 generation、tool/pointer id、起点和本地反馈，不提交。
2. `pointerup` 必须匹配同一 pointer 和 generation、未超过移动阈值，并重新读取当前 bounds、UI exclusion 和 touch zone。
3. `pointercancel`、blur、页面隐藏、教程接管、输入禁用、切换和销毁只取消，不提交。
4. 范围视觉可以使用进入/离开不同 padding 和短 hold 抑制抖动，但 visual hold 不授予 interaction hit。
5. release 不使用 press 时的旧 bounds，也不使用 last-known bounds。
6. 普通模型 hit test 不能在 bounds 暂缺时变成第二条道具命中权威。

NEKO 网页 canonical bounds 只接受已经是 finite number 的 `left/top/width/height`，并自行重算 `right/bottom/centerX/centerY`。PC adapter 应向 desktop runtime 提供规范数值 bounds；desktop runtime 内部保留的 compiled/legacy bounds 兼容只属于桌面输入适配，不应复制到 definition、协议或新的命中 fallback。

UI exclusion 至少覆盖 composer、工具菜单/快捷栏/manager、消息操作、拖拽缩放层、教程 shield、模型浮动控件、弹窗和其它桌面管理窗口。`overPetWindow` 是 Pet 交互候选，不能与 `insideHostWindow` 合并；Chat 和其它 Host 窗口必须排除并取消进行中的 press。

### 桌面视觉与平台输入

1. Full/Compact 只有当前可见且选中的 surface 可以发布 descriptor；切换时原子交接 owner，拒绝旧 surface 的迟到状态。
2. Pet ready/reload 后重放当前 owner 的最新 descriptor；隐藏或失活窗口的旧 shape 不参与命中。
3. main 普通全局 poll 只提供 move/sample，不合成 `down/up/cancel`。Chat host-boundary pointer 只服务平台交接/取消，不是 Avatar 命中链。
4. overlay 只展示道具视觉，始终 passthrough；Avatar Tool 不调用系统光标隐藏服务。
5. 原生 `setIgnoreMouseEvents` 的业务入口集中在 `src/main/window-host-ipc.js`，Pet lifecycle 和 window manager 使用注入入口，不直接写 `BrowserWindow`。平台 shape/crop 修复所需的 raw Electron 写入只能留在该权威模块内部，并维护 tracked state；异步重放受 revision 保护。
6. 平台 helper 不可用、发送失败或超时时必须有界释放 owner/lease，不能永久保留旧视觉或穿透状态。
7. 窗口隐藏、恢复、维护和 surface 切换必须成对 suspend/reactivate，保证窗口可见性、surface lease 和视觉 owner 一致。

## 已有道具行为

### 棒棒糖

1. range variant 依次驱动 `offer`、`tease`、后续 `tap_soft`。
2. `tap_soft` 根据连续提交升级为 `rapid` 或 `burst`。
3. 有效提交播放咬食音效；指定阶段生成固定爱心粒子。
4. 不读取位置或特殊布尔结果。

### 猫爪

1. 按下只切换本地按压变体，有效松开才提交 `poke`。
2. 连续提交只有 `normal` 和 `rapid`，没有 `burst`。
3. commit 使用松开时的真实 `touchZone`。
4. `rewardDrop` 命中时播放奖励声音和随机散落效果，不覆盖原 intensity 事实。

### 锤子

1. Avatar 外按下只产生短暂本地反馈，不提交、不累计 burst。
2. 有效松开提交 `bonk`，并执行 `windup -> swing -> impact -> recover -> idle`。
3. effect lifetime 内锁定重叠交互；锁只属于当前 session。
4. 彩蛋结果同时设置 `intensity=easter_egg` 和 `easterEgg=true`。
5. 挥动期间保持 effect visual ownership，不能因指针移出而出现第二把基础小锤。

### 猜拳（当前第二步）

1. 选择后在石头、剪刀、布之间轮播；进入 Avatar 有效范围只改变下一次切换间隔，不重置或跳过当前手势。
2. 三种手势的 icon/pointer 准备完成后才启动轮播；准备期间不接受猜拳点击。
3. 只有真实进入有效范围才显示当前猫娘快速循环的头顶准备手势；视觉停留不授权命中。按下只冻结用户手势，当前猫娘继续循环。
4. 合法松开只随机一次，保存本局用户手势、当前猫娘手势及双方实际图片变体，生成一次本地确认和确认声；取消链不确认。
5. 双方最终手势按当前 definition/descriptor 声明停留 `1600ms`。普通离开范围不会提前截断，失焦、页面隐藏、教程接管、surface/道具切换和销毁统一清理。
6. 头顶位置复用现有 reaction bubble 的头部几何，优先取头部矩形顶部，缺失时退回模型 bounds 顶部，再向上保留 `24px` 间距并限制在可见区域；不新增头部识别。
7. Web 使用现有 body portal 中的稳定展示节点；桌面端使用现有全屏 visual overlay 的第二个稳定图片节点。两者都不接收输入，也不重建视觉组件。
8. 当前尚未实现双方碰撞、胜负结果、Host/Python 互动事实和临时对话。

所有道具在 Avatar 范围内显示大形态、范围外显示小形态；这是共享 presentation 语义，不应在单个 tool handler 中重复实现。

## Host 回应生命周期

需要 Host 回应时，本地声音和效果在 commit 后立即执行，不等待模型回复或 ack。普通文本输入只在匹配的 Avatar interaction 回应周期内延后。仅有本地确认的 profile 不进入本节生命周期。

```text
interaction sent
  -> awaiting_result
  -> matching assistant turn active
  -> matching turn ended / awaiting_final_ack
  -> delivered/rejected ack 或 grace/total timeout
  -> release deferred text
```

只有 `meta.kind=avatar_interaction` 且 `meta.interaction_id` 匹配的 assistant turn 可以推进该周期。late ack、reject、duplicate、busy、cooldown、error 和 timeout 必须幂等收尾；ack 不回滚已经执行的本地表现。

## 新增道具标准流程

### 1. 先定义产品事件矩阵

在写代码前明确：

- tool id 和用户可见名称。
- range 外/内/按下/effect 期间的视觉。
- action，以及每个 action 允许的 intensity。
- 是否需要 release 时的 touch zone。
- 是否存在概率结果；结果字段、声音和效果是什么。
- Web 和 PC 的 visual/interaction capability。
- prompt/memory 中只应出现哪些客观事实。

没有明确事件事实的字段不能进入 payload；没有明确执行语义的状态不能加入 profile。

### 2. 判断是“新增 definition”还是“扩展协议”

| 需求 | 修改边界 |
|---|---|
| 复用现有 profile 和 effect kind | 新增并注册 definition；同步资源、i18n 和测试。只有会产生 Host commit 时才同步 Host/Python 契约与 prompt。PC 不新增 tool-id 分支。 |
| 需要新的交互状态机 | 新增明确的 profile kind；同步 catalog 类型/校验、Web interpreter、desktop projection、PC contract/runtime 和跨仓测试。 |
| 需要新的效果执行语义 | 新增明确的 effect kind；同步 Web presentation、desktop schema/output/adapter 和清理测试。 |
| 暂不支持桌面 | 显式设置 capability；`desktopInteraction` 不得在 `desktopVisual` 关闭时开启。PC 不做相似道具 fallback。 |

不要用 custom handler、页面 if/switch、Pet tool-id 分支或未知字段兜底来规避新增 kind。真正的新语义必须成为可校验的判别联合，并由两端解释器显式支持。

### 3. 按层落地

1. 在 `catalog.ts` 更新 `AVATAR_TOOL_DEFINITION_IDS`，添加完整 definition，并加入 `AVATAR_TOOL_REGISTRY`。
2. 补齐 label、三种 visual variant、hotspot/尺寸、声音、effect、interaction profile 和 capability；资源 id 唯一且引用闭合。
3. 更新 UI 资源与 8 个 locale；让菜单、快捷栏和持久化继续从注册表投影，不新增页面规则。
4. 若 profile 会生成 Host commit，更新 `static/app/app-buttons.js` 与 `avatar_interaction_contract.py` 的 action/intensity/touch-zone/special-field 契约。
5. 若 profile 会触发回应，按 `avatar-tool-prompt-guidelines.md` 更新 8 种语言的客观事件事实、memory 和代表样例。
6. 构建真实 desktop descriptor；只有新增 kind/version 时才修改 PC consumer。

### 4. 完成标准

新增道具只有同时满足以下条件才算进入当前能力：

- 已进入 ID 集合和 registry，definition 校验通过。
- Web 选择、范围视觉、press/release、取消、声音/effect 和单次 profile 输出正常。
- 产生 Host commit 时，Host 与 Python 对所有合法/非法组合保持 parity。
- 声明桌面能力时，真实 NEKO descriptor 能通过 PC validate/decode/runtime/output。
- Full/Compact 切换、Pet reload 和平台输入不会创建第二条命中或提交链。
- 需要回应时 prompt/memory 已同步；用户可见文案、资源准备和 8 个 locale 已同步。
- 跨仓测试消费 NEKO 实际输出，而不是只更新 PC 手写 fixture。

## 禁止模式

- 在 `App.tsx`、`FullChatSurface.tsx`、quickbar、manager 或 preload 写 tool-id 业务分支。
- 为新道具复制 timer、Audio、effect、burst、range 或 session lifecycle。
- 让 Chat descriptor、main poller、overlay 或普通模型 hit test 成为第二条 pointer/commit 链。
- 用默认 intensity、默认 touch zone、宽松布尔值或相似道具 fallback 接受非法 payload。
- 让 Host 或 Python 根据 tool id 反向猜前端没有提交的事实。
- 为了适配 PC 修改共享 Web 行为，或把平台 workaround 写入共享 profile。
- 把未注册资源、草稿或测试 fixture 当作已支持道具。

## 验证清单

### NEKO Web

1. definition、desktop producer 和 message callback schema 拒绝未知 ID、重复资源、非法引用、保留字段、超限值和矛盾 capability；要求唯一资源闭包的新 profile 还要在 definition 阶段拒绝未引用资源。legacy source extra-resource 只允许在 projection 前存在，不能进入 desktop descriptor。
2. Compact 中四种道具的选择、切换、取消和槽位移除正常；Full 保持原有三个道具，不因 Compact 新增猜拳而改变。
3. 范围内大形态、范围外小形态稳定；系统光标可见。
4. down 不提交；有效 up 单次提交；drag-out、超阈值、UI release、cancel、blur 和教程接管不提交。
5. release 重读当前 bounds/touch zone；视觉 hold 不授权命中。
6. RAF、timer、Audio、effect、Promise 和旧 generation 完整清理。
7. 猜拳准备手势只在真实范围内循环，按下不停止当前猫娘出拳；合法松开只确定一次双方手势，普通离开范围不截断最终停留。

### Host/Python

1. Host/Python normalizer 行为 parity，不只允许值列表一致。
2. 覆盖全部 action/intensity、touch zone 和特殊布尔组合。
3. 缺失 intensity/touch zone、越权字段、非法布尔值和矛盾彩蛋事实一致拒绝。
4. prompt/memory 覆盖所有 locale，`text_context` 不进入事件事实 instruction。
5. matching/non-matching turn、late ack、reject、timeout 和重复收尾不提前释放或卡住文本输入。

### NEKO-PC

1. descriptor ownership 在 Full/Compact 间原子交接，隐藏 surface 的迟到状态被拒绝。
2. Pet 原生 pointer 与 main sample 不互相取消；一次 down/poll/up 只 commit 一次。
3. Chat/Host 窗口排除、Pet 命中、reload replay 和首个 pointer sample 正常。
4. metadata replay、variant replay 和 contract reactivation 符合 generation/history/cleanup 语义。
5. 注入 RNG 的 chance 结果可确定；旧 timer/effect/press 不跨 selection。
6. overlay passthrough、native-ignore writer、平台 handoff 失败释放和系统光标不受破坏。
7. macOS、Windows、X11/Wayland/Niri 的现有窗口输入与截图暂停链路不回归。
8. 猜拳头顶手势由既有 overlay 第二个稳定节点显示，并按自身所在显示器换算和限制坐标；旧 generation、surface handoff 或窗口中断不残留上一局画面。

### 跨仓真实链路

```text
NEKO catalog/desktopContract 实际输出
  -> PC validate/decode/runtime
     -> 本地确认型 profile：验证本地结果后结束
     -> Host commit 型 profile：PC payload -> NEKO Host normalizer -> Python normalizer
```

NEKO-PC 的 `test/integration/avatar-tool-cross-repo.test.js` 通过 `NEKO_WEB_REPO_PATH` 读取 NEKO 实际 descriptor。PC 内部 fixture 只服务领域单测，不能替代这条跨仓事实链。

修改后运行与范围匹配的 typecheck、Vitest、Node contract、Python unit、PC unit/contract 和跨仓测试。涉及 pointer、窗口或平台输入时，还要以隔离数据目录进行真实 Full/Compact、reload、连续移动、按下、松开和取消验证。
