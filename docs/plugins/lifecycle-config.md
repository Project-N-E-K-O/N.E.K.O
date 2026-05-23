# Lifecycle & Config

## Lifecycle hooks

Lifecycle hooks run at specific moments in your plugin's life. They are all optional.

```python
from plugin.sdk.plugin import NekoPluginBase, neko_plugin, lifecycle, Ok

@neko_plugin
class MyPlugin(NekoPluginBase):

    @lifecycle(id="startup")
    async def on_startup(self):
        # Runs when the plugin starts
        # Good place to: load config, open connections, initialize state
        self.logger.info("Plugin started!")
        return Ok({"status": "ready"})

    @lifecycle(id="shutdown")
    async def on_shutdown(self):
        # Runs when the plugin stops
        # Good place to: close connections, flush data, cleanup
        return Ok({"status": "stopped"})

    @lifecycle(id="config_change")
    async def on_config_change(self):
        # Runs when config is updated (e.g. from the UI)
        # Good place to: reload settings without restart
        await self._reload_config()
        return Ok({"status": "reloaded"})

    @lifecycle(id="reload")
    async def on_reload(self):
        # Runs when user clicks "Reload" in Plugin Manager
        return Ok({"status": "reloaded"})
```

### All lifecycle IDs

| ID | When it runs |
|----|------|
| `startup` | Plugin process starts |
| `shutdown` | Plugin process stops |
| `reload` | User clicks Reload |
| `config_change` | Config is updated externally |
| `freeze` | Plugin is being suspended |
| `unfreeze` | Plugin is being resumed |

## Reading config

Your `plugin.toml` can contain custom sections. Read them with `self.config`:

```toml
# plugin.toml
[my_settings]
api_url = "https://api.example.com"
timeout = 30
max_retries = 3
```

```python
@lifecycle(id="startup")
async def on_startup(self):
    # Get the entire config as a dict
    cfg = await self.config.dump()
    settings = cfg.get("my_settings", {})
    self.api_url = settings.get("api_url", "https://default.com")
    self.timeout = settings.get("timeout", 30)
    return Ok({"status": "ready"})
```

### Path-based access

```python
# Get a single value by dot-path
url = await self.config.get("my_settings.api_url", default="https://default.com")

# Typed getters
timeout = await self.config.get_int("my_settings.timeout", default=30)
enabled = await self.config.get_bool("my_settings.enabled", default=True)
name = await self.config.get_str("my_settings.name", default="default")
```

### Updating config at runtime

```python
# Set a single value
await self.config.set("my_settings.timeout", 60)

# Update multiple values
await self.config.update({"my_settings": {"timeout": 60, "max_retries": 5}})
```

## Typed settings with PluginSettings

For type-safe config with validation, use `PluginSettings`:

```python
from plugin.sdk.plugin import NekoPluginBase, neko_plugin, PluginSettings, SettingsField

class MySettings(PluginSettings):
    model_config = {"toml_section": "my_settings"}

    api_url: str = SettingsField("https://default.com", description="API endpoint")
    timeout: int = SettingsField(30, hot=True, description="Request timeout (seconds)")
    max_retries: int = SettingsField(3, ge=1, le=10, description="Max retry attempts")

@neko_plugin
class MyPlugin(NekoPluginBase):

    class Settings(PluginSettings):
        model_config = {"toml_section": "my_settings"}

        api_url: str = SettingsField("https://default.com", description="API endpoint")
        timeout: int = SettingsField(30, hot=True, description="Timeout (seconds)")
```

- `hot=True` — this field can be changed at runtime without restart
- Validation constraints (`ge`, `le`, etc.) are enforced automatically
- The schema is exposed to the Plugin Manager UI

## Logging

The base class gives you `self.logger` automatically:

```python
self.logger.debug("Detailed info for debugging")
self.logger.info("Normal operation: started processing")
self.logger.warning("Something unexpected but handled")
self.logger.error("Something went wrong")
```

### File logging

If you want logs written to a file (persisted across restarts):

```python
def __init__(self, ctx):
    super().__init__(ctx)
    self.logger = self.enable_file_logging(log_level="INFO")
```

Logs go to `plugin/log/` and are visible in the Plugin Manager's log viewer.

## Persistent storage

### Key-value store

Enable in `plugin.toml`:

```toml
[plugin.store]
enabled = true
```

Use in code:

```python
# Save
await self.store.set("user_prefs", {"theme": "dark", "lang": "zh-CN"})

# Load
prefs = await self.store.get("user_prefs")
# → {"theme": "dark", "lang": "zh-CN"}

# Delete
await self.store.delete("user_prefs")
```

### SQLite database

Enable in `plugin.toml`:

```toml
[plugin.database]
enabled = true
```

Use in code:

```python
# self.db gives you a managed SQLite connection
await self.db.execute("CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY, text TEXT)")
await self.db.execute("INSERT INTO notes (text) VALUES (?)", ("hello",))
rows = await self.db.fetch_all("SELECT * FROM notes")
```

### Data directory

For arbitrary files:

```python
# Get a path under your plugin's data/ directory
cache_file = self.data_path("cache.json")
cache_file.parent.mkdir(parents=True, exist_ok=True)
cache_file.write_text('{"key": "value"}')
```
