# Local expression profile contract

These extensions are consumed by N.E.K.O's local manual-expression path. They are
not standard Cubism fields or a claim that arbitrary Live2D models support YUI
parameters. Ordinary profiles without the fields keep the normal SDK path.

| Field | Meaning |
| --- | --- |
| `NekoApplyAfterPhysics: true` | Select the manual-expression path even when native expression playback exists. Authored parameters are re-applied after motion/physics processing. |
| `FadeInTime` | Seconds, used when the caller does not supply `fadeInDuration`. The manual path bounds a valid duration to 50–5000 ms; missing, non-positive or invalid durations use 300 ms. |
| `NekoBlinkScale` | Finite multiplier bounded to 0.8–1.5, blended with the fade weight after the runtime blink writer. Only applies while automatic blinking is enabled and neither a motion nor another eye owner has suspended it. Authored eye values are preserved during takeover; scaling resumes when natural blinking resumes. A fully closed eye remains zero. |
| `NekoMouthRest` | Finite resting opening bounded to 0–0.25. Only applied when lipsync and speech amplitude are inactive. |
| `NekoPerformance` | Optional posture/hand motion and face accent, with the same resource lifetime as the manual expression. |

Example of the optional performance object:

```json
{
  "cue": "tears",
  "strength": 0.55,
  "motion": [
    { "Id": "ParamAngleZ", "Value": -8, "Amplitude": 0.4, "Period": 5, "Phase": 0 }
  ]
}
```

- `cue`: `tears` or `anger`; missing/unknown values draw no accent.
- `strength`: finite opacity multiplier, bounded to 0.25–1; defaults to 1.
- `motion`: inspect at most the first 12 entries. Each requires finite `Value`,
  `Amplitude` and `Period`, an allowed ID and a real parameter in the loaded Core.
  Amplitude is bounded to ±3 and period to 1.5–12 seconds. Phase is in radians and
  defaults to zero. The resulting value is bounded to the model's own min/max.
- Approved channels are `ParamAngleX/Y/Z`, `ParamBodyAngleX/Y/Z`, and the audited
  YUI hand adjustments `Param90`–`Param96`. Eye/lip channels are deliberately
  excluded. The static hand switches remain in the expression's `Parameters`.
- Motion is a sinusoidal target around `Value`, blended against the current
  parameter using the expression fade weight. It does not start an SDK motion.
- `apply(core, weight, elapsedMs)` is called by the active expression's final
  runtime hook. `dispose()` removes the cue and stops future writes. Replacement,
  clearing and model teardown retain the existing expression ownership checks.
  Creation/application failures disable the optional performance without stopping
  blink, lipsync, final pose overrides or observations.
- SVG accents update at most every 32 ms; head geometry is cached for 300 ms
  and refreshed early on model position/scale/rotation or renderer size changes.
  Observations reuse the drawn SVG and never traverse model drawables. Accents
  live inside the canvas container, inheriting its minimize, hover-fade and game
  visibility even when rendering stops. They have no pointer hit area.
  Reduced-motion preference is sampled when creating the resource and freezes
  motion and accents while preserving the pose.

Changing supported IDs requires checking the actual MOC/CDI and motions, plus
final-render tests and desktop-size screenshots. Keep profile values and model
specific capabilities here; do not add per-emotion branches to the Live2D engine.
