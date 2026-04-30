# N.E.K.O 插件系统开发指南

> SDK v2 完整开发教程，包含 Plugin / Extension / Adapter 三种开发范式

## 目录

- [第一章：概述](#第一章概述)
- [第二章：快速开始](#第二章快速开始)
- [第三章：SDK 核心功能](#第三章sdk-核心功能)
- [第四章：装饰器详解](#第四章装饰器详解)
- [第五章：上下文与运行时](#第五章上下文与运行时)
- [第六章：完整示例](#第六章完整示例)
- [第七章：Extension 扩展开发](#第七章extension-扩展开发)
- [第八章：Adapter 适配器开发](#第八章adapter-适配器开发)
- [第九章：高级主题](#第九章高级主题)
- [第十章：最佳实践](#第十章最佳实践)
- [第十一章：常见问题](#第十一章常见问题)
- [第十二章：API 参考](#第十二章api-参考)

---

## 第一章：概述

### 1.1 什么是 N.E.K.O 插件系统？

N.E.K.O 插件系统是一个基于 Python 的插件框架，允许开发者创建可扩展的功能模块。每个插件运行在独立的进程中，通过 ZMQ IPC 与主系统交互。

### 1.2 三种开发范式

| 范式 | 导入路径 | 用途 | 运行方式 |
|------|---------|------|---------|
| **Plugin** | `plugin.sdk.plugin` | 独立功能（搜索、提醒等） | 独立进程 |
| **Extension** | `plugin.sdk.extension` | 为现有插件添加路由/钩子 | 注入宿主插件进程 |
| **Adapter** | `plugin.sdk.adapter` | 对接外部协议（MCP、NoneBot 等） | 独立进程 + 网关管线 |

**如何选择？**

- **「我想添加一个新的独立功能」** → 用 **Plugin**（99% 的开发者只需要这个）
- **「我想给现有插件添加额外命令」** → 用 **Extension**
- **「我想把 MCP/NoneBot 等外部协议请求转发给插件」** → 用 **Adapter**

### 1.3 核心特性

- **进程隔离**：每个插件运行在独立进程中，崩溃不影响主系统
- **异步支持**：支持同步和异步入口函数
- **Result 类型**：`Ok`/`Err` 类型安全的错误处理（替代异常流）
- **Hook 系统**：`@before_entry`, `@after_entry`, `@around_entry`, `@replace_entry` 面向切面编程
- **跨插件调用**：`self.plugins.call_entry("other_plugin:entry_id")` 插件间通信
- **Memory 客户端**：`self.memory` 访问宿主记忆系统
- **系统信息**：`self.system_info` 查询宿主元数据
- **持久化存储**：`PluginStore` 键值对持久化
- **Bus 系统**：`self.bus` 事件发布/订阅
- **动态入口**：运行时注册/注销入口点
- **静态 UI**：从插件目录提供 Web UI
- **生命周期**：`startup`, `shutdown`, `reload`, `freeze`, `unfreeze`, `config_change`
- **定时任务**：`@timer_interval` 周期执行
- **消息处理**：`@message` 响应主系统消息
- **音乐播放**：`push_message` + `parts=[{type:'ui_action', action:'media_*'}]` 跨进程音频控制

### 1.4 系统架构

```text
┌────────────────────────────────────────────────────┐
│              主进程 (Host)                          │
│  ┌──────────────────────────────────────────────┐  │
│  │   Plugin Host (core/)                        │  │
│  │   - 插件生命周期管理                          │  │
│  │   - Bus 系统 (memory, events, messages)      │  │
│  │   - Extension 注入                           │  │
│  │   - ZMQ IPC 传输                             │  │
│  └──────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────┐  │
│  │   Plugin Server (server/)                    │  │
│  │   - HTTP API 端点 (FastAPI)                  │  │
│  │   - 插件注册表                                │  │
│  │   - 消息队列                                  │  │
│  └──────────────────────────────────────────────┘  │
└────────────────────┬───────────────────────────────┘
                     │ ZMQ IPC
      ┌──────────────┼──────────────┬────────────────┐
      ▼              ▼              ▼                ▼
  Plugin A       Plugin B      Extension C      Adapter D
  (独立进程)     (独立进程)     (注入宿主)       (独立进程)
```

### 1.5 SDK 包结构

```text
plugin/sdk/
├── plugin/         ← 标准插件开发入口（99% 的开发者只需要这个）
├── extension/      ← 扩展开发入口（为现有插件添加路由/钩子）
└── adapter/        ← 适配器开发入口（对接外部协议）
```

> `plugin/sdk/shared/` 是内部实现细节，不应被开发者直接导入。

### 1.6 插件目录结构

```text
plugin/plugins/
└── my_plugin/
    ├── __init__.py      # 插件代码（入口点）
    ├── plugin.toml      # 插件配置
    ├── config.json      # 可选：自定义配置
    ├── data/            # 可选：运行时数据目录
    └── static/          # 可选：Web UI 文件
```

---

## 第二章：快速开始

### 2.1 创建插件目录

```bash
mkdir -p plugin/plugins/hello_world
```

### 2.2 创建 `plugin.toml`

```toml
[plugin]
id = "hello_world"
name = "Hello World Plugin"
description = "一个简单的示例插件"
version = "1.0.0"
entry = "plugins.hello_world:HelloWorldPlugin"

[plugin.sdk]
recommended = ">=0.1.0,<0.2.0"
supported = ">=0.1.0,<0.3.0"
```

#### 配置字段说明

| 字段 | 必填 | 说明 |
|------|------|------|
| `id` | 是 | 插件唯一标识符 |
| `name` | 否 | 显示名称 |
| `description` | 否 | 插件描述 |
| `version` | 否 | 插件版本 |
| `entry` | 是 | 入口点：`模块路径:类名` |

#### SDK 版本字段

| 字段 | 说明 |
|------|------|
| `recommended` | 推荐的 SDK 版本范围 |
| `supported` | 最低支持范围（不满足时拒绝加载） |
| `untested` | 允许但加载时会警告 |
| `conflicts` | 拒绝的版本范围 |

### 2.3 创建 `__init__.py`

```python
from plugin.sdk.plugin import (
    NekoPluginBase, neko_plugin, plugin_entry, lifecycle,
    Ok, Err, SdkError,
)
from typing import Any

@neko_plugin
class HelloWorldPlugin(NekoPluginBase):
    """Hello World 插件示例"""

    def __init__(self, ctx: Any):
        super().__init__(ctx)
        self.logger = ctx.logger
        self.counter = 0

    @lifecycle(id="startup")
    def on_startup(self, **_):
        self.logger.info("HelloWorldPlugin 已启动！")
        return Ok({"status": "ready"})

    @lifecycle(id="shutdown")
    def on_shutdown(self, **_):
        self.logger.info("HelloWorldPlugin 已停止！")
        return Ok({"status": "stopped"})

    @plugin_entry(
        id="greet",
        name="问候",
        description="返回一条问候消息",
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
        self.counter += 1
        message = f"Hello, {name}! (第 {self.counter} 次调用)"
        self.logger.info(f"问候: {message}")
        return Ok({"message": message, "count": self.counter})
```

### 2.4 关键要点

- **`@neko_plugin`** — 必须的类装饰器，将类注册为插件
- **`NekoPluginBase`** — 所有插件必须继承的基类
- **`@plugin_entry`** — 定义外部可调用的入口点
- **`@lifecycle`** — 处理生命周期事件（`startup`, `shutdown`, `reload`）
- **`Ok(...)` / `Err(...)`** — 返回 Result 类型，类型安全的错误处理
- **`**_`** — 入口点签名中始终包含，用于捕获额外参数

### 2.5 测试

启动插件服务器后，通过 HTTP 调用插件：

```bash
curl -X POST http://localhost:48916/plugin/trigger \
  -H "Content-Type: application/json" \
  -d '{
    "plugin_id": "hello_world",
    "entry_id": "greet",
    "args": {"name": "N.E.K.O"}
  }'
```

---

## 第三章：SDK 核心功能

### 3.1 导入方式

所有插件开发 API 从 `plugin.sdk.plugin` 导入：

```python
from plugin.sdk.plugin import (
    # 基类
    NekoPluginBase, PluginMeta,
    # 装饰器
    neko_plugin, plugin_entry, lifecycle, timer_interval, message, on_event,
    custom_event, hook, before_entry, after_entry, around_entry, replace_entry,
    # Result 类型
    Ok, Err, Result, unwrap, unwrap_or,
    # 运行时工具
    Plugins, PluginRouter, PluginConfig, PluginStore,
    SystemInfo, MemoryClient,
    # 错误
    SdkError, TransportError,
    # 日志
    get_plugin_logger,
)
```

### 3.2 NekoPluginBase 属性

| 属性 | 类型 | 说明 |
|------|------|------|
| `self.ctx` | `PluginContext` | 运行时上下文（宿主注入） |
| `self.plugin_id` | `str` | 插件唯一标识符 |
| `self.config_dir` | `Path` | `plugin.toml` 所在目录 |
| `self.metadata` | `dict` | 来自 `plugin.toml` 的元数据 |
| `self.bus` | `Bus` | 事件总线（发布/订阅） |
| `self.plugins` | `Plugins` | 跨插件调用工具 |
| `self.memory` | `MemoryClient` | 宿主记忆系统访问 |
| `self.system_info` | `SystemInfo` | 宿主系统元数据 |

### 3.3 NekoPluginBase 方法

#### `report_status(status: dict) -> None`

向宿主报告插件状态：

```python
self.report_status({
    "status": "processing",
    "progress": 50,
    "message": "处理中..."
})
```

<a id="push-message-v2"></a>
#### `push_message(**kwargs) -> object`

`push_message` 是插件 → 主系统的**唯一**消息推送入口。两条独立的轴
决定下游行为，配合 `parts` 列表承载 OpenAI 风格的多模态内容。完整
schema 见
[plugin/sdk/shared/core/push_message_schema.py](sdk/shared/core/push_message_schema.py)。

```python
ctx.push_message(
    visibility=[],                  # ["chat"] / ["hud"] / ["chat","hud"] / []
    ai_behavior="respond",          # "respond" / "read" / "blind"
    parts=[                         # 有序的内容 parts
        {"type": "text",  "text": "看这个"},
        {"type": "image", "data": img_bytes, "mime": "image/png"},
        # 以下三种当前只在 schema 中占位，AI 注入链路 warn-drop，
        # 详见下文「当前实现限制」：
        # {"type": "image", "url": "https://example.com/cat.png"},
        # {"type": "audio", "data": audio_bytes, "mime": "audio/mpeg"},
        # {"type": "video", "url":  "https://example.com/clip.mp4"},
        {"type": "ui_action",
         "action": "media_play_url",
         "url": "https://example.com/song.mp3",
         "media_type": "audio"},
    ],
    source="my_feature",
    target_lanlan="灵",             # 可选，路由到指定 session
    metadata={...},
    priority=0,                     # 数字越大优先级越高
)
```

##### 两条轴的语义

* **`visibility`** — plugin 的 parts 直接渲染给用户的目标列表（**与 AI 无关**）：

  | 值 | 含义 |
  |---|---|
  | `"chat"` | parts 在 chat 框里**原文**渲染（plugin 的话直接当 chat 气泡） |
  | `"hud"`  | parts 在 agent UI / HUD 通知面板显示 |
  | `[]`     | 用户**不直接**看到 parts；如果 `ai_behavior="respond"`，用户看到的是 AI 的回复 |

  可同时多选（如 `["chat", "hud"]`）。

* **`ai_behavior`** — LLM 怎么处理 parts：

  | 值 | LLM 上下文 | 触发回复 turn | 何时被提及 |
  |---|---|---|---|
  | `"respond"`（默认） | ✅ | ✅ 立即起 turn | AI 立刻接茬 |
  | `"read"`  | ✅ | ❌ 不打断 | 下次用户开口时 AI 自然提及 |
  | `"blind"` | ❌ | ❌ | AI 看不到 |

##### `parts` part 类型

每个 part 是个 dict，必须有 `type` discriminator：

| `type` | 字段 | 用途 |
|---|---|---|
| `text` | `text: str` | 纯文本 |
| `image` | `data: bytes` + `mime` 或 `url` + `mime` | 图（inline 或远端） |
| `audio` | 同 image | 音频（schema 占位，**当前 AI 注入链路尚未消费**，会 warn-drop） |
| `video` | 同 image | 视频（schema 占位，**当前 AI 注入链路尚未消费**，会 warn-drop） |
| `ui_action` | `action: str` + 各 action 的字段 | 前端 UI 副作用 |

inline `data: bytes` 由 SDK 自动 base64 编码后随 payload 传出。

> **当前实现限制**（v0.9 移除前会逐步补齐）：
> - `ai_behavior in ("respond","read")` 时只有 **inline `image` parts** 真正进 LLM
>   上下文（走 `session.stream_image(base64)`）。
> - `image` 的 `url` 形态会被 main_server warn-drop，避免 event-bus 同步去抓远端
>   导致整路阻塞。需要 URL 形态时请在 plugin 自己 fetch 后回填 `data` 字段。
> - `audio` / `video` 当前没有对应的 realtime 注入通道（`stream_audio` 是 PCM 实时
>   麦克风专用，video 完全没有 API），都会 warn-drop。这两种 type 现阶段只
>   推荐配合 `ai_behavior="blind"` + `ui_action` 走纯前端展示。
>
> **大小限制**：inline part 通过 message_plane 走 ZMQ，整条 payload 上限是
> `MESSAGE_PLANE_PAYLOAD_MAX_BYTES`（默认 256 KB）。1080p 截图建议先压成
> JPEG q70 或 256x256 PNG；超过 256KB 的大文件请上传到 BlobStore 再用
> `parts=[{"type": "image", "url": ...}]` 引用，**注意上一条限制**：当前
> 只有 inline 形态才进 AI。

##### 常见组合

```python
# 1. 让 AI 转述（最常用）：plugin 文本 → LLM 上下文 → AI 立即回复
ctx.push_message(parts=[{"type": "text", "text": "用户给你打赏了 100 块"}])

# 2. 安静通知 AI：下次用户说话时 AI 自然提及
ctx.push_message(
    visibility=[],
    ai_behavior="read",
    parts=[{"type": "text", "text": "B站直播间又有新弹幕"}],
)

# 3. 只在 HUD 通知用户，不打扰 AI
ctx.push_message(
    visibility=["hud"],
    ai_behavior="blind",
    parts=[{"type": "text", "text": "插件 X 已启动"}],
)

# 4. plugin 直接在 chat 渲染卡片，AI 不知情（取代旧 music_play_url）
ctx.push_message(
    visibility=["chat"],
    ai_behavior="blind",
    parts=[{
        "type": "ui_action",
        "action": "media_play_url",
        "url": "https://music.example.com/song.mp3",
        "media_type": "audio",
        "name": "Test Song",
        "artist": "Bot",
    }],
)

# 5. 文字 + 图同时给 AI
ctx.push_message(
    parts=[
        {"type": "text",  "text": "看这张图"},
        {"type": "image", "data": img_bytes, "mime": "image/png"},
    ],
)

# 6. 注册音乐域名白名单（取代旧 register_music_domains）
ctx.push_message(
    ai_behavior="blind",
    parts=[{
        "type": "ui_action",
        "action": "media_allowlist_add",
        "domains": ["music.my-cdn.com"],
    }],
)
```

##### 已废弃字段（v0.9 移除）

| 旧字段 | 新写法 |
|---|---|
| `message_type="proactive_notification"` | 默认行为，去掉即可 |
| `message_type="music_play_url"` | `parts=[{"type":"ui_action","action":"media_play_url",...}]` + `visibility=["chat"], ai_behavior="blind"` |
| `message_type="music_allowlist_add"` | `parts=[{"type":"ui_action","action":"media_allowlist_add","domains":[...]}]` + `ai_behavior="blind"` |
| `content="X"` | `parts=[{"type":"text","text":"X"}]` |
| `binary_data=bytes, mime` | `parts=[{"type":"image","data":bytes,"mime":...}]` |
| `binary_url=URL` | `parts=[{"type":"image","url":URL,"mime":...}]` |
| `delivery="proactive"` / `reply=True` | 默认即是 `visibility=[], ai_behavior="respond"` |
| `delivery="passive"` | `visibility=[], ai_behavior="read"` |
| `delivery="silent"` / `reply=False` | `visibility=["hud"], ai_behavior="blind"` |
| `description`, `unsafe` | drop |

旧字段调用仍可用，但每次会触发 `DeprecationWarning` 提示在 v0.9 移除。
完整 changelog：[`docs/changelog/`](../docs/changelog/)。

> **`register_music_domains()` SDK helper 已删除**。请直接 push 一条带
> `ui_action: media_allowlist_add` 的消息（见上面例 6）。

#### `data_path(*parts) -> Path`

获取插件 `data/` 目录下的路径：

```python
db_path = self.data_path("cache.db")  # → <plugin_dir>/data/cache.db
```

#### `register_dynamic_entry(entry_id, handler, ...) -> bool`

运行时动态注册入口点（不通过装饰器）：

```python
self.register_dynamic_entry(
    entry_id="dynamic_greet",
    handler=lambda name="World", **_: Ok({"msg": f"Hi {name}"}),
    name="动态问候",
    description="动态注册的问候入口",
)
```

#### `unregister_dynamic_entry(entry_id) -> bool`

移除动态注册的入口点。

#### `list_entries(include_disabled=False) -> list[dict]`

列出所有入口点（静态 + 动态）。

#### `enable_entry(entry_id) / disable_entry(entry_id) -> bool`

启用或禁用动态入口点。

#### `register_static_ui(directory, *, index_file, cache_control) -> bool`

注册插件的静态 Web UI 目录：

```python
self.register_static_ui("static")  # 提供 <plugin_dir>/static/index.html
```

#### `include_router(router, *, prefix) -> None`

挂载 `PluginRouter`（Extension 使用）。

#### `run_update(**kwargs) -> object` (async)

在长时间运行操作期间发送更新。

#### `export_push(**kwargs) -> object` (async)

向宿主推送导出数据。

#### `finish(**kwargs) -> Any` (async)

通知宿主任务完成。

#### 回复控制（`finish()` 的 `delivery`）

`finish()` 仍接受 `delivery` 参数控制任务结果如何到达主 AI（三档枚举，默认 `"proactive"`）：

| `delivery` | 是否进 LLM 上下文 | 是否立即起 turn 播报 | 前端 HUD/通知 |
|---|---|---|---|
| `"proactive"`（默认） | ✅ | ✅ 立即起 turn | ✅ |
| `"passive"` | ✅ 写入上下文 | ❌ 不打断；下次用户发言时由 AI 自然提及 | ✅ |
| `"silent"` | ❌ AI 不知情 | ❌ | ✅（只剩 task_update） |

```python
# 正常：角色立即播报结果
return await self.finish(data={"summary": "天气晴朗"})

# 安静通知：进上下文不打断；下次用户开口时 AI 顺嘴提一下
return await self.finish(data={"summary": "番茄钟到点了"}, delivery="passive")

# 完全静默：AI 不知情；前端只通过 task_update 看到任务终态
return await self.finish(data={"summary": "..."}, delivery="silent")
```

> **`push_message()` 不再用 `delivery`**——改成 `visibility` + `ai_behavior`
> 两条独立轴（见上面 `push_message(**kwargs) -> object` 节）。旧 `delivery=`
> / `reply=` 仍能用但会 emit DeprecationWarning，v0.9 移除。

#### LLM 结果字段过滤

通过 `llm_result_fields` 控制主 LLM 能看到结果中的哪些字段：

```python
# 静态入口：在装饰器中声明
@plugin_entry(llm_result_fields=["summary"])
async def search(self, query: str):
    return await self.finish(data={"summary": "3条结果", "raw_results": [...]})

# 动态入口：在注册时声明
self.register_dynamic_entry(
    entry_id="my-tool",
    handler=handler,
    llm_result_fields=["summary"],
)
```

#### `push_message` 的 `message_type` 取值

`ctx.push_message()` 接受的 `message_type` 包含两类：

> ⚠️ **`message_type` 已废弃，v0.9 移除**。新代码请用 `parts` 列表 +
> `visibility` / `ai_behavior` 描述消息——对照表见上面 push_message
> 节的「已废弃字段」。如果你需要扩展 push_message 的能力，请直接在
> `parts` 加新的 `type`，**不要**新增 `message_type` 值。

### 3.4 Result 类型：Ok / Err

SDK 使用 Rust 风格的 Result 类型进行错误处理，替代传统异常：

```python
from plugin.sdk.plugin import Ok, Err, unwrap, unwrap_or, SdkError

# 返回成功
return Ok({"data": result})

# 返回错误
return Err(SdkError("出错了"))

# 消费结果
result = await self.plugins.call_entry("other:do_stuff")
if isinstance(result, Ok):
    data = result.value
else:
    error = result.error
    self.logger.error(f"调用失败: {error}")

# 辅助函数
value = unwrap(result)           # Err 时抛出异常
value = unwrap_or(result, None)  # Err 时返回默认值
```

### 3.5 跨插件调用 (Plugins)

通过 `self.plugins` 访问：

```python
# 列出所有插件
result = await self.plugins.list()

# 只列出已启用的插件
result = await self.plugins.list(enabled=True)

# 获取插件 ID 列表
result = await self.plugins.list_ids()

# 检查插件是否存在
result = await self.plugins.exists("other_plugin")

# 调用另一个插件的入口点
result = await self.plugins.call_entry("other_plugin:do_work", {"key": "value"})

# 调用并确保返回 JSON 对象
result = await self.plugins.call_entry_json("other_plugin:get_data")

# 要求插件必须存在且已启用
result = await self.plugins.require_enabled("dependency_plugin")
```

所有方法返回 `Result` 类型 — 使用前用 `isinstance(result, Ok)` 检查。

### 3.6 持久化存储 (PluginStore)

```python
from plugin.sdk.plugin import PluginStore

store = PluginStore(self.ctx)
await store.set("key", {"count": 42})
value = await store.get("key")  # → {"count": 42}
```

### 3.7 消息类型

`push_message` 没有 `message_type` 字段了——内容形态由 `parts` 各元素的
`type` 描述（`text` / `image` / `audio` / `video` / `ui_action`），下游
路由由 `visibility` + `ai_behavior` 决定。详见上面 [3.3 节
`push_message`](#push-message-v2)。

`message_type=...` 仍可作为旧 API 兼容形参传入，但每次会触发
`DeprecationWarning`，v0.9 移除。

### 3.8 优先级

| 范围 | 级别 | 用途 |
|------|------|------|
| 0-2 | 低 | 信息性消息 |
| 3-5 | 中 | 一般通知 |
| 6-8 | 高 | 重要通知 |
| 9-10 | 紧急 | 需要立即处理 |


### 3.9 音乐播放安全接口 (Music UI)

N.E.K.O 默认只允许来自 `music.163.com` 等已知安全域名的音频播放。如果你的插件引入了新的音频来源，必须在播放前通过以下接口将其域名加入白名单。

#### 3.9.1 前端扩展 API (JavaScript)

如果你的插件包含 Web UI（通过 `register_static_ui` 提供），你可以通过浏览器全局对象 `window.MusicPluginAPI` 注册域名。

**场景**：插件页面动态加载音频。

| 方法 | 参数 | 说明 |
|------|------|------|
| `getAllowlist()` | 无 | 获取当前所有合法的域名/IP 列表 |
| `addAllowlist(input)` | `string \| Array<string>` | 添加新域名。支持 URL 自动提取，自动去重 |

**推荐接入模式（异步安全）**：
由于插件页面可能通过 `<iframe>` 嵌入且加载时机不确定，建议监听 `music-ui-ready` 事件：

```javascript
window.addEventListener('music-ui-ready', () => {
    const api = window.MusicPluginAPI || (window.parent && window.parent.MusicPluginAPI);
    if (api) {
        api.addAllowlist(['my-music-cdn.com', 'https://safe-storage.org/stream/']);
    }
}, { once: true });
```

#### 3.9.2 后端插件 API (Python SDK)

后端插件（例如 AI 自动搜索并播放音乐）通过 `push_message` 推一条
`ui_action: media_allowlist_add` 给前端：

```python
@plugin_entry(id="play_external")
def play_external(self, url: str, **_):
    # 先加白域名，确保播放不会被 Music UI 拦截
    self.push_message(
        ai_behavior="blind",
        parts=[{
            "type": "ui_action",
            "action": "media_allowlist_add",
            "domains": [url],
        }],
    )

    # 然后让前端直接开播
    self.push_message(
        visibility=["chat"],
        ai_behavior="blind",
        parts=[{
            "type": "ui_action",
            "action": "media_play_url",
            "url": url,
            "media_type": "audio",
        }],
    )
    return Ok({"status": "playing", "url": url})
```

> 旧 SDK helper `self.register_music_domains(...)` 已**删除**。

---

## 第四章：装饰器详解

### 4.1 @neko_plugin

标记类为 N.E.K.O 插件，**所有插件类必须使用**：

```python
@neko_plugin
class MyPlugin(NekoPluginBase):
    pass
```

### 4.2 @plugin_entry

定义外部可调用的入口点：

```python
@plugin_entry(
    id="process",                  # 入口点 ID（省略时自动使用方法名）
    name="处理数据",                # 显示名称
    description="处理输入数据",      # 描述
    input_schema={...},            # JSON Schema 验证
    params=MyParamsModel,          # 或 Pydantic 模型（自动生成 schema）
    kind="action",                 # "action" | "service" | "hook" | "custom"
    auto_start=False,              # 加载时自动启动
    persist=False,                 # 跨重载持久化
    model_validate=True,           # 启用 Pydantic 验证
    timeout=30.0,                  # 执行超时（秒）
    llm_result_fields=["text"],    # LLM 消费的字段
    llm_result_model=MyResult,     # 结果的 Pydantic 模型
    metadata={"category": "data"}  # 额外元数据
)
def process(self, data: str, **_):
    return Ok({"result": data})
```

#### 参数说明

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `id` | `str` | 方法名 | 入口点唯一标识符 |
| `name` | `str` | `None` | 显示名称 |
| `description` | `str` | `""` | 描述 |
| `input_schema` | `dict` | `None` | 输入的 JSON Schema |
| `params` | `type` | `None` | Pydantic 模型（自动生成 `input_schema`） |
| `kind` | `str` | `"action"` | 入口类型 |
| `auto_start` | `bool` | `False` | 加载时自动启动 |
| `persist` | `bool` | `None` | 跨重载持久化状态 |
| `model_validate` | `bool` | `True` | 启用 Pydantic 验证 |
| `timeout` | `float` | `None` | 执行超时（秒） |
| `llm_result_fields` | `list[str]` | `None` | LLM 结果提取字段 |
| `llm_result_model` | `type` | `None` | 结果的 Pydantic 模型 |
| `metadata` | `dict` | `None` | 额外元数据 |

> 提示：始终在入口函数签名中包含 `**_`，以优雅处理未使用的参数。

### 4.3 @lifecycle

定义生命周期事件处理器：

```python
@lifecycle(id="startup")
def on_startup(self, **_):
    return Ok({"status": "ready"})

@lifecycle(id="shutdown")
def on_shutdown(self, **_):
    return Ok({"status": "stopped"})

@lifecycle(id="reload")
def on_reload(self, **_):
    return Ok({"status": "reloaded"})
```

有效的生命周期 ID：`startup`, `shutdown`, `reload`, `freeze`, `unfreeze`, `config_change`

### 4.4 @timer_interval

定义周期执行的定时任务：

```python
@timer_interval(
    id="cleanup",
    seconds=3600,           # 每小时执行一次
    name="清理任务",
    auto_start=True          # 自动启动（默认 True）
)
def cleanup(self, **_):
    # 在独立线程中运行
    return Ok({"cleaned": True})
```

> 注意：定时任务在独立线程中运行。异常会被记录但不会停止定时器。

### 4.5 @message

定义来自主系统的消息处理器：

```python
@message(
    id="handle_chat",
    source="chat",           # 按消息来源过滤
    auto_start=True
)
def handle_chat(self, text: str, sender: str, **_):
    return Ok({"handled": True})
```

### 4.6 @on_event

通用事件处理器：

```python
@on_event(
    event_type="custom_event",
    id="my_handler",
    kind="hook"
)
def custom_handler(self, event_data: str, **_):
    return Ok({"processed": True})
```

### 4.7 @custom_event

带触发方式控制的事件处理器：

```python
@custom_event(
    event_type="data_refresh",
    id="refresh_handler",
    trigger_method="message",
    auto_start=False
)
def on_refresh(self, source: str, **_):
    return Ok({"refreshed": True})
```

### 4.8 Hook 装饰器（AOP 面向切面）

Hook 装饰器提供面向切面编程能力，可以拦截入口点的执行。

#### @before_entry — 前置钩子

```python
@before_entry(target="process", priority=0)
def validate_input(self, *, args, entry_id, **_):
    if not args.get("data"):
        return Err(SdkError("data 是必填的"))
    # 返回 None 继续执行，返回 Err 中止
```

#### @after_entry — 后置钩子

```python
@after_entry(target="process", priority=0)
def log_result(self, *, result, entry_id, **_):
    self.logger.info(f"入口 {entry_id} 返回: {result}")
    # 返回 None 保留原结果，返回新值替换
```

#### @around_entry — 环绕钩子

```python
@around_entry(target="process", priority=0)
async def timing_wrapper(self, *, proceed, args, **_):
    import time
    start = time.time()
    result = await proceed(**args)
    elapsed = time.time() - start
    self.logger.info(f"耗时 {elapsed:.2f}s")
    return result
```

#### @replace_entry — 替换钩子

```python
@replace_entry(target="old_entry", priority=0)
def new_implementation(self, **kwargs):
    return Ok({"replaced": True})
```

#### Hook 参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `target` | `str` | `"*"` | 目标入口 ID（`"*"` = 所有入口） |
| `priority` | `int` | `0` | 执行顺序（越小越先） |
| `condition` | `str` | `None` | 可选条件表达式 |

### 4.9 命名空间风格：`plugin.*`

更简洁的替代语法：

```python
from plugin.sdk.plugin import plugin

@plugin.entry(id="greet", description="打招呼")
def greet(self, name: str = "World", **_):
    return Ok({"message": f"Hello, {name}!"})

@plugin.lifecycle(id="startup")
def on_startup(self, **_):
    return Ok({"status": "ready"})

@plugin.hook(target="greet", timing="before")
def validate(self, *, args, **_):
    pass

@plugin.timer(id="heartbeat", seconds=60)
def heartbeat(self, **_):
    return Ok({"alive": True})
```

---

## 第五章：上下文与运行时

### 5.1 PluginContext (ctx)

`ctx` 对象在构造时由宿主注入：

| 属性 | 类型 | 说明 |
|------|------|------|
| `ctx.plugin_id` | `str` | 插件标识符 |
| `ctx.config_path` | `Path` | `plugin.toml` 的路径 |
| `ctx.logger` | `Logger` | 日志实例 |
| `ctx.bus` | `Bus` | 事件总线 |
| `ctx.metadata` | `dict` | 插件元数据 |

### 5.2 事件总线 (Bus)

```python
# 发布事件
self.bus.emit("my_event", {"key": "value"})

# 订阅事件（通常在 startup 中）
self.bus.on("some_event", self._handle_event)
```

### 5.3 PluginConfig

结构化配置，支持多环境 Profile：

```python
from plugin.sdk.plugin import PluginConfig

config = PluginConfig(self.ctx)
timeout = config.get("timeout", default=30)
```

---

## 第六章：完整示例

### 6.1 带 Result 类型的基础插件

```python
from typing import Any
from plugin.sdk.plugin import (
    NekoPluginBase, neko_plugin, plugin_entry, lifecycle,
    Ok, Err, SdkError,
)

@neko_plugin
class GreeterPlugin(NekoPluginBase):
    def __init__(self, ctx: Any):
        super().__init__(ctx)
        self.logger = ctx.logger
        self.greet_count = 0

    @lifecycle(id="startup")
    def on_startup(self, **_):
        self.logger.info("GreeterPlugin 就绪")
        return Ok({"status": "ready"})

    @plugin_entry(
        id="greet",
        name="问候",
        description="根据名字打招呼",
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "default": "World"}
            }
        }
    )
    def greet(self, name: str = "World", **_):
        if not name.strip():
            return Err(SdkError("名字不能为空"))

        self.greet_count += 1
        return Ok({
            "message": f"Hello, {name}!",
            "total_greets": self.greet_count,
        })
```

### 6.2 异步 API 客户端 + 跨插件调用

```python
import aiohttp
from typing import Any, Optional
from plugin.sdk.plugin import (
    NekoPluginBase, neko_plugin, plugin_entry, lifecycle,
    Ok, Err, SdkError, unwrap_or,
)

@neko_plugin
class APIClientPlugin(NekoPluginBase):
    def __init__(self, ctx: Any):
        super().__init__(ctx)
        self.logger = ctx.logger
        self.session: Optional[aiohttp.ClientSession] = None

    @lifecycle(id="startup")
    async def startup(self, **_):
        self.session = aiohttp.ClientSession()
        return Ok({"status": "ready"})

    @lifecycle(id="shutdown")
    async def shutdown(self, **_):
        if self.session:
            await self.session.close()
        return Ok({"status": "stopped"})

    @plugin_entry(id="fetch")
    async def fetch(self, endpoint: str, method: str = "GET", **_):
        try:
            async with self.session.request(method, endpoint) as response:
                data = await response.json()
                return Ok({"status": response.status, "data": data})
        except Exception as e:
            return Err(SdkError(f"请求失败: {e}"))

    @plugin_entry(id="fetch_with_cache")
    async def fetch_with_cache(self, endpoint: str, **_):
        # 跨插件调用：先查缓存插件
        cached = await self.plugins.call_entry("cache_plugin:get", {"key": endpoint})
        cached_value = unwrap_or(cached, None)
        if cached_value and cached_value.get("hit"):
            return Ok(cached_value["data"])

        result = await self.fetch(endpoint=endpoint)
        if isinstance(result, Ok):
            await self.plugins.call_entry("cache_plugin:set", {"key": endpoint, "value": result.value})
        return result
```

### 6.3 带 Hook 和定时器的插件

```python
import time
from typing import Any
from plugin.sdk.plugin import (
    NekoPluginBase, neko_plugin, plugin_entry, lifecycle,
    timer_interval, before_entry, after_entry,
    Ok, Err, SdkError,
)

@neko_plugin
class MonitoredPlugin(NekoPluginBase):
    def __init__(self, ctx: Any):
        super().__init__(ctx)
        self.logger = ctx.logger
        self.call_stats: dict[str, int] = {}

    @lifecycle(id="startup")
    def on_startup(self, **_):
        return Ok({"status": "ready"})

    @before_entry(target="*")
    def count_calls(self, *, entry_id, **_):
        """统计每个入口点的调用次数"""
        self.call_stats[entry_id] = self.call_stats.get(entry_id, 0) + 1

    @after_entry(target="*")
    def log_results(self, *, entry_id, result, **_):
        """记录每个入口点的返回结果"""
        self.logger.info(f"[{entry_id}] result={result}")

    @plugin_entry(id="process", description="处理数据")
    def process(self, data: str, **_):
        return Ok({"processed": data.upper()})

    @plugin_entry(id="stats", description="获取调用统计")
    def stats(self, **_):
        return Ok({"stats": dict(self.call_stats)})

    @timer_interval(id="health_check", seconds=300, auto_start=True)
    def health_check(self, **_):
        self.report_status({
            "status": "healthy",
            "total_calls": sum(self.call_stats.values()),
        })
        return Ok({"healthy": True})
```

### 6.4 带持久化存储的插件

```python
from typing import Any
from plugin.sdk.plugin import (
    NekoPluginBase, neko_plugin, plugin_entry,
    PluginStore, Ok, Err, SdkError,
)

@neko_plugin
class NotesPlugin(NekoPluginBase):
    def __init__(self, ctx: Any):
        super().__init__(ctx)
        self.store = PluginStore(ctx)

    @plugin_entry(id="save_note")
    async def save_note(self, title: str, content: str, **_):
        await self.store.set(f"note:{title}", {"title": title, "content": content})
        return Ok({"saved": title})

    @plugin_entry(id="get_note")
    async def get_note(self, title: str, **_):
        note = await self.store.get(f"note:{title}")
        if note is None:
            return Err(SdkError(f"笔记未找到: {title}"))
        return Ok(note)
```

---

## 第七章：Extension 扩展开发

### 7.1 什么是 Extension？

Extension 为现有插件添加路由和钩子，无需修改原插件代码。它运行在宿主插件的进程内（不是独立进程）。

### 7.2 何时使用 Extension？

- 想给现有插件添加新命令
- 想钩住另一个插件的入口点
- 想实现插件内的模块化代码组织

### 7.3 创建 Extension

```python
from plugin.sdk.extension import (
    NekoExtensionBase, extension, extension_entry, extension_hook,
    Ok, Err,
)

@extension
class MyExtension(NekoExtensionBase):
    """为宿主插件添加额外命令"""

    @extension_entry(id="extra_command", description="扩展添加的额外命令")
    def extra_command(self, param: str = "", **_):
        return Ok({"extended": True, "param": param})

    @extension_hook(target="original_entry", timing="before")
    def validate(self, *, args, **_):
        # 在宿主插件的 "original_entry" 之前运行
        if not args.get("required_field"):
            return Err("缺少 required_field")
```

### 7.4 Extension 工作原理

1. 宿主在配置中注册 Extension
2. 启动时，宿主将 Extension 作为 `PluginRouter` 实例注入
3. Extension 的入口点在宿主插件的命名空间下可访问
4. Extension 的钩子可以拦截宿主的入口点

### 7.5 Extension SDK 导出

从 `plugin.sdk.extension` 导入：

- `NekoExtensionBase` — Extension 基类
- `extension` — 类装饰器
- `extension_entry` — 定义入口点
- `extension_hook` — 定义钩子
- `Ok`, `Err`, `Result` — Result 类型
- `PluginRouter` — 路由器
- `PluginConfig` — 配置
- `CallChain`, `AsyncCallChain` — 调用链追踪
- 完整的日志和错误处理工具

---

## 第八章：Adapter 适配器开发

### 8.1 什么是 Adapter？

Adapter 将外部协议（MCP、NoneBot 等）的请求翻译成内部插件调用。它实现了**网关管线 (Gateway Pipeline)** 模式。

### 8.2 何时使用 Adapter？

- 想通过 MCP（Model Context Protocol）暴露 N.E.K.O 插件
- 想接收 NoneBot 消息并路由到插件
- 想桥接任何外部协议到插件系统

### 8.3 网关管线架构

```
外部请求 → Normalizer → PolicyEngine → RouteEngine → PluginInvoker → ResponseSerializer → 外部响应
```

| 阶段 | 职责 |
|------|------|
| **Normalizer** | 将外部协议格式转换为 `GatewayRequest` |
| **PolicyEngine** | 访问控制、速率限制、验证 |
| **RouteEngine** | 决定调用哪个插件/入口 |
| **PluginInvoker** | 执行实际的插件调用 |
| **ResponseSerializer** | 将结果转换回外部协议格式 |

### 8.4 创建 Adapter

```python
from plugin.sdk.plugin import neko_plugin, plugin_entry, lifecycle, Ok, Err, SdkError
from plugin.sdk.adapter import (
    AdapterGatewayCore, DefaultPolicyEngine, NekoAdapterPlugin,
)
from plugin.sdk.adapter.gateway_models import ExternalRequest

@neko_plugin
class MyProtocolAdapter(NekoAdapterPlugin):
    def __init__(self, ctx):
        super().__init__(ctx)
        self.gateway = None

    @lifecycle(id="startup")
    async def startup(self, **_):
        self.gateway = AdapterGatewayCore(
            normalizer=MyNormalizer(),
            policy_engine=DefaultPolicyEngine(),
            route_engine=MyRouteEngine(),
            invoker=MyInvoker(self.ctx),
            serializer=MySerializer(),
            logger=self.logger,
        )
        return Ok({"status": "ready"})

    @plugin_entry(id="handle_request")
    async def handle_request(self, raw_data: dict, **_):
        external = ExternalRequest(protocol="my_protocol", raw=raw_data)
        response = await self.gateway.process(external)
        return Ok(response.to_dict())
```

### 8.5 Adapter 模式

| 模式 | 说明 |
|------|------|
| `GATEWAY` | 完整管线处理 |
| `ROUTER` | 仅路由（跳过策略） |
| `BRIDGE` | 直接透传 |
| `HYBRID` | 按请求选择模式 |

### 8.6 内置参考：MCP Adapter

参见 `plugin/plugins/mcp_adapter/` 获取完整的 Adapter 实现，演示了：
- 自定义 Normalizer (`MCPRequestNormalizer`)
- 自定义路由引擎 (`MCPRouteEngine`)
- 自定义调用器 (`MCPPluginInvoker`)
- 自定义序列化器 (`MCPResponseSerializer`)
- 自定义传输层 (`MCPTransportAdapter`)

### 8.7 Adapter SDK 导出

从 `plugin.sdk.adapter` 导入：

- `AdapterBase`, `AdapterConfig`, `AdapterContext`, `AdapterMode` — 基础类
- `NekoAdapterPlugin` — 适配器插件基类
- `AdapterGatewayCore` — 网关核心
- `DefaultPolicyEngine`, `DefaultRouteEngine` 等 — 默认管线组件
- `ExternalRequest`, `GatewayRequest`, `GatewayResponse` 等 — 数据模型
- 装饰器：`on_adapter_startup`, `on_adapter_shutdown`, `on_mcp_tool`, `on_mcp_resource`, `on_nonebot_message`

---

## 第九章：高级主题

### 9.1 异步编程

入口点可以是同步或异步的：

```python
# 同步入口（在线程池中运行）
@plugin_entry(id="sync_task")
def sync_task(self, **_):
    return Ok({"result": "done"})

# 异步入口（在事件循环中运行）
@plugin_entry(id="async_task")
async def async_task(self, url: str, **_):
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            return Ok({"data": await response.json()})
```

### 9.2 线程安全

定时任务在独立线程中运行，保护共享状态：

```python
import threading

@neko_plugin
class ThreadSafePlugin(NekoPluginBase):
    def __init__(self, ctx):
        super().__init__(ctx)
        self._lock = threading.Lock()
        self._counter = 0

    @plugin_entry(id="increment")
    def increment(self, **_):
        with self._lock:
            self._counter += 1
            return Ok({"count": self._counter})

    @timer_interval(id="report", seconds=60, auto_start=True)
    def report(self, **_):
        with self._lock:
            count = self._counter
        self.report_status({"count": count})
```

### 9.3 自定义配置

```python
import json

class ConfigurablePlugin(NekoPluginBase):
    def __init__(self, ctx):
        super().__init__(ctx)
        config_file = self.config_dir / "config.json"
        if config_file.exists():
            self.config = json.loads(config_file.read_text())
        else:
            self.config = {"timeout": 30}
```

### 9.4 SQLite 数据持久化

```python
import sqlite3

class PersistentPlugin(NekoPluginBase):
    def __init__(self, ctx):
        super().__init__(ctx)
        self.db_path = self.data_path("records.db")
        self.data_path().mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
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
```

---

## 第十章：最佳实践

### 10.1 始终使用 Result 类型

```python
@plugin_entry(id="process")
def process(self, data: str, **_):
    if not data:
        return Err(SdkError("data 是必填的"))
    try:
        result = self._do_work(data)
        return Ok({"result": result})
    except Exception as e:
        self.logger.exception(f"意外错误: {e}")
        return Err(SdkError("内部错误"))
```

### 10.2 合理使用日志级别

| 级别 | 用途 |
|------|------|
| `debug` | 详细诊断信息 |
| `info` | 正常运行里程碑 |
| `warning` | 意外但已处理的情况 |
| `error` | 需要关注的错误 |
| `exception` | 带完整堆栈的错误 |

### 10.3 跨插件调用错误处理

```python
@plugin_entry(id="orchestrate")
async def orchestrate(self, **_):
    dep = await self.plugins.require_enabled("dependency_plugin")
    if isinstance(dep, Err):
        return Err(SdkError("依赖插件不可用"))

    result = await self.plugins.call_entry("dependency_plugin:do_work", {"key": "val"})
    if isinstance(result, Err):
        self.logger.error(f"跨插件调用失败: {result.error}")
        return Err(SdkError("依赖调用失败"))

    return Ok({"combined": result.value})
```

### 10.4 优雅关闭

```python
@lifecycle(id="shutdown")
async def on_shutdown(self, **_):
    if self.session:
        await self.session.close()
    self.logger.info("插件优雅关闭")
    return Ok({"status": "stopped"})
```

### 10.5 使用路径工具

```python
# 插件目录（plugin.toml 所在位置）
config_file = self.config_dir / "config.json"

# 数据目录
db_path = self.data_path("cache.db")       # → <plugin_dir>/data/cache.db
logs_dir = self.data_path("logs")          # → <plugin_dir>/data/logs/
```

### 10.6 插件发布检查清单

- [ ] 所有入口点返回 `Ok`/`Err`（不是裸 dict 或异常）
- [ ] 实现了 `@lifecycle(id="startup")` 和 `@lifecycle(id="shutdown")`
- [ ] 所有接受参数的入口点定义了 `input_schema`
- [ ] 所有入口点签名包含 `**_`
- [ ] 使用 Logger 而非 `print()`
- [ ] 如果使用定时器，共享状态受锁保护
- [ ] 跨插件调用处理了 `Err` 结果
- [ ] `plugin.toml` 的 `entry` 路径和 SDK 版本约束正确

---

## 第十一章：常见问题

### Q: 插件崩溃会影响主系统吗？

不会。每个插件运行在独立进程中，崩溃不影响主系统或其他插件。

### Q: 如何在插件间传递数据？

使用 `self.plugins.call_entry("target_plugin:entry_id", {"key": "value"})` 进行跨插件调用。所有返回值都是 `Result` 类型。

### Q: 同步还是异步？

都支持。I/O 密集型操作建议用异步。同步入口点在线程池中运行，异步入口点在事件循环中运行。

### Q: 如何调试插件？

1. 使用 `self.logger` 输出日志
2. 使用 `self.report_status()` 报告状态
3. 检查插件进程的标准输出/错误输出

### Q: Plugin vs Extension vs Adapter 怎么选？

- **Plugin**：99% 的情况，写独立功能
- **Extension**：给别人的插件加功能，不改原代码
- **Adapter**：桥接外部协议（MCP、NoneBot 等）

### Q: `shared` 包是什么？我需要用它吗？

`shared` 是 SDK 的内部实现细节，包含 Plugin/Extension/Adapter 三者共享的底层基础设施。**你不应该直接导入它。** 始终从 `plugin.sdk.plugin`、`plugin.sdk.extension` 或 `plugin.sdk.adapter` 导入。

---

## 第十二章：API 参考

### Plugin SDK (`plugin.sdk.plugin`)

| 类别 | 导出 |
|------|------|
| **基类** | `NekoPluginBase`, `PluginMeta` |
| **装饰器** | `neko_plugin`, `plugin_entry`, `lifecycle`, `timer_interval`, `message`, `on_event`, `custom_event`, `hook`, `before_entry`, `after_entry`, `around_entry`, `replace_entry`, `plugin` |
| **Result** | `Ok`, `Err`, `Result`, `unwrap`, `unwrap_or` |
| **运行时** | `Plugins`, `PluginRouter`, `PluginConfig`, `PluginStore`, `SystemInfo`, `MemoryClient` |
| **错误** | `SdkError`, `TransportError` |
| **日志** | `get_plugin_logger` |

### Extension SDK (`plugin.sdk.extension`)

| 类别 | 导出 |
|------|------|
| **基类** | `NekoExtensionBase`, `ExtensionMeta` |
| **装饰器** | `extension`, `extension_entry`, `extension_hook` |
| **运行时** | `PluginRouter`, `PluginConfig`, `ExtensionRuntime`, `MessagePlaneTransport` |
| **Result** | `Ok`, `Err`, `Result` + 完整 Result 工具集 |
| **调用链** | `CallChain`, `AsyncCallChain` + 追踪工具 |

### Adapter SDK (`plugin.sdk.adapter`)

| 类别 | 导出 |
|------|------|
| **基类** | `AdapterBase`, `AdapterConfig`, `AdapterContext`, `AdapterMode` |
| **插件基类** | `NekoAdapterPlugin` |
| **网关** | `AdapterGatewayCore`, `DefaultPolicyEngine`, `DefaultRouteEngine`, `DefaultRequestNormalizer`, `DefaultResponseSerializer`, `CallablePluginInvoker` |
| **数据模型** | `ExternalRequest`, `GatewayRequest`, `GatewayResponse`, `GatewayAction`, `GatewayError`, `RouteDecision`, `RouteMode` |
| **协议** | `TransportAdapter`, `RequestNormalizer`, `PolicyEngine`, `RouteEngine`, `PluginInvoker`, `ResponseSerializer` |
| **装饰器** | `on_adapter_startup`, `on_adapter_shutdown`, `on_mcp_tool`, `on_mcp_resource`, `on_nonebot_message` |
| **类型** | `Protocol`, `RouteTarget`, `AdapterMessage`, `AdapterResponse`, `RouteRule` |
