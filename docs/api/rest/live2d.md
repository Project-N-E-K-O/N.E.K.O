# Live2D API

**Prefix:** `/api/live2d`

Manages Live2D models — listing, configuration, emotion mapping, and parameter editing.

## Model listing

### `GET /api/live2d/models`

List all available Live2D models (including Steam Workshop models).

**Query:** `simple` (optional, boolean) — If true, return only model names without full config.

## Model configuration

### `GET /api/live2d/model_config/{model_name}`

Get a model's full `model3.json` configuration.

### `POST /api/live2d/model_config/{model_name}`

Update model configuration (Motions / Expressions).

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

Update emotion mappings. Synchronises both EmotionMapping and FileReferences.

## Parameters

### `GET /api/live2d/model_parameters/{model_name}`

Get all available model parameters from the `.cdi3.json` file (for the parameter editor).

### `POST /api/live2d/save_model_parameters/{model_name}`

Save adjusted model parameters to `parameters.json`.

**Body:**

```json
{
  "parameters": { ... }
}
```

## File management

### `GET /api/live2d/model_files/{model_name}`

List motion files (`.motion3.json`) and expression files (`.exp3.json`) belonging to a model.
