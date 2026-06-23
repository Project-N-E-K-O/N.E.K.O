# Sample PNGTuber Test Model

This folder is an importable PNGTuber model package for testing the lightweight avatar pipeline.

## Purpose

Use this package to verify that PNGTuber can be imported, selected, previewed, saved to a character, loaded on the homepage, and controlled with the same basic avatar UI behaviors as the heavier model types.

## Files

- `model.json`: Package manifest. It must declare `model_type: "pngtuber"`.
- `idle.gif`: Default idle avatar image, 512x512, 253 frames.
- `talking.gif`: Talking avatar image, 512x512, 81 frames.
- `talking.gif`: Also used as the default `click_image` for click-feedback tests.
- `/static/assets/neko-idle/cat-idle-cat-move-1.gif`: Built-in hold/drag feedback image used as `drag_image`.
- `happy.png`: Optional happy expression image, 2480x2480.
- `sad.png`: Optional sad expression image, 2480x2480.
- `angry.png`: Optional angry expression image, 2480x2480.
- `surprised.png`: Optional surprised expression image, 2480x2480.
- `character-config.static.json`: Static config variant that points to this package under `/static` for direct development checks.

## Built-In GIF Presets

The manifest includes `gif_presets` that reference existing project GIF assets without copying them into this folder.

Default preset:

```json
{
  "idle_image": "idle.gif",
  "talking_image": "talking.gif",
  "click_image": "talking.gif",
  "drag_image": "/static/assets/neko-idle/cat-idle-cat-move-1.gif"
}
```

Alternate Cat 2 preset:

```json
{
  "idle_image": "/static/assets/neko-idle/cat-idle-cat2.gif",
  "talking_image": "/static/assets/neko-idle/cat-idle-cat2-click.gif",
  "click_image": "/static/assets/neko-idle/cat-idle-cat2-click.gif",
  "drag_image": "/static/assets/neko-idle/cat-idle-cat-move-2.gif"
}
```

Alternate Cat 3 preset:

```json
{
  "idle_image": "/static/assets/neko-idle/cat-idle-cat3.gif",
  "talking_image": "/static/assets/neko-idle/cat-idle-cat3-click.gif",
  "click_image": "/static/assets/neko-idle/cat-idle-cat3-click.gif",
  "drag_image": "/static/assets/neko-idle/cat-idle-cat-move-3.gif"
}
```

Optional action GIFs can be assigned to expression fields for playback checks:

```json
{
  "happy_image": "/static/assets/neko-idle/cat-idle-cat4-1.gif",
  "sad_image": "/static/assets/neko-idle/cat-idle-cat4-2.gif",
  "surprised_image": "/static/assets/neko-idle/cat-idle-cat4-3.gif"
}
```

The transition GIF can be used as a temporary `drag_image` stress test:

```json
{
  "drag_image": "/static/assets/neko-idle/cat_model_change.gif"
}
```

## Import Path

Import this whole folder through the model manager PNGTuber upload flow or the backend endpoint:

```text
POST /api/model/pngtuber/upload_model
```

After import, local image references are normalized to:

```text
/user_pngtuber/Sample_PNGTuber_Test_Model/<asset>
```

The imported model list is available at:

```text
GET /api/model/pngtuber/models
```

## Expected Runtime Behavior

- `idle.gif` displays when the character is idle.
- `talking.gif` displays while assistant/TTS speech events are active.
- `click_image` displays briefly after a direct click on the PNGTuber image, then the previous state image is restored.
- `drag_image` displays while the image is being dragged or pinch-moved, then the previous state image is restored.
- Missing or invalid talking state should fall back to idle.
- `happy.png`, `sad.png`, `angry.png`, and `surprised.png` are optional state images for expression-state checks.
- `gif_presets.cat2_static_assets` and `gif_presets.cat3_static_assets` can be copied into `pngtuber` to test alternate idle/talking/drag GIF sets.
- `gif_presets.action_gif_examples` can be copied into expression image fields to test GIF playback beyond idle and talking.
- The model can be dragged on the homepage and persists `offset_x` / `offset_y`.
- Mouse wheel and touch pinch can update `scale`.
- The lock button can disable drag and scale interaction.
- Floating avatar buttons should stay beside the PNGTuber image after drag or scale changes.

## Character Binding Payload

The uploaded `pngtuber` config can be saved to a character with:

```json
{
  "model_type": "pngtuber",
  "pngtuber": {
    "idle_image": "/user_pngtuber/Sample_PNGTuber_Test_Model/idle.gif",
    "talking_image": "/user_pngtuber/Sample_PNGTuber_Test_Model/talking.gif",
    "click_image": "/user_pngtuber/Sample_PNGTuber_Test_Model/talking.gif",
    "drag_image": "/static/assets/neko-idle/cat-idle-cat-move-1.gif",
    "happy_image": "/user_pngtuber/Sample_PNGTuber_Test_Model/happy.png",
    "sad_image": "/user_pngtuber/Sample_PNGTuber_Test_Model/sad.png",
    "angry_image": "/user_pngtuber/Sample_PNGTuber_Test_Model/angry.png",
    "surprised_image": "/user_pngtuber/Sample_PNGTuber_Test_Model/surprised.png",
    "source_type": "gif",
    "scale": 1,
    "offset_x": 0,
    "offset_y": 0,
    "mirror": false
  }
}
```

## Verification Commands

```powershell
uv run pytest tests\unit\test_pngtuber_router.py tests\unit\test_pngtuber_static_contracts.py tests\unit\test_characters_router_model_settings.py
```
