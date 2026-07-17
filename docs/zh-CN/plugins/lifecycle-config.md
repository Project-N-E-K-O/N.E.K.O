# 生命周期

插件在运行过程中会经历不同的阶段：启动、停止、配置变更等。你可以在这些时刻执行自己的逻辑。

所有生命周期钩子都是**可选的**。不需要就不写。

生命周期钩子是宿主分派的事件，不是普通运行时入口。当前宿主不会因为 `@plugin_entry(auto_start=True)` 就在进程启动时执行普通入口；初始化应使用 `@lifecycle(id="startup")`。同样，`self.config.update_own_config(...)` 会通过受支持的宿主路径更新并刷新配置，但不会在同一个插件进程内回派 `config_change`；调用后应主动刷新依赖配置的派生状态。

## 启动时做准备

插件进程启动后，`startup` 钩子会被调用。适合做初始化工作：加载配置、打开连接、准备资源。

```python
from plugin.sdk.plugin import NekoPluginBase, neko_plugin, lifecycle, Ok
import aiohttp

@neko_plugin
class MyPlugin(NekoPluginBase):

    @lifecycle(id="startup")
    async def on_startup(self):
        # 加载配置
        cfg = await self.config.dump()
        self.api_url = cfg.get("my_settings", {}).get("api_url", "https://default.com")

        # 打开连接
        self.session = aiohttp.ClientSession()

        self.logger.info("插件已启动，API: {}", self.api_url)
        return Ok({"status": "ready"})
```

## 停止时清理

插件被停止或 N.E.K.O 关闭时，`shutdown` 钩子会被调用。适合关闭连接、保存状态。

```python
    @lifecycle(id="shutdown")
    async def on_shutdown(self):
        if self.session:
            await self.session.close()
        self.logger.info("插件已停止")
        return Ok({"status": "stopped"})
```

## 用户点击重载

插件管理面板中的重载目前会重启插件：先执行 `shutdown`，再重新启动插件并执行 `startup`。清理逻辑放在 `shutdown`，初始化或配置刷新逻辑放在 `startup`。

SDK 装饰器仍然接受 `reload` 生命周期 ID 用于兼容，但插件管理面板的重载按钮不会派发它。

## 配置被外部修改

当配置通过 UI 或 API 被修改时触发。适合不重启就应用新设置。

```python
    @lifecycle(id="config_change")
    async def on_config_change(self, old_config, new_config, mode):
        self.timeout = new_config.get("my_settings", {}).get("timeout", 30)
        self.logger.info("配置更新模式: {}", mode)
        self.logger.info("配置已更新，新超时: {}s", self.timeout)
        return Ok({"status": "config_updated"})
```

## 生命周期事件和重载行为

| 事件/操作 | 发生时机 | 典型用途 |
|----|----------|----------|
| `startup` | 插件进程启动 | 初始化、加载配置、打开连接 |
| `shutdown` | 插件进程停止 | 关闭连接、保存状态、释放资源 |
| 插件管理面板重载 | 用户点击重载 | 先执行 `shutdown`，再执行 `startup` |
| `config_change` | 配置被外部修改 | 应用新设置 |
| `freeze` | 插件被挂起 | 暂停定时任务 |
| `unfreeze` | 插件被恢复 | 恢复定时任务 |

## 完整示例

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

    @plugin_entry(id="get_weather", name="查天气", description="查询城市天气")
    async def get_weather(self, city: str = "Beijing"):
        async with self.session.get(f"{self.api_url}/{city}?format=j1") as resp:
            data = await resp.json()
            return Ok({"city": city, "temp": data["current_condition"][0]["temp_C"]})
```
