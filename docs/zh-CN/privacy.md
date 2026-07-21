---
title: 分析统计与 Cookie 说明
description: Project N.E.K.O. 文档站如何在用户自愿同意后使用 Google Analytics、保存选择并支持随时撤回。
seoSchemaType: WebPage
---

# 分析统计与 Cookie 说明

最后更新：2026 年 7 月 21 日。

本说明适用于 `project-neko.online` 上的 Project N.E.K.O. 文档站。

## 在你作出选择之前

网站不会加载 Google Analytics，也不会向 Google Analytics 发送请求。在你点击**同意分析统计**或**拒绝**之前，同意面板不会保存选择。

## 同意分析统计后

网站会使用 Measurement ID `G-N4QZK4PHE3` 加载 Google Analytics 4，并发送页面浏览事件，以便了解哪些文档更有帮助以及访问者如何找到文档站。

Google Analytics 可能处理页面网址和标题、来源页面、浏览器与设备信息及大致地理位置等信息。本站配置会关闭广告存储、广告用户数据、广告个性化、Google Signals 和广告个性化信号。

你同意后，Google Analytics 可能设置 `_ga`、`_ga_<measurement-id>` 等第一方 Cookie。Google 对 Cookie 和数据收集的说明见：

- [Google Analytics Cookie 使用说明](https://support.google.com/analytics/answer/11397207?hl=zh-Hans)
- [Google Analytics 数据收集说明](https://support.google.com/analytics/answer/11593727?hl=zh-Hans)
- [Google 隐私权政策](https://policies.google.com/privacy?hl=zh-CN)

## 如何保存你的选择

浏览器会在本地存储的 `neko.docs.analytics-consent.v1` 项中保存选择，其中只包含同意或拒绝、格式版本及保存时间。选择会在 180 天后过期，届时网站会再次询问。

拒绝分析统计不会加载 Google Tag。如果你撤回此前授予的同意，网站会把分析统计权限改为拒绝，尝试删除脚本可访问的 `_ga` Cookie，并在重新加载页面后保持 Google Tag 不加载。

## 修改或撤回选择

使用任意文档页面底部的**分析统计设置**按钮，即可随时同意或拒绝。拒绝分析统计不会影响文档的正常使用。

如对本说明有疑问，请通过 [Project N.E.K.O. GitHub 仓库](https://github.com/Project-N-E-K-O/N.E.K.O/issues)联系项目。
