# API Reference

N.E.K.O.'s main FastAPI server listens on port `48911` by default. These pages document the routes that exist in the current source tree; they do **not** mean that every route is a stable, public, or remotely safe API.

## Compatibility boundary

| Surface | Intended consumer | Compatibility expectation |
|---|---|---|
| [Runtime Tools API](/api/rest/tools) | Local plugins and companion processes | Documented local integration contract |
| [Main WebSocket protocol](/api/websocket/protocol) | N.E.K.O. web, Electron, and mobile clients | Documented client protocol; message families marked internal may change with the first-party UI |
| [Cloud Save API](/api/rest/cloudsave) | Local data-management clients | Documented destructive data-operation contract; require explicit user action |
| Other main-server REST routes | First-party N.E.K.O. pages and contributors | Implementation-facing; may evolve with the UI and are not a general public web API |
| Memory server and Agent server | Main-server-to-service traffic | Internal only; use through the main server unless debugging N.E.K.O. itself |

N.E.K.O. does not currently publish a separately versioned HTTP API with a blanket backward-compatibility guarantee. The stable extension surface is the [plugin system](/plugins/); use the runtime-tools contract only when a plugin needs to expose model-callable callbacks.

## Base URL and security

```text
http://127.0.0.1:48911
```

There is no blanket authentication layer in front of the main API. Some sensitive integration routes, including `/api/tools` and `/api/capture`, enforce loopback access themselves; many first-party UI routes do not. Do not expose port `48911` to an untrusted LAN or the public Internet. Provider API keys are managed by the [configuration system](/config/), not by an API bearer token.

Paths documented here do not end in `/` unless explicitly shown.

## Main-server REST routes

### Documented integration and data operations

| Router | Prefix | Boundary |
|---|---|---|
| [Runtime tools](/api/rest/tools) | `/api/tools` | Loopback-only plugin callback registration |
| [Cloud save](/api/rest/cloudsave) | `/api/cloudsave` | Character-unit upload/download; destructive operations |
| [Capture bridge](/api/rest/capture) | `/api/capture` | Loopback-only first-party Electron/GalGame bridge |

### First-party application routes

These pages are useful for contributors and alternative local clients, but the routes primarily serve N.E.K.O.'s own UI.

| Router | Prefix | Scope |
|---|---|---|
| [Config](/api/rest/config) | `/api/config` | Provider settings, preferences, connectivity tests |
| [Characters](/api/rest/characters) | `/api/characters` | Character, persona, card, voice, and avatar operations |
| [Live2D](/api/rest/live2d) | `/api/live2d` | Live2D models and emotion mappings |
| [VRM](/api/rest/vrm) | `/api/model/vrm` | VRM models, configuration, animations, expressions |
| [MMD](/api/rest/mmd) | `/api/model/mmd` | MMD model and motion management |
| [PNGTuber](/api/rest/pngtuber) | `/api/model/pngtuber` | PNGTuber model management |
| [Memory](/api/rest/memory) | `/api/memory` | Recent-memory files, review/settings, rename, and legacy cleanup; recall uses the internal `/query_memory` route |
| [Agent proxy](/api/rest/agent) | `/api/agent` | Main-server proxy, task state, flags, and diagnostics |
| [Steam Workshop](/api/rest/workshop) | `/api/steam/workshop` | Workshop browsing, staging, publishing, and subscriptions |
| [Music](/api/rest/music) | `/api/music` | Music search and playback proxy |
| [Jukebox](/api/rest/jukebox) | `/api/jukebox` | Song and action library |
| [Minigames](/api/rest/game) | `/api/game` | Minigame state and actions |
| [GalGame](/api/rest/galgame) | `/api/galgame` | GalGame reply option generation |
| [Icebreaker](/api/rest/icebreaker) | `/api/icebreaker` | New-user onboarding flows |
| [Proactive chat](/api/rest/proactive) | `/api/proactive` | Proactive-chat mode and settings |
| [System](/api/rest/system) | `/api` | Startup, prompts, screenshots, utilities, Steam and diagnostics |

## WebSocket

The main application socket is `ws://127.0.0.1:48911/ws/{character_name}`.

| Page | Contents |
|---|---|
| [Protocol](/api/websocket/protocol) | Connection lifecycle, session actions, and security boundary |
| [Message Types](/api/websocket/message-types) | Client actions, input data, and server events |
| [Audio Streaming](/api/websocket/audio-streaming) | JSON PCM input and binary-frame server audio output |

## Internal and unversioned surfaces

The main server also mounts first-party implementation routers that intentionally do not have public reference pages:

- `/api/storage/location` — first-launch storage selection, migration, directory picker, restart, and retained-source cleanup.
- `/api/avatar-drop` — composer document parsing helper whose output follows the current first-party UI.
- `/api/card-assist` — character-card generation/refinement flows coupled to current prompts and configured LLM providers.
- `/api/auth` — local cookie and QR-login state, including compatibility endpoints; credential-sensitive.
- `/api/debug` — evolving diagnostic snapshots and browser health reports.
- `/health` — lightweight launcher/process health probe.
- `/api/beacon/shutdown` — browser-mode lifecycle control; not an application integration endpoint.
- `/market` and `/market/{path}` — an opaque same-origin reverse proxy to the user-plugin server, not a schema owned by the main API.

Do not build third-party integrations against these surfaces unless you also control the matching N.E.K.O. version.

## Internal service APIs

| Server | Default address | Boundary |
|---|---|---|
| [Memory Server](/api/memory-server) | `http://127.0.0.1:48912` | Internal memory lifecycle, rendering, and recall |
| [Agent Server](/api/agent-server) | `http://127.0.0.1:48915` | Internal Agent execution and ZeroMQ transport |

## Responses and content types

Response envelopes are router-specific. FastAPI/Pydantic validation commonly uses `detail`; several application routers instead return `success`, `error`/`code`, and message fields. Follow the contract on each page and branch on machine-readable codes rather than English messages.

Common request/response types include JSON, `multipart/form-data` for uploads, audio responses for voice previews, and binary WebSocket frames for server audio.
