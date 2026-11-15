---
title: 各 Agent 上手教程
description: 汇总 Codex、DeepSeek Harness、Claude Code、Cursor、Trae、Qoder / 通义灵码、CodeBuddy、VS Code 等 Coding Agent 的官方入门教程，并附 N.E.K.O 插件管理的真实界面截图。
---

# 各 Agent 上手教程

从下面挑一个你顺手的 Coding Agent，按官方教程装好、登录并打开一个空文件夹，然后回到 [用 Coding Agent 零基础做插件](/zh-CN/plugins/coding-agents) 复制 Setup Prompt 发给 AI，剩下的交给它。

<CodingAgentTutorials />

## 跑起来之后，怎样继续用大白话让 AI 升级插件？

第一个版本跑通后，不需要重开项目，直接在同一个聊天窗口里用日常语言提新要求即可：

- **加可视化配置项**：「帮我在 `plugin.toml` 和 `config.example.toml` 里加一个可调节提醒间隔分钟数的配置项，让用户能在插件管理页直接改，改完再跑一遍检查。」
- **加自定义面板 UI**：「先读一下 `docs/zh-CN/plugins/hosted-ui.md`，帮插件加一个 Hosted TSX 小面板统计今天喝了几杯水，并把所有检查跑通。」
- **准备上架插件市场**：「帮我跑一遍完整的市场发布前检查，然后对照 `docs/zh-CN/plugins/cli.md` 一步步教我怎么发布到插件市场。」
