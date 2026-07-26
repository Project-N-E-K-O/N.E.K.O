# Audio Streaming

Audio is asymmetric on the application WebSocket: microphone input is JSON sample arrays, while server speech output is a JSON header followed by binary audio.

## Wire formats

| Direction | Transport | Current first-party format |
|---|---|---|
| Client → server | JSON text frame | `stream_data.data` is an array of signed PCM16 sample integers. Mono, little-endian when packed by Python. |
| Server → client | JSON header + binary frame | Normally mono signed PCM16 at 48,000 Hz. The bundled player can also identify Ogg Opus binary chunks for compatibility. |

The application route does **not** accept base64 microphone audio and does not call `receive_bytes()` for client input.

## Start the voice session first

```json
{ "action": "start_session", "input_type": "audio" }
```

Wait for:

```json
{ "type": "session_started", "input_mode": "audio" }
```

before sending samples. `session_preparing` is not readiness. If startup fails, handle both the machine-readable `status` event and `session_failed`.

## Microphone input

The bundled AudioWorklet converts Float32 capture into signed PCM16, resamples desktop capture to 48 kHz and mobile capture to 16 kHz, then sends about 10 ms per JSON frame:

```json
{
  "action": "stream_data",
  "input_type": "audio",
  "data": [12, -41, 203, 98]
}
```

The backend packs the integer list with little-endian 16-bit `struct.pack`. Values outside the signed 16-bit range or non-integers can fail packing and the chunk is discarded.

Do not infer arbitrary sample-rate support from the array length. The implemented first-party paths are:

- 480 samples per 10 ms at 48 kHz on desktop;
- 160 samples per 10 ms at 16 kHz on mobile.

The exact provider transport may then resample those bytes again to the selected realtime API's native rate.

### Ordering and backpressure

`audio`, `avatar_drop_image`, and `user_image` stream messages are awaited in the router to preserve order. Other media can be scheduled asynchronously. The JSON representation is intentionally simple for the browser/Electron bridge but is bandwidth-heavy; this is a local UI protocol, not an efficient WAN audio transport.

When a game route is active, audio also feeds its realtime STT path.

## Noise reduction

Noise reduction is optional and controlled by the conversation preference `noiseReductionEnabled`. In the current backend, the dedicated 48 kHz preprocessing path is recognized by a 480-sample chunk. When enabled and available, the audio processor can buffer, denoise, and downsample before provider upload; an empty preprocessing result means "not enough buffered audio yet" and that frame is not forwarded.

Do not state that `pyrnnoise` is always loaded or that every arbitrary chunk size receives identical preprocessing.

## Speech boundary detection

Realtime providers normally own speech/VAD boundary detection after receiving the audio stream. The application also has a manager-level silence timeout that can emit:

```json
{
  "type": "auto_close_mic",
  "reason_code": "silence_timeout",
  "api_type": "...",
  "message": "..."
}
```

and then end the voice session. VAD behavior and thresholds therefore depend on the selected provider/configuration; there is no standalone WebSocket VAD configuration message.

## Server speech output

For each chunk the server writes two consecutive frames:

1. Header text frame:

   ```json
   { "type": "audio_chunk", "speech_id": "speech-id" }
   ```

2. One binary frame containing that chunk's audio bytes.

The bundled decoder associates binary frames with queued headers in arrival order. Do the same: never treat an unpaired binary frame as a new speech turn. `speech_id` remains stable across chunks of one speech and lets the client discard late audio from an interrupted turn.

Most TTS workers normalize native 24 kHz (or other provider-specific rates) to 48 kHz PCM16 with streaming `soxr`. That is a worker implementation detail, not proof that every provider originates at 24 kHz. Detect Ogg (`OggS`) if interoperating with compatibility providers; otherwise decode as little-endian PCM16 at 48 kHz.

There is no separate `audio_end` frame in this protocol. Text/turn lifecycle and frontend queue drain determine completion. The first-party client reports real audible boundaries back to the server:

```json
{ "action": "voice_play_start", "turnId": "speech-id", "source": "audio_playback" }
```

```json
{ "action": "voice_play_end", "turnId": "speech-id", "source": "audio_playback" }
```

These events keep proactive delivery from interrupting audio that has been generated but not yet finished playing.

## Interruption

Provider user-activity events and client-side `speech_id` tracking implement barge-in. On interruption the manager clears pending TTS work and the frontend drops queued/late chunks for the interrupted speech. Because frames already in transit cannot be recalled, clients must continue filtering by `speech_id` rather than assuming the next binary frame is always playable.

`pause_session` and `end_session` are explicit stop controls; both tear down the current provider session while leaving the application socket open.

## Image input during voice mode

`screen` and `camera` are realtime media input types, carried as image data URLs in `stream_data.data`. Optional `avatar_position` is paired with the fresh image. Capture cadence, idle throttling, and source selection are frontend policy, not fixed WebSocket protocol guarantees.
