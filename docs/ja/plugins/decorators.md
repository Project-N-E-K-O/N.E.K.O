# デコレーター

すべてのデコレーターは `plugin.sdk.plugin` からインポートします。

```python
from plugin.sdk.plugin import (
    neko_plugin, plugin_entry, lifecycle, timer_interval, message,
    on_event, custom_event,
    hook, before_entry, after_entry, around_entry, replace_entry,
    plugin, quick_action,  # namespace style と command-palette hint
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

外部から呼び出し可能なエントリーポイントを定義します。`@llm_tool` との違いは [`@plugin_entry` と `@llm_tool` の選び方](/ja/plugins/tool-calling#choosing-an-entry-type)を参照してください。

```python
@plugin_entry(
    id="process",                # エントリーポイント ID（省略時はメソッド名から自動生成）
    name="Process Data",         # 表示名
    description="Process data",  # 説明
    params=MyParamsModel,        # Pydantic モデル：呼び出し時に入力を検証（スキーマを自動生成）
    # input_schema={...},        # 代替：JSON Schema。公開のみで検証されない。params と併用不可
    kind="action",               # "action" | "service" | "hook" | "custom"
    auto_start=False,            # metadata flag。通常 entry は load 時に自動実行されない
    persist=False,               # call 後の state snapshot policy を override
    model_validate=True,         # params モデルで入力を検証（False なら生の引数を渡す）
    timeout=30.0,                # 実行タイムアウト（秒）
    llm_result_fields=["text"],  # LLM 消費用に抽出するフィールド
    # llm_result_model=MyResult, # 代替：結果スキーマ用 Pydantic モデル（llm_result_fields と排他）
    output_schema={...},         # 戻り値の JSON Schema（出力契約）
    strict_output=False,         # True: 戻り値が契約に合わないとき呼び出しを失敗にする
    metadata={"category": "data"}  # 追加メタデータ
)
async def process(self, data: str, **_):
    return Ok({"result": data})
```

### パラメーター

| パラメーター | 型 | デフォルト | 説明 |
|------------|------|----------|------|
| `id` | `str` | メソッド名 | 一意のエントリーポイント識別子 |
| `name` | `str` | `None` | 表示名 |
| `description` | `str` | `""` | 説明 |
| `input_schema` | `dict` | `None` | 呼び出し側に公開する JSON Schema。**実行時には検証されない**。`params` と排他 |
| `params` | `type` | `None` | Pydantic モデル。ハンドラー実行前にこれで入力を検証し、スキーマを `input_schema` として公開する |
| `kind` | `str` | `"action"` | エントリータイプ |
| `auto_start` | `bool` | `False` | metadata flag。通常の `plugin_entry` handler は load 時に自動実行されない |
| `persist` | `bool` | `None` | entry 実行後に configured freezable state を保存するか override |
| `model_validate` | `bool` | `True` | `params` モデルで入力を検証。`False` なら生の引数を渡す |
| `timeout` | `float` | `None` | 実行タイムアウト（秒） |
| `llm_result_fields` | `list[str]` | `None` | LLM 結果抽出用フィールド |
| `llm_result_model` | `type` | `None` | 結果スキーマ用 Pydantic モデル |
| `fields` | `type` | `None` | `llm_result_model` のエイリアス |
| `metadata` | `dict` | `None` | 追加メタデータ |
| `output_schema` | `dict` | `None` | 戻り値の JSON Schema。省略時、戻り値の型注釈が Pydantic モデルなら自動で推論される。[エントリーとパラメーター](./entries)の「出力契約」を参照 |
| `strict_output` | `bool` | `False` | 戻り値が出力契約に合わないとき、警告ログに加えて呼び出しを失敗（`PLUGIN_OUTPUT_SCHEMA_VIOLATION`）にする。デフォルトは警告ログのみ。検査されるのは Pydantic の戻り値型注釈から推論した契約だけなので、明示した `output_schema` には効果がない |

`output_schema` は `llm_result_fields` / `llm_result_model` とは独立しています。後者はモデルが読むフィールドを決め、`output_schema` は戻り値全体を記述します。両方を同時に使えます。

Pydantic の戻り値型注釈から推論した契約は実行時にモデルで検証され（`params` と同じ）、明示した `output_schema` は公開されるだけです（`input_schema` と同じ）。[エントリーとパラメーター](./entries)の「出力契約」を参照してください。

::: tip
handler が host からの追加 field を意図的に受け取る場合だけ `**_` を使います。明示的な signature では runtime が未対応 keyword を filter するため、必須ではありません。
:::

実行時エントリーは `async def` で定義してください。ホストは同期エントリーを受け付けません。

## @lifecycle

起動、終了、外部からの設定変更、プロセスの一時停止を処理する任意のハンドラーです。初期化には `startup` を使います。通常の `@plugin_entry(auto_start=True)` は、プラグインプロセスの起動時には実行されません。

```python
@lifecycle(id="startup")
async def on_startup(self, **_):
    cfg = await self.config.dump()
    self.timeout = cfg.get("my_settings", {}).get("timeout", 30)
    return Ok({"status": "ready"})

@lifecycle(id="shutdown")
async def on_shutdown(self, **_):
    stop = getattr(self, "_worker_stop", None)  # 自分で起動したワーカースレッドの threading.Event
    if stop:
        stop.set()
    return Ok({"status": "stopped"})

@lifecycle(id="config_change")
async def on_config_change(self, old_config, new_config, mode):
    self.timeout = new_config.get("my_settings", {}).get("timeout", 30)
    return Ok({"status": "config_updated"})
```

| ライフサイクル ID または操作 | 実行される時点 | 主な用途 |
| --- | --- | --- |
| `startup` | プラグインプロセスの起動時 | 設定の読み込みと検証、通常のリソースの準備 |
| `shutdown` | プラグインプロセスの終了時 | 自分で起動したスレッドの停止、状態の保存、通常のリソースの解放 |
| Plugin Manager の再読み込み | ユーザーが再読み込みを実行 | `shutdown` の後にプロセスを起動し、`startup` を実行 |
| `config_change` | 外部から設定が変更されたとき | 再起動せずに新しい設定を反映 |
| `freeze` / `unfreeze` | プラグインの一時停止または再開時 | 処理の停止または再開 |

SDK は互換性のため `reload` ID を受け付けますが、Plugin Manager の再読み込みボタンはプロセスを再起動するため、このイベントを通知しません。`await self.ctx.update_own_config(...)` または `await self.config.update(...)` で設定を更新した場合も、同じプロセスには `config_change` が通知されません。呼び出し後に派生状態を更新してください。

`startup`、`shutdown`、タイマーの各実行はそれぞれ別のイベントループ（`asyncio.run`）で実行され、すべてのエントリー呼び出しはそれとは別の長期間動くコマンドループを共有します。`startup` 内で `asyncio.create_task` で作成したタスクは `startup` が戻った時点でキャンセルされ、あるフェーズで作成した非同期 client や session は別のフェーズでは使用もクローズもできません。非同期 client はそれを使う呼び出しの中で作成してください（`async with ...`）。

## @timer_interval

固定間隔で実行されるスケジュールタスクを定義します。

```python
@timer_interval(
    id="cleanup",
    seconds=3600,           # 1時間ごとに実行
    name="Cleanup Task",
    auto_start=True          # 自動的に開始（デフォルト: True）
)
async def cleanup(self, **_):
    # 別スレッドで実行
    return Ok({"cleaned": True})
```

::: info
timer task は `async def` が必須です。各 task は専用の timer thread で実行され、実行のたびに新しい event loop（`asyncio.run`）が使われるため、async client を `self` に保持して実行をまたいで再利用しないでください。exception は log されますが timer は停止しません。
:::

## @message

メッセージハンドラーを登録します。

::: warning ホストからは配信されない
現在、ホストはホストメッセージやチャットメッセージを `@message` ハンドラーに配信しません。ハンドラーが実行されるのは、他のプラグインが `self.plugins.call_event("<plugin_id>:message:<id>", ...)` で明示的に呼び出した場合だけです。これを前提に機能を作らないでください。
:::

```python
@message(
    id="handle_chat",
    source="chat",           # メッセージソースでフィルタリング
)
async def handle_chat(self, text: str, sender: str, **_):
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
async def custom_handler(self, event_data: str, **_):
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
async def on_refresh(self, source: str, **_):
    return Ok({"refreshed": True})
```

## @quick_action

plugin entry を command palette で優先表示します。Python が先に適用するよう `@plugin_entry` の下に置きます。

```python
@plugin_entry(id="get_weather", name="Get Weather")
@quick_action(icon="🌤️", priority=10)
async def get_weather(self, city: str = ""):
    return Ok({"city": city})
```

`priority` が大きいほど先に表示されます。display metadata だけを変更し、Agent routing や自動実行には影響しません。

---

## フックデコレーター（AOP）

::: warning 現在はメタデータを記録するだけで実行されない
これらのデコレーターはメソッドにフックのメタデータを記録するだけです。現在のホストと SDK はこれを実行しません（`HookExecutorMixin` は `NotImplementedError` を送出する契約上のスタブです）。そのため、エントリー呼び出しのインターセプト、変更、中止、置換は**行われません**。こうした処理はエントリー本体か、そこから呼ぶヘルパーに書いてください。以下は設計上の意味です。
:::

### @before_entry

設計上はターゲットのエントリーポイントの前に実行され、引数の変更や実行の中止ができます（現在は実行されません）。

```python
@before_entry(target="process", priority=0)
def validate_input(self, *, args, entry_id, **_):
    if not args.get("data"):
        return Err(SdkError("data is required"))
    # 続行するには None を返し、中止するには Err を返す
```

### @after_entry

設計上はターゲットのエントリーポイントの後に実行され、結果を変更または置換できます（現在は実行されません）。

```python
@after_entry(target="process", priority=0)
def log_result(self, *, result, entry_id, **_):
    self.logger.info(f"Entry {entry_id} returned: {result}")
    # 元の結果を維持するには None を返し、置換するには新しい値を返す
```

### @around_entry

設計上はターゲットのエントリーポイントをラップし、実行を完全に制御できます（現在は実行されません）。

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

設計上はターゲットのエントリーポイントを完全に置換します（現在は実行されません）。

```python
@replace_entry(target="old_entry", priority=0)
async def new_implementation(self, **kwargs):
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
async def greet(self, name: str = "World", **_):
    return Ok({"message": f"Hello, {name}!"})

@plugin.lifecycle(id="startup")
async def on_startup(self, **_):
    return Ok({"status": "ready"})

@plugin.hook(target="greet", timing="before")  # メタデータのみ、現在は実行されない
def validate(self, *, args, **_):
    pass

@plugin.timer(id="heartbeat", seconds=60)
async def heartbeat(self, **_):
    return Ok({"alive": True})

@plugin.message(id="on_chat", source="chat")  # ホストからは配信されない、@message を参照
async def on_chat(self, text: str, **_):
    return Ok({"handled": True})
```
