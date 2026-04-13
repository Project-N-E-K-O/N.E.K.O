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
- 但按当前代码快照，`App.tsx` 仍会在范围外派发 `outside` / `wave_item` / `shadow_box` / `swing_air` 这类事件；这属于“当前实现尚未收口”的差异，不是本文目标行为
- 当前代码里 `textContext` 也还是按统一逻辑挂在已发出的互动 payload 上，因此如果不继续收口，范围外点击也可能携带草稿文本
- 文档收敛结论：范围外点击仍可保留前端本地视觉分支，但不再作为后续宿主 / WebSocket / 后端会话链路输入
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
- 但按当前代码快照，schema 仍允许 `target: 'avatar' | 'outside'`；这说明类型层也还没有完全收敛到本文后续定义的目标 contract

`static/app-react-chat-window.js`

- `handleComposerSubmit()` 只接收文本
- 默认走 `window.appButtons.sendTextPayload(detail.text)`
- 当前已新增 `handleAvatarInteraction()`、`setOnAvatarInteraction()` 与兼容事件 `react-chat-window:avatar-interaction`
- 但按当前代码快照：
  - `static/app-buttons.js` 里还没有在既有 setter 注册区注册 `setOnAvatarInteraction()`
  - `handleAvatarInteraction()` 在未注册 handler 时也还没有打印明确 warning

这意味着：

- React 组件到宿主这一段的结构化事件桥接已经具备
- 但“宿主如何把该事件继续送进 WebSocket / 会话链路”还没有在当前仓库代码里接完
- 因此端到端会话链路仍然未打通

### 3.3 当前会话链路

- 前端通过 WebSocket 发送 `stream_data`
- 后端 `LLMSessionManager.stream_data()` 按 `input_type` 进入文本或语音模式
- 模型回复流式回前端
- 回复结束后，前端再调用 `/api/emotion/analysis`
- 最终驱动 Live2D / VRM 表情

当前真实状态应表述为：

- 前端组件层已经有“用户对猫娘模型进行了道具交互”的结构化入口
- 宿主层也已经有对应 callback / 调试事件桥接
- 但后端 WebSocket 入口仍没有 `avatar_interaction` action，`LLMSessionManager` 里也还没有 `handle_avatar_interaction()`
- 因此“结构化事件 -> prompt 注入 -> 自然回复 -> 记忆/情绪收敛”的后半段仍未落地
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
  - 对应的是“拍一下 / 击掌 / 互动奖励”

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
  - 普通拍一下：`normal`
  - 短时间内连续快速拍打：`rapid`
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
- 当前仓库代码已经补了 host callback 这一层，但还没有继续接到后端 WebSocket

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
- 当前还缺的是“在 `static/app-buttons.js` 既有注册区接上这个 setter，并把 payload 规范化后真正送进后端”
- 当前也还缺“未注册 handler 时的清晰 warning”，否则仍有静默失败风险

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

建议增加一个轻量兜底：

- 命中互动后立刻打 `seedEmotion`
- 如果在 `1.2s ~ 2.0s` 内没有收到本次 `interactionId` 对应的回复开始事件
- 自动回退到 `neutral`

这能避免“点了一下她惊讶了，但后面没说话，结果一直保持惊讶脸”的异常体验。

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
[主人拍了拍你]
[主人用锤子敲了敲你的头]
```

高频场景下，建议进一步压缩：

```text
[主人连续拿棒棒糖喂你]
[主人连续拍了拍你]
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
- 交互事件也可以进入记忆，但必须先归一成类似 `[主人拍了拍你]` 的简化摘要
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
  - 直接归一成 `[主人拍了拍你]`
- 锤子正常点触：
  - 可以写入
  - 但只在一次完整锤击完成后写入，如 `[主人用锤子敲了敲你的头]`
  - 不记录 `windup / swing / recover` 中间阶段

哪些互动不应进入记忆，也要在文档里写死：

- 纯 UI 级切换，不形成实际互动结果的事件不进记忆
- 冷却内被丢弃、busy 被拒绝、语音模式跳过说话的事件默认不进记忆
- 高频连点中的每一次原子点击不应逐条入记忆，应合并成窗口摘要，如 `[主人连续拍了拍你]`
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
- 如果上一条互动回复仍在生成：
  - 默认丢弃低优先级交互
  - MVP 不允许任何互动打断当前回复
  - 第二阶段再讨论高优先级打断

推荐优先级：

```text
hammer > fist > lollipop
```

原因：

- 锤子行为最强烈，用户通常期待明显反应
- 棒棒糖更适合轻互动，不适合频繁打断

但需要明确：

- “优先级”在 MVP 里只用于排队/丢弃决策
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

- `offer`：从“被投喂”出发，强调甜味、靠近、轻微羞涩
- `tease`：从“距离拉近”出发，强调暧昧、亲昵、心跳感
- `tap_soft` / 高频爱心：从“被连续喂糖”出发，强调甜晕、兴奋、爱意泛滥

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

- `poke`：像拍一下、击掌、轻轻拍脸，强调“突然但不疼”
- 高频拍打：强调应接不暇、节奏变快、语气变短促
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
[系统指令] 棒棒糖已经快吃完了，你和用户贴得很近，空气里有点暧昧和甜甜的亲密感。请做出更亲昵、更害羞的反应。
```

高频点击：

```text
[系统指令] 用户正在连续给你喂糖，周围不断冒出爱心。你已经被甜得有点晕乎乎的，心情兴奋又幸福。请用明显升温的语气表达你的喜欢和依赖感。
```

#### 8.4.2 猫爪（Cat Paw，对应内部 `fist`）

普通点击：

```text
[系统指令] 用户用猫爪轻轻拍了你一下，像是在和你击掌或打招呼。请做出简短、轻快、有互动感的回应。
```

掉落触发附加提示：

```text
[附带触发] 刚才那一下拍击触发了掉落奖励，你注意到有宝物掉出来了。请在保持开心语气的同时，顺带提醒用户去捡。
```

频繁点击：

```text
[系统指令] 用户正在快速连续拍打你，节奏越来越快。你一边应接不暇，一边被这种热闹的互动带得兴奋起来。请用更活泼、更急促的语气回应。
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
- `fist.poke`: “用户轻轻拍了你一下，像是在击掌。”
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
- 猫爪的高频来自连续拍打
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
     -> 超时未回复则回退 neutral
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
      "default": "[主人拍了拍你]",
      "rapid": "[主人连续拍了拍你]"
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
            "reaction_focus": "第一次咬到甜味、轻微靠近、试探性的开心与羞涩",
            "style_hint": "更像被温柔投喂后的第一反应，甜但不要一下子过热",
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
| `lollipop` | `offer` | `normal` | 第一次咬到一点甜味，身体距离略微拉近，反应偏试探和心动 | `happy` | 温柔、甜、轻微害羞，不要直接进入表白级别 |
| `lollipop` | `tease` | `normal` | 棒棒糖快吃完了，距离非常近，暧昧感明显升高 | `surprised` | 强调亲密升温、眼神躲闪、呼吸停顿感 |
| `lollipop` | `tap_soft` | `rapid` | 连续被投喂，爱心不断冒出，甜得有点晕乎乎 | `happy` | 语气明显升温，可以更黏、更甜、更上头 |
| `lollipop` | `tap_soft` | `burst` | 疯狂喂糖，已经进入“甜晕了”的状态，爱意泛滥 | `happy` | 允许语无伦次、撒娇、过度幸福感，但仍要符合角色人设 |

#### 11.3.2 猫爪

| tool | action | intensity | reaction_focus | seed_emotion | style_hint |
|------|--------|-----------|----------------|--------------|------------|
| `fist` | `poke` | `normal` | 被轻轻拍一下，像击掌、打招呼或拍脸互动 | `happy` | 短、轻快、像即时喵一下的反应 |
| `fist` | `poke` | `rapid` | 用户连续快速拍打，节奏越来越快，开始应接不暇 | `surprised` | 语气变急促，句子更短，带一点兴奋和忙乱 |
| `fist` | `poke` | `burst` | 高频互动持续，气氛变得热闹、亢奋、停不下来 | `happy` | 活泼、急促、像一边被拍一边还在回嘴 |
| `fist` | `poke` | `normal` + `reward_drop=true` | 互动中意外掉落奖励，需要开心提醒用户去捡 | `happy` | 主反应仍然围绕拍一下，奖励提示只占一句里的后半部分 |

#### 11.3.3 锤子

| tool | action | intensity | reaction_focus | seed_emotion | style_hint |
|------|--------|-----------|----------------|--------------|------------|
| `hammer` | `bonk` | `normal` | 头顶被准确敲中，眼冒金星，短暂眩晕 | `surprised` | 要有冲击感，但仍是即时反馈，不要写成长段抱怨 |
| `hammer` | `bonk` | `rapid` | 连续被敲，忍耐度下降，从嫌弃升级到抗议 | `angry` | 语气逐步加重，体现“已经不高兴了” |
| `hammer` | `bonk` | `burst` | 连续重击造成明显破防或快要哭出来 | `angry` | 允许夸张抗议、委屈、炸毛，但保持角色风格 |
| `hammer` | `bonk` | `easter_egg` + `easter_egg=true` | 放大彩蛋触发，重击远超平时，彻底砸晕或彻底破防 | `angry` | 戏剧化、夸张、强烈，允许惨叫或爆发式反应 |

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
| `memory_note` | `tool_id/action_id/intensity` 归一 | 写入记忆的简化交互摘要，如 `[主人拍了拍你]` |
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
memory_note = "[主人拍了拍你]"
```

也就是：

- `instruction` 给 `prompt_ephemeral()` 使用
- `memory_note` 给记忆层使用
- 两者不要混用

### 11.6 推荐配置样例

下面给一份更接近后端可直接读取的数据草案：

其中：

- `normal / rapid / burst / easter_egg` 仍表示互动强度
- `reward_drop`、`easter_egg` 在当前代码快照里更适合作为附加状态覆盖项
- 因此猫爪掉落奖励、锤子彩蛋都建议挂在基础动作下面，而不是拆成新的主动作

```json
{
  "global": {
    "interaction_cooldown_ms": 600,
    "speak_cooldown_ms": 1500,
    "text_context_max_length": 80
  },
  "tools": {
    "lollipop": {
      "label": "棒棒糖",
      "actions": {
        "offer": {
          "normal": {
            "reaction_focus": "第一次尝到甜味，试探性开心，距离略微拉近",
            "style_hint": "甜、轻、带一点害羞",
            "seed_emotion": "happy",
            "reply_length": "short"
          }
        },
        "tease": {
          "normal": {
            "reaction_focus": "距离已经很近，暧昧感升高，呼吸和眼神都开始乱掉",
            "style_hint": "更亲昵、更心跳、更贴近",
            "seed_emotion": "surprised",
            "reply_length": "short"
          }
        },
        "tap_soft": {
          "rapid": {
            "reaction_focus": "连续被投喂，心情越来越甜，爱意不断上涌",
            "style_hint": "高甜、高亲密、上头",
            "seed_emotion": "happy",
            "reply_length": "short_plus"
          },
          "burst": {
            "reaction_focus": "彻底甜晕了，语无伦次地表达喜欢和依赖",
            "style_hint": "允许明显失控和幸福感爆发",
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
            "reaction_focus": "像击掌一样被轻轻拍了一下，轻快回应",
            "style_hint": "短、灵动、像即时喵一声",
            "seed_emotion": "happy",
            "reply_length": "short"
          },
          "rapid": {
            "reaction_focus": "连续快速拍打让人应接不暇，节奏感很强",
            "style_hint": "更急促、更热闹、更有互动感",
            "seed_emotion": "surprised",
            "reply_length": "short"
          },
          "reward_drop": {
            "reaction_focus": "拍击互动中意外掉出奖励，要开心提醒主人",
            "style_hint": "把奖励提醒放在反应后半句，别喧宾夺主",
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
            "reaction_focus": "头顶被敲中，眼冒金星，短暂眩晕",
            "style_hint": "有冲击感，但保持即时反应的短促性",
            "seed_emotion": "surprised",
            "reply_length": "short"
          },
          "rapid": {
            "reaction_focus": "连续被敲后忍耐度下降，从嫌弃升级到抗议",
            "style_hint": "语气明显加重",
            "seed_emotion": "angry",
            "reply_length": "short"
          },
          "burst": {
            "reaction_focus": "已经有点被敲破防了，委屈和炸毛同时上来",
            "style_hint": "允许夸张，但别脱离人设",
            "seed_emotion": "angry",
            "reply_length": "short_plus"
          },
          "easter_egg": {
            "reaction_focus": "超规格重击导致彻底砸晕或彻底破防",
            "style_hint": "戏剧化、夸张、强烈",
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

## 12. 首版实现建议

### 12.1 推荐 MVP 范围

首版只做以下内容：

1. `composer-emoji-btn` 主图标显示当前选中道具
2. 范围内点击时触发结构化 `avatar-interaction`
3. 仅支持每个道具 1 个主要动作：
   - `lollipop.offer`
   - `fist.poke`
   - `hammer.bonk`
4. 前端即时 seedEmotion
5. 仅在文本会话或可安全自动拉起文本会话时，后端生成 1 句短回复
6. 回复结束后走现有情绪分析收敛
7. 语音录制中只做视觉与表情反馈，不注入说话

### 12.2 暂缓内容

这些建议第二阶段再做：

- 文本输入与点击交互的复合提交
- 范围内不同身体区域命中
- 复杂连击系统
- 后端直接返回 emotion/action script
- 多轮交互记忆权重调优

但下面两项不建议延后：

- interaction instruction 使用标准系统提示前缀/后缀
- 交互进入记忆时先归一成简化 `memory_note`

---

## 13. 风险与对策

### 13.1 风险：点击过快导致刷屏

对策：

- 交互冷却
- 会话生成中做丢弃/合并
- 只保留最后一次低优先级互动

### 13.2 风险：模型回复太长，失去“即时反应”感觉

对策：

- 交互 prompt 单独限制长度
- 使用 interaction-specific max tokens
- 必要时增加后处理截断

### 13.3 风险：表情和回复不一致

对策：

- 先 seedEmotion，后 final emotion
- 种子情绪只作为瞬时反馈
- 最终以回复文本分析为准

### 13.4 风险：普通聊天与互动聊天互相污染

对策：

- 互动走独立事件通道
- 仅在后端内部拼 prompt
- 不把结构化字段直接混进用户可见文本
- 不把范围外动作、动画中间态、另一道具专属字段带进当前 payload
- `reward_drop` 只允许随 `fist` 上行，`easter_egg` 只允许随 `hammer` 上行

### 13.5 风险：互动逻辑打断现有语音/文本状态机

对策：

- MVP 只在文本模式或 idle 状态下注入互动回复
- 语音录制中不自动插入一轮新说话
- 不允许互动逻辑强制 `end_session()` 或切换模式
- 不允许互动逻辑复用普通 `stream_text()` 伪造用户消息

### 13.6 风险：互动事件没有被宿主真正消费

对策：

- 不把 `dispatchHostEvent()` 当作唯一接入方式
- 增加 `setOnAvatarInteraction()` 风格的 host callback
- 在 `static/app-buttons.js` 现有 setter 注册区接线，不新增并行 init path
- 在宿主未接线时打印清晰告警

### 13.7 风险：seedEmotion 提前触发后无法收敛

对策：

- 每次互动绑定 `interactionId`
- 等待本次互动对应的回复开始/结束事件
- 若超时未收到，则自动回 `neutral`

---

## 14. 测试建议

### 14.1 前端测试

以下条目是**完成态应满足的测试点**，不是在说明当前代码已经全部满足：

- 未选中道具时，`composer-emoji-btn` 显示默认图标
- 选中后显示当前道具图标
- 范围内点击会派发 `avatar-interaction`
- 范围外点击不派发 `avatar-interaction`，也不触发说话
- 不同道具映射出不同 `actionId`
- 宿主未注册 `onAvatarInteraction` 时有明确告警，不静默失败
- `textContext` 不会清空输入框，也不会立即生成一条用户消息
- seedEmotion 超时会自动回退

### 14.2 后端测试

- `avatar_interaction` payload 校验
- prompt 组装正确
- 冷却逻辑正确
- 回复长度受控
- 并发点击不会导致 session 异常
- 文本模式走 `prompt_ephemeral()`，不会生成额外用户发言
- 无活动会话时自动拉起文本会话的行为符合预期
- 活跃语音模式下不会强制切换或打断

### 14.3 联调测试

- Live2D 模型能即时切 seedEmotion
- VRM 模型也能即时切 seedEmotion
- 回复结束后最终表情能被正常覆盖
- 连续互动后会话历史仍然稳定
- 互动引导词本体不会进入长期记忆
- 交互进入记忆时会被压缩成类似 `[主人拍了拍你]` 的简化摘要
- 猫娘互动回复可以正常进入记忆
- 语音录制中点击猫娘不会导致录音中断或模式切换

---

## 15. 最终推荐方案

最终建议采用下面这套组合：

1. **UI 层**
   - `composer-emoji-btn` 显示当前选中道具图
   - 保留现有光标与点击动画

2. **事件层**
   - 新增独立 `avatar-interaction` host event
   - 新增独立 WebSocket action：`avatar_interaction`

3. **会话层**
   - 后端把交互事件转成内部 prompt context
   - 生成 1~2 句即时互动回复

4. **表情层**
   - 前端命中时立即给 seedEmotion
   - 回复结束后仍走现有情绪分析做最终收敛

5. **工程策略**
   - 首版不扩展 `ComposerSubmitPayload`
   - 首版不把交互控制信息混入用户文本
   - 首版只做每个道具一个主动作

这套设计的好处是：

- 体验上像真的在和猫娘互动
- 架构上不破坏现有聊天主链路
- 后续扩展动作、道具、部位、连击都比较顺

---

## 16. 后续落地任务拆分建议

可以按下面顺序开发：

1. React 聊天窗：
   - `composer-emoji-btn` 图标切换
   - 点击事件抽象成 `AvatarInteractionPayload`

2. Host 层：
   - 新增 `avatar-interaction` 事件桥接

3. 前端宿主：
   - 新增 WebSocket `avatar_interaction` 发送
   - 接入 seedEmotion 映射
   - 接入 `reactChatWindowHost.setOnAvatarInteraction()` 或等效桥接

4. 后端：
   - `websocket_router` 增加 action
   - `LLMSessionManager` 增加交互处理入口
   - `prompt_ephemeral()` 互动投递
   - `memory_note` 归一并写入记忆层
   - prompt builder + 冷却逻辑
   - 文本模式 / 无会话 / 活跃语音模式 三态分流

5. 联调：
   - 回复长度、频率、表情种子、最终情绪收敛

---

## 17. 实施清单

下面这份清单按“先不破坏现有模块，再逐步接入”的顺序排列。

### 17.1 React 组件层

- 为 `composer-emoji-btn` 增加“当前选中道具图标”显示逻辑
- 新增 `AvatarInteractionPayload` 类型定义
- 在 `pointerdown` 逻辑中抽出 `buildAvatarInteractionPayload()`
- 给每次互动生成 `interactionId`
- 增加 `onAvatarInteraction` prop
- 在命中 `avatar` 时调用 `onAvatarInteraction`
- 增加 seedEmotion 回退定时器

### 17.2 React Host 层

- 在 `message-schema.ts` 或 host 类型层增加 `onAvatarInteraction`
- 在 `static/app-react-chat-window.js` 增加：
  - `state.onAvatarInteraction`
  - `setOnAvatarInteraction(handler)`
  - `handleAvatarInteraction(payload)`
- 保留 `dispatchHostEvent('avatar-interaction', ...)` 作为兼容/调试事件
- 在未注册 handler 时打印清晰 warning

### 17.3 前端宿主层

这一节描述的是**后续实施要求**，不是当前宿主代码已完成的事实状态。

- 在 `static/app-buttons.js` 当前与 `setOnComposerSubmit()`、`setOnComposerImportImage()`、`setOnComposerScreenshot()`、`setOnComposerRemoveAttachment()` 相同的注册区注册 `reactChatWindowHost.setOnAvatarInteraction`
- 增加 `sendAvatarInteractionPayload(payload)` 方法
- 做 payload 规范化：
  - `toolId`
  - `actionId`
  - `target`
  - `interactionId`
  - `textContext`
  - `timestamp`
- 只接受 `target === 'avatar'`
- 做 tool/action 白名单隔离：
  - `lollipop -> offer | tease | tap_soft`
  - `fist -> poke`
  - `hammer -> bonk`
- 做字段隔离：
  - `rewardDrop` 仅 `fist`
  - `easterEgg` 仅 `hammer`
  - `textContext` 仅在非空时透传
- 范围外视觉动作不走这个发送入口
- 在发送前判断当前会话状态，避免错误模式切换
- 在前端维护 interaction -> seedEmotion fallback timer

### 17.4 WebSocket 协议层

- 在前端发送：

```json
{
  "action": "avatar_interaction",
  "interaction_id": "...",
  "tool_id": "...",
  "action_id": "...",
  "target": "avatar",
  "text_context": "...",
  "timestamp": 0
}
```

- 在 `main_routers/websocket_router.py` 增加 `avatar_interaction` 分支
- 对未知字段保持忽略式兼容，不要因为扩展字段报错

### 17.5 Session / Core 层

- 在 `LLMSessionManager` 中新增 `handle_avatar_interaction(payload)`
- 加入 `interactionId` 去重，避免重复投递
- 增加冷却状态：
  - interaction cooldown
  - speak cooldown
- 三态分流：
  - 文本会话：直接 `prompt_ephemeral()`
  - 无会话：可自动拉起文本会话再 `prompt_ephemeral()`
  - 活跃语音会话：MVP 不说话，直接返回 skipped
- 限制回复长度与 max tokens
- 不把原始互动事件直接写成用户消息
- 生成 `memory_note`
- 将 `memory_note + assistant reply` 送入记忆归档链路

### 17.6 Prompt 与配置层

- 新增 `avatar_interaction_config.json` 或等效配置
- 配置 tool label / action label / style hint / seedEmotion
- 配置 interaction instruction 的标准系统提示前缀/后缀
- 增加 prompt builder
- `textContext` 为空时不拼接字段
- `textContext` 超长时截断
- 增加 `memory_note_builder`
- 记忆摘要统一输出简化格式，如 `[主人拍了拍你]`

### 17.7 表情层

- 前端即时 `window.applyEmotion(seedEmotion)`
- interaction 超时未触发回复时回 `neutral`
- 回复结束后继续使用 `/api/emotion/analysis`
- 不新增模型专属 expression 文件逻辑

### 17.8 记忆与日志层

- 猫娘互动回复可以进入正常记忆链路
- 交互事件本身也进入记忆，但必须先转成简化 `memory_note`
- 推荐格式：
  - `[主人拿着棒棒糖逗了逗你]`
  - `[主人拍了拍你]`
  - `[主人用锤子敲了敲你的头]`
- 高频时允许压缩：
  - `[主人连续拍了拍你]`
  - `[主人连续敲了你好几下]`
- 当前 `prompt_ephemeral()` 生成的互动回复仍会进入 `_conversation_history`
- 真正需要过滤的是：
  - 原始交互 payload
  - prompt builder 生成的系统引导词
  - 冗长的中间字段描述
- 如果要进入 memory_server，应该只进入：
  - `memory_note`
  - 猫娘自然回复
- `memory_note` 只用于记忆层，不应作为前端可见用户消息回显
- 记录结构化 debug log：
  - `interactionId`
  - `toolId`
  - `actionId`
  - `target`
  - `memory_note`
  - `delivery_result`

#### 17.8.1 按当前代码核对后的真实入库顺序

- turn end 且本轮有用户输入时，`main_logic/cross_server.py` 会把新增消息发到 `/cache/{lanlan}`
- `/cache` 只做 `recent_history_manager.update_history(..., compress=False)`，目标是让 recent history 实时可见
- 会话热重置时，若有未结算增量，则走 `/renew/{lanlan}`；若没有增量，则走 `/settle/{lanlan}`
- `/renew` 会在锁内做：
  - `recent_history_manager.update_history(..., detailed=True)`
  - `time_manager.store_conversation(...)`
- `/settle` 会在锁内做：
  - `time_manager.store_conversation(...)`
  - `recent_history_manager.update_history([], ..., detailed=True)`
- `/process` 是 session end 结算入口，也会做：
  - `update_history(...)`
  - `store_conversation(...)`
- 在 `/renew`、`/settle`、`/process` 之后，memory server 才会异步触发：
  - `fact_store.extract_facts(...)`
  - `persona_manager.record_mentions(...)`
  - `reflection_engine.check_feedback(...)`
  - `recent_history_manager.review_history(...)`
- 反思生成与 persona 提升不是每次 turn 都发生：
  - facts 先沉淀
  - 反思需要至少 `5` 条未吸收 facts
  - reflection 后续再经 `confirmed/promoted` 才进 persona

#### 17.8.2 当前项目里“相似内容”是怎么处理的

- facts 层先做第一层精确去重：
  - `memory/facts.py` 对 `fact.text` 做 SHA-256，命中同 hash 直接跳过
- 然后做第二层近似去重：
  - `memory/timeindex.py` 用 FTS5 建 `facts_fts`
  - `search_facts()` 返回 BM25 分数
  - 当前阈值是 `score < -5` 时视为重复事实并跳过
- persona 层不是“相似就覆盖”，而是先做潜在矛盾探测：
  - `memory/persona.py` 用关键词重叠率 heuristic
  - overlap ratio `>= 0.4` 时进入 correction queue
  - correction prompt 允许 `replace / keep_new / keep_old / keep_both`
  - 其中 `keep_both` 就是“主题相近但不矛盾”的真实处理分支
- recent history 层还有一层冗余清理：
  - `HISTORY_REVIEW_PROMPT` 会删除矛盾、冗余、复读和人称错误
  - 明确要求“以删除为主，非必要不改写”
- persona 主动提及时还有 suppress：
  - 同一 persona 条目在 5 小时内被 AI 提及超过 2 次会被 `suppress`
  - 这解决的是“复读疲劳”，不是 facts 写入去重

#### 17.8.3 对互动记忆的落地约束

结合上面的真实实现，互动事件要避免污染记忆，至少要满足：

- 不要把原始结构化 payload 直接喂给 memory server
- 不要把 prompt builder 产出的系统引导词写进 recent / facts / persona
- 不要把每一次高频点击都变成独立自然语言句子，否则会绕过当前 exact dedupe，制造大量“近义脏事实”
- 不要把动画阶段事件当成记忆事件：
  - 棒棒糖只记“阶段变化结果”或“连续喂食结果”
  - 猫爪只记“拍了一下”或“连续拍了拍”
  - 锤子只记“完成一次敲击结果”或“连续敲击结果”，不记 `windup/swing/recover`
- 掉落奖励、爱心特效、放大彩蛋属于附带结果：
  - 可以影响 prompt
  - 默认不单独生成记忆
  - 只有当回复文本里真的形成新的可提取事实时，才允许随猫娘自然回复一起进入 facts 管线

#### 17.8.4 推荐的互动记忆准入规则

- 应进入记忆：
  - 一次完成的、用户可感知的互动结果摘要，如 `[主人拍了拍你]`
  - 高频窗口合并后的摘要，如 `[主人连续敲了你好几下]`
  - 猫娘基于互动生成的自然回复
- 不应进入记忆：
  - 被冷却丢弃的点击
  - busy/语音模式跳过的点击
  - 无实际语义变化的连点粒子特效
  - 原始 `text_context` 草稿
  - system prefix/suffix 和中间字段说明
- 推荐顺序：
  - 先构造 `instruction`
  - 再独立构造 `memory_note`
  - 回复成功投递后，才判断 `memory_note` 是否入记忆
  - 最后随现有 `/cache -> /renew|settle|process` 管线结算

#### 17.8.5 三种类型正常点触的写入结论

- 棒棒糖 normal：
  - 可写入
  - 记“阶段结果”或“完成一次投喂”的摘要
  - 不记纯视觉切层本身
- 猫爪 normal：
  - 可写入
  - 记成单次轻互动摘要 `[主人拍了拍你]`
- 锤子 normal：
  - 可写入
  - 只在完整敲击结果成立后写
  - 不记中间动作帧
- 三者共同前提：
  - 本次互动已成功投递
  - 猫娘产生有效自然反应
  - 本次事件未被冷却、busy、语音模式跳过或去重逻辑拦下

#### 17.8.6 与现有记忆 prompt 的对齐要求

- facts 提取 prompt 已明确要求忽略：
  - 闲聊
  - 寒暄
  - 模糊内容
  - 幻觉、胡言乱语、无意义编造
- 因此互动 `memory_note` 也要写成“简短、明确、原子化”的事实锚点
- 不建议在 `memory_note` 中加入暧昧修辞、大片情绪描写或长句，否则会降低 fact extraction 的稳定性
- recent summary / review prompt 已经在控制复读和冗余，所以互动摘要应尽量复用固定模板，而不是每次随机改写

#### 17.8.7 污染风险与保护结论

- 当前项目的记忆系统不是“完全裸奔”：
  - facts prompt 会过滤闲聊、模糊内容、胡言乱语和无意义内容
  - facts 层有 exact dedupe + FTS5 近似去重
  - recent history review 会清理冗余、复读和明显矛盾
- 但这些都是下游清洗，不应当被当成上游可以随便写脏数据的理由
- 如果互动实现把原始 payload、系统提示词、高频点击明细、动画中间阶段直接送进记忆，记忆仍然会被污染
- 因此本设计的最终原则是：
  - 上游先做严格归一与过滤
  - 下游 facts/review/dedupe 只作为第二道保护
  - 不能反过来依赖下游去“捞脏数据”

### 17.9 验收标准

- 不影响现有普通文本发送
- 不影响截图/点歌/翻译按钮
- 不影响语音录制与打断逻辑
- 不出现“点击一下猫娘就切走文本/语音模式”的副作用
- 不出现“模型没回复但表情卡住”的问题
- 不出现“互动提示词被当成普通用户消息显示出来”的问题

### 17.10 基于现有代码的实现审查结论

下面这些结论是按当前仓库代码核对后的实际边界，不是理想状态假设。

#### 17.10.1 已经相对安全的点

- React 侧三种道具状态机已经稳定存在，首版只需要在现有 `pointerdown` 逻辑上补“结构化事件派发”，不需要推翻现有动画状态机
- `prompt_ephemeral()` 适合做互动回复注入，因为隐藏 instruction 不会直接进入 `_conversation_history`
- 当前 host 桥接是 setter 模式，新增 `setOnAvatarInteraction()` 与现有 `setOnComposerSubmit()` 风格一致，方向正确
- MVP 规定“活跃语音模式不说话”，能有效避开 `user_activity`、TTS、中断、`current_speech_id` 冲突

#### 17.10.2 当前仍存在的真实风险

- 记忆写入策略还没完全落地：
  - 当前互动回复会进入 `_conversation_history`
  - 但“交互事件如何以简化摘要进入记忆”还没有独立通道
  - 如果不补 `memory_note` 归一，后续实现很容易直接把原始 payload 或整段 instruction 混进记忆
  - 正确做法应是：只让简化后的 `[主人拍了拍你]` 进入记忆

- 自动拉起文本会话不是“轻量操作”：
  - 当前复用的是 `start_session(..., input_mode='text')`
  - 这会重新走记忆服务拉取、TTS 准备、session 状态重置等完整流程
  - 适合作为 MVP 兜底，但不应把它描述成低成本即时注入

- 当前前端事件边界还没完全收口：
  - `App.tsx` 仍会派发 `outside` 目标与范围外动作
  - `message-schema.ts` 仍允许 `target = outside`
  - 如果不继续收口，文档里的“业务链路只保留 avatar”就不会真正成立

- Host 接线方式已经收敛，不再保留为开放问题：
  - 继续沿用现有 setter 模式
  - 直接放进 `app-buttons.js` 既有注册区实现
  - 不新增并行初始化路径，不把 `dispatchHostEvent()` 降级成唯一业务入口

- 宿主侧目前仍未真正接线：
  - `setOnAvatarInteraction()` 已经暴露
  - 但 `app-buttons.js` 还没有把它注册到现有主链路里
  - 所以当前状态仍然是“React 能派发，宿主未正式消费”

- 未接线告警目前也还没落地：
  - `handleAvatarInteraction()` 已经存在
  - 但无 handler 时仍缺少明确 warning
  - 因此“不要静默失败”当前仍是目标要求，不是现状事实

- 事件边界也已经收敛，不再保留 `outside` 业务事件：
  - 范围外点击只保留本地视觉反馈
  - 不进入宿主 callback
  - 不进入 WebSocket / prompt / memory

#### 17.10.3 实施前必须补的保护

- 给互动记忆写入增加独立 `memory_note` 归一层，避免把原始交互控制信息带进记忆
- 给互动回复增加可识别元信息，便于后续热切换/归档时识别来源
- 对 `handle_avatar_interaction()` 做严格节流、去重、busy 检查，不能并发打进 `prompt_ephemeral()`
- 自动拉起文本会话前，必须确认当前不在活跃录音态，也不主动切断现有语音模式
- host 层在未注册 `onAvatarInteraction` 时打印明确 warning，避免静默失败

---

## 18. 后端实现伪代码草案

这一节的目标是把上面的设计收敛成可直接实现的骨架代码。

建议 MVP 的实现顺序是：

1. 读配置
2. 校验 payload
3. 解析 intensity / reward / easter egg
4. 构造 reaction config
5. 构造 persona style
6. prompt builder 生成 instruction
7. `handle_avatar_interaction()` 进行三态分流
8. 调用 `prompt_ephemeral()`

### 18.1 推荐新增模块

建议新增一个独立模块，避免把逻辑直接散落进 `core.py`：

```text
utils/avatar_interaction.py
```

该模块建议包含：

- `load_avatar_interaction_config()`
- `normalize_avatar_interaction_payload()`
- `resolve_interaction_intensity()`
- `resolve_reaction_config()`
- `resolve_persona_tone_style()`
- `build_avatar_interaction_instruction()`
- `build_avatar_interaction_memory_note()`

这样 `LLMSessionManager` 里只保留调度和状态控制，不负责具体文案拼装。

### 18.2 配置读取伪代码

```python
from functools import lru_cache
import json
from pathlib import Path

CONFIG_PATH = Path("config/avatar_interaction_config.json")

@lru_cache(maxsize=1)
def load_avatar_interaction_config() -> dict:
    if not CONFIG_PATH.exists():
        return {
            "global": {
                "interaction_cooldown_ms": 600,
                "speak_cooldown_ms": 1500,
                "text_context_max_length": 80,
            },
            "tools": {},
            "persona_tone_presets": {},
        }

    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}
```

建议配一个热重载入口：

```python
def reload_avatar_interaction_config() -> dict:
    load_avatar_interaction_config.cache_clear()
    return load_avatar_interaction_config()
```

### 18.3 Payload 规范化伪代码

建议后端不要直接信任前端字段，统一先规范化。

```python
def normalize_avatar_interaction_payload(payload: dict) -> dict | None:
    if not isinstance(payload, dict):
        return None

    allowed_actions = {
        "lollipop": {"offer", "tease", "tap_soft"},
        "fist": {"poke"},
        "hammer": {"bonk"},
    }

    tool_id = str(payload.get("tool_id", "")).strip().lower()
    action_id = str(payload.get("action_id", "")).strip().lower()
    target = str(payload.get("target", "")).strip().lower()
    interaction_id = str(payload.get("interaction_id", "")).strip()

    if tool_id not in allowed_actions:
        return None
    if action_id not in allowed_actions[tool_id]:
        return None
    if target != "avatar":
        return None
    if not interaction_id:
        return None

    text_context = str(payload.get("text_context", "") or "").strip()
    timestamp = payload.get("timestamp")
    reward_drop = bool(payload.get("reward_drop", False)) if tool_id == "fist" else False
    easter_egg = bool(payload.get("easter_egg", False)) if tool_id == "hammer" else False
    intensity = str(payload.get("intensity", "") or "").strip().lower()

    return {
        "interaction_id": interaction_id,
        "tool_id": tool_id,
        "action_id": action_id,
        "target": target,
        "text_context": text_context,
        "timestamp": timestamp,
        "reward_drop": reward_drop,
        "easter_egg": easter_egg,
        "intensity": intensity,
    }
```

### 18.4 intensity 判定伪代码

MVP 有两种可行路径：

#### 路径 A：前端直接传 `intensity`

优点：

- 后端简单
- 和前端视觉状态机最一致

缺点：

- 后端无法完全自校验

#### 路径 B：后端兜底判定

推荐做法是：

- 允许前端传 `intensity`
- 后端如果没收到，就按工具和附加标志兜底推断

伪代码：

```python
def resolve_interaction_intensity(payload: dict) -> str:
    intensity = payload.get("intensity", "")
    if intensity in {"normal", "rapid", "burst", "easter_egg"}:
        return intensity

    if payload.get("easter_egg"):
        return "easter_egg"

    tool_id = payload["tool_id"]
    action_id = payload["action_id"]

    # MVP 保守兜底：没有显式强度时都视为 normal
    # 第二阶段可接入 session 内点击窗口统计来升级到 rapid/burst
    return "normal"
```

如果后面要支持“纯后端判断高频”，建议给 `LLMSessionManager` 加一个短窗口计数器：

```python
self.avatar_interaction_recent_hits = deque(maxlen=8)
```

记录近几次点击时间戳，用时间窗口推断：

- `<= 1.2s` 内 3 次：`rapid`
- `<= 2.0s` 内 5 次：`burst`

但 MVP 不必先做，避免和前端状态定义打架。

### 18.5 reaction config 解析伪代码

```python
def resolve_reaction_config(payload: dict, config: dict) -> dict:
    tools = config.get("tools", {})
    tool_cfg = tools.get(payload["tool_id"], {})
    actions = tool_cfg.get("actions", {})
    action_cfg = actions.get(payload["action_id"], {})

    intensity = resolve_interaction_intensity(payload)
    intensity_cfg = action_cfg.get(intensity)

    if not intensity_cfg and intensity != "normal":
        intensity_cfg = action_cfg.get("normal")

    if not intensity_cfg:
        intensity_cfg = {
            "reaction_focus": "用户刚刚和你发生了一次互动，请做出自然的即时反应。",
            "style_hint": "保持简短、自然、符合角色人设。",
            "seed_emotion": "neutral",
            "reply_length": "short",
        }

    return {
        "tool_label": tool_cfg.get("label", payload["tool_id"]),
        "action_label": payload["action_id"],
        "intensity": intensity,
        "reaction_focus": intensity_cfg.get("reaction_focus", ""),
        "style_hint": intensity_cfg.get("style_hint", ""),
        "seed_emotion": intensity_cfg.get("seed_emotion", "neutral"),
        "reply_length": intensity_cfg.get("reply_length", "short"),
    }
```

### 18.5.1 interaction -> memory note 归一伪代码

建议专门补一个“写记忆前压缩交互事件”的函数。

```python
def build_avatar_interaction_memory_note(payload: dict) -> str:
    tool_id = payload["tool_id"]
    action_id = payload["action_id"]
    intensity = payload.get("intensity", "normal")

    if tool_id == "lollipop":
        if intensity in {"rapid", "burst"}:
            return "[主人连续拿棒棒糖喂你]"
        if action_id == "tease":
            return "[主人拿着棒棒糖逗了逗你]"
        return "[主人喂了你一口棒棒糖]"

    if tool_id == "fist":
        if intensity in {"rapid", "burst"}:
            return "[主人连续拍了拍你]"
        return "[主人拍了拍你]"

    if tool_id == "hammer":
        if intensity in {"rapid", "burst", "easter_egg"}:
            return "[主人连续敲了你好几下]"
        return "[主人用锤子敲了敲你的头]"

    return "[主人刚刚碰了碰你]"
```

要求：

- 用自然语言短句
- 用中括号包裹，便于后续识别与压缩
- 不写坐标、概率、彩蛋字段、工具内部 id
- 不把 `text_context` 原文拼进去
- 尽量使用固定模板，不要做大幅随机改写

### 18.5.2 interaction -> memory 准入判断伪代码

仅有 `memory_note` 还不够，还要在写入前做一层运行时过滤。

```python
def should_persist_avatar_interaction_memory(
    payload: dict,
    delivery_result: str,
    runtime_state: dict,
) -> bool:
    if delivery_result != "delivered":
        return False

    if runtime_state.get("dropped_by_cooldown"):
        return False

    if runtime_state.get("skipped_for_voice_mode"):
        return False

    if runtime_state.get("session_busy"):
        return False

    if not runtime_state.get("reply_generated", True):
        return False

    # 动画中间帧不记，只记完成后的结果事件
    if payload.get("phase") in {"windup", "swing", "recover"}:
        return False

    # 棒棒糖 normal 允许写，但应由“阶段变化结果”触发，而不是纯切图触发
    if payload.get("tool_id") == "lollipop" and payload.get("phase") == "layer_switch_only":
        return False

    # 锤子 normal 只在完整结果成立后才写
    if payload.get("tool_id") == "hammer" and not runtime_state.get("impact_completed", True):
        return False

    return True
```

再补一层短窗口合并更稳：

```python
def merge_avatar_memory_note_in_window(
    memory_note: str,
    dedupe_key: str,
    now_ms: int,
    cache: dict,
    window_ms: int = 1500,
) -> str | None:
    prev = cache.get(dedupe_key)
    if not prev:
        cache[dedupe_key] = {"note": memory_note, "ts": now_ms, "count": 1}
        return memory_note

    if now_ms - prev["ts"] > window_ms:
        cache[dedupe_key] = {"note": memory_note, "ts": now_ms, "count": 1}
        return memory_note

    prev["count"] += 1
    prev["ts"] = now_ms

    # 第二次起转成窗口摘要；也可以只在 flush 时真正写一次
    if "拍了拍你" in memory_note:
        return "[主人连续拍了拍你]"
    if "棒棒糖" in memory_note:
        return "[主人连续拿棒棒糖喂你]"
    if "敲了敲你的头" in memory_note:
        return "[主人连续敲了你好几下]"
    return None
```

这个过滤层的目标不是“绝不重复”，而是：

- 不让无意义高频点击污染 recent history
- 尽量让 facts 层拿到稳定、原子、可 dedupe 的输入
- 把相似互动先在本地窗口并掉，减轻后续 memory server 压力
- 让“三种正常点触可写入”成立在一个受控前提下：
  - 写入的是结果事件
  - 不是原始点击流水

### 18.6 persona_summary -> tone_style 解析伪代码

推荐策略：

1. 先从当前角色 prompt / 配置中提炼简短 `persona_summary`
2. 再基于关键词匹配一个 tone preset
3. 匹配不到就用默认风格

伪代码：

```python
def resolve_persona_summary(session_mgr) -> dict:
    # MVP 先从现有角色 prompt / 基础配置中取一个简短摘要
    lanlan_basic = getattr(session_mgr, "lanlan_basic_config", {}) or {}
    return {
        "baseline": str(lanlan_basic.get("personality", "") or "").strip(),
        "affection": str(lanlan_basic.get("affection_style", "") or "").strip(),
        "anger": str(lanlan_basic.get("anger_style", "") or "").strip(),
        "vulnerability": str(lanlan_basic.get("vulnerability_style", "") or "").strip(),
    }

def resolve_persona_tone_style(persona_summary: dict, config: dict) -> dict:
    presets = config.get("persona_tone_presets", {})
    baseline = " ".join(str(v) for v in persona_summary.values() if v).lower()

    if any(k in baseline for k in ["傲娇", "嘴硬", "别扭", "tsundere"]):
        return presets.get("tsundere", {})
    if any(k in baseline for k in ["软", "撒娇", "黏人", "soft"]):
        return presets.get("soft", {})
    if any(k in baseline for k in ["冷淡", "克制", "慢热", "cool"]):
        return presets.get("cool", {})
    if any(k in baseline for k in ["元气", "活泼", "闹腾", "genki"]):
        return presets.get("genki", {})

    return {
        "tone_style": "自然、贴近角色、像刚被碰到后立刻作出的口头反应",
        "affection_bias": "按角色原本人设表达喜欢",
        "anger_bias": "按角色原本人设表达不满",
    }
```

### 18.7 text_context 清洗伪代码

```python
def sanitize_text_context(text: str, config: dict) -> str:
    text = str(text or "").strip()
    max_len = int(config.get("global", {}).get("text_context_max_length", 80) or 80)
    if not text:
        return ""
    if len(text) > max_len:
        text = text[:max_len].rstrip()
    return text
```

MVP 推荐：

- 只做长度裁剪
- 不做复杂 NLP 改写
- 不把它拼成“用户正式发言”

### 18.8 Prompt Builder 伪代码

```python
def build_avatar_interaction_instruction(
    payload: dict,
    reaction_cfg: dict,
    persona_summary: dict,
    tone_cfg: dict,
) -> str:
    text_context = payload.get("text_context", "")
    reward_hint = "是" if payload.get("reward_drop") else "否"
    easter_egg_hint = "是" if payload.get("easter_egg") else "否"
    intensity_label = reaction_cfg.get("intensity", "normal")

    persona_text = "；".join(
        f"{k}:{v}" for k, v in persona_summary.items() if v
    ).strip()
    if not persona_text:
        persona_text = "保持当前角色原本人设"

    tone_style = tone_cfg.get("tone_style", "自然、即时、符合角色")
    reaction_focus = reaction_cfg.get("reaction_focus", "做出自然的即时反应")
    style_hint = reaction_cfg.get("style_hint", "保持简短自然")
    system_prefix = "======[系统通知：以下是一次刚刚发生的道具互动，请将其视为即时互动引导，不要直接复述字段名或系统描述]======"
    system_suffix = "======[系统通知结束：请直接以当前角色口吻输出即时反应]======"

    context_line = f"- 附带上下文：{text_context}\n" if text_context else ""

    reply_rule = reaction_cfg.get("reply_length", "short")
    if reply_rule == "short_plus":
        reply_constraint = "默认 1 句，最多 2 句。"
    else:
        reply_constraint = "尽量只用 1 句完成反应。"

    return f'''
{system_prefix}

你正在对主人刚刚的一次即时互动做出反应。

互动事件：
- 道具：{reaction_cfg.get("tool_label", payload["tool_id"])}
- 动作：{reaction_cfg.get("action_label", payload["action_id"])}
- 强度：{intensity_label}
- 奖励掉落：{reward_hint}
- 彩蛋爆发：{easter_egg_hint}
{context_line}角色信息：
- 当前角色摘要：{persona_text}
- 本次语气风格：{tone_style}

反应重点：
{reaction_focus}

风格要求：
{style_hint}

回复约束：
- 像被碰到后立刻说出口的反应
- 保持当前角色人设
- 只输出自然对白
- {reply_constraint}
- 不解释系统、规则、字段名

{system_suffix}
'''.strip()
```

### 18.9 `handle_avatar_interaction()` 伪代码

建议直接加在 `LLMSessionManager` 中，但把文案构造委托给 `utils/avatar_interaction.py`。

```python
async def handle_avatar_interaction(self, payload: dict) -> dict:
    raw = normalize_avatar_interaction_payload(payload)
    if not raw:
        return {"ok": False, "reason": "invalid_payload"}

    if raw["target"] != "avatar":
        return {"ok": False, "reason": "target_not_avatar"}

    # 去重
    if raw["interaction_id"] in self._recent_avatar_interaction_ids:
        return {"ok": False, "reason": "duplicate"}

    now_ms = int(time.time() * 1000)

    # interaction cooldown
    if now_ms - self._last_avatar_interaction_at < self.avatar_interaction_cooldown_ms:
        return {"ok": False, "reason": "interaction_cooldown"}

    self._last_avatar_interaction_at = now_ms
    self._recent_avatar_interaction_ids.add(raw["interaction_id"])

    config = load_avatar_interaction_config()
    raw["text_context"] = sanitize_text_context(raw.get("text_context", ""), config)

    # 活跃语音模式：MVP 跳过说话
    if self.is_active and isinstance(self.session, OmniRealtimeClient):
        return {"ok": False, "reason": "active_voice_mode"}

    # speak cooldown
    if now_ms - self._last_avatar_interaction_speak_at < self.avatar_interaction_speak_cooldown_ms:
        return {"ok": False, "reason": "speak_cooldown"}

    # 文本会话不存在则自动拉起
    if not isinstance(self.session, OmniOfflineClient):
        ws = self.websocket
        if not ws or not hasattr(ws, "client_state") or ws.client_state != ws.client_state.CONNECTED:
            return {"ok": False, "reason": "websocket_not_connected"}
        try:
            await self.start_session(ws, new=False, input_mode="text")
        except Exception as e:
            logger.warning("[%s] avatar interaction auto start text session failed: %s", self.lanlan_name, e)
            return {"ok": False, "reason": "auto_start_failed"}

    if not isinstance(self.session, OmniOfflineClient):
        return {"ok": False, "reason": "text_session_unavailable"}

    if getattr(self.session, "_is_responding", False):
        return {"ok": False, "reason": "text_session_busy"}

    reaction_cfg = resolve_reaction_config(raw, config)
    persona_summary = resolve_persona_summary(self)
    tone_cfg = resolve_persona_tone_style(persona_summary, config)
    instruction = build_avatar_interaction_instruction(raw, reaction_cfg, persona_summary, tone_cfg)
    memory_note = build_avatar_interaction_memory_note(raw)

    async with self._proactive_write_lock:
        async with self.lock:
            self.current_speech_id = str(uuid4())
            self._tts_done_queued_for_turn = False

        if hasattr(self.session, "update_max_response_length"):
            self.session.update_max_response_length(self._get_text_guard_max_length())

        delivered = await self.session.prompt_ephemeral(instruction)
        if delivered:
            self._last_avatar_interaction_speak_at = now_ms
            # 这里的 memory_note 不是展示给前端的消息，
            # 而是供记忆层 / 热切换归档使用的隐藏简化事件摘要。
            self.enqueue_avatar_interaction_memory_note(memory_note)
            return {
                "ok": True,
                "reason": "delivered",
                "interaction_id": raw["interaction_id"],
                "memory_note": memory_note,
                "seed_emotion": reaction_cfg.get("seed_emotion", "neutral"),
            }

    return {"ok": False, "reason": "empty_response"}
```

### 18.10 `websocket_router` 伪代码

```python
elif action == "avatar_interaction":
    _fire_task(session_manager[lanlan_name].handle_avatar_interaction(message))
```

如果要增强可观测性，推荐在失败时可选发一条轻量 ack：

```python
await websocket.send_json({
    "type": "avatar_interaction_ack",
    "interaction_id": interaction_id,
    "ok": False,
    "reason": "speak_cooldown"
})
```

MVP 不是必须，但如果前端要做更精细的 seedEmotion 回退控制，这个 ack 会很有帮助。

### 18.11 `LLMSessionManager` 推荐新增字段

```python
self._recent_avatar_interaction_ids: deque[str] = deque(maxlen=32)
self._recent_avatar_interaction_id_set: set[str] = set()
self._last_avatar_interaction_at: int = 0
self._last_avatar_interaction_speak_at: int = 0
self.avatar_interaction_cooldown_ms: int = 600
self.avatar_interaction_speak_cooldown_ms: int = 1500
```

如果用 `deque + set`，记得在淘汰旧 ID 时同步清理 set。

### 18.12 建议的日志点

后端至少打这几个日志点，后面排查会非常有用：

```text
[AvatarInteraction] received
[AvatarInteraction] normalized
[AvatarInteraction] skipped: target_not_avatar
[AvatarInteraction] skipped: active_voice_mode
[AvatarInteraction] skipped: interaction_cooldown
[AvatarInteraction] skipped: speak_cooldown
[AvatarInteraction] auto_start_text_session
[AvatarInteraction] instruction_built
[AvatarInteraction] delivered
[AvatarInteraction] empty_response
```

### 18.13 MVP 实现建议

如果你要最小落地，建议只先实现这些：

1. 前端 / 宿主传：
   - `interaction_id`
   - `tool_id`
   - `action_id`
   - `target = avatar`
   - `text_context`
   - `timestamp`
   - 仅携带当前道具自己的附加字段

2. 后端先固定：
   - `intensity = normal`
   - 不处理 reward / easter egg 文本升级
   - 只在文本会话下说话

3. 范围外点击继续只做前端本地反馈：
   - 不派发 `avatar-interaction`
   - 不发 WebSocket
   - 不参与 memory / prompt

4. 再第二步补：
   - `rapid / burst / easter_egg`
   - reward_drop
   - persona preset 映射优化

5. 从第一步开始就建议同时补上：
   - 标准系统提示前缀/后缀
   - `memory_note` 归一

这样能先把链路打通，再逐步变聪明。
