# Architecture Overview

Project N.E.K.O. is built as a **multi-process microservice system** where three main servers cooperate through WebSocket, HTTP, and ZeroMQ messaging.

## System diagram

![Architecture](/framework.svg)

## Three-server design

| Server | Port | Entry point | Role |
|--------|------|-------------|------|
| **Main Server** | 48911 | `main_server.py` | Web UI, REST API, WebSocket chat, TTS |
| **Memory Server** | 48912 | `memory_server.py` | Semantic recall, time-indexed history, memory compression |
| **Agent Server** | 48915 | `agent_server.py` | Background task execution (MCP, Computer Use, Browser Use) |

The main server is the user-facing entry point. It serves the Web UI, handles all REST API requests, and maintains WebSocket connections for real-time voice/text chat. The memory and agent servers are internal services that the main server communicates with.

## Communication patterns

```
┌──────────────────────────────────────────┐
│              Main Server (:48911)         │
│                                          │
│  FastAPI ─── REST Routers                │
│  WebSocket ─── LLMSessionManager         │
│  ZeroMQ PUB ───┐                         │
│  ZeroMQ PULL ──┼── AgentEventBridge      │
│  HTTP Client ──┤                         │
└────────────────┼─────────────────────────┘
                 │
        ┌────────┼────────┐
        │        │        │
        ▼        ▼        ▼
   Memory     Agent    Monitor
   Server     Server   Server
   (:48912)   (:48915) (:48913)
```

- **Main ↔ Memory**: HTTP requests for storing/querying memories
- **Main ↔ Agent**: ZeroMQ pub/sub for task delegation and result streaming
- **Main ↔ Monitor**: WebSocket for real-time status updates

## Key architectural patterns

### Hot-swap sessions

The `LLMSessionManager` prepares a new LLM session in the background while the current session is still active. When the user ends a conversation turn, it seamlessly swaps to the pre-warmed session with zero downtime. Audio is cached during the transition and flushed afterward.

### Per-character isolation

Each character (identified by `lanlan_name`) gets its own:
- `LLMSessionManager` instance
- Sync connector thread
- WebSocket lock
- Message queue
- Shutdown event

### Async/sync boundary

FastAPI handlers are async. TTS synthesis runs in a dedicated thread with queue-based communication. Audio processing uses executor thread pools. The ZeroMQ event bridge runs a background recv thread.

## Next

- [Three-Server Design](./three-servers) — Detailed breakdown of each server
- [Data Flow](./data-flow) — Request lifecycle from frontend to LLM and back
- [Session Management](./session-management) — Hot-swap mechanism deep dive
