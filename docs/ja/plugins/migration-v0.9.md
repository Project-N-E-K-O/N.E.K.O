# Plugin SDK v0.9 移行ガイド

このページは、プラグインシステムの公開面を縮小した後の移行チェックリストです。一部の API はすでに削除済みで、残る `push_message` v1 互換引数は v0.9 で削除されます。

## 概要

| 以前の API | 状態 | 移行先 |
|---|---|---|
| `type = "script"` / script plugin | 削除済み、互換レイヤーなし | 通常の `plugin` パッケージと `NekoPluginBase` |
| `plugin._types.result` | 削除済み | `plugin.sdk.plugin` から `Result`、`Ok`、`Err`、`SdkError` などを import |
| Bus の `where_*` とリスト集合演算 | 削除済み | `get()`、`filter()` / `where()`、`sort()`、`limit()`、`watch()` を合成 |
| `get_message_plane_all` | 削除済み | bounded な `await self.bus.messages.get(...)` を使用 |
| Bus の incremental/local fast path | 削除済み | 標準の bounded/replayable read/watch パイプライン |
| 高レベル `self.memory` / SDK `MemoryClient` | 削除済み | `self.bus.memory.get(...)` または `await self.ctx.query_memory(...)` |
| Extension package type、`[plugin.host]`、`plugin.sdk.extension` | 削除済み、互換レイヤーなし | Router を所有する通常 Plugin に統合するか、独立した Plugin に変換 |
| `push_message` v1 フィールド | 非推奨、v0.9 で削除 | `parts`、`visibility`、`ai_behavior` |

## パッケージ種別

script plugin には互換 shim がありません。manifest とエントリークラスを標準 Plugin に変更します。

```toml
[plugin]
type = "plugin"
```

```python
from plugin.sdk.plugin import NekoPluginBase, neko_plugin, plugin_entry, Ok

@neko_plugin
class MyPlugin(NekoPluginBase):
    @plugin_entry(id="run")
    async def run(self, **_):
        return Ok({"status": "done"})
```

Extension に互換 shim はありません。`type = "extension"` と `[plugin.host]` を削除し、Router module を旧 host に移して `self.include_router(router)` を呼ぶか、通常の `NekoPluginBase` package に変換します。`plugin.sdk.extension` import は `plugin.sdk.plugin` の対応する public symbol に置き換えてください。`PluginRouter` は通常 Plugin 内部のコード整理にだけ残ります。

## Result の import

公開 Result スタックは 1 つだけです。

```python
# 以前
from plugin._types.result import Result, Ok, Err

# 移行後
from plugin.sdk.plugin import Result, Ok, Err, SdkError
```

削除されたモジュールの互換 alias を追加しないでください。

## Bus クエリと watcher

Bus は host state の read/watch facade であり、publish 可能な汎用イベントバスではありません。名前空間を 1 つ選び、結果件数を制限します。

```python
events = await self.bus.events.get(plugin_id=self.plugin_id)
events = (
    events
    .filter(priority_min=1)
    .filter(type="TASK_FINISHED")
    .sort(by="timestamp", reverse=True)
    .limit(20)
)

watcher = events.watch(self.ctx)

@watcher.subscribe(on="add")  # "add"、"del"、"change" のみ
def on_added(delta):
    for event in delta.added:
        self.logger.info(f"event: {event.type}")

watcher.start()
```

callable の `filter(predicate)`、`where(predicate)`、`sort(key=callable)` は local snapshot 処理には使えますが replay できません。`watch()` より前では使わず、watcher chain には structured `filter(field=value, ...)` と `sort(by=...)` を使います。`watch()` を使えるのは `messages`、`events`、`lifecycle` だけで、`conversations` と `memory` は read-only snapshot です。

削除済み helper は `where_in`、`where_eq`、`where_contains`、`where_regex`、`where_gt`、`where_ge`、`where_lt`、`where_le` と BusList の共通部分/差分演算です。条件は `filter(...)` または `where(predicate)` に書き換え、2 つの snapshot を結合する必要がある場合は record key を使って通常の Python で明示的に処理します。

`get_message_plane_all` は Message Plane の `messages` store を page 単位で読み、`max_items` 上限を持つ API でした。incremental な `after_seq` transport path が削除されたため、1 対 1 の代替 API はありません。bounded な `await self.bus.messages.get(max_count=..., ...)` に移行し、必要に応じて structured filter、`sort(by=...)`、`limit()` を使います。

削除された Bus fast path は、BusList `fast_mode`、incremental reload cursor、local message cache、revision/delta shortcut などの高速化分岐です。`watch()` に必要な replay plan と trace は残っており、`get()` / structured `filter(field=value)` / `sort(by=...)` / `limit()` が replayable chain を構成します。これらは旧 `push_message(fast_mode=...)` とは別物です。後者は v1 互換面に属し、v0.9 で削除予定です。v2 は標準の per-message host delivery path を使うため、旧 batching/backpressure 最適化を外す際は high-volume producer を再 benchmark してください。

## Memory

旧 SDK `MemoryClient` は record read と semantic query を混在させていました。操作を明示的に選択してください。

```python
# 1 bucket の最近の record
records = await self.bus.memory.get(bucket_id="default", limit=20)

# semantic lookup
matches = await self.ctx.query_memory("default", "ユーザーの好みは？")
```

`self.bus.memory` による memory record の読み取りと型付き record は残ります。削除されたのは高レベルの `self.memory` property と SDK/runtime の `MemoryClient` facade です。

## `push_message` v2

新規コードでは canonical schema だけを使います。

```python
self.push_message(
    source="my_plugin",
    visibility=["chat"],
    ai_behavior="blind",
    parts=[{"type": "text", "text": "タスクが完了しました"}],
)
```

同じ変更内で移行できない call に対して、稼働中の plugin source へ marker だけを一括挿入するのは避けてください。plugin maintainer の変更と競合しやすいため、まず issue または PR で warning を追跡します。自分が管理する plugin source では、次のような局所コメントを使用できます。

```python
# TODO(plugin-api-v0.9): v0.9 までに push_message v1 field を置換する。追跡: <issue-or-PR>。
```

v1 の `message_type`、`description`、`content`、`binary_data`、`binary_url`、`mime`、`delivery`、`reply`、`unsafe`、`fast_mode` は互換専用です。静的 check と runtime warning が対象呼び出しを示します。v0.9 より前に移行してください。完全な対応表は [`push_message` v2 の説明](/changelog/plugin-push-message-v2)を参照してください。

## 検証

```bash
uv run neko-plugin check <plugin_id-or-path> --strict
```

旧 `push_message` warning は抑制せず、すべて移行対象として扱ってください。
