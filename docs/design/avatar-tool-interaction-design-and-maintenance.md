# Avatar 道具交互设计与维护说明

本文记录聊天紧凑模式里“展开按钮 -> Avatar tools -> 道具交互”的当前设计、真实代码链路和后续维护边界。它关注的是用户如何选择道具、点击头像、触发前端表现和后端临时回应；提示词本身的写法以 `docs/design/avatar-tool-prompt-guidelines.md` 为准。

如果本文与当前代码、测试或真实运行结果冲突，以可复现证据和当前代码为准，并先更新本文再继续改动。

## 业务理解

这个功能让用户在紧凑聊天输入区展开工具轮盘后，选择一个“拿在鼠标上”的小道具，然后对 NEKO 头像做轻量互动。

当前用户路径：

1. 打开紧凑聊天输入区的工具轮盘。
2. 点击 `Avatar tools`。
3. 在快捷道具条里选择棒棒糖、猫爪或锤子。
4. 鼠标进入道具模式，轮盘自动收起。
5. 用户用道具点击头像命中区。
6. 前端播放本地光标、动画、音效和掉落效果。
7. 宿主把交互事件发给后端。
8. 后端用一次 `avatar_interaction` 临时 prompt 让角色自然回应。

核心产品语义：

1. 道具交互是“轻量打招呼/逗一下/喂一下”，不是普通文本输入。
2. 用户选择道具后进入持续的道具光标模式，直到主动清除或切换。
3. 只有点击头像命中区才触发角色交互；点到聊天按钮、模型侧边按钮或其它 UI 不触发。
4. 前端表现和后端回应要保持同一个事件事实：用了什么道具、做了什么动作、频率或彩蛋是什么。

## 当前范围

当前已实装 3 个道具：

| 道具 | tool id | 前端动作 | 后端 action |
|---|---|---|---|
| 棒棒糖 | `lollipop` | 投喂、连续投喂、爱心飘字 | `offer` / `tease` / `tap_soft` |
| 猫爪 | `fist` | 轻戳、按下态、概率掉金币 | `poke` |
| 锤子 | `hammer` | 轻敲、挥动动画、概率彩蛋 | `bonk` |

当前不包含：

1. 任意自定义道具系统。
2. 道具背包、消耗、购买或解锁。
3. 道具拖到头像即触发；当前是选择道具后点击头像触发。
4. 非头像目标交互。
5. 语音实时会话中的道具即时回应。

## 主要代码入口

前端 React 主链路：

1. `frontend/react-neko-chat/src/App.tsx`
   - 紧凑聊天主实现。
   - 渲染 `Avatar tools` 轮盘项。
   - 管理 `activeCursorToolId`、命中判定、道具光标、pointer 事件和本地特效。
2. `frontend/react-neko-chat/src/avatarTools.ts`
   - 道具清单、默认快捷道具、图片资源、光标热点和 localStorage key。
3. `frontend/react-neko-chat/src/AvatarToolQuickbar.tsx`
   - 展开后的快捷道具条。
   - 展示已启用道具和编辑入口。
4. `frontend/react-neko-chat/src/AvatarToolItemManager.tsx`
   - 快捷道具管理弹窗。
   - 负责 3 个槽位、道具库、拖拽、保存和取消。
5. `frontend/react-neko-chat/src/message-schema.ts`
   - 前端 props 和 `AvatarInteractionPayload` / `AvatarToolStatePayload` schema。

宿主和桌面桥接：

1. `static/app-react-chat-window.js`
   - 把 React 的 `onAvatarInteraction` 和 `onAvatarToolStateChange` 接到宿主。
   - 同时派发 `neko-react-chat-window:avatar-interaction` 和 `neko-react-chat-window:avatar-tool-state` 事件。
2. `static/app-buttons.js`
   - 归一化并校验 avatar interaction payload。
   - 通过 websocket 发送 `action: "avatar_interaction"`。
   - 处理本地 seed emotion、发送节流和普通文本输入延后。
3. `static/app-websocket.js`
   - 接收后端 `avatar_interaction_ack`，转成 `neko-avatar-interaction-ack` 生命周期事件。

后端语义链路：

1. `main_routers/websocket_router.py`
   - 收到 `action == "avatar_interaction"` 后转给当前 session manager。
2. `main_logic/core.py`
   - `handle_avatar_interaction()` 负责校验、去重、冷却、会话准备、临时 prompt 和 ack。
3. `config/prompts/prompts_avatar_interaction.py`
   - 归一化 payload。
   - 构造 avatar interaction instruction 和 memory note。
4. `main_logic/cross_server.py`
   - 处理 avatar interaction turn 的记忆隔离和去重持久化。

注意：`frontend/react-neko-chat/src/FullChatSurface.tsx` 里也有旧版道具代码，但文件头标注为 frozen legacy。紧凑模式当前主链路以 `App.tsx` 为准，不要把 full surface 当作新改动入口。

## 前端交互状态

核心状态：

| 状态 | 含义 |
|---|---|
| `toolMenuOpen` | Avatar tools 快捷道具条是否展开 |
| `activeCursorToolId` | 当前是否选中某个道具作为鼠标光标 |
| `activeAvatarToolIds` | 快捷道具槽位里的道具 id 列表 |
| `avatarToolManagerOpen` | 道具管理弹窗是否打开 |
| `avatarRangeCursorVariants` | 光标在头像命中区内时的道具图变体 |
| `outsideRangeCursorVariants` | 光标在头像命中区外时的道具图变体 |
| `isCursorOverAvatarRange` | 当前光标是否在头像命中区 |
| `isCursorOverCompactCursorZone` | 当前光标是否在聊天/工具 UI 区 |
| `isCursorInsideHostWindow` | 当前光标是否仍在宿主窗口内 |

状态转换：

1. 点击工具轮盘中的 `Avatar tools`：
   - 如果没有选中道具，切换 `toolMenuOpen`。
   - 如果已经选中道具，点击会清除当前道具模式并关闭工具轮盘。
2. 点击快捷道具条中的某个道具：
   - 如果点击的是当前道具，取消选中。
   - 否则设置 `activeCursorToolId`。
   - 重置该道具的光标变体为 `primary`。
   - 用户点击选择后关闭紧凑工具轮盘。
3. 点击快捷道具条的编辑按钮：
   - 记录编辑按钮的位置作为弹窗锚点。
   - 打开 `AvatarToolItemManager`。
4. 保存道具管理弹窗：
   - 对道具 id 做 sanitize。
   - 写入 localStorage。
   - 如果当前选中道具被移出快捷槽，清除道具模式。

## 命中判定

头像命中范围来源按优先级组合：

1. 桌面端注入的 `window.__nekoDesktopAvatarBounds`。
2. `window.mmdManager.getModelScreenBounds()`。
3. `window.vrmManager.getModelScreenBounds()`。
4. `window.live2dManager.getModelScreenBounds()`。

命中规则：

1. 容器必须可见。
2. bounds 必须有正数宽高。
3. 先用 100px padding 做外框快速过滤。
4. 再用头像中心椭圆判断实际命中。
5. 命中后按点击位置划分 `touchZone`：
   - 顶部两侧：`ear`
   - 上方：`head`
   - 中部：`face`
   - 下方：`body`

排除规则：

如果点击位置落在以下交互 UI 上，即使视觉上靠近头像，也不触发道具事件：

1. 聊天输入工具区和快捷道具条。
2. Avatar tool 管理弹窗。
3. 聊天历史、发送按钮、窗口顶栏按钮。
4. Live2D / VRM / MMD 浮动按钮、返回按钮、锁定按钮、弹窗。
5. 标记为 `data-neko-sidepanel` 的侧边面板。

维护时不要只看“坐标在头像附近”。必须同时检查命中区和 UI 覆盖排除，否则会出现点按钮也触发摸头/敲头的误触。

## 三种道具行为

### 棒棒糖

前端行为：

1. 第一次命中头像：`offer`。
2. 第二次命中头像：`tease`。
3. 之后命中头像：`tap_soft`。
4. `tap_soft` 连点会记录 burst 历史，短时间内多次点击升级为 `burst`。
5. 播放 `lollipop-bite.mp3`。
6. 第三阶段后生成爱心飘字。

payload：

```json
{
  "toolId": "lollipop",
  "actionId": "offer | tease | tap_soft",
  "intensity": "normal | rapid | burst"
}
```

棒棒糖不带 `touchZone`。它的语义是投喂，不是点到头像哪个部位。

### 猫爪

前端行为：

1. 点击头像时发送 `poke`。
2. 按下时光标切到 secondary，松开后恢复 primary。
3. 根据点击位置带上 `touchZone`。
4. 短时间连点会升级为 `rapid`。
5. 命中头像时有概率设置 `rewardDrop: true`。
6. `rewardDrop` 为真时播放 `coin-drop.mp3` 并生成金币掉落效果。

payload：

```json
{
  "toolId": "fist",
  "actionId": "poke",
  "intensity": "normal | rapid",
  "touchZone": "ear | head | face | body",
  "rewardDrop": true
}
```

`rewardDrop` 只对猫爪有效。不要把它泛化成所有道具通用字段。

### 锤子

前端行为：

1. 点击头像时发送 `bonk`。
2. 点击头像外只做短暂 outside cursor 变体反馈，不发送后端事件。
3. 命中头像且当前没有挥动动画时，进入 `windup -> swing -> impact -> recover -> idle` 动画阶段。
4. 短时间连敲会升级为 `rapid` 或 `burst`。
5. 命中时有小概率设置 `easterEgg: true`。
6. 普通敲击播放 `hammer-small.mp3`，彩蛋播放 `hammer-big.mp3`。

payload：

```json
{
  "toolId": "hammer",
  "actionId": "bonk",
  "intensity": "normal | rapid | burst | easter_egg",
  "touchZone": "ear | head | face | body",
  "easterEgg": true
}
```

锤子有本地挥动动画锁。维护时不要允许动画未结束就重复进入新挥动，否则会产生视觉叠加和重复发送。

## 宿主与后端事件

React 侧发出的 `AvatarInteractionPayload` 经过两层出口：

1. `onAvatarInteraction(payload)`：交给宿主业务处理。
2. `neko-react-chat-window:avatar-interaction`：作为事件总线兼容出口。

宿主归一化后 websocket 消息格式为：

```json
{
  "action": "avatar_interaction",
  "interaction_id": "...",
  "tool_id": "lollipop | fist | hammer",
  "action_id": "...",
  "target": "avatar",
  "timestamp": 0,
  "intensity": "normal | rapid | burst | easter_egg",
  "touch_zone": "ear | head | face | body",
  "reward_drop": true,
  "easter_egg": true,
  "text_context": "...",
  "pointer": {
    "clientX": 0,
    "clientY": 0
  }
}
```

后端 `handle_avatar_interaction()` 会：

1. 调用 `_normalize_avatar_interaction_payload()` 校验字段。
2. 用 `interaction_id` 做重复过滤。
3. 用 `avatar_interaction_cooldown_ms` 做交互冷却。
4. 在语音实时会话中拒绝该事件。
5. 必要时自动启动文本会话。
6. 如果文本会话忙或说话冷却未结束，拒绝该事件。
7. 构造 avatar interaction instruction 和 memory note。
8. 调用 `prompt_ephemeral(..., persist_response=False)`。
9. 发送 `avatar_interaction_ack`。

前端收到 ack 后通过 `neko-avatar-interaction-ack` 收束生命周期。宿主还会在道具交互回应期间延后普通文本输入，避免用户刚发的普通聊天和道具回应互相打断。

## 道具光标与桌面同步

前端会对宿主派发 `AvatarToolStatePayload`，包含：

1. 是否 active。
2. 当前 `toolId`。
3. 当前光标变体。
4. 是否在头像命中区。
5. 是否在紧凑 UI 区。
6. 当前 pointer client/screen 坐标。
7. 道具图片和光标热点信息。

用途：

1. Web 端可直接设置 CSS cursor。
2. 桌面单窗口可用本地 overlay 隐藏系统 cursor 并绘制更稳定的道具图。
3. 多窗口或桌面壳可以通过 state 同步光标位置、图片和状态。

维护边界：

1. 不要只改 React 光标，不同步 `onAvatarToolStateChange` payload。
2. 新增道具图片时必须同时提供 icon、cursor、hotspot 和显示尺寸。
3. 光标 overlay 层级必须高于模型侧边按钮和模型菜单，但不能拦截 pointer 事件。

## 道具管理弹窗

`AvatarToolItemManager` 的职责是管理“快捷道具槽”，不是管理道具语义。

当前规则：

1. 最多 3 个槽位。
2. 默认槽位是 `lollipop`、`fist`、`hammer`。
3. 支持从道具库拖入槽位。
4. 支持槽位间重排。
5. 支持移除槽位道具。
6. 保存后写入 `neko.reactChatWindow.activeAvatarTools`。
7. localStorage 无效或数据非法时回退默认槽位。

不要在管理弹窗里写道具业务逻辑。新增道具时，管理弹窗应只消费 `AVAILABLE_AVATAR_TOOLS`。

## i18n 与文案

用户可见文案必须走 i18n key。

当前相关 key 包括：

1. `chat.avatarToolsButtonAriaLabel`
2. `chat.avatarToolQuickbarAriaLabel`
3. `chat.avatarToolQuickbarEmpty`
4. `chat.avatarToolEdit`
5. `chat.clearCursorToolAriaLabel`
6. `chat.toolLollipop`
7. `chat.toolFist`
8. `chat.toolHammer`
9. 道具管理弹窗标题、槽位、保存、取消、提示类 key

新增或修改用户可见文案时，同步 8 个 locale：

```text
en / es / ja / ko / pt / ru / zh-CN / zh-TW
```

不要只在 TSX 里加中文 fallback。fallback 可以保留，但规范入口应是 locale JSON。

## 新增道具流程

新增道具时按这个顺序做。

### 1. 定义产品事件

先写清楚：

1. 用户选择它后看到什么光标。
2. 点击头像时发生什么本地反馈。
3. 是否区分点到耳朵、头、脸、身体。
4. 是否有概率掉落、彩蛋或连续点击升级。
5. 后端应该收到哪些 `action_id` 和 `intensity`。

不能直接从图片或道具名推断后端事件。后端事件必须是明确业务语义。

### 2. 更新前端道具清单

修改 `avatarTools.ts`：

1. 新增 `AVAILABLE_AVATAR_TOOLS` 项。
2. 提供 icon 和 cursor 图片。
3. 配置 cursor hotspot。
4. 如需默认出现在快捷栏，更新 `DEFAULT_ACTIVE_AVATAR_TOOL_IDS`。

同时检查：

1. `message-schema.ts` 的 `avatarInteractionPayloadSchema`。
2. `AvatarToolId` 类型来源。
3. `sanitizeAvatarToolIds()` 是否允许新 id。

### 3. 更新前端交互分支

在 `App.tsx` 的 pointerdown 处理里新增该道具的行为：

1. 命中头像才发后端事件。
2. 命中 UI 覆盖区不得发事件。
3. 明确本地动画和 sound。
4. 明确是否改变 cursor variant。
5. 明确是否需要 burst history。
6. 明确是否需要 cleanup timer。

不要把新道具做成默认 fallback 分支。每个道具都应有清晰的事件语义。

### 4. 更新宿主和后端白名单

需要同步：

1. `static/app-buttons.js` 的允许 action / intensity / seed emotion。
2. `config/prompts/prompts_avatar_interaction.py` 的 allowed actions、intensity combinations、labels、reaction profiles、memory meta。
3. 必要时更新 `main_logic/core.py` 的特殊处理，但优先保持通用链路不变。

前端能发不等于后端会接受。没有后端白名单的新道具会被当成 invalid payload。

### 5. 更新测试

至少覆盖：

1. 选择新道具后进入 tool cursor active。
2. 点头像范围内会发 `onAvatarInteraction`。
3. 点头像范围外不发。
4. 点 UI 覆盖区不发。
5. payload 符合 `message-schema.ts`。
6. 后端 `_normalize_avatar_interaction_payload()` 接受新事件。
7. 8 个 locale 的 prompt 和 memory meta 可生成。
8. 如果有掉落/彩蛋/连点升级，覆盖概率或强度分支。

## 修改现有行为的检查清单

改前端表现时检查：

1. 紧凑聊天工具轮盘是否仍能打开和关闭。
2. 已选道具是否仍能清除。
3. 道具管理弹窗是否仍能保存、取消、拖拽。
4. localStorage 旧值是否仍能 sanitize。
5. 点聊天工具区不会触发头像交互。
6. Live2D / VRM / MMD 三种模型 bounds 是否仍可命中。
7. 桌面多窗口光标状态是否仍同步。

改后端语义时检查：

1. `static/app-buttons.js` 和 `config/prompts/prompts_avatar_interaction.py` 白名单一致。
2. 前端 action / intensity / flag 与 prompt 事件事实一致。
3. `avatar_interaction_ack` 的 accepted/reason 仍能让前端收尾。
4. 冷却、去重、文本会话忙、语音会话 active 的拒绝路径不被绕过。
5. `prompt_ephemeral(..., persist_response=False)` 语义不被改成普通聊天输入。
6. memory note 和 dedupe rank 仍按 `cross_server.py` 的 avatar interaction 逻辑处理。

改视觉或 CSS 时检查：

1. 道具 cursor overlay 不遮挡点击。
2. overlay z-index 高于模型菜单，但不污染普通聊天。
3. 紧凑轮盘里的 quickbar 锚点仍在轮盘原点附近。
4. 小屏或桌面边缘时弹窗不会跑出视口。
5. 文案不溢出按钮。

## 验证建议

轻量静态验证：

```bash
cd frontend/react-neko-chat
npm test -- App.test.tsx
```

提示词和记忆验证：

```bash
uv run pytest tests/unit/test_avatar_interaction_memory_contract.py
```

后端归一和链路变更后，至少补充或运行相关 websocket / avatar interaction 单元测试。涉及桌面光标、命中区、窗口外同步时，需要用真实运行或 Playwright 观察，不要只靠代码阅读判断。

## 常见误区

1. 把 `FullChatSurface.tsx` 当主实现改。紧凑模式主链路在 `App.tsx`。
2. 只加前端按钮，不加 schema 和后端白名单。
3. 只改提示词，不改前端实际发送的 action / intensity。
4. 点到聊天 UI 时也触发头像事件。
5. 忘记桌面光标状态同步，只在浏览器 CSS cursor 里看起来正常。
6. 把 `rewardDrop`、`easterEgg` 做成所有道具通用语义。
7. 为了某个坏回复在 prompt 里堆禁令，导致道具交互变模板化。
8. 为了新增道具改共享会话、普通聊天或 voice session 语义。

