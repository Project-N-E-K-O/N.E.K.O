# ライフサイクル

プラグインは起動、停止、設定変更など、実行中にいくつかの段階を通ります。ライフサイクルフックを使うと、そのタイミングで独自の処理を実行できます。

すべてのライフサイクルフックは **任意** です。必要なければ書かなくてかまいません。

lifecycle hook は host-dispatched event であり、通常の runtime entry ではありません。現在の host は `@plugin_entry(auto_start=True)` を理由に通常 entry を process startup で実行しません。初期化には `@lifecycle(id="startup")` を使います。また `self.config.update_own_config(...)` は supported host path で config を更新・refresh しますが、同じ plugin process に `config_change` を dispatch しません。call 後に config 依存の derived state を更新してください。

## 起動時に初期化する

プラグインプロセスが開始されたあと、`startup` フックが呼ばれます。設定の読み込み、接続の作成、リソース準備に向いています。

```python
from plugin.sdk.plugin import NekoPluginBase, neko_plugin, lifecycle, Ok
import aiohttp

@neko_plugin
class MyPlugin(NekoPluginBase):

    @lifecycle(id="startup")
    async def on_startup(self):
        # 設定を読み込む
        cfg = await self.config.dump()
        self.api_url = cfg.get("my_settings", {}).get("api_url", "https://default.com")

        # 接続を開く
        self.session = aiohttp.ClientSession()

        self.logger.info("Plugin started, API: {}", self.api_url)
        return Ok({"status": "ready"})
```

## 停止時に後片付けする

プラグインが停止されたとき、または N.E.K.O が終了するときに呼ばれます。接続を閉じる、状態を保存する、といった処理に向いています。

```python
    @lifecycle(id="shutdown")
    async def on_shutdown(self):
        if self.session:
            await self.session.close()
        self.logger.info("Plugin stopped")
        return Ok({"status": "stopped"})
```

## ユーザーが Reload を押したとき

Plugin Manager の Reload は現在、プラグインを再起動します。つまり `shutdown` を実行してから、もう一度起動して `startup` を実行します。後片付けは `shutdown`、初期化や設定の再読み込みは `startup` に置いてください。

SDK デコレーターは互換性のため `reload` ライフサイクル ID を受け付けますが、Plugin Manager の Reload ボタンはこのフックを dispatch しません。

## 外部から設定が変更されたとき

UI または API 経由で設定が変更されたときに発火します。再起動なしで新しい設定を反映したい場合に便利です。

```python
    @lifecycle(id="config_change")
    async def on_config_change(self, old_config, new_config, mode):
        self.timeout = new_config.get("my_settings", {}).get("timeout", 30)
        self.logger.info("Config update mode: {}", mode)
        self.logger.info("Config updated, new timeout: {}s", self.timeout)
        return Ok({"status": "config_updated"})
```

## ライフサイクルイベントと Reload の挙動

| イベント/操作 | 発火するタイミング | 主な用途 |
|----|--------------------|----------|
| `startup` | プラグインプロセス開始時 | 初期化、設定読み込み、接続開始 |
| `shutdown` | プラグインプロセス停止時 | 接続終了、状態保存、リソース解放 |
| Plugin Manager Reload | ユーザーが Reload を押したとき | `shutdown` の後に `startup` を実行 |
| `config_change` | 設定が外部から変更されたとき | 新しい設定の反映 |
| `freeze` | プラグインが一時停止されたとき | タイマーの一時停止 |
| `unfreeze` | プラグインが再開されたとき | タイマーの再開 |

## 完全な例

```python
from plugin.sdk.plugin import NekoPluginBase, neko_plugin, lifecycle, plugin_entry, Ok
import aiohttp

@neko_plugin
class WeatherPlugin(NekoPluginBase):

    def __init__(self, ctx):
        super().__init__(ctx)
        self.session = None
        self.api_url = ""

    @lifecycle(id="startup")
    async def on_startup(self):
        cfg = await self.config.dump()
        self.api_url = cfg.get("weather", {}).get("api_url", "https://wttr.in")
        self.session = aiohttp.ClientSession()
        self.logger.info("Weather plugin ready, api={}", self.api_url)
        return Ok({"status": "ready"})

    @lifecycle(id="shutdown")
    async def on_shutdown(self):
        if self.session:
            await self.session.close()
        return Ok({"status": "stopped"})

    @lifecycle(id="config_change")
    async def on_config_change(self, old_config, new_config, mode):
        self.api_url = new_config.get("weather", {}).get("api_url", "https://wttr.in")
        self.logger.info("Config updated with mode={}", mode)
        return Ok({"status": "config_updated"})

    @plugin_entry(id="get_weather", name="Get Weather", description="Look up city weather")
    async def get_weather(self, city: str = "Beijing"):
        async with self.session.get(f"{self.api_url}/{city}?format=j1") as resp:
            data = await resp.json()
            return Ok({"city": city, "temp": data["current_condition"][0]["temp_C"]})
```
