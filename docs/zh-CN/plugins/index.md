# 插件系统概览

N.E.K.O. 插件系统是一个基于 Python 的插件框架，建立在**进程隔离**和**异步 IPC** 之上。平台只有两种包类型：产品功能使用 **Plugin（插件）**，外部协议桥接使用 **Adapter（适配器）**。原 **Extension（扩展）** 包类型已经移除；`PluginRouter` 仍可在普通 Plugin 内部使用。

## 架构

```
┌────────────────────────────────────────────────────┐
│              Main Process (Host)                   │
│  ┌──────────────────────────────────────────────┐  │
│  │   Plugin Host (core/)                        │  │
│  │   - Plugin lifecycle management              │  │
│  │   - Bus system (memory, events, messages)    │  │
│  │   - ZMQ IPC transport                        │  │
│  └──────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────┐  │
│  │   Plugin Server (server/)                    │  │
│  │   - HTTP API endpoints (FastAPI)             │  │
│  │   - Plugin registry                          │  │
│  │   - Message queue                            │  │
│  └──────────────────────────────────────────────┘  │
└────────────────────┬───────────────────────────────┘
                     │ ZMQ IPC
      ┌──────────────┼──────────────┐
      ▼              ▼              ▼
  Plugin A       Plugin B       Adapter D
  (process)      (process)      (process)
```

## 包类型

| 范式 | 导入来源 | 使用场景 | 运行方式 |
|------|----------|----------|----------|
| **Plugin** | `plugin.sdk.plugin` | 独立功能（搜索、提醒等） | 独立进程 |
| **Adapter** | `plugin.sdk.adapter` | 将外部协议（MCP、NoneBot）桥接到内部插件调用 | 独立进程，带网关管线 |

### 何时使用哪种范式？

- **"我想添加一个新的独立功能"** → 使用 **Plugin**
- **“我想在现有功能周围增加命令”** → 使用普通 **Plugin**；若你维护原宿主且代码很大，可在宿主内使用 `PluginRouter`
- **"我想接受 MCP/NoneBot/外部协议调用并将其路由到插件"** → 使用 **Adapter**

> 从 **Plugin** 开始。迁移原 Extension 时，把 Router 合并进所属 Plugin，或改造成独立 Plugin。

## 加载插件不等于选择运行时入口

不同层级都有一个叫 entry 的字段，但含义不同：

| 层级 | 声明 | 用途 |
|---|---|---|
| 宿主加载 | `[plugin].entry = "module.path:ClassName"` | 导入一个 `NekoPluginBase` 类并启动它的进程 |
| 运行时分派 | `@plugin_entry(id="search")` | 标识已加载插件中的一个可调用操作 |

用户插件的 Agent 分派采用两阶段选择。插件描述总量不超过配置阈值时跳过第一阶段；超过阈值时并行运行 BM25 和 LLM 粗筛，再与正则 `keywords` 命中项取并集。第二阶段读取候选插件的完整描述并返回 `plugin_id` 与运行时 `entry_id`。宿主严格按本轮实际候选校验两者，只带纠正提示重试一次，仍不合法就拒绝执行。

`passive = true` 的插件以及没有 Agent 可见入口的插件不会参与选择。这条路由链也不同于使用 `@llm_tool` 注册 LLM 工具。

## 主要特性

- **进程隔离** — Plugin 与 Adapter 均在独立进程中运行
- **异步支持** — 同时支持同步和异步入口点
- **Result 类型** — 使用 `Ok`/`Err` 进行类型安全的错误处理（正常流程中无异常）
- **钩子系统** — `@before_entry`、`@after_entry`、`@around_entry`、`@replace_entry` 实现 AOP
- **跨插件调用** — `self.plugins.call_entry("other_plugin:entry_id")` 实现插件间通信
- **系统信息** — `self.system_info` 查询宿主系统元数据
- **插件存储** — `PluginStore` 提供持久化键值存储
- **总线系统** — `self.bus` 通过 `messages`、`events`、`lifecycle`、`conversations`、`memory` 读取宿主状态；只有前三者支持 `watch()`，`conversations` 与 `memory` 是只读快照。可重放 watcher 链使用 `get()` → 结构化 `filter(field=value, ...)` → `sort(by=...)` → `limit()` → `watch()`，且只订阅 `add`、`del`、`change` 增量。它没有 publish/emit 接口。`self.bus.memory.get(...)` 读取有容量上限、只保存在内存中的近期用户话语事件（TTL 为一小时），并不是角色的持久记忆档案。`self.ctx.query_memory(...)` 只是已弃用的兼容调用，不提供语义召回。
- **动态入口** — 在运行时注册/注销入口点
- **Hosted UI** — 在插件管理器中构建 TSX 交互面板和 Markdown 教程页
- **静态 UI** — 从插件目录提供旧版 Web UI 服务
- **生命周期钩子** — `startup`、`shutdown`、`reload`、`freeze`、`unfreeze`、`config_change`
- **定时任务** — 使用 `@timer_interval` 实现周期性执行
- **消息处理器** — 响应来自宿主系统的消息

## 插件目录结构

```
plugin/plugins/
└── my_plugin/
    ├── __init__.py      # 插件代码（入口点）
    ├── plugin.toml      # 插件配置
    ├── config.json      # 可选：自定义配置
    ├── data/            # 可选：运行时数据目录
    ├── ui/              # 可选：Hosted TSX 面板
    ├── docs/            # 可选：Markdown 或 TSX 教程页
    ├── i18n/            # 可选：插件本地翻译
    └── static/          # 可选：旧版 Web UI 文件
```

## 快速链接

- [快速开始](./quick-start) — 5 分钟内创建你的第一个插件
- [v0.9 迁移](./migration-v0.9) — 已删除接口与准确替代方案
- [SDK 参考](./sdk-reference) — 基类、上下文 API、Result 类型
- [装饰器](./decorators) — 所有可用的装饰器
- [Hosted UI](./hosted-ui) — 构建 TSX 面板和 Markdown 教程页
- [示例](./examples) — 完整的可运行示例
- [高级主题](./advanced) — Router 组合、适配器、跨插件调用、钩子
- [LLM 工具调用](./tool-calling) — 注册插件功能给 LLM 在对话中调用
- [最佳实践](./best-practices) — 错误处理、测试、代码组织
