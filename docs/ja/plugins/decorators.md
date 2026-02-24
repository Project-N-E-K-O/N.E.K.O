# デコレーター

すべてのデコレーターは `plugin.sdk.decorators` からインポートします。

## @neko_plugin

クラスを N.E.K.O. プラグインとしてマークします。すべてのプラグインクラスに必須です。

```python
from plugin.sdk.decorators import neko_plugin

@neko_plugin
class MyPlugin(NekoPluginBase):
    pass
```

## @plugin_entry

外部から呼び出し可能なエントリーポイントを定義します。

```python
from plugin.sdk.decorators import plugin_entry

@plugin_entry(
    id="process",                # エントリーポイント ID（必須）
    name="Process Data",         # 表示名
    description="Process data",  # 説明
    input_schema={...},          # バリデーション用 JSON Schema
    kind="action",               # "action" | "service" | "hook"
    auto_start=False,            # 読み込み時に自動開始
    extra={"category": "data"}   # 追加メタデータ
)
def process(self, data: str, **_):
    return {"result": data}
```

### パラメーター

| パラメーター | 型 | デフォルト | 説明 |
|------------|------|----------|------|
| `id` | str | 必須 | 一意のエントリーポイント識別子 |
| `name` | str | None | 表示名 |
| `description` | str | `""` | 説明 |
| `input_schema` | dict | None | 入力バリデーション用 JSON Schema |
| `kind` | str | `"action"` | エントリータイプ |
| `auto_start` | bool | `False` | 読み込み時に自動開始 |
| `extra` | dict | None | 追加メタデータ |

::: tip
未使用のパラメーターを適切に処理するため、関数シグネチャに常に `**_` を含めてください。
:::

## @lifecycle

ライフサイクルイベントハンドラーを定義します。

```python
from plugin.sdk.decorators import lifecycle

@lifecycle(id="startup")
def on_startup(self, **_):
    self.logger.info("Starting up...")
    return {"status": "ready"}

@lifecycle(id="shutdown")
def on_shutdown(self, **_):
    self.logger.info("Shutting down...")
    return {"status": "stopped"}

@lifecycle(id="reload")
def on_reload(self, **_):
    self.logger.info("Reloading...")
    return {"status": "reloaded"}
```

有効なライフサイクル ID: `startup`、`shutdown`、`reload`

## @timer_interval

固定間隔で実行されるスケジュールタスクを定義します。

```python
from plugin.sdk.decorators import timer_interval

@timer_interval(
    id="cleanup",
    seconds=3600,           # 1時間ごとに実行
    name="Cleanup Task",
    auto_start=True          # 自動的に開始
)
def cleanup(self, **_):
    # 別スレッドで実行
    return {"cleaned": True}
```

::: info
タイマータスクは別スレッドで実行されます。例外はログに記録されますが、タイマーは停止しません。
:::

## @message

メインシステムからのメッセージハンドラーを定義します。

```python
from plugin.sdk.decorators import message

@message(
    id="handle_chat",
    source="chat",           # ソースでフィルタリング
    auto_start=True
)
def handle_chat(self, text: str, sender: str, **_):
    return {"handled": True}
```

## @on_event

カスタムイベントタイプの汎用イベントハンドラーです。

```python
from plugin.sdk.decorators import on_event

@on_event(
    event_type="custom_event",
    id="my_handler",
    kind="hook"
)
def custom_handler(self, event_data: str, **_):
    return {"processed": True}
```
