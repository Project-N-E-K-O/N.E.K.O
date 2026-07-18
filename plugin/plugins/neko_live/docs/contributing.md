# 为 NEKO Live 贡献代码

本文是短路径。架构和测试细节以 [开发规范](development.md) 为准。

## 选择任务

适合第一次贡献：

- 文档、fixture、测试和 i18n 同步；
- 只读 Dashboard 展示；
- 非核心模块的小修复；
- 已有 `config_schema()` 的安全扩展。

需要核心维护者重点 review：

- 事件 schema、provider 接入与协议解析；
- `live_events` 选择策略与 `live_support_events` 调度；
- pipeline、SafetyGuard、Dispatcher；
- runtime、持久化、凭据和隐私；
- Hosted UI 外壳与跨页面状态。

## 分支与 PR

- 从最新 `main` 或明确指定的发布分支创建独立分支。
- 一个 PR 只做一个可审查的 Slice，或单一 docs / tests / refactor 目的。
- 默认控制在 20 个文件以内；超过时在 PR 描述解释原因。
- 禁止堆叠式 PR：开放中的 PR 不得依赖另一个未合并 PR 才能正确测试、合并或回滚。
- 有前置依赖时，先合并前置 PR，再从更新后的目标分支创建下一分支。
- 不把功能、无关清理、面板重写、宿主修改和旧插件删除混在一起。

## 开始修改前

1. 确认工作区没有不属于本任务的改动。
2. 确认 PR head 仓库、分支、写权限和本地 upstream。
3. 找到对应权威文档与受保护模块。
4. 对 CPU、内存、网络轮询、token、依赖、存储或核心复杂度有成本的方案，先列选择、预算、降级与回滚，等维护者拍板。

## 完成标准

- 保持 EventBus、Pipeline、SafetyGuard、Dispatcher 与 Store 边界。
- 新事件有稳定的 stage、outcome 和必要的 skip reason。
- 不记录 raw 弹幕、cookie、token、头像 bytes/base64 或其他敏感 payload。
- 新 UI 文案同步 8 个 locale。
- 新模块或重要流程有对应开发文档。
- 运行与改动风险相称的测试和插件 CLI check。
- PR 描述写清行为变化、验证、风险、回滚和未做事项。

自动 reviewer 的意见需要结合代码独立判断：接受、部分接受或拒绝都要在原 thread 给出依据，不使用自动修复代替人工核对。
