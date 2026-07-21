# データフロー

## ブラウザと Electron の入口

Main Server はキャラクターごとに `/ws/{lanlan_name}` WebSocket を公開します。開発ページ（`/`、`index.html`）と Electron チャットウィンドウは、同じバックエンドプロトコルと `frontend/react-neko-chat/` の同じ React チャット実装を使用します。`/chat` などの Electron ルートでは window IPC 経由で socket をプロキシする場合がありますが、メッセージ契約は同じです。

廃止済み `#chat-container` DOM 実装は現在のデータ経路には含まれません。旧 `appendMessage()` 呼び出しは `static/app/app-chat-adapter.js` が捕捉し、React チャットへ転送します。

## WebSocket ライフサイクル

```text
Client                         Main Server                    LLM client
  │                                │                              │
  │── connect /ws/{name} ─────────>│ accept + bind manager        │
  │── start_session(audio|text) ──>│── create/connect ───────────>│
  │<─ session_preparing / started ─│                              │
  │── stream_data ────────────────>│── text or PCM input ────────>│
  │<─ gemini_response deltas ──────│<─ text/output callbacks ─────│
  │<─ audio_chunk header ──────────│<─ native or external TTS ────│
  │<─ binary PCM frame ────────────│                              │
  │<─ system: turn end ────────────│<─ response completion ───────│
  │── end_session ────────────────>│── cancel/close/cleanup ─────>│
```

`start_session` は非同期です。クライアントは `session_started` を待ち、`session_failed` を処理する必要があります。`session_preparing` は静かな起動区間を示します。音声モードは `OmniRealtimeClient`、テキストモードは `OmniOfflineClient` を作成します。

起動中やモード跨ぎの再構築中にも `stream_data` は到着できます。Manager は順序付きテキスト/画像をキャッシュし、音声は有界キューで直列処理します。

## クライアント → サーバー

制御メッセージは JSON テキストフレームです。

```json
{ "action": "start_session", "input_type": "audio", "new_session": true }
{ "action": "start_session", "input_type": "text" }
{ "action": "stream_data", "input_type": "audio", "data": [0, 12, -8] }
{ "action": "stream_data", "input_type": "text", "data": "こんにちは" }
{ "action": "stream_data", "input_type": "image", "data": "data:image/png;base64,..." }
{ "action": "end_session" }
{ "action": "ping" }
```

音声 payload は signed PCM sample の配列で、base64 文字列ではありません。ブラウザの capture 経路は通常 48 kHz mono chunk を送り、realtime 経路は前処理後に 16 kHz 音声を upstream へ送ります。

同じ socket は `greeting_check`、`avatar_interaction`、`screenshot_response`、`capture_bridge_*`、`goodbye_state`、`language_update`、`voice_play_start`、`voice_play_end`、telemetry も運びます。不明な action には構造化 `status` エラーを返します。

キャラクターごとに現在有効な接続世代は1つです。新接続が session ID を置き換え、古い socket は閉じられます。

## サーバー → クライアント

主な JSON message type は次のとおりです。

| Type | 用途 |
|---|---|
| `session_preparing`、`session_started`、`session_failed` | `input_mode` 付き起動状態 |
| `gemini_response` | ストリーミング assistant text。歴史的な名前を provider 間で維持 |
| `user_transcript` | 認識されたユーザー音声 |
| `audio_chunk` | `speech_id` を持つ header。次フレームが binary PCM |
| `system` + `turn end` / `turn end agent_callback` | ターン完了 |
| `status` | 構造化 status/error |
| `expression`、`focus_state`、`focus_charge` | キャラクター表示状態 |
| `agent_status_update`、`agent_task_update`、`agent_notification` | Agent 状態とタスク配信 |
| `request_screenshot`、`capture_bridge_request` | クライアント capture 要求 |
| `session_ended_by_server`、`reload_page`、`catgirl_switched` | 復旧やキャラクター変更 |
| `pong` | heartbeat response |

外部 TTS 音声は worker が 48 kHz mono PCM に統一し、`audio_chunk` JSON header と続く binary frame として送ります。JSON の `audio_data` には埋め込みません。

## REST リクエスト

```text
Browser / Electron renderer
        │
        ├─ /api/characters/* ──> character routers ──> ConfigManager
        ├─ /api/agent/* ───────> agent_router ───────> Agent Server :48915
        └─ other /api/* ───────> domain router ──────> shared-state getters
```

Router は `main_routers/shared_state.py` の getter から長寿命 manager を取得します。Agent 制御は Main Server proxy であり、ブラウザが 48915 に直接接続する必要はありません。

## Agent イベントフロー

```text
cross_server turn/session end
        │ recent messages + current-turn attachments
        ▼
MainServerAgentBridge ── ZMQ :48963 ──> AgentServerEventBridge
        ▲                                      │
        │ ACK / task_update / task_result      ▼
        └──────────── ZMQ :48962 ───── channel dispatch
        │
        ├─ task_update ──> WebSocket ──> Task HUD
        └─ task_result ──> LLMSessionManager callback queue ──> chat delivery
```

セッション broadcast は別の 48961 PUB/SUB を使います。分析要求は 48963 の信頼性付き PUSH/PULL queue を使い、48962 の Agent → Main channel で ACK されます。

## TTS データフロー

テキストと音声は常に同じ合成経路ではありません。

- テキストセッションはプロジェクト TTS runtime に送ります。TTS 無効時は dummy worker が無音になります。
- 対応する native voice の音声セッションは provider-native realtime audio を使います。custom voice や指定 external provider はプロジェクト TTS runtime を使います。
- TTS worker は thread queue で text を受け、response queue で 48 kHz PCM を返します。`send_speech()` が `speech_id` header と binary data を送信します。

並行性と中断規則は [Session Management](/architecture/session-management) と [TTS Pipeline](/architecture/tts-pipeline) を参照してください。
