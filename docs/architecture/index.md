# Architecture Overview

Project N.E.K.O. is built as a **multi-process microservice system** where three main servers cooperate through WebSocket, HTTP, and ZeroMQ messaging.

## System diagram

![Architecture](/framework.svg)

## Three-server design

| Server | Port | Entry point | Role |
|--------|------|-------------|------|
| **Main Server** | 48911 | `app/main_server.py` | Web UI, REST API, WebSocket chat, TTS |
| **Memory Server** | 48912 | `app/memory_server.py` | Semantic recall, time-indexed history, memory compression |
| **Agent Server** | 48915 | `app/agent_server.py` | Background task execution (Computer Use, Browser Use, OpenClaw remote agent, OpenFang standalone agent, User Plugins) |

The main server is the user-facing entry point. It serves the Web UI, handles all REST API requests, and maintains WebSocket connections for real-time voice/text chat. The memory and agent servers are internal services that the main server communicates with.

## Communication patterns

```
┌────────────────────────────────────────────────┐
│              Main Server (:48911)                │
│                                                  │
│  FastAPI ─── REST Routers                        │
│  WebSocket ─── LLMSessionManager                 │
│  HTTP Client (httpx) ───────────┐                │
│  ZeroMQ PUB  (:48961) ──┐       │                │
│  ZeroMQ PUSH (:48963) ──┼── MainServerAgentBridge│
│  ZeroMQ PULL (:48962) ──┘       │                │
└─────────┬───────────────────────┼────────────────┘
          │                       │
    ┌─────┴─────┐         ┌───────┴────────┐
    │           │         │                │
    ▼           ▼         ▼                ▼
  Memory     Monitor    Agent Server   User-Plugin
  Server     Server     (Tool Server)  Server
  (:48912)   (:48913)   (:48915)       (:48916)
   HTTP      one-way     HTTP REST +    HTTP REST
            status push  ZeroMQ events
```

- **Main ↔ Memory**: HTTP requests for storing/querying memories (memory server `:48912`)
- **Main ↔ Agent**: two channels working together —
  - **Control / dispatch**: HTTP REST via `httpx` to the Tool Server (`:48915`), plus the User-Plugin server (`:48916`)
  - **Event streaming**: ZeroMQ, where the main process binds the sockets — `PUB :48961` (session → agent), `PUSH :48963` (analyze queue → agent), `PULL :48962` (agent → main results); `agent_server` connects the mirror sockets
- **Main ↔ Monitor**: one-way status push to the monitor server (`:48913`)

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
