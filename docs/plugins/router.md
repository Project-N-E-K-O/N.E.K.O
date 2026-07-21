# PluginRouter — Splitting Large Plugins

As your plugin grows, putting all entry points in one `__init__.py` becomes hard to maintain. `PluginRouter` lets you group entry points into separate files by feature, while they still belong to the same plugin.

---

## When you need Router

- Your plugin has 5+ entry points
- Different entries belong to different feature areas (e.g. "weather", "routes", "food")
- Multiple people are working on the same plugin, each owning a module
- Code exceeds 300 lines and you want to split files

If your plugin only has 1-3 entry points, just write them in the main file. No Router needed.

---

## What it looks like in practice

The "Life Kit" plugin has 12 feature modules:

```text
plugin/plugins/lifekit/
├── __init__.py              ← main plugin: registers all routers
├── routers/
│   ├── __init__.py          ← exports all routers
│   ├── current.py           ← current weather
│   ├── hourly.py            ← hourly forecast
│   ├── travel.py            ← travel advice
│   ├── locations.py         ← location management
│   ├── trip.py              ← route planning
│   ├── nearby.py            ← nearby search
│   ├── food.py              ← food recommendations
│   ├── recipe.py            ← recipes
│   ├── air_quality.py       ← air quality
│   ├── currency.py          ← currency conversion
│   ├── countdown.py         ← countdown
│   └── unit_convert.py      ← unit conversion
└── plugin.toml
```

In Plugin Manager, users see one "Life Kit" plugin with 12+ entry points. They don't need to know how the code is organized.

---

## How to write a Router

### Step 1: Create the Router file

```python
# routers/countdown.py

from plugin.sdk.plugin import PluginRouter, plugin_entry, Ok, Err, SdkError


class CountdownRouter(PluginRouter):
    """Countdown feature."""

    def __init__(self):
        super().__init__(name="countdown")

    @plugin_entry(
        id="countdown",
        name="Countdown",
        description="Calculate days until a target date",
    )
    async def countdown(self, target_date: str, label: str = ""):
        # your business logic
        ...
        return Ok({"summary": f"{label} is in 30 days"})

    @plugin_entry(
        id="days_between",
        name="Days Between",
        description="Calculate days between two dates",
    )
    async def days_between(self, start_date: str = "", end_date: str = ""):
        ...
        return Ok({"summary": "100 days"})
```

Key points:
- Inherit from `PluginRouter`
- `super().__init__(name="countdown")` gives the router a name (used in debug logs)
- Use `@plugin_entry` to define entries — same syntax as in the main plugin

### Step 2: Register in the main plugin

```python
# __init__.py

from plugin.sdk.plugin import NekoPluginBase, neko_plugin, lifecycle, Ok
from .routers import CountdownRouter, WeatherRouter

@neko_plugin
class LifeKitPlugin(NekoPluginBase):

    def __init__(self, ctx):
        super().__init__(ctx)

        # Register routers — must be in __init__
        self.include_router(CountdownRouter())
        self.include_router(WeatherRouter())

    @lifecycle(id="startup")
    async def on_startup(self):
        self.logger.info("Life Kit started")
        return Ok({"status": "ready"})
```

`self.include_router()` registers all entry points from the router under the current plugin.

---

## What Routers can access

Once registered, a router is bound to the main plugin. You can access the main plugin's capabilities through these properties:

```python
from plugin.sdk.plugin import unwrap

class MyRouter(PluginRouter):

    @plugin_entry(id="example", name="Example", description="Demo router capabilities")
    async def example(self):
        # Logging
        self.logger.info("Logging from a router")

        # Read config
        cfg = await self.config.dump()

        # Use storage
        unwrap(await self.store.set("key", "value"))

        # Call other plugins
        result = await self.plugins.call_entry("other:entry")

        # Access database
        async with unwrap(await self.db.session()) as session:
            cursor = await session.execute("SELECT * FROM notes")
            rows = cursor.fetchall()

        # Access main plugin's custom attributes/methods
        plugin = self.main_plugin
        data = await plugin.some_shared_method()

        return Ok({"done": True})
```

| Property | Source |
|----------|--------|
| `self.logger` | Main plugin's logger |
| `self.config` | Main plugin's config |
| `self.store` | Main plugin's store |
| `self.db` | Main plugin's db |
| `self.plugins` | Main plugin's plugins |
| `self.plugin_id` | Main plugin's ID |
| `self.main_plugin` | The main plugin instance itself |

A router is not a separate process. It runs in the same process as the main plugin and shares all resources.

It is also not a manifest entry point: `[plugin].entry` must resolve to the owning `NekoPluginBase` class. Pointing it at a `PluginRouter` subclass is rejected by the host. `include_router()` performs binding and entry collection; the `on_mount` / `on_unmount` methods present on the router class are not automatically called by this base-class path.

---

## Sharing logic

When multiple routers need the same utility functions, put them in the main plugin or a shared module:

```text
plugin/plugins/lifekit/
├── __init__.py          ← main plugin, defines shared methods
├── _geo.py              ← shared: geolocation
├── _api.py              ← shared: API call utilities
├── _chat.py             ← shared: push messages to chat
└── routers/
    ├── current.py       ← uses self.main_plugin._resolve_location()
    └── travel.py        ← uses self.main_plugin._resolve_location()
```

Routers access main plugin methods via `self.main_plugin`:

```python
class WeatherRouter(PluginRouter):

    @plugin_entry(id="get_weather", name="Get Weather", description="Look up weather")
    async def get_weather(self, city: str = ""):
        plugin = self.main_plugin
        # Call shared method on main plugin
        location, error = await plugin._resolve_location(city)
        if not location:
            return Err(SdkError(error))
        ...
```

---

## Prefixed Routers

If you want to prefix all entry IDs in a router (to avoid ID conflicts):

```python
self.include_router(CountdownRouter(), prefix="time_")
```

Now the `countdown` entry's actual ID becomes `time_countdown`.

Most of the time you don't need prefixes — just make sure entry IDs don't collide across routers.

---

## Runtime removal

`exclude_router()` removes a router from the plugin's router list, but normal plugin code should not use it as a live feature toggle. Entries are collected when the host builds its dispatch table, so removing a router later does not automatically make its already-collected entries uncallable.

If you need runtime enable/disable behavior, gate the entry logic with explicit plugin configuration and rebuild/restart the plugin when its dispatch structure changes. Do not depend on the removed Extension control path.

```python
# Removes from the router list only
self.exclude_router(my_router_instance)

# Same, by name
self.exclude_router("countdown")
```

---

## Complete minimal example

A plugin with two routers:

```python
# routers/greet.py
from plugin.sdk.plugin import PluginRouter, plugin_entry, Ok

class GreetRouter(PluginRouter):
    def __init__(self):
        super().__init__(name="greet")

    @plugin_entry(id="hello", name="Hello", description="Say hello")
    async def hello(self, name: str = "World"):
        return Ok({"message": f"Hello, {name}!"})


# routers/math.py
from plugin.sdk.plugin import PluginRouter, plugin_entry, Ok, Err, SdkError

class MathRouter(PluginRouter):
    def __init__(self):
        super().__init__(name="math")

    @plugin_entry(id="add", name="Add", description="Add two numbers")
    async def add(self, a: float, b: float):
        return Ok({"result": a + b})

    @plugin_entry(id="divide", name="Divide", description="Divide two numbers")
    async def divide(self, a: float, b: float):
        if b == 0:
            return Err(SdkError("Cannot divide by zero"))
        return Ok({"result": a / b})


# __init__.py
from plugin.sdk.plugin import NekoPluginBase, neko_plugin
from .routers.greet import GreetRouter
from .routers.math import MathRouter

@neko_plugin
class MyPlugin(NekoPluginBase):
    def __init__(self, ctx):
        super().__init__(ctx)
        self.include_router(GreetRouter())
        self.include_router(MathRouter())
```

This plugin shows three entry points in the panel: `hello`, `add`, `divide`.
