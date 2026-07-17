# Jukebox API

**Prefix:** `/api/jukebox`

The jukebox is a per-character song / action library used for singing and canned actions. It manages uploaded **songs** (audio files), **actions** (animation files such as VMD/VRMA), the **bindings** between them, and config **import / export**.

The router has 19 first-party local file-management routes. Every shorter path heading below is relative to `/api/jukebox`; no route has a trailing slash. Upload, import, delete, visibility, metadata, and binding calls mutate the local user library and have no separate authentication layer. Explicit route errors normally use FastAPI's `{ "detail": "..." }`; per-file upload failures can instead appear inside a successful batch response.

Songs and actions each carry an MD5 index for deduplication. Resources shipped with the app are marked `isBuiltin` — deleting a built-in resource only hides it (`visible: false`) rather than removing the file.

::: info
The config returned by these endpoints merges the bundled (built-in) library with the user's library; user entries take precedence. Only user resources and override settings are persisted back to disk.
:::

## Config

### `GET /config`

Return the full jukebox config: `songs`, `actions`, `bindings`, `md5Index`, plus the summary fields described below.

**Response:**

```json
{
  "version": "1.0",
  "songs": { "song_001": { "id": "song_001", "name": "...", "artist": "...", "audio": "songs/...", "audioMd5": "...", "audioFormat": "mp3", "visible": true, "uploadDate": "...", "defaultAction": "" } },
  "actions": { "action_001": { "id": "action_001", "name": "...", "file": "actions/...", "fileMd5": "...", "format": "vmd", "uploadDate": "...", "visible": true, "missing": false } },
  "bindings": { "song_001": { "action_001": { "offset": 0 } } },
  "md5Index": { "songs": {}, "actions": {} },
  "configRevision": "...",
  "songCount": 0,
  "visibleSongCount": 0,
  "actionCount": 0
}
```

### `GET /config/summary`

Return a lightweight summary, suitable for polling whether the full playlist needs a refresh.

**Response:**

```json
{
  "configRevision": "...",
  "songCount": 0,
  "visibleSongCount": 0,
  "actionCount": 0
}
```

::: info
`configRevision` is a short stable hash of `version` + `songs` + `actions` + `bindings`. Poll `/config/summary` and only re-fetch `/config` when `configRevision` changes.
:::

## Songs

### `POST /songs`

Upload one or more songs. `multipart/form-data`.

**Body:**

- `files` — One or more audio files. Allowed extensions: `.mp3`, `.wav`, `.ogg`, `.flac`. Max 1 GB each.
- `metadata` — JSON string array, per-song `[{ "name": "...", "artist": "..." }, ...]`. Optional; missing entries fall back to embedded audio tags, then the filename.

**Response:** For a single file, the result object directly (`{ "success": true, "song": { ... } }` or `{ "success": false, "error": "..." }`). For multiple files, `{ "success": true, "results": [ ... ] }`. Duplicate audio (matching MD5) is rejected per item.

### `POST /songs/batch-delete`

Delete uploaded songs and hide built-in songs in one validated batch.

**Body:**

```json
{ "songIds": ["song_001", "song_002"] }
```

**Response:** Counts and per-item outcomes.

```json
{
  "success": true,
  "partial": false,
  "requestedCount": 2,
  "deletedCount": 1,
  "hiddenCount": 1,
  "failedCount": 0,
  "deleted": [{ "songId": "song_001", "name": "..." }],
  "hidden": [{ "songId": "song_002", "name": "..." }],
  "failed": []
}
```

### `DELETE /songs/{song_id}`

Delete an uploaded song, or hide a built-in song. For user songs this removes the file, bindings, and MD5 index entry. For built-in songs it returns `{ "success": true, "message": "...", "hidden": true }`.

**Path parameter:** `song_id` — The song ID.

### `PUT /songs/{song_id}/visibility`

Set whether a song is visible. `multipart/form-data`.

**Path parameter:** `song_id` — The song ID.

**Body:** `visible` — Boolean (form field).

### `PUT /songs/{song_id}/metadata`

Update a song's display name and/or artist. `multipart/form-data`.

**Path parameter:** `song_id` — The song ID.

**Body:** `name`, `artist` — Optional form fields; only provided fields are updated.

### `PUT /songs/{song_id}/default-action`

Set a song's default action. The action must already be bound to the song. Pass an empty string to clear the default.

**Path parameter:** `song_id` — The song ID.

**Body:** `action_id` — Form field; the action ID, or empty to unset.

**Response:** `{ "success": true, "defaultAction": "action_001" }`

## Actions

### `POST /actions`

Upload one or more actions (animations). `multipart/form-data`.

**Body:**

- `files` — One or more animation files. Allowed extensions: `.vmd`, `.bvh`, `.fbx`, `.vrma`. Max 1 GB each.
- `metadata` — JSON string array, per-action `[{ "name": "..." }, ...]`. Optional; missing names fall back to the filename.

**Response:** Same shape as `POST /songs`: a single result object for one file, or `{ "success": true, "results": [ ... ] }` for several. Duplicate files (matching MD5) are rejected per item.

### `POST /actions/batch-delete`

Delete uploaded actions and hide built-in actions in one validated batch.

**Body:**

```json
{ "actionIds": ["action_001", "action_002"] }
```

**Response:** Same counts / per-item shape as `POST /songs/batch-delete`, keyed by `actionId`.

### `DELETE /actions/{action_id}`

Delete an uploaded action, or hide a built-in action. For user actions this removes the file, clears references in bindings and any song's `defaultAction`, and removes the MD5 index entry. For built-in actions it returns `{ "success": true, "message": "...", "hidden": true }`.

**Path parameter:** `action_id` — The action ID.

### `PUT /actions/{action_id}/visibility`

Set whether an action is visible. `multipart/form-data`.

**Path parameter:** `action_id` — The action ID.

**Body:** `visible` — Boolean (form field).

### `PUT /actions/{action_id}/metadata`

Update an action's display name. `multipart/form-data`.

**Path parameter:** `action_id` — The action ID.

**Body:** `name` — Form field (required).

## Bind

### `POST /bind`

Bind an action to a song. `multipart/form-data`. If the song has no default action of the same animation type yet, the newly bound action becomes the default.

**Body:**

- `songId` — The song ID.
- `actionId` — The action ID.
- `offset` — Integer offset, defaults to `0`.

**Response:** `{ "success": true, "defaultAction": "action_001" }`

### `DELETE /bind`

Remove a binding between a song and an action. `multipart/form-data`. If the unbound action was the song's default, the default is cleared.

**Body:** `songId`, `actionId` — The song and action IDs.

**Response:** `{ "success": true, "defaultAction": "..." }`. Returns `404` if the binding does not exist.

## Import / Export

### `POST /export`

Export selected (or all) songs and actions as a ZIP archive. `multipart/form-data`. Built-in songs are skipped; built-in actions are exported by ID/MD5 only (no file). Bindings are exported at the MD5 level so they re-link correctly on another machine.

**Body:**

- `songIds` — Optional JSON string array of song IDs. If omitted, all songs are considered (subject to `includeHidden`).
- `actionIds` — Optional JSON string array of action IDs. If omitted (full export), all actions are exported.
- `includeHidden` — Boolean, defaults to `true`. When `false`, hidden songs/actions and their bindings are excluded.

**Response:** A streamed `application/zip` download (`jukebox_export.zip`) containing `config.json` plus the song/action files.

### `POST /import`

Import a previously exported ZIP archive. `multipart/form-data`. MD5-level bindings are converted back to local ID-level bindings; matching resources are merged rather than duplicated.

**Body:** `file` — The ZIP archive (max 10 GB).

**Response:** Import statistics.

```json
{
  "success": true,
  "stats": {
    "songsAdded": 0,
    "songsMerged": 0,
    "actionsAdded": 0,
    "actionsMerged": 0,
    "bindingsAdded": 0
  }
}
```

### `GET /file/{file_path:path}`

Serve a song or action file. Prefers the user documents directory and falls back to the bundled directory. Protected against directory traversal.

**Path parameter:** `file_path` — Relative path, e.g. `songs/song_001.mp3` or `actions/action_001.vmd`.

**Response:** The file, with a media type derived from its extension (e.g. `audio/mpeg` for `.mp3`).

### `POST /pack-folder`

Pack an arbitrary set of uploaded files (preserving their relative paths) into a single ZIP archive. `multipart/form-data`. A generic utility used by the jukebox import/export UI.

**Body:** `files` — One or more files, each carrying its relative path as the filename.

**Response:** A streamed `application/zip` download (`packed.zip`).
