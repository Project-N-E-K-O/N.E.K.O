# Nuitka パッケージングの注意点

N.E.K.O は Python バックエンドを Nuitka standalone で単一 exe にコンパイルし、
Electron でパッケージ配布しています。Nuitka には**デフォルト挙動**で踏みやすい罠が
いくつかあるため、**新しいディレクトリや動的 import を追加する前に必読**です。

## ルール 1: `.py` を含むディレクトリ名はアンダースコアを使う

Python パッケージ名にハイフンは使えないため、
`--include-package=plugin.my-tool.public` は Nuitka に拒否されます。代替手段の
`--include-data-dir=plugin/my-tool` も罠で、Nuitka の `--include-data-dir` は
**`.py`、`.pyc`、`.pyd`、`.so`、`.dll` などのコード拡張子をデフォルトでフィルタ**します
（インストール済み Nuitka の `nuitka/freezer/IncludedDataFiles.py` 内
`default_ignored_suffixes` タプルを参照——これは上流のデフォルト挙動であり、プロジェクト設定ではありません）。
バンドル後の dist には `.md` / `.json` などの非コードファイルしか残らず、
ランタイム import が直接 `ModuleNotFoundError` を出します。ビルド自体は成功するため、
影響を受けた機能をユーザーが開くまで気づきません。

**実際のバグ**: `plugin/neko-plugin-cli/` は歴史的に `public/` という Python パッケージを抱えていました。
サーバー側の呼び出し元は `sys.path.insert(_CLI_ROOT)` の後に `from public import ...` をしていました。
ソースモードでは動作しましたが、Nuitka standalone では `public/` パッケージ全体が静かに落とされ、
embedded user plugin server が起動せず、plugin 管理 UI に到達できなくなりました。

**正しい書き方**: `.py` ソースを含むディレクトリはすべてアンダースコア命名 + `__init__.py` を含むこと。
外部 CLI ツール用にハイフン付きの製品名が必要なら `pyproject.toml [project.scripts]` でマッピング；
内部の Python パッケージ名はアンダースコアを保ちます。

`tests/unit/test_no_hyphen_python_packages.py` が PR 段階で違反を自動的にブロックします。

## ルール 2: `--include-data-dir=` で `.py` を運ばない

`.py` ソースを非コンパイル形式でバンドルする必要が本当にある場合（稀。たとえば
ランタイムプラグイン sandbox）は、デフォルトの拡張子フィルタをスキップする
`--include-raw-dir=` を使います。それ以外は `--include-package=<dotted.name>` を優先し、
Nuitka にモジュールをバイナリへコンパイルさせます。

## ルール 3: 新規ディレクトリは build script + CI を同期更新

独立したビルド構成が 2 つ存在します:

- `build_nuitka.bat` — メンテナーローカルスクリプト、**gitignored**
  （署名パス、マシン固有設定を含む）。
- `.github/workflows/build-desktop.yml` — Linux/macOS/Windows リリース artifact 用 CI ビルド。

バンドルに含めるディレクトリを追加する場合、**両方を同期更新**する必要があります。
Nuitka ビルド後、CI は `scripts/check_nuitka_dist.py` を実行して重要な資産の存在を検証します;
必須資産を増やす場合はそのスクリプトにも登録してください。

## ルール 4: 診断目的でバンドル後の exe を気軽に起動しない

launcher は複数のサブプロセス（`memory_server`、`agent_server`、`main_server`、
plugin server 等）を起動します。launcher だけを kill してもサブプロセスが残ってファイルロックを
保持します。次回ビルドの `rmdir /s /q dist\Xiao8` は部分的に失敗し、続く
`move dist\launcher.dist dist\Xiao8` が新ビルドを残骸ディレクトリの**中**に落とし——
ブートはするが config/static/templates が欠落している、半壊のネスト bundle を産み出します。

パッケージング問題の診断には次を優先してください:

- `scripts/check_nuitka_dist.py dist/Xiao8` で資産インベントリを取る
- `grep -r <symbol> dist/Xiao8/` で内容を確認する
- どうしても exe を起動する必要があるなら、事後に `projectneko_server`、`neko_main_server`、
  `neko_memory_server`、`neko_agent_server` プロセスをすべて**明示的に kill** してください

## 多層防御

歴史的な neko-plugin-cli バグ（PR #1115、"rename neko-plugin-cli → neko_plugin_cli"）は
何の警告もなく数週間プロダクションに残りました。現在は 3 層の防御があります:

1. **ビルド時チェック** — `scripts/check_nuitka_dist.py` が CI の Nuitka 直後に走り、
   dist ルートを巡回して各重要ディレクトリの存在および各組み込みプラグインの `plugin.toml` を検証します。
2. **ソースレベル lint** — `tests/unit/test_no_hyphen_python_packages.py` が PR 段階で
   失敗します。tracked なハイフン付きディレクトリに `.py` が含まれていれば即検出。
3. **本ドキュメント** — パッケージング関連コードを追加する前に必読。
