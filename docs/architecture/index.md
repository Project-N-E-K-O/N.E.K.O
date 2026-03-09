# Architecture Overview

Project N.E.K.O. is built as a **multi-process microservice system** where four independent servers cooperate through WebSocket, HTTP, and ZeroMQ messaging, orchestrated by a unified launcher.

## System diagram

![Architecture](/framework.svg)

## Four-server design

| Server | Port | Entry point | Role |
|--------|------|-------------|------|
| **Main Server** | 48911 | `main_server.py` | Web UI, REST API, WebSocket chat, TTS |
| **Memory Server** | 48912 | `memory_server.py` | Semantic recall, time-indexed history, memory compression, settings |
| **Monitor Server** | 48913 | `monitor.py` | Health checks, config endpoints for launcher |
| **Agent Server** | 48915 | `agent_server.py` | Background task execution (MCP, Computer Use, Browser Use, User Plugins) |

The main server is the user-facing entry point. It serves the Web UI, handles all REST API requests, and maintains WebSocket connections for real-time voice/text chat. The memory, agent, and monitor servers are internal services — the main server communicates with them via HTTP, ZeroMQ, and WebSocket respectively.

## Communication patterns

```
┌──────────────────────────────────────────┐
│              Main Server (:48911)         │
│                                          │
│  FastAPI ─── REST Routers (11 routers)   │
│  WebSocket ─── LLMSessionManager         │
│  ZeroMQ PUB ───┐                         │
│  ZeroMQ PULL ──┼── AgentEventBridge      │
│  HTTP Client ──┤                         │
└────────────────┼─────────────────────────┘
                 │
     ┌───────────┼───────────┐
     │           │           │
     ▼           ▼           ▼
  Memory      Agent      Monitor
  Server      Server     Server
  (:48912)    (:48915)   (:48913)
```

- **Main ↔ Memory**: HTTP requests for storing/querying memories, settings extraction, and memory review
- **Main ↔ Agent**: ZeroMQ pub/sub for task delegation and result streaming; HTTP proxy for flags and capabilities
- **Main ↔ Monitor**: HTTP for health checks, config state, and launcher coordination
- **Launcher → All**: Process management, port fallback, graceful shutdown

## Key architectural patterns

### Hot-swap sessions

The `LLMSessionManager` prepares a new LLM session in the background while the current session is still active. A ~40-second cycle governs session lifetime — after the threshold, memory is archived and a new session is pre-warmed in the background. When the current turn ends, it seamlessly swaps to the pre-warmed session with zero downtime. Audio is cached during the transition and flushed afterward with rate-limiting.

### Per-character isolation

Each character (identified by `lanlan_name`) gets its own:
- `LLMSessionManager` instance
- Sync connector thread
- WebSocket lock
- Message queue
- Shutdown event

Characters can be added or removed at runtime without restarting — existing active sessions are preserved while config is hot-reloaded.

### Async/sync boundary

FastAPI handlers are async. TTS synthesis runs in a dedicated thread with queue-based communication. Audio processing uses `soxr` stateful resamplers (24kHz → 48kHz). The ZeroMQ event bridge runs a background recv thread with NOBLOCK sends and ACK/retry mechanisms.

### Startup lock

The launcher uses platform-specific locks (Windows Named Mutex `Global\NEKO_LAUNCHER_STARTUP_LOCK` / POSIX file lock) to prevent duplicate launcher instances.

### Health-check signatures

All servers return `{"app": "N.E.K.O", "service": "..."}` in their health endpoints. The launcher uses this signature to distinguish N.E.K.O. processes from other services that might occupy the same port.

## Next

- [Server Architecture](./three-servers) — Detailed breakdown of each server
- [Data Flow](./data-flow) — Request lifecycle from frontend to LLM and back
- [Session Management](./session-management) — Hot-swap mechanism deep dive
