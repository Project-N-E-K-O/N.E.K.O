# PluginRouter — 拆分大型插件

当你的插件功能越来越多，把所有入口点都写在一个 `__init__.py` 里会变得难以维护。`PluginRouter` 让你把入口点按功能分组到不同文件中，同时它们仍然属于同一个插件。

---

## 什么时候需要 Router

- 你的插件有大约 5 个以上入口点
- 不同入口点属于不同的功能模块（比如"天气"、"路线"、"美食"）
- 你想让多人协作开发同一个插件，各自负责不同模块
- 主模块超过几百行，想拆分文件

如果你的插件只有 1-3 个入口点，直接写在主文件里就好，不需要 Router。

---

## 实际效果

以"生活助手"插件为例，它有 12 个功能模块：

```text
plugin/plugins/lifekit/
├── __init__.py              ← 主插件：注册所有 router
├── routers/
│   ├── __init__.py          ← 导出所有 router
│   ├── current.py           ← 当前天气
│   ├── hourly.py            ← 逐小时预报
│   ├── travel.py            ← 出行建议
│   ├── locations.py         ← 地点管理
│   ├── trip.py              ← 路线规划
│   ├── nearby.py            ← 附近搜索
│   ├── food.py              ← 美食推荐
│   ├── recipe.py            ← 菜谱
│   ├── air_quality.py       ← 空气质量
│   ├── currency.py          ← 汇率换算
│   ├── countdown.py         ← 倒计时
│   └── unit_convert.py      ← 单位换算
└── plugin.toml
```

在插件管理面板中，用户看到的是一个"生活助手"插件，下面有 12+ 个入口点。他们不需要知道代码是怎么组织的。

---

## 怎么写一个 Router

### 第一步：创建 Router 文件

```python
# routers/countdown.py

from plugin.sdk.plugin import PluginRouter, plugin_entry, Ok, Err, SdkError


class CountdownRouter(PluginRouter):
    """倒计时功能。"""

    def __init__(self):
        super().__init__(name="countdown")

    @plugin_entry(
        id="countdown",
        name="倒计时",
        description="计算距离某个日期还有多少天",
    )
    async def countdown(self, target_date: str, label: str = ""):
        # 你的业务逻辑
        ...
        return Ok({"summary": f"距离 {label} 还有 30 天"})

    @plugin_entry(
        id="days_between",
        name="日期间隔",
        description="计算两个日期之间相隔多少天",
    )
    async def days_between(self, start_date: str = "", end_date: str = ""):
        ...
        return Ok({"summary": "共 100 天"})
```

关键点：
- 继承 `PluginRouter`
- `super().__init__(name="countdown")` 给 router 一个名字（用于调试日志）
- 用 `@plugin_entry` 定义入口点，写法和主插件里完全一样

### 第二步：在主插件中注册

```python
# __init__.py

from plugin.sdk.plugin import NekoPluginBase, neko_plugin, lifecycle, Ok
from .routers import CountdownRouter, WeatherRouter

@neko_plugin
class LifeKitPlugin(NekoPluginBase):
    # 在这里列出所有 router 类：宿主从这里扫描 router 入口
    __routers__ = [CountdownRouter, WeatherRouter]

    def __init__(self, ctx):
        super().__init__(ctx)

        # 注册 routers — 必须在 __init__ 中
        for router_cls in self.__routers__:
            self.include_router(router_cls())

    @lifecycle(id="startup")
    async def on_startup(self):
        self.logger.info("生活助手已启动")
        return Ok({"status": "ready"})
```

`self.include_router()` 绑定 router，并把其中的入口点注册到当前插件下，使它们可以运行。`__routers__` 类属性同样必须写：宿主不实例化插件就从插件类读取入口元数据，而且只扫描 `__routers__` 里列出的 router（类；实例则通过它的 `collect_entries()`）。只传给 `include_router()` 的 router 仍然能运行，但它的入口不在这次静态扫描里（`include_router()` 不会把它们报给宿主）：除非 `plugin.toml` 里有 `[[plugin.entries]]` 条目声明了它们（这样的条目只带它自己写的字段），插件管理面板和 Agent 的入口列表里都看不到它们；Hosted UI 动作也可能找不到它们；经宿主发起的调用以宿主默认超时为上限（装饰器的 `timeout` 更短时，插件进程仍会按它执行）。对列在 `__routers__` 里的 router 入口，`plugin.toml` 中 `[[plugin.entries]]` 的配置覆盖和主类上的入口一样生效。

用旧版 `neko-plugin build` 打包的插件（打包元数据 schema 4 及更早）里，router 入口可能缺少宿主侧的 handler。宿主无法区分，所以这类插件包第一次启动时都会重新扫描一次入口，并重写打包元数据；之后照常启动，用户不用做什么。如果元数据无法重写（例如插件包目录只读），每次启动都会重新扫描。用当前的 `neko-plugin build` 重新打包可以省掉这次扫描。

---

## Router 里能访问什么

Router 被注册后，会自动绑定到主插件。你可以通过以下属性访问主插件的能力：

```python
from plugin.sdk.plugin import unwrap

class MyRouter(PluginRouter):

    @plugin_entry(id="example", name="示例", description="演示 router 能力")
    async def example(self):
        # 日志
        self.logger.info("Router 中打日志")

        # 读取配置
        cfg = await self.config.dump()

        # 使用存储
        unwrap(await self.store.set("key", "value"))

        # 调用其他插件
        result = await self.plugins.call_entry("other:entry")

        # 访问数据库
        async with unwrap(await self.db.session()) as session:
            cursor = await session.execute("SELECT * FROM notes")
            rows = cursor.fetchall()

        # 访问主插件的自定义属性/方法
        plugin = self.main_plugin
        data = await plugin.some_shared_method()

        return Ok({"done": True})
```

| 属性 | 来源 |
|------|------|
| `self.logger` | 主插件的 logger |
| `self.config` | 主插件的 config |
| `self.store` | 主插件的 store |
| `self.db` | 主插件的 db |
| `self.plugins` | 主插件的 plugins |
| `self.plugin_id` | 主插件的 ID |
| `self.main_plugin` | 主插件实例本身 |

Router 不是独立进程，它和主插件运行在同一个进程中，共享所有资源。

Router 也不是 manifest 加载入口：`[plugin].entry` 必须解析到所属的 `NekoPluginBase` 类。把它指向 `PluginRouter` 子类会被宿主拒绝。`include_router()` 负责绑定并收集入口。Router 类上的 `on_mount` / `on_unmount` 不会被宿主调用；不要覆盖它们，也不要把初始化或清理写在里面。

---

## 共享逻辑

多个 router 需要用到相同的工具函数时，放在主插件或单独的模块中：

```text
plugin/plugins/lifekit/
├── __init__.py          ← 主插件，定义共享方法
├── _geo.py              ← 共享：地理位置解析
├── _api.py              ← 共享：API 调用工具
├── _chat.py             ← 共享：推送消息到聊天
└── routers/
    ├── current.py       ← 用 self.main_plugin._resolve_location()
    └── travel.py        ← 用 self.main_plugin._resolve_location()
```

Router 通过 `self.main_plugin` 访问主插件上的方法：

```python
class WeatherRouter(PluginRouter):

    @plugin_entry(id="get_weather", name="查天气", description="查询天气")
    async def get_weather(self, city: str = ""):
        plugin = self.main_plugin
        # 调用主插件上的共享方法
        location, error = await plugin._resolve_location(city)
        if not location:
            return Err(SdkError(error))
        ...
```

---

## 带前缀的 Router

如果你想给某个 router 的所有入口加一个前缀（避免 ID 冲突）：

```python
self.include_router(CountdownRouter(), prefix="time_")
```

这样 `countdown` 入口的实际 ID 变成 `time_countdown`。

::: warning 不要把 `prefix` 和 `__routers__` 一起用
router 类列在 `__routers__` 里时（如上面的示例），宿主的静态扫描从类上读取入口 ID，看不到这个前缀。宿主登记的 ID（`countdown`）和运行时的 ID（`time_countdown`）对不上，宿主入口列表里显示的 ID 调不通，真正能调用的 ID 又不在列表里，宿主也就找不到它的超时和 `[[plugin.entries]]` 配置覆盖。列在 `__routers__` 里的 router 类不要用 `prefix`，直接把命名空间写进入口 ID（例如 `id="time_countdown"`）。

这条只针对 router 类。如果 `__routers__` 里放的是 router 实例，扫描会调用它们的 `collect_entries()`，会带上实例上已经设好的前缀（例如 `set_prefix("time_")`）。这时要把同一批实例传给 `self.include_router(router)`；上面 `router_cls()` 的循环只适用于类。
:::

大多数情况下不需要前缀——只要确保不同 router 里的入口 ID 不重复就行。

---

## 运行时移除

`exclude_router()` 会把 router 从插件的 router 列表中移除，但普通插件代码不应把它当作实时功能开关。入口点是在宿主构建 dispatch table 时收集的，之后再移除 router 不会自动让已经收集的入口不可调用。

如果需要运行时启用/禁用功能，请在入口逻辑中使用明确的插件配置判断；dispatch 结构变化时重建或重启插件。不要依赖已经移除的 Extension 控制路径。

```python
# 只从 router 列表移除
self.exclude_router(my_router_instance)

# 按名字同样如此
self.exclude_router("countdown")
```

---

## 完整的最小示例

一个有两个 router 的插件：

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

    @plugin_entry(id="add", name="加法", description="两数相加")
    async def add(self, a: float, b: float):
        return Ok({"result": a + b})

    @plugin_entry(id="divide", name="除法", description="两数相除")
    async def divide(self, a: float, b: float):
        if b == 0:
            return Err(SdkError("不能除以零"))
        return Ok({"result": a / b})


# __init__.py
from plugin.sdk.plugin import NekoPluginBase, neko_plugin
from .routers.greet import GreetRouter
from .routers.math import MathRouter

@neko_plugin
class MyPlugin(NekoPluginBase):
    __routers__ = [GreetRouter, MathRouter]

    def __init__(self, ctx):
        super().__init__(ctx)
        for router_cls in self.__routers__:
            self.include_router(router_cls())
```

这个插件在面板中会显示三个入口点：`hello`、`add`、`divide`。
