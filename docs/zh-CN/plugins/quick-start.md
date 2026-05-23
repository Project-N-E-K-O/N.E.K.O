# 插件快速开始

## 1. 创建插件

```bash
mkdir -p plugin/plugins/hello_world
```

创建 `plugin/plugins/hello_world/plugin.toml`：

```toml
[plugin]
id = "hello_world"
name = "Hello World"
description = "一个简单的问候插件"
version = "0.1.0"
entry = "plugin.plugins.hello_world:HelloWorldPlugin"

[plugin.sdk]
recommended = ">=0.1.0,<0.2.0"
supported = ">=0.1.0,<0.3.0"

[plugin_runtime]
enabled = true
auto_start = true
```

创建 `plugin/plugins/hello_world/__init__.py`：

```python
from plugin.sdk.plugin import NekoPluginBase, neko_plugin, plugin_entry, Ok
from typing import Annotated

@neko_plugin
class HelloWorldPlugin(NekoPluginBase):

    @plugin_entry(id="greet", name="问候", description="打个招呼")
    async def greet(self, name: Annotated[str, "要问候的名字"] = "World"):
        return Ok({"message": f"Hello, {name}!"})
```

## 2. 运行

启动 N.E.K.O，打开插件管理面板。你的插件会出现在列表中。点击启动，打开详情页，执行 `greet` 入口。

修改代码 → 在面板中点击 **重载** → 立即生效。

## 3. 发生了什么

- `@neko_plugin` + `NekoPluginBase` — 让它成为一个插件
- `@plugin_entry` — 暴露一个可调用的入口点
- 类型注解 — SDK 自动从中生成 input schema
- `Ok(...)` — 包装返回值（错误用 `Err(...)`）
- 插件运行在独立进程中，崩溃不影响 N.E.K.O

## 下一步

- [SDK 参考](./sdk-reference) — 基类 API、Result 类型、跨插件调用
- [装饰器](./decorators) — 生命周期、定时器、钩子、消息
- [LLM Tool Calling](./tool-calling) — 让 AI 调用你的插件
- [Hosted UI](./hosted-ui) — 用 TSX 构建交互面板
- [示例](./examples) — 真实插件示例
