# TTS パイプライン

N.E.K.O. には `OmniRealtimeClient` の provider-native audio と、プロジェクトの external TTS runtime という2つの音声出力経路があります。本ページの queue/worker pipeline は後者であり、すべての音声セッションが使うわけではありません。

## 経路選択

`LLMSessionManager._resolve_session_use_tts()` が external runtime の使用を決定します。

| 状況 | 音声経路 |
|---|---|
| Text session | External TTS runtime |
| 対応 provider-native voice の audio session | Realtime provider native audio |
| Custom/clone voice または指定 external provider の audio session | External TTS runtime |
| Free realtime service の livestream route | Server-native audio |
| `DISABLE_TTS` | Dummy worker、音声なし |

Voice routing は構造化された source/provider/reference、現在の model config、保存済み clone metadata を使います。Voice ID は provider をまたいで一意ではありません。

## External runtime

```text
LLM text delta
    │ provider class に応じた normalize / strip
    ▼
thread-safe request Queue
    │ (speech_id, text) / control sentinel
    ▼
provider worker thread
    │ provider protocol + source-rate conversion
    ▼
thread-safe response Queue ──> async response handler
                                  │
                                  ├─ JSON {type: "audio_chunk", speech_id}
                                  └─ following WebSocket binary PCM frame
```

Queue と daemon thread は `LLMSessionManager` ごとに所有されます。Provider protocol が異なっても worker signature は `(request_queue, response_queue, api_key, voice_id)` です。

Worker は大きく2種類です。

- `ws_bistream`: long-lived WebSocket で text fragment と audio を双方向 streaming。Qwen、Step、CosyVoice など。Provider cadence を乱さないよう CJK whitespace normalization を省略します。
- `http_sentence`: 清掃済み text を sentence 単位で synthesis request にします。OpenAI、Gemini、MiniMax、MiMo、Doubao など。

どちらも Markdown marker と括弧内 stage direction を発話対象から除外します。

## Provider dispatch

`main_logic/tts_client.get_tts_worker()` はまず `utils/tts/provider_registry.py` から priority 順に special provider を選び、その後 native/core default に fallback します。

| Priority | Provider | 選択条件 |
|---:|---|---|
| 10 | GPT-SoVITS | 有効な local custom TTS |
| 20 | vLLM-Omni | 明示 endpoint/model 選択 |
| 30 | MiniMax | Clone metadata |
| 40 | ElevenLabs | Clone/design voice metadata |
| 50 | CosyVoice | Clone metadata |
| 60 | MiMo | Selected preset または clone metadata |
| 65 | Doubao TTS | Clone metadata |

Special route がなければ Qwen、free service、Step、CogTTS/GLM、Gemini、OpenAI、Grok の core default を使います。Unsupported native combination は無関係な provider を呼ばず dummy worker に fallback します。

Selection と credential は同時に解決されます。Provider-specific API-key override により、別の TTS config slot の credential を誤用しません。

## Streaming と完了

LLM delta は `speech_id` 付きです。Worker 未 ready 時は `tts_pending_chunks` に text を保存し、worker の `__ready__` 後に順番どおり flush します。

| Item | 意味 |
|---|---|
| `(speech_id, text)` | 現在の utterance の text を合成 |
| `(None, None)` | 現 utterance を finish/flush。worker は生存 |
| `("__interrupt__", None)` | 現 synthesis を停止し late callback を mute |
| `("__shutdown__", None)` | worker thread を終了 |

Done marker は readiness と pending-text flush の両方が満たされるまで遅延します。Speech-ID check により古い turn の done が新 turn を終了させません。

## 中断

新しいユーザー活動が現在の発話を preempt したとき、session/turn path が中断を開始します。汎用 provider `on_interrupt` callback に依存しません。

`_clear_tts_pipeline()` は response audio を drain し、live worker に `__interrupt__` を送り、text normalizer/stripper を reset し、短時間 mute を待ってから late audio と pending text を再度クリアします。

Frontend には中断された `speech_id` を含む user activity も届き、正しい utterance の playback を停止できます。Mirror-speech が native-audio session でも external worker を保持し得るため、interruption gate は `use_tts` ではなく worker liveness です。

## Audio contract とバックプレッシャー

Worker は response queue に入れる前に provider output を 48,000 Hz、mono、signed 16-bit little-endian PCM に変換します。多くの provider は 24 kHz ですが、GPT-SoVITS などは異なる source rate を使えるため、resampler は各 worker が所有します。

ブラウザは `audio_chunk` JSON header の次に raw binary audio を受けます。`speech_id` は header にあり PCM 内にはありません。

別系統の microphone input queue は最大300件で、満杯時に最古を捨てます。これは session input streaming 用で、TTS request/response queue とは別です。

## エラー復旧

Worker は response queue から ready、reconnecting、warning、error を報告します。Runtime は credential rejection、rate limit、quota、policy block、connection failure などを分類します。

Retryable error は当初 silent retry し、繰り返し失敗後に frontend へ通知します。Non-retryable code は即時通知します。Delayed respawn は expected session/TTS mode を確認し、変更済み session の worker を復活させません。

## Voice 作成と preview

Voice management は streaming synthesis と別です。

| Endpoint | 用途 |
|---|---|
| `POST /api/characters/voice_clone` | Multipart clone flow |
| `POST /api/characters/voice_clone_direct` | Direct/provider-specific clone |
| `POST /api/characters/voice_design_preview` | ElevenLabs design preview |
| `POST /api/characters/voice_design_create` | Designed voice の保存 |
| `GET /api/characters/voices` | 有効な voice catalog |
| `GET /api/characters/voice_preview` | Provider-aware preview |

Clone endpoint は DashScope 専用ではありません。CosyVoice 国内/国際、MiniMax 国内/国際、ElevenLabs、MiMo、vLLM-Omni、Doubao TTS に対称な分岐があります。Remote voice ID を返す provider もあれば、reference audio をローカル保存して synthesis ごとに inline する provider もあります。

## 実装マップ

| 関心事 | File |
|---|---|
| 経路選択、queue、中断、配信 | `main_logic/core/tts_runtime.py` |
| Provider dispatch/registration | `main_logic/tts_client/__init__.py` |
| Provider worker | `main_logic/tts_client/workers/` |
| Dispatch metadata | `utils/tts/provider_registry.py` |
| Native voice routing | `utils/tts/native_voice_registry.py` |
| Voice source storage | `utils/voice_config.py` |
| Clone/design endpoint | `main_routers/characters_router/voice_cloning.py` |
| Preview/catalog | `main_routers/characters_router/voice_preview.py` |
