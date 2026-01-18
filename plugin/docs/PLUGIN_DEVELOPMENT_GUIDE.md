# N.E.K.O 插件系统开发指南

> 一份极其详尽的插件开发教程，包含完整的功能介绍、实例代码和最佳实践

## 目录

- [第一章：概述](#第一章概述)
- [第二章：快速开始](#第二章快速开始)
- [第三章：SDK 核心功能](#第三章sdk-核心功能)
- [第四章：装饰器详解](#第四章装饰器详解)
- [第五章：插件间通信](#第五章插件间通信)
- [第六章：上下文对象](#第六章上下文对象)
- [第七章：完整示例](#第七章完整示例)
- [第八章：高级主题](#第八章高级主题)
- [第九章：最佳实践](#第九章最佳实践)
- [第十章：常见问题](#第十章常见问题)
- [第十一章：API 参考](#第十一章api-参考)

---

## 第一章：概述

### 1.1 什么是 N.E.K.O 插件系统？

N.E.K.O 插件系统是一个基于 Python 的插件框架，允许开发者创建可扩展的功能模块。每个插件运行在独立的进程中，通过进程间通信与主系统交互。

### 1.2 核心特性

- ✅ **进程隔离**：每个插件运行在独立进程中，提高稳定性和安全性
- ✅ **异步支持**：支持同步和异步函数
- ✅ **类型安全**：使用 Pydantic 进行数据验证
- ✅ **生命周期管理**：完整的启动、运行、关闭生命周期
- ✅ **消息推送**：插件可以向主系统推送消息
- ✅ **状态管理**：插件可以上报运行状态
- ✅ **定时任务**：支持定时执行任务
- ✅ **事件驱动**：支持多种事件类型
- ✅ **同步互调安全策略(A1)**：可配置在 handler 内进行同步互调时选择告警或拒绝，降低死锁风险

### 1.3 系统架构

```text
┌───────────────────────────────────────┐
│         主进程 (Main Process)            │
│  ┌───────────────────────────────────┐  │
│  │   Plugin Server (FastAPI)        │  │
│  │   - HTTP API 端点                 │  │
│  │   - 插件注册表                    │  │
│  │   - 消息队列                      │  │
│  └───────────────────────────────────┘  │
│           │                              │
│           │ Queue (IPC)                  │
│           ▼                              │
└───────────────────────────────────────┘
           │
    ┌──────┴──────┬──────────
    │             │          │          
    ▼             ▼          ▼          
┌────────┐       ┌────────┐       ┌────────┐
│Plugin 1│                  │Plugin 2│                │Plugin 3│
│Process │                  │Process │                │Process │
└────────┘       └────────┘       └────────┘
```

### 1.4 插件目录结构

```text
plugin/plugins/
├── my_plugin/
│   ├── __init__.py          # 插件主代码
│   └── plugin.toml          # 插件配置文件
```

---

## 第二章：快速开始

### 2.1 创建你的第一个插件

#### 步骤 1：创建插件目录

```bash
mkdir -p plugin/plugins/hello_world
cd plugin/plugins/hello_world
```

#### 步骤 2：创建配置文件 `plugin.toml`

```toml
[plugin]
id = "hello_world"
name = "Hello World Plugin"
description = "一个简单的示例插件"
version = "1.0.0"
entry = "plugins.hello_world:HelloWorldPlugin"

# 可选：插件作者信息
[plugin.author]
name = "Plugin Author Name"
email = "author@example.com"

[plugin.sdk]
# 推荐使用的 SDK 版本范围
recommended = ">=0.1.0,<0.2.0"
# 完全支持的 SDK 版本范围（必须满足或落在 untested 范围）
supported = ">=0.1.0,<0.3.0"
# 未经过完整测试但允许加载的范围（会告警）
untested = ">=0.3.0,<0.4.0"
# 明确冲突的范围（命中即拒绝加载）
conflicts = ["<0.1.0", ">=0.4.0"]

# 可选：安全策略
[plugin.safety]
# 同步互调安全策略(A1)：当插件在 handler 内触发同步等待类调用（例如读取配置、插件互调）时的行为
# - "warn"：仅记录 WARNING 日志（默认，兼容旧行为）
# - "reject"：直接抛异常拒绝该调用（更严格，推荐生产环境）
sync_call_in_handler = "warn"

# 可选：插件依赖配置
# 方式1：依赖特定插件ID
[[plugin.dependency]]
id = "timer_service"
recommended = ">=1.0.0,<2.0.0"
supported = ">=1.0.0,<3.0.0"
untested = ">=3.0.0,<4.0.0"  # 必须字段
conflicts = ["<1.0.0", ">=4.0.0"]

# 方式2：依赖特定入口点（任意插件提供该入口即可）
[[plugin.dependency]]
entry = "start_timer"
untested = ">=1.0.0,<2.0.0"
supported = ">=1.0.0,<3.0.0"

# 方式3：依赖多个候选插件（任一满足即可）
[[plugin.dependency]]
providers = ["timer_service", "timer_service_v2"]
untested = ">=1.0.0,<2.0.0"
```

**配置说明：**
- `id`: 插件的唯一标识符（必须）
- `name`: 插件的显示名称
- `description`: 插件描述
- `version`: 插件版本号
- `entry`: 插件入口点，格式为 `模块路径:类名`
- `plugin.author`: 插件作者信息（可选）
  - `name`: 作者名称
  - `email`: 作者邮箱
- `plugin.sdk`: SDK 版本要求专用区块
  - `recommended`: 推荐使用的 SDK 范围，命中之外会提示警告
  - `supported`: 完全支持的 SDK 范围，未命中将拒绝加载（除非命中 `untested`）
  - `untested`: 未经完整测试但允许加载的范围，命中时会告警
  - `conflicts`: 明确冲突的范围，命中即拒绝加载
- `plugin.dependency`: 插件依赖配置（可选，支持四种方式）
  - **方式1**：依赖特定插件ID - 使用 `id` 字段指定依赖的插件
  - **方式2**：依赖特定入口点 - 使用 `entry` 字段指定依赖的入口点
    - `entry` 字段只能引用 `@plugin_entry` 装饰器定义的入口点ID（`entry_id`）
    - 格式：`entry_id`（任意插件提供该入口）或 `plugin_id:entry_id`（指定插件必须提供该入口）
    - **注意**：`entry` 和 `custom_event` 互斥（不能同时使用）
  - **方式3**：依赖特定自定义事件 - 使用 `custom_event` 字段指定依赖的自定义事件
    - `custom_event` 字段只能引用 `@custom_event` 装饰器定义的事件
    - 格式：`event_type:event_id`（任意插件提供该事件）或 `plugin_id:event_type:event_id`（指定插件必须提供该事件）
    - **注意**：`entry` 和 `custom_event` 互斥（不能同时使用）
  - **方式4**：依赖多个候选插件 - 使用 `providers` 字段指定多个候选插件，任一满足即可
  - 如果使用依赖配置，`untested` 字段是必须的（简化格式除外）
  - 支持版本范围检查：`recommended`、`supported`、`untested`、`conflicts`

**安全策略说明：**
- `plugin.safety.sync_call_in_handler`: 同步互调安全策略（A1）
  - `warn`：当在 handler（`plugin_entry/custom_event/timer/lifecycle`）中触发同步等待类调用时，记录 WARNING 并继续执行
  - `reject`：当在 handler 中触发同步等待类调用时，直接抛异常阻止执行

#### 步骤 3：创建插件代码 `__init__.py`

```python
from plugin.sdk.base import NekoPluginBase
from plugin.sdk.decorators import neko_plugin, plugin_entry
from typing import Any

@neko_plugin
class HelloWorldPlugin(NekoPluginBase):
    """Hello World 插件示例"""
    
    def __init__(self, ctx: Any):
        super().__init__(ctx)
        self.logger = ctx.logger
        self.logger.info("HelloWorldPlugin initialized")
    
    @plugin_entry(
        id="greet",
        name="Greet",
        description="返回问候语",
        input_schema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "要问候的名字",
                    "default": "World"
                }
            }
        }
    )
    def greet(self, name: str = "World", **_):
        """问候函数"""
        message = f"Hello, {name}!"
        self.logger.info(f"Greeting: {message}")
        return {
            "message": message,
            "timestamp": "2024-01-01T00:00:00Z"
        }
```

#### 步骤 4：测试插件

启动插件服务器后，可以通过 HTTP API 调用插件：

```bash
curl -X POST http://localhost:8000/runs \
  -H "Content-Type: application/json" \
  -d '{
    "plugin_id": "hello_world",
    "entry_id": "greet",
    "args": {"name": "N.E.K.O"}
  }'
```

---

## 第三章：SDK 核心功能

### 3.1 NekoPluginBase 基类

所有插件都必须继承 `NekoPluginBase` 基类。

#### 3.1.1 初始化

```python
class MyPlugin(NekoPluginBase):
    def __init__(self, ctx: Any):
        super().__init__(ctx)
        # ctx 是 PluginContext 对象，包含：
        # - ctx.plugin_id: 插件ID
        # - ctx.logger: 日志记录器
        # - ctx.config_path: 配置文件路径
        # - ctx.update_status(): 更新状态方法
        # - ctx.push_message(): 推送消息方法
```

#### 3.1.2 基类方法

**`get_input_schema()`**
```python
def get_input_schema(self) -> Dict[str, Any]:
    """获取插件的输入模式"""
    # 默认从类属性 input_schema 获取
    # 可以重写此方法提供自定义模式
    return {
        "type": "object",
        "properties": {
            "param1": {"type": "string"}
        }
    }
```

**`report_status()`**
```python
def report_status(self, status: Dict[str, Any]) -> None:
    """上报插件状态到主进程"""
    # 实际实现略
```

使用示例：

```python
self.report_status({
    "status": "running",
    "progress": 50,
    "message": "Processing...",
})
```

**`collect_entries()`**
```python
def collect_entries(self) -> Dict[str, EventHandler]:
    """收集所有入口点（通常不需要手动调用）"""
    # 系统会自动扫描带有 @plugin_entry 装饰器的方法
    return entries
```

### 3.2 插件上下文 (PluginContext)

`PluginContext` 是插件运行时上下文，提供了与主系统交互的接口。

#### 3.2.1 属性

```python
ctx.plugin_id      # str: 插件ID
ctx.config_path    # Path: 配置文件路径
ctx.logger         # Logger: 日志记录器
ctx.status_queue   # Queue: 状态队列（内部使用）
ctx.message_queue  # Queue: 消息队列（内部使用）
```

#### 3.2.2 方法

**`update_status()`**
```python
def update_status(self, status: Dict[str, Any]) -> None:
    """更新插件状态"""
    ctx.update_status({
        "status": "processing",
        "current_item": 10,
        "total_items": 100
    })
```

**`push_message()`**
```python
def push_message(
    self,
    source: str,                    # 消息来源标识
    message_type: str,              # "text" | "url" | "binary" | "binary_url"
    description: str = "",         # 消息描述
    priority: int = 0,              # 优先级 (0-10)
    content: Optional[str] = None,  # 文本内容或URL
    binary_data: Optional[bytes] = None,  # 二进制数据
    binary_url: Optional[str] = None,     # 二进制文件URL
    metadata: Optional[Dict[str, Any]] = None  # 额外元数据
) -> None:
    """推送消息到主进程"""
    
    # 示例：推送文本消息
    ctx.push_message(
        source="my_feature",
        message_type="text",
        description="处理完成",
        priority=5,
        content="任务已成功完成",
        metadata={"task_id": "123", "duration": 10.5}
    )
    
    # 示例：推送URL消息
    ctx.push_message(
        source="web_scraper",
        message_type="url",
        description="发现新链接",
        priority=7,
        content="https://example.com/article",
        metadata={"title": "Example Article"}
    )
    
    # 示例：推送二进制数据（小文件）
    with open("image.png", "rb") as f:
        image_data = f.read()
    ctx.push_message(
        source="image_processor",
        message_type="binary",
        description="处理后的图片",
        priority=6,
        binary_data=image_data,
        metadata={"format": "png", "size": len(image_data)}
    )
    
    # 示例：推送大文件的URL引用
    ctx.push_message(
        source="file_processor",
        message_type="binary_url",
        description="大文件处理完成",
        priority=8,
        binary_url="https://storage.example.com/files/large_file.zip",
        metadata={"size": 1024*1024*100, "format": "zip"}
    )
```

---

## 第四章：装饰器详解

### 4.1 @neko_plugin

标记一个类为 N.E.K.O 插件。

```python
from plugin.sdk.decorators import neko_plugin

@neko_plugin
class MyPlugin(NekoPluginBase):
    pass
```

**说明：**
- 必须放在类定义之前
- 不需要参数
- 插件元数据从 `plugin.toml` 读取

### 4.2 @plugin_entry

定义插件的外部可调用入口点。

#### 4.2.1 基本用法

```python
from plugin.sdk.decorators import plugin_entry

@plugin_entry(
    id="my_function",
    name="My Function",
    description="这是一个示例函数"
)
def my_function(self, param1: str, **_):
    return {"result": param1}
```

#### 4.2.2 完整参数

```python
@plugin_entry(
    id="process_data",              # 入口点ID（必须）
    name="Process Data",            # 显示名称
    description="处理数据",        # 描述
    input_schema={                  # JSON Schema 输入验证
        "type": "object",
        "properties": {
            "data": {
                "type": "string",
                "description": "要处理的数据"
            },
            "options": {
                "type": "object",
                "properties": {
                    "format": {
                        "type": "string",
                        "enum": ["json", "xml", "csv"],
                        "default": "json"
                    }
                }
            }
        },
        "required": ["data"]
    },
    kind="action",                  # "action" | "service" | "hook"
    auto_start=False,               # 是否自动启动
    extra={                         # 额外元数据
        "category": "data_processing",
        "version": "1.0"
    }
)
def process_data(self, data: str, options: dict = None, **_):
    """处理数据的函数"""
    # 函数实现
    return {"processed": True}
```

#### 4.2.3 输入模式 (input_schema)

使用 JSON Schema 定义输入参数：

```python
input_schema = {
    "type": "object",
    "properties": {
        # 字符串类型
        "name": {
            "type": "string",
            "description": "名称",
            "minLength": 1,
            "maxLength": 100
        },
        # 数字类型
        "age": {
            "type": "integer",
            "description": "年龄",
            "minimum": 0,
            "maximum": 150
        },
        # 布尔类型
        "enabled": {
            "type": "boolean",
            "description": "是否启用",
            "default": True
        },
        # 数组类型
        "tags": {
            "type": "array",
            "description": "标签列表",
            "items": {
                "type": "string"
            },
            "minItems": 0,
            "maxItems": 10
        },
        # 对象类型
        "config": {
            "type": "object",
            "description": "配置对象",
            "properties": {
                "key1": {"type": "string"},
                "key2": {"type": "integer"}
            }
        },
        # 枚举类型
        "status": {
            "type": "string",
            "enum": ["pending", "processing", "completed"],
            "default": "pending"
        }
    },
    "required": ["name", "age"]  # 必填字段
}
```

#### 4.2.4 函数签名

入口函数可以接受关键字参数：

```python
@plugin_entry(id="example")
def example(self, param1: str, param2: int = 10, **_):
    """
    参数说明：
    - param1: 必需参数
    - param2: 可选参数，有默认值
    - **_: 捕获其他未使用的参数（推荐添加）
    """
    return {"result": f"{param1}:{param2}"}
```

#### 4.2.5 异步支持

```python
import asyncio

@plugin_entry(id="async_example")
async def async_example(self, url: str, **_):
    """异步函数示例"""
    import aiohttp
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            data = await response.text()
            return {"data": data}
```

### 4.3 @lifecycle

定义生命周期事件处理器。

#### 4.3.1 startup - 启动事件

```python
from plugin.sdk.decorators import lifecycle

@lifecycle(
    id="startup",
    name="Plugin Startup",
    description="插件启动时执行"
)
def startup(self, **_):
    """插件启动时的初始化逻辑"""
    self.logger.info("Plugin starting up...")
    
    # 初始化资源
    self._initialize_resources()
    
    # 上报状态
    self.report_status({"status": "initialized"})
    
    return {"status": "ready"}
```

#### 4.3.2 shutdown - 关闭事件

```python
@lifecycle(
    id="shutdown",
    name="Plugin Shutdown",
    description="插件关闭时执行"
)
def shutdown(self, **_):
    """插件关闭时的清理逻辑"""
    self.logger.info("Plugin shutting down...")
    
    # 清理资源
    self._cleanup_resources()
    
    # 保存状态
    self._save_state()
    
    return {"status": "stopped"}
```

#### 4.3.3 reload - 重载事件

```python
@lifecycle(
    id="reload",
    name="Plugin Reload",
    description="插件重载时执行"
)
def reload(self, **_):
    """插件重载时的逻辑"""
    self.logger.info("Plugin reloading...")
    
    # 重新加载配置
    self._reload_config()
    
    return {"status": "reloaded"}
```

**注意：** `auto_start` 参数对 lifecycle 事件无效，系统会自动调用。

### 4.4 @timer_interval

定义定时任务，按固定间隔执行。

```python
from plugin.sdk.decorators import timer_interval

@timer_interval(
    id="periodic_task",
    seconds=60,                    # 每60秒执行一次
    name="Periodic Task",
    description="定期执行的任务",
    auto_start=True               # 自动启动
)
def periodic_task(self, **_):
    """定期执行的任务"""
    self.logger.info("Running periodic task...")
    
    # 执行任务逻辑
    result = self._do_work()
    
    # 推送消息
    self.ctx.push_message(
        source="periodic_task",
        message_type="text",
        description="定期任务完成",
        priority=3,
        content=f"任务结果: {result}",
        metadata={"task_id": "periodic_001"}
    )
    
    return {"executed": True}
```

**重要说明：**
- `auto_start=True` 时，插件加载后自动开始定时执行
- 定时任务在独立线程中运行
- 支持同步和异步函数
- 任务异常不会中断定时器，会记录日志并继续

### 4.5 @message

定义消息事件处理器（用于处理来自主系统的消息）。

```python
from plugin.sdk.decorators import message

@message(
    id="handle_chat",
    name="Handle Chat Message",
    description="处理聊天消息",
    input_schema={
        "type": "object",
        "properties": {
            "text": {"type": "string"},
            "sender": {"type": "string"},
            "timestamp": {"type": "string"}
        }
    },
    source="chat",                # 消息来源过滤
    auto_start=True               # 自动订阅
)
def handle_chat(self, text: str, sender: str, timestamp: str, **_):
    """处理聊天消息"""
    self.logger.info(f"Received message from {sender}: {text}")
    
    # 处理消息逻辑
    response = self._process_message(text)
    
    # 推送回复
    self.ctx.push_message(
        source="chat_handler",
        message_type="text",
        description="消息回复",
        priority=5,
        content=response,
        metadata={"original_sender": sender}
    )
    
    return {"handled": True}
```

### 4.6 @custom_event

定义自定义事件类型，用于插件间的功能复用。

**设计理念**：
- `@plugin_entry`：用户调用插件的入口（对外服务）
- `@custom_event`：插件之间功能复用的机制（内部调用）

#### 4.6.1 基本用法

```python
from plugin.sdk.decorators import custom_event

@custom_event(
    event_type="file_change",      # 自定义事件类型名称
    id="on_file_modified",
    name="文件修改事件",
    description="当文件被修改时触发",
    input_schema={
        "type": "object",
        "properties": {
            "file_path": {"type": "string"},
            "action": {"type": "string", "enum": ["created", "modified", "deleted"]}
        },
        "required": ["file_path", "action"]
    },
    trigger_method="message",      # 触发方式：message | command | auto
    auto_start=False
)
def handle_file_change(self, file_path: str, action: str, **_):
    """处理文件修改事件"""
    self.logger.info(f"File {file_path} was {action}")
    return {"processed": True}
```

#### 4.6.2 参数说明

- `event_type`: 自定义事件类型名称（不能是标准类型：`plugin_entry`、`lifecycle`、`message`、`timer`）
- `id`: 事件ID（在插件内部唯一）
- `trigger_method`: 触发方式
  - `"message"`: 通过消息队列触发（推荐，异步）
  - `"command"`: 通过命令队列触发（同步，类似 `plugin_entry`）
  - `"auto"`: 自动启动（类似 `timer auto_start`）
- `auto_start`: 是否自动启动

#### 4.6.3 与其他插件通信

自定义事件可以被其他插件通过 `call_plugin()` 方法调用：

```python
# 在插件A中调用插件B的自定义事件
result = self.call_plugin(
    plugin_id="plugin_b",
    event_type="file_change",
    event_id="on_file_modified",
    args={
        "file_path": "/path/to/file.txt",
        "action": "modified"
    },
    timeout=5.0
)
```

### 4.7 @on_event

通用事件装饰器，可以定义自定义事件类型（底层实现）。

**注意**：推荐使用 `@custom_event` 装饰器，它提供了更清晰的语义和参数验证。

---

## 第五章：插件间通信

### 5.1 设计理念

N.E.K.O 插件系统提供了两种插件间通信机制：

- **`@plugin_entry`**：用户调用插件的入口（对外服务）
- **`@custom_event`**：插件之间功能复用的机制（内部调用）

### 5.2 同步互调与阻塞风险（A1）

插件间通信默认通过进程间队列（IPC）完成。部分 API 属于“同步等待”语义：

- 当你调用这些 API 时，当前 handler 会等待对方插件处理完并返回结果。
- 如果你在 handler 内发起同步互调，可能导致命令循环无法及时处理其他命令，从而出现超时甚至死锁。

为降低这类风险，系统提供 **同步互调安全策略(A1)**：

- **告警模式（默认）**：在 handler 内触发同步互调时输出 WARNING，提示你改造调用方式。
- **拒绝模式**：在 handler 内触发同步互调时直接抛异常，强制阻止高风险写法。

配置方式见 `plugin.toml` 的 `[plugin.safety]`。

### 5.3 使用 `call_plugin()` 方法

`call_plugin()` 是插件间的功能复用推荐方式，使用 Queue 机制，处理流程和 `plugin_entry` 一样，在单线程的命令循环中执行。

#### 5.3.1 调用其他插件的 custom_event

```python
from plugin.sdk.base import NekoPluginBase
from plugin.sdk.decorators import neko_plugin, plugin_entry

@neko_plugin
class PluginA(NekoPluginBase):
    @plugin_entry(id="do_something")
    def do_something(self, **_):
        """调用插件B的自定义事件"""
        try:
            result = self.call_plugin(
                plugin_id="plugin_b",
                event_type="file_change",      # 自定义事件类型
                event_id="on_file_modified",   # 事件ID
                args={
                    "file_path": "/path/to/file.txt",
                    "action": "modified"
                },
                timeout=5.0
            )
            self.logger.info(f"调用成功: {result}")
            return {"success": True, "result": result}
        except TimeoutError:
            self.logger.error("调用超时")
            return {"success": False, "error": "timeout"}
        except Exception as e:
            self.logger.exception(f"调用失败: {e}")
            return {"success": False, "error": str(e)}
```

提示：在 `@plugin_entry` 中同步调用 `call_plugin` 可能触发 A1 同步互调策略（`plugin.safety` 下的告警模式/拒绝模式）；建议将 `call_plugin` 下沉到后台线程/`asyncio.to_thread`，或改为消息化/异步流程，避免 handler 内阻塞导致超时或死锁。

#### 5.3.2 调用其他插件的 plugin_entry

```python
@plugin_entry(id="use_timer_service")
def use_timer_service(self, **_):
    """使用 timer_service 插件"""
    try:
        # 调用 timer_service 的 plugin_entry
        result = self.call_plugin(
            plugin_id="timer_service",
            event_type="plugin_entry",
            event_id="start_timer",
            args={
                "timer_id": "my_timer",
                "interval": 10,
                "immediate": True,
                "callback_plugin_id": self.ctx.plugin_id,
                "callback_entry_id": "on_timer_tick",
                "callback_args": {
                    "max_count": 5
                }
            },
            timeout=5.0
        )
        return {"success": True, "result": result}
    except Exception as e:
        self.logger.exception(f"启动定时器失败: {e}")
        return {"success": False, "error": str(e)}
```

#### 5.3.3 异步函数中的使用

在异步函数中，需要使用 `asyncio.to_thread` 包装同步调用：

```python
import asyncio

@plugin_entry(id="async_call")
async def async_call(self, **_):
    """在异步函数中调用其他插件"""
    # 使用 asyncio.to_thread 包装同步调用
    result = await asyncio.to_thread(
        self.call_plugin,
        plugin_id="other_plugin",
        event_type="plugin_entry",
        event_id="some_function",
        args={},
        timeout=5.0
    )
    return {"result": result}
```

### 5.4 定义可被其他插件调用的 custom_event

```python
from plugin.sdk.decorators import custom_event

@neko_plugin
class PluginB(NekoPluginBase):
    @custom_event(
        event_type="file_change",
        id="on_file_modified",
        name="文件修改事件",
        description="当文件被修改时触发",
        input_schema={
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "action": {"type": "string"}
            },
            "required": ["file_path", "action"]
        },
        trigger_method="command",  # 使用 command 方式，类似 plugin_entry
        auto_start=False
    )
    def handle_file_change(self, file_path: str, action: str, **_):
        """处理文件修改事件"""
        self.logger.info(f"File {file_path} was {action}")
        
        # 处理逻辑
        # ...
        
        return {"processed": True, "file": file_path}
```

### 5.5 通信机制说明

- **Queue 机制**：使用 `multiprocessing.Queue` 进行进程间通信
- **单线程处理**：所有调用都在插件的单线程命令循环中执行，保证线程安全
- **超时保护**：支持超时设置，防止调用挂起
- **错误处理**：调用失败会抛出异常，需要适当处理

### 5.6 使用 timer_service 插件示例

`timer_service` 是一个系统插件，提供定时器功能：

```python
@neko_plugin
class MyPlugin(NekoPluginBase):
    @plugin_entry(id="start_periodic_task")
    def start_periodic_task(self, **_):
        """启动一个定期任务"""
        timer_id = f"my_task_{int(time.time())}"
        
        # 调用 timer_service 启动定时器
        result = self.call_plugin(
            plugin_id="timer_service",
            event_type="plugin_entry",
            event_id="start_timer",
            args={
                "timer_id": timer_id,
                "interval": 30,  # 每30秒执行一次
                "immediate": True,  # 立即执行一次
                "callback_plugin_id": self.ctx.plugin_id,
                "callback_entry_id": "on_timer_tick",  # 回调入口点
                "callback_args": {
                    "task_name": "periodic_task"
                }
            },
            timeout=5.0
        )
        
        if result.get("success"):
            self.logger.info(f"定时器已启动: {timer_id}")
            return {"success": True, "timer_id": timer_id}
        else:
            return {"success": False, "error": result.get("error")}
    
    @plugin_entry(id="on_timer_tick")
    def on_timer_tick(self, timer_id: str, task_name: str, current_count: int, **_):
        """定时器回调"""
        self.logger.info(f"定时器 {timer_id} 触发，第 {current_count} 次")
        
        # 执行定期任务
        self._do_periodic_task(task_name)
        
        return {"executed": True}
```

---

## 第六章：上下文对象

### 6.1 日志记录

#### 6.1.1 基础日志

```python
class MyPlugin(NekoPluginBase):
    def __init__(self, ctx):
        super().__init__(ctx)
        self.logger = ctx.logger
    
    @plugin_entry(id="example")
    def example(self, **_):
        # 不同级别的日志
        self.logger.debug("调试信息")
        self.logger.info("一般信息")
        self.logger.warning("警告信息")
        self.logger.error("错误信息")
        self.logger.exception("异常信息（包含堆栈）")
        
        return {"status": "ok"}
```

#### 6.1.2 文件日志

插件支持将日志同时输出到文件和控制台，便于调试和问题追踪：

```python
class MyPlugin(NekoPluginBase):
    def __init__(self, ctx):
        super().__init__(ctx)
        import logging
        # 启用文件日志（同时输出到文件和控制台）
        self.file_logger = self.enable_file_logging(log_level=logging.INFO)
        self.logger = self.file_logger  # 使用文件日志器
    
    @plugin_entry(id="example")
    def example(self, **_):
        # 日志会同时显示在终端和保存到文件
        self.logger.info("这条日志会保存到文件")
        return {"status": "ok"}
```

**文件日志特性**：
- 日志文件保存在 `{plugin_dir}/logs/` 目录下
- 日志文件名格式：`{plugin_id}_{YYYYMMDD_HHMMSS}.log`
- 自动日志轮转：当日志文件达到最大大小时自动轮转
- 自动清理：超过最大文件数量限制的旧日志会自动删除
- 同时输出：日志会同时输出到文件和控制台（终端）

### 6.2 状态管理

```python
@plugin_entry(id="long_task")
def long_task(self, **_):
    """长时间运行的任务"""
    total_steps = 100
    
    for i in range(total_steps):
        # 执行步骤
        self._do_step(i)
        
        # 更新状态
        self.report_status({
            "status": "processing",
            "current_step": i + 1,
            "total_steps": total_steps,
            "progress": (i + 1) / total_steps * 100,
            "message": f"处理中: {i + 1}/{total_steps}"
        })
    
    # 完成
    self.report_status({
        "status": "completed",
        "message": "任务完成"
    })
    
    return {"completed": True}
```

### 6.3 消息推送

#### 6.3.1 文本消息

```python
self.ctx.push_message(
    source="my_feature",
    message_type="text",
    description="操作完成",
    priority=5,
    content="任务已成功完成",
    metadata={
        "task_id": "123",
        "duration": 10.5,
        "result": "success"
    }
)
```

#### 6.3.2 URL 消息

```python
self.ctx.push_message(
    source="web_scraper",
    message_type="url",
    description="发现新文章",
    priority=7,
    content="https://example.com/article/123",
    metadata={
        "title": "Example Article",
        "author": "John Doe",
        "published_at": "2024-01-01T00:00:00Z"
    }
)
```

#### 6.3.3 二进制数据

```python
# 小文件直接传输
with open("image.png", "rb") as f:
    image_data = f.read()

self.ctx.push_message(
    source="image_processor",
    message_type="binary",
    description="处理后的图片",
    priority=6,
    binary_data=image_data,
    metadata={
        "format": "png",
        "width": 1920,
        "height": 1080,
        "size": len(image_data)
    }
)

# 大文件使用URL引用
self.ctx.push_message(
    source="file_processor",
    message_type="binary_url",
    description="大文件处理完成",
    priority=8,
    binary_url="https://storage.example.com/files/large_file.zip",
    metadata={
        "size": 1024 * 1024 * 100,  # 100MB
        "format": "zip",
        "checksum": "abc123..."
    }
)
```

#### 6.3.4 优先级说明

优先级范围：0-10
- `0-2`: 低优先级（信息性消息）
- `3-5`: 中优先级（一般通知）
- `6-8`: 高优先级（重要通知）
- `9-10`: 紧急优先级（需要立即处理）

### 6.4 配置文件访问

```python
from pathlib import Path
import json

class MyPlugin(NekoPluginBase):
    def __init__(self, ctx):
        super().__init__(ctx)
        self.config_path = ctx.config_path
        self._load_config()
    
    def _load_config(self):
        """加载插件配置"""
        # config_path 指向 plugin.toml 文件
        config_dir = self.config_path.parent
        
        # 可以读取额外的配置文件
        custom_config_path = config_dir / "config.json"
        if custom_config_path.exists():
            with open(custom_config_path) as f:
                self.custom_config = json.load(f)
        else:
            self.custom_config = {}
```

**重要提示（A1）**：
- 通过 `ctx` 获取配置、查询插件、或触发插件互调等“同步等待”能力时，不建议在 handler（`startup`、`@plugin_entry`、`@custom_event`、`@timer_interval` 等）中直接调用。
- 如果确实要在 handler 中使用，请在 `plugin.toml` 配置 `sync_call_in_handler` 为 `warn` 或 `reject`，并在代码中做降级/兜底。

### 6.5 ctx.bus：标准化消息/记忆总线（Bus）

`ctx.bus` 提供一组统一的“总线接口”，用于从主进程拉取不同类型的数据（例如 memory、messages）。

它的目标是：
- 用一致的数据结构返回（Record/List）。
- 支持链式过滤（filter/where/limit）。
- 支持直接 dump 成可序列化结构（便于返回给 API、写日志、push_message）。

#### 6.5.1 BusList / BusRecord 的通用行为

- `BusRecord`：单条记录对象，提供 `dump()`。
- `BusList`：记录列表对象。
  - 可迭代：`for x in list_obj: ...`
  - 支持 `len(list_obj)`
  - 支持 `dump()`：返回 `List[Dict[str, Any]]`
  - 支持链式：`filter(...)` / `where(predicate)` / `limit(n)`

#### 6.5.2 ctx.bus.memory：读取上下文记忆（Memory Bus）

接口：

```python
memory_list = self.ctx.bus.memory.get(bucket_id="lanlan_name", limit=20, timeout=5.0)

# 例：只看某类事件，再限制数量
filtered = memory_list.filter(type="PLUGIN_TRIGGER").limit(10)

return {
    "count": len(memory_list),
    "history": memory_list.dump(),
    "filtered": filtered.dump(),
}
```

#### 6.5.3 ctx.bus.messages：读取消息队列（Messages Bus）

接口：

```python
msg_list = self.ctx.bus.messages.get(
    plugin_id="web_interface",  # 可选；不填时默认只拉取当前插件的消息
    max_count=50,
    priority_min=None,
    timeout=5.0,
)

# 例：按 source 精确过滤
filtered = msg_list.filter(source="web_interface").limit(10)

return {
    "count": len(msg_list),
    "messages": msg_list.dump(),
    "filtered": filtered.dump(),
}
```

`plugin_id` 的特别值：
- 当你传 `plugin_id="*"` 时，表示拉取“所有插件”的消息（不按 plugin_id 过滤）。

#### 6.5.4 filter 的规则（精确匹配 + 范围 + 正则）

`BusList.filter(...)` 使用“所有条件同时满足（AND）”的方式筛选。

已支持的字段：
- 精确匹配：`kind`、`type`、`plugin_id`、`source`
- 阈值/范围：`priority_min`、`since_ts`、`until_ts`
- 正则匹配：`kind_re`、`type_re`、`plugin_id_re`、`source_re`、`content_re`

示例：

```python
# source 包含 web（正则 search）
lst = msg_list.filter(source_re="web")

# type 是 url 或 text
lst2 = msg_list.filter(type_re="^(url|text)$")

# content 包含端口号
lst3 = msg_list.filter(content_re=r"127\\.0\\.0\\.1:8888")
```

#### 6.5.5 使用 plugin.toml 开启延迟自检（testPlugin 示例）

为了方便自动化测试，`testPlugin` 支持在 `plugin.toml` 中开启 `debug.messages`，在插件 startup 后延迟一段时间自动拉取一次 messages bus，并用 `ctx.push_message()` 把结果推送回主系统。

示例（`plugin/plugins/testPlugin/plugin.toml`）：

```toml
[debug.messages]
enable = true
delay_seconds = 5.0
plugin_id = "*"
max_count = 50
priority_min = 0
source = ""
timeout = 5.0
```

---

## 第七章：完整示例

### 7.1 示例 1：文件处理插件

```python
"""
文件处理插件示例
功能：处理文件上传、转换、下载
"""
import os
import shutil
from pathlib import Path
from typing import Any, Optional
from plugin.sdk.base import NekoPluginBase
from plugin.sdk.decorators import (
    neko_plugin,
    plugin_entry,
    lifecycle,
    timer_interval,
    custom_event  # 用于插件间功能复用
)

@neko_plugin
class FileProcessorPlugin(NekoPluginBase):
    """文件处理插件"""
    
    def __init__(self, ctx: Any):
        super().__init__(ctx)
        self.logger = ctx.logger
        self.work_dir = Path("/tmp/file_processor")
        self.work_dir.mkdir(exist_ok=True)
        self.processed_count = 0
    
    @lifecycle(id="startup")
    def startup(self, **_):
        """启动时初始化"""
        self.logger.info("FileProcessorPlugin starting...")
        self.report_status({
            "status": "initialized",
            "work_dir": str(self.work_dir)
        })
        return {"status": "ready"}
    
    @lifecycle(id="shutdown")
    def shutdown(self, **_):
        """关闭时清理"""
        self.logger.info("FileProcessorPlugin shutting down...")
        
        # 清理临时文件
        if self.work_dir.exists():
            shutil.rmtree(self.work_dir)
        
        self.report_status({"status": "stopped"})
        return {"status": "stopped"}
    
    @plugin_entry(
        id="process_file",
        name="Process File",
        description="处理上传的文件",
        input_schema={
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "文件路径"
                },
                "operation": {
                    "type": "string",
                    "enum": ["compress", "extract", "convert"],
                    "description": "操作类型"
                },
                "options": {
                    "type": "object",
                    "properties": {
                        "format": {"type": "string"},
                        "quality": {"type": "integer", "minimum": 1, "maximum": 100}
                    }
                }
            },
            "required": ["file_path", "operation"]
        }
    )
    def process_file(
        self,
        file_path: str,
        operation: str,
        options: Optional[dict] = None,
        **_
    ):
        """处理文件"""
        options = options or {}
        
        self.logger.info(f"Processing file: {file_path}, operation: {operation}")
        
        # 更新状态
        self.report_status({
            "status": "processing",
            "file": file_path,
            "operation": operation
        })
        
        try:
            # 执行处理
            if operation == "compress":
                result = self._compress_file(file_path, options)
            elif operation == "extract":
                result = self._extract_file(file_path, options)
            elif operation == "convert":
                result = self._convert_file(file_path, options)
            else:
                raise ValueError(f"Unknown operation: {operation}")
            
            self.processed_count += 1
            
            # 推送成功消息
            self.ctx.push_message(
                source="file_processor",
                message_type="text",
                description="文件处理完成",
                priority=6,
                content=f"文件 {file_path} 处理成功",
                metadata={
                    "operation": operation,
                    "result_path": result.get("output_path"),
                    "size": result.get("size")
                }
            )
            
            # 更新状态
            self.report_status({
                "status": "completed",
                "processed_count": self.processed_count
            })
            
            return {
                "success": True,
                "result": result
            }
            
        except Exception as e:
            self.logger.exception(f"Error processing file: {e}")
            
            # 推送错误消息
            self.ctx.push_message(
                source="file_processor",
                message_type="text",
                description="文件处理失败",
                priority=9,  # 高优先级错误
                content=f"处理文件 {file_path} 时出错: {str(e)}",
                metadata={
                    "operation": operation,
                    "error": str(e)
                }
            )
            
            return {
                "success": False,
                "error": str(e)
            }
    
    def _compress_file(self, file_path: str, options: dict) -> dict:
        """压缩文件"""
        # 实现压缩逻辑
        output_path = f"{file_path}.zip"
        # ... 压缩代码 ...
        return {
            "output_path": output_path,
            "size": os.path.getsize(output_path)
        }
    
    def _extract_file(self, file_path: str, options: dict) -> dict:
        """解压文件"""
        # 实现解压逻辑
        output_dir = f"{file_path}_extracted"
        # ... 解压代码 ...
        return {
            "output_path": output_dir,
            "size": 0
        }
    
    def _convert_file(self, file_path: str, options: dict) -> dict:
        """转换文件"""
        # 实现转换逻辑
        format = options.get("format", "pdf")
        output_path = f"{file_path}.{format}"
        # ... 转换代码 ...
        return {
            "output_path": output_path,
            "size": os.path.getsize(output_path)
        }
    
    @timer_interval(
        id="cleanup_temp_files",
        seconds=3600,  # 每小时执行一次
        name="Cleanup Temp Files",
        description="清理临时文件"
    )
    def cleanup_temp_files(self, **_):
        """定期清理临时文件"""
        self.logger.info("Cleaning up temporary files...")
        
        # 清理超过24小时的文件
        # ... 清理逻辑 ...
        
        self.ctx.push_message(
            source="file_processor",
            message_type="text",
            description="临时文件清理完成",
            priority=2,
            content="已清理临时文件",
            metadata={"cleaned_count": 10}
        )
        
        return {"cleaned": True}
```

**配置文件 `plugin.toml`:**
```toml
[plugin]
id = "file_processor"
name = "File Processor Plugin"
description = "处理文件上传、转换、下载"
version = "1.0.0"
entry = "plugins.file_processor:FileProcessorPlugin"
```

### 7.2 示例 2：Web API 客户端插件

```python
"""
Web API 客户端插件示例
功能：调用外部API、处理响应、错误重试
"""
import asyncio
import aiohttp
from typing import Any, Optional, Dict
from plugin.sdk.base import NekoPluginBase
from plugin.sdk.decorators import neko_plugin, plugin_entry, lifecycle

@neko_plugin
class APIClientPlugin(NekoPluginBase):
    """API客户端插件"""
    
    def __init__(self, ctx: Any):
        super().__init__(ctx)
        self.logger = ctx.logger
        self.session: Optional[aiohttp.ClientSession] = None
        self.base_url = "https://api.example.com"
    
    @lifecycle(id="startup")
    async def startup(self, **_):
        """启动时创建HTTP会话"""
        self.logger.info("APIClientPlugin starting...")
        self.session = aiohttp.ClientSession()
        self.report_status({"status": "ready"})
        return {"status": "ready"}
    
    @lifecycle(id="shutdown")
    async def shutdown(self, **_):
        """关闭时清理会话"""
        self.logger.info("APIClientPlugin shutting down...")
        if self.session:
            await self.session.close()
        self.report_status({"status": "stopped"})
        return {"status": "stopped"}
    
    @plugin_entry(
        id="fetch_data",
        name="Fetch Data",
        description="从API获取数据",
        input_schema={
            "type": "object",
            "properties": {
                "endpoint": {
                    "type": "string",
                    "description": "API端点路径"
                },
                "method": {
                    "type": "string",
                    "enum": ["GET", "POST", "PUT", "DELETE"],
                    "default": "GET"
                },
                "params": {
                    "type": "object",
                    "description": "查询参数"
                },
                "data": {
                    "type": "object",
                    "description": "请求体数据"
                },
                "headers": {
                    "type": "object",
                    "description": "请求头"
                }
            },
            "required": ["endpoint"]
        }
    )
    async def fetch_data(
        self,
        endpoint: str,
        method: str = "GET",
        params: Optional[Dict] = None,
        data: Optional[Dict] = None,
        headers: Optional[Dict] = None,
        **_
    ):
        """从API获取数据"""
        if not self.session:
            raise RuntimeError("Session not initialized")
        
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        
        self.logger.info(f"Fetching data from {url}")
        
        # 更新状态
        self.report_status({
            "status": "fetching",
            "url": url,
            "method": method
        })
        
        try:
            async with self.session.request(
                method=method,
                url=url,
                params=params,
                json=data,
                headers=headers
            ) as response:
                # 检查状态码
                if response.status >= 400:
                    error_text = await response.text()
                    raise aiohttp.ClientResponseError(
                        request_info=response.request_info,
                        history=response.history,
                        status=response.status,
                        message=error_text
                    )
                
                # 解析响应
                result = await response.json()
                
                # 推送成功消息
                self.ctx.push_message(
                    source="api_client",
                    message_type="text",
                    description="API调用成功",
                    priority=5,
                    content=f"成功获取数据: {endpoint}",
                    metadata={
                        "url": url,
                        "status": response.status,
                        "data_size": len(str(result))
                    }
                )
                
                # 更新状态
                self.report_status({
                    "status": "completed",
                    "endpoint": endpoint
                })
                
                return {
                    "success": True,
                    "data": result,
                    "status": response.status
                }
                
        except aiohttp.ClientError as e:
            self.logger.error(f"API request failed: {e}")
            
            # 推送错误消息
            self.ctx.push_message(
                source="api_client",
                message_type="text",
                description="API调用失败",
                priority=9,
                content=f"API调用失败: {endpoint} - {str(e)}",
                metadata={
                    "url": url,
                    "error": str(e)
                }
            )
            
            return {
                "success": False,
                "error": str(e)
            }
    
    @plugin_entry(
        id="batch_fetch",
        name="Batch Fetch",
        description="批量获取数据",
        input_schema={
            "type": "object",
            "properties": {
                "endpoints": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "端点列表"
                },
                "concurrent": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 10,
                    "default": 3,
                    "description": "并发数量"
                }
            },
            "required": ["endpoints"]
        }
    )
    async def batch_fetch(
        self,
        endpoints: list,
        concurrent: int = 3,
        **_
    ):
        """批量获取数据"""
        self.logger.info(f"Batch fetching {len(endpoints)} endpoints")
        
        # 创建信号量限制并发
        semaphore = asyncio.Semaphore(concurrent)
        
        async def fetch_with_limit(endpoint: str):
            async with semaphore:
                return await self.fetch_data(endpoint)
        
        # 并发执行
        tasks = [fetch_with_limit(ep) for ep in endpoints]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # 处理结果
        success_count = sum(1 for r in results if isinstance(r, dict) and r.get("success"))
        
        self.ctx.push_message(
            source="api_client",
            message_type="text",
            description="批量获取完成",
            priority=6,
            content=f"批量获取完成: {success_count}/{len(endpoints)} 成功",
            metadata={
                "total": len(endpoints),
                "success": success_count,
                "failed": len(endpoints) - success_count
            }
        )
        
        return {
            "success": True,
            "results": results,
            "success_count": success_count,
            "total_count": len(endpoints)
        }
```

### 7.3 示例 3：数据采集插件

```python
"""
数据采集插件示例
功能：定时采集数据、存储、推送通知
"""
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, List, Dict, Optional
from plugin.sdk.base import NekoPluginBase
from plugin.sdk.decorators import (
    neko_plugin,
    plugin_entry,
    lifecycle,
    timer_interval,
    custom_event  # 用于插件间功能复用
)

@neko_plugin
class DataCollectorPlugin(NekoPluginBase):
    """数据采集插件"""
    
    def __init__(self, ctx: Any):
        super().__init__(ctx)
        self.logger = ctx.logger
        self.data_dir = Path("/tmp/data_collector")
        self.data_dir.mkdir(exist_ok=True)
        self.collection_count = 0
        self.last_collection_time: Optional[datetime] = None
    
    @lifecycle(id="startup")
    def startup(self, **_):
        """启动时初始化"""
        self.logger.info("DataCollectorPlugin starting...")
        self.report_status({
            "status": "initialized",
            "data_dir": str(self.data_dir)
        })
        return {"status": "ready"}
    
    @plugin_entry(
        id="collect",
        name="Collect Data",
        description="采集数据",
        input_schema={
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "description": "数据源"
                },
                "filters": {
                    "type": "object",
                    "description": "过滤条件"
                }
            },
            "required": ["source"]
        }
    )
    def collect(self, source: str, filters: Optional[Dict] = None, **_):
        """采集数据"""
        self.logger.info(f"Collecting data from source: {source}")
        
        filters = filters or {}
        
        # 更新状态
        self.report_status({
            "status": "collecting",
            "source": source
        })
        
        try:
            # 模拟数据采集
            data = self._fetch_data(source, filters)
            
            # 保存数据
            timestamp = datetime.now().isoformat()
            filename = f"{source}_{timestamp}.json"
            filepath = self.data_dir / filename
            
            with open(filepath, 'w') as f:
                json.dump(data, f, indent=2)
            
            self.collection_count += 1
            self.last_collection_time = datetime.now()
            
            # 推送消息
            self.ctx.push_message(
                source="data_collector",
                message_type="text",
                description="数据采集完成",
                priority=6,
                content=f"从 {source} 采集了 {len(data)} 条数据",
                metadata={
                    "source": source,
                    "count": len(data),
                    "file": filename,
                    "timestamp": timestamp
                }
            )
            
            # 更新状态
            self.report_status({
                "status": "completed",
                "collection_count": self.collection_count,
                "last_collection": timestamp
            })
            
            return {
                "success": True,
                "data_count": len(data),
                "file": filename,
                "timestamp": timestamp
            }
            
        except Exception as e:
            self.logger.exception(f"Error collecting data: {e}")
            
            self.ctx.push_message(
                source="data_collector",
                message_type="text",
                description="数据采集失败",
                priority=9,
                content=f"从 {source} 采集数据失败: {str(e)}",
                metadata={
                    "source": source,
                    "error": str(e)
                }
            )
            
            return {
                "success": False,
                "error": str(e)
            }
    
    def _fetch_data(self, source: str, filters: Dict) -> List[Dict]:
        """获取数据（模拟）"""
        # 实际实现中，这里会调用真实的API或数据库
        return [
            {"id": 1, "name": "Item 1", "value": 100},
            {"id": 2, "name": "Item 2", "value": 200},
        ]
    
    @timer_interval(
        id="auto_collect",
        seconds=300,  # 每5分钟执行一次
        name="Auto Collect",
        description="自动采集数据"
    )
    def auto_collect(self, **_):
        """自动采集数据"""
        self.logger.info("Running auto collection...")
        
        # 从配置中获取数据源列表
        sources = ["source1", "source2", "source3"]
        
        results = []
        for source in sources:
            try:
                result = self.collect(source=source)
                results.append(result)
            except Exception as e:
                self.logger.error(f"Auto collect failed for {source}: {e}")
        
        success_count = sum(1 for r in results if r.get("success"))
        
        self.ctx.push_message(
            source="data_collector",
            message_type="text",
            description="自动采集完成",
            priority=4,
            content=f"自动采集完成: {success_count}/{len(sources)} 成功",
            metadata={
                "sources": sources,
                "success_count": success_count
            }
        )
        
        return {"collected": success_count, "total": len(sources)}
    
    @plugin_entry(
        id="get_stats",
        name="Get Statistics",
        description="获取采集统计信息"
    )
    def get_stats(self, **_):
        """获取统计信息"""
        # 统计文件数量
        json_files = list(self.data_dir.glob("*.json"))
        
        return {
            "collection_count": self.collection_count,
            "file_count": len(json_files),
            "last_collection": self.last_collection_time.isoformat() if self.last_collection_time else None,
            "data_dir": str(self.data_dir)
        }
```

---

## 第八章：高级主题

### 8.1 异步编程

插件支持异步函数，适合I/O密集型操作：

```python
import asyncio
import aiohttp

@plugin_entry(id="async_task")
async def async_task(self, url: str, **_):
    """异步任务示例"""
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            data = await response.json()
            return {"data": data}

# 并发执行多个异步任务
@plugin_entry(id="parallel_tasks")
async def parallel_tasks(self, urls: list, **_):
    """并行执行多个异步任务"""
    async def fetch_url(url: str):
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                return await response.json()
    
    tasks = [fetch_url(url) for url in urls]
    results = await asyncio.gather(*tasks)
    
    return {"results": results}
```

### 8.2 线程安全

如果插件使用多线程，需要注意线程安全：

```python
import threading
from typing import Any

@neko_plugin
class ThreadSafePlugin(NekoPluginBase):
    def __init__(self, ctx: Any):
        super().__init__(ctx)
        self._lock = threading.Lock()
        self._shared_data = {}
    
    @plugin_entry(id="update_data")
    def update_data(self, key: str, value: Any, **_):
        """线程安全地更新数据"""
        with self._lock:
            self._shared_data[key] = value
            return {"updated": True}
    
    @plugin_entry(id="get_data")
    def get_data(self, key: str, **_):
        """线程安全地获取数据"""
        with self._lock:
            return {"value": self._shared_data.get(key)}
```

### 8.3 错误处理和重试

```python
from tenacity import retry, stop_after_attempt, wait_exponential

@plugin_entry(id="retry_task")
@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=4, max=10)
)
def retry_task(self, url: str, **_):
    """带重试的任务"""
    import requests
    response = requests.get(url)
    response.raise_for_status()
    return {"data": response.json()}
```

### 8.4 配置管理

```python
import json
from pathlib import Path

class ConfigurablePlugin(NekoPluginBase):
    def __init__(self, ctx):
        super().__init__(ctx)
        self._load_config()
    
    def _load_config(self):
        """加载配置"""
        config_file = self.ctx.config_path.parent / "config.json"
        if config_file.exists():
            with open(config_file) as f:
                self.config = json.load(f)
        else:
            self.config = {
                "default_value": "default",
                "timeout": 30
            }
    
    @plugin_entry(id="get_config")
    def get_config(self, **_):
        """获取配置"""
        return {"config": self.config}
    
    @plugin_entry(id="update_config")
    def update_config(self, key: str, value: Any, **_):
        """更新配置"""
        self.config[key] = value
        # 保存到文件
        config_file = self.ctx.config_path.parent / "config.json"
        with open(config_file, 'w') as f:
            json.dump(self.config, f, indent=2)
        return {"updated": True}
```

### 8.5 数据持久化

```python
import sqlite3
from pathlib import Path

class PersistentPlugin(NekoPluginBase):
    def __init__(self, ctx):
        super().__init__(ctx)
        self.db_path = ctx.config_path.parent / "data.db"
        self._init_database()
    
    def _init_database(self):
        """初始化数据库"""
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT UNIQUE,
                value TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        conn.close()
    
    @plugin_entry(id="save_data")
    def save_data(self, key: str, value: str, **_):
        """保存数据"""
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "INSERT OR REPLACE INTO records (key, value) VALUES (?, ?)",
            (key, value)
        )
        conn.commit()
        conn.close()
        return {"saved": True}
    
    @plugin_entry(id="load_data")
    def load_data(self, key: str, **_):
        """加载数据"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute(
            "SELECT value FROM records WHERE key = ?",
            (key,)
        )
        row = cursor.fetchone()
        conn.close()
        return {"value": row[0] if row else None}
```

---

## 第九章：最佳实践

### 9.1 代码组织

```python
# ✅ 好的实践：清晰的代码组织
@neko_plugin
class WellOrganizedPlugin(NekoPluginBase):
    def __init__(self, ctx):
        super().__init__(ctx)
        self._initialize()
    
    def _initialize(self):
        """初始化逻辑"""
        pass
    
    def _helper_method(self):
        """辅助方法（私有）"""
        pass
    
    @plugin_entry(id="public_method")
    def public_method(self, **_):
        """公开方法"""
        pass

# ❌ 不好的实践：所有逻辑混在一起
@neko_plugin
class BadPlugin(NekoPluginBase):
    @plugin_entry(id="everything")
    def everything(self, **_):
        # 所有逻辑都在这里，难以维护
        pass
```

### 9.2 错误处理

```python
# ✅ 好的实践：详细的错误处理
@plugin_entry(id="robust_task")
def robust_task(self, param: str, **_):
    try:
        # 参数验证
        if not param:
            raise ValueError("param is required")
        
        # 业务逻辑
        result = self._do_work(param)
        
        return {"success": True, "result": result}
        
    except ValueError as e:
        self.logger.warning(f"Validation error: {e}")
        return {"success": False, "error": str(e)}
    except Exception as e:
        self.logger.exception(f"Unexpected error: {e}")
        return {"success": False, "error": "Internal error"}

# ❌ 不好的实践：忽略错误
@plugin_entry(id="fragile_task")
def fragile_task(self, param: str, **_):
    # 没有错误处理，任何异常都会导致插件崩溃
    result = self._do_work(param)
    return result
```

### 9.3 日志记录

```python
# ✅ 好的实践：适当的日志级别
@plugin_entry(id="good_logging")
def good_logging(self, **_):
    self.logger.debug("Detailed debug information")
    self.logger.info("General information")
    self.logger.warning("Warning message")
    self.logger.error("Error message")
    self.logger.exception("Exception with stack trace")

# ❌ 不好的实践：过度或不足的日志
@plugin_entry(id="bad_logging")
def bad_logging(self, **_):
    # 太多日志
    self.logger.info("Step 1")
    self.logger.info("Step 2")
    self.logger.info("Step 3")
    # ... 或者没有日志
```

### 9.4 状态管理

```python
# ✅ 好的实践：及时更新状态
@plugin_entry(id="good_status")
def good_status(self, **_):
    self.report_status({"status": "starting"})
    
    # 执行步骤1
    self._step1()
    self.report_status({"status": "step1_complete", "progress": 33})
    
    # 执行步骤2
    self._step2()
    self.report_status({"status": "step2_complete", "progress": 66})
    
    # 完成
    self.report_status({"status": "completed", "progress": 100})
    
    return {"success": True}

# ❌ 不好的实践：不更新状态
@plugin_entry(id="bad_status")
def bad_status(self, **_):
    # 长时间运行但没有状态更新
    self._long_running_task()
    return {"success": True}
```

### 9.5 输入验证

```python
# ✅ 好的实践：详细的输入模式
@plugin_entry(
    id="validated",
    input_schema={
        "type": "object",
        "properties": {
            "email": {
                "type": "string",
                "format": "email",
                "description": "邮箱地址"
            },
            "age": {
                "type": "integer",
                "minimum": 0,
                "maximum": 150,
                "description": "年龄"
            }
        },
        "required": ["email", "age"]
    }
)
def validated(self, email: str, age: int, **_):
    # 输入已经通过验证
    return {"email": email, "age": age}

# ❌ 不好的实践：没有输入验证
@plugin_entry(id="unvalidated")
def unvalidated(self, email: str, age: int, **_):
    # 需要手动验证
    if not email or "@" not in email:
        raise ValueError("Invalid email")
    if age < 0 or age > 150:
        raise ValueError("Invalid age")
    return {"email": email, "age": age}
```

### 9.6 避免在 handler 内同步互调（A1）

**背景**：插件运行在独立进程中，handler 由命令循环驱动。同步互调属于“发请求并等待响应”的模式，容易阻塞命令循环。

**推荐做法**：
- 在 `startup` / `@plugin_entry` / `@custom_event` / `@timer_interval` 中，避免直接调用同步等待类接口（如跨插件调用、配置读取/更新、插件查询）。
- 如果需要互调，优先改为：
  - 把互调逻辑放到后台线程/异步任务中执行（handler 尽快返回）；
  - 或设计为异步语义（先返回 accepted，后续通过消息/状态回推结果）。

**配置策略**：
- `sync_call_in_handler = "warn"`：开发阶段建议用它来发现潜在阻塞点。
- `sync_call_in_handler = "reject"`：生产环境更推荐，能更早暴露高风险调用。

---

## 第十章：常见问题

### Q1: 插件如何接收参数？

A: 通过函数参数接收，系统会根据 `input_schema` 验证参数：

```python
@plugin_entry(
    id="example",
    input_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "age": {"type": "integer"}
        }
    }
)
def example(self, name: str, age: int, **_):
    return {"name": name, "age": age}
```

### Q2: 如何处理可选参数？

A: 使用默认值：

```python
@plugin_entry(id="example")
def example(self, required: str, optional: str = "default", **_):
    return {"required": required, "optional": optional}
```

### Q3: 插件可以访问文件系统吗？

A: 可以，但建议使用 `ctx.config_path.parent` 作为工作目录：

```python
work_dir = self.ctx.config_path.parent / "data"
work_dir.mkdir(exist_ok=True)
```

### Q4: 如何实现插件间的通信？

A: 插件间通信有两种方式：

#### 方式1：使用 `call_plugin()` 方法（推荐）

用于调用其他插件的 `custom_event` 或 `plugin_entry`，使用 Queue 机制，在单线程中处理：

```python
# 调用其他插件的 custom_event
result = self.call_plugin(
    plugin_id="timer_service",
    event_type="timer_tick",
    event_id="handle_timer_tick",
    args={"timer_id": "xxx", "count": 1},
    timeout=5.0
)

# 调用其他插件的 plugin_entry
result = self.call_plugin(
    plugin_id="other_plugin",
    event_type="plugin_entry",
    event_id="some_function",
    args={"param": "value"},
    timeout=5.0
)
```

#### 方式2：通过主系统的消息队列

使用 `ctx.push_message()` 推送消息，其他插件通过 `@message` 装饰器接收。

### Q5: 定时任务可以动态启动/停止吗？

A: 可以！使用 `timer_service` 系统插件可以动态管理定时器：

```python
# 启动定时器
result = self.call_plugin(
    plugin_id="timer_service",
    event_type="plugin_entry",
    event_id="start_timer",
    args={
        "timer_id": "my_timer",
        "interval": 10,
        "callback_plugin_id": self.ctx.plugin_id,
        "callback_entry_id": "on_timer_tick"
    }
)

# 停止定时器
result = self.call_plugin(
    plugin_id="timer_service",
    event_type="plugin_entry",
    event_id="stop_timer",
    args={"timer_id": "my_timer"}
)
```

**注意**：`@timer_interval` 装饰器定义的定时任务在插件加载时自动启动（如果 `auto_start=True`），这是另一种定时任务方式。

### Q6: 插件崩溃会影响主系统吗？

A: 不会，每个插件运行在独立进程中，崩溃不会影响主系统。

### Q7: 如何调试插件？

A: 使用 `ctx.logger` 记录日志，查看插件进程的日志输出。

### Q8: 插件可以访问网络吗？

A: 可以，使用 `aiohttp` 或 `requests` 等库。

### Q9: 如何测试插件？

A: 可以编写单元测试，或者通过 HTTP API 调用插件进行测试。

### Q10: 插件可以访问数据库吗？

A: 可以，使用任何 Python 数据库库（如 `sqlite3`、`psycopg2`、`pymongo` 等）。

### Q11: 如何配置插件依赖？

A: 在 `plugin.toml` 中使用 `[[plugin.dependency]]` 配置依赖。支持四种方式：

#### 方式1：依赖特定插件ID
```toml
[[plugin.dependency]]
id = "timer_service"
untested = ">=1.0.0,<2.0.0"
supported = ">=1.0.0,<3.0.0"
```
**适用场景**：需要依赖整个插件，包括其所有 `plugin_entry` 和 `custom_event`。

#### 方式2：依赖特定入口点（更灵活）
```toml
# 任意插件提供 start_timer 入口即可
[[plugin.dependency]]
entry = "start_timer"
untested = ">=1.0.0,<2.0.0"

# 或指定特定插件的特定入口
[[plugin.dependency]]
entry = "timer_service:start_timer"
untested = ">=1.0.0,<2.0.0"
```
**重要限制**：
- `entry` 字段**只能引用 `@plugin_entry` 装饰器定义的入口点ID**
- `entry` 和 `custom_event` **互斥**（不能同时使用）

#### 方式3：依赖特定自定义事件（新功能）
```toml
# 任意插件提供 timer_tick.on_tick 事件即可
[[plugin.dependency]]
custom_event = "timer_tick:on_tick"
untested = ">=1.0.0,<2.0.0"

# 或指定特定插件的特定事件
[[plugin.dependency]]
custom_event = "timer_service:timer_tick:on_tick"
untested = ">=1.0.0,<2.0.0"
```
**重要限制**：
- `custom_event` 字段**只能引用 `@custom_event` 装饰器定义的事件**
- 格式：`event_type:event_id` 或 `plugin_id:event_type:event_id`
- `entry` 和 `custom_event` **互斥**（不能同时使用）

#### 方式4：依赖多个候选插件（任一满足即可）
```toml
[[plugin.dependency]]
providers = ["timer_service", "timer_service_v2"]
untested = ">=1.0.0,<2.0.0"
```

**注意：**
- 如果使用依赖配置，`untested` 字段是必须的（简化格式除外）
- 依赖检查失败时，插件将无法加载
- 版本范围使用语义化版本规范（如 `>=1.0.0,<2.0.0`）

**`entry` 与 `custom_event` 的区别：**
- `entry` 字段只能引用 `@plugin_entry` 定义的入口点（用户调用的接口）
- `custom_event` 字段只能引用 `@custom_event` 定义的事件（插件间内部通信机制）
- `entry` 和 `custom_event` **互斥**（不能在同一依赖配置中同时使用）

### Q12: 如何表示插件冲突？

A: 使用简化格式的依赖配置：

```toml
[[plugin.dependency]]
id = "conflicting_plugin"
conflicts = true
```

如果 `conflicting_plugin` 存在，当前插件将无法加载。

---

## 第十一章：API 参考

### 11.1 装饰器

#### @neko_plugin
```python
@neko_plugin
class MyPlugin(NekoPluginBase):
    pass
```

#### @plugin_entry
```python
@plugin_entry(
    id: str,                    # 入口点ID
    name: str | None = None,    # 显示名称
    description: str = "",      # 描述
    input_schema: dict | None = None,  # JSON Schema
    kind: str = "action",       # "action" | "service" | "hook"
    auto_start: bool = False,  # 是否自动启动
    extra: dict | None = None  # 额外元数据
)
```

#### @lifecycle
```python
@lifecycle(
    id: Literal["startup", "shutdown", "reload"],  # 生命周期事件
    name: str | None = None,
    description: str = "",
    extra: dict | None = None
)
```

#### @timer_interval
```python
@timer_interval(
    id: str,                    # 定时器ID
    seconds: int,              # 间隔秒数
    name: str | None = None,
    description: str = "",
    auto_start: bool = True,    # 是否自动启动
    extra: dict | None = None
)
```

#### @message
```python
@message(
    id: str,                    # 消息处理器ID
    name: str | None = None,
    description: str = "",
    input_schema: dict | None = None,
    source: str | None = None,  # 消息来源过滤
    extra: dict | None = None
)
```

#### @on_event
```python
@on_event(
    event_type: str,            # 事件类型
    id: str,                    # 事件ID
    name: str | None = None,
    description: str = "",
    input_schema: dict | None = None,
    kind: str = "action",
    auto_start: bool = False,
    extra: dict | None = None
)
```

### 11.2 基类方法

#### NekoPluginBase.get_input_schema()
```python
def get_input_schema(self) -> Dict[str, Any]:
    """获取输入模式"""
```

#### NekoPluginBase.report_status()
```python
def report_status(self, status: Dict[str, Any]) -> None:
    """上报状态"""
```

#### NekoPluginBase.collect_entries()
```python
def collect_entries(self) -> Dict[str, EventHandler]:
    """收集入口点"""
```

#### NekoPluginBase.enable_file_logging()
```python
def enable_file_logging(
    self,
    log_level: Optional[int] = None,
    max_bytes: Optional[int] = None,
    backup_count: Optional[int] = None,
    max_files: Optional[int] = None,
) -> logging.Logger:
    """
    启用插件文件日志功能
    
    为插件创建独立的文件日志，日志文件保存在插件的logs目录下。
    日志会同时输出到文件和控制台（终端）。
    自动管理日志文件数量，支持日志轮转。
    """
```

#### NekoPluginBase.call_plugin()
```python
def call_plugin(
    self,
    plugin_id: str,
    event_type: str,
    event_id: str,
    args: Dict[str, Any],
    timeout: float = 5.0
) -> Dict[str, Any]:
    """
    调用其他插件的自定义事件或入口点（插件间功能复用）
    
    这是插件间功能复用的推荐方式，使用 Queue 而不是 HTTP。
    处理流程和 plugin_entry 一样，在单线程的命令循环中执行。
    
    Args:
        plugin_id: 目标插件ID
        event_type: 事件类型（"plugin_entry" 或自定义事件类型）
        event_id: 事件ID
        args: 参数字典
        timeout: 超时时间（秒）
    
    Returns:
        事件处理器的返回结果
    
    Raises:
        RuntimeError: 如果通信队列不可用
        TimeoutError: 如果超时
        Exception: 如果事件执行失败
    """
```

### 11.3 上下文方法

#### PluginContext.update_status()
```python
def update_status(self, status: Dict[str, Any]) -> None:
    """更新状态"""
```

#### PluginContext.push_message()
```python
def push_message(
    self,
    source: str,
    message_type: str,          # "text" | "url" | "binary" | "binary_url"
    description: str = "",
    priority: int = 0,          # 0-10
    content: Optional[str] = None,
    binary_data: Optional[bytes] = None,
    binary_url: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None
) -> None:
    """推送消息"""
```

#### PluginContext.trigger_plugin_event()
```python
def trigger_plugin_event(
    self,
    target_plugin_id: str,
    event_type: str,
    event_id: str,
    args: Dict[str, Any],
    timeout: float = 5.0
) -> Dict[str, Any]:
    """
    触发其他插件的自定义事件或入口点（插件间通信）
    
    注意：通常使用基类的 call_plugin() 方法，它是对此方法的封装。
    """
```

---

## 附录

### A. 插件配置文件示例

```toml
[plugin]
id = "my_plugin"
name = "My Plugin"
description = "插件描述"
version = "1.0.0"
entry = "plugins.my_plugin:MyPlugin"

# 可选：插件作者信息
[plugin.author]
name = "Plugin Author Name"
email = "author@example.com"

# SDK 版本要求
[plugin.sdk]
recommended = ">=0.1.0,<0.2.0"
supported = ">=0.1.0,<0.3.0"
untested = ">=0.3.0,<0.4.0"
conflicts = ["<0.1.0", ">=0.4.0"]

# 可选：插件依赖配置
# 方式1：依赖特定插件ID
[[plugin.dependency]]
id = "timer_service"
recommended = ">=1.0.0,<2.0.0"
supported = ">=1.0.0,<3.0.0"
untested = ">=3.0.0,<4.0.0"  # 必须字段
conflicts = ["<1.0.0", ">=4.0.0"]

# 方式2：依赖特定入口点（任意插件提供该入口即可）
[[plugin.dependency]]
entry = "start_timer"  # 或 "timer_service:start_timer" 指定特定插件
untested = ">=1.0.0,<2.0.0"
supported = ">=1.0.0,<3.0.0"

# 方式3：依赖特定自定义事件（任意插件提供该事件即可）
[[plugin.dependency]]
custom_event = "timer_tick:on_tick"  # 或 "timer_service:timer_tick:on_tick" 指定特定插件
untested = ">=1.0.0,<2.0.0"
supported = ">=1.0.0,<3.0.0"

# 方式4：依赖多个候选插件（任一满足即可）
[[plugin.dependency]]
providers = ["timer_service", "timer_service_v2"]
untested = ">=1.0.0,<2.0.0"
supported = ">=1.0.0,<3.0.0"

# 简化格式：表示与某个插件冲突（插件存在则拒绝加载）
[[plugin.dependency]]
id = "conflicting_plugin"
conflicts = true
```

**依赖配置说明：**

插件依赖配置支持四种灵活的方式，满足不同的使用场景：

1. **依赖特定插件ID**：当插件需要依赖一个特定的插件时使用
   ```toml
   [[plugin.dependency]]
   id = "timer_service"
   untested = ">=1.0.0,<2.0.0"
   ```
   **适用场景**：
   - 需要依赖整个插件（包括所有功能）

2. **依赖特定入口点**：当插件只需要某个功能（入口点），而不关心是哪个插件提供时使用
   ```toml
   [[plugin.dependency]]
   entry = "start_timer"  # 任意插件提供 start_timer 入口即可
   untested = ">=1.0.0,<2.0.0"
   ```
   也可以指定特定插件的特定入口：
   ```toml
   [[plugin.dependency]]
   entry = "timer_service:start_timer"  # timer_service 插件必须提供 start_timer 入口
   untested = ">=1.0.0,<2.0.0"
   ```
   **重要说明**：
   - `entry` 字段**只能引用 `@plugin_entry` 装饰器定义的入口点ID**（`entry_id`）
   - `entry` 和 `custom_event` **互斥**（不能同时使用）

3. **依赖特定自定义事件**：当插件只需要某个自定义事件，而不关心是哪个插件提供时使用
   ```toml
   [[plugin.dependency]]
   custom_event = "timer_tick:on_tick"  # 任意插件提供 timer_tick.on_tick 事件即可
   untested = ">=1.0.0,<2.0.0"
   ```
   也可以指定特定插件的特定事件：
   ```toml
   [[plugin.dependency]]
   custom_event = "timer_service:timer_tick:on_tick"  # timer_service 插件必须提供该事件
   untested = ">=1.0.0,<2.0.0"
   ```
   **重要说明**：
   - `custom_event` 字段**只能引用 `@custom_event` 装饰器定义的事件**
   - 格式：`event_type:event_id` 或 `plugin_id:event_type:event_id`
   - `entry` 和 `custom_event` **互斥**（不能同时使用）

4. **依赖多个候选插件**：当插件可以接受多个候选插件中的任意一个时使用
   ```toml
   [[plugin.dependency]]
   providers = ["timer_service", "timer_service_v2"]  # 任一满足即可
   untested = ">=1.0.0,<2.0.0"
   ```

**`entry` 与 `custom_event` 的区别：**

| 字段/概念 | 定义方式 | 依赖配置中的使用 | 说明 |
|---------|---------|----------------|------|
| `entry` | `@plugin_entry` 装饰器 | ✅ 支持，使用 `entry` 字段 | 用户调用插件的入口（对外服务） |
| `custom_event` | `@custom_event` 装饰器 | ✅ 支持，使用 `custom_event` 字段 | 用于插件间的功能复用（内部调用） |

**重要限制**：
- `entry` 和 `custom_event` **互斥**（不能在同一依赖配置中同时使用）
- `entry` 字段只能引用 `@plugin_entry` 定义的内容
- `custom_event` 字段只能引用 `@custom_event` 定义的内容

**示例对比：**

```python
# 插件A：定义 plugin_entry（可以被依赖配置的 entry 字段引用）
@plugin_entry(id="start_timer")
def start_timer(self, **_):
    pass

# 插件B：定义 custom_event（可以被依赖配置的 custom_event 字段引用）
@custom_event(event_type="timer_tick", id="on_tick")
def on_tick(self, **_):
    pass
```

```toml
# ✅ 正确：依赖 plugin_entry
[[plugin.dependency]]
entry = "start_timer"  # 可以引用 @plugin_entry 定义的入口点
untested = ">=1.0.0,<2.0.0"

# ✅ 正确：依赖 custom_event
[[plugin.dependency]]
custom_event = "timer_tick:on_tick"  # 可以引用 @custom_event 定义的事件
untested = ">=1.0.0,<2.0.0"

# ❌ 错误：不能同时使用 entry 和 custom_event
# [[plugin.dependency]]
# entry = "start_timer"
# custom_event = "timer_tick:on_tick"  # 错误！entry 和 custom_event 互斥
```

**版本范围字段说明：**
- `untested`: 必须字段（简化格式除外），定义未测试但允许的版本范围
- `supported`: 可选，定义完全支持的版本范围
- `recommended`: 可选，定义推荐使用的版本范围（超出范围会警告）
- `conflicts`: 可选，定义冲突的版本范围列表，或设置为 `true`（简化格式）

**依赖检查规则：**
- 如果依赖检查失败，插件将无法加载
- 版本检查优先级：`conflicts` > `supported`/`untested` > `recommended`
- 如果版本不在 `untested` 或 `supported` 范围内，依赖检查失败
- 如果版本在 `conflicts` 范围内，依赖检查失败

### B. JSON Schema 参考

```json
{
  "type": "object",
  "properties": {
    "string_field": {
      "type": "string",
      "minLength": 1,
      "maxLength": 100,
      "pattern": "^[a-z]+$"
    },
    "number_field": {
      "type": "number",
      "minimum": 0,
      "maximum": 100
    },
    "integer_field": {
      "type": "integer",
      "minimum": 0,
      "maximum": 100
    },
    "boolean_field": {
      "type": "boolean"
    },
    "array_field": {
      "type": "array",
      "items": {
        "type": "string"
      },
      "minItems": 0,
      "maxItems": 10
    },
    "object_field": {
      "type": "object",
      "properties": {
        "nested": {"type": "string"}
      }
    },
    "enum_field": {
      "type": "string",
      "enum": ["option1", "option2", "option3"]
    }
  },
  "required": ["string_field", "number_field"]
}
```

### C. 消息类型说明

- **text**: 纯文本消息
- **url**: URL链接消息
- **binary**: 二进制数据（小文件，直接传输）
- **binary_url**: 二进制文件URL（大文件，使用URL引用）

### D. 优先级说明

- **0-2**: 低优先级（信息性）
- **3-5**: 中优先级（一般通知）
- **6-8**: 高优先级（重要通知）
- **9-10**: 紧急优先级（需要立即处理）

---

## 结语

这份教程涵盖了 N.E.K.O 插件系统开发的所有核心内容。如果你有任何问题或建议，欢迎反馈！

**祝你开发愉快！** 🚀

