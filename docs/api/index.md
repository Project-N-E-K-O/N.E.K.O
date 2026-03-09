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
| [Config](/api/rest/config) | `/api/config` | API keys, preferences, provider settings, language |
| [Characters](/api/rest/characters) | `/api/characters` | Character CRUD, voice settings, voice cloning, character cards |
| [Live2D](/api/rest/live2d) | `/api/live2d` | Live2D model management, emotion mapping |
| [VRM](/api/rest/vrm) | `/api/model/vrm` | VRM model management, animations, lighting |
| [Memory](/api/rest/memory) | `/api/memory` | Memory files, review configuration |
| [Agent](/api/rest/agent) | `/api/agent` | Agent flags, capabilities, tasks, health checks |
| [Workshop](/api/rest/workshop) | `/api/steam/workshop` | Steam Workshop subscriptions, publishing, metadata |
| [System](/api/rest/system) | `/api` | Emotion analysis, screenshots, utilities |
| Pages | `/pages` | Static page serving via Jinja2 templates |

## WebSocket

| Endpoint | Description |
|----------|-------------|
| [Protocol](/api/websocket/protocol) | Connection lifecycle and session management |
| [Message Types](/api/websocket/message-types) | All client→server and server→client message formats |
| [Audio Streaming](/api/websocket/audio-streaming) | Binary audio format, interruption, resampling |

## Internal APIs

These are inter-service APIs not intended for external use:

| Server | Port | Description |
|--------|------|-------------|
| [Memory Server](/api/memory-server) | 48912 | Memory storage, retrieval, settings extraction, review |
| [Agent Server](/api/agent-server) | 48915 | Agent task execution, capability flags |
| Monitor Server | 48913 | Health checks, configuration relay for launcher |

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
