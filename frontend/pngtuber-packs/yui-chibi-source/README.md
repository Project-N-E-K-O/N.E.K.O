# YUI Chibi optional resources

YUI Chibi is downloaded separately and is not part of the built-in manifest or
of the default desktop distribution. The catalog in `static/pngtuber-catalog.json`
pins its release version, archive sizes and SHA256 checksums.

The model manager offers Download / Cancel / Retry. Installation verifies every
part before transactional extraction into the user's PNGTuber directory. It does
not change the active model; select the installed model explicitly. Existing
installations remain usable if a download or validation fails. Each resource
checksum gets a separate installation folder, so a newly downloaded version never
replaces files still being read by the active renderer; older versions can be
removed explicitly after switching. Downloaded ZIPs
are removed after installation. The resource then works offline.

The test assets are hosted in UnityMer/N.E.K.O, tag `pngtuber-yui-chibi-v1`.
Before upstream merge, maintainers should publish the same immutable files to a
project-controlled release and update only the catalog release URL.

The seven Q90 parts, all PNG mouth patches, frame dimensions and timing are
unchanged from the reviewed package. Authoring JSON copies are kept with the
external resource work files rather than in the application repository.

To create a future release, run `scripts/pack_yui_chibi.py --source-dir <source>
--packs-dir <external-staging-dir> --compatibility-dir <installed-resource>`.
The staging directory may initially be empty. Never use the built-in pack folder
for an optional release. Preserve the original PNG masters separately.

For resource validation set `NEKO_YUI_RESOURCE_DIR` to the installed directory
before running `node --test tests/frontend/pngtuber_tracked_animation.test.cjs`.
Code-only CI does not download the optional model; its model-specific production
metadata check is skipped when that directory is not provided. Synthetic runtime
regressions always run.

Only one idle variant retains its full decoded loop. Other idle variants retain
six entry frames. Switching idle variants demotes the old cache and rejects late
decodes that would refill it. One-shot clips keep the rolling frame cache. No
images are resized or re-encoded, and no mouth coordinates or timing are changed.
Queued entries are decoded before the current one-shot finishes, while normal
request guards retain control over playback. Oversized idle variants retain the
bounded rolling-cache fallback rather than exceeding the decoded-memory budget.
The decoded body-frame estimate falls from 390.2 MiB to 208.3–213.6 MiB in steady
idle; process RSS and GPU memory include additional allocations.

Stable closed-mouth idle samples skip redundant canvas writes only when their
frame, cached image, canvas size, scaling and padding match. Playback deadlines
still advance. Speech, crossfade, transitions and snapshot rendering bypass this
optimization.

A fresh build contains only the three pre-existing built-in PNGTubers. Existing
local `static/pngtuber/yui-chibi` development resources are deliberately not deleted
by this change. Do not package them accidentally: the desktop verifier rejects
a distribution containing that optional folder. Prepare releases from a clean
checkout or move those local resources outside the source tree before packaging.

Removing ZIPs from the working tree does not remove earlier Git history. No
history rewrite is part of this change; coordinate that separately before merge.
