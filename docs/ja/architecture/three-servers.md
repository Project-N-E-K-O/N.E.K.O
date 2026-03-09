# サーバーアーキテクチャ

## メインサーバー（`main_server.py`、ポート48911）

メインサーバーは、すべてのインタラクションのユーザー向けエントリーポイントとして機能するFastAPIアプリケーションです。

### 起動シーケンス

1. **設定の読み込み** — `config_manager` の読み込み、キャラクターデータの初期化、ドキュメントディレクトリの検出
2. **セッション作成** — 定義された各キャラクターに対して `LLMSessionManager` を作成
3. **静的ファイルのマウント** — `/static`、`/user_live2d`、`/user_vrm`、`/workshop` をマウント
4. **ルーターの登録** — 全11個のAPIルーターを登録（agent、characters、config、cookies_login、live2d、memory、music、pages、system、vrm、websocket、workshop）
5. **イベントハンドラー** — Steamworksの初期化、ZeroMQブリッジの開始、音声モジュールのプリロード、言語検出、同期コネクタースレッドの開始
6. **Uvicornの起動** — `127.0.0.1:48911` にバインド

### 処理内容

- すべてのREST APIエンドポイント（11ルーター）
- リアルタイムチャット用WebSocket接続（`/ws/{lanlan_name}`）
- TTS合成（リクエスト/レスポンスキュー付きスレッドワーカー）
- 音声リサンプリング（24kHz → 48kHz、soxrステートフル `ResampleStream` 経由）
- 静的ファイル配信（モデル、CSS、JS、ロケール）
- HTMLページレンダリング（Jinja2テンプレート）
- エージェントイベントブリッジ（ZeroMQ PUB/SUB + PUSH/PULL）
- クロスサーバー同期コネクターデーモンスレッド（メモリ分析、モニター同期）

## メモリサーバー（`memory_server.py`、ポート48912）

メモリサーバーは永続的な会話履歴、セマンティック検索、キャラクター設定の抽出を管理します。

### ストレージレイヤー

| レイヤー | 用途 | バックエンド |
|---------|------|-------------|
| 最近のメモリ | キャラクターごとの直近NメッセージとLLM圧縮 | JSONファイル（`recent_*.json`） |
| 時間インデックス付きオリジナル | 完全な会話履歴 | SQLiteテーブル |
| 時間インデックス付き圧縮版 | 要約された履歴 | SQLiteテーブル |
| セマンティックメモリ | Embeddingベースの検索 | ベクトルストア |
| 重要設定 | 会話から抽出されたキャラクターの好みと知識 | JSONファイル |

### 主要な操作

- **キャッシュ**: 新しいターンの軽量追加（LLM処理なし）
- **プロセス**: フル処理パイプライン — 最近のメモリ更新、時間インデックスストレージ、セマンティックインデキシング、レビュー
- **リニューアル**: ホットスワップリニューアル — セッション移行用にコンテキストをアーカイブ
- **検索**: 全履歴に対するセマンティック類似性検索（LLMリランキング付き `hybrid_search`）
- **設定**: 会話からキャラクターの好みを抽出・検証（`ImportantSettingsManager`）
- **レビュー**: 矛盾とロジックエラーの非同期LLMベース監査（`review_history`）
- **新ダイアログ**: 新しい会話ターンのコンテキスト準備（括弧除去付き）

### RESTエンドポイント

| エンドポイント | メソッド | 用途 |
|---------------|---------|------|
| `/health` | GET | サービスヘルスチェック（N.E.K.O.シグネチャ） |
| `/cache/{lanlan_name}` | POST | 軽量追加（LLMなし） |
| `/process/{lanlan_name}` | POST | フル処理（recent + time + semantic + review） |
| `/renew/{lanlan_name}` | POST | ホットスワップリニューアル |
| `/get_recent_history/{lanlan_name}` | GET | LLMプロンプト用のフォーマット済みコンテキスト |
| `/search_for_memory/{lanlan_name}/{query}` | GET | セマンティック検索 |
| `/get_settings/{lanlan_name}` | GET | キャラクター設定JSON |
| `/new_dialog/{lanlan_name}` | GET | 新しい会話用コンテキスト（括弧除去） |
| `/reload` | POST | 全コンポーネントのリロード |
| `/cancel_correction/{lanlan_name}` | POST | レビュータスクの中断 |

## エージェントサーバー（`agent_server.py`、ポート48915）

エージェントサーバーは、会話コンテキストによってトリガーされるバックグラウンドタスクの実行を処理し、`DirectTaskExecutor` による並列能力評価を使用します。

### ZeroMQアドレッシング

| ソケット | アドレス | 方向 | 用途 |
|---------|---------|------|------|
| PUB/SUB | `tcp://127.0.0.1:48961` | Main → Agent | セッションイベント |
| PUSH/PULL | `tcp://127.0.0.1:48962` | Agent → Main | タスク結果 |
| PUSH/PULL | `tcp://127.0.0.1:48963` | Main → Agent | 分析リクエスト |

### RESTエンドポイント

| エンドポイント | メソッド | 用途 |
|---------------|---------|------|
| `/health` | GET | サービスヘルスチェック（N.E.K.O.シグネチャ） |
| `/status` | GET | エージェント状態スナップショット（タスク、フラグ、能力） |
| `/flag_set/{flag_name}/{value}` | POST | エージェント機能フラグの制御 |
| `/capabilities` | GET | 利用可能なエージェント能力 |
| `/task/spawn_computer_use` | POST | Computer Useタスクをキューに追加 |
| `/task/{task_id}` | GET | タスク状態と結果の取得 |
| `/task/{task_id}` | DELETE | タスクのキャンセル/削除 |
| `/reload_config` | POST | API設定のリロード |

### タスク実行パイプライン

1. メインサーバーがZeroMQ経由で分析リクエストをパブリッシュ
2. `DirectTaskExecutor` が全利用可能メソッドの**並列評価**を実行：
   - `_assess_mcp()` — MCPツールで処理可能か？
   - `_assess_browser_use()` — ブラウザ自動化で処理可能か？
   - `_assess_computer_use()` — デスクトップGUI自動化で処理可能か？
   - `_assess_user_plugin()` — ローカルプラグインで処理可能か？
3. 優先度ベースの選択：**MCP > Browser Use > Computer Use > User Plugin**
4. 選択されたアダプターがタスクを実行
5. 結果を分析（`analyzer.py`）し、重複を排除（`deduper.py`）
6. 最終結果をZeroMQ経由でストリーミング返却（`task_result`、`proactive_message`）

## モニターサーバー（`monitor.py`、ポート48913）

モニターサーバーは、ランチャーおよびプロセス間連携のためのヘルスチェックと設定エンドポイントを提供する軽量FastAPIサービスです。

### 用途

- **ヘルスモニタリング**: ランチャーがサービス状態を確認できるよう、N.E.K.O.シグネチャ付きのヘルスチェックエンドポイントを提供
- **設定リレー**: 他のプロセス向けに現在の設定状態を公開
- **ランチャー連携**: 起動順序の管理、ポートフォールバック、グレースフルシャットダウンを実現

### スタートアップロック

ランチャーはプラットフォーム固有のメカニズムを使用して重複インスタンスを防止：
- **Windows**: Named Mutex `Global\NEKO_LAUNCHER_STARTUP_LOCK`
- **POSIX**: システム一時ディレクトリ内のファイルロック
