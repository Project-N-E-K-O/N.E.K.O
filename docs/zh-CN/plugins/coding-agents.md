---
title: 用 AI 零基础做插件
description: 不会写代码也能做 N.E.K.O 插件：在「插件制作工作台」新建项目，把一段话发给任意 AI 编程工具，检查、重载、启动全在界面上点按钮完成。
---

# 用 AI 零基础做插件

你不需要会写代码。N.E.K.O 建好插件项目、自带写给 AI 看的说明书（AGENTS.md），AI 负责写代码和运行，你只负责点按钮。

**你能做出什么**：比如让猫娘定时提醒你喝水的小插件、她在聊天里能调用的查天气工具、或一个显示今日待办的小面板——只要能用一句话描述，就能做出来。

## 1. 准备两样东西

- **已安装的 N.E.K.O**：桌面版或 [Steam 版](https://store.steampowered.com/app/4099310/__NEKO/) 均可。
- **一个 AI 编程工具（Coding Agent）**：从下面挑一个装好登录，不会选就先看它的官方教程。

<CodingAgentOnboarding section="agent" />

## 2. 在「插件制作工作台」点「新建插件」

打开 N.E.K.O 插件管理，左侧进入「插件制作工作台」，点「新建插件」：填好插件名称和 ID，选好存放的父目录，点「预览项目」能看到它自带 AGENTS.md、CLAUDE.md 等标准文件，然后点「创建插件」。**记住这个项目文件夹的位置**，下一步要用。

![在插件制作工作台新建插件，预览标准文件](/images/plugins/onboarding/neko-studio-new.png)

## 3. 用你的 Agent 打开这个文件夹

按第 1 步所选工具卡里的提示，在它的界面里打开刚才新建的插件文件夹，并允许 AI 读写文件、执行操作。

## 4. 复制这段话发给 AI

<CodingAgentOnboarding section="prompt" />

AI 会自己读完 AGENTS.md、写代码、跑检查。它请求执行操作时你点「允许」就行，遇到问题它也会用大白话问你。

## 5. 回到工作台看效果

在项目详情页点「在插件管理中打开」，插件页右上角点「启动」就能试用；之后每次 AI 改完，点「重载」刷新效果。想验证项目有没有问题，工作台「发布」页签里有「开始检查」；想改功能就继续跟 AI 聊天提要求。

![工作室项目详情页：刷新、打开文件夹、在插件管理中打开](/images/plugins/onboarding/neko-studio-project.png)

![插件页右上角有「启动」「重载」按钮](/images/plugins/onboarding/neko-plugin-detail.png)

## 6. 想分享给别人

工作台里的「打包下载」可以把插件导出成 `.neko-plugin` 安装包发给朋友，对方在「插件管理」页点「导入」就能用；想正式发布到市场，看[发布插件](/zh-CN/plugins/cli)。

![朋友拿到 .neko-plugin 安装包后，在插件管理页点导入](/images/plugins/onboarding/neko-import-dialog.png)

> 如果新建的项目里没有 AGENTS.md，说明你的 N.E.K.O 版本较旧，请先更新到最新版本再重试。

---

**已经会用源码版、想手动开发？** 阅读[插件开发入门文档](/zh-CN/plugins/plugin-development)或[快速开始](/zh-CN/plugins/quick-start)。
