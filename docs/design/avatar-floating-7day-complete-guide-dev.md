# 7 日新手教程完整开发文档

本文是 7 日新手教程的工程落地规格，补足现有分日文档中没有逐句写清的高光、Ghost Cursor、情绪动作、跳过按钮和通用生命周期约束。

相关文档：

- `docs/design/avatar-floating-guide-feature-tree.md`
- `docs/design/avatar-floating-day1-home-guide-dev.md`
- `docs/design/avatar-floating-day2-screen-voice-guide-dev.md`
- `docs/design/avatar-floating-day3-agent-guide-dev.md`
- `docs/design/avatar-floating-day4-companion-guide-dev.md`
- `docs/design/avatar-floating-day5-personalization-guide-dev.md`
- `docs/design/avatar-floating-day6-agent-guide-dev.md`
- `docs/design/avatar-floating-day7-graduation-guide-dev.md`
- `docs/design/avatar-floating-pc-global-overlay-migration-plan.md`
- `docs/design/home-yui-guide-lifecycle-modularization.md`

## 代码入口

当前 7 日教程由以下入口共同实现：

| 能力 | 文件 |
| --- | --- |
| Day 1 首页主线 | `static/yui-guide-day1-home-guide.js` |
| Day 2-7 每日 round 配置 | `static/yui-guide-day2-screen-voice-guide.js` 至 `static/yui-guide-day7-graduation-guide.js` |
| 目标解析、时序、真实点击、Ghost Cursor | `static/yui-guide-director.js` |
| 高光渲染 | `static/yui-guide-overlay.js`、`static/tutorial-highlight-controller.js` |
| 接管、外置聊天窗同步 | `static/tutorial-interaction-takeover.js`、`static/app-interpage.js`、`templates/chat.html` |
| 跳过按钮 | `static/tutorial-skip-controller.js`、`static/universal-tutorial-manager.js` |
| 临时切换教程模型并恢复 | `static/tutorial-avatar-reload-controller.js`、`static/universal-tutorial-manager.js` |
| React 聊天窗真实工具按钮 | `frontend/react-neko-chat/src/App.tsx`、`static/app-react-chat-window.js` |
| PC 全局透明教程 overlay（迁移目标） | `docs/design/avatar-floating-pc-global-overlay-migration-plan.md`、`/Users/mac/Code/N.E.K.O.-PC/src/avatar-tool-cursor-service.js` |

所有选择器里的 `${p}` 都由 `YuiGuideDirector.resolveElement()` 按当前悬浮 UI 前缀展开。

## PC 全局透明 Overlay 迁移要求

七日主线在网页端继续使用当前 DOM overlay；在 N.E.K.O.-PC 中，Ghost Cursor、高光框、圆形高光和每日收尾花瓣转场需要迁移到 PC 全局透明 overlay。每日 scene、台词、emotion、operation、真实 UI 点击和完成态写入不改，只把视觉演出层替换为 `YuiGuidePcOverlayBridge` 到 PC 主进程全局 overlay 的链路。

迁移后的导演约束：

1. PC 端全局 overlay 是唯一视觉来源；Pet 页面、聊天窗、Agent HUD 和插件页不再各自叠加教程 cursor 或高光。
2. 所有目标矩形统一转换为 screen 坐标后发送给 PC overlay；overlay 再按 display bounds 渲染到对应透明窗口。
3. overlay 始终点击穿透，skip、真实按钮点击和教程接管白名单仍由原页面和 Manager 处理。
4. 如果 PC bridge 不可用、IPC 超时或运行在网页端，必须自动回退到当前 `YuiGuideOverlay`，不阻塞教程。
5. Day 1-7 每日收尾都复用同一套收尾 cue：收尾台词期间重新高亮聊天窗，约 70% 同步隐藏 Ghost Cursor、清理高光并播放花瓣。
6. 所有可见 Ghost Cursor 位置变化都必须走平滑移动动画；`showAt` 或静默坐标写入只允许用于首次出现前、隐藏状态下的种子坐标，不能让可见 cursor 瞬移。

迁移前必须先完成 [PC 全局透明教程 Overlay 迁移开发计划](avatar-floating-pc-global-overlay-migration-plan.md) 中的 Phase 0 可行性 demo：至少验证聊天窗输入区到模型旁按钮的跨窗口平滑移动、圆形按钮高光、聊天窗圆角矩形高光、每日收尾花瓣 cue 和 skip 清理。

## 通用生命周期硬要求

每一天教程启动后，都必须完整接入 `home-yui-guide-lifecycle-modularization.md` 抽出的通用模块，不允许在每日 scene 里复制这些生命周期逻辑。

| 模块 | 每日教程期间必须生效的行为 |
| --- | --- |
| `TutorialInteractionTakeover` | 教程接管期调用 `setTutorialTakingOver(true)`；只放行 skip、当前演示目标、系统弹窗和必要的真实 UI；外置聊天窗同步禁用/恢复按钮、同步 spotlight/cursor。 |
| `TutorialHighlightController` | 所有圆形、矩形、union、extra、virtual、precise 高光都经由 Director 包装方法调用；scene 切换、skip、destroy、angry exit 时统一清理。 |
| `TutorialInterruptController` | 接管期轻微打断走 `interrupt_resist_light`；有效打断由真实鼠标位移和加速度共同判断；达到阈值走 `interrupt_angry_exit`；angry exit 触发瞬间清理高光和 cursor，台词/演出结束后走 skip 语义，不写完成态。 |
| `TutorialSkipController` | `#neko-tutorial-skip-btn` 在教程全程可见且可点；点击后立刻进入 `handleTutorialSkipRequest()`，再由 Manager 调用 Director skip 和统一 destroy。 |
| `TutorialAvatarReloadController` | 教程开始临时切到 `yui-origin`；正常完成、skip、angry exit、destroy、pagehide、handoff 失败都必须恢复用户原模型和聊天头像身份。 |

### 跳过按钮始终有效

1. `#neko-tutorial-skip-btn` 必须保持 `position: fixed`、最高层级、`pointer-events: auto`，不能被 overlay、花瓣层、插件 handoff 蒙层遮住。
2. `isAllowedTutorialInteractionTarget()` 必须始终把 skip 按钮列为白名单；外置聊天窗或插件页需要转发 skip 时，必须回到首页 Manager 的统一入口。
3. 首次点击后可以禁用按钮防重复提交，但禁用只能发生在 skip 请求已经进入 `handleTutorialSkipRequest()` 之后；不能出现“按钮可见但点击无效”的窗口。
4. skip 期间必须立即停止后续 scene 进展、停止 Ghost Cursor 动画、清理当前高光；不得等待当前台词自然播放完才响应。
5. 插件页本地 skip 控件即使已经触发过 skip，也必须继续拦截 pointer/mouse/touch/click，避免点击穿透到底层页面。

## 高光不重叠原则

1. 每个时刻最多保留一套 primary spotlight；需要 persistent/secondary 时，必须和 primary 框选的 DOM 不相交。
2. 同一目标不能同时出现 action spotlight、extra spotlight、virtual spotlight 或 CSS precise highlight。
3. 大区域和小按钮不能同时框住同一层级。例如聊天窗 composer 区和 composer 内的 Avatar 工具按钮不能同时高亮；必须先清理 composer 区，再切到按钮。
4. 设置弹窗内不允许同时高亮整个弹窗和侧边栏按钮；需要说明弹窗时只高亮侧边栏容器，需要说明开关时只高亮开关本体。
5. Day 3 Avatar 工具阶段只高亮 Avatar 工具按钮，不高亮工具菜单前三个 `.composer-icon-button[data-avatar-tool-id]`；小游戏三个选项如真实出现，只允许圆形高光，不能使用猫耳、猫爪或第二层外框。
6. 收尾 scene 是唯一允许重新回到聊天窗大区域的阶段；进入收尾前必须先清掉当天临时菜单、按钮和侧边栏高光。

## 情绪与动作有效性

教程期间使用 `yui-origin` 模型。每句台词必须声明 emotion，并且只从有效动作池中取动作：

| emotion | 有效用途 | 动作要求 |
| --- | --- | --- |
| `happy` | 欢迎、邀请、撒娇、收尾、鼓励尝试 | 从 happy motion 池随机；不得覆盖 cursor lookAt、真实点击演出、花瓣收尾。 |
| `neutral` | 规则说明、安全边界、隐私、存储 | 从 neutral motion 池随机；动作幅度应小，不抢设置或 HUD 巡游注意力。 |
| `surprised` | 发现入口、冒险感、慌乱前奏 | 从 surprised motion 池随机；Day 5 慌乱 scene 由 `settings-peek-panic` 自定义动作优先。 |
| `sad` | 轻微委屈、未听过声音的承接 | 从 sad motion 池随机；不能升级成 angry exit。 |
| `angry` | 傲娇、强打断、生气退出 | 普通台词可用 angry 池；`interrupt_angry_exit` 必须使用自定义 angry exit 演出，并覆盖正在播放的教程动作 session。 |
| `Idle` | 等待用户选择、低强度停顿 | 只用于无强演出的等待态。 |

如果 motion 资源不存在或当前模型动作锁被自定义演出占用，运行时必须降级为表情或 Idle；不能因为动作缺失阻塞台词、高光、cursor 或 skip。

## 通用时序基线

除非逐句表格另写，Day 2-7 scene 使用 `playAvatarFloatingScene()` 的统一时序：

1. scene 进入：清理上一 scene 的 extra/virtual/geometry 高光；必要时 `prepareAvatarFloatingScene()` 先打开真实弹窗、侧边栏、HUD 或菜单。
2. T+0ms：把台词追加到聊天窗，播放对应 emotion 动作，建立当前 primary/persistent/secondary 高光。
3. T+0ms 至 T+220ms：高光稳定，Ghost Cursor 不立刻抢镜。
4. T+220ms：Ghost Cursor 按 `cursorAction` 移到 primary；`wobble` 停留，`move` 指认，`click` 播放点击动画并按 operation 决定是否调用真实 API/DOM click。
5. 真实操作后：只在 operation 需要时打开/关闭真实 UI，不做无意义的二次 settled 高光。`cleanup` scene 例外，收尾期间可重新高亮聊天窗。
6. narration 结束后：若有按钮选项则等待选择或超时；否则等待 260-420ms 进入下一 scene。
7. `petalTransition: true`：约 70% 台词处触发收尾 cue，同步启动花瓣层、隐藏 Ghost Cursor、清理所有内置/外置高光，不出现高光先消失后花瓣才出现的空档。
8. 跨 scene、跨窗口、外置聊天窗和 PC 全局 overlay 之间的 Ghost Cursor 坐标必须延续上一个可见位置；如果需要预置下一个起点，必须先隐藏 cursor，再写入静默种子坐标。

## 目标选择器字典

| 语义目标 | 首选元素 |
| --- | --- |
| 聊天窗整体 | `#react-chat-window-shell`、`#react-chat-window-root .chat-window`、`#react-chat-window-root` |
| 聊天输入/工具区 | `#react-chat-window-root .composer-panel`、`.composer-input-shell`、`.composer-bottom-tools` |
| 语音按钮 | `#${p}-btn-mic` |
| 屏幕分享按钮 | `#${p}-btn-screen` |
| 猫爪/Agent 按钮 | `#${p}-btn-agent` |
| 设置按钮 | `#${p}-btn-settings` |
| 锁定按钮 | `#${p}-lock-icon` |
| 请她离开/回来 | `#${p}-btn-goodbye`、`#${p}-btn-return` |
| Agent 面板 | `#${p}-popup-agent` |
| Agent 总开关 | `#${p}-toggle-agent-master` |
| 用户插件开关 | `#${p}-toggle-agent-user-plugin` |
| 用户插件管理面板入口 | `#neko-sidepanel-action-agent-user-plugin-management-panel` |
| 任务 HUD | `#agent-task-hud` |
| 设置侧边面板 | `[data-neko-sidepanel-type="chat-settings"]` 等 |
| 主动视觉/隐私 | `#${p}-toggle-proactive-vision` |
| 主动搭话 | `#${p}-toggle-proactive-chat` |
| 记忆入口 | `#${p}-menu-memory` |
| Avatar 工具按钮 | `#react-chat-window-root .composer-emoji-btn` |
| Galgame 按钮 | `#react-chat-window-root .composer-galgame-btn` |
| Avatar 道具菜单前三项 | `#composer-tool-popover .composer-icon-button[data-avatar-tool-id]` 前 3 个 |
| 小游戏选项前三项 | `.composer-choice-slot[data-choice-source="mini_game_invite"] .composer-choice-option` 前 3 个 |
| 外置聊天窗 spotlight | `#yui-guide-chat-spotlight`，kind 为 `window`、`input`、`avatar-tools`、`avatar-tool-items`、`galgame` |

## 主线与支线边界

下面这些功能在现有 7 日设计里出现过，但不属于每日强接管主线的逐句演示；文档必须明确归属，避免后续误加时序：

| 功能 | 归属 | 主线要求 |
| --- | --- | --- |
| 截图、导入图片、粘贴图片 | Day 1 能力背景或剧场后聊天窗支线 | Day 1 主线不逐个高亮聊天窗左侧按钮，不打开附件弹窗。 |
| 字幕翻译、点歌台 | Day 3 剧场后聊天窗支线 | Day 3 主线只高亮 Galgame 与 Avatar 工具，不扫完整工具栏。 |
| 备忘、学习陪伴、生活任务 | 剧场后聊天窗支线或插件支线 | 不塞进 Day 3 主线，不伪造任务状态。 |
| 屏幕来源列表、麦克风列表、空间音频、降噪、增益 | Day 2 后续扩展背景 | Day 2 主线只点击屏幕分享按钮触发真实限制提示，不选择来源、不改设备。 |
| 角色卡、创意工坊、云存档 | Day 5 支线或独立引导 | Day 5 主线只认角色设置、模型管理、声音/API/记忆入口，不跳转深页。 |
| Cookie 登录、遥测 opt-out、云端存储细节 | Day 7 支线或帮助文档 | Day 7 主线只说明长期存放概念，不登录、不上传、不下载、不展示账号或路径细节。 |

## Day 1：初次唤醒、聊天与基础入口

Day 1 使用首页主教程 scene：`intro_basic`、`takeover_capture_cursor`、`takeover_plugin_preview`、`takeover_settings_peek`、`takeover_return_control`。

| 台词/scene | emotion/动作 | 高光与 Ghost Cursor 时序 | 不重叠要求 |
| --- | --- | --- | --- |
| 输入激活前置：“点一下这里...” | `happy`，苏醒/轻欢迎动作优先 | 先 `ensureChatVisible()`；primary 高亮聊天输入区 `.composer-input-shell` 或 `.composer-panel`；Ghost Cursor 显示在输入区中心并 wobble；等待用户真实点击激活音频。 | 不同时高亮整聊天窗；外置聊天窗只用 kind `input`。 |
| 初见问候：“微风、阳光...” | `happy`，苏醒 pose/拥抱类自定义优先 | T+0 保持聊天区/输入区高光；Ghost Cursor 停留在输入区附近；台词结束清理 intro 聊天高光。 | 不切到语音按钮，不打开任何面板。 |
| `intro_basic`：“这里有一个神奇的按钮...” | `happy`，随机 happy，语音按钮 LookAt 优先 | T+0 追加台词；T+16% timeline 执行 `highlightVoiceControl`，primary 切到 `#${p}-btn-mic` 圆形高光；Ghost Cursor 移到按钮中心，只 wobble/指认，不点击。 | 清理聊天输入区高光后再高亮语音按钮。 |
| `takeover_capture_cursor`：“超级魔法按钮出现...” | `surprised` 或 `happy`，魔法开关段可用 surprised | T+14% 高亮猫爪 `#${p}-btn-agent`；T+220ms Ghost Cursor click 猫爪并真实打开 Agent 面板；T+32% 高亮/点击 `#${p}-toggle-agent-master`；T+58% 高亮/点击键鼠控制开关。 | persistent 为 Agent 面板时，primary 只落到当前开关；不把猫爪按钮和面板按钮重叠高亮。 |
| `takeover_plugin_preview`：“这里还有超多好玩的插件...” | `happy`，随机 happy | 保持 Agent 面板；T+24% 高亮并点击 `#${p}-toggle-agent-user-plugin`；T+54% 高亮并点击 `#neko-sidepanel-action-agent-user-plugin-management-panel`；T+76% 可 handoff 到插件面板，首页 cursor 隐藏。 | 插件管理入口高光与用户插件开关高光不能同时存在。 |
| `takeover_settings_peek` 第一段：“在这个只属于我们的空间里...” | `neutral`，温柔说明动作 | T+0 primary 为 `#${p}-btn-settings` 圆形高光；T+220ms Ghost Cursor 移到齿轮；T+54% click 齿轮并真实打开设置弹窗。 | 打开设置前只高亮齿轮；设置弹窗出现后清理齿轮高光。 |
| 设置细节：“不管是说话的温度...” | `neutral`，低幅度动作 | 设置弹窗稳定后 primary 只落到设置侧边栏容器；Ghost Cursor 在容器内短 tour，不展开角色侧栏。 | 不高亮整个弹窗和某个按钮的重叠区域。 |
| 主动搭话：“这个小按钮也很重要哦...” | `happy`，邀请动作 | primary 平滑切到 `#${p}-toggle-proactive-chat`；Ghost Cursor 移到开关，停留/wobble，不点击、不改配置。 | 切换前清理设置侧边栏容器高光。 |
| `takeover_return_control`：“好啦好啦...” | `happy`，挥手/花瓣自定义优先 | 收尾开始前关闭临时面板；T+0 primary 回到聊天窗；T+220ms Ghost Cursor 到聊天窗 wobble；T+70% `returnControl` cue 隐藏 cursor、清理所有高光并播放花瓣。 | 收尾期间不得保留设置/Agent/按钮高光。 |

### Day 1 可选 handoff 与子页落点

Day 1 registry 还注册了 API Key、记忆浏览和插件面板的 handoff scene。它们不是默认每日收尾主线的一部分，但如果由菜单或 handoff token 触发，仍必须遵守同一套时序和 skip 生命周期。

| scene/台词 | emotion/动作 | 高光与 Ghost Cursor 时序 | 不重叠要求 |
| --- | --- | --- | --- |
| `handoff_api_key` | 无普通台词，自动接力 | primary 高亮 `#${p}-menu-api-keys`；Ghost Cursor click；写入 handoff token 后打开 `/api_key`，首页 cursor 隐藏。 | 接力前清理设置/Agent 面板高光；skip 按钮继续有效。 |
| `handoff_memory_browser` | 无普通台词，自动接力 | primary 高亮 `#${p}-menu-memory`；Ghost Cursor click；写入 handoff token 后打开 `/memory_browser`。 | 不展示具体记忆内容。 |
| `handoff_plugin_dashboard` | 无普通台词，自动接力 | primary 高亮 `#${p}-btn-agent` 或插件入口；Ghost Cursor click；写入 handoff token 后打开 `/ui/`。 | 不和 Day 1 插件预览里的管理入口高光叠加。 |
| `api_key_intro`：“到啦...” | `happy`，轻说明动作 | 子页就绪后 primary 高亮 `#coreApiSelect-dropdown-trigger`；Ghost Cursor wobble；不展开下拉、不写 API Key。 | 子页本地高光/cursor 必须能被首页 skip/destroy 清理。 |
| `memory_browser_intro`：“这里会整理...” | `happy`，轻说明动作 | 子页就绪后 primary 高亮 `#memory-file-list`；Ghost Cursor wobble；不打开具体文件、不朗读记忆。 | 不高亮右侧详情和列表项内容。 |
| `plugin_dashboard_landing`：“这里就是插件管理面板...” | `happy`，轻说明动作 | 插件页就绪后 primary 高亮 `#plugin-list`；插件页 runtime 接管本地 cursor；首页 skip 通过插件页桥接回 Manager。 | 插件页不得自行写完成态；done/skip 结果回传首页统一入口。 |

### Day 1 打断分支

打断分支不是正常 scene 顺序，但接管期随时可能触发，必须有明确时序：

| 分支 | emotion/动作 | 高光与 Ghost Cursor 时序 | 结束语义 |
| --- | --- | --- | --- |
| `interrupt_resist_light`：“不要拽我啦...” | `angry` 或 `surprised`，轻微抵抗自定义动作优先 | 触发瞬间暂停当前 scene presentation；保留或短暂淡出当前高光；Ghost Cursor 朝真实鼠标移动方向的反方向播放抵抗/摆脱动作；抵抗台词结束后恢复原 scene 的高光、cursor 和旁白进度。 | 不写完成态，不触发 skip。 |
| `interrupt_angry_exit`：“人类！你真的很没礼貌...” | `angry`，生气退出自定义动作优先 | 触发瞬间停止当前 scene，立即清理所有高光、外置聊天窗高光和 Ghost Cursor；先停止语音/LookAt/慌乱/抵抗/挥手/idle sway 等仍在播放的教程动作，再播放 angry 台词和模型演出。 | 台词/演出结束后调用统一 skip/destroy，不能走正常完成或花瓣收尾。 |

## Day 2：屏幕分享、声音与小窗约定

| scene/台词 | emotion/动作 | 高光与 Ghost Cursor 时序 | 不重叠要求 |
| --- | --- | --- | --- |
| `day2_intro_context` 分支 A：“昨天听见你的声音以后...” | `happy`，亲近动作 | T+0 primary 高亮聊天窗；外置聊天窗用 kind `window`；Ghost Cursor 在聊天窗中心 wobble；台词结束后清理聊天窗高亮，保留或补种聊天窗 cursor 起点并进入屏幕分享 scene。 | 不显示“现在说一句/继续打字”选项，不补高亮语音按钮。 |
| `day2_intro_context` 分支 B：“昨天你一直在噼里啪啦打字...” | `sad`，轻委屈动作 | 同分支 A；台词结束后直接进入下一 scene，不等待选择或超时。 | 不把 sad 当 angry，不触发退出。 |
| `day2_screen_entry`：“在跟我通语音电话的时候...” | `happy`，撒娇邀请动作 | T+0 primary 切到 `#${p}-btn-screen` 圆形高光；T+220ms Ghost Cursor 从聊天窗起点平滑移动到按钮，不能闪现到页面中心；operation `click` 调用真实 `primaryTarget.click()`，触发真实限制提示；360ms 后继续。 | 不打开来源列表，不同时高亮语音按钮。 |
| `day2_screen_entry_invite`：“快让我也看看你眼前的世界...” | `happy`，撒娇邀请动作 | 继续高亮 `#${p}-btn-screen`；Ghost Cursor 在按钮附近 wobble；不再 click。 | 不重复触发屏幕分享限制提示。 |
| `day2_wrap_intro`：“今天的教程到这里就结束了呢。” | `happy`，温柔收尾动作 | 收尾开始前关闭临时提示/弹窗；T+0 primary 回到聊天窗；T+220ms cursor 从屏幕分享按钮位置平滑移动回聊天窗中间并 wobble，不能闪现到页面中心。 | 不触发花瓣，给下一句收尾留出完整转场。 |
| `day2_wrap_companion`：“其实只要能这样陪着你...” | `happy`，温柔收尾动作 | 继续高亮聊天窗；Ghost Cursor 在聊天窗附近 wobble。 | 不触发花瓣，给最终句留出完整转场。 |
| `day2_wrap`：“我们不需要着急...” | `happy`，温柔收尾动作 | 继续高亮聊天窗；T+70% 花瓣 cue 同步启动花瓣层、清理所有高光和 cursor；完成 Day 2。 | 不保留屏幕按钮高光，不出现高亮先消失后花瓣才启动的空档。 |

## Day 3：互动、娱乐与摸得到的陪伴

| scene/台词 | emotion/动作 | 高光与 Ghost Cursor 时序 | 不重叠要求 |
| --- | --- | --- | --- |
| `day3_chat_tools`：“来啦来啦...” | `happy`，兴奋邀请动作 | 首个 scene T+0 primary 高亮 composer 区：`.composer-panel` 优先，其次 `.composer-input-shell`；外置聊天窗 kind `input`；Ghost Cursor 在工具区中心 wobble；台词结束后清理该区域。 | 不高亮整聊天窗，不同时高亮具体工具按钮。 |
| `day3_avatar_tools` / `day3_avatar_tools_props` / `day3_avatar_tools_more`：“在这个小按钮里...”三句 | `happy`，互动玩耍动作 | 准备阶段关闭旧工具菜单；第一句 T+0 primary 圆形高亮 `.composer-emoji-btn`；若折叠，先打开 `.composer-overflow-btn` 找到真实按钮；T+220ms cursor 以 1480ms 移到按钮，播放 click 效果；operation 调用 `reactChatWindowHost.setAvatarToolMenuOpen(true, 'avatar-floating-guide-open-avatar-tool-menu')`；菜单出现后三个短气泡都继续保持 Avatar 工具按钮主高亮，不再高亮前三个 `.composer-icon-button[data-avatar-tool-id]`，cursor 不再依次划过道具。 | Avatar 工具按钮只用圆形高光；道具入口只展示，不出现高亮或 cursor tour；不触发真实道具消耗。 |
| `day3_galgame_games` / `day3_galgame_choices`：“快点开这个【Galgame模式】...”两句 | `surprised`，冒险期待动作 | `cleanupBefore` 先收起 Avatar 道具菜单；第一句 T+0 primary 圆形高亮 `.composer-galgame-btn`；若折叠先打开更多菜单；T+220ms cursor move/wobble，不强制点击；第二句继续保持 Galgame 按钮高亮。若真实存在 `choicePrompt.source === 'mini_game_invite'`，只能在内置聊天窗圆形高亮前三个真实选项并让 cursor 依次划过。 | Galgame 按钮和小游戏选项不能同时与 composer 大区域重叠；不得伪造小游戏局。 |
| `day3_wrap` / `day3_wrap_ready`：“今天带你认识的这些功能...”两句 | `happy`，鼓励尝试动作 | 收尾第一句关闭工具菜单/更多菜单并清理按钮高光；T+0 primary 回聊天窗；第二句继续保持聊天窗高亮与 cursor wobble；第二句 T+70% 花瓣 cue 清理内置/外置高光、工具区 spotlight 和 cursor；完成 Day 3。 | 不保留 Galgame 或道具菜单高光。 |

## Day 4：相处距离、主动陪伴与模型行为

| scene/台词 | emotion/动作 | 高光与 Ghost Cursor 时序 | 不重叠要求 |
| --- | --- | --- | --- |
| `day4_intro_companion`：“今天，就让我悄悄跟上...” | `happy`，温柔靠近动作 | T+0 primary 高亮聊天窗；外置聊天窗 kind `window`；cursor wobble；台词结束清理聊天窗高光。 | 不提前打开设置。 |
| `day4_chat_settings`：“如果有时候你觉得我发消息太频繁...” | `neutral`，规则说明动作 | operation `show-settings-sidepanel:chat-settings` 在 `prepare` 先打开设置并展开 `[data-neko-sidepanel-type="chat-settings"]`；T+0 primary 只框该侧边面板；T+220ms cursor tour 面板前 4 个可见按钮/开关，只指认不改值。 | 不再高亮齿轮或整个设置弹窗。 |
| `day4_animation_tracking`：“看这里看这里...” | `happy`，活泼说明动作 | operation `day4-animation-distance-showcase` 先展开 `animation-settings` 面板；T+0 primary 框面板；T+220ms cursor tour 画质/帧率/跟踪控件；约 48% cue 关闭设置，primary 先切 `#${p}-lock-icon`，cursor wobble，再切 `#${p}-btn-goodbye`，可 secondary 框 `#${p}-btn-return`。 | 动画面板、锁定按钮、离开按钮分阶段互斥。 |
| `day4_privacy_mode`：“当这个按钮关闭时...” | `neutral`，安全边界动作 | `cleanupBefore` 清理前段；operation `show-settings-sidepanel:interval-proactive-vision` 打开该侧边面板；primary 框 `#${p}-toggle-proactive-vision` 或其所在侧边面板；T+220ms cursor move 到开关，不点击。 | 不同时高亮主动搭话或聊天设置。 |
| `day4_wrap`：“真正舒服的陪伴...” | `happy`，温柔收束动作 | 收尾前关闭设置弹窗和侧边栏，恢复用户配置；T+0 primary 回聊天窗；T+70% 花瓣 cue 清理高光/cursor；完成 Day 4。 | 不保留隐私/动画面板高光。 |

## Day 5：个性化与长期配置

| scene/台词 | emotion/动作 | 高光与 Ghost Cursor 时序 | 不重叠要求 |
| --- | --- | --- | --- |
| `day5_character_settings`：“从今天起...” | `happy`，专属感动作 | operation `show-settings-sidepanel:character-settings` 打开设置并展开 `[data-neko-sidepanel-type="character-settings"]`；T+0 primary 框角色设置侧边面板；T+220ms cursor tour 模型/声音/API 入口组，只认门。 | 不跳转子页面，不同时高亮整个设置弹窗。 |
| `day5_character_panic`：“咦，这里居然还能把我换掉吗...” | `surprised`，`settings-peek-panic` 自定义慌乱优先 | primary `#${p}-sidepanel-live2d-manage`，secondary `#${p}-sidepanel-voice-clone`；T+220ms cursor tour；operation 按剩余台词时长运行慌乱演出。 | 只框模型管理和声音克隆入口，不框整个角色面板。 |
| `day5_memory_entry`：“如果你不小心忘记了...” | `angry`，傲娇动作，非 angry exit | operation `show-settings-menu:memory` 打开设置菜单 memory；T+0 primary `#${p}-menu-memory`；T+220ms cursor move/wobble；不打开 `/memory_browser`。 | 不把记忆入口和角色设置面板同时高亮。 |
| `day5_wrap`：“好啦好啦...” | `happy`，期待定制动作 | 收尾前关闭设置和侧边栏；T+0 primary 回聊天窗；T+70% 花瓣 cue 清理所有高光/cursor；完成 Day 5。 | 不保留记忆入口高光。 |

## Day 6：Agent、任务 HUD 与能力节奏

| scene/台词 | emotion/动作 | 高光与 Ghost Cursor 时序 | 不重叠要求 |
| --- | --- | --- | --- |
| `day6_intro_agent`：“噔噔噔噔...” | `happy`，兴奋介绍动作 | T+0 primary 圆形高亮 `#${p}-btn-agent`；T+220ms cursor click；operation `open-agent` 真实打开 `#${p}-popup-agent`；弹窗出现后 persistent 可框 Agent 面板。 | 按钮点击后清理按钮高光，面板和当前控件分层不重叠。 |
| `day6_agent_status_master` | 无台词过渡 scene，`neutral` 等待动作 | 该 scene 的 `voiceKey` 和 `text` 为空，只用于在 Agent 面板已打开后把 primary 切到 `#${p}-toggle-agent-master`；T+220ms cursor move 到总开关，不点击、不授权；停留 260-420ms 后进入下一句。 | persistent 面板大框不得盖住 primary 开关；必要时只保留 primary。 |
| `day6_plugin_side_panel`：“除了之前介绍的功能...” | `happy`，自信炫耀动作 | operation `show-agent-sidepanel:user-plugin:management-panel` 打开 Agent 用户插件侧边栏；primary `#${p}-toggle-agent-user-plugin`，secondary `#neko-sidepanel-action-agent-user-plugin-management-panel`；T+220ms cursor move；`activateSecondaryAction` 可点击管理面板入口，成功后关闭由教程创建的插件窗口。 | 用户插件开关和管理入口可分 primary/secondary，但不能再框整个侧边栏。 |
| `day6_agent_task_hud`：“看这里看这里...” | `happy`，打工热情动作 | `cleanupBefore` 清理 Agent 面板；operation `show-task-hud` 调 `AgentHUD.showAgentTaskHUD()`；T+0 primary `#agent-task-hud`；T+220ms cursor tour HUD 内前 4 个可见控件，如折叠、取消、任务卡。 | 不创建假任务；不与 Agent 面板高光并存。 |
| `day6_wrap`：“呼...” | `happy`，安心收束动作 | 收尾前关闭 Agent 面板、侧边栏和教程临时 HUD，恢复进入前 HUD 状态；T+0 primary 回聊天窗；T+70% 花瓣 cue 清理所有高光/cursor；完成 Day 6。 | 不保留 HUD 高光。 |

## Day 7：毕业、进阶入口与共生约定

| scene/台词 | emotion/动作 | 高光与 Ghost Cursor 时序 | 不重叠要求 |
| --- | --- | --- | --- |
| `day7_memory_review`：“七天前...” | `neutral`，仪式感回顾动作 | `prepare` 打开设置菜单 memory；T+0 primary `#${p}-menu-memory`；T+220ms cursor move 到入口，不打开敏感记忆页。 | 不展示或朗读具体记忆内容。 |
| `day7_memory_control`：“这些小脚印...” | `happy`，温柔积极动作 | 继续 primary `#${p}-menu-memory`，cursor wobble；只说明可整理/可放走，不点击保存、整理或清理。 | 不额外框存储或云存档入口。 |
| `day7_storage_entry`：“还有最后一件事呢...” | `neutral`，收纳说明动作 | `cleanupBefore` 清理 memory 高光；primary 回聊天窗；cursor wobble；不打开云存档、不登录、不上传/下载。 | 不高亮路径、账号或云存档按钮。 |
| `day7_graduation_wrap`：“微风还在窗边...” | `happy`，毕业收束/花瓣优先 | 收尾前清理所有临时状态；T+0 primary 聊天窗；T+220ms cursor wobble；T+70% 最终花瓣 cue 隐藏 cursor、清理所有高光并写入 Day 7 完成态。 | 不保留任何跨页入口高光。 |

## 外置聊天窗等价规则

外置聊天窗不直接使用首页 DOM 高光，必须用 `TutorialInteractionTakeover` 发送 kind：

| 首页语义 | 外置 kind | 要求 |
| --- | --- | --- |
| 聊天窗整体 | `window` | 高光整个独立聊天窗，cursor 在窗口中心 wobble。 |
| 输入/工具区 | `input` | 只高光 composer 区，不高光整窗。 |
| Avatar 工具按钮 | `avatar-tools` | 只高光真实 Avatar 工具按钮。 |
| Avatar 道具项 | `avatar-tool-items` | 当前 Day 3 主线不使用；如后续单独演示道具项，只高光真实道具按钮，不加外层第二圈。 |
| Avatar 工具按钮加道具项 | `avatar-tools-and-items` | 当前 Day 3 主线不使用；如后续需要同时展示工具按钮和道具项，最多包含工具按钮加真实道具，不能再叠加第二个外框。 |
| Galgame | `galgame` | 只高光 Galgame 按钮，不自动改设置。 |
| 小游戏选项 | 现有外置实现暂无独立 kind | 若要在外置聊天窗中演示小游戏三选项，必须新增等价 kind，并只高亮真实 `mini_game_invite` 选项。 |

外置聊天窗同样必须保证 skip 有效。跨窗口 skip 分两类：坐标命中首页 `#neko-tutorial-skip-btn` 时转发坐标并由首页真实按钮 click；明确 skip 源如插件页按钮或 angry exit 直接调用 Manager 统一 skip 入口。

## 验收清单

1. 每句台词都能在本文表格中找到 emotion、动作规则、高光目标和 Ghost Cursor 时序。
2. 所有高光目标都能映射到真实 DOM 或外置 kind；不可见时只允许降级到同组容器，不允许伪造元素。
3. 同一时刻没有重叠 spotlight；尤其是设置弹窗/侧边栏、聊天 composer/工具按钮、Agent 面板/开关、HUD/Agent 面板不能重叠。
4. Day 3 Avatar 工具阶段只持续高亮 Avatar 工具按钮，不高亮三个道具项，也不让 cursor 移动到三个道具项；小游戏三选项如真实出现，只出现纯圆形高光，不出现猫耳、猫爪或第二层边框。
5. 所有真实点击只发生在文档明确允许的位置：Day 2 屏幕分享限制提示、Day 3 Avatar 工具菜单 API 打开、Day 6 Agent/插件入口等；其余只 move/wobble。
6. 每句台词 emotion 都在 `yui-origin` 有效动作池内；自定义演出优先，motion 缺失能降级。
7. `#neko-tutorial-skip-btn` 在教程期间可见、可点、白名单放行；点击后立刻进入统一 skip。
8. skip、destroy、pagehide、angry exit 都会清理高光和 cursor，并恢复用户模型。
9. Day 2-7 收尾都复用 Day 1 花瓣转场语义：70% cue 清理 cursor/highlight，正常完成才播放，skip/angry exit/destroy 不播放正常收尾花瓣。
10. Day 1 handoff 子页、Day 2 选项按钮补高亮、Day 6 空台词过渡 scene 都有明确高光/cursor/skip 时序。
11. 独立或外置页面的本地 spotlight/cursor/skip 只能做等价适配，结果必须回到首页 Manager 的统一生命周期。
