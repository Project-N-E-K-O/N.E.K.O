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
| `GET` | `/api/memory/farewell-summary` | Read-only memory summary for the desktop exit farewell card |
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
| `chat[].source_index` | integer | no | Index of this entry in the array returned by the GET; used only to place entries |
| `chat[].theater` | boolean | no | Marks an entry that stands for a theater memory message; it is never written as ordinary text |

Each message is limited to 32,768 text characters and the request to 2,097,152 text characters in total. Unknown fields on a chat entry are not persisted.

Theater memory messages (theater episode capsules) are read-only here. Every
theater message of the current snapshot is kept verbatim, metadata included, at
its original position, whatever the posted `chat` contains for it; only ordinary
messages are replaced. If a client without `source_index` posts an entry that
matches no stored message while a theater message is also unmatched, the save is
rejected with `409` and `code: "RECENT_FILE_THEATER_READONLY"`.

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

Reads wait for pending memory-toggle writes before returning the persisted value. Missing files or setting keys default to `true`. Unreadable files, malformed JSON, a non-object configuration, or a non-boolean setting return HTTP `503` with `{"error": "..."}` and no `enabled` value. An invalid setting value additionally returns `"code": "invalid_memory_setting"`; the browser offers an explicit on/off repair choice. A valid boolean POST can repair that setting while preserving other fields.

### `POST /api/memory/review_config`

The body must be a JSON object with a required boolean `enabled`; malformed JSON, a missing field, or any other type returns HTTP `400` with `{"success": false, "error": "..."}`. Read or save failures return `success: false` without replacing an unreadable, malformed, or non-object configuration. Only a missing file starts a new configuration. The two memory-toggle updates are serialized through any required migration and file save. When saving powerful memory as `false`, migration runs unless the stored value is already the boolean `false`; an invalid legacy value is treated as unknown.

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

Reads wait for pending memory-toggle writes before returning the persisted value. Missing files or setting keys default to `true`. Unreadable files, malformed JSON, a non-object configuration, or a non-boolean setting return HTTP `503` with `{"error": "..."}` and no `enabled` value. An invalid setting value additionally returns `"code": "invalid_memory_setting"`; the browser offers an explicit on/off repair choice. A valid boolean POST can repair that setting while preserving other fields.

### `POST /api/memory/powerful_memory_config`

The body must be a JSON object with a required boolean `enabled`; malformed JSON, a missing field, or any other type returns HTTP `400` with `{"success": false, "error": "..."}`. Read or save failures return `success: false` without replacing an unreadable, malformed, or non-object configuration. Only a missing file starts a new configuration. The two memory-toggle updates are serialized through any required migration and file save. When saving powerful memory as `false`, migration runs unless the stored value is already the boolean `false`; an invalid legacy value is treated as unknown.

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

## Farewell summary

### `GET /api/memory/farewell-summary`

Read-only summary of the currently selected character, shown on the desktop app's exit farewell card. The desktop main process calls it over loopback with a 1.2-second timeout, so the route only reads local files: it calls no model, triggers no sync, and writes nothing.

```json
{
  "memory_count": 128,
  "conversation_count": 342,
  "flashback": {
    "id": "fact_20260314103000_ab12cd34",
    "text": "Master loves curling up at home with a book on rainy days",
    "date": "2026-03-14"
  }
}
```

| Field | Meaning |
|---|---|
| `memory_count` | Local memories, counted like the community card's `totalMemoryCount`: `facts.json` and `facts_archive.json` deduplicated, machine-log rows excluded. |
| `conversation_count` | User messages in the character's `time_indexed.db` (one per conversation turn). Omitted when the database is missing or unreadable. |
| `flashback` | One older memory worth recalling, or `null`. |
| `flashback.id` | Memory ID; IDs that are not plain strings become an opaque stable token. |
| `flashback.text` | The memory text, whitespace collapsed, at most 200 characters. |
| `flashback.date` | Local date (`YYYY-MM-DD`) the memory happened, or was recorded when no event time exists. |

The flashback is drawn only from shareable memories, using the same rules as `/api/card-drop/facts`: private, redacted, machine-log and `neko_visit` rows are skipped, as are archived rows carrying `subject_archived_at` or `arbitration_*`. Memories less than 7 days old are never chosen. Among the rest, memories of importance 5 or higher are preferred, and one is drawn from the oldest quarter weighted by importance, so consecutive calls can differ.

When no character is selected the response is `{"flashback": null}`; when anything fails the counts are omitted rather than returning an error.

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
