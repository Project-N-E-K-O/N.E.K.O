# 插件快速开始

## 第一步：创建插件目录

```bash
mkdir -p plugin/plugins/hello_world
```

## 第二步：创建 `plugin.toml`

```toml
[plugin]
id = "hello_world"
name = "Hello World Plugin"
description = "A simple example plugin"
version = "1.0.0"
entry = "plugins.hello_world:HelloWorldPlugin"

[plugin.sdk]
recommended = ">=0.1.0,<0.2.0"
supported = ">=0.1.0,<0.3.0"
```

### 配置字段

| 字段 | 是否必需 | 说明 |
|------|----------|------|
| `id` | 是 | 唯一插件标识符 |
| `name` | 否 | 显示名称 |
| `description` | 否 | 插件描述 |
| `version` | 否 | 插件版本 |
| `entry` | 是 | 入口点：`module_path:ClassName` |

### SDK 版本字段

| 字段 | 说明 |
|------|------|
| `recommended` | 推荐的 SDK 版本范围 |
| `supported` | 最低支持范围（不满足则拒绝加载） |
| `untested` | 允许但加载时会发出警告 |
| `conflicts` | 拒绝的版本范围 |

## 第三步：创建 `__init__.py`

```python
from plugin.sdk.plugin import (
    NekoPluginBase, neko_plugin, plugin_entry, lifecycle,
    Ok, Err,
)
from typing import Any

@neko_plugin
class HelloWorldPlugin(NekoPluginBase):
    """Hello World 插件示例。"""

    def __init__(self, ctx: Any):
        super().__init__(ctx)
        self.logger = ctx.logger
        self.counter = 0

    @lifecycle(id="startup")
    def on_startup(self, **_):
        self.logger.info("HelloWorldPlugin started!")
        return Ok({"status": "ready"})

    @lifecycle(id="shutdown")
    def on_shutdown(self, **_):
        self.logger.info("HelloWorldPlugin stopped!")
        return Ok({"status": "stopped"})

    @plugin_entry(
        id="greet",
        name="Greet",
        description="Return a greeting message",
        input_schema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Name to greet",
                    "default": "World"
                }
            }
        }
    )
    def greet(self, name: str = "World", **_):
        self.counter += 1
        message = f"Hello, {name}! (call #{self.counter})"
        self.logger.info(f"Greeting: {message}")
        return Ok({"message": message, "count": self.counter})
```

### 关键要点

- **`@neko_plugin`** — 必需的类装饰器，将类注册为插件
- **`NekoPluginBase`** — 所有插件必须继承的基类
- **`@plugin_entry`** — 定义一个可外部调用的入口点
- **`@lifecycle`** — 处理生命周期事件（`startup`、`shutdown`、`reload`）
- **`Ok(...)` / `Err(...)`** — 返回 Result 类型，实现类型安全的错误处理
- **`**_`** — 始终在入口点签名中包含此参数，以捕获额外的参数

## 第四步：测试

启动插件服务器后，通过 HTTP 调用你的插件：

```bash
curl -X POST http://localhost:48916/plugin/trigger \
  -H "Content-Type: application/json" \
  -d '{
    "plugin_id": "hello_world",
    "entry_id": "greet",
    "args": {"name": "N.E.K.O"}
  }'
```

## 下一步

- [SDK 参考](./sdk-reference) — 了解 `NekoPluginBase`、Result 类型和运行时辅助工具
- [装饰器](./decorators) — 所有可用的装饰器类型，包括钩子
- [Hosted UI](./hosted-ui) — 添加交互式 TSX 面板或 Markdown 教程页
- [示例](./examples) — 完整的可运行插件示例
