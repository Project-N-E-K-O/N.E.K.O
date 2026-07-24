# Architecture Overview

Project N.E.K.O. uses three principal Python service processes. The Main Server owns the UI and live sessions, the Memory Server owns durable memory, and the Agent Server assesses and executes optional background work. HTTP/WebSocket carry service and browser traffic; ZeroMQ carries the Agent event bridge.

## System diagram

![Architecture](/framework.svg)

## Principal services

| Service | Default port | Standalone entry | Role |
|---|---:|---|---|
| **Main Server** | 48911 | `app/main_server/__main__.py` | Web UI/static assets, REST API, browser WebSocket, per-character sessions, external TTS |
| **Memory Server** | 48912 | `app/memory_server/__main__.py` | Conversation ingest, recent context, facts/reflections/persona, startup rendering, and recall |
| **Agent Server / Tool Server** | 48915 | `app/agent_server/__main__.py` | Capability state, task assessment, channel dispatch, cancellation, and task results |

Each service's FastAPI application and implementation live in the matching package. In particular, the Agent implementation is `app/agent_server/`.

The Agent process also hosts an embedded user-plugin FastAPI service on `127.0.0.1:48916` in an isolated thread. It is a second HTTP listener, not a fourth principal process. The optional Monitor Server on `:48913` receives the per-character mirror stream and is also outside the core three-service control path.

## Communication map

```text
Browser
  │ HTTP + WebSocket
  ▼
Main Server :48911
  ├── HTTP ───────────────> Memory Server :48912
  ├── HTTP control ───────> Agent Tool Server :48915
  ├── HTTP proxy/calls ───> Embedded User-Plugin :48916
  ├── WebSocket mirror ───> Monitor Server :48913 (optional)
  └── ZeroMQ bridge
       PUB  :48961 ───────> Agent SUB       session/lifecycle events
       PUSH :48963 ───────> Agent PULL      reliable analyze requests
       PULL :48962 <─────── Agent PUSH      ACKs, task updates, results
```

The Main process binds all three ZeroMQ sockets; the Agent process connects the mirror sockets. Agent-to-Main results have no HTTP fallback.

## Key runtime patterns

### Per-character ownership

`app/main_server/character_runtime.py` owns one role-state slot per `lanlan_name`: an `LLMSessionManager`, an async WebSocket lock, a sync-message queue, and a cross-server connector asyncio task. Inactive managers can be replaced after their long-lived tasks are shut down; active or starting managers are preserved.

### Explicit session modes and conditional hot swap

Text input uses `OmniOfflineClient`; audio input uses `OmniRealtimeClient`. The manager does not silently fail over between them. Pending-session preparation is triggered by turn/token thresholds, renew state, or queued context, rather than unconditionally on every session start.

### Async, thread, and process boundaries

- FastAPI lifecycle, WebSocket I/O, model callbacks, cross-server connectors, and the Main-side Agent bridge coordination run on asyncio.
- External TTS provider workers run in per-character threads behind request/response queues.
- The Agent ZeroMQ bridge uses background receive threads around synchronous sockets so it works with the Windows Proactor loop.
- The embedded user-plugin HTTP server runs in its own thread but shares the Agent process.
- Memory and Agent state are process-local and are accessed through their public HTTP/ZeroMQ contracts, not by importing another process's runtime objects.

## Next

- [Three-Server Design](/architecture/three-servers) — service ownership and startup boundaries
- [Data Flow](/architecture/data-flow) — browser input through model output and persistence
- [Session Management](/architecture/session-management) — mode selection and hot-swap lifecycle
- [Memory System](/architecture/memory-system) — persistence, automatic rendering, and on-demand recall
- [Agent System](/architecture/agent-system) — assessment, channels, event delivery, and task state
