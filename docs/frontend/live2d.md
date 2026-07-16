# Live2D models

## Runtime

Live2D is rendered on the main-page `#live2d-canvas`. The current implementation is split across:

- `static/live2d/live2d-core.js`
- `static/live2d/live2d-model.js`
- `static/live2d/live2d-emotion.js`
- `static/live2d/live2d-interaction.js`
- `static/live2d/live2d-init.js`
- `static/live2d/live2d-ui-buttons.js`

`live2d-init.js` creates `window.live2dManager` and exposes the shared `window.LanLan1` compatibility methods. The model manager loads the same renderer modules for preview; there is no separate legacy Live2D page implementation.

## Models and sources

A model is discovered through a Cubism `.model3.json` file and its referenced `.moc3`, textures, motions, expressions, and optional physics files. Keep the model's relative directory structure intact.

`GET /api/live2d/models` combines:

- bundled models under the project static model directory;
- user models exposed through `/user_live2d` (and the writable `/user_live2d_local` shadow when configured);
- installed Steam Workshop models exposed through `/workshop/{item_id}/...`.

Use the URL returned by the API. Do not construct a URL from an absolute filesystem path.

## Emotion mapping

The editor and runtime use this logical shape:

```json
{
  "motions": { "happy": ["motions/happy.motion3.json"] },
  "expressions": { "happy": ["expressions/happy.exp3.json"] }
}
```

The server reads existing `EmotionMapping` data when present. Otherwise it derives groups from `FileReferences.Motions` and expression-name prefixes. Saving writes the standard Cubism `FileReferences.Motions` and `FileReferences.Expressions` structures; motion and expression paths must stay relative and may not traverse outside the model directory.

`window.LanLan1.setEmotion(name)` delegates to the active renderer. For Live2D, the manager applies a configured expression and motion, with graceful fallback when one side is absent. The special `常驻` group is expression-only.

## Management pages

- `/model_manager` selects, imports, previews, and deletes models.
- `/live2d_emotion_manager` maps emotion groups to motions and expressions.
- `/live2d_parameter_editor` edits saved model layout/parameter settings.

## API summary

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `GET` | `/api/live2d/models` | List local and Workshop models; `?simple=true` returns names only |
| `GET`, `POST` | `/api/live2d/model_config/{model_name}` | Read the Cubism config; update only motions and expressions |
| `GET`, `POST` | `/api/live2d/emotion_mapping/{model_name}` | Read or save emotion groups |
| `GET` | `/api/live2d/model_files/{model_name}` | List validated model resources |
| `GET` | `/api/live2d/model_parameters/{model_name}` | Inspect Cubism parameter metadata |
| `GET`, `POST` | `/api/live2d/load_model_parameters/{model_name}`, `/api/live2d/save_model_parameters/{model_name}` | Load or save parameter settings |
| `POST` | `/api/live2d/upload_model` | Import a multi-file model package |
| `POST` | `/api/live2d/upload_file/{model_name}` | Add a motion or expression file; maximum 50 MB |
| `DELETE` | `/api/live2d/model/{model_name}` | Delete a user model |

ID-based variants (`model_config_by_id` and `model_files_by_id`) support Workshop-backed models whose stable identity is the published item ID.

## Host boundary

Live2D assets and initialization run in `index.html`, including the Electron pet window that loads that template. Standalone `/chat` and `/subtitle` pages do not render a second avatar. Cross-window commands must be forwarded to the main page rather than initializing another Live2D manager.
