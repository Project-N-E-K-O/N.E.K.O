# Audio Streaming

Application WebSocket の audio は asymmetric です。microphone input は JSON sample array、server speech output は JSON header + binary audio です。

## Wire format

| Direction | Transport | 現 first-party format |
|---|---|---|
| Client → server | JSON text frame | `stream_data.data` は signed PCM16 sample integer array。mono、Python pack 後 little-endian。 |
| Server → client | JSON header + binary frame | 通常 48,000 Hz mono signed PCM16。同梱 player は compatibility 用 Ogg Opus chunk も識別。 |

Application route は base64 microphone audio を受けず、client input に `receive_bytes()` もしません。

## 先に voice session を開始

```json
{ "action": "start_session", "input_type": "audio" }
```

次を待ってから sample を送ります。

```json
{ "type": "session_started", "input_mode": "audio" }
```

`session_preparing` は readiness ではありません。Startup failure は machine-readable `status` と `session_failed` を両方処理してください。

## Microphone input

同梱 AudioWorklet は Float32 capture を signed PCM16 に変換し、desktop は 48 kHz、mobile は 16 kHz へ resample、約 10 ms ごとに JSON frame を送ります。

```json
{ "action": "stream_data", "input_type": "audio", "data": [12, -41, 203, 98] }
```

Backend は integer list を little-endian 16-bit `struct.pack` します。signed 16-bit 範囲外や非整数は packing failure となり chunk を discard します。

Array length から arbitrary sample-rate 対応を推測しないでください。実装済み first-party path は:

- desktop: 48 kHz、10 ms あたり 480 samples;
- mobile: 16 kHz、10 ms あたり 160 samples。

その後 provider transport が選択 realtime API の native rate へ再 resample する場合があります。

### Ordering / backpressure

Router は順序維持のため `audio`、`avatar_drop_image`、`user_image` を await します。他 media は async schedule の場合があります。JSON 表現は browser/Electron bridge には単純ですが bandwidth-heavy です。local UI protocol であり効率的 WAN audio transport ではありません。

Game route active 中は audio が realtime STT path にも入ります。

## Noise reduction

Noise reduction は任意で、conversation preference `noiseReductionEnabled` が制御します。現 backend は 480-sample chunk で専用 48 kHz preprocessing path を認識します。有効かつ processor available なら buffer、denoise、downsample 後に provider へ送信。空結果は buffer 不足を意味し、その frame は forward しません。

`pyrnnoise` が常時 load される、または任意 chunk size が同じ preprocessing を受けるとは記述できません。

## Speech boundary detection

Realtime provider が通常 audio stream 受信後の speech/VAD boundary を担当します。Application には manager-level silence timeout もあり、次を送る場合があります。

```json
{
  "type": "auto_close_mic",
  "reason_code": "silence_timeout",
  "api_type": "...",
  "message": "..."
}
```

その後 voice session を終了します。VAD behavior/threshold は provider/config 依存で、WebSocket に独立 VAD config message はありません。

## Server speech output

各 chunk は連続する 2 frame です。

1. Text header:

   ```json
   { "type": "audio_chunk", "speech_id": "speech-id" }
   ```

2. その chunk の audio byte を持つ binary frame 1 つ。

同梱 decoder は binary frame を到着順 header queue に対応付けます。同様に実装し、unpaired binary を新 speech として扱わないでください。1 speech の chunk は同じ `speech_id` を使い、interrupted turn の late audio を client が捨てられます。

多くの TTS worker は streaming `soxr` で provider native 24 kHz（または他 rate）から 48 kHz PCM16 へ正規化します。Worker detail であり全 provider が 24 kHz 起源という意味ではありません。Compatibility provider は Ogg (`OggS`) の場合があり、それ以外は 48 kHz little-endian PCM16 として decode します。

独立した `audio_end` frame はありません。Text/turn lifecycle と frontend queue drain で完了を判断します。同梱 client は audible boundary を server に返します。

```json
{ "action": "voice_play_start", "turnId": "speech-id", "source": "audio_playback" }
```

```json
{ "action": "voice_play_end", "turnId": "speech-id", "source": "audio_playback" }
```

これにより生成済みだが再生未完了の audio を proactive delivery が中断しません。

## Interruption

Provider user-activity event と client `speech_id` tracking で barge-in を実現します。Interruption 時 manager は pending TTS を clear し、frontend は interrupted speech の queued/late chunk を drop。既に transit 中の frame は回収できないため、常に `speech_id` で filter してください。

`pause_session` と `end_session` は明示 stop control で、現在 provider session を teardown し application socket は維持します。

## Voice mode の image input

`screen` と `camera` は realtime media input type で、image data URL を `stream_data.data` に入れます。任意 `avatar_position` は fresh image と対になります。Capture cadence、idle throttling、source selection は frontend policy で、固定 WebSocket guarantee ではありません。
