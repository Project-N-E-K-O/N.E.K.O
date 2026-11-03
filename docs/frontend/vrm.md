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

Output is disabled by default. The disabled path uses only a lightweight facade and performs no status polling, VMC timers, or frame sampling. From the main page, enable the default local destination with:

```js
await window.vrmVmcSender.enable('127.0.0.1', 39539, 60)
```

The VMC root is independent of `vrm.scene`, so desktop layout transforms are never exported. Model switching and disposal retire old expressions with acknowledged zero-value frames. Disabling or releasing the source ends with `/VMC/Ext/OK 0`.

See [VMC output](/api/rest/vmc) for receiver setup, controls, security boundaries, close codes, and troubleshooting.

## Runtime safeguards

The current renderer clamps frame delta after long stalls, reduces imported spring-bone collider radii, and scales MToon outline width through the lighting configuration. These are internal compatibility safeguards, not model-format requirements; do not pre-edit uploaded VRM files to reproduce them.

By default every collider radius is reduced to 50% (`VrmPhysicsProfile.DEFAULT_REDUCTION`). The factor is empirical: it offsets UniVRM exports whose collider radii were not normalized with the object scale. When a model needs a different value, override it with a per-model physics profile (below) instead of changing the global factor.

## Per-model physics profile

While a VRM loads, `vrm-physics-profile.js` requests `GET /api/model/vrm/physics_profile/{model_name}`, which reads `static/vrm/configs/{model_name}_physics.json`. A usable profile sets collider radii and spring-joint settings; a missing file, a corrupt file, a failed request, or a request slower than 4 seconds falls back to the 50% reduction above. The repository ships no `_physics.json`, so every model uses the 50% reduction by default.

`model_name` is the file name of the model URL without `.vrm` (URL-decoded first; kept as-is when decoding fails), the same name the emotion mapping uses. The request uses `cache: 'no-store'`, so reloading the model picks up an edited profile. The console prints `[VRM] Physics profile: source=file|default, colliders=N, joints=N, reset=true|false` to show how many entries actually matched.

Profiles can be exported by the external Unity tool [vrm-physics-tuner](https://github.com/ylh440104/vrm-physics-tuner). This repository does not depend on it, and profiles can also be written by hand.

### File format

The top level must be a JSON object, and at least one of `colliders` and `chains` must be an array; otherwise the file counts as no profile. The file is UTF-8, and a BOM (written by C# by default) is accepted. The endpoint replaces `NaN` / `Infinity` with `null`, and those fields are skipped.

```json
{
  "colliders": [
    { "bone": "J_Bip_C_Head", "shape": "sphere", "offset": [0, 0.05, 0], "radius": 0.06 },
    { "bone": "J_Bip_C_Chest", "shape": "capsule", "offset": [0, 0, 0], "tail": [0, 0.1, 0], "radius": 0.08 }
  ],
  "chains": [
    {
      "bones": ["J_Sec_Hair1_01", "J_Sec_Hair2_01"],
      "stiffness": 1.0,
      "drag_force": 0.4,
      "gravity_power": 0.0,
      "gravity_dir": [0, -1, 0],
      "hit_radius": 0.02
    }
  ]
}
```

| Field | Meaning |
| --- | --- |
| `colliders[].bone` | Original glTF node name of the bone that owns the collider |
| `colliders[].shape` | `sphere` or `capsule`, matching three-vrm's collider `shape.type`; when omitted, shape is not checked |
| `colliders[].offset` | Collider offset in the bone's local space. Omitted or `[0,0,0]` means "no position information" (the tuner always writes `[0,0,0]`) |
| `colliders[].tail` | Capsule tail, used only to prefer one of several capsules on the same bone; the tuner writes Unity coordinates, so a mismatch is not an error |
| `colliders[].radius` | Radius at model scale 1, multiplied by the current scale at runtime; must be a finite non-negative number, otherwise that collider keeps the 50% reduction |
| `chains[].bones` | Original names of the bones these settings apply to |
| `chains[].stiffness`, `gravity_power` | Non-negative numbers |
| `chains[].drag_force` | Range [0, 1] (the solver damps the previous displacement by `1 - dragForce`) |
| `chains[].hit_radius` | Non-negative, value at scale 1, scales with the model |
| `chains[].gravity_dir` | Optional three-element array |

An out-of-range joint setting skips only that field; the other settings in the same chain are still applied.

### Matching rules

- **Bone names**: match the original glTF name that GLTFLoader keeps in `userData.name` first; use the sanitized name only when the node has no recorded original name (GLTFLoader replaces whitespace with underscores and drops `[ ] . : /`). When a node has an original name that the profile does not contain, there is no fallback to the sanitized name, so bones such as `hair.001` and `hair001` that sanitize to the same string never borrow each other's settings.
- **Colliders**: only the radius of colliders the model already has is changed; colliders are never added or removed, and shape and position are left alone. Matching tries bone + shape + offset + tail, then bone + shape + offset, then pairs same-bone, same-shape entries in order when the profile has no position information. A different shape, or a known offset that does not match, stays unmatched and keeps the 50% reduction.
- **Joints**: chains are found by bone. When a bone appears in several chains, they are assigned in order; runtime joints beyond the profile's chains keep the model's own settings.
- After applying, `springBoneManager.reset()` restarts the springs from the new settings. When the model is scaled, collider radii scale as before; a joint's `hitRadius` scales only if the profile set it, so models without a profile behave as before.

### Known limitations

- **Lookup by file name.** Profiles are looked up by model file name, like the emotion mapping. Different Workshop items that each contain a `model.vrm` share one profile.
- **Matching by bone name, not bone path.** When a bone appears in several chains with different settings, they are assigned in order of appearance.
- **With the tuner's current export, colliders rarely match.** For the bundled `sister1.0.vrm`, the tuner exports 6 capsule colliders while the model has 28 sphere colliders at runtime: colliders match 0/28, joints match 71/71, so only the spring-joint settings take effect. For colliders to take effect, the tuner needs to export a radius for each existing model collider.

### Before making this user-facing

The mechanism currently serves developers and tuners only: there is no import UI, and no profile is bundled or distributed through the Workshop. A user-facing version needs at least these three points resolved first:

1. **Storage location.** Profiles are read from `project_root/static/vrm/configs`. In packaged builds `project_root` is the install directory, not the user data directory, so files users drop there are not a durable place to keep them. Move to a user-writable directory and keep reading the old location. The emotion mapping lives in the same directory and should move with it.
2. **Lookup name.** Once profiles are distributed, lookup by file name lets same-named Workshop models share a profile; switch to a name that distinguishes the source. The emotion mapping uses the same rule, so change both together.
3. **Format version.** The file has no version field; the code only checks whether `colliders` / `chains` exist. Before the tuner changes the format (for example exporting a radius per collider, or changing a field's meaning), add a version field and decide how older versions are handled.

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
| `GET` | `/api/model/vrm/physics_profile/{model_name}` | Read a per-model physics profile |
| `DELETE` | `/api/model/vrm/model` | Delete a user model by public URL |

## Host boundary

VRM renders in `index.html`, including the Electron pet window. The standalone chat and subtitle templates intentionally provide no second VRM scene; native windows use the shared cross-window bridge to coordinate with the main page.
