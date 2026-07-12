# 插件 SDK v0.9 迁移指南

本文是插件系统接口收敛后的迁移清单。部分接口已经直接移除；剩余的 `push_message` v1 兼容参数将在 v0.9 移除。

## 一览

| 旧接口 | 状态 | 替代方案 |
|---|---|---|
| `type = "script"` / script 插件 | 已移除，不提供兼容层 | 使用普通 `plugin` 包和 `NekoPluginBase` |
| `plugin._types.result` | 已移除 | 从 `plugin.sdk.plugin` 导入 `Result`、`Ok`、`Err`、`SdkError` 等 |
| Bus 的 `where_*` 与列表集合运算 | 已移除 | 组合 `get()`、`filter()` / `where()`、`sort()`、`limit()`、`watch()` |
| `get_message_plane_all` | 已移除 | 使用有界的 `await self.bus.messages.get(...)` 查询 |
| Bus 增量/本地 fast path | 已移除 | 使用标准的、有界且可重放的 read/watch 管线 |
| 高层 `self.memory` / SDK `MemoryClient` | 已移除 | 使用 `self.bus.memory.get(...)` 或 `await self.ctx.query_memory(...)` |
| Extension 插件类型、`[plugin.host]`、`plugin.sdk.extension` | 已移除，不提供兼容层 | 将 Router 合并进所属普通 Plugin，或把该包改造成独立 Plugin |
| `push_message` v1 字段 | 已弃用，v0.9 移除 | 使用 `parts`、`visibility`、`ai_behavior` |

## 包类型

script 插件没有兼容垫片。请把 manifest 和入口类改为标准 Plugin：

```toml
[plugin]
type = "plugin"
```

```python
from plugin.sdk.plugin import NekoPluginBase, neko_plugin, plugin_entry, Ok

@neko_plugin
class MyPlugin(NekoPluginBase):
    @plugin_entry(id="run")
    async def run(self, **_):
        return Ok({"status": "done"})
```

Extension 没有兼容垫片。删除 `type = "extension"` 和 `[plugin.host]` 后，要么把 Router 模块移入原宿主并调用 `self.include_router(router)`，要么把它改造成普通 `NekoPluginBase` 包。从 `plugin.sdk.extension` 的导入应改为 `plugin.sdk.plugin` 中对应的公共符号。`PluginRouter` 只继续用于普通 Plugin 内部的代码组织。

## Result 导入

现在只有一套公共 Result：

```python
# 旧代码
from plugin._types.result import Result, Ok, Err

# 新代码
from plugin.sdk.plugin import Result, Ok, Err, SdkError
```

不要为已删除的模块另建兼容别名。

## Bus 查询与监听

Bus 是宿主状态的只读/监听门面，不是可发布消息的通用事件总线。应查询一个明确的命名空间，并限制结果数量：

```python
events = await self.bus.events.get(plugin_id=self.plugin_id)
events = (
    events
    .filter(priority_min=1)
    .filter(type="TASK_FINISHED")
    .sort(by="timestamp", reverse=True)
    .limit(20)
)

watcher = events.watch(self.ctx)

@watcher.subscribe(on="add")  # 仅支持 "add"、"del"、"change"
def on_added(delta):
    for event in delta.added:
        self.logger.info(f"event: {event.type}")

watcher.start()
```

可调用形式 `filter(predicate)`、`where(predicate)` 与 `sort(key=callable)` 仍可处理本地快照，但不能重放。不要在 `watch()` 前使用它们；watcher 链应使用结构化 `filter(field=value, ...)` 与 `sort(by=...)`。只有 `messages`、`events`、`lifecycle` 支持 `watch()`；`conversations` 与 `memory` 是只读快照。

已删除的辅助方法包括 `where_in`、`where_eq`、`where_contains`、`where_regex`、`where_gt`、`where_ge`、`where_lt`、`where_le`，以及 BusList 的交集/差集运算。把条件改写为 `filter(...)` 或 `where(predicate)`；若必须合并两个快照，请按记录 key 使用普通 Python 显式处理。

`get_message_plane_all` 原本按页读取 Message Plane 的 `messages` store，并受 `max_items` 上限约束。由于增量 `after_seq` 传输路径已经删除，它没有一对一替代接口。请改用有界的 `await self.bus.messages.get(max_count=..., ...)`，再按需使用结构化过滤、`sort(by=...)` 与 `limit()`。

已删除的 Bus fast path 是加速分支，例如 BusList `fast_mode`、增量 reload 游标、本地消息缓存和 revision/delta 快捷路径。`watch()` 所需的 replay plan 与 trace 仍然保留；`get()` / 结构化 `filter(field=value)` / `sort(by=...)` / `limit()` 构成可重放链。它们与旧的 `push_message(fast_mode=...)` 参数不是同一条路径。后者属于 v1 兼容接口，也将在 v0.9 移除；v2 改走标准的逐条宿主投递路径，因此移除旧批处理/背压优化时应重新压测高频生产者。

## Memory

旧 SDK `MemoryClient` 把记录读取和语义检索混在一起。现在应明确选择操作：

```python
# 读取一个 bucket 的最近记录
records = await self.bus.memory.get(bucket_id="default", limit=20)

# 语义检索
matches = await self.ctx.query_memory("default", "用户偏好是什么？")
```

通过 `self.bus.memory` 读取 memory 记录和使用其类型化记录的能力仍然保留；删除的是高层 `self.memory` 属性以及 SDK/runtime 的 `MemoryClient` 门面。

## `push_message` v2

新代码只使用标准 schema：

```python
self.push_message(
    source="my_plugin",
    visibility=["chat"],
    ai_behavior="blind",
    parts=[{"type": "text", "text": "任务已完成"}],
)
```

如果某个调用无法在同一改动中完成迁移，不要为了加标记而批量修改正在维护的插件源码，这很容易与插件维护者的改动冲突。优先用 issue 或 PR 跟踪 warning；在你自己维护的插件源码中，可以使用下面的局部注释：

```python
# TODO(plugin-api-v0.9): 在 v0.9 前替换 push_message v1 字段；跟踪项：<issue-or-PR>。
```

v1 的 `message_type`、`description`、`content`、`binary_data`、`binary_url`、`mime`、`delivery`、`reply`、`unsafe`、`fast_mode` 仅用于兼容。静态检查和运行时 warning 会指出这些调用，请在 v0.9 前迁移。完整映射见 [`push_message` v2 说明](/changelog/plugin-push-message-v2)。

## 验证

```bash
uv run neko-plugin check <plugin_id-or-path> --strict
```

所有旧 `push_message` warning 都应视为待迁移项，不要通过屏蔽 warning 解决。
