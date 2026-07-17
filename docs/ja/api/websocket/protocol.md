# WebSocket Protocol

Main server の application WebSocket route は 1 つです。

```text
ws://127.0.0.1:48911/ws/{lanlan_name}
```

`{lanlan_name}` は URL encode してください。信頼できる reverse proxy が TLS termination する場合は `wss://` を使います。これは同梱 UI protocol で、独立して versioned された public wire standard ではありません。

## Connection accept

1. Server は WebSocket を accept し、path character を process 内 session manager と照合します。
2. Character が不明なら、有効な fallback を持つ `catgirl_switched` を送ってから close する場合があります。custom close code は保証されません。
3. 有効なら接続を character の session manager へ install。内部 UUID は生成しますが connection ack frame では送信しません。
4. Client は `greeting_check` などの control action をすぐ送れます。会話 media は先に `start_session` が必要です。

Router では character ごとに最新 connection UUID のみ authoritative です。古い接続が後から frame を送ると `CHARACTER_SWITCHING_TERMINAL` status を受け close されます。First-party multi-window は競合する複数 primary socket ではなく、project の inter-page proxy/sync layer に依存します。

## Frame model

- Client command は top-level `action` を持つ UTF-8 JSON text frame。
- Server event は通常 top-level `type` を持つ UTF-8 JSON text frame。
- TTS audio は例外で、`audio_chunk` JSON header の後に binary frame が 1 つ続きます。
- Server は client から `receive_text()` します。client binary audio frame は送らないでください。
- 任意の client JSON に `language` を付けられ、router は `action` dispatch 前に UI language を更新します。

Malformed JSON は connection-level error です。handler は best-effort で `SERVER_ERROR` status を送信し、receive loop を終了して現在 session を cleanup します。

## Session lifecycle

Application WebSocket と provider session の寿命は別です。

```text
socket open
    │
    ├─ start_session ─> session_preparing ─> session_started
    │                                      └> session_failed
    │
    ├─ stream_data / avatar_interaction / control events
    │
    ├─ pause_session または end_session ─> provider session end
    │                                        socket remains open
    │
    └─ socket close ─> current provider session / route state cleanup
```

### Start

```json
{
  "action": "start_session",
  "input_type": "audio",
  "new_session": false,
  "language": "ja"
}
```

有効な `input_type` は `audio`、`screen`、`camera`、`text`、`avatar_drop_image`、`user_image`。`text` と 2 種の one-shot image は text/offline mode、`audio`、`screen`、`camera` は realtime/audio mode を選びます。`new_session` は provider-session hint で、connection UUID ではありません。

Startup は非同期です。microphone sample の前に一致する `session_started.input_mode` を待ちます。`session_preparing` は progress だけで、`session_failed` は mode が開始しなかったことを示します。通常、失敗前に machine-readable な `status` が届きます。

Game route active 中、text/image は game controller に acknowledge/route される場合があり、audio session は game realtime STT provider になります。First-party game integration の挙動です。

### Pause と end

```json
{ "action": "pause_session" }
```

`pause_session` は manager を idle にして現在 provider session を終了します。再開可能な upstream paused stream は保持しません。

```json
{ "action": "end_session", "reason": "user_stop" }
```

`end_session` は provider cleanup を schedule し、後で再 start できるよう application WebSocket は維持します。任意 `goodbye_active: true` または `reason: "goodbye"` は silent-goodbye gate も有効化します。いずれも pre-warmed replacement session は保証しません。

Upstream disconnect、config change、timeout では server が `session_ended_by_server` を送る場合があります。

## Keep-alive

Application heartbeat:

```json
{ "action": "ping" }
```

```json
{ "type": "pong" }
```

間隔は client/proxy timeout に合わせます。backend handler 自体は固定 interval を要求しません。

## Status と error

歴史的 frontend compatibility のため status は nested JSON envelope です。

```json
{
  "type": "status",
  "message": "{\"code\":\"INVALID_INPUT_TYPE\",\"details\":{\"input_type\":\"file\"}}"
}
```

`message` をもう一度 JSON parse します。例は `INVALID_INPUT_TYPE`、`UNKNOWN_ACTION`、`SERVER_ERROR`、provider/auth/quota code、`CHARACTER_SWITCHING_TERMINAL`。集合は増えるため unknown code の generic fallback が必要です。

Unknown action は socket を閉じず `UNKNOWN_ACTION` を返します。一方 invalid JSON、superseded connection、character rename/delete、transport disconnect は cleanup につながります。

## Security boundary

Route に独立 auth handshake はありません。local trusted UI を前提に user-controlled text、image、telemetry、capture metadata を受け取ります。48911 は loopback に保つか、認証・Origin 制限 proxy の背後に置いてください。Telemetry dimension、local capture response、character name を trusted input として扱わないでください。

[Message Types](./message-types) と [Audio Streaming](./audio-streaming) も参照してください。
