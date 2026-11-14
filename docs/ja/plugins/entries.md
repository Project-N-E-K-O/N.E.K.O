# エントリーとパラメーター

entry point は、ロード済み plugin が Plugin Manager、他 plugin、Agent（ネコクロー、NekoClaw）に公開する callable operation です。`@plugin_entry(id=...)` の runtime `entry_id` で識別します。

`plugin.toml` の `[plugin].entry` と混同しないでください。そちらは plugin class を import する `module.path:ClassName` です。また `@llm_tool` とも別です。LLM tool は main_server の `/api/tools` registry に登録され、会話中にメインの対話モデルが呼びます。一方 Agent は、用件を任された後に plugin の runtime entry を選びます。

Agent はまず plugin を絞り込み、その後で entry metadata を評価します。精選は実在する `plugin_id` と `entry_id` を正確に返す必要があります。ID が欠けている、または存在しない場合は聞き直さず、どの entry も直接は呼ばれません。その用件は task agent に渡され、task agent が使えないときは「できない」と返されます。entry がいつ、誰に呼ばれ、結果がどう扱われるかは、後述の「Agent が entry を呼ぶとき」を参照してください。

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

この entry を呼ぶ task agent にも、この説明が渡ります。task agent は entry の `input_schema` をそのまま受け取るためです。一方、entry を直接呼ぶ精選に見えるのは、パラメーターの名前、型、`enum` の値だけです。精選に必ず伝えたいことは entry の `description` に書いてください。二つの呼び出し元の違いは、後述の「Agent が entry を呼ぶとき」で説明します。

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

`llm_result_fields` で、AI が結果のどのフィールドを読むかを宣言します。戻り値には検索結果の生データのような大きな値が入りがちですが、AI に必要なのは要約だけ、という場合に使います。

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

`llm_result_fields` を宣言しない場合に AI へ何が渡るかは、entry を誰が呼んだかで変わります。後述の「Agent が entry を呼ぶとき」にある結果の対照表を参照してください。

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

## Agent が entry を呼ぶとき

Agent（ネコクロー、NekoClaw）が entry を探すのは、メインの対話モデルが用件を任せたときだけです。つまり、モデルが `dispatch_task` ツールを呼んだとき、または口頭で引き受けてターン後チェックが代わりに dispatch したときです。ターンが終わるたびに自動で解析する処理はありません。全体の流れは[エージェントシステム](/ja/architecture/agent-system)、plugin の絞り込み方は [plugin.toml](./plugin-toml) を参照してください。ID が `__llm_tool__` で始まる entry（`@llm_tool` が作るもの）はこの選択に加わらず、メインの対話モデルだけが呼びます。

### Agent に見える entry

plugin が `passive = true` を設定している場合、または entry の `metadata` で `agent_auto`、`agent_exposed`、`llm_exposed` が `false`、あるいは `agent_hidden` が `true` の場合、その entry は対象から外れます。

```python
@plugin_entry(id="internal_sync", metadata={"agent_hidden": True})
async def internal_sync(self):
    ...
```

これらのスイッチが効くのは Agent に対してだけです。Plugin Manager や他の plugin からは、引き続き呼び出せます。

### 二つの呼び出し元

- **直接呼び出し（主経路）。** entry 一つで終わる用件では、精選がその entry を選び、引数を埋めます。精選に見えるのは、plugin の `description` と、可視 entry ごとの ID、`description`、短い引数ヒントです。引数ヒントは、先頭 8 個のパラメーターの型と `enum` の値、それに `required` です。パラメーターの説明は含まれません。
- **task agent。** 合う entry がない、または複数ステップが必要な用件は、task agent に渡されます。task agent は、画面上で「使い魔」と呼ばれる実行セッションです。能力ゲートウェイ経由で、entry をツールとして呼びます。ツールの説明は plugin 名、entry 名、entry の `description` を並べたもの（200 token で切り詰め）、ツールのパラメーターは entry の `input_schema` です。task agent が受け取るのは任された内容で、会話履歴ではありません。選択の際に候補に残った plugin の entry は、最初からツールとして並びます。それ以外の可視 entry は、説明で照合する検索ツールを通じて見つけます。

どちらの呼び出し元も同じ `/runs` リクエストを送り、`args._ctx` に同じキーを入れます。

| `_ctx` のキー | 値 |
|---|---|
| `lanlan_name` | その用件の持ち主であるキャラクター |
| `conversation_id` | dispatch に付いてきた識別子。ない場合は入らない |
| `entry_timeout` | この呼び出しに適用されるタイムアウト。entry の `timeout`、またはホストの既定値 |
| `latest_user_request` | そのターンのユーザーの元の発言。書き換えない |

plugin entry の権限カテゴリ（ホストの `permissions.json` の `plugin_entry`）は、既定で `allow` です。確認を求めるモードにすると、task agent からの呼び出しはユーザーの承認を待ちます。`deny` にすると、どちらの呼び出し元も plugin entry を使いません。

### 結果の扱い

| | 直接呼び出し | task agent からの呼び出し |
|---|---|---|
| 結果を読むのは | メインの対話モデル。その用件の報告として受け取る | task agent。ツール結果として受け取る。ユーザーに伝わるのは用件全体が終わったとき |
| `llm_result_fields` を宣言した場合 | 列挙したフィールドだけ | 列挙したフィールドだけ |
| `llm_result_fields` を宣言しない場合 | 一文だけ。`data.reply` が空でない文字列ならそれ、なければ Run の message | その一文に続けて、`data` を JSON で渡す |
| `meta.agent.delivery`、`result_kind`、`expires_in_s` | 有効 | 無効。ユーザーへの報告は、その用件自体の設定に従う |
| `data.needs_confirmation`（または `status: "confirm_required"`） | 用件は `blocked` で終わり、plugin のメッセージが報告される | ゲートウェイが plugin のメッセージを質問としてユーザーに尋ね、回答を task agent に渡す。task agent がユーザーの代わりに答えることはできない |
| `data.action: "clarify"`（または `status: "clarify"` / `"blocked"`） | 用件は `blocked` で終わり、plugin のメッセージが報告される | 「この呼び出しは実行されなかった」という注記を付けて task agent に返す |
| `data.deferred: true` | plugin がコールバックするまで用件は終わらない。1 時間コールバックがなければ失敗になる | 「plugin が後で自分で完了する」という注記を付けて task agent に返す。`bind_task` は呼ばれない |

直接呼び出しの列は、以前の Agent と同じです。結果に `observation_only` が付いている場合、どちらの呼び出し元も `needs_confirmation` や `clarify` として扱いません。

### 中止

用件が中止されると、ホストはその用件のまだ動いている Run すべてについて、プラグインサービスの `POST /runs/{run_id}/cancel` を呼びます。直接呼び出しでも、task agent からの呼び出しでも同じです。

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
