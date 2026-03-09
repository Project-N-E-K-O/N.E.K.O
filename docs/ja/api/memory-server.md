# Memory Server API

**ポート:** 48912（内部）

Memory Server は別プロセスとして実行され、すべての永続メモリ操作を処理します。直接の外部アクセスを意図していません — メインサーバーがメモリ関連のリクエストをプロキシします。

## 内部エンドポイント

Memory Server は以下のエンドポイントを提供します：

| エンドポイント | メソッド | 用途 |
|----------|--------|---------|
| `/health` | GET | N.E.K.O. シグネチャ付きヘルスチェック |
| `/query` | POST | プロンプト構築のための最近＋セマンティックメモリの取得 |
| `/store` | POST | タイムスタンプとエンベディング付き新規会話ターンの保存 |
| `/compress` | POST | 古い会話のサマリーへの圧縮 |
| `/recent` | GET | 最近の会話メッセージの取得（最大10件） |
| `/search` | POST | 過去の会話のセマンティック類似検索 |
| `/review` | GET | メモリ修正のレビュー履歴の取得 |
| `/review` | POST | メモリ修正または `cancel_correction` の送信 |
| `/settings` | GET | LLM が抽出した重要設定の取得 |
| `/settings` | POST | 重要設定の更新または再生成 |

## ストレージバックエンド

| レイヤー | バックエンド | 用途 |
|---------|----------|---------|
| Recent | JSON ファイル | 最新10件のメッセージ、高速コンテキスト取得 |
| Time-indexed (original) | SQLite | タイムスタンプ付き完全な会話履歴 |
| Time-indexed (compressed) | SQLite | 要約された古い会話 |
| Semantic | ベクトルストア | 類似検索用のエンベディング |
| Important Settings | JSON ファイル | LLM が抽出したユーザー設定と事実 |

## 主要コンポーネント

| コンポーネント | モジュール | 役割 |
|-------------|--------|------|
| `RecentMemory` | `memory/recent.py` | JSON ベースの最近メッセージバッファ（最大10件） |
| `TimeIndexedMemory` | `memory/timeindex.py` | デュアル SQLite のオリジナル＋圧縮ストア |
| `SemanticMemory` | `memory/semantic.py` | エンベディング＋リランカーによる類似検索 |
| `ImportantSettingsManager` | `memory/settings.py` | LLM Proposer→Verifier によるユーザー設定抽出 |
| `MemoryRouter` | `memory/router.py` | 全エンドポイントを公開する FastAPI ルーター |

## 使用モデル

| タスク | デフォルトモデル |
|------|---------------|
| エンベディング | `text-embedding-v4` |
| 要約 | `qwen-plus` (SUMMARY_MODEL) |
| ルーティング | `qwen-plus` (ROUTER_MODEL) |
| リランキング | `qwen-plus` (RERANKER_MODEL) |

## 通信

メインサーバーは HTTP リクエストと永続的な同期コネクタスレッド（`cross_server.py`）を介して Memory Server と通信します。メモリクエリはプロンプト構築時に発行され、最近の会話、セマンティックに関連する過去のやり取り、およびユーザー設定を含むコンテキストウィンドウを構築します。
