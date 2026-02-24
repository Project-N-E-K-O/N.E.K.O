# Live2D API

**Prefix:** `/api/live2d`

Manages Live2D models — listing, configuration, emotion mapping, file uploads, and parameter editing.

## Model listing

### `GET /api/live2d/models`

List all available Live2D models.

**Query:** `simple` (optional, boolean) — If true, return only model names without full config.

### `GET /api/live2d/user_models`

List user-imported models (as opposed to built-in or Workshop models).

## Model configuration

### `GET /api/live2d/model_config/{model_name}`

Get a model's full configuration (position, scale, expression mappings).

### `POST /api/live2d/model_config/{model_name}`

Save model configuration.

### `GET /api/live2d/model_config_by_id/{model_id}`

Get configuration by Steam Workshop item ID.

### `POST /api/live2d/model_config_by_id/{model_id}`

Save configuration by Workshop item ID.

## Emotion mapping

### `GET /api/live2d/emotion_mapping/{model_name}`

Get emotion-to-animation mappings for a model.

**Response example:**

```json
{
  "happy": { "expression": "f01", "motion": "idle_01" },
  "sad": { "expression": "f03", "motion": "idle_02" }
}
```

### `POST /api/live2d/emotion_mapping/{model_name}`

Update emotion mappings.

## Parameters

### `GET /api/live2d/model_parameters/{model_name}`

Get all available model parameters (for the parameter editor).

### `POST /api/live2d/save_model_parameters/{model_name}`

Save adjusted model parameters.

### `GET /api/live2d/load_model_parameters/{model_name}`

Load previously saved model parameters.

## File management

### `GET /api/live2d/model_files/{model_name}`

List all files belonging to a model.

### `GET /api/live2d/model_files_by_id/{model_id}`

List files by Workshop item ID.

### `POST /api/live2d/upload_model`

Upload a new Live2D model (multipart form with model archive).

### `POST /api/live2d/upload_file/{model_name}`

Upload an additional file to an existing model.

### `DELETE /api/live2d/model/{model_name}`

Delete a model and all its files.

### `GET /api/live2d/open_model_directory/{model_name}`

Open the model's directory in the system file explorer.
