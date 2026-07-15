# PNGTuber Models

## Overview

N.E.K.O. can render lightweight 2D image avatars ("PNGTuber" style) as an alternative to Live2D, MMD, or VRM models. PNGTuber avatars are driven by `static/pngtuber-core.js` (the `PNGTuberManager` class), which swaps between still images (or, for imported layered projects, draws a stacked canvas) in response to speech and pointer interaction.

Unlike the 3D/Live2D avatars, a PNGTuber package is just a folder of images plus a `model.json` descriptor — no rigging, no Cubism runtime.

## Package format

A PNGTuber model is a folder containing a `model.json` file with `model_type` set to `pngtuber`. The image references live under the `pngtuber` object:

```json
{
  "name": "My Avatar",
  "model_type": "pngtuber",
  "pngtuber": {
    "idle_image": "idle.png",
    "talking_image": "talking.png",
    "drag_image": "drag.png",
    "click_image": "click.png",
    "happy_image": "happy.png",
    "sad_image": "sad.png",
    "angry_image": "angry.png",
    "surprised_image": "surprised.png"
  }
}
```

### Image-state keys

| Key | Purpose |
|-----|---------|
| `idle_image` | **Required.** Default resting frame. |
| `talking_image` | Shown while the assistant is speaking. |
| `drag_image` | Shown while the avatar is being dragged. |
| `click_image` | Shown briefly when the avatar is clicked. |
| `happy_image` / `sad_image` / `angry_image` / `surprised_image` | Emotion frames (see [Emotion states](#emotion-states)). |

Relative paths resolve inside the package folder; absolute paths (`/…`) and `http(s)://` URLs are kept as-is. Image references are normalized server-side to `/user_pngtuber/<folder>/<file>`.

### Allowed extensions and size limits

| Constraint | Value |
|------------|-------|
| Image extensions | `.png`, `.gif`, `.jpg`, `.jpeg`, `.webp` |
| Max single file | 50 MB |
| Max package total | 250 MB |

The server validates that `idle_image` is present and that every `*_image` reference points to an existing file with an allowed extension before the package is accepted.

## Emotion states

`window.applyEmotion('happy')` drives PNGTuber emotions at runtime. `applyEmotion` (in `static/app-buttons.js`) routes to `window.pngtuberManager.setEmotion(emotion)` whenever the active `model_type` is `pngtuber`, before it would otherwise fall back to the Live2D path.

- **Simple packages** — `setEmotion('happy')` swaps to `happy_image` / `sad_image` / `angry_image` / `surprised_image` for a default of 5 seconds, then reverts to idle. Neutral values (`neutral`, `idle`, `default`, `none`, `clear`, or empty) clear the emotion immediately.
- **Layered packages** — `setEmotion` maps the emotion onto a layered state via `setLayeredEmotion`. Remix `emotion_mappings` take precedence; otherwise a fallback order of happy → 1, sad → 2, angry → 3, surprised → 4 applies when the model exposes at least five states.

`idle` ↔ `talking` is still toggled by assistant **speech** start/end events, and `drag` / `click` by pointer **interaction**. If no matching emotion image or state exists, `setEmotion` returns without changing the frame and logs `[PNGTuber] emotion unavailable`.

## Import formats

The upload endpoint detects the package type and normalizes it in place. The detected type is reported back as `source_format`.

| Source | Detection | `source_format` |
|--------|-----------|-----------------|
| Native simple package | `model.json` in the folder root | `source_format: "simple_package"` |
| PNGTuber-Plus | a `.save` project file | `source_format: "pngtuber_plus_save"` |
| PNGTube-Remix | a `.pngRemix` project file | `source_format: "pngtube_remix_pngremix"` |
| veadotube | a `.veadomini` / `.veado` file | `source_format: "veadotube"` |
| Images only, no project file | image files with no `model.json` | `source_format: "image_pair_candidate"` |

### Layered adapter (`layered_canvas_v1`)

When a PNGTuber-Plus or PNGTube-Remix project is imported, the converter emits a layered-metadata file (`adapter_version: 2`) and sets `adapter` to `layered_canvas_v1`. At runtime, `PNGTuberManager` draws the layers onto a `<canvas>` instead of swapping a single `<img>`. Plus and Remix are dispatched by `source_format` so their runtimes never contaminate each other. Both add randomized blink timers and a speech bounce; if the metadata fails to load, the runtime falls back to plain single-image mode.

## Capability matrix

`window.pngtuberManager.getDebugState()` reports which capabilities are live for the loaded model.

| Capability | `simple_package` | `pngtuber_plus_save` | `pngtube_remix_pngremix` |
|------------|:----------------:|:--------------------:|:------------------------:|
| idle / talking swap | ✅ | ✅ | ✅ |
| Emotion via `window.applyEmotion('happy')` | ✅ image swap | ✅ layered state | ✅ layered state |
| Blink + speech bounce | — | ✅ | ✅ |
| Costume hotkeys / toggles | — | ✅ | — |
| Sprite-sheet frames | — | ✅ | ✅ |
| `physics_v2` | — | approximate | ✅ |
| Mesh deformation | — | — | ✅ when real geometry ships |

Mesh deformation only flips `meshRuntime` to `true` in the debug state when the Remix project ships real vertices / triangles / UVs. Otherwise `meshMetadata` stays `true`, `meshRuntime` stays `false`, and the reason is listed under `unsupportedFeatures`.

### Failure prompts

- **veadotube** (`.veadomini` / `.veado`) → `source_format: "veadotube"`; the upload is rejected with a request for a real sample to adapt against.
- **Images only** → `source_format: "image_pair_candidate"`; the upload is rejected and points at the two-image importer or adding a `model.json`.
- **Multiple `.save` files** that can't be disambiguated → HTTP 400 with `source_format: "pngtuber_plus_save"` and the candidate list in `warnings`.
- **Unparseable `.pngRemix`** → classified as a PNGTube-Remix conversion failure (`source_format: "pngtube_remix_pngremix"`), never a missing-`model.json` error.

## Acceptance checklist

Static contracts (run from the repo root):

```powershell
node --check static\pngtuber-core.js
node --check static\app-buttons.js
uv run pytest tests\unit\test_pngtuber_static_contracts.py tests\unit\test_card_maker_static_contracts.py tests\unit\test_pngtuber_router_delete.py tests\unit\test_model_manager_window_features.py
```

Manual checks:

- Import a PNGTuber-Plus `.save` and confirm costumes, toggles, talk/blink, sprite-sheet frames, parent/child transforms and rectangular clip render.
- Import a multi-state PNGTube-Remix `.pngRemix` and confirm `window.applyEmotion('happy')` maps to the right state.
- Inspect `window.pngtuberManager.getDebugState()` for `sourceFormat`, `adapterVersion`, `runtimeFeatures`, `meshRuntime` and `physicsVersion`.

## Static serving

User PNGTuber packages are served from the `/user_pngtuber` mount, which maps to the configured PNGTuber directory on disk. Model files are referenced as `/user_pngtuber/<folder>/model.json` and `/user_pngtuber/<folder>/<image>`.

## API endpoints

**Prefix:** `/api/model/pngtuber`

### `POST /upload_model`

Upload a PNGTuber package as a multipart file list. Each file's `filename` carries its relative path inside the package; a single shared top-level folder is stripped automatically. The package is staged, detected, validated, and (for third-party projects) converted before being committed.

**Body** — `multipart/form-data` with a `files` field (one or more `UploadFile` entries).

**Response** (success)

```json
{
  "success": true,
  "message": "...",
  "model_type": "pngtuber",
  "model_name": "My Avatar",
  "name": "My Avatar",
  "folder": "My_Avatar",
  "url": "/user_pngtuber/My_Avatar/model.json",
  "pngtuber": { "idle_image": "/user_pngtuber/My_Avatar/idle.png", "...": "..." },
  "source_format": "simple_package",
  "warnings": [],
  "file_size": 123456
}
```

On failure the response is `{ "success": false, "error": "..." }` with an appropriate 4xx/5xx status. Third-party import errors also include `source_format` and `warnings`.

### `GET /models`

List all installed user PNGTuber packages.

**Response**

```json
{
  "success": true,
  "models": [
    {
      "name": "My Avatar",
      "folder": "My_Avatar",
      "filename": "My_Avatar",
      "location": "user",
      "type": "pngtuber",
      "model_type": "pngtuber",
      "url": "/user_pngtuber/My_Avatar/model.json",
      "pngtuber": { "idle_image": "/user_pngtuber/My_Avatar/idle.png", "...": "..." },
      "source_format": "simple_package"
    }
  ]
}
```

Folders without a valid `model.json` or whose `model_type` is not `pngtuber` are skipped.

### `DELETE /model`

Delete an installed PNGTuber package.

**Body**

```json
{ "folder": "My_Avatar" }
```

The identifier is resolved as a **folder slug** (precedence `folder` → `url` → `name`): a `model.json` URL such as `/user_pngtuber/My_Avatar/model.json` is resolved back to its folder. Prefer the `folder` slug (or the `url`) from `GET /models` — `name` is the human-readable display name and may differ from the slug, so passing `name` only works when it equals the folder. The target is confined to the PNGTuber directory.

**Response**

```json
{ "success": true, "message": "PNGTuber model My_Avatar deleted" }
```

::: info
PNGTuber model management lives in the shared `/model_manager` page. There is no separate PNGTuber emotion-manager page; the avatar's settings menu links to the character card manager, model manager, and voice clone pages.
:::
