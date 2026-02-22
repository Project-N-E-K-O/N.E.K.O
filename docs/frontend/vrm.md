# VRM Models

## Overview

N.E.K.O. supports VRM (Virtual Reality Model) format for 3D character rendering using Three.js and `@pixiv/three-vrm`.

## Model management

- Upload VRM files via `/api/model/vrm/upload` (max 200MB)
- Upload animations separately via `/api/model/vrm/animation/upload`
- Configure emotion mappings via `/vrm_emotion_manager`

## Lighting configuration

VRM models use a configurable lighting system:

| Light | Default | Range | Description |
|-------|---------|-------|-------------|
| Ambient | 0.4 | 0 - 1.0 | HemisphereLight intensity |
| Main | 1.2 | 0 - 2.5 | Primary directional light |
| Fill | 0.5 | 0 - 1.0 | Secondary fill light |
| Rim | 0.8 | 0 - 1.5 | Edge/rim lighting |
| Top | 0.3 | 0 - 1.0 | Top-down light |
| Bottom | 0.15 | 0 - 0.5 | Bottom-up light |

Configure via `PUT /api/characters/catgirl/{name}/lighting`.

## UI components

| Module | Purpose |
|--------|---------|
| `vrm-ui-buttons.js` | VRM-specific control buttons |
| `vrm-ui-popup.js` | VRM popup dialogs |

## Known issues & fixes

### SpringBone physics explosion

VRM `update(delta)` expects delta in **seconds**. Passing milliseconds or unclamped values causes hair to fly upward:

```javascript
let delta = clock.getDelta();
delta = Math.min(delta, 0.05); // Prevent physics explosion on tab switch
vrm.update(delta);
```

### Oversized colliders (affects nearly all VRM models)

VRM models exported from VRoid Studio have a known UniVRM bug ([#673](https://github.com/vrm-c/UniVRM/issues/673)) where collider radii are ~2x too large. This makes hair appear stuck horizontally. **Fix**: reduce all collider radii by 50% after loading:

```javascript
springBoneManager.colliders.forEach(collider => {
    if (collider.shape?.radius > 0) {
        collider._originalRadius = collider.shape.radius;
        collider.shape.radius *= 0.5;
    }
});
```

### MToon outline thickness

When models are scaled, MToon outlines become disproportionately thick. Switch to screen-space mode:

```javascript
material.outlineWidthMode = 'screenCoordinates';
material.outlineWidthFactor = 0.005; // 1-2 pixel thin outline
material.needsUpdate = true;
```

| Factor | Effect |
|--------|--------|
| 0.002 - 0.003 | Very thin (~1px) |
| 0.005 | Thin (1-2px) |
| 0.01 | Medium (2-3px) |
| 0.02+ | Thick |

### Camera drag inconsistency

Never use a fixed `panSpeed` for drag. Compute pixel-to-world mapping dynamically:

```javascript
const worldHeight = 2 * Math.tan(fov / 2) * cameraDistance;
const pixelToWorld = worldHeight / screenHeight;
```

See [Developer Notes](/contributing/developer-notes#vrm-model-gotchas) for the full reference.

## API endpoints

See [VRM API](/api/rest/vrm) for the full REST endpoint reference.
