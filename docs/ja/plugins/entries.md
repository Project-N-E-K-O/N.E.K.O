# エントリーとパラメーター

entry point は、ロード済み plugin が Plugin Manager、他 plugin、user-plugin Agent route に公開する callable operation です。`@plugin_entry(id=...)` の runtime `entry_id` で識別します。

`plugin.toml` の `[plugin].entry` と混同しないでください。そちらは plugin class を import する `module.path:ClassName` です。また `@llm_tool` とも別です。LLM tool は main_server の `/api/tools` registry に登録されて会話中に呼ばれますが、user-plugin Agent routing が選ぶのは plugin runtime entry です。

Agent routing は plugin-level filtering の後に entry metadata を評価します。Stage 2 は実在する `plugin_id` と `entry_id` を正確に返す必要があり、missing/unknown ID は correction retry 1 回の後に拒否されます。

---

## 最もシンプルなエントリー

プラグインに 1 つの処理を追加したい場合、メソッドに `@plugin_entry` を付けます。

```python
from plugin.sdk.plugin import NekoPluginBase, neko_plugin, plugin_entry, Ok

@neko_plugin
class MyPlugin(NekoPluginBase):

    @plugin_entry(id="hello", name="Say Hello", description="Say hello")
    async def hello(self):
        return Ok({"message": "Hello!"})
```

このエントリーにはパラメーターがありません。Plugin Manager で実行すると、`{"message": "Hello!"}` が返ります。

---

## パラメーターを追加する

多くのエントリーは入力を必要とします。関数シグネチャに普通にパラメーターを書くだけです。

```python
@plugin_entry(id="greet", name="Greet", description="Greet someone by name")
async def greet(self, name: str, times: int = 1):
    messages = [f"Hello, {name}!" for _ in range(times)]
    return Ok({"messages": messages})
```

SDK は自動的に次を行います。

- `name: str` と `times: int = 1` から入力フォームを生成します。フォームはパネルに表示されます。
- `name` はデフォルト値がないので必須です。
- `times` はデフォルト値 `1` があるので任意です。
- 型注釈はフォーム/schema のヒントとして使われます。直接呼び出された通常パラメーターはそのまま渡されます。実行時の検証が必要な場合は Pydantic モデルを使ってください。

JSON Schema を自分で書く必要はありません。

---

## パラメーターに説明を付ける

デフォルトでは、パネルには変数名、たとえば `name` や `times` が表示されます。より分かりやすい説明を表示したい場合は `Annotated` を使います。

```python
from typing import Annotated

@plugin_entry(id="greet", name="Greet", description="Greet someone by name")
async def greet(
    self,
    name: Annotated[str, "The person's name"],
    times: Annotated[int, "How many times to repeat"] = 1,
):
    messages = [f"Hello, {name}!" for _ in range(times)]
    return Ok({"messages": messages})
```

これでパネルでは、`name` の横に "The person's name"、`times` の横に "How many times to repeat" が表示されます。

AI エージェントにもこの説明が渡るため、どんな値を渡すべきか理解しやすくなります。

---

## パラメーターが多い場合は Pydantic モデルを使う

パラメーターが多い、または min/max、正規表現など複雑な検証が必要な場合は、Pydantic モデルとして定義します。

```python
from pydantic import BaseModel, Field

class SearchParams(BaseModel):
    query: str = Field(..., description="Search keywords")
    max_results: int = Field(default=10, ge=1, le=50, description="Max results")
    language: str = Field(default="zh-CN", description="Result language")
    include_images: bool = Field(default=False, description="Include image results")

@plugin_entry(id="search", name="Search", description="Search for content")
async def search(self, params: SearchParams):
    self.logger.info("Searching: {} (max {})", params.query, params.max_results)
    results = await self._do_search(params.query, params.max_results)
    return Ok({"results": results, "count": len(results)})
```

SDK は、関数に BaseModel 型のパラメーターが 1 つだけあることを検出すると、自動的に次を行います。

1. モデルから入力フォームを生成します。説明、デフォルト値、制約のヒントも反映されます。
2. 呼び出し時に `model_validate()` で検証します。たとえば `max_results=100` は `le=50` に反するので拒否されます。
3. 検証済みのモデルインスタンスを関数に渡します。

**Pydantic と通常のパラメーター、どちらを使うべきか？**

- パラメーターが 3 個以下で複雑な検証がない → 関数シグネチャに直接書く
- パラメーターが多い、検証制約が必要、ドキュメント性を高めたい → Pydantic モデルを使う

---

## 成功と失敗を返す

エントリーポイントは `Ok(...)` または `Err(...)` を返します。

```python
from plugin.sdk.plugin import Ok, Err, SdkError

@plugin_entry(id="divide", name="Divide", description="Divide two numbers")
async def divide(self, a: float, b: float):
    if b == 0:
        return Err(SdkError("Cannot divide by zero"))
    return Ok({"result": a / b})
```

- `Ok(data)` — 成功です。`data` には dict、list、文字列、数値を入れられます。
- `Err(SdkError("reason"))` — 失敗です。エラーメッセージはパネルに表示され、AI にも「この呼び出しは失敗した」と伝わります。

**なぜ例外をそのまま投げないのか？** プラグインは別プロセスで動くため、例外はメインシステムにそのまま伝播しません。`Ok`/`Err` はプロセス間でも安全な通信パターンです。とはいえ、予期しない例外が発生した場合はフレームワークが捕捉して自動的に `Err` へ変換します。クラッシュはしません。

---

## AI に見せる戻り値を制御する

デフォルトでは、AI は戻り値のすべてのフィールドを見ます。ただし、検索結果の生データのように大きな値を返す場合、AI には要約だけ見せたいことがあります。

```python
@plugin_entry(
    id="search",
    name="Search",
    description="Web search",
    llm_result_fields=["summary", "count"],
)
async def search(self, query: str):
    results = await self._do_search(query)
    summary = self._build_summary(results)
    return Ok({
        "summary": summary,          # ← AI が見られる
        "count": len(results),        # ← AI が見られる
        "raw_results": results,       # ← AI には見えないが、データは保存される
    })
```

`llm_result_fields` に指定したフィールドだけが AI に送られます。その他のフィールドは通常どおり保存され、パネルでも見られますが、AI のコンテキストには入りません。トークン節約に役立ちます。

---

## その他のエントリーオプション

`@plugin_entry` には追加オプションがあります。

```python
@plugin_entry(
    id="process",              # エントリー ID（省略時はメソッド名）
    name="Process Data",       # 表示名
    description="Process and transform data",  # 人間と AI 向けの説明
    timeout=60.0,              # host-side execution timeout（秒）
    kind="service",            # 種別タグ（デフォルトは "action"）
)
async def process(self, data: str):
    ...
```

ほとんどの場合、必要なのは `id`、`name`、`description` だけです。その他は必要に応じて使います。起動時の初期化には、エントリーの自動実行を期待せず、`startup` ライフサイクルフックを定義してください。

---

## 動的エントリー: 実行時に登録する

プラグインが持つエントリーを事前に決められないことがあります。設定、ユーザーの選択、外部サービスから返る能力に依存する場合です。そのときは動的登録を使います。

```python
from plugin.sdk.plugin import lifecycle, Ok

@lifecycle(id="startup")
async def on_startup(self):
    # 設定でコマンド一覧が定義されているとする
    cfg = await self.config.dump()
    commands = cfg.get("commands", {})

    for cmd_id, cmd_info in commands.items():
        self.register_dynamic_entry(
            entry_id=cmd_id,
            handler=self._make_handler(cmd_info),
            name=cmd_info.get("name", cmd_id),
            description=cmd_info.get("description", ""),
            input_schema=cmd_info.get("input_schema", {
                "type": "object",
                "properties": {
                    "cmd": {"type": "string", "description": "Command input"},
                },
            }),
        )

    self.logger.info("Registered {} dynamic entries", len(commands))
    return Ok({"status": "ready"})

def _make_handler(self, cmd_info):
    template = cmd_info.get("template", "Executed: {cmd}")
    async def handler(cmd: str = ""):
        return Ok({"output": template.format(cmd=cmd)})
    return handler
```

動的エントリーも静的エントリーと同じように動きます。パネルに表示され、実行でき、AI からも呼び出せます。handler がパラメーターを受け取る場合は、`input_schema` を明示的に渡してください。動的登録では handler のシグネチャから推論されません。

削除するには `self.unregister_dynamic_entry(entry_id)` を使います。

---

## まとめ

| シナリオ | 方法 |
|----------|------|
| シンプルな関数、少ないパラメーター | `@plugin_entry` + 関数シグネチャの型注釈 |
| パラメーター説明を表示したい | `Annotated[type, "description"]` を使う |
| パラメーターが多い、検証が必要 | Pydantic モデルを単一パラメーターとして定義 |
| 成功を返す | `return Ok({...})` |
| 失敗を返す | `return Err(SdkError("reason"))` |
| AI に見せる戻り値を制限する | `llm_result_fields=["field1", "field2"]` |
| 実行時までエントリーが分からない | `self.register_dynamic_entry(...)` |
