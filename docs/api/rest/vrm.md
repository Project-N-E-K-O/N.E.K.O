# VRM API

**Prefix:** `/api/model/vrm`

Manages VRM (3D) models — listing, uploading, animation management, and emotion mapping.

## Models

### `GET /api/model/vrm/models`

List all available VRM models (from both project and user directories).

### `POST /api/model/vrm/upload`

Upload a new VRM model.

**Body:** `multipart/form-data` with `.vrm` file.

::: info
Maximum file size: **200 MB**. Files are streamed in chunks with path traversal protection.
:::

## Configuration

### `GET /api/model/vrm/config`

Get the unified path configuration for VRM models (project directory, user directory).

## Animations

### `GET /api/model/vrm/animations`

List all available VRM animation files (`.vrma` and `.vrm`).

### `POST /api/model/vrm/upload_animation`

Upload a VRM animation file (`.vrma`).

**Body:** `multipart/form-data` with animation file.

## Emotion mapping

### `GET /api/model/vrm/emotion_mapping/{model_name}`

Get emotion-to-expression mappings for a VRM model.

### `POST /api/model/vrm/emotion_mapping/{model_name}`

Update VRM emotion mappings.

**Body:**

```json
{
  "happy": ["expressionName1"],
  "sad": ["expressionName2"]
}
```

## Expressions

### `GET /api/model/vrm/expressions/{model_name}`

Get the list of supported expressions for a VRM model (generic reference list).
