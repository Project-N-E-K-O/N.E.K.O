---
title: 隐私政策
description: Project N.E.K.O. 文档站如何处理可选分析统计与访问者的隐私选择。
seoSchemaType: WebPage
editLink: false
lastUpdated: false
---

# 隐私政策

本政策适用于 Project N.E.K.O. 文档站。

## 你的选择

分析统计由你选择是否启用。在你允许之前，网站不会加载 Google Analytics；拒绝分析统计也不会影响文档的正常访问。

网站不会加载 Google Analytics，也不会向 Google Analytics 发送请求。在你点击**允许**或**拒绝**之前，同意横幅不会保存选择。

## 启用分析统计后使用的信息

允许分析统计后，网站会使用 Measurement ID `G-N4QZK4PHE3` 加载 Google Analytics 4，并发送页面浏览事件，以便了解哪些文档更有帮助以及访问者如何找到文档站。当访客点击前往 N.E.K.O. Steam 页面的链接时，网站还会发送 `steam_cta_click` 事件，其中包含清理后的目标网址、CTA 位置、清理后的当前页面网址和页面标题。

Google Analytics 可能处理页面网址和标题、来源页面、浏览器与设备信息及大致地理位置等信息。本站配置会关闭广告存储、广告用户数据、广告个性化、Google Signals 和广告个性化信号。

这些信息仅用于汇总报告和改进文档。广告跟踪与广告个性化始终保持关闭。

## 信息如何处理

Google Analytics 代表网站处理分析信息。文档站不会有意通过分析统计发送账号凭据、私人消息、表单内容或其他敏感信息。

发送分析事件之前，页面网址只会保留获准的 `utm_source`、`utm_medium`、`utm_campaign`、`utm_content` 和 `utm_term` 活动参数，且每个值最长 100 个字符。其他查询参数和网址片段会被移除；发送 Steam 目标网址时也不会包含查询参数或网址片段。

浏览器会在本地存储的 `neko.docs.analytics-consent.v1` 项中保存选择，其中只包含同意或拒绝、格式版本及保存时间。选择会在 180 天后过期，届时网站会再次询问。

对于 GA4“数据保留”设置所涵盖的用户级和事件级数据，保留时间最长为 14 个月；媒体资源管理员可将其缩短为 2 个月。该设置不影响汇总的标准报告。详见 [Google Analytics 数据保留说明](https://support.google.com/analytics/answer/7667196?hl=zh-Hans)。

网站可能依赖外部服务进行托管，也可能打开 Steam 等外部页面。这些服务会按照各自的政策处理信息。

## 修改或撤回选择

使用每个文档页面底部常驻的 **Cookie 设置**入口，即可随时允许或拒绝分析统计。如果你撤回此前授予的同意，网站会把分析统计权限改为拒绝，尝试删除脚本可访问的 `_ga` Cookie，并在重新加载页面后保持 Google Tag 不加载。你也可以通过浏览器清除本站保存的数据，这会重置已保存的选择。拒绝分析统计不会影响文档的正常使用。

## 联系方式

如有隐私问题，请通过 [Project N.E.K.O. GitHub 仓库](https://github.com/Project-N-E-K-O/N.E.K.O/issues)联系项目，并避免公开提交敏感信息。
