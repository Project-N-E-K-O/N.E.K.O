# WebSocket メッセージタイプ

すべてのメッセージは JSON テキストフレームです。

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

### `text`

LLM からのストリーミングテキストレスポンス。

```json
{
  "type": "text",
  "text": "Hi there! How can I help you?"
}
```

### `audio`

オーディオレスポンス（TTS 出力または直接 LLM オーディオ）。

```json
{
  "type": "audio",
  "audio_data": "<base64 encoded PCM 48kHz>"
}
```

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
