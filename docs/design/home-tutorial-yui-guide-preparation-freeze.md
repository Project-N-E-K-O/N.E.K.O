# 首页教程 Yui 引导准备阶段接口冻结说明

## 1. 文档目的

本文档用于完成主负责人在“正式并行开始前”必须给出的第一版冻结说明。

它解决的不是功能怎么实现，而是先把下面几件事钉死：

- 哪些场景 ID 已经定版
- `YuiGuideStep` 这一层共享契约长什么样
- `Director` 和 `handoff` 最小接口长什么样
- 哪些文件谁能改，谁不能改
- 合并顺序应该怎么走

如果这份冻结说明没有先出来，后面开发 B 和开发 C 很容易在同一个共享文件里边写边猜，最后把“协作”写成“重新对齐现场”。

本文冻结基准时间为 `2026-04-15`。

---

## 2. 冻结范围

本次冻结只覆盖准备阶段必须先定下来的内容，不提前决定下面这些实现细节：

- 不冻结气泡、语音、ghost cursor 的具体实现方式
- 不冻结 `/ui/` 的最终接入版本结论
- 不冻结跨页恢复后的完整目标页场景树
- 不冻结具体素材资源、样式细节和时长参数

本次冻结重点只放在：

- 场景命名
- 字段形状
- 入口锚点规则
- 文件 owner
- 合并与冲突控制

---

## 3. 当前冻结结论

### 3.1 场景 ID 冻结

第一版最小场景 ID 冻结如下：

| 场景 ID | 页面归属 | 语义说明 |
|---|---|---|
| `intro_basic` | `home` | 开场第一句，介绍文字输入与语音召唤 |
| `intro_proactive` | `home` | 开场第二句，介绍主动搭话 / 主动视觉 |
| `intro_cat_paw` | `home` | 开场第三句，指向猫爪 / OpenClaw 入口 |
| `takeover_capture_cursor` | `home` | 接管动作一，借用鼠标 |
| `takeover_plugin_preview` | `home` | 接管动作二，首页插件预演 |
| `takeover_settings_peek` | `home` | 接管动作三，打开设置一瞥 |
| `takeover_return_control` | `home` | 接管动作四，归还控制权 |
| `interrupt_resist_light` | `home` | 轻微抵抗 |
| `interrupt_angry_exit` | `home` | 激烈抢夺 / 生气退出 |
| `handoff_api_key` | `home` | 从首页接力到 `/api_key` |
| `handoff_memory_browser` | `home` | 从首页接力到 `/memory_browser` |
| `handoff_steam_workshop` | `home` | 从首页接力到 `/steam_workshop_manager` |
| `handoff_plugin_dashboard` | `home` | 从首页接力到 `/api/agent/user_plugin/dashboard -> /ui` |

冻结规则：

- 已冻结的场景 ID 不允许改名
- 如果后续需要拆细场景，只能新增 ID，不能偷偷改旧 ID 的语义
- 若 `intro_proactive` 后续要拆成“主动搭话”和“主动视觉”两个独立节点，必须新增新 ID，不得重定义旧 ID

### 3.2 锚点写法冻结

`anchor` 字段第一版允许两种写法：

1. 普通 CSS 选择器，例如 `#text-input-area`
2. 带模型前缀模板的选择器，例如 `#${p}-btn-agent`

其中：

- `${p}` 表示当前页面中由教程系统决定的模型前缀
- 开发 B 不改 `anchor` 语义
- 开发 C 负责保证锚点与真实页面入口一致

冻结规则：

- 不允许把 `anchor` 改成“看起来像锚点，其实不是实际 DOM”的假值
- 不允许在准备阶段引入第三种锚点表达协议

### 3.3 `YuiGuideStep` 字段形状冻结

第一版共享契约冻结为：

```ts
type YuiGuideStep = {
  id: string;
  page: 'home' | 'api_key' | 'memory_browser' | 'steam_workshop' | 'plugin_dashboard';
  anchor: string;
  tutorial: {
    title: string;
    description: string;
    autoAdvance?: boolean;
    allowUserInteraction?: boolean;
  };
  performance?: {
    bubbleText?: string;
    bubbleTextKey?: string;
    voiceKey?: string;
    emotion?: string;
    cursorAction?: 'move' | 'click' | 'wobble' | 'none';
    cursorTarget?: string;
    settingsMenuId?: string;
    cursorSpeedMultiplier?: number;
    delayMs?: number;
    interruptible?: boolean;
    resistanceVoices?: string[];
    resistanceVoiceKeys?: string[];
  };
  navigation?: {
    openUrl?: string;
    windowName?: string;
    resumeScene?: string | null;
  };
  interrupts?: {
    mode?: 'ignore' | 'degrade' | 'theatrical_abort';
    threshold?: number;
    throttleMs?: number;
    resetOnStepAdvance?: boolean;
  };
};
```

字段职责冻结为：

- `tutorial` 由主负责人维护整体形状，服务教程骨架
- `performance` 由开发 B 主改，服务演出层
- `navigation` 由开发 C 主改，服务跨页与真实入口
- `interrupts` 由主负责人定形状，开发 B 在既定形状内补策略

冻结规则：

- 准备阶段不新增 `meta`、`debug`、`owner` 等额外字段
- 若未来确需扩展字段，必须先改冻结说明，再改共享文件

### 3.4 `Director` 最小接口冻结

第一版最小接口冻结为：

```ts
interface YuiGuideDirector {
  startPrelude(): Promise<void>;
  enterStep(stepId: string, context: unknown): Promise<void>;
  leaveStep(stepId: string): Promise<void>;
  handleInterrupt(event: Event): void;
  skip(reason?: string): void;
  abortAsAngryExit(source?: string): Promise<void>;
  destroy(): void;
}
```

冻结规则：

- 主负责人只冻结接口名和职责边界，不提前规定内部实现细节
- `destroy()` 只允许作为统一清理终点，不允许后续多处各自调用半套清理
- `UniversalTutorialManager` 在第一阶段只承诺四类生命周期出口：`prelude-start`、`step-enter`、`step-leave`、`tutorial-end`
- 这四类生命周期在运行时通过 `window` 上的 `CustomEvent('neko:yui-guide:<name>')` 向外广播，供开发 B 在 `Director` 未完全接入前调试或旁路监听
- 第一阶段只有 `sceneOrder[page]` 非空的页面才会真正启用这套运行时桥接；按 `2026-04-15` 的冻结结果，当前只有 `home`

补充约定：

- `tutorial-end` 第一阶段先保留 `skip / complete / destroy` 三类结束原因
- `angry_exit` 作为第二阶段接管流程的特殊结束原因再并入，不在准备阶段提前写死实现

### 3.4.1 首页步骤映射冻结

第一版首页教程骨架允许在旧 tutorial step 上额外挂一个 `yuiGuideSceneId`，只承担“这个旧 step 对应哪个 Yui 场景”的映射职责，不承担演出实现职责。

`2026-04-15` 首批冻结映射为：

- `#${p}-btn-agent` -> `intro_cat_paw`
- `#${p}-toggle-proactive-chat` -> `intro_proactive`
- `#${p}-btn-settings` -> `takeover_settings_peek`
- `#${p}-menu-api-keys` -> `handoff_api_key`
- `#${p}-menu-memory` -> `handoff_memory_browser`
- `#${p}-menu-steam-workshop` -> `handoff_steam_workshop`

额外说明：

- `intro_basic` 暂不通过新增 driver step 承接，而是先通过 `startPrelude()` + 注册表中的 `#text-input-area` 锚点承接
- `startPrelude()` 只负责处理“当前页面里还没有挂到旧 tutorial step 上的 intro 场景”
- 按 `2026-04-15` 的落地结果，首页 `prelude` 当前只承接 `intro_basic`
- `intro_proactive` 与 `intro_cat_paw` 不在第一阶段的 `prelude` 中重复播放，而是分别跟随旧首页步骤进入
- 这样做是为了避免在主负责人前置阶段提前重排首页旧教程顺序，降低与后续实现合流时的冲突概率

### 3.4.2 M2 首页交互包装 API 冻结（补充）

为降低开发 B 的演出层与开发 C 的真实首页交互之间的运行时耦合，进入 `Milestone 2` 前补冻结一组最小首页交互包装 API。

这组 API 的定位不是替代真实 DOM，而是：

- 由开发 C 统一封装首页真实入口与打开时机
- 由开发 B 优先调用这些 API，而不是自行假设页面点击链稳定
- 由主负责人审核字段、语义与失败回退是否一致

第一版最小 API 冻结建议为：

```ts
interface YuiGuideHomeBridge {
  openAgentPanel(options?: { source?: string }): Promise<boolean>;
  openSettingsPanel(options?: { source?: string }): Promise<boolean>;
  ensureSettingsMenuVisible(
    menuId: 'character' | 'api-keys' | 'memory' | 'steam-workshop',
    options?: { source?: string }
  ): Promise<boolean>;
  closeSettingsPanel(options?: { source?: string }): Promise<boolean>;
}
```

字段与语义冻结为：

- `openAgentPanel()`：
  - 负责稳定打开首页真实 `猫爪 / Agent / OpenClaw` 入口
  - 成功返回 `true`，失败或入口不存在返回 `false`
- `openSettingsPanel()`：
  - 负责稳定打开首页真实设置弹层
  - 不要求开发 B 知道点击链、遮挡关系或 hover 细节
- `ensureSettingsMenuVisible(menuId)`：
  - 负责保证对应菜单项真实可见且可定位
  - `menuId` 第一版仅允许 `character`、`api-keys`、`memory`、`steam-workshop`
  - 不在这一阶段引入新的菜单 ID 命名协议
- `closeSettingsPanel()`：
  - 负责优雅关闭首页真实设置弹层
  - 供 `takeover_settings_peek` 的收尾与 `skip / angry_exit` 清理复用

补充约束：

- 第一版返回值统一为 `Promise<boolean>`，不在 M2 前扩成复杂结果对象
- 若后续确需返回额外调试信息，必须先改冻结说明，再改实现
- 开发 B 不应在 M2 中绕过这组 API，直接把 `click()`、`mouseover()`、临时定时器散写回 `Director`
- 开发 C 不应把该桥接逻辑散落进多个按钮回调，而应收口到单独模块或统一导出入口
- 主负责人应优先把这组 API 视为 M2 的 B/C 边界，而不是让双方继续围绕页面偶然行为联调

### 3.5 `handoff` 最小字段冻结

第一版最小字段冻结为：

```ts
type YuiGuideHandoffToken = {
  token: string;
  token_version: number;
  flow_id: string;
  source_page: string;
  target_page: string;
  resume_scene: string | null;
  created_at: number;
  expires_at: number;
};
```

冻结规则：

- 准备阶段允许 `resume_scene` 暂时为 `null`
- 不允许在后续实现里把 `resume_scene` 换成别的字段名
- 真正的签名与消费语义在后续阶段继续收口，但字段名字先不再改

### 3.6 文件 owner 冻结

第一版 owner 冻结如下：

| 文件 / 模块 | owner | 说明 |
|---|---|---|
| `static/universal-tutorial-manager.js` | 主负责人 | 高冲突骨架文件 |
| `templates/index.html` | 主负责人 | 首页脚本装配入口 |
| `static/yui-guide-steps.js` | 主负责人 | 共享场景注册表与步骤契约 |
| `static/yui-guide-director.js` | 开发 B | 演出层主模块 |
| `static/yui-guide-overlay.js` | 开发 B | 演出层 UI 壳 |
| `static/css/yui-guide.css` | 开发 B | 演出样式 |
| `static/assets/tutorial/` | 开发 B | 演出素材目录 |
| `static/avatar-ui-popup.js` | 开发 C | 首页设置入口与菜单结构 |
| `static/app-interpage.js` | 开发 C | 跨页消息桥 |
| `static/yui-guide-page-handoff.js` | 开发 C | handoff 主体模块 |
| `static/app-ui.js` | 开发 C 主导，主负责人收口 | 共管文件 |
| `static/app-buttons.js` | 开发 C 主导，主负责人收口 | 共管文件 |

### 3.7 合并顺序与节奏冻结

第一版合并顺序冻结为：

1. 主负责人先冻结共享契约和空壳挂接点
2. 先合开发 B 的新增演出模块
3. 再合开发 C 的真实入口与 handoff 逻辑
4. 最后由主负责人完成骨架层接线和联调

第一版合并节奏冻结为：

- 每天至少两个合并窗口
- 中午优先看开发 B 的新增模块
- 晚上优先看开发 C 的入口与 handoff 变更

---

## 4. 落地产物

本次准备阶段实际落地产物为：

- [home-tutorial-yui-guide-main-owner-stage-breakdown.md](./home-tutorial-yui-guide-main-owner-stage-breakdown.md)
- [home-tutorial-yui-guide-three-person-collaboration.md](./home-tutorial-yui-guide-three-person-collaboration.md)
- `static/yui-guide-steps.js`

其中 `static/yui-guide-steps.js` 的定位不是“已完成实现”，而是：

- 第一版共享场景注册表
- 第一版字段形状落地点
- 后续开发 B 和开发 C 都必须围绕它改，而不是各自再造一份配置

---

## 5. 准备阶段检查结论

### 5.1 是否能很好支持后续实现

结论：**可以，且比只停留在口头分工更稳。**

原因：

- 场景 ID 已冻结，后续不会再因为叫法不同而联调失败
- 字段形状已冻结，后续不会再在 `performance / navigation / interrupts` 上各扩一套
- `static/yui-guide-steps.js` 被明确为单一共享契约入口，后续实现不需要再发明第二份步骤配置
- `/ui/` 被明确后置，避免过早把 Vue 面板和首页教程绑死

### 5.2 是否还存在后续冲突风险

结论：**仍有冲突风险，但已经从“无序冲突”降成“可控热点”。**

当前仍需重点盯住的热点是：

1. `static/yui-guide-steps.js`
原因：这是共享契约文件，B 和 C 都会碰。
控制方式：主负责人长期持有；B 只主改 `performance`；C 只主改 `anchor / navigation`。

2. `static/app-ui.js` / `static/app-buttons.js`
原因：这两个文件后续既可能被入口包装用到，也可能被“请她离开”等统一动作用到。
控制方式：开发 C 主导，主负责人只做最小收口，开发 B 不直接改。

3. `/ui/` 范围漂移
原因：如果没人把范围钉死，很容易有人提前把插件面板教程硬塞进当前阶段。
控制方式：只有主负责人能判定是否进入 `Milestone 4`。

### 5.3 需要立即优化的点

本次冻结后，建议后续实现继续遵守以下优化规则：

- `static/yui-guide-steps.js` 里按场景分块，避免三个人在文件里到处散插
- 每次新增场景都追加到现有命名体系，不改旧 ID 语义
- 所有“打开页面”“关闭页面”“请她离开”动作，都优先封成可复用包装，不直接写死 DOM 点击链路
- 任何涉及 `destroy()`、`skip()`、`abortAsAngryExit()` 的改动，都必须先经过主负责人 review

---

## 6. 变更控制规则

从本说明发布后，以下内容视为“冻结内容”：

- 场景 ID 名称
- `YuiGuideStep` 顶层字段形状
- `Director` 最小接口名
- `handoff` 最小字段名
- 文件 owner 划分

若后续需要改动这些冻结内容，必须满足：

1. 先改本冻结说明
2. 再改共享契约文件
3. 再通知开发 B 和开发 C 调整实现

不允许先改代码、后补说明。
