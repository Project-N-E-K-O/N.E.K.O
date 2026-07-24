# プラグインベースクラス

次のように書くと、

```python
@neko_plugin
class MyPlugin(NekoPluginBase):
    ...
```

プラグインは一連の機能を自動的に得ます。自分で実装する必要はありません。`NekoPluginBase` が用意しています。

このページでは、実際によく使う順にそれぞれの機能を紹介します。

---

## self.logger — ログ出力

**必要になる場面**: ほぼ常に。デバッグ、状態追跡、問題調査に使います。

`self.logger` は `super().__init__(ctx)` の直後から使えます。設定は不要です。ログは Plugin Manager のログビューアーに自動的に表示されるため、プラグインの動きをリアルタイムで確認できます。

```python
self.logger.info("Starting to process user request")
self.logger.debug("Received params: query={}", query)
self.logger.warning("API response slow, took {}s", elapsed)
self.logger.error("Request failed: {}", error_msg)
```

フォーマットは `{}` プレースホルダーに対応しています。Python の `str.format` と同じ感覚で使えます。f-string は不要です。

**ログをファイルにも書きたい場合** は、デフォルトではログはパネルにだけ表示されます。再起動後も履歴を確認したい場合は、`__init__` でファイルログを有効にします。

```python
def __init__(self, ctx):
    super().__init__(ctx)
    self.logger = self.enable_file_logging(log_level="INFO")
```

ファイルは `plugin/log/` に書き込まれ、同時にパネルにも表示されます。

---

## self.config — 設定の読み書き

**必要になる場面**: API URL、タイムアウト、機能フラグなど、調整可能な設定を持つプラグイン。

`plugin.toml` で定義したカスタム設定セクションは `self.config` からアクセスできます。たとえば toml に次の設定がある場合:

```toml
[my_settings]
api_url = "https://api.example.com"
timeout = 30
enabled = true
```

コードでは次のように読み取ります。

```python
# 方法 1: 設定全体を取得し、必要な部分を取り出す
cfg = await self.config.dump()
settings = cfg.get("my_settings", {})
api_url = settings.get("api_url", "https://default.com")

# 方法 2: パスで直接取得する（より簡潔）
api_url = await self.config.get("my_settings.api_url", default="https://default.com")
timeout = await self.config.get_int("my_settings.timeout", default=30)
enabled = await self.config.get_bool("my_settings.enabled", default=True)
```

**実行時に設定更新をリクエストする** こともできます。たとえばエントリがユーザーの選択を保存する場合です。

```python
await self.ctx.update_own_config({"my_settings": {"timeout": 60}})
```

これは現在サポートされている host の更新経路を使います。変更は保存され、`self.config` も更新されますが、プラグインプロセス内の `config_change` ライフサイクルフックは呼ばれません。新しい値がキャッシュ済みの派生状態に影響する場合は、`update_own_config()` が戻ったあとでその状態を明示的に再読み込みしてください。

---

## self.plugins — 他のプラグインを呼び出す

**必要になる場面**: ほかのプラグインの機能が必要なとき。たとえば "daily summary" プラグインが "web search" を呼び出してニュースを取得する場合です。

```python
# web_search プラグインの search エントリーをパラメーター付きで呼び出す
result = await self.plugins.call_entry("web_search:search", {"query": "today's news"})

# result は Ok または Err。必ず確認する
from plugin.sdk.plugin import Ok, Err
if isinstance(result, Ok):
    news = result.value  # 成功。データを取得できた
else:
    self.logger.error("Search failed: {}", result.error)
```

よく使う操作:

```python
from plugin.sdk.plugin import unwrap

# プラグインが利用可能か確認
exists = unwrap(await self.plugins.exists("web_search"))

# 実行中のプラグイン一覧
running = unwrap(await self.plugins.list(enabled=True))
```

---

## self.store — キーバリューストレージ

**必要になる場面**: 再起動後も残るデータを保存したいとき。ユーザー設定、最後の検索、累積統計などです。

`plugin.toml` で有効にする必要があります。

```toml
[plugin.store]
enabled = true
```

コードでは次のように使います。

```python
from plugin.sdk.plugin import unwrap

# 保存（文字列、数値、dict、list に対応）
unwrap(await self.store.set("last_query", "what's the weather today"))
unwrap(await self.store.set("stats", {"total_calls": 42, "last_used": "2025-01-01"}))

# 読み込み（見つからない場合は None）
query = unwrap(await self.store.get("last_query"))
stats = unwrap(await self.store.get("stats"))

# 削除
deleted = unwrap(await self.store.delete("last_query"))
```

データはプラグインの `data/` ディレクトリに保存され、再起動後も残ります。

---

## self.db — SQLite データベース

**必要になる場面**: キーバリューだけでは足りない大量の構造化データを保存したいとき。ノート、チャットログ、タスクキューなどです。

`plugin.toml` で有効にする必要があります。

```toml
[plugin.database]
enabled = true
```

コードでは次のように使います。

```python
from plugin.sdk.plugin import unwrap

async with unwrap(await self.db.session()) as session:
    # テーブル作成
    await session.execute("""
        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            content TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 挿入
    await session.execute(
        "INSERT INTO notes (title, content) VALUES (?, ?)",
        ("Groceries", "Tomatoes, eggs, milk")
    )
    await session.commit()

    # クエリ
    cursor = await session.execute("SELECT * FROM notes ORDER BY created_at DESC")
    for row in cursor.fetchall():
        self.logger.info("Note: {} - {}", row["title"], row["content"])
```

データベースファイルはプラグインの `data/` ディレクトリに保存されます。

---

## self.i18n — 国際化

**必要になる場面**: 中国語と英語など、複数言語のユーザーに対応したいとき。

`plugin.toml` で設定が必要です。

```toml
[plugin.i18n]
default_locale = "zh-CN"
locales_dir = "i18n"
```

次に、たとえば `i18n/en.json` のようなロケールファイルを作ります。

```json
{
  "greeting": "Hello, {name}!",
  "error.not_found": "Cannot find {item}"
}
```

コードでは次のように使います。

```python
msg = self.i18n.t("greeting", name="Alice")  # → "Hello, Alice!"
err = self.i18n.t("error.not_found", item="note")  # → "Cannot find note"
```

システムはユーザーの言語設定に基づいて、適切なロケールファイルを自動的に選びます。

---

## self.data_path(...) — ファイル保存

**必要になる場面**: キャッシュ、ダウンロードしたリソース、一時ファイルなど、任意のファイルを保存したいとき。

```python
# パスを取得（ディレクトリは自動作成される）
cache_file = self.data_path("cache", "results.json")
# → <plugin_dir>/data/cache/results.json

# 書き込み
cache_file.parent.mkdir(parents=True, exist_ok=True)
cache_file.write_text('{"cached": true}')

# 読み込み
content = cache_file.read_text()
```

---

## self.bus — Bus read/watch

**必要になる場面**: namespace ごとの host-state snapshot を read/watch したいとき。この facade に publish/emit API はありません。

```python
# 最近のイベントを読む
recent_events = await self.bus.events.get(max_count=50)
recent_events = recent_events.filter(type="note_created").sort(
    by="timestamp", reverse=True,
).limit(20)

# 最近のメッセージを読む
recent_messages = await self.bus.messages.get(max_count=20)

# メモリ上の bucket から最近のユーザー発話イベントを読む
memory_records = await self.bus.memory.get(bucket_id="default", limit=20)
```

list surface は `filter` / `where`、`sort`、`limit`、`watch` です。callable の `filter(predicate)`、`where(predicate)`、`sort(key=...)` は local-only のため、watcher chain では structured `filter(field=value, ...)` と `sort(by=...)` を使います。`watch()` を使えるのは `messages`、`events`、`lifecycle` だけで、`conversations` と `memory` は read-only snapshot です。watcher は `add`、`del`、`change` のみ受け付けます。

`memory` bucket は件数制限付きで、プロセス内に 1 時間保持されます。これは plugin context であり、キャラクターの永続的な facts、reflections、persona へのアクセスではありません。`ctx.query_memory(...)` は非推奨の placeholder endpoint に対する互換呼び出しとしてのみ残されており、semantic recall は行いません。

---

## report_status(...) — ステータス push

**必要になる場面**: プラグインが長時間処理を行っていて、パネル上で進捗を見せたいとき。

```python
self.report_status({
    "status": "processing",
    "progress": 50,
    "message": "Processing item 5/10..."
})
```

ステータスは Plugin Manager のパネルにリアルタイム表示されます。

---

## push_message(...) — チャットへ送る

**必要になる場面**: プラグインからユーザーへ能動的に通知したいとき。リマインダー、通知、処理結果などです。メッセージは N.E.K.O のチャット画面に表示されます。

```python
self.push_message(
    source="smart_notes",
    visibility=["chat"],
    ai_behavior="blind",
    parts=[{"type": "text", "text": "Reminder: you have a pending task"}],
    priority=5,
)
```

---


## self.system_info — システム情報

**必要になる場面**: 現在の実行環境について知りたいとき。

```python
from plugin.sdk.plugin import unwrap_or

config = unwrap_or(await self.system_info.get_system_config(), {})
settings = unwrap_or(await self.system_info.get_server_settings(), {})
python_env = unwrap_or(await self.system_info.get_python_env(), {})
```

---

## まとめ

| 機能 | 目的 | 追加設定が必要か |
|------|------|------------------|
| `self.logger` | ログとデバッグ | 不要 |
| `self.config` | toml 設定の読み書き | 不要 |
| `self.plugins` | 他のプラグインを呼び出す | 不要 |
| `self.store` | キーバリュー永続化 | `[plugin.store] enabled = true` |
| `self.db` | SQLite データベース | `[plugin.database] enabled = true` |
| `self.i18n` | 多言語対応 | `[plugin.i18n]` |
| `self.data_path()` | ファイル保存 | 不要 |
| `self.bus` | バススナップショットを読み取り・監視 | 不要 |
| `report_status()` | パネルに進捗表示 | 不要 |
| `push_message()` | チャットへ送信 | 不要 |
| `self.system_info` | システム情報を取得 | 不要 |
