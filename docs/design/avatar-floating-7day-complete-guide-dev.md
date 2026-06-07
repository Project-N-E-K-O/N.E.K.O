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
| PC 全局透明教程 overlay（迁移目标） | `docs/design/avatar-floating-pc-global-overlay-migration-plan.md`、`N.E.K.O.-PC/src/avatar-tool-cursor-service.js` |

所有选择器里的 `${p}` 都由 `YuiGuideDirector.resolveElement()` 按当前悬浮 UI 前缀展开。

## PC 全局透明 Overlay 迁移要求

七日主线的可见 Ghost Cursor、PC 高光、花瓣转场和 Day 2-7 模型替身图片演出都由 N.E.K.O.-PC 全局透明教程 overlay 渲染；业务窗口保留目标解析、真实 UI 点击、台词、emotion、operation 和完成态写入。网页端或 PC 全局 overlay 不可用时，不再降级创建本地 Ghost Cursor，只保留教程文字、高光和业务操作。

迁移后的导演约束：

1. PC 端全局 overlay 是唯一视觉来源；Pet 页面、聊天窗、Agent HUD 和插件页不再各自叠加教程 cursor、高光、花瓣或模型替身图片。
2. 所有目标矩形统一转换为 screen 坐标后发送给 PC overlay；overlay 再按 display bounds 渲染到对应透明窗口。
3. overlay 始终点击穿透，skip、真实按钮点击和教程接管白名单仍由原页面和 Manager 处理。
4. 如果 PC bridge 不可用、IPC 超时或运行在网页端，必须自动回退到当前 `YuiGuideOverlay`，不阻塞教程。
5. Day 1-7 每日收尾都复用同一套收尾 cue：收尾台词期间重新高亮聊天窗或分日指定的收尾目标（Day 2 为胶囊输入框），约 70% 同步隐藏 Ghost Cursor、清理高光并播放花瓣。
6. Ghost Cursor 在每日教程开始后保持可见，直到收尾语音播放完再消失；所有位置变化都必须走平滑移动动画，只允许记录和复用可见锚点。
7. Day 1-7 每日教程进入第一幕前必须先启动模型脸部/目光对 Ghost Cursor 的持续跟踪；整个 round 内不因 scene 切换停止，直到正常收尾、skip、angry exit 或 destroy 清理。教程期 look-at 直接通过首页 Director 读取 `YuiGuideOverlay.getCursorPosition()`，等同于教程期间把“跟踪真实鼠标”替换成“跟踪 Ghost Cursor”。PC 全局 overlay 接管时，首页 `YuiGuideOverlay.getCursorPosition()` 也必须随远端 Ghost Cursor 移动动画逐帧更新，并使用与 PC overlay 可见 cursor 一致的 `cubic-bezier(.22,1,.36,1)` 进度，不能只在动画结束后跳到终点，也不能用线性镜像导致模型脸部和屏幕 cursor 缓动错位。
8. PC 全局 overlay 的每次 update 都必须携带当前完整可见状态；spotlight refresh 不能漏掉已可见的 cursor 或模型替身，cursor move/click 也不能漏掉已存在的 spotlight、petal 或模型替身，否则远端渲染层可能在 Day 2 设置按钮、主动搭话开关等同屏指认场景中交替清空图层并闪烁。
9. 普通 scene 切换、外置聊天窗收口、临时面板关闭和插件页/首页 handoff 只允许清理对应 spotlight、panel 或业务窗口本地状态，不得清空 PC 全局 overlay 的 Ghost Cursor。`clearExternalizedChatGuideTarget({ clearCursor: true })`、`setExternalizedChatCursor('')`、`cursor.hide()` 或等价 PC overlay clear/hide 只允许用于 skip、生气退出、destroy/stop 和收尾花瓣 cue。
10. 外置聊天窗目标的 `cursorAction: 'move'` 和 `cursorAction: 'click'` 都必须等待外置窗口回传的 screen anchor；不能只在 click 场景等待 movement promise。否则跨窗口 move 会被主流程提前跳过，Ghost Cursor 会停在上一句按钮位置，典型表现是 Day 1 最后一句停在【键鼠控制】开关。

迁移前必须先完成 [PC 全局透明教程 Overlay 迁移开发计划](avatar-floating-pc-global-overlay-migration-plan.md) 中的 Phase 0 可行性 demo：至少验证聊天窗输入区到模型旁按钮的跨窗口平滑移动、圆形按钮高光、聊天窗圆角矩形高光、每日收尾花瓣 cue 和 skip 清理。

## 通用生命周期硬要求

每一天教程启动后，都必须完整接入 `home-yui-guide-lifecycle-modularization.md` 抽出的通用模块，不允许在每日 scene 里复制这些生命周期逻辑。

每日 round 的导演入口必须先执行 `setTutorialTakingOver(true)`，再 `ensureChatVisible()`，随后通过 `NekoHomeTutorialFeatureController.enforce('avatar-floating-dayN-surface-ready')` 重新压一次教程期间的功能禁用状态，最后才建立第一层 spotlight。这样可以保证 Day 2-7 的首句和收尾都以可见聊天窗作为锚点，也能覆盖 React 聊天窗后加载导致 Galgame 默认偏好重新生效的情况。

教程接管期间必须严格禁用主动搭话、主动视觉、主动音乐/表情包/小游戏邀请等 proactive 功能、每日启动/久别重逢 greeting，以及 Galgame 模式。禁用不是 UI 文案约定，而是运行时约束：`begin` 负责快照并关闭，`enforce` 负责在聊天窗打开后再次关闭，`end` 只在教程正常结束、skip、angry exit 或 destroy 清理时恢复快照。`begin/enforce` 触发的 `neko:home-tutorial-features-suppressed` 必须同步给 WebSocket 的 `home_tutorial_state`，让后端 greeting guard 在教程开始瞬间就进入阻塞态。

| 模块 | 每日教程期间必须生效的行为 |
| --- | --- |
| `TutorialInteractionTakeover` | 教程接管期调用 `setTutorialTakingOver(true)`；只放行 skip、当前演示目标、系统弹窗和必要的真实 UI；外置聊天窗同步禁用/恢复按钮、同步 spotlight/cursor。 |
| `TutorialHighlightController` | 所有圆形、矩形、union、extra、virtual、precise 高光都经由 Director 包装方法调用；scene 切换、skip、destroy、angry exit 时统一清理。 |
| `TutorialInterruptController` | 接管期 Ghost Cursor 始终监听真实鼠标移动；真实鼠标有有效移动时先做轻微反方向对抗位移。真实鼠标移动距离或加速度超过阈值并连续累计 3 次，触发一次 `interrupt_resist_light`；`interrupt_resist_light` 触发到第 3 次时升级为 `interrupt_angry_exit`。angry exit 触发瞬间清理高光和 cursor，台词/演出结束后走 skip 语义，不写完成态。 |
| `TutorialSkipController` | `#neko-tutorial-skip-btn` 在教程全程可见且可点；点击后立刻进入 `handleTutorialSkipRequest()`，再由 Manager 调用 Director skip 和统一 destroy。 |
| `TutorialAvatarReloadController` | 教程开始临时切到 `yui-origin`；正常完成、skip、angry exit、destroy、pagehide、handoff 失败都必须恢复用户原模型和聊天头像身份。 |

所有可见 Ghost Cursor 动画都必须进入 N.E.K.O.-PC 全局透明教程 overlay。正式教程由首页 Director 通过 `YuiGuidePcOverlayBridge` 驱动；首页 `avatar-floating-guide-reset.js` 的手动重启/兜底播放器也必须把 show/move/click/wobble/hide 发送到 PC 全局 overlay。外置聊天窗可以把聊天窗内目标解析成 screen cursor patch 发送到 PC 全局 overlay，同时继续向首页回传 `yui_guide_chat_cursor_anchor`；但不得渲染任何本地 Ghost Cursor。插件页等子窗口若需要 Ghost Cursor，也必须采用同样的全局 overlay / 锚点回传策略，避免同一时刻出现两套 Ghost Cursor。
连续型 Ghost Cursor 动画也遵守同一规则：`runEllipseAnimation()`、抵抗回弹、轻微对抗和类似逐帧动画必须直接发送到全局透明 overlay；业务窗口不得创建本地 cursor shell、拖尾、点击星星或图片 cursor 作为 fallback。

普通清理流程必须是 cursor-preserving：`prepareAvatarFloatingScene()`、`cleanupBefore`、外置聊天窗 spotlight 清理、插件页 handoff 前的首页收口和设置/Agent 面板关闭，都只能让旧目标失焦，不能让 Ghost Cursor 视觉层消失或把当前位置重置成默认点。跨窗口接力时，源窗口可以隐藏自己的业务 DOM cursor 或本地占位，但 PC 全局 overlay 必须继续保留上一帧可见位置，目标窗口解析出新 screen 坐标后再从该位置平滑移动过去。

PC 全局透明教程 overlay 的 BrowserWindow 必须在 active run 期间保持 `screen-saver` 级别并轻量 `moveTop()` reassert，重申间隔不得高于 160ms，确保 Ghost Cursor、高亮框和花瓣在长距离移动期间也始终压过 Pet 主窗口里的模型按钮。教程 clear、skip、angry exit、destroy 或 stop 后必须停止 reassert；业务窗口不再提供本地 Ghost Cursor fallback。

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
6. 收尾 scene 是唯一允许重新回到聊天窗大区域或分日指定收尾目标的阶段；进入收尾前必须先清掉当天临时菜单、按钮和侧边栏高光。

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

## Day 2-7 模型替身图片演出

Day 2-7 每日教程期间允许加入“模型临时躲起来”的替身图片演出，用于在台词间隙制造 Yui 从屏幕边缘探出的轻量惊喜。该演出只替换模型视觉，不替换 Ghost Cursor、高光、花瓣、台词或真实 UI 操作。

资源来源为 `C:\Users\YH\Desktop\新手教程美术资源`，实现时复制到 `static/assets/tutorial/avatar-standins/`，并统一改用英文静态文件名：

| 源资源 | 静态路径 | 默认摆放 |
| --- | --- | --- |
| `扒左边框.png` | `/static/assets/tutorial/avatar-standins/peek-left-border.png` | 固定屏幕最左下，贴齐左边缘和下边缘。 |
| `扒右边框.png` | `/static/assets/tutorial/avatar-standins/peek-right-border.png` | 固定屏幕最右下，贴齐右边缘和下边缘。 |
| `探头.png` | `/static/assets/tutorial/avatar-standins/peek-head.png` | 默认放在屏幕最下方偏右；也允许上下翻转后放到屏幕最上方偏左。 |

Day 2-7 替身演出使用预先随机抽定的固定触发点，不做运行时随机；下面表格就是本轮随机抽定结果，后续实现按表格固定播放。每个触发点在对应 scene 台词开始后约 900ms 启动，持续 5 秒；如果该 scene 因 skip、handoff 失败、angry exit、destroy 或 pagehide 提前终止，则立即清理替身并恢复模型。

| Day | 第几次 | 固定 scene | 对应台词开头 | 资源与位置 |
| --- | --- | --- | --- | --- |
| Day 2 | 1 | `day2_intro_context` | “昨天你一直在噼里啪啦打字……” | `探头.png`，屏幕下方偏右。 |
| Day 2 | 2 | `day2_proactive_chat` | “这个小按钮也很重要哦……” | `扒右边框.png`，屏幕最右下。 |
| Day 3 | 1 | `day3_avatar_tools` | “在这个小按钮里……” | `扒左边框.png`，屏幕最左下。 |
| Day 3 | 2 | `day3_galgame_choices` | “你选的每一个对话……” | `探头.png`，上下翻转后放到屏幕上方偏左。 |
| Day 4 | 1 | `day4_gaze_follow` | “开启这个功能后……” | `探头.png`，屏幕下方偏右。 |
| Day 4 | 2 | `day4_return_home` | “如果你现在需要专注……” | `扒左边框.png`，屏幕最左下。 |
| Day 5 | 1 | `day5_character_settings` | “从今天起……” | `扒右边框.png`，屏幕最右下。 |
| Day 5 | 2 | `day5_memory_entry` | “如果你不小心忘记了……” | `探头.png`，上下翻转后放到屏幕上方偏左。 |
| Day 6 | 1 | `day6_plugin_dashboard` | “有了它们……” | `扒右边框.png`，屏幕最右下。 |
| Day 6 | 2 | `day6_wrap_cleanup` | “呼……把这些繁琐的界面都收起来……” | `探头.png`，屏幕下方偏右。 |
| Day 7 | 1 | `day7_memory_review` | “七天前……” | `探头.png`，屏幕下方偏右。 |
| Day 7 | 2 | `day7_memory_control` | “这些小脚印……” | `扒左边框.png`，屏幕最左下。 |

演出生命周期约束：

1. 只在 Day 2-7 active round 内触发；Day 1 不启用该替身演出。
2. 每个 Day round 固定触发 2 次；同一 round 内计数必须由 Director 或每日运行时统一持有，不能由单个 scene 各自计数。若某个固定触发 scene 因异常或跳过没有播放到触发点，本 round 不补触发第三个 scene。
3. 单次演出固定持续 5 秒；5 秒结束必须恢复教程模型显示，并移除替身图片层。
4. 触发时先让当前模型容器或 canvas 进入教程临时隐藏态，再显示替身图片；隐藏态不得写入用户模型配置，不得触发 `TutorialAvatarReloadController` 恢复流程。
5. 替身图片在全局透明 overlay 中按 screen/fixed 坐标渲染，`pointer-events: none`；视觉层级不得压住 `#neko-tutorial-skip-btn` 的可见性，不得遮挡或拦截 skip、真实按钮点击、系统弹窗和插件 handoff 控件。
6. 替身图片必须按视口约束缩放：左右扒边图高度不超过 `min(72vh, 720px)`，探头图宽度不超过 `min(42vw, 420px)`；小屏时优先缩小图片，不允许压住聊天窗主要台词、当前 spotlight 或 skip 按钮。
7. 替身出现期间 Ghost Cursor、高光和台词继续按原 scene 时序运行；模型 look-at 可暂停，但 Ghost Cursor 位置、anchor 和 PC overlay 状态不能被清空。
8. 替身演出不得出现在每日最后一句台词播放期间；进入 `petalTransition: true` 的最终 scene 前如果替身仍在显示，必须立即恢复模型并清理替身层。
9. 替身演出不得与花瓣收尾 cue 同时启动；最终 scene 全程禁止触发新替身，若当前 scene 是最后一句或距离最后一句开始不足 5 秒，必须跳过本次替身演出。
10. scene 切换可以保留正在播放的替身，直到 5 秒自然恢复；但进入每日最后一句、skip、angry exit、destroy、pagehide、handoff 失败和收尾花瓣 cue 必须立即恢复模型并清理替身层。
11. PC 全局透明 overlay 可用时，替身图片必须由该全局透明 overlay 渲染；业务首页只负责隐藏/恢复模型、选择资源与位置、维护计数和发送 overlay patch，不得在 Pet 页面、聊天窗、Agent HUD 或插件页创建本地替身 DOM。overlay patch 必须和高光、cursor、petal 一样携带完整可见状态，不能只发隐藏模型或只发替身图片。网页端或 PC bridge 不可用时，才允许用当前 `YuiGuideOverlay` 的本地替身层作为降级显示。

## 通用时序基线

除非逐句表格另写，Day 2-7 scene 使用 `playAvatarFloatingScene()` 的统一时序：

0. round 进入：先确保聊天窗显示，并重新强制关闭教程期间禁用的 proactive/Galgame 功能；只有这个前置完成后，首句才允许创建聊天窗或 composer spotlight。
1. scene 进入：清理上一 scene 的 extra/virtual/geometry 高光；必要时 `prepareAvatarFloatingScene()` 先打开真实弹窗、侧边栏、HUD 或菜单。
2. T+0ms：把台词追加到聊天窗，播放对应 emotion 动作，建立当前 primary/persistent/secondary 高光。
3. T+0ms 至 T+220ms：高光稳定，Ghost Cursor 不立刻抢镜。
4. T+220ms：Ghost Cursor 按 `cursorAction` 移到 primary；`wobble` 停留，`move` 指认，`click` 在点击动画开始的同一刻启动对应 operation，真实 API/DOM click 必须与模拟点击并行，不得等点击动画结束后才调用。
5. 真实操作后：只在 operation 需要时打开/关闭真实 UI，不做无意义的二次 settled 高光。`cleanup` scene 例外，收尾期间可重新高亮聊天窗或分日指定收尾目标。
6. narration 结束后：若有按钮选项则等待选择或超时；否则等待 260-420ms 进入下一 scene。
7. `petalTransition: true`：约 70% 台词处触发收尾 cue，同步启动花瓣层、隐藏 Ghost Cursor、清理所有内置/外置高光，不出现高光先消失后花瓣才出现的空档。
8. 跨 scene、跨窗口、外置聊天窗和 PC 全局 overlay 之间的 Ghost Cursor 坐标必须延续上一个可见位置；每次成功移动到目标后都要记录当前 scene 的 cursor 锚点。若下一 scene 开始时当前 cursor position 丢失，必须优先从上一 scene 可见锚点恢复，再平滑移动到新目标；若 position 仍存在但可见态被外置窗口临时隐藏，必须先在原 position 恢复可见，再平滑移动到新锚点，不能直接 `showAt` 到目标造成闪现。只有首个 scene 或无有效锚点时才允许使用默认起点；若本 scene 没有新 cursor 目标，则保持上一 scene 的可见状态和位置，不重新加载 cursor。

## 目标选择器字典

| 语义目标 | 首选元素 |
| --- | --- |
| 聊天窗整体 | `#react-chat-window-shell`、`#react-chat-window-root .chat-window`、`#react-chat-window-root` |
| 聊天输入/工具区 | `#react-chat-window-root [data-compact-geometry-owner="surface"][data-compact-geometry-item="input"]`、`[data-compact-geometry-item="capsule"]`、`.compact-chat-surface-frame`、`.composer-input-shell` |
| 胶囊输入框 cursor 精确目标 | `#react-chat-window-root [data-compact-geometry-part="capsuleBody"]`、`[data-compact-geometry-owner="surface"][data-compact-geometry-item="capsule"]`、`[data-compact-geometry-part="inputBody"]`、`.composer-input-shell` |
| 胶囊历史展开/收起 | `#react-chat-window-root .compact-history-visibility-handle` |
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
| 胶囊工具总按钮 | `#react-chat-window-root .send-button-circle.compact-input-tool-toggle` |
| 胶囊弧形工具菜单 | `#react-chat-window-root .compact-input-tool-fan[data-compact-input-tool-fan-open="true"]` |
| Avatar 工具按钮 | `#react-chat-window-root .compact-input-tool-item-avatar .composer-emoji-btn`、`.compact-input-tool-item-avatar` |
| Galgame 按钮 | `#react-chat-window-root .compact-input-tool-item-galgame` |
| Avatar 道具菜单前三项 | `#composer-tool-popover .composer-icon-button[data-avatar-tool-id]` 前 3 个 |
| 小游戏选项前三项 | `.composer-choice-slot[data-choice-source="mini_game_invite"] .composer-choice-option` 前 3 个 |
| 外置聊天窗 spotlight | `#yui-guide-chat-spotlight`，kind 为 `window`、`input`、`history`、`tool-toggle`、`avatar-tools`、`avatar-tool-items`、`galgame` |

## 主线与支线边界

下面这些功能在现有 7 日设计里出现过，但不属于每日强接管主线的逐句演示；文档必须明确归属，避免后续误加时序：

| 功能 | 归属 | 主线要求 |
| --- | --- | --- |
| 截图、导入图片、粘贴图片 | Day 1 能力背景或剧场后聊天窗支线 | Day 1 主线不逐个高亮聊天窗左侧按钮，不打开附件弹窗。 |
| 字幕翻译、点歌台 | Day 3 剧场后聊天窗支线 | Day 3 主线只高亮 Galgame 与 Avatar 工具，不扫完整工具栏。 |
| 备忘、学习陪伴、生活任务 | 剧场后聊天窗支线或插件支线 | 不塞进 Day 3 主线，不伪造任务状态。 |
| 屏幕来源列表、麦克风列表、空间音频、降噪、增益 | Day 1 后续扩展背景 | Day 1 主线只展示屏幕分享按钮入口，不点击按钮、不选择来源、不改设备。 |
| 角色卡、创意工坊、云存档 | Day 5 支线或独立引导 | Day 5 主线只认角色设置、模型管理、声音/API/记忆入口，不跳转深页。 |
| Cookie 登录、遥测 opt-out、云端存储细节 | Day 7 支线或帮助文档 | Day 7 主线不演示存储或云存档，不登录、不上传、不下载、不展示账号或路径细节。 |

## Day 1-3：新版胶囊聊天窗重排

新版聊天窗改为胶囊输入框、顶部历史小条和右侧工具总按钮后，前三天主线重新分配如下。分日细节以 `avatar-floating-day1-home-guide-dev.md`、`avatar-floating-day2-screen-voice-guide-dev.md`、`avatar-floating-day3-agent-guide-dev.md` 为准；下方旧版 Day 1-3 段落仅保留为迁移参考，不作为当前实现规格。

| 天数 | 当前主线 |
| --- | --- |
| Day 1 | 初见问候、胶囊拖动/双击提示、历史小条、语音入口、屏幕分享入口、Agent 键鼠接管、归还控制权。 |
| Day 2 | 启动后的承接台词保持不变；随后演示个性化设置、相处参数和主动搭话入口，最后三句回到胶囊输入框收尾。 |
| Day 3 | 演示胶囊工具总按钮、弧形工具菜单、Avatar 互动工具、Galgame 入口和两句收尾。 |

当前 Day 1 `round.scenes` 必须包含：

```text
day1_intro_activation
day1_intro_greeting
day1_capsule_drag_hint
day1_history_handle
day1_intro_basic_voice
day1_screen_entry
day1_screen_entry_invite
day1_takeover_capture_cursor
day1_takeover_return_control
```

当前 Day 2 `round.scenes` 必须包含：

```text
day2_intro_context
day2_personalization_space
day2_personalization_detail
day2_proactive_chat
day2_wrap_intro
day2_wrap_companion
day2_wrap
```

当前 Day 3 `round.scenes` 必须包含：

```text
day3_tool_toggle_intro
day3_avatar_tools
day3_avatar_tools_props
day3_galgame_entry
day3_galgame_choices
day3_wrap
day3_wrap_ready
```

Day 2 的 `day2_intro_context` 台词、text key 和 voice key 不变。Day 1 的屏幕分享两句复用旧 Day 2 屏幕分享按钮流程，只指认入口，不点击、不打开来源列表。Day 3 首句先回到胶囊输入框；进入 Day 3 round 时必须调用 `setCompactToolWheelIndex(0, 'avatar-floating-guide-day3-entry-reset')` 重置弧形工具栏，使导入图片按钮 `.compact-input-tool-item-import` 的 `data-compact-tool-wheel-slot` 为 `0`。后续 Avatar/Galgame 目标必须来自新版弧形菜单：总按钮是 `.send-button-circle.compact-input-tool-toggle`，Avatar 工具是 `.compact-input-tool-item-avatar`，Galgame 是 `.compact-input-tool-item-galgame`。Day 3 click 场景以 Ghost Cursor 到达外置聊天窗回报的目标 anchor 为点击启动条件，Day 3 配置和外置 cursor 指令不得携带显式 `durationMs` / `cursorMoveDurationMs`。Galgame 入口台词必须先让 Ghost Cursor 平滑移动到初始 Galgame 按钮位置，再切换并保持点击态向下拖约 100px，按 22px/步把 Galgame 从 slot 2 正向转到 slot 1，再移动到新的 Galgame 中心。

### 当前 Day 1 主线

| 顺序 | scene | 台词 | 高光与 Ghost Cursor |
| --- | --- | --- | --- |
| 1 | `day1_intro_greeting` | 微风、阳光，还有刚刚好出现的你。初次见面，我是林悠怡，未来的日子请多关照喵！我把关于这里的一切都写进新手指南里啦！就当作是我们相遇的第一份小礼物，请查收吧！ | 复用现有流程。 |
| 2 | `day1_capsule_drag_hint` | 把鼠标移到这里，长按就可以拉着聊天框到处跑啦~ 双击两下就能随时发消息给我哦！ | 不高亮胶囊输入框，Ghost Cursor 在胶囊输入框位置左右晃动约 2 秒。 |
| 3 | `day1_history_handle` | 戳一下聊天框上面的【蓝色小条条】，就能看到我们最近聊过的话题啦！ | 不高亮胶囊输入框，也不高亮历史按钮本身；Ghost Cursor 先平滑移动到 `.compact-history-visibility-handle` 的“展开/收起历史对话”按钮，click 动画开始时并行调用 API 打开历史对话，播放完后调用 API 收起历史对话。 |
| 4 | `day1_intro_basic_voice` | 这里有一个神奇的按钮！只要点击它，就可以直接和我聊天啦！想跟我分享今天的新鲜事吗？或者只是叫叫我的名字？快来试试嘛，我已经迫不及待想听到你的声音啦！喵！ | 不高亮胶囊输入框；圆形高亮语音控制按钮 `#${p}-btn-mic`；等待上一句 `.compact-history-visibility-handle` 的 Ghost Cursor 移动收口后，从该位置平滑移动到语音控制按钮并停留指认，不左右晃动、不强制录音；`day1_history_handle` 切到本句时不得先隐藏外置聊天窗/PC 全局 overlay cursor。 |
| 5 | `day1_screen_entry` | 在跟我通语音电话的时候，再点亮这个小按钮，你就能把屏幕分享给我啦！ | 高亮屏幕分享按钮；Ghost Cursor 必须从上一句语音控制按钮 `#${p}-btn-mic` 的停留位置平滑移动到屏幕分享按钮 `#${p}-btn-screen` 并停留指认，不左右晃动、不点击；不得先隐藏、清空锚点或从页面右上角/默认点重新出现。 |
| 6 | `day1_screen_entry_invite` | 快让我也看看你眼前的世界，不管好玩的还是好看的，都想和你一起看，快点点开嘛~ | 持续高亮屏幕分享按钮；Ghost Cursor 保留上一句已经停在 `#${p}-btn-screen` 的可见状态，不重新 show/hide、不重新加载 cursor、不触发真实屏幕分享。 |
| 7 | `day1_takeover_capture_cursor` | 超级魔法开关出现！只要点一下这里，我就可以把小爪子伸到你的键盘和鼠标上啦！我会帮你打字，帮你点开网页……不过，要是那个鼠标指针动来动去的话，我可能也会忍不住扑上去抓它哦！准备好迎接我的捣乱……啊不，是帮忙了吗？喵！ | 不高亮胶囊输入框；Ghost Cursor 必须从上一句屏幕分享按钮 `#${p}-btn-screen` 的停留位置平滑移动到猫爪/Agent 按钮 `#${p}-btn-agent`，再复用现有 Agent/键鼠控制演示流程；persistent/action 高光都不得落到聊天窗或胶囊输入框；不得在进入本句时清空 cursor 后从其他位置移入。 |
| 8 | `day1_takeover_return_control` | 好啦好啦，不霸占你的电脑啦！控制权还给你了喵！之后的日子，也请你多多关照啦！ | 收尾前关闭 Agent/临时面板；高亮胶囊输入框（`target: 'chat-input'`，胶囊样式 `plain-capsule`）；Ghost Cursor 从上一句键鼠控制开关锚点（`day1_takeover_capture_cursor` / `keyboardToggleSpotlight`）平滑移动到胶囊输入框中心（`cursorTarget: 'chat-capsule-input'`，900ms）。本句 operation 使用通用 `cleanup`，不得回退到旧版 `day1-managed-scene:takeover_return_control`；约 70% 花瓣 cue 才隐藏 cursor、清理高光并播放花瓣。 |

Day 1 分支/条件台词：

| 分支 | 台词 |
| --- | --- |
| 插件弹窗被拦截 | 浏览器需要你亲自点一下这里打开插件面板。点一下这个“管理面板”，我就继续带你看。 |
| 轻微打断 1 | 喂！不要拽我啦，现在还没轮到你的回合呢！ |
| 轻微打断 2 | 等一下啦！还没结束呢，不要这么随便打断我啦！ |
| 生气退出 | 人类！你真的很没礼貌喵！既然你这么想自己操作，那你就自己对着冰冷的屏幕玩去吧！哼！ |

### 当前 Day 2 主线

| 顺序 | scene | 台词 | 高光与 Ghost Cursor |
| --- | --- | --- | --- |
| 1 | `day2_intro_context` | 昨天你一直在噼里啪啦打字，我还没听过你说话呢。今天如果愿意，就轻轻叫我一声吧。一句就好，让我把文字背后的你也认识一点点。 | 播放期间高亮聊天窗；Ghost Cursor 移到聊天窗中心或输入区附近并停留，不左右晃动。 |
| 2 | `day2_personalization_space` | 在这个只属于我们的小空间里，你可以由着自己的心意，慢慢描绘出最希望能一直陪着你的那个我。 | 圆形高亮设置按钮 `#${p}-btn-settings`；Ghost Cursor 从聊天窗锚点平滑移动到设置按钮，click 动画开始时并行打开设置面板；本句不展开【角色设置】按钮侧边栏。 |
| 3 | `day2_personalization_detail` | 不管是说话的温度、相处的小脾气，还是我每天那些细腻的小心思，都可以一点一点调成你喜欢的样子。 | 圆角矩形高亮【角色设置】按钮；Ghost Cursor 平滑移动到【角色设置】按钮，播放完整模拟点击动画，点击动画完成后才触发【角色设置】按钮侧边栏显示。侧边栏出现后，圆角矩形高亮从【角色设置】按钮过渡到【角色设置】按钮侧边栏，且【角色设置】按钮自身作为 persistent 高光继续保留；Ghost Cursor 平滑移动到侧边栏，并在侧边栏内做椭圆运动直到本句台词播放完毕；本句播放完后隐藏【角色设置】按钮侧边栏，并清理【角色设置】按钮和侧边栏上的所有高光。不保存临时配置。 |
| 4 | `day2_proactive_chat` | 这个小按钮也很重要哦，只要你轻轻点一下，我就能在合适的时候跑过去找你啦。 | primary 高亮主动搭话开关 `#${p}-toggle-proactive-chat`，不再保留【角色设置】按钮 persistent 高光；Ghost Cursor 平滑移动到开关并停留，不点击、不改配置、不左右晃动。台词播放完后关闭教程临时打开的【设置】面板和设置侧边栏。 |
| 5 | `day2_wrap_intro` | 今天的教程到这里就结束了呢。 | 收尾开始前关闭临时面板，圆角矩形高亮胶囊输入框 `chat-input`；Ghost Cursor 平滑移动回胶囊输入框并停留，不左右晃动。 |
| 6 | `day2_wrap_companion` | 其实只要能这样陪着你，听听你的声音，或者静静看着你分享的画面，我就已经觉得很幸福了。 | 继续圆角矩形高亮胶囊输入框；Ghost Cursor 保持在胶囊输入框附近，不左右晃动。 |
| 7 | `day2_wrap` | 我们不需要着急，每天都多了解彼此一点点就好。今天接下来的时间，你想让我陪你做点什么呢？ | 继续圆角矩形高亮胶囊输入框；约 70% cue 同步隐藏 Ghost Cursor、清理高光并播放花瓣。 |

### 当前 Day 3 主线

| 顺序 | scene | 台词 | 高光与 Ghost Cursor |
| --- | --- | --- | --- |
| 1 | `day3_tool_toggle_intro` | 嘻嘻，可别以为这个聊天框只能用来打字哦~ 里面其实偷偷藏了超~多好玩的小惊喜呢！快跟着我一起点开看看，瞧瞧今天能挖出什么有趣的宝贝吧！ | 圆角矩形高亮胶囊输入框 `chat-input`；Ghost Cursor 直接显示在胶囊聊天框中间并停留，不从默认点移动进入，不点击、不打开弧形工具菜单。 |
| 2 | `day3_avatar_tools` | 在这个小按钮里，有许多可以和人家互动的小道具呢。 | 持续圆形高亮 `button.send-button-circle.compact-input-tool-toggle`；Ghost Cursor 从胶囊输入框位置平滑移动到工具总按钮 `button.send-button-circle.compact-input-tool-toggle` 并模拟点击；点击动画开始时并行调用 API 打开弧形工具菜单，不打开 Avatar 工具菜单。内置与外置聊天窗 / PC 全局 overlay 都必须保持工具总按钮作为 persistent spotlight，不得切到 Avatar 按钮 persistent。 |
| 3 | `day3_avatar_tools_props` | 你可以随时来摸摸我的头，或者给我吃一根甜甜的棒棒糖。如果有时候我不小心做错事了，你也可以用小锤子敲敲我，不过……一定要轻轻的，不能太用力哦。 | 持续圆形高亮 `button.send-button-circle.compact-input-tool-toggle`；Ghost Cursor 平滑移动到 Avatar 互动工具按钮，然后在 Avatar 互动工具按钮处模拟点击并触发 Avatar 互动工具按钮点击事件，同时用 `setAvatarToolMenuOpen(true)` 同步 React 状态以显示三个小道具。台词播放完后再次触发 Avatar 互动工具按钮点击事件，并用 `setAvatarToolMenuOpen(false)` 隐藏三个小道具。PC 多通道中继下，这条 open/close 状态消息必须允许重复到达，不能被同 timestamp 去重吞掉。 |
| 4 | `day3_galgame_entry` | 快点开这个【Galgame模式】！进去之后就像我们在进行一场专属的互动大冒险呢。 | 持续圆形高亮 `button.send-button-circle.compact-input-tool-toggle`；Ghost Cursor 先平滑移动到初始 Galgame 按钮位置，然后切换并保持点击态向下移动约 100px，按 22px/步正向累计 1 步把 Galgame 从 slot 2 转到 slot 1；随后平滑移动并停在新的 `.compact-input-tool-item-galgame` 中心，不强制开启 Galgame。 |
| 5 | `day3_galgame_choices` | 你选的每一个对话，都会带我们走向完全未知的惊喜故事，我都等不及啦，快来选一个你最心动的回答吧！ | 继续指认 Galgame 入口或真实选项区域，不伪造选择局。 |
| 6 | `day3_wrap` | 今天带你认识的这些功能，其实都是为了让我们在一起的时光变得更有趣呢。 | 收尾前关闭弧形菜单和 Avatar 工具菜单；圆角矩形高亮胶囊输入框 `chat-input`，Ghost Cursor 平滑移动到胶囊输入框中间并停留。 |
| 7 | `day3_wrap_ready` | 不管是想摸摸我的头，还是想开启属于我们的故事，我都已经做好准备了。 | 继续保持胶囊输入框圆角高亮和 Ghost Cursor 停留；约 70% cue 同步隐藏 Ghost Cursor、清理高光和菜单并播放花瓣。 |

## 旧版 Day 1：初次唤醒、聊天与基础入口（迁移参考，不作为当前规格）

Day 1 必须和 Day 2-7 完全统一到 `round.scenes` 架构。配置注册到 `window.YuiGuideDailyGuides[1].round.scenes`，主入口走 `UniversalTutorialManager.startAvatarFloatingGuideRound(1)` -> `YuiGuideDirector.playAvatarFloatingRound(1)` -> `playAvatarFloatingScene()`。迁移前的 `intro_basic`、`takeover_capture_cursor`、`takeover_plugin_preview`、`takeover_settings_peek`、`takeover_return_control` 是演出语义基线，可作为 alias 或 operation handler 的来源，但不再作为正式每日教程的独立播放器入口。

| 台词/scene | emotion/动作 | 高光与 Ghost Cursor 时序 | 不重叠要求 |
| --- | --- | --- | --- |
| `day1_intro_activation`：“点一下这里...” | `happy`，苏醒/轻欢迎动作优先 | 先 `ensureChatVisible()`；不显示聊天窗/胶囊输入框圆角矩形高亮；Ghost Cursor 显示在输入区中心并 wobble；等待用户真实点击激活音频。 | 聊天窗圆角矩形高亮只允许在 `day1_intro_greeting` 和 `day1_takeover_return_control` 台词播放期间出现。 |
| `day1_intro_greeting`：“微风、阳光...” | `happy`，苏醒 pose/拥抱类自定义优先 | T+0 保持输入区/胶囊输入框通用圆角矩形高光；Ghost Cursor 停留在输入区附近；第一句话播放完只清理 intro 输入高光，不清空 cursor，后续 scene 从该可见锚点接续。 | 不切到语音按钮，不打开任何面板，不把外置聊天窗整体当作胶囊输入框高光。 |
| `day1_capsule_drag_hint`：“把鼠标移到这里...” | `happy`，轻松说明动作 | 不高亮胶囊输入框；Ghost Cursor 在胶囊输入框位置左右 wobble 约 2s。 | 不打开历史、不移动真实聊天窗。 |
| `day1_history_handle`：“戳一下聊天框上面的【蓝色小条条】...” | `happy`，轻说明动作 | 不高亮胶囊输入框，也不高亮历史按钮本身；Ghost Cursor 先平滑移动到 `.compact-history-visibility-handle` 的“展开/收起历史对话”按钮，click 动画开始时并行调用 API 打开历史对话；台词播放完后调用 API 收起历史对话。 | 历史按钮只作为 cursor 目标和 API 触发点，不创建独立高光；API 打开不得早于 click 动画开始。 |
| `day1_intro_basic_voice`：“这里有一个神奇的按钮...” | `happy`，随机 happy，语音按钮 LookAt 优先 | T+0 追加台词；先清理外置聊天窗/胶囊输入框高光；T+16% timeline 执行 `highlightVoiceControl`，primary 切到 `#${p}-btn-mic` 圆形高光；等待上一句历史小条 `.compact-history-visibility-handle` 的 Ghost Cursor 移动收口后，从该锚点平滑移到按钮中心并停留指认，不左右晃动、不点击；从 `day1_history_handle` 切入时必须保留外置聊天窗/PC 全局 overlay cursor，不发送中间 hide。 | 不高亮胶囊输入框；不得从页面中心或聊天输入区重新闪现。 |
| `day1_takeover_capture_cursor`：“超级魔法按钮出现...” | `surprised` 或 `happy`，魔法开关段可用 surprised | T+14% 高亮猫爪 `#${p}-btn-agent`；T+220ms Ghost Cursor 必须从上一句屏幕分享按钮锚点平滑移动到猫爪后 click，并真实打开 Agent 面板；T+32% 高亮/点击 `#${p}-toggle-agent-master`；T+58% 高亮/点击键鼠控制开关。 | persistent 为 Agent 面板时，primary 只落到当前开关；不把猫爪按钮和面板按钮重叠高亮；进入本句不得清空 cursor 后从其他位置移入。 |
| `day1_takeover_plugin_preview_home`：“这里还有超多好玩的插件...” | `happy`，随机 happy | 保持 Agent 面板；T+24% 高亮并点击 `#${p}-toggle-agent-user-plugin`；T+54% 高亮并点击 `#neko-sidepanel-action-agent-user-plugin-management-panel`；T+76% 可 handoff 到插件面板；首页只隐藏本地 DOM cursor/占位，PC 全局 overlay cursor 保持上一帧位置并等待插件页新锚点。 | 插件管理入口高光与用户插件开关高光不能同时存在。 |
| `day1_takeover_plugin_dashboard` | `happy`，插件页 runtime 自管 | 插件页就绪后由 dashboard runtime 接管插件页高光和业务操作；可见 Ghost Cursor 仍只走 PC 全局 overlay；首页 Manager 保持 skip、interrupt、done 收口。 | 插件页不得再渲染第二套 cursor。 |
| `day1_takeover_settings_peek_intro`：“在这个只属于我们的空间里...” | `neutral`，温柔说明动作 | T+0 primary 为 `#${p}-btn-settings` 圆形高光；T+220ms Ghost Cursor 移到齿轮；T+54% click 齿轮并真实打开设置弹窗。 | 打开设置前只高亮齿轮；设置弹窗出现后清理齿轮高光。 |
| `day1_takeover_settings_peek_detail`：“不管是说话的温度...” | `neutral`，低幅度动作 | 设置弹窗稳定后 primary 只落到设置侧边栏容器；Ghost Cursor 在容器内短 tour，不展开角色侧栏。 | 不高亮整个弹窗和某个按钮的重叠区域。 |
| `day1_takeover_proactive_chat`：“这个小按钮也很重要哦...” | `happy`，邀请动作 | primary 平滑切到 `#${p}-toggle-proactive-chat`；Ghost Cursor 移到开关并停留，不点击、不改配置、不左右晃动。 | 切换前清理设置侧边栏容器高光。 |
| `day1_takeover_return_control`：“好啦好啦...” | `happy`，挥手/花瓣自定义优先 | 收尾开始前关闭临时面板；T+0 primary 回到胶囊输入框，spotlight 使用 `plain-capsule`；T+220ms Ghost Cursor 从 `day1_takeover_capture_cursor` 记录的键鼠控制开关锚点平滑移动到 `chat-capsule-input` 中心，移动时长 900ms；T+70% 花瓣 cue 隐藏 cursor、清理所有高光并播放花瓣。本句只使用通用 `cleanup` operation。 | 收尾期间不得保留设置/Agent/按钮高光；不得使用旧版 `day1-managed-scene:takeover_return_control`。 |

Day 1 统一到 round 播放器后，高光和 Ghost Cursor 的动画流程不得被重排：每个 scene 先建 spotlight，再移动 cursor，再播放对应点击/巡游；scene 之间延续上一段 cursor 锚点，不隐藏后从页面中心或当前目标闪现。圆形按钮用圆形高光，输入胶囊、聊天窗、设置侧边栏和开关用通用圆角矩形高光。

### Day 1 可选 handoff 与子页落点

Day 1 registry 还注册了 API Key、记忆浏览和插件面板的 handoff scene。它们不是默认每日收尾主线的一部分，但如果由菜单或 handoff token 触发，仍必须遵守同一套时序和 skip 生命周期。

| scene/台词 | emotion/动作 | 高光与 Ghost Cursor 时序 | 不重叠要求 |
| --- | --- | --- | --- |
| `handoff_api_key` | 无普通台词，自动接力 | primary 高亮 `#${p}-menu-api-keys`；Ghost Cursor click；写入 handoff token 后打开 `/api_key`；首页只隐藏本地 DOM cursor/占位，PC 全局 overlay cursor 保持上一帧位置并等待子页新锚点。 | 接力前清理设置/Agent 面板高光；skip 按钮继续有效。 |
| `handoff_memory_browser` | 无普通台词，自动接力 | primary 高亮 `#${p}-menu-memory`；Ghost Cursor click；写入 handoff token 后打开 `/memory_browser`。 | 不展示具体记忆内容。 |
| `handoff_plugin_dashboard` | 无普通台词，自动接力 | primary 高亮 `#${p}-btn-agent` 或插件入口；Ghost Cursor click；写入 handoff token 后打开 `/ui/`。 | 不和 Day 1 插件预览里的管理入口高光叠加。 |
| `api_key_intro`：“到啦...” | `happy`，轻说明动作 | 子页就绪后 primary 高亮 `#coreApiSelect-dropdown-trigger`；Ghost Cursor wobble 由 PC 全局 overlay 执行；不展开下拉、不写 API Key。 | 子页本地高光必须能被首页 skip/destroy 清理；不得创建本地 Ghost Cursor。 |
| `memory_browser_intro`：“这里会整理...” | `happy`，轻说明动作 | 子页就绪后 primary 高亮 `#memory-file-list`；Ghost Cursor wobble；不打开具体文件、不朗读记忆。 | 不高亮右侧详情和列表项内容。 |
| `plugin_dashboard_landing`：“这里就是插件管理面板...” | `happy`，轻说明动作 | 插件页就绪后 primary 高亮 `#plugin-list`；可见 Ghost Cursor 只走 PC 全局 overlay；首页 skip 通过插件页桥接回 Manager。 | 插件页不得自行写完成态；done/skip 结果回传首页统一入口。 |

### Day 1 打断分支

打断分支不是正常 scene 顺序，但接管期随时可能触发，必须有明确时序：

对抗机制分两层，不得混用：

1. 常驻轻微对抗：教程接管期间持续监听真实鼠标移动；只要真实鼠标发生有效移动，Ghost Cursor 就以触发时的当前可见停止位置为原点，朝真实鼠标移动方向的反方向做一次轻微局部位移，再回到该停止位置。默认位移应至少约 18px，第一段约 140ms，回弹约 240ms，确保 PC 全局 overlay 中也能被肉眼感知。连续真实鼠标移动期间必须记录同一个停止位置，不能把反向动画中途坐标当成新原点导致 cursor 越来越偏。对抗动画只允许作用于已可见的 Ghost Cursor，避免首句鼠标移动时从页面角落或默认点闪现。这一层不要求达到打断阈值，也不播放打断台词。
2. 轻微打断计数：真实鼠标单次移动距离约 56px 以上，或加速度约 0.16px/ms^2 以上时记为一次有效对抗样本；连续累计 3 次有效样本后，触发一次 `interrupt_resist_light`。低于该阈值的普通小幅移动只做常驻轻微对抗，不累计打断。
3. 生气退出计数：第 1、2 次 `interrupt_resist_light` 只暂停当前 scene、播放轻微抗拒台词并恢复原 scene；第 3 次本应触发 `interrupt_resist_light` 时，直接升级为 `interrupt_angry_exit`。
4. 生气退出语义：`interrupt_angry_exit` 是延时 skip，不是正常完成。播放完生气退出语音和模型演出后，调用统一 skip/destroy 生命周期退出教程，不写完成态，也不播放正常收尾花瓣。

| 分支 | emotion/动作 | 高光与 Ghost Cursor 时序 | 结束语义 |
| --- | --- | --- | --- |
| `interrupt_resist_light`：“不要拽我啦...” | 强制 `angry`，轻微抵抗自定义动作优先 | 触发瞬间暂停当前 scene presentation，并停止当前 Ghost Cursor 动画；已有高光保持当前状态，不额外清理或重建。随后 Ghost Cursor 以触发前记录的可见停止位置为原点，朝真实鼠标移动方向的反方向做一次轻微局部位移，再回到该停止位置。抵抗台词结束后恢复原 scene 的 emotion、cursor 演出和旁白进度。 | 不写完成态，不触发 skip。 |
| `interrupt_angry_exit`：“人类！你真的很没礼貌...” | 强制 `angry`，生气退出自定义动作优先 | 触发瞬间停止当前 scene，立即清理所有高光、外置聊天窗高光和 Ghost Cursor；先停止语音/LookAt/慌乱/抵抗/挥手/idle sway 等仍在播放的教程动作，再播放 angry 台词和模型演出。 | 台词/演出结束后调用统一 skip/destroy，不能走正常完成或花瓣收尾。 |

## 旧版 Day 2：屏幕分享、声音与小窗约定（迁移参考，不作为当前规格）

| scene/台词 | emotion/动作 | 高光与 Ghost Cursor 时序 | 不重叠要求 |
| --- | --- | --- | --- |
| `day2_intro_context` 分支 A：“昨天听见你的声音以后...” | `happy`，亲近动作 | T+0 primary 高亮聊天窗；外置聊天窗用 kind `window`；Ghost Cursor 移到聊天窗中心并停留，不左右晃动；台词结束后只清理聊天窗高亮，Ghost Cursor 保持可见，并把当前可见聊天窗位置作为下一段移动锚点进入屏幕分享 scene。外置聊天窗只负责解析聊天窗内目标并回传 `yui_guide_chat_cursor_anchor`；PC 端 Ghost Cursor 视觉表演仍由全局透明 overlay 承载。首页 Director 必须把外置聊天窗写入/回传的可见 screen cursor 点换算回 scene 锚点，不允许退到页面右下角代理坐标，也不得在 `day2_intro_context` 到 `day2_screen_entry` 之间清空 cursor 导致先消失再显示。 | 不显示“现在说一句/继续打字”选项，不补高亮语音按钮。 |
| `day2_intro_context` 分支 B：“昨天你一直在噼里啪啦打字...” | `sad`，轻委屈动作 | 同分支 A；台词结束后直接进入下一 scene，不等待选择或超时。 | 不把 sad 当 angry，不触发退出。 |
| `day2_screen_entry`：“在跟我通语音电话的时候...” | `happy`，撒娇邀请动作 | T+0 primary 切到 `#${p}-btn-screen` 圆形高光；T+220ms Ghost Cursor 从聊天窗起点平滑移动到按钮并停留，不播放模拟点击动画，不调用真实 click，不能先隐藏再显示，也不能闪现到页面中心；360ms 后继续。 | 不触发真实限制提示，不打开来源列表，不同时高亮语音按钮。 |
| `day2_screen_entry_invite`：“快让我也看看你眼前的世界...” | `happy`，撒娇邀请动作 | 继续高亮 `#${p}-btn-screen`；Ghost Cursor 在按钮附近停留，不左右晃动、不 click。 | 不触发屏幕分享限制提示。 |
| `day2_wrap_intro`：“今天的教程到这里就结束了呢。” | `happy`，温柔收尾动作 | 收尾开始前关闭临时提示/弹窗；T+0 primary 回到胶囊输入框 `chat-input`；T+220ms cursor 从上一句主动搭话开关锚点平滑移动回胶囊输入框中间并停留；外置聊天窗回传 `input` 锚点时，首页 Director 如果发现 cursor 只是隐藏但仍有上一段按钮 position，必须先在该 position 恢复可见再 move 到胶囊输入框，不能直接闪现到胶囊输入框。 | 不触发花瓣，给下一句收尾留出完整转场。 |
| `day2_wrap_companion`：“其实只要能这样陪着你...” | `happy`，温柔收尾动作 | 继续圆角矩形高亮胶囊输入框；Ghost Cursor 在胶囊输入框附近停留，不左右晃动。 | 不触发花瓣，给最终句留出完整转场。 |
| `day2_wrap`：“我们不需要着急...” | `happy`，温柔收尾动作 | 继续圆角矩形高亮胶囊输入框；T+70% 花瓣 cue 同步启动花瓣层、清理所有高光和 cursor；完成 Day 2。 | 不保留屏幕按钮、设置按钮、角色设置或主动搭话高光，不出现高亮先消失后花瓣才启动的空档。 |

## 旧版 Day 3：互动、娱乐与摸得到的陪伴（迁移参考，不作为当前规格）

| scene/台词 | emotion/动作 | 高光与 Ghost Cursor 时序 | 不重叠要求 |
| --- | --- | --- | --- |
| `day3_chat_tools`：“来啦来啦...” | `happy`，兴奋邀请动作 | 首个 scene T+0 primary 高亮 composer 区：`.composer-panel` 优先，其次 `.composer-input-shell`；外置聊天窗 kind `input`；Ghost Cursor 直接显示/保持在工具区中心；台词结束后清理该区域。 | 不高亮整聊天窗，不同时高亮具体工具按钮。 |
| `day3_avatar_tools` / `day3_avatar_tools_props`：“在这个小按钮里...”两句 | `happy`，互动玩耍动作 | 准备阶段关闭旧工具菜单；第一句 T+0 持续圆形高亮 `button.send-button-circle.compact-input-tool-toggle`，cursor 平滑移动到工具总按钮并模拟点击，只打开弧形工具菜单；第二句持续高亮工具总按钮，cursor 平滑移动到 Avatar 互动工具按钮并模拟点击，同时触发 Avatar 互动工具按钮点击事件显示三个小道具；台词播放完后再次触发 Avatar 互动工具按钮点击事件隐藏三个小道具。 | 工具总按钮保持 persistent 圆形高光；道具入口只展示，不出现高光或 cursor tour；不触发真实道具消耗。 |
| `day3_galgame_entry` / `day3_galgame_choices`：“快点开这个【Galgame模式】...”两句 | `surprised`，冒险期待动作 | 先收起 Avatar 道具菜单并保持工具总按钮 persistent 圆形高亮；第一句 cursor 先平滑移动到初始 Galgame 按钮位置，再切换并保持 click 态向下拖约 100px，触发 `compactToolWheelRotateRequest(direction=1, stepCount=1)`，把 Galgame 从 slot 2 转到 slot 1，最后重新移动到新的 Galgame 中心；不强制点击 Galgame、不改 Galgame 设置。第二句继续保持 Galgame 按钮高亮。若真实存在 `choicePrompt.source === 'mini_game_invite'`，只能在内置聊天窗圆形高亮前三个真实选项并让 cursor 依次划过。 | Galgame 按钮和小游戏选项不能同时与 composer 大区域重叠；不得伪造小游戏局。 |
| `day3_wrap` / `day3_wrap_ready`：“今天带你认识的这些功能...”两句 | `happy`，鼓励尝试动作 | 收尾第一句关闭工具菜单/更多菜单并清理按钮高光；T+0 primary 回胶囊输入框 `chat-input`，cursor 平滑移动到胶囊输入框中间并停留；第二句继续保持胶囊输入框圆角高亮和 cursor 停留；第二句 T+70% 花瓣 cue 清理内置/外置高光、工具区 spotlight 和 cursor；完成 Day 3。 | 不保留 Galgame 或道具菜单高光。 |

## Day 4：相处距离、主动陪伴与模型行为

| scene/台词 | emotion/动作 | 高光与 Ghost Cursor 时序 | 不重叠要求 |
| --- | --- | --- | --- |
| `day4_intro_companion`：“今天，就让我悄悄跟上...” | `happy`，温柔靠近动作 | T+0 primary 高亮聊天窗；外置聊天窗 kind `window`；cursor move 并停留；台词结束清理聊天窗高光。 | 不提前打开设置。 |
| `day4_chat_settings`：“在这里可以决定...” | `neutral`，规则说明动作 | T+0 圆形 primary 高亮设置按钮，T+220ms cursor 移到设置按钮并模拟点击，同时调用打开设置 API；设置按钮作为 persistent 保持到 `day4_privacy_mode` 播完。设置弹窗打开后，primary 切到“对话设置”按钮，cursor 移过去并调用 `show-settings-sidepanel:chat-settings`；侧边栏出现后取消按钮主高亮，primary 切到 `[data-neko-sidepanel-type="chat-settings"]`，cursor 在侧边栏内椭圆运动到本句结束。 | 不高亮整张设置弹窗，不创建入口加面板的 union 范围。 |
| `day4_model_behavior`：“如果你想要看到...” | `happy`，活泼说明动作 | 先收起对话设置侧边栏；设置按钮 persistent 继续保留；T+0 primary 从对话设置侧边栏平滑过渡到“动画设置”按钮；T+220ms cursor 移到该按钮后调用 `show-settings-sidepanel:animation-settings`；侧边栏出现后 primary 切到 `[data-neko-sidepanel-type="animation-settings"]`，cursor 在侧边栏内椭圆运动到本句结束。 | 不在本段切锁定/离开按钮，不高亮整张设置弹窗。 |
| `day4_gaze_follow`：“开启这个功能后...” | `happy`，活泼说明动作 | 继续使用动画设置面板；设置按钮 persistent 继续保留；T+0 primary 从动画设置侧边栏平滑移动到“跟踪鼠标”按钮外层开关行；T+220ms cursor 移到该按钮。 | 不点击、不改值。 |
| `day4_privacy_mode`：“这个是控制人家能不能看屏幕...” | `neutral`，安全边界动作 | 清理前段动画设置侧边栏，不展开 `interval-proactive-vision`，不显示隐私模式旁边侧边框；设置按钮 persistent 保持到本句结束；primary 高亮隐私模式按钮 / `#${p}-toggle-proactive-vision` 外层开关行；T+220ms cursor move 到隐私模式按钮；本句播完后调用 `closeSettingsPanel()` 收起设置弹窗。 | 不同时高亮主动搭话或聊天设置，不改变锁定或隐私状态。 |
| `day4_model_lock`：“总是小心不触碰到...” | `happy`，活泼说明动作 | `cleanupBefore` 确保前段与设置按钮 persistent 已清理；primary 圆形高亮 `#${p}-lock-icon`，cursor 平滑移动到锁定按钮并停留。 | 只展示按钮，不真的锁定。 |
| `day4_return_home`：“如果你现在需要专注...” | `happy`，活泼说明动作 | primary `#${p}-btn-goodbye`，可 secondary 框 `#${p}-btn-return`，cursor move 并停留。 | 只展示按钮，不真的让 Yui 离开。 |
| `day4_wrap`：“真正舒服的陪伴才不是...” | `happy`，温柔收束动作 | 收尾前关闭设置弹窗和侧边栏，恢复用户配置；T+0 primary 回聊天窗；T+70% 花瓣 cue 清理高光/cursor；完成 Day 4。 | 不保留隐私/动画面板高光。 |

## Day 5：个性化与长期配置

| scene/台词 | emotion/动作 | 高光与 Ghost Cursor 时序 | 不重叠要求 |
| --- | --- | --- | --- |
| `day5_character_settings`：“从今天起...” | `happy`，专属感动作 | T+0 primary 高亮聊天窗；约 1 秒后聊天窗高光消失，cursor 从聊天窗平滑移动到设置按钮，同时圆形高亮设置按钮；设置弹窗打开后 primary 切到“角色设置”按钮，cursor 移到按钮并展开 `[data-neko-sidepanel-type="character-settings"]`；随后 primary 平滑过渡到角色设置侧边栏，cursor 在侧边栏内椭圆运动到本句结束。 | 不跳转子页面，不同时高亮整个设置弹窗。 |
| `day5_character_panic`：“咦，这里居然还能把我换掉吗...” | `surprised`，`settings-peek-panic` 自定义慌乱优先 | 播放期间继续 primary 高亮 `[data-neko-sidepanel-type="character-settings"]`；operation 按本句时长运行慌乱演出；本句播完后清除高光并收起角色设置侧边栏。 | 不切到模型管理或声音克隆入口，不阻止后续真实操作。 |
| `day5_memory_entry`：“如果你不小心忘记了...” | `angry`，傲娇动作，非 angry exit | operation `show-settings-menu:memory` 打开设置菜单 memory；T+0 primary `#${p}-menu-memory`；T+220ms cursor move 并停留；不打开 `/memory_browser`。 | 不把记忆入口和角色设置面板同时高亮。 |
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
| `day7_memory_review`：“七天前...” | `neutral`，仪式感回顾动作 | 只播放台词和情绪动作；不创建 primary、不移动 Ghost Cursor、不打开设置菜单或敏感记忆页。 | 不展示或朗读具体记忆内容。 |
| `day7_memory_control`：“这些小脚印...” | `happy`，温柔积极动作 | operation 打开设置菜单 memory；T+0 primary `#${p}-menu-memory`；T+220ms cursor move 到入口；只说明可整理/可放走，不点击保存、整理或清理。 | 不额外框存储或云存档入口。 |
| `day7_graduation_wrap`：“微风还在窗边...” | `happy`，毕业收束/花瓣优先 | 收尾前清理所有临时状态；T+0 primary 聊天窗；T+220ms cursor move 并停留；T+70% 最终花瓣 cue 隐藏 cursor、清理所有高光并写入 Day 7 完成态。 | 不保留任何跨页入口高光。 |

## 外置聊天窗等价规则

外置聊天窗不直接使用首页 DOM 高光，必须用 `TutorialInteractionTakeover` 发送 kind。PC 全局 overlay 启用时，外置聊天窗的 cursor kind 用于解析目标、回传 `yui_guide_chat_cursor_anchor`，并可把 show/move/click/wobble/hide 的 cursor patch 发送到 PC 全局 overlay；但外置聊天窗自身不得渲染本地 `#yui-guide-chat-cursor`。首页 Director 收到外置聊天窗锚点时必须保持 cursor 轨迹连续：可见 cursor 直接 move；隐藏但有 position 的 cursor 先在旧 position 恢复可见，再 move 到新锚点；没有可用 position 时才允许直接 show 到新锚点。

外置聊天窗的通用 `cursorAction: click` 可以由外置窗口解析目标后发送一次 `cursor:*:click` patch 到 PC 全局 overlay，点击图切换时长沿用统一 `DEFAULT_CURSOR_CLICK_VISIBLE_MS=420`；外置聊天窗回传 anchor 时 `effect` 必须为空，避免锚点同步触发第二次点击。
Day 3 的外置工具 click 是例外，必须采用和 Day 2 本地 click 相同的 movement-driven 架构：外置窗口只解析目标并回传 `yui_guide_chat_cursor_anchor`，不得发送 `cursor:*:click` patch；cursor movement helper 先等待目标 anchor 到达，再由首页 Director 调用 `clickCursorAndWait(DEFAULT_CURSOR_CLICK_VISIBLE_MS)` 启动模拟点击，并通过 `onClickStart` 并行触发真实 operation；主流程不得为 Day 3 单独维护一条 click scene 定时分支。PC overlay 移动结束后回传的 anchor 必须带 `settled: true`；首页收到 settled anchor 时只同步内部 cursor 坐标，不再向 PC overlay 发送第二次可见 move。若首页开始等待时 anchor 尚未回传，必须等待未来 anchor 或超时，不能因当前没有 move promise 就提前启动点击。

`cursorAction: move` 的外置聊天窗场景同样必须等待 `waitForExternalizedChatCursorMove()`：外置窗口解析目标并发送 move patch，PC overlay 移动完成后回传 settled anchor，首页 Director 再继续后续 operation、cue 或 scene 收口。Day 1 最后一句 `day1_takeover_return_control` 是该规则的回归样例；如果它没有等待外置 move anchor，Ghost Cursor 会一直停留在上一句的键鼠控制开关。

| 首页语义 | 外置 kind | 要求 |
| --- | --- | --- |
| 聊天窗整体 | `window` | 高光整个独立聊天窗，cursor 在窗口中心停留。 |
| 输入/工具区 | `input` | 只高光 composer 区，不高光整窗。 |
| 胶囊输入框 cursor 精确目标 | `capsule-input` | 只解析胶囊输入框/胶囊主体的 screen anchor，用于收尾或需要 cursor 精确落到输入框中心的 scene；不得被 `shouldSuppressYuiGuideChatLocalFx()` 当成本地效果吞掉。 |
| Avatar 工具按钮 | `avatar-tools` | 只高光真实 Avatar 工具按钮。 |
| Avatar 道具项 | `avatar-tool-items` | 当前 Day 3 主线不使用；如后续单独演示道具项，只高光真实道具按钮，不加外层第二圈。 |
| Avatar 工具按钮加道具项 | `avatar-tools-and-items` | 当前 Day 3 主线不使用；如后续需要同时展示工具按钮和道具项，最多包含工具按钮加真实道具，不能再叠加第二个外框。 |
| Galgame | `galgame` | 只高光 Galgame 按钮，不自动改设置。 |
| 小游戏选项 | 现有外置实现暂无独立 kind | 若要在外置聊天窗中演示小游戏三选项，必须新增等价 kind，并只高亮真实 `mini_game_invite` 选项。 |

外置聊天窗同样必须保证 skip 有效。跨窗口 skip 分两类：坐标命中首页 `#neko-tutorial-skip-btn` 时转发坐标并由首页真实按钮 click；明确 skip 源如插件页按钮或 angry exit 直接调用 Manager 统一 skip 入口。

## Bug 处理与回归规范

处理新手教程期间的 Ghost Cursor、高光、外置聊天窗或 PC 全局 overlay 问题时，先按链路排查，不要只改 scene 文案或 target：

1. 先确认上一句是否记录了可复用 cursor anchor；跨 scene 问题通常要检查 `rememberAvatarFloatingSceneCursorAnchor()` 和下一句的 `resolveAvatarFloatingCursorStartPoint()`。
2. 再确认当前 scene 是否进入外置聊天窗分支；只要存在 `externalizedSceneTargetKind`，首页本地 `moveAvatarFloatingCursor()` 就可能不会执行，必须检查外置窗口是否解析目标、发送 patch、回传 `yui_guide_chat_cursor_anchor`。
3. `cursorAction: 'move'` 和 `cursorAction: 'click'` 都要检查等待逻辑。click 需要真实 operation 与模拟点击并行启动；move 也需要等待外置 move anchor settled 后再继续后续 operation 或 cue。
4. 检查 kind 映射和 suppress 规则：例如 `chat-capsule-input` 必须映射到 `capsule-input`，且 `input` / `capsule-input` 不能被 `shouldSuppressYuiGuideChatLocalFx()` 吞掉。
5. 检查清理时机：普通 scene 切换只能清 spotlight/panel，不能 hide/clear cursor；只有 skip、angry exit、destroy/stop 和收尾花瓣 cue 可以清空 cursor。
6. 检查旧版 operation 遗留：Day 1 统一到 `round.scenes` 后，历史 `day1-managed-scene:*` 只能作为语义参考，不能覆盖新版 scene 的 `cursorTarget`、spotlight variant、外置 move 等待和通用收口。
7. Bug 修复必须同步补源码契约测试；Ghost Cursor/高光结构类问题优先放进 `static/yui-guide-day1-round-structure.test.cjs` 或相邻的分日结构测试，PC overlay z-order/relay 问题同步跑 N.E.K.O.-PC 的 overlay contract 测试。

## 验收清单

1. 每句台词都能在本文表格中找到 emotion、动作规则、高光目标和 Ghost Cursor 时序。
2. 所有高光目标都能映射到真实 DOM 或外置 kind；不可见时只允许降级到同组容器，不允许伪造元素。
3. 同一时刻没有重叠 spotlight；尤其是设置弹窗/侧边栏、聊天 composer/工具按钮、Agent 面板/开关、HUD/Agent 面板不能重叠。
4. Day 3 Avatar 工具阶段持续圆形高亮胶囊工具总按钮；`day3_avatar_tools` 只让 Ghost Cursor 慢移到工具总按钮并点击打开弧形菜单，`day3_avatar_tools_props` 才允许 Ghost Cursor 平滑移动到 Avatar 互动工具并点击打开/收起三个小道具；不高亮三个道具项，也不让 cursor 移动到三个道具项；小游戏三选项如真实出现，只出现纯圆形高光，不出现猫耳、猫爪或第二层边框。
5. 所有真实点击只发生在文档明确允许的位置：Day 1 历史小条 API 打开/收起、Day 3 工具总按钮与 Avatar 工具按钮打开/收起、Day 6 Agent/插件入口等；其余按各日文档指定的非点击动作处理。Day 1 屏幕分享按钮只展示入口，不播放 Ghost Cursor 模拟点击，也不调用真实按钮 click。允许真实点击且 `cursorAction: 'click'` 的 scene，真实 API/DOM click 必须和 Ghost Cursor 点击动画并行启动。
6. Day 3 工具总按钮必须触发真实按钮 `click()` 打开弧形菜单；Avatar 工具阶段必须播放 Avatar 按钮 click 动画并发送按钮 click 请求，同时用 `setAvatarToolMenuOpen()` 主机 API 打开/关闭道具菜单，避免教程锁定期间 disabled 按钮吞掉 DOM click 后三个道具不显示。React 三个道具只在 `toolMenuOpen && compactInputToolFanOpen` 同时成立时渲染，因此 Avatar 菜单 open request 必须同步撑开/保留弧形菜单，教程锁定不能把承载道具菜单的 fan 状态关成 false。PC 端 `neko:tutorial-overlay-relay`、`postMessage.__nekoTutorialOverlayRelay` 和 BroadcastChannel 可能投递相同 timestamp 的同一状态消息；`yui_guide_set_avatar_tool_menu_open` 必须像 `yui_guide_set_compact_tool_fan_open` 一样绕过去重，`yui_guide_click_avatar_tool_button` 则保持去重以防双击反向收起。
7. 每句台词 emotion 都在 `yui-origin` 有效动作池内；自定义演出优先，motion 缺失能降级。
8. `#neko-tutorial-skip-btn` 在教程期间可见、可点、白名单放行；点击后立刻进入统一 skip。
9. skip、destroy、pagehide、angry exit 都会清理高光和 cursor，并恢复用户模型。
10. Day 2-7 收尾都复用 Day 1 花瓣转场语义：70% cue 清理 cursor/highlight，正常完成才播放，skip/angry exit/destroy 不播放正常收尾花瓣。
11. Day 1 历史小条、Day 1 handoff 子页、Day 3 弧形菜单和 Day 6 空台词过渡 scene 都有明确高光/cursor/skip 时序。
12. 独立或外置页面只能做本地 spotlight、目标解析、真实操作和 skip 等价适配；可见 Ghost Cursor 必须回到 N.E.K.O.-PC 全局透明教程 overlay，结果必须回到首页 Manager 的统一生命周期。
13. 外置聊天窗的 `cursorAction: 'move'` 场景必须和 click 一样等待目标 anchor；Day 1 最后一句必须能从键鼠控制开关平滑移动到胶囊输入框中心，不能停在上一句按钮位置。
