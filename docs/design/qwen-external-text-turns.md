# Qwen external text turns

## Enablement

The phase-3 path is opt-in and defaults off:

```text
NEKO_INDEPENDENT_ASR_ENABLED=1
NEKO_INDEPENDENT_ASR_CORE_TYPE=qwen
NEKO_INDEPENDENT_ASR_LANGUAGE=zh
NEKO_SMART_TURN_THRESHOLD=0.5
NEKO_SMART_TURN_CANDIDATE_SILENCE_MS=300
```

Run `tools/voice_eval/prepare_voice_turn_assets.py` once in a source checkout.
Packaged builds use the same SHA-256-verified assets from `data/vad_models`.

## Hard boundaries

- Independent ASR must support manual endpointing. A server-VAD-only ASR route
  is rejected instead of being allowed to bypass Smart Turn.
- Microphone PCM goes to independent ASR and the local Smart Turn models only.
  It never falls through to Qwen's `input_audio_buffer.append` path.
- ASR partial/final events are caption data. Only one Smart Turn completion is
  persisted through `handle_input_transcript` and submitted to Qwen.
- Qwen receives a persistent `input_text` user item plus a JSON-escaped copy in
  per-response instructions. User text is explicitly marked untrusted and
  cannot close the instruction delimiter.
- Every client-issued `response.create` uses one global arbiter. The lane is
  held through item ACK, `response.created`, and `response.done/error/cancel`.
- Item ACK timeout never retries `conversation.item.create`; the response uses
  instructions insurance and reports uncertain context persistence.
- Production logs contain turn IDs, character counts, hashes, timings, and
  state only. They do not contain raw transcripts.

## Initial timeouts

| Stage | Timeout | Action |
|---|---:|---|
| `conversation.item.created` | 1.5 s | Continue with instructions; do not resend item |
| `response.created` | 5 s | Fail the queued request |
| `response.done` | 60 s | Send `response.cancel` |
| cancel terminal event | 3 s | Treat the realtime connection as unhealthy |

## Deliberately out of scope

- ASR worker/provider algorithm changes
- Smart Turn model/inference changes
- Uploading duplicate user audio to Qwen
- A settings-page redesign; the first release uses explicit environment flags
- Replacing local history with Qwen server history
