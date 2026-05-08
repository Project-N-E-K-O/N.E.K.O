# 手動セットアップ

あらゆるプラットフォームでの開発とカスタマイズ向けです。

## 前提条件

- Python 3.11（厳密に -- 3.12 以降は不可）
- [uv](https://docs.astral.sh/uv/getting-started/installation/) パッケージマネージャー
- Node.js（>=20.19）
- Git

## インストール

```bash
git clone https://github.com/Project-N-E-K-O/N.E.K.O.git
cd N.E.K.O
uv sync
```

## オプション：ローカル埋め込みモデル

ベクトルメモリはオプションのローカル ONNX モデルアセットを使用します。ダウンロードコマンド、ディレクトリ構成、PyInstaller / Nuitka ビルドのパッケージング手順については
[`embedding-models.md`](embedding-models.md) を参照してください。

## フロントエンドのビルド

プロジェクトには `frontend/` 配下に2つのフロントエンドプロジェクトがあり、実行前にビルドが必要です。

**推奨** -- プロジェクトルートから一括ビルドスクリプトを使用してください。これが公式にサポートされているビルド方法です：

```bash
# Windows
build_frontend.bat

# Linux / macOS
./build_frontend.sh
```

手動で実行する場合は、スクリプトと同じコマンドを使用してください：

```bash
cd frontend/react-neko-chat && npm install && npm run build && cd ../..
cd frontend/plugin-manager && npm install && npm run build-only && cd ../..
```

## 起動

可能であれば統合ランチャーを優先してください：

```bash
uv run python launcher.py
```

この起動経路ではローカルの `cloudsave/` bootstrap とステージ済みスナップショットの適用を先に行ってからバックエンドサービスを起動するため、実際の Steam / デスクトップ版の起動経路により近くなります。

必要なサーバーを別々のターミナルで起動します：

```bash
# ターミナル 1 -- メモリサーバー（必須）
uv run python memory_server.py

# ターミナル 2 -- メインサーバー（必須）
uv run python main_server.py

# ターミナル 3 -- エージェントサーバー（オプション）
uv run python agent_server.py
```

補足:

- 本番の Steam Auto-Cloud 主経路を検証したい場合は、引き続き Steam またはデスクトップランチャー経由で起動してください。現在は Windows / macOS / Linux のソース実行でも、Steam が起動中かつログイン済みであれば RemoteStorage bundle helper を使ったクロスデバイス検証が可能ですが、この経路はあくまで開発用の互換パスであり、パッケージ版の主同期経路ではありません。
- 手動の 3 サーバーモードでは、必要に応じて `main_server` がフォールバックのスナップショット import を実行し、その後 `memory_server` に reload を通知しようとします。
- shutdown では実行中データを `cloudsave/` に自動で書き戻しません。Steam に新しいキャラクターデータをアップロードしたい場合は、終了前に Cloud Save Manager から対象キャラクターの staged snapshot を手動で生成または上書きしてください。
- macOS でソース実行したときに「Apple は `SteamworksPy.dylib` を検証できません」と表示される場合、通常は Gatekeeper がローカルの未公証 Steamworks ライブラリをブロックしています。まずプロジェクトのルートディレクトリから起動していることを確認してください。まだブロックされる場合は、リポジトリルートで次を実行します:

```bash
xattr -dr com.apple.quarantine SteamworksPy.dylib libsteam_api.dylib
codesign --force --sign - libsteam_api.dylib
codesign --force --sign - SteamworksPy.dylib
```

- その後、`uv run python launcher.py` または `uv run python main_server.py` を再実行してください。

## 設定

1. ブラウザで `http://localhost:48911/api_key` を開きます
2. Core API プロバイダーを選択します
3. API キーを入力します
4. 保存をクリックします

または、起動前に環境変数を設定します：

```bash
export NEKO_CORE_API_KEY="sk-your-key"
export NEKO_CORE_API="qwen"
uv run python main_server.py
```

## 代替手段: pip install

uv よりも pip を使用したい場合：

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python memory_server.py
python main_server.py
```

## 確認

`http://localhost:48911` を開きます -- キャラクターインターフェースが表示されるはずです。
