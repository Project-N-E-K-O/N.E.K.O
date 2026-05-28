# Day 1 首页 Yui 新手教程开发文档

本文把 `avatar-floating-guide-feature-tree.md` 中 Day 1 的“初次唤醒、聊天与基础入口”落到当前首页 Yui 教程实现上。Day 1 不新增 `AVATAR_FLOATING_GUIDE_ROUNDS[1]`，继续复用现有首页教程 `HOME_SCENE_ORDER`。

相关文档：

- `docs/design/avatar-floating-guide-feature-tree.md`
- `docs/design/home-yui-guide-text-highlight-cursor-flow.md`
- `docs/design/home-yui-guide-lifecycle-modularization.md`
- `docs/design/home-tutorial-yui-guide-performance-owner-stage-breakdown.md`

## 目标体验

Day 1 使用首因效应建立“她很鲜活，也很好接近”的第一印象。主线只让用户记住三件事：

1. 悠怡可以聊天，也期待听见用户声音。
2. 猫爪是未来帮忙做事的入口，但需要用户授权。
3. 设置里可以慢慢调整角色、声音、记忆、主动搭话和相处方式。

总剧本的功能清单仍提到文本输入、语音入口、截图/导入图片/粘贴图片、翻译与点歌入口提示、Agent 按钮预览、插件管理预览、设置一瞥。当前 Day 1 小剧场只明确安排“聊天输入、语音、猫爪粗略介绍、设置与主动搭话”四类演出；截图、导入图片、翻译和点歌不要强塞进主线，可后续作为聊天窗支线或 Day 3 娱乐工具补充。

导演校准：当前聊天窗底部左侧已有导入图片和截图按钮，右侧有 Galgame、字幕翻译、点歌和 Avatar 互动工具。Day 1 只在台词层告诉用户“可以把内容主动递给她”，镜头不扫完整工具栏，避免首日第一印象变成按钮说明书；这些真实按钮留到 Day 3 或剧场后支线再展示。

## 现有代码入口

Day 1 主线来自 `static/yui-guide-steps.js` 的 `HOME_SCENE_ORDER`：

```text
intro_basic
takeover_capture_cursor
takeover_plugin_preview
takeover_settings_peek
takeover_return_control
```

运行时由 `static/yui-guide-director.js` 负责：

- 输入框激活与首句正式旁白。
- 语音按钮高亮与 Ghost Cursor 指向。
- 猫爪按钮、Agent 总开关、键鼠控制开关自动演示。
- 用户插件入口和插件管理面板 handoff。
- 设置面板入口、设置侧边栏、主动搭话入口展示和归还控制权。

生命周期能力必须继续复用：

- `TutorialInteractionTakeover`
- `TutorialHighlightController`
- `TutorialInterruptController`
- `TutorialSkipController`
- `TutorialAvatarReloadController`

不要在 Day 1 专属逻辑里复制接管、高亮、打断、跳过或临时切模逻辑。

## 通用生命周期复用

Day 1 是首页 Yui 主教程，必须完整复用 `home-yui-guide-lifecycle-modularization.md` 中定义的五类通用模块。Day 1 专属代码只负责 scene 编排、DOM selector、Ghost Cursor 路径和真实 UI 点击。

| 通用能力 | Day 1 使用方式 | 禁止事项 |
| --- | --- | --- |
| `TutorialInteractionTakeover` | 输入框激活后进入 takeover；猫爪、插件预览、设置一瞥和归还控制权期间统一调用 `setTutorialTakingOver(true/false)`；外置聊天窗模式同步按钮禁用和 spotlight。 | 不在 Day 1 代码里重新注册 document 级鼠标/触控守卫，不复制正脸锁逻辑。 |
| `TutorialHighlightController` | 语音按钮圆形 spotlight、猫爪/设置 retained extra、开关 virtual spotlight、设置侧边栏 union spotlight 全部通过 Director 包装方法调用；同一目标同一时刻只保留一套主 spotlight。 | 不手写虚拟高亮 DOM，不创建后再隐藏重复高亮，不手动残留 `data-yui-guide-spotlight-*` 属性。 |
| `TutorialInterruptController` | takeover 场景中轻微打断走 `interrupt_resist_light`，生气退出走 `interrupt_angry_exit`；抵抗结束后恢复当前 presentation。 | 不把 angry exit 当成正常完成；触发瞬间必须清理当前高亮和 Ghost Cursor，语音后走统一 skip。 |
| `TutorialSkipController` | Manager 显示 `#neko-tutorial-skip-btn`，skip 统一进入 `handleTutorialSkipRequest()`。插件 dashboard 的 skip 请求必须转回首页 Manager。 | 不在插件 handoff 或 scene 内拼新的 skip teardown。 |
| `TutorialAvatarReloadController` | Day 1 启动时用 `beginTutorialAvatarOverride()` 切到教程模型；正常完成、skip、angry exit、destroy 时用 `restoreTutorialAvatarOverride()` 恢复用户模型和聊天头像。 | 不直接改 Manager 内部 override 状态，不绕过恢复流程。 |

页面专属白名单仍留在 Director：输入框激活、skip 按钮、插件管理面板手动打开、系统弹窗等允许点击目标由 `isAllowedTutorialInteractionTarget()` 和 `isSystemDialogInteractionTarget()` 判断。

## 模型动作与情绪随机池

Day 1 启动时由 `TutorialAvatarReloadController` 临时切换到 `yui-origin` Live2D。普通台词按情绪从 `yui-origin` 内置动作池随机播放：`happy` 12 个、`sad` 6 个、`angry` 7 个、`neutral` 7 个、`surprised` 5 个、`Idle` 3 个。

Day 1 现有自定义动作必须保留并优先：苏醒 pose、输入激活/语音按钮 LookAt、猫爪接管期间 Ghost Cursor LookAt、插件 dashboard handoff、设置一瞥慌乱动作、归还控制权挥手和花瓣转场。随机动作只在没有抢占这些自定义 motion/params/lookAt/expression 锁时播放。

| 台词段落 | 情绪分类 | 随机动作规则 |
| --- | --- | --- |
| 初见问候：“微风、阳光……” | `happy` | 可随机 happy motion；苏醒 pose 和爱心/拥抱演出优先。 |
| 语音入口：“这里有一个神奇的按钮……” | `happy` | 可随机 happy motion；不得打断语音按钮 LookAt。 |
| 猫爪粗略介绍：“超级魔法开关出现……” | `surprised` | 从 surprised 池随机；授权说明段可回落 neutral。 |
| 插件预览：“这里还有超多好玩的插件呢……” | `happy` | 从 happy 池随机；插件 dashboard 页面内 runtime 自己负责页面内动作。 |
| 设置一瞥：“在这个只属于我们的小空间里……” | `neutral` | 从 neutral 池随机；设置 tour 稳定说明。 |
| 主动搭话：“这个小按钮也很重要哦……” | `happy` | 从 happy 池随机。 |
| 归还控制权：“好啦好啦……” | `happy` | 开头可 happy；70% cue 后归还控制权挥手和花瓣转场独占。 |
| 轻微打断/生气退出 | `angry` | 保留现有抵抗/angry exit 自定义动作，随机 angry motion 不抢锁。 |

## 剧本到现有 Scene 的映射

| 剧本阶段 | 现有 scene | 实现策略 |
| --- | --- | --- |
| 初次见面与聊天输入 | intro prelude + `intro_basic` 前置问候 | 聊天输入区高亮，悠怡用普通聊天消息完成自我介绍。 |
| 语音入口 | `intro_basic` | 高亮模型旁语音按钮；Ghost Cursor 只指向入口，不强制开始录音。 |
| 猫爪粗略介绍 | `takeover_capture_cursor` | 沿用 Agent 按钮、总开关和键鼠控制演示，只说明“需要授权才会动”。 |
| 插件管理预览 | `takeover_plugin_preview` | 新剧本没有独立阶段，但 Day 1 功能清单保留插件管理预览；文案降级为轻量 handoff。 |
| 设置一瞥与主动搭话 | `takeover_settings_peek` | 打开设置弹窗，先只展示设置侧边栏区域，再高亮主动搭话入口。 |
| 归还控制权 | `takeover_return_control` | 剧本没有单独列出，但代码必须保留收尾聊天窗高亮、花瓣转场清理与完成态。 |

## 动作时序

Day 1 已有真实首页导演流程，动作时序必须以 `home-yui-guide-text-highlight-cursor-flow.md` 和当前 `static/yui-guide-director.js` 为准。新增或改文案时，不要只改台词；必须同步确认对应台词播放时的 spotlight、Ghost Cursor 和真实 UI 操作。

高亮去重必须从导演流程源头处理：聊天窗、按钮、设置侧边栏和虚拟入口不要同时创建两套同目标 spotlight；不采用“先创建再隐藏”的兜底方式。

| 台词段落 | 高亮时序 | Ghost Cursor 时序 | 收尾/清理 |
| --- | --- | --- | --- |
| 输入框激活提示：“点一下这里……” | 苏醒后先 `ensureChatVisible()`；普通首页模式把 persistent spotlight 放到聊天输入区；外置聊天窗模式改用外置窗口 spotlight。 | Cursor 出现在输入区中心并 wobble；等待用户点击完成浏览器音频激活；激活后再 wobble 一次。外置聊天窗模式通过 `setExternalizedChatCursor('input')` 把独立聊天窗 cursor 放到输入区。 | 激活提示气泡隐藏，overlay 进入 taking-over，才允许进入正式问候。 |
| 初见问候：“微风、阳光……” | 普通首页延续输入区/聊天区 spotlight；外置聊天窗模式保持聊天窗整体 spotlight，不改成输入框高亮。 | 普通首页 cursor 停留在输入区附近；外置聊天窗 cursor 仍落在输入框中心，不移动到新 UI。 | 旁白和模型演出完成后调用 `clearIntroGreetingChatHighlight()` 或清理外置聊天窗 spotlight/cursor，后续主线不再把 spotlight 拉回聊天窗。 |
| 语音入口：“这里有一个神奇的按钮……” | 台词进入聊天窗；action spotlight 放到 `#${p}-btn-mic` 的圆形按钮 shell。 | 旁白前段移动到语音按钮中心；移动时间按语音时长约 16% 估算；只指向，不点击。 | 停止 intro voice look-at handle，清理语音按钮方向的临时视线锁。 |
| 猫爪粗略介绍：“超级魔法开关出现……” | 进入 takeover；猫爪按钮 `#${p}-btn-agent` 保持 retained extra spotlight；打开 Agent 面板后依次高亮 `agent-master` 和 `agent-keyboard` 虚拟 spotlight。 | Cursor 移到猫爪按钮并 click；移到总开关并 click；再移到键鼠控制开关并 click。 | 清掉 retained extra、虚拟 spotlight 和 action spotlight；恢复进入前的相关临时开关状态。 |
| 插件预览：“这里还有超多好玩的插件呢……” | 保持/打开 Agent 面板；高亮用户插件开关；hover 后高亮“管理面板”虚拟入口。 | Cursor 移到用户插件开关并 click；再移到“管理面板”入口并 click。插件 dashboard 成功打开后首页 cursor 隐藏，插件页 runtime 接管自己的 cursor。 | 插件页完成后关闭由教程打开的窗口，回到首页恢复 cursor 原位置；清理 Agent 面板和用户插件侧面板。 |
| 设置一瞥第一句：“在这个只属于我们的小空间里……” | 先关闭 Agent 面板；settings 按钮 `#${p}-btn-settings` 作为圆形 retained spotlight；到 `openSettingsPanel` cue 时 action spotlight 指向齿轮。 | 台词开始后 Ghost Cursor 先平滑移动到齿轮并 wobble；等 cue 到达后再 click，真实打开设置弹窗。 | 打开后等待设置弹窗稳定，不自动展开角色设置侧边栏。 |
| 设置一瞥细节第一句：“不管是说话的温度、相处的小脾气……” | 只高亮设置弹窗自身的侧边栏区域 `#${p}-popup-settings`；不展开、不高亮角色设置侧边栏。 | Ghost Cursor 的椭圆巡游收束在设置侧边栏内部，不扫到角色设置侧边栏或子项。 | 进入第二句前停止设置侧边栏巡游。 |
| 设置一瞥细节第二句：“这个小按钮也很重要哦……” | spotlight 平滑切到主动搭话按钮 `#${p}-toggle-proactive-chat` 本体，并持续到这句播放完。 | Ghost Cursor 平滑移动到主动搭话按钮；不点击，也不打开 `interval-proactive-chat` 侧边栏。 | 这句播放完后再清掉 scene extra、virtual、precise 和 action spotlight；关闭设置面板。 |
| 归还控制权：“好啦好啦……” | 收尾台词播放期间高亮聊天窗；外置聊天窗模式同步高亮独立聊天窗。70% 语音 cue 触发每日花瓣转场时清掉所有高亮。 | Cursor 移到聊天窗附近并 wobble；外置聊天窗模式让独立聊天窗 Ghost Cursor 在窗口区域 wobble。70% cue 时隐藏 cursor。 | 花瓣转场期间恢复用户原模型；结束后关闭 taking-over，教程完成。 |

Day 1 的 `takeover_return_control` 是七日每日收尾的基准实现。Day 2-7 的 wrap/收尾 scene 必须复用同一套收尾动作：收尾台词播放期间重新高亮聊天窗；外置聊天窗模式同步高亮独立聊天窗；收尾台词约 70% 使用同一套花瓣转场 cue；cue 触发瞬间同步隐藏 Ghost Cursor、清理内置 overlay spotlight、外置聊天窗 spotlight/cursor、extra/virtual spotlight；正常完成才播放，skip、angry exit 和 destroy 不播放正常收尾花瓣。

## 需要修改的内容

### 1. 文案与音频 key

优先保持现有 key 稳定：

- `tutorial.yuiGuide.lines.introGreetingReply`
- `tutorial.yuiGuide.lines.introBasic`
- `tutorial.yuiGuide.lines.takeoverCaptureCursor`
- `tutorial.yuiGuide.lines.takeoverPluginPreviewHome`
- `tutorial.yuiGuide.lines.takeoverPluginPreviewDashboard`
- `tutorial.yuiGuide.lines.takeoverSettingsPeekIntro`
- `tutorial.yuiGuide.lines.takeoverSettingsPeekDetailPart1`
- `tutorial.yuiGuide.lines.takeoverSettingsPeekDetailPart2`
- `tutorial.yuiGuide.lines.takeoverReturnControl`

文案以总稿 Day 1 为准：

- 初见：“微风、阳光，还有刚刚好出现的你……”
- 语音：“这里有一个神奇的按钮……”
- 猫爪：“超级魔法开关出现……”
- 设置：“在这个只属于我们的小空间里……”
- 主动搭话：“这个小按钮也很重要哦，只要你轻轻点一下，我就能在合适的时候跑过去找你啦……”

如果只替换中文文案，修改 `static/locales/zh-CN.json` 即可。若要同步更新默认兜底文案，再改 `static/yui-guide-steps.js` 的 `performance.bubbleText`。

预录语音文件名目前由 `static/yui-guide-day1-home-guide.js` 的 `audioFileNames`/`audioFilesByKey` 绑定，Director 只从 daily guide registry 读取音频映射。新文案上线前可继续使用 TTS/旧音频兜底；正式发版时再替换对应 mp3 并更新时长配置。

### 2. 设置一瞥加入主动搭话

`takeover_settings_peek` 需要对齐新版 Day 1 的第二句设置台词：

- 主按钮/菜单：`#${p}-toggle-proactive-chat` 或对应设置菜单项。

建议流程：

1. 先高亮齿轮按钮并打开设置弹窗。
2. 播放“不管是说话的温度……”时，只高亮设置弹窗自身的侧边栏区域，Ghost Cursor 椭圆巡游也限制在该区域内。
3. 播放“这个小按钮也很重要哦”时，把 spotlight 平滑切到主动搭话按钮本体，并保持到该句播放完。
4. Ghost Cursor 平滑移动到主动搭话按钮，但不点击、不打开主动搭话侧边栏、不实际改用户配置；收尾时恢复设置面板状态。

### 3. 插件预览保留但轻量化

`takeover_plugin_preview` 当前已经落地插件管理面板 handoff。虽然新版 Day 1 小剧场没有单独列插件阶段，但 Day 1 功能清单仍写了插件管理预览，且猫爪粗略介绍会自然带出“需要授权才会动”的能力边界。

保留该 scene，文案只讲“以后可以给猫爪添装备”，不要把 Day 1 变成插件说明会。

### 4. Day 1 完成态

Day 1 完成或跳过后，仍由现有 Manager 流程把 `avatarFloatingGuide.completedRounds` 标记包含 `1`。不要新增另一套 Day 1 完成标记。

## 支线能力

Day 1 新版总稿没有强制聊天窗支线。若后续恢复“聊天工具小提示”，应走聊天窗消息按钮，而不是插进首页主线；具体触发条件和按钮设计统一见 [七日新手教程剧场后聊天窗支线设计](avatar-floating-post-theater-chat-branches.md)。

## 验收清单

1. 首次进入首页时，输入框激活提示、首句问候、语音按钮高亮顺序正常。
2. `intro_basic` 期间 Ghost Cursor 指向语音按钮，Yui 视线跟随，结束后视线恢复。
3. `takeover_capture_cursor` 能真实打开 Agent 面板并演示总开关、键鼠控制。
4. `takeover_plugin_preview` 的插件管理 handoff 成功；弹窗受阻时仍能手动继续。
5. `takeover_settings_peek` 能打开设置面板，只展示设置侧边栏区域，并高亮主动搭话入口。
6. 教程演示不保存主动搭话或其他设置的临时改动。
7. `takeover_return_control` 播放收尾台词时重新高亮聊天窗，外置聊天窗模式同步高亮独立聊天窗。
8. 同一目标同一时刻只保留一套主 spotlight，不创建后再隐藏重复高亮。
9. `takeover_return_control` 的 70% cue 能触发花瓣转场，并在触发瞬间清理高亮、面板、Ghost Cursor、接管态和临时模型。
10. skip、轻微打断、生气退出在任意 takeover 场景中仍走通用模块语义。
