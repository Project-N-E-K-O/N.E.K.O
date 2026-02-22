# Live2D Integration

## Overview

N.E.K.O. renders Live2D models using the Cubism SDK via Pixi.js. Models are displayed in the main chat interface and respond to emotions detected in conversation.

## Model sources

| Source | Location |
|--------|----------|
| Built-in | `static/` directory |
| User-imported | `user_live2d/` directory |
| Steam Workshop | `workshop/` directory (auto-mounted) |

## Emotion mapping

Each Live2D model can define mappings from emotion labels to expressions and motions:

```json
{
  "happy": { "expression": "f01", "motion": "idle_01" },
  "sad": { "expression": "f03", "motion": "idle_02" },
  "angry": { "expression": "f05", "motion": "idle_03" }
}
```

Emotions are detected by the backend (`/api/analyze_emotion`) and sent to the frontend via WebSocket.

## UI components

| Module | Purpose |
|--------|---------|
| `live2d-ui-buttons.js` | Control buttons (model switch, settings) |
| `live2d-ui-drag.js` | Drag and zoom for model positioning |
| `live2d-ui-hud.js` | Heads-up display overlays |
| `live2d-ui-popup.js` | Popup dialogs and menus |

## Model management pages

- `/model_manager` — Browse, upload, and delete models
- `/live2d_parameter_editor` — Fine-tune model parameters
- `/live2d_emotion_manager` — Configure emotion-to-animation mappings

## API endpoints

See [Live2D API](/api/rest/live2d) for the full REST endpoint reference.
