# WebSocket メッセージタイプ

オーディオペイロードを除き、メッセージは JSON テキストフレームです。オーディオの場合、サーバーはまず `audio_chunk` JSON ヘッダーフレームを送信し、続いて PCM バイトを運ぶ独立した生のバイナリフレームを送信します（詳細は下記の `audio_chunk` セクションを参照）。

## クライアント → サーバー

### `start_session`

LLM セッションを初期化します。

```json
{
  "action": "start_session",
  "input_type": "audio",
  "new_session": true
}
```

### `stream_data`

ユーザー入力（オーディオチャンクまたはテキスト）を送信します。

**オーディオ入力:**
```json
{
  "action": "stream_data",
  "input_type": "audio",
  "data": "<base64 encoded PCM audio>"
}
```

**テキスト入力:**
```json
{
  "action": "stream_data",
  "input_type": "text",
  "data": "Hello, how are you?"
}
```

**画面データ:**
```json
{
  "action": "stream_data",
  "input_type": "screen",
  "data": "<base64 encoded screenshot>"
}
```

### `end_session`

現在のセッションを終了します。

```json
{ "action": "end_session" }
```

### `pause_session`

接続を閉じずに処理を一時停止します。

```json
{ "action": "pause_session" }
```

### `ping`

キープアライブハートビート。

```json
{ "action": "ping" }
```

## サーバー → クライアント

### `gemini_response`

LLM からのストリーミングテキストレスポンス。

```json
{
  "type": "gemini_response",
  "text": "Hi there! How can I help you?",
  "isNewMessage": true,
  "turn_id": "<turn id>",
  "request_id": "<request id>"
}
```

### `audio_chunk`

オーディオレスポンス（TTS 出力または直接 LLM オーディオ）。サーバーはまずこの JSON ヘッダーフレームを送信し、続いて生の PCM オーディオバイトを独立したバイナリ WebSocket フレームとして送信します（base64 として埋め込まれません）。`speech_id` は割り込み（barge-in）時にこのターンの音声を正確に照合するために使われます。

```json
{
  "type": "audio_chunk",
  "speech_id": "<このターンの speech_id>"
}
```

その後にバイナリフレーム（生の PCM バイト）が続きます。クライアントは上記のヘッダーフレームを受信した後、次のバイナリフレームを読み取ってデコードし再生します。

### `status`

セッション状態に関するステータスメッセージ。

```json
{
  "type": "status",
  "message": "Session started successfully"
}
```

### `emotion`

モデルの表情を駆動するための感情ラベル。

```json
{
  "type": "emotion",
  "emotion": "happy"
}
```

### `catgirl_switched`

サーバー側でアクティブなキャラクターが変更された通知。

```json
{
  "type": "catgirl_switched",
  "new_catgirl": "new_character",
  "old_catgirl": "old_character"
}
```

クライアントは `/ws/{new_catgirl}` に再接続する必要があります。

### `reload_page`

サーバーがクライアントにページの更新を要求します。

```json
{
  "type": "reload_page",
  "message": "Configuration changed, please refresh"
}
```

### `agent_notification`

エージェントタスクの更新通知。

```json
{
  "type": "agent_notification",
  "text": "Found relevant information about...",
  "source": "web_search",
  "status": "completed"
}
```

### `agent_task_update`

エージェントタスクの詳細ステータス。

```json
{
  "type": "agent_task_update",
  "task": {
    "id": "task-uuid",
    "status": "running",
    "progress": 50
  }
}
```

### `agent_status_update`

エージェントシステムのステータススナップショット。

```json
{
  "type": "agent_status_update",
  "snapshot": {
    "active_tasks": 1,
    "flags": { "agent_enabled": true }
  }
}
```

### `pong`

`ping` に対するレスポンス。

```json
{ "type": "pong" }
```
