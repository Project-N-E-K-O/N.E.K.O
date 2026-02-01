---
name: vrm-mtoon-outline
description: Adjusting VRM MToon material outline thickness in three-vrm. Covers outline width modes, property access, and how to fix outlines that appear too thick when models are scaled.
---

# VRM MToon Outline Thickness Adjustment

This skill covers how to adjust outline thickness for VRM models using MToon materials in `@pixiv/three-vrm`.

## Common Symptoms

1. **Outline too thick** when model is scaled up or camera is close
2. **Outline thickness inconsistent** at different zoom levels
3. **Need to adjust outline programmatically** at runtime

---

## Key Concepts

### Outline Width Modes

MToon materials have two main outline width modes:

| Mode | Description | Behavior |
|------|-------------|----------|
| `'worldCoordinates'` | Outline width is a physical world-space size | Outline appears **thicker when model is scaled up** or camera is closer |
| `'screenCoordinates'` | Outline width is relative to screen pixels | Outline **stays consistent size** regardless of zoom/scale |

### Critical Properties

```javascript
material.outlineWidthMode    // 'none' | 'worldCoordinates' | 'screenCoordinates'
material.outlineWidthFactor  // Number - the actual width value
material.isMToonMaterial     // Boolean - true for MToon materials
material.needsUpdate         // Set to true after modifying properties
```

---

## Solution: Switch to Screen Coordinates

To make outlines stay consistent regardless of zoom/scale:

```javascript
function adjustOutlineThickness(vrm, screenFactor = 0.005) {
    vrm.scene.traverse((object) => {
        if (object.isMesh || object.isSkinnedMesh) {
            const materials = Array.isArray(object.material) ? object.material : [object.material];
            
            materials.forEach(material => {
                if (!material || !material.isMToonMaterial) return;
                
                // Check if this material has outlines enabled
                const hasOutline = material.outlineWidthFactor > 0 && 
                                   material.outlineWidthMode !== 'none';
                
                if (hasOutline) {
                    // Switch to screen coordinates mode
                    material.outlineWidthMode = 'screenCoordinates';
                    material.outlineWidthFactor = screenFactor;
                    material.needsUpdate = true;
                }
            });
        }
    });
}
```

### Factor Value Guidelines

For `screenCoordinates` mode:

| Factor Value | Approximate Effect |
|-------------|-------------------|
| `0.002 - 0.003` | Very thin outline (1 pixel) |
| `0.005` | Thin outline (1-2 pixels) |
| `0.01` | Medium outline (2-3 pixels) |
| `0.02+` | Thick outline |

---

## Detection Pattern

To diagnose outline issues, log all materials:

```javascript
function debugOutlineMaterials(vrm) {
    let count = 0;
    vrm.scene.traverse((object) => {
        if (!object.isMesh && !object.isSkinnedMesh) return;
        
        const materials = Array.isArray(object.material) ? object.material : [object.material];
        materials.forEach(material => {
            if (material?.isMToonMaterial || 'outlineWidthFactor' in material) {
                count++;
                console.log({
                    name: material.name || '未命名',
                    type: material.type,
                    isMToonMaterial: material.isMToonMaterial,
                    outlineWidthMode: material.outlineWidthMode,
                    outlineWidthFactor: material.outlineWidthFactor
                });
            }
        });
    });
    console.log(`Found ${count} MToon/Outline materials`);
}
```

---

## Important Notes

> [!IMPORTANT]
> **Call timing matters!** The function must be called AFTER `currentModel` is set. In `vrm-core.js`, the `loadModel()` function sets `manager.currentModel` near line 923. Any function that accesses `currentModel` must be called after this point.

> [!NOTE]
> Materials named `"XXX (Outline)"` are outline pass materials automatically created by three-vrm. They share properties with the main material but render the outline effect.

---

## API Reference (three-vrm MToon)

The enum values for `outlineWidthMode`:

```javascript
// From three-vrm.module.min.js
{
    None: "none",
    WorldCoordinates: "worldCoordinates", 
    ScreenCoordinates: "screenCoordinates"
}
```

| Property | Type | Description |
|----------|------|-------------|
| `outlineWidthMode` | string | Width calculation mode |
| `outlineWidthFactor` | number | Width value (meaning depends on mode) |
| `outlineColorFactor` | Color | Outline color |
| `outlineLightingMixFactor` | number | How much lighting affects outline |
| `outlineWidthMultiplyTexture` | Texture | Texture to modulate outline width |
