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
| `happy_image` / `sad_image` / `angry_image` / `surprised_image` | Emotion frames (see [Emotion states](#emotion-states-not-yet-driven-by-emotion-analysis)). |

Relative paths resolve inside the package folder; absolute paths (`/…`) and `http(s)://` URLs are kept as-is. Image references are normalized server-side to `/user_pngtuber/<folder>/<file>`.

### Allowed extensions and size limits

| Constraint | Value |
|------------|-------|
| Image extensions | `.png`, `.gif`, `.jpg`, `.jpeg`, `.webp` |
| Max single file | 50 MB |
| Max package total | 250 MB |

The server validates that `idle_image` is present and that every `*_image` reference points to an existing file with an allowed extension before the package is accepted.

## Emotion states (not yet driven by emotion analysis)

::: warning Honest status
The `happy_image` / `sad_image` / `angry_image` / `surprised_image` keys are part of the package schema and are **server-validated** (path and extension are checked on upload), but the PNGTuber runtime does **not** yet switch to them based on emotion analysis.

`PNGTuberManager` only drives:

- `idle` ↔ `talking`, toggled by assistant **speech** start/end events.
- `drag` and `click`, toggled by pointer **interaction**.

There is no `setEmotion`-style hook equivalent to Live2D / MMD / VRM, and there is no dedicated PNGTuber emotion-manager page. The four emotion image keys are stored and shipped with the package so they are ready for a future emotion-driven path, but supplying them today has no visible effect beyond passing validation.
:::

## Import formats

The upload endpoint detects the package type and normalizes it in place. The detected type is reported back as `source_format`.

| Source | Detection | Result |
|--------|-----------|--------|
| Native simple package | `model.json` in the folder root | `source_format: simple_package` — used directly. |
| **PNGTuber-Plus** | a `.save` project file | Converted to the `layered_canvas_v1` adapter. |
| **PNGTube-Remix** | a `.pngremix` project file | Converted to the `layered_canvas_v1` adapter. |
| veadotube | a `.veadomini` / `.veado` file | **Recognized but unsupported** — upload is rejected with an explanatory error. |

### Layered adapter (`layered_canvas_v1`)

When a PNGTuber-Plus or PNGTube-Remix project is imported, the converter emits a layered-metadata file and sets `adapter` to `layered_canvas_v1`. At runtime, `PNGTuberManager` draws the layers onto a `<canvas>` instead of swapping a single `<img>`, and adds:

- **Blink** — eye layers blink on a randomized timer.
- **Speech bounce** — the avatar bounces/squishes while talking.

Hotkeys, physics, and multi-frame animation from the source project are preserved in the metadata for later runtime support but are not all driven yet. If the metadata fails to load, the runtime falls back to plain single-image mode.

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
