# Day 3 新版聊天工具与互动菜单教程开发文档

本文对齐新版胶囊聊天窗 UI。Day 3 不讲 Agent、HUD 或插件管理；只演示胶囊输入框右侧总按钮、弧形工具菜单、Avatar 互动工具和 Galgame 入口。

通用生命周期、skip、打断对抗、临时切模、PC 全局 overlay 和收尾花瓣，以 `avatar-floating-7day-complete-guide-dev.md` 为准。

本日启用完整指南中的 Day 2-7 模型替身图片演出：教程模型可临时隐藏 5 秒，并通过全局透明 overlay 将替身贴到屏幕边缘；单轮固定触发 2 次，分别在 `day3_avatar_tools` 播放“在这个小按钮里……”时显示 `扒左边框.png`，以及在 `day3_galgame_choices` 播放“你选的每一个对话……”时显示上下翻转的 `探头.png`。替身不得出现在最后一句 `day3_wrap_ready` 播放期间。替身层只做视觉装饰，不能遮挡胶囊工具、Avatar 工具、Galgame 入口、skip、高光或 Ghost Cursor，也不能导致弧形菜单和道具菜单状态被清理。

## 主线流程

进入 Day 3 round 时必须先重置弧形工具栏轮盘：调用 `setCompactToolWheelIndex(0, 'avatar-floating-guide-day3-entry-reset')`，使导入图片按钮 `.compact-input-tool-item-import` 的 `data-compact-tool-wheel-slot` 为 `0`。

| 顺序 | scene | 台词 | 高光与 Ghost Cursor |
| --- | --- | --- | --- |
| 1 | `day3_tool_toggle_intro` | 嘻嘻，可别以为这个聊天框只能用来打字哦~ 里面其实偷偷藏了超~多好玩的小惊喜呢！快跟着我一起点开看看，瞧瞧今天能挖出什么有趣的宝贝吧！ | 圆角矩形高亮胶囊输入框 `chat-input`；Ghost Cursor 直接显示在胶囊聊天框中间并停留，不从默认点移动进入，不点击、不打开弧形工具菜单。 |
| 2 | `day3_avatar_tools` | 在这个小按钮里，有许多可以和人家互动的小道具呢。 | 持续圆形高亮 `button.send-button-circle.compact-input-tool-toggle`；Ghost Cursor 从胶囊输入框位置平滑移动到工具总按钮 `button.send-button-circle.compact-input-tool-toggle` 并模拟点击；点击动画开始时并行调用 API 打开弧形工具菜单，不打开 Avatar 工具菜单。 |
| 3 | `day3_avatar_tools_props` | 你可以随时来摸摸我的头，或者给我吃一根甜甜的棒棒糖。如果有时候我不小心做错事了，你也可以用小锤子敲敲我，不过……一定要轻轻的，不能太用力哦。 | 持续圆形高亮 `button.send-button-circle.compact-input-tool-toggle`；Ghost Cursor 平滑移动到 Avatar 互动工具按钮，然后在 Avatar 互动工具按钮处模拟点击并触发 Avatar 互动工具按钮点击事件，显示三个小道具。台词播放完后再次触发 Avatar 互动工具按钮点击事件并隐藏三个小道具。`day3_avatar_tools_props` 的前置条件：弧形工具菜单必须保持 open 状态，否则三个道具不会渲染。React 内部渲染条件是 `toolMenuOpen && compactInputToolFanOpen`，所以从 `day3_avatar_tools` 进入 `day3_avatar_tools_props` 时，不能只调用 `setAvatarToolMenuOpen(true)`，还必须保证弧形菜单仍为 open；如果弧形菜单已关闭，需要先用 host request / `openCompactInputToolFan(..., { ignoreDisabled: true })` 重新打开，再同步 Avatar 道具菜单。 |
| 4 | `day3_galgame_entry` | 快点开这个【Galgame模式】！进去之后就像我们在进行一场专属的互动大冒险呢。 | 持续圆形高亮 `button.send-button-circle.compact-input-tool-toggle`；Ghost Cursor 先平滑移动到初始 Galgame 按钮位置，然后切换为点击状态并保持，向下移动约 100px。轮盘按 22px/步累计阈值正向转 1 步，把 Galgame 从 slot 2 转到 slot 1；旋转完成后 Ghost Cursor 平滑移动并停在新的 `.compact-input-tool-item-galgame` 中心。教程期间不强制开启 Galgame。 |
| 5 | `day3_galgame_choices` | 你选的每一个对话，都会带我们走向完全未知的惊喜故事，我都等不及啦，快来选一个你最心动的回答吧！ | 继续指认 Galgame 入口或真实选项区域；不伪造选择局。 |
| 6 | `day3_wrap` | 今天带你认识的这些功能，其实都是为了让我们在一起的时光变得更有趣呢。 | 收尾前关闭弧形菜单和 Avatar 工具菜单；圆角矩形高亮胶囊输入框 `chat-input`，Ghost Cursor 平滑移动到胶囊输入框中间并停留。 |
| 7 | `day3_wrap_ready` | 不管是想摸摸我的头，还是想开启属于我们的故事，我都已经做好准备了。 | 继续保持胶囊输入框圆角高亮和 Ghost Cursor 停留；约 70% cue 同步隐藏 Ghost Cursor、清理高光和菜单并播放花瓣。 |

## 新版 UI 目标

| 语义 | 目标 |
| --- | --- |
| 胶囊工具总按钮 | `button.send-button-circle.compact-input-tool-toggle` |
| 弧形工具菜单 | `.compact-input-tool-fan[data-compact-input-tool-fan-open="true"]` |
| Avatar 互动工具按钮 | `.compact-input-tool-item-avatar` 内的 `.composer-emoji-btn`，fallback 到 `.compact-input-tool-item-avatar` |
| Galgame 按钮 | `.compact-input-tool-item-galgame` |

Avatar 工具按钮和 Galgame 按钮只使用圆形高光；总按钮持续高亮时使用同一套圆形高光，不给菜单项再叠猫耳、猫爪或第二层外框。
`day3_avatar_tools` 在内置与外置聊天窗 / PC 全局 overlay 模式下都必须保持 `chat-tool-toggle` 作为 persistent spotlight；Ghost Cursor 只移动到 `chat-tool-toggle` 并点击，点击后只打开弧形工具菜单，不打开 Avatar 工具菜单，也不得把 persistent spotlight 切成 Avatar 工具按钮。
Day 3 主线仅使用 `move` 和 `click`：需要指认的位置使用 `move`，需要真实触发的位置使用 `click`，不使用左右晃动停留动作。
所有 Day 3 的 `click` scene 都必须在 Ghost Cursor 点击动画开始的同一刻触发真实目标按钮的 `click()`；弧形工具菜单打开继续以工具总按钮点击为准。Avatar 道具菜单的显示/隐藏必须同时调用 `setAvatarToolMenuOpen()` 主机 API 同步 React 状态，因为教程锁定期间 Avatar 按钮可能处于 disabled，浏览器会吞掉 DOM click。React 内三个道具的渲染条件是 `toolMenuOpen && compactInputToolFanOpen`，所以 Avatar 道具菜单 open request 必须同时保证弧形工具菜单仍为 open；教程锁定 `composerDisabled=true` 时不得因为禁用输入而把承载道具菜单的弧形菜单关掉。

PC 外置聊天窗 / 全局 overlay 模式下，`day3_avatar_tools` 与 `day3_avatar_tools_props` 的外置聊天窗只负责解析目标并回传 `yui_guide_chat_cursor_anchor`，不得再发送 `cursor:*:click` patch。首页 Director 必须像 Day 2 本地 click 场景一样，在锚点移动完成后调用统一的 `clickCursorAndWait(DEFAULT_CURSOR_CLICK_VISIBLE_MS)` 播放点击图切换；真实 operation 只能通过该 helper 的 `onClickStart` 在点击动画开始时并行触发。外置聊天窗回传 anchor 时只回传坐标和 kind，`effect` 必须为空，避免锚点同步触发第二次点击。PC overlay 移动结束后回传的 anchor 必须带 `settled: true`；首页收到 settled anchor 时只同步内部 cursor 坐标，不再向 PC overlay 发送第二次可见 move。
Day 3 外置 click 场景必须和 Day 2 本地 click 场景使用同一类时序架构：cursor movement helper 负责等待目标位置到达并启动首页 Director 拥有的模拟点击，真实 operation 只能通过该 helper 的 `onClickStart` 在点击动画开始时并行触发；主流程不得绕过 cursor movement 单独定义点击定时器。

PC 端教程 overlay 会把同一条聊天窗指令同时通过 `neko:tutorial-overlay-relay` 和 `postMessage.__nekoTutorialOverlayRelay` 中继，并可能携带相同 `timestamp`。`yui_guide_set_avatar_tool_menu_open` 是幂等状态同步，不是一次性点击事件，必须和 `yui_guide_set_compact_tool_fan_open` 一样跳过消息去重；否则某个较早通道先占用去重 key 后，后续能真正同步 React host 的通道会被挡掉，表现为 Avatar 按钮 click 动画播放了但 `lollipop` / `fist` / `hammer` 三个道具始终不渲染。`yui_guide_click_avatar_tool_button` 仍然要保留去重，避免同一时间戳下双击按钮导致菜单刚打开又被反向收起。

## 验收清单

1. Day 3 scene 顺序与本文一致。
2. Day 3 不再使用旧 `.composer-emoji-btn` / `.composer-galgame-btn` 作为未打开弧形菜单时的主目标。
3. 弧形菜单通过教程 host request 打开/收起，不依赖放开用户鼠标禁用。
4. Avatar 工具菜单通过既有 `setAvatarToolMenuOpen()` API 打开/关闭，不自动消耗道具。
5. `day3_galgame_entry` 必须先让 Ghost Cursor 平滑移动到初始 Galgame 按钮位置，再切换并保持点击态向下拖约 100px；触发 `compactToolWheelRotateRequest(direction=1, stepCount=1)`，让 Galgame 从 slot 2 转到 slot 1；旋转完成后 cursor 再平滑移动并停在新的 Galgame 中心。教程期间不强制开启 Galgame，不伪造小游戏或选项。
6. Day 3 click 场景必须以 Ghost Cursor 到达外置聊天窗回报的目标 anchor 为点击启动条件；Day 3 配置和外置 cursor 指令不得携带显式 `durationMs` / `cursorMoveDurationMs`。若首页开始等待时 anchor 尚未回传，必须等待未来 anchor 或超时，不能因当前没有 move promise 就立即启动模拟点击。
7. 收尾清理弧形菜单、Avatar 工具菜单、Ghost Cursor 和所有高光。
8. PC 端重启第三天教程时，播放 `day3_avatar_tools_props` 后能在真实聊天窗 DOM 中看到 `#composer-tool-popover-compact`，并且可见 `data-avatar-tool-id` 依次包含 `lollipop`、`fist`、`hammer`；同一时刻 Ghost Cursor 只停在 Avatar 互动工具按钮，不移动到三个道具项。
9. Day 3 单轮模型替身图片固定出现 2 次：`day3_avatar_tools` 使用 `扒左边框.png`，`day3_galgame_choices` 使用上下翻转的 `探头.png`；每次 5 秒后恢复模型。进入 `day3_wrap_ready` 前如果替身仍在显示，必须立即清理替身并恢复模型。替身必须由全局透明 overlay 与高光、Ghost Cursor、花瓣一起携带完整可见状态；替身出现期间弧形工具菜单、Avatar 工具菜单、Galgame 轮盘和外置聊天窗锚点不得被重置。
