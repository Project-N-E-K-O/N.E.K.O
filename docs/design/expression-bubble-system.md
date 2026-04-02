# 表情气泡系统设计评估与落地方案

## 1. 文档目的

本文档不是纯概念草案，而是基于当前项目实际代码结构，对“模型旁边的轻量表情气泡”功能做一次**可落地的设计评估**。

目标有两个：

- 先判断项目现在已经具备哪些基础能力、还缺哪些关键环节
- 在不重写现有聊天、TTS、表情、字幕链路的前提下，给出一条最小侵入的接入方案

结论先写在前面：

- 这个功能可以以前端附加表现层的方式接入
- 首版不需要新增后端接口
- 当前前端主链路已经基本接通，已进入“Phase 2 完成，Phase 3 部分收口”的状态
- 已落地独立气泡模块、模拟气泡框资源、五种 emotion 主题、turn/speech 生命周期事件、Live2D 统一边界接口
- 当前主要剩余工作已经从“前端主链路搭建”转向“多模型回归、视觉细化与验收收口”

---

## 2. 基于实际项目核对后的结论

### 2.1 当前项目已经具备的能力

1. **AI 回复文本流和聊天气泡链路已经稳定存在**
   - `static/app-websocket.js`
   - `static/app-chat.js`

2. **回合结束后的情绪分析和模型表情切换已经存在**
   - `static/app-websocket.js` 在 `system / turn end` 分支里汇总完整文本并触发情绪分析
   - `static/app-buttons.js` 暴露 `window.analyzeEmotion()` 和 `window.applyEmotion()`
   - `static/live2d-init.js` 中的 `window.LanLan1.setEmotion()` 已经会根据当前激活模型类型分发到 Live2D / VRM / MMD

3. **三套模型并存和切换已经落地**
   - Live2D: `static/live2d-*.js`
   - VRM: `static/vrm-*.js`
   - MMD: `static/mmd-*.js`
   - 切换逻辑: `static/app-character.js`

4. **统一设置面板和本地持久化已经存在**
   - 入口和 UI: `static/avatar-ui-popup.js`
   - 运行时状态: `static/app-state.js`
   - 本地保存与恢复: `static/app-settings.js`

5. **对话设置不仅写 localStorage，还会同步到后端**
   - 前端接口: `GET/POST /api/config/conversation-settings`
   - 路由: `main_routers/config_router.py`
   - 白名单和校验: `utils/preferences.py`

6. **模型屏幕边界能力已经部分存在**
   - VRM: `static/vrm-manager.js#getModelScreenBounds()`
   - MMD: `static/mmd-manager.js#getModelScreenBounds()`
   - Live2D: 目前没有统一同名接口，但已有大量 `model.getBounds()` 的使用

### 2.2 当前已经补齐的能力

以下能力已在当前工作区实现：

1. 已有独立的“表情气泡表现层模块”
   - `static/avatar-reaction-bubble.js`
   - `static/css/avatar-reaction-bubble.css`

2. 已有统一的 assistant turn / speech 生命周期事件
   - `neko-assistant-turn-start`
   - `neko-assistant-turn-end`
   - `neko-assistant-emotion-ready`
   - `neko-assistant-speech-start`
   - `neko-assistant-speech-end`
   - `neko-assistant-speech-cancel`

3. `app-state.js`、`app-settings.js`、`avatar-ui-popup.js` 已接入气泡开关字段
   - `avatarReactionBubbleEnabled`

4. Live2D 已补齐与 VRM / MMD 对齐的 `getModelScreenBounds()` 接口

5. 切角色、中断、丢弃回复、会话结束时，已有面向气泡层复用的统一清理事件

### 2.3 当前仍需注意的缺口

1. 文档里的“首版仅占位样式、暂不接 emotion 主题”已经过时，当前实现已经包含模拟气泡框资源和五种 emotion 主题
2. `avatarReactionBubbleEnabled` 已补入 `utils/preferences.py` 服务端对话设置白名单，设置全链路已闭环
3. 多模型人工回归和最终验收记录仍需补充，避免文档误判为已完全收尾

---

## 3. 实际项目里的真实链路

当前实现不是抽象意义上的“turn-start / speech-start / speech-end”系统，而是下面这条真实链路：

### 3.1 文本流入口

`static/app-websocket.js`

- 收到 `gemini_response`
- 将 `response.text` 交给 `window.appendMessage(...)`
- 如果服务端带了 `response.turn_id`，前端会写入 `window.realisticGeminiCurrentTurnId`

### 3.2 本轮完整文本缓存

`static/app-chat.js`

- 在 `appendMessage(text, 'gemini', isNewMessage)` 内维护 `window._geminiTurnFullText`
- 同时维护当前回合的聊天气泡引用 `window.currentTurnGeminiBubbles`

### 3.3 回合结束后的情绪分析

`static/app-websocket.js` + `static/app-buttons.js` + `main_routers/system_router.py`

- 收到 `response.type === 'system' && response.data === 'turn end'`
- flush 残余缓冲
- 从 `window._geminiTurnFullText` 或当前聊天气泡里取完整文本
- 先剥离 `[play_music:...]` 这类控制指令，再调用 `window.analyzeEmotion(fullText)`
- `window.analyzeEmotion()` 会请求 `POST /api/emotion/analysis`
- 后端当前要求模型只返回 `happy | sad | angry | neutral | surprised`
- 后端若 `confidence < 0.3`，会把结果自动降级成 `neutral`
- 前端拿到结果后调用 `window.applyEmotion(emotion)`

这部分有两个和设计直接相关的现实约束：

- 当前主对话页分析的是 **AI 本轮回复文本**，不是用户输入
- `thinking` 只是气泡表现层阶段态，不是现有后端会返回的 emotion label

### 3.4 实际说话链路

`static/app-websocket.js` + `static/app-audio-playback.js`

- `audio_chunk` 只代表音频分片头信息到达
- 实际 Blob 解码、排队、调度、播放都在 `static/app-audio-playback.js`
- 口型同步的启停也在 `static/app-audio-playback.js`
- 当前可以通过“第一段音频进入调度”和“最后一个 source.onended 且队列为空”推导出 speech start / speech end

### 3.5 真实中断链路

以下分支已经会打断当前输出，后续必须补充给气泡系统：

- `response.type === 'user_activity'`
- `response.type === 'response_discarded'`
- `response.type === 'session_ended_by_server'`
- 切角色 / 切模型时的 `static/app-character.js`

### 3.6 一个重要现实约束

当前项目**没有统一的“AI 开始思考”事件**。

这意味着：

- 如果要实现“真正早于首个 token 的 thinking 气泡”，需要新增一条显式事件来源
- 仅靠现在的代码，最稳妥的 turn-start 触发点是“收到本轮第一条 `gemini_response`”
- 文本模式下也可以在用户点击发送后做乐观显示，但语音模式仍然缺统一起点

因此首版建议：

- 把 `gemini_response` 的首条消息作为 turn-start
- 如果后续要追求“用户刚说完就显示 thinking”，再补更早的信号

---

## 4. 现有可直接复用的能力

### 4.1 设置入口

对话设置侧边面板当前由 `static/avatar-ui-popup.js` 生成，`createChatSettingsSidePanel()` 里已经有：

- `mergeMessages`
- `focusMode`

新增“表情气泡”开关时，应该直接扩展这组 toggle，而不是新做一个单独弹窗。

### 4.2 设置保存链路

新增设置项不能只改 localStorage。

当前项目的对话设置保存链路是：

1. `static/app-state.js` 增加状态字段
2. `static/app-settings.js`
   - `saveSettings()`
   - `loadSettings()`
   - `getConversationSettings()`
3. `/api/config/conversation-settings`
4. `utils/preferences.py`
   - `_ALLOWED_CONVERSATION_SETTINGS`
   - 对应字段类型校验

如果只改前端，不改 `utils/preferences.py`，服务端同步备份时会丢字段。

如果希望这个开关在头像设置弹窗里也能即时同步显示，还要顺手检查这些“镜像同步”文件：

- `static/avatar-ui-popup-config.js`
- `static/avatar-ui-drag.js`
- `static/live2d-ui-drag.js`

### 4.3 情绪结果复用

这部分可以直接复用，不需要另起一套情绪分析：

- `window.analyzeEmotion(fullText)`
- `window.applyEmotion(emotion)`

并且要注意一个实际项目细节：

- `window.applyEmotion()` 本身只是转调
- 真正的跨模型分发在 `static/live2d-init.js` 的 `window.LanLan1.setEmotion()`
- 它已经根据 `lanlan_config.model_type` / `lanlan_config.live3d_sub_type` 兼容 Live2D、VRM、MMD

所以新气泡主题完全可以复用当前 emotion label，不必单独发明另一套模型类型判断。

### 4.4 模型定位能力

当前最接近统一锚点能力的是：

- VRM: `getModelScreenBounds()`
- MMD: `getModelScreenBounds()`
- Live2D: `model.getBounds()`

因此最合理的落地方式不是在气泡模块里写三套定位逻辑，而是补一个统一适配层：

- `Live2DManager.prototype.getModelScreenBounds()`
- `getActiveAvatarBubbleAnchor()`

### 4.5 主动搭话气泡参考

`static/app-proactive.js` 可以参考，但只能参考两点：

- 附加 UI 与聊天正文分离
- 生命周期独立管理

不建议直接复用其 DOM 和逻辑，因为它是聊天附件气泡，不是模型旁边的悬浮表现层。

---

## 5. 当前文档里需要修正的地方

这部分是对原设想和实际项目差异的修正。

### 5.1 `turn-start` 不建议放在 `app-chat.js`

原思路里把 turn-start 放在 `app-chat.js`，但按当前项目结构，**更适合放在 `app-websocket.js`**：

- `app-websocket.js` 能直接拿到 `response.turn_id`
- `app-chat.js` 只拿到文本和 `isNewMessage`
- turn 生命周期原本也主要由 WebSocket 消息驱动

建议：

- `gemini_response && isNewMessage === true` 时，由 `app-websocket.js` 派发 `neko-assistant-turn-start`

### 5.2 `speech-start` / `speech-end` 应从音频调度层推导

当前项目里没有服务端直接发这两个事件，因此不应在更高层“猜”。

建议：

- `speech-start` 由 `static/app-audio-playback.js` 在第一段音频实际进入播放调度时发出
- `speech-end` 由最后一个 `source.onended` 且队列清空时发出

### 5.3 turnId 的真实来源要按现状设计

当前只有 `gemini_response` 会带 `response.turn_id`，而 `system / turn end` 并不带 turnId。

因此需要：

- 在 turn-start 时缓存当前回合 ID
- 之后 `emotion-ready` / `speech-end` / `speech-cancel` 都复用这份缓存
- 如果没有拿到服务端 turnId，再用前端自增 ID 兜底

### 5.4 当前项目里的模型类型不是简单三选一

实际项目使用的是：

- `model_type = live2d`
- `model_type = live3d`
- `live3d_sub_type = vrm | mmd`

因此气泡模块不应该只判断 `live2d | vrm | mmd` 三个独立顶层值，而要兼容现有配置结构。

### 5.5 现有 WebSocket 文档不是当前实现的准确来源

项目中的 `docs/api/websocket/message-types.md` 还是偏旧的抽象版本。

这次功能设计应以实际前端处理链为准，即：

- `gemini_response`
- `audio_chunk`
- `user_activity`
- `response_discarded`
- `system / turn end`

并且要特别注意：

- 文档里虽然还能看到 `emotion` WebSocket message
- 但当前主对话页的表情切换实际不是靠这条消息驱动
- 主页面现状是 `turn end -> REST 情绪分析 -> applyEmotion`
- 因此气泡系统不应等待一条“服务端主动推送的 emotion 消息”再工作

---

## 6. 推荐架构

### 6.1 当前已落地的模块

当前实现已经采用独立表现层模块：

- `static/avatar-reaction-bubble.js`
- `static/css/avatar-reaction-bubble.css`

职责只做三件事：

1. 管理显示/隐藏与状态机
2. 管理内容、主题、淡入淡出
3. 管理锚点定位和跟随

不要把这套逻辑塞进：

- `app-chat.js`
- `app-websocket.js`
- 任意一个模型 manager

这些模块只负责**派发事件或提供接口**。

### 6.2 推荐的模块边界

#### `static/avatar-reaction-bubble.js`

负责：

- 创建和销毁 DOM
- 维护 `bubbleState`
- 监听 turn / emotion / speech 事件
- 向当前激活模型获取锚点
- 处理跟随、越界翻转、超时清理

不负责：

- 情绪分析
- TTS 调度
- 聊天气泡渲染
- 模型表情驱动

#### `static/app-websocket.js`

负责新增派发：

- `neko-assistant-turn-start`
- `neko-assistant-turn-end`
- `neko-assistant-emotion-ready`
- `neko-assistant-speech-cancel`

同时继续负责：

- 汇总本轮文本
- 调用现有 `window.analyzeEmotion()`
- 在情绪结果可用后派发事件，而不是在气泡模块里重复请求情绪接口

#### `static/app-buttons.js`

继续负责已有能力：

- `window.analyzeEmotion()`
- `window.applyEmotion()`

这部分不建议为了气泡功能再包装出第二套同名接口。

#### `static/app-audio-playback.js`

负责新增派发：

- `neko-assistant-speech-start`
- `neko-assistant-speech-end`

#### 模型层

负责提供统一接口：

- `getModelScreenBounds()`

---

## 7. 推荐事件设计

### 7.1 事件名

- `neko-assistant-turn-start`
- `neko-assistant-turn-end`
- `neko-assistant-emotion-ready`
- `neko-assistant-speech-start`
- `neko-assistant-speech-end`
- `neko-assistant-speech-cancel`

### 7.2 最小 detail 结构

```js
{
  turnId: 12,
  timestamp: Date.now()
}
```

带 emotion 的事件：

```js
{
  turnId: 12,
  emotion: 'happy',
  confidence: 0.82,
  timestamp: Date.now()
}
```

可选增加：

```js
{
  turnId: 12,
  source: 'gemini_response' | 'turn_end' | 'audio_playback' | 'user_activity',
  timestamp: Date.now()
}
```

### 7.3 实际推荐派发位置

#### `neko-assistant-turn-start`

文件：`static/app-websocket.js`

触发条件：

- `response.type === 'gemini_response'`
- 且 `response.isNewMessage === true`

推荐原因：

- 能拿到真实 `response.turn_id`
- 与当前回合生命周期最贴近

#### `neko-assistant-turn-end`

文件：`static/app-websocket.js`

触发条件：

- `response.type === 'system' && response.data === 'turn end'`

说明：

- 这个事件主要用于文本模式兜底
- 它不是“说完了”，而是“本轮文本结束了”
- 事件 detail 里的 `turnId` 需要复用 turn-start 时缓存的当前回合 ID，因为 `system / turn end` 本身不带 `turn_id`

#### `neko-assistant-emotion-ready`

文件：`static/app-websocket.js`

触发条件：

- `turn end` 分支中情绪分析成功返回后

detail 建议至少带：

- `turnId`
- `emotion`
- `confidence`

说明：

- 对于“低置信度被后端自动归一成 neutral”的情况，仍然应该正常派发 `emotion-ready`
- 只有超时 / 请求异常这类拿不到结果的情况，才交给气泡模块自身的超时和兜底逻辑处理

#### `neko-assistant-speech-start`

文件：`static/app-audio-playback.js`

触发条件：

- 当前回合第一段音频开始进入播放调度时，只发一次

#### `neko-assistant-speech-end`

文件：`static/app-audio-playback.js`

触发条件：

- 最后一个 `source.onended`
- 且 `scheduledSources.length === 0`
- 且 `audioBufferQueue.length === 0`

#### `neko-assistant-speech-cancel`

至少应在以下位置派发：

- `static/app-websocket.js` 的 `user_activity`
- `static/app-websocket.js` 的 `response_discarded`
- `static/app-websocket.js` 的 `session_ended_by_server`
- `static/app-character.js` 切角色前后
- `static/app-audio-playback.js` 的 `clearAudioQueue()` / `clearAudioQueueWithoutDecoderReset()`

---

## 8. 运行时状态与显示策略

### 8.1 推荐状态

```js
const bubbleState = {
  enabled: false,
  visible: false,
  turnId: 0,
  phase: 'idle',         // idle | thinking | emotion-ready | fading
  theme: 'thinking',     // thinking | happy | sad | angry | neutral | surprised | default
  emotion: null,
  emotionConfidence: 0,
  content: '',
  side: 'right',         // right | left
  anchorX: 0,
  anchorY: 0,
  shownAt: 0,
  speechStartedAt: 0,
  followRafId: 0,
  hideTimerId: 0,
  timeoutTimerId: 0
};
```

### 8.2 推荐显示逻辑

#### 首版现实方案

1. `turn-start` 时显示 `thinking`
2. `emotion-ready` 时切换主题和内容
3. `speech-start` 时保证气泡仍然可见
4. `speech-end` 时延迟淡出
5. 如果是纯文本回合，没有 `speech-start`，则在 `turn-end` 后短延迟淡出

#### 为什么这样更符合当前项目

因为当前项目：

- 文本流和音频流是分开来的
- 不是每轮都有 TTS
- `turn end` 早于“最后一段音频播放结束”
- 如果只盯 `turn end`，语音回复时会过早消失
- 如果只盯 `speech-end`，纯文本回合会无法结束

### 8.3 推荐时间参数

```js
const BUBBLE_TIMING = {
  minVisibleMs: 360,
  fadeDurationMs: 220,
  maxThinkingMs: 10000,
  textOnlyHoldMs: 600,
  speechEndHoldMs: 360
};
```

### 8.4 内容策略

当前实现仍使用本地预置表，不接远程表情包，但已经不是“只有占位态”的版本，而是：

- `thinking` 阶段使用独立预置内容
- `happy | sad | angry | neutral | surprised` 五种 emotion 均有独立主题
- 当前已有独立悬浮表现层，并已接入模拟气泡框资源

```js
const AVATAR_REACTION_PRESETS = {
  thinking: ['...', '⋯', '｡･ω･｡'],
  happy: ['^_^', '(>w<)', '(*^▽^*)', '✨'],
  sad: ['QAQ', '(；ω；)', 'T_T'],
  angry: ['(╬ Ò﹏Ó)', '>_<', '(▼皿▼#)'],
  neutral: ['...', '(=^-ω-^=)'],
  surprised: ['!?', 'Σ( ° △ °|||)']
};
```

约束：

- 单条内容建议控制在 1 到 8 个可见字符
- 同一回合只随机一次
- 主题缺图时回退 `default`
- 收到未知 emotion label 时回退 `neutral` 或 `default`
- `thinking` 只用于气泡自身，不传给 `window.applyEmotion()`

---

## 9. 模型锚点设计

### 9.1 统一接口

建议补一个统一入口：

```js
function getActiveAvatarBubbleAnchor() {
  // return {
  //   type: 'live2d' | 'vrm' | 'mmd',
  //   bounds: { left, right, top, bottom, width, height, centerX, centerY },
  //   anchorX,
  //   anchorY
  // }
}
```

### 9.2 各模型实现建议

#### Live2D

当前没有 `getModelScreenBounds()`，建议补到 `Live2DManager` 上。

可以基于当前大量已有的：

- `model.getBounds()`

输出结构与 VRM / MMD 对齐。

#### VRM

直接复用：

- `static/vrm-manager.js#getModelScreenBounds()`

#### MMD

直接复用：

- `static/mmd-manager.js#getModelScreenBounds()`

### 9.3 当前激活模型判定

建议同时参考：

- `window.lanlan_config.model_type`
- `window.lanlan_config.live3d_sub_type`
- 容器可见性
  - `#live2d-container`
  - `#vrm-container`
  - `#mmd-container`

不要只靠某一个全局变量。

### 9.4 默认锚点

建议：

- `anchorX = bounds.right - bounds.width * 0.12`
- `anchorY = bounds.top + bounds.height * 0.18`

默认显示在右上方，越界后翻到左侧。

---

## 10. 文件改动建议

### 10.1 已新增文件

- `static/avatar-reaction-bubble.js`
- `static/css/avatar-reaction-bubble.css`

注：

- `static/icons/reaction-bubble/` 目前不是现状必需项
- 当前主题主要由 CSS 和文本预置驱动，尚未依赖单独图标资源目录

### 10.2 现有文件改动点

### `templates/index.html`

- 已引入新的 CSS
- 已引入新的 JS
- 当前加载方式满足“能读取设置，且不晚于统一初始化”的目标

### `static/app-state.js`

已新增：

- `avatarReactionBubbleEnabled`

如需后续调试可选新增：

- `avatarReactionBubbleDebug`

并同步检查：

- `window` 双向绑定的 key 列表，确保新开关能和全局变量联动

### `static/app-settings.js`

已更新：

- `saveSettings()`
- `loadSettings()`
- `getConversationSettings()`
- 本地默认值

当前状态：

- 服务端白名单已同步补齐
- `getConversationSettings()` 发出的 `avatarReactionBubbleEnabled` 不会再被服务端过滤

### `static/avatar-ui-popup.js`

已接入这一个新 toggle：

- `avatar-reaction-bubble`

具体包括：

- `createChatSettingsSidePanel()` 的 `chatToggles`
- `_createSettingsToggleItem()` 里的初始勾选状态
- `handleToggleChange()` 里的保存与联动逻辑

### `static/avatar-ui-popup-config.js`

当前已补充：

- popup show 时的 checkbox 同步逻辑

### `static/avatar-ui-drag.js` / `static/live2d-ui-drag.js`

当前工作区里尚未看到这两处与气泡开关直接相关的同步改动，因此这里仍保留为待确认项：

- 新 toggle 的视觉状态同步

### `static/locales/*.json`

已新增文案键，例如：

- `settings.toggles.avatarReactionBubble`

### `utils/preferences.py`

已新增字段到：

- `_ALLOWED_CONVERSATION_SETTINGS`
- 布尔校验字段集合

### `static/app-websocket.js`

已新增派发：

- `turn-start`
- `turn-end`
- `emotion-ready`
- `speech-cancel`

同时需要：

- 在 `gemini_response + isNewMessage` 时缓存/派发 turn-start
- 在 `turn end` 时把缓存的 turnId 带给 turn-end
- 在 emotion REST 返回后派发 `emotion-ready`
- 不要改动现有 `window.applyEmotion()` 调用链

### `static/app-audio-playback.js`

已新增派发：

- `speech-start`
- `speech-end`
- `speech-cancel`（建议放在清队列 helper 里）

### `static/app-character.js`

切角色、切模型或强制清理时，当前已发：

- `speech-cancel`

### `static/live2d-core.js` 或 `static/live2d-model.js`

已补：

- `getModelScreenBounds()`

---

## 11. 兼容性和防回归要求

必须满足：

- 默认关闭
- 开关关闭时不创建持续定位循环
- 不写入聊天区 DOM
- 不影响现有 `window.applyEmotion()` 行为
- 不修改当前音频调度和口型同步逻辑，只补事件派发
- 模块加载失败时静默降级
- 旧回合事件必须丢弃
- 切角色、中断、丢弃回复、会话结束时必须清理残留

### 11.1 必须处理的异常

- 没有 `emotion` 结果
- `emotion` 请求超时或异常
- 后端因低置信度自动回退 `neutral`
- 当前回合没有 TTS
- 当前没有可见模型
- 主题图片缺失
- `turn end` 到达但 turnId 缺失
- 旧回合迟到的 `speech-end`
- 设置只写入前端但没有进入服务端白名单

当前实现已覆盖或部分覆盖：

- 当前回合没有 TTS
- 当前没有可见模型
- 旧回合迟到的 `speech-end`
- 长回合 thinking 超时清理

当前仍需单独核实：

- 多模型切换后的全链路人工回归
- 图片资源接入后的实际视觉效果与移动端布局

---

## 12. 分阶段实现建议

### Phase 1: 最小可见版本

目标：

- 先把附加表现层和开关链路跑通

范围：

- 新增气泡模块和 CSS
- 新增设置项
- `turn-start -> thinking -> fade out`
- Live2D / VRM / MMD 的基础定位

约束：

- 先用纯 CSS 边框或占位样式
- 暂不接真实 emotion 主题

当前状态：

- 已完成，且实现已超过本阶段约束
- 当前版本已经不是“只有 thinking 占位态”，而是已接入模拟气泡框和真实 emotion 主题

### Phase 2: 接入真实生命周期

目标：

- 补齐 emotion 和 speech 链路

范围：

- 接入 `emotion-ready`
- 接入 `speech-start / speech-end / speech-cancel`
- 处理 turnId 归属
- 补齐异常清理

当前状态：

- 前端链路已完成
- `turn-start / turn-end / emotion-ready / speech-start / speech-end / speech-cancel` 均已接通
- turnId 已按“服务端 turnId 优先，前端自增兜底”处理
- `response_discarded`、`user_activity`、`session_ended_by_server`、切角色清理链路已接入
- 若以“前端表现层是否完成”为标准，Phase 2 可视为完成
- 设置全链路含后端持久化现已补齐

### Phase 3: 视觉和回归收口

目标：

- 替换正式资源并完成多模型回归

范围：

- 主题图替换
- 越界翻转和移动端适配
- 快速短回复防闪烁
- 长回合超时清理

当前状态：

- 已部分进入本阶段
- 越界翻转、移动端适配、长回合超时清理已有实现
- 主题图资源替换与系统化回归验证仍未在文档中闭环

---

## 13. 验收清单

- `gemini_response` 首条到达后可以显示 thinking 气泡
- `turn end` 后 emotion 返回时，气泡能切到对应主题
- Live2D、VRM、MMD 都能正确取锚点
- 语音回复时，气泡不会早于实际播报结束而消失
- 纯文本回复时，气泡也能正常结束
- `response_discarded` 不会留下残影
- `user_activity` 打断时不会留下残影
- 切角色后不会残留旧气泡
- 设置关闭时完全不显示，且不持续占用 RAF
- 页面刷新后开关状态正确恢复
- 不影响原有聊天区、字幕、表情切换、主动搭话附件

### 13.1 回归清单

建议按下面这组顺序做人工回归，优先覆盖 Phase 3 最容易出问题的视觉细节：

1. 短回复闪烁
   - 触发一条极短回复，确认 `thinking` 不会一闪而过立刻切图
   - 确认 emotion 图切入时没有“刚出现就立刻消失”的突兀感

2. 纯文本回合收尾
   - 关闭或跳过 TTS，确认 `turn end` 后气泡会正常延迟淡出
   - 确认没有 lingering 残影

3. 语音回合收尾
   - 让回复带 TTS，确认气泡持续到实际播报结束后再退出
   - 确认 `speech-end` 早到或迟到时不会误清掉新回合气泡

4. 左右侧翻转
   - 把模型拖到靠左、靠右位置，确认气泡会切到另一侧
   - 确认尾巴朝向模型，不是只在原地镜像

5. 头部锚点贴合
   - 检查气泡是否贴近头部附近，而不是贴整个人物外接框
   - 对比 Live2D、VRM、MMD 三种模型是否都保持在头部上侧区域

6. 桌面端居中
   - 分别检查左侧和右侧时，表情图与 `。。。` 是否都在可见主体内居中
   - 确认图片资源尺寸不会过小或撑出主体框

7. 移动端适配
   - 在窄屏下确认气泡不会越界
   - 确认 `thinking` 和 emotion 图都仍然可读

8. 跟随稳定性
   - 缓慢移动模型或切换模型姿态，确认气泡不会高频抖动
   - 确认尺寸不会随着边界微小抖动频繁跳变

9. 中断清理
   - 触发 `user_activity`
   - 触发 `response_discarded`
   - 切角色
   - 结束会话
   - 以上场景都确认不会残留旧气泡

---

## 14. 最终建议

从当前项目结构看，这个功能最稳妥的做法是：

- 复用现有设置体系
- 复用现有 emotion 分析结果
- 复用现有模型边界能力
- 新增一个独立的前端表现层模块

不建议把气泡系统直接塞进聊天模块、WebSocket 模块或任一模型 manager。

按当前代码现状，更准确的说法是：

1. 前端独立模块、五种 emotion 主题和真实生命周期已经接通
2. 当前剩余重点是做多模型回归、视觉细化与验收收口
3. 如需继续推进，再考虑正式主题资源、视觉细化与验收记录沉淀

这样更符合当前工作区的真实状态，也能避免后续继续按旧草案误判进度。
