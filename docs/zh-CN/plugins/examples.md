# 插件示例

## 使用 Result 类型的基础插件

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
        self.greet_count = 0

    @lifecycle(id="startup")
    async def on_startup(self, **_):
        self.logger.info("GreeterPlugin ready")
        return Ok({"status": "ready"})

    @plugin_entry(
        id="greet",
        name="Greet",
        description="Greet someone by name",
        # 只发布给调用方，运行时不校验。
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "default": "World"}
            }
        }
    )
    async def greet(self, name: str = "World", **_):
        if not name.strip():
            return Err(SdkError("Name cannot be empty"))

        self.greet_count += 1
        return Ok({
            "message": f"Hello, {name}!",
            "total_greets": self.greet_count,
        })
```

## 带跨插件调用的异步 API 客户端

```python
from typing import Any, Literal

import aiohttp
from pydantic import BaseModel
from plugin.sdk.plugin import (
    NekoPluginBase, neko_plugin, plugin_entry,
    Ok, Err, SdkError, unwrap_or,
)

class FetchParams(BaseModel):
    endpoint: str
    method: Literal["GET", "POST"] = "GET"

@neko_plugin
class APIClientPlugin(NekoPluginBase):
    def __init__(self, ctx: Any):
        super().__init__(ctx)
        self.base_url = "https://api.example.com"

    @plugin_entry(id="fetch", name="Fetch Data", params=FetchParams)
    async def fetch(self, endpoint: str, method: str = "GET", **_):
        try:
            url = f"{self.base_url}/{endpoint.lstrip('/')}"
            # 每次调用新建 session：startup、入口调用和 shutdown 运行在不同的事件循环里，
            # startup 里创建的 session 不能在这里使用，也无法在 shutdown 里干净地关闭。
            async with aiohttp.ClientSession() as session:
                async with session.request(method, url) as response:
                    data = await response.json()
                    return Ok({"status": response.status, "data": data})
        except Exception as e:
            return Err(SdkError(f"Request failed: {e}"))

    @plugin_entry(id="fetch_with_cache")
    async def fetch_with_cache(self, endpoint: str, **_):
        # 跨插件调用：先检查缓存插件
        cached = await self.plugins.call_entry(
            "cache_plugin:get", {"key": endpoint}
        )
        cached_value = unwrap_or(cached, None)
        if cached_value and cached_value.get("hit"):
            return Ok(cached_value["data"])

        # 获取新数据
        result = await self.fetch(endpoint=endpoint)
        if isinstance(result, Ok):
            # 存入缓存插件
            await self.plugins.call_entry(
                "cache_plugin:set",
                {"key": endpoint, "value": result.value}
            )
        return result
```

`aiohttp` 是第三方包：请在插件的 `pyproject.toml` 的 `[project].dependencies` 中声明，并运行 `neko-plugin sync <plugin>`，不要依赖宿主环境碰巧装了它。

## 带调用计数和定时器的插件

钩子装饰器（`@before_entry`、`@after_entry` 等）只登记元数据，目前不会执行（见[装饰器](./decorators)的「钩子装饰器」一节），所以要在入口内自行计数：

```python
import threading
import time
from typing import Any
from plugin.sdk.plugin import (
    NekoPluginBase, neko_plugin, plugin_entry, lifecycle,
    timer_interval,
    Ok, Err, SdkError,
)

@neko_plugin
class MonitoredPlugin(NekoPluginBase):
    def __init__(self, ctx: Any):
        super().__init__(ctx)
        self.call_stats: dict[str, int] = {}
        self._stats_lock = threading.Lock()  # 定时器在另一个线程里运行

    @lifecycle(id="startup")
    async def on_startup(self, **_):
        return Ok({"status": "ready"})

    def _count(self, entry_id: str) -> None:
        with self._stats_lock:
            self.call_stats[entry_id] = self.call_stats.get(entry_id, 0) + 1

    # --- 入口点 ---

    @plugin_entry(id="process", description="Process some data")
    async def process(self, data: str, **_):
        self._count("process")
        return Ok({"processed": data.upper()})

    @plugin_entry(id="stats", description="Get call statistics")
    async def stats(self, **_):
        self._count("stats")
        with self._stats_lock:
            return Ok({"stats": dict(self.call_stats)})

    # --- 定时器 ---

    @timer_interval(id="health_check", seconds=300, auto_start=True)
    async def health_check(self, **_):
        with self._stats_lock:
            total_calls = sum(self.call_stats.values())
        self.report_status({
            "status": "healthy",
            "uptime": time.time(),
            "total_calls": total_calls,
        })
        return Ok({"healthy": True})
```

## 带持久化存储的插件

`self.store` 默认关闭，需要在 `plugin.toml` 中开启；关闭时 `set()` 不会保存任何内容，`get()` 返回默认值：

```toml
[plugin.store]
enabled = true
```

```python
from typing import Any
from pydantic import BaseModel
from plugin.sdk.plugin import (
    NekoPluginBase, neko_plugin, plugin_entry,
    Ok, Err, SdkError,
)

class NoteInput(BaseModel):
    title: str
    content: str

@neko_plugin
class NotesPlugin(NekoPluginBase):
    def __init__(self, ctx: Any):
        super().__init__(ctx)
        # self.store 已由 NekoPluginBase 构造好，直接使用即可。

    @plugin_entry(id="save_note", params=NoteInput)
    async def save_note(self, title: str, content: str, **_):
        saved = await self.store.set(f"note:{title}", {
            "title": title,
            "content": content,
        })
        if isinstance(saved, Err):
            return saved
        return Ok({"saved": title})

    @plugin_entry(id="get_note")
    async def get_note(self, title: str, **_):
        stored = await self.store.get(f"note:{title}")
        if isinstance(stored, Err):
            return stored
        note = stored.value
        if note is None:
            return Err(SdkError(f"Note not found: {title}"))
        return Ok(note)
```

## 带动态入口的插件

```python
from typing import Any
from plugin.sdk.plugin import (
    NekoPluginBase, neko_plugin, plugin_entry, lifecycle,
    Ok, Err, SdkError,
)

@neko_plugin
class DynamicPlugin(NekoPluginBase):
    def __init__(self, ctx: Any):
        super().__init__(ctx)

    @lifecycle(id="startup")
    async def on_startup(self, **_):
        # 根据配置在运行时注册入口
        cfg = await self.config.dump()
        commands = cfg.get("commands", {})
        for cmd_id, cmd_config in commands.items():
            self.register_dynamic_entry(
                entry_id=cmd_id,
                handler=self._make_handler(cmd_config),
                name=cmd_config.get("name", cmd_id),
                description=cmd_config.get("description", ""),
            )
        return Ok({"registered": list(commands.keys())})

    @plugin_entry(id="list_commands")
    async def list_commands(self, **_):
        entries = self.list_entries()
        return Ok({"commands": [e["id"] for e in entries]})

    def _make_handler(self, config):
        template = config.get("template", "Executed: {cmd}")
        async def handler(cmd: str = "", **_):
            return Ok({"output": template.format(cmd=cmd)})
        return handler
```
