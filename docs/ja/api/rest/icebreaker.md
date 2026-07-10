# アイスブレイク API

**プレフィックス:** `/api/icebreaker`

新規ユーザーのオンボーディング（「アイスブレイク」）用エンドポイントです。アイスブレイクはミニゲームではなくオンボーディングの会話であり、ゲームルートのライフサイクルとは別に独自の状態を保持します。コンテキストの追記や固定のオンボーディング台詞の発話は行えますが、`/api/game/route/active` がミニゲームウィンドウの起動中を報告することは決してありません。

::: info
すべての書き込み系エンドポイント（`/route/start`、`/route/end`、`/context`、`/choice`、`/speak`）はローカル書き込み用エンドポイントであり、バックエンドの他部分と同じ CSRF / ローカルリクエスト検証で保護されています。検証に失敗すると `{ "ok": false, "reason": "csrf_validation_failed" }` を返します。
:::

::: info
`lanlan_name` は対象のキャラクターを識別し、書き込み系 POST エンドポイントでは**必須**です。`/route/start`、`/route/end`、`/speak` はまず Body から解決し、解決できない場合は現在選択中のキャラクター（`当前猫娘`）にフォールバックします。それでもキャラクターを解決できない場合は `{ "ok": false, "reason": "missing_lanlan_name" }` を返します。`/context` はより厳格で、Body に空でない `lanlan_name` が必須（フォールバックなし）であり、未指定または空の場合は `{ "ok": false, "reason": "missing_lanlan_name" }` を返します。
:::

## ルートのライフサイクル

### `POST /api/icebreaker/route/start`

キャラクターのアイスブレイクルートを有効化し、セッションに紐付けます。

**Body:**

```json
{
  "lanlan_name": "character_name",
  "session_id": "session-uuid"
}
```

`session_id` は必須です。未指定の場合は `{ "ok": false, "reason": "missing_session_id" }` を返します。

**Response:**

```json
{
  "ok": true,
  "state": {
    "icebreaker_active": true,
    "lanlan_name": "character_name",
    "session_id": "session-uuid",
    "started_at": 0.0,
    "last_activity": 0.0,
    "source": "new_user_icebreaker"
  }
}
```

### `POST /api/icebreaker/route/end`

有効なアイスブレイクルートを終了します。

**Body:**

```json
{
  "lanlan_name": "character_name",
  "session_id": "session-uuid",
  "reason": "icebreaker_end"
}
```

`reason` は任意で、既定値は `icebreaker_end` です。

**Response:** `{ "ok": true, "state": <ルート状態> }`。

::: info
`session_id` が指定されていても有効なルートのセッションと一致しない場合（例: 2 つ目のタブが新しいセッションを開始した場合）、ルートを終了せずにリクエストを拒否します:

```json
{
  "ok": false,
  "reason": "session_id_mismatch",
  "handled": false,
  "lanlan_name": "character_name",
  "method": "route_end",
  "state": "<ルート状態>"
}
```
:::

### `GET /api/icebreaker/route/state`

キャラクターの現在のアイスブレイクルート状態を取得します。

**Query:** `lanlan_name` —— キャラクター名（任意。選択中のキャラクターにフォールバックします）。

**Response:** 公開用のルート状態。有効なルートがない場合、`state` は `{ "icebreaker_active": false }` になります。

```json
{ "ok": true, "state": { "icebreaker_active": false } }
```

## オンボーディングの操作

### `POST /api/icebreaker/context`

オンボーディングのコンテキスト 1 行（user または assistant）をプロジェクトのセッション履歴に追記します。テキストはメモリシステム用にもキャッシュされます。同一セッションに紐付いた有効なルートが必要です。

**Body:**

```json
{
  "lanlan_name": "character_name",
  "session_id": "session-uuid",
  "role": "assistant",
  "text": "ようこそ！セットアップをお手伝いします。"
}
```

- `role` は `assistant` または `user` である必要があります。それ以外は `{ "ok": false, "reason": "invalid_role" }` を返します。
- `text` は必須（`missing_text`）で、上限は 2000 文字です（`invalid_text_length`）。
- 任意の `request_id`（`event.request_id` も受け付けます）は重複排除に使用されます。

**Response:**

```json
{
  "ok": true,
  "method": "project_session_history",
  "lanlan_name": "character_name",
  "source": "new_user_icebreaker",
  "session_id": "session-uuid",
  "memory_cached": true
}
```

重複した追記は同じ構造に `"deduped": true` を付けて返します。

有効なルートがない場合は次を返します:

```json
{
  "ok": false,
  "reason": "route_not_active",
  "lanlan_name": "character_name",
  "source": "new_user_icebreaker",
  "method": "project_session_history"
}
```

有効なルートはあるが指定された `session_id` が一致しない場合（古い、または置き換えられたセッション）、追記はスキップされます:

```json
{
  "ok": true,
  "skipped": "stale_session",
  "reason": "session_id_mismatch",
  "handled": false,
  "lanlan_name": "character_name",
  "method": "project_session_history",
  "state": "<ルート状態>"
}
```

### `POST /api/icebreaker/choice`

有効なチュートリアルの選択 1 件を永続的な選択プールに保存し、セッションをまたいで残します。同一セッションに紐付いた有効なルートが必要です。

::: info
このエンドポイントは現時点では書き込み専用です。記録された選択はメモリシステムに入らず、モデルにも影響しません。`/context`（一時的なセッション履歴に供給）とは意図的に分離しており、このプールは後から段階的に消費できる独立したシグナルとして保たれます。
:::

**Body:**

```json
{
  "lanlan_name": "character_name",
  "session_id": "session-uuid",
  "day": 1,
  "node_id": "intro",
  "choice": "option_a",
  "label": "もっと教えて",
  "handoff": false,
  "completed": false,
  "seq": 0
}
```

**Response:** 選択プールからの永続化結果で、`source` は `new_user_icebreaker` に設定されます。有効なルートがない場合は `{ "ok": false, "reason": "route_not_active", ... }` を返します。

### `POST /api/icebreaker/speak`

固定のオンボーディング台詞をプロジェクトの TTS パイプライン経由で発話します（チャット画面にもミラーリングします）。同一セッションに紐付いた有効なルートが必要です。

**Body:**

```json
{
  "lanlan_name": "character_name",
  "session_id": "session-uuid",
  "line": "はじめまして！",
  "mirror_text": true,
  "emit_turn_end": true,
  "interrupt_audio": false
}
```

- `line` は必須（`missing_line`）です。SSML 風タグは除去され、台詞は 240 文字に切り詰められます。
- `mirror_text` と `emit_turn_end` の既定値は `true`、`interrupt_audio` の既定値は `false` です。

**Response:** TTS 結果の構造で、`method: "project_tts"` と `voice_source` ブロックを含みます:

```json
{
  "ok": true,
  "lanlan_name": "character_name",
  "method": "project_tts",
  "voice_source": { "provider": "project_tts", "method": "project_tts" }
}
```

有効なルートがない場合は次を返します:

```json
{
  "ok": false,
  "reason": "route_not_active",
  "lanlan_name": "character_name",
  "source": "new_user_icebreaker",
  "method": "project_tts",
  "audio_sent": false
}
```

有効なルートはあるが指定された `session_id` が一致しない場合（古い、または置き換えられたセッション）、台詞は発話されません:

```json
{
  "ok": true,
  "skipped": "stale_session",
  "reason": "session_id_mismatch",
  "handled": false,
  "lanlan_name": "character_name",
  "method": "project_tts",
  "state": "<ルート状態>",
  "audio_sent": false,
  "audio_committed": false,
  "voice_source": {
    "provider": "project_tts",
    "method": "project_tts",
    "skipped": "stale_session"
  }
}
```
