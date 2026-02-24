# Audio Streaming

## Audio format

| Parameter | Client → Server | Server → Client |
|-----------|----------------|-----------------|
| Sample rate | Depends on input device | 48,000 Hz |
| Bit depth | 16-bit signed | 16-bit signed |
| Encoding | PCM little-endian | PCM little-endian |
| Channels | Mono | Mono |
| Transport | Base64 in JSON | Base64 in JSON |

## Input pipeline

```
Microphone ──> Browser AudioContext ──> PCM chunks ──> Base64 ──> WebSocket
                                                                    │
                                                               Main Server
                                                                    │
                                                    OmniRealtimeClient.send_audio()
                                                                    │
                                                              LLM Provider
```

The server handles sample rate conversion internally. Input audio at any common sample rate (44.1kHz, 48kHz, etc.) is accepted.

## Output pipeline

```
LLM Provider ──> on_audio_delta ──> 24kHz PCM
                                        │
                                   soxr resampler
                                        │
                                   48kHz PCM ──> Base64 ──> WebSocket ──> Browser
```

The `soxr` library provides high-quality sample rate conversion from the LLM's native 24kHz to the browser's 48kHz playback rate.

## Interruption

When the user starts speaking while the character is outputting audio:

1. LLM provider fires `on_interrupt`
2. TTS request and response queues are flushed
3. Pending audio frames are discarded
4. Character stops speaking immediately
5. System begins processing new user input

This enables natural turn-taking in voice conversations.

## Voice Activity Detection (VAD)

N.E.K.O. uses **server-side VAD** by default. The LLM provider (e.g., Qwen Omni) detects speech boundaries automatically. This means:

- No client-side VAD configuration needed
- The server decides when the user has finished speaking
- Natural pauses within speech are handled intelligently

## Native image input

During voice sessions, the system can also capture and send screen data:

- Minimum interval: **1.5 seconds** between captures
- Idle rate multiplier: **5x** (images sent less frequently when no voice activity)
- Images are sent alongside audio for multi-modal understanding

## Noise reduction

Optional noise reduction using `pyrnnoise`:

- Loaded lazily on first audio input
- Applied before sending audio to the LLM provider
- Can be disabled if input audio is already clean
