# 猫娘锐评文档

本文是 `neko_roast` 文档地图和维护路由。它只说明“去哪份文档找权威信息”，不复制架构规范、运行步骤或路线图细节。

## 使用文档

- [快速开始](quickstart.md)：主播和开发者的实际使用流程。用户可见步骤、按钮、操作顺序、运行注意事项以这里为准。

## 开发文档

- [开发者指南（从这里开始）](developer-guide.md)：新开发者入口。负责心智模型、最短阅读路径、第一周建议和常见入口导航。
- [开发文档](development.md)：长期开发规范和架构契约的权威来源。负责模块边界、pipeline、数据边界、测试门禁、协作规范和文档更新要求。
- [开发总结与路线图](live-center-roadmap.md)：阶段目标、已完成进度和下一阶段路线。只记录“做到哪、接下来做什么”，不维护详细架构规范或运行 runbook。
- [UI 与模块贡献架构基线](ui-architecture.md)：面板 UI、模块贡献模型、`config_schema`、Hosted UI 约束和渐进组件化规则。
- [开发日志](devlog.md)：宿主 / SDK 侧历史问题、跨层事故、迁移原因和兼容取舍。
- [输出契约与弹幕回复模块](modules/output_contract.md)：普通弹幕分类、提示词、回复质量、长度整形和 dispatcher 输出边界。
- [直播主持流程](modules/live_hosting.md)：暖场、冷场、主动营业的选择、门禁、去重和 pipeline 边界。
- [主动话题材料](modules/active_topic_materials.md)：话题 family、profile、pack、轮换 shape 的归属、契约和降级边界。
- [主动营业内容目录](modules/active_content_catalogs.md)：静态 fallback 话题目录、公共访问器以及 host 目录未落地时的降级边界。
- [AI/IDE 开发规则](../AGENTS.md)：面向 IDE agent、自动化 reviewer 和贡献者的硬性维护规则。

- [宿主内容目录](modules/host_content_catalogs.md)：静态 idle-hosting beat、数据回退与共享素材接口。

## Canonical Source

同一种事实只能有一个权威来源；其他文档只链接引用，不复制规范。

| 事实类型 | 权威文档 | 其他文档如何处理 |
|---|---|---|
| 项目定位、用户入口、当前不做 | `../README.md` | 只链接，不复述完整定位 |
| 用户/主播操作流程 | `quickstart.md` | 只给入口链接 |
| 新开发者阅读路径 | `developer-guide.md` | 不在 roadmap 中重复 onboarding |
| 架构边界、模块边界、数据边界 | `development.md` | roadmap / guide 只写摘要并链接 |
| 协作规范、PR 规则、测试门禁、文档要求 | `development.md` | `AGENTS.md` 保留可执行硬规则摘要 |
| Agent 硬规则和 Reviewer Checklist | `../AGENTS.md` | `development.md` 解释背景和理由 |
| UI 架构、模块 UI 贡献、Hosted UI 限制 | `ui-architecture.md` | `development.md` 只链接 UI 细节 |
| 阶段目标、完成状态、下一阶段顺序 | `live-center-roadmap.md` | 不承载详细开发规范 |
| 宿主 / SDK 历史事故和取舍 | `devlog.md` | roadmap 只保留状态链接 |

## 文档更新路由

- 改用户流程、按钮、操作顺序：更新 `quickstart.md`。
- 改模块边界、pipeline、数据边界、协作规范、测试门禁：更新 `development.md`。
- 改新开发者入口或第一周路径：更新 `developer-guide.md`。
- 改阶段目标、完成状态、下一阶段顺序：更新 `live-center-roadmap.md`。
- 改面板结构、模块 UI 贡献模型、Hosted UI 约束：更新 `ui-architecture.md`。
- 发现宿主 / SDK 侧历史问题或跨层取舍：更新 `devlog.md`。
- 改硬规则、禁止事项、Reviewer Checklist、必跑命令：更新 `../AGENTS.md`。

新增 UI 文案时同步更新 8 个 locale 文件。Python 命令统一使用 `uv run`。如果一项改动跨越多个事实类型，先更新对应的权威文档，再在其他文档中保留短链接。
