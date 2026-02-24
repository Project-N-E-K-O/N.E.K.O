# データフロー

## WebSocketチャットライフサイクル

これは主要なインタラクションフローであり、ユーザーがAIキャラクターとチャットする流れです。

```
Browser                    Main Server                   LLM Provider
  │                            │                              │
  │──── WS connect ───────────>│                              │
  │     /ws/{lanlan_name}      │                              │
  │                            │                              │
  │──── start_session ────────>│                              │
  │     {input_type: "audio"}  │──── WS connect ─────────────>│
  │                            │     (OmniRealtimeClient)     │
  │                            │                              │
  │──── stream_data ──────────>│──── send_audio ─────────────>│
  │     {audio chunks}         │                              │
  │                            │<──── on_text_delta ──────────│
  │<──── {type: "text"} ──────│                              │
  │                            │<──── on_audio_delta ─────────│
  │<──── {type: "audio"} ─────│     (resampled 24→48kHz)     │
  │                            │                              │
  │──── end_session ──────────>│──── close ───────────────────│
  │                            │                              │
  │                            │── hot-swap to next session ──│
```

### メッセージフォーマット

**クライアント → サーバー（JSONテキストフレーム）：**

```json
{ "action": "start_session", "input_type": "audio", "new_session": true }
{ "action": "stream_data", "input_type": "audio", "data": "<base64 PCM>" }
{ "action": "stream_data", "input_type": "text", "data": "Hello!" }
{ "action": "end_session" }
{ "action": "ping" }
```

**サーバー → クライアント（JSONテキストフレーム）：**

```json
{ "type": "text", "text": "Hi there!" }
{ "type": "audio", "audio_data": "<base64 PCM 48kHz>" }
{ "type": "status", "message": "Session started" }
{ "type": "emotion", "emotion": "happy" }
{ "type": "agent_notification", "text": "...", "source": "...", "status": "..." }
{ "type": "pong" }
```

## REST APIリクエストフロー

```
Browser ──── GET /api/characters/ ────> FastAPI Router
                                            │
                                            ├── shared_state（グローバルセッションマネージャー）
                                            ├── config_manager（キャラクターデータ）
                                            └── Response（JSON）
```

すべてのRESTエンドポイントは標準的なFastAPIパターンに従います。ルーターは循環インポートを避けるため、`shared_state.py` のゲッター関数を通じてグローバル状態にアクセスします。

## エージェントタスクフロー

```
LLMSessionManager                  Agent Server
  │                                    │
  │── ZMQ PUB (analyze request) ──────>│
  │                                    │── Planner: タスクプランを作成
  │                                    │── Executor: アクションを実行
  │                                    │   ├── MCP tool calls
  │                                    │   ├── Computer Use
  │                                    │   └── Browser Use
  │                                    │── Analyzer: 結果を評価
  │<── ZMQ PUSH (task_result) ────────│
  │                                    │
  │── 次のLLMターンに注入 ──>         │
```

## TTSパイプライン

```
LLM text output ──> TTS request queue ──> TTS worker thread
                                              │
                                              ├── DashScope CosyVoice
                                              ├── GPT-SoVITS（ローカル）
                                              └── Custom endpoint
                                              │
                                         TTS response queue
                                              │
                                         Audio resampler（24→48kHz）
                                              │
                                         WebSocket send to browser
```

TTSパイプラインは完全に中断可能です — ユーザーが話し始めると（割り込みイベント）、保留中のTTS出力は即座に破棄されます。
