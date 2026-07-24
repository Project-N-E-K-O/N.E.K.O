# 插件基类

当你写下这段代码：

```python
@neko_plugin
class MyPlugin(NekoPluginBase):
    ...
```

你的插件就自动获得了一系列能力。这些能力不需要你自己实现，`NekoPluginBase` 已经帮你准备好了。

本页按日常开发中的使用频率排序，介绍每个能力的用途和用法。

---

## self.logger — 打日志

**什么时候用**：随时。调试、记录运行状态、排查问题。

插件启动后 `self.logger` 就可以直接用，不需要任何配置。日志会自动出现在插件管理面板的日志查看器中，方便你实时观察插件在做什么。

```python
self.logger.info("开始处理用户请求")
self.logger.debug("收到参数: query={}", query)
self.logger.warning("API 响应慢，耗时 {}s", elapsed)
self.logger.error("请求失败: {}", error_msg)
```

日志格式支持 `{}` 占位符（类似 Python 的 `str.format`），不需要用 f-string。

**想要日志写入文件？** 默认日志只在面板中显示。如果你想重启后还能查看历史日志，在 `__init__` 中启用文件日志：

```python
def __init__(self, ctx):
    super().__init__(ctx)
    self.logger = self.enable_file_logging(log_level="INFO")
```

文件会写入 `plugin/log/` 目录，同时仍然在面板中可见。

---

## self.config — 读取和修改配置

**什么时候用**：你的插件有可调整的设置（API 地址、超时时间、开关等）。

你在 `plugin.toml` 中定义的自定义配置段，可以通过 `self.config` 读取。比如你的 toml 里有：

```toml
[my_settings]
api_url = "https://api.example.com"
timeout = 30
enabled = true
```

在代码中读取：

```python
# 方式一：获取整个配置，自己取值
cfg = await self.config.dump()
settings = cfg.get("my_settings", {})
api_url = settings.get("api_url", "https://default.com")

# 方式二：按路径直接取（更简洁）
api_url = await self.config.get("my_settings.api_url", default="https://default.com")
timeout = await self.config.get_int("my_settings.timeout", default=30)
enabled = await self.config.get_bool("my_settings.enabled", default=True)
```

**运行时请求更新配置**（比如某个入口保存用户选择）：

```python
await self.ctx.update_own_config({"my_settings": {"timeout": 60}})
```

这会走当前支持的 host 更新路径。它会持久化修改并刷新 `self.config`，但不会在插件进程内触发 `config_change` 生命周期钩子。如果新值会影响已缓存的派生状态，请在 `update_own_config()` 返回后主动重新加载这些状态。

---

## self.plugins — 调用其他插件

**什么时候用**：你的插件需要借助其他插件的能力。比如你做了一个"每日摘要"插件，想调用"网络搜索"插件获取新闻。

```python
# 调用 web_search 插件的 search 入口，传入参数
result = await self.plugins.call_entry("web_search:search", {"query": "今日新闻"})

# result 是 Ok 或 Err，需要检查
from plugin.sdk.plugin import Ok, Err
if isinstance(result, Ok):
    news = result.value  # 成功，拿到数据
else:
    self.logger.error("搜索失败: {}", result.error)
```

其他常用操作：

```python
from plugin.sdk.plugin import unwrap

# 检查某个插件是否可用
exists = unwrap(await self.plugins.exists("web_search"))

# 列出所有正在运行的插件
running = unwrap(await self.plugins.list(enabled=True))
```

---

## self.store — 键值存储

**什么时候用**：你需要保存一些数据，下次启动还能用。比如用户偏好、上次的查询记录、累计统计等。

需要先在 `plugin.toml` 中启用：

```toml
[plugin.store]
enabled = true
```

然后在代码中：

```python
from plugin.sdk.plugin import unwrap

# 保存（支持字符串、数字、字典、列表）
unwrap(await self.store.set("last_query", "今天天气怎么样"))
unwrap(await self.store.set("stats", {"total_calls": 42, "last_used": "2025-01-01"}))

# 读取（不存在时返回 None）
query = unwrap(await self.store.get("last_query"))
stats = unwrap(await self.store.get("stats"))

# 删除
deleted = unwrap(await self.store.delete("last_query"))
```

数据以文件形式保存在插件的 `data/` 目录中，重启不丢失。

---

## self.db — SQLite 数据库

**什么时候用**：你需要存储大量结构化数据，键值存储不够用了。比如笔记列表、聊天记录、任务队列等。

需要先在 `plugin.toml` 中启用：

```toml
[plugin.database]
enabled = true
```

然后在代码中：

```python
from plugin.sdk.plugin import unwrap

async with unwrap(await self.db.session()) as session:
    # 建表
    await session.execute("""
        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            content TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 插入
    await session.execute(
        "INSERT INTO notes (title, content) VALUES (?, ?)",
        ("买菜", "西红柿、鸡蛋、牛奶")
    )
    await session.commit()

    # 查询
    cursor = await session.execute("SELECT * FROM notes ORDER BY created_at DESC")
    for row in cursor.fetchall():
        self.logger.info("笔记: {} - {}", row["title"], row["content"])
```

数据库文件保存在插件的 `data/` 目录中。

---

## self.i18n — 国际化

**什么时候用**：你的插件需要支持多语言（比如同时支持中文和英文用户）。

需要先在 `plugin.toml` 中配置：

```toml
[plugin.i18n]
default_locale = "zh-CN"
locales_dir = "i18n"
```

然后创建语言文件，比如 `i18n/zh-CN.json`：

```json
{
  "greeting": "你好，{name}！",
  "error.not_found": "找不到 {item}"
}
```

在代码中使用：

```python
msg = self.i18n.t("greeting", name="小明")  # → "你好，小明！"
err = self.i18n.t("error.not_found", item="笔记")  # → "找不到 笔记"
```

系统会根据用户的语言设置自动选择对应的语言文件。

---

## self.data_path(...) — 文件存储

**什么时候用**：你需要存放任意文件（缓存、下载的资源、临时文件等）。

```python
# 获取路径（目录会自动创建）
cache_file = self.data_path("cache", "results.json")
# → <插件目录>/data/cache/results.json

# 写入
cache_file.parent.mkdir(parents=True, exist_ok=True)
cache_file.write_text('{"cached": true}')

# 读取
content = cache_file.read_text()
```

---

## self.bus — Bus 读取与监听

**什么时候用**：你想读取或监听按命名空间组织的宿主状态快照。该门面没有 publish/emit API。

```python
# 读取最近事件
recent_events = await self.bus.events.get(max_count=50)
recent_events = recent_events.filter(type="note_created").sort(
    by="timestamp", reverse=True,
).limit(20)

# 读取最近消息
recent_messages = await self.bus.messages.get(max_count=20)

# 从内存 bucket 读取近期用户话语事件
memory_records = await self.bus.memory.get(bucket_id="default", limit=20)
```

列表接口为 `filter` / `where`、`sort`、`limit`、`watch`。可调用形式 `filter(predicate)`、`where(predicate)` 与 `sort(key=...)` 仅处理本地快照，因此 watcher 链必须使用结构化 `filter(field=value, ...)` 与 `sort(by=...)`。只有 `messages`、`events`、`lifecycle` 支持 `watch()`；`conversations` 与 `memory` 是只读快照。watcher 只接受 `add`、`del`、`change`。

`memory` bucket 有容量上限，在进程内保留一小时。它是插件上下文，不是角色持久化的事实、反思或人格。`ctx.query_memory(...)` 只为兼容而保留，它调用已弃用的占位端点，不执行语义召回。

---

## report_status(...) — 状态推送

**什么时候用**：你的插件在执行耗时操作，想让用户在面板中看到进度。

```python
self.report_status({
    "status": "processing",
    "progress": 50,
    "message": "正在处理第 5/10 条..."
})
```

状态会实时显示在插件管理面板中。

---

## push_message(...) — 向聊天推送消息

**什么时候用**：你的插件想主动告诉用户一些事情（提醒、通知、结果等），消息会出现在 N.E.K.O 的聊天界面中。

```python
self.push_message(
    source="smart_notes",
    visibility=["chat"],
    ai_behavior="blind",
    parts=[{"type": "text", "text": "提醒：你有一条待办事项要处理"}],
    priority=5,
)
```

---


## self.system_info — 系统信息

**什么时候用**：你需要知道当前运行环境的信息。

```python
from plugin.sdk.plugin import unwrap_or

config = unwrap_or(await self.system_info.get_system_config(), {})
settings = unwrap_or(await self.system_info.get_server_settings(), {})
python_env = unwrap_or(await self.system_info.get_python_env(), {})
```

---

## 汇总

| 能力 | 用途 | 需要额外配置？ |
|------|------|---------------|
| `self.logger` | 打日志、排查问题 | 不需要 |
| `self.config` | 读写 toml 中的配置 | 不需要 |
| `self.plugins` | 调用其他插件 | 不需要 |
| `self.store` | 键值持久化 | `[plugin.store] enabled = true` |
| `self.db` | SQLite 数据库 | `[plugin.database] enabled = true` |
| `self.i18n` | 多语言 | `[plugin.i18n]` |
| `self.data_path()` | 存放文件 | 不需要 |
| `self.bus` | 读取/监听总线快照 | 不需要 |
| `report_status()` | 面板中显示进度 | 不需要 |
| `push_message()` | 向聊天推送消息 | 不需要 |
| `self.system_info` | 查询系统信息 | 不需要 |
