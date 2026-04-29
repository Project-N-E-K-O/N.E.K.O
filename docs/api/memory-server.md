# Memory Server API

**Port:** 48912 (internal)

The memory server runs as a separate process and handles all persistent memory operations. It is not intended for direct external access — the main server proxies memory-related requests.

## Internal endpoints

The memory server provides endpoints for:

- **Storing** new conversation turns with timestamps and embeddings
- **Querying** recent context for LLM prompt construction
- **Searching** semantically similar past conversations
- **Compressing** old conversations into summaries
- **Managing** memory review settings

## Storage backend

| Table | Purpose |
|-------|---------|
| `time_indexed_original` | Full conversation history |
| `time_indexed_compressed` | Summarized conversation history |
| Embedding store | Vector embeddings for semantic search |

## Models used

| Task | Source |
|------|--------|
| Embeddings | Local ONNX model bundled under `data/embedding_models/<profile>/` (see `memory/embeddings.py::EmbeddingService`) |
| Fact extraction / signal detection / reflection / promotion merge / fact dedup / recall rerank | Tier `summary` (`get_model_api_config('summary')`) |
| History review / persona correction | Tier `correction` (`get_model_api_config('correction')`) |
| Negative-target keyword check | Tier `emotion` |

## Communication

The main server communicates with the memory server via HTTP requests and a persistent sync connector thread (`cross_server.py`).
