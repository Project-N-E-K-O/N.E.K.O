# 插件系统概览

N.E.K.O. 插件系统是一个基于 Python 的插件框架，建立在**进程隔离**和**异步 IPC** 之上。它支持三种开发范式 — **Plugin（插件）**、**Extension（扩展）** 和 **Adapter（适配器）** — 以覆盖从简单功能到复杂协议桥接的不同使用场景。

## 架构

```
┌────────────────────────────────────────────────────┐
│              Main Process (Host)                   │
│  ┌──────────────────────────────────────────────┐  │
│  │   Plugin Host (core/)                        │  │
│  │   - Plugin lifecycle management              │  │
│  │   - Bus system (memory, events, messages)    │  │
│  │   - Extension injection                      │  │
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
      ┌──────────────┼──────────────┬────────────────┐
      ▼              ▼              ▼                ▼
  Plugin A       Plugin B      Extension C      Adapter D
  (process)      (process)     (injected)       (process)
```

## 三种开发范式

| 范式 | 导入来源 | 使用场景 | 运行方式 |
|------|----------|----------|----------|
| **Plugin** | `plugin.sdk.plugin` | 独立功能（搜索、提醒等） | 独立进程 |
| **Extension** | `plugin.sdk.extension` | 向现有插件添加路由/钩子 | 注入到宿主插件进程中 |
| **Adapter** | `plugin.sdk.adapter` | 将外部协议（MCP、NoneBot）桥接到内部插件调用 | 独立进程，带网关管线 |

### 何时使用哪种范式？

- **"我想添加一个新的独立功能"** → 使用 **Plugin**
- **"我想为现有插件扩展额外的命令"** → 使用 **Extension**
- **"我想接受 MCP/NoneBot/外部协议调用并将其路由到插件"** → 使用 **Adapter**

> 99% 的开发者只需要 **Plugin**。从这里开始。

## 主要特性

- **进程隔离** — 每个插件在独立进程中运行；崩溃不会影响宿主
- **异步支持** — 同时支持同步和异步入口点
- **Result 类型** — 使用 `Ok`/`Err` 进行类型安全的错误处理（正常流程中无异常）
- **钩子系统** — `@before_entry`、`@after_entry`、`@around_entry`、`@replace_entry` 实现 AOP
- **跨插件调用** — `self.plugins.call_entry("other_plugin:entry_id")` 实现插件间通信
- **内存客户端** — `self.memory` 访问宿主内存系统
- **系统信息** — `self.system_info` 查询宿主系统元数据
- **插件存储** — `PluginStore` 提供持久化键值存储
- **总线系统** — `self.bus` 用于事件发布/订阅
- **动态入口** — 在运行时注册/注销入口点
- **静态 UI** — 从插件目录提供 Web UI 服务
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
    └── static/          # 可选：Web UI 文件
```

## 快速链接

- [快速开始](./quick-start) — 5 分钟内创建你的第一个插件
- [SDK 参考](./sdk-reference) — 基类、上下文 API、Result 类型
- [装饰器](./decorators) — 所有可用的装饰器
- [示例](./examples) — 完整的可运行示例
- [高级主题](./advanced) — 扩展、适配器、跨插件调用、钩子
- [最佳实践](./best-practices) — 错误处理、测试、代码组织
