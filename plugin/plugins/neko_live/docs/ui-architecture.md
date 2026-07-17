# NEKO Live UI 与模块贡献架构基线

> 配套 `development.md`（已落地设计）/ `live-center-roadmap.md`（路线）。本文记录**面板 UI、模块贡献模型、兜底契约**这三件多人开发要共享的架构决定，供后续每个模块作者照此填。更新：2026-07-15。

## 0. 出发点（一切设计的锚）

N.E.K.O 是一只**桌面 AI 猫娘**；neko_live 让她去给主播**当直播搭子**（真身是覆盖直播全生命周期的「直播中心」，见 development.md/roadmap §1）。由此定调：

- **猫开口才是产品，面板只是遥控器**——面板退到背景，优先「控制 + 监看」，别精装修。
- **直播时主播不会盯着面板**——面板优先服务开播前检查、少量关键控制、紧急暂停/恢复、以及事后复盘；不要把直播中的核心体验设计成需要主播持续读面板。
- **后续 UI 要先减噪再加诊断**——首屏只保留「能不能开播」「为什么安静」「接下来会做什么」「安全控制」；链路追踪、模块清单、健康行、详细诊断应放到次级页或开发者视图。
- **用户多是电脑小白；场景是 LIVE 直播**（不可重来、当众）。所以第一性原则是**全程可信赖**：宁可漏评，不可崩坏输出 / 不可一个模块炸了搞砸全场。可靠性 > 功能多 > 界面炫。

## 1. UI 结构：生命周期-域导航（外壳）+ 模块贡献

面板 = 一个**薄外壳**（固定导航 + 通用渲染）+ 各模块**贡献**进来的内容。导航按**直播生命周期 / 能力域**切分，恒定不随模块数膨胀，每个 roadmap 阶段都有预留落点：

| 一级页(tab) | id | 域 | 现在 | 未来落点 |
|---|---|---|---|---|
| 控制台 | `console` | 开播 | 登录优先的账号入口 + 显式无账号兜底 + 房号/链接确认 + 查询/连接 + 状态总览四格 + 模式（已折入原「直播间配置」） | 锐评 feed |
| 直播间互动 | `interaction` | 直播间互动 + 互动产出 | 首次出场锐评、后续弹幕接话、礼物事件、开场营业、冷场陪播和主动营业各自拥有独立功能开关；头像画面分析作为首次锐评的子开关。`live_enabled` 仍只由控制台底部的开始/停止直播动作管理 | 保持功能开关与真实 runtime gate 一一对应，禁止只做 UI 假开关 |
| 观众 | `viewers` | 本场/档案 | 内部固定分为「本场直播 / 观众档案」：前者展示互动观众、弹幕、支持事件、NEKO 发言和最多 30 位最近互动观众；后者用精简摘要表 + 详情弹窗承载安全画像，并在详情内提供二次确认的“重置印象 / 删除档案”。链路解释与最近 pipeline 结果只在开发者工具出现 | 暂不加入观看时长/贡献能力；不伪装在线人数 |
| ⚙设置 | `settings` | 平台 | 内部固定分为「安全与性能 / 数据与隐私 / 帮助与高级」：安全暂停默认开启且关闭前二次确认；队列只暴露“谨慎 3 / 标准 5 / 宽松 8”三档并即时保存；隐私页提供个性化记忆开关、固定 90 天说明、存储状态与二次确认的清空全部档案；完整路径按需弹窗；新手教程与开发者开关集中在帮助页。冷却/说话频率留在控制台“节奏设置”，`dry_run` 不向普通用户展示 | 自定义目录待配置持久化修复后再评估入口 |
| 开发者工具 | `dev` | 调试 | **仅开发者模式开启时出现**；内部固定分为「身份与头像查询 / 模拟直播事件 / 运行结果」三个子页。链路解释、最近 pipeline 结果、高级安全状态、审计摘要、模块总览和沙盒结果统一放在“运行结果”，不占用普通设置页 | — |

> 当前实现的常规 tab id 顺序：`console / interaction / viewers / settings`（+ `dev` 按 `developer_tools_enabled` 条件追加）。`viewers` 内部固定使用 `session / profiles` 两个子页；`settings` 内部固定使用 `safety / privacy / help` 三个子页；开发者工具内部固定使用 `identity / event / results` 三个子页；原 `live-room` 页已折入 `console`、`data`→`viewers`，原 `advanced` 的运行信息已迁入开发者 `results`。

## 2. 模块贡献模型（多人开发 / 扩展的核心）

后端已经模块化（`InteractionModule` + `ModuleRegistry`），runtime 只注册真实模块；模块导入失败时才用 `ReservedModule` 做安全降级，不再为未来能力预建空模块。**让 UI 镜像它**：一个功能 = 一个自包含 `modules/<id>/` 文件夹，**声明四个面向**，平台据此组合：

```
① 生命周期: setup / teardown（+ on_enable / on_disable，已落地：ModuleRegistry.enable/disable 隔离调用）
② 事件:     订阅 LiveEvent.type（EventBus 已落地：ctx.event_bus.subscribe(type, handler, owner)，隔离+归属+audit）
③ 数据:     只经 viewer_store / audit_store 边界（4 不变量）
④ 界面:     domain（归哪个一级页）+ config_schema()（声明式参数，面板自动渲染成设置卡）
            （+ 必须声明的「安全降级行为」，见 §4）
```

**加新功能 = 加模块 + 声明上面这些，零改外壳、并行无冲突。** 这正是「能让多人放心各写各模块」的地基。

## 3. 功能参数跟功能走（config_schema）

参照 [LangBot](https://docs.langbot.app/zh/plugin/dev/basic-info) 的 `spec.config` 思路：**模块声明自己的配置 schema，面板按当前受支持的 type 自动渲染进该功能的卡。**这里只承诺项目已经端到端实现并有真实消费者的最小契约，不照搬外部框架尚未使用的字段。

字段形状（`module.config_schema()` 返回 list[dict]）：
```python
{"name": <配置键>, "type": "boolean|select|text|string",
 "label": <i18n key>, "default": <值>,
 "options": [{"value":..,"label":<i18n key>}],   # select 用
 "hint": <i18n key>}                               # boolean 可选说明
```
面板渲染器（`panel.tsx` 的 `renderConfigField`）：`boolean→ToggleSwitch(+可选 hint 说明) / select→pill 组(选中 primary 蓝填充、未选 muted) / text|string→Input`，改即存（`saveConfig({[name]: v})`）。当前唯一真实消费者是 `avatar_roast.config_schema()`：强度（select→渲成 pill：温柔/正常/毒舌）/ 同人去重（boolean + `hint` 说明，`hint` 经 `module_registry.snapshot` 透传），渲进「弹幕锐评」卡。数值输入和条件显示目前既无生产声明也无产品需求，因此不属于现行契约；以后只有在出现真实模块消费者时，才连同类型清洗、hosted-ui 渲染和测试一起加入。

**「一张嘴」切分**——猫只有一张嘴，参数分两类：
- **功能级**（跟功能走，进功能卡）：开关、强度、致谢门槛、欢迎对象… —「这个功能开不开、触发时怎么表现」。
- **平台级**：安全暂停与队列策略留在「设置」；节奏 `rate_limit` / `activity_level` 和 co/solo 模式贴近日常开播动作，分别放在控制台的“节奏设置”和“直播主题”弹窗。普通设置不暴露原始队列数字、内部安全阈值或 `dry_run`；`dry_run` 默认关闭并仅保留为内部测试能力。

配置存储：锐评是核心切片，其参数沿用 `RoastConfig` 顶层字段；**未来功能模块用 `config.<module_id>.*` 命名空间**，避免全局扁平 config 膨胀。

## 4. 模块兜底（贯穿五层的同一条原则）

LIVE + 多模块 + 多人写 ⇒ **任何单个模块失败都不能搞砸直播**。这是平台保证，不是各模块自觉：

1. **注册层**：`ModuleRegistry.setup_all/teardown_all` 逐模块 try/except——坏模块标 `degraded` + 记 audit，**其余照常起停**；`snapshot()` 对 `status()/config_schema()` 也守卫。（已实现，见 `core/module_registry.py` + `tests/test_module_registry.py`）
3. **输出层**：`neko_dispatcher` 是唯一出口；**不确定时宁可沉默，不要崩坏输出**（dry_run / 限流 / 急停 / 队列 已在守）。
4. **UI 层**：渲染器对每个模块贡献包错误边界，单模块 schema/渲染抛错 → 降级卡，整盘面板照常。**已落地**：`panel_components.tsx` 的 `ModuleRenderBoundary` 用 try/catch 包住每张互动模块卡的同步渲染（hosted-ui runtime 无 class 组件 / `componentDidCatch`），抛错降级成带 `panel.modules.renderError` 文案的降级卡；`config_schema` 守卫亦在。契约 `test_panel_wraps_module_cards_in_error_boundary`。
5. **操作层**：永远在手边的一键急停 + 「安不安全」状态灯 + 自动急停（小白兜底）。

**契约**：模块声明贡献的**同时必须声明安全降级行为**；平台保证隔离。降级在 UI 以 `degraded` 徽章可见。

## 5. 分期（买期权，不预建）

- **P0 外壳 + 兜底**（部分已落地）：生命周期导航 ✓、`registry` 隔离 ✓、`config_schema` 契约 + 面板 mini 渲染器 ✓、弹幕锐评功能卡样例 ✓。
- **P1 事件模块化已落地**：Gift / SC / Guard 已由 `live_support_events` 订阅后的正常 pipeline 路径产出短句致谢；进场 / follow 仍明确不在当前产品范围。EventBus 订阅隔离与模块渲染兜底均已完成；schema 最小契约固定为 boolean / select / text / string，不再把没有消费者的数值字段和条件显示列作未完成能力。
- **P2 回迁/演进**：tab 命名已收敛为 4 项 `console/interaction/viewers/settings` + 条件 `dev`；UI error boundary 已落地。`ModuleRegistry.on_enable/on_disable` 保留为真实模块生命周期基础能力，但普通用户的功能偏好开关继续映射到明确的 runtime config gate，不得为了“接上调用方”而把偏好开关误当成卸载模块。剩余可选架构债只有在新增真实模块配置时再评估 config 命名空间化；不得预建无消费者的 schema 能力。

## 6. 约束（宿主 hosted-ui，写 UI 前必读）

- `ui/panel.tsx` 与 `panel_components.tsx`、`panel_data_sections.tsx`、`panel_helpers.ts`、`panel_state.ts` 是可维护源码；主分支宿主当前通过 `plugin.toml` 加载单文件兼容入口 `ui/panel_compat.tsx`。兼容入口只负责内联这些模块，不拥有独立行为。修改源码时必须同步重建兼容入口，并用 diff 确认两者行为一致；不要只改 bundle。
- 组件来自 `@neko/plugin-ui`（`Card/Stack/Grid/Field/Input/Select/Tabs/Text/StatCard/StatusBadge/DataTable/Alert/Button` + `useState/useEffect/useForm/useToast`；**无 sidebar、无 useRef**）。`ToggleSwitch` / `AvatarPreview` 等面板本地展示组件集中在 `ui/panel_components.tsx`，状态类型与默认表单值集中在 `ui/panel_state.ts`。
- 宿主 runtime（`frontend/plugin-manager/.../ui-kit/runtime.js`）：`isSafeUrl` **会剥 `<img src>` 里的 `data:` URL**（用 CSS `background-image` 绕过）；`createElement` 无 NS，**SVG 渲不了**；但**支持数组子元素**（`normalizeChild` 递归展平）和 `key`/`on*` 事件。
- 面板状态刷新必须走低成本前台契约：只有直播相关状态需要刷新时才启动 3 秒递归 `setTimeout`；`document.visibilityState` 非 `visible` 时停止排队；重新可见时立即补刷一次；同一时刻只允许一个 `refresh()` 请求；卸载时必须清理 timer 和 `visibilitychange` 监听。不要为只读状态新增常驻 `setInterval` 或后台高频轮询。
- 改 `panel.tsx`/`i18n` **运行时转译、不用 rebuild**，重开面板即生效；但提交 / 导出前必须同步生成 `panel_compat.tsx`，因为 manifest 入口使用单文件兼容版以支持主分支插件中心。`panel_compat.tsx` 必须是**完整功能面板**的单文件内联版本：允许保留 `@neko/plugin-ui` import 和 hooks，但不得包含相对 import、`window.NekoUiKit` 或 `__modules` linker 包装；不要为了兼容把它替换成只剩状态概览的最小 fallback 壳。**新 UI 文案必须 8 locale 同步。**

UI 只读取宿主投影的 dashboard state，并通过声明权限内的 action/config API 写入；不直接读取凭据或 store 文件，也不绕过 runtime、pipeline、`safety_guard` 或 `neko_dispatcher`。验证至少运行完整插件测试、插件 CLI check、8 locale key-set 对齐检查和 `git diff --check`。若兼容入口无法加载，回滚 `plugin.toml` 的 panel entry 到上一可用版本；后端直播、store 和输出链路不受影响。

## 7. 直播会话状态与异步操作约束

- `connecting`、`authenticating`、`reconnecting`、`connected` 和 `receiving` 都属于会话进行中。会话进行中不得重复开始，也不得切换平台、账号、凭据或直播间目标；主播必须先结束当前会话。
- 所有会改变配置、认证或直播状态的按钮都必须有 pending 锁，防止双击产生并发请求。乐观更新失败时恢复旧值；一个模块开关的保存不得让其他卡片尺寸或状态跳变。
- action 成功与随后 dashboard 刷新失败必须分开呈现：操作已经成功时不得改报为失败。连续刷新失败应显示“状态可能过期”和最后成功刷新时间，手动刷新也必须走同一状态跟踪入口。
- 面板日期和时间必须使用插件当前 locale，而不是浏览器默认语言；用户可见的新状态文案仍需同步全部 8 个 locale。
- 内部胶囊式子页必须实现标准 tab 键盘语义：当前项进入 Tab 顺序，并支持方向键、Home、End 切换和聚焦。条件移除开发者页时，外层 Tabs 必须回到有效选中项。
