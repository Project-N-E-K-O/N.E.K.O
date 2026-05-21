# Day 1 首页 YUI 教程：初次见面、接管与核心入口

本文按 Day 1 首页 YUI 新手教程期间文本输出的先后顺序，记录首次激活、语音入口、Agent/键鼠控制、插件管理预览、设置一瞥和归还控制权的高亮与 ghost cursor 流程。它只描述 Day 1 的文本、spotlight/highlight、ghost cursor、真实 UI 点击和场景清理；更细的文本、高亮和 cursor 维护细节看 `home-yui-guide-text-highlight-cursor-flow.md`，通用生命周期边界看 `home-yui-guide-lifecycle-modularization.md`，总览和跨天排期看 `avatar-floating-panel-functions.md`。

若本文与当前代码冲突，以当前代码为准。主要代码入口：

1. `static/universal-tutorial-manager.js`：Day 1 启动、跳过按钮、临时切模、完成/跳过。
2. `static/yui-guide-steps.js`：`HOME_SCENE_ORDER`、主线 scene、台词 key、默认 cursor target。
3. `static/yui-guide-director.js`：文本输出、高亮、ghost cursor、真实 UI 点击、插件 dashboard handoff、设置一瞥和归还控制权。
4. `frontend/plugin-manager/src/yui-guide-runtime.ts`：插件管理页内部 `main` spotlight、页面内 ghost cursor、skip / interrupt bridge。

## 介绍内容树

```text
Day 1：首页 YUI 教程
├─ 初次见面
│  ├─ 苏醒演出
│  ├─ 输入框点击激活
│  ├─ 初次问候
│  └─ 介绍结束后清理聊天 persistent spotlight
├─ 语音入口
│  ├─ 语音按钮在哪里
│  ├─ ghost cursor 看向按钮
│  └─ 只展示入口，不开始通话
├─ 猫爪与键鼠控制
│  ├─ Agent / 猫爪按钮
│  ├─ Agent 总开关
│  ├─ 键鼠控制开关
│  └─ 教程临时打开，结束后恢复接管前状态
├─ 插件入口与管理面板预览
│  ├─ 用户插件开关
│  ├─ 管理面板入口
│  ├─ 插件 dashboard 页面内演示
│  ├─ dashboard skip / interrupt bridge
│  └─ 插件页本地 skip 控件防穿透
├─ 设置一瞥
│  ├─ 设置按钮
│  ├─ 角色设置入口
│  ├─ 外形 / 声音克隆 / 记忆相关入口
│  └─ 只展示，不保存用户设置变化
└─ 归还控制权
   ├─ 关闭临时面板
   ├─ 花瓣转场
   ├─ 恢复用户原模型
   └─ 解除 taking-over
```

## 当前顺序

Day 1 正常主线由首页 intro 前置流程加 `HOME_SCENE_ORDER` 组成。用户可见顺序如下：

```text
wakeup_prelude
intro_activation_hint
intro_greeting_reply
intro_basic
takeover_capture_cursor
takeover_plugin_preview
takeover_settings_peek
takeover_return_control
```

`interrupt_resist_light` 和 `interrupt_angry_exit` 是教程期间的打断分支，不属于正常主线，但可能插入任意 takeover 场景。

## 0. 苏醒与输入框激活

文本输出：

1. `tutorial.yuiGuide.lines.introActivationHint`
2. 语音 key：无正式旁白语音，仅作为激活提示。
3. 中文：“点一下这里，我就能开始说话啦～”

高亮流程：

1. `runWakeupPrelude()` 完成苏醒后进入聊天 intro。
2. 普通首页模式调用 `ensureChatVisible()`，再用 `focusAndHighlightChatInput()` 把 persistent spotlight 放到聊天输入区。
3. 气泡锚定输入区，提示用户点击。
4. 外置聊天窗模式跳过首页输入框点击激活，改为同步外置聊天窗 spotlight。

ghost cursor 流程：

1. cursor 出现在输入区中心。
2. cursor wobble，等待用户真实点击输入区完成浏览器音频/播放激活。
3. 用户激活后隐藏提示气泡，overlay 进入 taking-over 状态，cursor 再 wobble 一次。

真实 UI 操作：

1. 用户必须真实点击输入区或外置聊天窗完成激活。
2. 教程不发送聊天消息。

## 1. 初次见面问候

文本输出：

1. `tutorial.yuiGuide.lines.introGreetingReply`
2. 语音 key：`intro_greeting_reply`
3. 中文：“微风、阳光，还有刚刚好出现的你。初次见面，我是林悠怡……”

高亮流程：

1. 延续输入区/聊天区 spotlight。
2. 旁白、拥抱和爱心演出全部结束后，`clearIntroGreetingChatHighlight()` 清掉聊天 persistent spotlight。
3. 外置聊天窗模式同步清掉外置窗口 spotlight。

ghost cursor 流程：

1. 激活后 cursor 保持在输入区附近。
2. 这一段主要由 YUI 模型演出承接，ghost cursor 不负责展示新 UI。

真实 UI 操作：

1. 无。

清理：

1. 设置 `introGreetingChatHighlightCleared = true`。
2. 后续首页 `highlightChatWindow()` 不再把聊天窗恢复成 persistent spotlight。

## 2. 语音入口介绍

文本输出：

1. `tutorial.yuiGuide.lines.introBasic`
2. 语音 key：`intro_basic`
3. 中文：“这里有一个神奇的小按钮！只要点击它，就可以直接和我聊天啦！……”

高亮流程：

1. action spotlight 放到语音控制按钮，也就是 `#${p}-btn-mic` 的圆形按钮 shell。
2. 语音按钮写入圆形 spotlight geometry hint。
3. 聊天窗口只承载文本，不再作为本段 persistent spotlight。

ghost cursor 流程：

1. 如果 cursor 还没有位置，先从输入区或默认原点出现。
2. cursor 在旁白前段移动到语音控制按钮中心。
3. 移动时长按语音时长约 16% 估算，限制在 900-2200ms。
4. 只展示按钮，不点击语音按钮。

真实 UI 操作：

1. 无。
2. 不开始语音通话。

## 3. 猫爪与键鼠控制

文本输出：

1. `tutorial.yuiGuide.lines.takeoverCaptureCursor`
2. 语音 key：`takeover_capture_cursor`
3. 中文：“超级魔法开关出现！只要点一下这里，我就可以把小爪子伸到你的键盘和鼠标上啦！……”

高亮流程：

1. overlay 进入 taking-over。
2. 猫爪按钮 `#${p}-btn-agent` 作为 retained extra spotlight 保留。
3. 点击猫爪后打开猫爪/Agent 面板。
4. 猫爪总开关 `agent-master` 用虚拟 spotlight `takeover-agent-master-toggle` 扩大高亮范围。
5. 键鼠控制开关 `agent-keyboard` 用虚拟 spotlight `takeover-keyboard-toggle` 高亮。

ghost cursor 流程：

1. cursor 移动到猫爪按钮。
2. visible click，director 调用 `openAgentPanel()`。
3. cursor 移动到猫爪总开关并 visible click。
4. director 调用 `setAgentMasterEnabled(true)` 并等待状态同步。
5. cursor 移动到键鼠控制开关并 visible click。
6. director 调用 `setAgentFlagEnabled('computer_use_enabled', true)` 并等待状态同步。

真实 UI 操作：

1. 打开 Agent 面板。
2. 临时打开 Agent 总开关和键鼠控制开关。

清理：

1. 清掉 retained extra spotlight、两个虚拟 spotlight 和 action spotlight。
2. 后续插件预览结束时恢复猫爪总开关、键鼠控制和用户插件开关到接管前状态。

## 4. 插件入口与管理面板预览

文本输出一：

1. `tutorial.yuiGuide.lines.takeoverPluginPreviewHome`
2. 语音 key：`takeover_plugin_preview_home`
3. 中文：“还没完呢！你快看快看，这里还有超多好玩的插件呢！”

高亮流程一：

1. 打开或保持猫爪/Agent 面板。
2. 用户插件开关 `agent-user-plugin` 被高亮并打开。
3. hover 用户插件开关，露出侧面板里的“管理面板”入口。
4. “管理面板”入口用虚拟 spotlight `plugin-management-entry` 高亮。

ghost cursor 流程一：

1. cursor 移动到用户插件开关。
2. visible click，director 调用 `setAgentFlagEnabled('user_plugin_enabled', true)`。
3. cursor 移动到“管理面板”入口。
4. visible click，director 调用插件 dashboard 打开逻辑。
5. 如果弹窗或窗口打开受阻，保持管理入口高亮并等待用户手动打开。

真实 UI 操作一：

1. 临时打开用户插件开关。
2. 打开插件管理 dashboard 或进入手动打开 fallback。

文本输出二：

1. `tutorial.yuiGuide.lines.takeoverPluginPreviewDashboard`
2. 语音 key：`takeover_plugin_preview_dashboard`
3. 中文：“有了它们，我不光能看 B 站弹幕，还能帮你关灯开空调……”

高亮流程二：

1. dashboard handoff 成功后，首页 overlay 清掉 action spotlight 和 persistent spotlight。
2. 首页 ghost cursor 隐藏。
3. 插件 dashboard 内部高亮 `main` 区域，并由 `frontend/plugin-manager/src/yui-guide-runtime.ts` 驱动页面内 ghost cursor。
4. dashboard 旁白完成后，首页通知插件 dashboard narration finished。
5. 回到首页后关闭或收起临时打开的猫爪面板、用户插件侧面板和插件 dashboard 窗口。

ghost cursor 流程二：

1. 进入 dashboard 讲解时保存首页 cursor 位置。
2. 插件页 runtime 创建页面内 ghost cursor，在插件页内部移动、滚动和巡游。
3. 用户真实点击首页跳过按钮的屏幕区域时，插件页根据 `skipButtonScreenRect` 发送带 `screenX/screenY` 的 skip request，首页校验后转发给 `#neko-tutorial-skip-btn.click()`。
4. 插件页右上角桌面 skip 按钮发送 `source: 'plugin_dashboard_button'`，不要求携带屏幕坐标。
5. dashboard 完成并回到首页后，如果有保存位置，首页 cursor 在原位置恢复显示。

真实 UI 操作二：

1. 打开插件 dashboard 页面。
2. 不安装、不启用、不配置具体插件。
3. 不把生气退出当成 `plugin-dashboard:done`。

清理：

1. 关闭教程创建的 dashboard 窗口。
2. 收起用户插件侧边面板。
3. 恢复猫爪总开关、键鼠控制和用户插件开关到接管前状态。
4. 插件页本地 skip 控件必须拦截 pointer/mouse/touch/click，避免点击穿透到底层插件页面。

## 5. 设置一瞥

文本输出一：

1. `tutorial.yuiGuide.lines.takeoverSettingsPeekIntro`
2. 语音 key：`takeover_settings_peek_intro`
3. 中文：“当然啦，如果你想让本喵多和你聊聊天，也不是不行啦……设置都在这个齿轮里。”

高亮流程一：

1. 场景开始先关闭猫爪/Agent 面板。
2. settings 按钮 `#${p}-btn-settings` 被设置为圆形 spotlight，并作为 retained extra spotlight 保留。
3. 到 `openSettingsPanel` 语音 cue 时，action spotlight 放到 settings 按钮。

ghost cursor 流程一：

1. 等待 `takeover_settings_peek_intro` 的 `openSettingsPanel` cue。
2. cue 到达后 cursor 移动到 settings 按钮。
3. visible click，director 调用 `openSettingsPanel()`。

真实 UI 操作一：

1. 打开 settings 面板。

文本输出二：

1. `tutorial.yuiGuide.lines.takeoverSettingsPeekDetailPart1`
2. `tutorial.yuiGuide.lines.takeoverSettingsPeekDetailPart2`
3. 语音 key：`takeover_settings_peek_detail`
4. 第一段先以流式消息进入聊天窗口，第二段在 `showSecondLine` cue 到达时追加。

高亮流程二：

1. director 等待角色设置入口 `characterMenu` 可见。
2. 确保角色设置侧面板展开。
3. action spotlight 先高亮角色设置入口。
4. `refreshSettingsPeekSpotlights()` 组合 settings 按钮、角色设置入口、角色设置侧面板，或外形/声音克隆条目的 union spotlight。
5. 细节旁白结束或超时后，清掉 scene extra spotlight、虚拟 spotlight、precise highlight 和 action spotlight。

ghost cursor 流程二：

1. cursor 移动到角色设置入口。
2. 刷新设置相关高亮后，cursor 移动到侧面板或条目 union 的中心。
3. cursor 围绕侧面板或条目 union 做椭圆巡游。
4. 巡游持续到细节旁白结束、场景终止或 angry exit。

真实 UI 操作二：

1. 展开角色设置侧面板。
2. 不点击外形、声音克隆、记忆或跨页管理入口。
3. 不保存设置变化。

清理：

1. 收起角色设置侧面板。
2. 关闭 settings 面板。
3. 清理 settings retained / scene extra / virtual / precise spotlight。

## 6. 归还控制权

文本输出：

1. `tutorial.yuiGuide.lines.takeoverReturnControl`
2. 语音 key：`takeover_return_control`
3. 中文：“好啦好啦，不霸占你的电脑啦！控制权还给你了喵！……”

高亮流程：

1. 场景开始清掉 persistent spotlight。
2. cursor 目标是 `#${p}-container`，通常是当前模型/主容器。
3. 旁白完成后关闭所有 managed panels。
4. 清掉 persistent spotlight 和 action spotlight。
5. 语音 70% cue 触发花瓣转场和模型淡出。

ghost cursor 流程：

1. 如果 cursor 还有位置，先移动到目标容器中心；否则直接在视口中心出现。
2. 台词播放后 cursor wobble。
3. 关闭面板并清掉 spotlight 后，cursor 移动到视口中心。
4. cursor 再 wobble 一次，然后隐藏。
5. 第 6 段语音 70% cue 触发时隐藏 cursor 并清掉高亮。

真实 UI 操作：

1. 关闭教程期间打开的面板。
2. 触发教程头像恢复流程，按新手教程开启前保存的模型快照重新加载用户原模型。

完成：

1. 花瓣转场剩余时间播完后淡出转场层。
2. `setTutorialTakingOver(false)`。
3. 标记 Day 1 complete 或 skip。
4. Day 1 完成或跳过后，后续自然日从 Day 2 开始。

## 7. 轻微打断分支

触发条件：

1. 用户在 takeover 或 interruptible 场景中移动真实鼠标、试图抢回控制，且达到当前阻力判断条件。
2. 未达到 angry exit 阈值。

文本输出：

1. `tutorial.yuiGuide.lines.interruptResistLight1`
2. `tutorial.yuiGuide.lines.interruptResistLight3`
3. 文本进入聊天窗口，不等待当前 scene 的流式暂停。

高亮流程：

1. 当前 scene 暂停，原高亮状态被保留。
2. 抵抗结束后恢复当前 scene 的 presentation/highlight。

ghost cursor 流程：

1. 当前 cursor 动画取消或暂停。
2. cursor 根据用户真实鼠标位置执行 `resistTo(x, y)`。
3. 抵抗语音结束后恢复原 scene。

完成：

1. 不标记教程完成或跳过。
2. 不清理主线 scene，只恢复被暂停的主线。

## 8. 生气退出分支

触发条件：

1. 连续有效打断达到阈值。
2. 或流程主动请求 angry exit。

文本输出：

1. `tutorial.yuiGuide.lines.interruptAngryExit`
2. 语音 key：`interrupt_angry_exit`
3. 中文：“人类！你真的很没礼貌喵！既然你这么想自己操作……”

高亮流程：

1. 触发瞬间清理当前高亮和插件 preview，隐藏普通气泡。
2. overlay 保持 taking-over，并设置 angry 状态。
3. 如果插件 dashboard 已打开，插件页本地 `main` spotlight 也必须立即清掉。

ghost cursor 流程：

1. 触发瞬间停止当前 scene 的 cursor 动画，并隐藏 ghost cursor。
2. 如果插件 dashboard 已打开，插件页 runtime 调用 `stopGhostCursorAnimation()` 移除 cursor 的可见状态、点击星星和轨迹粒子。
3. 生气退出语音播放期间不恢复主线 cursor。

完成：

1. 生气退出台词语音完整播放后，走和跳过按钮一致的 skip / destroy 路径。
2. 该分支不是正常完成分支，不能发送或等价处理为 `plugin-dashboard:done`。

## 清理要求

1. 关闭 Agent 面板、用户插件侧边面板、settings 面板和角色设置侧边面板。
2. 关闭教程创建的插件 dashboard 窗口。
3. 恢复猫爪总开关、键鼠控制和用户插件开关到接管前状态。
4. 清理 retained、scene extra、virtual、precise、action 和 persistent spotlight。
5. 停止 ghost cursor、插件页本地 ghost cursor、临时 LookAt handle 和插件 dashboard corner peek。
6. 跳过、pagehide、remote terminate 和 angry exit 都必须恢复用户原模型。
7. 不安装插件，不修改设置项，不保留教程期间临时打开的面板。
