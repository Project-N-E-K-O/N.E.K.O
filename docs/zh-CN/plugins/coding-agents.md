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

## 3. 跟着做

<CodingAgentRoute route="A">

1. 用你的 Agent **打开一个空文件夹**（任意位置，专门放这个项目；具体怎么打开，看第 2 节你选的 Agent 卡片里的官方教程）。
2. 把这段话发给它：

   <CodingAgentOnboarding section="prompt" route="A" />

3. AI 请求执行操作时点「允许」。它会自己下载和安装，准备时间比较长，耐心等。
4. 打开它给你的网址。第一次要填一次模型 API（还没有 API Key 的话，可以按页面顶部提示选「免费版」）：

   ![第一次打开网页时填写模型 API](/images/plugins/onboarding/neko-api-setup.png)

   1. 选择一个核心 API 服务商。
   2. 填入你的 API Key。
   3. 点「保存设置」。

5. 打开插件管理（方法见[怎么打开插件管理](#怎么打开插件管理)）。
6. 在插件列表里点你的插件：

   ![插件列表里点进你的插件](/images/plugins/onboarding/neko-plugin-list.png)

   1. 点你的插件卡片，进入详情页。

7. 点「启动」试用：

   ![插件详情页的启动和重载按钮](/images/plugins/onboarding/neko-plugin-detail.png)

   1. 点「启动」运行插件。
   2. 之后 AI 改完会自动重载；没生效时再点「重载」。

8. 之后直接跟 AI 说想改什么就行；下次想继续，对它说「帮我启动 N.E.K.O」。

</CodingAgentRoute>

<CodingAgentRoute route="B">

1. 先装好 Git（路线 B 卡片里有官网下载链接，用安装包一路下一步）。
2. 打开插件管理（方法见[怎么打开插件管理](#怎么打开插件管理)）。
3. 左侧点「插件制作工作台」，再点「新建插件」：

   ![插件制作工作台入口和新建插件按钮](/images/plugins/onboarding/neko-studio-entry.png)

   1. 左侧的「插件制作工作台」。
   2. 右上角「新建插件」按钮。

4. 填好名称和 ID，点「选择文件夹」选一个放项目的地方：

   ![填写插件名称、ID 并选择文件夹](/images/plugins/onboarding/neko-studio-new.png)

   1. 插件名称（随便起）。
   2. 插件 ID（小写字母开头）。
   3. 「选择文件夹」选项目放哪。

5. 点「预览项目」确认文件齐全（能看到 AGENTS.md），再点「创建插件」：

   ![预览标准项目文件后点创建插件](/images/plugins/onboarding/neko-studio-preview.png)

   1. 预览里框出的 AGENTS.md 和 CLAUDE.md 就是给 AI 看的说明书。
   2. 确认后点「创建插件」。

6. 创建完成。**记住项目文件夹的位置**：

   ![创建完成后的项目资料和源码目录](/images/plugins/onboarding/neko-studio-created.png)

   1. 「源码目录」就是项目文件夹的位置。
   2. 点「打开文件夹」可以直接看到里面的文件。
   3. 点「在插件管理中打开」可以跳到插件详情页。

7. 用你的 Agent **打开这个项目文件夹**，把这段话发给它：

   <CodingAgentOnboarding section="prompt" route="B" />

8. 点「在插件管理中打开」进入插件详情页，点「启动」试用；之后每次 AI 改完，点「重载」看效果：

   ![插件详情页的启动和重载按钮](/images/plugins/onboarding/neko-plugin-detail.png)

   1. 点「启动」运行插件。
   2. 每次 AI 改完，点「重载」刷新效果。

9. 如果出错，打开「日志」页，把报错复制下来原样发给 AI：

   ![插件日志页里复制报错](/images/plugins/onboarding/neko-plugin-logs.png)

   1. 点「日志」标签页。
   2. 复制标着 ERROR 的那几行发给 AI。

</CodingAgentRoute>

## 怎么打开插件管理

路线 A 和路线 B 都要用到插件管理。在 N.E.K.O 主界面这样打开：

![在 N.E.K.O 主界面打开插件管理](/images/plugins/onboarding/neko-open-plugin-manager.png)

1. 把鼠标移到猫娘身上，点出现的圆形「猫爪」按钮。
2. 在弹出的面板里打开「猫爪总开关」和「用户插件」开关。
3. 鼠标悬停「用户插件」那一行，点旁边出现的「管理面板」，插件管理就会在新窗口打开。

## 4. 想分享给别人

**路线 B**：在工作台你的项目页面点「打包下载」：

![工作台里的打包下载按钮](/images/plugins/onboarding/neko-studio-package.png)

1. 点「打包下载」得到一个 `.neko-plugin` 安装包。

**路线 A**：直接让 AI 帮你打包。

朋友拿到 `.neko-plugin` 文件后，在插件管理点「导入」，把文件拖进去就能用：

![插件管理页的导入按钮](/images/plugins/onboarding/neko-import.png)

![导入对话框里拖入插件包](/images/plugins/onboarding/neko-import-dialog.png)

1. 在插件管理页点「导入」。
2. 把 `.neko-plugin` 文件拖进框里，或点击选择文件。

想正式发布到插件市场，两条路线都可以，需要一个 GitHub 账号：让 AI 把项目上传到 GitHub 并发布版本；第一次投稿审核在[插件市场投稿页](https://market.project-neko.cn/#/upload)上点几下就行，完整流程见[发布插件](/zh-CN/plugins/cli)。路线 B 工作台里的一键发布在 Steam / 桌面版里用不了，交给 AI 做即可。

> 如果新建的项目里没有 AGENTS.md，说明你的 N.E.K.O 版本较旧，请先更新到最新版本再重试。

---

**已经会用源码版、想手动开发？** 阅读[插件开发入门文档](/zh-CN/plugins/plugin-development)或[快速开始](/zh-CN/plugins/quick-start)。
