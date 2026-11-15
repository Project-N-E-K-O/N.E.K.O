# VRM / MMD：加载竞态、销毁、语义动作

## 加载并发：只用 token，不排队

`VRMManager.loadModel`（`static/vrm/vrm-manager.js`）和 `MMDManager.loadModel` 的并发模型相同，Live2D 用的也是 load token：

- 入口同步递增 `_activeLoadToken`，把自己的 token 传给 `core.loadModel(modelUrl, options, loadToken)`。
- 同一时刻可能有几个加载在并发跑（快速连切），只有持有当前 token 的那个能穿过 core 里的守卫去改共享状态。被取代的那个在守卫处退出，并用 `_disposeAbandonedVRM` 释放自己已经加载好的资源。
- 代价是快速连切时，被取代的加载各自下载完才退出，浪费带宽。这是有意的取舍。

**为什么不排串行队列。** 试过「入口 token 加 promise 队列」。问题是：队列里每个前驱在后继入队那一刻其实已经被取代了，让新加载等它，等于空等一个注定作废的旧加载；`GLTFLoader.load` 没有超时也没有中止，一个慢或卡死的加载会从「只拖累自己」变成「堵住之后所有切换」。只要 core 里有 token 守卫保证并发安全，队列就只剩坏处。不要再加回来。

**没有队列之后多出来的一条约束。** 被取代的实例在 `core.loadModel` 返回 `null` 后必须直接早退，**不能写 `_loadState`**，否则会盖掉正在并发跑的胜出者的状态。

## ⚠️ 守卫放在哪

`vrm-core.js` 的 `loadModel` 现在有两道守卫，缺一不可：

1. GLTF 网络加载和依赖导入之后、移除旧模型之前。少了它，迟到的旧加载会把新会话已经装好的模型移除并销毁。
2. 最后一个 `await`（等 3 帧）之后、任何共享写入之前。第一道守卫之后还有方向修正、偏好读取、等帧这几个 `await`，而共享相机的位姿和 `enableFaceCamera` 是在 `scene.add` **之前**写的。少了这道，被取代的旧加载会用它自己模型存的相机位置盖掉共享相机，把已经显示出来的新模型顶到屏幕外。「只在 `scene.add` 前守一道」是不够的。

规则：**每个 `await` 之后、每处写共享 manager 状态之前都要有守卫；守卫到共享写入之间不能再有 `await`**（会重新开出没守卫的窗口）。共享状态指 `this.manager` 上的 scene、currentModel、camera、interaction、animationMixer、controls。写自己这次加载出来的 vrm 对象（还没挂到 manager 上）不算。

审计方法：把函数里所有 `await` 和所有 `this.manager.X = ...` / 对共享对象的修改列出来，逐个看每处写入前面最近的一个 `await` 之后有没有守卫。

MMD 侧另有一条：token 必须在 `mmd-core.js` 的 `loadModel` 第一个 `await` 之前取好，并且由 manager 传进去。否则一次已经被取代的调用（比如失败后的回退路径）能通过过期检查，叠出一个幽灵模型，或者清掉新调用刚装好的模型。

静态契约测试：`tests/unit/test_avatar_model_load_race_static_contracts.py`。

## 销毁

- `VRMManager.dispose()` 里 `await this.core.disposeVRM()` 之后有一句 `if (!this._isDisposed) return`。意思是：销毁等待期间如果发生了重新初始化（`initThreeJS` 把 `_isDisposed` 置回 false），就中止后面的清理，免得清掉新会话的资源。这是协作交接，不是漏清理。
- `VRMManager.cleanupUI()` 委托给 mixin 的 `cleanupFloatingButtons()`，和 `MMDManager.cleanupUI()` 对偶。它曾经在一次合并里被整个删掉，而三处调用点都带 `typeof` 守卫，于是静默跳过了很久，`document` 级的拖拽监听没人清。教训：带 `typeof x === 'function'` 守卫的调用，被调方法消失时不会报错；删方法前搜一下调用点。Live2D 没有 `cleanupUI`，它的 UI 清理在调用方手动做。

## 无头 / 后台验证的限制

VRM 加载链路上有好几处靠帧驱动的等待：core 里的「等 3 帧」、`_waitForSceneStability`、显示时的淡入。页面不可见时 rAF 不派发、定时器被强节流，这些等待会卡住或极慢，`ready` 之后的视觉收尾在这种环境里验不了。垫一个 rAF 转 `setTimeout` 的替身只能部分缓解。遇到「后台标签页里 VRM 加载不完」先排除这个原因，别当成回归去修。

## VRM 语义动作（`static/vrm/motion/`）

助手说的话在前端本地被分析成动作（选哪个动作不再调模型）：

| 文件 | 职责 |
|---|---|
| `core.js` | `window.NekoMotionCore`：按 `semantics.json` 的规则分析文本。入口是 `analyze(text, options)` 和 `analyzeSpeech(text, options)` |
| `semantics.json` / `manifest.json` | 八种语言的词表和规则；动作资源清单（`registerActionCards(manifest.assets)` 注册，重复注册是幂等的） |
| `runtime.js` | 回合状态机：开始回合、扫描文本、等官方情绪、收尾 |
| `bridge.js` | 把回合事件转发给其它窗口，并暂存「这一轮对应的用户原话」 |
| `player.js` | 动作播放的所有权和生命周期 |

**核实一条关于分析结果的说法，先跑再看。** 不用起浏览器：在 Node 里用 `vm.runInThisContext` 加载 `core.js`，读进两个 JSON，`new NekoMotionCore(semantics).registerActionCards(manifest.assets)`，直接调 `analyze()`。`tests/frontend/vrm_motion_semantics.test.cjs` 的开头就是现成的写法。评审意见里说的「某句话会误触发某动作」经常在当前代码上已经不复现，照单全改是白干。

**评审意见怎么判。**

- 收：结构性缺陷。已经修过的例子——跨顺承边界时，后一段的否定词和前一段的动作词被拼成另一个动作（`_frameAcrossClauses` 按顺承边界切段）；过去时语气没有传到同一句里并列 / 顺承的后续动作（`actionHistorical`）；绕过回合隔离的状态读取点。
- 不收（作为阻塞项）：扩充词表、新的时间标记词、假设语句、某种语言的称谓词这类。它们属于产品建议，问题是开放集合，补不完。可以记下来，不挡合并。
- 把过去语气传到同一句的并列 / 顺承子句时，用的标记表 `HISTORICAL_CLAUSE_MARKERS` **刻意不含单独的 was / were / had**（动作词所在子句内部的 was / were / had 仍然算），否则「That was fun, so I clap」里前一句的系动词会误挡后一句的动作。不要「补全」这张表。

**几条不显然的约束。**

- `window._turnIsStructured` 是「本页这一轮是结构化富文本」的标记。桥接过来的回合不能用本页的这个标记来判断（`runtime.js` 里用 `isBridgedTurn` 隔开）。`tests/frontend/vrm_motion_policy.test.cjs` 数着 `runtime.js` 里读这个标记的位置个数，新增读取点要想清楚再改断言。
- 每个 `await waitForOfficialEmotion(turn)` 后面都要跟一次 `settleMissingEmotion(turn, emotionReceived)`，测试按调用点数量相等来钉。
- 用户打断时（`speech-cancel` 事件，来源是 `user_activity` 或 `user_activity_delayed`），`bridge.js` 只丢掉**已经被回合开始取用过**的用户原话（`dropConsumedContexts`）。不能整表清：这个事件不带请求号，而打断通常就是新消息触发的，整表清会把刚发出、还没等到回复的那条一起抹掉。也不能不清：被打断那一轮的原话会留到超时，被下一条不带请求号的主动回复捡走，一句「好的」就能把上一轮被打断的指令重放出来。
- 这些测试是「读源码做正则断言」的类型，改函数名会连带变红。跑法：`node --test tests/frontend/vrm_motion_*.test.cjs`（只用 Node 内建模块）；CI 入口是 `tests/unit/test_vrm_motion_architecture.py`。
