# 开发指南

Project N.E.K.O. 是包含形象渲染、实时/文本交互、持久记忆、Agent 执行与插件的开源 AI 伴侣平台。本站面向当前仓库的贡献者和集成者，不是价格、额度或 Provider 能力宣传页。

主要边界包括 `app/` 的 Python 3.11 服务、`main_logic/` 与 `memory/`、`brain/`、Jinja/static + 共享 React 聊天、Vue plugin manager、N.E.K.O.-PC Electron shell，以及 `docker/`。

| 目标 | 页面 |
| --- | --- |
| 检查工具 | [前置条件](./prerequisites) |
| 配置环境 | [开发环境搭建](./dev-setup) |
| 首次运行 | [快速开始](./quick-start) |
| 浏览仓库 | [项目结构](./project-structure) |
| 理解服务 | [架构](/zh-CN/architecture/) |
| 开发插件 | [插件快速开始](/zh-CN/plugins/quick-start) |
| 部署 | [部署](/zh-CN/deployment/) |

所有 Python 示例都使用 `uv run`。若文档与同 revision 的入口、loader 或 workflow 冲突，以当前代码为准并报告文档漂移。
