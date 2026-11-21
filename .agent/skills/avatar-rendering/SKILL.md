---
name: avatar-rendering
description: Live2D / VRM / MMD 形象渲染这一层的不变式和踩过的坑：空闲降帧与 Pet 窗口帧率总闸、Live2D SDK 能改到哪一层、VRM 加载竞态、形象周边的弹窗和点击穿透、内置模型与动作文件处理。改 static/live2d、static/vrm、static/mmd、static/avatar、static/frame-pacing.js、static/subtitle/subtitle-window.js，或者要讨论「渲染性能还能怎么优化」之前读。
---

# 形象渲染（Live2D / VRM / MMD）

已有文档不在这里重复：

- 模块划分和接口：`docs/frontend/live2d.md`、`docs/frontend/vrm.md`
- 待机动作的保存与恢复：`docs/live2d_motion_plan.md`
- 表演编排层（session、能力锁、driver）：`docs/design/avatar-performance-module-maintenance.md`
- VRM 弹簧骨、MToon 描边、3D 拖拽缩放、MMD 物理：`.agent/skills/vrm-physics`、`vrm-mtoon-outline`、`3d-interaction`、`ammo-mmd-physics-hacks`
- 收起后的猫形态状态机：`docs/design/cat-idle-state-machine-rules.md`、`cat-idle-states-feature.md`

## 先分清代码跑在哪个页面

- 陪伴用的形象只在 `templates/index.html` 里渲染，`/chat`、`/subtitle` 等独立窗口不渲染第二个。开发时 `index.html` 是浏览器单窗口；分发版里它是 Electron 的 Pet 窗口（没有单独的 `/pet` 路由），一个盖住桌面的透明窗口，不受后台节流影响。
- `index.html` 头部的同步脚本设置 `window.__LANLAN_IS_ELECTRON_PET__`。判据里包含 UA 里有 `Electron`，所以**任何 Electron 内嵌的浏览器打开开发页都会被当成 Pet 窗口**：`isMobileWidth()` 恒为 false、帧率总闸生效、Cubism 的 profile 保存被跳过。要测手机布局分支，得在页面加载后把这个标记改回 false。
- ⚠️ `window.__NEKO_MULTI_WINDOW__` 只表示「在 Electron 里」，Pet 窗口也带。要判断「是不是聊天窗口」必须再按路径 `/^\/chat(?:_full)?(?:\/|$)/` 区分（#3125）。
- 模型管理、角色卡、卡面制作、小游戏等预览页设了 `window.__NEKO_DISABLE_AVATAR_IDLE_THROTTLE__ = true`：满帧，不启动空闲治理。
- 一个页面里可能有多个 `Live2DManager`（`live2d-init.js`、`card_maker.js`、`character_card_manager/model-previews.js` 各建一个）。只有 Pet 窗口保证只有一个，所以只有那里才接管全局的 `PIXI.Ticker.shared/system`。

## 帧率：五条不变式

细节和排查入口见 `references/frame-pacing.md`。

1. **降帧要停掉 rAF 请求本身。** `ticker.maxFPS` 只是跳过回调体，挂着的 rAF 请求仍让浏览器按显示器刷新率跑完整主帧，CPU/GPU 降不下来。所以空闲态是停掉 ticker、用 `setInterval` 手动 `update()`（`_enterIdleTickMode`）。定时器驱动期间相关 ticker 的 `maxFPS` 必须是 0，否则定时器抖动会被它丢帧、实际帧率减半。
2. **三个后端结构对偶。** Live2D（`live2d-core.js`）、VRM（`vrm-manager.js`）、MMD（`mmd-core.js`）各有同名的 `_enterIdleTickMode(fps)` / `_exitIdleTickMode` / `_idleTickMode` / `_idleTickFps`，`static/frame-pacing.js` 靠这两个字段名读状态。改一个就要改三个。
3. **`index.html` 上任何每帧循环都通过 `window.nekoFramePacing.requestPacedFrame` 排帧**（没有这个对象时退回 rAF）。渲染后端切到定时器驱动后，别处只要还有一条自己的 rAF 链，就会单独把主帧拉回刷新率，前面省下的全部作废。
4. ⚠️ **只有指针坐标真的变了才算「有活动」。** 桌面壳会按固定间隔给页面派发坐标不变的合成 `pointermove`。页面如果见到 `pointermove` 就升帧，就永远进不了空闲态——「帧率调低了没效果」的根因就是这个。新增由指针事件触发的升帧，先比较坐标。
5. 待机动作不算活动：`hasActiveMotionPlayback()` 只认优先级高于 idle 的动作。光标停在模型上不动也不算。

## Live2D SDK：能改到哪一层

细节见 `references/live2d-sdk-patching.md`。

- Cubism Core（`static/libs/live2dcubismcore.min.js`）闭源，许可禁止修改和反编译，而且没有脏检测：参数完全不变时 `update` 耗时也一样。「参数没变就跳过」「给 Core 降精度」这类方案在这一层做不了。
- 现行做法是**运行时补丁**：在 `static/live2d/*.js` 里对实例或原型做方法级覆写。`static/libs/index.min.js`（pixi-live2d-display `v0.5.0-ls-6`）只有一处本地改动，由 `tests/unit/live2d_motion_curve.test.js` 按字符串钉住。
- ⚠️ 补丁坏了多半不报错：我们包的 `motionManager.update` 会吞掉 SDK 抛的异常，表现是模型定格、口型和眨眼照常。验证补丁要对着真实 bundle 做逐帧差分，不能「跑起来看着没事」。
- ⚠️ 性能上的结论：Pet 窗口的结构性成本是「整个桌面那么大的透明窗口加全窗 WebGL 画布」，后备缓冲随桌面总面积增长。Core 和 SDK 的 JS 侧各项优化加起来只占帧预算的几个百分点。再有人提「换 Core」「改 SDK 渲染器」「降精度」，先看 `references/live2d-perf-findings.md`，不要重复审计。

## VRM / MMD 加载

细节见 `references/vrm-loading-and-motion.md`。

- `loadModel` 是「后到者胜」：入口同步递增 `_activeLoadToken`，**不排串行队列**。队列会让新加载空等一个已经被自己取代的旧加载，而 `GLTFLoader.load` 没有超时和中止，旧加载卡住就把后面所有切换都堵死。
- ⚠️ 守卫放置规则：`vrm-core.js` 的 `loadModel` 里，每个 `await` 之后、每处写共享的 manager 状态（scene、currentModel、camera、interaction、animationMixer、controls）之前，都要比一次 token；守卫和共享写入之间不能再插 `await`。
- `VRMManager.dispose()` 在 `await this.core.disposeVRM()` 之后的 `if (!this._isDisposed) return` 是和重新初始化的交接，不是漏清理。评审意见要求「补齐后面的清理」时应驳回。

## 形象周边的 UI

细节见 `references/avatar-ui-contracts.md`。

- `index.html` 整页 `html, body { pointer-events: none }`，所有可交互元素靠自己声明 `auto`。「看得见但点不动」先量 `getComputedStyle(el).pointerEvents`，不是先查 `z-index`。
- 四种形象的弹窗共用 `AvatarPopupMixin`（`static/avatar/avatar-ui-popup.js`），不要在某一种形象里另写一套。
- 字幕窗口的点击穿透命中只能靠轮询全局光标，改不成事件驱动。

## 模型资源

细节见 `references/model-assets.md`。

- ⚠️ 给动作文件「瘦身」只允许两件事：`scripts/smooth_yui_mouth.py`（嘴部两条曲线平滑）和 `scripts/slim_live2d_motions.py`（只改序列化）。**不删任何动作曲线**，物理会覆盖的输出参数也不删。
- 删除或搬动 `static/` 下已经发布过的内置素材时，在 `utils/static_asset_aliases.py` 的 `LEGACY_STATIC_ASSET_ALIASES` 登记旧路径，不要留副本：用户保存的配置里可能存着旧的绝对路径。

## 验证时的坑

- 页面不可见（后台标签页、不出帧的内嵌预览）时 rAF 完全不派发，`setTimeout` 也被强节流。VRM 的「等 3 帧」、场景稳定等待、淡入，以及弹窗动画都会卡住或极慢，这不是回归。可以用 `window.requestAnimationFrame = cb => setTimeout(cb, 16)` 垫一下驱动状态机，但视觉收尾仍然验不了。
- 高端独显机器上，全窗画布和裁剪画布的差别量不出来。GPU 侧结论要在核显机器上验。
- `tests/frontend/` 下的文件 CI 不直接收集。要进 CI，要么在 `tests/unit/` 里包一层 pytest（照 `tests/unit/test_pet_frame_pacing_gate.py`），要么写进 `.github/workflows/unit-test-shards.yml` 的 node 清单。`tests/unit/*.test.js` 由 CI 用 `node --test` 按通配全跑。`tests/frontend/test_subtitle_incremental.py`（真实浏览器）CI 不跑，改字幕窗口要本地跑。

## references 索引

- `references/frame-pacing.md` — 改帧率、ticker、空闲判定，或排查「降帧没效果 / 切角色后模型冻住」时读。
- `references/live2d-sdk-patching.md` — 要给 Live2D SDK 打补丁、升级 bundle，或评审这类改动时读：分层、补丁规则、现有补丁清单、已否决的方案。
- `references/live2d-perf-findings.md` — 讨论渲染性能方向、量性能之前读：成本结构、Core 层为什么不是瓶颈、测量方法上的坑。
- `references/vrm-loading-and-motion.md` — 改 VRM/MMD 加载与销毁、VRM 语义动作（`static/vrm/motion/`）时读。
- `references/avatar-ui-contracts.md` — 改弹窗、锁图标、点击穿透、字幕窗口穿透、姿态时间线、猫形态门控时读。
- `references/model-assets.md` — 加内置模型、处理动作文件、碰角色卡导出里的模型判定、离线预录 Live2D 时读。
