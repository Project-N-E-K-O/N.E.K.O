# VRM API

**Prefix:** `/api/model/vrm`

Manages VRM (3D) models — listing, uploading, animation management, and emotion mapping.

## Models

### `GET /api/model/vrm/config`

Return the runtime path configuration for user VRM files, bundled VRM files, and bundled animations. This is a frontend integration response; its filesystem paths are not a portable public schema.

### `GET /api/model/vrm/models`

List all available VRM models.

### `POST /api/model/vrm/upload`

Upload a new VRM model.

**Body:** `multipart/form-data` with `.vrm` file.

::: info
Maximum file size: **200 MB**. Files are streamed in 1 MB chunks.
:::

### `DELETE /api/model/vrm/model/{model_name}`

Delete a user-imported VRM model by name (also removes its associated emotion mapping and physics profile configs when no built-in model of the same name exists; each is handled separately, so one failed removal does not block the other). Built-in/static models cannot be deleted (returns 404).

::: warning
Path traversal is protected by `safe_vrm_path()` validation.
:::

### `DELETE /api/model/vrm/model`

Delete a user-imported VRM model by URL. Send a JSON body `{ "url": "/user_vrm/<file>.vrm" }`. Only top-level `.vrm` files under `/user_vrm/` may be deleted.

## Animations

### `GET /api/model/vrm/animations`

List all available VRM animations.

### `POST /api/model/vrm/upload_animation`

Upload a VRM animation file.

**Body:** `multipart/form-data` with animation file.

## Emotion mapping

### `GET /api/model/vrm/emotion_mapping/{model_name}`

Get emotion-to-animation mappings for a specific VRM model.

### `POST /api/model/vrm/emotion_mapping/{model_name}`

Update emotion mappings for a specific VRM model.

### `GET /api/model/vrm/expressions/{model_name}`

Return a common reference expression list. The handler does **not** parse the named VRM file; the frontend discovers the model's actual expressions after loading it. Do not use this response as model-specific capability detection.

## Physics profile

### `GET /api/model/vrm/physics_profile/{model_name}`

Read-only endpoint that reads `static/vrm/configs/{model_name}_physics.json` and never creates the directory. See [VRM models · Per-model physics profile](/frontend/vrm#per-model-physics-profile) for the file format, matching rules, and known limitations.

| Case | Status | Response |
| --- | --- | --- |
| JSON object read | 200 | `{ "success": true, "config": {...}, "source": "file" }`, with `NaN` / `Infinity` replaced by `null` |
| File missing | 200 | `{ "success": true, "config": null, "source": "absent" }` |
| Corrupt JSON, not an object, or undecodable | 200 | `{ "success": true, "config": null, "source": "corrupt" }` |
| Invalid model name (same validation as upload, including path-traversal checks) | 400 | `{ "success": false, "error": "..." }` |
| Other errors | 500 | `{ "success": false, "error": "..." }` |

The file is read as `utf-8-sig`, so a BOM written by Unity / C# is not treated as corruption.
