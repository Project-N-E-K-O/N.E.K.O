# AvatarPerformance 模块维护指南

本文只作为后续维护、复用和扩展 `AvatarPerformance` 的参考文件，不再作为分阶段实施计划。

如果本文和当前代码、测试、运行日志冲突，以可复现证据和当前代码为准。修改本模块前，应先阅读本文，再检查真实入口和调用链。

## 一句话定位

`AvatarPerformance` 是项目里的模型演出运行时。它负责在一个明确的演出 session 内临时接管指定 avatar 的位置、大小、旋转、透明度、动作、表情、参数、LookAt 和相关运行时状态；演出结束、失败或被中断后，必须释放锁并尽量恢复接管前状态。

它不是教程系统，不负责页面剧情、高亮框、文本、语音播放、按钮语义或业务流程推进。

## 当前代码落点

通用模块：

1. `static/avatar/avatar-performance-stage.js`
   - `AvatarPerformanceStage`
   - `AvatarPerformanceCoordinator`
   - `Live2DAvatarPerformanceDriver`
   - `window.AvatarPerformance`
   - 兼容导出 `window.AvatarPerformanceStage`

Live2D 正常链路保护：

1. `static/live2d-model.js`
   - 临时 pose override
   - idle motion / random LookAt / eye blink 等正常链路让位
   - `isAvatarPerformanceCapabilityLocked()`
2. `static/live2d-emotion.js`
   - `setEmotion()` / `playExpression()` / `playMotion()` 在被锁 capability 下让位
   - performance 写入通过 bypass 进入，不被自身锁挡住

首页 Yui 新手引导适配层：

1. `static/tutorial/avatar/yui-stage.js`
   - 首页专用 adapter
   - 把 director 的 step、speech、timeline、wakeup 事件翻译成 AvatarPerformance sequence
2. `static/tutorial/yui-guide/director.js`
   - 首页新手引导导演层
   - 仍负责高亮、文本、语音、步骤推进和业务动作
3. `templates/index.html`
   - 加载顺序为 `avatar-performance-stage.js`、`tutorial/avatar/yui-stage.js`、`tutorial/yui-guide/director.js`
4. `main_routers/pages_router.py`
   - 静态资源版本缓存包含上述新模块

兼容入口：

1. `static/tutorial/yui-guide/wakeup.js` 仍保留为兼容/预热桥接入口，由 `tutorial/yui-guide/director.js` 和新的演出适配层承接实际流程。
2. `window.YuiGuideWakeup` 只允许作为兼容桥接对象存在，不应恢复为独立视觉演出链路或新增剧情逻辑。

## 模块边界

`AvatarPerformance` 负责：

1. 演出 session 生命周期。
2. 按 avatarId 和 capability 建立接管锁。
3. 优先级与抢占。
4. 容器 frame 变换：`x / y / scale / rotate / opacity`。
5. motion、expression、emotion、param、poseTimeline、LookAt 等 sequence step。
6. Live2D driver 的 capture / restore。
7. reduced motion 降级。
8. driver 不可用时 no-op 或安全返回 false。
9. release / destroy 时清理 tween、临时参数、LookAt、pose override、容器 inline style。

`AvatarPerformance` 不负责：

1. 页面剧情和步骤推进。
2. 首页新手引导文案、按钮语义、高亮框和 ghost cursor 状态。
3. 教程语音播放、口型语音队列或旁白 cue 的业务编排。
4. 正常聊天、idle、拖拽、模型加载、模型切换的业务入口。
5. 持久化演出状态到 localStorage 或配置文件。
6. 为某个具体 Live2D 模型臆造 motion / expression / emotion 名称。

页面要接入演出，应新增或维护页面适配层，把页面已有语义翻译成通用 sequence，而不是把页面剧情塞进 `static/avatar/avatar-performance-stage.js`。

## 导出接口

当前主入口：

```js
window.AvatarPerformance = {
  createStage(options),
  createCoordinator(options),
  createLive2DDriver(options),
  createLive2DPerformance(options),
  createNoopDriver(),
  createNoopCoordinator(),
  getDefaultCoordinator(),
  isCapabilityLocked(avatarId, capability),
  getLockedCapabilities(avatarId),
  contracts
};
```

兼容入口：

```js
window.AvatarPerformanceStage = {
  create(options),
  createLive2DDriver(options),
  createLive2DStage(options),
  AvatarPerformanceStage,
  Live2DAvatarPerformanceDriver,
  AvatarPerformanceCoordinator
};
```

新增能力优先落在 `window.AvatarPerformance`；`window.AvatarPerformanceStage` 只作为兼容桥继续保留。

## Session 与锁

一个演出 session 由 `AvatarPerformanceStage.acquire()` 建立，由 `release()` 或 `destroy()` 清理。

跨模块仲裁由 `AvatarPerformanceCoordinator` 完成。锁的粒度是：

```text
avatarId + capability
```

当前 capability：

1. `frame`
2. `motion`
3. `expression`
4. `params`
5. `lookAt`

典型 acquire 请求：

```js
const session = coordinator.acquire({
  owner: 'home-yui-guide',
  avatarId: 'main-live2d',
  characterId: 'yui',
  priority: 80,
  force: true,
  capabilities: ['params', 'lookAt']
});
```

维护规则：

1. 只锁当前演出需要写的 capability。
2. 未锁 capability 应继续由原系统工作。
3. 低优先级不能抢占高优先级。
4. `force: true` 可抢占同 avatar 上冲突 capability 的旧 session。
5. release 必须释放 coordinator 锁和 stage session。
6. 页面中断、跳过教程、关闭页面、模型切换前必须 release 或 destroy。

首页 Yui 新手引导当前使用：

1. 普通 step / speech / timeline：默认锁 `params`、`lookAt`。
2. 苏醒动作：锁 `params`、`motion`、`lookAt`、`expression`。
3. 情绪表达：锁 `motion`、`expression`，并只传递 director 已有 emotion。

## Sequence DSL

当前通用 Stage 支持的 step 类型以 `window.AvatarPerformance.contracts.sequenceStepTypes` 为准，主要包括：

1. `frame`
2. `motion` / `playMotion`
3. `motionWithFallback` / `optionalMotion`
4. `expression` / `applyExpression`
5. `emotion` / `setEmotion` / `applyEmotion`
6. `param` / `setParam` / `setTemporaryParam`
7. `poseTimeline` / `runPoseTimeline`
8. `lookAt`
9. `clearLookAt`
10. `clearExpression`
11. `clearParams`
12. `wait` / `delay`
13. `sequence`
14. `speechCue`

维护规则：

1. sequence 不直接访问业务 DOM。
2. DOM element、rect、point 等目标由页面 adapter 通过 `targets` 传入。
3. `required: true` 的 step 失败时可终止 sequence。
4. 非 required 资源缺失应安全返回 false 或跳过，不能卡住业务流程。
5. `reducedMotion` 下，大幅 frame / preset / pose 应落为短时或直接完成。
6. nested sequence 共享当前 session。

## Live2D Driver 能力

`Live2DAvatarPerformanceDriver` 当前负责把通用 sequence 翻译到 `window.live2dManager` 和当前 Live2D model。

已实现能力：

1. 解析当前 manager、model、coreModel、container。
2. capture / restore 容器 inline style。
3. acquire 时临时关闭容器 transition，release 后恢复。
4. frame 写入 `transform` / `opacity`。
5. motion 解析：
   - profile 显式映射
   - group / index
   - motion file 到 runtime group/index
   - manager `playMotion()`
6. expression 解析：
   - profile 显式映射
   - manager `emotionMapping.expressions`
   - manager `fileReferences.Expressions`
   - expression file 参数 fallback
7. emotion 执行：
   - 默认走 manager `setEmotion()`
   - 可通过 `parts` 拆分 expression / motion
8. param 写入：
   - profile param key 可映射到多个候选参数
   - 写入前 capture，release / clear 时 restore
9. poseTimeline：
   - 等待 Live2D context
   - 临时暂停 eye blink
   - 使用 manager temporary pose override
   - 按 progress 写 coreModel 参数
   - 完成或失败后 clear override 并恢复参数
10. LookAt：
   - 支持 DOM element、rect、point、pointer event
   - 优先使用 temporary pose override
   - clear / release 恢复快照

维护规则：

1. 不要在通用 driver 里写首页 step 名、文案或剧情。
2. 不要假设所有模型都有 `happy`、`smile`、`Idle`、`TapBody`、`Guide` 等资源。
3. 有资源就执行；缺资源时走 fallback 或安全返回 false。
4. expression / motion 的真实资源解析应使用当前模型的 profile、`emotionMapping`、`fileReferences`、motionManager runtime definitions。
5. release 不应硬编码回某个固定 Idle 表情或 motion，应恢复 capture 到的状态或恢复正常调度。

## Live2D 正常链路保护

正常链路只在对应 capability 被锁时让位。

当前接入点：

1. `Live2DManager.prototype.isAvatarPerformanceCapabilityLocked(capability)`
2. idle motion loop 在 `motion` 锁定时不启动。
3. motionManager update 在 `motion` 锁定时不写普通 motion。
4. random LookAt 在 `lookAt` 锁定时不写。
5. `playExpression()` 在 `expression` 锁定时返回 false。
6. `playMotion()` 在 `motion` 锁定时返回 false。
7. `setEmotion()` 在 `expression` 或 `motion` 锁定时返回 false。
8. performance driver 写入时使用 `_avatarPerformanceBypassLocks`，避免被自己的锁挡住。

维护规则：

1. 不要让锁永久生效；所有入口都必须有 release / destroy 路径。
2. 不要把普通聊天、点击、idle 的行为搬进 performance 模块。
3. 不要用锁替代业务状态判断。
4. 新增正常链路写模型状态时，应先判断对应 capability 是否被锁。

## 首页 Yui 新手引导接入

首页新手引导是当前第一条真实调用链，但不是通用模块边界。

实际调用关系：

```text
YuiGuideDirector
  -> createAvatarStage()
  -> YuiGuideAvatarStage.create()
  -> AvatarPerformanceStage + Live2D driver + default coordinator
```

当前 Day1 苏醒链路：

```text
playAvatarFloatingRound(1)
  -> day1_intro_activation
  -> runWakeupPrelude()
  -> callAvatarStage('runWakeup')
  -> YuiGuideAvatarStage.runWakeup()
  -> AvatarPerformance poseTimeline
```

后续每日 round scene 链路分为准备、播放、执行和收口四段；`operation` 不是固定在台词前执行，普通 scene 会先启动旁白，再在 cursor move/click 阶段触发 operation：

```text
playAvatarFloatingScene(scene)
  -> SceneOrchestrator.playScene()
  -> 准备阶段:
       clear previous scene state
       prepareAvatarFloatingScene()
       resolve/apply spotlight
  -> 播放阶段:
       speakGuideLine()
       -> callAvatarStage('onSpeechStart')
       -> 原有语音播放
       -> callAvatarStage('onSpeechEnd')
  -> 执行阶段:
       move/click Ghost Cursor
       runAvatarFloatingSceneOperation()
       callAvatarTimelineAction()
         -> callAvatarStage('onTimelineAction')
  -> 收口阶段:
       wait narration / action / petal transition
       callAvatarStage('exitStep')
```

情绪链路：

```text
performance.emotion
  -> applyGuideEmotion()
  -> YuiGuideAvatarStage.applyEmotion()
  -> AvatarPerformance emotion step
  -> Live2DManager.setEmotion()
```

维护规则：

1. 首页高亮、文本、语音、面板开关和 cursor 仍由 director / overlay 负责。
2. `YuiGuideAvatarStage` 只消费 director 给出的 step context、targets、emotion、timeline action。
3. 适配器不得删除或改写首页 overlay、`yui-taking-over`、ghost cursor 等状态。
4. 适配器不得自己猜 emotion 分类。
5. 除苏醒 pose 外，step 动作应优先使用已有程序判断出的 emotion 和 director 的目标元素。
6. 苏醒是首页新手引导 prelude 的一部分，不是独立外置功能。
7. `playAngryExit()` 是打断退出的最高优先级动作入口；启动前必须停止仍在播放的首页教程专属 session，并跳过旧 session 的长释放动画，确保 angry exit 独占 frame/params 写入。
8. 如果 angry exit 的 Live2D 演出不可用，director 仍必须兜底应用 `angry` emotion；动作失败不能让生气退出退回普通表情或阻塞 skip 清理。

## Yui 苏醒 pose

当前 Yui 苏醒动作落在 `YuiGuideAvatarStage.runWakeup()`，由 `AvatarPerformance` 的 `poseTimeline` 执行。

当前参数映射在 `static/tutorial/avatar/yui-stage.js` 的 `YUI_WAKEUP_PARAMS` 中，包括：

1. eye open
2. head angle
3. eye ball
4. eye smile
5. body angle
6. Yui 右手挥手相关参数

维护规则：

1. 这些参数是 Yui 模型专用参数，只能留在 Yui adapter，不能移入通用 Stage。
2. 其他 Live2D 模型不能默认复用这套参数。
3. 若后续支持不同角色苏醒，应通过角色 profile 或 adapter 分支提供参数映射。
4. reduced motion 下苏醒应短时完成，不跑大幅动作。
5. storage location overlay 可见时，苏醒应安全跳过并 reveal 已准备模型，不能阻塞存储选择流程。

## 表情、动作与情绪

项目已有情绪判断和资源映射链路，`AvatarPerformance` 不新增情绪分类。

规则：

1. 已有业务链路给出的 emotion 是输入，不是由 adapter 再判断。
2. adapter 不硬编码 `neutral_yyy`、`neutral_z1`、`neutral_sbx` 等具体模型资源名。
3. 通用 driver 可以接收 `emotion`，但真实执行由当前 Live2D manager 的 `setEmotion()`、`playExpression()`、`playMotion()` 和资源表决定。
4. 如果某个模型缺少对应 expression 或 motion，非 required step 应跳过或 fallback。
5. 不允许为了某个首页 step 在通用模块里新增臆测 emotion。
6. 七日新手教程可以在 `yui-origin` 模型 profile 或页面 adapter 中声明该模型的情绪 motion 池数量与随机策略，但这属于模型专属配置；不得把 `yui-origin` 的 `happy/sad/angry/neutral/surprised/Idle` 资源假设推广到其他 Live2D 模型或通用 driver。

## 不同 Live2D 模型资源差异

不同 Live2D 模型的 motion group、motion 文件名、expression 名称、参数 ID、默认 idle、表情持久化方式都可能不同。

可复用写法：

```js
profile = {
  motions: {
    guidePoint: [
      { group: 'Guide', index: 0 },
      { group: 'TapBody', index: 0 }
    ]
  },
  expressions: {
    smile: [
      { name: 'happy' },
      { file: 'expressions/happy.exp3.json' },
      { params: { ParamSmile: 1 } }
    ]
  },
  params: {
    blush: ['ParamCheek', 'ParamBlush']
  }
};
```

维护规则：

1. profile 描述“当前模型如何实现某个语义动作”，不描述页面剧情。
2. 同一个语义可以有多个候选，driver 按可用资源选择。
3. 找不到资源时，非 required step 不报错卡死。
4. 参数 fallback 必须被 capture / restore。
5. 模型专用参数只放在模型 profile 或页面 adapter，不放进通用 Stage。

## reduced motion

当 `prefers-reduced-motion: reduce` 生效时：

1. 大位移 frame 直接落位或缩短 duration。
2. 装饰性 preset 返回 false 或跳过。
3. poseTimeline 使用 `reducedMotionDurationMs` / `reducedHandoffMs`。
4. 苏醒等动作应保留流程完成语义，但减少幅度和时长。
5. release / restore 行为不变。

## 失败与恢复

任何演出失败都不应卡住页面业务流程。

规则：

1. driver 不可用时返回 false。
2. 单个非 required step 失败只影响该 step。
3. required step 失败可终止当前 sequence，但仍必须走 release。
4. release 尽量恢复所有已 capture 的状态，即使部分恢复失败也继续处理其他部分。
5. destroy 可重复调用。
6. 页面 adapter 的异步调用必须 catch，不能让教程主流程因模型演出失败中断。

## 3D 后续接入指导

VRM / MMD / 其他 3D avatar 后续应作为新的 driver 接入同一套 `AvatarPerformance` 契约，而不是复制首页 Yui adapter。

建议 driver 命名：

1. `VRMAvatarPerformanceDriver`
2. `MMDAvatarPerformanceDriver`

3D driver 应映射这些能力：

1. root object transform，对应 `frame` capability。
2. animation clip 播放，对应 `motion` capability。
3. expression / morph target，对应 `expression` capability。
4. bone / morph / runtime 参数，对应 `params` capability。
5. head aim / look target，对应 `lookAt` capability。
6. animation mixer 状态 capture / restore。
7. physics 暂停与恢复。
8. camera framing，如后续 sequence 需要可作为 driver/profile 扩展，不应污染通用 Stage。

3D 接入规则：

1. 先实现同一 driver 方法集合，缺能力安全返回 false。
2. 只在对应 capability 被锁时让正常 3D 链路让位。
3. 不要让 Live2D 专用参数、expression 文件、motion group 语义进入 3D driver。
4. 页面 adapter 应只关心通用 sequence，不关心模型类型。
5. 3D 的资源差异也应通过 profile 或当前 manager 资源表解析。

## 验证清单

修改本模块或首页适配层后，至少检查：

1. `static/avatar/avatar-performance-stage.js` 不包含首页 step、文案、overlay 或 Yui 剧情。
2. `static/tutorial/avatar/yui-stage.js` 不删除首页 overlay，不改 `yui-taking-over` 或 ghost cursor 状态。
3. 旧 `static/tutorial/yui-guide/wakeup.js` 只保留兼容桥接职责，模板加载它时不能绕过 director / adapter。
4. `templates/index.html` 脚本顺序正确。
5. `main_routers/pages_router.py` 的静态资源版本路径包含新模块。
6. 首页新手引导从苏醒到 intro，再到 takeover steps 能推进。
7. `applyGuideEmotion()` 继续使用已有 `performance.emotion`。
8. 演出结束后 normal idle / chat emotion / drag / model switch 不被永久污染。
9. reduced motion 下 sequence 能完成。
10. driver 不可用时教程不崩。

推荐命令（Windows / PowerShell 示例）：

```powershell
.venv\Scripts\python.exe -m pytest tests/test_agent_rewrite_regression.py tests/test_emotion_heuristic.py tests/frontend/test_yui_guide_avatar_performance_flow.py -q
node --check static/avatar/avatar-performance-stage.js
node --check static/tutorial/avatar/yui-stage.js
node --check static/tutorial/yui-guide/director.js
python -m py_compile main_routers/pages_router.py config/prompts/prompts_emotion.py
git diff --check
```

macOS / Linux 可使用等价命令：

```bash
./.venv/bin/python -m pytest tests/test_agent_rewrite_regression.py tests/test_emotion_heuristic.py tests/frontend/test_yui_guide_avatar_performance_flow.py -q
node --check static/avatar/avatar-performance-stage.js
node --check static/tutorial/avatar/yui-stage.js
node --check static/tutorial/yui-guide/director.js
python3 -m py_compile main_routers/pages_router.py config/prompts/prompts_emotion.py
git diff --check
```

## 维护禁区

不要做这些事：

1. 把首页剧情、文案、按钮选择器写进 `AvatarPerformanceStage`。
2. 在 adapter 里自己发明 emotion 分类或 motion/expression 名。
3. 为了首页新手引导删除或重置 overlay、taking-over、ghost cursor 等业务状态。
4. 把 `window.YuiGuideWakeup` 或旧 `tutorial/yui-guide/wakeup.js` 扩回独立视觉演出链路。
5. 在 release 时硬编码回某个固定 Idle / neutral 资源。
6. 让某个演出 session 没有 release / destroy 路径。
7. 为 3D 接入复制 Live2D 参数逻辑。

## 当前已知扩展点

这些不是实施阶段，只是后续维护时可参考的扩展方向：

1. 为 VRM / MMD 增加真实 driver。
2. 为不同角色建立独立 performance profile。
3. 为更多页面新增 adapter，但保持通用模块无业务语义。
4. 增加更多真实模型 fixture，覆盖不同 Live2D 资源表差异。
5. 增加调试视图，展示 active session、locked capabilities、当前 driver kind 和 pending tween 数。
