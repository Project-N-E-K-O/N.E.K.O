# 新手引导提示维护说明

## 目标

主页新手引导提示的目标是：

- 只在真正的新用户场景下触发
- 只在主页空闲一段时间、且没有发生强操作时触发
- 先弹出决策提示，再由用户决定是否开始引导
- 保证 `shown_count` 只在弹窗真实展示后增加
- 保证提示漏斗可追踪

## 关键文件

- `utils/tutorial_prompt_state.py`
  负责状态持久化、资格判断、token/ack、漏斗统计、配置读取
- `main_routers/system_router.py`
  暴露 `/api/tutorial-prompt/state|heartbeat|shown|decision`
- `static/app-tutorial-prompt.js`
  负责主页 heartbeat、弱/强操作采集、弹窗、前端日志
- `static/universal-tutorial-manager.js`
  负责真正启动教程，并发出 `neko:tutorial-started` / `neko:tutorial-completed`
- `config/tutorial_prompt_config.json`
  负责提示阈值配置

## 配置项

配置文件：`config/tutorial_prompt_config.json`

- `min_prompt_foreground_ms`
  主页空闲多长时间后才允许弹提示
- `later_cooldown_ms`
  用户点击“稍后再说”后的冷却时间
- `failure_cooldown_ms`
  用户点击“开始引导”但教程启动失败后的冷却时间
- `max_prompt_shows`
  最多展示多少次提示

如果配置缺失，会回退到默认值。

如果配置非法，会被 clamp：

- `min_prompt_foreground_ms`: 15s 到 12h
- `later_cooldown_ms`: 5min 到 30d
- `failure_cooldown_ms`: 1min 到 7d
- `max_prompt_shows`: 1 到 10

## 状态字段

持久化文件：`tutorial_prompt.json`

关键字段如下：

- `foreground_ms`
  自上次弱操作后累计的主页前台停留时间
- `home_interactions`
  弱操作累计次数
- `last_weak_home_interaction_at`
  最近一次弱操作时间
- `chat_turns`
  强操作之一，表示已经真正发送过用户内容
- `voice_sessions`
  强操作之一，表示已经真正开启过语音会话
- `manual_home_tutorial_viewed`
  强操作之一，表示用户手动启动过主页教程
- `home_tutorial_completed`
  表示主页教程已完成
- `active_prompt_token`
  当前待确认提示的 token，仅内部使用
- `last_acknowledged_prompt_token`
  最近一个已 ack 的提示 token，仅内部使用
- `shown_count`
  提示实际展示次数
- `accepted_at`
  用户点击“开始引导”的时间
- `started_at`
  教程真正开始的时间
- `started_via_prompt`
  该次教程是否由空闲提示启动
- `completed_at`
  教程完成时间
- `status`
  当前状态，常见值为 `observing / prompted / deferred / started / completed / never / error`
- `funnel_counts`
  漏斗统计，包含 `issued / shown / later / never / accept / started / completed / failed`

## 强弱操作规则

### 强操作

以下行为会直接阻断后续提示：

- 发消息
- 开语音
- 手动启动主页教程
- 完成主页教程

### 弱操作

以下行为不会永久阻断提示，只会重置空闲计时：

- 点击主页按钮/链接/交互入口
- 输入框聚焦
- 控件切换与变更

当前实现里，弱操作会：

1. 前端累计 `home_interactions_delta`
2. 后端增加 `home_interactions`
3. 将 `foreground_ms` 重置为 0
4. 等待下一轮空闲时间重新累计

## 状态流转

### 1. Heartbeat

前端定时调用 `/api/tutorial-prompt/heartbeat`，上报：

- `foreground_ms_delta`
- `home_interactions_delta`
- `chat_turns_delta`
- `voice_sessions_delta`
- `manual_home_tutorial_viewed`
- `home_tutorial_completed`

后端根据状态判断是否应该提示。

如果满足条件：

- 生成或复用 `prompt_token`
- 增加 `funnel_counts.issued`
- 返回 `should_prompt = true`

### 2. Shown Ack

前端只有在弹窗真正渲染后，才调用 `/api/tutorial-prompt/shown`。

后端收到后：

- `shown_count += 1`
- 写入 `last_shown_at`
- 清掉 `active_prompt_token`
- 增加 `funnel_counts.shown`

同一个 token 重复 ack 不会重复计数。

### 3. Decision

前端调用 `/api/tutorial-prompt/decision`，决策有三种：

- `later`
- `never`
- `accept`

如果 `shown` ack 丢了，但用户已经做出了 decision，后端会在 decision 阶段自动补 ack。

对应漏斗：

- `later` -> `funnel_counts.later`
- `never` -> `funnel_counts.never`
- `accept` -> `funnel_counts.accept`
- `accept + started` -> `funnel_counts.started`
- `accept + failed` -> `funnel_counts.failed`

### 4. Tutorial Events

真正的教程启动与完成，靠前端事件串起来：

- `neko:tutorial-started`
- `neko:tutorial-completed`

只有当 `neko:tutorial-started` 的 `source === 'idle_prompt'` 时，才表示这是一次由空闲提示转化出来的教程启动。

完成时，如果 `started_via_prompt = true`，后端会增加 `funnel_counts.completed`。

## shown_count 与 ack 机制

`shown_count` 的含义不是“后端发出去过几次提示”，而是“用户真实看到过几次提示”。

因此：

- `heartbeat` 只负责下发 `prompt_token`
- `shown` 才负责真正增加 `shown_count`
- `decision` 会在必要时补做一次 ack

这套机制是为了避免：

- 后端认为提示已经展示，但前端实际上没有渲染成功
- 网络抖动导致 ack 丢失，`shown_count` 和真实展示不一致

## 前端日志

浏览器控制台统一使用前缀：

- `[TutorialPromptFlow]`

关键日志节点：

- `heartbeat`
- `prompt-open`
- `shown`
- `decision`
- `tutorial-started`
- `tutorial-completed`
- `strong-action`
- `weak-action`

排查问题时，优先按同一个 `prompt_token` 的短前缀串日志。

## 常见排查路径

### 为什么没弹？

优先看：

1. `heartbeat` 日志中的 `reason`
2. `tutorial_prompt.json` 里的 `status`
3. 是否已经出现强操作
4. 是否被弱操作重置了空闲时间
5. 是否命中 `deferred_until` 或 `max_prompt_shows`

### 为什么弹了但没开始？

优先看：

1. `decision` 是否是 `accept`
2. 是否收到 `neko:tutorial-started`
3. `decision.result` 是 `started` 还是 `failed`
4. `last_error` 是否记录了失败原因

### 为什么 shown_count 不对？

优先看：

1. 是否真的触发了 `shown`
2. 是否重复 ack 了同一个 token
3. 是否是 `decision` 阶段自动补 ack

## 兼容说明

当前新逻辑主文件已经迁到 `utils/tutorial_prompt_state.py`。

旧文件 `utils/autostart_prompt_state.py` 仅作为兼容层保留，避免历史引用立即失效。新代码请优先引用：

- `utils.tutorial_prompt_state`
