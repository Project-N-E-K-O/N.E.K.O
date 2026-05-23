# 生命周期与配置

## 生命周期钩子

生命周期钩子在插件生命中的特定时刻运行。它们都是可选的。

```python
from plugin.sdk.plugin import NekoPluginBase, neko_plugin, lifecycle, Ok

@neko_plugin
class MyPlugin(NekoPluginBase):

    @lifecycle(id="startup")
    async def on_startup(self):
        # 插件启动时运行
        # 适合：加载配置、打开连接、初始化状态
        self.logger.info("Plugin started!")
        return Ok({"status": "ready"})

    @lifecycle(id="shutdown")
    async def on_shutdown(self):
        # 插件停止时运行
        # 适合：关闭连接、刷新数据、清理资源
        return Ok({"status": "stopped"})

    @lifecycle(id="config_change")
    async def on_config_change(self):
        # 配置被更新时运行（比如从 UI 修改）
        # 适合：不重启就重新加载设置
        await self._reload_config()
        return Ok({"status": "reloaded"})

    @lifecycle(id="reload")
    async def on_reload(self):
        # 用户在插件管理面板点击"重载"时运行
        return Ok({"status": "reloaded"})
```

### 所有生命周期 ID

| ID | 何时运行 |
|----|----------|
| `startup` | 插件进程启动 |
| `shutdown` | 插件进程停止 |
| `reload` | 用户点击重载 |
| `config_change` | 配置被外部更新 |
| `freeze` | 插件被挂起 |
| `unfreeze` | 插件被恢复 |

## 读取配置

`plugin.toml` 可以包含自定义段。用 `self.config` 读取：

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
    # 获取整个配置为 dict
    cfg = await self.config.dump()
    settings = cfg.get("my_settings", {})
    self.api_url = settings.get("api_url", "https://default.com")
    self.timeout = settings.get("timeout", 30)
    return Ok({"status": "ready"})
```

### 路径式访问

```python
# 用点路径获取单个值
url = await self.config.get("my_settings.api_url", default="https://default.com")

# 带类型的 getter
timeout = await self.config.get_int("my_settings.timeout", default=30)
enabled = await self.config.get_bool("my_settings.enabled", default=True)
name = await self.config.get_str("my_settings.name", default="default")
```

### 运行时更新配置

```python
# 设置单个值
await self.config.set("my_settings.timeout", 60)

# 更新多个值
await self.config.update({"my_settings": {"timeout": 60, "max_retries": 5}})
```

## 类型安全配置：PluginSettings

需要带验证的类型安全配置时，用 `PluginSettings`：

```python
from plugin.sdk.plugin import NekoPluginBase, neko_plugin, PluginSettings, SettingsField

@neko_plugin
class MyPlugin(NekoPluginBase):

    class Settings(PluginSettings):
        model_config = {"toml_section": "my_settings"}

        api_url: str = SettingsField("https://default.com", description="API 端点")
        timeout: int = SettingsField(30, hot=True, description="请求超时（秒）")
        max_retries: int = SettingsField(3, ge=1, le=10, description="最大重试次数")
```

- `hot=True` — 这个字段可以在运行时修改，不需要重启
- 验证约束（`ge`、`le` 等）自动生效
- Schema 会暴露给插件管理面板 UI

## 日志

基类自动提供 `self.logger`：

```python
self.logger.debug("调试用的详细信息")
self.logger.info("正常操作：开始处理")
self.logger.warning("意外但已处理的情况")
self.logger.error("出错了")
```

### 文件日志

如果想把日志写入文件（重启后保留）：

```python
def __init__(self, ctx):
    super().__init__(ctx)
    self.logger = self.enable_file_logging(log_level="INFO")
```

日志写入 `plugin/log/`，在插件管理面板的日志查看器中可见。

## 持久化存储

### 键值存储

在 `plugin.toml` 中启用：

```toml
[plugin.store]
enabled = true
```

代码中使用：

```python
# 保存
await self.store.set("user_prefs", {"theme": "dark", "lang": "zh-CN"})

# 读取
prefs = await self.store.get("user_prefs")
# → {"theme": "dark", "lang": "zh-CN"}

# 删除
await self.store.delete("user_prefs")
```

### SQLite 数据库

在 `plugin.toml` 中启用：

```toml
[plugin.database]
enabled = true
```

代码中使用：

```python
# self.db 提供托管的 SQLite 连接
await self.db.execute("CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY, text TEXT)")
await self.db.execute("INSERT INTO notes (text) VALUES (?)", ("hello",))
rows = await self.db.fetch_all("SELECT * FROM notes")
```

### 数据目录

存放任意文件：

```python
# 获取插件 data/ 目录下的路径
cache_file = self.data_path("cache.json")
cache_file.parent.mkdir(parents=True, exist_ok=True)
cache_file.write_text('{"key": "value"}')
```
