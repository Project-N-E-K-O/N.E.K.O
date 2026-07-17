# Live2D API

**Prefix:** `/api/live2d`

These 17 routes back N.E.K.O.'s first-party Live2D model manager and parameter editor. They read and mutate model files on the local machine; they are not a remote model-hosting API.

## Route inventory

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/live2d/models` | List built-in, user, and installed Workshop models |
| `GET` | `/api/live2d/user_models` | List user-imported models |
| `GET` / `POST` | `/api/live2d/model_config/{model_name}` | Read a Cubism `.model3.json`; update its motion/expression references |
| `GET` / `POST` | `/api/live2d/model_config_by_id/{model_id}` | Same operations for a Steam Workshop item ID |
| `GET` / `POST` | `/api/live2d/emotion_mapping/{model_name}` | Read or write emotion-to-motion/expression mappings |
| `GET` | `/api/live2d/model_files/{model_name}` | List `.motion3.json` and `.exp3.json` files |
| `GET` | `/api/live2d/model_files_by_id/{model_id}` | List those files by Workshop ID, with model-name fallback |
| `GET` | `/api/live2d/model_parameters/{model_name}` | Read parameter metadata from `.cdi3.json` |
| `POST` | `/api/live2d/save_model_parameters/{model_name}` | Save editor values to `parameters.json` |
| `GET` | `/api/live2d/load_model_parameters/{model_name}` | Load saved editor values |
| `POST` | `/api/live2d/upload_model` | Import a multi-file model package |
| `POST` | `/api/live2d/upload_file/{model_name}` | Add one motion or expression JSON file |
| `GET` | `/api/live2d/open_model_directory/{model_name}` | Open a local model directory in the OS file manager |
| `DELETE` | `/api/live2d/model/{model_name}` | Delete a user-imported model |

## Listing

`GET /api/live2d/models?simple=false` returns the full model array directly for compatibility. With `simple=true`, it returns `{ "success": true, "models": ["..."] }`. Workshop models are included only when Steam reports an installed item containing a `.model3.json` file.

`GET /api/live2d/user_models` returns `{ "success": true, "models": [...] }` for the user model directories N.E.K.O. can read.

## Configuration and emotion mappings

The configuration `GET` routes return `{ "success": true, "config": {...} }`. If a readable `.model3.json` lacks `FileReferences.Motions` or `FileReferences.Expressions`, the handler adds the missing containers and attempts to write them back.

The configuration `POST` routes accept a JSON object shaped like Cubism configuration, but deliberately persist only:

```json
{
  "FileReferences": {
    "Motions": {},
    "Expressions": []
  }
}
```

Other submitted `.model3.json` fields are ignored. This is not an endpoint for replacing the full model configuration.

`GET /api/live2d/emotion_mapping/{model_name}` returns the stored `EmotionMapping`, or derives `{ "motions": {...}, "expressions": {...} }` from `FileReferences`. `POST` accepts that latter shape. It normalizes safe relative paths, writes both the standard Cubism references and the compatibility `EmotionMapping`, and ignores motions in the reserved `常驻` group.

## Files and parameters

- `model_files` and `model_files_by_id` recursively return `motion_files` and `expression_files`; the ID variant also returns `model_config_url`.
- `model_parameters` reads parameter and group metadata from the model's `.cdi3.json`. It does not read live runtime values.
- `save_model_parameters` expects `{ "parameters": { ... } }`; `parameters` must be an object.
- `load_model_parameters` returns an empty object when `parameters.json` does not exist or is not an object.

## Import and mutation

`POST /api/live2d/upload_model` is `multipart/form-data` with one or more `files` parts whose filenames preserve relative paths. It is **not** a single archive upload. Exactly one `.model3.json` must be present. Unsafe paths, zero/multiple model configuration files, or an existing valid destination produce HTTP `400`. After import, N.E.K.O. clears mouth/lip-sync curves from uploaded motion files so runtime lip sync can drive them.

`POST /api/live2d/upload_file/{model_name}?file_type=motion` accepts one `file` part. `file_type` is `motion` or `expression`; the file must respectively end in `.motion3.json` or `.exp3.json`, contain UTF-8 JSON, and be at most 50 MB. Existing files are not overwritten.

`DELETE /api/live2d/model/{model_name}` only deletes models inside a writable user import directory. Built-in/Workshop models and user directories made read-only by Windows protection return HTTP `403`.

`GET /api/live2d/open_model_directory/{model_name}` launches Explorer, Finder, or `xdg-open`; it has a local desktop side effect and is intended for the first-party settings UI.

## Errors

Most mutation failures use `{ "success": false, "error": "..." }` with HTTP `400`, `403`, `404`, or `500`. Some legacy read helpers return the same failure envelope with HTTP `200`; always inspect `success` rather than relying on status alone. Paths have no trailing slash.
