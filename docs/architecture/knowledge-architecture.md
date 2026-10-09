# Public Knowledge

Public knowledge is reference material the user imports as packs: meme explanations, setting notes, encyclopedia-style entries, dialogue or writing examples. It is not memory. Nothing in it is about the user or a character, and nothing the user says is ever written into it.

## Principle: data isolation, process co-location

The knowledge subsystem runs **inside the Memory Server process** but shares none of memory's data:

| | Memory | Public knowledge |
|---|---|---|
| Root | `<app docs>/memory/` | `<app docs>/knowledge/` |
| Database | per-character stores | `knowledge/knowledge.db` |
| HTTP surface | `/query_memory/*`, `/internal/memory/*`, … | `/internal/knowledge/*` (own router module) |
| Background work | memory loops | knowledge import jobs and indexer (own tasks) |
| Shared | — | the process-wide `EmbeddingService`, used only once memory's warmup worker has made it ready |

The `knowledge` package never imports `memory`; the Memory Server injects an embedder adapter. Exceptions raised by knowledge code are turned into `{ok: false, reason}` replies and never reach memory handlers. The routes sit on the Memory Server app, so they inherit its `HostOriginGuardMiddleware`, `InboundBodySizeLimitMiddleware` and storage startup gate (limited mode answers 409, exactly as for memory).

Earlier drafts kept knowledge in the Main Server. That meant a second copy of the embedding model, heavy indexing competing with streaming and TTS for the GIL, and extra startup/shutdown steps in Main. Main now reaches knowledge only over HTTP, and `scripts/check_module_layering.py` pins that boundary (`FORBIDDEN_EDGES`: `main_logic`, `main_routers`, `app/main_server` and `plugin` must not import `knowledge`; `knowledge` must not import `memory`).

```text
plugin-manager page ──► plugin server /market/knowledge/*   (bridge token, loopback)
                     ──► Main /api/public-knowledge/*        (CSRF + Origin, size cap, streamed)
Main session ──► query_public_knowledge tool (HTTP, timeout, failure = no result)
                     │
                     ▼
Memory Server /internal/knowledge/*
   ├─ knowledge/registry.json + knowledge/packs/   (user data)
   ├─ knowledge/knowledge.db                       (derived index)
   ├─ import job runner, indexer                   (own background tasks)
   └─ shared EmbeddingService                      (never reads or writes memory data)
```

## Storage

- `registry.json` — installed packs, per-pack policy (automatic context, local vectors, material-type override), disabled entries, and the global switch. User data.
- `packs/<pack_id>.<sha>.json` — the normalized raw pack. User data.
- `knowledge.db` — entries, FTS index, chunks and vectors. Derived: when it is missing, damaged or written by an unknown schema, the Memory Server rebuilds it from the registry and the raw packs on startup.

Storage migration moves the whole `knowledge/` directory, database included. Migration runs in the launcher before any server starts (or after every server has exited), so there is no live writer, the same condition under which memory's SQLite files are moved. The rebuild path covers a database that does not survive anyway.

## Packs

A schema-v1 pack is UTF-8 JSON with exactly `schema_version`, `pack_id`, `material_type` (`knowledge` or `corpus`), `source` (`name`, `homepage`, `license`) and `entries`. Each entry has at most `title`, `terms` (`alias`, `recognition`), `tags`, `summary` and `content`. System-derived data (chunks, hashes, vectors, model ids) is rejected.

v1 ships raw text only. Vectors are computed locally by the shared `EmbeddingService`, whose model is chosen per hardware tier and can change; pre-built vectors would be tied to one model and silently go stale. They can come back once the pack format can declare its model and fall back on a mismatch.

Limits: 10 MiB per pack file, 5,000 entries and 10,000 chunks per pack, 20,000 entries, 20,000 chunks and 64 MiB of pack files in total.

Defaults for a newly imported pack: automatic context **off**, local vectors **on**. The global switch defaults to on.

## Import, indexing and retrieval

- **Import** validates the pack, checks capacity and stages it; a single job runner then writes the raw file, replaces the pack's rows in one transaction and updates the registry, in an order that a crash at any point converges on startup. Vectors of unchanged chunks are carried over when a pack is updated.
- **Indexing** embeds pending chunks of packs with local vectors enabled, in small batches with pauses, and only while the `EmbeddingService` is ready. It never asks the service to load; until it is ready (or if it is disabled on this hardware) knowledge stays BM25-only.
- **Retrieval** fuses an exact title / alias / recognition match, BM25 over CJK bigrams and Latin words, and cosine similarity, with reciprocal-rank fusion. Each signal must clear its own bar (token coverage for BM25, a similarity floor for vectors), so an unrelated question returns nothing. Every query has a time budget; when query embedding is too slow, the lookup proceeds on BM25.

## Model-facing output

Matches are returned as one block between paired `======以下为本地公共知识参考======` / `======以上为本地公共知识参考======` lines (localized), with a note that the content is reference material, not instructions and not memory. Every piece of pack text in the block, titles included, has chat-control tokens and role markers stripped until nothing changes, has `=` runs shortened so it cannot close the fence, and is budgeted in tokens.

## Main Server side

- `query_public_knowledge` tool (`main_logic/public_knowledge.py`): an HTTP call to `/internal/knowledge/query` with a timeout; any failure reads as "no result". The tool is registered only while at least one usable pack exists and the global switch is on. Main caches that flag, refreshes it in the background and from every proxied management reply, and re-syncs session tools when it flips.
- `/api/public-knowledge/*` (`main_routers/public_knowledge_router.py`): a thin proxy. Writes require the local CSRF token and an allowed Origin before any body is read; bodies are size-capped and streamed through unparsed, so multipart uploads pass the same way as JSON. Only allowlisted paths are forwarded.

## Not yet in scope

- Automatic per-turn context (with a general turn-context interface in core).
- Market subscriptions (`subscriptions/apply`, streamed through Main).
