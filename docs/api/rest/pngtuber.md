# PNGTuber API

**Prefix:** `/api/model/pngtuber`

Manages PNGTuber avatars — 2D image-based avatars whose appearance is driven by swapping image states (idle, talking, reactions). Endpoints cover package upload, listing, and deletion.

These are three first-party local model-management routes: `POST /api/model/pngtuber/upload_model`, `GET /api/model/pngtuber/models`, and `DELETE /api/model/pngtuber/model`. Upload/delete write the user model directory, use `{ "success": false, "error": "..." }` on failure, and have no separate authentication layer. Paths have no trailing slash.

## Model package

A PNGTuber model is a folder (uploaded as a multi-file package) containing a `model.json` with `model_type` set to `"pngtuber"`. The `pngtuber` config block maps avatar states to image files. An `idle_image` is required; all other states are optional.

Supported image states:

- `idle_image` (**required**)
- `talking_image`
- `drag_image`
- `click_image`
- `happy_image`
- `sad_image`
- `angry_image`
- `surprised_image`

Supported image extensions: `.png`, `.gif`, `.jpg`, `.jpeg`, `.webp`.

::: info
Size limits: each file may be at most **50 MB**, and the whole package at most **250 MB**.
:::

## Upload

### `POST /api/model/pngtuber/upload_model`

Upload a PNGTuber package as a multi-file `multipart/form-data` request. Each part is a file whose `filename` carries its relative path inside the package (a common top-level folder is stripped automatically). Files are streamed into a staging directory, the package is detected and normalized, validated, and then committed to the user model directory.

**Body:** `multipart/form-data` with one or more `files` parts. The package must contain either a root `model.json` (`model_type: "pngtuber"`) or a recognized third-party project file (see Import adapters below).

**Response (success):**

```json
{
  "success": true,
  "message": "...",
  "model_type": "pngtuber",
  "model_name": "...",
  "name": "...",
  "folder": "...",
  "url": "/user_pngtuber/<folder>/model.json",
  "pngtuber": { },
  "source_format": "simple_package",
  "warnings": [],
  "file_size": 0
}
```

The `pngtuber` object is the normalized config: image-state paths rewritten under `/user_pngtuber/<folder>/...`, plus layout fields (`scale`, `offset_x`, `offset_y`, `mobile_scale`, `mobile_offset_x`, `mobile_offset_y`, `mirror`), `adapter`, `layered_metadata`, `source_type`, and `source_format`.

On error the response is `{ "success": false, "error": "..." }` (recognized-but-failed imports also include `source_format` and `warnings`).

::: info
Validation requires `model_type` to be `"pngtuber"` and a non-empty `idle_image`. Each relative `*_image` path must use a supported extension and point to a file that actually exists in the package.
:::

#### Import adapters

When the package is not already a native `model.json`, the uploader detects the source format and converts it in place. The detected format is echoed back as `source_format`:

- `source_format: "simple_package"` — native N.E.K.O package: a root `model.json` with `model_type: "pngtuber"`. Used as-is; drives idle/talking/drag/click plus lightweight emotion images.
- `source_format: "pngtuber_plus_save"` — PNGTuber-Plus (`.save`), converted through the **`layered_canvas_v1`** adapter (`adapter_version: 2`): costumes, toggles, talk/blink, multi-frame sprite sheets, the Plus node tree, rectangular clip and approximate physics.
- `source_format: "pngtube_remix_pngremix"` — PNGTube-Remix (`.pngRemix`), converted through the **`layered_canvas_v1`** adapter (`adapter_version: 2`): state switching, `emotion_mappings`, sprite sheets, `effective_z_index` ordering, `physics_v2` and usable mesh deformation.
- `source_format: "veadotube"` — veadotube (`.veadomini` / `.veado`); recognized but **not supported**. The upload is rejected with a request for a sample to adapt against.
- `source_format: "image_pair_candidate"` — image files with no `model.json` or project file; rejected and pointed at the two-image importer.

#### Capability and failure matrix

`window.pngtuberManager.getDebugState()` reports the live capabilities per `source_format`. Emotions are driven by `window.applyEmotion('happy')`, which routes to `pngtuberManager.setEmotion` for `pngtuber` models.

| Capability | `simple_package` | `pngtuber_plus_save` | `pngtube_remix_pngremix` |
|------------|:----------------:|:--------------------:|:------------------------:|
| idle / talking swap | ✅ | ✅ | ✅ |
| Emotion via `window.applyEmotion('happy')` | ✅ image swap | ✅ layered state | ✅ layered state |
| Blink + speech bounce | — | ✅ | ✅ |
| Costume hotkeys / toggles | — | ✅ | — |
| Sprite-sheet frames | — | ✅ | ✅ |
| `physics_v2` | — | approximate | ✅ |
| Mesh deformation (`meshRuntime`) | — | — | ✅ when real geometry ships |

`meshRuntime` only reads `true` in the debug state when the Remix project ships real vertices / triangles / UVs; otherwise `meshMetadata` stays `true`, `meshRuntime` stays `false`, and the reason is listed under `unsupportedFeatures`.

Failure responses:

- `source_format: "veadotube"` → recognized but rejected; awaits a real sample.
- `source_format: "image_pair_candidate"` → rejected; use the two-image importer or add a `model.json`.
- Multiple ambiguous `.save` files → HTTP 400 with `source_format: "pngtuber_plus_save"` and the candidate list in `warnings`.
- Unparseable `.pngRemix` → PNGTube-Remix conversion failure (`source_format: "pngtube_remix_pngremix"`), never a missing-`model.json` error.

#### Acceptance checks

```powershell
node --check static\pngtuber-core.js
node --check static\app-buttons.js
uv run pytest tests\unit\test_pngtuber_static_contracts.py tests\unit\test_card_maker_static_contracts.py tests\unit\test_pngtuber_router_delete.py tests\unit\test_model_manager_window_features.py
```

## List

### `GET /api/model/pngtuber/models`

List all imported PNGTuber models. Each entry is read from a package's `model.json` (only folders whose `model.json` has `model_type: "pngtuber"` are included; invalid packages are skipped).

**Response:**

```json
{
  "success": true,
  "models": [
    {
      "name": "...",
      "folder": "...",
      "filename": "...",
      "location": "user",
      "type": "pngtuber",
      "model_type": "pngtuber",
      "url": "/user_pngtuber/<folder>/model.json",
      "pngtuber": { },
      "source_format": "simple_package"
    }
  ]
}
```

## Delete

### `DELETE /api/model/pngtuber/model`

Delete a PNGTuber model package and all of its files.

**Body:**

```json
{ "folder": "<folder>" }
```

The target is resolved by **folder slug**: the handler reads `folder`, falling back to `url`, then `name`. Whichever value is supplied is treated as a folder slug (a `url` pointing at `.../<folder>/model.json` is resolved down to its `<folder>`), never matched against the human-readable display name.

Prefer deleting by the `folder` slug returned from `GET /models`, or by the model.json `url`. Avoid relying on `name`: `GET /models` returns `name` as the display name and `folder` as the on-disk slug, and the two can differ — passing the display `name` only works when it happens to equal the folder slug, so use it as a last-resort fallback that may be ambiguous. The resolved path must stay inside the PNGTuber directory.

**Response:** `{ "success": true, "message": "..." }`. Missing identifier or out-of-bounds path returns `400`; a non-existent model returns `404`.

Unexpected import, listing, or filesystem failures return HTTP `500`. Treat this as a first-party file-management API and do not expose it to untrusted clients.
