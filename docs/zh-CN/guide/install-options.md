---
title: 应该从 Steam、GitHub Releases 还是源码安装 N.E.K.O？
description: 对比 Project N.E.K.O 的 Steam、GitHub Release 与源码安装路径，包括平台范围、更新预期、限制和适合人群。
seoSchemaType: WebPage
---

# 应该从 Steam、GitHub Releases 还是源码安装 N.E.K.O？

普通用户优先选择 Steam；需要 Linux 等独立发行资产时查看 GitHub Releases；需要开发、集成或深度定制时再从源码运行。

事实最后核验于 **2026-07-23**。核验时最新稳定 GitHub Release 为 **v0.8.3**；请始终查看当前 Release 页面，不要把该版本号视为长期固定信息。

## 安装渠道对照

| 渠道 | 核验时可见平台 | 最适合 | 重要限制 |
|---|---|---|---|
| [Steam](https://store.steampowered.com/app/4099310/__NEKO/?utm_source=project-neko.online&utm_medium=referral&utm_campaign=buyer_guides&utm_content=install_table_zh_cn) | Windows、macOS | 希望简化安装并使用 Workshop、成就和 Steam Cloud 的普通用户 | 处于抢先体验阶段；平台支持以当前 Steam 页面为准 |
| [GitHub Releases](https://github.com/Project-N-E-K-O/N.E.K.O/releases) | v0.8.3 提供 Windows、Linux 与 macOS arm64 资产 | 需要独立发行包或 Linux 包的用户 | 每个 Release 的资产名称和平台范围可能不同 |
| 源码 Checkout | 取决于当前依赖和已测试环境 | 贡献者、集成者和自定义部署者 | 需要 Python 3.11、`uv`、兼容的 Node 工具链和手动配置 |
| Nightly 预发布 | 取决于当次 Workflow | 测试最新改动 | 不代表稳定版承诺 |

项目中存在 Windows、macOS 和 Linux 构建资产，不代表同一个下载 URL 可以覆盖所有平台。尤其不能把 Steam URL 当成 Linux 下载 URL。

## 什么时候选择 Steam

- 希望获得常规桌面安装体验；
- 需要 Steam Workshop、成就或 Steam Cloud；
- 你的平台列在当前 Steam 商店页面中；
- 接受产品仍处于抢先体验阶段。

Steam 基础应用目前免费。AI Provider 的费用和条款仍需另行考虑，参阅 [N.E.K.O 免费吗？](./cost-and-providers)。

## 什么时候选择 GitHub Releases

- 需要 Steam 之外发布的独立资产；
- 需要当前 Release 提供的 Linux AppImage 或 tar 包；
- 需要查看 Release Notes 和准确文件名；
- 希望独立于 Steam 发行渠道测试某个版本。

在 2026-07-23 核验时，[v0.8.3](https://github.com/Project-N-E-K-O/N.E.K.O/releases/tag/v0.8.3) 包含：

```text
N.E.K.O_0.8.3.1_win.zip
N.E.K.O_0.8.3_win.zip
N.E.K.O_0.8.3_linux.AppImage
N.E.K.O_0.8.3_linux.tar.gz
N.E.K.O_0.8.3_mac_arm64.zip
```

这只是该版本的历史证据，不保证后续版本继续提供相同资产。

## 什么时候从源码运行

- 准备贡献代码或文档；
- 需要检查或修改模型、记忆、Agent、插件或部署行为；
- 正在构建自定义本地或服务器部署；
- 能够维护所需开发工具。

当前源码开发需要：

- Python **3.11**；
- 使用 [`uv`](https://docs.astral.sh/uv/) 管理 Python 环境和执行命令；
- 与仓库 Lockfile 兼容的 Node；Plugin Manager 当前要求 `^20.19.0 || >=22.12.0`；
- 你所启用功能对应的平台依赖。

请从[前置条件](./prerequisites)、[开发环境搭建](./dev-setup)和[快速开始](./quick-start)开始。

## 稳定版与 Nightly 输出的区别

跨平台 Workflow 可以生成 Windows、macOS 和 Linux 输出。定时任务的输出属于 **Nightly 预发布版本**。成功生成的 Nightly 资产适合测试，但不能描述成最新稳定版或长期支持包。

## 安装前检查

1. 在所选渠道确认平台和 CPU 架构。
2. 阅读当前 Release Notes 或 Steam 抢先体验说明。
3. 决定使用免费远端配置、自己的 Provider Key，还是本地组件。
4. 阅读[本地与离线边界](./local-and-offline)。
5. 阅读[技术数据流与隐私控制](./data-and-privacy)。

> 准备使用标准桌面安装路径？[从 Steam 安装 N.E.K.O.](https://store.steampowered.com/app/4099310/__NEKO/?utm_source=project-neko.online&utm_medium=referral&utm_campaign=buyer_guides&utm_content=install_footer_zh_cn)；如果 Steam 不适合你的平台，请使用上面的其他渠道。

## 相关文档

- [前置条件](./prerequisites)
- [开发环境搭建](./dev-setup)
- [快速开始](./quick-start)
- [部署概览](/zh-CN/deployment/)
- [GitHub Releases](https://github.com/Project-N-E-K-O/N.E.K.O/releases)
- [Steam 商店页面](https://store.steampowered.com/app/4099310/__NEKO/?utm_source=project-neko.online&utm_medium=referral&utm_campaign=buyer_guides&utm_content=install_source_zh_cn)
