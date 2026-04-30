# 首页新手引导体验优化路线

## 背景

当前首页新手引导已经从传统 driver.js 提示，演进为 Yui Guide 演出层：

- 首页通过 `static/yui-guide-steps.js` 维护场景契约。
- `static/yui-guide-director.js` 负责语音、表情、Ghost Cursor、真实业务动作和退出清理。
- `static/yui-guide-overlay.js` 与 `static/css/yui-guide.css` 负责 spotlight、气泡、预演层和虚拟光标。
- 首页不再接入创意工坊，新手引导只保留聊天入口、主动搭话、猫爪、插件面板、设置一瞥、API 密钥、记忆浏览、插件面板接力等当前目标。

本轮已直接落地低风险优化：气泡进度提示、视口自适应定位、明暗主题视觉统一、spotlight 过渡、Ghost Cursor 观感、跳过按钮可读性，以及少量步骤节奏参数。下面内容用于约束后续更复杂的逻辑和功能改造，避免在当前实现上继续堆硬编码。

## 成熟引导的可借鉴原则

- 少打断：首次引导只讲必须成功的路径，更多内容放到后续可召回引导。
- 任务化：用户看到的是“我正在完成什么”，不是“第几个 DOM 节点”。
- 可恢复：跳过、关闭窗口、跨页失败后，应能从明确状态恢复。
- 可降级：语音、外置聊天窗、插件面板、Electron bridge 任一失败时，仍能给出可理解的文本引导。
- 可退出：跳过按钮、Esc、窗口关闭、angry exit 都必须走同一清理链路。
- 尊重平台：Windows、macOS、Linux、Web 对窗口坐标、焦点、触控板、系统托盘入口的表现不同，不能只用一套桌面假设。

## 后续复杂优化

### 1. 场景图代替固定线性队列

现状：

- `HOME_SCENE_ORDER` 是固定数组。
- `intro_*`、`takeover_*`、`handoff_*` 在同一列表里表达顺序。
- 复杂条件主要写在 Director 方法内部。

建议：

- 新增 `homeTutorialGraph`，将场景拆成 `node / edge / guard / fallback`。
- 保留 `sceneOrder.home` 作为兼容层，由 graph 生成默认顺序。
- 每个节点声明进入条件、退出条件、失败回退、可跳过性和是否计入进度。

收益：

- 可以支持“快速引导 / 完整引导 / 桌面端增强引导”三条路线。
- 可以把跨页失败、插件面板打开失败、外置聊天不可用等情况从 Director 硬编码中移出。

### 2. 任务清单式进度

现状：

- 本轮只在气泡上显示轻量阶段进度。
- 用户仍不知道整个流程还剩哪些目标。

建议：

- 引入一个轻量任务面板，只展示 3 到 5 个用户目标：
  - 认识聊天入口
  - 开启猫爪能力
  - 查看插件管理
  - 找到设置入口
  - 接力到关键配置页
- 任务面板只在大段切换时出现，不常驻抢占画面。
- 每个任务绑定一个或多个 scene，完成后打勾。

收益：

- 用户能理解“为什么现在要点这里”。
- 后续可以支持断点恢复和用户主动重播某一段。

### 3. 平台能力矩阵

现状：

- 已有部分 Windows、macOS、Linux、Web 坐标和 skip 命中适配。
- 仍有不少能力通过运行时探测散落在 Director 与插件面板 runtime 内。

建议：

- 新增 `homeTutorialPlatformCapabilities`：
  - `windowBoundsSource`
  - `supportsExternalChat`
  - `supportsSystemTrayHint`
  - `supportsPluginDashboardWindow`
  - `pointerProfile`
  - `preferredSkipHitPadding`
- Director 只读取能力矩阵，不直接判断平台。

收益：

- 三端差异可测试。
- 后续打包桌面端和纯 Web 端时，不容易把 Electron-only 能力泄漏到 Web。

### 4. 旁白与动作的 cue 时间轴

现状：

- 部分 cue 已按真实音频比例映射。
- 仍有一些动作依赖固定等待或散落的 `wait()`。

建议：

- 每个有语音的 scene 声明 `timeline`：
  - `at: 0.18 -> highlight`
  - `at: 0.42 -> cursorClick`
  - `at: 0.78 -> openPanel`
- `timeline` 使用音频实际时长归一化，不按语言写死毫秒。
- 没有音频时，使用文本长度估算时长。

收益：

- 多语言节奏更自然。
- 被暂停、抵抗、恢复时不容易漂移。

### 5. 可恢复断点和失败回退

现状：

- handoff token 已后端权威化并支持单次消费。
- 首页主流程完成、跳过、angry exit 有统一清理要求。
- 但“用户中途关闭窗口后下次从哪里继续”还没有清晰产品口径。

建议：

- 为每个可恢复 scene 增加 `checkpointKey`。
- 只在完成一个大任务后保存断点，不保存每个小动作。
- 恢复时先展示“继续上次引导 / 从头开始 / 不再提示”。
- 跨页失败时回到首页并显示明确失败原因，而不是静默结束。

收益：

- 用户不会因为一次窗口关闭失去引导。
- 测试也能用 checkpoint 复现关键路径。

### 6. 可访问性和低动效模式

现状：

- 已有 `prefers-reduced-motion` 的基础处理。
- 气泡有 `aria-live` 之后可读性更好，但语音字幕和键盘路径仍不完整。

建议：

- 为每段旁白提供同步字幕区域。
- Esc 与跳过按钮共用终止链路。
- 在低动效模式下，Ghost Cursor 改为瞬移加淡入，而不是椭圆运动。
- 对视觉重点增加非颜色提示，例如边框样式和短文本状态。

收益：

- 低动效用户、键盘用户、屏幕阅读器用户都能完成引导。
- 复杂动画不会成为理解负担。

### 7. 本地体验指标

现状：

- 有 prompt lifecycle 日志和部分 tutorial event。
- 缺少“哪一步用户跳过 / 哪一步失败”的本地聚合。

建议：

- 只在本地记录：
  - scene 开始/完成/失败
  - 跳过原因
  - handoff 失败原因
  - angry exit 触发次数
- 不上传隐私数据。
- 在开发模式提供调试面板或导出按钮。

收益：

- 优化可以基于真实卡点，而不是凭感觉调节剧情长度。
- 不引入隐私风险。

## 分阶段落地建议

### Phase A：稳定当前体验

- 保持当前线性流程。
- 继续完善样式、定位、明暗主题、skip 命中和清理测试。
- 给关键 cue 增加回归测试。

### Phase B：抽出平台能力矩阵

- 先覆盖 Windows、macOS、Linux、Web 的窗口坐标和 pointer profile。
- 插件面板 runtime 只消费矩阵，不自行猜平台。

### Phase C：引入任务清单和 checkpoint

- 先只保存大任务完成状态。
- 不做任意 scene 恢复，避免恢复到半开的设置面板或插件窗口。

### Phase D：场景图和时间轴

- 用 graph 生成现有默认流程，保证行为不回退。
- 再增加快速引导、完整引导等分支。

## 测试要求

- `node --check static/yui-guide-*.js static/universal-tutorial-manager.js`
- `uv run pytest tests/test_agent_rewrite_regression.py tests/unit/test_tutorial_prompt_router.py -q`
- `npm.cmd run type-check` in `frontend/plugin-manager`
- `.\build_frontend.bat`
- 手动验证：
  - 首页普通 Web 模式
  - N.E.K.O.-PC 外置 `/chat` 模式
  - 插件面板 `/ui/` 接力
  - 明暗主题切换
  - Windows、macOS、Linux 至少各一次 skip 命中
  - `prefers-reduced-motion` 下无强制动画
