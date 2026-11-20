# ベストプラクティス

## Result 型を一貫して使用する

エントリーポイントでは例外を発生させる代わりに、常に `Ok`/`Err` を返してください：

```python
from plugin.sdk.plugin import Ok, Err, SdkError

@plugin_entry(id="process")
async def process(self, data: str, **_):
    if not data:
        return Err(SdkError("data is required"))

    try:
        result = self._do_work(data)
        return Ok({"result": result})
    except ValueError as e:
        return Err(SdkError(f"Validation error: {e}"))
    except Exception as e:
        self.logger.exception(f"Unexpected error: {e}")
        return Err(SdkError(f"Internal error"))
```

## コード構成

初期化、ヘルパー、パブリックエントリーポイントを分離してください：

```python
@neko_plugin
class WellOrganizedPlugin(NekoPluginBase):
    def __init__(self, ctx):
        super().__init__(ctx)
        self._initialize()

    # --- ライフサイクル ---
    @lifecycle(id="startup")
    async def on_startup(self, **_):
        return Ok({"status": "ready"})

    # --- プライベートヘルパー ---
    def _initialize(self):
        """リソースのセットアップ。"""
        pass

    def _validate(self, data):
        """内部バリデーション。"""
        pass

    # --- パブリックエントリーポイント ---
    @plugin_entry(id="process")
    async def process(self, data: str, **_):
        self._validate(data)
        return Ok({"result": self._do_work(data)})
```

## ロギング

適切なログレベルを使用してください：

| レベル | 使用場面 |
|--------|---------|
| `debug` | 詳細な診断情報 |
| `info` | 通常動作のマイルストーン |
| `warning` | 予期しないが処理された状況 |
| `error` | 注意が必要なエラー |
| `exception` | スタックトレース付きエラー |

会話の原文、ユーザーが入力した秘密情報、その他の機密データをログやプロセス出力に書かないでください。通常の診断には、伏せ字にした長さ、ID、エラー種別を使います。機密内容を含む再現が必要な場合も、元データを出力せず、合成したテストデータを使ってください。

```python
self.logger.debug(f"Processing item {item_id}")
self.logger.info(f"Plugin started successfully")
self.logger.warning(f"Retry attempt {attempt}/3")
self.logger.error(f"Failed to connect: {err}")
self.logger.exception(f"Unexpected error in process()")
```

## ステータス更新

長時間実行される操作中は進捗を報告してください：

```python
@plugin_entry(id="batch_job")
async def batch_job(self, items: list, **_):
    total = len(items)
    for i, item in enumerate(items):
        self._process(item)
        self.report_status({
            "status": "processing",
            "progress": (i + 1) / total * 100,
            "message": f"Processing {i+1}/{total}"
        })

    self.report_status({"status": "completed", "progress": 100})
    return Ok({"processed": total})
```

## 入力バリデーション

入力を検証する必要がある場合は、`params=` に Pydantic モデルを渡してください。プラグインのランタイムがハンドラーを呼ぶ前にモデルで引数を検証し、不正な入力を拒否します。これはランタイム経由で配送される呼び出し（ホスト、他のプラグイン、UI から）にだけ適用され、プラグイン内部でメソッドを直接呼ぶ場合（`await self.fetch(...)`）は検証されません。`input_schema` マッピング単体は呼び出し側（UI、Agent、他のプラグイン）に公開されるだけで、実行時には**検証されません**。`params` と `input_schema` は排他的で、両方を渡すと `ValueError` になります。

```python
# オプション A: Pydantic モデル（呼び出し時に検証、スキーマはモデルから生成）
from pydantic import BaseModel, Field

class UserInput(BaseModel):
    email: str = Field(..., description="User email")
    age: int = Field(..., ge=0, le=150)

@plugin_entry(id="validated", params=UserInput)
async def validated(self, email: str, age: int, **_):
    return Ok({"email": email, "age": age})

# オプション B: JSON Schema（公開のみで検証されない：値は自分で確認する）
@plugin_entry(
    id="described",
    input_schema={
        "type": "object",
        "properties": {
            "email": {"type": "string", "format": "email"},
            "age": {"type": "integer", "minimum": 0, "maximum": 150}
        },
        "required": ["email", "age"]
    }
)
async def described(self, email: str = "", age: int = -1, **_):
    if not isinstance(email, str) or "@" not in email:
        return Err(SdkError("a valid email is required"))
    if not isinstance(age, int) or isinstance(age, bool) or not 0 <= age <= 150:
        return Err(SdkError("age must be an integer from 0 to 150"))
    return Ok({"email": email, "age": age})
```

## 作業ディレクトリ

`self.plugin_dir` は、パッケージ内のコードと読み取り専用リソースにだけ使います。実行時設定は `self.config` で管理し、永続データとキャッシュはホストが割り当てた状態ディレクトリに保存します：

```python
# 実行コードのディレクトリ（plugin.toml とパッケージ内の素材がある場所）
manifest_path = self.plugin_dir / "plugin.toml"

# 実行コードとは分離された状態ディレクトリ
db_path = self.data_path("cache.db")       # → <plugin-state-root>/data/cache.db
preview_path = self.cache_path("preview.png")  # → <plugin-state-root>/cache/preview.png
```

## プラグイン間呼び出しのエラーハンドリング

他のプラグインを呼び出す際は常に `Err` を処理してください：

```python
@plugin_entry(id="orchestrate")
async def orchestrate(self, **_):
    # まず依存関係を確認
    dep = await self.plugins.require_enabled("dependency_plugin")
    if isinstance(dep, Err):
        return Err(SdkError("Required plugin 'dependency_plugin' is not available"))

    # 呼び出しを実行
    result = await self.plugins.call_entry("dependency_plugin:do_work", {"key": "val"})
    if isinstance(result, Err):
        self.logger.error(f"Cross-plugin call failed: {result.error}")
        return Err(SdkError("Dependency call failed"))

    return Ok({"combined": result.value})
```

## グレースフルシャットダウン

shutdown ライフサイクルでリソースをクリーンアップしてください：

```python
@lifecycle(id="shutdown")
async def on_shutdown(self, **_):
    # 自分で起動したスレッドを止める（例：スレッドが監視する threading.Event）。
    # 非同期 HTTP client/session はここで閉じない：shutdown は独自のイベントループで
    # 動くため、エントリー内で呼び出しごとに作成する。
    stop = getattr(self, "_worker_stop", None)
    if stop is not None:
        stop.set()

    # self.store のフラッシュは不要です：各 set() は同期的にコミットされます。
    # 自分で store を開いた場合は、ここで閉じてください（任意）：
    # await self.store.close()

    # タイマーをキャンセル（自動的に処理されますが、ログに記録）
    self.logger.info("Plugin shutting down gracefully")
    return Ok({"status": "stopped"})
```

## プラグインページから CSRF トークンを送る

プラグインが静的ページ（またはその他のブラウザコード）を同梱し、`/runs`、`/uploads/...`、`/plugin/<id>/ui-api/...`、`/plugin/<id>/config` などプラグインサーバーへ `POST`、`PUT`、`PATCH`、`DELETE` リクエストを送る場合は、この SDK リリースからインスタンスの CSRF トークンを付けて送ってください。

```js
let tokenPromise = null

async function csrfHeaders() {
  tokenPromise ??= fetch('/security/csrf-token', { credentials: 'same-origin' })
    .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
    .then((data) => data.csrf_token)
  try {
    return { 'X-CSRF-Token': await tokenPromise }
  } catch (error) {
    // Tokenless page writes are accepted by default: keep working, retry later.
    tokenPromise = null
    return {}
  }
}

await fetch('/runs', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json', ...(await csrfHeaders()) },
  body: JSON.stringify({ plugin_id: 'my_plugin', entry_id: 'do_work', args: {} }),
})
```

- トークンを送らないページも既定では引き続き動作します。ホストはリクエストが信頼できるオリジンから来たかだけを確認します。マーケットで公開済みのプラグインがこの変更で動かなくなることはありません。
- トークンは公開環境にデプロイする人向けのオプションです。`NEKO_PLUGIN_PAGE_MUTATION_REQUIRE_TOKEN=1` を設定すると、トークンを送らないページは `403`（`csrf_validation_failed`）になります。
- トークンの取得に失敗した場合は、例のようにトークンなしでリクエストを送り、操作を止めないでください。ホストは既定で受け付けます。
- 送るトークンは正しい値でなければなりません。`detail.csrf_failure` が `"token"` の `403` を受け取ったら、キャッシュしたトークンを破棄して取り直し、1 回だけ再試行してください。
- Hosted TSX の画面は `props.api` 経由でアクションを呼ぶため、トークンは自動で付きます。

## プラグインチェックリスト

プラグインをリリースする前に確認してください：

- [ ] すべてのエントリーポイントが `Ok`/`Err` を返している（生の dict や例外ではなく）
- [ ] setup/cleanup が本当に必要な resource にだけ lifecycle hook を追加している
- [ ] 入力の検証が必要な entry は Pydantic `params` モデルを使っている（明示的な `input_schema` は公開のみで検証されない）
- [ ] handler signature は消費する field だけを宣言し、追加 field を意図する場合だけ `**_` を使う
- [ ] 通常 metadata は logger、raw privacy-sensitive content は logger に書かない
- [ ] タイマーを使用する場合、共有状態がロックで保護されている
- [ ] プラグイン間呼び出しが `Err` 結果を処理している
- [ ] `plugin.toml` の host-loading `[plugin].entry` path と SDK version constraint が正しい
- [ ] ブラウザページからプラグインサーバーへの書き込みリクエストに `X-CSRF-Token` を付けている
