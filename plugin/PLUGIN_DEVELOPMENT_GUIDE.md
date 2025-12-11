# N.E.K.O 插件系统开发指南

> 一份极其详尽的插件开发教程，包含完整的功能介绍、实例代码和最佳实践

## 目录

- [第一章：概述](#第一章概述)
- [第二章：快速开始](#第二章快速开始)
- [第三章：SDK 核心功能](#第三章sdk-核心功能)
- [第四章：装饰器详解](#第四章装饰器详解)
- [第五章：上下文对象](#第五章上下文对象)
- [第六章：完整示例](#第六章完整示例)
- [第七章：高级主题](#第七章高级主题)
- [第八章：最佳实践](#第八章最佳实践)
- [第九章：常见问题](#第九章常见问题)
- [第十章：API 参考](#第十章api-参考)

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

### 1.3 系统架构

```
┌─────────────────────────────────────────┐
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
└─────────────────────────────────────────┘
           │
    ┌──────┴──────┬──────────┬──────────┐
    │             │          │          │
    ▼             ▼          ▼          ▼
┌────────┐  ┌────────┐  ┌────────┐  ┌────────┐
│Plugin 1│  │Plugin 2│  │Plugin 3│  │Plugin N│
│Process │  │Process │  │Process │  │Process │
└────────┘  └────────┘  └────────┘  └────────┘
```

### 1.4 插件目录结构

```
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

[plugin.sdk]
# 推荐使用的 SDK 版本范围
recommended = ">=0.1.0,<0.2.0"
# 完全支持的 SDK 版本范围（必须满足或落在 untested 范围）
supported = ">=0.1.0,<0.3.0"
# 未经过完整测试但允许加载的范围（会告警）
untested = ">=0.3.0,<0.4.0"
# 明确冲突的范围（命中即拒绝加载）
conflicts = ["<0.1.0", ">=0.4.0"]
```

**配置说明：**
- `id`: 插件的唯一标识符（必须）
- `name`: 插件的显示名称
- `description`: 插件描述
- `version`: 插件版本号
- `plugin.sdk`: SDK 版本要求专用区块
  - `recommended`: 推荐使用的 SDK 范围，命中之外会提示警告
  - `supported`: 完全支持的 SDK 范围，未命中将拒绝加载（除非命中 `untested`）
  - `untested`: 未经完整测试但允许加载的范围，命中时会告警
  - `conflicts`: 明确冲突的范围，命中即拒绝加载
- `entry`: 插件入口点，格式为 `模块路径:类名`

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
curl -X POST http://localhost:8000/plugin/trigger \
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
    self.report_status({
        "status": "running",
        "progress": 50,
        "message": "Processing..."
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

### 4.6 @on_event

通用事件装饰器，可以定义自定义事件类型。

```python
from plugin.sdk.decorators import on_event

@on_event(
    event_type="custom_event",    # 自定义事件类型
    id="my_custom_handler",
    name="Custom Event Handler",
    description="处理自定义事件",
    input_schema={
        "type": "object",
        "properties": {
            "event_data": {"type": "string"}
        }
    },
    kind="hook",
    auto_start=False,
    extra={
        "category": "custom",
        "version": "1.0"
    }
)
def custom_handler(self, event_data: str, **_):
    """处理自定义事件"""
    # 处理逻辑
    return {"processed": True}
```

---

## 第五章：上下文对象

### 5.1 日志记录

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

### 5.2 状态管理

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

### 5.3 消息推送

#### 5.3.1 文本消息

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

#### 5.3.2 URL 消息

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

#### 5.3.3 二进制数据

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

#### 5.3.4 优先级说明

优先级范围：0-10
- `0-2`: 低优先级（信息性消息）
- `3-5`: 中优先级（一般通知）
- `6-8`: 高优先级（重要通知）
- `9-10`: 紧急优先级（需要立即处理）

### 5.4 配置文件访问

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

---

## 第六章：完整示例

### 6.1 示例 1：文件处理插件

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
    timer_interval
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

### 6.2 示例 2：Web API 客户端插件

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

### 6.3 示例 3：数据采集插件

```python
"""
数据采集插件示例
功能：定时采集数据、存储、推送通知
"""
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, List, Dict
from plugin.sdk.base import NekoPluginBase
from plugin.sdk.decorators import (
    neko_plugin,
    plugin_entry,
    lifecycle,
    timer_interval
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

## 第七章：高级主题

### 7.1 异步编程

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

### 7.2 线程安全

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

### 7.3 错误处理和重试

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

### 7.4 配置管理

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

### 7.5 数据持久化

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

## 第八章：最佳实践

### 8.1 代码组织

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

### 8.2 错误处理

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

### 8.3 日志记录

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

### 8.4 状态管理

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

### 8.5 输入验证

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

---

## 第九章：常见问题

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

A: 目前通过主系统的消息队列，未来可能支持直接通信。

### Q5: 定时任务可以动态启动/停止吗？

A: 目前定时任务在插件加载时自动启动（如果 `auto_start=True`），未来可能支持动态控制。

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

---

## 第十章：API 参考

### 10.1 装饰器

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

### 10.2 基类方法

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

### 10.3 上下文方法

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

# 可选：定义入口点（也可以在代码中使用装饰器定义）
# [plugin.entries]
# [[plugin.entries]]
# id = "entry1"
# name = "Entry 1"
# description = "Entry 1 description"
```

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

