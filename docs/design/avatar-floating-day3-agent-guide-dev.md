# Day 3 新版聊天工具与互动菜单教程文字说明

本文只保留 Day 3 新手教程的体验、台词和行为边界。

Day 3 不讲 Agent、任务 HUD 或插件管理；只演示胶囊输入框右侧的工具总入口、互动小道具和 Galgame 入口。

## 当前实现状态

Day 3 七个 round scene 已全部接入共享 Timeline/Command 播放路径，覆盖工具入口开场、工具总按钮 click-onStart 打开弧形工具菜单、Avatar 道具按钮 click-onStart 打开并在真实旁白结束后关闭道具菜单、Galgame 入口弧形旋转、Galgame hold、收尾 cleanup、胶囊输入框高光、Ghost Cursor move 和最终花瓣 cue。首句台词开始前由共享 `SceneOrchestrator` 先把 Ghost Cursor 固定到胶囊输入框，并由 `TutorialVisualRuntime` 在 timeline `spotlight.show` 中强制复用首句胶囊高亮规则，直到第一个显式目标移动接管。后续 timeline scene 的 `spotlight.show` 也必须在台词开始时同步外置聊天窗 spotlight，优先使用 `persistent` 目标，避免工具菜单打开或 cursor click operation 后才补高亮造成胶囊输入框闪烁。Day 3 外置聊天窗 click 必须走 PC overlay/externalized cursor click effect，不得回落到首页 Ghost Cursor click，避免胶囊输入框误抖。`day3_galgame_entry` 通过 `compactToolWheel.rotateGalgameIntoCenter` command 复用原导演层 Galgame 旋转演出，Ghost Cursor 必须在 click 态下做 1/5 圆弧运动；进入 `day3_galgame_choices` 后必须只采样一次 Galgame 入口的屏幕坐标并冻结该绝对点，后续播放期间不得再按 DOM 目标重算或重发 move。模型替身演出在场景表面准备完成后排程，避免被开场清理或工具菜单准备阶段清掉。

## 2026-06-16 Bug 修复记录

1. `day3_galgame_choices` 台词“你选的每一个对话……”播放期间，Ghost Cursor 必须停在 Galgame 按钮上。根因不是对抗机制，而是上一句 `day3_galgame_entry` 的圆弧分段 timer 和首页 PC overlay cursor 输出仍可能在外置聊天窗接管后继续写 cursor。修复边界是：外置聊天窗接管 cursor 后首页 `YuiGuideOverlay` 只维护内部位置和 DOM suppression，不再向 PC overlay 转发 cursor；Galgame freezePoint hold 入场时递增 cursor request token，并只在 `kind === 'galgame'` 时递增 arc token 让上一句残留圆弧分段自行退出；不得恢复 active-arc timestamp 拦截普通 cursor 请求。
2. `day3_galgame_choices` 的 `cursorHoldSettleMs: 260` 是一次性补采样，不是通用轮询。它只解决 Galgame 轮盘从上一句切入下一句时 React/CSS settle 未完成导致的按钮中心短暂偏移；不能扩展到其他 ghost cursor 流程，也不能用 `[120, 360, 720]` 一类多 timer 反复重钉 DOM 坐标。
3. Day 3 替身演出图片已确认通讯链路正常：网页侧发送 `avatarStandIn.url/resource/position`，NekoPC 透明 overlay 收到并加载图片。左右扒边图尺寸问题属于 NekoPC preload 渲染口径，不属于 Day scene 触发点问题。`peek-left-border.png` 使用 `top-left-border`，`peek-right-border.png` 使用 `top-right-border`，两者固定屏幕左下/右下，宽度按普通替身宽度 100% 渲染，即 `min(42vw, 420px)`；`peek-head.png` 保持探头图普通替身口径，底部用 `bottom-right`，顶部倒置用 `top-left-flipped`。

## 主线流程

| 顺序 | 台词 | 演出描述 |
| --- | --- | --- |
| 1 | 嘻嘻，可别以为这个聊天框只能用来打字哦~ 里面其实偷偷藏了超~多好玩的小惊喜呢！快跟着我一起点开看看，瞧瞧今天能挖出什么有趣的宝贝吧！ | 高亮胶囊输入框，Ghost Cursor 停在聊天框附近，先让用户知道工具藏在聊天入口里。 |
| 2 | 在这个小按钮里，有许多可以和人家互动的小道具呢。 | 高亮工具总按钮，Ghost Cursor 从胶囊输入框移动过去并演示打开工具菜单。 |
| 3 | 你可以随时来摸摸我的头，或者给我吃一根甜甜的棒棒糖。如果有时候我不小心做错事了，你也可以用小锤子敲敲我，不过……一定要轻轻的，不能太用力哦。 | Ghost Cursor 指认 Avatar 互动工具，并短暂展示摸头、棒棒糖和小锤子等道具入口。演示结束后收起道具，不自动消耗道具。 |
| 4 | 快点开这个【Galgame模式】！进去之后就像我们在进行一场专属的互动大冒险呢。 | Ghost Cursor 指向 Galgame 入口，并在 click 态下做 1/5 圆弧运动，让入口清楚出现。教程期间不强制开启 Galgame。 |
| 5 | 你选的每一个对话，都会带我们走向完全未知的惊喜故事，我都等不及啦，快来选一个你最心动的回答吧！ | Ghost Cursor 停留在 Galgame 入口位置，说明玩法想象，不伪造选择、不启动小游戏。 |
| 6 | 今天带你认识的这些功能，其实都是为了让我们在一起的时光变得更有趣呢。 | 收起工具菜单和道具菜单，重新高亮胶囊输入框。 |
| 7 | 不管是想摸摸我的头，还是想开启属于我们的故事，我都已经做好准备了。 | 保持收尾高亮，台词后段清理 Ghost Cursor、菜单和高光，并播放花瓣效果。 |

## 体验约束

1. Day 3 只讲聊天工具、互动小道具和 Galgame 入口。
2. 工具菜单由教程受控打开，用户不需要真实点击。
3. 互动道具只展示入口，不自动使用道具。
4. Galgame 只做入口级介绍，不强制进入、不伪造选项。
5. 模型替身图片只作为短暂视觉演出，不遮挡工具菜单、互动工具、Galgame 入口、跳过按钮、高光或 Ghost Cursor。
6. 收尾时关闭临时菜单，回到胶囊输入框。
7. 首句台词播放时 Ghost Cursor 必须已经在胶囊输入框中，spotlight 必须高亮胶囊输入框，并保持到工具入口移动演出开始。
8. `day3_galgame_entry` 的外置聊天窗 1/5 圆弧分段移动只使用自身 arc token 保护圆弧分段，不得让后续普通 `set_chat_cursor` 请求强制销毁圆弧 timer，也不得用 active-arc timestamp 拦截普通 cursor 请求。`day3_galgame_choices` 的 hold 必须携带 `cursorHoldFreezePoint: true`，外置聊天窗收到后只在入场时把 Galgame 入口转换成一次性屏幕坐标，并只允许这个 Galgame freezePoint 请求递增 arc token 让上一句残留圆弧分段 timeout 自行退出；freezePoint cursor 必须和普通 cursor 一样绕过桥接 dedup，并在入场时取消旧 cursor retry，PC overlay 收到显式 `durationMs: 0` 时必须瞬时钉住，不得再套 fallback 平滑移动；同一 timestamp 的重复消息必须复用首次采样点，播放期间不得用 `[120, 360, 720]` 这类重钉 timer 反复按 DOM 位置重算。PC 外置聊天窗在 `day3_galgame_entry` 刚切入 `day3_galgame_choices` 时可能仍处于 Galgame 轮盘 CSS/React settle 阶段，因此 `day3_galgame_choices` 只允许配置一次 `cursorHoldSettleMs: 260` 的延迟 freeze hold，用新的 timestamp 在按钮稳定后复采样一次 Galgame 坐标；不得扩展成通用轮询或影响其他 ghost cursor 流程。
9. Avatar 互动工具入口和 quickbar 子工具必须分开定位：`chat-avatar-tools` 只锚定 `.compact-input-tool-item-avatar > .composer-emoji-btn` 或入口容器；quickbar 子工具使用 `composer-tool-popover-compact` / `composer-avatar-tool-quickbar` 下的 `data-avatar-tool-id` 按钮。点击 Avatar 入口这类 cursor scene 的 spotlight 优先 `target` 而不是 `persistent`，避免先回到工具总按钮或胶囊输入框造成闪烁。
10. Day 3 替身演出验收时必须同时看网页 payload 和 NekoPC renderer：如果 `avatarStandIn` payload 已到达，优先检查 `N.E.K.O.-PC/src/preload-tutorial-global-overlay.js` 的 placement/size 规则。当前左右扒边图不得再按原始 PNG 50% 或 legacy `maxHeight` 口径缩放，必须按 `width: min(42vw, 420px); height: auto` 贴屏幕底边渲染。

## 验收要点

1. Day 3 主线顺序与本文一致。
2. 用户能理解工具总按钮、互动道具和 Galgame 入口各自的作用。
3. 工具菜单、互动道具和 Galgame 演示都不产生真实副作用。
4. Ghost Cursor 轨迹自然连续，不在菜单演示中突兀跳转。
5. Galgame choices 台词播放期间，Ghost Cursor 保持在 Galgame 入口，不被上一句圆弧演出的残留 timer 带走。
6. 点击 Avatar 互动工具入口时，胶囊输入框不出现额外闪烁；入口 spotlight/cursor 不应被 quickbar 子工具或旧 popover selector 抢走。
7. 最后清理临时菜单、高光和 Ghost Cursor，并完成花瓣收尾。
