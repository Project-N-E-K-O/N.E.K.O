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
uv run python app/memory_server.py

# ターミナル 2 -- メインサーバー（必須）
uv run python -m app.main_server

# ターミナル 3 -- エージェントサーバー（オプション）
uv run python -m app.agent_server
```

補足:

- 本番の Steam クラウド経路を検証する場合は、Steam またはデスクトップランチャー経由で起動してください。Windows / macOS / Linux のパッケージ版とソース実行は、Steam が起動中かつログイン済みであれば RemoteStorage bundle helper を使用します。同時に、Steam 側の Auto-Cloud ルールがプラットフォームの anchor パスと一致している場合は、従来どおり生の `cloudsave/` ディレクトリも同期されます。
- 手動の 3 サーバーモードでは、必要に応じて `main_server` がフォールバックのスナップショット import を実行し、その後 `memory_server` に reload を通知しようとします。
- shutdown では実行中データを `cloudsave/` に自動で書き戻しません。Steam に新しいキャラクターデータをアップロードしたい場合は、終了前に Cloud Save Manager から対象キャラクターの staged snapshot を手動で生成または上書きしてください。
- macOS でソース実行したときに「Apple は `SteamworksPy.dylib` を検証できません」と表示される場合、通常は Gatekeeper がローカルの未公証 Steamworks ライブラリをブロックしています。まずプロジェクトのルートディレクトリから起動していることを確認してください。まだブロックされる場合は、リポジトリルートで次を実行します:

```bash
xattr -dr com.apple.quarantine steamworks/SteamworksPy.dylib steamworks/libsteam_api.dylib
codesign --force --sign - steamworks/libsteam_api.dylib
codesign --force --sign - steamworks/SteamworksPy.dylib
```

- その後、`uv run python launcher.py` または `uv run python -m app.main_server` を再実行してください。

## 設定

1. ブラウザで `http://localhost:48911/api_key` を開きます
2. Core API プロバイダーを選択します
3. API キーを入力します
4. 保存をクリックします

または、起動前に環境変数を設定します：

```bash
export NEKO_CORE_API_KEY="sk-your-key"
export NEKO_CORE_API="qwen"
uv run python -m app.main_server
```

## 代替手段: pip install

uv よりも pip を使用したい場合：

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app/memory_server.py
python -m app.main_server
```

## 確認

`http://localhost:48911` を開きます -- キャラクターインターフェースが表示されるはずです。
