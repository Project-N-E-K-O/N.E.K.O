# デコレーター

すべてのデコレーターは `plugin.sdk.plugin` からインポートします。

```python
from plugin.sdk.plugin import (
    neko_plugin, plugin_entry, lifecycle, timer_interval, message,
    on_event, custom_event,
    hook, before_entry, after_entry, around_entry, replace_entry,
    plugin,  # 名前空間スタイルの代替
)
```

## @neko_plugin

クラスを N.E.K.O. プラグインとしてマークします。すべてのプラグインクラスに**必須**です。

```python
@neko_plugin
class MyPlugin(NekoPluginBase):
    pass
```

## @plugin_entry

外部から呼び出し可能なエントリーポイントを定義します。

```python
@plugin_entry(
    id="process",                # エントリーポイント ID（省略時はメソッド名から自動生成）
    name="Process Data",         # 表示名
    description="Process data",  # 説明
    input_schema={...},          # バリデーション用 JSON Schema
    params=MyParamsModel,        # 代替：入力用 Pydantic モデル（スキーマを自動生成）
    kind="action",               # "action" | "service" | "hook" | "custom"
    auto_start=False,            # 読み込み時に自動開始
    persist=False,               # リロード間で永続化
    model_validate=True,         # Pydantic バリデーションを有効化
    timeout=30.0,                # 実行タイムアウト（秒）
    llm_result_fields=["text"],  # LLM 消費用に抽出するフィールド
    llm_result_model=MyResult,   # 結果スキーマ用 Pydantic モデル
    metadata={"category": "data"}  # 追加メタデータ
)
def process(self, data: str, **_):
    return Ok({"result": data})
```

### パラメーター

| パラメーター | 型 | デフォルト | 説明 |
|------------|------|----------|------|
| `id` | `str` | メソッド名 | 一意のエントリーポイント識別子 |
| `name` | `str` | `None` | 表示名 |
| `description` | `str` | `""` | 説明 |
| `input_schema` | `dict` | `None` | 入力バリデーション用 JSON Schema |
| `params` | `type` | `None` | Pydantic モデル（`input_schema` を自動生成） |
| `kind` | `str` | `"action"` | エントリータイプ |
| `auto_start` | `bool` | `False` | 読み込み時に自動開始 |
| `persist` | `bool` | `None` | リロード間で状態を永続化 |
| `model_validate` | `bool` | `True` | Pydantic バリデーションを有効化 |
| `timeout` | `float` | `None` | 実行タイムアウト（秒） |
| `llm_result_fields` | `list[str]` | `None` | LLM 結果抽出用フィールド |
| `llm_result_model` | `type` | `None` | 結果スキーマ用 Pydantic モデル |
| `fields` | `type` | `None` | `params` のエイリアス |
| `metadata` | `dict` | `None` | 追加メタデータ |

::: tip
未使用のパラメーターを適切にキャプチャするため、関数シグネチャに常に `**_` を含めてください。
:::

## @lifecycle

ライフサイクルイベントハンドラーを定義します。

```python
@lifecycle(id="startup")
def on_startup(self, **_):
    self.logger.info("Starting up...")
    return Ok({"status": "ready"})

@lifecycle(id="shutdown")
def on_shutdown(self, **_):
    self.logger.info("Shutting down...")
    return Ok({"status": "stopped"})

@lifecycle(id="reload")
def on_reload(self, **_):
    self.logger.info("Reloading config...")
    return Ok({"status": "reloaded"})
```

有効なライフサイクル ID: `startup`、`shutdown`、`reload`、`freeze`、`unfreeze`、`config_change`

## @timer_interval

固定間隔で実行されるスケジュールタスクを定義します。

```python
@timer_interval(
    id="cleanup",
    seconds=3600,           # 1時間ごとに実行
    name="Cleanup Task",
    auto_start=True          # 自動的に開始（デフォルト: True）
)
def cleanup(self, **_):
    # 別スレッドで実行
    return Ok({"cleaned": True})
```

::: info
タイマータスクは別スレッドで実行されます。例外はログに記録されますが、タイマーは停止しません。
:::

## @message

ホストシステムからのメッセージハンドラーを定義します。

```python
@message(
    id="handle_chat",
    source="chat",           # メッセージソースでフィルタリング
    auto_start=True
)
def handle_chat(self, text: str, sender: str, **_):
    return Ok({"handled": True})
```

## @on_event

カスタムイベントタイプの汎用イベントハンドラーです。

```python
@on_event(
    event_type="custom_event",
    id="my_handler",
    kind="hook"
)
def custom_handler(self, event_data: str, **_):
    return Ok({"processed": True})
```

## @custom_event

トリガーメソッド制御を備えた特殊化されたイベントハンドラーです。

```python
@custom_event(
    event_type="data_refresh",
    id="refresh_handler",
    trigger_method="message",  # このイベントがトリガーされる方法
    auto_start=False
)
def on_refresh(self, source: str, **_):
    return Ok({"refreshed": True})
```

---

## フックデコレーター（AOP）

フックデコレーターはアスペクト指向プログラミング機能を提供します。エントリーポイントの実行をインターセプトします。

### @before_entry

ターゲットのエントリーポイントの前に実行されます。引数を変更したり、実行を中止したりできます。

```python
@before_entry(target="process", priority=0)
def validate_input(self, *, args, entry_id, **_):
    if not args.get("data"):
        return Err(SdkError("data is required"))
    # 続行するには None を返し、中止するには Err を返す
```

### @after_entry

ターゲットのエントリーポイントの後に実行されます。結果を変更または置換できます。

```python
@after_entry(target="process", priority=0)
def log_result(self, *, result, entry_id, **_):
    self.logger.info(f"Entry {entry_id} returned: {result}")
    # 元の結果を維持するには None を返し、置換するには新しい値を返す
```

### @around_entry

ターゲットのエントリーポイントをラップします。実行を完全に制御できます。

```python
@around_entry(target="process", priority=0)
async def timing_wrapper(self, *, proceed, args, **_):
    import time
    start = time.time()
    result = await proceed(**args)
    elapsed = time.time() - start
    self.logger.info(f"Took {elapsed:.2f}s")
    return result
```

### @replace_entry

ターゲットのエントリーポイントを完全に置換します。

```python
@replace_entry(target="old_entry", priority=0)
def new_implementation(self, **kwargs):
    return Ok({"replaced": True})
```

### フックパラメーター

| パラメーター | 型 | デフォルト | 説明 |
|------------|------|----------|------|
| `target` | `str` | `"*"` | フック対象のエントリー ID（`"*"` = 全エントリー） |
| `priority` | `int` | `0` | 実行順序（小さいほど先に実行） |
| `condition` | `str` | `None` | オプションの条件式 |

---

## 名前空間スタイルの代替: `plugin.*`

よりクリーンな構文のために、`plugin` 名前空間オブジェクトを使用できます：

```python
from plugin.sdk.plugin import plugin

@plugin.entry(id="greet", description="Say hello")
def greet(self, name: str = "World", **_):
    return Ok({"message": f"Hello, {name}!"})

@plugin.lifecycle(id="startup")
def on_startup(self, **_):
    return Ok({"status": "ready"})

@plugin.hook(target="greet", timing="before")
def validate(self, *, args, **_):
    pass

@plugin.timer(id="heartbeat", seconds=60)
def heartbeat(self, **_):
    return Ok({"alive": True})

@plugin.message(id="on_chat", source="chat")
def on_chat(self, text: str, **_):
    return Ok({"handled": True})
```
