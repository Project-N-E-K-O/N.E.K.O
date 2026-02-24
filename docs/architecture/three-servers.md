# Three-Server Design

## Main Server (`main_server.py`, port 48911)

The main server is a FastAPI application that serves as the user-facing entry point for all interactions.

### Startup sequence

1. **Configuration loading** — Load `config_manager`, initialize character data
2. **Session creation** — Create an `LLMSessionManager` for each defined character
3. **Static file mounting** — Mount `/static`, `/user_live2d`, `/user_vrm`, `/workshop`
4. **Router registration** — Include all 10 API routers
5. **Event handlers** — Initialize Steamworks, start ZeroMQ bridge, preload audio modules, detect language
6. **Uvicorn launch** — Bind to `127.0.0.1:48911`

### What it handles

- All REST API endpoints (10 routers)
- WebSocket connections for real-time chat (`/ws/{lanlan_name}`)
- TTS synthesis (threaded workers)
- Audio resampling (24kHz → 48kHz via soxr)
- Static file serving (models, CSS, JS, locales)
- HTML page rendering (Jinja2 templates)

## Memory Server (`memory_server.py`, port 48912)

The memory server manages persistent conversation history and semantic recall.

### Storage layers

| Layer | Purpose | Backend |
|-------|---------|---------|
| Recent memory | Last N messages per character | JSON files (`recent_*.json`) |
| Time-indexed original | Full conversation history | SQLite table |
| Time-indexed compressed | Summarized history | SQLite table |
| Semantic memory | Embedding-based recall | Vector store |

### Key operations

- **Store**: Save new conversation turns with timestamps
- **Query**: Retrieve recent context for LLM prompts
- **Search**: Semantic similarity search across all history
- **Compress**: Periodically summarize old conversations to save context window space
- **Review**: Allow users to browse and correct stored memories

## Agent Server (`agent_server.py`, port 48915)

The agent server handles background task execution triggered by conversation context.

### ZeroMQ addressing

| Socket | Address | Direction | Purpose |
|--------|---------|-----------|---------|
| PUB/SUB | `tcp://127.0.0.1:48961` | Main → Agent | Session events |
| PUSH/PULL | `tcp://127.0.0.1:48962` | Agent → Main | Task results |
| PUSH/PULL | `tcp://127.0.0.1:48963` | Main → Agent | Analyze requests |

### Task execution pipeline

1. Main server publishes a task via ZeroMQ
2. Agent server receives and creates a task plan (`planner.py`)
3. Actions execute through adapters:
   - **MCP Client** — Model Context Protocol tool calls
   - **Computer Use** — Screenshot analysis, mouse/keyboard actions
   - **Browser Use** — Web browsing automation
4. Results are analyzed (`analyzer.py`) and deduped (`deduper.py`)
5. Final results stream back via ZeroMQ (`task_result`, `proactive_message`)
