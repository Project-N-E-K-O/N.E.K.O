# Memory API

**Prefix:** `/api/memory`

This is the main server's browser and settings API for memory. It exposes recent-memory editing, memory feature toggles, character-memory renaming, and user-initiated cleanup of legacy storage. It is not a generic proxy for the process-local [Memory Server API](/api/memory-server).

All routes are declared without a trailing slash. Write operations can return `409` while cloud storage is in maintenance or read-only mode.

## Endpoint summary

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/memory/recent_files` | List logical recent-memory filenames |
| `GET` | `/api/memory/recent_file` | Read one recent-memory file |
| `POST` | `/api/memory/recent_file/save` | Replace one character's recent-memory history |
| `POST` | `/api/memory/update_catgirl_name` | Compatibility alias for the full character rename transaction |
| `GET` | `/api/memory/review_config` | Read the automatic recent-memory review toggle |
| `POST` | `/api/memory/review_config` | Update the automatic recent-memory review toggle |
| `GET` | `/api/memory/powerful_memory_config` | Read the powerful-memory toggle |
| `POST` | `/api/memory/powerful_memory_config` | Update the powerful-memory toggle and run any required migration |

## Recent memory files

The browser API retains the logical filename `recent_<character>.json` for compatibility. Current storage resolves that name to `memory/<character>/recent.json`; legacy flat files are still readable during migration.

### `GET /api/memory/recent_files`

No parameters.

The route searches both the active and project memory roots, deduplicates logical filenames, and returns them in sorted order.

```json
{
  "files": ["recent_小天.json", "recent_小夜.json"]
}
```

### `GET /api/memory/recent_file`

**Query parameters**

| Name | Type | Required | Description |
|---|---|---:|---|
| `filename` | string | yes | Logical filename such as `recent_小天.json`; path separators and `..` are rejected |

The `content` field is the file's UTF-8 JSON text, not a parsed message array.

```json
{
  "content": "[{\"type\":\"human\",\"data\":{...}}]",
  "fingerprint": "b4c1000000000000000000000000000000000000000000000000000000000000",
  "identity_token": "a7e3111111111111111111111111111111111111111111111111111111111111"
}
```

`fingerprint` and `identity_token` are opaque snapshot tokens. A client that
edits this content must retain both values and return them unchanged with the
corresponding save request.

Errors use `{"success": false, "error": "..."}` with `400` for an invalid filename and `404` when the logical file cannot be resolved.

### `POST /api/memory/recent_file/save`

Replaces the selected character's recent history and cancels any in-flight review for that character so the manual edit can take effect.

**Request body**

```json
{
  "filename": "recent_小天.json",
  "fingerprint": "b4c1000000000000000000000000000000000000000000000000000000000000",
  "identity_token": "a7e3111111111111111111111111111111111111111111111111111111111111",
  "chat": [
    { "role": "human", "text": "Hello!" },
    { "role": "ai", "text": "Hi there!" }
  ]
}
```

| Field | Type | Required | Description |
|---|---|---:|---|
| `filename` | string | yes | Must match `recent_<character>.json`; the character name is derived from this field |
| `fingerprint` | string | yes | Opaque content token returned by the GET used to populate this edit |
| `identity_token` | string | yes | Opaque file-identity token returned by the same GET |
| `chat` | array | yes | Replacement history, up to 10,000 entries |
| `chat[].role` | string | yes | Stored message type, normally `human`, `ai`, or `system` |
| `chat[].text` | string | no | Message text; defaults to an empty string |

Each message is limited to 32,768 text characters and the request to 2,097,152 text characters in total. Unknown fields on a chat entry are not persisted.

Success:

```json
{
  "success": true,
  "need_refresh": true,
  "catgirl_name": "小天",
  "fingerprint": "d8f2222222222222222222222222222222222222222222222222222222222222",
  "identity_token": "a7e3111111111111111111111111111111111111111111111111111111111111"
}
```

Validation failures return `400` with `success: false`. Missing snapshot tokens
or a concurrent file/identity change returns `409`; conflicts use
`code: "RECENT_FILE_CONFLICT"` and include the current tokens. On conflict,
GET the file again and merge the user's edits before saving. Do not replay the
old `chat` merely with the returned tokens. A successful response returns the
tokens for a subsequent edit. A storage failure returns `success: false` and an
`error` field; cloud-storage maintenance is also reported as `409`.

## Character memory rename

### `POST /api/memory/update_catgirl_name`

Compatibility alias for the canonical
`POST /api/characters/catgirl/{old_name}/rename` transaction. It drains memory
review/compression tasks, publishes the character rename, migrates all memory
and card state, reloads the memory server, and rolls back on failure. A legacy
follow-up call after that transaction already committed succeeds as an
idempotent no-op.

```json
{
  "old_name": "旧名字",
  "new_name": "新名字"
}
```

Both fields are required strings. Historical `old_name` values may contain dots; `new_name` uses the current character-name rules and cannot be a reserved route name.

```json
{
  "success": true,
  "memory_renamed": true,
  "memory_server_reloaded": true
}
```

An idempotent follow-up returns `changed: false`, `already_renamed: true`, and
`exists_after`. This no-op is allowed only when configuration already contains
the new name and the old storage is gone. If configuration was published but
old storage remains, the route returns `409` instead of hiding a partial rename;
repair it through the canonical character-management endpoint. Invalid or
missing names return `400`. The operation can also return `409` when storage is
not writable.

## Memory feature toggles

### `GET /api/memory/review_config`

Returns whether automatic review and correction of recent memory is enabled. The default is `true` when the setting is absent.

```json
{ "enabled": true }
```

### `POST /api/memory/review_config`

```json
{ "enabled": false }
```

The route persists `recent_memory_auto_review` in `core_config.json`.

```json
{ "success": true, "enabled": false }
```

Failures use `{"success": false, "error": "..."}`. Storage maintenance can return `409`.

### `GET /api/memory/powerful_memory_config`

Returns the `powerful_memory_enabled` setting. The default is `true` for existing installations without an explicit value.

```json
{ "enabled": true }
```

### `POST /api/memory/powerful_memory_config`

```json
{ "enabled": false }
```

The powerful-memory switch controls the evidence-driven LLM paths, including signal analysis, merge-on-promotion, rebuttal checks, negative-target checks, fact deduplication, and persona corrections. The lightweight feedback path remains available when powerful memory is off.

An `ON` to `OFF` transition first asks the memory-server process to reset the age anchor on confirmed reflections. This prevents old confirmed entries from being promoted immediately by the time-driven fallback. The configuration is saved only after that migration succeeds.

Success:

```json
{ "success": true, "enabled": false }
```

Migration or persistence failure:

```json
{ "success": false, "error": "migration HTTP 409" }
```
