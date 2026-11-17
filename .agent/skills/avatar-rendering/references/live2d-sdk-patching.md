# Live2D SDK：分层、补丁规则、已否决的方案

## 版本线和每层能不能改

`templates/index.html` 里的加载顺序是 `live2dcubismcore.min.js` → `live2d.min.js` → `pixi.min.js` → `index.min.js`，都在 `static/libs/`。

| 文件 | 是什么 | 能不能改 |
|---|---|---|
| `pixi.min.js` | PixiJS 7.4.3 | 能，但没有改的理由 |
| `index.min.js` | pixi-live2d-display 的口型同步分支 `v0.5.0-ls-6`，里面打进了 CubismWebFramework（4-r.7 血统）。有一处本地改动，见下 | 能。胶水层和 Framework 的许可都允许修改 |
| `live2dcubismcore.min.js` | Cubism Core，闭源 | **不能**。许可禁止修改、反编译 |
| `live2d.min.js` | Cubism 2.1 运行时 | 不动。`index.min.js` 要求它先加载，否则直接报错 |

关于 Core，下面几条是实测观察（Node 里直接跑这份文件得到）：

- 版本号是 5.1.0；实现是 wasm2js 产出的纯 JS，不是真的 wasm。上游不发布 wasm 版的 Web Core。
- 没有脏检测，也没有降频接口：参数完全不变时 `update` 耗时相同，每次 `update` 后所有 drawable 的「顶点已变」标志都置位。所以不能用这个标志跳过上传或蒙版重画。
- 就算 Core 支持跳过，本产品里也命中不了：呼吸、眨眼、物理、待机动作每帧都在改参数。

关于跟进上游（截至 2026 年 9 月核对，属于外部事实，动手前重查）：官方 Framework 新版硬性要求 WebGL2 加 Core 6.0，目前没有内置它的 pixi 分支；新版本的变更记录里没有一条自述为通用的渲染性能提升，同样保留着每个 mesh 每帧重传顶点 / UV / 索引的写法。**跟进上游的理由是兼容新版编辑器导出的模型，不是性能**，而且对我们是重新移植，不是合并。

## 改 SDK 的三条路，现行选哪条

1. **运行时补丁（现行默认）。** 在 `static/live2d/*.js` 里覆写实例或原型上的方法。不用构建，换 bundle 时逐个覆写点重验。只能做方法级的替换或包裹：bundle 压缩后方法名和属性名还在，但类名和局部变量被改写，模块私有的东西（着色器单例、曲线求值函数）拿不到。
2. **直接改压缩后的 bundle（只有一处）。** 动作曲线求值时原本每帧把「当前段到数组末尾」整段拷贝，改成只拷后面用得到的 4 个点（#3346）。这是在 `index.min.js` 里改了一个字符串。再这样改必须满足：改动是单点的字符串替换；有测试按原字符串和新字符串各出现几次来钉（`tests/unit/live2d_motion_curve.test.js`，同时钉了 `t.VERSION="v0.5.0-ls-6"`）；有对真实 bundle 的行为差分。原因：这个文件是 15 万字符的单行，diff 没法审；整体换新 bundle 时改动会被无声丢掉，只有测试能拦。配套的基准脚本是 `scripts/benchmark_live2d_motion_curve.cjs`。
3. **把源码放进仓库自己构建。** 没有采用。实测过可行：按上游对应 tag 重新构建，产物和上游发布的 bundle 逐字节相同，说明构建是确定的。只有要改渲染器内部（比如重写 `drawMesh`）时才值得走这条，那种体量不应该以「在 `static/live2d` 里维护一份压缩过的渲染器副本」的形式存在。把 Framework 源码放进公开仓库涉及单独的许可标注，要维护者先定。

不走的路：维护自己的 fork 再把产物拷回来（主仓库里只看得到二进制 diff，一个补丁要两个仓库两次评审）；给上游提改动（上游基本停更，官方新版又依赖 Core 6.0 和 WebGL2）。

## 写运行时补丁的规则

- **每个补丁有自己的幂等标记**，名字用 `__neko` 前缀，打在被改的那个对象上；原方法另存（如 `__nekoOriginalSaveProfile`）。装之前先查标记，避免把上一次装的包装当成原方法存起来。
- **改类或原型时，标记打在类上，不打在 manager 或 renderer 实例上。** 同一页面可能有多个 `Live2DManager`，实例级标记会让第二个 manager 再包一层。
- **先做形状检查再装。** Cubism 2 模型没有 `internalModel.renderer`，它的 `coreModel` 是另一套接口（`saveParam` / `loadParam` / `getParamIndex`），物理对象只有 `update`。用 `typeof` 判，判不过就静默跳过。
- ⚠️ **`getParameterIndex` 对不存在的参数名不返回 -1**，而是分配一个大于等于参数总数的「虚拟索引」。判断参数是否真实存在要用 `idx < getParameterCount()`（`installMouthOverride` 里的 `resolveSavedParamEntry` 就是这么判的）。给它加缓存时未命中必须调原函数并缓存其返回值，不能自作主张返回 -1。测试里的假 `coreModel` 如果用 `indexOf` 实现，语义和真 bundle 不一样。
- **`installMouthOverride`（`live2d-model.js`）的还原范围很窄。** 重装前它只把 `motionManager.update` 和 `coreModel.update` 还原成原函数。打在同一个 `coreModel` 实例上的其它覆写既不会被还原也不会被重包，所以别的补丁不要依赖它的还原，也不要把它装的包装当原函数备份。
- ⚠️ **临时替换 `coreModel.update` 的代码，还原时要先确认当前值还是自己放上去的那个。** `coreModel.update` 的包装出错时会自卸载，并由 `_scheduleReinstallOverride` 在 100ms 后重装（最多 3 次）。如果你的代码中途让出过事件循环，重装可能已经发生，无条件写回会把新装的包装覆盖掉。
- **失败是静默的。** `motionManager.update` 的包装会吞掉 SDK 抛的异常（SDK 在动作异步加载期间本来就会抛）。补丁把动作求值弄坏时，现象是动作队列不再推进、待机循环不再排下一段、模型定格，但口型和眨眼照常。
- **bundle 升级是最大的风险。** 私有字段或方法改名后，带形状检查的补丁会无声失效，不带的会把画面弄坏（比如颜色字段大小写变了，整个模型变黑或透明且不抛错）。依赖私有字段的补丁要有版本哨兵：测试里断言 `PIXI.live2d.VERSION` 和 bundle 里对应的源码片段。

## 现有补丁清单

| 位置 | 做了什么 | 约束 |
|---|---|---|
| `live2d-core.js` `_optimizeRendererProfile` | 把 Cubism 渲染器每帧的 `saveProfile` / `restoreProfile` 置空 | 只在 Linux X11 桌面运行时和 Electron Pet 窗口生效（#3593、#3599）；模型管理、预览页、浏览器开发页保持原样。见下 |
| `live2d-model.js` `patchLive2DRendererProfile` | 修正 bundle 里 profile 保存时把元素缓冲绑定写进了数组缓冲字段的错 | 给仍然走保存 / 恢复的页面用；先用源码正则确认 bundle 里确实是那个写法才装 |
| `live2d-model.js` `configureLive2DClipping` | 蒙版渲染纹理数量：最少 3 张，按蒙版上下文数量增长，最多 32 张；再多就改用高精度蒙版逐个画。同时替换 SDK 的布局分配和包围盒计算 | 必须在模型加入舞台、蒙版帧缓冲创建之前调；不能再调 SDK 的 `initialize()`（这个版本会往已有映射里追加重复项）。守卫：`tests/unit/test_live2d_clipping_static.py` |
| `live2d-model.js` `_applyTextureQuality` | 空函数 | 运行期替换或缩小图集贴图会让 UV、蒙版和贴图实际尺寸对不上，出现部件丢失。画质设置只改画布后备缓冲的分辨率（`_getRenderResolutionForQuality`） |
| `live2d-model.js` `installMouthOverride` | 包 `motionManager.update` 和 `coreModel.update`，做口型、眨眼、常驻表情、临时姿态覆盖 | 见上面的规则 |
| `static/libs/index.min.js` | 曲线求值的拷贝上限 | 见上 |

**profile 保存被置空的前提。** 这段保存里有几个 `gl.getParameter` 在浏览器里不走缓存，每次都要主线程同步等 GPU 进程；透明全屏窗口下尤其慢（#3593）。能跳过是因为：Pet 窗口的 PIXI 舞台上只有这一个 Live2D 模型，没有 PIXI 的 `mask` 和 `filters`，模型画完后 SDK 自己会重置 PIXI 的状态机。⚠️ 以后要往这个舞台上加带 `mask` / `filters` 的 PIXI 对象，必须重新评估——Cubism 绘制前关掉的 scissor / stencil 测试不会被 PIXI 的状态重置恢复，那些对象会静默失效。测试：`tests/frontend/live2d_renderer_profile_skip.test.cjs`、`tests/unit/live2d_renderer_profile_load.test.js`。

不要顺手去掉绘制入口里对 `FRAMEBUFFER_BINDING` 的那次查询：它是客户端缓存应答，几乎不花时间；去掉或换成缓存值会打坏头像截取（`static/avatar/avatar-portrait.js`）和卡面截图（`static/js/model_manager/card-face.js`）的离屏渲染。

## 已经核实过、不要再提的方案

每条都做过实测或源码核对，结论是不做。

- **蒙版在「静止帧」跳过重画。** 本应用没有蒙版源顶点不变的帧（呼吸每帧都在动），命中率是 0；按单个上下文跳过还会画错裁剪。
- **把蒙版渲染纹理从 3 张退回 1 张。** 那是画质回归（每个蒙版的分辨率降 2 到 4 倍），不是性能收益。最少 3 张是有意保留的下限。
- **按显示尺寸换小贴图。** 每帧收益为 0（有 mipmap 时采样的本来就是同一层）；只省显存；运行期换贴图不安全，不换又会在卡面导出、放大、跨屏时变糊。
- **开 PIXI 的 `CREATE_IMAGE_BITMAP`。** 实测有三条贴图永久空白的回归（新建 GL 上下文后、贴图被回收后切回、同地址热重载），并让 Cubism 2 模型贴图上下颠倒。
- **关 mipmap。** 桌宠是把 4096 的图集缩到几百像素显示，关了会闪烁和出摩尔纹。
- **为了性能升级 PixiJS 8 或 Core 6.0。** 对单个自绘容器的每帧成本结构没有实质差别，迁移要重写全部调用。
- **把 `pointermove` 处理合并到 rAF 来省 CPU。** 浏览器本来就把 `pointermove` 对齐到每帧一次，处理器每次只花几微秒。
- **蒙版绘制时不设乘色 / 屏幕色、TextureGC 改手动、调 `powerPreference` / `clearBeforeRender`。** 收益为 0 或有副作用（关 `clearBeforeRender` 会留残影）。

## 评估过、目前没有实现的候选

代码里没有这些。数字都来自单台高端独显桌面机，核显机器没有实测过；谁要做，先在目标机器上重量。

- **矩阵运算去掉每次的临时数组**（蒙版设置阶段每帧分配上百个小 `Float32Array`）。只有「在 `Float32Array` 暂存区上原地累加」的写法和原实现逐位一致；用双精度累加会有极小偏差，肉眼看不出，但会让逐位比较的守卫失效。只对蒙版多的模型有可测收益。
- **加载期物理预跑跳过 Core。** `_preTickPhysics` 在画布不可见期间跑约 125 步完整 `internalModel.update`，其中 Core 的计算占大头却没人读结果。可行的写法是循环期间把 `coreModel.update` 临时换成空函数。⚠️ 不要改成「只跑物理」的循环：那样物理看不到呼吸和常驻表情的输入，模型显示出来的瞬间会有一次钟摆式的摆动，等于把修好的加载变形问题退回去。另外，函数里「20 步约 0.3ms」的注释偏小约两个数量级（实测每批几十毫秒）。
- **`getParameterIndex` 加 Map 缓存。** 每帧省几十微秒；必须保留虚拟索引语义。
- **自己实现 `drawMesh`**（每个 drawable 常驻顶点缓冲、不再每次绘制后 `useProgram(null)`）。这是 JS 侧每帧最大的单项，但绝对值在 1ms 量级，收益主要落在 GPU 进程；要立项，先在核显机器上证明 GPU 进程一侧是瓶颈。体量上属于自己维护一份渲染器分支，走源码构建那条路。
- **自建不带 depth / stencil 的 WebGL 上下文交给 PIXI。** 只省一点显存；副作用是 PIXI 走「外部上下文」分支后会忽略 `preserveDrawingBuffer` 等选项（卡面制作非嵌入模式靠它截图），扩展表也要手动补。
