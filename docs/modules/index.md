# Core Modules

These pages document the Python runtime modules that assemble a conversation session. The source is package-based: the public import normally comes from each package's `__init__.py`, while the implementation is split into focused mixins and provider workers.

## Runtime map

| Module | Current source | Responsibility |
|---|---|---|
| [LLMSessionManager](/modules/core) | `main_logic/core/` | Owns session lifecycle and coordinates input, model, memory, tools, and speech output |
| [Realtime Client](/modules/omni-realtime) | `main_logic/omni_realtime_client/` | Runs native audio/realtime sessions over provider-specific transports |
| [Offline Client](/modules/omni-offline) | `main_logic/omni_offline_client/` | Runs streamed text and vision turns against chat-completion APIs |
| [TTS Client](/modules/tts-client) | `main_logic/tts_client/` | Resolves an external TTS worker and exposes the worker queue contract |
| [Config Manager](/modules/config-manager) | `utils/config_manager/` | Resolves runtime storage, migrations, character data, API profiles, and persisted settings |

## How they compose

`LLMSessionManager` reads normalized settings through `ConfigManager`, then selects one conversation client from the input mode:

- text input creates `OmniOfflineClient`;
- audio input creates `OmniRealtimeClient`;
- external speech output additionally starts the worker returned by `get_tts_worker()`;
- native-audio realtime providers send their own audio and do not need the external TTS path.

The offline client is therefore not an automatic failure fallback for the realtime client. Switching between them is a session-mode decision made by the manager.

## Execution boundaries

- Configuration and most persistence methods are synchronous filesystem operations. Async callers must use the provided `a*` wrappers where available or offload work explicitly.
- Both conversation clients expose async methods. Realtime keeps a persistent transport and background receive tasks; offline performs a streamed request per turn.
- External TTS runs in a dedicated worker thread. The session manager bridges async model output to that thread with request and response queues.

Start with [LLMSessionManager](/modules/core) for the call path, then use the client and TTS pages for provider-specific behavior.
