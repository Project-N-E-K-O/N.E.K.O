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
| `POST` | `/api/memory/update_catgirl_name` | Rename a character's memory storage |
| `GET` | `/api/memory/review_config` | Read the automatic recent-memory review toggle |
| `POST` | `/api/memory/review_config` | Update the automatic recent-memory review toggle |
| `GET` | `/api/memory/powerful_memory_config` | Read the powerful-memory toggle |
| `POST` | `/api/memory/powerful_memory_config` | Update the powerful-memory toggle and run any required migration |
| `GET` | `/api/memory/legacy/scan` | Scan user-visible legacy memory roots without changing them |
| `POST` | `/api/memory/legacy/purge` | Delete explicitly selected entries from scanned legacy roots |

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
  "content": "[{\"type\":\"human\",\"data\":{...}}]"
}
```

Errors use `{"success": false, "error": "..."}` with `400` for an invalid filename and `404` when the logical file cannot be resolved.

### `POST /api/memory/recent_file/save`

Replaces the selected character's recent history and cancels any in-flight review for that character so the manual edit can take effect.

**Request body**

```json
{
  "filename": "recent_小天.json",
  "chat": [
    { "role": "human", "text": "Hello!" },
    { "role": "ai", "text": "Hi there!" }
  ]
}
```

| Field | Type | Required | Description |
|---|---|---:|---|
| `filename` | string | yes | Must match `recent_<character>.json`; the character name is derived from this field |
| `chat` | array | yes | Replacement history, up to 10,000 entries |
| `chat[].role` | string | yes | Stored message type, normally `human`, `ai`, or `system` |
| `chat[].text` | string | no | Message text; defaults to an empty string |

Each message is limited to 32,768 text characters and the request to 2,097,152 text characters in total. Unknown fields on a chat entry are not persisted.

Success:

```json
{
  "success": true,
  "need_refresh": true,
  "catgirl_name": "小天"
}
```

Validation failures return `400` with `success: false`. A storage failure returns `success: false` and an `error` field; cloud-storage maintenance is reported as `409`.

## Character memory rename

### `POST /api/memory/update_catgirl_name`

Renames the character's complete memory storage through the shared character-memory migration helper. It is not limited to the recent-history file.

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
  "changed": true,
  "exists_after": true
}
```

Invalid or missing names return `400`. The operation can return `409` when storage is not writable.

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

## Legacy memory cleanup

Legacy cleanup is an explicit two-step operation: scan first, then submit selected absolute paths. The scan never migrates or deletes data.

### `GET /api/memory/legacy/scan`

No parameters.

The response identifies candidate roots outside the active runtime memory directory and describes each direct child. `size_bytes` is `-1` when size calculation is unavailable or exceeds the safety scan limit.

```json
{
  "success": true,
  "runtime_memory_dir": "C:\\...\\memory",
  "legacy_roots": [
    {
      "root": "C:\\...\\old-root\\memory",
      "source": "legacy_app_root",
      "exists": true,
      "entries": [
        {
          "name": "小天",
          "path": "C:\\...\\old-root\\memory\\小天",
          "is_dir": true,
          "size_bytes": 12345,
          "is_unlinked": false,
          "runtime_has_same_name": true
        }
      ]
    }
  ],
  "total_entries": 1,
  "total_size_bytes": 12345
}
```

Unexpected scan failures return `500` with `success: false`.

### `POST /api/memory/legacy/purge`

This is destructive. Send only paths returned as entries by the latest scan.

```json
{
  "paths": [
    "C:\\...\\old-root\\memory\\小天"
  ]
}
```

`paths` must be a non-empty array of absolute paths. Every path must resolve strictly below a currently recognized legacy root. The route rejects relative paths, `..` segments, the root directory itself, and the active runtime memory directory. Missing targets count as already removed, making retries idempotent.

Deletion is best-effort per entry. A successful request can therefore contain both `removed` and `errors`:

```json
{
  "success": true,
  "removed": ["C:\\...\\old-root\\memory\\小天"],
  "errors": [
    { "path": "C:\\...\\not-allowed", "error": "..." }
  ]
}
```

Malformed bodies return `400`; no recognized legacy roots returns `409`; initialization failure returns `500`.
