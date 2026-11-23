# プラグイン設定 (plugin.toml)

すべてのプラグインのルートには `plugin.toml` があります。package の種類、host が import する Python class、公開する optional capability を N.E.K.O に伝えます。

::: warning 2 種類の entry
`[plugin].entry = "module.path:ClassName"` は **host-loading entry point** です。plugin process 起動時に 1 つの `NekoPluginBase` class を import します。`greet` のような runtime entry ID は `@plugin_entry(id="greet")` または `register_dynamic_entry(...)` から作られ、plugin のロード後に Agent が選択します。
:::

以下は架空の "Smart Notes" プラグインの完全な設定例です。このプラグインはノートの検索と作成、自分専用の UI、多言語対応、AI エージェントからの呼び出しに対応しています。

## 完全な例

```toml
[plugin]
id = "smart_notes"
name = "Smart Notes"
type = "plugin"
description = "Manage your notes: search, create, organize, with AI-powered classification."
short_description = "Note management with AI-powered organization."
keywords = ["note", "筆記", "memo", "record", "メモ"]
version = "1.2.0"
entry = "plugin.plugins.smart_notes:SmartNotesPlugin"

[plugin.author]
name = "Alice"

[plugin.sdk]
recommended = ">=0.1.0,<0.2.0"
supported = ">=0.1.0,<0.3.0"

[plugin.i18n]
default_locale = "zh-CN"
locales_dir = "i18n"

[plugin.store]
enabled = true

[plugin.ui]
enabled = true

[[plugin.ui.panel]]
id = "main"
title = "Smart Notes"
entry = "ui/panel.tsx"
context = "dashboard"
permissions = ["state:read", "action:call"]

[[plugin.ui.guide]]
id = "quickstart"
title = "User Guide"
entry = "docs/guide.md"
permissions = ["state:read"]

[plugin_runtime]
enabled = true
auto_start = true

[notes]
max_per_page = 20
auto_classify = true
```

## セクションごとの説明

### `[plugin]` — このプラグインについて

```toml
[plugin]
id = "smart_notes"
name = "Smart Notes"
version = "1.2.0"
entry = "plugin.plugins.smart_notes:SmartNotesPlugin"
```

サポート対象の check / release workflow では、この 4 フィールドが **必須** です。従来のソース探索では、不完全な manifest やディレクトリ名と ID が異なるプラグインを読み込める場合がありますが、有効なリリース package であることを意味しません。`id` は一意でなければなりません。ホストは `^[A-Za-z0-9_-]+$` に一致する任意の `id` を読み込めますが、`neko-plugin init` とリリースチェック（`neko-plugin check -r`）は Market ID を要求します。先頭は英小文字、以降は `a-z`、`0-9`、`_` のみ（`^[a-z][a-z0-9_]*$`）です。`check -r` はさらに最大 100 文字に制限します。公開する予定のプラグインでは、この厳しい形式を使ってください。パッケージのビルドと本番環境へのインストールでは、宣言 ID、アーカイブ内のディレクトリ、インストール先、エントリーパッケージを一致させる必要があります。競合時に接尾辞付きのコピーは作成されません。`entry` は `module.path:ClassName` 形式で `NekoPluginBase` のサブクラスを指す必要があり、`PluginRouter` は直接起動できません。

通常の plugin では `type = "plugin"` は default なので省略できます。Adapter package のみ `type = "adapter"` を使います。削除済みの `extension` type と `[plugin.host]` table は拒否されます。

リリース間で `id` を変更しないでください。upgrade、reinstall、downgrade は実行コードだけを置き換え、実行時の `config`、`data`、`cache` は保持します。`id` を変更すると別のプラグインとして扱われます。任意の `previous_ids` は新旧 ID の同時インストールを防ぐだけで、runtime alias ではなく、旧データの移行や削除も行いません。置換操作にはユーザーの明示的な確認が必要です。

```toml
description = "Manage your notes: search, create, organize, with AI-powered classification."
short_description = "Note management with AI-powered organization."
keywords = ["note", "筆記", "memo", "record", "メモ"]
```

host が plugin をロードした後、Agent（にゃんパワー、NekoPower）はこれらの field を使って plugin を選びます。

- `description` — plugin metadata と精選に使う完全な説明です。
- `short_description` — 粗選別が読む短い説明で、読むのは最大 150 token です。**書いておいてください。** 省略されていて `description` がこの 150 token を超えるときは、ホストがバックグラウンドで小さいモデルに `description` から書かせますデータディレクトリの `nekopower/plugin_short_descriptions.json` に保存し、成功すればプラグインと説明の版ごとにモデルへのリクエスト 1 回です。150 token を超えた結果は 150 に切り詰めて保存します。リクエストが失敗した場合は保存せず、10 分後に再試行し、そのたびにリクエストが 1 回かかります。同じ版の `description` には 1 日最大 3 回まで（再起動をまたいでも数えます）で、`description` が変われば数え直します。保存されるまでは `description` の先頭部分を代わりに使います。
- `keywords` — 正規表現 pattern です。一致した plugin は粗選別の候補に入り、精選では「キーワード一致」の印が付きます。精選を省略したり、実行を保証したりはしません。

選択は二段階です。選択対象の全 plugin と、その可視 entry の説明の合計が 3000 token を超えるときは、先に粗選別を行い、三つの集合の和を残します。keywords が一致した plugin、BM25 のテキスト一致の上位 10 件（plugin の ID、名前、説明と、entry の ID・説明が対象）、小型モデルが短い説明から選んだ plugin です。その後、精選が `plugin_id` と runtime `entry_id` を選び、引数を埋めます。3000 token 以下なら粗選別は行わず、選択対象の plugin すべてが精選に渡ります。

選択が走るのは、メインの対話モデルが用件を任せたときだけです。つまり、モデルが `dispatch_task` ツールを呼んだとき、または口頭で引き受けてターン後チェックが代わりに dispatch したときです。ターンが終わるたびに自動で解析する処理はなくなったため、「ユーザーが何気なく口にしただけで選ばれる」ことに頼っていた plugin は、選ばれる回数が減ります。keywords と BM25 が照合するテキストは、ユーザーの元の発言と、モデルが dispatch した一文を合わせたものです。ユーザーの発言が keywords に一致することは、そのターンをターン後チェックへ通すシグナルの一つでもあります。

listener/integration を Agent dispatch から完全に外すには `passive = true` を設定します。non-passive plugin も Agent-visible runtime entry が 1 つ以上なければ candidate になりません。

精選が返した `plugin_id` と `entry_id` は、そのとき見せた candidate set と照合されます。不正な場合は聞き直さず、どの entry も直接は呼ばれません。その用件は task agent に渡され、task agent が使えないときは「できない」と返されます。精選は、誰にもできない用件、またはそもそも片付ける用件ではないもの（`route="none"`）と答えることもでき、その場合は何も始まりません。entry が呼ばれたときの動作は、[エントリーとパラメーター](./entries)の「Agent が entry を呼ぶとき」を参照してください。

```toml
version = "1.2.0"
```

check / release workflow では必須です。バージョン管理やマーケットプレイス公開で使います。

---

### `[plugin.author]` — 作者情報

```toml
[plugin.author]
name = "Alice"
```

任意です。Plugin Manager に表示されます。

---

### `[plugin.sdk]` — 対応 SDK バージョン

```toml
[plugin.sdk]
recommended = ">=0.1.0,<0.2.0"
supported = ">=0.1.0,<0.3.0"
```

package が対応する plugin SDK version を host に伝えます。値は Python packaging の specifier syntax です。

- `supported` — 通常サポートする範囲
- `recommended` — 最もよく検証した範囲。範囲外では warning
- `untested` — 追加で許可する範囲。該当時は warning
- `conflicts` — 他の範囲に一致していても明示的に拒否する範囲

`supported` がある場合、host は `supported` または `untested` に入らなければロードされません。不正な specifier も拒否されます。

---

### `[plugin_runtime]` — 実行方法

```toml
[plugin_runtime]
enabled = true
auto_start = true
priority = 0
timeout = 10
startup_failure = "warn"
```

- `enabled` — `false` にすると、ファイルを削除せず一時的に無効化できます
- `auto_start` — 省略時は `false`。保存済みのユーザー設定が manifest の値より優先され、自動起動にはユーザーの承認も必要です
- `priority` — optional integer runtime ordering hint
- `timeout` — startup readiness を待つ秒数。`0 < timeout <= 300` が必要で、省略時は system default
- `startup_failure` — `startup` hook failure の扱い。`warn`（default、process を残して degraded）、`fail`（startup abort）、`ignore`（log only）

プラグインセンターの独立した自動起動スイッチで、ユーザー設定を保存します。
初めてローカルにインストールするときは、パッケージに `auto_start = true` とあっても
設定を `false` として保存し、スイッチをオフで表示します。通常の更新や再インストールでは
保存済みの選択を維持します。既存のインストールで明示された manifest の値は、
ユーザー設定による上書きがなければ引き続き適用されます。

既定では、手動の起動・停止・再読み込みで自動起動設定は変わりません。
新規インストールは承認待ちとなり、独立した自動起動スイッチをオンにすると承認されます。
手動の起動や再読み込みでは、以後の自動起動は承認されません。

`NEKO_PLUGIN_SYNC_AUTO_START_ON_TOGGLE=1` を設定すると、従来の設定同期を有効にできます。
手動の起動・再読み込みは `auto_start=true`、停止は `auto_start=false` を保存します。
「手動の起動・停止ではこの設定は変わらない」というスイッチの説明は既定の動作を示します。
従来の同期を有効にしても、承認待ちのプラグインには独立したスイッチによる明示的な承認が必要です。

---

### `[plugin.i18n]` — 多言語対応

```toml
[plugin.i18n]
default_locale = "zh-CN"
locales_dir = "i18n"
```

多言語対応が必要な場合、プラグインディレクトリに `i18n/` フォルダーを作り、ロケールファイルを置きます。

```text
i18n/
├── en.json
└── zh-CN.json
```

i18n が不要なら、このセクションは書かなくてかまいません。

---

### `[plugin.store]` — 永続ストレージ

```toml
[plugin.store]
enabled = true
```

有効にすると、コード内で `self.store` を使って、再起動後も残るデータを保存・取得できます。

ストレージが不要なら、このセクションは書かなくてかまいません。デフォルトでは無効です。

---

### `[plugin.database]` — SQLite データベース

```toml
[plugin.database]
enabled = true
name = "plugin.db"   # プラグインのデータディレクトリ内のファイル名（デフォルト: plugin.db）
```

有効にすると、`self.db` はプラグインのデータディレクトリにある SQLite データベースファイルを使います。SQL には `async with unwrap(await self.db.session()) as session:`（`session()` は `Result` を返します）、キーと値のデータには `self.db.kv` を使います。デフォルトでは無効です。

---

### `[plugin_state]` — インスタンス属性の保存と復元

```toml
[plugin_state]
backend = "file"        # 状態の保存先
persist_mode = "auto"   # 保存するタイミング
```

ランタイムはプラグインクラスの `__freezable__` リストに挙げたインスタンス属性を保存し、プラグインの次回起動時に復元します。復元に成功すると保存済みの状態は消去されます。`backend` がスイッチです。`"file"` はプラグインのデータディレクトリ内のファイルに保持し、プラグインプロセスの再起動後も残るのはこれだけです。`"memory"` はプラグインプロセスが動いている間だけ保持します。`backend` を書かなければ（このテーブル自体がなければ）何も保存されません（`"off"` や空文字列を書いた場合と同じです）。`file` / `memory` / `off` 以外の値（たとえば `sqlite`）を書くとプラグインは起動に失敗します。判断に使われるのは実効設定、つまりマニフェストにランタイム側のコピー（プラグインのユーザーデータディレクトリにある `config/plugin.toml`。初回利用時に `config.example.toml` から、それがなければマニフェストからコピーされます）と現在のプロファイルをマージした結果です。そのため、ランタイム側のコピーにも `backend` がある場合、マニフェストから消すだけでは保存は止まりません。保存を止めるには、実際に効く層で `backend = "off"` を指定してください。ランタイム側のコピーと現在のプロファイルはマニフェストより優先されます。`persist_mode` はどのエントリー呼び出しの後に保存するかを決めます。`"auto"` ではエントリー呼び出しが正常に戻るたびに保存し（`Err` を返した場合も正常に戻ったとみなします。例外や厳格な出力契約の違反では保存しません。ランタイム経由で配送された呼び出しだけが対象で、プラグイン内部でエントリーメソッドを直接呼ぶ場合（タイマーからなど）は保存されません）、`"manual"` では `@plugin_entry(persist=True)` で宣言したエントリーの呼び出し後だけ保存し、`"off"` ではエントリー呼び出し後に保存しません。エントリーの `persist=True` / `persist=False` は `"auto"` / `"manual"` より優先されます。モードは次の順で決まります。まずクラス属性 `__persist_mode__`（デフォルト `"off"`）、次に `[plugin_state].persist_mode` が `auto` / `manual` / `off` のいずれかならそれで上書きします（それ以外の値はランタイムでは無視されます。`neko-plugin check` は `always` / `disabled` を error とし、`auto` / `off` への変更を提案します）。結果が `"off"` の場合（`persist_mode = "off"` を明示した場合も含む）、旧設定 `[plugin_checkpoint] freeze_mode` が `"auto"` または `"manual"` ならそれで上書きされます。また `persist_mode` に関係なく、プラグインをフリーズするときにも状態が保存されます。

---

### `[plugin.shared]` — 他のプラグインの共有ファイルを読む

```toml
[plugin.shared]
reads = ["weather"]
```

どのプラグインにも共有ディレクトリ `<ユーザーデータルート>/plugin_shared/<プラグイン id>/` があり、他のプラグインに読ませてよいファイルを置けます。持ち主のプラグインは `self.shared_path(...)` で書き込みます。他のプラグインが公開したファイルを読むには、そのプラグインの id を `reads` に書いたうえで `self.shared_path_of("weather", ...)` を呼びます。宣言がないまま呼ぶと `ValidationError` が発生します。

知っておくべき点が 3 つあります。

- **これは取り決めであり、セキュリティ境界ではありません。** プラグイン同士はサンドボックスで隔離されていないため、あるプラグインが他のプラグインのディレクトリに書き込むことを防ぐ仕組みはありません。宣言は、この依存関係をマニフェスト上で見えるようにするためのものです。他のプラグインのファイルは読み取り専用として扱い、自分の共有ディレクトリに秘密情報を置かないでください。
- **共有ディレクトリはプラグインのプライベートな状態ではありません。** 公開したプラグインが削除されてもディレクトリは残り、ユーザーが保存場所を変更したときはユーザーデータと一緒に移動します。
- **リンクを置かないでください。** このディレクトリはユーザーデータと一緒に移動するため、中にシンボリックリンクやジャンクションが 1 つでもあると（仮想環境、git のチェックアウト、`node_modules` によく含まれます）、保存場所の変更が全体として失敗します。公開するのは通常のファイルとディレクトリだけにしてください。

公開する側に宣言は要りません。共有ファイルを公開も参照もしないなら、このセクションは書かなくてかまいません。

---

### `[plugin.ui]` — カスタム UI

```toml
[plugin.ui]
enabled = true

[[plugin.ui.panel]]
id = "main"
title = "Smart Notes"
entry = "ui/panel.tsx"
context = "dashboard"
permissions = ["state:read", "action:call"]

[[plugin.ui.guide]]
id = "quickstart"
title = "User Guide"
entry = "docs/guide.md"
permissions = ["state:read"]
```

Plugin Manager に独自の画面を出したい場合に使います。

- `panel` — ボタン、テーブル、フォームを持てるインタラクティブなパネルです。TSX で書きます。
- `guide` — 読み取り専用のドキュメントです。Markdown で書きます。

拡張子で表示方式が決まります。`.tsx` はインタラクティブパネル、`.md` はドキュメントとして扱われます。

UI が不要なら、このセクションは書かなくてかまいません。

---

### `[permissions]` — ユーザーに求める権限

```toml
[permissions.require]
"system.network:connect" = { reason = "天気サービスに問い合わせる", hosts = ["api.example.com"] }

[permissions.ask]
"plugin_sdk.main_agent.memory:read" = { reason = { ja = "普段住んでいる都市の天気を出す", en = "Use your home city for the forecast" } }
"plugin_sdk.models:invoke" = { reason = "天気を一文のお知らせにする" }
```

> **現在の状態：** 宣言は今のところ `neko-plugin check` / `check -r` でコードと比較するためだけに使われます。インストール時の `require` 権限の確認と、初回使用時の `ask` 権限の確認は今後のリリースで有効になる予定で、まだ動作していません。

- `require`：これがないとプラグインが動きません。今後、ユーザーはインストール時に同意を求められます。
- `ask`：今後、プラグインが初めて実際に使うときにユーザーに確認します。
- どの項目にも `reason` が必要です。ユーザーはこの権限の横でこれを読むので、ユーザー向けに書いてください。文字列 1 つか「言語コード = テキスト」のテーブルにできます。書かなかった言語は、書いた言語のどれかにフォールバックします。
- `ask` 項目をユーザーが使っていないときにも使う場合は、`background = true` と `background_reason` を書きます。
- `hosts`（`system.network:connect` のみ）は参考としてユーザーに表示されます。

`require` と `ask` の選び方：

- `require` の権限はバックグラウンドでの使用も含むので、`require` の項目には `background` を書きません。
- タイマーの中や起動時に使う `ask` 項目には、必ず `background = true` と `background_reason` を書きます。
- 環境変数から設定を読むこと、プラグインの環境変数を引き継ぐプログラムを起動することは、常に `system.env:read` として `require` に入れます。
- `system.network:listen` は常に `require` です。

権限 ID：

| ID | 意味 |
|---|---|
| `plugin_sdk.main_agent.memory:read` | 記憶を読む |
| `plugin_sdk.main_agent.conversations:read` | 会話を読む |
| `plugin_sdk.main_agent.messages:push` | N.E.K.O にメッセージ・カード・返信の添付を送る |
| `plugin_sdk.models:invoke` | AI モデルを呼び出す |
| `system.network:connect` | ネットワークに接続する |
| `system.network:listen` | このコンピューターでネットワークポートを開き、他のプログラムから接続できるようにする |
| `system.fs:read` / `system.fs:write` | プラグイン自身のデータディレクトリ以外のファイルを読む / 書く |
| `system.process:spawn` | 他のプログラムを起動する |
| `system.env:read` | 環境変数を読む |

`neko-plugin check` と `check -r` は、宣言とプラグインの Python コードのスキャン結果を比べます：

- コードが明らかに使っているのに宣言されていない権限は error で、`check -r`（つまりマーケットの CI）を通りません。reason 付きで宣言を追加してください。今のところ `[permissions]` テーブル自体がないプラグインには warning を出し、今後のリリースで error になります。
- スキャンで確定できない使用の可能性（レシーバーを解決できない、import のみ、プラグイン自身のデータかもしれないファイルパス）は warning です。
- 宣言されているのにスキャンで使用が見つからないものは warning です。動的な使い方なら問題ありません。
- スキャンで見通せないコード（動的 import、名前を計算する `getattr`、`eval`/`exec`、`ctypes`、スレッド、`vendor/`、Python 以外のファイル）は info として表示されます。「何も見つからない」はプラグインが無害だと証明されたという意味ではありません。

このトップレベルのテーブルは、`[[plugin.ui.panel]]` / `[[plugin.ui.guide]]` の `permissions` リストとは別物です。

#### コンポーネントごとの絞り込み

コンポーネントは、デコレーターに `permissions=[...]` を書くことで、テーブルで宣言した権限の一部だけを使うことを示せます。対象は `@plugin_entry`、`@llm_tool`、エントリフック（`@hook`、`@before_entry`、`@after_entry`、`@around_entry`、`@replace_entry`）、`@on_event`、`@custom_event`、`@timer_interval` と、それぞれの `plugin.entry` / `plugin.event` / `plugin.hook` / `plugin.timer` / `plugin.custom_event` 形式です。`register_llm_tool`、`register_dynamic_entry`、`PluginRouter.add_entry` も同じ引数を受け付けます。

```python
@llm_tool(name="weather", permissions=["plugin_sdk.main_agent.memory:read"])
async def weather(self, **_): ...
```

- 書かないのが既定です。コンポーネントは宣言した権限をすべて引き継ぎます。絞り込みは「この権限をオフにするとどの機能が使えなくなるか」をユーザーに示すためだけに使われ、コンポーネントの動作は制限しません。
- プラグインがコンポーネントを登録するとき、絞り込みは `[permissions.require]` + `[permissions.ask]` の部分集合でなければなりません。それ以外を書いたコンポーネントは登録されず（エントリは呼び出せず、イベントハンドラーは購読されません）、どのコンポーネントがなぜ登録されなかったかがプラグインのログに出ます。公開前の `neko-plugin check` も同じ問題を報告します。
- エントリフックの権限は、`target` に一致する各エントリに加わります。宣言されていない権限を書いたフックは、それらのエントリを閉じます。
  - 注意：`permissions=` を書かないフックは宣言した権限をすべて必要とするため、一致するエントリもすべてを引き継ぎます。そのようなフックが `target="*"` に 1 つでもあると、すべてのエントリの絞り込みが無効になります。エントリを絞り込む場合は、フックにも `permissions=` を書いてください。
- `PluginRouter` はクラス属性 `permissions = [...]`（または `PluginRouter(permissions=[...])`）で全エントリを絞り込めます。エントリ自身や `add_entry(..., permissions=...)` で指定したものが優先されます。組み込み済みの router で宣言されていない権限を `add_entry` に渡すと `Err(PermissionDeniedError)` が返ります。
- `@neko_plugin`、`@lifecycle`、`@message` などコンポーネント以外のデコレーターは `permissions=` を受け付けません。

---

### カスタムセクション — プラグイン固有の設定

```toml
[notes]
max_per_page = 20
auto_classify = true
```

追加の top-level section は business config として保持され、コードから読み取れます。

```python
cfg = await self.config.dump()
notes_cfg = cfg.get("notes", {})
max_per_page = notes_cfg.get("max_per_page", 20)
```

必要なだけ自由にカスタムセクションを定義できます。ただし `permissions` は使えません。トップレベルの `[permissions]` テーブルは権限宣言用に予約されています（上記参照）。すでに `[permissions]` を独自の設定に使っているプラグインはセクション名を変更してください。変更しないと `neko-plugin check` が無効な権限宣言としてエラーを報告します。

---

## このプラグインのディレクトリ構造

```text
plugin/plugins/smart_notes/
├── plugin.toml              ← 上記の設定ファイル
├── __init__.py              ← プラグインコード
├── i18n/                    ← ロケールファイル（[plugin.i18n] を設定したため）
│   ├── en.json
│   └── zh-CN.json
├── ui/                      ← インタラクティブパネル（[[plugin.ui.panel]] を設定したため）
│   └── panel.tsx
├── docs/                    ← ユーザーガイド（[[plugin.ui.guide]] を設定したため）
│   └── guide.md
```

書き込み可能な状態データは、ソースやインストール済みコードのディレクトリとは分けて保存されます。

```text
<ユーザーデータルート>/plugins/smart_notes/
├── config/plugin.toml      ← 設定のランタイム用コピー
├── data/                   ← self.data_path()
└── cache/                  ← self.cache_path()

<ユーザーデータルート>/plugin_shared/smart_notes/   ← self.shared_path()。宣言したプラグインが読む
```

必須なのは `plugin.toml` と `[plugin].entry` が指す、インポート可能な Python モジュールです。一般的には `__init__.py` を使いますが、それに限定されません。インストール済みコードは、これらの書き込み可能な状態データとは別に保存されます。

## 設定パネル用 JSON Schema

プラグインの `plugin.toml` と同じディレクトリに、任意の `config.schema.json` を置くと、汎用の「設定」タブに項目名、説明、入力型を指定できます。マニフェストへの追加設定やカスタム UI は不要です。書き込み可能な実行時設定や profile のディレクトリではなく、プラグインの配布ファイルに含めてください。パッケージの include 許可リストを使用する場合、このファイルも追加します。

`[notes]` セクションの例：

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "properties": {
    "notes": {
      "type": "object",
      "title": "ノート設定",
      "properties": {
        "max_per_page": {
          "type": "integer",
          "title": "ページあたりのノート数",
          "description": "1 ページに表示するノートの最大数。",
          "x-title-i18n": { "ja": "ページあたりのノート数", "en": "Notes per page" },
          "x-description-i18n": { "ja": "1 ページに表示するノートの最大数。", "en": "Maximum number of notes shown on a page." },
          "minimum": 1,
          "maximum": 100,
          "default": 20
        },
        "auto_classify": {
          "type": "boolean",
          "title": "自動分類",
          "description": "新しいノートを自動的に整理します。"
        },
        "sort_order": {
          "type": "string",
          "title": "並び順",
          "enum": ["newest", "oldest"]
        }
      }
    }
  }
}
```

| キーワード | フォームの動作 |
| --- | --- |
| `properties` | 実際の設定構造に対応するオブジェクトの項目。未定義の既存項目も編集できます。 |
| `additionalProperties` | オブジェクト形式のスキーマは `properties` にない動的キーに適用され、パスワード入力とプレビューのマスクにも使われます。名前付き項目が優先されます。真偽値は項目情報を提供せず、キーの追加や削除は制限しません。 |
| `title` / `description` | プレーンテキストの表示名と説明。内部キーも補助情報として表示し、名前がない場合はキーを使います。 |
| `type` | 単一の `string`、`number`、`integer`、`boolean`、`object`、`array` に対応する入力を表示します。省略時は現在値から推測します。 |
| `items` | 配列要素の子スキーマ。ネストしたオブジェクトや配列も指定できます。 |
| `enum` | 文字列、数値、真偽値の空でない一覧を選択肢にし、保存時の型を維持します。 |
| `minimum` / `maximum` | 数値入力の上下限。`integer` は整数のみを受け付けます。 |
| `maxLength` | テキスト入力の最大文字数。 |
| `readOnly` | 項目と子コントロールの編集を無効にします。 |
| 文字列項目の `writeOnly: true` | パスワード入力（一時表示可）を使い、基準値のヒント、変更の概要、JSON データ表示で空でない値をマスクします。保存には実際の値を使用します。表示のマスクであり、暗号化やアクセス制御ではありません。 |
| `default` | 項目や配列要素を明示的に追加する際の初期値。実行時設定の既定値ではありません。 |
| `x-title-i18n` / `x-description-i18n` | 任意の locale とテキストの対応表。標準の `title` と `description` は文字列のままです。 |

翻訳の優先順位は、現在の locale、基本言語、`en-US`、`en`、対応表の最初の空でない値、最後に `title` / `description` です。例は 2 言語のみですが、公開時はプラグインが対応するすべての言語を用意してください。

これはフォーム表示用であり、**完全な JSON Schema バリデーターやサーバー側の権限・設定検証ではありません**。`required`、`pattern`、スキーマ合成、`$ref`、真偽値スキーマ、型の配列、`null` 入力は非対応です。`$schema` や `$ref` の URL は取得しません。実行時の検証はプラグインが行い、既定値は `plugin.toml` / `config.example.toml` に定義してください。

ページを開いても `default` の自動挿入や profile の書き込みは行いません。スキーマにだけ存在する項目は表示され、編集後にのみ保存されます。オブジェクトのマージと配列全体の置換は従来どおりです。最上位の `plugin` セクションは保護され、profile 編集には表示されません。

ファイルは UTF-8 JSON、ルートは `"type": "object"`、最大 256 KiB、`properties` / `items` / `additionalProperties` の深さは最大 32 階層です。ファイルがない場合は従来の編集画面を使います。不正なファイルや対応キーワードの構造エラーがある場合は警告を表示し、汎用エディターに戻ります。設定 API は `config_schema` に表示情報を返し、`config` や profile には混ぜません。
