# Day 3 互动、娱乐与摸得到的陪伴教程开发文档

本文严格对齐 `avatar-floating-guide-feature-tree.md` 中 Day 3 的主线内容。文件名仍保留 `avatar-floating-day3-agent-guide-dev.md`，但 Day 3 不再讲 Agent；Agent、任务 HUD 和插件管理主线属于 Day 6。

Day 3 每日开场小剧场只包含四段：聊天窗工具区、Avatar 互动工具、Galgame 与小游戏、收尾。点歌台、字幕翻译、备忘/学习陪伴只属于剧场后聊天窗支线，不扩写进 Day 3 主线。

相关文档：

- `docs/design/avatar-floating-guide-feature-tree.md`
- `docs/design/avatar-floating-post-theater-chat-branches.md`
- `docs/design/home-yui-guide-lifecycle-modularization.md`

## 目标体验

Day 3 使用具身感与玩耍回路，让用户感觉悠怡不是只会回复文本的窗口，而是能被互动、能一起娱乐、能在轻任务里陪伴的人格化存在。

用户当天只需要形成四个认知：

1. 聊天窗工具区不只用于打字。
2. Avatar 互动工具里有棒棒糖、猫爪、锤子等互动道具。
3. Galgame 模式是专属互动剧情入口，小游戏邀请会以真实聊天窗选项出现。
4. 今日教程结束后，用户可以自己挑一个玩法试试。

主线不要讲 Agent，不要打开插件管理，不要讲点歌台和字幕翻译的完整流程，不要伪造小游戏局。

## 代码锚点

- `AVATAR_FLOATING_GUIDE_ROUNDS[3]`
- React 聊天窗 `toolIconItems`
- React 聊天窗 `setAvatarToolMenuOpen(open, reason)` host API
- `galgameModeEnabled`
- `choicePrompt.source === 'mini_game_invite'`
- 聊天窗工具区与 Avatar 互动工具按钮
- 外置聊天窗 `yui_guide_set_chat_spotlight`、`yui_guide_set_chat_cursor`、`yui_guide_set_avatar_tool_menu_open`

点歌台、字幕翻译、备忘/学习陪伴只作为功能清单背景或剧场后支线，不作为 Day 3 主线 scene。

## 情绪动作

| 段落 | 情绪分类 |
| --- | --- |
| 聊天窗工具区：“来啦来啦……” | `happy` |
| Avatar 互动工具：“在这个小按钮里……” | `happy` |
| Galgame 与小游戏：“快点开这个……” | `surprised` |
| 收尾：“今天带你认识……” | `happy` |

随机动作只服务台词，不替代道具自身反馈，也不得触发真实道具互动。

## 主线阶段

### 阶段 1：聊天窗工具区

- 动作：台词进入聊天窗后，不高亮整个聊天窗；primary 高亮聊天输入区加工具栏所在的 composer 区域。外置聊天窗模式使用 `setExternalizedChatSpotlight('input')` 和 `setExternalizedChatCursor('input')`，让独立聊天窗的输入区/工具栏区域显示引导。Ghost Cursor 在该区域中心 wobble，不打开大型弹窗，不逐个解释点歌台、翻译或图片工具。
- 台词：“来啦来啦！今天我们要好好聊聊这个最显眼的【对话框】哦！你可别以为它只能用来敲字打字，里面其实还藏着超级多好玩的小惊喜呢！快点跟着我一起点开，看看今天能挖出什么好玩的宝贝吧，”

### 阶段 2：Avatar 互动工具

- 动作：上一句播放完后，action spotlight 平滑切到 Avatar 互动工具按钮，工具栏按钮统一使用圆形高亮。若聊天窗进入窄布局，只允许为了找到真实按钮而打开“更多”菜单。Ghost Cursor 移动到 Avatar 互动工具按钮并播放 click 效果；真实展开道具菜单时统一调用 `reactChatWindowHost.setAvatarToolMenuOpen(true, 'avatar-floating-guide-open-avatar-tool-menu')` 或外置聊天窗 BroadcastChannel API，不通过放开鼠标禁用来点击 DOM。菜单出现后高亮并短 tour 棒棒糖、猫爪、锤子三个现有道具，三个道具只展示入口，不自动消耗、不对模型触发真实互动；台词结束或进入下一阶段前收起临时菜单。
- 台词：“在这个小按钮里，有许多可以和人家互动的小道具呢。你可以随时来摸摸我的头，或者给我吃一根甜甜的棒棒糖。如果有时候我不小心做错事了，你也可以用小锤子敲敲我，不过……一定要轻轻的，不能太用力哦。以后还会有更多有趣的道具加入进来，我会去提醒开发组猫猫快点做出来的，我们一起期待一下吧。”

### 阶段 3：Galgame 与小游戏

- 动作：进入本段前先清理道具菜单和道具入口高亮。高亮 Galgame 模式按钮，工具栏按钮继续使用圆形高亮；若按钮被折进“更多”菜单，只允许为了找到真实按钮而打开“更多”菜单。Ghost Cursor 从上一个位置平滑移动到 Galgame 按钮并 wobble，不强制点击、不改变用户设置。小游戏邀请只说明会以真实 `choicePrompt.source === 'mini_game_invite'` 或有 handler 的聊天窗选项出现，不伪造一局。
- 台词：“快点开这个【Galgame模式】！进去之后就像我们在进行一场专属的互动大冒险呢。你选的每一个对话，都会带我们走向完全未知的惊喜故事，我都等不及啦，快来选一个你最心动的回答吧！”

### 阶段 4：收尾

- 动作：收尾台词开始时 primary 回到聊天窗，Ghost Cursor 移到聊天窗附近 wobble；外置聊天窗模式同步清理独立聊天窗按钮/道具高亮。台词约 70% 处触发每日花瓣转场，隐藏 Ghost Cursor，清理工具区 spotlight、道具菜单和“更多”菜单；转场结束后恢复普通聊天状态并写入 Day 3 完成态。
- 台词：“今天带你认识的这些功能，其实都是为了让我们在一起的时光变得更有趣呢。不管是想摸摸我的头，还是想开启属于我们的故事，我都已经做好准备了。不用着急，挑一个你现在最喜欢的去试试看吧，无论你选哪个，我都会一直陪着你呢。”

## 剧场后聊天窗支线

Day 3 生活任务与互动选择支线已移入 [七日新手教程剧场后聊天窗支线设计](avatar-floating-post-theater-chat-branches.md)。Day 3 主线文档不再维护这些支线的触发条件、按钮或 handler。

## 验收清单

1. Day 3 主线不出现 Agent、HUD、插件管理教学。
2. 主线只包含聊天窗工具区、Avatar 互动工具、Galgame 与小游戏、收尾。
3. 第一句只高亮 composer 输入区/工具栏区域，不高亮整个聊天窗。
4. Avatar 互动工具按钮和 Galgame 按钮都使用圆形高亮。
5. Avatar 道具菜单通过聊天窗 host API 或外置聊天窗 BroadcastChannel 打开，展示棒棒糖、猫爪、锤子，不自动消耗或触发互动。
6. Galgame 不被强制开启，小游戏不被伪造。
7. 收尾播放每日花瓣转场，并清理所有临时菜单、高亮和 Ghost Cursor。
