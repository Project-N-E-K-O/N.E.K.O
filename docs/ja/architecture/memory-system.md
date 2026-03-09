# メモリシステム

N.E.K.O. のメモリシステムは、セッション間で永続的なコンテキストを提供し、キャラクターが過去の会話、ユーザーの好み、進化する関係性を記憶できるようにします。

## ストレージレイヤー

| レイヤー | ストレージ | 保持期間 | アクセスパターン |
|---------|-----------|---------|-----------------|
| **最近のメモリ** | JSONファイル（`recent_*.json`） | スライディングウィンドウ（最大10メッセージ） | 直接読み取り、キャラクターごと |
| **時間インデックス付きオリジナル** | SQLite（`time_indexed_original`） | 永続 | 時間範囲クエリ |
| **時間インデックス付き圧縮版** | SQLite（`time_indexed_compressed`） | 永続 | 時間範囲クエリ |
| **セマンティックメモリ** | ベクトルEmbedding（`text-embedding-v4`） | 永続 | LLMリランキング付きハイブリッド検索 |
| **重要設定** | JSONファイル（キャラクターごと） | 永続 | LLMによる自動抽出 |

## メモリが会話に流れ込む仕組み

1. 新しいセッションが開始されると、システムは**最近のメモリ**（直近Nメッセージ）を即時コンテキストとして読み込みます。
2. **セマンティック検索**（`hybrid_search`）が、現在のトピックに基づいてオリジナル＋圧縮結果をLLMリランキングで組み合わせ、関連する過去の会話を取得します。
3. **時間インデックス付きクエリ**が、時間的な参照（「昨日」「先週」）に対する時系列コンテキストを提供します。
4. **重要設定**（キャラクターの好み、関係性、知識）が読み込まれます。
5. 取得されたすべてのメモリは、括弧除去による整形後、LLMシステムプロンプトにコンテキストとして注入されます。

## 圧縮パイプライン

最近の履歴が `max_history_length`（デフォルト：10メッセージ）を超えると、会話履歴が自動的に圧縮されます：

```
Raw conversation ──> LLM summary ("対話摘要" JSON format)
                         │
                    if > 500 chars ──> 追加圧縮
                         │
                    SystemMessage(memorandum) としてマージ
                         │
                    time_indexed_compressed に保存
```

圧縮は失敗時に3回リトライの指数バックオフを使用します。`CompressedRecentHistoryManager` が全ライフサイクルを管理します。

## 重要設定

`ImportantSettingsManager` は会話からキャラクター固有の知識を抽出・維持します：

1. **LLM Proposer**: 会話を分析して設定（特徴、関係性、好み）を抽出
2. **LLM Verifier**: 新しい設定と既存設定の間の矛盾を解決
3. **予約フィールド**は自動的に除去：`system_prompt`、`live2d`、`voice_id`、ワークショップデータ、レンダリング設定

## メモリレビュー

`review_history()` メソッドは非同期LLMベースの監査を実行して以下を検出：

- 保存されたメモリ内の矛盾とロジックエラー
- 「メモリ」として保存されたモデルのハルシネーション
- キャラクターが内部化した不正確な事実
- 会話要約内の繰り返しパターン

レビュータスクはキャラクターごとで、`cancel_correction/{lanlan_name}` で古いコンテキスト修正を防ぐためにキャンセル可能です。ユーザーは `http://localhost:48911/memory_browser` でもメモリを閲覧できます。

## APIエンドポイント

メインサーバーのエンドポイントについては [メモリREST API](/api/rest/memory) を参照してください。

メモリサーバーは内部エンドポイントを公開しています：

| エンドポイント | メソッド | 用途 |
|---------------|---------|------|
| `/cache/{lanlan_name}` | POST | 軽量追加（LLMなし） |
| `/process/{lanlan_name}` | POST | フル処理（recent + time + semantic + review） |
| `/renew/{lanlan_name}` | POST | ホットスワップリニューアル |
| `/get_recent_history/{lanlan_name}` | GET | LLMプロンプト用のフォーマット済みコンテキスト |
| `/search_for_memory/{lanlan_name}/{query}` | GET | セマンティック検索 |
| `/get_settings/{lanlan_name}` | GET | キャラクター設定JSON |
| `/new_dialog/{lanlan_name}` | GET | 新しい会話用コンテキスト（括弧除去） |
| `/reload` | POST | 全コンポーネントのリロード |
| `/cancel_correction/{lanlan_name}` | POST | レビュータスクの中断 |
