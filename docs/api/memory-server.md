# Memory Server API

**Default address:** `http://127.0.0.1:48912`

**Configuration:** `MEMORY_SERVER_PORT`

The memory server is an internal, loopback-only service used by the main server, chat runtime, proactive-chat flow, games, and bundled plugins. It is not exposed through the main server as a generic proxy, and its routes are not a stable third-party HTTP contract. External integrations should prefer the main server's documented APIs.

The standalone entry point binds to `127.0.0.1`; the launcher can host the same FastAPI application in its managed runtime. Requests use plain HTTP inside the local process group.

## Shared conventions

- `{lanlan_name}` is a URL-encoded character name. Invalid names return `400`.
- History-write endpoints accept an object whose `input_history` field is itself a JSON-serialized message array:

  ```json
  {
    "input_history": "[{\"role\":\"user\",\"content\":\"Hello\"}]"
  }
  ```

- During storage selection, migration, or recovery, the service enters limited mode. Except for `/health`, `/shutdown`, and the two `/internal/storage/startup/*` controls, requests return `409`:

  ```json
  {
    "ok": false,
    "error_code": "storage_startup_blocked",
    "blocking_reason": "...",
    "limited_mode": true,
    "error": "..."
  }
  ```

- Several internal handlers report operational failure in a `200` response with `status: "error"` or `ok: false`. Internal callers must check the response body as well as the HTTP status.

## Runtime and lifecycle endpoints

| Method | Path | Parameters | Response |
|---|---|---|---|
| `GET` | `/health` | none | `{"app":"N.E.K.O","service":"memory","status":"ok","instance_id":"..."}` |
| `POST` | `/release_character/{lanlan_name}` | path only | `{"status":"success","character_name":"..."}` after releasing that character's SQLite handles; invalid names preserve their HTTP error status and other failures return `500` |
| `POST` | `/reload` | none | Rebuilds and atomically swaps memory components; returns `{"status":"success","message":"..."}` or `status: "error"` |
| `POST` | `/shutdown` | none | `shutdown_signal_received` when the standalone process enabled shutdown, otherwise `shutdown_disabled` |
| `POST` | `/internal/storage/startup/continue` | optional `{"reason":"..."}` | Releases limited mode after storage is ready: `{"ok":true,"initialized":true|false}`; still-blocked storage returns `409` |
| `POST` | `/internal/storage/startup/block` | optional `{"reason":"..."}` | Restores limited mode after an upstream startup failure: `{"ok":true,"limited_mode":true,"reason":"..."}` |
| `POST` | `/internal/memory/reset_confirmed_at` | none | Powerful-memory `ON` to `OFF` migration: `{"ok":true,"count":N}` or `{"ok":false,"error":"...","count":0}` |

The three `/internal/*` endpoints are control-plane calls between the main and memory processes. They must not be exposed as user-facing administration routes.

## Conversation persistence endpoints

All four endpoints use the `input_history` request shape described above.

### `POST /cache/{lanlan_name}`

The lightweight turn-end path. It appends non-empty history to `recent.json` without foreground compression, stores the raw chronological rows in `time_indexed.db`, and registers durable post-turn signal work. It does not run the Stage-1 fact-extraction LLM in the request path.

```json
{ "status": "cached", "count": 2 }
```

An empty message list returns `count: 0`. Failure returns `{"status":"error","message":"..."}`.

### `POST /process/{lanlan_name}`

Processes an increment of conversation history, allowing normal recent-history compression, writing raw chronological rows, scheduling post-turn work, and offering the history-review task to its gates.

```json
{ "status": "processed" }
```

Failure returns `{"status":"error","message":"..."}`.

### `POST /renew/{lanlan_name}`

Processes the first increment of a renewed session. It uses detailed compression while holding the per-character settle lock so `/new_dialog` cannot read a half-settled context. The remaining background work matches `/process`.

```json
{ "status": "processed" }
```

Failure returns `{"status":"error","message":"..."}`.

### `POST /settle/{lanlan_name}`

Completes a conversation that was already written through `/cache`. It runs detailed recent-history settlement even when `input_history` contains an empty list. If the request does contain uncached messages, those are also written to the time index and registered for post-turn processing.

```json
{ "status": "settled" }
```

Failure returns `{"status":"error","message":"..."}`.

## Context and recall endpoints

### `GET /new_dialog/{lanlan_name}`

Returns `text/plain` context for a new model session. For a valid character it renders the persona, active pending and confirmed reflections, dynamic inner-thought context, recent history, chat-gap hints, and holiday context. It waits for any in-progress `/renew` or `/settle` operation for the same character. An unknown character returns an empty string.

This endpoint does not perform semantic fact recall. Semantic or temporal recall is requested separately through `/query_memory` by the model's `recall_memory` tool.

### `GET /get_recent_history/{lanlan_name}`

Returns a localized formatted history string. Unknown characters receive the localized no-history string. This is used by game pre-session context and is distinct from `/new_dialog`'s full prompt context.

### `POST /query_memory/{lanlan_name}`

Structured recall over active facts, active reflections, and archived facts.

```json
{
  "query": "What food did the user like?",
  "time": "2026-05-01/2026-05-07"
}
```

Both fields are optional strings. The routing is:

| Input | Behavior |
|---|---|
| `query` only | BM25 and optional cosine recall, fused with reciprocal-rank fusion |
| `query` and valid `time` | Hard-filter to the time window, then run hybrid semantic recall |
| valid `time` only | Sort facts and reflections by distance from the requested event-time window |
| neither | Return an empty `results` list |
| invalid `time` with a query | Ignore the invalid window and fall back to query-only recall |

`time` accepts an hour (`2026-05-01T14`), day, month, year, or an inclusive pair of tokens separated by `/` or `..`. Full ISO timestamps are reduced to their containing hour.

Normal response:

```json
{
  "results": [
    {
      "id": "fact_...",
      "text": "Original memory text",
      "tier": "fact",
      "entity": "master",
      "score": 0.032787,
      "created_at": "2026-05-02T10:00:00",
      "event_start_at": "2026-05-01T00:00:00",
      "event_end_at": null
    }
  ],
  "query": "What food did the user like?",
  "candidates_total": 12,
  "elapsed_ms": 7.4
}
```

`tier` is `fact`, `reflection`, or `fact_archive`. Time-only results also include the input `time` and use `score: null`. If the runtime has not initialized its fact and reflection stores, the endpoint returns `503`. Other recall failures degrade to an empty successful response with `error_code: "hybrid_recall_failed"`; raw exception details are deliberately not returned.

Hybrid recall has no LLM fine-rerank in the user-visible tool loop. BM25 remains available when the optional local embedding service is disabled or still warming up.

### `GET /search_for_memory/{lanlan_name}/{query}` <Badge type="warning" text="Deprecated" />

Compatibility-only endpoint for old callers. It no longer performs semantic recall and returns localized placeholder text. New code must use `POST /query_memory/{lanlan_name}`.

### `GET /get_settings/{lanlan_name}`

Returns the rendered persona and active reflections as a formatted string. If persona data is unavailable, it falls back to the legacy settings renderer. Unknown characters receive an empty-settings string.

### `GET /get_persona/{lanlan_name}`

Returns the character's complete internal persona JSON object. No current memory-browser flow calls this route; it is reserved for internal or diagnostic consumers. The persona schema is versioned internal data and is not a stable editing contract.

### `GET /last_conversation_gap/{lanlan_name}`

```json
{ "gap_seconds": 1820.5 }
```

Returns `-1` when no previous conversation exists. Unexpected failure returns `500` with `{"gap_seconds":-1,"error":"server_error"}`.

## Reflection and proactive-chat endpoints

### `POST /reflect/{lanlan_name}`

Requests reflection synthesis and schedules the applicable automatic promotion path. Current proactive chat no longer calls this endpoint in its latency-sensitive path; periodic background synthesis and promotion loops provide the normal lifecycle.

```json
{
  "reflection": null,
  "auto_transitions": 0
}
```

`reflection` contains the synthesis result when available. `auto_transitions` is always `0` because promotion is fire-and-forget.

### `GET /followup_topics/{lanlan_name}`

Returns proactive-chat topic candidates without marking them as shown:

```json
{ "topics": [] }
```

The caller must submit the reflection IDs actually used to `/record_surfaced`.

### `POST /record_surfaced/{lanlan_name}`

```json
{ "reflection_ids": ["reflection_..."] }
```

Records proactive-chat surfacing and refreshes cooldowns. An empty or missing list is a no-op. The stable response is `{"ok":true}`; persistence failures are logged and do not fail the caller.

### `POST /cancel_correction/{lanlan_name}`

Cancels an in-flight recent-memory correction after a trusted manual edit.

```json
{ "status": "cancelled" }
```

Returns `{"status":"no_task"}` when no correction is running.

## Evidence analytics endpoint

### `GET /api/memory/funnel/{lanlan_name}`

**Query parameters**

| Name | Type | Required | Default |
|---|---|---:|---|
| `since` | ISO 8601 datetime | no | seven days before now |
| `until` | ISO 8601 datetime | no | now |

Returns read-only transition counts from the character's event log:

```json
{
  "lanlan_name": "小天",
  "since": "2026-05-01T00:00:00",
  "until": "2026-05-08T00:00:00",
  "counts": {
    "facts_added": 3,
    "reflections_synthesized": 1,
    "reflections_confirmed": 1,
    "reflections_promoted": 0,
    "reflections_merged": 0,
    "reflections_denied": 0,
    "reflections_archived": 0,
    "persona_entries_added": 0,
    "persona_entries_rewritten": 0,
    "persona_entries_archived": 0
  }
}
```

Invalid datetimes or `since > until` return `400`.

## Storage backend

Memory data is scoped under `memory/<character>/`. The principal stores are:

| Store | Purpose |
|---|---|
| `recent.json` and `recent_meta.json` | Working recent history and its compression metadata |
| `time_indexed.db` / `time_indexed_original` | Raw chronological conversation rows and timestamps |
| `facts.json` and `facts_archive.json` | Active extracted facts and older archived facts |
| `reflections.json` and `reflection_archive/` | Active reflection lifecycle and sharded reflection archives |
| `persona.json`, `persona_corrections.json`, and `persona_archive/` | Rendered long-term persona, correction state, and sharded archives |
| `events.ndjson`, `outbox.ndjson`, and `cursors.json` | Durable transition log, retryable work queue, and background-loop cursors |
| Additional sidecars | Surfacing cooldowns, synthesis backoff, pending fact deduplication, and other recoverable worker state |

`time_indexed_compressed` is a compatibility table only. New summaries are not written to it; durable abstractions are represented by facts, reflections, and persona entries. `retrieve_summary_by_timeframe` is deprecated and returns no data.

There is no standalone embedding database. Optional local CPU ONNX embeddings are cached on active entries with text and model fingerprints. Vector loading is lazy and can disable itself when the model, runtime, compatible CPU path, or minimum RAM is unavailable. Recall then continues with BM25.

## Current model tiers

Models are selected from configured tiers; memory code does not hardcode provider model names.

| Work | Current tier or runtime |
|---|---|
| Local text embeddings for optional cosine retrieval and dedup candidates | Bundled CPU ONNX profile under `data/embedding_models/<profile>/`; no API model tier |
| Recent-history compression, fact extraction, evidence signal detection, reflection synthesis and feedback checks, fact dedup decisions, and internal LLM recall reranking | `summary` |
| Recent-history review, persona correction/refinement, reflection refinement, and reflection promotion merge | `correction` |
| Negative-keyword target classification | `emotion` |
| User-facing `POST /query_memory` fusion | No LLM tier; BM25 plus optional cosine and reciprocal-rank fusion |

These are implementation defaults, not API guarantees. Operators configure the underlying model, base URL, and credentials for each tier.

## Main callers

- `main_logic/cross_server.py` drives `/cache`, `/process`, `/renew`, and `/settle`.
- Chat lifecycle and bundled channels fetch `/new_dialog`; the model tool handler calls `/query_memory`.
- Proactive chat reads `/followup_topics` and records used reflections through `/record_surfaced`.
- Character administration uses `/reload` and `/release_character`; the public memory browser uses `/cancel_correction` after manual recent-history edits.
- The main server owns storage startup controls, shutdown, and the powerful-memory migration call.
