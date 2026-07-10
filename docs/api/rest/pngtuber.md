# PNGTuber API

**Prefix:** `/api/model/pngtuber`

Manages PNGTuber avatars — 2D image-based avatars whose appearance is driven by swapping image states (idle, talking, reactions). Endpoints cover package upload, listing, and deletion.

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

When the package is not already a native `model.json`, the uploader detects the source format and converts it in place:

- **`simple_package`** — native N.E.K.O package: a root `model.json` with `model_type: "pngtuber"`. Used as-is.
- **PNGTuber-Plus** (`.save`) → `source_format: "pngtuber_plus_save"`, converted through the **`layered_canvas_v1`** adapter. Speech and blink layers are enabled first; physics and multi-frame animation are preserved as metadata for later runtime support.
- **PNGTube-Remix** (`.pngRemix`) → `source_format: "pngtube_remix_pngremix"`, converted through the **`layered_canvas_v1`** adapter. Speech and blink layers are enabled first; hotkeys, physics, and mesh are preserved as metadata.
- **veadotube** (`.veadomini` / `.veado`) → recognized but **not supported**; the upload is rejected with `source_format: "veadotube"` and a request for a sample to adapt against.

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
