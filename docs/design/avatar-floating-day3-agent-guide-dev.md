# Day 3 新版聊天工具与互动菜单教程开发文档

本文对齐新版胶囊聊天窗 UI。Day 3 不讲 Agent、HUD 或插件管理；只演示胶囊输入框右侧总按钮、弧形工具菜单、Avatar 互动工具和 Galgame 入口。

通用生命周期、skip、打断对抗、临时切模、PC 全局 overlay 和收尾花瓣，以 `avatar-floating-7day-complete-guide-dev.md` 为准。

## 主线流程

| 顺序 | scene | 台词 | 高光与 Ghost Cursor |
| --- | --- | --- | --- |
| 1 | `day3_tool_toggle_intro` | 嘻嘻，可别以为这个聊天框只能用来打字哦~ 里面其实偷偷藏了超~多好玩的小惊喜呢！快跟着我一起点开看看，瞧瞧今天能挖出什么有趣的宝贝吧！ | 圆角矩形高亮胶囊输入框 `chat-input`；Ghost Cursor 直接显示在胶囊聊天框中间并停留，不从默认点移动进入，不点击、不打开弧形工具菜单。 |
| 2 | `day3_avatar_tools` | 在这个小按钮里，有许多可以和人家互动的小道具呢。 | 持续圆形高亮 `button.send-button-circle.compact-input-tool-toggle`；Ghost Cursor 从胶囊输入框位置以约 1480ms 慢慢平滑移动到工具总按钮 `button.send-button-circle.compact-input-tool-toggle` 并模拟点击；点击动画开始时并行调用 API 打开弧形工具菜单，不打开 Avatar 工具菜单。 |
| 3 | `day3_avatar_tools_props` | 你可以随时来摸摸我的头，或者给我吃一根甜甜的棒棒糖。如果有时候我不小心做错事了，你也可以用小锤子敲敲我，不过……一定要轻轻的，不能太用力哦。 | 持续圆形高亮 `button.send-button-circle.compact-input-tool-toggle`；Ghost Cursor 平滑移动到 Avatar 互动工具按钮，然后在 Avatar 互动工具按钮处模拟点击并触发 Avatar 互动工具按钮点击事件，显示三个小道具。台词播放完后再次触发 Avatar 互动工具按钮点击事件并隐藏三个小道具。 |
| 4 | `day3_galgame_entry` | 快点开这个【Galgame模式】！进去之后就像我们在进行一场专属的互动大冒险呢。 | 持续圆形高亮 `button.send-button-circle.compact-input-tool-toggle`；Ghost Cursor 移动到 `.compact-input-tool-item-galgame`。教程期间不强制开启 Galgame。 |
| 5 | `day3_galgame_choices` | 你选的每一个对话，都会带我们走向完全未知的惊喜故事，我都等不及啦，快来选一个你最心动的回答吧！ | 继续指认 Galgame 入口或真实选项区域；不伪造选择局。 |
| 6 | `day3_wrap` | 今天带你认识的这些功能，其实都是为了让我们在一起的时光变得更有趣呢。 | 收尾前关闭弧形菜单和 Avatar 工具菜单，重新高亮聊天窗。 |
| 7 | `day3_wrap_ready` | 不管是想摸摸我的头，还是想开启属于我们的故事，我都已经做好准备了。 | 约 70% cue 同步隐藏 Ghost Cursor、清理高光和菜单并播放花瓣。 |

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

PC 端教程 overlay 会把同一条聊天窗指令同时通过 `neko:tutorial-overlay-relay` 和 `postMessage.__nekoTutorialOverlayRelay` 中继，并可能携带相同 `timestamp`。`yui_guide_set_avatar_tool_menu_open` 是幂等状态同步，不是一次性点击事件，必须和 `yui_guide_set_compact_tool_fan_open` 一样跳过消息去重；否则某个较早通道先占用去重 key 后，后续能真正同步 React host 的通道会被挡掉，表现为 Avatar 按钮 click 动画播放了但 `lollipop` / `fist` / `hammer` 三个道具始终不渲染。`yui_guide_click_avatar_tool_button` 仍然要保留去重，避免同一时间戳下双击按钮导致菜单刚打开又被反向收起。

## 验收清单

1. Day 3 scene 顺序与本文一致。
2. Day 3 不再使用旧 `.composer-emoji-btn` / `.composer-galgame-btn` 作为未打开弧形菜单时的主目标。
3. 弧形菜单通过教程 host request 打开/收起，不依赖放开用户鼠标禁用。
4. Avatar 工具菜单通过既有 `setAvatarToolMenuOpen()` API 打开/关闭，不自动消耗道具。
5. 教程期间不强制开启 Galgame，不伪造小游戏或选项。
6. 收尾清理弧形菜单、Avatar 工具菜单、Ghost Cursor 和所有高光。
7. PC 端重启第三天教程时，播放 `day3_avatar_tools_props` 后能在真实聊天窗 DOM 中看到 `#composer-tool-popover-compact`，并且可见 `data-avatar-tool-id` 依次包含 `lollipop`、`fist`、`hammer`；同一时刻 Ghost Cursor 只停在 Avatar 互动工具按钮，不移动到三个道具项。
