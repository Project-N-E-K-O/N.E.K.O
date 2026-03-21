# 応用トピック

## Extension

Extension は、既存のプラグインを変更せずにルートやフックを追加します。ホストプラグインのプロセス内で実行されます（別プロセスではありません）。

### Extension を使うべき場合

- 既存プラグインに新しいコマンドを追加したい
- 他のプラグインのエントリーポイントにフックしたい
- プラグイン内でモジュール化されたコード構成にしたい

### Extension の作成

```python
from plugin.sdk.extension import (
    NekoExtensionBase, extension, extension_entry, extension_hook,
    Ok, Err,
)

@extension
class MyExtension(NekoExtensionBase):
    """ホストプラグインに追加コマンドを提供します。"""

    @extension_entry(id="extra_command", description="An extra command added by extension")
    def extra_command(self, param: str = "", **_):
        return Ok({"extended": True, "param": param})

    @extension_hook(target="original_entry", timing="before")
    def validate(self, *, args, **_):
        # ホストプラグインの "original_entry" の前に実行
        if not args.get("required_field"):
            return Err("Missing required_field")
```

### Extension の仕組み

1. ホストが設定で Extension を登録する
2. 起動時に、ホストが Extension を `PluginRouter` インスタンスとしてインジェクトする
3. Extension のエントリーはホストプラグインの名前空間でアクセス可能になる
4. Extension のフックがホストのエントリーポイントをインターセプトする

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

### イベントバス

```python
# バス経由でイベントを発行
self.bus.emit("my_event", {"key": "value"})

# イベントをサブスクライブ（通常は startup で行う）
self.bus.on("some_event", self._handle_event)
```

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
