# 応用トピック

## Router の構成

Extension package type と `plugin.sdk.extension` facade は削除されました。`PluginRouter` は通常 Plugin 内部の構成要素として引き続き利用できます。Router は所有する Plugin と同じ source tree に置き、明示的に mount します。

```python
from plugin.sdk.plugin import PluginRouter, plugin_entry, Ok


class ExtraRouter(PluginRouter):
    @plugin_entry(id="extra_command", description="追加コマンド")
    async def extra_command(self, param: str = "", **_):
        return Ok({"param": param})
```

所有する `NekoPluginBase` の constructor で `self.include_router(ExtraRouter(name="extra"))` を呼びます。旧 Extension はその Plugin の source tree に統合するか、独立した通常 Plugin に変換してください。`type = "extension"`、`[plugin.host]`、`plugin.sdk.extension` import は拒否されます。[v0.9 移行ガイド](./migration-v0.9)を参照してください。

---

## Adapter

Adapter は外部プロトコル（MCP、NoneBot など）を内部プラグイン呼び出しにブリッジします。**ゲートウェイパイプライン**パターンを実装します。

### Adapter を使うべき場合

- N.E.K.O. プラグインを MCP（Model Context Protocol）経由で公開したい
- NoneBot メッセージを受け付けてプラグインにルーティングしたい
- 外部プロトコルをプラグインシステムにブリッジしたい

### Adapter ゲートウェイパイプライン

```
External Request → Normalizer → PolicyEngine → RouteEngine → PluginInvoker → ResponseSerializer → External Response
```

| ステージ | 責務 |
|---------|------|
| **Normalizer** | 外部プロトコル形式を `GatewayRequest` に変換 |
| **PolicyEngine** | アクセス制御、レート制限、バリデーション |
| **RouteEngine** | 呼び出すプラグイン/エントリーを決定 |
| **PluginInvoker** | 実際のプラグイン呼び出しを実行 |
| **ResponseSerializer** | 結果を外部プロトコル形式に変換 |

### Adapter の作成

```python
from plugin.sdk.plugin import neko_plugin, plugin_entry, lifecycle, Ok, Err, SdkError
from plugin.sdk.adapter import (
    AdapterGatewayCore, DefaultPolicyEngine, NekoAdapterPlugin,
)
from plugin.sdk.adapter.gateway_models import ExternalRequest

@neko_plugin
class MyProtocolAdapter(NekoAdapterPlugin):
    def __init__(self, ctx):
        super().__init__(ctx)
        self.gateway = None

    @lifecycle(id="startup")
    async def startup(self, **_):
        self.gateway = AdapterGatewayCore(
            normalizer=MyNormalizer(),
            policy_engine=DefaultPolicyEngine(),
            route_engine=MyRouteEngine(),
            invoker=MyInvoker(self.ctx),
            serializer=MySerializer(),
            logger=self.logger,
        )
        return Ok({"status": "ready"})

    @plugin_entry(id="handle_request")
    async def handle_request(self, raw_data: dict, **_):
        external = ExternalRequest(protocol="my_protocol", raw=raw_data)
        response = await self.gateway.process(external)
        return Ok(response.to_dict())
```

### Adapter モード

| モード | 説明 |
|--------|------|
| `GATEWAY` | 完全なパイプライン処理 |
| `ROUTER` | ルーティングのみ（ポリシーをスキップ） |
| `BRIDGE` | 直接パススルー |
| `HYBRID` | リクエストごとにモードを選択 |

### 組み込みリファレンス: MCP Adapter

`plugin/plugins/mcp_adapter/` に、MCP プロトコルを N.E.K.O. プラグインにブリッジする完全な Adapter 実装があります。以下を実演しています：
- カスタム Normalizer（`MCPRequestNormalizer`）
- カスタム RouteEngine（`MCPRouteEngine`）
- カスタム Invoker（`MCPPluginInvoker`）
- カスタム Serializer（`MCPResponseSerializer`）
- カスタム Transport（`MCPTransportAdapter`）

---

## プラグイン間通信

### 直接エントリー呼び出し

```python
# 他のプラグインのエントリーポイントを呼び出す
result = await self.plugins.call_entry("target_plugin:entry_id", {"arg": "value"})

if isinstance(result, Ok):
    data = result.value
else:
    self.logger.error(f"Call failed: {result.error}")
```

### ディスカバリ

```python
# 利用可能なすべてのプラグインを一覧表示
plugins = await self.plugins.list(enabled=True)

# 依存関係が存在するか確認
exists = await self.plugins.exists("required_plugin")

# プラグインを要求する（見つからない場合は即座に失敗）
dep = await self.plugins.require_enabled("required_plugin")
```

### Bus read と watcher

`self.bus` は 5 つの readable namespace snapshot `messages`、`events`、`lifecycle`、`conversations`、`memory` を公開し、`emit()` や `on()` はありません。`watch()` を使えるのは `messages`、`events`、`lifecycle` だけで、`conversations` と `memory` は read-only snapshot です。

```python
# async entry では get() を await する
events = await self.bus.events.get(plugin_id=self.plugin_id, max_count=50)
recent = events.filter(priority_min=1).sort(by="timestamp", reverse=True).limit(20)

# subscribe() は "add"、"del"、"change" のみ受け付ける
watcher = recent.watch(self.ctx)

@watcher.subscribe(on="add")
def _handle_event(delta):
    for event in delta.added:
        self.logger.info(f"new event: {event.type}")

watcher.start()
```

callable の `filter(predicate)`、`where(predicate)`、`sort(key=callable)` は materialize 済み snapshot に対する local-only 変換で、`watch()` では replay できません。watcher chain では上例のように structured `filter(field=value, ...)` と `sort(by=...)` を使います。

最近の memory record は `await self.bus.memory.get(bucket_id="default", limit=...)`、semantic lookup は `await self.ctx.query_memory("default", query)` を使います。旧高レベル `self.memory` / `MemoryClient` API は削除済みです。

---

## 非同期プログラミング

エントリーポイントは同期でも非同期でも定義できます：

```python
# 同期エントリー（スレッドプールで実行）
@plugin_entry(id="sync_task")
def sync_task(self, **_):
    return Ok({"result": "done"})

# 非同期エントリー（イベントループで実行）
@plugin_entry(id="async_task")
async def async_task(self, url: str, **_):
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            return Ok({"data": await response.json()})
```

---

## スレッドセーフティ

タイマータスクは別スレッドで実行されます。共有状態を保護してください：

```python
import threading

@neko_plugin
class ThreadSafePlugin(NekoPluginBase):
    def __init__(self, ctx):
        super().__init__(ctx)
        self._lock = threading.Lock()
        self._counter = 0

    @plugin_entry(id="increment")
    def increment(self, **_):
        with self._lock:
            self._counter += 1
            return Ok({"count": self._counter})

    @timer_interval(id="report", seconds=60, auto_start=True)
    def report(self, **_):
        with self._lock:
            count = self._counter
        self.report_status({"count": count})
```

---

## カスタム設定

```python
import json

class ConfigurablePlugin(NekoPluginBase):
    def __init__(self, ctx):
        super().__init__(ctx)
        config_file = self.config_dir / "config.json"
        if config_file.exists():
            self.config = json.loads(config_file.read_text())
        else:
            self.config = {"timeout": 30}
```

または、プロファイル付きの構造化された設定には `PluginConfig` を使用します：

```python
from plugin.sdk.plugin import PluginConfig

config = PluginConfig(self.ctx)
timeout = config.get("timeout", default=30)
```

---

## SQLite によるデータ永続化

```python
import sqlite3

class PersistentPlugin(NekoPluginBase):
    def __init__(self, ctx):
        super().__init__(ctx)
        self.db_path = self.data_path("records.db")
        self.data_path().mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT UNIQUE,
                value TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        conn.close()
```
