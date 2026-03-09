# Steam Workshop API

**Prefix:** `/api/steam/workshop`

Manages Steam Workshop items — browsing subscribed items, publishing, local mod management, and configuration.

::: info
Steam Workshop features require the Steam client to be running and the Steamworks SDK to be initialized.
:::

## Subscribed items

### `GET /api/steam/workshop/subscribed-items`

Get all subscribed Steam Workshop items.

### `GET /api/steam/workshop/item/{item_id}`

Get detailed information for a specific Workshop item.

### `GET /api/steam/workshop/item/{item_id}/path`

Get the local installation path for a Workshop item.

### `POST /api/steam/workshop/unsubscribe`

Unsubscribe from a Workshop item.

**Body:**

```json
{ "item_id": 12345678 }
```

## Publishing

### `POST /api/steam/workshop/prepare-upload`

Prepare content for publishing (create temp directory, copy files).

**Body:**

```json
{
  "charaData": { ... },
  "modelName": "model_name",
  "fileName": "optional_filename",
  "character_card_name": "optional_card_name"
}
```

### `POST /api/steam/workshop/publish`

Publish an item to Steam Workshop.

**Body:**

```json
{
  "title": "Item Title",
  "content_folder": "/path/to/content",
  "visibility": 0,
  "preview_image": "/path/to/preview.jpg",
  "description": "Item description",
  "tags": ["tag1", "tag2"],
  "change_note": "Initial release",
  "character_card_name": "card_name"
}
```

### `POST /api/steam/workshop/upload-preview-image`

Upload a preview image (JPEG/PNG, unified as `preview.*`).

**Body:** `multipart/form-data` with `file` field and optional `content_folder`.

### `GET /api/steam/workshop/check-upload-status`

Check the current upload status.

**Query:** `item_path` — Content path to check.

### `POST /api/steam/workshop/cleanup-temp-folder`

Clean up temporary upload directories.

**Body:**

```json
{ "temp_folder": "/path/to/temp" }
```

## Configuration

### `GET /api/steam/workshop/config`

Get Workshop configuration (paths, settings).

### `POST /api/steam/workshop/config`

Update Workshop configuration.

**Body:**

```json
{
  "default_workshop_folder": "/path",
  "auto_create_folder": true,
  "user_mod_folder": "/path"
}
```

## Metadata

### `GET /api/steam/workshop/meta/{character_name}`

Get Workshop metadata for a character card.

## Local items

### `POST /api/steam/workshop/local-items/scan`

Scan a local directory for Workshop-compatible items.

**Body:**

```json
{ "folder_path": "/path/to/scan" }
```

### `GET /api/steam/workshop/local-items/{item_id}`

Get a local Workshop item.

**Query:** `folder_path` (optional)

## File utilities

### `GET /api/steam/workshop/read-file`

Read content from a Workshop file.

**Query:** `path` — File path within Workshop content.

### `GET /api/steam/workshop/list-chara-files`

List all `.chara.json` files in a directory.

**Query:** `directory`

### `GET /api/steam/workshop/list-audio-files`

List all audio files in a directory.

**Query:** `directory`

::: warning
Path traversal protection is enforced on all file operations.
:::
