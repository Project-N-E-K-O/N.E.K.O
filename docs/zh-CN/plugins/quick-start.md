# 插件快速开始

本指南手把手带你从零创建第一个插件。不需要任何插件开发经验。

## 前置条件

- N.E.K.O 已安装并能正常启动
- 基础 Python 知识（函数、类）

## 你要做什么

一个简单的 "Hello World" 插件，功能：按名字问候别人。

完成后，文件结构长这样：

```
plugin/
└── plugins/
    └── hello_world/          ← 你的新插件
        ├── plugin.toml       ← 配置文件：告诉 N.E.K.O 这个插件是什么
        └── __init__.py       ← 代码文件：你的插件逻辑
```

## 第一步：创建文件夹

在 N.E.K.O 项目中找到 `plugin/plugins/` 目录，在里面新建一个叫 `hello_world` 的文件夹。

## 第二步：创建 `plugin.toml`

在 `hello_world/` 里面，创建一个叫 `plugin.toml` 的文件。这是配置文件，告诉 N.E.K.O 你的插件是什么。

粘贴以下内容：

```toml
[plugin]
id = "hello_world"
name = "Hello World"
description = "我的第一个插件 - 按名字问候别人"
version = "0.1.0"
entry = "plugin.plugins.hello_world:HelloWorldPlugin"

[plugin.sdk]
recommended = ">=0.1.0,<0.2.0"
supported = ">=0.1.0,<0.3.0"

[plugin_runtime]
enabled = true
auto_start = true
```

关键点：
- `id` 是全局唯一插件 ID。建议与文件夹名保持一致（`hello_world`），避免 profile 查找和工具处理目录不匹配。
- `[plugin].entry` 告诉宿主加载哪个类，格式是 `模块路径:类名`；它不是下文的运行时 `entry_id`。
- `auto_start = true` 表示 N.E.K.O 启动时自动运行这个插件

## 第三步：创建 `__init__.py`

在 `hello_world/` 里面，创建一个叫 `__init__.py` 的文件。这是你的插件代码。

粘贴以下内容：

```python
from plugin.sdk.plugin import NekoPluginBase, neko_plugin, plugin_entry, Ok
from typing import Annotated


@neko_plugin
class HelloWorldPlugin(NekoPluginBase):
    """我的第一个插件。"""

    @plugin_entry(id="greet", name="问候", description="跟某人打个招呼")
    async def greet(self, name: Annotated[str, "要问候的名字"] = "World"):
        return Ok({"message": f"Hello, {name}!"})
```

每行代码的作用：

| 代码 | 作用 |
|------|------|
| `@neko_plugin` | 把这个类标记为插件 |
| `NekoPluginBase` | 基类 — 提供日志、配置、存储等能力 |
| `@plugin_entry(...)` | 让这个函数可以从插件管理面板调用 |
| `Annotated[str, "要问候的名字"]` | 一个字符串参数，带描述 |
| `= "World"` | 默认值，不传参数时使用 |
| `Ok({...})` | 返回成功结果 |

这里的运行时入口 ID 是 `greet`。用户插件 Agent 路由选择它时会返回 `plugin_id="hello_world"` 和 `entry_id="greet"`，宿主会严格校验两者。这与使用 `@llm_tool` 注册对话期 LLM 工具是两套不同机制。

## 第四步：运行

1. 启动（或重启）N.E.K.O
2. 从主界面打开 **插件管理** 面板
3. "Hello World" 出现在插件列表中，状态：运行中
4. 点击它 → 看到 **问候** 入口点
5. 点击执行，输入一个名字，查看结果

::: tip 已经在运行了？
不需要重启。打开插件管理面板 → 点击 **刷新** → 找到你的插件 → 点击 **启动**。
:::

## 第五步：修改并重载

修改 `__init__.py` 中的消息：

```python
return Ok({"message": f"嘿 {name}，欢迎来到 N.E.K.O！"})
```

保存 → 在插件管理面板中点击 **重载** → 完成。不需要重启。

## 接下来做什么？

| 我想要... | 看这个 |
|---|---|
| 添加更多功能和参数 | [SDK 参考](./sdk-reference) |
| 在启动/关闭时执行代码 | [装饰器](./decorators) |
| 让用户插件 Agent 路由选择入口 | [入口与参数](./entries) |
| 注册对话期 LLM 工具 | [LLM Tool Calling](./tool-calling) |
| 给插件做一个 UI 面板 | [Hosted UI](./hosted-ui) |
| 看真实的插件示例 | [示例](./examples) |
| 正确处理错误 | [最佳实践](./best-practices) |
