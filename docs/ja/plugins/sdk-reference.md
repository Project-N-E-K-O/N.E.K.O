# プラグイン機能と SDK リファレンス

すべてのプラグイン開発 API は `plugin.sdk.plugin` からインポートします。

```python
from plugin.sdk.plugin import (
    # ベース
    NekoPluginBase, PluginMeta,
    # デコレーター
    EntryKind, neko_plugin, plugin_entry, lifecycle, timer_interval, message,
    on_event, custom_event, hook, before_entry, after_entry, around_entry,
    replace_entry, quick_action, plugin, ui,
    # LLM tool と OS activity
    llm_tool, LlmToolMeta, OsActivitySnapshot, get_os_activity_snapshot,
    # plugin-local i18n と settings
    PluginI18n, tr, PluginSettings, SettingsField,
    # Result 型
    Ok, Err, Result, PushMessageResult, unwrap, unwrap_or,
    # ランタイムヘルパー
    Plugins, PluginRouter, PluginConfig, PluginStore,
    SystemInfo,
    # エラー
    SdkError, TransportError,
    # ロギング
    get_plugin_logger,
)
```

`plugin.sdk.plugin` が supported developer import surface です。root `plugin.sdk` package は意図的に conservative shared subset だけを公開するため、plugin-only helper がすべて再 export されるとは仮定しないでください。

## NekoPluginBase

すべてのプラグインは `NekoPluginBase` を継承する必要があります。

```python
@neko_plugin
class MyPlugin(NekoPluginBase):
    def __init__(self, ctx):
        super().__init__(ctx)
```

### プロパティ

| プロパティ | 型 | 説明 |
|----------|------|------|
| `self.ctx` | `PluginContext` | ランタイムコンテキスト（ホストにより注入） |
| `self.plugin_id` | `str` | このプラグインの一意の識別子 |
| `self.plugin_dir` | `Path` | コード、Manifest、静的リソースを含むインストール先ディレクトリ |
| `self.config_dir` | `Path` | `self.plugin_dir` の互換エイリアス |
| `self.storage_dir` | `Path` | このプラグインに割り当てられたユーザーストレージルート |
| `self.runtime_config_path` | `Path` | 外部ランタイム設定ファイルのパス |
| `self.metadata` | `dict` | 現在、プラグインプロセスでは常に `{}`（`plugin.toml` の内容は渡されません）。設定は `await self.config.dump()` で読みます |
| `self.bus` | `SdkBusContext` | host state の read/watch facade。publish/emit API はありません |
| `self.plugins` | `Plugins` | プラグイン間呼び出しヘルパー |
| `self.system_info` | `SystemInfo` | ホストシステムのメタデータ |

### よく使う機能

`NekoPluginBase` は、追加設定なしでログと実行時設定を提供します：

```python
self.logger.info("Processing request: %s", request_id)
timeout = await self.config.get_int("my_settings.timeout", default=30)
```

ログは Plugin Manager に表示され、ホストが管理するログディレクトリにも保存されます。ファイルの保存先とローテーション方針はホストが管理します。実行時設定の更新には `await self.ctx.update_own_config(...)` または `await self.config.update(...)` を使い、呼び出し後に設定へ依存する派生状態を更新してください。

データの種類に応じて保存方法を選びます：

| 用途 | API |
| --- | --- |
| 小さなキー・バリュー状態 | `[plugin.store]` を有効にして `self.store` を使用 |
| 構造化された SQLite データ | `[plugin.database]` を有効にして `self.db` を使用 |
| 任意の永続ファイル | `self.data_path(...)` |
| 再生成可能なファイル | `self.cache_path(...)` |
| 他のプラグインに読ませるファイル | `self.shared_path(...)` |
| 他のプラグインが公開したファイル | `[plugin.shared]` で宣言したうえで `self.shared_path_of("<plugin_id>", ...)` |

```python
from plugin.sdk.plugin import unwrap

unwrap(await self.store.set("last_query", "weather"))

async with unwrap(await self.db.session()) as session:
    await session.execute(
        "CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY, title TEXT)"
    )
    await session.commit()
```

プラグインの表示文を翻訳する場合は、`plugin.toml` で `[plugin.i18n]` を設定し、各言語の JSON ファイルを追加してから `self.i18n.t(...)` を使います：

```python
message = self.i18n.t("greeting", name="Alice")
```

`self.i18n.t(...)` はユーザーの言語に従いません。`locale=` を渡さない場合、プラグインの `default_locale`、その基本言語（例：`fr-CA` なら `fr`）、`en` の順に探し、最後に `default` 引数またはキー名を返します。ホストが登録するユーザー向け文字列（プラグインやエントリーの名前、説明、スキーマのラベル、UI アクションのラベル）には `tr("key", default="...")` を使ってください。こちらはホストがユーザーごとの言語で解決します。

### メソッド

#### `report_status(status: dict) -> None`

プラグインのステータスをホストプロセスに報告します。

```python
self.report_status({
    "status": "processing",
    "progress": 50,
    "message": "Halfway done..."
})
```

#### `push_message(**kwargs) -> PushMessageResult`

v2 schema でホストシステムにメッセージをプッシュします。`visibility` はユーザーのチャットまたは HUD への表示を決め、`ai_behavior` はモデルがメッセージを読むか応答するかを独立して決め、`parts` がテキスト・画像・`ui_action` を運びます。

```python
result = self.push_message(
    source="my_feature",
    visibility=["chat"],       # []、["chat"]、["hud"]、または両方
    ai_behavior="blind",       # "respond"、"read"、"blind"
    parts=[{"type": "text", "text": "タスクが完了しました"}],
    priority=5,
)
if not result["submitted"]:
    self.logger.warning("push failed: %s", result.get("reason"))
```

戻り値はローカル送信の結果（`submitted` / `reason`）だけを示します。2 つの軸の完全なセマンティクス、`parts` の種類、payload と画像のサイズ制限、`ctx.images.upload()`、v1 フィールドの移行は[メッセージ配信](./push-message)を参照してください。

#### `data_path(*parts) -> Path`

プラグインの `data/` ディレクトリ配下のパスを取得します。

```python
db_path = self.data_path("records.db")
# → <storage-root>/plugins/<plugin_id>/data/records.db
```

#### `cache_path(*parts) -> Path`

プラグインの削除可能なキャッシュディレクトリ配下のパスを取得します。

```python
preview_path = self.cache_path("preview.png")
# → <storage-root>/plugins/<plugin_id>/cache/preview.png
```

#### `shared_path(*parts) -> Path`

このプラグインの共有ディレクトリ配下のパスを取得します。ここに置いたファイルは他のプラグインに読ませるためのものです。ディレクトリは自動では作成されず、プラグインが削除されても残ります。ディレクトリ名はプラグイン id に基づきます。id に含まれる小文字の英字、数字、`_`、`-` 以外の文字はディレクトリ名の中でエスケープされるため、パスは必ずこのメソッドから取得してください。

```python
cities = self.shared_path("cities.json")
# → <storage-root>/plugin_shared/<plugin_id>/cities.json
cities.parent.mkdir(parents=True, exist_ok=True)
```

#### `shared_path_of(plugin_id, *parts) -> Path`

他のプラグインの共有ディレクトリ配下のパスを、読み取り用に取得します。そのプラグインの id が `plugin.toml` の `[plugin.shared] reads` に書かれている必要があり、書かれていなければ `ValidationError`（`code="shared_not_declared"`）が発生します。共有ディレクトリの外に出るパスも拒否されます（`code="shared_bad_path"`）。

```python
cities = self.shared_path_of("weather", "cities.json")
# → <storage-root>/plugin_shared/weather/cities.json
```

これはプラグイン間の取り決めであり、セキュリティ境界ではありません。プラグイン同士はサンドボックスで隔離されていません。取得したファイルは読み取り専用として扱ってください。

#### `register_dynamic_entry(entry_id, handler, ...) -> bool`

実行時にエントリーポイントを登録します（デコレーター経由ではなく）。

```python
async def greet(name: str = "World", **_):
    return Ok({"msg": f"Hi {name}"})

self.register_dynamic_entry(
    entry_id="dynamic_greet",
    handler=greet,
    name="Dynamic Greet",
    description="動的に登録された挨拶",
)
```

handler は `async def` 関数である必要があります。登録時は呼び出し可能かどうかしか確認しませんが、handler がコルーチン関数でないエントリーを呼び出すと `Entry must be 'async def'` で失敗します。

#### `unregister_dynamic_entry(entry_id) -> bool`

動的に登録されたエントリーを削除します。

#### `list_entries(include_disabled=False) -> list[dict]`

すべてのエントリーポイント（静的 + 動的）を一覧表示します。

#### `enable_entry(entry_id) / disable_entry(entry_id) -> bool`

実行時に動的エントリーを有効化または無効化します。

#### `register_static_ui(directory, *, index_file, cache_control) -> bool`

このプラグインの静的 Web UI ディレクトリを登録します。

```python
self.register_static_ui("static")  # <plugin_dir>/static/index.html を配信
```

#### `include_router(router, *, prefix) -> None`

大規模または機能分割された通常 Plugin を整理するために `PluginRouter` を mount します。

各 router クラスはプラグインのクラス属性 `__routers__` にも列挙してください。ホストの静的エントリースキャンはこのリストだけを読みます（[PluginRouter](./router) を参照）。ここに列挙した router クラスに `prefix` を組み合わせないでください。一覧の ID が呼び出せなくなります（[prefix 付き Router](./router#prefixed-routers) を参照）。関連 method は `exclude_router(router_or_name) -> bool`、`get_router(name)`、`list_routers()` です。Router は manifest `[plugin].entry` にはできません。`on_mount` / `on_unmount` はホストから呼ばれないため、オーバーライドしないでください。

#### Hosted/static UI と list action

Hosted TSX は exported `ui` namespace と manifest surface を使います。日本語 mirror は未整備のため [English Hosted UI](/plugins/hosted-ui) を参照してください。legacy static UI は `register_static_ui(...)`、list-row action は `set_list_actions(...)`、`register_list_action(...)`、`clear_list_actions()`、`get_list_actions()` で管理します。

#### LLM tool method

`register_llm_tool(...)`、`unregister_llm_tool(name)`、`list_llm_tools()` は `@llm_tool` の imperative API です。conversation-time tool を登録し、user-plugin Agent entry とは別です。[LLM Tool Calling](./tool-calling) を参照してください。

#### `run_update(**kwargs) -> object` (async)

長時間実行中の操作中にホストに更新を送信します。

#### `export_push(**kwargs) -> object` (async)

エクスポートデータをホストにプッシュします。

#### `finish(**kwargs) -> Any` (async)

タスク完了をホストに通知します。

### 返信制御

`finish()` は `delivery` パラメータ（`"proactive"`（既定）/ `"passive"` / `"silent"`）で、結果をメイン AI のコンテキストに入れるか、キャラクターにすぐ話させるかを制御します。`reply=True/False` は非推奨のブール別名です。`delivery` が効くのは、Agent がエントリを直接呼んだ場合です。task agent がエントリを呼んだ場合、結果は task agent に返り、ユーザーへの伝え方はその用件自体の設定に従います。

```python
# 通常：キャラクターが結果を報告する
return await self.finish(data={"summary": "完了"})

# サイレント：結果は記録されるがキャラクターは話さない
return await self.finish(data={"summary": "完了"}, delivery="silent")
```

3 つのモードの違い、`result_kind` による結果セマンティクス、`{MASTER_NAME}` / `{LANLAN_NAME}` ロールプレースホルダーは[メッセージ配信](./push-message)を参照してください。

### LLM 結果フィールドフィルタリング

`@plugin_entry`（静的エントリ）または `register_dynamic_entry()`（動的エントリ）の `llm_result_fields` パラメータを使用して、モデルが参照できる結果フィールドを制御します。直接呼び出しではメイン LLM、task agent からの呼び出しではその task agent が対象です。リストにないフィールドはプロンプトから除外されますが、Run の結果には残ります。エントリがフィールドを宣言しない場合にモデルへ何が渡るかは、[エントリーとパラメーター](./entries)の「Agent が entry を呼ぶとき」を参照してください。

```python
# 静的エントリ
@plugin_entry(llm_result_fields=["summary"])
async def search(self, query: str):
    return await self.finish(data={"summary": "3件の結果", "raw_results": [...]})

# 動的エントリ
self.register_dynamic_entry(
    entry_id="my-tool",
    handler=handler,
    llm_result_fields=["summary"],
)
```

### 出力契約

`@plugin_entry` または `register_dynamic_entry()` の `output_schema` で戻り値の JSON Schema を宣言します。入力と対称です。静的エントリーで戻り値の型注釈が Pydantic モデルなら自動で推論され、返したデータは実行時にモデルで検証されます（`params` と同じ）。明示した `output_schema` の dict（動的エントリーはすべてこれ）は公開されるだけで、検証されません（`input_schema` と同じ）。デフォルトでは合わない場合にプラグインログへ警告するだけで（頻度制限あり）、`strict_output=True` なら呼び出しは `PLUGIN_OUTPUT_SCHEMA_VIOLATION` で失敗します。ログとエラーに含まれるのはフィールドのパスとエラーの種類だけです。詳しくは[エントリーとパラメーター](./entries)の「出力契約」を参照してください。

```python
self.register_dynamic_entry(
    entry_id="my-tool-described",
    handler=handler,
    output_schema={"type": "object", "required": ["summary"]},  # 公開のみ、検証しない
)
```

---

## Result 型: Ok / Err

SDK は例外の代わりに、Rust にインスパイアされた Result 型をエラーハンドリングに使用します。

```python
from plugin.sdk.plugin import Ok, Err, unwrap, unwrap_or

# 成功を返す
return Ok({"data": result})

# エラーを返す
return Err(SdkError("something went wrong"))

# 結果を消費する
result = await self.plugins.call_entry("other:do_stuff")
if isinstance(result, Ok):
    data = result.value
else:
    error = result.error
    self.logger.error(f"Call failed: {error}")

# ヘルパー関数
value = unwrap(result)           # Err の場合は例外を発生
value = unwrap_or(result, None)  # Err の場合はデフォルト値を返す
```

---

## Plugins（プラグイン間呼び出し）

`self.plugins` 経由でアクセスします。

```python
# すべてのプラグインを一覧表示
result = await self.plugins.list()

# 有効なプラグインのみを一覧表示
result = await self.plugins.list(enabled=True)

# プラグイン ID を取得
result = await self.plugins.list_ids()

# プラグインが存在するか確認
result = await self.plugins.exists("other_plugin")

# 他のプラグインのエントリーポイントを呼び出す
result = await self.plugins.call_entry("other_plugin:do_work", {"key": "value"})

# JSON オブジェクトレスポンスを保証して呼び出す
result = await self.plugins.call_entry_json("other_plugin:get_data")

# プラグインが存在し有効であることを要求する
result = await self.plugins.require_enabled("dependency_plugin")
```

すべてのメソッドは `Result` 型を返します — `.value` を使用する前に `isinstance(result, Ok)` で確認してください。

### 呼び出し元：`ctx.call_chain`

`self.ctx.call_chain` は、現在の呼び出しがどこから来たかを示す読み取り専用のビューです。ホストが自身の記録から作るもので、プラグインは読むだけです。書き換えてもホストには影響しません。

```python
chain = self.ctx.call_chain
chain.callers        # ここまで呼び出してきたプラグイン ID（根が先頭）。ホストが直接呼んだ場合は ()
chain.origin.kind    # "run"、"chat_tool"、"chat_hook"、"ui_action"、"plugin_self"、"system" のいずれか
chain.origin.ref     # run ID などの識別子。メッセージ本文は含みません
chain.hops           # 各ステップの (plugin_id, event_type, event_id)
chain.rights         # チェーン上のすべてのプラグインがユーザーに承認されている能力
chain.rights_known   # ホストが示さなかった場合は False（後述）
chain.allows("memory.read")
```

- `None` になることはありません。呼び出しの外（タイマー、エントリが戻った後も動いているバックグラウンドタスク）や、ホスト側でポリシーがオフのときは、自分のプラグインだけを含むチェーンになり、`rights_known=False` です。
- `allows()` は参考情報です。権限が不明なときは常に `False` を返すため、「持っていない」と「わからない」はどちらも `False` になります。区別するには `rights_known` を見てください。各利用の可否はホストが判定します。ここで `False` でもホストが拒否するとは限らず、`True` も呼び出しが配送された時点のスナップショットです。
- `loop.run_in_executor` や素の `threading.Thread` に渡した処理は呼び出しコンテキストを失い、ホストからはプラグインが自発的に行ったリクエストとして扱われます。`asyncio.to_thread` は保持します。独自のスレッドでは `contextvars.copy_context().run(...)` で処理を包んでください。
- 旧来の `plugin.sdk.shared.runtime.call_chain`（1 つのプラグインプロセス内の呼び出しだけを追跡）とは別物です。

ホストはプラグイン間呼び出しと能力の利用を監査用に記録します（識別子と能力名のみで、引数・戻り値・メッセージ本文は記録しません）。設計：[`docs/design/plugin-call-chain.md`](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/docs/design/plugin-call-chain.md)。

---

## PluginStore（永続ストレージ）

`self.store` 経由でアクセスします（ホストがプラグイン構築時に事前生成して注入するため、自分でインスタンス化する必要はありません）。`plugin.toml` の `[plugin.store]` で `enabled = true` にしない限り無効で、無効の間は `set()` は何も保存せず、`get()` はデフォルト値を返します。

`PluginStore` のすべてのメソッドは `Result` を返すため、`unwrap_or(...)` で展開してください。

```python
unwrap_or(await self.store.set("key", {"count": 42}), None)
value = unwrap_or(await self.store.get("key"), None)  # → {"count": 42}
```

---

## SystemInfo

`self.system_info` 経由でアクセスします。これらのメソッドはいずれも `Result` を返すため、`unwrap_or(...)` で展開してください。

```python
config = unwrap_or(await self.system_info.get_system_config(), {})
settings = unwrap_or(await self.system_info.get_server_settings(), {})
python_env = unwrap_or(await self.system_info.get_python_env(), {})
```

---

## PluginContext (ctx)

`ctx` オブジェクトは構築時にホストにより注入されます。

| プロパティ | 型 | 説明 |
|----------|------|------|
| `ctx.plugin_id` | `str` | プラグイン識別子 |
| `ctx.config_path` | `Path` | `plugin.toml` へのパス |
| `ctx.logger` | `Logger` | ロガーインスタンス |
| `ctx.bus` | `SdkBusContext` | host state の read/watch facade |
| `ctx.metadata` | `dict` | 現在、プラグインプロセスでは常に `{}`。設定は `await self.config.dump()` で読みます |

### Bus と Memory

async entry 内では、local list 操作より先に `get()` を await します。

```python
events = await self.bus.events.get(plugin_id=self.plugin_id, max_count=50)
recent = events.filter(priority_min=1).sort(by="timestamp", reverse=True).limit(20)

records = await self.bus.memory.get(bucket_id="default", limit=20)
```

list surface は `filter` / `where`、`sort`、`limit`、`watch` です。callable の `filter(predicate)`、`where(predicate)`、`sort(key=...)` は local-only です。replayable な watcher chain では structured `filter(field=value, ...)` と `sort(by=...)` を使います。`watch()` を使えるのは `messages`、`events`、`lifecycle` だけで、`conversations`、`memory`、`frames` は read-only snapshot です。watcher subscription は `add`、`del`、`change` のみ受け付けます。

【アクセス範囲】これらの store は共有で、読み取りは plugin 単位で制限されません：有効化されたどの plugin も `conversations` と `frames` を読めます。user 由来のものも、他の plugin 由来の turn や画像も含みます。host が既に model へ送った内容が広がるわけではありません（session が送らなかった frame はここにも現れません）が、**誰が見られるか**は広がります——model provider から、user が有効にしたすべての plugin へ。plugin を install することはその可視性を与えることだと考えてください。これらを読む plugin は、その旨を自分の説明に書いてください。

`bus.memory` に入るのは、件数制限付きでメモリ上に保持される最近のユーザー発話イベント（TTL は 1 時間）です。キャラクターの永続的な facts、reflections、persona とは別物です。`ctx.query_memory(...)` は組み込みの `recall_memory` ツールと同じハイブリッド recall をキャラクターの private メモリに対して実行し、`{"result": text}` を返します。`text` はキャラクターのプロンプト言語でレンダリングされた、番号付き・token 予算付きのヒット一覧で、関連するものがなければ空文字列です。

### Frames

`bus.frames` には、host が model provider にすでに送った直近数枚の frame が入ります。provider が実際に受け取ったバイト列そのものです。

```python
frames = await self.bus.frames.get(max_count=4)
latest = frames.sort(by="timestamp", reverse=True).limit(1)
```

プラグインから撮影を要求することはできません。session 側の throttle や delivery-mode fence で落ちた frame は provider に送られていないので、ここにも現れません。

**これは log でも queue でもありません。** frame は 4 箇所で意図的に捨てられます。message plane の PUB socket は slow joiner と high-water mark で落としますし、host 側の publish は non-blocking、送信 queue は上限付きで遅れ始めた時点で frame を拒否し、store 自体も数枚しか保持しません（`NEKO_MESSAGE_PLANE_FRAMES_STORE_MAXLEN`、2-8 に丸められ、既定は 4）。「すべての frame」を polling したり、一度読めた frame がまだ残っていると考えたりすると間違います。

各 record は `image_base64`（1 部のみ。読める raw bytes の複製はありません）、`mime`、`source`、`captured_at`、`turn_id`、`generation`、`frame_id`、`metadata` を持ちます。`source` は host がその frame に割り当てた channel です：`screen`、`camera`、`plugin`、`callback`、`proactive`、`user`、そして turn が組み換えられ host が判断できなくなった場合の `unknown` です。`screen` と `camera` がこの 2 つの live channel を区別するのは音声経路だけです。text mode では user が共有するすべての frame——共有画面、camera の静止画、drag & drop した写真——が区別を保たない 1 本の queue を通るため、`user` として報告されます。両方の mode で「user が character に見せたもの」が欲しい場合は `user` で絞り込んでください。重複排除は `frame_id` で行ってください。`generation` は常時取得される frame の順序を表すだけで、単発の cue 画像では進まないため、2 つの record が同じ値を持ち得ます。

tool が返した画像も、それを載せた後続 request に応答があった時点でこの bus に載ります。その frame は `source="plugin"`、`metadata` は `{"tool_name": "..."}` です。tool は base64 で最大 2 MiB まで返せます。配信予算（500 KiB）を超えたものは、model 向け画像と同じ profile（JPEG、最大 1280x720）で host が再エンコードするので、message plane の record 上限に収まり、model にも この bus にも届きます。その場合 record の `mime` は tool が PNG を返していても `image/jpeg` になり、tool result には再エンコードされた旨の model から見える注記が付きます。再エンコードしても収まらない画像は、専用の警告とともに落とされます。pixel より先に `source` を読んでください。`plugin` の frame は何らかの plugin がモデルに渡した media であり（自分のものとは限りません）、ユーザーがキャラクターに共有した画面ではありません。

### 優先度レベル

| 範囲 | レベル | 用途 |
|------|--------|------|
| 0-2 | 低 | 情報メッセージ |
| 3-5 | 中 | 一般的な通知 |
| 6-8 | 高 | 重要な通知 |
| 9-10 | 緊急 | 即座の対応が必要 |
