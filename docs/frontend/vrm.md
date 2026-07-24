# VRM models

## Runtime and formats

The VRM renderer uses Three.js and `@pixiv/three-vrm`. Models are `.vrm` files; animations are normally `.vrma` files loaded through `@pixiv/three-vrm-animation`.

The active implementation lives under `static/vrm/`: core, manager, initialization, animation, expression, interaction, cursor-follow, orientation, and UI modules. `vrm-init.js` creates `window.vrmManager` and initializes `#vrm-canvas` only for a VRM character.

## Models and animations

`GET /api/model/vrm/models` combines top-level bundled files in `static/vrm/`, user files exposed through `/user_vrm`, and installed Workshop files under `/workshop/{item_id}/...`. The API returns public URLs and never exposes absolute filesystem paths.

Animations are listed from `static/vrm/animation/` and `/user_vrm/animation/`. Uploads accept `.vrm` for models and `.vrma` for animations, with a 200 MB limit per file. User model deletion is restricted to top-level `.vrm` files inside the configured VRM directory.

## Lighting

Backend defaults from `config/character_defaults.py` are injected into templates before renderer scripts as `window.VRM_DEFAULT_LIGHTING`. The current keys are:

```json
{
  "ambient": 0.83,
  "main": 1.91,
  "fill": 0.0,
  "rim": 0.0,
  "top": 0.0,
  "bottom": 0.0,
  "exposure": 1.1,
  "toneMapping": 7,
  "outlineWidthScale": 1.0
}
```

Character-specific lighting may override these values. Keep backend defaults, template context, and the defensive fallback in `vrm-core.js` aligned.

## Emotion mapping

VRM emotions map a semantic name to ordered candidate expression names:

```json
{
  "neutral": ["neutral"],
  "happy": ["happy", "joy", "fun", "smile"],
  "surprised": ["surprised", "surprise", "shock", "e", "o"]
}
```

The server stores per-model maps under `static/vrm/configs/`. `vrm-expression.js` merges a saved map over the defaults and uses exact, case-insensitive expression-name matching. The management page can obtain actual model expressions from `/api/model/vrm/expressions/{model_name}` before saving.

`window.LanLan1.setEmotion(name)` delegates to `window.vrmManager.expression.setMood(name)` when VRM is active. Non-neutral moods return to neutral after the runtime delay.

## VMC motion output

`vrm-vmc-sender.js` can sample the active VRM after animation, look-at, spring-bone, and expression updates, then publish the resulting humanoid pose through the dedicated `/api/vmc/ws` channel. The backend converts coordinates and sends OSC/UDP to a VMC-compatible receiver.

Output is disabled by default. From the main page, enable the default local destination with:

```js
await window.vrmVmcSender.enable('127.0.0.1', 39539, 60)
```

The VMC root is independent of `vrm.scene`, so desktop layout transforms are never exported. Model switching and disposal retire old expressions with acknowledged zero-value frames. Disabling or releasing the source ends with `/VMC/Ext/OK 0`.

See [VMC output](/api/rest/vmc) for receiver setup, controls, security boundaries, close codes, and troubleshooting.

## Runtime safeguards

The current renderer clamps frame delta after long stalls, reduces imported spring-bone collider radii, and scales MToon outline width through the lighting configuration. These are internal compatibility safeguards, not model-format requirements; do not pre-edit uploaded VRM files to reproduce them.

## API summary

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `POST` | `/api/model/vrm/upload` | Upload one `.vrm` model |
| `POST` | `/api/model/vrm/upload_animation` | Upload one `.vrma` animation |
| `GET` | `/api/model/vrm/models` | List bundled, user, and Workshop models |
| `GET` | `/api/model/vrm/animations` | List bundled and user animations |
| `GET` | `/api/model/vrm/config` | Return public VRM URL prefixes |
| `GET`, `POST` | `/api/model/vrm/emotion_mapping/{model_name}` | Read or save an expression map |
| `GET` | `/api/model/vrm/expressions/{model_name}` | Inspect expression names in a model |
| `DELETE` | `/api/model/vrm/model` | Delete a user model by public URL |

## Host boundary

VRM renders in `index.html`, including the Electron pet window. The standalone chat and subtitle templates intentionally provide no second VRM scene; native windows use the shared cross-window bridge to coordinate with the main page.
