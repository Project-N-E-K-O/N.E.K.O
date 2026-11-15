---
title: 图文实操：Codex 与 DeepSeek Harness
description: N.E.K.O 插件零基础保姆级图文实操教程：以 DeepSeek Harness (dsh) 与 OpenAI Codex 为例，演示从新建空文件夹、粘贴 Setup Prompt 到通过 neko-plugin check 并运行插件的完整步骤。
---

# 图文实操：Codex 与 DeepSeek Harness

本页挑选两款有代表性的工具——国内开源带浏览器可视化界面的 **DeepSeek Harness (`dsh`)** 与国际常用的 **OpenAI Codex**，手把手演示如何从**一个空文件夹**出发，通过 `neko-plugin` CLI 做出一个完整的**「喝水提醒（`water_reminder`）」**插件。

> 如果你用的是 Trae、Claude Code、Cursor、Qoder / 通义灵码、CodeBuddy 或 VS Code，请先在 [用 Coding Agent 零基础做插件](/zh-CN/plugins/coding-agents) 选择对应工具复制 Setup Prompt；粘贴之后的对话过程与下方完全一致。

<CodingAgentWalkthrough />

## 跑起来之后，怎样继续用大白话让 AI 升级插件？

第一个版本跑通后，不需要重开项目，直接在同一个聊天窗口里用日常语言提新要求即可：

- **加可视化配置项**：「帮我在 `plugin.toml` 和 `config.example.toml` 里加一个可调节提醒间隔分钟数的配置项，让用户能在插件管理页直接改，改完跑一下 `neko-plugin check`。」
- **加自定义面板 UI**：「先读一下 `docs/zh-CN/plugins/hosted-ui.md`，帮插件加一个 Hosted TSX 小面板统计今天喝了几杯水，并跑通 `check-hosted-tsx` 和 `neko-plugin check`。」
- **准备上架插件市场**：「帮我运行 `uv run neko-plugin precheck water_reminder` 跑一遍完整的市场 CI 检查，然后对照 `docs/zh-CN/plugins/cli.md` 一步步教我怎么发布到插件市场。」
