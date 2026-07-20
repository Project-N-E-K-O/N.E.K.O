# Cloud Save API

The cloud-save router exposes the character-unit synchronization used by the desktop UI. It runs on the main server under `/api/cloudsave` and uses the configured Steam Auto Cloud storage backend.

> [!CAUTION]
> Upload and download mutate character data. A download can terminate an active character session, release its memory-server database handle, replace local files, reload character state, and refresh the memory server. Treat these operations as user-confirmed data-management actions, not background polling endpoints.

## Route inventory

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/cloudsave/summary` | Compare local and cloud character units and include Steam Workshop asset status |
| `GET` | `/api/cloudsave/steam-autocloud-config` | Report the selected sync backend and Steam Auto Cloud availability |
| `GET` | `/api/cloudsave/character/{name}` | Return synchronization detail for one character |
| `POST` | `/api/cloudsave/character/{name}/upload` | Export one coherent character unit to cloud storage |
| `POST` | `/api/cloudsave/character/{name}/download` | Import one character unit into local storage and reload runtime state |

Routes do not have a trailing slash.

## Read endpoints

`GET /api/cloudsave/summary` returns the cloud-save summary produced from the current character configuration. It also includes `sync_backend`, `steam_autocloud`, and—when an item references a Steam Workshop asset—its current Workshop status.

`GET /api/cloudsave/steam-autocloud-config` returns:

```json
{
  "success": true,
  "sync_backend": "steam_auto_cloud",
  "steam_autocloud": {}
}
```

The exact `steam_autocloud` fields describe the current installation and may vary with Steam availability.

`GET /api/cloudsave/character/{name}` returns the local/cloud comparison for the named character. A missing cloud character returns HTTP `404` with code `CLOUDSAVE_CHARACTER_NOT_FOUND`.

## Upload a character

```http
POST /api/cloudsave/character/Lanlan/upload
Content-Type: application/json

{"overwrite": false}
```

`overwrite` is optional and defaults to `false`. It must be a JSON boolean. The successful response includes `character_name`, the refreshed `detail`, exported `meta`, `sequence_number`, `sync_backend`, and `steam_autocloud`.

The export is a character unit, not just a card, but it is **not a complete memory-directory backup**. The current snapshot copies only these allowlisted flat files when they exist:

```text
recent.json
settings.json
facts.json
facts_archive.json
persona.json
persona_corrections.json
reflections.json
reflections_archive.json
surfaced.json
time_indexed.db
```

The snapshot does not include current sharded archives such as `reflection_archive/` or `persona_archive/`, recent-summary metadata (`recent_meta.json`), recovery state (`cursors.json`, `outbox.ndjson`, `events.ndjson`, `events_applied.json`), or worker sidecars such as `facts_pending_dedup.json`. Those paths are not uploaded or restored on another device. A pre-overwrite operation backup is a local rollback aid; it does not expand cloud coverage. If the cloud unit already exists and `overwrite` is false, the endpoint returns HTTP `409`.

## Download a character

```http
POST /api/cloudsave/character/Lanlan/download
Content-Type: application/json

{
  "overwrite": true,
  "backup_before_overwrite": true,
  "force": false
}
```

| Field | Type | Default | Meaning |
|---|---|---:|---|
| `overwrite` | boolean | `false` | Allow replacement of an existing local character |
| `backup_before_overwrite` | boolean | `true` | Create an operation backup before replacement |
| `force` | boolean | `false` | Terminate an active session before importing |

When the character has an active session and `force` is not true, the endpoint returns HTTP `409`, code `ACTIVE_SESSION_BLOCKED`, and `can_force: true`. With `force: true`, the server terminates the session and releases the memory-server handle before importing.

After import, the server reloads character configuration and asks the memory server to reload. If that reload fails, the server attempts to restore the operation backup and returns HTTP `500` with code `LOCAL_RELOAD_FAILED_ROLLED_BACK` and rollback fields. A successful response includes `detail`, `backup_path`, `sync_backend`, and `steam_autocloud`.

## Errors

Cloud-save errors use this envelope rather than FastAPI's `detail`-only error:

```json
{
  "success": false,
  "error": "LOCAL_CHARACTER_EXISTS",
  "code": "LOCAL_CHARACTER_EXISTS",
  "message": "local character already exists: Lanlan",
  "message_key": "cloudsave.error.localCharacterExists",
  "message_params": {},
  "character_name": "Lanlan"
}
```

Common status codes:

| Status | Meaning |
|---:|---|
| `400` | Invalid JSON, a non-boolean option, name audit failure, or another rejected operation |
| `404` | The requested local or cloud character does not exist |
| `409` | Existing destination, active session, or cloud-save write fence |
| `500` | Unexpected upload/download failure or reload/rollback failure |
| `503` | Cloud provider unavailable, session termination failed, or memory handle could not be released |

Do not depend only on the English `message`; branch on `code` and use `message_key` for localized UI text.
