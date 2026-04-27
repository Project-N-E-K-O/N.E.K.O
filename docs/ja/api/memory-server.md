# Memory Server API

**ポート:** 48912（内部）

Memory Server は別プロセスとして実行され、すべての永続メモリ操作を処理します。直接の外部アクセスを意図していません — メインサーバーがメモリ関連のリクエストをプロキシします。

## 内部エンドポイント

Memory Server は以下のエンドポイントを提供します：

- タイムスタンプとエンベディング付きの新しい会話ターンの**保存**
- LLM プロンプト構築のための最近のコンテキストの**クエリ**
- 意味的に類似した過去の会話の**検索**
- 古い会話のサマリーへの**圧縮**
- メモリレビュー設定の**管理**

## ストレージバックエンド

| テーブル | 用途 |
|-------|---------|
| `time_indexed_original` | 完全な会話履歴 |
| `time_indexed_compressed` | 要約された会話履歴 |
| Embedding store | セマンティック検索用のベクトルエンベディング |

## 使用モデル

| タスク | ソース |
|------|--------|
| エンベディング | `data/embedding_models/<profile>/` 配下のバンドル ONNX モデル（`memory/embeddings.py::EmbeddingService` 参照） |
| 事実抽出 / シグナル検出 / 反省 / プロモーションマージ / 事実重複除去 / 想起リランキング | tier `summary`（`get_model_api_config('summary')`） |
| 履歴レビュー / ペルソナ訂正 | tier `correction`（`get_model_api_config('correction')`） |
| ネガティブターゲットキーワード判定 | tier `emotion` |

## 通信

メインサーバーは HTTP リクエストと永続的な同期コネクタスレッド（`cross_server.py`）を介して Memory Server と通信します。
