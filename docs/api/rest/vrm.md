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

Delete a user-imported VRM model by name (also removes its associated emotion mapping config when no built-in model of the same name exists). Built-in/static models cannot be deleted (returns 404).

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
