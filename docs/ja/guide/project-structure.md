# プロジェクト構成

```
N.E.K.O/
├── main_server.py              # メインサーバーエントリーポイント（ポート48911）
├── memory_server.py            # メモリサーバーエントリーポイント（ポート48912）
├── agent_server.py             # エージェントサーバーエントリーポイント（ポート48915）
├── launcher.py                 # デスクトップランチャー（Steam/exe）
├── monitor.py                  # モニターサービス
│
├── brain/                      # エージェント＆タスク実行
│   ├── task_executor.py        # メインタスク実行エンジン
│   ├── computer_use.py         # コンピュータビジョン/インタラクション
│   ├── browser_use_adapter.py  # ブラウザ自動化アダプター
│   ├── mcp_client.py           # Model Context Protocolクライアント
│   ├── planner.py              # タスク計画＆分解
│   ├── analyzer.py             # 結果分析
│   ├── deduper.py              # 重複検出
│   ├── processor.py            # タスク処理パイプライン
│   └── agent_session.py        # エージェントセッション管理
│
├── config/                     # 設定
│   ├── __init__.py             # 定数、デフォルト値、ポート定義
│   ├── api_providers.json      # APIプロバイダープロファイル
│   ├── prompts_sys.py          # システムプロンプト（感情、プロアクティブチャット）
│   └── prompts_chara.py        # キャラクターシステムプロンプト
│
├── main_logic/                 # コアビジネスロジック
│   ├── core.py                 # LLMSessionManager（中央セッションハンドラー）
│   ├── omni_realtime_client.py # Realtime API WebSocketクライアント
│   ├── omni_offline_client.py  # テキスト/レスポンスAPIクライアント（オフラインフォールバック）
│   ├── tts_client.py           # TTSエンジンアダプター（CosyVoice、GPT-SoVITS）
│   ├── cross_server.py         # サーバー間通信
│   └── agent_event_bus.py      # ZeroMQイベントブリッジ（Main ↔ Agent）
│
├── main_routers/               # FastAPIルートハンドラー
│   ├── websocket_router.py     # WebSocket /ws/{lanlan_name}
│   ├── characters_router.py    # /api/characters/*
│   ├── config_router.py        # /api/config/*
│   ├── live2d_router.py        # /api/live2d/*
│   ├── vrm_router.py           # /api/model/vrm/*
│   ├── memory_router.py        # /api/memory/*
│   ├── agent_router.py         # /api/agent/*
│   ├── workshop_router.py      # /api/steam/workshop/*
│   ├── system_router.py        # /api/*（その他のシステムエンドポイント）
│   ├── pages_router.py         # HTMLページ配信
│   └── shared_state.py         # ルーター間で共有されるグローバル状態
│
├── memory/                     # メモリ管理
│   └── store/                  # メモリデータストレージ（SQLite）
│
├── plugin/                     # プラグインシステム
│   ├── sdk/                    # プラグインSDK（ベースクラス、デコレーター）
│   │   ├── base.py             # NekoPluginBase
│   │   └── decorators.py       # @neko_plugin、@plugin_entry など
│   └── plugins/                # ユーザープラグインディレクトリ
│
├── utils/                      # ユーティリティモジュール
│   ├── config_manager.py       # 一元化された設定管理（1500行以上）
│   ├── language_utils.py       # i18n、言語検出、翻訳
│   ├── audio_processor.py      # 音声リサンプリング、ノイズ除去
│   ├── frontend_utils.py       # モデル検出、テキストユーティリティ
│   ├── api_config_loader.py    # APIプロバイダー解決
│   ├── logger_config.py        # レート制限付きロギングセットアップ
│   ├── translation_service.py  # LLMベースの翻訳
│   ├── workshop_utils.py       # Steam Workshopヘルパー
│   ├── web_scraper.py          # Webコンテンツスクレイピング＆フィルタリング
│   └── screenshot_utils.py     # ビジョンAPI用スクリーンショット処理
│
├── static/                     # フロントエンドアセット
│   ├── app.js                  # メインアプリケーションJS
│   ├── theme-manager.js        # ダーク/ライトモード
│   ├── css/                    # スタイルシート
│   ├── js/                     # 機能別JSモジュール
│   ├── locales/                # i18n JSONファイル（en、zh-CN、zh-TW、ja、ko）
│   └── live2d-ui-*.js          # Live2D UIコンポーネント
│
├── templates/                  # Jinja2 HTMLテンプレート
│   ├── index.html              # メインインターフェース
│   ├── chara_manager.html      # キャラクター管理
│   ├── api_key_settings.html   # APIキー設定
│   └── ...                     # その他のページテンプレート
│
├── docker/                     # Dockerデプロイメントファイル
│   ├── Dockerfile
│   ├── docker-compose.yml
│   ├── entrypoint.sh
│   └── CONFIG_REFERENCE.md     # 設定リファレンス
│
├── tests/                      # テストスイート
│   ├── unit/                   # ユニットテスト
│   ├── frontend/               # フロントエンド統合テスト（Playwright）
│   ├── e2e/                    # エンドツーエンドテスト
│   └── utils/                  # テストユーティリティ
│
├── pyproject.toml              # プロジェクトメタデータ＆依存関係
└── requirements.txt            # 固定された依存関係リスト
```

## 主要ファイル

| ファイル | 行数 | 役割 |
|---------|------|------|
| `main_logic/core.py` | 約2300 | 中央セッションマネージャー — システムの心臓部 |
| `utils/config_manager.py` | 約1500 | 設定の読み込み、検証、永続化 |
| `main_logic/tts_client.py` | 約2300 | マルチプロバイダー対応TTS合成 |
| `brain/task_executor.py` | 約1600 | エージェントのタスク計画と実行 |
| `utils/web_scraper.py` | 約1900 | プロアクティブチャット用Webコンテンツスクレイピング |
