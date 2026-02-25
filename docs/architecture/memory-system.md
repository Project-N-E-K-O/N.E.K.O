# Memory System

N.E.K.O.'s memory system provides persistent context across sessions, enabling characters to remember past conversations, user preferences, and evolving relationships.

## Storage layers

| Layer | Storage | Retention | Access pattern |
|-------|---------|-----------|---------------|
| **Recent memory** | JSON files (`recent_*.json`) | Sliding window | Direct read, per-character |
| **Time-indexed original** | SQLite (`time_indexed_original`) | Permanent | Time range queries |
| **Time-indexed compressed** | SQLite (`time_indexed_compressed`) | Permanent | Time range queries |
| **Semantic memory** | Vector embeddings (`text-embedding-v4`) | Permanent | Similarity search |

## How memory flows into conversations

1. When a new session starts, the system loads **recent memory** (last N messages) as immediate context.
2. A **semantic search** retrieves relevant past conversations based on the current topic.
3. A **time-indexed query** provides chronological context for temporal references ("yesterday", "last week").
4. All retrieved memory is injected into the LLM system prompt as context.

## Compression pipeline

Old conversations are periodically compressed to save context window space:

```
Raw conversation ──> Summary model (qwen-plus) ──> Compressed summary
                                                        │
                                                   Stored in time_indexed_compressed
```

The `ROUTER_MODEL` (default: `qwen-plus`) decides which memories are relevant enough to retain in full versus compress.

## Memory review

Users can browse and correct stored memories at `http://localhost:48911/memory_browser`. This helps address:

- Model hallucinations stored as "memories"
- Incorrect facts the character has internalized
- Repetitive patterns in conversation summaries

## API endpoints

See the [Memory REST API](/api/rest/memory) for the full endpoint reference.

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/memory/recent_files` | GET | List all memory files |
| `/api/memory/recent_file` | GET | Get specific memory file content |
| `/api/memory/recent_file/save` | POST | Save updated memory |
| `/api/memory/update_catgirl_name` | POST | Rename character across memories |
| `/api/memory/review_config` | GET/POST | Memory review settings |
