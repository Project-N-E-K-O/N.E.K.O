# Quick Start

This page walks you through a first run of N.E.K.O. after completing the [Development Setup](./dev-setup).

## 1. Start the servers

```bash
# In separate terminals:
uv run python memory_server.py
uv run python main_server.py
```

## 2. Configure an API provider

Navigate to `http://localhost:48911/api_key` and configure at least the **Core API** provider.

For a quick test without an API key, select **Free** as the Core API provider.

## 3. Interact with the default character

Open `http://localhost:48911` in your browser. The default character ("小天") will be loaded with a Live2D model.

**Text mode:** Type a message in the chat input and press Enter.

**Voice mode:** Click the microphone button to start a voice session. Speak naturally — the system uses server-side VAD (Voice Activity Detection) to detect when you finish speaking.

## 4. Customize the character

Navigate to `http://localhost:48911/chara_manager` to:

- Change the character's name, gender, age, and personality traits
- Set a custom Live2D or VRM model
- Clone a custom voice (upload a ~15-second clean audio sample)
- Edit the system prompt for full control over behavior

## 5. Explore the Web UI pages

| URL | Purpose |
|-----|---------|
| `/` | Main chat interface |
| `/chara_manager` | Character settings |
| `/api_key` | API key configuration |
| `/model_manager` | Live2D/VRM model management |
| `/live2d_emotion_manager` | Emotion-to-animation mapping |
| `/vrm_emotion_manager` | VRM emotion mapping |
| `/voice_clone` | Voice cloning |
| `/memory_browser` | Browse and edit memories |
| `/steam_workshop_manager` | Steam Workshop items |

## Next steps

- [Project Structure](./project-structure) — Understand the codebase layout
- [Architecture Overview](/architecture/) — How the three servers cooperate
- [API Reference](/api/) — All REST and WebSocket endpoints
