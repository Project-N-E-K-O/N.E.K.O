# Memory Server API

**既定アドレス:** `http://127.0.0.1:48912`

**設定:** `MEMORY_SERVER_PORT`

Memory Server は、メインサーバー、チャットランタイム、プロアクティブチャット、ゲーム、同梱プラグインが使用する内部のループバックサービスです。メインサーバーから汎用プロキシとして公開されず、ルートは安定したサードパーティ HTTP 契約ではありません。外部連携では、メインサーバーの公開 API を優先してください。

スタンドアロンエントリポイントは `127.0.0.1` にバインドします。ランチャーは同じ FastAPI アプリケーションを管理対象ランタイムでホストできます。ローカルのプロセスグループ内では通常の HTTP を使用します。

## 共通規則

- `{lanlan_name}` は URL エンコードされたキャラクター名です。不正な名前は `400` になります。
- 履歴書き込みエンドポイントは、`input_history` フィールド自体が JSON 文字列化されたメッセージ配列であるオブジェクトを受け取ります。

  ```json
  {
    "input_history": "[{\"role\":\"user\",\"content\":\"こんにちは\"}]"
  }
  ```

- ストレージの選択、移行、復旧中は limited mode になります。`/health`、`/shutdown`、2 つの `/internal/storage/startup/*` 制御を除くリクエストは `409` です。

  ```json
  {
    "ok": false,
    "error_code": "storage_startup_blocked",
    "blocking_reason": "...",
    "limited_mode": true,
    "error": "..."
  }
  ```

- 一部の内部ハンドラーは、`200` 応答内の `status: "error"` または `ok: false` で運用上の失敗を示します。内部呼び出し側は HTTP ステータスと本文の両方を確認する必要があります。

## ランタイムとライフサイクルのエンドポイント

| メソッド | パス | パラメーター | 応答 |
|---|---|---|---|
| `GET` | `/health` | なし | `{"app":"N.E.K.O","service":"memory","status":"ok","instance_id":"..."}` |
| `POST` | `/release_character/{lanlan_name}` | パスのみ | キャラクターの SQLite ハンドルを解放後、`{"status":"success","character_name":"..."}`。不正な名前は元の HTTP エラーステータス、それ以外の失敗は `500` |
| `POST` | `/reload` | なし | メモリコンポーネントを再構築してアトミックに交換。`{"status":"success","message":"..."}` または `status: "error"` |
| `POST` | `/shutdown` | なし | スタンドアロンプロセスで shutdown が有効なら `shutdown_signal_received`、無効なら `shutdown_disabled` |
| `POST` | `/internal/storage/startup/continue` | 任意の `{"reason":"..."}` | ストレージ準備完了後に limited mode を解除: `{"ok":true,"initialized":true|false}`。まだブロック中なら `409` |
| `POST` | `/internal/storage/startup/block` | 任意の `{"reason":"..."}` | 上流の起動失敗後に limited mode を復元: `{"ok":true,"limited_mode":true,"reason":"..."}` |
| `POST` | `/internal/memory/reset_confirmed_at` | なし | Powerful Memory の `ON` → `OFF` 移行: `{"ok":true,"count":N}` または `{"ok":false,"error":"...","count":0}` |

3 つの `/internal/*` エンドポイントは、メインプロセスと Memory Server プロセス間の制御プレーンです。ユーザー向け管理ルートとして公開しないでください。

## 会話永続化エンドポイント

以下の 4 エンドポイントは、前述の `input_history` リクエスト形式を使用します。

### `POST /cache/{lanlan_name}`

ターン終了時の軽量経路です。空でない履歴をフォアグラウンド圧縮なしで `recent.json` に追加し、生の時系列行を `time_indexed.db` に保存し、永続的なターン後シグナル処理を登録します。リクエスト中に Stage-1 の事実抽出 LLM は実行しません。

```json
{ "status": "cached", "count": 2 }
```

空のメッセージ配列は `count: 0`。失敗時は `{"status":"error","message":"..."}` です。

### `POST /process/{lanlan_name}`

会話履歴の増分を処理し、通常の最近の履歴圧縮、生の時系列行の保存、ターン後処理のスケジュール、履歴レビュータスクのゲート判定を行います。

```json
{ "status": "processed" }
```

失敗時は `{"status":"error","message":"..."}` です。

### `POST /renew/{lanlan_name}`

更新されたセッションの最初の履歴増分を処理します。キャラクター単位の settle lock を保持して詳細圧縮を行うため、`/new_dialog` が中途半端なコンテキストを読みません。残りのバックグラウンド処理は `/process` と同じです。

```json
{ "status": "processed" }
```

失敗時は `{"status":"error","message":"..."}` です。

### `POST /settle/{lanlan_name}`

すでに `/cache` で保存済みの会話を確定します。`input_history` が空の配列でも、最近の履歴の詳細な確定処理を実行します。未キャッシュのメッセージが含まれる場合は、時間インデックスへの保存とターン後処理の登録も行います。

```json
{ "status": "settled" }
```

失敗時は `{"status":"error","message":"..."}` です。

## コンテキストと想起のエンドポイント

### `GET /new_dialog/{lanlan_name}`

新しいモデルセッション用の `text/plain` コンテキストを返します。有効なキャラクターでは、persona、アクティブな pending/confirmed reflections、動的な内面コンテキスト、最近の履歴、会話間隔のヒント、祝日コンテキストをレンダリングします。同じキャラクターの `/renew` または `/settle` が進行中なら待機します。未知のキャラクターは空文字列です。

このエンドポイントは事実のセマンティック想起を実行しません。セマンティックまたは時間指定の想起は、モデルの `recall_memory` ツールが `/query_memory` を別途呼び出します。

### `GET /get_recent_history/{lanlan_name}`

ローカライズされた整形済み履歴文字列を返します。未知のキャラクターには、ローカライズされた「履歴なし」の文字列を返します。ゲーム開始前のコンテキストで使用され、`/new_dialog` の完全なプロンプトコンテキストとは異なります。

### `POST /query_memory/{lanlan_name}`

アクティブな facts、アクティブな reflections、アーカイブ済み facts を対象に、構造化された想起を行います。

```json
{
  "query": "ユーザーが好きな食べ物は？",
  "time": "2026-05-01/2026-05-07"
}
```

両方とも任意の文字列です。ルーティングは次のとおりです。

| 入力 | 動作 |
|---|---|
| `query` のみ | BM25 と任意の cosine 想起を reciprocal-rank fusion で統合 |
| `query` と有効な `time` | 時間窓でハードフィルターした後、ハイブリッドなセマンティック想起 |
| 有効な `time` のみ | 指定イベント時間窓からの距離で facts と reflections を並べる |
| どちらもなし | 空の `results` 配列を返す |
| 無効な `time` と `query` | 無効な時間窓を無視し、query のみの想起にフォールバック |

`time` は時間単位（`2026-05-01T14`）、日、月、年、または `/`・`..` で区切った 2 トークンの範囲を受け付けます。完全な ISO タイムスタンプは、その時刻を含む 1 時間に切り下げられます。

通常の応答:

```json
{
  "results": [
    {
      "id": "fact_...",
      "text": "元のメモリテキスト",
      "tier": "fact",
      "entity": "master",
      "score": 0.032787,
      "created_at": "2026-05-02T10:00:00",
      "event_start_at": "2026-05-01T00:00:00",
      "event_end_at": null
    }
  ],
  "query": "ユーザーが好きな食べ物は？",
  "candidates_total": 12,
  "elapsed_ms": 7.4
}
```

`tier` は `fact`、`reflection`、`fact_archive` のいずれかです。時間のみの結果には入力 `time` も含まれ、`score: null` になります。ランタイムで fact/reflection ストアが未初期化の場合は `503`。その他の想起エラーは、`error_code: "hybrid_recall_failed"` を含む空の成功応答へ縮退し、生の例外詳細は返しません。

ユーザー向けツールループ内のハイブリッド想起では、LLM による最終リランキングを行いません。任意のローカル embedding サービスが無効またはウォームアップ中でも、BM25 は使用できます。

### `GET /search_for_memory/{lanlan_name}/{query}` <Badge type="warning" text="非推奨" />

旧呼び出し側を壊さないためだけの互換エンドポイントです。セマンティック想起はすでに行わず、ローカライズされたプレースホルダーテキストを返します。新しいコードは `POST /query_memory/{lanlan_name}` を使用してください。

### `GET /get_settings/{lanlan_name}`

レンダリング済み persona とアクティブな reflections を整形文字列として返します。persona データが利用できない場合は、旧 settings レンダラーへフォールバックします。未知のキャラクターには空の settings 文字列を返します。

### `GET /get_persona/{lanlan_name}`

キャラクターの完全な内部 persona JSON オブジェクトを返します。現在のメモリブラウザー経路はこのルートを呼び出しておらず、内部または診断用途のために予約されています。persona スキーマはバージョン管理された内部データであり、安定した編集契約ではありません。

### `GET /last_conversation_gap/{lanlan_name}`

```json
{ "gap_seconds": 1820.5 }
```

以前の会話がない場合は `-1`。予期しない失敗は `500` と `{"gap_seconds":-1,"error":"server_error"}` を返します。

## Reflection とプロアクティブチャットのエンドポイント

### `POST /reflect/{lanlan_name}`

reflection の合成を要求し、適用される自動昇格経路をスケジュールします。現在のプロアクティブチャットは、遅延が重要な経路でこのエンドポイントを呼びません。通常のライフサイクルは、定期的なバックグラウンド合成・昇格ループが担います。

```json
{
  "reflection": null,
  "auto_transitions": 0
}
```

利用できる場合、`reflection` に合成結果が入ります。昇格は fire-and-forget のため、`auto_transitions` は常に `0` です。

### `GET /followup_topics/{lanlan_name}`

表示済みとは記録せずに、プロアクティブチャットの話題候補を返します。

```json
{ "topics": [] }
```

呼び出し側は、実際に使用した reflection ID を `/record_surfaced` に送信する必要があります。

### `POST /record_surfaced/{lanlan_name}`

```json
{ "reflection_ids": ["reflection_..."] }
```

プロアクティブチャットで表示した reflections を記録し、クールダウンを更新します。空または省略された配列は no-op です。安定した応答は `{"ok":true}`。保存失敗はログに記録されますが、呼び出し側を失敗させません。

### `POST /cancel_correction/{lanlan_name}`

信頼された手動編集の後、実行中の最近のメモリ修正をキャンセルします。

```json
{ "status": "cancelled" }
```

実行中の修正がなければ `{"status":"no_task"}` を返します。

## 証拠分析エンドポイント

### `GET /api/memory/funnel/{lanlan_name}`

**クエリパラメーター**

| 名前 | 型 | 必須 | 既定値 |
|---|---|---:|---|
| `since` | ISO 8601 日時 | いいえ | 現在時刻の 7 日前 |
| `until` | ISO 8601 日時 | いいえ | 現在時刻 |

キャラクターのイベントログから、読み取り専用の遷移件数を返します。

```json
{
  "lanlan_name": "小天",
  "since": "2026-05-01T00:00:00",
  "until": "2026-05-08T00:00:00",
  "counts": {
    "facts_added": 3,
    "reflections_synthesized": 1,
    "reflections_confirmed": 1,
    "reflections_promoted": 0,
    "reflections_merged": 0,
    "reflections_denied": 0,
    "reflections_archived": 0,
    "persona_entries_added": 0,
    "persona_entries_rewritten": 0,
    "persona_entries_archived": 0
  }
}
```

不正な日時または `since > until` は `400` です。

## ストレージバックエンド

メモリデータは `memory/<character>/` 単位で分離されます。主要ストアは次のとおりです。

| ストア | 用途 |
|---|---|
| `recent.json` と `recent_meta.json` | 作業中の最近の履歴と圧縮メタデータ |
| `time_indexed.db` / `time_indexed_original` | 生の時系列会話行とタイムスタンプ |
| `facts.json` と `facts_archive.json` | アクティブな抽出済み facts と古いアーカイブ facts |
| `reflections.json` と `reflection_archive/` | アクティブな reflection ライフサイクルと分割アーカイブ |
| `persona.json`、`persona_corrections.json`、`persona_archive/` | レンダリングされる長期 persona、修正状態、分割アーカイブ |
| `events.ndjson`、`outbox.ndjson`、`cursors.json` | 永続的な遷移ログ、再試行可能な作業キュー、バックグラウンドループのカーソル |
| その他の sidecar | 表示クールダウン、合成バックオフ、保留中の fact 重複排除、その他の復旧可能な worker 状態 |

`time_indexed_compressed` は互換用テーブルにすぎません。新しいサマリーは書き込まれず、永続的な抽象は facts、reflections、persona entries で表現されます。`retrieve_summary_by_timeframe` は非推奨で、データを返しません。

独立した embedding データベースはありません。任意のローカル CPU ONNX embeddings は、テキストとモデルのフィンガープリントとともにアクティブな項目へキャッシュされます。ベクトルは遅延ロードされ、モデル、ランタイム、互換 CPU 経路、最低 RAM のいずれかが利用できない場合は自動的に無効化されます。その場合も BM25 による想起は継続します。

## 現在のモデル tier

モデルは設定済み tier から選択され、メモリコードに provider のモデル名はハードコードされません。

| 処理 | 現在の tier またはランタイム |
|---|---|
| 任意の cosine 想起および重複排除候補用のローカルテキスト embeddings | `data/embedding_models/<profile>/` 内の CPU ONNX profile。API モデル tier は不使用 |
| 最近の履歴圧縮、事実抽出、証拠シグナル検出、reflection 合成とフィードバックチェック、fact 重複排除判断、内部 LLM recall リランキング | `summary` |
| 最近の履歴レビュー、persona 修正/精錬、reflection 精錬、reflection promotion merge | `correction` |
| ネガティブキーワードの対象分類 | `emotion` |
| ユーザー向け `POST /query_memory` の統合 | LLM tier なし。BM25 + 任意の cosine + reciprocal-rank fusion |

これらは現在の実装既定値であり、API の保証ではありません。運用者は tier ごとに、基盤モデル、base URL、認証情報を設定します。

## 主な呼び出し側

- `main_logic/cross_server.py` が `/cache`、`/process`、`/renew`、`/settle` を駆動します。
- チャットライフサイクルと同梱チャネルが `/new_dialog` を取得し、モデルのツールハンドラーが `/query_memory` を呼びます。
- プロアクティブチャットは `/followup_topics` を読み、実際に使用した reflections を `/record_surfaced` で記録します。
- キャラクター管理は `/reload` と `/release_character` を使用し、公開メモリブラウザーは最近の履歴を手動編集した後に `/cancel_correction` を使用します。
- メインサーバーが、ストレージ起動制御、shutdown、Powerful Memory の移行呼び出しを担当します。
