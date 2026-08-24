---
title: N.E.K.O 能完全离线运行吗，哪些组件仍需要联网？
description: 了解 Project N.E.K.O 哪些组件在本地存储或运行、哪些功能会连接远端服务，以及为什么 OmniOfflineClient 不代表断网模式。
seoSchemaType: WebPage
---

# N.E.K.O 能完全离线运行吗，哪些组件仍需要联网？

N.E.K.O 目前不是开箱即用的完全离线产品。界面和默认记忆存储在本机，部分组件可以自托管；但常用的免费路径，以及许多模型、语音、Steam、Cloud、Workshop、在线内容、浏览器和 Agent 功能仍需要网络。

事实最后核验于 **2026-07-23**。

## 四个不能混为一谈的概念

| 概念 | 本文中的含义 |
|---|---|
| 本地存储 | 数据写入用户设备上的文件或数据库 |
| 本地推理 | 模型在用户自己的硬件上执行 |
| 非 Realtime 客户端 | 文本通过普通请求/响应 API 处理，而不是 Realtime 会话 |
| 完全离线 | 预期工作流在不连接任何外部端点时仍能继续 |

内部名称 `OmniOfflineClient` 指的是**非 Realtime 的文本 Chat Completions 路径**。它仍会调用配置的模型端点，因此不能作为“完全离线”的证据。

## 逐组件联网矩阵

| 组件 | 默认或常见位置 | 是否可能联网 | 本地选项或限制 |
|---|---|---:|---|
| 主界面与 Avatar 运行时 | 本机 | 有时 | 渲染在本地；联网功能仍会发起请求 |
| 角色记忆文件 | 本机 | 处理时可能 | 默认本地存储，但摘要和抽取可能使用配置的 Provider |
| BM25 记忆召回 | 本地进程 | 排序不要求 Provider | Vector 不可用时仍可继续 |
| 可选记忆 Embedding | 本地 CPU ONNX | 通常不需要 | 只有 Embedding 阶段在本地，不会让所有记忆 LLM 任务本地化 |
| Core 与 Assist 模型 | 常见配置为远端 | 是 | 部分组件可以配置兼容的自托管端点 |
| 随附的免费配置 | Project N.E.K.O 托管的远端服务 | 是 | 不需要用户 API Key，但不是离线服务 |
| ASR、TTS 与声音注册 | 取决于 Provider | 经常需要 | 存在部分本地 TTS 或 vLLM-Omni 路径，要求各不相同 |
| Steam、Workshop 与 Steam Cloud | Steam 服务 | 是 | 断网时不可用 |
| 浏览器、Feed、趋势与在线内容 | 外部来源 | 是 | 没有网络就无法获取当前外部内容 |
| 远端 Agent 渠道 | 取决于渠道 | 经常需要 | Computer Use 可以在本机执行动作，但判断和模型调用仍可能在远端 |

## 默认保留在本机的内容

- 主 Web 界面运行在本地主服务器上。
- 角色记忆存放在配置的每角色记忆目录中。
- Recent、Facts、Reflections、Persona、Journal 和恢复状态默认是本地文件或数据库，除非用户主动调用同步或导出路径。
- 没有 Vector 推理时，BM25 检索仍可工作。
- 可选 Embedding 推理使用本地 CPU ONNX Execution Provider。
- 用户导入的 Avatar 素材可以在导入后于本地渲染。

本地存储本身不能决定模型处理发生在哪里。

## 通常可能离开设备的内容

- 发给所选聊天或 Realtime Provider 的对话输入；
- 摘要、抽取、反思、晋升、审阅或纠错任务使用的相关对话或记忆文本；
- 作为工具结果返回给当前对话 Provider 的召回记忆片段；
- 经当前 N.E.K.O 服务路径转发的免费 API 请求；
- 发给所选云端 ASR、TTS 或声音服务的语音样本或文本；
- 用户主动触发的 Steam Cloud 或 Workshop 内容；
- 在线 Feed、浏览器请求和远端 Agent 工作。

数据流视角请参阅 [N.E.K.O 会把对话和记忆发送到哪里？](./data-and-privacy)。

## 更本地化的配置需要什么

更本地化的方案需要逐个组件搭建：

1. 选择兼容的本地或自托管对话端点；
2. 验证所需的每个角色，而不仅是文本聊天，是否都受支持；
3. 在可用时配置本地语音组件；
4. 保持可选 Embedding 在本地执行；
5. 如果 Steam Cloud、Workshop、在线 Feed、浏览器工作和远端 Agent 渠道超出你的边界，就禁用或避开它们；
6. 阻断出站网络后实测应用，并记录哪些功能会降级。

Project N.E.K.O 当前没有一个经过验证、可以一次完成上述工作的“一键离线模式”。

## 断网时会发生什么

结果取决于具体配置。本地渲染和已存储文件可能仍然可用，而远端对话、免费配置、在线语音、Workshop、Cloud、Feed 和远端 Agent 渠道可能失败或不可用。不能因为某个组件名称里含有“local”或“offline”，就把它当成整个系统的保证。

> 能接受这些联网边界？[前往 Steam 查看 N.E.K.O.](https://store.steampowered.com/app/4099310/__NEKO/?utm_source=project-neko.online&utm_medium=referral&utm_campaign=buyer_guides&utm_content=offline_footer_zh_cn)。如果你的本地化要求更严格，请从源码配置并逐项验证组件。

## 相关技术文档

- [记忆系统](/zh-CN/architecture/memory-system)
- [API Providers](/zh-CN/config/api-providers)
- [TTS Client](/zh-CN/modules/tts-client)
- [TTS 流水线](/zh-CN/architecture/tts-pipeline)
- [部署概览](/zh-CN/deployment/)
- [费用与 Provider 选择](./cost-and-providers)
