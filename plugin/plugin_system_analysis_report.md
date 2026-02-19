# N.E.K.O Plugin 系统架构分析报告

## 一、系统概述

N.E.K.O Plugin 系统是一个基于 Python 的高性能插件框架,采用进程隔离架构,支持异步编程和类型安全。该系统通过 FastAPI 提供 HTTP API 接口,使用 ZeroMQ 实现高效的进程间通信。

### 核心特性
- **进程隔离**: 每个插件运行在独立进程中,提高稳定性和安全性
- **异步支持**: 完整支持同步和异步函数,基于 asyncio
- **类型安全**: 使用 Pydantic 进行数据验证和序列化
- **高性能通信**: 基于 ZeroMQ 的消息传递机制
- **生命周期管理**: 完整的启动、运行、关闭生命周期控制
- **事件驱动**: 支持多种事件类型和自定义事件
- **消息平面**: Python 实现的消息总线

## 二、模块组成与功能

### 1. **核心模块 (core/)**

#### 1.1 状态管理 (state.py)
- **功能**: 管理全局插件运行时状态
- **核心类**: `PluginRuntimeState`
- **职责**:
  - 维护所有插件的注册信息
  - 跟踪插件运行状态
  - 提供线程安全的状态访问

#### 1.2 上下文管理 (context.py)
- **功能**: 提供插件运行时上下文
- **核心类**: `PluginContext`
- **关键功能**:
  - 状态更新 (`update_status`)
  - 消息推送 (`push_message`) - 支持快速模式和批处理
  - Run 管理 (`run_update`, `export_push_*`)
  - 同步互调安全策略 (A1 策略)
  - Bus 总线集成 (`ctx.bus.memory/messages/events/lifecycle`)
  - ZeroMQ 消息批处理优化

### 2. **运行时模块 (runtime/)**

#### 2.1 插件注册表 (registry.py)
- **功能**: 插件的发现、加载和注册
- **核心函数**:
  - `load_plugins_from_toml()`: 从 TOML 配置加载插件
  - `scan_static_metadata()`: 扫描插件元数据
  - `register_plugin()`: 注册插件到系统
  - `get_plugins()`: 获取已注册插件列表
- **特性**:
  - 支持版本范围检查 (recommended, supported, untested, conflicts)
  - 插件依赖管理
  - SDK 版本兼容性验证

#### 2.2 进程宿主 (host.py)
- **功能**: 管理插件进程的生命周期
- **核心类**: `PluginProcessHost`
- **关键功能**:
  - 启动独立进程运行插件
  - 命令队列处理 (cmd_queue, res_queue)
  - 健康检查机制
  - 优雅关闭和强制终止
  - 日志配置和管理
- **进程间通信**:
  - 使用 `multiprocessing.Queue` 进行 IPC
  - 支持命令触发和结果返回
  - 状态和消息队列分离

#### 2.3 通信资源管理 (communication.py)
- **功能**: 管理插件间通信资源
- **核心类**: `PluginCommunicationResourceManager`
- **职责**:
  - 创建和管理通信队列
  - 资源清理和回收

#### 2.4 状态管理器 (status.py)
- **功能**: 跟踪和管理插件状态
- **核心类**: `PluginStatusManager`
- **职责**:
  - 收集插件状态更新
  - 提供状态查询接口

### 3. **SDK 模块 (sdk/)**

#### 3.1 基础类 (base.py)
- **核心类**: `NekoPluginBase`
- **功能**:
  - 所有插件的基类
  - 提供配置管理 (`PluginConfig`)
  - 插件间调用 (`Plugins`)
  - 入口点收集 (`collect_entries`)
  - 状态上报 (`report_status`)
  - 文件日志功能 (`enable_file_logging`)

#### 3.2 装饰器 (decorators.py)
- **核心装饰器**:
  - `@neko_plugin`: 标记插件类
  - `@plugin_entry`: 定义外部可调用入口
  - `@lifecycle`: 生命周期事件 (startup, shutdown, reload)
  - `@timer_interval`: 定时任务
  - `@message`: 消息事件处理
  - `@custom_event`: 自定义事件类型
  - `@on_event`: 通用事件装饰器

#### 3.3 事件系统 (events.py)
- **核心类**:
  - `EventMeta`: 事件元数据
  - `EventHandler`: 事件处理器
  - `EventType`: 事件类型枚举
- **支持的事件类型**:
  - plugin_entry: 外部调用入口
  - lifecycle: 生命周期事件
  - message: 消息事件
  - timer: 定时器事件
  - custom: 自定义事件

#### 3.4 配置管理 (config.py)
- **核心类**: `PluginConfig`
- **功能**:
  - 读取和更新插件配置
  - 支持同步和异步操作
  - 配置验证

#### 3.5 插件互调 (plugins.py)
- **核心类**: `Plugins`
- **功能**:
  - `call_plugin()`: 调用其他插件的入口或事件
  - 支持超时控制
  - 错误处理和重试

#### 3.6 总线系统 (bus/)
Bus 总线系统提供了从消息平面查询和订阅各类数据的统一接口:

- **memory.py**: 用户上下文客户端
  - `MemoryClient.get(bucket_id, limit, timeout)`: 获取用户上下文历史记录
  - `MemoryRecord`: 上下文记录数据结构
  - `MemoryList`: 上下文记录列表,支持过滤和查询
  - **用途**: 获取用户对话历史、上下文信息
  
- **messages.py**: 消息总线客户端
  - `MessageClient.get_recent(limit, timeout)`: 从消息平面查询消息记录
  - `MessageRecord`: 消息记录数据结构 (包含 message_id, message_type, description)
  - `MessageList`: 消息记录列表,支持链式操作和过滤
  - **用途**: 查询插件推送的历史消息
  
- **events.py**: 事件总线客户端
  - `EventClient.get_recent(limit, timeout)`: 查询插件事件记录
  - `EventRecord`: 事件记录数据结构 (包含 event_id, entry_id, args)
  - `EventList`: 事件记录列表
  - **用途**: 追踪插件调用和事件触发历史
  
- **lifecycle.py**: 生命周期事件客户端
  - `LifecycleClient.get_recent(limit, timeout)`: 查询插件生命周期事件
  - `LifecycleRecord`: 生命周期记录 (包含 lifecycle_id, detail)
  - `LifecycleList`: 生命周期记录列表
  - **用途**: 追踪插件启动、停止、重载等生命周期事件
  
- **运行实例管理**: 通过 `plugin/server/runs.py` 管理
  - `RunRecord`: 运行记录数据结构 (包含 run_id, status, progress, stage, metrics)
  - `ExportItem`: 导出项记录 (text, url, binary, binary_url)
  - **用途**: 追踪插件运行实例的状态、进度和导出结果
  - **存储**: runs 和 export 两个独立的 topic store
  
- **types.py**: 基础类型定义
  - `BusRecord`: 所有记录的基类
  - `BusList`: 记录列表基类,提供过滤、排序、限制等操作
  - `BusOp`: 总线操作类型
  - `GetNode`: 查询节点定义

#### 3.7 日志系统 (logger.py)
- **功能**: 基于 loguru 的插件日志
- **特性**:
  - 文件日志和控制台日志
  - 日志轮转和自动清理
  - 日志级别控制

### 4. **服务器模块 (server/)**

#### 4.1 主服务器 (plugin_server.py / FastAPI 应用)
- **技术栈**: FastAPI
- **核心功能**:
  - HTTP API 端点
  - WebSocket 支持
  - CORS 配置
  - 生命周期管理 (lifespan)
  - 事件循环监控 (watchdog)

#### 4.2 生命周期管理 (lifecycle.py)
- **功能**:
  - 系统启动初始化
  - 插件自动加载
  - 优雅关闭处理

#### 4.3 插件管理 (management.py)
- **核心函数**:
  - `start_plugin()`: 启动插件
  - `stop_plugin()`: 停止插件
  - `reload_plugin()`: 重载插件

#### 4.4 服务层 (services.py)
- **核心函数**:
  - `build_plugin_list()`: 构建插件列表
  - `trigger_plugin()`: 触发插件执行
  - `get_messages_from_queue()`: 获取消息队列

#### 4.5 运行管理 (runs.py)
- **功能**: 管理插件运行实例
- **核心类**:
  - `RunRecord`: 运行记录
  - `create_run()`: 创建运行实例
  - `cancel_run()`: 取消运行
  - `get_run()`: 获取运行状态

#### 4.6 WebSocket 支持
- **ws_run.py**: 运行实例的 WebSocket 端点
- **ws_admin.py**: 管理端的 WebSocket 端点

#### 4.7 配置服务 (config_service.py)
- **功能**:
  - 加载插件配置
  - 更新插件配置
  - 替换插件配置

#### 4.8 日志服务 (logs.py)
- **功能**:
  - 获取插件日志
  - 日志文件管理
  - 日志流式传输

#### 4.9 指标收集 (metrics_service.py)
- **功能**: 收集和提供系统指标
- **核心类**: `metrics_collector`

#### 4.10 认证授权 (auth.py)
- **功能**: 管理员权限验证
- **核心函数**: `require_admin()`

#### 4.11 Blob 存储 (blob_store.py)
- **功能**: 大文件存储和管理
- **核心类**: `blob_store`

#### 4.12 错误处理 (error_handler.py, exceptions.py)
- **功能**:
  - 统一异常处理
  - 错误响应格式化
  - 安全执行包装器

### 5. **消息平面 (message_plane/)**

#### 5.1 RPC 服务器 (rpc_server.py)
- **核心类**: `MessagePlaneRpcServer`
- **功能**:
  - 基于 ZeroMQ 的 RPC 服务
  - 消息查询和检索
  - 正则表达式查询支持
  - 协议版本管理

#### 5.2 发布服务器 (pub_server.py)
- **核心类**: `MessagePlanePubServer`
- **功能**: 消息发布和订阅

#### 5.3 摄取服务器 (ingest_server.py)
- **功能**: 接收和处理消息推送
- **特性**: 高性能批量处理

#### 5.4 存储管理 (stores.py)
- **核心类**:
  - `TopicStore`: 主题存储
  - `StoreRegistry`: 存储注册表
- **功能**: 消息持久化和检索

#### 5.5 协议定义 (protocol.py)
- **功能**: 定义消息平面通信协议
- **核心模型**:
  - `BusGetRecentArgs`: 获取最近消息参数
  - `BusQueryArgs`: 查询参数
  - 响应格式定义

#### 5.6 验证 (validation.py)
- **功能**: RPC 消息验证

#### 5.7 消息平面实现

**Python 实现** (`plugin/message_plane/`):
- **语言**: Python
- **架构**: 
  - 三个独立服务器: RPC Server, Pub Server, Ingest Server
  - 使用 Python 多线程 (threading)
  - 基于 ZeroMQ (pyzmq) 进行通信
- **存储**: 
  - `TopicStore`: Python 字典 + collections.deque
  - `StoreRegistry`: 管理多个 topic store (messages, events, lifecycle, runs, export, memory)
- **序列化**: ormsgpack (MessagePack)
- **优点**:
  - 易于调试和修改
  - 与 Python 生态系统集成良好
  - 开发速度快

### 6. **API 模块 (api/)**

#### 6.1 数据模型 (models.py)
- **核心模型**:
  - `PluginMeta`: 插件元数据
  - `PluginPushMessage`: 推送消息
  - `PluginPushMessageRequest`: 推送请求
  - `PluginPushMessageResponse`: 推送响应
  - `HealthCheckResponse`: 健康检查响应
  - `PluginAuthor`: 作者信息
  - `PluginDependency`: 依赖配置

#### 6.2 异常定义 (exceptions.py)
- **异常层次**:
  - `PluginError`: 基础异常
  - `PluginNotFoundError`: 插件未找到
  - `PluginNotRunningError`: 插件未运行
  - `PluginTimeoutError`: 超时异常
  - `PluginExecutionError`: 执行错误
  - `PluginCommunicationError`: 通信错误
  - `PluginLoadError`: 加载错误
  - `PluginImportError`: 导入错误
  - `PluginLifecycleError`: 生命周期错误
  - `PluginTimerError`: 定时器错误
  - `PluginEntryNotFoundError`: 入口未找到
  - `PluginMetadataError`: 元数据错误
  - `PluginQueueError`: 队列错误

### 7. **前端模块 (frontend/)**
- **技术栈**: Vue.js
- **功能**: 插件管理 Web 界面
- **目录**: `vue-project/`

### 8. **工具模块 (utils/)**
- **zeromq_ipc.py**: ZeroMQ IPC 封装
  - `MessagePlaneIngestBatcher`: 消息批处理器
  - 高性能消息推送优化

### 10. **配置模块 (settings.py)**
- **功能**: 全局配置管理
- **配置项**:
  - 队列容量配置 (EVENT_QUEUE_MAX, MESSAGE_QUEUE_MAX)
  - 超时配置 (PLUGIN_EXECUTION_TIMEOUT, PLUGIN_TRIGGER_TIMEOUT)
  - 路径配置 (PLUGIN_CONFIG_ROOT)
  - 安全策略配置 (SYNC_CALL_IN_HANDLER_POLICY)
  - 消息平面配置
  - Blob 存储配置
  - 日志配置

## 三、核心工作流程

### 1. 插件加载流程
1. **扫描**: `registry.py` 扫描 `plugins/` 目录
2. **解析**: 读取 `plugin.toml` 配置文件
3. **验证**: 检查 SDK 版本兼容性和依赖关系
4. **注册**: 将插件元数据注册到 `state`
5. **实例化**: 创建 `PluginProcessHost` 实例

### 2. 插件启动流程
1. **进程创建**: `host.py` 创建独立进程
2. **环境初始化**: 配置 Python 路径和日志
3. **插件导入**: 动态导入插件模块
4. **实例创建**: 创建插件实例并传入 `PluginContext`
5. **入口收集**: 扫描并注册所有装饰器标记的方法
6. **生命周期**: 执行 `startup` 生命周期事件
7. **命令循环**: 启动命令队列处理循环

### 3. 插件调用流程
1. **HTTP 请求**: 客户端发送 POST 请求到 `/runs`
2. **创建运行**: `runs.py` 创建运行实例
3. **命令发送**: 通过 `cmd_queue` 发送触发命令
4. **进程处理**: 插件进程从队列取出命令
5. **入口执行**: 调用对应的入口函数
6. **结果返回**: 通过 `res_queue` 返回结果
7. **响应返回**: HTTP 响应返回给客户端

### 4. 消息推送流程
1. **插件调用**: 插件调用 `ctx.push_message()`
2. **路由选择**:
   - **快速模式**: 使用 ZeroMQ 直接推送到消息平面
   - **普通模式**: 通过 `message_queue` 推送
3. **消息平面**: `ingest_server.py` 接收消息
4. **存储**: 保存到 `TopicStore`
5. **发布**: 通过 `pub_server.py` 发布给订阅者
6. **查询**: 支持通过 RPC 查询历史消息

### 5. 插件间通信流程
1. **调用发起**: 插件 A 调用 `self.plugins.call_plugin()`
2. **安全检查**: 检查同步互调策略 (A1)
3. **命令构造**: 构造跨插件调用命令
4. **队列传递**: 通过 `plugin_comm_queue` 传递
5. **目标执行**: 插件 B 执行对应入口
6. **结果返回**: 结果通过响应队列返回
7. **超时保护**: 支持超时和错误处理

## 四、技术亮点

### 1. 进程隔离架构
- 每个插件独立进程,故障隔离
- 资源限制和安全沙箱
- 支持热重载

### 2. 高性能通信
- ZeroMQ 零拷贝消息传递
- 消息批处理优化
- Python 实现的消息平面

### 3. 类型安全
- Pydantic 数据验证
- JSON Schema 输入验证
- 类型提示支持

### 4. 异步编程
- 完整的 asyncio 支持
- 同步/异步函数兼容
- 事件循环监控

### 5. 安全策略
- 同步互调检测 (A1 策略)
- 死锁预防机制
- 超时保护

### 6. 可观测性
- 结构化日志 (loguru)
- 指标收集
- 健康检查
- WebSocket 实时监控

### 7. 扩展性
- 装饰器驱动的插件开发
- 自定义事件系统
- 插件依赖管理
- 版本兼容性控制

## 五、总结

N.E.K.O Plugin 系统是一个设计精良、功能完善的插件框架。它通过进程隔离保证稳定性,通过 ZeroMQ 优化性能,通过完善的 SDK 降低开发门槛。系统支持复杂的插件间通信、事件驱动编程、定时任务等高级特性,同时提供了完整的生命周期管理、配置管理、日志系统和监控能力。

整个系统由 10 个主要模块组成,涵盖了从底层通信到上层 API 的完整技术栈,是一个生产级别的插件系统实现。Bus 总线系统提供了统一的数据查询接口,使插件能够方便地访问用户上下文、消息历史、事件记录和生命周期信息。
