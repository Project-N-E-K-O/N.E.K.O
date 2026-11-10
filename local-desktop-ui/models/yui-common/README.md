# Shared YUI outfit expressions

One authored set of 11 profiles covers five chat emotions and six listening
states (default, joyful, tender, calm, sad and intense). The files were first
authored for Sister; they now live here so the three audited outfits cannot drift
through separate copies. Runtime installs still use `expressions/*.exp3.json`.

| Outfit | Audited Core parameters | Original motions | Native gesture support |
| --- | --- | --- | --- |
| yui-origin | 261 | 40 | Heart, mouth/cheek cover, left/right raised open palms |
| yui-lolita | 137 | 44 | Same gesture channels and ranges |
| yui-sister | 138 | 32 | Same gesture channels and ranges |

On 2026-11-10 the three real MOCs and original motion lists were inspected.
All authored IDs exist in all three rigs. Hand switches `Param74`–`Param77`
range from 0 to 1; adjustments `Param90`–`Param96` range from −1 to 1. All three
support the native angry pout and stars. None provides a native fist or full
face-cover switch. Actual desktop-size previews confirmed that Origin and Lolita
can share the authored poses without changing their amplitudes. No fabricated
per-outfit values are needed merely to make their configuration different.

The motion list remains intact. Facial expressions use the manual expression
slot, so sad stays awake, natural blink still closes, and lipsync owns the mouth.
Native hand meshes distinguish the silhouettes; small SVG tears/anger marks are
the same optional accents used by Sister. See [PROFILE_FORMAT.md](PROFILE_FORMAT.md)
and the [Sister rig audit](../yui-sister/README.md).

`../yui-outfits.json` pins the audited MOC hashes. `install_yui_profile.py` refuses
unknown or changed rigs and custom conflicting emotion mappings before writing.
Only expression files and model expression references are changed; textures,
motions, physics, saved position, character settings and credentials are untouched.
The complete backup receipt is created before the first write, and model references
are published after the expressions. Repeated installation performs no writes.

The installer is opt-in and accepts explicit model and backup paths:

```sh
uv run --no-sync python local-desktop-ui/install_yui_profile.py --model path/to/yui-origin.model3.json --emotions
uv run --no-sync python local-desktop-ui/install_yui_profile.py --model path/to/yui-origin.model3.json --emotions --apply --backups path/to/backups
```

The first command only compares. After installation, reload the model. Repeat for
Lolita or an existing, licensed Sister installation. This patch supplies original
expression JSON and a rig hash registry; it does not redistribute Sister binaries
or modify textures, physics or motion archives. Build pipelines may call the same
installer after extracting a verified model archive. The local release wrapper
uses this path for its fresh runtime, so local and public profiles stay identical.

To restore, use the saved model JSON and expression files identified in the
receipt; remove newly added files whose `existed` flag is false. Preserve later
user edits before restoring. Automatic rollback is not performed after a failed
write; the complete receipt exists before the first mutation.

Tests use the tracked Origin/Lolita MOCs with actual Cubism final updates for all
11 profiles, including hand switches, awake eyes and natural full blink. Installer
tests cover both archives, repeat installation, conflicting mappings, changed
MOC rejection and rollback receipt availability on write failure. Screenshots and
manual acceptance records remain outside Git in migration records.
