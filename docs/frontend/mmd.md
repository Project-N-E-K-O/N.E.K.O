# MMD Models

## Overview

N.E.K.O. renders MMD (MikuMikuDance) models with Three.js and its MMD loader. A built-in model ships at `static/mmd/Miku/Miku.pmx` and is loaded by default. Models respond to emotions detected in conversation, driven through **morph-target (blendshape)** weights rather than Live2D-style motion files.

## Formats

| Kind | Extensions | Notes |
|------|-----------|-------|
| Model | `.pmx`, `.pmd` | Loaded by the Three.js MMD loader |
| Animation | `.vmd` | Pose / motion tracks (idle, lip-sync, etc.) |

::: info
Maximum upload size is **500 MB** (MMD models with textures can be large). See `MAX_FILE_SIZE` in `main_routers/mmd_router.py` (lines 57–64). ZIP packages are additionally bounded by a 2 GB uncompressed limit and 10000-entry limit to guard against zip bombs.
:::

## Model sources

| Source | Location |
|--------|----------|
| Built-in | `/static/mmd` (e.g. `static/mmd/Miku/Miku.pmx`) |
| User-imported models | `/user_mmd` |
| User-imported animations | `/user_mmd/animation` |
| Steam Workshop | `/workshop/<item_id>/...` (auto-mounted) |

Per-model emotion mapping overrides are stored under `/user_mmd/emotion_config` as `<model>.json`.

## Rendering & static modules

The viewer is composed of `mmd-*.js` modules under `static/mmd/`:

| Module | Purpose |
|--------|---------|
| `mmd-core.js` | Three.js scene, renderer, and MMD model loading |
| `mmd-manager.js` | Top-level manager (`window.mmdManager`) wiring submodules together |
| `mmd-init.js` | Bootstrap / initialization |
| `mmd-animation.js` | VMD animation playback and lip-sync values |
| `mmd-expression.js` | Morph-target control and the emotion system (`mmd-expression.js`) |
| `mmd-interaction.js` | Pointer / interaction handling |
| `mmd-cursor-follow.js` | Cursor-follow behavior |
| `mmd-ui-buttons.js` | MMD-specific control buttons |
| `mmd-ui-debug.js` | Debug overlay |

## Emotion mapping

Unlike Live2D (which maps emotions to expression + motion files), MMD emotions are applied as **morph-target / blendshape** weights. `mmd-expression.js` ships a default `moodMap` from emotion labels to candidate morph names (Japanese / English), for example:

```javascript
{
  "happy":     ["笑い", "にやり", "にこり", "smile", "happy", "joy", "ワ"],
  "sad":       ["悲しい", "泣き", "sad", "sorrow", "しょんぼり"],
  "angry":     ["怒り", "angry", "anger", "むっ"],
  "surprised": ["驚き", "びっくり", "surprised", "shock", "おっ"],
  "relaxed":   ["穏やか", "relaxed", "calm", "微笑み"],
  "fear":      ["恐怖", "fear", "scared", "おびえ"]
}
```

When an emotion is set, `MMDExpression.setEmotion(emotion)` looks up the candidate morph names, picks the first one that exists on the current model, and drives its weight to `1.0` (auto-returning to neutral after a delay). It is applied via:

```javascript
window.mmdManager.expression.setEmotion('happy');
```

Each model can override the default map. The frontend calls `GET /api/model/mmd/emotion_mapping?model=<name>` (via `loadMoodMap()`) and merges the returned mapping over the defaults; the editor saves overrides through `POST /api/model/mmd/emotion_mapping`.

## Model management pages

- `/model_manager` — Browse, upload, and delete MMD models and animations
- `/mmd_emotion_manager` — Configure per-model emotion-to-morph mappings

## REST API

**Prefix:** `/api/model/mmd`

Defined in `main_routers/mmd_router.py`. All success responses are JSON with a `success` boolean; error responses use `{ "success": false, "error": "..." }` with an appropriate status code.

### `POST /api/model/mmd/upload`

Upload an MMD model file (`.pmx` / `.pmd`).

**Body:** `multipart/form-data` with a model `file`.

**Response:** `{ success, message, model_name, model_url, file_size }`. The file is streamed in 1 MB chunks into `/user_mmd`; uploading a name that already exists is rejected.

### `POST /api/model/mmd/upload_animation`

Upload a `.vmd` animation file.

**Body:** `multipart/form-data` with a `.vmd` `file`.

**Response:** `{ success, message, filename, file_path }`. Stored under `/user_mmd/animation`.

### `POST /api/model/mmd/upload_zip`

Upload a `.zip` package (model `.pmx`/`.pmd` plus textures), automatically extracted into a subdirectory.

**Body:** `multipart/form-data` with a `.zip` `file`.

**Response:** `{ success, message, model_name, model_url, file_count, file_size }`.

::: info
ZIP filenames are decoded with CJK-aware detection (Shift-JIS / CP932, GBK, Big5, EUC-KR) because MMD packages frequently use non-UTF-8 names. Path traversal, zip bombs, and reserved directory names (`animation`, `emotion_config`) are rejected.
:::

### `GET /api/model/mmd/models`

List MMD models. Searches built-in `static/mmd`, user `/user_mmd` (recursively, skipping reserved dirs), and subscribed Steam Workshop items.

**Response:** `{ success, models: [...] }`. Each entry includes `name`, `filename`, `url`, `rel_path`, `type`, `size`, and `location` (`project`, `user`, or `steam_workshop`). Leftover model directories with no valid model file are returned with `broken: true`.

### `GET /api/model/mmd/animations`

List `.vmd` animations from built-in `static/mmd/animation` and user `/user_mmd/animation`.

**Response:** `{ success, animations: [...] }` with `name`, `filename`, `url`, `type`, `size`.

### `GET /api/model/mmd/config`

Return MMD path configuration.

**Response:** `{ success, paths: { user_mmd: "/user_mmd", static_mmd: "/static/mmd" } }`.

### `GET /api/model/mmd/emotion_mapping`

Get the emotion mapping for a model.

**Query:** `model=<name>`.

**Response:** `{ success, mapping }`. Returns an empty mapping when no override is stored. Model names containing path separators are rejected.

### `POST /api/model/mmd/emotion_mapping`

Update the emotion mapping for a model.

**Body:** JSON `{ "model": "<name>", "mapping": { ... } }`.

**Response:** `{ success, message }`. The mapping is written atomically to `/user_mmd/emotion_config/<model>.json`.

### `DELETE /api/model/mmd/model`

Delete a user-imported model (and the associated resources in its directory).

**Body:** JSON `{ "url": "/user_mmd/<...>" }`.

**Response:** `{ success, message, deleted_files }`. A model in a subdirectory removes the whole subdirectory; the matching `emotion_config/<model>.json` is also deleted. Built-in `/static/mmd/` models cannot be deleted.

### `GET /api/model/mmd/animations/list`

List user `.vmd` animations eligible for deletion (from `/user_mmd/animation`).

**Response:** `{ success, animations: [...] }` with `name`, `filename`, `url`, `path`.

### `DELETE /api/model/mmd/animation`

Delete a user-imported `.vmd` animation.

**Body:** JSON `{ "url": "/user_mmd/animation/<file>.vmd" }`.

**Response:** `{ success, message }`. Only `.vmd` files under `/user_mmd/animation` may be deleted.
