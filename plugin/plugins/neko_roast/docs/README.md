# NEKO Live 文档

本文是 `neko_roast` 文档地图和维护路由。它只说明“去哪份文档找权威信息”，不复制架构规范、运行步骤或路线图细节。

## 使用文档

- [快速开始](quickstart.md)：主播和开发者的实际使用流程。用户可见步骤、按钮、操作顺序、运行注意事项以这里为准。
- [猫猫独播体验指南](solo-stream-test-guide.md)：给组长、内测主播或接手开发者的 `solo_stream` 试播 runbook。只保留启动、连接、dry_run、真实输出、监控和通过标准。
- [2026-07-07 交接说明](handoff-2026-07-07.md)：当前 `roast` 分支的短交接页。只记录接手时最容易踩的边界、下一步和验证基线。

## 开发文档

- [开发者指南（从这里开始）](developer-guide.md)：新开发者入口。负责心智模型、最短阅读路径、第一周建议和常见入口导航。
- [开发文档](development.md)：长期开发规范和架构契约的权威来源。负责模块边界、pipeline、数据边界、测试门禁、协作规范和文档更新要求。
- [Independent Mode Product Plan](independent-mode-product-plan.md)：Independent Mode 的产品路线权威来源。负责产品命题、当前阶段开发分工、Slice 顺序、MVP、非目标和内测节奏。
- [Runtime Observability](runtime-observability.md)：运行态观测语言的权威来源。负责 Runtime Timeline、Stage、Event Outcome、Skip Reason、Monitor Signal 和 Dashboard Visibility。
- [开发总结与路线图](live-center-roadmap.md)：阶段目标、已完成进度和下一阶段路线。只记录“做到哪、接下来做什么”，不维护详细架构规范或运行 runbook。
- [UI 与模块贡献架构基线](ui-architecture.md)：面板 UI、模块贡献模型、`config_schema`、Hosted UI 约束和渐进组件化规则。
- [开发日志](devlog.md)：宿主 / SDK 侧历史问题、跨层事故、迁移原因和兼容取舍。
- [输出契约与弹幕回复模块](modules/output_contract.md)：普通弹幕分类、提示词、回复质量、长度整形和 dispatcher 输出边界。
- [直播主持流程](modules/live_hosting.md)：暖场、冷场、主动营业的选择、门禁、去重和 pipeline 边界。
- [主动话题材料](modules/active_topic_materials.md)：话题 family、profile、pack、轮换 shape 的归属、契约和降级边界。
- [主动营业内容目录](modules/active_content_catalogs.md)：静态 fallback 话题目录、公共访问器以及 host 目录未落地时的降级边界。
- [AI/IDE 开发规则](../AGENTS.md)：面向 IDE agent、自动化 reviewer 和贡献者的硬性维护规则。

## 模块文档

- [live_events](modules/live_events.md)：直播事件窗口择优、冷却期候选选择、弹幕低质过滤、主题凝练、回复技巧提示和运行期观众偏好提示。
- [live_support_events](modules/live_support_events.md)：Gift / SC / Guard 被选中后的短句致谢 handler，复用 pipeline / safety guard / dispatcher。
- [douyin_live_ingest](modules/douyin_live_ingest.md)：抖音只读 live provider、公开投影脱敏、内置托管 douyinLive bridge、事件归一化、bridge-only 可替换后端边界，以及未纳入 v1 的自动登录/签名边界。
- [宿主内容目录](modules/host_content_catalogs.md)：静态 idle-hosting beat、数据回退与共享素材接口。
- [直播状态助手](modules/live_status_helpers.md)：连接与活跃状态的就绪度、计时、主持状态判定、导播下一步投影与主题上下文投影，纯计算无副作用。
- [观众与安全存储](modules/viewer_stores.md)：观众档案、审计脱敏与加密凭据命名空间。

## Canonical Source

同一种事实只能有一个权威来源；其他文档只链接引用，不复制规范。

| 事实类型 | 权威文档 | 其他文档如何处理 |
|---|---|---|
| 项目定位、用户入口、当前不做 | `../README.md` | 只链接，不复述完整定位 |
| 用户/主播操作流程 | `quickstart.md` | 只给入口链接 |
| 猫猫独播试播 runbook | `solo-stream-test-guide.md` | quickstart 只给入口链接 |
| 当前分支交接状态 | `handoff-2026-07-07.md` | 只做短入口，不替代权威规范 |
| Independent Mode 产品路线、当前阶段开发分工、MVP、内测节奏 | `independent-mode-product-plan.md` | roadmap 只保留阶段指针 |
| 新开发者阅读路径 | `developer-guide.md` | 不在 roadmap 中重复 onboarding |
| 架构边界、模块边界、数据边界 | `development.md` | roadmap / guide 只写摘要并链接 |
| 运行态观测语言、Skip Reason、Monitor Signal | `runtime-observability.md` | development / AGENTS 只写入口和必查项 |
| 协作规范、PR 规则、测试门禁、文档要求 | `development.md` | `AGENTS.md` 保留可执行硬规则摘要 |
| Agent 硬规则和 Reviewer Checklist | `../AGENTS.md` | `development.md` 解释背景和理由 |
| UI 架构、模块 UI 贡献、Hosted UI 限制、`panel_compat.tsx` 兼容入口策略 | `ui-architecture.md` | `development.md` 只保留测试门禁和短约束 |
| 阶段目标、完成状态、下一阶段顺序 | `live-center-roadmap.md` | 不承载详细开发规范 |
| 宿主 / SDK 历史事故和取舍 | `devlog.md` | roadmap 只保留状态链接 |

## 文档更新路由

- 改用户流程、按钮、操作顺序：更新 `quickstart.md`。
- 改猫猫独播试播步骤、dry_run 使用、30 分钟验收流程：更新 `solo-stream-test-guide.md`。
- 改 Independent Mode 产品路线、当前阶段开发分工、MVP、非目标或内测节奏：更新 `independent-mode-product-plan.md`。
- 改模块边界、pipeline、数据边界、协作规范、测试门禁：更新 `development.md`。
- 改 Runtime Timeline、Stage、Event Outcome、Skip Reason、Monitor Signal 或 Dashboard Visibility：更新 `runtime-observability.md`。
- 改新开发者入口或第一周路径：更新 `developer-guide.md`。
- 改阶段目标、完成状态、下一阶段顺序：更新 `live-center-roadmap.md`。
- 改面板结构、模块 UI 贡献模型、Hosted UI 约束或 `panel_compat.tsx` 兼容入口策略：更新 `ui-architecture.md`。
- 发现宿主 / SDK 侧历史问题或跨层取舍：更新 `devlog.md`。
- 改硬规则、禁止事项、Reviewer Checklist、必跑命令：更新 `../AGENTS.md`。

新增 UI 文案时同步更新 8 个 locale 文件。Python 命令统一使用 `uv run`。如果一项改动跨越多个事实类型，先更新对应的权威文档，再在其他文档中保留短链接。
