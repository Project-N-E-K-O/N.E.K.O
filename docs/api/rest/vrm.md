# VRM API

**Prefix:** `/api/model/vrm`

Manages VRM (3D) models — listing, uploading, animation management, and emotion mapping.

## Models

### `GET /api/model/vrm/models`

List all available VRM models.

### `GET /api/model/vrm/models/{model_name}`

Get details for a specific VRM model.

### `POST /api/model/vrm/upload`

Upload a new VRM model.

**Body:** `multipart/form-data` with `.vrm` file.

::: info
Maximum file size: **200 MB**. Files are streamed in 1 MB chunks.
:::

### `DELETE /api/model/vrm/delete/{model_name}`

Delete a VRM model.

::: warning
Path traversal is protected by `safe_vrm_path()` validation.
:::

## Animations

### `GET /api/model/vrm/animation/list`

List all available VRM animations.

### `POST /api/model/vrm/animation/upload`

Upload a VRM animation file.

**Body:** `multipart/form-data` with animation file.

## Emotion mapping

### `GET /api/model/vrm/emotion_mapping`

Get emotion-to-animation mappings for VRM models.

### `POST /api/model/vrm/emotion_mapping`

Update VRM emotion mappings.
