# 用 CLI 创建第一个插件

这篇快速开始只做一件事：确认你电脑上的 Plugin CLI 可以正常运行，并用它创建一个能够继续开发的独立插件仓库。

完成后，你会得到一个已经包含插件示例、测试、代码检查和 GitHub 发布配置的 `hello_world` 项目。

## 1. 确认 Git 和 uv 已安装

打开终端并运行：

```bash
git --version
uv --version
```

两条命令都应显示版本号。如果系统找不到 `uv`，请先按照 [uv 安装说明](https://docs.astral.sh/uv/getting-started/installation/)完成安装。

## 2. 获取 N.E.K.O 源码

Plugin CLI 目前随 N.E.K.O 源码提供，还不能单独安装。第一次使用时，克隆官方仓库：

```bash
git clone --filter=blob:none https://github.com/Project-N-E-K-O/N.E.K.O.git
cd N.E.K.O
```

如果你已经有 N.E.K.O 源码，不要再次克隆，也不要删除现有目录。直接进入原来的仓库：

```bash
cd /path/to/N.E.K.O
```

::: warning 不要在有同名目录时继续克隆
`git clone` 遇到已存在的 `N.E.K.O` 目录会停止。先确认那个目录是不是你已有的源码；不要为了继续教程直接删除它，其中可能有你的配置或未提交修改。
:::

## 3. 准备环境并检查 CLI

在 N.E.K.O 仓库根目录运行：

```bash
uv sync --locked
uv run neko-plugin --help
```

帮助信息中应至少能看到：

```text
init
check
sync
build
publish
```

看到这些命令，说明 CLI 已经可以使用。后续都使用 `uv run neko-plugin`，不要假设系统中已经安装了一个全局的 `neko-plugin` 命令。

如果 `uv sync --locked` 失败，先停下来并保留完整错误信息；网络、Python 平台或依赖状态都可能是原因，不要通过重新生成 `uv.lock` 来绕过错误。只有准备更新源码时才运行 `git status --short`：如果有本地修改，先保留自己的工作，不要直接拉取或重置；如果仓库干净且帮助中缺少 `init` 或 `publish`，可以执行 `git pull --ff-only` 后重新运行上面的两条命令。

## 4. 创建独立插件仓库

仍在 N.E.K.O 仓库根目录运行：

```bash
uv run neko-plugin init hello_world --type plugin --name "Hello World" --output ../n.e.k.o_plugin_hello_world
```

`--output` 是最终目录，不会再自动追加一层文件夹。这里把插件放在 N.E.K.O 旁边，避免在 N.E.K.O 仓库内部创建嵌套 Git 仓库。

如果目标目录已经存在，CLI 会停止且不会覆盖其中的文件。请换一个新目录，或者先确认已有目录的用途。

## 5. 运行第一次检查

```bash
uv run neko-plugin check ../n.e.k.o_plugin_hello_world
```

新项目应显示：

```text
[OK] hello_world: check found 0 error(s), 2 warning(s)
```

此时出现“尚未配置 GitHub remote”或“工作区有未提交修改”的警告是正常的：项目还没有推送和提交。只有出现 `[FAIL]` 或 error 时才需要先停下来，根据命令给出的修复建议处理。

## CLI 已经为你准备了什么

```text
n.e.k.o_plugin_hello_world/
├── .git/
├── .gitignore
├── .vscode/
├── plugin.toml
├── config.example.toml
├── __init__.py
├── pyproject.toml
├── README.md
├── tests/test_smoke.py
├── ruff.toml
└── .github/workflows/
    ├── verify.yml
    └── release.yml
```

你不需要手写插件目录结构，也不需要自己拼 GitHub Actions。下面继续使用生成的文件完成第一个功能。

## 6. 看懂插件配置

打开 `../n.e.k.o_plugin_hello_world/plugin.toml`。CLI 已经写好插件身份和入口：

```toml
[plugin]
id = "hello_world"
name = "Hello World"
version = "0.1.0"
type = "plugin"
entry = "plugin.plugins.hello_world:HelloWorldPlugin"

[plugin.sdk]
recommended = ">=0.1.0,<0.2.0"
supported = ">=0.1.0,<0.3.0"
```

关键点：

- `id` 是插件的稳定身份，也是安装后的目录名；
- `version` 是下一次构建和发布使用的版本；
- `entry` 指向 Python 文件中的插件类，格式是 `模块路径:类名`；
- `[plugin.sdk]` 声明这个插件支持的 SDK 版本。

是否启用、是否自动启动属于用户运行设置，放在 `config.example.toml`，不放进插件身份配置：

```toml
[plugin_runtime]
enabled = true
auto_start = false
```

`auto_start = false` 表示安装后由你在插件管理器中手动启动；改成 `true` 才会随 N.E.K.O 自动启动。

## 7. 写第一个插件功能

打开生成的 `__init__.py`。它已经包含一个可以按名字问候用户的入口：

```python
from typing import Any

from plugin.sdk.plugin import (
    NekoPluginBase,
    Ok,
    lifecycle,
    neko_plugin,
    plugin_entry,
)


@neko_plugin
class HelloWorldPlugin(NekoPluginBase):
    """Hello World"""

    def __init__(self, ctx: Any):
        super().__init__(ctx)
        self.logger = ctx.logger

    @lifecycle(id="startup")
    def on_startup(self, **_):
        self.logger.info("HelloWorldPlugin started")
        return Ok({"status": "ready"})

    @lifecycle(id="shutdown")
    def on_shutdown(self, **_):
        self.logger.info("HelloWorldPlugin stopped")
        return Ok({"status": "stopped"})

    @plugin_entry(
        id="hello",
        name="Hello",
        description="Say hello",
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "default": "World"}
            }
        }
    )
    async def hello(self, name: str = "World", **_):
        return Ok({"message": f"Hello, {name}!"})
```

| 代码 | 作用 |
| --- | --- |
| `@neko_plugin` | 把这个类声明为 N.E.K.O 插件 |
| `NekoPluginBase` | 提供日志、配置、存储等插件能力 |
| `@lifecycle(...)` | 在插件启动和停止时执行代码 |
| `@plugin_entry(...)` | 在插件管理器中公开一个可调用功能 |
| `input_schema` | 描述界面需要显示的输入参数 |
| `Ok({...})` | 返回一次成功结果 |

`plugin_id` 是 `hello_world`，这个功能自己的 `entry_id` 是 `hello`。两者用途不同，不要互换。

::: tip Agent 和 LLM 工具
用户插件 Agent 选择这个功能时会同时返回 `plugin_id` 和 `entry_id`，宿主会分别校验它们。这与使用 `@llm_tool` 注册对话期工具是两套不同机制。第一次运行插件不需要先理解这一区别。
:::

现在把最后一行改成：

```python
return Ok({"message": f"你好，{name}！"})
```

保存后再次检查：

```bash
uv run neko-plugin check ../n.e.k.o_plugin_hello_world
```

## 8. 构建并在 N.E.K.O 中运行

检查通过后生成本地安装包：

```bash
uv run neko-plugin build ../n.e.k.o_plugin_hello_world --out ../hello_world.neko-plugin
```

启动 N.E.K.O，打开 **插件管理**，点击 **导入**，选择刚生成的 `hello_world.neko-plugin`。导入完成后：

1. 找到 **Hello World** 并启动；
2. 打开它的 **Hello** 入口；
3. 输入一个名字并执行；
4. 确认返回 `你好，名字！`。

如果还没有启动源码版 N.E.K.O，请先按[开发环境搭建](../guide/dev-setup)完成前端构建和启动。

## 9. 修改并重新载入

继续修改独立仓库中的 `__init__.py`，然后重新运行 `check` 和 `build`。在插件管理器中再次导入同一个包时，N.E.K.O 会先显示升级确认；确认后会安全替换已安装版本，运行中的插件会自动重启。

不要直接修改 `plugin/plugins/hello_world` 中已经安装的副本，否则改动不会回到你的独立插件仓库。

## 接下来做什么

| 我想要…… | 看这里 |
| --- | --- |
| 继续检查、打包并发布插件 | [用命令创建和发布插件](./cli) |
| 理解 `plugin.toml` | [插件配置](./plugin-toml) |
| 添加可调用的插件功能 | [入口与参数](./entries) |
| 在启动或关闭时执行代码 | [装饰器](./decorators) |
| 注册对话期 LLM 工具 | [LLM Tool Calling](./tool-calling) |
| 给插件制作 UI 面板 | [Hosted UI](./hosted-ui) |
| 查看真实插件示例 | [示例](./examples) |
| 正确处理错误 | [最佳实践](./best-practices) |
| 查询完整 SDK | [SDK 参考](./sdk-reference) |
