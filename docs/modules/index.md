# Core Modules

This section provides deep dives into N.E.K.O.'s core Python modules for developers who need to understand or modify the internal logic.

## Module map

| Module | File | Purpose |
|--------|------|---------|
| [LLMSessionManager](./core) | `main_logic/core.py` | Central session coordinator |
| [Realtime Client](./omni-realtime) | `main_logic/omni_realtime_client.py` | WebSocket client for Realtime APIs |
| [Offline Client](./omni-offline) | `main_logic/omni_offline_client.py` | Text-based LLM client (fallback) |
| [TTS Client](./tts-client) | `main_logic/tts_client.py` | Text-to-Speech synthesis |
| [Config Manager](./config-manager) | `utils/config_manager.py` | Configuration loading and persistence |
