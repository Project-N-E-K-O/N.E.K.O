# Realtime Client

**Package:** `main_logic/omni_realtime_client/`

`OmniRealtimeClient` は、音声入力時に `LLMSessionManager` が選択するネイティブ音声会話経路を所有します。クラスは transport、response、audio、media/Gemini、tool の各 mixin から構成され、安定した `main_logic.omni_realtime_client` パスで import されます。

## Provider トランスポート

| Provider ルート | トランスポート動作 |
|---|---|
| Qwen | DashScope realtime WebSocket event |
| OpenAI / GPT | OpenAI realtime WebSocket event。上り音声は 24 kHz に変換 |
| Step | Step realtime WebSocket event |
| GLM | Zhipu realtime WebSocket event |
| Grok | Realtime WebSocket event 経路 |
| Gemini | raw WebSocket 実装ではなく Google GenAI SDK live session |
| Free route | endpoint に応じて Gemini-proxy/live-stream または Step 互換動作 |

クライアントは `api_type`、model、endpoint 設定から一つの branch を選びます。接続失敗は session manager へ通知され、暗黙に `OmniOfflineClient` を作成することはありません。

## 公開 lifecycle

| メソッド | 契約 |
|---|---|
| `connect(instructions, ...)` | provider session を開き、turn detection、音声、tool、instruction を設定 |
| `handle_messages()` | WebSocket transport の受信 loop を実行 |
| `update_session(config)` | provider 固有の session update を送信 |
| `stream_audio(audio_chunk)` | PCM input chunk を処理して upload |
| `stream_image(image_b64, bypass_rate_limit=False)` | visual frame を送信または解析 |
| `prime_context(text, skipped=False)` | provider 固有の意味で起動/hot-swap context を投入 |
| `create_response(instructions, skipped=False)` | user item を追加して response を要求 |
| `inject_text_and_request_response(text, on_rejected=None)` | proactive text injection と response request を atomic に実行 |
| `prompt_ephemeral(...)` | 一時的な proactive response を要求 |
| `cancel_response()` / `handle_interruption()` | provider が対応する範囲で active response を cancel/truncate |
| `close()` | background task を停止し、live transport と media/tool state を解放 |

Callback にはストリーミングテキスト・音声、入出力 transcript、response 完了、tool call、status、connection error があります。Interruption は lifecycle メソッドで処理し、このクライアントに汎用 constructor-level `on_interrupt` event 契約はありません。

## 音声とターン検出

クライアントは sample rate 引数なしの PCM bytes を受け取り、アプリが使う二つの capture format を区別します。

- PC の 480 sample / 960 byte chunk は 48 kHz で届き、RNNoise 経路を通って内部 16 kHz stream へ downsample されます。
- Mobile の 512 sample / 1024 byte chunk はすでに 16 kHz で、PC denoise を通りません。
- OpenAI realtime upload は最終送信時に内部 stream から 24 kHz へ resample されます。

`TurnDetectionMode.SERVER_VAD` が既定ですが、すべてのルートに server VAD があるわけではありません。Gemini、free Gemini proxy、livestream、明示的 manual mode は client-side turn handling を使います。ローカル fallback は RNNoise VAD を優先し、利用不能時は sustain/grace timing 付き RMS speech detection を使います。

## 画像

Qwen、GLM、GPT、Gemini、および互換 free Gemini route はネイティブ visual frame を受け取れます。それ以外の realtime model では、別途設定された vision model が現在のターンで最初の関連 frame を text context に変換します。

ネイティブ frame は `NATIVE_IMAGE_MIN_INTERVAL`（1.5 秒）で throttle されます。idle capture では `IMAGE_IDLE_RATE_MULTIPLIER`（5）が掛かります。`bypass_rate_limit=True` は proactive screenshot など意図的な one-shot cue に限って使います。

## Tool と proactive injection

Tool definition は正規化された後、選択 provider が対応する wire format へ encode され、結果は provider 固有 event で返されます。bounded sliding-window guard が realtime tool-call flood を防ぎます。

`inject_text_and_request_response()` は即時発話が必要な proactive callback で使います。別 response が session を所有している場合、二つの response stream を交錯させず、work を reject または requeue します。

## 並行処理、backpressure、障害

音声と画像処理には専用 async lock があります。send は semaphore で制限され、fire-and-forget work は `close()` で cancel できるよう追跡されます。HTTP/WebSocket 503 は短時間の send throttle を発動し、fatal frame、timeout、transport failure は connection-error callback を通知して session を閉じます。

これらは一つの live provider connection を保護する仕組みです。Provider failover と text/audio client の切り替えは session manager の責務です。
