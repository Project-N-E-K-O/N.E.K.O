# Day 4 悬浮窗教程：猫娘互动体验

本文按 Day 4 新手教程期间文本输出的先后顺序，记录对话设置、主动搭话、隐私模式、动画表现、锁定、离开和回来的高亮与 ghost cursor 流程。它只描述 Day 4 的文本、spotlight/highlight、ghost cursor、真实 UI 点击和场景清理；通用生命周期边界看 `home-yui-guide-lifecycle-modularization.md`，总览和跨天排期看 `avatar-floating-panel-functions.md`。

若本文与当前代码冲突，以当前代码为准。主要代码入口：

1. `static/universal-tutorial-manager.js`：Day 4 启动、状态持久化、临时切模、完成/跳过。
2. `static/yui-guide-director.js`：`AVATAR_FLOATING_GUIDE_ROUNDS[4]`、文本输出、高亮、ghost cursor、真实 UI 操作。
3. `static/avatar-floating-guide-reset.js`：首页“第 4 天”重置按钮入口。
4. `static/avatar-ui-popup.js`、`static/avatar-ui-drag.js`、`static/app-proactive.js`：设置侧边面板、主动搭话、隐私和离开/回来相关 UI。

## 介绍内容树

```text
Day 4：猫娘互动体验
├─ 相处方式开场
│  ├─ 今天不只看按钮
│  └─ 重点是让她更适合陪在旁边
├─ 对话设置
│  ├─ 合并消息
│  ├─ 允许打断
│  ├─ 表情气泡
│  └─ 回复 token 上限
├─ 主动搭话
│  ├─ 主动搭话开关
│  ├─ 最低间隔
│  ├─ 媒体凭证入口
│  └─ 屏幕 / 新闻 / 视频 / 个人动态 / 音乐 / 表情包 / 小游戏话题
├─ 隐私边界
│  ├─ 隐私模式
│  └─ 主动视觉感知间隔
├─ 动画表现
│  ├─ 画质
│  ├─ 帧率
│  ├─ 鼠标跟踪
│  └─ 锁定悬停淡化
├─ 交互边界
│  ├─ 锁定模型交互
│  ├─ 请她离开
│  └─ 请她回来
└─ 四日教程结束
   ├─ 清理所有临时 UI
   └─ 不再自动弹出 Day 2-4
```

## 当前顺序

Day 4 主线顺序来自 `AVATAR_FLOATING_GUIDE_ROUNDS[4].scenes`：

```text
day4_intro_companion
day4_chat_settings
day4_proactive_chat
day4_privacy_mode
day4_animation_tracking
day4_lock_interaction
day4_goodbye_return
day4_wrap
```

## 1. 相处方式开场

文本输出：

1. `tutorial.avatarFloating.day4.intro`
2. 语音 key 占位：`avatar_floating_day4_intro`
3. 临时文案：“最后一天，我们不只看按钮。今天讲讲怎么让我更适合陪在你旁边。”

高亮流程：

1. persistent spotlight 放到聊天窗口。
2. 不打开弹窗。

ghost cursor 流程：

1. cursor 在聊天窗口附近出现。
2. 轻微 wobble。

真实 UI 操作：

1. 无。

## 2. 对话设置

文本输出：

1. `tutorial.avatarFloating.day4.chatSettings`
2. 语音 key 占位：`avatar_floating_day4_chat_settings`
3. 临时文案：“如果你想让我少刷屏、可以被打断、或者多一点表情反馈，就看这里。”

高亮流程：

1. action spotlight 放到 `#${p}-btn-settings`。
2. cursor click 打开设置弹窗。
3. persistent spotlight 切到 `#${p}-popup-settings`。
4. action spotlight 放到对话设置菜单项。
5. 展开 `data-neko-sidepanel-type="chat-settings"`。
6. virtual spotlight 依次标记合并消息、允许打断、表情气泡、回复 token 上限滑条。

ghost cursor 流程：

1. cursor 点击设置按钮。
2. cursor 移动到对话设置入口并 hover / 展开侧边面板。
3. cursor 逐项移动。
4. 不点击开关，不拖动滑条。

真实 UI 操作：

1. 打开设置弹窗。
2. 展开对话设置侧边面板。
3. 不修改设置。

清理：

1. 收起对话设置侧边面板或调用侧边面板互斥收起。

## 3. 主动搭话

文本输出：

1. `tutorial.avatarFloating.day4.proactiveChat`
2. 语音 key 占位：`avatar_floating_day4_proactive_chat`
3. 临时文案：“主动搭话决定我要不要偶尔找你说话，也能选择我从哪里找话题。”

高亮流程：

1. action spotlight 放到主动搭话主开关。
2. 展开 `data-neko-sidepanel-type="interval-proactive-chat"`。
3. virtual spotlight 依次标记最低间隔、媒体凭证入口、屏幕分享话题、新闻、视频、个人动态、音乐、表情包、小游戏邀请。

ghost cursor 流程：

1. cursor 移动到主动搭话主开关。
2. 不点击主开关。
3. cursor 移动到最低间隔滑条，不拖动。
4. cursor 移动到媒体凭证入口，不点击跨页入口。
5. cursor 逐项扫过搭话方式。

真实 UI 操作：

1. 展开主动搭话侧边面板。
2. 不修改开关和滑条。

清理：

1. 收起主动搭话侧边面板。

## 4. 隐私模式

文本输出：

1. `tutorial.avatarFloating.day4.privacyMode`
2. 语音 key 占位：`avatar_floating_day4_privacy_mode`
3. 临时文案：“隐私模式打开时，我不会主动看屏幕。你可以把边界设得很清楚。”

高亮流程：

1. action spotlight 放到隐私模式开关。
2. 展开 `data-neko-sidepanel-type="interval-proactive-vision"`。
3. virtual spotlight 放到主动视觉感知间隔。
4. 如果有 tooltip 或 title，允许高亮说明区域。

ghost cursor 流程：

1. cursor 移动到隐私模式开关。
2. 不点击。
3. cursor 移动到感知间隔滑条，不拖动。

真实 UI 操作：

1. 展开隐私模式侧边面板。
2. 不改变 `proactiveVisionEnabled`。

清理：

1. 收起隐私模式侧边面板。

## 5. 动画与鼠标跟踪

文本输出：

1. `tutorial.avatarFloating.day4.animationTracking`
2. 语音 key 占位：`avatar_floating_day4_animation_tracking`
3. 临时文案：“动画设置会影响我的画质、帧率，还有我会不会跟着你的鼠标看。”

高亮流程：

1. action spotlight 放到动画设置菜单项。
2. 展开 `data-neko-sidepanel-type="animation-settings"`。
3. virtual spotlight 依次标记画质滑条、帧率滑条、鼠标跟踪、全屏/局部跟踪、锁定悬停淡化。

ghost cursor 流程：

1. cursor 移动到动画设置入口。
2. cursor 逐项扫过滑条和开关。
3. 不点击，不拖动。
4. 后续如果实现互动点，可让用户真实移动鼠标观察模型；当前阶段只预留。

真实 UI 操作：

1. 展开动画设置侧边面板。
2. 不修改画质、帧率、跟踪或悬停淡化。

清理：

1. 收起动画设置侧边面板。
2. 关闭设置弹窗。

## 6. 锁定模型交互

文本输出：

1. `tutorial.avatarFloating.day4.lockInteraction`
2. 语音 key 占位：`avatar_floating_day4_lock_interaction`
3. 临时文案：“这个小锁能锁住我的交互。想拖动窗口或避免误点时，它会很有用。”

高亮流程：

1. action spotlight 放到 `#${p}-lock-icon`。
2. 如果锁图标因当前状态隐藏，教程期间可按现有逻辑显示，或退化为文字说明。

ghost cursor 流程：

1. cursor 移动到锁图标。
2. 可见 click。
3. 如果演示真实点击，必须记录原锁定状态，并在小节结束恢复。

真实 UI 操作：

1. 可调用真实 lock click 一次，再恢复原状态。
2. 如果恢复风险高，则只 hover，不点击。

清理：

1. 恢复原锁定状态。
2. 清理锁图标 action spotlight。

## 7. 请她离开与回来

文本输出：

1. `tutorial.avatarFloating.day4.goodbyeReturn`
2. 语音 key 占位：`avatar_floating_day4_goodbye_return`
3. 临时文案：“如果你想一个人安静一会儿，可以让我先离开；想我回来，再点这个按钮就好。”

高亮流程：

1. action spotlight 放到 `#${p}-btn-goodbye`。
2. cursor click 后等待模型隐藏和 `#${p}-btn-return` 出现。
3. action spotlight 切到 `#${p}-btn-return`。
4. 可用 virtual spotlight 标记返回按钮可拖拽区域。

ghost cursor 流程：

1. cursor 移动到“请她离开”按钮。
2. visible click，调用当前模型对应离开事件。
3. 等待返回按钮出现。
4. cursor 移动到返回按钮。
5. 可演示很短的拖拽轨迹，但当前阶段建议只说明可拖拽，不实际改变位置。
6. cursor click 返回按钮，恢复模型。

真实 UI 操作：

1. 触发离开。
2. 触发回来。

清理：

1. 确保模型、悬浮按钮、锁图标恢复。
2. 如果返回按钮位置被临时拖动，恢复原位置。

## 8. Day 4 收尾

文本输出：

1. `tutorial.avatarFloating.day4.wrap`
2. 语音 key 占位：`avatar_floating_day4_wrap`
3. 临时文案：“四天的小教程到这里就结束啦。之后这些按钮都在我旁边，想用的时候叫我就好。”

高亮流程：

1. persistent spotlight 放到聊天窗口或当前模型容器。
2. 清掉所有 action / virtual / retained / scene extra spotlight。

ghost cursor 流程：

1. cursor 移动到视口中心或聊天窗口。
2. wobble 后隐藏。

完成：

1. `setTutorialTakingOver(false)`。
2. 标记 Day 4 complete。
3. Day 2-4 全部完成后不再自动弹出后续轮次。

## 清理要求

1. 关闭设置弹窗和所有侧边面板。
2. 不修改对话、主动搭话、隐私、动画、鼠标跟踪等用户偏好。
3. 如果演示锁定，必须恢复原锁定状态。
4. 如果演示离开/回来，必须确保模型和悬浮按钮恢复。
5. 不自动打开媒体凭证页面。
6. 不保留返回按钮拖拽位置的临时变化。
