# プラグイン設定 (plugin.toml)

すべてのプラグインのルートには `plugin.toml` があります。これは N.E.K.O に「このプラグインは何か」「どう読み込むか」「どんな機能を持つか」を伝える設定ファイルです。

以下は架空の "Smart Notes" プラグインの完全な設定例です。このプラグインはノートの検索と作成、自分専用の UI、多言語対応、AI エージェントからの呼び出しに対応しています。

## 完全な例

```toml
[plugin]
id = "smart_notes"
name = "Smart Notes"
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
entry = "plugin.plugins.smart_notes:SmartNotesPlugin"
```

この 3 つは **必須** です。`id` はフォルダー名と一致させます。`entry` は Python クラスの場所をシステムに伝えます。

```toml
description = "Manage your notes: search, create, organize, with AI-powered classification."
short_description = "Note management with AI-powered organization."
keywords = ["note", "筆記", "memo", "record", "メモ"]
```

この 3 つは **AI エージェントがプラグインを見つけられるか** に影響します。

- `short_description` — AI が「このプラグインで何ができるか」を判断するために使います。短く正確に書きます。
- `keywords` — ユーザー意図のマッチに使います。ユーザーが「メモして」と言い、keywords に "memo" があればマッチしやすくなります。
- `description` — Plugin Manager に表示される、人間向けの詳しい説明です。

AI から呼び出される必要がないプラグイン、たとえば純粋なリスナーなら、これらを省略して `passive = true` を追加できます。

```toml
version = "1.2.0"
```

任意です。バージョン管理やマーケットプレイス公開で使います。

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

このプラグインがどの SDK バージョン向けに書かれているかを伝えます。ユーザーの N.E.K.O が古すぎる、または新しすぎる場合、システムは警告したり読み込みを拒否したりします。

- `supported` — この範囲外なら読み込みを拒否
- `recommended` — この範囲が最も安定
- `untested` — 読み込みは許可するが「未テスト」と警告
- `conflicts` — 明示的に互換性がないバージョン

---

### `[plugin_runtime]` — 実行方法

```toml
[plugin_runtime]
enabled = true
auto_start = true
```

- `enabled` — `false` にすると、ファイルを削除せず一時的に無効化できます
- `auto_start` — `true` なら N.E.K.O 起動時に自動開始、そうでなければパネルから手動開始します

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

### カスタムセクション — プラグイン固有の設定

```toml
[notes]
max_per_page = 20
auto_classify = true
```

フレームワークが認識しないセクションは、プラグイン固有の業務設定として扱われます。コードから読み取れます。

```python
cfg = await self.config.dump()
notes_cfg = cfg.get("notes", {})
max_per_page = notes_cfg.get("max_per_page", 20)
```

必要なだけ自由にカスタムセクションを定義できます。

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
└── data/                    ← 実行時データ（自動作成、self.data_path() が指す場所）
```

必須なのは `plugin.toml` と `__init__.py` だけです。その他のディレクトリは必要に応じて作成します。
