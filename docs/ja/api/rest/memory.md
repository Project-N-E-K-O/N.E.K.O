# Memory API

**プレフィックス:** `/api/memory`

これは、メモリブラウザーおよび設定画面向けにメインサーバーが提供する API です。最近のメモリの編集、メモリ機能の切り替え、キャラクターメモリの名前変更、ローカルの反復表現インサイト、口癖回避リスト管理、外部メモリインポート、ユーザーが明示的に開始する旧ストレージのクリーンアップを扱います。プロセス内部の [Memory Server API](/ja/api/memory-server) を汎用的にプロキシするものではありません。

すべてのルートは末尾のスラッシュなしで定義されています。クラウドストレージがメンテナンス中または読み取り専用の場合、書き込み操作は `409` を返すことがあります。

## エンドポイント一覧

| メソッド | パス | 用途 |
|---|---|---|
| `GET` | `/api/memory/recent_files` | 最近のメモリの論理ファイル名を一覧表示 |
| `GET` | `/api/memory/recent_file` | 最近のメモリファイルを 1 件読み取り |
| `POST` | `/api/memory/recent_file/save` | 1 キャラクター分の最近の履歴を置換 |
| `POST` | `/api/memory/update_catgirl_name` | 完全なキャラクター名変更トランザクションの互換エイリアス |
| `GET` | `/api/memory/review_config` | 最近のメモリの自動レビュー設定を取得 |
| `POST` | `/api/memory/review_config` | 最近のメモリの自動レビュー設定を更新 |
| `GET` | `/api/memory/powerful_memory_config` | Powerful Memory 設定を取得 |
| `POST` | `/api/memory/powerful_memory_config` | Powerful Memory 設定を更新し、必要な移行を実行 |
| `GET` | `/api/memory/insight_characters` | 反復表現インサイト分析の対象キャラクター一覧を取得 |
| `POST` | `/api/memory/repetition_insights` | 保存済みアシスタント発話に対するローカル専用の反復表現分析を実行 |
| `POST` | `/api/memory/repetition_effects/reset` | 1 キャラクター分のローカル反復抑止効果集計をリセット |
| `GET` | `/api/memory/habitual_phrases` | キャラクターの口癖回避リストとパネル状態を取得 |
| `POST` | `/api/memory/habitual_phrases/override` | 口癖フレーズ 1 件の許可・抑制・解除のユーザー上書きを保存 |
| `POST` | `/api/memory/habitual_phrases/recompute` | キャラクターの口癖回避リストを今すぐ再計算 |
| `POST` | `/api/memory/external_import/preview` | 書き込みを行わずに OpenClaw / Hermes / Molili のインポートをプレビュー |
| `POST` | `/api/memory/external_import/commit` | プレビュー済みの外部メモリをキャラクターのストアへ統合保存 |
| `GET` | `/api/memory/external_import/progress` | キャラクターの外部インポート進捗カウンターを取得 |
| `GET` | `/api/memory/farewell-summary` | デスクトップ版の終了時お別れカード用の読み取り専用メモリ概要 |
| `GET` | `/api/memory/legacy/scan` | ユーザーに提示できる旧メモリルートを読み取り専用で走査 |
| `POST` | `/api/memory/legacy/purge` | 走査済みの旧ルートから明示的に選択された項目を削除 |

## 最近のメモリファイル

ブラウザー API は互換性のため、論理ファイル名 `recent_<character>.json` を維持しています。現在のストレージでは `memory/<character>/recent.json` に解決され、移行中は旧形式のフラットファイルも読み取れます。

### `GET /api/memory/recent_files`

パラメーターはありません。

アクティブなメモリルートとプロジェクトメモリルートを検索し、論理ファイル名を重複排除してソート順で返します。

```json
{
  "files": ["recent_小天.json", "recent_小夜.json"]
}
```

### `GET /api/memory/recent_file`

**クエリパラメーター**

| 名前 | 型 | 必須 | 説明 |
|---|---|---:|---|
| `filename` | 文字列 | はい | `recent_小天.json` などの論理ファイル名。パス区切り文字と `..` は拒否されます |

`content` はファイルの UTF-8 JSON テキストであり、解析済みのメッセージ配列ではありません。

```json
{
  "content": "[{\"type\":\"human\",\"data\":{...}}]",
  "fingerprint": "b4c1000000000000000000000000000000000000000000000000000000000000",
  "identity_token": "a7e3111111111111111111111111111111111111111111111111111111111111"
}
```

`fingerprint` と `identity_token` は不透明なスナップショットトークンです。クライアントは編集時に両方を保持し、対応する保存リクエストでそのまま返す必要があります。

エラーは `{"success": false, "error": "..."}` 形式です。無効なファイル名は `400`、論理ファイルを解決できない場合は `404` です。

### `POST /api/memory/recent_file/save`

選択したキャラクターの最近の履歴を置換し、そのキャラクターで実行中のレビューをキャンセルして、手動編集を反映させます。

**リクエストボディ**

```json
{
  "filename": "recent_小天.json",
  "fingerprint": "b4c1000000000000000000000000000000000000000000000000000000000000",
  "identity_token": "a7e3111111111111111111111111111111111111111111111111111111111111",
  "chat": [
    { "role": "human", "text": "こんにちは！" },
    { "role": "ai", "text": "こんばんは！" }
  ]
}
```

| フィールド | 型 | 必須 | 説明 |
|---|---|---:|---|
| `filename` | 文字列 | はい | `recent_<character>.json` と一致する必要があります。キャラクター名はこの値から導出されます |
| `fingerprint` | 文字列 | はい | この編集を読み込んだ GET が返す不透明なコンテンツトークン |
| `identity_token` | 文字列 | はい | 同じ GET が返す不透明なファイル識別トークン |
| `chat` | 配列 | はい | 置換後の履歴。最大 10,000 件 |
| `chat[].role` | 文字列 | はい | 保存するメッセージ種別。通常は `human`、`ai`、`system` |
| `chat[].text` | 文字列 | いいえ | メッセージ本文。既定値は空文字列 |
| `chat[].source_index` | 整数 | いいえ | GET が返した配列でのこの項目の位置。配置にのみ使用 |
| `chat[].theater` | 真偽値 | いいえ | 小劇場記憶メッセージを表す項目であることを示す。通常テキストとして保存されることはない |

メッセージ 1 件のテキスト上限は 32,768 文字、リクエスト全体のメッセージテキスト合計は 2,097,152 文字です。チャット項目の未知のフィールドは保存されません。

小劇場記憶メッセージ（小劇場エピソードカプセル）はここでは読み取り専用です。送信された `chat` の内容にかかわらず、現在のスナップショット内の小劇場メッセージはメタデータを含めて元の位置にそのまま保持され、置き換わるのは通常メッセージだけです。`source_index` を持たないクライアントが保存済みメッセージと一致しない項目を送り、かつ一致しない小劇場メッセージもある場合、保存は `409`（`code: "RECENT_FILE_THEATER_READONLY"`）で拒否されます。

成功時:

```json
{
  "success": true,
  "need_refresh": true,
  "catgirl_name": "小天",
  "fingerprint": "d8f2222222222222222222222222222222222222222222222222222222222222",
  "identity_token": "a7e3111111111111111111111111111111111111111111111111111111111111"
}
```

検証エラーは `400` と `success: false` を返します。スナップショットトークンがない場合、またはファイルの内容や識別情報が並行更新された場合は `409` となり、競合レスポンスには `code: "RECENT_FILE_CONFLICT"` と現在のトークンが含まれます。競合時はファイルを再度 GET し、ユーザーの編集をマージしてから保存してください。返された新しいトークンだけを使って古い `chat` を再送してはいけません。成功レスポンスは次回編集用のトークンを返します。ストレージエラーは `success: false` と `error` を返し、クラウドストレージのメンテナンスも `409` です。

## キャラクターメモリの名前変更

### `POST /api/memory/update_catgirl_name`

正規の `POST /api/characters/catgirl/{old_name}/rename` トランザクションに対する互換エイリアスです。メモリのレビュー/圧縮タスクを排出し、キャラクター名を公開して、すべてのメモリとカード状態を移行し、memory server を再読み込みします。失敗時にはロールバックします。正規トランザクションの完了後に旧クライアントが重複して呼び出した場合は、べき等な no-op として成功します。

```json
{
  "old_name": "旧名",
  "new_name": "新名"
}
```

両フィールドとも必須の文字列です。過去の `old_name` ではピリオドを許可します。`new_name` には現在のキャラクター名規則が適用され、予約済みルート名は使用できません。

```json
{
  "success": true,
  "memory_renamed": true,
  "memory_server_reloaded": true
}
```

べき等な重複呼び出しは `changed: false`、`already_renamed: true`、`exists_after` を返します。この no-op が許可されるのは、設定に新しい名前がすでに存在し、古いストレージが消えている場合だけです。設定が公開済みでも古いストレージが残っている場合は、部分的な名前変更を隠さず `409` を返すため、正規のキャラクター管理エンドポイントで修復してください。名前の欠落または不正は `400`。ストレージが書き込み不可の場合も `409` になることがあります。

## メモリ機能の切り替え

### `GET /api/memory/review_config`

最近のメモリの自動レビューと修正が有効かを返します。設定がない場合の既定値は `true` です。

```json
{ "enabled": true }
```

読み取りは進行中のメモリ設定の保存完了を待ち、保存済みの値を返します。ファイルまたは設定キーがない場合は `true` が既定値です。ファイルを読み取れない、JSON が壊れている、設定がオブジェクトではない、または値が真偽値ではない場合は、HTTP `503` と `{"error": "..."}` を返し、`enabled` は返しません。この設定値だけが無効な場合、応答に `"code": "invalid_memory_setting"` も含まれ、画面でオンかオフを明示的に選んで修復できます。有効な真偽値の POST は他のフィールドを保持してこの設定を修復できます。

### `POST /api/memory/review_config`

ボディは必須の真偽値フィールド `enabled` を含む JSON オブジェクトである必要があります。不正な JSON、フィールドの欠落、型の不一致は HTTP `400` と `{"success": false, "error": "..."}` を返します。読み取りや保存の失敗は `success: false` を返し、読み取れない、JSON が不正、またはルートがオブジェクトではない設定ファイルを新しい設定で上書きしません。ファイルが存在しない場合のみ新規作成します。2 つのメモリ設定の更新は、必要な移行とファイル保存が完了するまで直列化されます。強力メモリを `false` に保存する際、既存値が真偽値の `false` である場合のみ移行を省略します。無効な旧値は不明な状態として扱います。

```json
{ "enabled": false }
```

このルートは `core_config.json` の `recent_memory_auto_review` を保存します。

```json
{ "success": true, "enabled": false }
```

失敗時は `{"success": false, "error": "..."}`。ストレージメンテナンス中は `409` になることがあります。

### `GET /api/memory/powerful_memory_config`

`powerful_memory_enabled` 設定を返します。明示的な設定がない既存環境では `true` が既定値です。

```json
{ "enabled": true }
```

読み取りは進行中のメモリ設定の保存完了を待ち、保存済みの値を返します。ファイルまたは設定キーがない場合は `true` が既定値です。ファイルを読み取れない、JSON が壊れている、設定がオブジェクトではない、または値が真偽値ではない場合は、HTTP `503` と `{"error": "..."}` を返し、`enabled` は返しません。この設定値だけが無効な場合、応答に `"code": "invalid_memory_setting"` も含まれ、画面でオンかオフを明示的に選んで修復できます。有効な真偽値の POST は他のフィールドを保持してこの設定を修復できます。

### `POST /api/memory/powerful_memory_config`

ボディは必須の真偽値フィールド `enabled` を含む JSON オブジェクトである必要があります。不正な JSON、フィールドの欠落、型の不一致は HTTP `400` と `{"success": false, "error": "..."}` を返します。読み取りや保存の失敗は `success: false` を返し、読み取れない、JSON が不正、またはルートがオブジェクトではない設定ファイルを新しい設定で上書きしません。ファイルが存在しない場合のみ新規作成します。2 つのメモリ設定の更新は、必要な移行とファイル保存が完了するまで直列化されます。強力メモリを `false` に保存する際、既存値が真偽値の `false` である場合のみ移行を省略します。無効な旧値は不明な状態として扱います。

```json
{ "enabled": false }
```

Powerful Memory は、シグナル分析、昇格時のマージ、反証チェック、ネガティブ対象チェック、事実の重複排除、persona 修正など、証拠駆動の LLM 経路を制御します。Powerful Memory が無効でも、軽量フィードバック経路は利用できます。

`ON` から `OFF` への変更では、まず Memory Server プロセスに confirmed reflection の経過時間アンカーをリセットさせます。これにより、古い confirmed 項目が時間駆動フォールバックで直ちに昇格することを防ぎます。移行に成功した場合のみ設定を保存します。

成功時:

```json
{ "success": true, "enabled": false }
```

移行または保存の失敗:

```json
{ "success": false, "error": "migration HTTP 409" }
```

## 反復表現インサイトと抑止効果

### `GET /api/memory/insight_characters`

パラメーターはありません。

`POST /api/memory/repetition_insights` が受け付けるキャラクター名をソート順で返します。設定済みキャラクターに加え、シンボリックリンクや旧ベクターストアディレクトリを除いた実キャラクターディレクトリおよび最近のメモリファイルから構築されます。

```json
{
  "characters": ["小天", "小夜"]
}
```

### `POST /api/memory/repetition_insights`

LLM 呼び出しや外部送信を行わず、保存済みアシスタント発話をローカルのみで分析します。Memory Server の `/internal/memory/{character_name}/repetition_insights` へ転送し、内部用の `_anti_repeat_response_ids` を除去したうえで、ローカルの抑止効果 `effectiveness` と集約済み `associations` を付与して返します。

**リクエストボディ**

| フィールド | 型 | 必須 | 既定値 | 説明 |
|---|---|---:|---|---|
| `character_name` | 文字列 | はい | — | 対象キャラクター名 |
| `language` | 文字列 | はい | — | `en`、`es`、`pt`、`ru`、`ja`、`ko`、`zh-CN`、`zh-TW` のいずれか |
| `assistant_message_limit` | 整数 | いいえ | `100` | 分析対象の直近アシスタント発話件数（`3`–`100`） |
| `effect_days` | 整数 | いいえ | `30` | フォールバック効果集計日数（`7`、`30`、`90`。旧クライアント互換用） |

成功時は `200` と `success: true`、`schema_version`、`artifact_type`、`character_name`、`language`、`parameters`、`summary`、`candidates`、`effectiveness`、`associations` を返します。不正なキャラクター名、非対応言語、マイナーエラーは `422`、未登録キャラクターは `404`、Memory Server 利用不可または書き込みフェンス中は `503` です。

### `POST /api/memory/repetition_effects/reset`

既存キャラクター 1 体のローカル反復抑止効果の集計だけを消去します。

**リクエストボディ**

```json
{
  "character_name": "小天"
}
```

成功時は `{"success": true, "character_name": "小天", "cleared": true}` を返します。不正な名前は `422`、キャラクター未検出は `404`、効果ストア利用不可は `503` です。

## 口癖回避リスト

### `GET /api/memory/habitual_phrases`

次回セッション開始時に適用されるキャラクターの口癖回避リストパネル状態を返します。

**クエリパラメーター**

| 名前 | 型 | 必須 | 説明 |
|---|---|---:|---|
| `character_name` | 文字列 | はい | 対象キャラクター名 |
| `ui_language` | 文字列 | いいえ | 永続化されたキャラクター用プロンプト言語がない場合の UI 言語フォールバック |

成功時は `200` と `success`、`character_name`、`switches`（`natural_expression`、`review`）、`rules`（`min_sessions`、`session_gap_minutes`、`max_items`、`candidate_pool`、`prompt_max_tokens`、`refresh_interval_hours`、`max_age_days`）、`next_refresh_at`、`computed_at`、`language`、`language_matches`、`stale`、`parameters`、`source`、`review`、`entries`、`injected`、`overrides` を返します。不正な名前は `422`、未検出は `404`、ストア利用不可は `503` です。

### `POST /api/memory/habitual_phrases/override`

1 つのフレーズに対するユーザー上書き（`allow`、`suppress`、`clear`）を保存し、`GET /api/memory/habitual_phrases` と同じ更新済みパネルを返します。

**リクエストボディ**

| フィールド | 型 | 必須 | 説明 |
|---|---|---:|---|
| `character_name` | 文字列 | はい | 対象キャラクター名 |
| `phrase` | 文字列 | はい | フレーズ文字列（`1`–`200` 文字） |
| `action` | 文字列 | はい | `"allow"`、`"suppress"`、`"clear"` のいずれか |
| `ui_language` | 文字列 | いいえ | 任意の UI 言語ヒント（最大 `32` 文字） |

不正な名前またはフレーズは `422`、未検出は `404`、復元・メンテナンス中またはストア利用不可は `503` です。

### `POST /api/memory/habitual_phrases/recompute`

Memory Server 経由（130 秒転送タイムアウト）でキャラクターの口癖回避リストを即時再計算し、更新済みパネルを返します。

**リクエストボディ**

| フィールド | 型 | 必須 | 説明 |
|---|---|---:|---|
| `character_name` | 文字列 | はい | 対象キャラクター名 |
| `ui_language` | 文字列 | いいえ | 任意の UI 言語ヒント（最大 `32` 文字） |

既に再計算中の場合は `409`（`{"success": false, "error": "already recomputing"}`）、不正な名前は `422`、未検出は `404`、再計算不可は `503` を返します。

## 外部メモリインポート

### `POST /api/memory/external_import/preview`

メモリファイルへ書き込まずに OpenClaw、Hermes、Molili の Markdown または ZIP アーカイブを解析します。リクエストボディサイズは `EXTERNAL_IMPORT_MAX_REQUEST_BODY_BYTES` で制限されます。

**リクエストボディ**

| フィールド | 型 | 必須 | 説明 |
|---|---|---:|---|
| `character_name` | 文字列 | はい | 対象キャラクター名（ドット許可、最大 `50` 文字） |
| `source_format` | 文字列 | いいえ | `"auto"`（既定）、`"openclaw"`、`"hermes"`、`"molili"` |
| `files` | 配列 | いいえ | `{path/name, content}` オブジェクト配列。`archive_b64` と排他 |
| `archive_b64` | 文字列 | いいえ | Base64 エンコードされた ZIP アーカイブ（`data:` プレフィックス可）。`files` と排他 |

成功時は `success`、`character_name`、`source_format`、`files`、`counts`（`persona`、`facts`、`daily`）、`candidate_count`、`persona_fusion_calls`、`persona_candidate_tokens`、`daily_extraction_calls`、`daily_candidate_tokens`、`warning_count`、`warnings`（最大 20 件）、`candidates`（最大 100 件）、`truncated_preview` を返します。Molili アーカイブでは `character_card`、`master_profile`、`skipped`、`snapshot_time` も含まれます。リクエストボディが `EXTERNAL_IMPORT_MAX_REQUEST_BODY_BYTES` を超える場合は、`InboundBodySizeLimitMiddleware` が事前に HTTP `413` と `{"ok": false, "error_code": "payload_too_large", "max_bytes": ..., "error": ...}`（`{success, error}` 形式ではない）で拒否します。検証・解析失敗は `400`、想定外エラーは `500` です。

### `POST /api/memory/external_import/commit`

プレビュー済みの外部メモリを `POST /internal/memory/import_external_markdown`（480 秒転送タイムアウト）経由でキャラクターのメモリストアへ統合します。

**リクエストボディ**

`preview` と同じ `character_name`、`source_format`、`files` / `archive_b64` に加えて、以下を受け付けます。

| フィールド | 型 | 必須 | 説明 |
|---|---|---:|---|
| `acknowledge_warnings` | 真偽値 | 条件付き | 不審な命令パターンの警告がある場合は `true` が必須（未指定・`false` は `409`） |
| `render_language` | 文字列 | いいえ | 描画時のみ使う検証済みロケールフォールバック。永続的な言語設定には保存されません |

成功時は `success: true`、`need_refresh: true`、`memory_server_reloaded: true`、`status: "success"`、`character_name`、`source_format`、`imported_files`、`added_persona`、`added_facts`、`skipped_duplicates`、`warning_count` を返します。リクエストボディが `EXTERNAL_IMPORT_MAX_REQUEST_BODY_BYTES` を超える場合は、`InboundBodySizeLimitMiddleware` が事前に HTTP `413` と `{"ok": false, "error_code": "payload_too_large", "max_bytes": ..., "error": ...}`（`{success, error}` 形式ではない）で拒否します。ルーター・Memory Server 側のエラーは `400`（不正入力）、`409`（警告未確認またはクラウドストレージメンテナンス中）、`413`（`error_code: "external_import_too_large"` と `partial_import`）、`500`（`error_code: "external_import_partial"` と `partial_import` または汎用エラー）、`504`（`error_code: "external_import_timeout"`、転送タイムアウト後も Memory Server は処理継続）です。

### `GET /api/memory/external_import/progress`

Memory Server が保持する外部インポート進捗カウンターをポーリングします。

**クエリパラメーター**

| 名前 | 型 | 必須 | 説明 |
|---|---|---:|---|
| `character_name` | 文字列 | はい | 対象キャラクター名（ドット許可、最大 `50` 文字） |

現在の Memory Server プロセスで当該キャラクターのインポートが未実行の場合は `{"success": true, "active": false}`、実行中は `{"success": true, "active": true, "stage": "persona"|"daily", "persona_total": ..., "persona_done": ..., "daily_total": ..., "daily_done": ..., "finished": ...}`、完了後は保持されたエントリーにより `{"success": true, "active": false, "finished": true, ...}` と最終ステージ・件数を返します。不正な名前は `400`、Memory Server が返す 200 以外のステータスコードはそのまま透過し、転送失敗は `502` です。

## お別れカードの概要

### `GET /api/memory/farewell-summary`

現在選択中のキャラクターの読み取り専用メモリ概要で、デスクトップ版の終了時お別れカードに表示されます。デスクトップのメインプロセスがループバック経由で 1.2 秒のタイムアウト付きで呼び出すため、このルートはローカルファイルを読むだけです。モデル呼び出し、同期、書き込みは一切行いません。

```json
{
  "memory_count": 128,
  "conversation_count": 342,
  "flashback": {
    "id": "fact_20260314103000_ab12cd34",
    "text": "ご主人様は雨の日に家で本を読むのが大好き",
    "date": "2026-03-14"
  }
}
```

| フィールド | 説明 |
|---|---|
| `memory_count` | ローカルの有効メモリ数。コミュニティカードの `totalMemoryCount` と同じ基準で、`facts.json` と `facts_archive.json` を重複排除し、機械ログ行を除きます。 |
| `conversation_count` | キャラクターの `time_indexed.db` 内のユーザーメッセージ数（会話 1 往復につき 1 件）。データベースがない、または読めない場合は省略されます。 |
| `flashback` | 振り返る価値のある古いメモリ 1 件、または `null`。 |
| `flashback.id` | メモリ ID。単純な文字列でない ID は安定した不透明トークンになります。 |
| `flashback.text` | メモリ本文。空白をまとめ、最大 200 文字。 |
| `flashback.date` | メモリの出来事のローカル日付（`YYYY-MM-DD`）。出来事の時刻がない場合は記録日時を使います。 |

フラッシュバックは共有可能なメモリからのみ選ばれ、規則は `/api/card-drop/facts` と同じです。private、redacted、機械ログ行、`neko_visit` 由来の行、および `subject_archived_at` や `arbitration_*` を持つアーカイブ行は除外されます。7 日未満のメモリは選ばれません。残りのうち importance 5 以上を優先し、最も古い 4 分の 1 から importance で重み付けして 1 件を抽選するため、呼び出しごとに結果が変わることがあります。

キャラクターが選択されていない場合は `{"flashback": null}` を返します。失敗した場合はエラーを返さず、件数フィールドを省略します。

## 旧メモリのクリーンアップ

旧メモリのクリーンアップは明示的な 2 段階操作です。まず走査し、次に選択した絶対パスを送信します。走査は移行も削除も行いません。

### `GET /api/memory/legacy/scan`

パラメーターはありません。

アクティブなランタイムメモリディレクトリ外にある候補ルートと、その直下の項目を返します。サイズを計算できない、または安全な走査上限を超えた場合、`size_bytes` は `-1` です。

```json
{
  "success": true,
  "runtime_memory_dir": "C:\\...\\memory",
  "legacy_roots": [
    {
      "root": "C:\\...\\old-root\\memory",
      "source": "legacy_app_root",
      "exists": true,
      "entries": [
        {
          "name": "小天",
          "path": "C:\\...\\old-root\\memory\\小天",
          "is_dir": true,
          "size_bytes": 12345,
          "is_unlinked": false,
          "runtime_has_same_name": true
        }
      ]
    }
  ],
  "total_entries": 1,
  "total_size_bytes": 12345
}
```

予期しない走査エラーは `500` と `success: false` を返します。

### `POST /api/memory/legacy/purge`

これは破壊的な操作です。直近の走査で項目として返されたパスだけを送信してください。

```json
{
  "paths": [
    "C:\\...\\old-root\\memory\\小天"
  ]
}
```

`paths` は空でない絶対パスの配列である必要があります。各パスは、現在認識されている旧ルートの厳密な配下へ解決されなければなりません。相対パス、`..` セグメント、ルートディレクトリ自体、アクティブなランタイムメモリディレクトリは拒否されます。存在しない対象は削除済みとして扱われるため、再試行は冪等です。

削除は項目ごとにベストエフォートで実行されるため、成功したリクエストに `removed` と `errors` の両方が含まれることがあります。

```json
{
  "success": true,
  "removed": ["C:\\...\\old-root\\memory\\小天"],
  "errors": [
    { "path": "C:\\...\\not-allowed", "error": "..." }
  ]
}
```

不正なボディは `400`、認識できる旧ルートがない場合は `409`、初期化失敗は `500` です。
