# 首页 YUI 新手教程文本、高亮与 Ghost Cursor 流程

本文按首页新手教程期间文本输出的先后顺序，记录当前高亮和 ghost cursor 的流程情况。它只描述首页教程的文本、spotlight/highlight、ghost cursor、真实 UI 点击和流程交接；YUI 模型动作演出看 `home-tutorial-yui-guide-performance-owner-stage-breakdown.md`，通用动作运行时维护规则看 `avatar-performance-module-maintenance.md`。

若本文与当前代码冲突，以当前代码为准。主要代码入口：

1. `static/yui-guide-steps.js`：scene 顺序、台词 key、默认 cursor target。
2. `static/yui-guide-director.js`：文本输出、高亮、ghost cursor 移动、真实 UI 点击。
3. `static/locales/zh-CN.json`：中文台词文案。

## 当前首页顺序

首页主线顺序来自 `HOME_SCENE_ORDER`：

```text
intro_basic
takeover_capture_cursor
takeover_plugin_preview
takeover_settings_peek
takeover_return_control
```

`interrupt_resist_light` 和 `interrupt_angry_exit` 是教程期间的打断分支，不属于正常主线，但可能插入任意 takeover 场景。

## 0. 苏醒与输入框激活提示

文本输出：

1. 网页端先显示激活提示：`tutorial.yuiGuide.lines.introActivationHint`
   - 中文：“点一下这里，我就能开始说话啦～”
2. 桌面 Pet 外置聊天窗模式跳过首页输入框点击激活，不显示这一步输入框激活气泡。

高亮流程：

1. `runWakeupPrelude()` 完成苏醒后进入聊天 intro。
2. 普通首页模式调用 `ensureChatVisible()`，再用 `focusAndHighlightChatInput()` 把 persistent spotlight 放到聊天输入区。
3. 气泡锚定输入区，手动定位到输入框正上方。
4. 外置聊天窗模式不高亮首页输入区，改为 `setExternalizedChatSpotlight('window')`。

ghost cursor 流程：

1. 普通首页模式读取输入区 rect。
2. cursor 出现在输入区中心。
3. cursor 先 wobble，等待用户点击输入区完成激活。
4. 用户激活后隐藏提示气泡，overlay 进入 taking-over 状态，cursor 再 wobble 一次。

注意：

1. 这一步是浏览器音频/播放激活需要，不是剧情条件。
2. 激活完成前不会进入首句正式旁白。

## 1. 初次见面问候

文本输出：

1. `tutorial.yuiGuide.lines.introGreetingReply`
   - 中文：“微风、阳光，还有刚刚好出现的你。初次见面，我是林悠怡……”
2. 文本进入聊天窗口，语音 key 为 `intro_greeting_reply`。

高亮流程：

1. 这一段延续上一阶段输入区/聊天区的 spotlight 状态。
2. 不打开新的业务面板。
3. 不执行真实 UI 点击。

ghost cursor 流程：

1. 激活后 cursor 保持在输入区附近。
2. 这一段主要由 YUI 模型演出承接，ghost cursor 不负责展示新 UI。

时间 cue：

1. `intro_greeting_reply` 有 `showIntroGiftHeart` cue。
2. cue 只驱动头像演出，不改变高亮或 cursor 目标。

## 2. 语音入口介绍

文本输出：

1. `tutorial.yuiGuide.lines.introBasic`
   - 中文：“这里有一个神奇的小按钮！只要点击它，就可以直接和我聊天啦！……”
2. 文本进入聊天窗口，语音 key 为 `intro_basic`。

高亮流程：

1. `runIntroVoiceControlButtonShowcase()` 先调用 `highlightChatWindow()`。
2. persistent spotlight 放到聊天窗口；如果是外置聊天窗，则同步外置窗口 spotlight。
3. action spotlight 放到语音控制按钮，也就是 `#${p}-btn-mic` 的圆形按钮 shell。

ghost cursor 流程：

1. 如果 cursor 还没有位置，先从输入区或默认原点出现。
2. cursor 在旁白前段移动到语音控制按钮中心。
3. 移动时长按语音时长约 16% 估算，限制在 900-2200ms。
4. 当前流程只展示按钮，不点击语音按钮。

模型动作流程：

1. 进入语音入口展示时，director 调用 `YuiGuideAvatarStage.startIntroVoiceCursorLookAt()` 建立短 session，只锁 `lookAt`，不接管 `motion` 或 `expression`。
2. 该 session 期间打开 `window.nekoYuiGuideIntroVoiceLookAtActive`，只让第 2 段绕过新手教程正脸锁的视线归零逻辑。
3. cursor 从输入区移动到语音控制按钮期间，模型面部和视线跟随 ghost cursor 当前坐标；实现上由 director 提供 `overlay.getCursorPosition()`，adapter 每帧把该点喂给 Live2D 的 `model.focus(x, y)`，并以 Live2D 头部/气泡锚点作为 LookAt 原点，在 Yui 专用 temporary pose 中直接写入 `ParamAngleX/Y/Z`、`ParamEyeBallX/Y`、`ParamBodyAngleX/Y/Z`。
4. cursor 到达语音控制按钮并短暂停留时，模型面部和视线也停在按钮方向，强化“她在看这个按钮”的因果关系。
5. Live2D 的 temporary pose override 会在原始 `coreModel.update()` 后再应用一次，确保本段 ghost cursor LookAt 不被正脸锁、SDK 原始 update、motion 或 focusController 在同一帧覆盖掉。
6. 语音入口展示结束后，director 在 `finally` 中停止 handle；adapter 清理本段 temporary pose、恢复捕获到的参数、清零 Live2D `focusController`，并关闭 `nekoYuiGuideIntroVoiceLookAtActive`，让教程正脸锁继续接管后续阶段。

注意：

1. `intro_basic.performance.timeline` 里有 `highlightVoiceControl`，但当前实际展示主要由 `runIntroVoiceControlButtonShowcase()` 完成。
2. 这一段结束后进入 takeover 主流程；进入下一段前不能遗留语音按钮方向的目光锁。

## 3. 猫爪与键鼠控制

文本输出：

1. `tutorial.yuiGuide.lines.takeoverCaptureCursor`
   - 中文：“超级魔法开关出现！只要点一下这里，我就可以把小爪子伸到你的键盘和鼠标上啦！……”
2. 文本进入聊天窗口，语音 key 为 `takeover_capture_cursor`。
3. 旁白和 UI 自动操作并行执行。

高亮流程：

1. 场景开始时 overlay 进入 taking-over。
2. persistent spotlight 先回到聊天窗口，表示台词仍在聊天区输出。
3. 猫爪按钮 `#${p}-btn-agent` 被作为 retained extra spotlight 保留。
4. 点击猫爪后打开猫爪/Agent 面板。
5. 猫爪总开关 `agent-master` 用虚拟 spotlight `takeover-agent-master-toggle` 扩大高亮范围。
6. 键鼠控制开关 `agent-keyboard` 用虚拟 spotlight `takeover-keyboard-toggle` 高亮。
7. 场景结束时清掉 retained extra spotlight、两个虚拟 spotlight 和 action spotlight。

ghost cursor 流程：

1. cursor 移动到猫爪按钮。
2. cursor click，director 调用 `openAgentPanel()` 打开面板。
3. cursor 移动到猫爪总开关虚拟 spotlight。
4. cursor click，director 调用 `setAgentMasterEnabled(true)` 并等待开关状态同步。
5. cursor 移动到键鼠控制开关虚拟 spotlight。
6. cursor click，director 调用 `setAgentFlagEnabled('computer_use_enabled', true)` 并等待状态同步。

模型动作流程：

1. 进入本段时，director 会启动 ghost cursor LookAt handle，复用第 2 段同一套 `YuiGuideAvatarStage.startIntroVoiceCursorLookAt()` session。
2. 该 handle 持续读取 `overlay.getCursorPosition()`，所以 cursor 在猫爪按钮、猫爪总开关、键鼠控制开关之间移动和点击时，模型面部与视线会同步跟随这些 ghost cursor 坐标。
3. 因为底层仍复用 `window.nekoYuiGuideIntroVoiceLookAtActive` 这条豁免标记，所以本段同样会临时绕过教程期间的正脸锁，避免头部/眼球在同一帧被重置回正面。
4. 本段结束或提前终止时，director 在 `finally` 中停止 handle，adapter 清理 temporary pose override 并恢复视线归零，让后续场景重新回到默认教程锁脸逻辑。

注意：

1. 这段使用 `runTakeoverKeyboardControlSequence()`。
2. 操作节奏会按当前语音时长缩放，保证台词与 cursor 行为大致对齐。
3. 用户在 takeover 期间打断时，当前 scene 会暂停，cursor 动画也会暂停或产生抵抗反馈。

## 4. 插件入口与管理面板预览

文本输出一：

1. `tutorial.yuiGuide.lines.takeoverPluginPreviewHome`
   - 中文：“还没完呢！你快看快看，这里还有超多好玩的插件呢！”
2. 文本进入聊天窗口，语音 key 为 `takeover_plugin_preview_home`。

高亮流程一：

1. 场景开始调用 `highlightChatWindow()`。
2. 打开或保持猫爪/Agent 面板。
3. 用户插件开关 `agent-user-plugin` 被高亮并打开。
4. hover 用户插件开关，露出侧面板里的“管理面板”入口。
5. “管理面板”入口用虚拟 spotlight `plugin-management-entry` 高亮。

ghost cursor 流程一：

1. cursor 移动到用户插件开关。
2. cursor click，director 调用 `setAgentFlagEnabled('user_plugin_enabled', true)`。
3. cursor 移动到“管理面板”入口。
4. cursor click，director 调用插件面板打开逻辑。
5. 如果弹窗或窗口打开受阻，会保持管理入口高亮并等待用户手动打开。

文本输出二：

1. `tutorial.yuiGuide.lines.takeoverPluginPreviewDashboard`
   - 中文：“有了它们，我不光能看 B 站弹幕，还能帮你关灯开空调……”
2. 插件 dashboard 打开后，文本仍追加到聊天窗口。
3. 语音 key 为 `takeover_plugin_preview_dashboard`。

高亮流程二：

1. dashboard handoff 成功后，首页 overlay 清掉 action spotlight 和 persistent spotlight。
2. 首页不再继续强调猫爪面板，插件 dashboard 自己负责内部演示。
3. dashboard 旁白完成后，通知插件 dashboard narration finished。
4. 回到首页后关闭或收起临时打开的猫爪面板、用户插件侧面板和插件 dashboard 窗口。
5. 恢复猫爪总开关和用户插件开关到接管前状态。

ghost cursor 流程二：

1. 进入 dashboard 讲解时保存首页 cursor 位置。
2. 首页 ghost cursor 隐藏。
3. dashboard 完成并回到首页后，如果有保存位置，cursor 在原位置恢复显示。

弹窗受阻文本：

1. 如果浏览器需要用户手动点开插件面板，会使用 `tutorial.yuiGuide.lines.pluginDashboardPopupBlocked`。
2. 此时管理面板入口继续保持高亮，用户点击后流程继续。

## 5. 设置一瞥

文本输出一：

1. `tutorial.yuiGuide.lines.takeoverSettingsPeekIntro`
   - 中文：“当然啦，如果你想让本喵多和你聊聊天，也不是不行啦……设置都在这个齿轮里。”
2. 文本进入聊天窗口，语音 key 为 `takeover_settings_peek_intro`。

高亮流程一：

1. 场景开始先关闭猫爪/Agent 面板。
2. settings 按钮 `#${p}-btn-settings` 被设置为圆形 spotlight，并作为 retained extra spotlight 保留。
3. persistent spotlight 仍放在聊天窗口。
4. 到 `openSettingsPanel` 语音 cue 时，action spotlight 放到 settings 按钮。

ghost cursor 流程一：

1. 等待 `takeover_settings_peek_intro` 的 `openSettingsPanel` cue。
2. cue 到达后 cursor 移动到 settings 按钮。
3. cursor click，director 调用 `openSettingsPanel()`。

文本输出二：

1. `tutorial.yuiGuide.lines.takeoverSettingsPeekDetailPart1`
   - 中文：“你看，这里可以穿我的新衣服、给我换一个好听的声音……换一个猫娘，或是修改记忆？”
2. `tutorial.yuiGuide.lines.takeoverSettingsPeekDetailPart2`
   - 中文：“等一下！你在干嘛？该不会是想把我换掉吧？啊啊啊不行！快关掉，快关掉！”
3. 语音 key 统一为 `takeover_settings_peek_detail`。
4. 第一段先以流式消息进入聊天窗口，第二段在 `showSecondLine` cue 到达时追加。

高亮流程二：

1. director 等待角色设置入口 `characterMenu` 可见。
2. 确保角色设置侧面板展开。
3. action spotlight 先高亮角色设置入口。
4. 随后 `refreshSettingsPeekSpotlights()` 组合这些高亮：
   - settings 按钮；
   - 角色设置入口；
   - 角色设置侧面板，或外形/声音克隆条目的 union spotlight。
5. 细节旁白结束或超时后，清掉 scene extra spotlight、虚拟 spotlight、precise highlight 和 action spotlight。
6. 收起角色设置侧面板并关闭 settings 面板。

ghost cursor 流程二：

1. cursor 移动到角色设置入口。
2. 刷新设置相关高亮后，cursor 移动到侧面板或条目 union 的中心。
3. cursor 围绕侧面板或条目 union 做椭圆巡游。
4. 巡游持续到细节旁白结束、场景终止或 angry exit。

模型动作流程：

1. 本段从设置按钮展示开始，就会启动 ghost cursor LookAt handle，并持续到整个 `runSettingsPeekScene()` 结束。
2. 在第一段旁白里，cursor 朝 settings 按钮移动并点击时，模型面部和视线会跟随到齿轮按钮方向。
3. 进入细节展示后，cursor 移向角色设置入口、再移向侧面板/条目 union 中心并做椭圆巡游时，模型会继续跟随这些 ghost cursor 轨迹，而不是固定看正前方。
4. 第二段情绪反转出现时，`playSettingsPeekPanic()` 负责额外的慌乱表情/位移动作；ghost cursor LookAt 不会停掉，因此会形成“慌乱姿态 + 仍盯着当前设置项”的叠加效果。
5. 本段结束、超时、场景终止或 angry exit 后，director 会停止 handle，adapter 清理 temporary pose override、恢复捕获参数并关闭正脸锁豁免。

注意：

1. 这段有两个 cue：`openSettingsPanel` 和 `showSecondLine`。
2. 第二段文本是情绪反转点，高亮和 cursor 仍服务真实设置项，不把 spotlight 移到 YUI 身上。

## 6. 归还控制权

文本输出：

1. `tutorial.yuiGuide.lines.takeoverReturnControl`
   - 中文：“好啦好啦，不霸占你的电脑啦！控制权还给你了喵！……”
2. 文本进入聊天窗口，语音 key 为 `takeover_return_control`。
3. 语音播放到 70% 时触发 `returnPetalTransition` cue。

高亮流程：

1. 场景开始清掉 persistent spotlight。
2. cursor 目标是 `#${p}-container`，通常是当前模型/主容器。
3. 旁白完成后关闭所有 managed panels。
4. 清掉 persistent spotlight 和 action spotlight。
5. 语音 70% cue 触发花瓣转场：先启动一次持续约 4.2 秒的右手挥手 `playReturnControlCueWave()`，该动作复用开场 `computeWakeupPose()` 的右手挥手曲线，只写 `Param75/90/92/95`，不带入苏醒时的眼睛和身体姿态。
6. 同一 cue 加载预渲染 30fps animated WebP `static/assets/tutorial/petals/yui-guide-petal-transition.webp`，运行时直接用 `<img>` 播放，不再用 canvas requestAnimationFrame 或大背景 sprite sheet 逐帧切换。
7. animated WebP 由 `yui-guide-petal-1.png` 和 `yui-guide-petal-2.png` 预生成：花瓣从模型中心出发，先大幅向右形成弧线，再向左铺开并从页面左边消失，同时保留噪声摆动和翻滚旋转；新版资源把起点收得更紧、花瓣单体进一步缩小，并把总体排布调整为起点更密、终点更疏，让前段看起来更小更密，随后在时间轴后半段逐渐放大并把扩散范围拉开，让尾段变得更稀松。
8. 播放层覆盖到视口外侧，并按当前模型屏幕中心做轻量偏移；为抵消预渲染动画观感偏左，播放层额外向右校准约 `6vw`。运行时外层播放壳维持更收的初始 scale，并在播放过程中只做一段温和放大，帮助资源内部的“小而密 -> 渐渐放大并变稀松”变化更自然地落到屏幕上。
9. 从 70% cue 到语音播放完成期间，当前教程模型 DOM 层和底层模型 `alpha` 使用线性进度从 100% 渐变到 0%；花瓣整体透明度与模型淡出分离，不跟随模型归零，播放层最终通过 CSS 透明度保持约 60% 覆盖继续流动，不使用顶点破碎或解体效果。
10. 花瓣动画的整体时间轴比“70% cue 到语音结束”的剩余时长多延长约 1 秒，且非 reduced-motion 下最短播放约 6.2 秒；模型淡出只跟随该句语音剩余时长，最后一句语音播放完成后立即调用教程头像恢复流程，按新手教程开启前保存的模型快照重新加载用户原模型。
11. 模型快照恢复期间不暂停花瓣动画；恢复完成后先等待约 6.2 秒 animated WebP 剩余时间播完，再淡出花瓣转场层并关闭 taking-over 状态。

ghost cursor 流程：

1. 如果 cursor 还有位置，先移动到目标容器中心；否则直接在视口中心出现。
2. 台词播放后 cursor wobble。
3. 关闭面板并清掉 spotlight 后，cursor 移动到视口中心。
4. cursor 再 wobble 一次，然后隐藏。
5. 第 6 段语音 70% cue 触发时隐藏 cursor 并清掉高亮，避免转场期间 ghost cursor 残留在全屏花瓣层上。

注意：

1. `takeover_return_control.performance.timeline` 里有 `returnControl`，但当前主要收尾动作在 `playScene()` 的 return-control 分支完成。
2. 花瓣转场只在正常第 6 段归还控制权时触发；跳过教程和愤怒退出仍走普通销毁恢复路径。
3. 这一步结束后用户鼠标和页面交互恢复。

## 7. 轻微打断分支

触发条件：

1. 用户在 takeover 或 interruptible 场景中移动真实鼠标、试图抢回控制，且达到当前阻力判断条件。
2. 未达到 angry exit 阈值时进入轻微抵抗。

文本输出：

1. 第一次常用 `tutorial.yuiGuide.lines.interruptResistLight1`
   - 中文：“喂！不要拽我啦，现在还没轮到你的回合呢！”
2. 后续可能使用 `tutorial.yuiGuide.lines.interruptResistLight3`
   - 中文：“等一下啦！还没结束呢，不要这么随便打断我啦！”
3. 文本进入聊天窗口，不等待当前 scene 的流式暂停。

高亮流程：

1. 当前 scene 暂停，原高亮状态被保留。
2. 抵抗结束后恢复当前 scene 的 presentation/highlight。

ghost cursor 流程：

1. 当前 cursor 动画取消或暂停。
2. cursor 根据用户真实鼠标位置执行 `resistTo(x, y)`：
   - 先被真实鼠标方向拉近一小段；
   - wobble；
   - 再回到上一个目标点。
3. 抵抗语音结束后恢复原 scene。

## 8. 生气退出分支

触发条件：

1. 连续有效打断达到阈值。
2. 或流程主动请求 angry exit。

文本输出：

1. `tutorial.yuiGuide.lines.interruptAngryExit`
   - 中文：“人类！你真的很没礼貌喵！既然你这么想自己操作……”
2. 文本进入聊天窗口，允许 angry exit 期间继续流式输出。

高亮流程：

1. 清理当前 scene timers。
2. 禁用 interrupts。
3. overlay 保持 taking-over，并设置 angry 状态。
4. 隐藏插件 preview 和普通气泡。
5. 语音结束后请求教程终止。

ghost cursor 流程：

1. 当前 scene 的 cursor 动画不再继续。
2. angry exit 当前不新增独立 cursor 轨迹；重点是停止教程并恢复页面。

## 高亮类型速查

1. persistent spotlight：持续高亮当前讲解上下文，例如聊天窗口、输入区。
2. action spotlight：当前 cursor 要移动/点击的主要目标，例如猫爪按钮、开关、设置按钮。
3. secondary spotlight：辅助目标，主要由 `applyGuideHighlights()` 支持。
4. retained extra spotlight：跨多个动作保留的目标，例如猫爪按钮、settings 按钮。
5. virtual spotlight：根据真实元素 rect 创建的扩展高亮，例如开关区域和插件管理入口。
6. scene extra spotlight：设置一瞥中组合多个设置相关目标。
7. precise highlight：更细粒度的 DOM highlight，设置一瞥结束时会清理。

## 维护规则

1. 新增教程台词时，先确认文本输出位置：聊天窗口、overlay 气泡，还是外部页面 handoff。
2. 每段台词最多有一个主 persistent spotlight；多个 UI 目标应使用 action/secondary/extra，而不是反复重设 persistent。
3. ghost cursor 的移动必须跟真实 UI 操作一致：先高亮，再移动，再 click，再调用真实打开/开关 API。
4. 不能只移动 cursor 而不执行真实状态变更，也不能只改状态而没有可见 click 反馈。
5. dashboard、settings 等跨面板流程结束时必须清理 retained、virtual、scene extra 和 action spotlight。
6. 打断分支要暂停并恢复当前 scene，不能把抵抗文本当成新的主线 step。
7. 如果某个目标找不到，当前流程应安全跳过或走 fallback，不能卡死教程。
8. 外置聊天窗模式没有首页输入框激活，但后续台词、高亮和 takeover 主线仍继续。
