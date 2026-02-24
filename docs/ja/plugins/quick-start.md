# プラグイン クイックスタート

## ステップ 1: プラグインディレクトリの作成

```bash
mkdir -p plugin/plugins/hello_world
```

## ステップ 2: `plugin.toml` の作成

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

### 設定フィールド

| フィールド | 必須 | 説明 |
|-----------|------|------|
| `id` | はい | 一意のプラグイン識別子 |
| `name` | いいえ | 表示名 |
| `description` | いいえ | プラグインの説明 |
| `version` | いいえ | プラグインバージョン |
| `entry` | はい | エントリーポイント：`module_path:ClassName` |

### SDK バージョンフィールド

| フィールド | 説明 |
|-----------|------|
| `recommended` | 推奨 SDK バージョン範囲 |
| `supported` | 最小サポート範囲（満たさない場合は拒否） |
| `untested` | 許可されるが読み込み時に警告 |
| `conflicts` | 拒否されるバージョン範囲 |

## ステップ 3: `__init__.py` の作成

```python
from plugin.sdk.base import NekoPluginBase
from plugin.sdk.decorators import neko_plugin, plugin_entry
from typing import Any

@neko_plugin
class HelloWorldPlugin(NekoPluginBase):
    """Hello World plugin example"""

    def __init__(self, ctx: Any):
        super().__init__(ctx)
        self.logger = ctx.logger
        self.logger.info("HelloWorldPlugin initialized")

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
        """Greeting function"""
        message = f"Hello, {name}!"
        self.logger.info(f"Greeting: {message}")
        return {
            "message": message
        }
```

## ステップ 4: テスト

プラグインサーバーを起動した後、HTTP でプラグインを呼び出します：

```bash
curl -X POST http://localhost:48916/plugin/trigger \
  -H "Content-Type: application/json" \
  -d '{
    "plugin_id": "hello_world",
    "entry_id": "greet",
    "args": {"name": "N.E.K.O"}
  }'
```

## 次のステップ

- [SDK リファレンス](./sdk-reference) — `NekoPluginBase` と `PluginContext` について学ぶ
- [デコレーター](./decorators) — 利用可能なすべてのデコレータータイプ
- [サンプル](./examples) — 完全に動作するプラグインのサンプル
