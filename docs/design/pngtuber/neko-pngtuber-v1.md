# NEKO PNGTuber v1

`NEKO PNGTuber v1` is the native package format for lightweight PNG/WebP based
avatars in N.E.K.O. It is designed as the common output target for manual
imports, AutoRig output, and future model tools.

## Package Layout

```text
model.json
metadata.neko-pngtuber.v1.json
preview.png
assets/
  idle.png
  talking.png
  layers/
    00_body.png
    01_eye_open.png
    02_eye_closed.png
    03_mouth_closed.png
    04_mouth_open.png
```

Required files:

- `model.json`: package entry manifest.
- `metadata.neko-pngtuber.v1.json`: layered runtime metadata.
- At least one `pngtuber.idle_image` asset.
- At least one layer image referenced by metadata.

Recommended files:

- `assets/talking.png`: simple fallback when layered mode fails.
- `preview.png`: Model Manager preview.
- `assets/layers/`: transparent PNG/WebP layer assets.

## model.json

Required top-level fields:

- `format`: must be `neko.pngtuber.package.v1`.
- `model_type`: must be `pngtuber`.
- `name`: display name.
- `version`: package revision integer.
- `pngtuber`: runtime entry object.

Required `pngtuber` fields:

- `adapter`: must be `neko_pngtuber_v1`.
- `idle_image`: package-relative PNG/WebP/GIF/JPEG path.
- `metadata`: must point to `metadata.neko-pngtuber.v1.json`.

Recommended `pngtuber` fields:

- `talking_image`: package-relative fallback image.
- `layered_metadata`: same value as `metadata` for older callers.
- `scale`, `offset_x`, `offset_y`, `mirror`: initial placement.

## metadata.neko-pngtuber.v1.json

Required fields:

- `format`: must be `neko.pngtuber.v1`.
- `runtime`: `neko_layered_canvas` is preferred. `layered_canvas` is accepted
  for compatibility.
- `canvas.width` and `canvas.height`: positive integers.
- `layers`: ordered layer list.

Required layer fields:

- `id` or `name`: stable layer identifier.
- `image`: package-relative image path.

Recommended layer fields:

- `order`: render order.
- `role`: one of `body`, `head`, `hair`, `eye`, `mouth`, `accessory`, or a
  tool-specific role.
- `state`: transform and visibility defaults.
- `showTalk`: `1` means idle-only, `2` means talking-only.
- `showBlink`: `1` means open-eye frame, `2` means blink frame.
- `states`: optional state-index overrides.

## Path Rules

All v1 package paths must be package-relative. Absolute paths, protocol-relative
URLs, empty path segments, `.` segments, and `..` segments are invalid. Runtime
normalization may later convert package-relative paths to `/user_pngtuber/...`
after installation.

## Compatibility Contract

The v1 loader must be able to fall back to image mode using `idle_image` and
`talking_image` when layered metadata or layer assets fail. Model Manager
diagnostics should report the failed metadata URL, missing layer images, and the
runtime fallback reason.

## Related Files

- `main_routers/pngtuber_protocol.py`: backend constants, path normalization, and v1 validation.
- `static/neko-pngtuber-protocol.js`: browser-side load plan helpers.
- `tests/unit/test_pngtuber_protocol_v1.py`: generated minimal package coverage without checked-in binary fixtures.
