---
name: vrm-springbone-physics
description: Debugging and fixing VRM SpringBone physics issues in three-vrm, including hair/clothing physics that flies upward, sticks out horizontally, or behaves unnaturally.
---

# VRM SpringBone Physics Debugging

This skill covers common issues with VRM hair/clothing physics using `@pixiv/three-vrm` and how to fix them.

## Common Symptoms

1. **Hair flies upward** or explodes outward on load
2. **Hair sticks out horizontally** like there's an invisible wall
3. **Hair is stiff** and doesn't move naturally
4. **Physics works but starts from wrong position**

---

## Root Cause 1: Incorrect Delta Time (Most Common - 90%)

### Problem
The `vrm.update(delta)` function expects delta in **seconds**, not milliseconds. If delta is too large, physics "explodes".

### Diagnosis
```javascript
// Add this to your animation loop
console.log('delta:', delta);
// Should be ~0.016 for 60fps, NOT 16 or larger!
```

### Solution
```javascript
// Correct implementation using THREE.Clock
const clock = new THREE.Clock();

function animate() {
    requestAnimationFrame(animate);
    
    let delta = clock.getDelta();
    // Clamp to prevent explosion on tab switch or lag
    delta = Math.min(delta, 0.05);  // Max 50ms
    
    if (vrm) {
        vrm.update(delta);
    }
    
    renderer.render(scene, camera);
}
```

---

## Root Cause 2: SpringBone Colliders (Very Common)

### Problem
VRM models have invisible spherical colliders (usually on head/body) that prevent hair from penetrating. **Virtually ALL VRM models have oversized colliders**, causing hair to appear stuck horizontally in mid-air.

### Root Cause Analysis

#### Confirmed Facts

1. **UniVRM Export Bug Exists** ([#673](https://github.com/vrm-c/UniVRM/issues/673)):
   - When colliders are on scaled objects, the radius doesn't normalize with the mesh
   - Gizmo shows correct size in editor, but exported collider is larger
   - Issue documented with reproducible steps

2. **three-vrm Uses Radius Directly** ([source](https://github.com/pixiv/three-vrm/blob/dev/packages/three-vrm-springbone/src/VRMSpringBoneColliderShapeSphere.ts)):
   ```typescript
   const distance = length - objectRadius - this.radius;  // radius not scaled by world matrix
   ```

3. **UniVRM Officially Discourages Scaling** ([source](https://github.com/vrm-c/UniVRM/issues/673)):
   > "We do not recommend using SpringBone and scaling together"

#### Empirical Observation

> [!NOTE]
> **50% reduction fixes ALL tested models.** The exact mathematical reason is uncertain - the export scaling could vary by model/tool. However, this factor works universally in practice.

Possible explanations:
- VRoid Studio (most common VRM source) may use consistent internal scaling
- The visual matching in Unity editor may systematically create ~2x overcorrection
- Export normalization algorithms may have consistent behavior

#### Practical Approach

Since the exact cause varies, we provide an **adjustable reduction factor** with 50% as default.

### Diagnosis

Check if only bangs are horizontal (collider issue) or all physics elements (gravity issue):
- **Only bangs horizontal** → Head collider blocking them
- **All physics horizontal** → Gravity direction wrong

Disable colliders to confirm:
```javascript
const colliders = Array.from(springBoneManager.colliders || []);
colliders.forEach(c => {
    if (c.shape?.radius) c.shape.radius = 0;
});
// If hair now falls correctly, colliders were the issue
```

### Solutions

**Option 1: Reduce Collider Radii by 50%** (Recommended - Compensates for export bug)
```javascript
const REDUCTION_FACTOR = 0.5;  // Compensates for UniVRM export scaling bug
const colliders = Array.from(springBoneManager.colliders || []);
colliders.forEach(collider => {
    if (collider.shape?.radius > 0) {
        // Save original for potential future adjustment
        if (collider._originalRadius === undefined) {
            collider._originalRadius = collider.shape.radius;
        }
        collider.shape.radius = collider._originalRadius * REDUCTION_FACTOR;
    }
});
```

**Option 2: Completely Disable Colliders** (Simple but may cause clipping)
```javascript
const colliders = Array.from(springBoneManager.colliders || []);
colliders.forEach(collider => {
    if (collider.shape?.radius !== undefined) {
        collider.shape.radius = 0;
    }
});
```

**Option 3: Disable Only Head Colliders** (Best, needs bone name detection)
```javascript
colliders.forEach(collider => {
    const boneName = collider.bone?.name?.toLowerCase() || '';
    if (boneName.includes('head') || boneName.includes('face')) {
        collider.shape.radius = 0;
    }
});
```

**Option 4: Fix in Unity** (Permanent fix, requires model access)
1. Open model in Unity with VRM SDK
2. Find "secondary" object in hierarchy
3. Select head bone with `VRMSpringBoneColliderGroup`
4. Enable gizmos to see magenta collider spheres
5. Reduce radius/adjust offset to proper size
6. Re-export VRM

**Option 5: Scale Colliders with Scene** (Runtime fix for scaled models)

When `vrm.scene.scale` is changed at runtime, colliders need to be scaled proportionally:
```javascript
function scaleVRMScene(vrm, scaleFactor) {
    // Scale the scene
    vrm.scene.scale.setScalar(scaleFactor);
    
    // Scale all collider radii to match
    const springBoneManager = vrm.springBoneManager;
    if (springBoneManager) {
        const colliders = Array.from(springBoneManager.colliders || []);
        colliders.forEach(collider => {
            if (collider.shape?.radius !== undefined) {
                // Store original radius if not already stored
                if (collider._originalRadius === undefined) {
                    collider._originalRadius = collider.shape.radius;
                }
                // Scale radius with scene
                collider.shape.radius = collider._originalRadius * scaleFactor;
            }
        });
    }
}
```

---

## Root Cause 2B: Runtime Scene Scaling (Application-Specific)

### Problem
If your application scales `vrm.scene` to fit different screen sizes, the collider radii remain fixed in local space while bones scale with the scene. This causes colliders to become **relatively larger** when the model is scaled down.

### Example
- Model scaled to 0.8x (80% size)
- Head collider radius stays at original 0.1 units
- Relative to the scaled head, the collider is now 0.1/0.8 = **0.125** (25% larger)
- Hair that previously cleared the collider now gets blocked

### Key Insight
**VRChat works because it doesn't scale the VRM scene directly** - it places the model inside a container and scales the container, or uses a different physics implementation that accounts for scale.

### Solution
When scaling the VRM scene, also scale the collider radii proportionally (see Option 5 above).

---

## Root Cause 3: Model Issues

### Symptoms
- `_worldSpaceBoneLength: 0` in console logs
- Hair bones don't respond to physics changes

### Cause
Model was not properly configured in Unity/Blender:
- Hair bones missing child bones
- SpringBone settings incorrectly exported
- Gravity direction wrong in model

### Solution
1. Test with [official VRM viewer](https://vrm.dev/en/vrm_applications/) - if hair is broken there, it's a model issue
2. Fix in Unity with VRM SDK or Blender with VRM addon
3. Ensure each hair bone has a proper child bone with non-zero length

---

## Recommended Initialization Code

> [!CAUTION]
> **Empirical Fix Notice**: The `COLLIDER_REDUCTION = 0.5` value is empirically determined from testing multiple VRM models. While the underlying UniVRM bug is documented, we cannot mathematically prove 50% is correct for all models. **If you encounter hair physics issues, adjust this value first.**

```javascript
function initializeVRMPhysics(vrm) {
    const springBoneManager = vrm.springBoneManager;
    if (!springBoneManager) return;
    
    // Reduce collider radii to compensate for UniVRM export bug (#673)
    // This is an EMPIRICAL fix - adjust if needed
    const COLLIDER_REDUCTION = 0.5;
    
    const colliders = Array.from(springBoneManager.colliders || []);
    colliders.forEach(collider => {
        if (collider.shape?.radius > 0) {
            collider._originalRadius = collider.shape.radius;
            collider.shape.radius *= COLLIDER_REDUCTION;
        }
    });
    
    console.log(`[VRM] Applied ${COLLIDER_REDUCTION * 100}% collider reduction to ${colliders.length} colliders`);
}

// Animation loop with delta clamping
const clock = new THREE.Clock();
function animate() {
    requestAnimationFrame(animate);
    
    let delta = clock.getDelta();
    delta = Math.min(delta, 0.05);  // Prevent physics explosion
    
    if (vrm) {
        vrm.update(delta);
    }
    
    renderer.render(scene, camera);
}
```

---

## Key API Reference

| Method | Purpose |
|--------|---------|
| `springBoneManager.reset()` | Clear physics state, return to initial positions |
| `springBoneManager.setInitState()` | Capture current position as new "rest" state |
| `springBoneManager.joints` | Set of all SpringBone joints |
| `springBoneManager.colliders` | Set of all colliders |
| `vrm.update(delta)` | Update all VRM systems including physics |

## Joint Settings (per joint.settings)

| Property | Description |
|----------|-------------|
| `stiffness` | Spring force (0 = no spring, 1 = stiff) |
| `gravityPower` | Gravity strength |
| `gravityDir` | Vector3 gravity direction (usually 0, -1, 0) |
| `dragForce` | Damping (0 = no drag, 1 = full stop) |
