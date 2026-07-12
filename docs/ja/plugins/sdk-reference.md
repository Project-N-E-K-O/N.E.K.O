# SDK リファレンス

すべてのプラグイン開発 API は `plugin.sdk.plugin` からインポートします。

```python
from plugin.sdk.plugin import (
    # ベース
    NekoPluginBase, PluginMeta,
    # デコレーター
    neko_plugin, plugin_entry, lifecycle, timer_interval, message, on_event,
    custom_event, hook, before_entry, after_entry, around_entry, replace_entry,
    # Result 型
    Ok, Err, Result, unwrap, unwrap_or,
    # ランタイムヘルパー
    Plugins, PluginRouter, PluginConfig, PluginStore,
    SystemInfo,
    # エラー
    SdkError, TransportError,
    # ロギング
    get_plugin_logger,
)
```

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
| `self.config_dir` | `Path` | `plugin.toml` を含むディレクトリ |
| `self.metadata` | `dict` | `plugin.toml` からのプラグインメタデータ |
| `self.bus` | `SdkBusContext` | host state の read/watch facade。publish/emit API はありません |
| `self.plugins` | `Plugins` | プラグイン間呼び出しヘルパー |
| `self.system_info` | `SystemInfo` | ホストシステムのメタデータ |

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

#### `push_message(**kwargs) -> object`

v2 schema でホストシステムにメッセージをプッシュします。

```python
self.push_message(
    source="my_feature",
    visibility=["chat"],       # []、["chat"]、["hud"]、または両方
    ai_behavior="blind",       # "respond"、"read"、"blind"
    parts=[{"type": "text", "text": "タスクが完了しました"}],
    priority=5,
)
```

v1 field（`message_type`、`content`、`delivery`、`reply` および他の legacy alias）は非推奨で、v0.9 で削除されます。[移行ガイド](./migration-v0.9#push-message-v2)を参照してください。

#### `data_path(*parts) -> Path`

プラグインの `data/` ディレクトリ配下のパスを取得します。

```python
db_path = self.data_path("cache.db")  # → <plugin_dir>/data/cache.db
```

#### `register_dynamic_entry(entry_id, handler, ...) -> bool`

実行時にエントリーポイントを登録します（デコレーター経由ではなく）。

```python
self.register_dynamic_entry(
    entry_id="dynamic_greet",
    handler=lambda name="World", **_: Ok({"msg": f"Hi {name}"}),
    name="Dynamic Greet",
    description="動的に登録された挨拶",
)
```

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

#### `run_update(**kwargs) -> object` (async)

長時間実行中の操作中にホストに更新を送信します。

#### `export_push(**kwargs) -> object` (async)

エクスポートデータをホストにプッシュします。

#### `finish(**kwargs) -> Any` (async)

タスク完了をホストに通知します。

### 返信制御

`finish()` メソッドは `reply` パラメータ（デフォルト `True`）を受け付け、プラグインの結果がメインキャラクターの発話をトリガーするかどうかを制御します。

```python
# 通常：キャラクターが結果を報告する
return await self.finish(data={"summary": "完了"}, reply=True)

# サイレント：結果は記録されるがキャラクターは話さない
return await self.finish(data={"summary": "完了"}, reply=False)
```

### LLM 結果フィールドフィルタリング

`@plugin_entry`（静的エントリ）または `register_dynamic_entry()`（動的エントリ）の `llm_result_fields` パラメータを使用して、メイン LLM が参照できる結果フィールドを制御します。リストにないフィールドは LLM プロンプトから除外されますが、タスクレジストリには保存されます。

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

---

## PluginStore（永続ストレージ）

`self.store` 経由でアクセスします（ホストがプラグイン構築時に事前生成して注入するため、自分でインスタンス化する必要はありません）。

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
| `ctx.metadata` | `dict` | プラグインメタデータ |

### Bus と Memory

async entry 内では、local list 操作より先に `get()` を await します。

```python
events = await self.bus.events.get(plugin_id=self.plugin_id)
recent = events.filter(priority_min=1).sort(by="timestamp", reverse=True).limit(20)

records = await self.bus.memory.get(bucket_id="default", limit=20)
matches = await self.ctx.query_memory("default", "ユーザーの好み")
```

list surface は `filter` / `where`、`sort`、`limit`、`watch` です。callable の `filter(predicate)`、`where(predicate)`、`sort(key=...)` は local-only です。replayable な watcher chain では structured `filter(field=value, ...)` と `sort(by=...)` を使います。`watch()` を使えるのは `messages`、`events`、`lifecycle` だけで、`conversations` と `memory` は read-only snapshot です。watcher subscription は `add`、`del`、`change` のみ受け付けます。

### 優先度レベル

| 範囲 | レベル | 用途 |
|------|--------|------|
| 0-2 | 低 | 情報メッセージ |
| 3-5 | 中 | 一般的な通知 |
| 6-8 | 高 | 重要な通知 |
| 9-10 | 緊急 | 即座の対応が必要 |
