# NEKO Live 开发者指南

这是一份上手导览，不是完整规格。先读完本文建立心智模型，再按 [文档索引](README.md) 深入对应主题。

## 这是什么

NEKO Live 是 N.E.K.O 的直播互动中心。它连接直播平台事件，选择值得回应的内容，经统一安全链路让猫猫发言，并向主播展示可解释的运行状态。

“新观众头像锐评”只是一个互动模块，不代表整个产品。当前还包含普通弹幕接话、礼物 / SC / 舰长短句致谢、暖场、冷场陪播、主动营业、本场统计和观众档案。

## 记住五条不变量

1. 所有直播输入先归一为 provider-neutral 事件，再进入 EventBus。
2. 直播输入与开发者沙盒共用 `core/pipeline.py` 和 `core/safety_guard.py`。
3. 所有猫猫输出只走 `adapters/neko_dispatcher.py`，模块不得直接调用 `push_message`。
4. 观众档案与审计分别只走 `stores/viewer_store.py` 和 `stores/audit_store.py`。
5. 登录凭据只走 `stores/credential_store.py` 加密保存，不进入配置、日志、审计、UI 或事件 raw 字段。

## 一次事件如何流动

```text
Provider / Sandbox
  -> normalize
  -> EventBus / Selection
  -> Pipeline
  -> PermissionGate + SafetyGuard
  -> NekoDispatcher
  -> N.E.K.O output
  -> Dashboard / audit projection
```

不是每个事件都会让猫猫开口。低价值、重复、冷却中、不可信或会挤占更高优先级事件的输入应被跳过，并留下稳定的运行态解释。

## 目录入口

```text
neko_live/
├─ __init__.py       插件入口与宿主 action
├─ core/             contracts、pipeline、runtime、安全门、状态与导播逻辑
├─ modules/          平台接入和可独立维护的直播能力
├─ adapters/         登录服务、唯一输出适配器
├─ stores/           观众档案、审计、凭据和头像缓存
├─ ui/               Hosted UI 源码与单文件兼容入口
├─ tools/            监控与压力工具
├─ tests/            插件测试
└─ docs/             本文档集
```

`core/` 已按职责拆成小文件。新增能力时先找现有 subsystem 的公开入口，不要因为文件多就重新建立第二套 facade、状态或配置路径。

## 最常见的扩展方式

新增直播事件能力时：

1. 在 `modules/<module_id>/` 实现模块。
2. 在 `setup()` 订阅 `ctx.event_bus.subscribe(type, handler, owner=self.id)`，在 `teardown()` 取消订阅。
3. handler 只构造安全、归一化的 payload，并交给现有 pipeline。
4. 需要配置时声明真实有消费者的 `config_schema()`；不要预建未来字段。
5. 写对应模块测试和 `docs/modules/<module_id>.md`。

`modules/live_events` 与 `modules/live_support_events` 是事件选择和支持事件处理的参考实现。

## UI 心智模型

普通主播只需要四个一级页面：控制台、直播间互动、观众、设置。开发者模式开启后才显示开发者工具。

- `ui/panel.tsx` 等文件是可维护源码。
- `ui/panel_compat.tsx` 是插件 manifest 实际加载的完整单文件入口。
- 修改模块化源码后必须同步兼容入口，不能只改其中一份。
- 新增用户可见文案必须同步 8 个 locale。
- 面板只读宿主投影并调用已声明 action；不得直接读凭据或 store 文件。

完整约束见 [UI 架构](ui-architecture.md)。

## 本地验证

从仓库根目录运行：

```powershell
uv run pytest plugin/plugins/neko_live/tests -q
uv run python -m plugin.neko_plugin_cli.cli check plugin/plugins/neko_live
git diff --check
```

只改文档时可以不跑代码测试，但仍应检查链接、Markdown 和 `git diff --check`。不要在长期文档中硬编码“当前共有多少测试”；以 CI 与本次 PR 记录为准。

## 下一步阅读

- 写代码前读 [开发规范](development.md)。
- 改面板读 [UI 架构](ui-architecture.md)。
- 改状态、audit 或 monitor 读 [Runtime Observability](runtime-observability.md)。
- 选任务和提 PR 读 [贡献指南](contributing.md)。
- 判断当前是否该做某项能力读 [路线图](live-center-roadmap.md)。
