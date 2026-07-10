# API Reference

N.E.K.O. exposes a comprehensive API surface through FastAPI. All endpoints are served from the main server (default `http://localhost:48911`).

## Base URL

```
http://localhost:48911
```

## Authentication

The API does not require authentication for local access. API keys for LLM providers are managed separately through the [Configuration](/config/) system.

## REST endpoints

| Router | Prefix | Description |
|--------|--------|-------------|
| [Config](/api/rest/config) | `/api/config` | API keys, preferences, provider settings |
| [Characters](/api/rest/characters) | `/api/characters` | Character CRUD, voice settings, microphone |
| [Live2D](/api/rest/live2d) | `/api/live2d` | Live2D model management, emotion mapping |
| [VRM](/api/rest/vrm) | `/api/model/vrm` | VRM model management, animations |
| [Memory](/api/rest/memory) | `/api/memory` | Memory files, review configuration |
| [Agent](/api/rest/agent) | `/api/agent` | Agent flags, tasks, health checks |
| [Workshop](/api/rest/workshop) | `/api/steam/workshop` | Steam Workshop items, publishing |
| [MMD](/api/rest/mmd) | `/api/model/mmd` | MMD model management |
| [PNGTuber](/api/rest/pngtuber) | `/api/model/pngtuber` | PNGTuber model management |
| [Music](/api/rest/music) | `/api/music` | Music search and playback proxy |
| [Jukebox](/api/rest/jukebox) | `/api/jukebox` | Song and action library |
| [Game](/api/rest/game) | `/api/game` | Minigame backend |
| [Galgame](/api/rest/galgame) | `/api/galgame` | Galgame reply options |
| [Icebreaker](/api/rest/icebreaker) | `/api/icebreaker` | New-user onboarding |
| [Proactive](/api/rest/proactive) | `/api/proactive` | Proactive-chat mode and settings |
| [System](/api/rest/system) | `/api` | Emotion analysis, screenshots, utilities |

### Other / internal routers

The following routers are live but not individually documented here: `capture` (`/api/capture`), `cloudsave` (`/api/cloudsave`), `storage-location` (`/api/storage/location`), `avatar-drop` (`/api/avatar-drop`), `card-assist` (`/api/card-assist`), `auth`/cookies (`/api/auth`), `tool` (`/api/tools`), and `debug` (`/api/debug`).

## WebSocket

| Endpoint | Description |
|----------|-------------|
| [Protocol](/api/websocket/protocol) | Connection lifecycle and session management |
| [Message Types](/api/websocket/message-types) | All client→server and server→client message formats |
| [Audio Streaming](/api/websocket/audio-streaming) | Binary audio format, interruption, resampling |

## Internal APIs

These are inter-service APIs not intended for external use:

| Server | Description |
|--------|-------------|
| [Memory Server](/api/memory-server) | Memory storage and retrieval (port 48912) |
| [Agent Server](/api/agent-server) | Agent task execution (port 48915) |

## Response format

All REST endpoints return JSON. Successful responses typically include the data directly. Error responses follow FastAPI's default format:

```json
{
  "detail": "Error message describing what went wrong"
}
```

## Content types

- `application/json` — Most endpoints
- `multipart/form-data` — File uploads (models, voice samples)
- `audio/*` — Voice preview responses
