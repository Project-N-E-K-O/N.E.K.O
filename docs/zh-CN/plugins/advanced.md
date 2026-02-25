# 高级主题

## 异步编程

插件入口点可以是异步函数，适用于 I/O 密集型操作：

```python
@plugin_entry(id="async_task")
async def async_task(self, url: str, **_):
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            return {"data": await response.json()}
```

## 线程安全

如果你的插件在多个线程之间使用共享状态（例如定时任务访问共享数据），请使用锁：

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

## 带重试的错误处理

使用 `tenacity` 实现对瞬时故障的自动重试：

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

## 自定义配置

将插件特定的配置存储在 `plugin.toml` 旁边：

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

## 使用 SQLite 进行数据持久化

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
