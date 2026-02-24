# 応用トピック

## 非同期プログラミング

プラグインのエントリーポイントは I/O 集約型の操作に非同期関数を使用できます：

```python
@plugin_entry(id="async_task")
async def async_task(self, url: str, **_):
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            return {"data": await response.json()}
```

## スレッドセーフティ

プラグインがスレッド間で共有状態を使用する場合（例：タイマータスクが共有データにアクセスする場合）、ロックを使用してください：

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

## リトライ付きエラーハンドリング

一時的な障害に対する自動リトライには `tenacity` を使用します：

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

## カスタム設定

プラグイン固有の設定を `plugin.toml` の横に保存します：

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

## SQLite によるデータ永続化

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
