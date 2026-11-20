# Plugin Examples

## Basic plugin with Result types

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
        # Published only (shown to callers); not validated at runtime.
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

## Async API client with cross-plugin calls

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
            # One session per call: startup, entry calls, and shutdown run on
            # different event loops, so a session created in startup cannot be
            # used here or closed cleanly in shutdown.
            async with aiohttp.ClientSession() as session:
                async with session.request(method, url) as response:
                    data = await response.json()
                    return Ok({"status": response.status, "data": data})
        except Exception as e:
            return Err(SdkError(f"Request failed: {e}"))

    @plugin_entry(id="fetch_with_cache")
    async def fetch_with_cache(self, endpoint: str, **_):
        # Cross-plugin call: check cache plugin first
        cached = await self.plugins.call_entry(
            "cache_plugin:get", {"key": endpoint}
        )
        cached_value = unwrap_or(cached, None)
        if cached_value and cached_value.get("hit"):
            return Ok(cached_value["data"])

        # Fetch fresh data
        result = await self.fetch(endpoint=endpoint)
        if isinstance(result, Ok):
            # Store in cache plugin
            await self.plugins.call_entry(
                "cache_plugin:set",
                {"key": endpoint, "value": result.value}
            )
        return result
```

`aiohttp` is a third-party package: declare it in your plugin's `pyproject.toml` `[project].dependencies` and run `neko-plugin sync <plugin>`, rather than relying on what the host environment happens to have installed.

## Plugin with call counting and timer

The hook decorators (`@before_entry`, `@after_entry`, ...) only record metadata and are not executed today (see [Hook Decorators](./decorators#hook-decorators-aop)), so count calls inside the entries themselves:

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
        self._stats_lock = threading.Lock()  # the timer runs in another thread

    @lifecycle(id="startup")
    async def on_startup(self, **_):
        return Ok({"status": "ready"})

    def _count(self, entry_id: str) -> None:
        with self._stats_lock:
            self.call_stats[entry_id] = self.call_stats.get(entry_id, 0) + 1

    # --- Entry points ---

    @plugin_entry(id="process", description="Process some data")
    async def process(self, data: str, **_):
        self._count("process")
        return Ok({"processed": data.upper()})

    @plugin_entry(id="stats", description="Get call statistics")
    async def stats(self, **_):
        self._count("stats")
        with self._stats_lock:
            return Ok({"stats": dict(self.call_stats)})

    # --- Timer ---

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

## Plugin with persistent storage

`self.store` is disabled unless `plugin.toml` enables it; while disabled, `set()` stores nothing and `get()` returns the default:

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
        # self.store is already constructed by NekoPluginBase; use it directly.

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

## Plugin with dynamic entries

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
        # Register entries at runtime based on config
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
