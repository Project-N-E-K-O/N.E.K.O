# Plugin Base Class

When you write this:

```python
@neko_plugin
class MyPlugin(NekoPluginBase):
    ...
```

Your plugin automatically gains a set of capabilities. You don't need to implement them yourself — `NekoPluginBase` has them ready for you.

This page introduces each capability in order of how often you'll use them in practice.

---

## self.logger — Logging

**When you need it**: Always. Debugging, tracking state, diagnosing issues.

`self.logger` is ready to use immediately after `super().__init__(ctx)`. No config needed. Logs automatically appear in the Plugin Manager's log viewer, so you can watch what your plugin is doing in real time.

```python
self.logger.info("Starting to process user request")
self.logger.debug("Received params: query={}", query)
self.logger.warning("API response slow, took {}s", elapsed)
self.logger.error("Request failed: {}", error_msg)
```

The format supports `{}` placeholders (like Python's `str.format`) — no need for f-strings.

**Want logs written to a file?** By default, logs only show in the panel. If you want to review history after a restart, enable file logging in `__init__`:

```python
def __init__(self, ctx):
    super().__init__(ctx)
    self.logger = self.enable_file_logging(log_level="INFO")
```

Files are written to `plugin/log/` and still appear in the panel.

---

## self.config — Read and modify config

**When you need it**: Your plugin has adjustable settings (API URLs, timeouts, feature flags, etc.).

Custom config sections you define in `plugin.toml` are accessible via `self.config`. For example, if your toml has:

```toml
[my_settings]
api_url = "https://api.example.com"
timeout = 30
enabled = true
```

Read it in code:

```python
# Option 1: get the full config, pick what you need
cfg = await self.config.dump()
settings = cfg.get("my_settings", {})
api_url = settings.get("api_url", "https://default.com")

# Option 2: get by path directly (more concise)
api_url = await self.config.get("my_settings.api_url", default="https://default.com")
timeout = await self.config.get_int("my_settings.timeout", default=30)
enabled = await self.config.get_bool("my_settings.enabled", default=True)
```

**Request a config update at runtime** (e.g. an entry saves a user choice):

```python
await self.ctx.update_own_config({"my_settings": {"timeout": 60}})
```

This uses the supported host update path. It persists the change and refreshes `self.config`, but it does **not** dispatch the `config_change` lifecycle hook inside the plugin process. If the new value affects cached state, reload that state after `update_own_config()` returns.

---

## self.plugins — Call other plugins

**When you need it**: Your plugin needs another plugin's capabilities. For example, a "daily summary" plugin that calls "web search" to get news.

```python
# Call the web_search plugin's search entry with parameters
result = await self.plugins.call_entry("web_search:search", {"query": "today's news"})

# result is Ok or Err — check it
from plugin.sdk.plugin import Ok, Err
if isinstance(result, Ok):
    news = result.value  # success, got data
else:
    self.logger.error("Search failed: {}", result.error)
```

Other common operations:

```python
from plugin.sdk.plugin import unwrap

# Check if a plugin is available
exists = unwrap(await self.plugins.exists("web_search"))

# List all running plugins
running = unwrap(await self.plugins.list(enabled=True))
```

---

## self.store — Key-value storage

**When you need it**: You need to save data that survives restarts. User preferences, last query, cumulative stats, etc.

Requires enabling in `plugin.toml`:

```toml
[plugin.store]
enabled = true
```

Then in code:

```python
from plugin.sdk.plugin import unwrap

# Save (supports strings, numbers, dicts, lists)
unwrap(await self.store.set("last_query", "what's the weather today"))
unwrap(await self.store.set("stats", {"total_calls": 42, "last_used": "2025-01-01"}))

# Load (returns None if not found)
query = unwrap(await self.store.get("last_query"))
stats = unwrap(await self.store.get("stats"))

# Delete
deleted = unwrap(await self.store.delete("last_query"))
```

Data is saved as files in the plugin's `data/` directory and persists across restarts.

---

## self.db — SQLite database

**When you need it**: You need to store large amounts of structured data and key-value isn't enough. Notes, chat logs, task queues, etc.

Requires enabling in `plugin.toml`:

```toml
[plugin.database]
enabled = true
```

Then in code:

```python
from plugin.sdk.plugin import unwrap

async with unwrap(await self.db.session()) as session:
    # Create table
    await session.execute("""
        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            content TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Insert
    await session.execute(
        "INSERT INTO notes (title, content) VALUES (?, ?)",
        ("Groceries", "Tomatoes, eggs, milk")
    )
    await session.commit()

    # Query
    cursor = await session.execute("SELECT * FROM notes ORDER BY created_at DESC")
    for row in cursor.fetchall():
        self.logger.info("Note: {} - {}", row["title"], row["content"])
```

The database file is stored in the plugin's `data/` directory.

---

## self.i18n — Internationalization

**When you need it**: Your plugin needs to support multiple languages (e.g. both Chinese and English users).

Requires config in `plugin.toml`:

```toml
[plugin.i18n]
default_locale = "zh-CN"
locales_dir = "i18n"
```

Then create locale files, e.g. `i18n/en.json`:

```json
{
  "greeting": "Hello, {name}!",
  "error.not_found": "Cannot find {item}"
}
```

Use in code:

```python
msg = self.i18n.t("greeting", name="Alice")  # → "Hello, Alice!"
err = self.i18n.t("error.not_found", item="note")  # → "Cannot find note"
```

The system automatically picks the right locale file based on the user's language setting.

---

## self.data_path(...) — File storage

**When you need it**: You need to store arbitrary files (caches, downloaded resources, temp files, etc.).

```python
# Get a path (directory is created automatically)
cache_file = self.data_path("cache", "results.json")
# → <plugin_dir>/data/cache/results.json

# Write
cache_file.parent.mkdir(parents=True, exist_ok=True)
cache_file.write_text('{"cached": true}')

# Read
content = cache_file.read_text()
```

---

## self.bus — Bus read/watch

**When you need it**: You want to read or watch namespaced host-state snapshots. This facade has no publish/emit API.

```python
# Read recent events
recent_events = await self.bus.events.get(max_count=50)
recent_events = recent_events.filter(type="note_created").sort(
    by="timestamp", reverse=True,
).limit(20)

# Read recent messages
recent_messages = await self.bus.messages.get(max_count=20)

# Read memory records from a bucket
memory_records = await self.bus.memory.get(bucket_id="default", limit=20)

# Semantic lookup is a separate context operation
matches = await self.ctx.query_memory("default", "user preferences")
```

The list surface is `filter` / `where`, `sort`, `limit`, and `watch`. Callable `filter(predicate)`, `where(predicate)`, and `sort(key=...)` are local-only, so watcher chains must use structured `filter(field=value, ...)` and `sort(by=...)`. Only `messages`, `events`, and `lifecycle` support `watch()`; `conversations` and `memory` are read-only snapshots. Watcher subscriptions accept only `add`, `del`, or `change`.

---

## report_status(...) — Status push

**When you need it**: Your plugin is doing a long-running operation and you want users to see progress in the panel.

```python
self.report_status({
    "status": "processing",
    "progress": 50,
    "message": "Processing item 5/10..."
})
```

Status appears in real time in the Plugin Manager panel.

---

## push_message(...) — Push to chat

**When you need it**: Your plugin wants to proactively tell the user something (reminders, notifications, results). The message appears in N.E.K.O's chat interface.

```python
self.push_message(
    source="smart_notes",
    visibility=["chat"],
    ai_behavior="blind",
    parts=[{"type": "text", "text": "Reminder: you have a pending task"}],
    priority=5,
)
```

---


## self.system_info — System info

**When you need it**: You need to know about the current runtime environment.

```python
from plugin.sdk.plugin import unwrap_or

config = unwrap_or(await self.system_info.get_system_config(), {})
settings = unwrap_or(await self.system_info.get_server_settings(), {})
python_env = unwrap_or(await self.system_info.get_python_env(), {})
```

---

## Summary

| Capability | Purpose | Needs extra config? |
|------------|---------|---------------------|
| `self.logger` | Logging and debugging | No |
| `self.config` | Read/write toml config | No |
| `self.plugins` | Call other plugins | No |
| `self.store` | Key-value persistence | `[plugin.store] enabled = true` |
| `self.db` | SQLite database | `[plugin.database] enabled = true` |
| `self.i18n` | Multi-language | `[plugin.i18n]` |
| `self.data_path()` | Store files | No |
| `self.bus` | Read/watch bus snapshots | No |
| `report_status()` | Show progress in panel | No |
| `push_message()` | Push to chat | No |
| `self.system_info` | Query system info | No |
