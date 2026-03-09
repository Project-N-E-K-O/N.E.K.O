# Memory System

N.E.K.O.'s memory system provides persistent context across sessions, enabling characters to remember past conversations, user preferences, and evolving relationships.

## Storage layers

| Layer | Storage | Retention | Access pattern |
|-------|---------|-----------|---------------|
| **Recent memory** | JSON files (`recent_*.json`) | Sliding window (max 10 messages) | Direct read, per-character |
| **Time-indexed original** | SQLite (`time_indexed_original`) | Permanent | Time range queries |
| **Time-indexed compressed** | SQLite (`time_indexed_compressed`) | Permanent | Time range queries |
| **Semantic memory** | Vector embeddings (`text-embedding-v4`) | Permanent | Hybrid search with LLM reranking |
| **Important settings** | JSON files (per-character) | Permanent | LLM-extracted preferences |

## How memory flows into conversations

1. When a new session starts, the system loads **recent memory** (last N messages) as immediate context.
2. A **semantic search** (`hybrid_search`) retrieves relevant past conversations based on the current topic, combining original + compressed results with LLM reranking.
3. A **time-indexed query** provides chronological context for temporal references ("yesterday", "last week").
4. **Important settings** (character preferences, relationships, knowledge) are loaded.
5. All retrieved memory is injected into the LLM system prompt as context, with bracket stripping for cleaner formatting.

## Compression pipeline

Conversation history is automatically compressed when the recent history exceeds `max_history_length` (default: 10 messages):

```
Raw conversation ──> LLM summary ("对话摘要" JSON format)
                         │
                    if > 500 chars ──> Further compression
                         │
                    Merge as SystemMessage(memorandum)
                         │
                    Stored in time_indexed_compressed
```

Compression uses 3-retry exponential backoff on failure. The `CompressedRecentHistoryManager` handles the full lifecycle.

## Important settings

The `ImportantSettingsManager` extracts and maintains character-specific knowledge from conversations:

1. **LLM Proposer**: Analyzes conversation to extract settings (traits, relationships, preferences)
2. **LLM Verifier**: Resolves contradictions between new and existing settings
3. **Reserved fields** are automatically stripped: `system_prompt`, `live2d`, `voice_id`, workshop data, rendering configs

## Memory review

The `review_history()` method runs asynchronous LLM-based auditing to detect:

- Contradictions and logical errors in stored memories
- Model hallucinations stored as "memories"
- Incorrect facts the character has internalized
- Repetitive patterns in conversation summaries

Review tasks are per-character and can be cancelled via `cancel_correction/{lanlan_name}` to prevent stale context corrections. Users can also browse memories at `http://localhost:48911/memory_browser`.

## API endpoints

See the [Memory REST API](/api/rest/memory) for the main server endpoints.

The memory server exposes internal endpoints:

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/cache/{lanlan_name}` | POST | Lightweight append (no LLM) |
| `/process/{lanlan_name}` | POST | Full processing (recent + time + semantic + review) |
| `/renew/{lanlan_name}` | POST | Hot-swap renewal |
| `/get_recent_history/{lanlan_name}` | GET | Formatted context for LLM prompts |
| `/search_for_memory/{lanlan_name}/{query}` | GET | Semantic search |
| `/get_settings/{lanlan_name}` | GET | Character settings JSON |
| `/new_dialog/{lanlan_name}` | GET | Context for new conversation (strips brackets) |
| `/reload` | POST | Reload all components |
| `/cancel_correction/{lanlan_name}` | POST | Abort review task |
