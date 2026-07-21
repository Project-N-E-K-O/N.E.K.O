# MMD models

## Runtime and formats

The MMD renderer accepts PMX and PMD models and VMD animations. Three.js loads the model, while `@moeru/three-mmd-physics-ammo` and Ammo provide optional rigid-body physics.

The implementation is split across `static/mmd/`: `mmd-core.js`, `mmd-manager.js`, `mmd-init.js`, `mmd-animation.js`, `mmd-expression.js`, `mmd-interaction.js`, `mmd-cursor-follow.js`, and the UI modules. `mmd-init.js` owns `window.mmdManager` and initializes `#mmd-canvas` only when the selected character uses an MMD avatar.

## Model and animation sources

`GET /api/model/mmd/models` recursively combines:

- bundled PMX/PMD files under `static/mmd/`;
- user models under `/user_mmd`;
- installed Workshop models under `/workshop/{item_id}/...`.

VMD animations are listed from `static/mmd/animation/` and `/user_mmd/animation/`. Model packages should keep PMX/PMD files and referenced textures in the same relative layout.

Direct PMX/PMD and VMD uploads are allowed. ZIP import is intended for a complete model plus textures: it selects the first PMX/PMD, preserves a single top-level folder when present, corrects common Japanese and CJK filename encodings, rejects absolute or parent-traversal paths, and extracts into a user-model subdirectory.

Limits are 500 MB for an uploaded file, 2 GB total uncompressed ZIP content, and 10,000 ZIP entries.

## Emotion mapping

MMD emotions map semantic names to one or more morph names:

```json
{
  "neutral": ["default", "ニュートラル"],
  "happy": ["笑い", "smile"]
}
```

Mappings are stored per model under the user's MMD `emotion_config` directory. At runtime `mmd-expression.js` merges them over built-in candidates, selects the first morph present in the loaded model, and returns non-neutral expressions to neutral after the configured delay. The editor exposes `neutral`, `happy`, `relaxed`, `sad`, `angry`, `surprised`, and `fear` groups.

Use `/mmd_emotion_manager` to inspect actual morph names and save a mapping. `window.LanLan1.setEmotion(name)` delegates to `window.mmdManager.expression.setEmotion(name)` when MMD is active.

## Management behavior

`/model_manager` imports models and animations, previews the selected avatar, and deletes user-owned content. Bundled and Workshop assets are read-only through these endpoints. Deleting a user model inside a package subdirectory removes the top-level package directory so referenced textures are not orphaned; its emotion mapping is removed as well.

## API summary

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `POST` | `/api/model/mmd/upload` | Upload one `.pmx` or `.pmd` file |
| `POST` | `/api/model/mmd/upload_animation` | Upload one `.vmd` file |
| `POST` | `/api/model/mmd/upload_zip` | Import a model package |
| `GET` | `/api/model/mmd/models` | List bundled, user, and Workshop models |
| `GET` | `/api/model/mmd/animations` | List bundled and user VMD animations |
| `GET` | `/api/model/mmd/config` | Return public MMD URL prefixes |
| `GET`, `POST` | `/api/model/mmd/emotion_mapping` | Read or save a per-model morph map |
| `DELETE` | `/api/model/mmd/model` | Delete a user model/package by public URL |
| `GET` | `/api/model/mmd/animations/list` | List user animations eligible for deletion |
| `DELETE` | `/api/model/mmd/animation` | Delete a user VMD animation by public URL |

## Host boundary

MMD renders only in the main-page avatar surface, including the Electron pet window that loads `/`. `/chat` and `/subtitle` are independent windows and must communicate with the main page instead of creating another MMD scene.
