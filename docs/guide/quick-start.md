# Quick Start

If you are choosing between Steam, a standalone release, and a source checkout, read [Install options](./install-options) first.

Complete [Development Setup](./dev-setup) first.

## 1. Launch

```bash
uv run python launcher.py
```

The launcher starts the cooperating services and reports selected ports. Use the reported main URL; `http://127.0.0.1:48911` is only the preferred default.

## 2. Configure providers

Open `/api_key` on the main URL. Select a Core provider and, if needed, an Assist provider. Enter credentials for the selected provider and run connectivity checks. Provider/model lists are revision-specific; do not copy old screenshots.

## 3. Open chat

A fresh data root is initialized from locale-specific character defaults and the bundled Yui Origin asset. Character identifiers/display names come from active data; the old fixed “小天” claim is no longer valid.

Text chat uses the shared React component. Voice availability depends on the selected core/TTS path and microphone permission.

## 4. Customize and inspect

| Route | Purpose |
| --- | --- |
| `/character_card_manager` | Character data and avatar selection |
| `/model_manager` | Avatar model management |
| `/live2d_emotion_manager` | Live2D emotion mapping |
| `/vrm_emotion_manager` | VRM emotion mapping |
| `/voice_clone` | Provider-dependent voice cloning |
| `/memory_browser` | Memory outputs and processing state |
| `/api_key` | Provider and credential settings |

Not every provider or deployment mode supports every feature. Agent and hosted-plugin capabilities require the agent/tool service.

## Next steps

- [Configuration](/config/)
- [Architecture](/architecture/)
- [API Reference](/api/)
- [Testing](/contributing/testing)
