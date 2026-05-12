# 首页 YUI 新手引导动作设计

> 这是首页新手引导专用动作文档，只讲首页分镜、构图、节奏和体验边界。  
> 通用演出能力与维护规则看 `avatar-performance-module-maintenance.md`。  
> 若本文与代码冲突，以当前可验证代码为准。

## 目标

把首页新手引导里的 YUI 设计成“醒来后主动带用户认识小窝”的引导者，而不是站在旁边念台词的看板娘。

核心目标不变：

1. 保留开场苏醒、睁眼、首句打招呼的顺序。
2. 保留首页现有新手引导流程：语音入口、猫爪/键鼠控制、插件预览、设置一瞥、归还控制权、轻微打断、生气退出。
3. 动作必须服务真实 UI，不抢用户要看的按钮、面板和文字。
4. 首页专属动作写在首页适配层，不回写到通用 `AvatarPerformanceStage` 业务语义里。
5. 所有动作结束后必须恢复用户原模型、正常最小化/拖拽/按钮链路和教程完成状态。

平台前提：

1. 网页端需要一次用户点击来解锁后续语音/播放能力，这是浏览器运行限制导致的实现差别。
2. 桌面端不需要这次点击；桌面端应直接进入苏醒和首句打招呼流程。
3. 这次点击不是剧情设定，不应被包装成“YUI 等用户点一下才醒来”的情节。
4. 动作设计可以在网页端承接这次点击，但桌面端不能为了跨端视觉一致而强行多等一步。

## 全流程动作指导

这一段用于建立全流程画面感，但必须以当前代码已经实现的动作边界为准。不要一上来只想 `frame`、`preset`、`lookAt`，也不要把尚未接入的动作误写成已有能力。当前首页新手引导已经落地的 YUI 演出主要有四类：

1. 苏醒：`runWakeupPrelude()` 调用 `YuiGuideWakeup.run()`，再委托 `YuiGuideAvatarStage.createWakeupSession()` 执行闭眼、睁眼、抬头、短挥手等 Yui 专用 pose。若 storage location overlay 可见，会跳过苏醒并 reveal 已准备模型，不能阻塞存储选择。
2. 首句打招呼强化：`playIntroGreetingReply()` 并行调用 `runIntroGreetingHugPerformance()` 和 `runIntroGiftHeartPerformance()`；前者是靠近/拥抱式构图，后者等待 `showIntroGiftHeart` 语音 cue 后播放爱心跳动。
3. 插件面板陪看：`runPluginDashboardPreviewScene()` 在 dashboard 打开后调用 `startPluginDashboardCornerPeekPerformance()`，让 YUI 以角落斜探/陪看的方式短暂出现在首页边缘，并在 dashboard 关闭或 cleanup 时 stop。
4. 降级与恢复：上述动作均接入 reduced motion、取消判断、模型切换判断和 performance lock；动作结束必须释放锁、清 pose override、恢复参数或 frame。

因此，开场已经不是“待设计”的抽象段落，而是当前最成熟的一段。YUI 会先在 storage 闸门放行后进入苏醒链路，动作重点是“刚醒来、看见用户、准备开口”。这个阶段不要再新增外置遮罩、粒子舞台或独立 wakeup 入口；只允许在现有苏醒 session 和首句打招呼之间补轻量 settle。网页端的输入框点击只是音频解锁/激活流程，不应被写成 YUI 的剧情条件；桌面端也不应为了视觉一致强行等待这次点击。

首句打招呼现在已经有明确动作：拥抱靠近负责情绪亲近，爱心跳动负责在语音 cue 上给一次明显反馈。这里的“明显”不是小幅 `tinyHop`，而是现有代码已经证明可行的量级：hug 会把模型 frame 推到约 `scale 1.38`，并按视口下移 360-820px；gift heart 会做 4 次横向跳动，横摆约 118px，同时写入手臂、手掌、耳朵、头发、裙摆、挂饰和鞋带参数。后续设计语音入口时，要承接这两个动作的结束状态，而不是重新来一套更弱的小点头。也就是说，打完招呼后的 YUI 应该从“靠近用户”自然退回“半身引导”，再把视线和构图让给输入区、麦克风按钮或语音控制按钮；动作量级应足够被用户看见，但不能挡住输入区。

猫爪和键鼠控制目前主要由 director、overlay、ghost cursor 和真实按钮点击完成，YUI 还没有完整的专属动作 session。后续不能只补几个 10px 小动作，而应该沿用现有 `Live2DIntroGiftHeartSession` 的写法：用一个页面专用 session 同时写 frame 和 Yui 参数。点击前可以先把上半身向目标侧探出去，手部参数切到“想按按钮”的预备姿势；面板打开后再用明显的 frame 让位，幅度应接近现有 corner / hug 的构图变化，而不是只挪一点点。开关打开时可复用 `Param90-Param96` 做手臂/手掌反馈，眼睛和头部再跟 ghost cursor 走。不要在开关点击的瞬间做大幅 frame 位移，否则会抢掉真实 UI 的因果关系；但点击前后 500-900ms 的预告和反应必须足够明显。

插件预览已经有一段真实实现：dashboard 打开后，首页 YUI 使用 `Live2DPluginDashboardCornerSession` 退到角落陪看，保持原 scale，只改位置、旋转和透明度，并在关闭/cleanup 时恢复原 frame 和 alpha。后续插件段动作不应再设计成“dashboard 期间持续大动作”。合适的增强点在 dashboard 打开前和关闭后：打开前可以短促表达“想到好东西”，打开后保持角落陪看，关闭后再从角落回到首页构图。

设置一瞥、归还控制、轻微打断和生气退出目前还没有像 intro hug、gift heart、plugin corner peek 那样的独立 avatar performance session。它们仍可以保留在动作蓝图里，但必须标成后续实施，不应在当前全流程描述里暗示已经存在。实现时也不要优先写通用 `frame` 小序列，而要参考已有三个 session 的结构：构造时 capture 当前 model frame / params / alpha，`start()` 获取 performance lock，tick 中按 progress 计算 Yui 专用 pose，`stop()` / `cancel()` 恢复。设置段的理想方向仍是“熟练介绍 -> 心虚保护”，归还控制仍是“收住动作后再 release”，打断仍是“先小抗议，连续后才生气退出”；只是实现方式必须沿用 Yui adapter 的模型参数驱动，不把剧情塞进通用 `AvatarPerformanceStage`。

整体情绪曲线按“已实现 + 待实施”分层理解：

1. 已实现，刚醒：安静、迷糊、柔和，使用 `YuiGuideWakeup` + `Live2DWakeupSession`。
2. 已实现，首句打招呼：开心、亲近，使用 intro hug + gift heart。
3. 待实施，语音入口：从亲近退回引导，期待但不挡输入区。
4. 待实施，猫爪控制：得意、炫耀，用 LookAt 和轻反馈服务真实按钮。
5. 部分已实现，插件预览：dashboard 期间角落陪看；打开前和回场动作待补。
6. 待实施，设置一瞥：熟练介绍 -> 慌张保护。
7. 待实施，归还控制：轻松、满足、正式收尾。
8. 待实施，轻微打断：惊讶、小抗议，不破坏当前场景。
9. 待实施，生气退出：短暂爆发，然后退场恢复。

## 后续实施方案（按已有实现扩展）

这一节只列适合在现有实现方式上继续补的内容。原则不是“多拼几个通用小 preset”，而是复用现在已经验证过的模式：每个重要段落新增一个首页专用 `Live2D...Session`，由 `YuiGuideAvatarStage` 暴露语义方法，由 `YuiGuideDirector` 在已有 scene、voice cue、handoff 生命周期里调用。session 内部直接使用 Yui 模型已有参数和当前 model frame，失败时 fallback，不新增独立业务入口，不把首页 scene 名写入通用 `AvatarPerformanceStage`。

当前可复用的实现模板：

1. `Live2DWakeupSession`：适合做“从安静到醒来”的参数 timeline，重点是眼睛、头、身体、右手挥手。
2. `Live2DIntroGreetingHugSession`：适合做“近景切入/拥抱/情绪亲近”，已经验证大 scale、大 frameY、双手参数、表情参数和 release settle。
3. `Live2DIntroGiftHeartSession`：适合做“明显的开心反馈”，已经验证横摆、跳动、手臂、耳朵、头发、裙摆、挂饰、鞋带等 Yui 专用参数。
4. `Live2DPluginDashboardCornerSession`：适合做“真实 UI 接管时让位/角落陪看”，已经验证先隐藏、再斜探到角落、提升 container z-index、关闭时恢复 frame / alpha。

### 1. 语音入口承接动作

目标：不要只做 LookAt。承接 `playIntroGreetingReply()` 的 hug / gift heart 后，用一个明显的“从近景退回引导位，然后指向语音入口”的 session，让用户看懂 YUI 从撒娇打招呼切换到功能引导。

实现位置：

1. `static/yui-guide-avatar-stage.js` 新增 `Live2DIntroVoiceEntrySession` 和 `playIntroVoiceEntryGuide(options)`。
2. `static/yui-guide-director.js` 在 `playIntroGreetingReply()` 之后调用；网页端放在输入框激活完成后，桌面端直接进入。

动作内容：

1. 起点读取当前 model frame；如果 hug final placement 仍在近景，先用 420-680ms 退到半身引导位，scale 从当前值回落到约 `1.12-1.18`，frameY 从 hug 的大下移收回一半以上。
2. 头和眼睛看向输入区/语音按钮，身体向目标侧倾；不是 10px 小倾，而是用 `ParamAngleX/Y/Z`、`ParamBodyAngleX/Y/Z` 给出清楚的上半身指向。
3. 手部用 `Param90-Param96` 做“示意按钮”的轻伸手：不需要新 motion 文件，直接复用 hug/gift 已确认可写的 forearm / hand / wave 参数。
4. 说到期待用户声音时，不再做新的大 hug；改成短 0.8-1.1s 的半身前倾，cheek / mouth / eye smile 稍增，然后退回不挡输入区的位置。

降级：

1. reduced motion 下取消 frame 大位移，但保留头眼方向和手部示意的最终姿态短停。
2. 模型参数缺失时按现有 `hasAnyWakeupParam` / mapped params 方式 fallback，不阻塞 intro 文案、语音和按钮 showcase。

### 2. 猫爪控制预告与开关反馈

目标：让 YUI 真的“参与操作展示”，而不是旁边轻轻点头。动作要参考 gift heart 的参数丰富度，用 Yui 的手臂和身体参数表达“我来按这个秘密开关”。

实现位置：

1. `static/yui-guide-avatar-stage.js` 新增 `Live2DCaptureCursorDemoSession`，暴露 `startCaptureCursorDemo(options)`、`markCapturePanelOpened()`、`markCaptureSwitchEnabled(kind)`、`stop()`。
2. `static/yui-guide-director.js` 在 `takeover_capture_cursor` 已有 ghost cursor 移动、点击、面板展开、开关启用节点调用这些 mark 方法。调用点必须贴着现有自动点击流程，不能新造并行流程。

动作内容：

1. 点击猫爪按钮前：YUI 向按钮方向明显探身，身体侧倾 `2-5deg` 量级，头眼先到按钮，右手或双手进入预备姿势。可复用 `Param90/92/95` 做右手准备，必要时 `Param91/93/96` 做双手兴奋。
2. 面板展开后：frame 明显让位到面板反方向，幅度应按当前模型 bounds 和面板 rect 算，至少达到“用户一眼看出她让开了”的程度；这类动作参考 plugin corner 的 frame 计算方式，不写固定小 px。
3. 总开关打开：做一次参数驱动的开心反馈，优先手臂/耳朵/头发联动，类似 gift heart 的短版，而不是普通 `tinyHop`。
4. 键鼠控制打开：头眼跟 ghost cursor 到开关，再回看用户；手部从“按开关”回到“展示完成”的 pose。

降级：

1. reduced motion 下保留构图让位的最终状态和头眼方向，取消连续跳动。
2. 如果面板 rect 拿不到，只做参数姿态，不猜 UI 坐标。

### 3. 插件 preview 前后补强

目标：现有 corner peek 已经比小动作更可靠，后续只补“打开前的兴奋切入”和“回首页后的邀功”，不要破坏 dashboard 期间真实 UI 是主角。

实现位置：

1. 保留 `Live2DPluginDashboardCornerSession` 作为 dashboard 期间唯一主要动作。
2. 新增 `playPluginDashboardLeadIn(options)` 和可选 `playPluginDashboardReturn(options)`，二者都放在 `YuiGuideAvatarStage`。
3. `static/yui-guide-director.js` 在 `runPluginDashboardPreviewScene()` 中，dashboard 打开前调用 lead-in；`stopPluginDashboardCornerPeekPerformance()` 与 `waitForHomeMainUIReady()` 成功后调用 return。

动作内容：

1. lead-in：短促近景或侧身“想起好东西”，scale 可到 `1.16-1.24`，手部参数进入邀请/展示姿态，持续不超过 1.2s。
2. dashboard 期间：继续使用 corner peek 的 45 度斜探、hide/appear、z-index 提升和 alpha 恢复，不再叠加额外跳动。
3. return：从 corner session 恢复后，不做小点头，而是做一次明显但短的“邀功”反馈：身体回正、双手轻摆、耳朵/头发跟随一下，时长 0.8-1.4s。

降级：

1. popup blocked、handoff 失败、runId 变化或教程终止时只 cleanup，不播放 lead-in / return。
2. reduced motion 下 dashboard 期间直接落到 cornerFrame，关闭后直接恢复。

### 4. 设置一瞥情绪反转

目标：用 Yui 模型参数做喜剧反应，而不是通用 shake。第二段 cue 上要有“糟糕，被你看到太多了”的身体和表情变化。

实现位置：

1. `static/yui-guide-avatar-stage.js` 新增 `Live2DSettingsPeekReactionSession`，暴露 `playSettingsPeekIntro(options)` 和 `playSettingsPeekPanic(options)`；也可以一个 session 内用 cue 切 phase。
2. `static/yui-guide-director.js` 在设置按钮点击前调用 intro；在已有 `showSecondLine` timeline cue 或第二段文案开始处调用 panic。

动作内容：

1. intro：看向设置按钮，身体向齿轮方向倾，手部用 `Param90-Param96` 做“我带你看这里”的展示 pose。
2. 面板展开：像 plugin corner 一样根据真实面板 rect 做让位，不挡设置项。不要写固定 40px 小挪动。
3. panic：用表情参数 `ParamBrowRY/LY`、`ParamBrowRAngle/LAngle`、`ParamMouthForm`、`ParamCheek` 加头身后撤，接 2 次以内的身体小摆；耳朵、头发、裙摆可以跟随，但持续时间短。
4. 收住：横移到不挡 UI 的一侧，视线从设置项切回用户，像故作镇定。

降级：

1. reduced motion 下保留 panic 的最终表情和让位构图，取消抖动。
2. 不修改设置页业务逻辑、菜单结构或 overlay 状态。

### 5. 归还控制收尾

目标：收尾不应只是清锁，也不应强行回到全身站位。归还控制要保持“动作二”结束后的当前位置和构图，在这个位置上做清楚的完成反馈，再交还控制权。

实现位置：

1. `static/yui-guide-avatar-stage.js` 新增 `Live2DReturnControlFinaleSession` 和 `playReturnControlFinale(options)`。
2. `static/yui-guide-director.js` 在执行最终 `returnControl` / `finish` 之前调用，并设置短 timeout，避免动作失败阻塞教程结束。

动作内容：

1. 起点就是动作二结束后的当前 model frame，不回 `homeFullSafe`，不重新居中，不写死全身位置。
2. 在当前位置使用 gift heart 的短版完成反馈：一次明确上扬/落地，双手或右手挥一下，耳朵/头发/裙摆随动，但不要再召唤爱心。
3. 反馈后回到动作二结束位附近，最后 300-500ms 站稳，看向用户，再由 director 释放教程层。

降级：

1. reduced motion 下保持动作二结束位并短停。
2. 若模型不可用，跳过动作，继续教程完成。

### 6. 打断与生气退出

目标：打断动作要用 Yui 的“身体退让 + 表情/手部参数”表达，而不是泛用小 shake。生气退出要像一个短剧场镜头，且结束后干净恢复。

实现位置：

1. `static/yui-guide-avatar-stage.js` 新增 `Live2DInterruptReactionSession`，支持 `light` 和 `angryExit` 两种 phase。
2. `static/yui-guide-director.js` 在已有 light interrupt / angry exit 分支调用，不能用动作模块替代阈值判断。

动作内容：

1. 轻微打断：根据鼠标/事件点计算躲避方向；frame 做明显反向后撤，头眼看向打断点，手部收回或护住，持续 0.6-1.0s 后回当前场景基线。
2. 生气退出：先用近景前冲，表情参数先变凶，双手/身体压上来；台词尾部再横向离场或向边缘退走，alpha 降到 0。
3. 离场完成后 release，director 继续走现有终止、模型恢复和 dashboard termination 通知链路。

降级：

1. reduced motion 下取消前冲和横移，只做最终表情/方向或直接跳过。
2. 不保存 angry 状态，不影响用户原模型恢复。

## 成熟立绘演出转译

参考 Ren'Py ATL / transition、Live2D Cubism motion / expression / LookAt 的设计方式，首页后续动作应按“立绘演出”来设计，而不是按“角色在 UI 上乱跑”来设计。

### 立绘演出的核心不是大动作

成熟 galgame / Live2D 演出通常靠这些东西建立画面：

1. 站位：角色在左、中、右、近景、边缘的变化，决定谁是当前焦点。
2. 表情：开心、得意、疑惑、惊讶、生气的切换，比大位移更能表达情绪。
3. 视线：眼睛先看目标，用户自然会跟着看过去。
4. 停顿：表情变化后停 200-500ms，让用户读懂反应。
5. 切镜：短近景用于情绪峰值，不用于长时间讲解。
6. 让位：真实 UI 出现时，角色必须主动退到边缘或半身侧位。
7. 回收：每个动作都要有 settle，不要停在奇怪的偏移和旋转上。

### 每段动作按四拍写

后续每个首页场景都应该有四拍，而不是一串平均用力的动作：

1. 预告：YUI 的眼睛或身体先朝目标偏过去。
2. 展示：真实 UI 高亮、打开、点击或切换。
3. 反应：YUI 给出表情变化，例如得意、惊讶、心虚。
4. 收住：YUI 回到不挡 UI 的位置，继续说话或交还焦点。

### 表情优先级

如果 motion 资源有限，优先做表情/视线，不要优先做位移：

1. 开心：微笑眼、嘴角上扬、轻微前倾。
2. 期待：看用户 -> 看按钮 -> 再看用户，停顿更长。
3. 得意：半眯眼或笑眼，身体轻微上扬，短 `hop` 只用一次。
4. 疑惑：头部轻歪，眼睛先看目标再扫回用户。
5. 惊讶：眼睛睁大、身体小幅后撤，不要长时间 shake。
6. 心虚：看设置项 -> 看用户 -> 立刻挪开，像“被发现了”。
7. 生气：眉形/嘴形先变，身体再前冲；不要直接滑走。

## 幅度与进出场校准

参考成熟 VN/galgame 立绘演出，动作幅度要分三层：小幅用于“活着”，中幅用于“表达”，大幅只用于“切镜/进出场”。如果全程都小幅，画面会没精神；如果全程都大幅，就像 UI 上有东西乱窜。

### 幅度分层

#### 小幅动作

用途：说话中、等待中、看按钮时的生命感。

范围：

1. `x: 4-12px`
2. `y: 2-8px`
3. `scale: 0.004-0.018`
4. `rotate: 0.5-2deg`
5. 时长 220-520ms，循环呼吸可到 3200-4600ms。

适用：

1. 呼吸。
2. 轻点头。
3. 看向按钮前的小歪头。
4. 普通讲解中的微反应。

#### 中幅动作

用途：用户应该明显看见 YUI 在“指、让、靠、躲”。

范围：

1. `x: 16-56px`
2. `y: 8-28px`
3. `scale: 0.025-0.08`
4. `rotate: 2-5deg`
5. 时长 260-620ms。

适用：

1. 猫爪发现时探身。
2. 面板打开后让位。
3. 设置第二段后撤。
4. 轻微打断躲鼠标。

#### 大幅动作

用途：进场、退场、短近景、强情绪峰值。

范围：

1. `x: 80-280px`
2. `y: 18-48px`
3. `scale: 0.08-0.18`
4. `rotate` 通常仍控制在 0-4deg，除非是喜剧打断。
5. 时长 420-820ms。

适用：

1. 插件回场。
2. 生气退出。
3. close-up 切近景。
4. 从边缘回到全身站位。

### 自然进场

YUI 的进场不要从完全静止突然出现，也不要像网页浮层一样淡入。成熟立绘进场通常是“从屏幕边缘或稍远位置进入 + 轻透明/缩放 settle”。

首页可用三种进场：

1. `softEnterFromRest`：开场苏醒已经使用，不重做。只在醒来后 settle。
2. `returnFromEdge`：dashboard 或设置让位后，从边缘探回。
3. `closeCutIn`：情绪峰值短近景，从当前半身构图快速靠近。

#### `returnFromEdge`

动作：

1. 起点：`x: edgeSide 72-120px`，`y: 8-16`，`scale: -0.05`，`opacity: 0.82-0.9`。
2. 第一拍：移动回 `homeWaistGuide` 附近，`x` 仍保留 12-20px 的侧向偏移，时长 360-520ms。
3. 第二拍：`curiousTilt` 或 `lookNod`，停 220-360ms。
4. 第三拍：回到基线或接 `happyHop`。

自然性要求：

1. 回场先探头/探身，再完全回正。
2. 不要从透明直接 pop 到中心。
3. 如果刚从 dashboard 回来，可以让她先看 dashboard 方向，再看用户。

#### `closeCutIn`

动作：

1. 起点：当前半身构图。
2. 进入：`scale +0.1`，`y -18`，`x` 向屏幕中心修正 8-16px，180-240ms。
3. 表情变化：期待/惊讶/生气先发生，停 240-480ms。
4. 退出：回到半身或让位构图，300-420ms。

自然性要求：

1. 近景前必须有情绪理由。
2. 近景后必须退出，不能挂在前景讲完整段。

### 自然出场

出场要有“离开意图”，不能只是 opacity 变 0。角色要先转移视线或身体方向，再移动离开。

首页可用三种出场：

1. `yieldOut`：真实 UI 接管时退到边缘，不是真离场。
2. `finishSettle`：教程结束前站稳，release 后回普通 idle。
3. `angrySlideOut`：生气退出，带情绪离场。

#### `yieldOut`

动作：

1. 看目标 UI 200-360ms。
2. 向反方向移动 `x: 56-110px`。
3. 缩小 `scale: -0.04` 到 `-0.08`。
4. opacity 降到 `0.82-0.9`。
5. 停在边缘陪看，不继续大动作。

自然性要求：

1. 退场方向必须远离真实 UI。
2. 退到边缘后身体回正，不要歪着挡面板。

#### `finishSettle`

动作：

1. 保持动作二结束后的当前位置，不回 `homeFullSafe`。
2. 轻微 `lookNod`。
3. 如需收尾，用一次短完成反馈，幅度围绕当前位置展开。
4. 最后 300-500ms 不再播放跳、抖、横移，只在当前位置站稳。

自然性要求：

1. release 前必须先视觉收住。
2. 不要在大跳的中间交还控制权。
3. 不要为了收尾破坏动作二结束后的构图连续性。

#### `angrySlideOut`

动作：

1. `angryLeanIn` 先前冲，建立冲突。
2. 台词尾部转开视线。
3. `slideExit` 横向离场，`x: 180-280px`，`opacity: 0`，520-760ms。
4. 离场完成后 release。

自然性要求：

1. 先生气，再离开。
2. 不能直接淡出。
3. 离场方向应避开用户当前关注的 UI。

### 幅度是否够的判断

后续实现时，用这四个问题检查动作够不够：

1. 用户不看文案，只看 YUI，能不能看出她在“指哪里”？
2. 面板打开时，YUI 有没有明显让出焦点，而不是只小挪 10px？
3. 情绪峰值有没有近景或中幅变化，而不是只有表情一闪？
4. 场景结束前有没有 settle，而不是动作戛然而止？

如果答案是否定的，幅度通常不够；优先增加 `x` 位移和构图切换，不要先增加摇晃次数。

## 具体动作规格

所有数值都是以当前 `#live2d-container` 为基准的相对 frame。实现时可以按屏幕和模型大小微调，但动作关系要保持：小动作 4-12px，中动作 16-56px，大动作只在切镜/进出场使用。

### `softBreathe`

用途：普通讲解时让 YUI 活着，但不抢戏。

动作：

1. `y: 0 -> -3 -> 0`，周期 3200-4600ms。
2. `scale: 1 -> 1.006 -> 1`。
3. 不加 `rotate`。
4. 循环时只在当前 session 内生效，场景结束停止。

### `lookNod`

用途：确认用户、确认按钮、表示“对，就是这里”。

动作：

1. 先 LookAt 目标 280-420ms。
2. `y: 0 -> 3 -> 0`，时长 220-320ms。
3. `scale` 不变或最多 `+0.004`。
4. 适合语音入口、开关打开、归还控制前。

### `sideLeanLeft` / `sideLeanRight`

用途：看向按钮、发现秘密道具、侧身让出 UI。

动作：

1. 向目标方向移动 `x: -14` 或 `x: +14`。
2. 轻微上提 `y: -4`。
3. 缩放 `scale: +0.012`。
4. 旋转方向与身体探出方向相反：向左探时 `rotate: +2deg`，向右探时 `rotate: -2deg`。
5. 进入 260-360ms，保持 360-800ms，回正 260-420ms。

说明：

1. 这是“探身看”的动作，不是横向滑走。
2. 用在猫爪按钮、插件入口、设置按钮前。

### `uiYieldLeft` / `uiYieldRight`

用途：真实 UI 面板打开后让位。

动作：

1. 向 UI 反方向移动 `x: -42` 到 `-86`，或 `x: +42` 到 `+86`。
2. 下沉 `y: 4-10`，表现退后。
3. 缩小 `scale: -0.035` 到 `-0.07`。
4. 旋转回到 `0deg`，不要歪着挡 UI。
5. opacity 可从 `1` 降到 `0.82-0.92`。
6. 进入 420-620ms，保持到该 UI 演示结束。

说明：

1. 让位动作必须比 UI 展开晚 80-160ms，像看到面板打开后主动退开。
2. dashboard / 设置面板 / 猫爪开关区域都用这个动作。

### `closeIn`

用途：短近景，表达期待、兴奋、惊讶。

动作：

1. `scale: +0.08` 到 `+0.14`。
2. `y: -10` 到 `-24`，像靠近屏幕。
3. `x` 根据不挡 UI 的方向偏移 `-12` 到 `+12`。
4. `rotate` 只用 `-1.5deg` 到 `+1.5deg`。
5. 进入 180-280ms，停 220-520ms，退出 280-420ms。

说明：

1. closeIn 不能超过 1.2 秒。
2. 用完必须退回半身或让位构图。

### `curiousTilt`

用途：疑惑、发现、卖乖。

动作：

1. `rotate: -3deg` 或 `+3deg`。
2. `x: 6-12px` 向视线目标靠近。
3. `y: -2`。
4. 进入 260ms，保持 500-900ms。
5. 回正 320ms。

说明：

1. 头部/身体歪斜要小，像“欸？”而不是摔倒。
2. 用在猫爪、插件入口、设置前半段。

### `happyHop`

用途：完成、回场、得意，不用于普通按钮介绍。

动作：

1. 起跳：`y: -22` 到 `-34`，`scale: +0.045` 到 `+0.075`，`rotate: +2deg` 或 `-2deg`，160-220ms。
2. 滞空回摆：`y` 回到起跳高度的 35%，`scale` 回到 `+0.015`，`rotate` 反向 0.8-1.5deg，100-160ms。
3. 落地：回到基线，180-260ms。
4. 总时长 420-620ms。

说明：

1. 一段场景最多一次。
2. 只用在插件回场、归还控制、非常明确的完成反馈。

### `tinyHop`

用途：开关打开或轻度开心。

动作：

1. `y: -8` 到 `-14`。
2. `scale: +0.018` 到 `+0.03`。
3. 不旋转或 `rotate: 0.8deg`。
4. 总时长 260-360ms。

说明：

1. 用来替代过大的 `happyHop`。
2. 猫爪总开关打开时用这个更合适。

### `startleBack`

用途：设置第二段、轻微打断、看到用户乱点。

动作：

1. 先 `closeIn` 或维持当前构图。
2. 快速后撤：`y: 8-16`，`scale: -0.025` 到 `-0.05`，120-180ms。
3. `rotate: -2deg` 或 `+2deg`，方向远离鼠标/目标。
4. 停 160-260ms。
5. 转入 `smallShake` 或 `uiYield`。

说明：

1. 这是“吓一跳”的第一拍。
2. 不要直接从正常讲解进入大幅 shake。

### `smallShake`

用途：惊慌、抗议、心虚。

动作：

1. 循环 2-3 次。
2. `x: -7 -> +7 -> -5 -> +5 -> 0`。
3. `rotate: +1.4deg -> -1.4deg -> +0.8deg -> -0.8deg -> 0`。
4. `y` 最多 `2px`。
5. 总时长 260-420ms。

说明：

1. 设置第二段用 2 次即可。
2. 轻微打断可以用 2-3 次。
3. 不要用长时间持续 shake。

### `dodgeAway`

用途：用户轻微打断时躲开鼠标。

动作：

1. 根据鼠标方向反向移动 `x: 24-46`。
2. `y: -4` 或 `y: 6`，看当前构图。
3. `rotate` 远离鼠标 `2-4deg`。
4. 进入 140-220ms，停 180-300ms。
5. 回到当前场景基线 280-420ms。

说明：

1. 这是短反应，不改变当前场景目标。
2. 回正后继续原场景或进入打断台词。

### `angryLeanIn`

用途：生气退出第一拍。

动作：

1. `scale: +0.12` 到 `+0.18`。
2. `y: -18` 到 `-30`。
3. `x` 向用户中心靠近 `-12` 到 `+12`。
4. `rotate: -1deg` 到 `+1deg`，不要滑稽。
5. 进入 160-240ms，停 300-600ms。

说明：

1. 表情先变凶，再做前冲。
2. 不要直接离场，否则没有“生气”的读秒。

### `slideExit`

用途：生气退出最后离场。

动作：

1. `x: +180` 到 `+280` 或 `x: -180` 到 `-280`，按当前不挡 UI 的方向选择。
2. `y: 8-24`。
3. `scale: -0.06` 到 `-0.1`。
4. `opacity: 1 -> 0`。
5. 时长 520-760ms。

说明：

1. 离场后立即 release。
2. 不保留 opacity 或 transform inline style。

## 后续流程画面版

这一节只描述苏醒打招呼之后的画面。开场已经有实现，不在这里重做。

### 语音入口：从“刚认识”到“邀请你说话”

YUI 打完招呼后，不要立刻进入工具讲解。她先像确认用户还在一样看向用户，然后把目光轻轻移到输入区。网页端如果刚发生过一次解锁点击，她可以低头看一下输入框，像是注意到用户已经回应了；桌面端跳过这个确认，直接转向麦克风按钮。

介绍麦克风时，她应该站在按钮反方向，身体稍微侧开，眼睛先看按钮，再回头看用户。这里的画面应该像视觉小说里角色指着屏幕边缘的功能点，而不是像弹窗说明。说到“想听到你的声音”时，她可以短暂切近景，表情更期待一点，像往前凑了一下，但这个近景只持续一瞬，随后退回半身，把输入区还给用户。

具体动作：

1. 进入 `homeWaistGuide` 后先 `lookNod` 用户中心。
2. 网页端若有点击承接，短看输入框后回正。
3. 看麦克风按钮时用 `sideLeanLeft` 或 `sideLeanRight`，方向取决于按钮所在侧。
4. “想听到你的声音”附近用一次 `closeIn`，不要用 `happyHop`。

### 猫爪控制：发现秘密道具

进入猫爪段落时，YUI 的情绪从温柔变成“我要给你看个厉害的”。她看到猫爪按钮时不要直接点，而是先探身看过去，头部轻歪，眼睛亮一点。这个瞬间要像 galgame 里角色突然露出“我有主意了”的表情。

ghost cursor 准备点击猫爪按钮前，YUI 先用视线预告按钮位置；点击后面板打开，她马上往开关反方向挪开。她不是站在面板上讲解，而是像站在展示柜旁边。总开关打开时，她可以轻轻开心一下；键鼠控制打开时，她的眼睛跟着 cursor 走到开关，再回头看用户，表情像在说“看，我真的可以接管这个小爪子”。

这一段的重点是“展示能力”，不是“角色抢戏”。动作要清晰、短促、有停顿，不能一直跳。

具体动作：

1. 发现猫爪按钮时用 `curiousTilt`，不要立即点击。
2. 点击前 `lookAt(#${p}-btn-agent)` 280-420ms。
3. 面板展开后用 `uiYieldLeft` 或 `uiYieldRight` 退到开关反方向。
4. 总开关打开时用 `tinyHop`。
5. 键鼠控制打开时只用 LookAt 跟随 ghost cursor，不额外跳。

### 插件预览：突然想起更大的展柜

插件段落要像她突然想起还有一个更大的玩具箱。开头可以短近景一下：YUI 露出兴奋表情，稍微靠近屏幕，好像忍不住要把用户拉过去看。但当插件入口或 dashboard 要成为焦点时，她必须立刻退开。

dashboard 打开后，首页 YUI 的最佳状态不是继续演，而是“在边上陪看”。她可以半透明或靠边，只保留轻微呼吸和偶尔看向 dashboard 内容。等 dashboard 完成回到首页，她再从边缘探回来，做一个轻快的得意动作，像“怎么样，是不是很多东西”。这个回场动作比打开前更重要，因为它让跨窗口演示有结束感。

具体动作：

1. “还没完呢”时用 `closeIn` 180-280ms 进入，停不超过 420ms。
2. dashboard 打开前用 `uiYieldLeft` / `uiYieldRight` 或 `homeEdgeYield`。
3. dashboard 期间只保留 `softBreathe`，不播放 `happyHop`。
4. dashboard 完成回到首页后，用 `sideLeanLeft/Right` 探回，再用一次 `happyHop`。

### 设置一瞥：从熟练到心虚

设置段落要有明确的情绪反转。前半段 YUI 是熟门熟路的小向导：看齿轮，侧身让位，打开后站到不挡菜单的位置。她可以带一点得意，像“这里我很熟”。

第二段 cue 到来时，画面应该突然变成喜剧反应。她先看见设置里的角色相关项目，眼睛变大，身体小幅后撤，像意识到“糟糕，给你看太多了”。然后不是疯狂抖，而是轻轻抖两下，赶紧横移到一边，表情从惊讶变成故作镇定。她的视线可以在设置项和用户之间来回一次，形成“别乱动这个”的潜台词。

这一段不要做成单纯 `shake`，而要做成“发现 -> 心虚 -> 保护式让位 -> 假装镇定”的四拍。

具体动作：

1. 打开设置前用 `sideLeanLeft/Right` 指向齿轮。
2. 设置面板展开后用 `uiYieldLeft/Right`。
3. 第二段 cue 触发时先 `startleBack`。
4. 紧接 `smallShake` 2 次。
5. 然后 `uiYieldLeft/Right` 横移到不挡设置项的位置。
6. 最后 `lookNod` 用户中心，但表情保持心虚/故作镇定。

### 归还控制：正式把小窝交回用户

归还控制权不是简单退场，而是整段引导的谢幕。YUI 不需要从动作二之后的位置回到完整站位；她应该保留动作二结束后的构图，先认真看用户一下，像确认用户已经了解小窝。随后在原位置用一个轻快但不夸张的动作表示完成，可以是短跳、轻挥手或开心点头。

真正触发 `returnControl` 前，动作要收住。她最后应该站稳、看向用户，然后才释放教程层。用户看到的感受应该是“她带我参观完，把控制权交回来了”，而不是“自动脚本结束了”。

具体动作：

1. 保持动作二结束后的构图，不回 `homeFullSafe`。
2. 在当前位置 `lookNod` 用户中心。
3. 台词中段使用一次 `happyHop`。
4. `returnControl` 前 300-500ms 停止跳动，只保留动作二结束位附近的站稳和看用户。

### 轻微打断：被戳了一下

轻微打断要像 VN 里角色被玩家打断台词的小反应。YUI 可以突然切近一点，眼睛睁大，看向鼠标方向，然后往反方向小躲一下。她的表情是惊讶和不满，不是愤怒。动作结束后要回到当前场景原本构图，表示教程还在继续。

如果用户多次轻微打断，可以换台词和表情，但动作不要每次都更夸张。真正升级到 angry exit 之前，YUI 仍然是在“抗议”，不是“退出”。

具体动作：

1. 首次有效打断用 `startleBack`。
2. 看向鼠标方向 240-360ms。
3. 用 `dodgeAway` 反向躲开。
4. 如果台词需要强调，再加一次 `smallShake`。
5. 回到当前场景基线，不改变原场景目标。

### 生气退出：短爆发，不留状态

生气退出要像一个短的戏剧切镜。YUI 先前冲，表情和眉形先变凶，再说重话。台词尾部“哼”附近，她转开视线或横移离开，opacity 降低，最后释放演出。这个场景要有情绪，但不能留下长期生气状态，也不能破坏恢复用户模型。

关键是“爆发 -> 退场 -> 干净恢复”，不要把 angry exit 做成持续压迫用户的状态。

具体动作：

1. 表情先切 angry。
2. 用 `angryLeanIn` 前冲质问。
3. 台词中段可用一次低幅 `smallShake`，不是持续 shake。
4. “哼”附近停止看用户，视线转开。
5. 用 `slideExit` 离场，然后 release。

## 设计原则

### 导演原则

1. YUI 先动，再说话；文字或语音通常延后 120-220ms。
2. 每个场景只保留一个主动作意图：看见、邀请、指向、让位、得意、收尾、抗议。
3. 指向 UI 时遵循“看目标 -> 轻微让位 -> 说话 -> 回看用户”。
4. 大动作只发生在场景切换或情绪峰值；普通讲解使用小幅呼吸、视线和构图变化。
5. 任何自动化点击前，YUI 必须先用视线或身体方向预告目标，避免用户感觉按钮被无缘无故操作。

### 技术原则

1. 使用 `AvatarPerformanceStage` 的 `frame / preset / motion / motionWithFallback / lookAt / wait / clearLookAt / clearParams` 拼动作。
2. `hop` 用于完成、得意、回场；`shake` 用于惊慌、拒绝、打断反馈。
3. motion group 只能作为加分项；资源缺失时必须能退回 frame/preset。
4. LookAt 只做临时覆盖，场景结束必须清理。
5. reduced motion 下保留构图切换和 LookAt 语义，取消跳、抖、快速冲刺和持续浮动。

## 构图系统

构图只表达演出位置，不承载业务状态。具体数值由 `static/yui-guide-avatar-stage.js` 的 `profile.composition` 实现。

### `homeFullSafe`

用途：开场、强恢复、生气退场前的完整站位。归还控制收尾不默认回到这里，应保持动作二结束位。

视觉要求：

1. 全身或接近全身可见。
2. 默认在主 UI 右侧或不遮挡聊天输入区的位置。
3. 适合用户建立“这是引导角色”的第一印象。

### `homeWaistGuide`

用途：主体讲解、按钮介绍、自动化演示前预告。

视觉要求：

1. 半身构图，YUI 有存在感但不压住目标控件。
2. 可根据目标在左/右两侧轻微偏移。
3. 适合配合 LookAt 指向按钮和面板。

### `homeCloseInvite`

用途：亲近邀请、兴奋强调、惊讶、轻微抗议。

视觉要求：

1. 只用于 0.5-1.8 秒的短镜头。
2. 不遮挡输入框、弹层标题、开关和教程按钮。
3. 用完必须退回 `homeWaistGuide` 或 `homeFullSafe`。

### `homeEdgeYield`

用途：插件 dashboard、设置面板、真实 UI 演示期间让出舞台。

视觉要求：

1. YUI 退到屏幕边缘或轻微透明。
2. 仍能看到她在“陪看”，但焦点属于真实 UI。
3. 不允许在该构图里播放大幅 motion。

## 动作词典

### `wakeSoftSettle`

含义：苏醒后从轻微下沉回到稳定站位。

组合：

1. `frame(homeFullSafe)`。
2. `frame({ y: 14-20, scale: 0.985 })` 作为起点。
3. `frame(homeFullSafe, 620-760ms)` 回正。

### `noticeUser`

含义：睁眼后确认“看见用户”。

组合：

1. LookAt 从微低点回到 viewport 中心。
2. 停顿 260-420ms。
3. 轻微 `pulse`，幅度要比普通强调小。

### `pointThenReturn`

含义：介绍某个按钮或面板。

组合：

1. `lookAt(target)` 360-520ms。
2. `frame(homeWaistGuide)` 向目标反方向让位。
3. 说话中保持 1-2 秒。
4. `lookAt(userCenter)` 或 `clearLookAt()`。

### `curiousLean`

含义：看到新按钮、开关或插件时的好奇探身。

组合：

1. `frame({ x: towardTarget * 8-18, y: -4, scale: +0.015, rotate: towardTarget * -2 })`。
2. `motionWithFallback(happy, fallback: pulse)`。
3. 退回当前半身构图。

### `excitedPop`

含义：短促兴奋，不能长时间霸屏。

组合：

1. `frame(homeCloseInvite, 220-300ms)`。
2. `preset(hop, durationMs: 360-460)` 或 `pulse`。
3. `frame(homeWaistGuide, 280-420ms)`。

### `yieldStage`

含义：真实 UI 需要成为主角时主动让位。

组合：

1. `lookAt(targetPanel)`。
2. `frame(homeEdgeYield, 420-620ms)`。
3. opacity 可轻降，但不要低到像消失。
4. 自动化演示结束后再 `hop` 回场。

### `panicProtect`

含义：设置第二段或用户连续打断时的惊慌保护。

组合：

1. `motionWithFallback(surprised, fallback: shake)`。
2. `preset(shake, cycles: 2-3)`。
3. 保护式横移到不挡 UI 的一侧。
4. 回看用户。

### `angryExit`

含义：连续有效打断后的戏剧退场。

组合：

1. `frame(homeCloseInvite, 220-300ms)` 前冲。
2. `motionWithFallback(angry, fallback: shake)`。
3. 停顿到台词“哼”附近。
4. `frame({ x: exitSide, opacity: 0 }, 520-760ms)`。
5. release 并走原教程终止/恢复链路。

## 首页分镜

### 0. 准备阶段

触发条件：

1. storage 闸门已放行。
2. 首页主 UI、模型容器和浮动按钮已准备。
3. 教程判断需要启动。
4. 网页端若需要音频解锁点击，应先完成该平台前置条件；桌面端不需要等待这一步。

动作设计：

1. 临时加载 `yui-origin`，但不要改变用户持久模型设置。
2. YUI 可先处于透明或低存在感状态，等待 `runWakeupPrelude()` 接管。
3. 建立 `homeFullSafe`，防止醒来第一帧跳位置。
4. 开启教程期间的正脸锁；需要看按钮时只通过演出层临时 LookAt。

禁止事项：

1. 不创建新的覆盖舞台遮挡首页。
2. 不在准备阶段播放大 motion。
3. 不绕过 storage / tutorial manager 的启动闸门。

### 1. 苏醒、睁眼、打招呼

保留项：

1. `runWakeupPrelude()` 在前。
2. `playIntroGreetingReply()` 在后。
3. 闭眼、睁眼、抬头、短挥手的核心顺序不改。
4. 首句欢迎文案、语音 key、消息插入位置不改。

动作设计：

1. 苏醒前落到 `homeFullSafe`。
2. 睁眼前保持安静，不加额外粒子和抢眼镜头。
3. 睁眼后执行 `noticeUser`：视线从微低处回到用户中心，停 260-420ms。
4. 首句打招呼开始前做 `wakeSoftSettle`，像刚醒来站稳。
5. 打招呼期间只保留轻微呼吸或小幅 `idleFloat`，避免首句被动作抢戏。

成功标准：

1. 用户明确看到“她醒了、看见我、然后打招呼”。
2. 没有模型第一帧跳变。
3. 开场比现在更有生命感，但不改变流程语义。

### 2. 输入框解锁与语音入口

对应场景：`intro_basic`

平台差异：

1. 网页端的一次点击是为浏览器播放限制服务的前置交互，可作为进入 `intro_basic` 前后的动作承接点。
2. 桌面端没有这个前置点击，`intro_basic` 应直接从打招呼后的自然状态进入语音入口介绍。
3. 两端后续的语义一致：都要把用户注意力引到语音按钮，而不是把“点击输入框”设计成长期剧情目标。

动作设计：

1. YUI 从 `homeFullSafe` 进入 `homeWaistGuide`。
2. 网页端若刚完成点击，先看输入框 240-420ms，确认前置交互完成；桌面端跳过该确认动作。
3. 切到麦克风按钮目标，执行 `pointThenReturn`。
4. 说到“迫不及待”附近，短暂 `excitedPop`，然后马上退回半身构图。
5. 若用户停留不操作，只允许每 6-8 秒一次小幅 `curiousLean` 或 LookAt，不重复大 motion。

节奏：

1. 网页端 0-420ms：可短暂确认输入框/解锁点击。
2. 桌面端 0-220ms：直接转向麦克风按钮。
3. 麦克风目标明确后：语音/文字开始，YUI 让位讲解。

禁止事项：

1. 不遮挡输入框和麦克风按钮。
2. 不让 ghost cursor 或 highlight 在 YUI 大动作下失焦。

### 3. 猫爪与键鼠控制

对应场景：`takeover_capture_cursor`

动作设计：

1. 场景开始时 YUI 保持 `homeWaistGuide`，看向猫爪按钮。
2. “超级魔法按钮出现”前 120-200ms 执行 `curiousLean`。
3. ghost cursor 点击猫爪按钮前，YUI 先 `lookAt(#${p}-btn-agent)`。
4. 面板打开后，YUI 横移到开关反方向，让出总开关区域。
5. 开启总开关时做一次低幅 `pulse`，不要跳。
6. 开启键鼠控制时眼睛跟随 ghost cursor 到开关，再回看用户。
7. 讲到鼠标指针时，允许一次短暂追随真实鼠标方向，然后立即清 LookAt。

情绪：

1. 主情绪是“兴奋炫耀”，不是慌乱。
2. motion 优先 `happy`，缺失时用 `pulse`。

禁止事项：

1. 不把 YUI 放在面板中央。
2. 不在开关被点击的 300ms 内做大幅 frame 变化。

### 4. 插件预览与 Dashboard 接力

对应场景：`takeover_plugin_preview`

动作设计：

1. 首页内先保持 `homeWaistGuide`，看向插件相关入口。
2. 说“还没完呢”时做一个短 `excitedPop`，表现她突然想起还有好东西。
3. 打开管理面板前，YUI 退到 `homeEdgeYield`。
4. handoff 到 dashboard 前，她看向即将打开的方向，像把用户视线递出去。
5. dashboard 演示期间：首页 YUI 不抢焦点，只保留边缘陪看或隐藏。
6. dashboard 完成回到首页后，YUI 从边缘探回，使用一次轻 `hop` 表示“看吧，很厉害”。

跨窗口边界：

1. 入口是否打开、start/ready/done 回执是否收到，要分层验证。
2. 不在前端写死本地端口。
3. dashboard 接力失败时，使用已有 popup blocked/恢复链路，不新增孤立协议。

禁止事项：

1. 不在 dashboard 前景演示期间让首页 YUI 持续大幅动。
2. 不用装饰性镜头遮盖真实插件列表。

### 5. 设置一瞥

对应场景：`takeover_settings_peek`

动作设计：

1. 第一段开始时 YUI 在 `homeWaistGuide`，看向设置按钮。
2. ghost cursor 点击设置按钮前执行 `pointThenReturn`。
3. 设置面板展开后立刻 `yieldStage`，退到不挡侧栏和菜单项的位置。
4. 第一段情绪是“得意介绍”，可用小幅 `pulse`，不要惊慌。
5. `showSecondLine` cue 触发时，情绪切到 `surprised`，短切 `homeCloseInvite`。
6. “啊啊啊不行”附近执行 `panicProtect`：小抖 -> 横移让开 -> 回看用户。
7. 结束前看向关闭/返回区域，再看用户，表示“这里先别乱动”。

节奏：

1. 设置按钮点击前：指向明确。
2. 面板打开后：立即让位。
3. 第二段台词开始：情绪反转。
4. 情绪反转后 0.8-1.2 秒内回到不挡 UI 的构图。

禁止事项：

1. 不遮挡真实设置条目。
2. 不把惊讶动作做成持续摇晃。
3. 不修改设置页业务逻辑或菜单结构。

### 6. 归还控制权

对应场景：`takeover_return_control`

动作设计：

1. 保持动作二结束后的让位构图，不回 `homeFullSafe`。
2. 开头先在当前位置回看用户，确认引导结束。
3. 台词中段做一次 `hop`，表达轻快收尾。
4. 触发 `returnControl` 前，YUI 收住动作，避免 release 时视觉突变。
5. release 后恢复普通 idle 和用户原模型。

成功标准：

1. 用户感到“演出结束，电脑还给我了”。
2. 所有临时 LookAt、参数、frame、教程锁都清理。
3. 不影响正常最小化、拖拽、聊天窗口和浮动按钮。

### 7. 轻微打断

对应场景：`interrupt_resist_light`

动作设计：

1. 打断发生时，YUI 快速切到 `homeCloseInvite`，但只停短镜头。
2. 播放 `surprised` 或 `shake`，幅度控制在“抗议”而不是“崩坏”。
3. 看向用户鼠标方向 240-360ms。
4. 做一次反向躲闪，再回到当前场景原构图。
5. 多次轻微打断时可以换台词，但动作不能逐次升级太快；升级由 angry exit 阈值决定。

禁止事项：

1. 不打断当前恢复链路。
2. 不把轻微打断当成教程完成。
3. 不在每次鼠标移动时触发，只响应已有有效打断判定。

### 8. 生气退出

对应场景：`interrupt_angry_exit`

动作设计：

1. 进入时抢占当前演出 session，优先级高于普通场景。
2. YUI 先前冲到 `homeCloseInvite`，制造“真的生气了”的瞬间。
3. 使用 `angry` motion；缺失时用短 `shake`。
4. 台词尾部“哼”附近转身或横移离场。
5. 最后 opacity 降低并 release，走原教程终止和模型恢复链路。

禁止事项：

1. 不新增持久生气状态。
2. 不保存任何演出状态到 localStorage。
3. 不破坏用户原模型恢复。

## 场景到动作映射

| Scene | 主构图 | 主情绪 | 主动作 | UI 焦点 |
| --- | --- | --- | --- | --- |
| wakeup prelude | `homeFullSafe` | soft | `wakeSoftSettle`, `noticeUser` | YUI |
| `intro_basic` | `homeWaistGuide` | happy | `pointThenReturn`, `excitedPop` | 输入框 / 麦克风 |
| `takeover_capture_cursor` | `homeWaistGuide` | happy | `curiousLean`, LookAt cursor | 猫爪面板 / 开关 |
| `takeover_plugin_preview` | `homeEdgeYield` | excited | `excitedPop`, `yieldStage`, `hop` | 插件入口 / dashboard |
| `takeover_settings_peek` | `homeWaistGuide` -> `homeEdgeYield` | proud -> surprised | `pointThenReturn`, `panicProtect` | 设置按钮 / 设置面板 |
| `takeover_return_control` | 动作二结束位 | happy | `finishSettle`, clear/release | 用户 |
| `interrupt_resist_light` | `homeCloseInvite` | surprised | short `shake`, dodge | 用户鼠标 |
| `interrupt_angry_exit` | `homeCloseInvite` -> exit | angry | `angryExit` | 退出链路 |

## 实现落点

### `static/yui-guide-avatar-stage.js`

负责首页动作适配：

1. 定义首页构图 profile。
2. 定义首页 sequence。
3. 包装 `AvatarPerformanceStage`，暴露首页语义方法。
4. 不写教程业务状态，不处理跨窗口协议。

### `static/yui-guide-director.js`

负责在正确场景和 timeline cue 调用动作：

1. 场景开始调用 enter sequence。
2. timeline action 调用对应动作 cue。
3. 场景结束清 LookAt 或 release。
4. 打断和 angry exit 使用更高优先级抢占。

### `static/yui-guide-wakeup.js`

负责保留现有醒来逻辑：

1. 不重写苏醒参数动画。
2. 只允许接入苏醒后的 settle / resume。
3. 继续保证恢复 captured params、motion hold 和 pose override。

### `static/avatar-performance-stage.js`

只补通用能力：

1. frame tween。
2. preset。
3. LookAt。
4. 临时参数。
5. motion fallback。

不要把首页 scene 名、台词、按钮选择器写进通用层。

## 验收标准

1. 开场仍是苏醒睁眼后再打招呼。
2. 每个自动点击前，YUI 都先看向或让位到目标 UI。
3. 语音入口、猫爪、插件、设置四段视觉节奏不同，不是同一个动作重复播放。
4. Dashboard 和设置面板演示期间，真实 UI 是焦点。
5. `showSecondLine` 有明确情绪反转，但不挡设置条目。
6. 轻微打断不会破坏当前场景；连续打断进入 angry exit 后能恢复用户原模型。
7. reduced motion 下流程仍可理解。
8. release/destroy 后容器 inline style、LookAt、临时参数和 tween 都被清理。

## 验证方式

1. 代码检查：确认首页动作只落在 `yui-guide-avatar-stage.js` / `yui-guide-director.js` / `yui-guide-wakeup.js` 适配层。
2. 静态测试：保证首页 runtime script 加载顺序仍是 wakeup -> avatar performance -> avatar adapter -> director。
3. Playwright 验证：录制或截图 `intro_basic`、`takeover_capture_cursor`、`takeover_settings_peek`、`takeover_return_control`。
4. 日志验证：关键 cue 记录 scene、action、sessionId、release reason。
5. 回归检查：完成、跳过、打断、生气退出后，用户原模型和正常首页按钮链路可用。

## 禁止清单

1. 不改首句打招呼顺序。
2. 不新增全局长期演出状态。
3. 不 monkey patch `live2dManager.playMotion()`。
4. 不把首页剧情写进 `AvatarPerformanceStage`。
5. 不遮挡真实 UI 来追求戏剧性。
6. 不为了动作效果破坏教程完成、跳过、恢复和跨窗口 handoff。
7. 不把 `.agent` 文档纳入提交。

## 外部参考

这些参考只用于提炼演出原则，不作为项目 API 真值：

1. Ren'Py Transforms / ATL：立绘位置、缩放、旋转、透明度和可复用 transform 的组织方式。
2. Live2D Cubism Expression Motion：表情作为相对当前状态的临时表达，并可用 fade 平滑切换。
3. Live2D Cubism Motion / MotionFade：motion 切换需要 fade/settle，避免动作之间硬切。
4. Live2D Cubism LookAt：视线跟随目标点，用眼睛和头部参数先引导用户注意力。
