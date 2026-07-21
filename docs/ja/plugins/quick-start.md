# プラグイン クイックスタート

このガイドでは、Plugin Manager から実行できる `Hello World` plugin を作ります。

## 前提条件

- N.E.K.O が正常に起動すること
- Python の class と function の基本を理解していること

## 作るもの

名前を受け取り greeting を返す runtime entry を 1 つ持つ `Hello World` plugin を作ります。完成後は Plugin Manager から start、execute、reload できます。

## 1. ディレクトリを作る

repository の `plugin/plugins/` の下に `hello_world/` を作ります。

```text
plugin/plugins/hello_world/
├── plugin.toml
└── __init__.py
```

## 2. `plugin.toml` を作る

```toml
[plugin]
id = "hello_world"
name = "Hello World"
description = "My first plugin — greets people by name"
version = "0.1.0"
entry = "plugin.plugins.hello_world:HelloWorldPlugin"

[plugin.sdk]
recommended = ">=0.1.0,<0.2.0"
supported = ">=0.1.0,<0.3.0"

[plugin_runtime]
enabled = true
auto_start = true
```

- `id` は一意な plugin ID です。directory 名と同じ `hello_world` にすることを推奨します。
- `[plugin].entry` は host-loading entry で、`module.path:ClassName` 形式です。
- `auto_start = true` なら N.E.K.O 起動時に process を開始します。

`[plugin].entry` は runtime operation の ID ではありません。次の `greet` は `@plugin_entry(id="greet")` から作られます。

## 3. `__init__.py` を作る

```python
from typing import Annotated

from plugin.sdk.plugin import NekoPluginBase, Ok, neko_plugin, plugin_entry


@neko_plugin
class HelloWorldPlugin(NekoPluginBase):
    """My first plugin."""

    @plugin_entry(id="greet", name="Greet", description="Say hello to someone")
    async def greet(self, name: Annotated[str, "Name to greet"] = "World"):
        return Ok({"message": f"Hello, {name}!"})
```

| Code | Meaning |
|---|---|
| `@neko_plugin` | class を plugin として宣言 |
| `NekoPluginBase` | config、storage、bus などを提供する base class |
| `@plugin_entry(...)` | runtime entry `greet` を公開 |
| `Annotated[...]` | parameter の schema description |
| `Ok({...})` | successful `Result` |

Agent の user-plugin route がこの operation を選ぶ場合、結果は `plugin_id="hello_world"` と `entry_id="greet"` です。host は候補に対して両方を検証します。これは `@llm_tool` を main_server の tool registry に登録する仕組みとは別です。

## 4. 起動して実行する

1. N.E.K.O を起動または再起動します。
2. main interface から **Plugin Manager** を開きます。
3. `Hello World` を開き、`Greet` entry を実行します。
4. parameter を入力し、`Ok` result を確認します。

すでに N.E.K.O が起動している場合は、Plugin Manager を refresh して plugin を start できます。未公開の internal endpoint を直接呼ぶ手順には依存しません。

## 5. 変更を reload する

`__init__.py` を変更したら Plugin Manager の **Reload** を使います。現在の reload は `shutdown` 後に process を再起動し、`startup` を実行します。`reload` lifecycle ID 自体は compatibility のため受理されますが、Reload button はその hook を dispatch しません。

## 次のステップ

| 目的 | ドキュメント |
|---|---|
| parameter と runtime entry | [エントリーとパラメーター](./entries) |
| lifecycle と decorator | [デコレーター](./decorators) |
| conversation-time LLM tool | [LLM Tool Calling](./tool-calling) |
| SDK API | [SDK リファレンス](./sdk-reference) |
| error handling | [ベストプラクティス](./best-practices) |
