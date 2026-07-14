# Voice Design Architecture

## Boundary

Voice Clone is an established feature on `main`. Its provider clients remain in:

```text
utils/voice_clone.py
```

Voice Design is the new, structurally dual feature. Its provider clients live in:

```text
utils/voice_design.py
```

Do not add Voice Design payloads, endpoint construction, response parsing, or
validation methods to `utils/voice_clone.py`. Likewise, do not put Voice Clone
provider behavior in `utils/voice_design.py`.

## Provider Matrix

| Provider | Voice Clone | Voice Design |
| --- | --- | --- |
| CosyVoice | `QwenVoiceCloneClient` | `_cosyvoice_design_voice` |
| MiniMax CN/Intl | `MinimaxVoiceCloneClient` | `_minimax_design_voice` |
| ElevenLabs | `_elevenlabs_clone_voice` | preview + create helpers |
| MiMo | `MimoVoiceCloneClient` | `MimoVoiceDesignClient` |

The different method shapes reflect upstream API differences:

- CosyVoice and MiniMax return reusable remote voice IDs in one request.
- ElevenLabs requires preview then create; the unified route performs both.
- MiMo has no remote enrollment ID. NEKO stores the description and sends it
  again when synthesizing with the Voice Design model.

## Router And Runtime

`main_routers/characters_router/voice_cloning.py` retains the existing route
module to avoid moving or rewriting Voice Clone. Voice Design routes import all
provider-specific Design operations from `utils.voice_design`.

`main_logic/tts_client/workers/` remains the synthesis layer. A saved designed
voice must use the same character prompt templates and routing contract as a
cloned voice; only provider-required voice input differs.

Provider capabilities and documented hard limits remain declarative in
`utils/tts/provider_registry.py` and the corresponding registrations in
`main_logic/tts_client/__init__.py`.
