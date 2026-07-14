# PluginRouter — 大きなプラグインを分割する

プラグインが大きくなると、すべてのエントリーポイントを 1 つの `__init__.py` に置くのは保守しにくくなります。`PluginRouter` を使うと、機能ごとにエントリーを別ファイルへ分けつつ、ユーザーからは 1 つのプラグインとして見せられます。

---

## Router が必要になる場面

- プラグインに 5 個以上のエントリーポイントがある
- エントリーが複数の機能領域に分かれている。例: "weather"、"routes"、"food"
- 複数人で同じプラグインを開発し、それぞれが別モジュールを担当している
- コードが 300 行を超え、ファイルを分割したい

エントリーが 1〜3 個だけなら、メインファイルにそのまま書けば十分です。Router は不要です。

---

## 実際の見え方

"Life Kit" プラグインには 12 個の機能モジュールがあります。

```text
plugin/plugins/lifekit/
├── __init__.py              ← メインプラグイン: すべての router を登録
├── routers/
│   ├── __init__.py          ← すべての router を export
│   ├── current.py           ← 現在の天気
│   ├── hourly.py            ← 時間ごとの予報
│   ├── travel.py            ← 旅行アドバイス
│   ├── locations.py         ← 場所管理
│   ├── trip.py              ← 経路計画
│   ├── nearby.py            ← 周辺検索
│   ├── food.py              ← 食事のおすすめ
│   ├── recipe.py            ← レシピ
│   ├── air_quality.py       ← 空気質
│   ├── currency.py          ← 通貨換算
│   ├── countdown.py         ← カウントダウン
│   └── unit_convert.py      ← 単位変換
└── plugin.toml
```

Plugin Manager では、ユーザーには 12 個以上のエントリーを持つ 1 つの "Life Kit" プラグインとして表示されます。コードがどう分割されているかを意識する必要はありません。

---

## Router の書き方

### Step 1: Router ファイルを作る

```python
# routers/countdown.py

from plugin.sdk.plugin import PluginRouter, plugin_entry, Ok, Err, SdkError


class CountdownRouter(PluginRouter):
    """Countdown feature."""

    def __init__(self):
        super().__init__(name="countdown")

    @plugin_entry(
        id="countdown",
        name="Countdown",
        description="Calculate days until a target date",
    )
    async def countdown(self, target_date: str, label: str = ""):
        # your business logic
        ...
        return Ok({"summary": f"{label} is in 30 days"})

    @plugin_entry(
        id="days_between",
        name="Days Between",
        description="Calculate days between two dates",
    )
    async def days_between(self, start_date: str = "", end_date: str = ""):
        ...
        return Ok({"summary": "100 days"})
```

重要な点:

- `PluginRouter` を継承します。
- `super().__init__(name="countdown")` で router に名前を付けます。これはデバッグログなどで使われます。
- `@plugin_entry` でエントリーを定義します。メインプラグイン内で書く場合と同じ構文です。

### Step 2: メインプラグインで登録する

```python
# __init__.py

from plugin.sdk.plugin import NekoPluginBase, neko_plugin, lifecycle, Ok
from .routers import CountdownRouter, WeatherRouter

@neko_plugin
class LifeKitPlugin(NekoPluginBase):

    def __init__(self, ctx):
        super().__init__(ctx)

        # Router の登録。必ず __init__ で行う
        self.include_router(CountdownRouter())
        self.include_router(WeatherRouter())

    @lifecycle(id="startup")
    async def on_startup(self):
        self.logger.info("Life Kit started")
        return Ok({"status": "ready"})
```

`self.include_router()` は、router 内のすべてのエントリーポイントを現在のプラグインに登録します。

---

## Router からアクセスできるもの

登録された router はメインプラグインに束縛されます。以下のプロパティ経由で、メインプラグインの機能にアクセスできます。

```python
from plugin.sdk.plugin import unwrap

class MyRouter(PluginRouter):

    @plugin_entry(id="example", name="Example", description="Demo router capabilities")
    async def example(self):
        # ログ
        self.logger.info("Logging from a router")

        # 設定を読む
        cfg = await self.config.dump()

        # ストレージを使う
        unwrap(await self.store.set("key", "value"))

        # 他のプラグインを呼ぶ
        result = await self.plugins.call_entry("other:entry")

        # データベースへアクセス
        async with unwrap(await self.db.session()) as session:
            cursor = await session.execute("SELECT * FROM notes")
            rows = cursor.fetchall()

        # メインプラグイン独自の属性やメソッドへアクセス
        plugin = self.main_plugin
        data = await plugin.some_shared_method()

        return Ok({"done": True})
```

| プロパティ | 参照元 |
|------------|--------|
| `self.logger` | メインプラグインの logger |
| `self.config` | メインプラグインの config |
| `self.store` | メインプラグインの store |
| `self.db` | メインプラグインの db |
| `self.plugins` | メインプラグインの plugins |
| `self.plugin_id` | メインプラグインの ID |
| `self.main_plugin` | メインプラグインのインスタンス自体 |

Router は別プロセスではありません。メインプラグインと同じプロセスで動き、すべてのリソースを共有します。

---

## ロジックを共有する

複数の router が同じユーティリティ関数を必要とする場合は、メインプラグインまたは共有モジュールに置きます。

```text
plugin/plugins/lifekit/
├── __init__.py          ← メインプラグイン、共有メソッドを定義
├── _geo.py              ← 共有: 位置情報
├── _api.py              ← 共有: API 呼び出しユーティリティ
├── _chat.py             ← 共有: チャットへのメッセージ push
└── routers/
    ├── current.py       ← self.main_plugin._resolve_location() を使う
    └── travel.py        ← self.main_plugin._resolve_location() を使う
```

Router は `self.main_plugin` からメインプラグインのメソッドへアクセスします。

```python
class WeatherRouter(PluginRouter):

    @plugin_entry(id="get_weather", name="Get Weather", description="Look up weather")
    async def get_weather(self, city: str = ""):
        plugin = self.main_plugin
        # メインプラグイン上の共有メソッドを呼ぶ
        location, error = await plugin._resolve_location(city)
        if not location:
            return Err(SdkError(error))
        ...
```

---

## prefix 付き Router

ID の衝突を避けるため、router 内のすべてのエントリー ID に prefix を付けたい場合があります。

```python
self.include_router(CountdownRouter(), prefix="time_")
```

この場合、`countdown` エントリーの実際の ID は `time_countdown` になります。

多くの場合、prefix は不要です。router 間でエントリー ID が重複しないようにすれば十分です。

---

## 実行中の削除

`exclude_router()` はプラグインの router リストから router を外しますが、通常のプラグインコードではライブの機能トグルとして使わないでください。エントリーポイントは host が dispatch table を構築するときに収集されるため、その後で router を外しても、すでに収集されたエントリーが自動的に呼び出せなくなるわけではありません。

実行中に機能を有効化/無効化する場合は entry logic で明示的な Plugin 設定を確認し、dispatch 構造が変わるときは Plugin を再構築または再起動してください。削除済みの Extension 制御経路には依存しないでください。

```python
# router リストから外すだけ
self.exclude_router(my_router_instance)

# 名前でも同様
self.exclude_router("countdown")
```

---

## 最小の完全例

2 つの router を持つプラグインです。

```python
# routers/greet.py
from plugin.sdk.plugin import PluginRouter, plugin_entry, Ok

class GreetRouter(PluginRouter):
    def __init__(self):
        super().__init__(name="greet")

    @plugin_entry(id="hello", name="Hello", description="Say hello")
    async def hello(self, name: str = "World"):
        return Ok({"message": f"Hello, {name}!"})


# routers/math.py
from plugin.sdk.plugin import PluginRouter, plugin_entry, Ok, Err, SdkError

class MathRouter(PluginRouter):
    def __init__(self):
        super().__init__(name="math")

    @plugin_entry(id="add", name="Add", description="Add two numbers")
    async def add(self, a: float, b: float):
        return Ok({"result": a + b})

    @plugin_entry(id="divide", name="Divide", description="Divide two numbers")
    async def divide(self, a: float, b: float):
        if b == 0:
            return Err(SdkError("Cannot divide by zero"))
        return Ok({"result": a / b})


# __init__.py
from plugin.sdk.plugin import NekoPluginBase, neko_plugin
from .routers.greet import GreetRouter
from .routers.math import MathRouter

@neko_plugin
class MyPlugin(NekoPluginBase):
    def __init__(self, ctx):
        super().__init__(ctx)
        self.include_router(GreetRouter())
        self.include_router(MathRouter())
```

このプラグインはパネルに `hello`、`add`、`divide` の 3 つのエントリーを表示します。
