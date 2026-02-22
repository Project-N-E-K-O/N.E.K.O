# Advanced Topics

## Async programming

Plugin entry points can be async functions for I/O-intensive operations:

```python
@plugin_entry(id="async_task")
async def async_task(self, url: str, **_):
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            return {"data": await response.json()}
```

## Thread safety

If your plugin uses shared state across threads (e.g., timer tasks accessing shared data), use locks:

```python
import threading

@neko_plugin
class ThreadSafePlugin(NekoPluginBase):
    def __init__(self, ctx):
        super().__init__(ctx)
        self._lock = threading.Lock()
        self._shared_data = {}

    @plugin_entry(id="update")
    def update(self, key: str, value: str, **_):
        with self._lock:
            self._shared_data[key] = value
            return {"updated": True}
```

## Error handling with retry

Use `tenacity` for automatic retries on transient failures:

```python
from tenacity import retry, stop_after_attempt, wait_exponential

@plugin_entry(id="reliable_fetch")
@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=4, max=10))
def reliable_fetch(self, url: str, **_):
    import requests
    response = requests.get(url)
    response.raise_for_status()
    return {"data": response.json()}
```

## Custom configuration

Store plugin-specific configuration alongside `plugin.toml`:

```python
import json

class ConfigurablePlugin(NekoPluginBase):
    def __init__(self, ctx):
        super().__init__(ctx)
        self.config_file = ctx.config_path.parent / "config.json"
        self._load_config()

    def _load_config(self):
        if self.config_file.exists():
            self.config = json.loads(self.config_file.read_text())
        else:
            self.config = {"timeout": 30}

    def _save_config(self):
        self.config_file.write_text(json.dumps(self.config, indent=2))
```

## Data persistence with SQLite

```python
import sqlite3

class PersistentPlugin(NekoPluginBase):
    def __init__(self, ctx):
        super().__init__(ctx)
        self.db_path = ctx.config_path.parent / "data.db"
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

    @plugin_entry(id="save")
    def save(self, key: str, value: str, **_):
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "INSERT OR REPLACE INTO records (key, value) VALUES (?, ?)",
            (key, value)
        )
        conn.commit()
        conn.close()
        return {"saved": True}
```
