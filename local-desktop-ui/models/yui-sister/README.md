# YUI gestures and expression accents

Field definitions and ownership rules: [PROFILE_FORMAT.md](../yui-common/PROFILE_FORMAT.md).

Audited against the installed yui-sister MOC/CDI and all 32 bundled motion files on 2026-11-09. Preserve the user's model binaries; these are opt-in expression profiles.

| Control | Actual supported behavior | Range |
|---|---|---|
| Param74 | Both hands form a heart, with native heart decorations | 0–1 switch |
| Param76 | Left hand near mouth/cheek | 0–1 switch |
| Param75 / Param77 | Right / left open palm raised at shoulder level | 0–1 switches |
| Param94 | Fine adjustment of the hand near the mouth, a few pixels at pet size | −1–1 |
| Param90 / Param91 | Forearm adjustment | −1–1 |
| Param92 / Param93 | Hand adjustment | −1–1 |
| Param95 / Param96 | Wrist/wave adjustment | −1–1 |
| Param68 | Native star eyes and surrounding stars | 0–1 |
| Param78 | Native angry pout | 0–1 |
| Param86 | Half-open eyes; deliberately disabled for these sad/angry profiles | 0–1 |

There is no finger-closing/fist switch, full face-cover pose, tear rig or anger-vein rig in this model. Do not describe the raised open hand as a fist. `sad3` contains the mouth-cover switch; `angry1/2/4` also use it. `surprise2/4` raise both hands. Most other emotion motions are head/body movements; replaying them by label would not guarantee a distinct silhouette.

Chat profiles now use distinct silhouettes: neutral hands down, happy native heart, sad hand near cheek/mouth, angry one palm raised and wrist inward, surprised both hands raised. Listening remains softer: tender heart without stars, sad mouth-cover with lower-opacity tears. Hands are model meshes, not painted replacements.

`NekoPerformance` is handled by `static/avatar/avatar-expression-performance.js` through the existing expression resource lifetime. Its bounded motion list adds slow changes only to approved posture/hand channels at the final runtime stage. It never owns eye opening or speech amplitude. `cue: tears` draws moving droplets from face geometry; `cue: anger` adds forehead veins and a light cheek tint. These small SVG accents follow face position/scale and do not intercept pointer input. Removing/replacing an expression or model removes its accent immediately. Reduced-motion preference retains the pose and static accents.

Use `install_yui_profile.py --emotions --apply --backups <path>` to install, with a backup of every overwritten file. Do not patch a runtime or replace the entire model folder. Tests use the real bundled Cubism SDK to inspect final draw parameters plus cue ownership/cleanup; actual YUI screenshots belong in external acceptance records.
