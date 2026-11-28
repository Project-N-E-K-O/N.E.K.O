
# Nuitka パッケージング

追跡対象のパッケージング契約は、desktop workflow とその準備・検証スクリプトです。現在のリポジトリに追跡された `build_nuitka.bat` はないため、第二の正規入口として記載しないでください。

## Python package 名

import 可能な `.py` を含むディレクトリは underscore 名を使い、`__init__.py` を含めます。hyphen を含む import package は通常の Python 命名に反し、data inclusion とも相性が悪くなります。`tests/unit/test_no_hyphen_python_packages.py` がこの規則を検査します。

import 可能な Python を `--include-data-dir` で配布しません。Nuitka は data directory から code-like suffix を除外します。package は通常どおり compile し、明確な runtime 契約を持つ interpreted／sandboxed source payload だけを raw data として含めます。

## 組み込み plugin の staging

現在の desktop workflow は次を実行します。

1. `scripts/prepare_nuitka_plugins.py prepare`
2. 生成された `build_nuitka_launcher.py` を compile
3. `scripts/prepare_nuitka_plugins.py install` で build distribution へ install
4. `scripts/check_nuitka_dist.py <dist> --plugin-stage build/nuitka-plugins` を実行

Staging script は各 plugin の `[tool.neko.build]` 規則を適用し、選択的な exclusion を生成します。全量の `--include-data-dir=plugin/plugins=plugin/plugins` や `--nofollow-import-to=plugin.plugins` を復活させないでください。どちらも別の形で staging 契約を迂回します。

## Asset と dynamic import

新しい runtime asset／dynamic import では、次の協調変更が必要になる場合があります。

- cross-platform と Linux-only workflow の Nuitka include option
- plugin payload 用 `scripts/prepare_nuitka_plugins.py`
- `scripts/check_nuitka_dist.py` の必須 asset 検証
- 対象を絞った import／package test

Embedding と tiktoken asset は workflow で別々に準備・検証されます。

## 安全な診断

パッケージ版 launcher は複数 service を起動します。親だけを終了すると、`dist/Xiao8` を保持する process が残ることがあります。起動前に静的検査を優先します。

```bash
uv run python scripts/check_nuitka_dist.py dist/Xiao8 --plugin-stage build/nuitka-plugins
uv run pytest tests/unit/test_no_hyphen_python_packages.py -q
```

パッケージ実行が必要なら、正確な artifact／revision を記録し、再ビルド前に全 child service を停止します。古いロック済み distribution を未レビューの再帰削除で直さないでください。

## ローカル Nuitka ビルド手順

デスクトップ backend は通常 CI がビルドしますが、同じビルドをローカルでも再現できます。Nuitka のオプションは `.github/workflows/build-desktop.yml`（`Build with Nuitka (Unix)` と `Build with Nuitka (Windows)` の各 step）が正であり、以下は順序だけを示します。コマンドはすべてリポジトリのルートで実行します。C コンパイラ（Windows は MSVC、それ以外は gcc または clang）が必要です。

1. 環境: `uv sync --group galgame` の後に `uv pip install --python .venv nuitka==4.2.2 ordered-set zstandard` を実行します。Nuitka のバージョンは固定されており、workflow に書かれたものに従います。Windows では先に、パッチ適用済みの wake-word runtime をビルドしてインストールします（workflow の `Build and install compatible Windows wake-word runtime` step。説明は `docs/development/wake-word-runtime.md`）。Windows のオプションが `sherpa_onnx` をバンドルするためです。
2. フロントエンド: `./build_frontend.sh`（Linux/macOS）または `.\build_frontend.bat`（Windows）。
3. オフライン asset とシード設定（workflow と同じ）: `scripts/prepare_embedding_model.py`（引数は `Prepare embedding model assets` step）、`scripts/prepare_voice_turn_assets.py`、`scripts/prepare_speaker_model.py` を実行し、tiktoken キャッシュを `data/tiktoken_cache/` にウォームアップします（コマンドは embedding model のページ）。存在しなければ、中立な `config/characters.json`、`config/core_config.json`（`{"coreApi": "free", "assistApi": "free"}`、キーは書かない）、`config/user_preferences.json`（`[]`）を作成します。個人用の `config/*.json` を含まないクリーンなチェックアウト（またはそのコピー）でビルドしてください。workflow はこの 3 ファイルをそのままパッケージに入れるため、保存済みのキーや個人データを含む既存ファイルがあると、パッケージと一緒に配布されてしまいます。実際の設定を持つチェックアウトで作ったパッケージは共有しないでください。
4. `uv run --no-sync python scripts/prepare_nuitka_plugins.py prepare` が組み込み plugin の staging を行い、`build_nuitka_launcher.py` を生成します。
5. `steamworks/` にある他プラットフォーム用のネイティブライブラリを削除してから compile します: `uv run --no-sync python -m nuitka --standalone --output-dir=dist --output-filename=projectneko_server <workflow のオプション> build_nuitka_launcher.py`。macOS では workflow が `--standalone` の代わりに `--mode=app --macos-app-mode=background` を使います。
6. 成果物の整形。Linux と Windows: `dist/build_nuitka_launcher.dist` を `dist/Xiao8` に改名し、`dist/Xiao8` を実行ディレクトリとします。macOS（`--mode=app`）: `dist/Xiao8` を作り、`dist/build_nuitka_launcher.app` を `dist/Xiao8/projectneko_server.app` へ移動し、workflow の `Rename build output` step が書く `dist/Xiao8/projectneko_server` の小さなラッパースクリプトを追加し、`dist/Xiao8/projectneko_server.app/Contents/MacOS` を実行ディレクトリとします。その後 `uv run --no-sync python scripts/prepare_nuitka_plugins.py install --destination-dir <実行ディレクトリ>/plugin/plugins` を実行し、最後に `uv run --no-sync python scripts/check_nuitka_dist.py <実行ディレクトリ> --plugin-stage build/nuitka-plugins` で検証します。

3 つの Nuitka step（`build-desktop.yml` の Unix と Windows、`build-desktop-linux.yml`）のオプションは一緒に変更します。変更がローカルのパッケージングスクリプトにも影響する場合は、PR 説明に書いてください。
