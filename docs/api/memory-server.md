# Memory Server API

**Port:** 48912 (internal)

The memory server runs as a separate process and handles all persistent memory operations. It is not intended for direct external access — the main server proxies memory-related requests.

## REST endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/health` | GET | Health check with N.E.K.O. signature |
| `/query` | POST | Retrieve recent + semantic memory for prompt construction |
| `/store` | POST | Store a new conversation turn with timestamp and embedding |
| `/compress` | POST | Compress old conversations into summaries |
| `/recent` | GET | Get recent conversation messages (up to 10) |
| `/search` | POST | Semantic similarity search across past conversations |
| `/review` | GET | Retrieve review history of memory corrections |
| `/review` | POST | Submit a memory correction or `cancel_correction` |
| `/settings` | GET | Get important settings extracted by LLM |
| `/settings` | POST | Update or regenerate important settings |

## Storage backend

| Layer | Backend | Purpose |
|-------|---------|---------|
| Recent | JSON file | Last 10 messages, fast context retrieval |
| Time-indexed (original) | SQLite | Full conversation history with timestamps |
| Time-indexed (compressed) | SQLite | Summarised old conversations |
| Semantic | Vector store | Embeddings for similarity search |
| Important Settings | JSON file | LLM-extracted user preferences and facts |

## Key components

| Component | Module | Role |
|-----------|--------|------|
| `RecentMemory` | `memory/recent.py` | JSON-based recent message buffer (max 10) |
| `TimeIndexedMemory` | `memory/timeindex.py` | Dual-SQLite original + compressed store |
| `SemanticMemory` | `memory/semantic.py` | Embedding + reranker similarity search |
| `ImportantSettingsManager` | `memory/settings.py` | LLM Proposer→Verifier extraction of user preferences |
| `MemoryRouter` | `memory/router.py` | FastAPI router exposing all endpoints |

## Models used

| Task | Default model |
|------|---------------|
| Embeddings | `text-embedding-v4` |
| Summarization | `qwen-plus` (SUMMARY_MODEL) |
| Routing | `qwen-plus` (ROUTER_MODEL) |
| Reranking | `qwen-plus` (RERANKER_MODEL) |

## Communication

The main server communicates with the memory server via HTTP requests through a persistent sync connector thread (`cross_server.py`). Memory queries are issued during prompt construction to build context windows that include recent conversation, semantically relevant past exchanges, and user preference settings.
