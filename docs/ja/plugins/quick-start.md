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
from plugin.sdk.plugin import (
    NekoPluginBase, neko_plugin, plugin_entry, lifecycle,
    Ok, Err,
)
from typing import Any

@neko_plugin
class HelloWorldPlugin(NekoPluginBase):
    """Hello World プラグインのサンプル。"""

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

### 重要なポイント

- **`@neko_plugin`** — 必須のクラスデコレーター、クラスをプラグインとして登録します
- **`NekoPluginBase`** — すべてのプラグインが継承する必要があるベースクラス
- **`@plugin_entry`** — 外部から呼び出し可能なエントリーポイントを定義します
- **`@lifecycle`** — ライフサイクルイベント（`startup`、`shutdown`、`reload`）を処理します
- **`Ok(...)` / `Err(...)`** — 型安全なエラーハンドリングのための Result 型を返します
- **`**_`** — 追加パラメーターをキャプチャするため、エントリーポイントのシグネチャに常に含めてください

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

- [SDK リファレンス](./sdk-reference) — `NekoPluginBase`、Result 型、ランタイムヘルパーについて学ぶ
- [デコレーター](./decorators) — フックを含むすべてのデコレータータイプ
- [サンプル](./examples) — 完全に動作するプラグインのサンプル
