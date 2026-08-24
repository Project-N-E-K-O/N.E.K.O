---
title: N.E.K.O 免费吗，使用 AI API 还会产生哪些费用？
description: 了解 Project N.E.K.O 哪些部分当前免费、何时需要 API Key 或付费 Provider，以及免费远端路径与本地或自费配置的区别。
seoSchemaType: WebPage
---

# N.E.K.O 免费吗，使用 AI API 还会产生哪些费用？

N.E.K.O 基础应用目前可在 Steam 免费使用，项目代码采用 Apache License 2.0；但 AI 模型、语音及其他第三方服务可能另有费用、额度和使用条款。

事实最后核验于 **2026-07-23**。价格、额度、模型和 Provider 可用性都可能变化；做购买决定前，请查看相应服务的最新页面。

## “免费”具体指什么

| 项目 | 当前情况 | 仍可能产生的费用 |
|---|---|---|
| Steam 基础应用 | 免费，处于抢先体验阶段 | 后续发行条款可能变化，请以 Steam 页面为准 |
| 项目源代码 | Apache License 2.0 | 第三方依赖、素材、商标和服务各自适用独立条款 |
| 内置免费 Provider 路径 | 随附的免费配置不要求用户填写 API Key | 它是远端服务，可用性和额度可以调整 |
| 自带 Provider API Key | 由你选择并承担 Provider 账户费用 | Token、Realtime、语音、图像或其他服务费用 |
| 语音克隆与 TTS | 存在多种云端和本地服务路径 | 云 Provider 可能要求 Key、账户或付费额度 |
| Steam Cloud 与 Workshop | 可通过受支持的 Steam 功能使用 | 需要联网和相应的 Steam 账户 |

“开源”和“应用免费”并不代表每个模型、音色、角色素材或托管服务都由 Project N.E.K.O 免费提供或授权。

## 使用 AI 服务的三种常见方式

### 1. 使用随附的免费路径

当前配置包含不要求用户填写 API Key 的免费 Core 和 Assist 配置。这些配置会连接 Project N.E.K.O 的远端服务，**不是本地模型，也不是离线模式**。

当前 [Steam EULA](https://store.steampowered.com/eula/4099310_eula_0) 说明，免费 API 请求可能经 N.E.K.O 服务器转发给服务合作方，免费服务额度也可能按运营需要调整。因此，本页不把某个每日额度写成长期承诺。

### 2. 使用自己的 API Key

你可以使用自己的账户和凭据配置受支持的 Provider。此时：

- 价格、速率限制、地区可用性和数据保留条款由 Provider 决定；
- N.E.K.O 的不同功能可能使用不同 Provider 角色；
- 支持文本聊天不等于同时支持 Realtime 语音、视觉、ASR、TTS 或 Agent；
- 更换模型可能同时改变质量和费用。

按照当前 Steam EULA，使用付费 Provider API 时，输入会从设备发送给你所选择的 Provider。请同时查看该 Provider 的最新条款。

### 3. 配置本地或自托管组件

部分组件可以使用本地或自托管服务，例如可选的本地 Embedding，以及部分语音或 vLLM-Omni 路径。这能减少对托管 API 的依赖，但不是一个统一开关，通常还需要硬件、模型资源和额外配置。

在把本地配置理解为“零费用”或“完全断网”前，请先阅读 [N.E.K.O 能完全离线运行吗？](./local-and-offline)。

## 为什么本页不公布固定 Provider 数量

Provider 定义由数据驱动，而且以下类别会分别变化：

- 主要对话与 Realtime 配置；
- 用于文本、视觉、摘要、纠错或 Agent 的 Assist 配置；
- ASR、TTS、语音克隆等功能专用注册表；
- 地区、账户套餐和软件版本。

把这些类别合并成“支持 N+ 个 Provider”很容易过期。配置行为请查看当前 [API Provider 参考](/zh-CN/config/api-providers)，并以你正在运行的版本中实际可见的 Provider 为准。

## 应该怎样选择

| 你的优先目标 | 建议起点 |
|---|---|
| 尽量少配置，先体验 N.E.K.O | 先使用当前可用的免费配置，同时接受它需要联网且额度可调整 |
| 自己控制模型和账单 | 配置你自己的受支持 Provider Key |
| 减少外部处理 | 按组件逐项评估本地或自托管方案 |
| 预估每月费用 | 查看 Provider 的用量面板和最新价格表；费率并非由 N.E.K.O 制定 |
| 避免意外的数据流向 | 阅读 [N.E.K.O 会把对话和记忆发送到哪里？](./data-and-privacy) |

> 想先体验免费本体，再决定使用哪个 Provider？[前往 Steam 查看 N.E.K.O.](https://store.steampowered.com/app/4099310/__NEKO/?utm_source=project-neko.online&utm_medium=referral&utm_campaign=buyer_guides&utm_content=cost_footer_zh_cn)，然后再回到本页比较不同 API 路径。

## 相关技术文档

- [API Providers](/zh-CN/config/api-providers)
- [模型配置](/zh-CN/config/model-config)
- [TTS Client](/zh-CN/modules/tts-client)
- [本地与离线边界](./local-and-offline)
- [Steam 商店页面](https://store.steampowered.com/app/4099310/__NEKO/?utm_source=project-neko.online&utm_medium=referral&utm_campaign=buyer_guides&utm_content=cost_source_zh_cn)
- [Steam EULA](https://store.steampowered.com/eula/4099310_eula_0)
