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

The `knowledge` package never imports `memory`; the Memory Server injects an embedder adapter. Exceptions raised by knowledge code are turned into `{ok: false, reason}` replies and never reach memory handlers. The routes sit on the Memory Server app, so they inherit its `HostOriginGuardMiddleware`, `InboundBodySizeLimitMiddleware` and storage startup gate (limited mode answers 409, exactly as for memory). Loopback traffic needs no token on the Memory Server and its guard checks only the Host of HTTP requests, so the knowledge router itself refuses every request that carries an `Origin` or `Referer` header before reading any body (the server serves no pages, so not even a loopback origin, such as a page of another local app, is a legitimate caller), its JSON routes accept only `application/json` and pack import only `application/json` or `multipart/form-data`: without this, any web page could import, remove or switch off knowledge with a CORS-simple POST to the fixed port. Main and the plugin server send no browser origin.

Earlier drafts kept knowledge in the Main Server. That meant a second copy of the embedding model, heavy indexing competing with streaming and TTS for the GIL, and extra startup/shutdown steps in Main. Main now reaches knowledge only over HTTP, and `scripts/check_module_layering.py` pins that boundary (`FORBIDDEN_EDGES`: `main_logic`, `main_routers`, `app/main_server` and `plugin` must not import `knowledge`; `knowledge` must not import `memory`).

```text
plugin-manager page ──► plugin server /market/knowledge/*   (bridge token, loopback)
                     ──► Main /api/public-knowledge/*        (CSRF + Origin, size cap, streamed)
Market page ──► plugin server /market/knowledge/subscribe … (paired bridge token, via Main's /market proxy)
   plugin server subscription worker ──► Main /api/public-knowledge/subscriptions/apply (streamed)
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
- **Indexing** embeds pending chunks of packs with local vectors enabled, in small batches (4 chunks), and only while the `EmbeddingService` is ready. After each batch, including one whose chunks all failed, it pauses at least three times as long as the batch took (`INDEX_TARGET_DUTY` = 25%), so background indexing is busy at most a quarter of the time; inference itself may use several threads while it runs. The measured time is wall-clock (waiting for the shared model, a laptop sleeping mid-batch), so the pause is capped at 30 seconds. New vectors become searchable when the snapshot is reloaded: once the backlog drains, and during a long backfill at least every `VECTOR_REFRESH_SECONDS` (60 s), since an indexing round ends when that reload is due. It never asks the service to load; until it is ready (or if it is disabled on this hardware) knowledge stays BM25-only.
- **Retrieval** fuses an exact title / alias / recognition match, BM25 over CJK bigrams and Latin words, and cosine similarity, with reciprocal-rank fusion. Each signal must clear its own bar (token coverage for BM25, a similarity floor for vectors), so an unrelated question returns nothing. Every query has a time budget; when query embedding is too slow, the lookup proceeds on BM25.

## Model-facing output

Matches are returned as one block between paired `======以下为本地公共知识参考======` / `======以上为本地公共知识参考======` lines (localized), with a note that the content is reference material, not instructions and not memory. Every piece of pack text in the block, titles included, has chat-control tokens and role markers stripped until nothing changes, has `=` runs shortened so it cannot close the fence, and is budgeted in tokens.

## Main Server side

- `query_public_knowledge` tool (`main_logic/public_knowledge.py`): an HTTP call to `/internal/knowledge/query` with a timeout; any failure reads as "no result". The tool is registered only while at least one usable pack exists and the global switch is on. Main caches that flag, refreshes it in the background and from every proxied management reply, and re-syncs session tools when it flips.
- `/api/public-knowledge/*` (`main_routers/public_knowledge_router.py`): a thin proxy. Writes require the local CSRF token and an allowed Origin before any body is read; bodies are size-capped and streamed through unparsed, so multipart uploads pass the same way as JSON. Only allowlisted paths are forwarded.

## Market subscriptions

A pack can also be installed from the plugin market. The Market page is paired with the local client: the manager page asks the plugin server for a one-time code (`POST /market/pair-code`, local origins only) and opens the Market with it; the page exchanges the code for the bridge token at `/market/token-exchange`. The returned port is Main's, like `bridge.json`: the page reaches the plugin server through Main's `/market` proxy, which hands the plugin server's CORS answer back unchanged.

The plugin server (`plugin/server/routes/knowledge_market.py`) then runs one worker per subscription: it reads the version descriptor from the market API, downloads the raw pack artifact (HTTPS, allowlisted hosts and redirect targets, size and SHA-256 checked; a GitHub Release asset is also tried through the GitHub mirrors the plugin install path uses, ordered by its speed test, and a source serving other bytes is skipped), and sends it with its subscription metadata to Main's `subscriptions/apply`, which streams it to the Memory Server like any write. It never calls the Memory Server directly. The browser-facing bridge does not forward `subscriptions/apply`, so a page holding the bridge token cannot push a pack labelled as a market subscription.

- **Metadata** (`knowledge/subscriptions.py`): provider (`plugin-market`), package id, remote id, pack id, version, channel, artifact SHA-256 and material type, stored in the pack's registry record. The Memory Server installs the artifact only if its SHA-256, pack id and material type match.
- **Identity rules**: a pack is local or subscribed for good (neither overwrites the other); a subscribed pack keeps its provider, package id, remote id and material type; one market package installs as one pack. Checked when the import is admitted and again under the write lock when it commits.
- **v1 has no prebuilt vectors**: the artifact is raw text; vectors are backfilled locally like any import. Index artifacts a descriptor lists are ignored.
- **Bounds**: at most 4 workers download or install at once; a worker gives its slot back once the import is staged, while the total number of live workers is capped at 32. A job pruned while being polled is checked against the installed registry, every subscription field (package, pack, version, channel, artifact SHA-256). Busy answers (write lock, import slots, a service starting) are retried for a while and then reported as a retryable `knowledge_busy`, never as an invalid pack.
- **Unsubscribe** of a package not installed here (for example removed from the management page) still tells the market, so its record does not outlive the pack. Otherwise it stops an in-flight install (waiting for an install request already sent and cancelling an import it staged), then removes the pack with `expected_subscription` (only the subscribed pack it names) and an `operation_id`. If the answer is lost, the request polls `packs/remove/status` at a short fixed interval within its budget; after that a background drain keeps confirming with exponential backoff (0.25 s doubling, capped at 30 s) and reports the unsubscribe to the market once the removal commits. A tracked removal runs to its end on the Memory Server even if its caller stops waiting.

## Not yet in scope

- Automatic per-turn context (with a general turn-context interface in core).
