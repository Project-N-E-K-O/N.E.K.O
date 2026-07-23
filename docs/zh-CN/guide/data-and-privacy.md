---
title: N.E.K.O 会把对话和记忆发送到哪里？
description: 从技术数据流角度了解 Project N.E.K.O 的本地记忆、AI Provider、免费 API 转发、遥测、主动视觉、Steam Cloud 与 Workshop。
seoSchemaType: WebPage
---

# N.E.K.O 会把对话和记忆发送到哪里？

N.E.K.O 默认把角色记忆存储在本机；但当你使用模型 Provider、记忆处理任务、免费 API 路径、云语音、Steam Cloud、Workshop、在线 Feed、浏览器功能或远端 Agent 渠道时，相关内容可能离开设备。

事实最后核验于 **2026-07-23**。

::: warning 技术数据流说明
本页解释当前实现和官方发行声明，不替代针对具体司法辖区的隐私政策、Provider 协议或法律审查。
:::

## 数据流概览

```text
用户输入
├── 本地对话与每角色记忆存储
├── 所选对话或 Realtime Provider
├── 可选的记忆处理 Provider
├── 免费 API 转发路径
├── 可选遥测
├── 可选的主动屏幕上下文
├── 用户触发的 Steam Cloud 或 Workshop 操作
└── 在线内容、浏览器、语音或 Agent 服务
```

## 每条路径会发生什么

| 路径 | 涉及的数据 | 目的地 | 重要边界 |
|---|---|---|---|
| 角色记忆存储 | Recent、Facts、Reflections、Persona、Journal 与索引 | 配置的本地记忆目录 | 本地存储不代表所有处理都在本地 |
| 对话 Provider | 当前 Prompt、对话上下文、附加输入 | 所选模型 Provider | 适用 Provider 的条款、保留规则、地区和账户套餐 |
| 记忆维护 | 相关对话或记忆文本 | 配置的摘要、抽取或纠错 Provider | 仅在相应任务运行时使用，但可能含用户内容 |
| 显式记忆召回 | 选中的召回片段 | 作为工具输出返回当前对话 Provider | 不会自动发送完整记忆数据库 |
| 随附免费 API 路径 | 完成免费请求所需的输入 | N.E.K.O 转发服务及服务合作方 | 当前 Steam EULA 将它与自带付费 API Key 的路径分开说明 |
| 遥测 | 下文所列的使用与运行元数据 | N.E.K.O 遥测服务 | 可以通过环境变量关闭 |
| 主动视觉 | 已启用功能所需的屏幕流或截图 | 本地流水线及配置的视觉/模型路径 | 隐私模式停止主动查看；手动截图是另一条路径 |
| Steam Cloud | 白名单内的角色设置和记忆文件 | Steam Auto Cloud | 快照不是完整记忆目录备份 |
| Workshop 发布 | 用户选择的角色卡、受支持模型文件、预览图和可选参考语音 | Steam Workshop | 是否发布以及素材授权由用户决定 |
| DEBUG 诊断 | 部分调试路径中的查询或工具参数 | 默认在本地日志中，除非用户主动分享 | 不能假设所有日志都不含内容 |

## 免费 API 与自己的 Provider Key

当前 [Steam EULA](https://store.steampowered.com/eula/4099310_eula_0) 说明了两条相关路径：

- 使用付费 Provider API 时，输入从设备发送到所选择的 Provider；
- 使用免费 API 服务时，输入可能经 N.E.K.O 服务器转发给服务合作方。

所选 Provider 是独立服务方，适用其自己的条款。配置中存在某个 Provider 条目，不代表所有 Provider 都具有相同的数据保留或隐私行为。

## 本地记忆与远端处理

记忆系统为每个角色维护 Recent、Facts、Reflections 和 Persona 等独立层，并使用本地时序数据库作为底层记录。多类维护任务可能调用 LLM：

- Recent 历史压缩与审阅；
- Fact 抽取与纠错；
- Reflection 综合与晋升；
- Persona 合并与矛盾处理；
- 把显式召回结果返回给当前对话模型。

可选 Embedding 推理使用本地 CPU ONNX，但这不会自动把基于 LLM 的维护任务变成本地任务。当前运行时约定见 [记忆系统](/zh-CN/architecture/memory-system)。

## 遥测

仓库 README 说明遥测默认开启，并收集以下运行类别：

- 模型、调用类型；
- Token、请求与错误次数；
- 应用版本、实验信息、Locale、时区和发行渠道；
- 假名化设备标识；在适用的 Steam 环境中还可能包含 Steam 数字 ID。

README 同时说明，原始对话文本、语音、图片、API Key、邮箱和电话号码不是遥测 Payload。实现和 README 必须持续保持一致。

可通过以下任一环境变量退出：

```text
DO_NOT_TRACK=1
```

或：

```text
NEKO_DO_NOT_TRACK=1
```

## 屏幕与主动视觉控制

隐私模式会停止主动视觉并释放其屏幕流。它不表示手动截图或用户主动发起的屏幕共享在技术上完全不可能。不同发行版或地区的首次运行状态可能不同，因此应核对当前设置，而不是假设一个全局默认值。

Agent 和插件能力还有各自的启用与就绪控制。参阅 [Agent 系统](/zh-CN/architecture/agent-system)；任务 HUD 的完整技术页目前仅有[英文版](/architecture/task-hud-system)。

## Steam Cloud 只是部分角色快照

Cloud Save 通过 Steam Auto Cloud 上传或下载一个角色单元。白名单包含 Recent、Facts、Persona、Reflections 和 `time_indexed.db` 等常用平面文件，但不包括当前的分片 Archive、部分元数据、恢复 Journal 和 SQLite Sidecar。

下载操作可能替换本地同名角色数据，因此包含确认、活动会话处理和本地操作备份。把它称为完整备份或完整迁移方案前，请阅读 [Cloud Save API](/zh-CN/api/rest/cloudsave)。

## 当前可用的控制

| 控制 | 它能做到什么 | 它不能证明什么 |
|---|---|---|
| 选择 Provider | 改变接收对应请求的服务 | 其他每项功能都使用同一 Provider |
| 关闭遥测 | 停止项目自身的遥测路径 | 第三方 Provider 不会收到任何请求 |
| 开启隐私模式 | 停止主动屏幕查看 | 手动截图不可能被请求 |
| 关闭 Agent 渠道 | 阻止这些渠道分发任务 | 对话或记忆 Provider 已经本地化 |
| 不使用 Cloud / Workshop | 避免这些可选传输路径 | 模型 API 已经离线 |
| 删除当前角色 | 删除当前运行时的角色记忆路径 | 所有历史 Legacy 目录或远端 Provider 副本都被删除 |

> 接受这些数据流边界后想亲自体验？[前往 Steam 查看 N.E.K.O.](https://store.steampowered.com/app/4099310/__NEKO/?utm_source=project-neko.online&utm_medium=referral&utm_campaign=buyer_guides&utm_content=privacy_footer_zh_cn)。发送个人内容前，也请阅读所选 AI Provider 的条款。

## 相关文档与来源

- [记忆系统](/zh-CN/architecture/memory-system)
- [Cloud Save API](/zh-CN/api/rest/cloudsave)
- [Agent 系统](/zh-CN/architecture/agent-system)
- [本地与离线边界](./local-and-offline)
- [费用与 Provider 选择](./cost-and-providers)
- [Steam EULA](https://store.steampowered.com/eula/4099310_eula_0)
- [项目仓库](https://github.com/Project-N-E-K-O/N.E.K.O)
