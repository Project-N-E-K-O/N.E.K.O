# Steam Workshop API

**Prefix:** `/api/steam/workshop`

This is the bundled Workshop UI's integration surface for local staging, Steam UGC discovery/download/publish, character synchronization, unsubscribe cleanup, and optional reference-voice packaging.

::: warning First-party and local-only
Many request/response fields are UI workflow state rather than a versioned third-party schema. Some routes accept local paths or mutate Steam subscriptions and character data. Keep the service loopback-only. Steam-dependent operations return `503` when Steamworks is not initialized.
:::

## Configuration and sandboxed file helpers

| Method and path | Purpose |
|---|---|
| `GET /api/steam/workshop/config` | Read `default_workshop_folder`, `user_mod_folder`, and auto-create settings. |
| `POST /api/steam/workshop/config` | Merge those supported fields and create the configured folder when enabled. |
| `GET /api/steam/workshop/read-file` | Read required query `path` under the configured Workshop root. Text is returned directly; known binary types are base64. Limit: 5 MiB. |
| `GET /api/steam/workshop/list-chara-files` | List top-level `*.chara.json` files in required query `directory` under the Workshop root. |
| `GET /api/steam/workshop/list-audio-files` | List top-level `.mp3`/`.wav` files in required query `directory` under the Workshop root. |

Path-containment checks reject traversal. Missing paths use `404`, oversized reads `413`, and other read failures `500`.

## Steam item discovery and download

| Method and path | Purpose |
|---|---|
| `GET /api/steam/workshop/status` | Report whether Steamworks is initialized. |
| `GET /api/steam/workshop/subscribed-items` | Return cached/refreshed metadata for subscribed UGC items. |
| `GET /api/steam/workshop/item/{item_id}` | Return metadata for one item. |
| `GET /api/steam/workshop/item/{item_id}/path` | Resolve an installed item's local path. |
| `POST /api/steam/workshop/item/{item_id}/download` | Trigger download. Optional body: `high_priority`, `wait`, and `timeout` (1–600 seconds). |
| `GET /api/steam/workshop/item/{item_id}/download-status` | Poll state, byte progress, and installed path. |

A non-numeric ID is `400`; an unsubscribed download is `409`; Steam rejection may be `502`. With `wait: true`, a timeout returns HTTP `202` plus current progress so callers can continue polling. An already installed and current item returns success immediately.

## Staging and publishing

### `POST /api/steam/workshop/prepare-upload`

Creates a temporary `WorkshopExport/item_*` directory and copies a character card plus a Live2D, VRM, or MMD model into it. Required UI fields are `charaData` and `modelName`; `modelType` defaults to `live2d`. Optional fields include `fileName` and `character_card_name`. Existing uploaded metadata, unsupported types, unsafe paths, or missing model assets are rejected.

### Upload and cleanup helpers

| Method and path | Purpose |
|---|---|
| `POST /api/steam/workshop/upload-preview-image` | Upload multipart JPEG/PNG field `file`; optional `content_folder` selects the staging directory. Returns `file_path`. |
| `GET /api/steam/workshop/check-upload-status` | Inspect query `item_path` for staging/upload status. |
| `POST /api/steam/workshop/cleanup-temp-folder` | Delete body `temp_folder` only when it resolves under `WorkshopExport`. |

### `POST /api/steam/workshop/publish`

Publishes a prepared folder. Required JSON fields are `title`, `content_folder`, and integer `visibility`; optional fields include `description`, `preview_image`, `tags`, `change_note`, and `character_card_name`. `content_folder` must remain inside the configured Workshop root. Steam callbacks make this an asynchronous native integration; the response reports create/update progress and failures through the `success` envelope and HTTP status.

::: info Platform boundary
Native Steamworks publish is deliberately refused on macOS arm64 where the current binding has a known callback crash risk.
:::

## Character metadata and synchronization

| Method and path | Purpose |
|---|---|
| `GET /api/steam/workshop/meta/{character_name}` | Read the card's local `.workshop_meta.json` snapshot and upload state. |
| `POST /api/steam/workshop/sync-characters` | Scan subscribed installed items and synchronize their character cards. |
| `POST /api/steam/workshop/sync-character/{item_id}` | Synchronize cards from one subscribed item. |
| `POST /api/steam/workshop/unsubscribe` | Unsubscribe body `item_id`, then perform guarded cleanup of characters/assets associated with that UGC item. |

Synchronization may report skipped/conflicting cards, missing installs, or a storage write fence in its JSON result. Unsubscribe uses origin metadata and conservative disk checks; it does not delete a same-named local character merely because a Workshop folder contains that name.

## Reference voice packaging

| Method and path | Purpose |
|---|---|
| `POST /api/steam/workshop/upload-reference-audio` | Upload multipart `file` plus `content_folder` under `WorkshopExport`; accepts MP3/WAV and writes `voice_manifest.json`. Optional: `prefix`, `display_name`, `ref_language`, `provider_hint`. |
| `POST /api/steam/workshop/remove-reference-audio` | Remove the staged sample and manifest from body `content_folder`. |
| `GET /api/steam/workshop/voice-reference/{item_id}` | Return the normalized reference-voice manifest from an installed subscribed item, or `available: false`. |
| `GET /api/steam/workshop/voice-reference/{item_id}/audio` | Stream that item's validated reference audio file. |

These routes package reference material; they do not clone or register a local TTS voice themselves.

## Implementation-verified route inventory

```text
GET  /api/steam/workshop/config
POST /api/steam/workshop/config
GET  /api/steam/workshop/read-file
GET  /api/steam/workshop/list-chara-files
GET  /api/steam/workshop/list-audio-files
GET  /api/steam/workshop/status
POST /api/steam/workshop/item/{item_id}/download
GET  /api/steam/workshop/item/{item_id}/download-status
GET  /api/steam/workshop/item/{item_id}/path
GET  /api/steam/workshop/item/{item_id}
GET  /api/steam/workshop/meta/{character_name}
POST /api/steam/workshop/upload-preview-image
GET  /api/steam/workshop/check-upload-status
POST /api/steam/workshop/prepare-upload
POST /api/steam/workshop/cleanup-temp-folder
POST /api/steam/workshop/publish
POST /api/steam/workshop/sync-characters
POST /api/steam/workshop/sync-character/{item_id}
GET  /api/steam/workshop/subscribed-items
POST /api/steam/workshop/unsubscribe
POST /api/steam/workshop/upload-reference-audio
POST /api/steam/workshop/remove-reference-audio
GET  /api/steam/workshop/voice-reference/{item_id}
GET  /api/steam/workshop/voice-reference/{item_id}/audio
```
