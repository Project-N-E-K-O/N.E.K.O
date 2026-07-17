# LLMSessionManager

**Package:** `main_logic/core/`

`LLMSessionManager` は、ブラウザー WebSocket、一つの active model client、メモリ、tool、proactive result、音声出力を接続するキャラクター単位の coordinator です。`app/main_server/character_runtime.py` がキャラクターごとに manager を作成・保持します。

クラスは `manager.py` で組み立てられます。`__init__` が全 instance state の唯一の owner で、メソッドは `context_append`、`focus`、`tts_runtime`、`turn`、`tool_calling`、`lifecycle`、`proactive`、`greeting`、`streaming`、`notify` mixin から供給されます。`main_logic.core` が再 export するのは、旧 monolithic module のうち引き続きサポートされる互換 surface だけです。

## セッション選択と起動

```python
await manager.start_session(websocket, new=False, input_mode="audio")
```

`start_session()` は直列化され、同一 mode の重複や cross-mode start の競合を防ぎます。ユーザーが明示的に開始した request は、別 mode の in-flight start を置き換えられます。background greeting/proactive start はユーザー request を上書きしません。

起動 phase は次のとおりです。

1. frontend WebSocket を bind し、runtime、character、voice、provider 設定を再読込して preparation 開始を通知します。
2. 旧 active/pending session を終了し、input/TTS state を reset します。
3. Memory Server から `GET /new_dialog/{lanlan_name}` を prefetch します。
4. 選択 route が外部 TTS を必要とする場合は worker を開始します。
5. text input なら `OmniOfflineClient`、audio input なら `OmniRealtimeClient` を作り、callback/tool を接続し、rendered memory context と共に connect します。
6. 接続済み client を `self.session` に昇格し、必要な receive task を開始し、queued context/input を flush して ready を通知します。

Memory context は必須の起動依存です。`/new_dialog` の connection failure、timeout、non-2xx は session start を失敗させます。空 context で暗黙に続行しません。

Start failure が連続すると local circuit が開き、audio chunk ごとの再接続試行を防ぎます。frontend からの明示的 retry が circuit を reset します。

## 入力ルーティング

```python
await manager.stream_data(message)
```

`stream_data()` は WebSocket router の media entry point です。session start 中は対象 input を queue し、message type に別 mode が必要なら client を作成・再構築できます。

- text input は `OmniOfflineClient.stream_text()` に渡します。
- audio bytes は bounded audio queue 経由で `OmniRealtimeClient.stream_audio()` に渡します。
- screenshot、camera frame など対応 visual input は処理後、現在 client の image path に渡します。
- 適切な active session がない場合、live vision frame は session を暗黙起動せず drop します。

Text session と audio session は明示的な代替関係です。Realtime transport error が新しい mode/start 判断なしに Offline session へ変換されることはありません。

## 出力とターン lifecycle

Model client は frontend へ直接書き込まず、manager callback を呼び出します。

| Callback | 責務 |
|---|---|
| `handle_new_message()` | realtime user-turn 境界で古い speech を interrupt し、resample/TTS state を reset して `speech_id` を rotate |
| `handle_text_data()` | assistant text を UI へ stream し、外部 TTS 利用時は TTS request queue にも送信 |
| `handle_audio_data()` | native model PCM を転送し、24 kHz PCM を frontend 用 48 kHz PCM へ resample |
| `handle_input_transcript()` | voice input の記録、activity/language state 更新、user context publish、memory/agent mirror の調整 |
| `handle_output_transcript()` | assistant transcript を mirror し、downstream system 用 turn text を維持 |
| `handle_response_complete()` | TTS を完了し、WebSocket と sync queue へ turn-end を送り、archive/prewarm と callback delivery を判断 |

各 reply には `speech_id` があります。Interruption と proactive race guard は、置換済み turn の遅延 TTS/model chunk をこの ID で拒否します。

`end_session()` は active/pending client を閉じ、receive/preparation work を cancel して stream state を reset し、準備済み hot swap を完了する場合があります。`cleanup(expected_websocket=...)` は ownership guard により、古い WebSocket disconnect が新しい connection を破棄するのを防ぎます。`shutdown()` は、character runtime が inactive manager を置換するときに長寿命 task を終了する同期 finalizer です。

## Hot swap と context append

Hot-swap preparation は turn/token threshold、renew signal、または追加 context により開始され、すべての新 session で無条件には実行されません。`_background_prepare_pending_session()` が接続済み pending client を作り、`_perform_final_swap_sequence()` が lock の下で昇格させ、移行中に到着した input/context を保持します。

`append_context()` は durable context injection の公開経路です。request を dedupe し、active/pending client を target にし、配信不能な context を次回 startup prompt 用に queue します。Model-visible proactive message は別契約で、`submit_proactive_callback()` / `enqueue_agent_callback()` により登録され、`trigger_agent_callbacks()` が response と voice-playback gate の下で配信します。

## Tool とメモリ

Manager は `ToolRegistry` を所有します。`register_tool_and_sync()` と対応する unregister/clear メソッドが active client の tool definition を更新します。`_register_builtin_tools()` は offline/realtime 両 session に `recall_memory` を登録します。

Model が利用するメモリ経路は二つあります。

1. **自動 new-dialog context** — startup/hot-swap が `GET /new_dialog/{lanlan_name}` を取得します。Memory Server は persona、利用可能な reflection、圧縮済み recent history、time-sensitive context を initial prompt に render します。
2. **オンデマンド recall** — `recall_memory` は自然言語 `query`、`time` expression、または両方を `POST /query_memory/{lanlan_name}` に送ります。検索対象は fact と reflection で、persona はすでに自動 render されるため除外されます。

Transcript persistence は `main_logic/cross_server.py` が別途調整します。incremental content は `/cache`、turn/session boundary は `/process`、`/renew`、`/settle` を使います。永続化された fact/reflection がすべて自動注入されるわけではありません。

## 実行境界

```text
Main Server asyncio loop
  ├─ frontend WebSocket receive/send
  ├─ LLMSessionManager lifecycle、client callback、tool/proactive task
  ├─ active-client receive task と任意の pending-session preparation
  ├─ キャラクター別 cross-server sync connector asyncio task
  └─ TTS response-handler asyncio task

Dedicated TTS worker thread
  └─ request/response queue を使う同期 provider worker
```

Async lock が session replacement、frontend write、cache、media queue を保護します。TTS worker が manager 内の主な thread boundary です。blocking queue wait と一部 filesystem/SDK 操作は `asyncio.to_thread()` へ offload されます。

## 主な呼び出し元と依存先

- `main_routers/websocket_router.py` が `start_session()`、`stream_data()`、`end_session()`、guard 付き `cleanup()` を呼びます。
- `app/main_server/character_runtime.py` が character ごとの manager、WebSocket lock、sync queue、cross-server connector task を所有します。
- `ConfigManager` が character、API、voice、response-length 設定を提供します。
- `OmniOfflineClient` / `OmniRealtimeClient` が model transport を提供します。
- Memory Server は initial context の必須依存です。Agent bridge と proactive delivery は任意の background result を提供します。
- `main_logic/tts_client/` が外部 TTS worker 関数を提供し、thread と queue は manager が所有します。
