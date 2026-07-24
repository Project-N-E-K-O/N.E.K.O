# Three-Server Design

The standalone development commands are `uv run python -m app.main_server`, `uv run python -m app.memory_server`, and `uv run python -m app.agent_server`. Each command executes the package's `__main__.py`; the FastAPI application and implementation stay in the package.

## Main Server (`app/main_server/`, port 48911)

`app/main_server/__main__.py` configures Uvicorn and serves the FastAPI `app` assembled by `app/main_server/__init__.py` and `web_app.py`. `character_runtime.py` owns the per-character runtime slots.

### Startup ownership

The Main Server:

1. resolves the storage startup state and initializes `ConfigManager`;
2. optionally imports cloud-save state and asks the Memory Server to reload when an import changed runtime data;
3. loads character data and creates or preserves each character's `LLMSessionManager`, WebSocket lock, queue, and sync connector task;
4. initializes Steam/Workshop integrations and dynamic static mounts;
5. starts the Main-side Agent ZeroMQ bridge, background warmups, game cleanup, and token tracking;
6. exposes the assembled routers and WebSocket endpoint on `127.0.0.1:48911`.

If storage selection is blocked, the server can remain in a limited mode until the browser releases the startup barrier. Startup is therefore not just a fixed sequence of imports.

### What it owns

- browser pages and mounted frontend/model/Workshop assets;
- REST routers and `/ws/{lanlan_name}` browser sessions;
- one `LLMSessionManager` per character role;
- explicit text (`OmniOfflineClient`) or audio (`OmniRealtimeClient`) model sessions;
- external TTS worker threads and frontend 48 kHz PCM delivery;
- HTTP clients for Memory/Agent control plus the Main-owned ZeroMQ sockets;
- the per-character cross-server stream used by memory persistence and optional monitor mirroring.

Native realtime audio arrives at 24 kHz on the relevant provider path and is resampled by the session manager to 48 kHz. External TTS workers already return the application's output PCM contract.

## Memory Server (`app/memory_server/`, port 48912)

The memory server owns per-character persistent memory. Live working context stays in the main server's LLM session; the memory server receives completed turns, maintains durable views, renders startup context, and answers explicit recall requests.

### Durable data and derived indexes

| Data | Purpose | Backend |
|---|---|---|
| Recent history | Sliding conversation window plus an LLM-compressed memo | Per-character `recent.json` |
| Time-indexed original | Chronological source conversation history | SQLite `time_indexed_original` table |
| Facts | Extracted statements with source and processing metadata | `facts.json` plus flat `facts_archive.json` |
| Reflections | Evidence-scored observations with pending, confirmed, promoted/merged, denied, and archived states | `reflections.json` plus archive shards |
| Persona | Durable character/user profile rendered into new-dialog context | `persona.json` plus archive shards |
| Retrieval indexes | BM25 and optional local-ONNX vectors over recall candidates | Derived caches; not the source of truth |

The legacy SQLite `time_indexed_compressed` table is retained for compatibility but receives no new writes. Recent summaries stay in `recent.json`; durable abstractions now live in facts, reflections, and persona.

### Key operations

- **Ingest and settle** completed turns, preserving the timestamped original history
- **Compress** the recent sliding window without replacing the chronological source record
- **Extract and refine** facts, detect evidence, synthesize reflections, and promote or merge stable observations into persona
- **Render** persona, usable reflections, and recent context for `/new_dialog`
- **Recall on demand** through time-only lookup or hybrid BM25/optional-vector retrieval with Reciprocal Rank Fusion (RRF); this latency-sensitive tool path does not add an LLM rerank
- **Recover and audit** mutations with cursors, an outbox, an event log, reconciliation, decay, and archive sweeps
- **Review recent history** through `/memory_browser`; that UI does not directly edit facts, reflections, or persona

`app/memory_server/__main__.py` is the standalone Uvicorn entry. The package can also be imported and mounted by the launcher. A storage startup barrier can keep mutation-heavy runtime work limited until the Main Server confirms the active storage root.

See [Memory System](/architecture/memory-system) for the complete lifecycle and automatic-versus-on-demand context boundary.

## Agent Server (`app/agent_server/`, ports 48915 and 48916)

`app/agent_server/__main__.py` starts the package's Tool Server FastAPI app on `127.0.0.1:48915`. The implementation is split across `api_runtime.py`, `api_routes.py`, `capabilities.py`, `registry.py`, `tracker.py`, `results.py`, `plugin_host.py`, and `channels/`.

During Agent startup, the process initializes capability state, `DirectTaskExecutor`, the Agent-side ZeroMQ bridge, channel probes, and background schedulers. `plugin_host.py` starts the embedded user-plugin FastAPI listener on `127.0.0.1:48916` in an isolated thread. User-plugin execution remains gated by its feature flag and plugin lifecycle even though the listener is part of the same process.

### HTTP ownership

- **`:48915` Tool Server** — Agent flags/capabilities, task submission and inspection, cancellation, health, proactive triggers, and internal channel control.
- **`:48916` embedded user-plugin service** — installed plugin discovery, run lifecycle, market bridge targets, and deferred plugin completion support.

The Main Server proxies the public `/api/agent/*` surface to these internal services. Browser code should treat the Main Server response as authoritative rather than calling process-local objects.

### ZeroMQ ownership

| Default address | Pattern | Bind owner | Direction | Purpose |
|---|---|---|---|---|
| `tcp://127.0.0.1:48961` | PUB / SUB | Main | Main → Agent | Session and lifecycle events |
| `tcp://127.0.0.1:48963` | PUSH / PULL | Main | Main → Agent | Reliable `analyze_request` queue |
| `tcp://127.0.0.1:48962` | PUSH / PULL | Main (`PULL`) | Agent → Main | ACKs, status, task updates, and results |

The Agent connects its SUB/PULL/PUSH mirror sockets. The bridge uses background receive threads around synchronous ZeroMQ sockets. Agent → Main delivery has no HTTP fallback.

### Task execution path

1. `main_logic/cross_server.py` publishes a bounded conversation view as an `analyze_request`; the Main bridge waits for an ACK and retries once on timeout.
2. The Agent applies master/capability gates, cancellation redaction, and deduplication.
3. `DirectTaskExecutor` in `brain/task_executor.py` assesses enabled channels. The retired Analyzer/Planner/Processor classes are not part of the live pipeline.
4. Non-plugin work selects the first executable channel by priority; user plugins use deterministic candidate filtering followed by validated LLM entry selection.
5. The selected adapter under `app/agent_server/channels/` registers the task, emits updates, performs or delegates the work, and records a terminal result.
6. ACKs, `task_update`, `task_result`, and proactive events return over `:48962`. The Main bridge updates the browser and queues model-visible results on the matching `LLMSessionManager`.

Cancellation marks registry state before provider-specific teardown so late provider output cannot overwrite a terminal task. Deferred user-plugin tasks stay running until their completion endpoint or timeout resolves them.

See [Agent System](/architecture/agent-system) for channel routing, flags, retention, and delivery semantics.
