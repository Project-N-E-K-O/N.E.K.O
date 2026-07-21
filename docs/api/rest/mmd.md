# MMD API

**Prefix:** `/api/model/mmd`

Manage MMD (MikuMikuDance) avatar models — PMX/PMD models, VMD animations, and per-model emotion mappings.

These 11 routes back N.E.K.O.'s first-party local model manager. Upload and delete calls write the user MMD directory; built-in project assets are read-only through this API.

| Method | Routes |
|---|---|
| `POST` | `/upload`, `/upload_animation`, `/upload_zip`, `/emotion_mapping` |
| `GET` | `/models`, `/animations`, `/animations/list`, `/config`, `/emotion_mapping` |
| `DELETE` | `/model`, `/animation` |

Every path in this table is relative to `/api/model/mmd` and has no trailing slash.

::: info
MMD expressions are morph-target / blendshape based: an emotion is rendered by driving the model's named morphs. The emotion mapping endpoints below associate emotion labels with a model's morph targets.
:::

## Upload

### `POST /api/model/mmd/upload`

Upload a single MMD model file (`.pmx` / `.pmd`). The request is `multipart/form-data` with a `file` field, streamed to disk in chunks. Maximum size is 500 MB.

**Response:**

```json
{
  "success": true,
  "message": "...",
  "model_name": "<filename stem>",
  "model_url": "/user_mmd/<filename>",
  "file_size": 0
}
```

On error (no file, wrong extension, file already exists, or too large) the response is `{ "success": false, "error": "..." }` with a 4xx/5xx status.

### `POST /api/model/mmd/upload_animation`

Upload a single VMD animation file (`.vmd`). Same `multipart/form-data` `file` field and 500 MB limit as model upload. Stored under the user MMD `animation/` subdirectory.

**Response:**

```json
{
  "success": true,
  "message": "...",
  "filename": "<filename>",
  "file_path": "/user_mmd/animation/<filename>"
}
```

### `POST /api/model/mmd/upload_zip`

Upload an MMD model **ZIP package** (a `.pmx`/`.pmd` model plus its textures). The archive is written to a temp file, validated, and extracted into a subdirectory named after the model (or the archive's existing top-level folder).

::: info
Many MMD archives originate in Japan and store filenames in Shift-JIS / CP932 (and Chinese/Korean packages in GBK, Big5, or EUC-KR) without the UTF-8 flag bit. The server detects the actual filename encoding and recovers the original CJK names during extraction, instead of leaving them as mojibake.
:::

The ZIP must contain at least one `.pmx`/`.pmd` file. Zip-bomb guards apply: at most 10000 entries and 2 GB total uncompressed size; entries with absolute paths or `..` are rejected.

**Response:**

```json
{
  "success": true,
  "message": "...",
  "model_name": "<model stem>",
  "model_url": "/user_mmd/<relative path to model>",
  "file_count": 0,
  "file_size": 0
}
```

## List

### `GET /api/model/mmd/models`

List available MMD models (`.pmx` / `.pmd`), searched recursively across the project `static/mmd/` directory, the user MMD directory, and subscribed Steam Workshop items.

**Response:** `{ "success": true, "models": [ ... ] }`. Each entry includes `name`, `filename`, `url`, `rel_path`, `type`, `size`, and `location` (`"project"`, `"user"`, or `"steam_workshop"`). Workshop entries also carry `source` and `item_id`. A leftover model directory with no model file is reported with `"broken": true`.

### `GET /api/model/mmd/animations`

List VMD animation files from the project `static/mmd/animation/` directory and the user MMD `animation/` directory.

**Response:** `{ "success": true, "animations": [ ... ] }`. Each entry includes `name`, `filename`, `url`, `type` (`"vmd"`), and `size`.

### `GET /api/model/mmd/animations/list`

List the user-uploaded VMD animations that are eligible for deletion (those under the user MMD `animation/` directory).

**Response:** `{ "success": true, "animations": [ ... ] }`. Each entry includes `name`, `filename`, `url`, and `path`.

## Configuration

### `GET /api/model/mmd/config`

Return the MMD path configuration.

**Response:**

```json
{
  "success": true,
  "paths": {
    "user_mmd": "/user_mmd",
    "static_mmd": "/static/mmd"
  }
}
```

## Emotion mapping

### `GET /api/model/mmd/emotion_mapping`

Get the emotion mapping configuration for a model.

**Query:** `model` — Model name (no path separators allowed). When omitted or when no config exists, an empty mapping is returned.

**Response:** `{ "success": true, "mapping": { ... } }`

### `POST /api/model/mmd/emotion_mapping`

Create or update the emotion mapping for a model. The mapping is persisted per model under the user MMD `emotion_config/` directory.

**Body:**

```json
{
  "model": "<model name>",
  "mapping": { }
}
```

`model` is required and must not contain path separators; `mapping` must be an object.

**Response:** `{ "success": true, "message": "..." }`

## Error boundary

Application errors use `{ "success": false, "error": "..." }`. Invalid uploads, unsafe paths, unsupported file types, built-in-model deletion, and malformed mapping bodies normally return HTTP `400`; a missing model/animation returns `404`, while storage and unexpected failures return `500`. There is no separate authentication layer on these first-party routes, so do not expose the main-server port to untrusted clients.

## Delete

### `DELETE /api/model/mmd/model`

Delete a user MMD model. When the model lives in a subdirectory, its entire directory (textures and other associated resources) is removed; a top-level model file is removed on its own. The matching emotion-mapping config is deleted as well. Project built-in models (`/static/mmd/...`) cannot be deleted.

**Body:**

```json
{
  "url": "/user_mmd/<relative path>"
}
```

**Response:** `{ "success": true, "message": "...", "deleted_files": 0 }`

### `DELETE /api/model/mmd/animation`

Delete a user-uploaded VMD animation. The target must be a `.vmd` file under the user MMD `animation/` directory.

**Body:**

```json
{
  "url": "/user_mmd/animation/<filename>"
}
```

**Response:** `{ "success": true, "message": "..." }`
