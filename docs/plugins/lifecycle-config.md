# Lifecycle

Plugins go through different phases during their lifetime: startup, shutdown, config change, etc. You can hook into these moments to run your own logic.

All lifecycle hooks are **optional**. Don't need one? Don't write it.

Lifecycle hooks are host-dispatched events, not ordinary runtime entries. `@plugin_entry(auto_start=True)` is only metadata in the current host and does not run a normal entry at process startup; use `@lifecycle(id="startup")` for initialization. Likewise, `self.config.update_own_config(...)` refreshes the plugin's config through the supported host path but does not dispatch `config_change` back into the same plugin process—refresh any derived state after the call.

## Do setup on startup

After the plugin process starts, the `startup` hook is called. Good for initialization: loading config, opening connections, preparing resources.

```python
from plugin.sdk.plugin import NekoPluginBase, neko_plugin, lifecycle, Ok
import aiohttp

@neko_plugin
class MyPlugin(NekoPluginBase):

    @lifecycle(id="startup")
    async def on_startup(self):
        # Load config
        cfg = await self.config.dump()
        self.api_url = cfg.get("my_settings", {}).get("api_url", "https://default.com")

        # Open connection
        self.session = aiohttp.ClientSession()

        self.logger.info("Plugin started, API: {}", self.api_url)
        return Ok({"status": "ready"})
```

## Clean up on shutdown

Called when the plugin is stopped or N.E.K.O shuts down. Good for closing connections, saving state.

```python
    @lifecycle(id="shutdown")
    async def on_shutdown(self):
        if self.session:
            await self.session.close()
        self.logger.info("Plugin stopped")
        return Ok({"status": "stopped"})
```

## User clicks Reload

Plugin Manager reload currently restarts the plugin: it runs `shutdown`, then starts the plugin again and runs `startup`. Put cleanup in `shutdown` and initialization or config refresh in `startup`.

The `reload` lifecycle ID is accepted by the SDK decorator for compatibility, but the Plugin Manager reload button does not dispatch it.

## Config changed externally

Triggered when config is modified via UI or API. Good for applying new settings without restart.

```python
    @lifecycle(id="config_change")
    async def on_config_change(self, old_config, new_config, mode):
        self.timeout = new_config.get("my_settings", {}).get("timeout", 30)
        self.logger.info("Config update mode: {}", mode)
        self.logger.info("Config updated, new timeout: {}s", self.timeout)
        return Ok({"status": "config_updated"})
```

## Lifecycle events and reload behavior

| Event/action | When it happens | Typical use |
|----|---------------|-------------|
| `startup` | Plugin process starts | Initialize, load config, open connections |
| `shutdown` | Plugin process stops | Close connections, save state, release resources |
| Plugin Manager Reload | User clicks Reload | Runs `shutdown`, then `startup` |
| `config_change` | Config modified externally | Apply new settings |
| `freeze` | Plugin is suspended | Pause timers |
| `unfreeze` | Plugin is resumed | Resume timers |

## Full example

```python
from plugin.sdk.plugin import NekoPluginBase, neko_plugin, lifecycle, plugin_entry, Ok
import aiohttp

@neko_plugin
class WeatherPlugin(NekoPluginBase):

    def __init__(self, ctx):
        super().__init__(ctx)
        self.session = None
        self.api_url = ""

    @lifecycle(id="startup")
    async def on_startup(self):
        cfg = await self.config.dump()
        self.api_url = cfg.get("weather", {}).get("api_url", "https://wttr.in")
        self.session = aiohttp.ClientSession()
        self.logger.info("Weather plugin ready, api={}", self.api_url)
        return Ok({"status": "ready"})

    @lifecycle(id="shutdown")
    async def on_shutdown(self):
        if self.session:
            await self.session.close()
        return Ok({"status": "stopped"})

    @lifecycle(id="config_change")
    async def on_config_change(self, old_config, new_config, mode):
        self.api_url = new_config.get("weather", {}).get("api_url", "https://wttr.in")
        self.logger.info("Config updated with mode={}", mode)
        return Ok({"status": "config_updated"})

    @plugin_entry(id="get_weather", name="Get Weather", description="Look up city weather")
    async def get_weather(self, city: str = "Beijing"):
        async with self.session.get(f"{self.api_url}/{city}?format=j1") as resp:
            data = await resp.json()
            return Ok({"city": city, "temp": data["current_condition"][0]["temp_C"]})
```
