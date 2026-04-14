# 猫娘道具交互与提示词驱动回复设计方案

## 1. 背景

当前 React 聊天窗已经具备以下基础能力：

- `composer-emoji-btn` 可打开三种道具选择浮层
- 已选中道具时，`composer-emoji-btn` 主按钮可显示当前道具图标
- 选择道具后，鼠标光标会切换为对应图片
- 前端已经能判断鼠标是否处于猫娘模型的可交互范围
- 范围内外点击时，三种道具已有不同的视觉反馈

但目前这套交互仍主要停留在“前端状态与宿主事件”层，还没有真正进入后端会话链路。现在要做的是：

1. `composer-emoji-btn` 主按钮图标切换为当前选中道具图片
2. 当用户携带道具在猫娘判定范围内点击时，系统根据：
   - 当前选中的道具
   - 用户的点击动作
   - 是否命中猫娘判定范围
   - 可选的文本输入上下文
   生成不同提示词
3. 让猫娘基于这些提示词自然“说话”
4. 同时产生匹配的表情和动作

这不是一个单纯的 UI 改动，而是一个“前端交互 -> 结构化事件 -> 会话提示词 -> 回复生成 -> 表情驱动”的完整链路设计问题。

---

## 2. 设计目标

### 2.1 功能目标

- 支持三种道具在猫娘范围内点击时触发差异化互动
- 支持同一道具根据不同用户操作产生不同互动结果
- 让猫娘的回复像“正在被互动后真实反应”，而不是机械模板文本
- 让表情和动作尽量与回复情绪一致

### 2.2 工程目标

- 不污染普通文本聊天主链路
- 不把交互语义硬编码进用户输入文本
- 前端和后端职责清晰，后续方便扩展第四、第五种道具
- 兼容现有 `sendTextPayload()` / WebSocket / emotion analysis 机制
- 在网络抖动、模型超时、情绪分析失败时有可接受降级

### 2.3 体验目标

- 用户看到的是“我拿着某种道具碰了她一下，她有反应”
- 不是“我发送了一句隐藏 prompt，猫娘照着回”
- 不要每次都重复同一句台词
- 不要因为点击过快导致模型连续乱说或表情闪烁

---

## 3. 当前实现现状

### 3.1 前端已有能力

`frontend/react-neko-chat/src/App.tsx`

以下“现状”描述以当前仓库代码快照为准：

- 基准时间：`2026-04-13`
- 参考实现：`frontend/react-neko-chat/src/App.tsx`、`frontend/react-neko-chat/src/message-schema.ts`、`static/app-react-chat-window.js`

- `toolIconItems` 已定义三种道具：`lollipop`、`fist`、`hammer`
- `isPointerWithinAvatarRange()` 已能判断当前坐标是否位于猫娘模型可交互范围
- `activeCursorToolId` 已能表示当前选中道具
- `pointerdown` 已区分道具和范围内外，产生不同视觉效果
- `pointerdown` 当前已直接按 `event.clientX / event.clientY` 重新命中判定，避免依赖异步刷新的旧范围状态
- `composer-emoji-btn` 当前已显示所选道具图标，未选中时回退默认 emoji 图标
- 前端当前已可通过 `onAvatarInteraction(payload)` 向宿主发出结构化互动事件
- 当前前端事件边界已经收口：范围外点击只保留本地视觉反馈，不再进入宿主 / WebSocket / 后端会话链路
- `textContext` 仍会作为范围内互动的附加语境字段随 payload 发送，但范围外点击不再携带业务互动事件
- `submitDraft()` 目前只提交 `{ text }`

### 3.2 宿主桥接限制

`frontend/react-neko-chat/src/message-schema.ts`

- 当前 `ComposerSubmitPayload` 只有：

```ts
{
  text: string
}
```

- 当前已新增 `AvatarInteractionPayload` schema
- 当前已新增 `onAvatarInteraction` callback 类型入口
- 当前 schema 已经把 `target` 收敛为 `avatar`
- 但动作合法性仍需要持续与前端发送链路、宿主规范化链路、后端校验链路保持一致，避免 schema 先行放宽而运行时并不接受

`static/app-react-chat-window.js`

- `handleComposerSubmit()` 只接收文本
- 默认走 `window.appButtons.sendTextPayload(detail.text)`
- 当前已新增 `handleAvatarInteraction()`、`setOnAvatarInteraction()` 与兼容事件 `react-chat-window:avatar-interaction`
- `static/app-buttons.js` 已在既有 setter 注册区注册 `setOnAvatarInteraction()`
- `handleAvatarInteraction()` 在未注册 handler 时也已有明确 warning

这意味着：

- React 组件到宿主这一段的结构化事件桥接已经具备
- 宿主也已经能够把该事件继续送进 WebSocket / 会话链路
- 因此端到端“发事件 -> 后端注入 prompt -> 猫娘给出短回复”的基础主链路已经打通

### 3.3 当前会话链路

- 前端通过 WebSocket 发送 `stream_data`
- 后端 `LLMSessionManager.stream_data()` 按 `input_type` 进入文本或语音模式
- 模型回复流式回前端
- 回复结束后，前端再调用 `/api/emotion/analysis`
- 最终驱动 Live2D / VRM 表情

当前真实状态应表述为：

- 前端组件层已经有“用户对猫娘模型进行了道具交互”的结构化入口
- 宿主层也已经有对应 callback / 调试事件桥接
- 后端链路已经具备基础实现：
  - WebSocket `avatar_interaction` action 已在 `main_routers/websocket_router.py` 接入
  - `LLMSessionManager.handle_avatar_interaction()` 已在 `main_logic/core.py` 落地
- 联调测试时，前端/宿主应优先从 `onAvatarInteraction` 回调接线，再由宿主转成 WebSocket `avatar_interaction` 消息；`react-chat-window:avatar-interaction` 仍可作为调试事件观察入口
- 因此“结构化事件 -> prompt 注入 -> 自然回复”的主链路已存在，后文重点约束的是如何让它更稳、更安全，并与记忆/表情策略对齐
- 当前仓库代码已经补上两条关键收口：
  - 宿主发送 `avatar_interaction` 时会立即应用 `seedEmotion`，并在超时无最终情绪时自动回退
  - 后端会为互动生成简化 `memory_note`，再通过 `sync_message_queue` 标记给 `cross_server`，仅以 `[主人摸了摸你的头]` 这类摘要进入 `memory_server /cache`
- 仍保留为后续增强项的主要是更细粒度的 ack / 可观测性，而不是主链路缺失
- 后文统一以“范围外只保留本地视觉反馈，不保留为业务互动事件”作为收敛前提

---

## 4. 核心设计结论

推荐采用：

**结构化交互事件 + 后端提示词组装 + 前端即时反馈 + 后端最终回复与表情收敛**

不推荐采用：

- 方案 A：前端把交互转成一段隐藏文本直接拼到输入框里
- 方案 B：前端命中范围后直接本地伪造猫娘回复
- 方案 C：所有表情都在前端硬编码，不经过模型

### 4.1 为什么不建议把提示词直接拼进用户文本

例如前端偷偷发送：

```text
[系统提示：用户拿着棒棒糖在你的脸旁轻点了一下，请害羞地回应]
```

问题：

- 污染对话历史
- 容易被模型复读出来
- 难区分“真实用户文本”和“交互控制信息”
- 后面要做日志、调试、埋点、冷却、A/B 都会变乱

### 4.2 为什么不建议纯前端假回复

优点是快，但问题更大：

- 会和后端真实会话历史脱节
- 猫娘“说过的话”不会进入记忆/上下文
- 下一轮模型无法理解刚刚发生了什么
- 很难维持统一的人设语气

### 4.3 更好的方案

把道具互动视为一种特殊的“用户意图输入”，但以结构化字段传输，不直接混入用户可见文本。

后端收到后：

1. 将交互事件转成内部 prompt context
2. 选择是否追加用户当前文本
3. 让模型生成自然回复
4. 返回建议情绪 / 或沿用现有情绪分析链路

---

## 5. 交互模型设计

### 5.0 以实际代码为准的道具语义映射

在进入提示词和反应文案设计前，必须先以当前代码行为为准统一语义。

`frontend/react-neko-chat/src/App.tsx`

- 棒棒糖：
  - 命中范围内点击时，按 `primary -> secondary -> tertiary` 递进切换
  - 到 `tertiary` 后继续点击，不再换图，而是触发爱心上飘
  - 对应的是“亲密度逐步升高 + 高潮态连续投喂”

- 猫爪：
  - 代码内部 id 仍为 `fist`，但视觉资源已经是猫爪风格
  - 命中范围内按下时切到副图，松开恢复主图
  - 命中范围内当前实现有 `25%` 概率触发掉落特效（代码为 `Math.random() < 0.25`）
  - 对应的是“摸头 / 亲近互动 / 互动奖励”

- 锤子：
  - 命中范围内点击时走一次完整演出：`windup -> swing -> impact -> recover`
  - 动画未结束前不会开启下一次
  - 另有 `5%` 概率触发放大彩蛋
  - 对应的是“重击演出 + 压力/冲击感”

因此在设计对外文案和 prompt 时，建议采用：

- 内部字段名沿用代码：
  - `lollipop`
  - `fist`
  - `hammer`
- 对外展示文案可写成：
  - 棒棒糖
  - 猫爪
  - 锤子

不要把“猫爪”直接改成新的内部 `toolId`，否则会增加前后端改造面和兼容成本。

### 5.1 事件对象

建议把进入宿主 / 后端业务链路的交互数据结构收敛为：

注意：下面这一节定义的是**目标 contract**，不是当前前端 schema 已经完全满足的事实状态。

```ts
type AvatarInteractionPayload = {
  interactionId: string;
  toolId: 'lollipop' | 'fist' | 'hammer';
  actionId: string;
  target: 'avatar';
  pointer: {
    clientX: number;
    clientY: number;
  };
  touchZone?: 'ear' | 'head' | 'face' | 'body';
  textContext?: string;
  timestamp: number;
  intensity?: 'normal' | 'rapid' | 'burst' | 'easter_egg';
  rewardDrop?: boolean;
  easterEgg?: boolean;
};
```

补充说明：

- 上面这份结构是“业务链路版本”的 payload contract
- 范围外点击继续留在前端本地视觉层处理，不进入 `onAvatarInteraction` / WebSocket / prompt / memory
- 如果调试时仍保留 `outside` 概念，也只能存在于本地诊断或视觉状态，不能继续向后传递
- 到 WebSocket 层时，再按需要转换成后端更适合消费的 snake_case 字段
- 当前 `message-schema.ts` 已经用 `toolId` 判别联合约束了 `tool/action` 组合，并把 `target` 收敛为 `avatar`
- 当前 `rewardDrop` / `easterEgg` 仍位于共享 base schema，上面这条“字段隔离”在运行时主要依赖 `static/app-buttons.js` 规范化阶段二次收口，而不是 schema 层完全拒绝跨道具附加字段

### 5.2 `actionId` 设计

不要只用“点击”一个动作值，而是定义成可扩展枚举。

建议首版动作语义：

| 道具 | 进入业务链路的动作 | 当前允许的附加字段 |
|------|------------------|--------------------|
| `lollipop` | `offer`, `tease`, `tap_soft` | `intensity` |
| `fist` | `poke` | `intensity`, `reward_drop` |
| `hammer` | `bonk` | `intensity`, `easter_egg` |

其中首版可以先不做全部动作来源 UI，而是由当前前端已有点击状态机推导：

- 棒棒糖范围内点击：
  - `primary -> secondary` 视为 `offer`
  - `secondary -> tertiary` 视为 `tease`
  - `tertiary` 再次命中视为 `tap_soft`
- 猫爪范围内点击：
  - 默认 `poke`
- 锤子范围内点击：
  - 正常挥击 `bonk`
  - 放大彩蛋触发时，建议仍保留主动作 `bonk`，并附带 `easter_egg: true`

这样既能复用现有视觉逻辑，又不会额外增加复杂 UI。

需要额外说明的是：

- 范围外动作如 `wave_item`、`shadow_box`、`swing_air` 只保留在前端本地视觉层，不进入宿主 / WebSocket / 后端
- `bonk_light`、`threaten_playful`、`slam_warning`、`fake_out` 更适合作为后续扩展动作保留位，不纳入当前 MVP 主链路
- 不同道具的附加字段不能互相污染：
  - `reward_drop` 只属于 `fist.poke`
  - `easter_egg` 只属于 `hammer.bonk`
  - `lollipop` 不携带上述两个字段

按当前代码快照与 MVP 收敛，真正已有稳定来源的主要动作仍是：

- `lollipop.offer` / `lollipop.tease` / `lollipop.tap_soft`
- `fist.poke`
- `hammer.bonk`

而 `reward_drop`、`easter_egg` 更适合作为附加状态字段，而不是当前代码快照里的一级主动作。

### 5.2.1 高频互动状态定义

你这次补充的建议里，“频繁点击”是关键体验点。但项目实际代码里三种道具的高频条件并不相同，所以不能只定义一个笼统的 `rapid_click`。

推荐在前端或宿主层先归一成下面几类“互动强度”：

```ts
type InteractionIntensity =
  | 'normal'
  | 'rapid'
  | 'burst'
  | 'easter_egg';
```

建议按当前代码语义映射：

- `lollipop`
  - `primary -> secondary`: `normal`
  - `secondary -> tertiary`: `normal`
  - `tertiary` 后继续点击并触发爱心：`rapid`
  - 在短时间内连续多次爱心触发：`burst`

- `fist`
  - 普通摸头：`normal`
  - 短时间内连续快速摸头：`rapid`
  - 触发掉落奖励：额外附加 `reward_drop: true`

- `hammer`
  - 一次完整锤击：`normal`
  - 多次连续完整锤击且命中：`rapid`
  - 触发放大彩蛋：`easter_egg`

MVP 可以不把这些全部作为独立字段暴露给后端，但建议至少在内部 prompt builder 里能区分：

- 正常互动
- 高频互动
- 彩蛋爆发

### 5.3 命中范围设计

保留当前 `isPointerWithinAvatarRange()` 作为单一判定源，不重复造一套后端坐标判定。

建议业务链路里只产出一种 target：

- `avatar`: 命中猫娘交互区并准备进入业务互动链路

原因：

- 这一功能本质是“是否触发猫娘互动”
- 范围外点击已经收敛为纯前端视觉反馈，不再保留成业务事件
- 更细粒度分脸、头顶、身体在当前阶段没有稳定必要
- 后续如果真的需要，可以新增 `hitZone`

但按当前代码快照，实际业务 contract 已经额外携带了：

- `touchZone`: `ear | head | face | body`

它目前的定位是：

- 不改变 `target` 仍然只有 `avatar` 这一前提
- 已进入前端 schema、宿主规范化链路和后端 prompt builder
- 只作为“空间事实”帮助即时反应生成
- 当前不进入 `memory_note`

---

## 6. 前端方案设计

### 6.1 UI 变化

#### 6.1.1 `composer-emoji-btn` 图标变为当前选中道具图片

推荐行为：

- 未选中道具时，保持当前 emoji 图标
- 选中道具后，`composer-emoji-btn` 主按钮图标显示当前道具图
- 再次打开浮层时，仍显示所有道具供切换

这样用户能清楚知道当前“手里拿着什么”。

#### 6.1.2 是否让其他 `composer-tool-btn` 跟着变化

你的需求里提到 `composer-tool-btn composer-emoji-btn` 内图标变为其中的图片。建议收敛为：

- 只让“道具入口按钮”变化
- 不让导图、截图、翻译、点歌这些普通工具按钮一起变化

原因：

- `composer-tool-btn` 是通用类，不代表都应该表达“当前装备道具”
- 只改 `composer-emoji-btn` 语义最清楚

### 6.2 事件触发时机

建议在 `pointerdown` 且 `activeCursorToolId !== null` 时，做如下分流：

1. 先处理视觉动画
2. 如果命中 `avatar`，派发结构化交互事件
3. 根据配置决定是否立即触发说话

建议首版只在命中 `avatar` 时触发说话；范围外保持纯视觉反馈。

### 6.3 前端新增 host 事件

建议新增独立事件，不复用 `submit`。

但这里需要注意一个容易忽略的事实：

- `dispatchHostEvent('avatar-interaction', ...)` 只是在 `window` 上抛出事件
- 如果没有 host callback 或显式 listener，单靠这个事件并不能自动进入业务链路
- 当前仓库代码已经补了 host callback，并已继续接到宿主侧 `sendAvatarInteractionPayload()` 与后端 WebSocket `avatar_interaction`

推荐做法：

1. 在 React chat host 上增加专用 setter，例如：

```ts
setOnAvatarInteraction?: (payload: AvatarInteractionPayload) => void;
```

2. `App.tsx` 内部优先调用 host setter
3. 仅把 `react-chat-window:avatar-interaction` 当作调试/兼容事件，不作为唯一集成通道
4. 宿主接线直接放到 `static/app-buttons.js` 现有 setter 注册区，和 `setOnComposerSubmit()`、`setOnComposerImportImage()` 等走同一入口，不另起平行初始化路径

原因：

- 现有 `submit`、`import-image`、`screenshot` 等主流程，都是通过 host setter 接入
- 单靠自定义事件容易出现“React 发了，但宿主没接”的静默失败

按当前代码快照看：

- `setOnAvatarInteraction()` 已经存在
- `dispatchHostEvent('avatar-interaction', ...)` 也已存在
- `static/app-buttons.js` 已在既有注册区接上这个 setter，并会把 payload 规范化后送进后端
- 未注册 handler 时已有清晰 warning，因此不会完全静默失败

```ts
dispatchHostEvent('avatar-interaction', {
  interactionId,
  toolId,
  actionId,
  target: 'avatar',
  pointer,
  textContext: draft.trim(),
  timestamp: Date.now(),
  intensity,
  rewardDrop,
  easterEgg,
});
```

理由：

- `submit` 是发送聊天文本
- `avatar-interaction` 是模型互动控制
- 两者语义不同，拆开更干净

### 6.4 是否扩展 `ComposerSubmitPayload`

推荐两阶段方案：

#### 阶段一

新增独立事件 `avatar-interaction`，不改 `ComposerSubmitPayload`

优点：

- 改动范围小
- 不影响现有发消息逻辑
- 可以快速验证交互玩法

#### 阶段二

如果后续希望“点击猫娘 + 输入文本”作为一次复合提交，再扩展：

```ts
type ComposerSubmitPayload = {
  text: string;
  interaction?: AvatarInteractionPayload;
};
```

首版不建议直接走阶段二，因为会把“普通文本提交”和“模型交互事件”耦合得太早。

### 6.5 前端即时反馈

建议前端在交互触发后立刻做两类反馈：

1. 视觉反馈
   - 保留已有心心、挥击、切图、光标变化
2. 非最终表情反馈
   - 可以立即调用一次轻量 `window.applyEmotion(seedEmotion)`

例如：

- `lollipop.offer` -> `happy`
- `lollipop.tease` -> `surprised`
- `fist.poke` -> `happy`
- `hammer.bonk` -> `surprised`

这类即时反馈只负责“点击瞬间有反应”，不保证最终状态。

随后：

- 模型回复结束后仍走现有情绪分析链路
- 最终表情由完整回复文本收敛

这是“即时反馈 + 最终收敛”的双层机制。

### 6.6 `textContext` 使用约束

`textContext` 是最容易引发逻辑重复的字段，必须加约束。

建议：

- 只读取当前 `draft.trim()`
- 不自动清空输入框
- 不把 `textContext` 当成一次正式用户发言
- 长度限制建议 `<= 80` 字；超出则截断或直接忽略
- 当 `textContext` 为空时，不要在 prompt 中保留空字段

这样可以避免下面的问题：

- 用户只是手里还留着一段草稿，点击猫娘时被误当成正式输入
- 后面用户再点发送时，同一句话又作为普通聊天发送一次
- 一次互动携带太长文本，导致“即时反应”失去即时性

### 6.7 表情兜底回退

如果前端已经提前打了 `seedEmotion`，但后端因为冷却、无 session、网络错误而没有产出回复，那么表情不能一直挂着。

按当前宿主代码，已实现的兜底行为是：

- WebSocket 发送成功后立刻应用 `seedEmotion`
- 宿主会先记录当前模型表情为 `previousEmotion`
- 默认 `2200ms` 后自动回退到 `previousEmotion`；拿不到时才回 `neutral`
- 如果先收到了 `neko-assistant-emotion-ready`，则清除这次回退定时器，不再强制回滚

当前仍需在文档里明确一个真实边界：

- 现在的回退/续发等待主要依赖通用 assistant lifecycle 事件
- 还没有把 `interactionId` 和具体 `assistant turnId` 做严格绑定

这已经能避免大多数“表情挂住”问题，但若未来插入其他 assistant turn，仍建议继续补强更严格的 interaction-turn 关联。

---

## 7. 后端方案设计

### 7.1 新增输入通道

推荐在 WebSocket 增加一个新 action：

```json
{
  "action": "avatar_interaction",
  "interaction_id": "avatar-int-001",
  "tool_id": "lollipop",
  "action_id": "offer",
  "target": "avatar",
  "text_context": "你今天好可爱",
  "timestamp": 1710000000000
}
```

而不是伪装成 `stream_data` 文本。

### 7.2 Session Manager 处理方式

在 `websocket_router` 和 `LLMSessionManager` 中新增对应处理：

```python
async def handle_avatar_interaction(self, payload: dict):
    ...
```

内部做三件事：

1. 校验 payload
2. 根据 `tool_id + action_id + target` 生成内部 prompt context
3. 选择合适的投递通道，让猫娘回复

这里必须明确区分当前会话状态，否则很容易把现有模式切换逻辑打乱。

#### 推荐 MVP 规则

1. 如果当前是 `OmniOfflineClient` 文本会话：
   - 使用 `prompt_ephemeral()` 生成互动回复
   - 不走 `stream_text()`

2. 如果当前没有活动会话，但 WebSocket 已连接：
   - 可按“主动搭话”同样的方式自动拉起文本会话
   - 再通过 `prompt_ephemeral()` 投递

3. 如果当前正在语音录制或处于 `OmniRealtimeClient` 活跃语音会话：
   - MVP 不触发说话
   - 只保留前端即时视觉反馈和 seedEmotion
   - 等第二阶段再设计语音模式专属互动注入策略

#### 为什么 MVP 不建议直接支持语音模式说话

当前代码里：

- 文本模式有成熟的 `prompt_ephemeral()` 注入能力
- 语音模式的 `OmniRealtimeClient.prompt_ephemeral()` 目前不是通用“带自定义 instruction 说一句话”的稳定入口
- 强行在语音录制中插入互动回复，容易和 `user_activity`、打断、TTS、current_speech_id 状态机冲突

所以 MVP 最稳的策略是：

- 文本模式支持完整互动回复
- 语音模式只做即时表情反馈，不做新一轮说话

这样不会把现有录音/打断/热切换逻辑搞乱。

### 7.3 是否进入对话历史

推荐：

- 用户可见的猫娘回复进入正常会话历史
- 用户的“点击交互事件”不直接作为原始文本进入历史
- 但需要转成一条简化后的交互摘要进入记忆链路
- 这条摘要应与猫娘的即时反应一起，形成“发生了什么 + 她怎么回应”的成对记忆

推荐的交互摘要格式：

```text
[主人拿着棒棒糖逗了逗你]
[主人摸了摸你的头]
[主人用锤子敲了敲你的头]
```

高频场景下，建议进一步压缩：

```text
[主人连续拿棒棒糖喂你]
[主人连续摸了摸你的头]
[主人连续敲了你好几下]
```

这条摘要：

- 可进入短期上下文和长期记忆管线
- 不直接展示给用户
- 不带程序字段名，不带坐标，不带冗长控制信息
- 便于模型与记忆服务理解连续互动

还需要明确和当前项目真实记忆链路对齐的约束：

- turn end 阶段通过 `/cache/{lanlan}` 进入的只会先写 recent history，不会立刻触发事实提取、反思或 persona 更新
- 真正触发摘要压缩、时间索引、事实提取、history review 的是 `/renew`、`/settle`、`/process`
- 所以互动事件即使要入记忆，也应视为“先进入 recent，再随结算进入长期管线”，而不是一次点击就直接写 persona
- `memory_note` 的职责是给 recent / time-index / facts 管线一个干净、稳定、可压缩的自然语言事件锚点

还需要补一条约束：

- 交互进入记忆时，必须走简化后的 `memory_note`
- 不能把原始 `tool_id/action_id/intensity/text_context` 直接原样写入记忆
- 不能把整段系统引导词写入记忆

但要特别注意当前项目里的真实行为边界：

- `prompt_ephemeral()` 确实不会把隐藏 instruction 本身写进 `_conversation_history`
- 但它生成出来的 AI 回复，当前仍会追加到 `_conversation_history`
- 这意味着“猫娘反应进入记忆”与当前实现方向本身是兼容的
- 真正需要额外补的是：
  - 交互事件本身的简化记忆写入
  - 避免把原始交互控制指令、结构化字段、系统提示全文带进记忆

所以实现时要把表述收敛为：

- 猫娘的自然反应可以进入记忆
- 交互事件也可以进入记忆，但必须先归一成类似 `[主人摸了摸你的头]` 的简化摘要
- 记忆中应保留“互动发生了”这一事实，而不是保存原始控制协议
- 简化摘要应尽量稳定复用，避免同一类连续点击生成几十种近义写法，破坏现有 fact 去重与 recent review 效果

对“三种类型的正常点触交互是否写入”也要明确：

- 三种类型的正常点触都可以写入记忆
- 但写入的是“归一后的结果事件”，不是“每一次点击日志”
- 前提是这次互动真正成功投递，并产生了有效反应
- 如果只是 UI 切图、冷却丢弃、busy 拒绝、语音模式跳过，则不写

推荐收敛为：

- 棒棒糖正常点触：
  - 可以写入
  - 但优先写“阶段结果”，如 `[主人喂了你一口棒棒糖]`
  - 不建议把每一次纯图层切换都当作独立记忆
- 猫爪正常点触：
  - 可以写入
  - 直接归一成 `[主人摸了摸你的头]`
- 锤子正常点触：
  - 可以写入
  - 但只在一次完整锤击完成后写入，如 `[主人用锤子敲了敲你的头]`
  - 不记录 `windup / swing / recover` 中间阶段

哪些互动不应进入记忆，也要在文档里写死：

- 纯 UI 级切换，不形成实际互动结果的事件不进记忆
- 冷却内被丢弃、busy 被拒绝、语音模式跳过说话的事件默认不进记忆
- 高频连点中的每一次原子点击不应逐条入记忆，应合并成窗口摘要，如 `[主人连续摸了摸你的头]`
- 无附加语义的重复爱心飘动、重复掉落粒子、锤子动画中间阶段（`windup/swing/recover`）不应单独入记忆
- 原始 `text_context` 草稿、系统前后缀、调试字段、坐标、概率结果不应进入记忆

原因：

- 如果每次都把完整交互细节写进去，长期记忆会很脏
- 但如果完全不记，模型又会丢失“刚刚被摸了/拍了/敲了”的上下文连续性
- 最合理的是：保留简化事实，丢弃控制细节

### 7.4 冷却与串行策略

必须加节流。

建议：

- 同一道具交互冷却：`600ms`
- 触发猫娘说话冷却：`1500ms`
- 真实实现应放两层限制：
  - 宿主发送口前置闸门：在 `sendAvatarInteractionPayload()` 本地先做串行与冷却判断，不满足条件时直接丢弃，不再发 WebSocket
  - 后端兜底冷却：`LLMSessionManager.handle_avatar_interaction()` 继续保留去重、interaction cooldown、speak cooldown 与 busy/voice mode 拒绝
- 如果上一条互动回复仍在生成：
  - 默认丢弃低优先级交互
  - MVP 不允许任何互动打断当前回复
  - 第二阶段再讨论高优先级打断
- 宿主前置闸门的最低要求：
  - 同一时刻只允许一个点触互动处于 `awaiting_ack / awaiting_turn / active_turn`
  - 在 WebSocket 打开等待阶段也要先占位，避免快速点击在 `await ack` 前并发穿透
  - 被宿主闸门拦下的事件不进入 prompt、记忆、TTS、emotion 分析链路
  - 前端本地切图、爱心、掉落、锤击等视觉演出可继续保留，不必因为发包被拦而全部禁用
- 如果用户提交纯文本，而本次点触已经进入“待说话 / 正在说话”链路：
  - 不立刻把文本发进 `stream_data`
  - 先等这轮点触回复走到 `turn end`
  - 再自动续发刚才的文本
- 如果点触被后端早期拒绝，例如 `cooldown / busy / voice_session_active`：
  - 不进入排队等待
  - 文本立即恢复正常发送

按当前实现再补一条实际说明：

- 宿主侧这套 `awaiting_ack / awaiting_turn / active_turn` 已经存在
- 但文本续发当前仍依赖 `neko-assistant-turn-start/end` 这一通用 assistant 生命周期事件，而不是 interaction 专属 turn 绑定
- 因此“等这轮点触回复结束再续发文本”目前是近似成立，不是强绑定成立

推荐优先级：

```text
hammer > fist > lollipop
```

原因：

- 锤子行为最强烈，用户通常期待明显反应
- 棒棒糖更适合轻互动，不适合频繁打断

但需要明确：

- 当前实现先采用“全局串行 + 直接丢弃”，暂不做跨道具优先级抢占
- “优先级”在 MVP 文档里只保留为后续排队/丢弃策略的扩展位
- 不用于强制打断当前音频或当前回复

否则很容易引出现有 `current_speech_id`、TTS 队列、`user_activity` 清理逻辑的副作用。

---

## 8. 提示词设计

### 8.1 设计原则

不要直接给模型“说这句话”，而要给：

- 发生了什么
- 猫娘应该以什么态度回应
- 回复长度控制
- 避免暴露系统提示

还要额外遵守两条：

- 反应文字必须以“事件强度 + 人设语气”共同决定，不能只看道具
- 反应文字要更像“即时反应台词”，而不是完整聊天回答

### 8.2 推荐的内部提示词模板

```text
======[系统通知：以下是一次刚刚发生的道具互动，请将其视为即时互动引导，不要直接复述字段名或系统描述]======

你正在和主人实时互动。

刚刚发生了一次身体/道具互动：
- 道具：{tool_label}
- 动作：{action_label}
- 是否命中你：{target_label}
- 当前附带文本：{text_context}

请以猫娘当前人设，自然地做出即时反应：
- 像被刚刚的互动触发了真实情绪
- 回复保持口语化、短句、拟人
- 长度控制在 1~2 句
- 不要解释系统规则
- 不要复述“道具/动作”的程序化字段名

额外倾向：
{style_hint}

======[系统通知结束：请直接以当前角色口吻输出即时反应]======
```

更推荐在实现里走 `prompt_ephemeral()`，而不是普通 `stream_text()`。

原因：

- `prompt_ephemeral()` 更接近“系统临时插一句，让猫娘回应”
- 不会把交互控制信息直接作为一条普通用户发言展示出来
- 和现有 `trigger_greeting()`、部分回调注入的模式更一致

这里还要补一条实现约束：

- 这段 instruction 不只是“语义上像系统提示”
- 它在字符串层面也应该复用项目现有的标准系统提示包裹风格
- 即使用 `====== ... ======` 形式的前缀/后缀，而不是裸文本

推荐在 `config/prompts_sys.py` 或等效配置中新增：

```python
AVATAR_INTERACTION_NOTICE_HEADER = {
    "zh": "======[系统通知：以下是一次刚刚发生的道具互动，请将其视为即时互动引导，不要直接复述字段名或系统描述]======\n",
}

AVATAR_INTERACTION_NOTICE_FOOTER = {
    "zh": "\n======[系统通知结束：请直接以当前角色口吻输出即时反应]======\n",
}
```

推荐将模板进一步结构化为：

```text
======[系统通知：以下是一次刚刚发生的道具互动，请将其视为即时互动引导，不要直接复述字段名或系统描述]======

你正在和主人进行一次即时身体互动反应。

互动信息：
- 道具：{tool_label}
- 动作：{action_label}
- 强度：{intensity_label}
- 是否触发奖励：{reward_hint}
- 是否触发彩蛋：{easter_egg_hint}
- 当前附带文本：{text_context}

角色要求：
- 保持 {persona_summary}
- 用 {tone_style} 的方式反应
- 回复像“被碰到后立刻说出来的话”
- 优先输出 1 句，最多 2 句
- 不解释系统设定
- 不把字段名原样说出来

这次反应重点：
{reaction_focus}

额外风格提示：
{style_hint}

======[系统通知结束：请直接以当前角色口吻输出即时反应]======
```

这里的关键不是让模型“写得华丽”，而是让模型知道：

- 这是即时反应，不是完整对话
- 这次更偏甜、惊喜、抗议还是破防
- 这只猫娘自己平时是什么性格

### 8.3 不同道具的风格提示

#### `lollipop`

倾向：

- 可爱
- 被逗
- 有点期待 / 害羞 / 开心

示例风格 hint：

```text
如果是被喂、被逗、被轻点，优先表现出可爱、撒娇、开心或害羞。
```

反应重点建议：

- `offer`：从“吃到第一口”出发，强调第一次入口、味道确认、短暂停顿
- `tease`：从“继续吃第二口”出发，强调已经尝过后又被喂到嘴边的延续感
- `tap_soft` / 高频爱心：从“被连续喂糖”出发，强调节奏被打乱、连续接话、短时间内重复发生

#### `fist`

倾向：

- 抗议
- 小炸毛
- 但默认仍偏 playful，不要直接变攻击性

示例风格 hint：

```text
如果是被戳、被威胁式挥拳，优先表现出小脾气、抗议、炸毛，但保持亲密互动感。
```

反应重点建议：

- `poke`：像轻轻摸头、顺手安抚一下，强调“亲近且不疼”
- 高频摸头：强调应接不暇、节奏变快、语气变短促
- 掉落奖励：不是主反应，而是附加惊喜，可以在一句里顺带提醒

#### `hammer`

倾向：

- 受惊
- 委屈
- 强烈抗议
- 连击时可以更夸张

示例风格 hint：

```text
如果是被锤、被重击威胁，优先表现出震惊、委屈、夸张抗议，语气可以更明显。
```

反应重点建议：

- `bonk`：突出“头顶被敲中”“眼冒金星”“短暂晕眩”
- 连续重击：突出忍耐度下降、从嫌弃变为抗议
- 彩蛋爆发：突出“彻底破防”“夸张惨叫/怒气”

### 8.4 基于实际玩法的反应文案参考

下面这些不是最终要硬编码给用户看到的固定台词，而是推荐作为 prompt builder 的“事件语义描述层”。

#### 8.4.1 棒棒糖（Lollipop）

普通点击：

- 图1 -> 图2

```text
[系统指令] 用户递来了棒棒糖，你小心地咬住了一小块，舌尖尝到一点甜味。请做出带有试探感的初步反馈。
```

- 图2 -> 图3

```text
[系统指令] 用户又把棒棒糖递到你嘴边，这是继续吃下去的第二口。请围绕“又被喂了一口”的延续感做出即时反馈。
```

高频点击：

```text
[系统指令] 用户正在连续给你喂糖，周围不断冒出爱心。请围绕“连续被喂、回答节奏被打断、这一下还没说完又来下一下”的状态给出更急一点的即时反应。
```

#### 8.4.2 猫爪（Cat Paw，对应内部 `fist`）

普通点击：

```text
[系统指令] 用户用猫爪轻轻摸了摸你的头，像是在亲近地打招呼或安抚你。请做出简短、轻快、有互动感的回应。
```

掉落触发附加提示：

```text
[附带触发] 刚才这次摸头触发了掉落奖励，你注意到有宝物掉出来了。请在保持开心语气的同时，顺带提醒用户去捡。
```

频繁点击：

```text
[系统指令] 用户正在快速连续摸你的头，节奏越来越快。你一边应接不暇，一边被这种亲近又热闹的互动带得反应更快。请用更活泼、更急促的语气回应。
```

#### 8.4.3 锤子（Hammer）

普通点击：

```text
[系统指令] 咚的一声，用户用锤子精准敲中了你的头顶。你感到一阵晕眩，像是眼前冒出小星星。请用夸张但仍符合人设的方式描述你的即时状态。
```

频繁点击：

```text
[系统指令] 用户已经连续敲打你很多次，你的忍耐正在快速下降。请让语气从不满逐渐升级成明确的抗议，但仍保持角色本身的说话风格。
```

彩蛋爆发：

```text
[系统指令] 这一次是特别夸张的重击，冲击感远超平时，你被彻底砸晕了，或者彻底破防了。请给出非常戏剧化、非常夸张的惨叫、抗议或爆发式反馈。
```

### 8.5 不同人设下的语气适配

你特别提到“到时候结合用户猫娘各自不同的人设”，这点很关键。

这里不建议为每只猫娘手写完全不同的一套台词，而是采用：

**事件语义固定 + 人设语气调制**

也就是：

- “发生了什么”由工具和强度决定
- “怎么说出来”由人设决定

推荐抽象三个维度：

```ts
type PersonaReactionStyle = {
  baselineTone: string;
  intimacyExpression: string;
  angerExpression: string;
  embarrassmentExpression: string;
};
```

例如：

- 傲娇型：
  - 甜的时候：嘴硬、别扭、偷偷开心
  - 被敲的时候：抗议强、嘴上凶、底层仍亲密

- 软萌型：
  - 甜的时候：撒娇、依赖、软乎乎
  - 被拍的时候：会喵、会开心回应
  - 被锤的时候：委屈、夸张哭诉

- 冷淡型：
  - 甜的时候：克制、淡淡害羞
  - 被连续互动时：从平静逐渐破功

- 病娇/强占有型：
  - 棒棒糖高频：爱意浓烈、黏人、占有欲上升
  - 锤子高频：可能从委屈转成危险的反问或报复感

实现时建议后端从角色已有 prompt / 人设摘要中提炼一小段 `persona_summary`，拼进互动 prompt，而不是重新维护一份巨大的人设分支表。

### 8.6 “用于反应的文字”最终落地方式

最终建议不要把上面的参考文案直接当成用户可见固定回复，而是拆成两层：

#### 第一层：事件描述文

固定、结构化、可配置。

例如：

- `lollipop.offer`: “你咬住了一小块棒棒糖，尝到一点甜味。”
- `fist.poke`: “用户轻轻摸了摸你的头，像是在亲近地打招呼。”
- `hammer.bonk`: “你的头顶被敲中，眼前一阵发晕。”

#### 第二层：角色即时反应

由模型根据人设即时生成。

例如同样的 `hammer.bonk`：

- 软萌型：`呜哇……头顶都在转圈圈了……`
- 傲娇型：`你、你敲哪里啊！会变笨的啦！`
- 冷淡型：`……刚才那一下，确实有点重。`

这样做的好处是：

- 事件逻辑稳定
- 角色表现仍有生命力
- 不会每只猫娘都说同一句模板话

### 8.7 高频点击的文案升级规则

为了避免高频点击时回复重复，建议把互动反应分为三级：

#### Level 1：首次/普通

- 以“发生了什么”为主
- 语气轻

#### Level 2：持续/快速

- 以“我现在有点受不了了/有点上头了”为主
- 语气明显升温

#### Level 3：爆发/彩蛋

- 以“彻底甜晕 / 彻底破防 / 奖励爆出”这类强反馈为主
- 允许更夸张、更戏剧化

这比简单按点击次数硬分更稳，因为：

- 棒棒糖的高频来自第三阶段爱心
- 猫爪的高频来自连续摸头
- 锤子的高频来自完整重击反复命中

三者触发机制不一样，但都能落到“强度升级”这一统一概念上。

### 8.8 回复长度策略

建议严格限制交互回复长度。

推荐：

- 默认：1 句
- 有 `text_context` 时：1~2 句
- 连击彩蛋：最多 2 句

因为这是“即时反应”，不是进入长篇闲聊。

---

## 9. 表情与动作设计

### 9.1 双层表情机制

推荐分两层：

#### 第一层：前端即时表情种子

点击瞬间立刻触发，让手感及时。

映射示例：

| tool_id | action_id | seedEmotion |
|---------|-----------|-------------|
| lollipop | offer | happy |
| lollipop | tease | surprised |
| lollipop | tap_soft | happy |
| fist | poke | happy |
| fist | poke + reward_drop | happy |
| hammer | bonk | surprised |
| hammer | bonk + easter_egg | angry |

#### 第二层：回复完成后的最终表情

继续沿用现有 `/api/emotion/analysis`：

- 基于完整回复文本分析
- 再统一切最终表情

这样可以避免：

- 点一下没反应
- 或者最终表情和说的话完全不一致

### 9.2 是否让后端直接返回 emotion

建议预留但不强依赖。

可选返回格式：

```json
{
  "reply": "呜哇，不可以突然敲我啦……",
  "emotion": "surprised"
}
```

但首版仍建议：

- 回复文本由主链路产出
- emotion 继续由现有情绪分析统一收敛

原因是现有系统已经有成熟情绪分析流程，直接复用更稳。

### 9.3 Live2D / VRM 的兼容策略

前端交互层只产出标准情绪：

- `happy`
- `sad`
- `angry`
- `surprised`
- `neutral`

不要在交互层直接关心具体 expression 文件名。

这样：

- Live2D 走 `live2dManager.setEmotion()`
- VRM 走 `vrmManager.expression.setMood()`

模型层差异仍由现有映射系统承担。

---

## 10. 推荐的数据流

### 10.1 范围内点击触发互动

```text
用户选择道具
  -> composer-emoji-btn 显示当前道具
  -> 鼠标进入猫娘范围
  -> pointerdown
  -> 前端视觉反馈 + seedEmotion
  -> 生成 interactionId
  -> Host callback / 兼容事件分发 avatar-interaction
  -> 宿主决定是否发送 avatar_interaction
  -> 若当前为文本会话或可安全自动拉起文本会话
     -> LLMSessionManager.handle_avatar_interaction()
     -> prompt_ephemeral() 生成短回复
     -> gemini_response 流回前端
     -> turn end
     -> /api/emotion/analysis
     -> 最终表情收敛
  -> 若当前为活跃语音模式
     -> 仅保留即时视觉反馈与 seedEmotion
     -> 超时未回复则回退到 previousEmotion；拿不到时再回 neutral
```

### 10.2 范围外点击

```text
用户选择道具
  -> pointerdown outside avatar
  -> 仅视觉反馈
  -> 不调用 `onAvatarInteraction`
  -> 不发送 `avatar_interaction`
  -> 不触发说话 / seedEmotion / memory
  -> 可选更新道具状态
```

首版建议范围外点击不说话，避免用户把普通页面点击误当成和猫娘互动。

---

## 11. 配置化设计

### 11.1 为什么要配置化

如果把所有映射写死在前端代码里，后续调体验会很痛苦。

建议新增配置文件，例如：

`config/avatar_interaction_config.json`

```json
{
  "global": {
    "speakCooldownMs": 1500,
    "interactionCooldownMs": 600,
    "maxReplySentences": 2,
    "systemPrefix": "======[系统通知：以下是一次刚刚发生的道具互动，请将其视为即时互动引导，不要直接复述字段名或系统描述]======",
    "systemSuffix": "======[系统通知结束：请直接以当前角色口吻输出即时反应]======"
  },
  "tools": {
    "lollipop": {
      "label": "棒棒糖",
      "seedEmotion": {
        "offer": "happy",
        "tease": "surprised",
        "tap_soft": "happy"
      }
    },
    "fist": {
      "label": "猫爪",
      "seedEmotion": {
        "poke": "happy"
      }
    },
    "hammer": {
      "label": "锤子",
      "seedEmotion": {
        "bonk": "surprised"
      }
    }
  },
  "memory_note_templates": {
    "lollipop": {
      "default": "[主人喂了你一口棒棒糖]",
      "tease": "[主人拿着棒棒糖逗了逗你]",
      "rapid": "[主人连续拿棒棒糖喂你]"
    },
    "fist": {
      "default": "[主人摸了摸你的头]",
      "rapid": "[主人连续摸了摸你的头]"
    },
    "hammer": {
      "default": "[主人用锤子敲了敲你的头]",
      "rapid": "[主人连续敲了你好几下]"
    }
  }
}
```

这里的 `seedEmotion` 建议优先表达“基础动作默认值”。
像猫爪掉落奖励、锤子放大彩蛋这类当前代码里的附加状态，推荐通过 `reward_drop` / `easter_egg` 覆盖，而不是再额外扩出新的一级 `action_id`。

截至当前代码快照，这一节仍主要是“后续配置化目标”，不是现状事实：

- `seedEmotion`、tool/action 白名单、fallback 超时和 host 冷却主要硬编码在 `static/app-buttons.js`
- `tool_label` / `action_label` / `touch_zone_label` / `reaction_focus` / `style_hint` / `memory_note` 主要硬编码在 `main_logic/core.py`
- 当前还没有独立的 `avatar_interaction_config.json`

### 11.2 提示词模板配置化

进一步建议把每种道具的 `style_hint` 也做成配置，而不是散落在 Python 里。

这样策划体验会更轻松。

### 11.3 配置草案：`tool -> action -> intensity -> reaction_focus`

下面给出一份推荐的配置草案，目标不是一次把所有台词写死，而是把后端 prompt builder 最需要的“事件解释层”定下来。

建议配置文件中至少覆盖以下字段：

- `tool_label`
- `action_label`
- `intensity`
- `reaction_focus`
- `style_hint`
- `seed_emotion`
- `reply_length`

推荐结构：

```json
{
  "tools": {
    "lollipop": {
      "label": "棒棒糖",
      "actions": {
        "offer": {
          "normal": {
            "reaction_focus": "刚吃到第一口棒棒糖，重点放在第一次入口、确认味道和短暂停顿",
            "style_hint": "像第一次被温和投喂后的即时反应，可带一点轻微不好意思，但不要升级成靠近戏码",
            "seed_emotion": "happy",
            "reply_length": "short"
          }
        }
      }
    }
  }
}
```

推荐完整映射如下。

#### 11.3.1 棒棒糖

| tool | action | intensity | reaction_focus | seed_emotion | style_hint |
|------|--------|-----------|----------------|--------------|------------|
| `lollipop` | `offer` | `normal` | 刚吃到第一口，重点是第一次入口、确认味道、短暂停顿 | `happy` | 温和、偏轻，可带一点轻微不好意思，但不要迅速升级成亲密剧情 |
| `lollipop` | `tease` | `normal` | 这是继续喂来的第二口，重点是“已经尝过后又被喂一口”的延续感 | `surprised` | 比第一口更顺一点，但不要写成突然靠近或暧昧升级 |
| `lollipop` | `tap_soft` | `rapid` | 连续投喂让互动节奏变快，更像话还没说完又被继续喂 | `happy` | 语气可以更连贯、更轻快，但不要反复套同一句 |
| `lollipop` | `tap_soft` | `burst` | 高频连续投喂后，反应节奏明显被打乱，重点写这一下的急促和延续 | `happy` | 允许稍微失序或慌乱，但不要写成强烈告白 |

#### 11.3.2 猫爪

| tool | action | intensity | reaction_focus | seed_emotion | style_hint |
|------|--------|-----------|----------------|--------------|------------|
| `fist` | `poke` | `normal` | 整体是偏安抚的轻触互动；若落在头顶更像摸头，其他部位则体现相应差异 | `happy` | 短、轻、柔，可带一点放松感 |
| `fist` | `poke` | `rapid` | 连续轻触让反应节奏被带快，注意力被持续拉住 | `surprised` | 句子更短、更连贯，但保持安抚感 |
| `fist` | `poke` | `burst` | 高频摸头持续一段时间，互动显得更密集、更贴近 | `happy` | 连续、轻快，但不要过分闹腾 |
| `fist` | `poke` | `normal` + `reward_drop=true` | 摸头过程中伴随奖励掉落，需要顺带提醒用户注意 | `happy` | 先回应摸头，再简短提到奖励 |

#### 11.3.3 锤子

| tool | action | intensity | reaction_focus | seed_emotion | style_hint |
|------|--------|-----------|----------------|--------------|------------|
| `hammer` | `bonk` | `normal` | 头顶受到一次明确敲击，出现短暂停顿或轻微眩晕 | `surprised` | 有冲击感，但保持简短克制 |
| `hammer` | `bonk` | `rapid` | 连续敲击带来累积压力，语气可以逐步变重 | `angry` | 更直接、更不耐，但不要过分激烈 |
| `hammer` | `bonk` | `burst` | 多次敲击后反应幅度变大，出现更明显的抗议或抱怨 | `angry` | 允许稍夸张，但仍是短促当场反应 |
| `hammer` | `bonk` | `easter_egg` + `easter_egg=true` | 放大彩蛋触发后，这次冲击明显超出普通敲击 | `angry` | 戏剧化一些，但不要完全失控 |

### 11.4 配置草案：`persona_summary -> tone_style`

这里的目标不是把所有猫娘人设离散成固定枚举，而是给后端一个“将角色摘要压缩为语气控制变量”的映射层。

推荐先把角色人设抽成一段简短摘要：

```json
{
  "persona_summary": {
    "baseline": "嘴硬但在意主人，容易害羞，平时不会把喜欢说得太直白",
    "affection": "亲密时会别扭、嘴硬、偷偷开心",
    "anger": "生气时更多是炸毛和抗议，不会真的恶意伤人",
    "vulnerability": "被突然亲近或敲头时会破功"
  }
}
```

然后映射到 `tone_style`：

| persona_summary 特征 | tone_style |
|----------------------|-----------|
| 傲娇、嘴硬、在意主人 | 嘴硬别扭、句尾克制、容易在第二句露馅 |
| 软萌、黏人、爱撒娇 | 软、甜、主动贴近、情绪表达更直接 |
| 冷淡、克制、慢热 | 反应短、淡、克制，但在高强度互动下会逐渐破功 |
| 元气、活泼、闹腾 | 节奏快、感叹多、即时互动感强 |
| 病娇、占有欲强 | 高亲密时更黏、更偏执；高冲击时可能从委屈转为危险反问 |
| 成熟、姐姐系、包容 | 语气稳、会调侃、对用户互动更像“纵容中带提醒” |

推荐把它配置成：

```json
{
  "persona_tone_presets": {
    "tsundere": {
      "tone_style": "嘴硬、别扭、容易害羞，第一反应常是否认，第二拍才露出真实情绪",
      "affection_bias": "害羞 > 直球示爱",
      "anger_bias": "炸毛抗议 > 真正发火"
    },
    "soft": {
      "tone_style": "软萌、黏人、情绪表达直接，容易撒娇和依赖",
      "affection_bias": "甜和贴贴 > 克制",
      "anger_bias": "委屈抗议 > 凶"
    },
    "cool": {
      "tone_style": "克制、冷静、简短，情绪在强互动下才明显外露",
      "affection_bias": "克制心动 > 明说",
      "anger_bias": "冷淡不满 > 大吵大闹"
    },
    "genki": {
      "tone_style": "活泼、反应快、带一点闹腾和夸张",
      "affection_bias": "直接开心 > 扭捏",
      "anger_bias": "吵闹抗议 > 压抑"
    }
  }
}
```

后端实现建议：

1. 先从角色现有 prompt / 配置中提取 `persona_summary`
2. 再映射到最接近的 `tone_preset`
3. 最终把两者都带进 prompt builder：
   - `persona_summary` 负责“这是谁”
   - `tone_style` 负责“这次怎么说”

### 11.5 Prompt Builder 字段表

下面这份字段表可以直接作为后端 prompt builder 的输入协议草案。

#### 11.5.1 输入字段

| 字段名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| `interaction_id` | `string` | 是 | 本次互动唯一 ID，用于日志、去重、前端回退控制 |
| `tool_id` | `string` | 是 | `lollipop` / `fist` / `hammer` |
| `action_id` | `string` | 是 | 当前道具动作，如 `offer` / `poke` / `bonk` |
| `intensity` | `string` | 是 | `normal` / `rapid` / `burst` / `easter_egg` |
| `reward_drop` | `boolean` | 否 | 猫爪掉落奖励时为 `true` |
| `easter_egg` | `boolean` | 否 | 锤子放大彩蛋等特殊状态 |
| `text_context` | `string` | 否 | 用户当前草稿，用作附加语境，不是正式用户发言 |
| `persona_summary` | `object/string` | 否 | 当前猫娘的人设摘要 |
| `tone_style` | `string` | 否 | 从人设摘要映射出的风格控制文本 |
| `reaction_focus` | `string` | 是 | 本次互动最核心的感受描述 |
| `style_hint` | `string` | 否 | 道具/动作级的文风提示 |
| `seed_emotion` | `string` | 否 | 前端即时表情种子 |
| `reply_length` | `string` | 否 | `short` / `short_plus` 等控制回复长度 |
| `memory_policy` | `string/object` | 否 | 本次事件的记忆策略，如 `skip` / `append_once` / `merge_window` |
| `memory_window_key` | `string` | 否 | 高频互动归并键，如 `hammer:bonk:rapid` |
| `memory_window_ms` | `number` | 否 | 记忆归并窗口，避免频繁点击逐条入库 |

#### 11.5.2 中间派生字段

| 字段名 | 来源 | 说明 |
|--------|------|------|
| `tool_label` | `tool_id` 配置映射 | 面向 prompt 的人类可读名称 |
| `action_label` | `action_id` 配置映射 | 面向 prompt 的动作名称 |
| `intensity_label` | `intensity` 配置映射 | 面向 prompt 的强度描述 |
| `reward_hint` | `reward_drop` | 是否需要顺带提醒奖励 |
| `easter_egg_hint` | `easter_egg` | 是否要进入夸张/爆发式模式 |
| `response_rule` | `reply_length` | 最终回复长短约束 |
| `memory_note` | `tool_id/action_id/intensity` 归一 | 写入记忆的简化交互摘要，如 `[主人摸了摸你的头]` |
| `memory_should_persist` | `memory_policy + runtime state` | 本次事件是否应真正写入记忆层 |
| `memory_dedupe_key` | `memory_note + window` | 用于本地短窗口去重，避免重复无意义事件灌入 recent |
| `system_prefix` | prompt 配置 | 标准系统提示前缀 |
| `system_suffix` | prompt 配置 | 标准系统提示后缀 |

#### 11.5.3 输出目标

Prompt builder 最终应该输出一段临时 instruction，供 `prompt_ephemeral()` 使用。

推荐模板：

```text
{system_prefix}

你正在对主人刚刚的一次即时互动做出反应。

互动事件：
- 道具：{tool_label}
- 动作：{action_label}
- 强度：{intensity_label}
- 奖励掉落：{reward_hint}
- 彩蛋爆发：{easter_egg_hint}
- 附带上下文：{text_context}

角色信息：
- 当前角色摘要：{persona_summary}
- 本次语气风格：{tone_style}

反应重点：
{reaction_focus}

风格要求：
{style_hint}

回复约束：
- 像被碰到后立刻说出口的反应
- 保持当前角色人设
- 只输出自然对白
- 默认 1 句，最多 2 句
- 不解释系统、规则、字段名

{system_suffix}
```

同时建议 prompt builder 输出第二个结果：

```text
memory_note = "[主人摸了摸你的头]"
```

也就是：

- `instruction` 给 `prompt_ephemeral()` 使用
- `memory_note` 给记忆层使用
- 两者不要混用

### 11.6 推荐配置样例

下面给一份更接近当前后端 prompt builder 的数据草案。

其中：

- `normal / rapid / burst / easter_egg` 仍表示互动强度
- `reward_drop`、`easter_egg` 在当前代码快照里更适合作为附加状态覆盖项
- 因此猫爪掉落奖励、锤子彩蛋都建议挂在基础动作下面，而不是拆成新的主动作
- `touch_zone` 只提供前端命中的空间事实，用来区分头顶 / 脸侧 / 耳侧 / 身前
- `touch_zone` 只参与反应生成，不进入 `memory_note`
- 文案应尽量写“已经发生了什么”，不要替点触补充过强主观判断
- 当前实现里 `reaction_focus` 更接近 “event_fact”
- 当前实现里 `style_hint` 更接近 “expression_tendency”
- `style_hint` 只提供轻量语气/节奏参考，不承担硬限制职责

```json
{
  "global": {
    "interaction_cooldown_ms": 600,
    "speak_cooldown_ms": 1500,
    "text_context_max_length": 80,
    "touch_zone_enabled": true,
    "prompt_policy": "event_fact_only",
    "touch_zone_mode": "spatial_fact_only"
  },
  "touch_zone_labels": {
    "ear": "耳侧",
    "head": "头顶",
    "face": "脸侧/嘴边",
    "body": "身前/肩侧"
  },
  "tools": {
    "lollipop": {
      "label": "棒棒糖",
      "actions": {
        "offer": {
          "normal": {
            "reaction_focus": "棒棒糖第一次完成入口接触。",
            "style_hint": "可以带一点初次入口后的停顿感、尝味感，语气自然偏轻。",
            "seed_emotion": "happy",
            "reply_length": "short"
          }
        },
        "tease": {
          "normal": {
            "reaction_focus": "棒棒糖已完成第一口，本次是紧接着的第二口接触。",
            "style_hint": "比第一口更顺一点、更接得上上一拍，语气保持自然。",
            "seed_emotion": "surprised",
            "reply_length": "short"
          }
        },
        "tap_soft": {
          "rapid": {
            "reaction_focus": "第三阶段后继续点触，前端已表现为爱心上飘；本次属于连续喂食中的一次。",
            "style_hint": "节奏可以更快、分句可以更短，像连续被打断中的即时反应。",
            "seed_emotion": "happy",
            "reply_length": "short_plus"
          },
          "burst": {
            "reaction_focus": "短时间内连续多次点触，属于更高频的连续喂食。",
            "style_hint": "允许更碎一点、更急一点，保持当场反应感。",
            "seed_emotion": "happy",
            "reply_length": "short_plus"
          }
        }
      }
    },
    "fist": {
      "label": "猫爪",
      "actions": {
        "poke": {
          "normal": {
            "reaction_focus": "猫爪产生一次短促轻触。",
            "style_hint": "短、轻、柔，根据部位差异自然带出细微区别。",
            "seed_emotion": "happy",
            "reply_length": "short"
          },
          "rapid": {
            "reaction_focus": "短时间内连续多次轻触。",
            "style_hint": "可以更连贯一点、更快一点，保持轻触感。",
            "seed_emotion": "surprised",
            "reply_length": "short"
          },
          "reward_drop": {
            "reaction_focus": "本次轻触同时触发奖励掉落。",
            "style_hint": "先接住轻触，再顺手带一句掉落物。",
            "seed_emotion": "happy",
            "reply_length": "short_plus"
          }
        }
      }
    },
    "hammer": {
      "label": "锤子",
      "actions": {
        "bonk": {
          "normal": {
            "reaction_focus": "完成一次完整锤击流程并进入命中结果。",
            "style_hint": "短促、带一点冲击停顿感，像被打断后的第一反应。",
            "seed_emotion": "surprised",
            "reply_length": "short"
          },
          "rapid": {
            "reaction_focus": "短时间内再次完成锤击，已形成连续敲击。",
            "style_hint": "可以更直接一点，体现连续敲击后的累积感。",
            "seed_emotion": "angry",
            "reply_length": "short"
          },
          "burst": {
            "reaction_focus": "连续锤击次数进一步增加，本次属于更高强度结果。",
            "style_hint": "反应幅度可以更大一些，但仍保持即时、短促。",
            "seed_emotion": "angry",
            "reply_length": "short_plus"
          },
          "easter_egg": {
            "reaction_focus": "本次锤击触发放大彩蛋，命中结果明显强于普通锤击。",
            "style_hint": "可以更夸张、更有戏剧停顿，但仍保持角色口吻。",
            "seed_emotion": "angry",
            "reply_length": "short_plus"
          }
        }
      }
    }
  }
}
```

---


以下落地实现、任务拆分、测试与伪代码内容已舍弃；本文仅保留设计思路、交互模型、数据流与策略约束。
