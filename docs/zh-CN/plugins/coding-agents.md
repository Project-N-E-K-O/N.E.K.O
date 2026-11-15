---
title: 用 Coding Agent 零基础做插件
description: 面向零基础开发者的 N.E.K.O 插件 AI 辅助入门：选择 DeepSeek Harness、Codex、Trae、Claude Code、Cursor、通义灵码或 CodeBuddy，一键复制引导 Prompt 让 AI 自动创建插件。
---

# 用 Coding Agent 零基础做插件

即使你没写过完整的 Python 项目，也可以直接用 AI 编程助手做出自己的第一个 N.E.K.O 插件。在下方点选你正在用的工具，复制一段现成的 **Setup Prompt** 粘给它，剩下的环境准备、插件创建和代码自检都会由 AI 自动完成。

<CodingAgentOnboarding />

## 粘贴 Setup Prompt 后，AI 会在后台帮你做哪几件事？

这段引导词把老手开发插件的标准动作全部打包教给了 Coding Agent：

1. **检查并准备环境** — 自动确认 `git` 与 `uv` 可用（缺了什么它自己装），按需拉取或复用 `Project-N-E-K-O/N.E.K.O` 仓库并同步依赖。
2. **用官方工具生成标准骨架** — 调用官方 `neko-plugin` 工具在 `plugin/plugins/<plugin_id>/` 下生成 `plugin.toml`、`config.example.toml`、`__init__.py`、测试与发布配置。
3. **对照本地最新文档写代码** — 直接翻阅仓库内的 `docs/zh-CN/plugins/` 与 `plugin/sdk/plugin/`，按现行规范编写异步 `@plugin_entry` 或对话工具 `@llm_tool`。
4. **自动跑通校验与打包** — 自动运行插件检查、代码风格校验与测试确保 `0 error(s)`，并可随时生成 `.neko-plugin` 安装包。

## 下一步去哪看？

- 想看各工具官方上手教程和 N.E.K.O 真实界面截图？打开 [各 Agent 上手教程](/zh-CN/plugins/coding-agent-guide)。
- 想自己看懂生成的两个核心文件？阅读 [插件开发入门文档](/zh-CN/plugins/plugin-development) 或手动版 [快速开始](/zh-CN/plugins/quick-start)。
- 插件做好了想分享给所有玩家？前往 [把插件发布到 N.E.K.O 插件市场](/zh-CN/plugins/cli)。
