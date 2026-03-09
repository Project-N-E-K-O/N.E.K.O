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
{ "action": "pause_session" }
{ "action": "screenshot_response", "data": "<base64 image>" }
{ "action": "ping" }
```

**サーバー → クライアント（JSONテキストフレーム）：**

```json
{ "type": "text", "text": "Hi there!" }
{ "type": "audio", "audio_data": "<base64 PCM 48kHz>" }
{ "type": "status", "message": "Session started" }
{ "type": "emotion", "emotion": "happy" }
{ "type": "agent_notification", "text": "...", "source": "...", "status": "..." }
{ "type": "catgirl_switched", "data": { ... } }
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
  │                                    │── DirectTaskExecutor:
  │                                    │   parallel assess:
  │                                    │   ├── _assess_mcp()
  │                                    │   ├── _assess_browser_use()
  │                                    │   ├── _assess_computer_use()
  │                                    │   └── _assess_user_plugin()
  │                                    │   priority select & execute
  │                                    │── Analyzer: 結果を評価
  │<── ZMQ PUSH (task_result) ────────│
  │                                    │
  │── 次のLLMターンに注入 ──>         │
```

## TTSパイプライン

```
LLM text output ──> TTS request queue ──> TTS worker thread
                                              │
                                     ┌────────┼──────────────┐
                                     │        │              │
                                     ▼        ▼              ▼
                                CosyVoice  GPT-SoVITS   StepFun RT
                               (DashScope) (Local)      (WebSocket)
                                     │        │              │
                                     └────────┼──────────────┘
                                              │
                                    TTS response queue
                                              │
                                    soxr ResampleStream (24→48kHz)
                                        (stateful, per-message)
                                              │
                                    WebSocket ──> Browser
```

TTSパイプラインは完全に中断可能です — ユーザーが話し始めると（割り込みイベント）、保留中のTTS出力は即座に破棄されます。`soxr` リサンプラーはチャンク境界での不連続を防ぐためチャンク間で内部状態を保持し、アーティファクトを避けるため新しいメッセージごとに状態をリセットします。
