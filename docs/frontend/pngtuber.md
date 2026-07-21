# PNGTuber models

## Runtime

PNGTuber avatars are image-state models rendered by `static/pngtuber-core.js`. `PNGTuberManager` supports simple image swapping and normalized layered packages through the `layered_canvas_v1` adapter. The renderer integrates with the same main-page avatar selection and `window.LanLan1.setEmotion()` contract as Live2D, VRM, and MMD.

## Normalized package

Every imported model is stored under the configured user PNGTuber directory and exposed as `/user_pngtuber/{folder}/model.json`. The minimum simple package is:

```json
{
  "model_type": "pngtuber",
  "name": "Example",
  "pngtuber": {
    "idle_image": "idle.png",
    "talking_image": "talking.png"
  }
}
```

`model_type` must be `pngtuber`, and `idle_image` is required. Relative image paths must remain inside the package. Supported image extensions are `.png`, `.gif`, `.jpg`, `.jpeg`, and `.webp`.

Optional image-state keys are `talking_image`, `drag_image`, `click_image`, `happy_image`, `sad_image`, `angry_image`, and `surprised_image`. The runtime falls back from missing states—for example, talking can use idle—so only `idle_image` is mandatory.

Layout keys include `scale`, `offset_x`, `offset_y`, mobile-specific scale/offset values, and `mirror`. Layered imports additionally use `adapter: "layered_canvas_v1"` and `layered_metadata`.

## Import formats

The importer detects formats in this order:

1. a native simple package with root `model.json`;
2. a PNGTuber Plus `.save` project;
3. a PNGTube Remix `.pngRemix` project;
4. veadotube `.veadomini` or `.veado` files.

PNGTuber Plus and PNGTube Remix projects are converted into the normalized package and may produce layered metadata and warnings. veadotube is currently recognized but rejected as unsupported. A folder containing only images is also rejected by the package endpoint; use the model manager's image-pair flow or supply a valid `model.json`.

The request may upload a folder tree. The server removes one shared top-level directory, validates every relative path, writes through a temporary directory, and only renames the package into place after import succeeds. Existing model folders are never overwritten.

Limits are 50 MB per file and 250 MB for the complete package.

## State and emotion behavior

The base states are idle, talking, drag, and click. Semantic emotions use the four optional `happy`, `sad`, `angry`, and `surprised` images or equivalent layered states. Layered adapters can preserve third-party visibility states, hotkeys, toggles, sprite sheets, blinking, and physics metadata when the source importer supports them.

The normalized `source_format` reports how the package was produced; clients should treat it as diagnostics, not select rendering behavior from it. Rendering behavior is determined by the normalized `pngtuber` object and adapter metadata.

## API summary

All endpoints use the `/api/model/pngtuber` prefix.

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `POST` | `/api/model/pngtuber/upload_model` | Upload and normalize a folder/package |
| `GET` | `/api/model/pngtuber/models` | List valid user packages |
| `DELETE` | `/api/model/pngtuber/model` | Delete one package by `folder`, `url`, or `name` |

Successful upload responses include the normalized model, public URL, `source_format`, warnings, and total uploaded size. The list endpoint skips directories without a valid PNGTuber `model.json`. Deletion accepts only a direct package folder or `/user_pngtuber/{folder}/model.json`; nested and traversal paths are rejected.

## Host boundary

PNGTuber renders in the main page and Electron pet window that use `index.html`. `/chat` and `/subtitle` do not initialize another PNGTuber manager; they communicate with the main window when avatar state must be reflected across windows.
