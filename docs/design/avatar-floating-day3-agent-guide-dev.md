# Day 3 新版聊天工具与互动菜单教程开发文档

本文对齐新版胶囊聊天窗 UI。Day 3 不讲 Agent、HUD 或插件管理；只演示胶囊输入框右侧总按钮、弧形工具菜单、Avatar 互动工具和 Galgame 入口。

通用生命周期、skip、打断对抗、临时切模、PC 全局 overlay 和收尾花瓣，以 `avatar-floating-7day-complete-guide-dev.md` 为准。

## 主线流程

| 顺序 | scene | 台词 | 高光与 Ghost Cursor |
| --- | --- | --- | --- |
| 1 | `day3_tool_toggle_intro` | 嘻嘻，可别以为这个聊天框只能用来打字哦~ 里面其实偷偷藏了超~多好玩的小惊喜呢！快跟着我一起点开看看，瞧瞧今天能挖出什么有趣的宝贝吧！ | 圆形高亮 `button.send-button-circle.compact-input-tool-toggle`；Ghost Cursor 平滑移动到该按钮并模拟点击，同时并行调用 API 触发按钮点击事件，打开弧形工具菜单。 |
| 2 | `day3_avatar_tools` | 在这个小按钮里，有许多可以和人家互动的小道具呢。 | 持续圆形高亮 `button.send-button-circle.compact-input-tool-toggle`；Ghost Cursor 平滑移动到 Avatar 互动工具按钮并模拟点击，同时并行调用 API 触发该按钮点击事件。 |
| 3 | `day3_avatar_tools_props` | 你可以随时来摸摸我的头，或者给我吃一根甜甜的棒棒糖。如果有时候我不小心做错事了，你也可以用小锤子敲敲我，不过……一定要轻轻的，不能太用力哦。 | 持续圆形高亮 `button.send-button-circle.compact-input-tool-toggle`；Ghost Cursor 停留在 Avatar 互动工具。台词播放完后再次触发 Avatar 互动工具按钮点击事件。 |
| 4 | `day3_avatar_tools_more` | 以后还会有更多有趣的道具加入进来，我会去提醒开发组猫猫快点做出来的，我们一起期待一下吧。 | 持续圆形高亮 `button.send-button-circle.compact-input-tool-toggle`；Ghost Cursor 在点击总按钮后出现的弧形菜单栏上模拟滑动，直到 Galgame 按钮出现在弧形菜单栏右偏下约 45 度位置。 |
| 5 | `day3_galgame_entry` | 快点开这个【Galgame模式】！进去之后就像我们在进行一场专属的互动大冒险呢。 | 持续圆形高亮 `button.send-button-circle.compact-input-tool-toggle`；Ghost Cursor 移动到 `.compact-input-tool-item-galgame`。教程期间不强制开启 Galgame。 |
| 6 | `day3_galgame_choices` | 你选的每一个对话，都会带我们走向完全未知的惊喜故事，我都等不及啦，快来选一个你最心动的回答吧！ | 继续指认 Galgame 入口或真实选项区域；不伪造选择局。 |
| 7 | `day3_wrap` | 今天带你认识的这些功能，其实都是为了让我们在一起的时光变得更有趣呢。 | 收尾前关闭弧形菜单和 Avatar 工具菜单，重新高亮聊天窗。 |
| 8 | `day3_wrap_ready` | 不管是想摸摸我的头，还是想开启属于我们的故事，我都已经做好准备了。 | 约 70% cue 同步隐藏 Ghost Cursor、清理高光和菜单并播放花瓣。 |

## 新版 UI 目标

| 语义 | 目标 |
| --- | --- |
| 胶囊工具总按钮 | `button.send-button-circle.compact-input-tool-toggle` |
| 弧形工具菜单 | `.compact-input-tool-fan[data-compact-input-tool-fan-open="true"]` |
| Avatar 互动工具按钮 | `.compact-input-tool-item-avatar` 内的 `.composer-emoji-btn`，fallback 到 `.compact-input-tool-item-avatar` |
| Galgame 按钮 | `.compact-input-tool-item-galgame` |

Avatar 工具按钮和 Galgame 按钮只使用圆形高光；总按钮持续高亮时使用同一套圆形高光，不给菜单项再叠猫耳、猫爪或第二层外框。

## 验收清单

1. Day 3 scene 顺序与本文一致。
2. Day 3 不再使用旧 `.composer-emoji-btn` / `.composer-galgame-btn` 作为未打开弧形菜单时的主目标。
3. 弧形菜单通过教程 host request 打开/收起，不依赖放开用户鼠标禁用。
4. Avatar 工具菜单通过既有 `setAvatarToolMenuOpen()` API 打开/关闭，不自动消耗道具。
5. 教程期间不强制开启 Galgame，不伪造小游戏或选项。
6. 收尾清理弧形菜单、Avatar 工具菜单、Ghost Cursor 和所有高光。
