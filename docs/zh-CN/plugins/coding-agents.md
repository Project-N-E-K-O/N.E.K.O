---
title: 用 AI 零基础做插件
description: 不会写代码也能做 N.E.K.O 插件：选一个 Coding Agent，复制一段话发给 AI，让它从零准备环境并写插件；或用已装好的 N.E.K.O 工作台新建项目交给 AI。
---

# 用 AI 零基础做插件

你不需要会写代码。选一个 AI 编程工具（Coding Agent），复制一段话发给它，剩下的交给 AI，你只负责点「允许」和界面上的按钮。

**你能做出什么**：比如让猫娘定时提醒你喝水的小插件、她在聊天里能调用的查天气工具、或一个显示今日待办的小面板——只要能用一句话描述，就能做出来。

## 1. 选一条路

<CodingAgentOnboarding section="routes" />

## 2. 选你的 Agent

<CodingAgentOnboarding section="agent" />

## 3. 复制这段话发给 AI

先在卡片上选好路线（A 或 B），再复制。

<CodingAgentOnboarding section="prompt" />

## 4. 路线 A：让 AI 从头带你做

1. 用你的 Agent **打开一个空文件夹**（任意位置，专门用来放这个项目）。
2. 把上面「路线 A」那段话发给它。
3. AI 请求执行操作时点「允许」；它会自己下载、安装、配置，准备时间比较长。等它告诉你网址后打开网页，第一次按提示在界面里填一次模型 API。
4. 在网页的插件管理里点「启动」试用你的插件；之后 AI 每次改完会自动重载。
5. 下次想继续，对 AI 说「帮我启动 N.E.K.O」即可。

![插件页右上角有「启动」「重载」按钮](/images/plugins/onboarding/neko-plugin-detail.png)

## 5. 路线 B：用已装好的 N.E.K.O

1. 先装好 Git：[官网下载页](https://git-scm.com/downloads)选你的系统，用安装包一路下一步。
2. 打开 N.E.K.O 插件管理，左侧进「插件制作工作台」，点「新建插件」：填名称和 ID、选好父目录、点「预览项目」确认文件齐全后点「创建插件」。**记住项目文件夹的位置**。

   ![在插件制作工作台新建插件，预览标准文件](/images/plugins/onboarding/neko-studio-new.png)

3. 用你的 Agent **打开这个插件项目文件夹**。
4. 把上面「路线 B」那段话发给它。
5. 插件页点「启动」试用，之后每次 AI 改完点「重载」刷新效果；出错就把报错原样发给 AI。

![插件页右上角有「启动」「重载」按钮](/images/plugins/onboarding/neko-plugin-detail.png)

## 6. 想分享给别人

路线 B 在工作台点「打包下载」，路线 A 直接让 AI 帮你打包，都能得到一个 `.neko-plugin` 安装包发给朋友，对方在「插件管理」页点「导入」就能用。正式发布到插件市场需要源码版——走路线 A 的可以直接发布，看[发布插件](/zh-CN/plugins/cli)。

![朋友拿到 .neko-plugin 安装包后，在插件管理页点导入](/images/plugins/onboarding/neko-import-dialog.png)

> 如果新建的项目里没有 AGENTS.md，说明你的 N.E.K.O 版本较旧，请先更新到最新版本再重试。

---

**已经会用源码版、想手动开发？** 阅读[插件开发入门文档](/zh-CN/plugins/plugin-development)或[快速开始](/zh-CN/plugins/quick-start)。
