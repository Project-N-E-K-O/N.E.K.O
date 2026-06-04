# NEKO Local Lightweight TTS

This service is the first-phase local TTS bridge for NEKO. It intentionally
keeps the same WebSocket protocol expected by `local_cosyvoice_worker`, so NEKO
can use it without changing the main TTS pipeline.

## Protocol

Endpoint:

```text
ws://127.0.0.1:50000/v1/audio/speech/stream
```

Client messages:

```json
{"voice":"kokoro:zf_001","speed":1.0}
{"text":"Hello from NEKO."}
{"text":"Local Kokoro TTS test."}
{"event":"end"}
```

Server response:

```text
binary PCM s16le chunks, mono, 22050 Hz
```

NEKO's existing `local_cosyvoice_worker` then resamples this audio to 48 kHz.

## Start

From the repository root:

```bash
uv run python local_server/local_tts_server/server.py --host 127.0.0.1 --port 50000
```

In NEKO settings, use the existing local custom TTS path:

```text
ws://127.0.0.1:50000
```

Enable custom TTS and set the URL to `ws://` or `wss://`. WebSocket custom TTS
routes to `local_cosyvoice_worker` directly; the HTTP GPT-SoVITS path is only
used for `http://` or `https://` custom TTS URLs.

## WebSocket Origin Safety

The synthesis WebSocket accepts local non-browser clients without an `Origin`
header and browser clients from `localhost`, `127.0.0.1`, or `[::1]`. To allow
another trusted local UI origin, set `LOCAL_TTS_ALLOWED_ORIGINS` to a
comma-separated list of exact origins, for example
`http://127.0.0.1:48911,http://localhost:48911`.

## Voice Selector

The service accepts a model prefix in `voice`:

```text
kokoro:<voice>
melotts:<voice>
melo:<voice>
chattts:<voice>
```

If the prefix is missing, `LOCAL_TTS_DEFAULT_MODEL` is used. The default is
`kokoro`.

## Kokoro / MeloTTS / ChatTTS

These are exposed through command adapters for now. The command must write a
16-bit WAV file to `{out_file}`.

The Kokoro launcher defaults to the Chinese-enhanced
`hexgrad/Kokoro-82M-v1.1-zh` model and voice `zf_001`.
It expects a local model directory under
`local_server/local_tts_server/kokoro_models/Kokoro-82M-v1.1-zh` or an explicit
`LOCAL_TTS_KOKORO_MODEL_DIR`. Hugging Face auto-download is disabled by the
launcher so model provenance stays user-managed.

Kokoro voices are static `.pt` files under the selected model directory. This
server exposes `/v1/voices` for discovery and the shared
`/v1/audio/speech/stream` WebSocket for synthesis, but it does not implement
CosyVoice-style speaker cloning or registration. Calls to
`/v1/speakers/register` return a clear unsupported response instead of creating
local voice metadata.

### Windows examples

```powershell
set LOCAL_TTS_KOKORO_MODEL_DIR=F:\models\Kokoro-82M-v1.1-zh
set LOCAL_TTS_KOKORO_REPO_ID=hexgrad/Kokoro-82M-v1.1-zh
set LOCAL_TTS_KOKORO_DEFAULT_VOICE=zf_001
set LOCAL_TTS_KOKORO_CMD=python F:\tts_wrappers\kokoro_cli.py "{text_file}" "{out_file}" "{voice}" {speed}
set LOCAL_TTS_MELOTTS_CMD=python F:\tts_wrappers\melotts_cli.py --text-file "{text_file}" --out "{out_file}" --voice "{voice}" --speed {speed}
set LOCAL_TTS_CHATTTS_CMD=python F:\tts_wrappers\chattts_cli.py --text-file "{text_file}" --out "{out_file}" --voice "{voice}" --speed {speed}
```

### Linux / macOS examples

```bash
export LOCAL_TTS_KOKORO_MODEL_DIR=/models/Kokoro-82M-v1.1-zh
export LOCAL_TTS_KOKORO_REPO_ID=hexgrad/Kokoro-82M-v1.1-zh
export LOCAL_TTS_KOKORO_DEFAULT_VOICE=zf_001
export LOCAL_TTS_KOKORO_CMD='python /opt/tts_wrappers/kokoro_cli.py "{text_file}" "{out_file}" "{voice}" {speed}'
export LOCAL_TTS_MELOTTS_CMD='python /opt/tts_wrappers/melotts_cli.py --text-file "{text_file}" --out "{out_file}" --voice "{voice}" --speed {speed}'
export LOCAL_TTS_CHATTTS_CMD='python /opt/tts_wrappers/chattts_cli.py --text-file "{text_file}" --out "{out_file}" --voice "{voice}" --speed {speed}'
```

ChatTTS is AGPL-3.0. Keep it as an optional external backend unless the product
licensing story is settled.
