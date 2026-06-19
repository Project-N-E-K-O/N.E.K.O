# 猫娘锐评文档

> 同步提醒：涉及直播语境、开发者模式、NEKO 输出、沙盒、数据边界或 UI 流程的改动，需要同步检查 `../README.md`、`quickstart.md`、`development.md` 和 `../AGENTS.md`。当前实现会在插件启动时注入直播语境，开发者模式开启时叠加调试语境，关闭时发送恢复语境。

## 使用文档

- [快速开始](quickstart.md)：直播间配置、猫娘锐评、直播总结、观众档案、开发者沙盒和内置案例的基本使用方式。

## 开发文档

- [开发者指南（从这里开始）](developer-guide.md)：接手 / 参与开发的**单一入口**——心智模型、架构总览、加事件 handler 的核心契约、运行态、测试、红线、文档地图。**新人先读这份，再按需深入下面的参考文档。**
- [开发文档](development.md)：长期开发规范，包含当前实现快照、模块边界（含 `bili_live_ingest` 吞并的真实弹幕监听器）、pipeline、锐评生成（自适应焦点 + 头像 META）、安全测试态（dry-run）、限流（rate_limit_seconds）、直播事件中枢（`live_events` 窗口择优）、富模型弹幕解析、配置持久化与写竞争免疫、房号链接输入、直播间查询与 -352 风控、B 站登录态（P5）、安全门、输出边界、数据边界（观众档案本地 JSON 持久化）、UI 约定、i18n 和测试要求。
- [开发总结与路线图](live-center-roadmap.md)：整体定位、已完成进度（含真机验证）、关键决策与踩坑、运行态交互速查、后续路线与下一阶段 TODO。
- [UI 与模块贡献架构基线](ui-architecture.md)：面板的生命周期-域导航、模块贡献模型（`config_schema`）、五层兜底契约和宿主 hosted-ui 约束，多人开发的共享基线。
- [开发日志](devlog.md)：开发中发现的**宿主 / SDK 侧**历史问题（store.enabled 构造期冻结、插件数据不跟随 selected_root 等）、当前修复状态及插件侧保留的兼容取舍。
- [AI/IDE 开发规则](../AGENTS.md)：面向 IDE agent 和后续贡献者的硬性维护规则。

## 维护约定

相关变更必须同步检查这些文档，不要只更新其中一份：

- `../README.md`：插件门牌，只放简介、文档入口、当前重点、沙盒数据规则和当前不做。
- `quickstart.md`：主播或开发者实际使用流程变化时更新。
- `development.md`：长期开发规范和当前实现快照；模块边界、pipeline、锐评焦点规则、头像 META、安全门、输出边界、数据边界、UI、i18n、测试结果、沙盒语义和后续接入点变化时更新。
- `../AGENTS.md`：AI/IDE agent 的硬性维护规则变化时更新。

新增 UI 文案时同步更新 8 个 locale 文件。Python 命令统一使用 `uv run`。猫猫输出和锐评语境注入统一走 `adapters/neko_dispatcher.py`。真实 B 站直播接入优先扩展 `modules/bili_live_ingest`，不要直接拷旧插件大文件。

头像视觉输入也必须走 `adapters/neko_dispatcher.py`，并经 dispatcher 压缩。超过 ingest 的 256KB payload 上限仍会被丢弃，所以过大时应降级为纯文字锐评。（曾有一个传输层 bug 让任何带图 `push_message` 在 PUB 序列化阶段被静默丢弃——已在 `plugin/message_plane/pub_server.py` 修复，详见 `development.md` 的 Message Plane 预算。）
