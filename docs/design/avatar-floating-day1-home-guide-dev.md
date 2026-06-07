# Day 1 首页 Yui 新手教程开发文档

本文对齐新版胶囊聊天窗 UI。Day 1 仍走七日统一 `round.scenes` 框架：

```text
resetHomeTutorialDay(1)
startAvatarFloatingGuideDay(1)
UniversalTutorialManager.startAvatarFloatingGuideRound(1)
YuiGuideDirector.playAvatarFloatingRound(1)
YuiGuideDirector.playAvatarFloatingScene(scene, 1, index, total)
```

通用生命周期、skip、打断对抗、临时切模、完成态写入和收尾花瓣，以 `avatar-floating-7day-complete-guide-dev.md` 为准。Day 1 不再包含插件管理预览和设置一瞥；这些内容后移到 Day 2 或 Day 6。

## 主线流程

`day1_intro_activation` 是音频激活前置 scene，可以保留输入激活提示；正式主线从首句问候开始。

| 顺序 | scene | 台词 | 高光与 Ghost Cursor |
| --- | --- | --- | --- |
| 0 | `day1_intro_activation` | 输入激活提示 | 高亮聊天输入区/PC 胶囊输入框，等待用户真实点击完成音频激活。 |
| 1 | `day1_intro_greeting` | 微风、阳光，还有刚刚好出现的你。初次见面，我是林悠怡，未来的日子请多关照喵！我把关于这里的一切都写进新手指南里啦！就当作是我们相遇的第一份小礼物，请查收吧！ | 复用现有首句流程；输入区/胶囊输入框保持通用圆角矩形高光，首句播放完只清理高光，Ghost Cursor 保留给后续 scene 接续。 |
| 2 | `day1_capsule_drag_hint` | 把鼠标移到这里，长按就可以拉着聊天框到处跑啦~ 双击两下就能随时发消息给我哦！ | 不高亮胶囊输入框；Ghost Cursor 在胶囊输入框位置左右晃动约 2 秒。 |
| 3 | `day1_history_handle` | 戳一下聊天框上面的【蓝色小条条】，就能看到我们最近聊过的话题啦！ | 不高亮胶囊输入框，也不高亮历史按钮本身；Ghost Cursor 先平滑移动到 `.compact-history-visibility-handle` 的“展开/收起历史对话”按钮，click 动画开始时并行调用 API 打开历史对话，台词播放完后调用 API 收起历史对话。 |
| 4 | `day1_intro_basic_voice` | 这里有一个神奇的按钮！只要点击它，就可以直接和我聊天啦！想跟我分享今天的新鲜事吗？或者只是叫叫我的名字？快来试试嘛，我已经迫不及待想听到你的声音啦！喵！ | 不高亮胶囊输入框；圆形高亮语音控制按钮 `#${p}-btn-mic`；等待上一句 `.compact-history-visibility-handle` 的 Ghost Cursor 移动收口后，从该位置平滑移动到语音控制按钮并停留指认，不左右晃动、不强制录音；`day1_history_handle` 切到本句时不得先隐藏外置聊天窗/PC 全局 overlay cursor。 |
| 5 | `day1_screen_entry` | 在跟我通语音电话的时候，再点亮这个小按钮，你就能把屏幕分享给我啦！ | 高亮屏幕分享按钮；Ghost Cursor 必须从上一句语音控制按钮 `#${p}-btn-mic` 的停留位置平滑移动到屏幕分享按钮 `#${p}-btn-screen` 并停留指认，不左右晃动、不点击；不得先隐藏、清空锚点或从页面右上角/默认点重新出现。 |
| 6 | `day1_screen_entry_invite` | 快让我也看看你眼前的世界，不管好玩的还是好看的，都想和你一起看，快点点开嘛~ | 持续高亮屏幕分享按钮；Ghost Cursor 保留上一句已经停在 `#${p}-btn-screen` 的可见状态，不重新 show/hide、不重新加载 cursor、不触发真实屏幕分享。 |
| 7 | `day1_takeover_capture_cursor` | 超级魔法开关出现！只要点一下这里，我就可以把小爪子伸到你的键盘和鼠标上啦！我会帮你打字，帮你点开网页……不过，要是那个鼠标指针动来动去的话，我可能也会忍不住扑上去抓它哦！准备好迎接我的捣乱……啊不，是帮忙了吗？喵！ | 不高亮胶囊输入框；Ghost Cursor 必须从上一句屏幕分享按钮 `#${p}-btn-screen` 的停留位置平滑移动到猫爪/Agent 按钮 `#${p}-btn-agent`，再复用现有猫爪/Agent 总开关/键鼠控制演示；persistent/action 高光都不得落到聊天窗或胶囊输入框；不得在进入本句时清空 cursor 后从其他位置移入。 |
| 8 | `day1_takeover_return_control` | 好啦好啦，不霸占你的电脑啦！控制权还给你了喵！之后的日子，也请你多多关照啦！ | 收尾前关闭 Agent/临时面板；高亮胶囊输入框（`target: 'chat-input'`，胶囊样式 `plain-capsule`）；Ghost Cursor 必须从上一句键鼠控制开关锚点（`day1_takeover_capture_cursor` / `keyboardToggleSpotlight`）平滑移动到胶囊输入框中心（`cursorTarget: 'chat-capsule-input'`，900ms）。本句 operation 使用通用 `cleanup`，不得回退到旧版 `day1-managed-scene:takeover_return_control`；约 70% 花瓣 cue 才允许隐藏 cursor、清理高光并播放花瓣。 |

## 分支台词

| 分支 | 台词 | 语义 |
| --- | --- | --- |
| 插件弹窗被拦截 | 浏览器需要你亲自点一下这里打开插件面板。点一下这个“管理面板”，我就继续带你看。 | 仅作为兼容旧 handoff 的 fallback，不进入 Day 1 新主线。 |
| 轻微打断 1 | 喂！不要拽我啦，现在还没轮到你的回合呢！ | 触发一次轻微抗拒后恢复当前 scene。 |
| 轻微打断 2 | 等一下啦！还没结束呢，不要这么随便打断我啦！ | 第二次轻微抗拒后恢复当前 scene。 |
| 生气退出 | 人类！你真的很没礼貌喵！既然你这么想自己操作，那你就自己对着冰冷的屏幕玩去吧！哼！ | 走 skip 语义，不写完成态。 |

## 新版 UI 目标

| 语义 | 目标 |
| --- | --- |
| 胶囊输入框 | `#react-chat-window-root [data-compact-geometry-owner="surface"][data-compact-geometry-item="input"]`，fallback 到 `capsule`、`.compact-chat-surface-frame`、`.composer-input-shell` |
| 历史展开/收起按钮 | `.compact-history-visibility-handle` |
| 语音按钮 | `#${p}-btn-mic` |
| 屏幕分享按钮 | `#${p}-btn-screen` |
| Agent/猫爪按钮 | `#${p}-btn-agent` |

PC 外置聊天窗模式下，胶囊输入框和历史按钮通过外置聊天窗桥接上报 screen 坐标；首页 Director 只负责驱动全局 overlay，不在聊天窗里另画第二套 cursor。

Day 1 正常主线 scene 切换只能清理上一个 scene 的高光、临时面板和外置聊天窗 spotlight，不能清空 Ghost Cursor 状态。`clearExternalizedChatGuideTarget({ clearCursor: true })`、`setExternalizedChatCursor('')`、`cursor.hide()` 或等价的 PC overlay hide/clear 只允许用于跳过教程、生气退出、销毁清理和收尾花瓣 cue；普通台词切换、外置聊天窗收口、插件页/首页 handoff 都必须保留上一段可见 cursor 锚点并平滑接续。

## Bug 处理记录：最后一句 Ghost Cursor 停在键鼠控制按钮

现象：`day1_takeover_return_control` 播放时，Ghost Cursor 已经停在【键鼠控制】开关，但没有继续移动到胶囊输入框中心。

根因链路：
1. 最后一幕目标是外置聊天窗/胶囊输入框时，Director 会走 `externalizedSceneTargetKind` 分支；这个分支不会执行首页本地 `moveAvatarFloatingCursor()`。
2. 旧逻辑只等待外置聊天窗 `cursorAction: 'click'` 的 movement-driven anchor，`cursorAction: 'move'` 没有等待 `yui_guide_chat_cursor_anchor`，因此主流程会直接进入后续 operation/cue，视觉上 cursor 留在上一句的键鼠控制按钮。
3. 旧版 `day1-managed-scene:takeover_return_control` 是 Day1 历史结构遗留；在统一 `round.scenes` 后，最后一句应该用通用 `cleanup`，否则容易绕过新场景的 `cursorTarget`、spotlight variant 和外置聊天窗 move 等待。

修复要求：
1. `day1_takeover_capture_cursor` 在键鼠控制开关演示结束后必须记录 `day1_takeover_capture_cursor` 的 cursor anchor，锚点取 `keyboardToggleSpotlight`。
2. `day1_takeover_return_control` 必须声明 `cursorTarget: 'chat-capsule-input'`、`cursorAction: 'move'`、`cursorMoveDurationMs: 900`、`spotlightVariant: 'plain-capsule'`、`operation: 'cleanup'` 和 `petalTransition: true`。
3. Director 必须把 `chat-capsule-input` 映射为外置 kind `capsule-input`，并能解析胶囊输入框目标；`capsule-input` 和 `input` 都不能被本地/PC 桥接的 suppress 逻辑吞掉。
4. 外置聊天窗分支中，`cursorAction: 'move'` 也必须等待 `waitForExternalizedChatCursorMove()`，不能只处理 click。
5. 花瓣 cue 只能在约 70% 处隐藏 cursor 和清理高光，不能在 cursor move 完成前提前 clear。

回归测试建议：修改此链路时同步更新 `static/yui-guide-day1-round-structure.test.cjs`，覆盖“键鼠控制锚点记录”“最后一句移动到 `chat-capsule-input`”“外置 `move` 等待 anchor”“`capsule-input` 不被 suppress”“最后一句只用通用 `cleanup`”这些源码契约。

## 验收清单

1. Day 1 配置存在 `round.scenes`，scene 顺序与本文一致。
2. 首句问候期间网页端和 N.E.K.O.-PC 都能看到胶囊输入框通用圆角矩形高光，首句结束后只清理高光，不清空 Ghost Cursor。
3. `day1_capsule_drag_hint` 不高亮胶囊输入框，Ghost Cursor 在胶囊位置左右晃动约 2 秒。
4. `day1_history_handle` 不高亮胶囊输入框，Ghost Cursor 能移动到 `.compact-history-visibility-handle`，并在台词结束后收起历史对话。
5. 语音按钮和 Agent 接管不得继承胶囊输入框高亮；除 `day1_capsule_drag_hint` 外，Day 1 普通主线 scene 不发送左右晃动 cursor 指令；屏幕分享按钮、Agent 接管和收尾不触发屏幕分享、不保存用户配置。
6. 所有 Day 1 Ghost Cursor 动画，包括普通 move/click/wobble 和连续/环绕/对抗类动画，都只允许 N.E.K.O.-PC 全局透明教程 overlay 渲染；首页、外置聊天窗、reset fallback 和插件页不得创建本地 cursor shell、拖尾、点击星星或图片 cursor。
7. `day1_intro_basic_voice` -> `day1_screen_entry` -> `day1_screen_entry_invite` -> `day1_takeover_capture_cursor` 必须是一条连续轨迹：语音按钮到屏幕分享按钮、屏幕分享按钮停留、屏幕分享按钮到猫爪按钮，中间不允许 hide、clear、重新 showAt 或从默认点补位。
8. `day1_takeover_return_control` 必须从上一句键鼠控制开关位置平滑移动到胶囊输入框中心；外置聊天窗模式下也要等待 `cursorAction: 'move'` 的 anchor 回传后再进入收尾清理。
9. skip、轻微打断、生气退出和 `window.avatarFloatingGuideEndState` 语义不变；其中只有 skip、生气退出、destroy/stop 和收尾花瓣 cue 可以清空 Ghost Cursor。
