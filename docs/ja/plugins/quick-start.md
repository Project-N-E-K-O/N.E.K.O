# N.E.K.O Plugin CLI で最初のプラグインを作成する

このページの目的は一つです。GitHub から N.E.K.O のソースを取得できること、そのソースに含まれる N.E.K.O Plugin CLI が自分の環境で動くことを確認し、開発を続けられる独立したプラグインリポジトリを作成します。

完了すると、サンプルコード、テスト、コードチェック、GitHub Actions のリリース設定を含む `hello_world` プロジェクトができます。このプロジェクトがプラグイン開発の出発点になります。

## 1. Git と uv を確認する

ターミナルで次を実行します：

```bash
git --version
uv --version
```

| コマンド | 確認すること |
| --- | --- |
| `git --version` | Git を使用できること。N.E.K.O のソースを複製し、プラグインの変更履歴を記録して GitHub へ送信するために使います。 |
| `uv --version` | uv を使用できること。`uv.lock` に固定された Python 依存関係のインストールと Plugin CLI の実行に使います。 |

両方ともバージョンを表示する必要があります。どちらかで「コマンドが見つかりません」と表示された場合は、先にそのツールをインストールします：

- [Git 公式ダウンロード](https://git-scm.com/downloads)から Git をインストールします。
- [uv 公式インストールガイド](https://docs.astral.sh/uv/getting-started/installation/)に従って uv をインストールします。

## 2. N.E.K.O のソースを取得する

Plugin CLI は N.E.K.O のソースに含まれており、単独ではインストールできません。**N.E.K.O プラグイン**の開発を始める推奨方法は、ソースを直接取得することです：

```bash
git clone --filter=blob:none https://github.com/Project-N-E-K-O/N.E.K.O.git
cd N.E.K.O
```

すでにソースがある場合は、再度 clone せず、その checkout に移動します：

```bash
cd /path/to/N.E.K.O
```

::: warning 既存ディレクトリへ clone しないでください
`N.E.K.O` というディレクトリがすでにあると `git clone` は停止します。既存のソースかどうかを確認してください。設定や未コミットの変更が含まれる可能性があるため、ガイドを続ける目的だけで削除しないでください。
:::

## 3. 環境を準備して CLI を確認する

N.E.K.O リポジトリのルートで実行します：

```bash
uv sync
uv run neko-plugin --help
```

`neko-plugin` のヘルプには少なくとも次のコマンドが表示されます：

```text
init
check
sync
build
publish
```

これらが表示されれば CLI を使用できます。以後も `uv run neko-plugin` を使用します。

## 4. 独立したプラグインリポジトリを作成する

N.E.K.O リポジトリのルートから実行します：

```bash
uv run neko-plugin init hello_world --type plugin --name "Hello World" --output ../n.e.k.o_plugin_hello_world
```

このコマンドは **Hello World** プラグインを初期化します。

N.E.K.O の checkout 内に Git リポジトリを入れ子にしないよう、プラグインは N.E.K.O の隣に作成されます。以下の手順にある `../n.e.k.o_plugin_hello_world` は、今作成したプラグインのディレクトリです。N.E.K.O のソースディレクトリから一つ上の階層へ移動すると到達できます。

対象ディレクトリがすでにある場合、CLI は上書きせず停止します。別の新しいディレクトリを選ぶか、既存ディレクトリの用途を確認してください。

## 5. 最初のチェックを実行する

```bash
uv run neko-plugin check ../n.e.k.o_plugin_hello_world
```

新しいプロジェクトでは次のように表示されます：

```text
[OK] hello_world: check found 0 error(s), 2 warning(s)
```

GitHub remote が未設定、またはファイルが未コミットという警告は、この時点では正常です。新しいリポジトリをまだ commit、push していないためです。今後 `[FAIL]` または error が表示された場合は停止し、コマンドの修正案に従ってください。

## CLI が作成したもの

```text
n.e.k.o_plugin_hello_world/
├── .git/
├── .gitignore
├── .vscode/
├── plugin.toml
├── config.example.toml
├── __init__.py
├── pyproject.toml
├── README.md
├── tests/test_smoke.py
├── ruff.toml
└── .github/workflows/
    ├── verify.yml
    └── release.yml
```

ディレクトリ構成や GitHub Actions を手作業で用意する必要はありません。生成されたファイルを使って最初の機能を作ります。

## 6. プラグイン設定を理解する

`../n.e.k.o_plugin_hello_world/plugin.toml` を開きます。CLI は identity と entry point をすでに記述しています：

```toml
[plugin]
id = "hello_world"
name = "Hello World"
version = "0.1.0"
type = "plugin"
entry = "plugin.plugins.hello_world:HelloWorldPlugin"

[plugin.sdk]
recommended = ">=0.1.0,<0.2.0"
supported = ">=0.1.0,<0.3.0"
```

- `id` は安定したプラグイン identity であり、インストール後のディレクトリ名です。
- `version` は次の build と release に使われます。
- `entry` は `module.path:ClassName` 形式で Python class を指定します。
- `[plugin.sdk]` は対応する SDK version を宣言します。

ユーザーが変更できる runtime default は `plugin.toml` ではなく `config.example.toml` に置きます：

```toml
[plugin_runtime]
enabled = true
auto_start = false
```

`auto_start = false` の場合、インストール後に Plugin Manager から手動で start します。N.E.K.O と同時に自動起動する必要がある場合だけ `true` に変更します。

## 7. 最初のプラグイン機能を書く

生成された `__init__.py` には、名前を受け取って greeting を返す entry がすでにあります：

```python
from typing import Any

from plugin.sdk.plugin import (
    NekoPluginBase,
    Ok,
    lifecycle,
    neko_plugin,
    plugin_entry,
)


@neko_plugin
class HelloWorldPlugin(NekoPluginBase):
    """Hello World"""

    def __init__(self, ctx: Any):
        super().__init__(ctx)
        self.logger = ctx.logger

    @lifecycle(id="startup")
    def on_startup(self, **_):
        self.logger.info("HelloWorldPlugin started")
        return Ok({"status": "ready"})

    @lifecycle(id="shutdown")
    def on_shutdown(self, **_):
        self.logger.info("HelloWorldPlugin stopped")
        return Ok({"status": "stopped"})

    @plugin_entry(
        id="hello",
        name="Hello",
        description="Say hello",
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "default": "World"}
            }
        }
    )
    async def hello(self, name: str = "World", **_):
        return Ok({"message": f"Hello, {name}!"})
```

| Code | Meaning |
| --- | --- |
| `@neko_plugin` | class を N.E.K.O plugin として宣言 |
| `NekoPluginBase` | logging、config、storage などを提供 |
| `@lifecycle(...)` | plugin の start と stop で code を実行 |
| `@plugin_entry(...)` | Plugin Manager に呼び出し可能な機能を公開 |
| `input_schema` | interface に表示する入力項目を記述 |
| `Ok({...})` | successful result を返す |

`plugin_id` は `hello_world`、この機能の `entry_id` は `hello` です。別の identity なので混同しないでください。

::: tip Agent と LLM tool
user-plugin Agent がこの機能を選ぶと `plugin_id` と `entry_id` の両方を返し、host が別々に検証します。これは `@llm_tool` で conversation-time tool を登録する仕組みとは異なります。最初の実行前にこの違いを理解する必要はありません。
:::

最後の行を変更します：

```python
return Ok({"message": f"こんにちは、{name}さん！"})
```

保存後、もう一度チェックします：

```bash
uv run neko-plugin check ../n.e.k.o_plugin_hello_world
```

## 8. build して N.E.K.O で実行する

チェック成功後にローカルパッケージを作ります：

```bash
uv run neko-plugin build ../n.e.k.o_plugin_hello_world --out ../hello_world.neko-plugin
```

N.E.K.O を起動し、**Plugin Manager** の **Import** から `hello_world.neko-plugin` を選びます。import 後：

1. **Hello World** を見つけて start します。
2. **Hello** entry を開きます。
3. 名前を入力して実行します。
4. 変更した greeting が返ることを確認します。

ソース版 N.E.K.O がまだ起動できない場合は、[開発環境の準備](../guide/dev-setup)に従って frontend build と起動を完了してください。

## 9. 変更して新しい build を読み込む

独立リポジトリの `__init__.py` を変更し、`check` と `build` を再実行します。Plugin Manager で同じパッケージを再度 import すると upgrade の確認が表示されます。確認後、インストール済みの内容が安全に置き換えられ、実行中なら自動的に再起動します。

インストール先の `plugin/plugins/hello_world` を直接編集しないでください。その変更は独立プラグインリポジトリには戻りません。

## 次のステップ

| 目的 | ドキュメント |
| --- | --- |
| チェック、パッケージ化、公開を続ける | [コマンドでプラグインを作成・公開する](./cli) |
| `plugin.toml` を理解する | [プラグイン設定](./plugin-toml) |
| 呼び出し可能な機能を追加する | [エントリーとパラメーター](./entries) |
| startup と shutdown の処理を追加する | [デコレーター](./decorators) |
| conversation-time LLM tool を登録する | [LLM Tool Calling](./tool-calling) |
| 実際の plugin 例を見る | [サンプル](./examples) |
| error handling を学ぶ | [ベストプラクティス](./best-practices) |
| SDK 全体を確認する | [SDK リファレンス](./sdk-reference) |
