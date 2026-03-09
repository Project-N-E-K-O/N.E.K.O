# Server Architecture

## Main Server (`main_server.py`, port 48911)

The main server is a FastAPI application that serves as the user-facing entry point for all interactions.

### Startup sequence

1. **Configuration loading** — Load `config_manager`, initialize character data, detect documents directory
2. **Session creation** — Create an `LLMSessionManager` for each defined character
3. **Static file mounting** — Mount `/static`, `/user_live2d`, `/user_vrm`, `/workshop`
4. **Router registration** — Include all 11 API routers (agent, characters, config, cookies_login, live2d, memory, music, pages, system, vrm, websocket, workshop)
5. **Event handlers** — Initialize Steamworks, start ZeroMQ bridge, preload audio modules, detect language, start sync connector threads
6. **Uvicorn launch** — Bind to `127.0.0.1:48911`

### What it handles

- All REST API endpoints (11 routers)
- WebSocket connections for real-time chat (`/ws/{lanlan_name}`)
- TTS synthesis (threaded workers with request/response queues)
- Audio resampling (24kHz → 48kHz via soxr stateful `ResampleStream`)
- Static file serving (models, CSS, JS, locales)
- HTML page rendering (Jinja2 templates)
- Agent event bridge (ZeroMQ PUB/SUB + PUSH/PULL)
- Cross-server sync connector daemon threads (memory analysis, monitor sync)

## Memory Server (`memory_server.py`, port 48912)

The memory server manages persistent conversation history, semantic recall, and character settings extraction.

### Storage layers

| Layer | Purpose | Backend |
|-------|---------|---------|
| Recent memory | Last N messages per character with LLM compression | JSON files (`recent_*.json`) |
| Time-indexed original | Full conversation history | SQLite table |
| Time-indexed compressed | Summarized history | SQLite table |
| Semantic memory | Embedding-based recall | Vector store |
| Important settings | Character preferences and knowledge extracted from conversations | JSON files |

### Key operations

- **Cache**: Lightweight append of new turns (no LLM processing)
- **Process**: Full processing pipeline — recent memory update, time-indexed storage, semantic indexing, and review
- **Renew**: Hot-swap renewal — archive current context for session transition
- **Search**: Semantic similarity search across all history (`hybrid_search` with LLM reranking)
- **Settings**: Extract and verify character preferences from conversations (`ImportantSettingsManager`)
- **Review**: Async LLM-based auditing for contradictions and logic errors (`review_history`)
- **New Dialog**: Prepare context for a new conversation turn (with bracket stripping)

### REST endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/health` | GET | Service health check (N.E.K.O. signature) |
| `/cache/{lanlan_name}` | POST | Lightweight append (no LLM) |
| `/process/{lanlan_name}` | POST | Full processing (recent + time + semantic + review) |
| `/renew/{lanlan_name}` | POST | Hot-swap renewal |
| `/get_recent_history/{lanlan_name}` | GET | Formatted context for LLM prompts |
| `/search_for_memory/{lanlan_name}/{query}` | GET | Semantic search |
| `/get_settings/{lanlan_name}` | GET | Character settings JSON |
| `/new_dialog/{lanlan_name}` | GET | Context for new conversation (strips brackets) |
| `/reload` | POST | Reload all components |
| `/cancel_correction/{lanlan_name}` | POST | Abort review task |

## Agent Server (`agent_server.py`, port 48915)

The agent server handles background task execution triggered by conversation context, using the `DirectTaskExecutor` for parallel capability assessment.

### ZeroMQ addressing

| Socket | Address | Direction | Purpose |
|--------|---------|-----------|---------|
| PUB/SUB | `tcp://127.0.0.1:48961` | Main → Agent | Session events |
| PUSH/PULL | `tcp://127.0.0.1:48962` | Agent → Main | Task results |
| PUSH/PULL | `tcp://127.0.0.1:48963` | Main → Agent | Analyze requests |

### REST endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/health` | GET | Service health check (N.E.K.O. signature) |
| `/status` | GET | Agent state snapshot (tasks, flags, capabilities) |
| `/flag_set/{flag_name}/{value}` | POST | Control agent feature flags |
| `/capabilities` | GET | Available agent capabilities |
| `/task/spawn_computer_use` | POST | Queue a Computer Use task |
| `/task/{task_id}` | GET | Get task status and result |
| `/task/{task_id}` | DELETE | Cancel/delete a task |
| `/reload_config` | POST | Reload API configurations |

### Task execution pipeline

1. Main server publishes an analyze request via ZeroMQ
2. `DirectTaskExecutor` runs **parallel assessment** of all available methods:
   - `_assess_mcp()` — Can an MCP tool handle this?
   - `_assess_browser_use()` — Can browser automation handle this?
   - `_assess_computer_use()` — Can desktop GUI automation handle this?
   - `_assess_user_plugin()` — Can a local plugin handle this?
3. Priority-based selection: **MCP > Browser Use > Computer Use > User Plugin**
4. The selected adapter executes the task
5. Results are analyzed (`analyzer.py`) and deduped (`deduper.py`)
6. Final results stream back via ZeroMQ (`task_result`, `proactive_message`)

## Monitor Server (`monitor.py`, port 48913)

The monitor server is a lightweight FastAPI service that provides health and configuration endpoints for the launcher and inter-process coordination.

### Purpose

- **Health monitoring**: Provides health-check endpoints with N.E.K.O. signatures so the launcher can verify service status
- **Configuration relay**: Exposes current configuration state for other processes
- **Launcher coordination**: Enables the launcher to manage startup ordering, port fallback, and graceful shutdown

### Startup lock

The launcher uses platform-specific mechanisms to prevent duplicate instances:
- **Windows**: Named Mutex `Global\NEKO_LAUNCHER_STARTUP_LOCK`
- **POSIX**: File lock in the system temp directory
