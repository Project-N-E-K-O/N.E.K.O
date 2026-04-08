# 開発環境セットアップ

## リポジトリのクローン

```bash
git clone https://github.com/Project-N-E-K-O/N.E.K.O.git
cd N.E.K.O
```

## 依存関係のインストール

```bash
uv sync
```

これにより、すべてのPython依存関係がマネージド仮想環境にインストールされます。プロジェクトにはPython 3.11が必要です。

## サーバーの起動

N.E.K.O. は複数の協調するサーバーとして動作します。最低限、**メインサーバー**と**メモリサーバー**が必要です：

```bash
# ターミナル1 — メモリサーバー
uv run python memory_server.py

# ターミナル2 — メインサーバー
uv run python main_server.py
```

オプションで、バックグラウンドタスク実行用のエージェントサーバーを起動できます：

```bash
# ターミナル3 — エージェントサーバー（オプション）
uv run python agent_server.py
```

## APIキーの設定

メインサーバーが起動したら、Web UIを開いてAPIキーを設定します：

```
http://localhost:48911/api_key
```

お好みのCore APIプロバイダーを選択し、APIキーを入力してください。各プロバイダーの詳細は[APIプロバイダー](/config/api-providers)を参照してください。

## セットアップの確認

メインインターフェースを開きます：

```
http://localhost:48911
```

Live2Dモデルを含むキャラクターインターフェースが表示されるはずです。テキストメッセージを送信するか、音声セッションを開始して、すべてが正常に動作することを確認してください。

## デフォルトポート

| サーバー | ポート | 用途 |
|---------|--------|------|
| メインサーバー | 48911 | Web UI、REST API、WebSocket |
| メモリサーバー | 48912 | メモリの保存と検索 |
| モニターサーバー | 48913 | ステータス監視 |
| エージェント/ツールサーバー | 48915 | エージェントタスクの実行 |
| プラグインサーバー | 48916 | ユーザープラグイン |

## フロントエンドプロジェクトのビルド

プロジェクトには `frontend/` 配下に2つのモダンフロントエンドプロジェクトがあります。アプリケーションを完全に実行するには、両方をビルドする必要があります。

### 一括ビルド

```bash
# Windows
build_frontend.bat

# Linux / macOS
./build_frontend.sh
```

### チャットウィンドウ（React）

```bash
cd frontend/react-neko-chat
npm install
npm run dev          # 開発サーバー（ポート5174）
npm run build        # 本番ビルド → static/react/neko-chat/
```

チャットウィンドウはIIFEライブラリ（`NekoChatWindow`）としてビルドされ、`templates/index.html` に埋め込まれます。

### プラグインマネージャー（Vue）

```bash
cd frontend/plugin-manager
npm install
npm run dev          # 開発サーバー（ポート5173、APIをlocalhost:48916にプロキシ）
npm run build-only   # 本番ビルド → frontend/plugin-manager/dist/
```

プラグインマネージャーダッシュボードは、プラグインサーバー（ポート48916）の `/ui/` で配信されます。

## テストの実行

```bash
uv run pytest
```

テストスイートの構造と特定のテストカテゴリの実行方法については、`tests/README.md` を参照してください。
