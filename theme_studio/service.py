"""neko_theme_studio -- the N.E.K.O Theme Studio.

Responsibilities (see theme-studio/THEME_CONTRACT.md):

1. Own the "palette" JSON (``static/theme.json``), the only data source read
   by the app-side loader (``resources/bin/static/neko-theme-studio.js``).
2. Provide entries for the static panel (``static/index.html`` +
   ``static/app.js``) to read / modify / reset / export / import the palette,
   plus a theme library (save / switch / delete / share bundles) and chat
   background image uploads.
3. **Server-side validation is the security boundary**: the panel is a
   cross-origin iframe and all input is untrusted. Any non-conforming variable
   name or value must be rejected and never written to disk.

Built-in note (v1.0.6): this module now runs as a **built-in host feature**,
not a plugin -- no plugin host, no port 48916, no patches that inject anything
into the host. The palette lives in the user data directory
(``<app_docs>/theme_studio/theme.json``) and is served by
``main_routers/theme_studio_router.py`` via ``/api/theme_studio/*``; the two
files under ``static/theme-studio/loader/`` are ordinary host static assets,
included directly by templates via <script>.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import os
import re
import threading
import time
import unicodedata
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ._sdk_compat import (
    Err,
    NekoPluginBase,
    Ok,
    SdkError,
    lifecycle,
    neko_plugin,
    plugin_entry,
    tr,
    ui,
)

from . import assets as _assets
from . import materials as _materials
from . import trash as _trash
from .i18n import msg as _msg

# ---------------------------------------------------------------------------- paths
#: Directory containing this file (``theme_studio/`` in the host code tree).
PACKAGE_DIR = Path(__file__).resolve().parent
#: Host root directory.
APP_ROOT = PACKAGE_DIR.parent
#: Read-only resources: frontend static files such as the two loader files, served by the host's /static route.
APP_STATIC_DIR = APP_ROOT / "static" / "theme-studio"
#: Read-only built-in themes (shipped with the host).
PRESETS_DIR = PACKAGE_DIR / "presets"
#: HTTP prefix of the in-host Theme Studio; must match ``main_routers/theme_studio_router.py``.
API_BASE = "/api/theme_studio"
#: Public URL prefix for background images.
BACKGROUNDS_URL_BASE = API_BASE + "/backgrounds"

# --------------------------------------------------------------------------- constants

SCHEMA_VERSION = 1
DEFAULT_THEME_NAME = "默认"

#: Global lock for file writes. The host may call entries concurrently (panel clicks + polling + lifecycle),
#: so the whole "read -> merge -> atomic replace" sequence must be serialized or updates get lost.
# RLock rather than Lock: "hold the lock for a read-modify-write, then call an inner function that also
# takes the lock" on the same thread is an easy nesting to write (save_theme once self-deadlocked this way --
# the plugin process's entire entry queue was blocked by a thread waiting forever, and every entry hit the
# 30s timeout). A reentrant lock makes that pattern safe.
_WRITE_LOCK = threading.RLock()

#: Variable name prefix and shape (contract §3).
RE_VAR_NAME = re.compile(r"^--nts-[a-z0-9-]{1,60}$")

#: Allowlist of known variables -- **well-formed does not mean meaningful**.
#:
#: Without this gate you get "silent no-ops reported as success": when ``apply_theme`` receives a
#: misspelled variable name (e.g. ``--nts-nope-var``), the backend accepts it, writes it to theme.json
#: and echoes it as accepted, but no rule in ``neko-theme-studio.css`` consumes it, so the user sees
#: no change. An independent verifier confirmed this in practice.
#:
#: Source of the list = every ``--nts-*`` appearing in the stylesheet actually produced by
#: ``tools/gen-css.js`` (cross-check with ``build/vars.json``: the 59 consumed by CSS = this set).
#: To add a variable: first add a target in ``test/targets.js`` -> run ``tools/build-theme-css.ps1``
#: -> add the names newly appearing in the generated CSS here and to the panel CATALOG.
KNOWN_VARS = frozenset(
    {
        "--nts-accent",
        "--nts-accent-bg",
        "--nts-ai-bubble-bg",
        "--nts-ai-bubble-border",
        "--nts-ai-bubble-text",
        "--nts-bubble-radius",
        "--nts-bubble-veil",
        "--nts-chat-bg-image-brightness",
        "--nts-chat-top-fade",
        "--nts-button-bg",
        "--nts-button-hover-bg",
        "--nts-button-text",
        "--nts-card-radius",
        "--nts-chat-bg",
        "--nts-chat-bg-image",
        "--nts-chat-blur",
        "--nts-chat-opacity",
        "--nts-chat-panel-bg",
        "--nts-chat-panel-radius",
        "--nts-chip-bg",
        "--nts-chip-text",
        "--nts-code-bg",
        "--nts-code-text",
        "--nts-font-family",
        "--nts-font-size",
        "--nts-highlight",
        "--nts-image-bg",
        "--nts-input-bg",
        "--nts-input-border",
        "--nts-input-placeholder",
        "--nts-input-text",
        "--nts-link",
        "--nts-modal-bg",
        "--nts-modal-radius",
        "--nts-modal-text",
        "--nts-overlay-bg",
        "--nts-panel-bg",
        "--nts-panel-border-color",
        "--nts-panel-border-width",
        "--nts-panel-corner-glow",
        "--nts-panel-edge-gradient",
        "--nts-panel-glass-blur",
        "--nts-panel-glass-tint",
        "--nts-panel-glow-color",
        "--nts-panel-glow-pulse",
        "--nts-panel-glow-size",
        "--nts-panel-glow-speed",
        "--nts-panel-glow-strength",
        "--nts-panel-inner-glow",
        "--nts-panel-sheen",
        "--nts-panel-border",
        "--nts-panel-text",
        "--nts-panel-text-sub",
        "--nts-popover-bg",
        "--nts-popover-border",
        "--nts-popover-radius",
        "--nts-popover-text",
        "--nts-radius",
        "--nts-rec-bg",
        "--nts-rec-border",
        "--nts-rec-radius",
        "--nts-rec-text",
        "--nts-scrollbar-thumb",
        "--nts-scrollbar-track",
        "--nts-send-bg",
        "--nts-send-text",
        "--nts-shadow",
        "--nts-shell-radius",
        "--nts-status-bg",
        "--nts-status-text",
        "--nts-tool-bubble-bg",
        "--nts-tool-bubble-text",
        "--nts-topbar-bg",
        "--nts-topbar-text",
        "--nts-user-bubble-bg",
        "--nts-user-bubble-border",
        "--nts-user-bubble-text",
    }
)

#: Added in v0.1.11: liquid glass + custom gradients (contract _work/FEATURE-CONTRACT-0.1.11.md §1).
#: Appended as a union so ``KNOWN_VARS`` stays the single public list (the panel/loader variable tables follow it).
KNOWN_VARS = KNOWN_VARS | frozenset(
    {
        "--nts-glass-filter",
        "--nts-glass-tint",
        "--nts-glass-highlight",
        "--nts-bubble-glass-filter",
        "--nts-bubble-glass-tint",
        "--nts-chat-gradient",
        "--nts-panel-gradient",
        "--nts-user-bubble-gradient",
        "--nts-ai-bubble-gradient",
        #: v0.13.0: text gradient. A linear/radial-gradient here replaces the text fill of the
        #: **chat body** with that gradient (``background-clip: text``). It applies only to message
        #: text blocks, not bubble backgrounds -- otherwise the bubble's own fill would be clipped to the text shape too.
        "--nts-text-gradient",
    }
)

#: Added in v0.2.0: real liquid-glass texture (contract _work/FEATURE-CONTRACT-0.2.0.md §1).
#: ``--nts-glass-rim`` is the edge reflection color; ``--nts-glass-sheen`` / ``--nts-glass-noise``
#: are 0–1 continuous amounts (sheen strength / frosted grain), merged into ``GLOW_AMOUNT_VARS``;
#: an invalid value means "back to native", not "fully bright".
KNOWN_VARS = KNOWN_VARS | frozenset(
    {
        "--nts-glass-rim",
        "--nts-glass-sheen",
        "--nts-glass-noise",
    }
)

#: Added in v0.3.0: Apple Liquid Glass style material params (contract _work/FEATURE-CONTRACT-0.3.0.md §1).
#: All three are 0–1 continuous amounts; unset == zero effect (old themes look unchanged):
#:   ``--nts-glass-lens``      edge lens ring (perceptual approximation of the refraction band)
#:   ``--nts-glass-thickness`` glass thickness: top-edge highlight / bottom-edge shade / inner soft glow
#:   ``--nts-glass-specular``  specular highlight strength (position written to --nts-spec-x/y by the loader, following the pointer)
KNOWN_VARS = KNOWN_VARS | frozenset(
    {
        "--nts-glass-lens",
        "--nts-glass-thickness",
        "--nts-glass-specular",
    }
)

#: Added in v0.4.0: three continuous amounts for the lighting engine (contract _work/FEATURE-CONTRACT-0.4.0.md §1).
#: Likewise 0–1 with unset == zero effect, so old themes/presets look exactly the same:
#:   ``--nts-ambient-glow``      pointer light: a soft glow following the mouse (more like real lighting than a single specular spot)
#:   ``--nts-glass-dispersion``  edge dispersion: two 1px refraction lines, warm top-left / cool bottom-right
#:   ``--nts-glass-caustic``     flowing caustics: light spots behind the glass slowly swell and shrink (13s, out of sync with the sheen)
KNOWN_VARS = KNOWN_VARS | frozenset(
    {
        "--nts-ambient-glow",
        "--nts-glass-dispersion",
        "--nts-glass-caustic",
    }
)

#: Added in v0.12.0: background image position / repeat / line height (batch three).
#: The first two are keywords (see ``KEYWORD_VARS``); line height is the newly introduced ``ratio`` kind below.
KNOWN_VARS = KNOWN_VARS | frozenset(
    {
        "--nts-chat-bg-position",
        "--nts-chat-bg-repeat",
        "--nts-line-height",
    }
)

#: Filter chain (fed to ``backdrop-filter``): ``blur(20px) saturate(150%)``.
#: 1–4 tokens, no duplicates; blur ≤ 40px, percentages ≤ 400% (see ``_filter_chain_ok``).
FILTER_VARS = frozenset({"--nts-glass-filter", "--nts-bubble-glass-filter"})
#: Gradients (fed to ``background-image``): ``linear-gradient(135deg, #a 0%, #b 100%)``.
#: Only linear / radial, 2–4 stops, each stop must carry a percentage (see ``_gradient_ok``).
GRADIENT_VARS = frozenset(
    {
        "--nts-chat-gradient",
        "--nts-panel-gradient",
        "--nts-user-bubble-gradient",
        "--nts-ai-bubble-gradient",
        #: v0.13.0: text gradient. Consumed via ``background-image`` + ``background-clip: text``,
        #: with the same validation as background gradients (linear/radial, 2–4 stops, stops must carry a percentage).
        "--nts-text-gradient",
    }
)
RE_FILTER_TOKEN = re.compile(
    r"^(blur|saturate|brightness|contrast|grayscale)"
    r"\(([0-9]{1,3}(?:\.[0-9]+)?)(px|%)\)$"
)
FILTER_MAX_BLUR_PX = 40.0
FILTER_MAX_PERCENT = 400.0
RE_GRADIENT_HEAD = re.compile(r"^(linear|radial)-gradient\(([\s\S]*)\)$", re.IGNORECASE)
RE_GRADIENT_ANGLE = re.compile(r"^-?[0-9]{1,3}(?:\.[0-9]+)?deg$", re.IGNORECASE)
RE_GRADIENT_STOP = re.compile(
    r"^((?:#[0-9a-f]{3,8})|(?:rgba?\([^()]*\))|(?:hsla?\([^()]*\))|(?:[a-z]{3,20}))"
    r"(?:\s+([0-9]{1,3}(?:\.[0-9]+)?)%)?$",
    re.IGNORECASE,
)
GRADIENT_MAX_STOPS = 4
GRADIENT_MAX_CHARS = 300

#: Allowed color value shapes. Deliberately conservative: fully anchored, so arbitrary CSS cannot be injected into variables.
RE_HEX = re.compile(
    r"^#([0-9a-f]{3}|[0-9a-f]{4}|[0-9a-f]{6}|[0-9a-f]{8})$", re.IGNORECASE
)
RE_RGB = re.compile(
    r"^rgba?\(\s*[0-9]{1,3}\s*,\s*[0-9]{1,3}\s*,\s*[0-9]{1,3}"
    r"\s*(?:,\s*(?:0|1|0?\.[0-9]+)\s*)?\)$",
    re.IGNORECASE,
)
RE_HSL = re.compile(
    r"^hsla?\(\s*-?[0-9]{1,3}(?:\.[0-9]+)?\s*,"
    r"\s*[0-9]{1,3}(?:\.[0-9]+)?%\s*,"
    r"\s*[0-9]{1,3}(?:\.[0-9]+)?%\s*"
    r"(?:,\s*(?:0|1|0?\.[0-9]+)\s*)?\)$",
    re.IGNORECASE,
)
#: CSS named colors (the 148 in the spec) plus currentcolor.
#: Deliberately does **not** accept inherit / initial / unset / revert -- they inherit the parent's color,
#: which would let "change only the bubble text color" leak elsewhere.
RE_KEYWORD_COLOR = re.compile(
    r"^(?:transparent|aliceblue|antiquewhite|aqua|aquamarine|azure|beige|bisque|black|blanchedalmond|blue|blueviolet|brown|burlywood|cadetblue|chartreuse|chocolate|coral|cornflowerblue|cornsilk|crimson|currentcolor|cyan|darkblue|darkcyan|darkgoldenrod|darkgray|darkgreen|darkgrey|darkkhaki|darkmagenta|darkolivegreen|darkorange|darkorchid|darkred|darksalmon|darkseagreen|darkslateblue|darkslategray|darkslategrey|darkturquoise|darkviolet|deeppink|deepskyblue|dimgray|dimgrey|dodgerblue|firebrick|floralwhite|forestgreen|fuchsia|gainsboro|ghostwhite|gold|goldenrod|gray|green|greenyellow|grey|honeydew|hotpink|indianred|indigo|ivory|khaki|lavender|lavenderblush|lawngreen|lemonchiffon|lightblue|lightcoral|lightcyan|lightgoldenrodyellow|lightgray|lightgreen|lightgrey|lightpink|lightsalmon|lightseagreen|lightskyblue|lightslategray|lightslategrey|lightsteelblue|lightyellow|lime|limegreen|linen|magenta|maroon|mediumaquamarine|mediumblue|mediumorchid|mediumpurple|mediumseagreen|mediumslateblue|mediumspringgreen|mediumturquoise|mediumvioletred|midnightblue|mintcream|mistyrose|moccasin|navajowhite|navy|oldlace|olive|olivedrab|orange|orangered|orchid|palegoldenrod|palegreen|paleturquoise|palevioletred|papayawhip|peachpuff|peru|pink|plum|powderblue|purple|rebeccapurple|red|rosybrown|royalblue|saddlebrown|salmon|sandybrown|seagreen|seashell|sienna|silver|skyblue|slateblue|slategray|slategrey|snow|springgreen|steelblue|tan|teal|thistle|tomato|turquoise|violet|wheat|white|whitesmoke|yellow|yellowgreen)$",
    re.IGNORECASE,
)

#: Non-color items: length / percentage.
RE_LENGTH = re.compile(r"^-?[0-9]{1,4}(?:\.[0-9]+)?(?:px|rem|em|%|pt|vh|vw)$", re.IGNORECASE)
RE_PERCENT = re.compile(r"^[0-9]{1,3}(?:\.[0-9]+)?%$")
RE_ZERO = re.compile(r"^0$")

#: Opacity-like values (0–1 or percentage).
RE_NUMBER = re.compile(r"^(?:0|1|0?\.[0-9]+|1\.0+)$")  # also allows 1.0 / 1.00

#: Time values (v0.1.11, breathing rhythm). Accepts ``3s`` / ``1.5s`` / ``800ms`` and bare numbers
#: (interpreted as seconds, ``3`` = ``3s``). Capped at 60s: anything longer can't be tried out in the
#: panel, and an infinitely looping 60s animation already feels "motionless", so more is pointless.
RE_TIME = re.compile(r"^(?:(?:0|[1-9][0-9]{0,3})(?:\.[0-9]{1,3})?(?:s|ms))$", re.IGNORECASE)
RE_TIME_BARE = re.compile(r"^(?:0|[1-9][0-9]{0,3})(?:\.[0-9]{1,3})?$")
TIME_MAX_SECONDS = 60.0

#: Font name allowlist: alphanumerics, CJK, spaces, commas, quotes, hyphens, underscores, dots.
RE_FONT = re.compile(r"^[A-Za-z0-9\u4e00-\u9fff ,'\"\-_.]{1,200}$")

#: Explicitly rejected dangerous fragments. Note that parentheses are **not** banned here -- valid
#: rgb()/hsl() values need them; safety comes from each kind's fully anchored regex above.
RE_FORBIDDEN = re.compile(r"[;{}<>\\]|@import|url\s*\(", re.IGNORECASE)

#: Variables with length/percentage semantics.
#:
#: The per-area radii (``--nts-bubble-radius`` and the others, 6 in all) are **parallel**, independent
#: knobs alongside ``--nts-radius``, not aliases: ``neko-theme-studio.css`` emits a separate declaration
#: per area, and each variable has its own native fallback (bubble 16px/8px, shell 14px, card 0px,
#: modal 0px, popover 12px, recommendation card 18px). So leaving them empty keeps the native look
#: exactly -- allowlisting them does not break contract §9.3's "unset means zero change".
LENGTH_VARS = frozenset(
    {
        "--nts-radius",
        "--nts-font-size",
        "--nts-bubble-radius",
        "--nts-shell-radius",
        "--nts-card-radius",
        "--nts-modal-radius",
        "--nts-popover-radius",
        "--nts-rec-radius",
        "--nts-chat-panel-radius",
    }
)
#: Contract §3 describes ``--nts-chat-blur`` as "px", but ``neko-theme-studio.css`` actually writes its
#: native value as ``blur(18px)`` and feeds it straight to ``backdrop-filter``:
#:   ``backdrop-filter: var(--nts-chat-blur, blur(18px))``
#: With just ``18px`` the whole backdrop-filter is dropped as invalid syntax -> the frosted glass
#: disappears with **no error at all**. Both forms are accepted here, and the panel always emits ``blur(...)``.
BLUR_VARS = frozenset({
    "--nts-chat-blur",
    "--nts-panel-glass-blur",
})
RE_BLUR = re.compile(r"^blur\(\s*-?[0-9]{1,4}(?:\.[0-9]+)?(?:px|rem|em|pt)\s*\)$", re.IGNORECASE)
#: Bare lengths that are valid for blur. ``blur()`` only accepts absolute lengths; ``%``/``vh``/``vw``
#: make ``backdrop-filter`` drop the whole declaration, but ``RE_LENGTH`` must allow them for shadow
#: offsets, so a stricter regex lives here to avoid "passes validation, invalid CSS" output like ``blur(18%)``.
RE_BLUR_BARE = re.compile(r"^-?[0-9]{1,4}(?:\.[0-9]+)?(?:px|rem|em|pt)$", re.IGNORECASE)
#: Panel edge glow / glassmorphism (v0.1.11).
#:
#: A set of **purely decorative** variables, all optional; when empty the whole CSS block is not loaded
#: and the look is identical to native (the contract's "unset means zero change" still holds).
#: Mount points are defined by NTS_GLOW in targets.js:
#:
#:   * outer glow / edge halo  -> .compact-export-history-anchor::before
#:     (cannot go on .compact-export-history-scroll: it has its own mask-image, and in practice
#:      only 4 pixels of outer glow showed through, verified with a full-image diff)
#:   * outline / corner highlights / reflection highlight / inner shadow -> .compact-export-history-anchor::after
#:   * frosted layer (blurring the background image itself) -> .compact-export-history-scroll::before, z-index:-1
#:     On ::after it would cover the message text, and backdrop-filter cannot blur a background image
#:     painted on the element itself; a negative-z layer sits exactly between "container background"
#:     and "message content" (measured: image sharpness -62%, text sharpness -1%)
GLOW_COLOR_VARS = frozenset(
    {
        "--nts-panel-glow-color",
        "--nts-panel-border-color",
        "--nts-panel-glass-tint",
    }
)

#: 0–1 multipliers. Like ``--nts-bubble-veil``, continuous amounts rather than boolean switches:
#: users can ask for "just a little glow", and an invalid value means back to native (not fully bright/dark).
GLOW_AMOUNT_VARS = frozenset(
    {
        "--nts-panel-glow-strength",
        "--nts-panel-glow-pulse",
        "--nts-panel-corner-glow",
        "--nts-panel-edge-gradient",
        "--nts-panel-sheen",
        "--nts-panel-inner-glow",
        # v0.2.0: the two 0–1 continuous amounts of real liquid glass.
        "--nts-glass-sheen",
        "--nts-glass-noise",
        # v0.3.0: the three 0–1 continuous amounts of the Apple Liquid Glass material.
        "--nts-glass-lens",
        "--nts-glass-thickness",
        "--nts-glass-specular",
        # v0.4.0: the three 0–1 continuous amounts of the lighting engine.
        "--nts-ambient-glow",
        "--nts-glass-dispersion",
        "--nts-glass-caustic",
    }
)

#: Lengths: glow spread radius, outline width.
GLOW_LENGTH_VARS = frozenset(
    {
        "--nts-panel-glow-size",
        "--nts-panel-border-width",
    }
)

#: Times: breathing period. Units s / ms; unitless values are seconds (``3`` = ``3s``).
TIME_VARS = frozenset({"--nts-panel-glow-speed"})

#: Shadow variables: contract §3 lists them as non-color items, but what is actually needed is a
#: box-shadow string, so it is validated token by token (length / safe color / inset), not as a single length.
SHADOW_VARS = frozenset({"--nts-shadow"})
#: Font variables.
FONT_VARS = frozenset({"--nts-font-family"})
#: Opacity variables (contract §3 lists them as non-color items, but they are neither lengths nor fonts).
#: ``--nts-bubble-veil`` is the master switch for "the veil over the chat background": 0 = fully clear
#: (white veil and background blur off), 1 = native look (translucent white veil + backdrop blur). It is
#: a 0–1 multiplier that CSS ``calc()`` multiplies into both alpha and blur radius -- see the bubble veil
#: section of gen-css.js. Why a multiplier rather than a boolean: it is continuous, so users can ask for
#: "half veil", and when the value is invalid the dropped declaration means "back to native" rather than
#: "fully transparent".
NUMBER_VARS = frozenset({
    "--nts-chat-opacity",
    "--nts-bubble-veil",
    "--nts-chat-bg-image-brightness",
    "--nts-chat-top-fade",
})

#: v0.13.0: text gradient (see ``GRADIENT_VARS``).
KNOWN_VARS = KNOWN_VARS | frozenset({"--nts-text-gradient"})

#: Line height: unitless multiple. Unitless numbers are the most robust for CSS ``line-height`` (they scale
#: with font size), but ``RE_NUMBER``'s 0–1 multiplier semantics cannot hold common line heights like
#: 1.4~2.0, so there is a separate ``ratio`` kind: 0.8–3.0, at most two decimals. All three sides
#: (Python / panel / loader) apply the same check.
RATIO_VARS = frozenset({"--nts-line-height"})
RE_RATIO = re.compile(r"^[0-9](?:\.[0-9]{1,2})?$")
RATIO_MIN = 0.8
RATIO_MAX = 3.0

#: Max JSON text length accepted by ``import_theme`` (same source as the input_schema ``maxLength``).
#: input_schema is only a declaration to the host, which does not guarantee enforcement, so the code must guard it itself.
IMPORT_MAX_CHARS = 262144

#: Public path prefix the panel / loader use to read the palette (same origin as the host, see main_routers/theme_studio_router.py).
PLUGIN_ID = "neko_theme_studio"

#: The theme library (``<data dir>/themes/<id>.json``) and background images (``<data dir>/backgrounds/<hash>.<ext>``)
#: both live in the user data directory -- the host hands images to app windows via
#: ``/api/theme_studio/backgrounds/*``, so we can no longer rely on a plugin static directory as in the plugin era.
LIBRARY_DIR_NAME = "themes"
BACKGROUND_DIR_NAME = "backgrounds"

#: Built-in themes (shipped with the host, not deletable by users): ``theme_studio/presets/<id>.json``.
#: Same format as library entries, plus ``"builtin": true``. Background images in them are written in the
#: **portable** form ``url("images/<hash>.<ext>")`` and rewritten to this machine's absolute URL when read --
#: otherwise a preset moved to another machine would point at someone else's port. The image files go into
#: ``backgrounds/`` in the user data directory (same directory and naming as user uploads), copied over on import.
PRESET_DIR_NAME = "presets"

#: Schedule for "switch theme automatically by time of day" (``<data dir>/schedule.json``): written by the panel,
#: read by the service. The loader decides on its own
#: which built-in theme to use right now, regardless of whether the panel is open.
SCHEDULE_FILE_NAME = "schedule.json"

#: The four zones. **The rule must match the loader's ``scheduleZone()``**:
#: 06:00–09:59 morning, 10:00–17:59 day, 18:00–21:59 evening, 22:00–05:59 night.
#: Boundaries are defined here and returned by ``get_schedule``; panel text uses them too, so there are not three separate copies.
SCHEDULE_ZONES = ("morning", "day", "evening", "night")

#: Zone boundaries (local hour, half-open [start, end)). night wraps midnight, expressed as (22, 6).
SCHEDULE_ZONE_HOURS: dict[str, tuple[int, int]] = {
    "morning": (6, 10),
    "day": (10, 18),
    "evening": (18, 22),
    "night": (22, 6),
}


#: The schedule has two zone tables: weekday (Mon–Fri) and weekend (Sat–Sun).
#: An empty weekend table = same as weekdays (old schedule.json files have exactly this shape, so they stay compatible).
SCHEDULE_DAY_KINDS = ("weekday", "weekend")

#: Saturday / Sunday in Python ``datetime.weekday()`` (Monday = 0).
SCHEDULE_WEEKEND_WEEKDAYS = (5, 6)

SCHEDULE_DAY_KIND_LABELS = {"weekday": "工作日", "weekend": "周末"}



def _default_data_dir() -> Path:
    """Writable data directory: prefer the user data root from the host config manager.

    On failure (e.g. imported from a script without the host runtime) fall back
    to the user's Documents directory; never let a missing directory break
    service construction.
    """
    try:  # deferred import: avoids a circular dependency with app package initialization
        from utils.config_manager import get_config_manager

        docs = getattr(get_config_manager(), "app_docs_dir", None)
        if docs:
            return Path(docs) / "theme_studio"
    except Exception:  # noqa: BLE001
        pass
    return Path.home() / "Documents" / "N.E.K.O" / "theme_studio"


def _day_kind_for(weekday: Any) -> str:
    """Map a weekday (Monday=0) to weekday / weekend.

    ``SCHEDULE_WEEKEND_WEEKDAYS`` is the single source of truth; do not
    hard-code 5/6 elsewhere -- panel text uses ``now.day_kind`` returned by
    ``get_schedule``.
    """
    try:
        index = int(weekday) % 7
    except (TypeError, ValueError):
        return SCHEDULE_DAY_KINDS[0]
    return "weekend" if index in SCHEDULE_WEEKEND_WEEKDAYS else "weekday"


def _schedule_windows() -> list[dict[str, Any]]:
    """Shape the zone boundaries for direct use by the panel / loader (hours only, no localized text)."""
    return [
        {"zone": zone, "start": SCHEDULE_ZONE_HOURS[zone][0], "end": SCHEDULE_ZONE_HOURS[zone][1]}
        for zone in SCHEDULE_ZONES
    ]


#: Background check interval (seconds). The loader polls theme.json every 1.5s, so the actual delay before
#: a theme switch takes effect = this interval + 1.5s. Zones are hour-granular, so 30s is accurate enough
#: without keeping the thread spinning for nothing.
SCHEDULE_TICK_SECONDS = 30.0


def _zone_for_hour(hour: Any) -> str:
    """Map a local hour to a zone name.

    Boundaries come **only from SCHEDULE_ZONE_HOURS**; no second copy of the
    numbers here -- three separate copies would inevitably drift (the loader
    side uses ``scheduleZone()``, panel text uses ``windows`` returned by
    ``get_schedule``).
    """
    try:
        h = int(hour) % 24
    except (TypeError, ValueError):
        h = 0
    for zone in SCHEDULE_ZONES:
        start, end = SCHEDULE_ZONE_HOURS[zone]
        if start < end:
            if start <= h < end:
                return zone
        elif h >= start or h < end:
            # night wraps midnight: (22, 6) -> 22,23,0..5
            return zone
    return SCHEDULE_ZONES[-1]


def _zone_occurrence_date(stamp: datetime, zone: str) -> str:
    """ISO date of the occurrence of ``zone`` that ``stamp`` falls in.

    A zone that wraps midnight (night, 22:00-06:00) belongs to the day it
    started on, so 01:00 on Tuesday is still Monday's night.
    """
    start, end = SCHEDULE_ZONE_HOURS.get(zone, (0, 24))
    day = stamp.date()
    if start > end and stamp.hour < end:
        day = day - timedelta(days=1)
    return day.isoformat()


#: Theme "style families": the panel's theme library is browsed grouped by family (v0.2.0).
#: Values must match the family table in ``plugin/static/app.js`` and ``_work/FEATURE-CONTRACT-0.2.0.md`` §3
#: (three places); unknown families are normalized to ``light`` (old entries without a family field are inferred from light/dark).
PRESET_FAMILIES = (
    "glassy",    # liquid glass
    "gradient",  # gradient
    "dark",      # dark
    "light",     # light
    "texture",   # paper / texture
    "minimal",   # minimal
)

#: Size limits for share bundles (zip) and images. Archives are files **from other people** and must be treated as untrusted input.
ARCHIVE_MAX_BYTES = 8 * 1024 * 1024
IMAGE_MAX_BYTES = 4 * 1024 * 1024
ARCHIVE_MAX_ENTRIES = 300
ARCHIVE_MAX_UNCOMPRESSED = 24 * 1024 * 1024
LIBRARY_MAX_ITEMS = 200
#: Deleted library entries kept in ``<data_dir>/trash`` for undo / restore; older ones are pruned on delete.
TRASH_LIMIT = 30
#: Recycle bin directory name under the data dir. Kept outside ``themes/`` so library globs never see it.
TRASH_DIR_NAME = "trash"
# Each theme has one JSON, and light/dark mode may each reference one background image; plus the manifest and readme.
LIBRARY_ARCHIVE_MAX_ENTRIES = LIBRARY_MAX_ITEMS * 3 + 2
#: box-shadow may stack multiple comma-separated layers (common in neon/glow themes: two halos in different colors).
#: All three validators used to accept a single layer only and rejected any value containing a comma -- a theme
#: obtained elsewhere silently lost its `--nts-shadow` on apply, with nobody telling the user why. Uniformly allow up to 4 layers.
SHADOW_MAX_LAYERS = 4

#: Readme inside the share bundle (plain text, so whoever receives the zip knows how to use it).
ARCHIVE_README = (
    "N.E.K.O 主题工坊 · 主题分享包\n"
    "\n"
    "theme.json 是调色板；images/ 里是与它配套的背景图。\n"
    "用法：在 N.E.K.O 里打开 主题工坊 → 主题仓库 → 导入压缩包，选择本文件即可。\n"
    "导入只会把主题存进仓库，不会立刻改动你当前的配色；在仓库里点「应用」才切换。\n"
)

#: Format marker for theme library backup bundles (zip). Unlike a single-theme share bundle, one bundle holds
#: multiple ``themes/*.json`` + a shared ``images/`` + ``manifest.json``.
LIBRARY_ARCHIVE_FORMAT = "neko-theme-studio-library/1"

#: Readme inside the library backup bundle.
LIBRARY_ARCHIVE_README = (
    "N.E.K.O 主题工坊 · 主题库备份包\n"
    "\n"
    "themes/ 里是你保存过的每一套主题（每套一个 json），images/ 是它们共用的背景图。\n"
    "用法：在 N.E.K.O 里打开 主题工坊 → 主题仓库 → 恢复备份，选择本文件即可。\n"
    "恢复只会往仓库里「新增」主题，不会覆盖或删除现有的任何一套；同名不同 id 会各存一份。\n"
    "恢复不会立刻改动你当前的配色；在仓库里点「应用」才切换。\n"
)

#: Theme library entry id: generated server-side, never taken from the uploaded file name (that would be a path traversal entry point).
RE_THEME_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
#: Background image file name: generated server-side from a content hash.
RE_BG_NAME = re.compile(r"^[a-z0-9]{8,32}\.(?:png|jpg|jpeg|webp|gif)$")
# Pulls background image references out of the raw text of theme.json / library entries (for orphan cleanup).
#: Both reference forms must be recognized: theme.json / library entries use ``backgrounds/<name>``,
#: while built-in themes use the portable ``images/<name>``. It used to match only the former, so on startup
#: images "referenced only by built-in themes" were deleted as orphans -- presets then opened with no background.
RE_BG_REF_TEXT = re.compile(
    r"(?:backgrounds|images)/([a-z0-9]{8,32}\.(?:png|jpg|jpeg|webp|gif))"
)
#: Background image variables (only two valid value shapes: ``none`` or a url() pointing at our own backgrounds/).
IMAGE_VARS = frozenset({"--nts-chat-bg-image"})
#: Captures the file name from a value (handles both ``url("images/x.png")`` and absolute URLs).
RE_BG_VALUE_CAPTURE = re.compile(r"/backgrounds/([A-Za-z0-9._-]{1,80})")
RE_URL_ANY = re.compile(r'url\(\s*"?([^")\s]+)"?\s*\)')
#: Absolute URLs and bare paths are both accepted, but they must point at our own backgrounds/ directory.
RE_BG_URL = re.compile(
    r'^url\(\s*"?(?:https?://(?:127\.0\.0\.1|localhost):[0-9]{2,5})?'
    r"/api/theme_studio/backgrounds/[A-Za-z0-9._-]{1,80}"
    r'"?\s*\)$'
)
#: Image detection (magic bytes first; extensions are only used to filter files inside share bundles).
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".gif")


def _image_extension(data: bytes) -> str:
    """Detect the image format by magic bytes; return "" if unknown (never trust the file extension)."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return ""


def _strip_controls(text: str) -> str:
    """Strip C0/C1 control characters and format characters (RTL overrides, zero-width spaces, BOM, ...).

    Theme names / notes / tags end up in theme.json, the library, share bundles
    and logs. Control characters are legal in JSON (as ``\u0000``), but they break
    lines in the panel, splice log lines, and show up as garbage for bundle
    recipients -- cleaning them at the entry point is more reliable than
    patching the render layer.
    """
    if not text:
        return text
    return "".join(ch for ch in text if unicodedata.category(ch)[0] != "C")


def _clip_name(value: Any, fallback: str) -> str:
    """Normalize a theme name: strip control chars and surrounding whitespace, clip to 60 chars, use fallback if empty."""
    if isinstance(value, str):
        cleaned = _strip_controls(value).strip()
        if cleaned:
            return cleaned[:60]
    return fallback


#: Which variables the panel's theme card thumbnails use (v0.1.12).
#:
#: The panel must not fetch all 82 variables just to draw a thumbnail (a library screen shows a dozen or so
#: themes, and 16 KB of JSON each is not worth it), so only the 7 most recognizable colors/gradients are picked.
#: All values come from ``_validate_palette``'s output and are still truncated to 300 chars before going to the panel.
PREVIEW_VARS = (
    ("bg", "--nts-chat-bg"),
    ("gradient", "--nts-chat-gradient"),
    ("accent", "--nts-send-bg"),
    ("user", "--nts-user-bubble-gradient"),
    ("userSolid", "--nts-user-bubble-bg"),
    ("ai", "--nts-ai-bubble-bg"),
    ("panel", "--nts-panel-glass-tint"),
)


def _material_badge(raw: Any) -> dict[str, Any] | None:
    """Summary of a theme's bundled materials (for the panel card badge).

    ``None`` means the theme has **no** material settings -- applying it leaves
    materials unchanged (backward compatible with old share bundles and library
    entries); when a dict is returned, applying replaces materials wholesale
    unless the user has locked the current materials.
    """
    if not isinstance(raw, dict) or not raw:
        return None
    try:
        clean, _ = _materials.normalize_materials(raw)
    except Exception:  # noqa: BLE001 - a failed summary must not crash the whole theme list
        return None
    preset = str(clean.get("preset") or "custom")
    quality = str(clean.get("quality") or "balanced")
    return {
        "enabled": bool(clean.get("enabled")),
        "preset": preset,
        "label": _materials.preset_label(preset),
        "quality": quality,
        "quality_label": _materials.quality_label(quality),
    }

def _preset_preview(light: dict, dark: dict) -> dict:
    """Thumbnail swatch for the panel: 7 validated values each for light / dark."""

    def snap(source: dict) -> dict:
        out: dict[str, str] = {}
        for name, var in PREVIEW_VARS:
            value = source.get(var)
            if isinstance(value, str) and value:
                out[name] = value[:300]
        return out

    return {"light": snap(light), "dark": snap(dark)}


def _color_luma(value: Any) -> float | None:
    """Best-effort fold of ``#rgb/#rrggbb/rgba()/rgb()`` into 0-1 luma; None if unrecognized.

    Only used to guess a family for old presets without a ``family`` field, so
    precision is not needed -- unrecognized values are treated as light.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    try:
        if text.startswith("#"):
            body = text[1:]
            if len(body) in (3, 4):
                body = "".join(ch * 2 for ch in body[:3])
            elif len(body) in (6, 8):
                body = body[:6]
            else:
                return None
            r, g, b = (int(body[i : i + 2], 16) for i in (0, 2, 4))
        else:
            match = re.match(r"rgba?\(([^()]*)\)", text)
            if not match:
                return None
            parts = [part.strip() for part in match.group(1).split(",")]
            if len(parts) < 3:
                return None
            r, g, b = (int(float(parts[i])) for i in range(3))
    except (TypeError, ValueError):
        return None
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0


def _color_alpha(value: Any) -> float | None:
    """Return a color's alpha (0-1); 1.0 for opaque colors, None if unrecognized."""
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    if not text:
        return None
    if text == "transparent":
        return 0.0
    match = re.match(r"rgba?\(([^()]*)\)", text)
    if match:
        parts = [part.strip() for part in match.group(1).split(",")]
        if len(parts) >= 4:
            try:
                return max(0.0, min(1.0, float(parts[3])))
            except ValueError:
                return None
        return 1.0
    match = re.fullmatch(r"#([0-9a-f]{4}|[0-9a-f]{8})", text)
    if match:
        digits = match.group(1)
        return int(digits[-2:], 16) / 255.0
    if re.fullmatch(r"#[0-9a-f]{3}|#[0-9a-f]{6}", text):
        return 1.0
    return 1.0


def _preset_family(raw: dict, vars_map: dict, dark_map: dict) -> str:
    """Style family: prefer ``family`` from the JSON, else infer "truly translucent -> dark -> light".

    Inference only serves old entries / user-saved themes without a ``family``
    field: only a genuinely translucent glass tint (alpha <= 0.35) counts as
    glassy; most themes carry ``--nts-glass-filter``, so classifying all of
    them as glassy would make the categories meaningless.
    """
    declared = raw.get("family")
    if isinstance(declared, str) and declared.strip().lower() in PRESET_FAMILIES:
        return declared.strip().lower()
    for source in (vars_map or {}, dark_map or {}):
        tint = source.get("--nts-glass-tint")
        alpha = _color_alpha(tint)
        chain = str(source.get("--nts-glass-filter") or "")
        blur = re.search(r"blur\(\s*([0-9]+(?:\.[0-9]+)?)px", chain)
        # Only genuinely translucent (0.02 < alpha ≤ 0.30) + a strong enough backdrop blur counts as glassy;
        # many old themes use ``transparent``/``blur(18px)`` just to "leave native untouched", and
        # classifying them as glassy would make the categories meaningless.
        if (
            alpha is not None
            and 0.02 < alpha <= 0.30
            and blur is not None
            and float(blur.group(1)) >= 20.0
        ):
            return "glassy"
    # Light mode is the default look users see first: only a dark light-mode background counts as dark, everything else is light.
    light_luma = _color_luma((vars_map or {}).get("--nts-chat-bg"))
    if light_luma is not None and light_luma < 0.42:
        return "dark"
    return "light"


def _fmt_saved_at(value: Any) -> str:
    """``2026-11-15T14:02:11+08:00`` -> ``2026-11-15 14:02:11`` (for panel display)."""
    if isinstance(value, str) and value.strip():
        return value.strip().replace("T", " ")[:19]
    return "—"


def _safe_archive_name(name: str) -> str:
    """Turn a theme name into a safe download file name (drop path separators and Windows-illegal chars)."""
    cleaned = re.sub(r'[\\/:*?"<>|\s]+', "-", name or "").strip("-")
    return cleaned[:40] or "theme"


def _parse_theme_json(payload: bytes) -> dict:
    """Parse a theme JSON file (a shared .json / exported theme.json) and return the theme dict.

    Two shapes are accepted:

    - the full ``theme.json`` written by the panel's export
      (``schema``/``name``/``vars``/``dark_vars``/``note``/``tags``);
    - a hand-trimmed ``{"vars": {...}, "dark_vars": {...}}``.

    Only structure + type are checked here; each variable's validity is still
    decided by ``_validate_palette``, and bad values are dropped one by one
    rather than rejecting the whole file (same as zip share bundles).
    """
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise SdkError(_msg("theme.file.notUtf8")) from exc
    text = text.strip()
    if not text:
        raise SdkError(_msg("theme.file.empty"))
    if len(text) > IMPORT_MAX_CHARS:
        raise SdkError(_msg("theme.file.tooLong", length=len(text), limit=IMPORT_MAX_CHARS))
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise SdkError(_msg("theme.file.invalidJson", error=exc)) from exc
    if not isinstance(parsed, dict):
        raise SdkError(_msg("theme.file.notObject"))
    if not isinstance(parsed.get("vars"), dict) and not isinstance(
        parsed.get("dark_vars"), dict
    ):
        raise SdkError(_msg("theme.file.missingVars"))
    return parsed


def _theme_note(value: Any) -> str:
    """One-line note kept on import / save (clipped to 120 chars, same as built-in themes)."""
    return _strip_controls(value).strip()[:120] if isinstance(value, str) else ""


#: Tag limits shared by save / import / edit / listing (the panel's parseTagInput mirrors them).
THEME_TAG_LIMIT = 4
THEME_TAG_MAX_LEN = 12


def _theme_tags(value: Any) -> list:
    """Normalise a tag list: trim, drop control chars / empties / non-strings, clip, dedupe.

    At most ``THEME_TAG_LIMIT`` unique tags of ``THEME_TAG_MAX_LEN`` chars each
    (same as built-in themes). Duplicates are compared after trimming and
    clipping, so "warm" and " warm " count once and do not waste a slot -- the
    panel's tag filter counts each tag once per theme.
    """
    if not isinstance(value, list):
        return []
    out: list = []
    for tag in value:
        if len(out) >= THEME_TAG_LIMIT:
            break
        if not isinstance(tag, str):
            continue
        cleaned = _strip_controls(tag).strip()[:THEME_TAG_MAX_LEN].strip()
        if cleaned and cleaned not in out:
            out.append(cleaned)
    return out


# --------------------------------------------------------------------------- default palette

def _default_vars() -> dict[str, str]:
    """Light (``:root``) defaults -- **empty**, deliberately.

    Contract section 9.3 requires zero visual change when nothing is
    configured. But in ``neko-theme-studio.css`` the same variable carries
    **different** native fallbacks on different rules, e.g.:

    - ``--nts-radius``: ``border-radius: var(--nts-radius, 0|8px|10px|12px)``
      (topbar is 0, cards/panels 12px, buttons/send key 10px)
    - ``--nts-input-bg``: ``#text-input-area`` falls back to
      ``rgba(40,40,40,0.65)``, while ``.composer-input`` falls back to
      ``transparent``

    Pinning any value on ``:root`` would override all of those rules with one
    value -- a visible change without any theme configured, directly violating
    section 9.3. The app-side loader likewise treats an empty palette as
    "restore original" (``neko-theme-studio.js``: empty vars => removeStyle).

    So the factory palette is empty; the full variable catalog + native
    reference values live in the panel's CATALOG in ``static/app.js`` (the
    panel shows native values by default and only writes a row once the user
    actually changes it).
    """
    return {}


def _default_dark_vars() -> dict[str, str]:
    """Dark (``:root[data-theme="dark"]``) defaults -- also empty, for the reason above."""
    return {}


def _now_iso() -> str:
    """Local-timezone ISO-8601, e.g. ``2026-11-07T16:00:00+08:00``."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def default_palette() -> dict[str, Any]:
    """Built-in default palette (used by ``reset_theme`` and the first write to disk)."""
    return {
        "schema": SCHEMA_VERSION,
        "name": DEFAULT_THEME_NAME,
        "enabled": True,
        "updated_at": _now_iso(),
        "vars": _default_vars(),
        "dark_vars": _default_dark_vars(),
        # Materials are **disabled** by default: the panel has the full param set to tweak right away, but app windows see zero change.
        "materials": _materials.default_materials(),
    }


# --------------------------------------------------------------------------- validation

#: Variables that accept only fixed keywords (the value is neither a color nor a length but a CSS enum value).
#: ``--nts-chat-bg-fit`` decides whether the background image is ``cover`` (fills, may crop) or ``contain``
#: (whole image visible, base color on the sides). Allowlist matched in full; ``var()`` / extra content is never accepted.
KEYWORD_VARS: dict[str, tuple[str, ...]] = {
    "--nts-chat-bg-fit": ("cover", "contain"),
    #: v0.8.0: corner geometry. ``round`` = ordinary rounded corners (native behavior), ``continuous`` =
    #: continuous curvature (superellipse / the iOS-style "no sudden straightening at the corner" transition).
    #: The latter needs native ``corner-shape`` in Chrome 139+; on older engines the attribute set by the loader
    #: is still there, but the whole CSS block inside ``@supports`` does not apply -- it falls back to ordinary
    #: rounded corners without breaking.
    "--nts-corner-style": ("round", "continuous"),
    #: v0.12.0: background image position / repeat. Each controls one declaration (``background-position`` /
    #: ``background-repeat``) and leaves ``background-image`` and ``background-size`` alone, so they combine
    #: freely with "background image / brightness / fit". When unset that CSS block does not match and the
    #: look is identical to the native app (centered, no repeat).
    "--nts-chat-bg-position": ("center", "top", "bottom", "left", "right"),
    "--nts-chat-bg-repeat": ("no-repeat", "repeat", "repeat-x", "repeat-y"),
}

#: Added in v0.4.27: background image fit. Merged into ``KNOWN_VARS``; all three sides (backend / panel / loader) share the table.
KNOWN_VARS = KNOWN_VARS | frozenset({"--nts-chat-bg-fit", "--nts-corner-style"})


def _value_kind(name: str) -> str:
    if name in KEYWORD_VARS:
        return "keyword"
    # The glow / glassmorphism group must be classified before the default "color",
    # otherwise 0-1 multipliers and lengths would be treated as colors and rejected outright.
    if name in FILTER_VARS:
        return "filter"
    if name in GRADIENT_VARS:
        return "gradient"
    if name in GLOW_AMOUNT_VARS:
        return "number"
    if name in GLOW_LENGTH_VARS:
        return "length"
    if name in TIME_VARS:
        return "time"
    if name in IMAGE_VARS:
        return "image"
    if name in RATIO_VARS:
        return "ratio"
    if name in LENGTH_VARS:
        return "length"
    if name in BLUR_VARS:
        return "blur"
    if name in SHADOW_VARS:
        return "shadow"
    if name in FONT_VARS:
        return "font"
    if name in NUMBER_VARS:
        return "number"
    return "color"


def _shadow_tokens(value: str) -> list[str]:
    """Split a shadow string on whitespace outside parentheses; return [] if parens are unbalanced."""
    tokens: list[str] = []
    depth = 0
    buf = ""
    for ch in value:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                return []
        if ch.isspace() and depth == 0:
            if buf:
                tokens.append(buf)
                buf = ""
            continue
        buf += ch
    if buf:
        tokens.append(buf)
    if depth != 0:
        return []
    return tokens


def _split_shadow_layers(value: str) -> list:
    """Split a multi-layer box-shadow into layers on top-level commas; [] if parens are unbalanced.

    ``0 0 20px rgba(0,0,0,.4), 0 0 15px rgba(179,136,255,.5)``
    -> two layers. A plain ``value.split(",")`` won't do: colors like
    ``rgba(0, 0, 0, .4)`` are full of commas.
    """
    layers: list = []
    depth = 0
    buf = ""
    for ch in value:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                return []
        if ch == "," and depth == 0:
            if buf.strip():
                layers.append(buf.strip())
            buf = ""
            continue
        buf += ch
    if depth != 0:
        return []
    if buf.strip():
        layers.append(buf.strip())
    return layers


def _shadow_layer_ok(tokens: list) -> bool:
    """Token validation for a single box-shadow layer (formerly inlined in ``_is_safe_value``)."""
    if not tokens or len(tokens) > 6:
        return False
    lengths = 0
    colors = 0
    insets = 0
    for token in tokens:
        if token.lower() == "inset":
            insets += 1
            if insets > 1:
                return False
            continue
        if (
            RE_KEYWORD_COLOR.match(token)
            or RE_HEX.match(token)
            or RE_RGB.match(token)
            or RE_HSL.match(token)
        ):
            colors += 1
            if colors > 1:
                return False
            continue
        if RE_PERCENT.match(token) or "%" in token:
            return False
        if RE_LENGTH.match(token) or RE_ZERO.match(token):
            lengths += 1
            if lengths > 4:
                return False
            # Negative values are only allowed for the first two lengths (x/y offset); blur/spread radii cannot be negative
            if token.startswith("-") and lengths > 2:
                return False
            continue
        return False
    return lengths >= 2


def _split_top_level_commas(text: str) -> list[str] | None:
    """Split on top-level commas (commas inside parens are kept); ``None`` if parens are unbalanced.

    Gradient / shadow colors contain commas (``rgba(0, 0, 0, .4)``); a plain
    ``split(",")`` would cut one color into pieces and wrongly reject the whole
    value. Same paren-counting logic as ``_split_shadow_layers``, extracted
    for shared use.
    """
    parts: list[str] = []
    depth = 0
    buf = ""
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                return None
        if ch == "," and depth == 0:
            parts.append(buf.strip())
            buf = ""
            continue
        buf += ch
    if depth != 0:
        return None
    parts.append(buf.strip())
    return parts


def _color_ok(value: str) -> bool:
    """A single color (for gradient stops): one of hex / rgb() / hsl() / named color / transparent."""
    return bool(
        RE_KEYWORD_COLOR.match(value)
        or RE_HEX.match(value)
        or RE_RGB.match(value)
        or RE_HSL.match(value)
    )


def _filter_chain_ok(value: str) -> bool:
    """``blur(20px) saturate(150%)`` -- validate a backdrop-filter chain.

    Validated per token rather than with one big regex because we also need
    upper bounds (blur <= 40px, percent <= 400%) and "no repeated function",
    which a single regex cannot express. All three sides (Python / panel
    app.js / loader) must judge identically; see FEATURE-CONTRACT-0.1.11.md
    section 2.1.
    """
    text = value.strip()
    if not text or len(text) > 120:
        return False
    tokens = text.split(" ")
    if not 1 <= len(tokens) <= 4:
        return False
    seen: set[str] = set()
    for token in tokens:
        match = RE_FILTER_TOKEN.match(token)
        if not match:
            return False
        func = match.group(1).lower()
        unit = match.group(3).lower()
        number = float(match.group(2))
        if func in seen:
            return False
        seen.add(func)
        if match.group(0) != token:  # fully anchored, ruling out case/whitespace tricks
            return False
        if func == "blur":
            if unit != "px" or number > FILTER_MAX_BLUR_PX:
                return False
        elif unit != "%" or number > FILTER_MAX_PERCENT:
            return False
    return True


def _gradient_ok(value: str) -> bool:
    """``linear-gradient(135deg, #fff 0%, #000 100%)`` -- validate a gradient value.

    Only linear / radial are accepted (the panel only generates these two;
    conic needs extra ``from`` syntax and there is no real demand yet). For
    linear the first argument must be an angle, and every stop must carry a
    percentage -- so panel-generated values have exactly one shape and the
    check can anchor on the whole string.
    """
    text = value.strip()
    if not text or len(text) > GRADIENT_MAX_CHARS:
        return False
    head = RE_GRADIENT_HEAD.match(text)
    if not head:
        return False
    kind = head.group(1).lower()
    parts = _split_top_level_commas(head.group(2))
    if parts is None:
        return False
    if kind == "linear":
        if len(parts) < 3 or not RE_GRADIENT_ANGLE.match(parts[0]):
            return False
        stops = parts[1:]
    else:
        stops = parts
    if not 2 <= len(stops) <= GRADIENT_MAX_STOPS:
        return False
    for stop in stops:
        match = RE_GRADIENT_STOP.match(stop)
        if not match:
            return False
        if not _color_ok(match.group(1)):
            return False
        # Stops must carry a percentage: that way "what the panel generates is what validation accepts",
        # and values like ``#fff`` relying on browser auto-distribution cannot make the two sides disagree.
        if match.group(2) is None or float(match.group(2)) > 100.0:
            return False
    return True


def _is_safe_value(name: str, value: Any) -> bool:
    """Whether a value is safe to write into the given variable (full-string match, nothing extra allowed)."""
    if not isinstance(value, str):
        return False
    v = value.strip()
    if not v or len(v) > 300:
        return False

    kind = _value_kind(name)
    if kind == "filter":
        # Before RE_FORBIDDEN: filter chains contain no url(= / semicolons, but keeping this path separate is clearer.
        return _filter_chain_ok(v)
    if kind == "gradient":
        return _gradient_ok(v)
    if kind == "image":
        # A background image value is itself url(...), so it must bypass RE_FORBIDDEN's url( block
        # and is instead fully anchored by RE_BG_URL (only files in our own backgrounds/ directory).
        return v.lower() == "none" or bool(RE_BG_URL.match(v))
    if kind == "keyword":
        # Only allowlisted keywords (cover / contain / round / continuous), full match; case-insensitive.
        return v.lower() in KEYWORD_VARS[name]
    if RE_FORBIDDEN.search(v):
        return False
    if kind == "color":
        return bool(
            RE_KEYWORD_COLOR.match(v)
            or RE_HEX.match(v)
            or RE_RGB.match(v)
            or RE_HSL.match(v)
        )
    if kind == "length":
        # Negative lengths are always invalid here in CSS (``border-radius`` / ``font-size`` drop the whole
        # declaration on a negative value -> panel reports success, look unchanged). RE_LENGTH must allow a
        # minus sign for shadow offsets, but shadows take their own token branch, so blocking it here suffices.
        if "-" in v:
            return False
        return bool(RE_LENGTH.match(v) or RE_PERCENT.match(v) or RE_ZERO.match(v))
    if kind == "blur":
        # Accept both `blur(18px)` (the form CSS can use) and a bare length `18px` (the contract §3 form);
        # bare lengths are normalized to blur(...) later.
        # A negative radius invalidates the whole ``backdrop-filter`` (silently no change), and blur never needs a minus sign.
        if "-" in v:
            return False
        # Absolute lengths only: blur(18%) / blur(18vw) are not valid CSS; letting them through would persist
        # a value the loader always drops (panel reports success, look unchanged).
        return bool(RE_BLUR.match(v) or RE_BLUR_BARE.match(v) or RE_ZERO.match(v))
    if kind == "time":
        # Breathing period. Bare numbers are seconds; with a unit, convert to seconds then apply the cap.
        # Only s / ms units: breathing rhythm needs nothing else, and loosening this would only let invalid CSS through.
        if RE_TIME.match(v):
            unit = v[-2:].lower()
            try:
                seconds = float(v[:-2]) / 1000.0 if unit == "ms" else float(v[:-1])
            except ValueError:  # pragma: no cover - RE_TIME already guarantees a number
                return False
            return 0.0 <= seconds <= TIME_MAX_SECONDS
        if RE_TIME_BARE.match(v):
            return float(v) <= TIME_MAX_SECONDS
        return False
    if kind == "number":
        # The contract semantics are a 0–1 decimal; percentages are the panel's friendly form, but only 0%–100%
        # is meaningful (999% normalizes to >1, which the loader drops -> "saved but unusable" again).
        if RE_NUMBER.match(v):
            return True
        if not RE_PERCENT.match(v):
            return False
        try:
            return float(v[:-1]) <= 100.0
        except ValueError:  # pragma: no cover - RE_PERCENT already guarantees a number
            return False
    if kind == "ratio":
        # Unitless multiple (line height). Range 0.8–3.0: below 0.8 multi-line text gets squashed together,
        # above 3.0 chat bubbles are no longer readable.
        if not RE_RATIO.match(v):
            return False
        return RATIO_MIN <= float(v) <= RATIO_MAX
    if kind == "font":
        return bool(RE_FONT.match(v))
    if kind == "shadow":
        # box-shadow CSS grammar is inset? && <length>{2,4} && <color>?.
        # A plain token allowlist would accept 0 0 10% #000 (percentages are not valid box-shadow lengths),
        # 1px (too few lengths), 1px 2px 3px 4px 5px (too many) -- the browser drops the whole declaration
        # while the panel shows it as applied (same symptom as "changed but nothing happened").
        # The panel's isSafeShadow / loader's isSafeValue must apply identical checks (THEME_CONTRACT 9.10b).
        layers = _split_shadow_layers(v)
        if not layers or len(layers) > SHADOW_MAX_LAYERS:
            return False
        return all(
            _shadow_layer_ok(_shadow_tokens(layer)) for layer in layers
        )
    return False


def _canonicalize_value(name: str, value: str) -> str:
    """Canonicalize a validated value into the form CSS actually needs.

    Cases:

    - ``--nts-chat-blur``: consumed as
      ``backdrop-filter: var(--nts-chat-blur, blur(18px))``, so a bare length
      ``18px`` must become ``blur(18px)``, or the browser drops the whole
      declaration.
    - ``--nts-chat-opacity``: the contract means a 0-1 fraction, but the panel
      lets users type ``50%``. Normalize to a fraction (rounded to 4 places,
      trailing zeros stripped) so theme.json always holds what the loader can
      consume directly -- otherwise the loader silently drops the percentage
      and the user sees "panel said success, opacity unchanged".
    - ``--nts-panel-glow-speed`` (v0.1.11): the panel allows typing just
      ``3``; append ``s`` to get ``3s``. ``animation-duration: 3`` is invalid
      CSS (durations need a unit), so without this the whole animation
      declaration is dropped -> "speed filled in but nothing moves".
    """
    if name in KEYWORD_VARS:
        # Keywords are case-insensitive (as in CSS itself); always persisted lowercase so the panel dropdown matches.
        return value.lower()
    if name in BLUR_VARS and RE_BLUR_BARE.match(value):
        return f"blur({value})"
    if name in TIME_VARS and RE_TIME_BARE.match(value):
        return f"{value}s"
    if name in NUMBER_VARS and value.endswith("%"):
        try:
            fraction = round(float(value[:-1]) / 100.0, 4)
        except ValueError:  # pragma: no cover - validation already guarantees a number
            return value
        text = f"{fraction:.4f}".rstrip("0").rstrip(".")
        return text or "0"
    return value


def _validate_palette(
    vars_in: Any, dark_in: Any
) -> tuple[dict[str, str], dict[str, str], list[str]]:
    """Validate and filter both variable sets.

    :returns: ``(clean_vars, clean_dark, errors)`` -- the clean dicts are the
        **only** content allowed to be written to disk; errors is the list of
        user-facing rejection reasons.
    """
    clean_vars: dict[str, str] = {}
    clean_dark: dict[str, str] = {}
    errors: list[str] = []

    for label, src, out in (("vars", vars_in, clean_vars), ("dark_vars", dark_in, clean_dark)):
        if src is None:
            continue
        if not isinstance(src, dict):
            errors.append(_msg("theme.palette.notObject", label=label))
            continue
        for raw_name, raw_value in src.items():
            name = str(raw_name)
            if not RE_VAR_NAME.match(name):
                errors.append(_msg("theme.palette.badVarName", label=label, var=repr(name)))
                continue
            if name not in KNOWN_VARS:
                # Well-formed but consumed by no rule -> must be an error. Otherwise a "silent no-op reported
                # as success" makes users think the change worked (an independent verifier tested --nts-nope-var).
                errors.append(
                    _msg("theme.palette.unknownVar", label=label, var=repr(name))
                )
                continue
            if raw_value is None:
                # Explicitly empty = delete the key (fall back to the N.E.K.O native value).
                continue
            if not isinstance(raw_value, str):
                errors.append(_msg("theme.palette.valueNotString", label=label, var=name))
                continue
            value = raw_value.strip()
            if not value:
                # Empty string = delete the key (the panel's "clear" semantics).
                continue
            if not _is_safe_value(name, value):
                errors.append(
                    _msg("theme.palette.badValue", label=label, var=name, value=repr(value))
                )
                continue
            out[name] = _canonicalize_value(name, value)

    return clean_vars, clean_dark, errors


def _permission_hint(exc: BaseException) -> str:
    """Translate a PermissionError into an actionable fix suggestion.

    Root cause (observed 2026-11-18): N.E.K.O plugin subprocesses run at
    **Low integrity level (IL)** (``projectneko_server.exe
    --multiprocessing-fork``), while an elevated install.ps1 run leaves the
    plugin directory / files at Medium/High. A Low process is denied CREATE of
    new files there, which shows up as the panel's apply failing with
    ``[Errno 13] Permission denied: ...\\static\\.theme.json...
    .tmp``, and plugin startup silently skipping its patches because it cannot
    write the template backup. Echoing Errno 13 verbatim makes users think the
    disk is full, so the fix command is given directly here.
    """
    plugin_dir = Path(__file__).resolve().parent
    return _msg("theme.permissionHint", error=exc, plugin_dir=plugin_dir)


_TRUE_WORDS = frozenset({"true", "1", "yes", "on"})
_FALSE_WORDS = frozenset({"false", "0", "no", "off"})


def _coerce_bool(value: Any) -> bool | None:
    """Coerce a boolean-ish value to bool; return ``None`` when undecidable.

    Why: theme.json may be hand-edited to ``"enabled": "false"``, and
    ``bool("false")`` is True -- which would silently re-enable a theme the
    user turned off.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    if isinstance(value, float):
        return None if value != value else bool(value)  # NaN -> undecidable
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in _TRUE_WORDS:
            return True
        if lowered in _FALSE_WORDS:
            return False
    return None


class _ThemeUnreadable(RuntimeError):
    """theme.json exists but cannot be read (permissions / locked / corrupt JSON).

    Write paths must abort on this: if they fell back to ``default_palette()``
    like read paths do, a single transient read failure (an antivirus or
    indexer lock on Windows is enough) would wipe all of the user's saved
    variables, keep only the new values, and still return Ok.
    """


def _safe_int(value: Any, default: int = 0) -> int:
    """Safely convert a numeric field reported by the loader to int.

    Why the fallback: ``loader_report``'s only input comes from the in-page
    loader, which on a version mismatch may send strings, bools or objects
    (``"12"`` / ``true`` / ``{"a":1}``). A bare ``int(...)`` let one odd type
    crash the whole entry, the host saw a 500, and no diagnostics were written
    -- exactly when they were needed most. Non-numeric, negative or oversized
    values all fall back to the default.
    """
    if isinstance(value, bool):
        return int(value)
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    if number < 0:
        return default
    return min(number, 1_000_000_000)


def _clean_attrs(attrs: Any) -> dict[str, str]:
    """Root-node attributes: at most 40 entries, keys 40 chars, values 200 chars.

    The diagnostics file is fully rewritten on every report; without caps a
    single render bug (e.g. className concatenated into an attribute value, a
    whole style string stuffed into data-*) could grow it to tens of MB.
    """
    if not isinstance(attrs, dict):
        return {}
    clean: dict[str, str] = {}
    for key, val in list(attrs.items())[:40]:
        if isinstance(key, str):
            clean[key[:40]] = str(val)[:200]
    return clean


def _clean_list(value: Any, limit: int, count: int) -> list[str]:
    """Chain / error arrays in a report: non-arrays become empty; both item length and count are clipped."""
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item)[:limit] for item in list(value)[:count]]


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    """Atomically write JSON: same-directory temp file + fsync + ``os.replace`` (with retries on Windows).

    Same directory is mandatory, otherwise ``os.replace`` may fail across
    volumes (non-atomic).

    Why retry: on Windows, if any other process (host reading the file, AV
    scan, editor preview) happens to hold a handle on the target,
    ``os.replace`` raises ``PermissionError: [WinError 5]`` -- "save randomly
    fails once, works on retry". Handles are usually released within
    50-200ms, so short retries absorb most of the jitter; we only raise once
    retries are exhausted.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False)
    tmp_path = path.parent / f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        last_error: OSError | None = None
        for attempt in range(6):
            try:
                os.replace(tmp_path, path)
                last_error = None
                break
            except OSError as exc:
                last_error = exc
                if getattr(exc, "winerror", None) not in (5, 32) or attempt == 5:
                    raise
                time.sleep(0.04 * (attempt + 1))
        if last_error is not None:  # pragma: no cover - the loop above either breaks or raises
            raise last_error
    finally:
        # After a successful replace tmp no longer exists; the failure path must clean it up itself.
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass


# --------------------------------------------------------------------------- plugin

class ThemeStudioService(NekoPluginBase):
    """Theme Studio: the single authoritative owner of the palette."""

    def __init__(
        self,
        data_dir: Path | str | None = None,
        ctx: Any = None,
    ) -> None:
        super().__init__(ctx)
        try:
            self.logger = self.enable_file_logging(log_level="INFO")
        except Exception:  # noqa: BLE001 - an unavailable file log must not block functionality
            self.logger = getattr(ctx, "logger", None)

        #: Host code tree (read-only): static assets such as the two loader files.
        self.plugin_root = PACKAGE_DIR
        self.static_dir = APP_STATIC_DIR
        #: Writable data directory: theme.json / themes / backgrounds / presets cache / exports.
        self.data_dir = Path(data_dir) if data_dir else _default_data_dir()
        self.theme_path = self.data_dir / "theme.json"
        #: State of the background thread that switches themes by time of day.
        self._schedule_lock = threading.Lock()
        self._schedule_stop_event = threading.Event()
        self._schedule_thread: threading.Thread | None = None
        self._schedule_last_result: dict[str, Any] = {}
        # Optional gate set by the owner (theme_studio.get_service): returns False
        # once the app is exiting, so a startup still in progress cannot start the
        # schedule thread after shutdown already ran.
        self.schedule_allowed: Any = None

    # ------------------------------------------------------------- internal helpers

    def _log(self, level: str, message: str) -> None:
        logger = getattr(self, "logger", None)
        fn = getattr(logger, level, None)
        if callable(fn):
            try:
                fn(message)
            except Exception:  # noqa: BLE001
                pass

    def _normalize(self, raw: Any) -> dict[str, Any]:
        """Normalize data from any source into a valid palette; bad data falls back to defaults."""
        base = default_palette()
        if not isinstance(raw, dict):
            return base

        # Old theme.json files may have a "version" counter field: deliberately ignored here, it simply
        # disappears on the next write (the schema is unchanged, so reading old files does not error).
        name = raw.get("name")
        name = str(name) if isinstance(name, str) and name.strip() else base["name"]

        updated_at = raw.get("updated_at")
        updated_at = (
            str(updated_at)
            if isinstance(updated_at, str) and updated_at.strip()
            else base["updated_at"]
        )

        # On-disk content must be filtered too: the file may have been hand-edited or corrupted by another program.
        clean_vars, clean_dark, errors = _validate_palette(
            raw.get("vars"), raw.get("dark_vars")
        )
        for message in errors:
            self._log("warning", f"theme.json 中被忽略的条目 —— {message}")

        # Materials: missing / old theme.json files lack this section -> fill in defaults (disabled).
        clean_materials, material_warnings = _materials.normalize_materials(
            raw.get("materials")
        )
        for message in material_warnings:
            self._log("warning", f"theme.json 的材质设置被修正 —— {message}")

        # Cannot just bool(...): the string "false" is truthy and would silently re-enable a theme the user turned off.
        raw_enabled = raw.get("enabled", True)
        enabled = _coerce_bool(raw_enabled)
        if enabled is None:
            self._log("warning", f"theme.json 的 enabled 无法解析（{raw_enabled!r}），按启用处理")
            enabled = True

        return {
            "schema": SCHEMA_VERSION,
            "name": name,
            "enabled": enabled,
            "updated_at": updated_at,
            "vars": clean_vars,
            "dark_vars": clean_dark,
            "materials": clean_materials,
        }

    def _read_unlocked(self, *, strict: bool = False) -> dict[str, Any]:
        """Read from disk. Missing / corrupt / permission error -> defaults; never raises.

        :param strict: for **write paths**. When the file exists but cannot be
            read, raise ``_ThemeUnreadable`` instead of returning defaults --
            defaults only suit a first run; using them as the merge base would
            silently wipe the user's saved variables (see ``_ThemeUnreadable``).
            When the file genuinely does not exist both paths return defaults
            (the normal first-run case).
        """
        try:
            # Read bytes and decode in the parsing section below: non-UTF-8 content (UnicodeDecodeError is a
            # ValueError) must take the "parse failed" branch instead of raising straight out of here.
            text = self.theme_path.read_bytes()
        except FileNotFoundError:
            return default_palette()
        except OSError as exc:
            if strict:
                raise _ThemeUnreadable(_msg("theme.themeJson.readFailed", error=exc)) from exc
            self._log("warning", f"读取 theme.json 失败，使用默认值：{exc}")
            return default_palette()
        try:
            return self._normalize(json.loads(text.decode("utf-8")))
        except (ValueError, TypeError) as exc:
            if strict:
                raise _ThemeUnreadable(_msg("theme.themeJson.parseFailed", error=exc)) from exc
            self._log("warning", f"theme.json 解析失败，使用默认值：{exc}")
            return default_palette()

    # ---------------------------------------------------------------- asset fingerprints
    # The two fingerprints carried in theme.json (v0.7.0): the loader uses them to decide "is the CSS in the page
    # the one currently on disk" and "is the loader running in the window the current version", **without
    # downloading** the full CSS/JS again.
    def _asset_meta(self) -> dict[str, str]:
        """Return ``{"css_sha8", "loader_version"}`` of the loader assets.

        Shares :func:`theme_studio.assets.loader_asset_meta` with the template
        ``?v=`` builder so both sides parse ``SCRIPT_VERSION`` the same way.
        Missing / unreadable files yield empty strings: the loader treats empty
        values as "old host, use the full-download path", so this must never
        raise or invent a fingerprint (a fake one would loop stylesheet swaps).
        """
        meta = _assets.loader_asset_meta(self.static_dir / "loader")
        return {
            "css_sha8": str(meta.get("css_sha8") or ""),
            "loader_version": str(meta.get("loader_version") or ""),
        }

    def _with_asset_meta(self, palette: dict[str, Any]) -> dict[str, Any]:
        """Copy the palette and attach both fingerprints (the caller's object is not modified)."""
        payload = dict(palette)
        meta = self._asset_meta()
        if meta.get("css_sha8"):
            payload["css_sha8"] = meta["css_sha8"]
        if meta.get("loader_version"):
            payload["loader_version"] = meta["loader_version"]
        return payload

    def _with_material_vars(self, palette: dict[str, Any]) -> dict[str, Any]:
        """Copy the palette and attach the **compiled material variables** (not persisted, purely derived).

        Why this is not the source of truth: ``materials`` is the single source
        of truth and the variables are just its projection. The on-disk mirror
        (see ``_with_material_cache``) is for the static route / loader; on read
        ``_normalize`` drops it and recomputes from materials -- so hand-edited
        theme.json, imported themes, or write-backs from old plugin versions
        can never end up with "variables say A, parameters say B".

        With ``enabled=false`` empty light/dark maps are returned -- the loader
        then writes no variables and sets no ``data-nts-mt`` attribute, leaving
        the material CSS fully inert.
        """
        payload = dict(palette)
        raw_materials = payload.get("materials")
        clean, _warnings = _materials.normalize_materials(raw_materials)
        payload["materials"] = clean
        if clean.get("enabled"):
            light, dark = _materials.compile_materials(clean)
            payload["material_vars"] = {"light": light, "dark": dark}
            payload["material_attrs"] = _materials.material_attributes(clean)
            payload["material_compile_version"] = _materials.COMPILE_VERSION
        else:
            payload["material_vars"] = {"light": {}, "dark": {}}
            payload["material_attrs"] = {}
        return payload

    def _with_material_cache(self, palette: dict[str, Any]) -> dict[str, Any]:
        """Attach compiled materials to the **persisted** payload (a cache mirror, not the source of truth).

        The loader fetches the theme via `/plugin/<id>/ui/theme.json` -- that
        route has the host read the file from disk directly, bypassing the
        entry. If the derived variables were only on the entry's return value,
        app windows would never see materials (that is where `mt_vars=0` in the
        runtime diagnostics came from).

        The read side does not trust this mirror: `_normalize` only honours
        `materials` and recomputes on read, discarding the on-disk
        `material_vars` / `material_attrs`.
        """
        payload = dict(palette)
        materials = payload.get("materials")
        clean: dict[str, Any] | None = None
        if isinstance(materials, dict):
            clean, _warnings = _materials.normalize_materials(materials)
        if clean is not None and clean.get("enabled"):
            light, dark = _materials.compile_materials(clean)
            payload["material_vars"] = {"light": light, "dark": dark}
            payload["material_attrs"] = _materials.material_attributes(clean)
            payload["material_compile_version"] = _materials.COMPILE_VERSION
        else:
            payload.pop("material_vars", None)
            payload.pop("material_attrs", None)
            payload.pop("material_compile_version", None)
        return payload

    def _write_unlocked(self, palette: dict[str, Any]) -> None:
        """Atomically write theme.json (implemented by the module-level ``_write_json_atomic``)."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        # Attach asset fingerprints (v0.7.0): the loader uses them to tell, with zero downloads, whether the CSS / loader is stale.
        # Material mirror (v0.15.1): the static route does not go through an entry, so the compiled result must be on disk.
        payload = self._with_asset_meta(self._with_material_cache(palette))
        _write_json_atomic(self.theme_path, payload)

    def _ensure_file(self) -> None:
        """Ensure ``static/theme.json`` exists at startup (invalid content is backed up, then reset to defaults)."""
        with _WRITE_LOCK:
            if not self.theme_path.exists():
                self._write_unlocked(default_palette())
                self._log("info", f"已创建默认调色板：{self.theme_path}")
                return
            try:
                text = self.theme_path.read_text(encoding="utf-8")
                parsed = json.loads(text)
                # v0.7.0: fill in missing asset fingerprints once. install.ps1 restores the user palette by writing
                # the backup file straight back, without css_sha8 / loader_version -- without this the loader would
                # only get fingerprints the next time the user applies a theme, and would keep downloading the full
                # CSS/JS until then. Only fingerprints are filled; variables and name are left untouched.
                if '"css_sha8"' not in text and isinstance(parsed, dict):
                    self._write_unlocked(self._normalize(parsed))
                    self._log("info", "已为 theme.json 补上资源指纹（css_sha8 / loader_version）")
                elif isinstance(parsed, dict):
                    # v0.15.4: stale fingerprints must be refreshed too. When install upgrades the loader, theme.json
                    # still has the old loader_version, so the old in-page loader concludes "the remote is my version"
                    # and never upgrades -- the only fix is refreshing fingerprints to the current assets at startup.
                    meta = self._asset_meta()
                    stale = (
                        parsed.get("css_sha8") != meta["css_sha8"]
                        or parsed.get("loader_version") != meta["loader_version"]
                        or parsed.get("material_compile_version")
                        != _materials.COMPILE_VERSION
                    )
                    if stale:
                        self._write_unlocked(self._normalize(parsed))
                        self._log(
                            "info",
                            f"已刷新 theme.json 资源指纹（css_sha8={meta['css_sha8']} / "
                            f"loader_version={meta['loader_version']}）",
                        )
                    # v0.15.1: an old theme.json with materials enabled but no compiled mirror -- the loader reads
                    # the disk file, so a missing mirror means materials have no effect. Fill it once at startup so
                    # the user need not click apply again.
                    materials = parsed.get("materials")
                    if (
                        not stale
                        and isinstance(materials, dict)
                        and materials.get("enabled")
                        and "material_vars" not in parsed
                    ):
                        self._write_unlocked(self._normalize(parsed))
                        self._log("info", "已为 theme.json 补上材质变量镜像")
            except OSError as exc:
                # "Cannot read" (locked / permissions) != "content is corrupt": never overwrite with defaults,
                # or a single transient lock would destroy the user's theme. Leave it as-is and retry next startup.
                self._log("warning", f"theme.json 暂时不可读（{exc}），保持原样")
                return
            except ValueError as exc:
                # The content really is corrupt: archive the original file before writing defaults; never silently destroy user data.
                backup = self.theme_path.with_name(self.theme_path.name + ".corrupt")
                try:
                    os.replace(self.theme_path, backup)
                    self._log(
                        "warning",
                        f"theme.json 解析失败（{exc}），原文件已备份为 {backup.name}",
                    )
                except OSError as move_exc:  # pragma: no cover - a failed backup does not block recovery
                    self._log(
                        "warning",
                        f"theme.json 解析失败（{exc}），备份失败（{move_exc}）",
                    )
                self._write_unlocked(default_palette())

    def _apply_and_store(
        self,
        *,
        vars_in: Any = None,
        dark_in: Any = None,
        name: Any = None,
        enabled: Any = None,
        materials_in: Any = None,
    ) -> tuple[dict[str, Any], list[str], int]:
        """Merge -> validate -> atomic write. Returns (new palette, rejection reasons, accepted input field count).

        The third value lets callers distinguish "all rejected" from "partial
        success": the former should be reported as a failure, otherwise a
        caller looking only at status would think it was written (when not a
        single byte on disk changed).
        """
        with _WRITE_LOCK:
            # strict: abort if the real baseline is unavailable; never overwrite the user's saved palette with defaults.
            palette = self._read_unlocked(strict=True)

            merged_vars = dict(palette["vars"])
            merged_dark = dict(palette["dark_vars"])

            clean_vars, clean_dark, errors = _validate_palette(vars_in, dark_in)

            # Delete semantics: an explicit empty string / None = fall back to the N.E.K.O native value.
            # Note: **rejected** values never reach here (they are neither in clean nor empty),
            # so the old value is kept as-is -- one bad input cannot destroy an existing good value.
            cleared = 0
            for source, merged in ((vars_in, merged_vars), (dark_in, merged_dark)):
                if not isinstance(source, dict):
                    continue
                for raw_name, raw_value in source.items():
                    key = str(raw_name)
                    if not RE_VAR_NAME.match(key):
                        continue
                    if raw_value is None or (
                        isinstance(raw_value, str) and not raw_value.strip()
                    ):
                        merged.pop(key, None)
                        # A clear of a known variable is an accepted edit (_validate_palette
                        # reports no error for it); unknown names are rejected there.
                        if key in KNOWN_VARS:
                            cleared += 1
            merged_vars.update(clean_vars)
            merged_dark.update(clean_dark)

            palette["vars"] = merged_vars
            palette["dark_vars"] = merged_dark

            # How many input fields were actually accepted. Silently dropped entries (not in KNOWN_VARS, invalid
            # value) are not counted -- they took no effect and must not be treated as success.
            # Successful clears do count: "clear one var + one unknown var" did write something.
            accepted = len(clean_vars) + len(clean_dark) + cleared

            if isinstance(name, str):
                # Empty / whitespace-only = restore the default name (the panel takes this path when the input is cleared), not an error.
                palette["name"] = _clip_name(name, DEFAULT_THEME_NAME)
                accepted += 1
            elif name is not None:
                errors.append(_msg("theme.apply.nameNotString", type=type(name).__name__))

            if enabled is not None:
                coerced = _coerce_bool(enabled)
                if coerced is None:
                    errors.append(_msg("theme.apply.enabledNotBool", value=repr(enabled)))
                else:
                    palette["enabled"] = coerced
                    accepted += 1

            # Materials are **replaced wholesale**, not merged per key: the panel holds the full param tree and
            # submits all of it on every save; per-key merging would make "reset a param to default" impossible
            # to express (missing key = keep unchanged, which collides semantically with "reset to default").
            if isinstance(materials_in, dict):
                clean_materials, material_warnings = _materials.normalize_materials(
                    materials_in
                )
                # custom_presets is the user's asset library, unlike the five param sections: those are "the current
                # set", so wholesale replacement is right; if the preset library were replaced wholesale too, one save
                # where the panel omits it would wipe all the user's saved presets. So merge by name -- deletion goes
                # through the separate delete_material_preset path.
                stored_materials = _materials.normalize_materials(
                    palette.get("materials")
                )[0]
                merged_custom = dict(stored_materials.get("custom_presets") or {})
                merged_custom.update(clean_materials.get("custom_presets") or {})
                clean_materials["custom_presets"] = merged_custom
                # When a custom preset name is sent without the library (an old panel re-importing an export), restore the label from the merged result.
                raw_preset = str((materials_in or {}).get("preset") or "").strip()
                if (
                    raw_preset
                    and raw_preset not in _materials.PRESET_IDS
                    and raw_preset in merged_custom
                ):
                    clean_materials["preset"] = raw_preset
                palette["materials"] = clean_materials
                # Material warnings mean "clamped and applied", not "rejected" -- they must not go into errors,
                # or the panel would report "N rejected" for a successful save.
                for message in material_warnings:
                    self._log("warning", f"apply_theme 材质被修正 —— {message}")
                accepted += 1
            elif materials_in is not None:
                errors.append(
                    _msg("theme.apply.materialsNotObject", type=type(materials_in).__name__)
                )

            palette["updated_at"] = _now_iso()
            palette["schema"] = SCHEMA_VERSION

            self._write_unlocked(palette)
            return palette, errors, accepted

    # ------------------------------------------------------------- lifecycle

    def startup_sync(self) -> dict[str, Any]:
        """Startup initialization (sync): create dirs and theme.json, collect unused backgrounds, start the schedule thread.

        The host has no plugin lifecycle layer: the service starts lazily on the
        first Theme Studio request (in practice the loader's first palette poll)
        and is stopped by the main server's shutdown hook. Callers run it in a
        worker thread; ``startup()`` is only a plugin-era coroutine wrapper.
        """
        # An unwritable data directory (permissions / read-only disk) must not fail startup as a whole: otherwise
        # every poll would retry startup and return 500. The read path already falls back to the default palette when the file is missing.
        try:
            self._ensure_file()
        except OSError as exc:
            self._log("warning", f"无法初始化 theme.json（{exc}），本次使用默认调色板")
        self._ensure_dirs()
        palette = self._read_unlocked()
        # Clean up unreferenced background images. A failure must never affect startup.
        try:
            self._gc_backgrounds()
        except Exception as exc:  # noqa: BLE001
            self._log("warning", f"背景图回收失败（忽略）：{exc}")
        self._log(
            "info",
            "主题工坊已就绪，调色板 "
            f"({len(palette['vars'])} 亮色 / {len(palette['dark_vars'])} 暗色变量)",
        )
        # Time-of-day theme switching: a background thread that wakes every 30s and writes only when the zone changes.
        # Started along with the service (triggered by the first poll from any window's loader), so the schedule still applies after the app restarts.
        self._start_schedule_worker()
        return {"status": "ready", "theme_path": str(self.theme_path)}

    @lifecycle(id="startup")
    async def startup(self, **_):
        return Ok(self.startup_sync())

    @lifecycle(id="shutdown")
    def shutdown(self, **_):
        # The schedule thread is a daemon and dies with the process anyway; but setting the flag explicitly ends
        # the 30s wait immediately, so no thread is left writing theme.json when the app exits.
        self._stop_schedule_worker()
        self._log("info", "主题工坊已停止")
        return Ok({"status": "shutdown"})

    # ------------------------------------------------------------- UI context

    @ui.context(id="dashboard", title=tr("plugin.name", default="主题工坊"))
    async def studio_context(self) -> dict[str, Any]:
        palette = self._read_unlocked()
        return {
            "name": palette["name"],
            "enabled": palette["enabled"],
            "updated_at": palette["updated_at"],
            "var_count": len(palette["vars"]),
            "dark_count": len(palette["dark_vars"]),
            "theme_url": f"{API_BASE}/theme.json",
            "actions": [
                {"id": "get_theme", "entry_id": "get_theme"},
                {"id": "apply_theme", "entry_id": "apply_theme"},
                {"id": "reset_theme", "entry_id": "reset_theme"},
                {"id": "export_theme", "entry_id": "export_theme"},
                {"id": "theme_json", "entry_id": "theme_json"},
                {"id": "import_theme", "entry_id": "import_theme"},
                {"id": "list_themes", "entry_id": "list_themes"},
                {"id": "get_schedule", "entry_id": "get_schedule"},
                {"id": "save_schedule", "entry_id": "save_schedule"},
                {"id": "save_theme", "entry_id": "save_theme"},
                {"id": "apply_saved_theme", "entry_id": "apply_saved_theme"},
                {"id": "delete_theme", "entry_id": "delete_theme"},
                {"id": "delete_themes", "entry_id": "delete_themes"},
                {"id": "rename_material_preset", "entry_id": "rename_material_preset"},
                {"id": "duplicate_material_preset", "entry_id": "duplicate_material_preset"},
                {"id": "restore_theme", "entry_id": "restore_theme"},
                {"id": "clear_trash", "entry_id": "clear_trash"},
                {"id": "update_theme_meta", "entry_id": "update_theme_meta"},
                {"id": "import_theme_archive", "entry_id": "import_theme_archive"},
                {"id": "export_theme_archive", "entry_id": "export_theme_archive"},
                {"id": "import_background", "entry_id": "import_background"},
                {"id": "import_library", "entry_id": "import_library"},
            ],
        }

    # ------------------------------------------------------------- entries

    @plugin_entry(
        id="get_theme",
        name=tr("entries.get_theme.name", default="读取当前主题"),
        description=tr(
            "entries.get_theme.description",
            default="返回当前调色板：亮色变量、暗色变量、名称与启用状态。",
        ),
        input_schema={"type": "object", "properties": {}},
    )
    async def get_theme(self, **_):
        try:
            # Include material_vars: the loader does not parse material params, it only reads the compiled variable table.
            return Ok(self._with_material_vars(self._read_unlocked()))
        except Exception as exc:  # noqa: BLE001
            return Err(SdkError(_msg("theme.getTheme.failed", error=exc)))

    @plugin_entry(
        id="studio_status",
        name=tr("entries.studio_status.name", default="读取运行状态"),
        description=tr(
            "entries.studio_status.description",
            default=(
                "返回内置主题工坊的运行状态：数据目录、当前主题的名称/更新时间/启用状态、"
                "主题仓库与背景图数量、加载器资源版本，以及应用侧加载器最近一次上报的诊断。"
                "面板用它在「运行状态」卡片里说明「主题到底有没有真的贴到界面上」。"
            ),
        ),
        input_schema={"type": "object", "properties": {}},
    )
    async def studio_status(self, **_):
        try:
            palette = self._read_unlocked()
        except Exception as exc:  # noqa: BLE001
            return Err(SdkError(_msg("theme.status.readFailed", error=exc)))
        try:
            library_count = len(self._library_items())
        except Exception as exc:  # noqa: BLE001
            self._log("warning", f"列出主题仓库失败：{exc}")
            library_count = 0
        try:
            preset_count = len(self._preset_items())
        except Exception as exc:  # noqa: BLE001
            self._log("warning", f"列出内置主题失败：{exc}")
            preset_count = 0
        backgrounds: list[str] = []
        try:
            bg_dir = self._backgrounds_dir()
            if bg_dir.is_dir():
                backgrounds = sorted(p.name for p in bg_dir.iterdir() if p.is_file())
        except OSError as exc:
            self._log("warning", f"读取背景图目录失败：{exc}")
        meta = self._asset_meta()
        diagnostics = None
        diag_path = self.data_dir / "diagnostics.json"
        try:
            if diag_path.is_file():
                raw_diag = json.loads(diag_path.read_text(encoding="utf-8"))
            else:
                raw_diag = None
        except (OSError, ValueError):
            raw_diag = None
        if isinstance(raw_diag, dict):
            # Return only a summary for the panel: the full payload is tens of thousands of chars the panel does not need.
            diagnostics = {
                "received_at": str(raw_diag.get("received_at") or ""),
                "page": str(raw_diag.get("page") or ""),
                "loader_version": str(raw_diag.get("loader_version") or ""),
                "nts_vars": _safe_int(raw_diag.get("nts_vars")),
                "mt_vars": _safe_int(raw_diag.get("mt_vars")),
                "rules": _safe_int(raw_diag.get("rules")),
                "enabled": bool(raw_diag.get("enabled")),
                "errors": _clean_list(raw_diag.get("errors"), 200, 5),
            }
        try:
            trash = self._trash_stats()
        except OSError as exc:
            self._log("warning", f"Could not read the theme trash: {exc}")
            trash = {"count": 0, "files": 0, "bytes": 0, "limit": TRASH_LIMIT, "items": []}
        return Ok(
            {
                "data_dir": str(self.data_dir),
                "theme_path": str(self.theme_path),
                "theme_name": _clip_name(palette.get("name"), DEFAULT_THEME_NAME),
                "theme_enabled": bool(palette.get("enabled")),
                "theme_updated_at": str(palette.get("updated_at") or ""),
                "trash": trash,
                "light_vars": len(palette.get("vars") or {}),
                "dark_vars": len(palette.get("dark_vars") or {}),
                "library_count": library_count,
                "preset_count": preset_count,
                "background_count": len(backgrounds),
                "backgrounds": backgrounds[:12],
                "loader_version": meta.get("loader_version", ""),
                "css_sha8": meta.get("css_sha8", ""),
                "diagnostics": diagnostics,
            }
        )

    @plugin_entry(
        id="material_spec",
        name=tr("entries.material_spec.name", default="读取材质参数表"),
        description=tr(
            "entries.material_spec.description",
            default=(
                "返回高级材质系统的参数定义、内置预设、画质档位与组件表，"
                "以及当前生效的材质设置。面板据此渲染表单，避免前后端参数表漂移。"
            ),
        ),
        input_schema={"type": "object", "properties": {}},
    )
    async def material_spec(self, **_):
        try:
            palette = self._read_unlocked()
            current = palette.get("materials")
            if not isinstance(current, dict):
                current = _materials.default_materials()
            return Ok(
                {
                    "version": _materials.MATERIAL_VERSION,
                    "params": [
                        {
                            "key": param.key,
                            "section": param.section,
                            "kind": param.kind,
                            "default": param.default,
                            "min": param.minimum,
                            "max": param.maximum,
                            "choices": list(param.choices),
                            "label": _materials.param_label(param),
                            "var": _materials.var_name(param),
                        }
                        for param in _materials.PARAMS
                    ],
                    "sections": [
                        {"id": section, "label": _materials.section_label(section)}
                        for section in _materials.SECTIONS
                    ],
                    "components": [
                        {"id": comp, "label": _materials.component_label(comp)}
                        for comp in _materials.COMPONENTS
                    ],
                    "component_params": list(_materials.COMPONENT_PARAMS),
                    "quality": [
                        {"id": mode, "label": _materials.quality_label(mode)}
                        for mode in _materials.QUALITY_MODES
                    ],
                    # Six built-ins + custom make seven cards: custom is not "a separately saved set of values" but
                    # "back to param defaults", so its params are the default table, not an empty one.
                    "presets": {
                        preset_id: {
                            "label": _materials.preset_label(preset_id),
                            "params": _materials.PRESETS.get(preset_id)
                            or _materials._blank_params(),
                        }
                        for preset_id in _materials.PRESET_IDS
                    },
                    "preset_labels": {
                        preset_id: _materials.preset_label(preset_id)
                        for preset_id in _materials.PRESET_IDS
                    },
                    "easings": list(_materials.EASINGS),
                    "blur_levels": dict(_materials.BLUR_LEVELS),
                    # User-saved presets: the panel lists cards by name; on switch the backend lays down the whole
                    # param set, through the same apply_materials branch as built-in presets.
                    "custom_presets": dict(
                        _materials.normalize_materials(current)[0].get("custom_presets") or {}
                    ),
                    "current": current,
                }
            )
        except Exception as exc:  # noqa: BLE001
            return Err(SdkError(_msg("theme.materialSpec.readFailed", error=exc)))

    @plugin_entry(
        id="preview_materials",
        name=tr("entries.preview_materials.name", default="预览材质变量"),
        description=tr(
            "entries.preview_materials.description",
            default=(
                "把给定的材质设置编译成 CSS 变量表返回，不落盘。"
                "面板拖动滑块时用它做实时预览：编译逻辑只有后端这一份，前端不再重写，预览与应用窗口因此不会出现偏差。"
            ),
        ),
        input_schema={
            "type": "object",
            "properties": {
                "materials": {
                    "type": "object",
                    "description": "待编译的材质设置；省略时使用当前已保存值",
                },
                "mode": {
                    "type": "string",
                    "enum": ["light", "dark"],
                    "default": "light",
                },
            },
        },
    )
    async def preview_materials(self, materials: Any = None, mode: Any = None, **_):
        try:
            if isinstance(materials, dict):
                clean, warnings = _materials.normalize_materials(materials)
            else:
                palette = self._read_unlocked()
                clean, warnings = _materials.normalize_materials(palette.get("materials"))
            # Note: enabled is ignored here. The point of preview is "see the effect before enabling";
            # whether it is enabled is decided by get_theme / the loader's material_attrs.
            light_vars, dark_vars = _materials.compile_materials(clean)
            want_dark = str(mode or "light").strip().lower() == "dark"
            return Ok(
                {
                    "mode": "dark" if want_dark else "light",
                    "vars": dark_vars if want_dark else light_vars,
                    "light": light_vars,
                    "dark": dark_vars,
                    "attrs": _materials.material_attributes(clean),
                    "warnings": warnings,
                    "materials": clean,
                }
            )
        except Exception as exc:  # noqa: BLE001
            return Err(SdkError(_msg("theme.previewMaterials.failed", error=exc)))

    @plugin_entry(
        id="save_material_preset",
        name=tr("entries.save_material_preset.name", default="保存自定义材质预设"),
        description=tr(
            "entries.save_material_preset.description",
            default=(
                "把当前五节材质参数存成一份命名预设（custom_presets），预设列表里即可一键切换。",
                "传入 materials 时以传入值为准（等于「保存并应用」）。"
            ),
        ),
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "预设名（1-40 字符）"},
                "materials": {"type": "object", "description": "可选；要保存的材质树，缺省用磁盘当前值"},
            },
            "required": ["name"],
        },
    )
    async def save_material_preset(self, name: Any = None, materials: Any = None, **_):
        try:
            title = str(name or "").strip()
            if not title:
                return Err(SdkError(_msg("theme.preset.nameEmpty")))
            if len(title) > 40:
                return Err(SdkError(_msg("theme.preset.nameTooLong", length=len(title))))
            if _materials.is_reserved_preset_name(title):
                # normalize_materials would silently drop it, so the save would report Ok
                # while nothing was stored; refuse before writing.
                return Err(SdkError(_msg("theme.preset.nameReserved", name=title)))
            palette = self._read_unlocked(strict=True)
            source = materials if isinstance(materials, dict) else palette.get("materials")
            clean, warnings = _materials.normalize_materials(source)
            for warning in warnings:
                self._log("warning", f"混合材质预设：{warning}")
            stored = dict(clean.get("custom_presets") or {})
            stored[title] = {section: dict(clean.get(section) or {}) for section in _materials.SECTIONS}
            clean["custom_presets"] = stored
            palette, errors, _accepted = self._apply_and_store(materials_in=clean)
            if errors:
                return Err(SdkError(_msg("theme.errorSeparator").join(errors)))
            current = palette.get("materials") or {}
            return Ok({
                "name": title,
                "names": sorted((current.get("custom_presets") or {}).keys()),
                "materials": current,
            })
        except _ThemeUnreadable as exc:
            return Err(SdkError(str(exc)))
        except Exception as exc:  # noqa: BLE001
            return Err(SdkError(_msg("theme.preset.saveFailed", error=exc)))

    @plugin_entry(
        id="delete_material_preset",
        name=tr("entries.delete_material_preset.name", default="删除自定义材质预设"),
        description=tr(
            "entries.delete_material_preset.description",
            default="按名字删除一份自定义预设；正在使用该预设时回落到 custom（参数保持不动）。",
        ),
        input_schema={
            "type": "object",
            "properties": {"name": {"type": "string", "description": "预设名"}},
            "required": ["name"],
        },
    )
    async def delete_material_preset(self, name: Any = None, **_):
        try:
            title = str(name or "").strip()[:40]
            if not title:
                return Err(SdkError(_msg("theme.preset.nameEmpty")))
            # Deletion must bypass _apply_and_store's "merge preset library by name": merging would add the just-deleted
            # entry back. Modify materials on disk directly here and write atomically.
            with _WRITE_LOCK:
                palette = self._read_unlocked(strict=True)
                clean, _warnings = _materials.normalize_materials(palette.get("materials"))
                stored = dict(clean.get("custom_presets") or {})
                if title not in stored:
                    return Err(SdkError(_msg("theme.preset.notFound", name=title)))
                stored.pop(title, None)
                clean["custom_presets"] = stored
                if clean.get("preset") == title:
                    # Deleting the preset in use: params stay as they are, only the label falls back to custom.
                    clean["preset"] = "custom"
                palette["materials"] = clean
                palette["updated_at"] = _now_iso()
                self._write_unlocked(palette)
            current = palette.get("materials") or {}
            return Ok({
                "deleted": title,
                "names": sorted((current.get("custom_presets") or {}).keys()),
                "materials": current,
            })
        except _ThemeUnreadable as exc:
            return Err(SdkError(str(exc)))
        except Exception as exc:  # noqa: BLE001
            return Err(SdkError(_msg("theme.preset.deleteFailed", error=exc)))

    def _check_preset_title(self, raw: Any) -> str:
        """Validate a custom preset name the same way save_material_preset does."""
        title = str(raw or "").strip()
        if not title:
            raise SdkError(_msg("theme.preset.nameEmpty"))
        if len(title) > 40:
            raise SdkError(_msg("theme.preset.nameTooLong", length=len(title)))
        if _materials.is_reserved_preset_name(title):
            raise SdkError(_msg("theme.preset.nameReserved", name=title))
        return title

    def _copy_material_preset(self, source: Any, target: Any, *, keep_source: bool) -> dict[str, Any]:
        """Copy (``keep_source``) or move a saved custom preset under ``_WRITE_LOCK``.

        Writes theme.json directly, like delete_material_preset: going through
        _apply_and_store would merge the preset library by name and bring a
        renamed preset back under its old name.
        """
        old = str(source or "").strip()[:40]
        if not old:
            raise SdkError(_msg("theme.preset.nameEmpty"))
        with _WRITE_LOCK:
            palette = self._read_unlocked(strict=True)
            clean, _warnings = _materials.normalize_materials(palette.get("materials"))
            stored = dict(clean.get("custom_presets") or {})
            if old not in stored:
                raise SdkError(_msg("theme.preset.notFound", name=old))
            if target is None or not str(target).strip():
                suffix = _msg("theme.preset.copySuffix")
                base = f"{old} {suffix}"[:40].strip()
                new, counter = base, 2
                while new in stored or _materials.is_reserved_preset_name(new):
                    tail = f" {counter}"
                    new = base[: 40 - len(tail)] + tail
                    counter += 1
            else:
                new = self._check_preset_title(target)
            if new in stored and (keep_source or new != old):
                raise SdkError(_msg("theme.preset.nameTaken", name=new))
            stored[new] = {
                section: dict(values) if isinstance(values, dict) else values
                for section, values in stored[old].items()
            }
            if not keep_source and new != old:
                stored.pop(old, None)
                if clean.get("preset") == old:
                    # The preset in use keeps being used under its new name.
                    clean["preset"] = new
            clean["custom_presets"] = stored
            palette["materials"] = clean
            palette["updated_at"] = _now_iso()
            self._write_unlocked(palette)
        current = palette.get("materials") or {}
        return {
            "name": new,
            "from": old,
            "names": sorted((current.get("custom_presets") or {}).keys()),
            "materials": current,
        }

    @plugin_entry(
        id="rename_material_preset",
        name=tr("entries.rename_material_preset.name", default="重命名自定义材质预设"),
        description=tr(
            "entries.rename_material_preset.description",
            default="给一份已保存的自定义预设改名；正在使用它时跟着改名。",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "原预设名"},
                "new_name": {"type": "string", "description": "新预设名（1-40 字符）"},
            },
            "required": ["name", "new_name"],
        },
    )
    async def rename_material_preset(self, name: Any = None, new_name: Any = None, **_):
        try:
            return Ok(self._copy_material_preset(name, self._check_preset_title(new_name), keep_source=False))
        except SdkError as exc:
            return Err(exc)
        except _ThemeUnreadable as exc:
            return Err(SdkError(str(exc)))
        except Exception as exc:  # noqa: BLE001
            return Err(SdkError(_msg("theme.preset.saveFailed", error=exc)))

    @plugin_entry(
        id="duplicate_material_preset",
        name=tr("entries.duplicate_material_preset.name", default="复制自定义材质预设"),
        description=tr(
            "entries.duplicate_material_preset.description",
            default="把一份已保存的自定义预设复制成新预设；不给新名字时自动加「副本」后缀。",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "原预设名"},
                "new_name": {"type": "string", "description": "可选；新预设名"},
            },
            "required": ["name"],
        },
    )
    async def duplicate_material_preset(self, name: Any = None, new_name: Any = None, **_):
        try:
            return Ok(self._copy_material_preset(name, new_name, keep_source=True))
        except SdkError as exc:
            return Err(exc)
        except _ThemeUnreadable as exc:
            return Err(SdkError(str(exc)))
        except Exception as exc:  # noqa: BLE001
            return Err(SdkError(_msg("theme.preset.saveFailed", error=exc)))

    @plugin_entry(
        id="loader_report",
        name=tr("entries.loader_report.name", default="加载器诊断"),
        description=tr(
            "entries.loader_report.description",
            default="应用侧加载器回传运行期状态，写入 diagnostics.json 供真机自检取证。",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "report": {"type": "object", "description": "加载器上报的诊断对象"},
            },
        },
    )
    async def loader_report(self, report: Any = None, **_):
        """Record runtime diagnostics from the app-side loader.

        Why this exists: whether materials were really injected into the page
        can only be inferred from outside the host process. The loader reports
        the variable count, root attributes, style rule count and errors it
        sees into ``static/diagnostics.json``, turning "did it take effect?"
        into readable data.
        """
        if not isinstance(report, dict):
            return Err(_msg("theme.loaderReport.notObject"))
        received_at = datetime.now().astimezone().isoformat(timespec="seconds")
        attrs = report.get("attrs")
        errors = report.get("errors")
        raw_samples = report.get("samples")
        sample_clean: dict[str, str] = {}
        if isinstance(raw_samples, dict):
            for key, val in list(raw_samples.items())[:20]:
                if isinstance(key, str) and key.startswith("--nts-"):
                    sample_clean[key[:40]] = str(val)[:160]
        raw_elem = report.get("elemSamples")
        elem_clean: dict[str, str] = {}
        ELEM_PROPS = {
            "background-color",
            "background-image",
            "backdrop-filter",
            "border-radius",
            "box-shadow",
            "opacity",
        }
        if isinstance(raw_elem, dict):
            for key, val in list(raw_elem.items())[:24]:
                if isinstance(key, str) and (key in ELEM_PROPS or key.startswith("--nts-mt-")):
                    elem_clean[key[:40]] = str(val)[:200]
        raw_elem2 = report.get("elem2")
        elem2_clean: dict[str, dict[str, str]] = {}
        if isinstance(raw_elem2, dict):
            for key, val in list(raw_elem2.items())[:8]:
                if isinstance(key, str) and isinstance(val, dict):
                    elem2_clean[key[:40]] = {
                        "bg": str(val.get("bg") or "")[:100],
                        "bf": str(val.get("bf") or "")[:80],
                    }
        raw_root = report.get("rootInfo")
        root_clean: dict[str, str] = {}
        if isinstance(raw_root, dict):
            for key in ("className", "inline", "bg", "bgi", "bf", "hits", "rect", "opacity",
                        "visibility", "display", "zIndex"):
                if raw_root.get(key):
                    root_clean[key] = str(raw_root.get(key))[:400]
        raw_shell = report.get("shellInfo")
        shell_clean: dict[str, str] = {}
        if isinstance(raw_shell, dict):
            for key in ("className", "rect", "bg", "bgi", "bf", "opacity", "visibility", "display", "zIndex"):
                if raw_shell.get(key):
                    shell_clean[key] = str(raw_shell.get(key))[:160]
        raw_rects = report.get("compRects")
        rects_clean: dict[str, str] = {}
        if isinstance(raw_rects, dict):
            for sel, rect in list(raw_rects.items())[:8]:
                if isinstance(sel, str) and isinstance(rect, str):
                    rects_clean[sel[:80]] = rect[:40]
        payload = {
            "received_at": received_at,
            "page": str(report.get("page") or "")[:120],
            "comp_matched": str(report.get("compMatched") or "")[:80],
            "loader_version": str(report.get("version") or ""),
            "nts_vars": _safe_int(report.get("ntsVars")),
            "mt_vars": _safe_int(report.get("mtVars")),
            "attrs": _clean_attrs(attrs),
            "rules": _safe_int(report.get("rules")),
            "css_len": _safe_int(report.get("cssLen")),
            "focus_rules": _safe_int(report.get("focusRules")),
            "backdrop": bool(report.get("backdrop")),
            "pointer": bool(report.get("pointer")),
            "enabled": bool(report.get("enabled")),
            "applied_at": str(report.get("appliedAt") or ""),
            "errors": _clean_list(errors, 400, 20),
            "samples": sample_clean,
            "elem_samples": elem_clean,
            "elem2": elem2_clean,
            "root_info": root_clean,
            "shell_info": shell_clean,
            "comp_rects": rects_clean,
            "center_chain": _clean_list(report.get("centerChain"), 260, 10),
            "shell_chain": _clean_list(report.get("shellChain"), 240, 10),
            "viewport": str(report.get("viewport") or "")[:20],
            "mt_rule_count": _safe_int(report.get("mtRuleCount")),
            "mt_decl_count": _safe_int(report.get("mtDeclCount")),
            "page_theme": str(report.get("pageTheme") or "")[:20],
            "history": [],
        }
        target = self.data_dir / "diagnostics.json"
        try:
            # Keep the last 20, to make it easy to correlate "change a version -> look once".
            if target.is_file():
                try:
                    old = json.loads(target.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    old = {}
                past = old.get("history")
                if isinstance(past, list):
                    payload["history"] = past[-19:]
            payload["history"].append({
                "at": received_at,
                "page": payload["page"],
                "loader_version": payload["loader_version"],
                "nts_vars": payload["nts_vars"],
                "mt_vars": payload["mt_vars"],
                "rules": payload["rules"],
                "focus_rules": payload["focus_rules"],
                "attrs": payload["attrs"],
                "errors": payload["errors"],
                "under_glow": sample_clean.get("--nts-mt-under-glow", ""),
                "rim_img": sample_clean.get("--nts-mt-rim-img", "")[:80],
                "comp_matched": payload["comp_matched"],
                "bg_c": sample_clean.get("--nts-mt-bg-c", "")[:60],
                "elem_bg": elem_clean.get("background-color", ""),
                "elem_filter": elem_clean.get("backdrop-filter", "")[:60],
            })
            _write_json_atomic(target, payload)
            return Ok({"path": str(target), "received_at": received_at})
        except OSError as exc:
            return Err(_msg("theme.loaderReport.writeFailed", error=exc))

    @plugin_entry(
        id="apply_theme",
        name=tr("entries.apply_theme.name", default="应用主题"),
        description=tr(
            "entries.apply_theme.description",
            default="把给定的 CSS 变量合并进当前调色板并原子写入 theme.json。",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "vars": {"type": "object", "description": "亮色变量表 --nts-* → 值"},
                "dark_vars": {"type": "object", "description": "暗色变量表 --nts-* → 值"},
                "name": {"type": "string", "maxLength": 60},
                "enabled": {"type": "boolean", "default": True},
                "materials": {
                    "type": "object",
                    "description": (
                        "高级材质设置（整份替换）：version/enabled/preset/quality/"
                        "global/lighting/refraction/surface/animation/dark/"
                        "components/custom_presets"
                    ),
                },
            },
        },
    )
    async def apply_theme(
        self,
        vars: Any = None,  # noqa: A002 - name matches the contract input_schema
        dark_vars: Any = None,
        name: Any = None,
        enabled: Any = None,
        materials: Any = None,
        **_,
    ):
        try:
            palette, errors, accepted = self._apply_and_store(
                vars_in=vars,
                dark_in=dark_vars,
                name=name,
                enabled=enabled,
                materials_in=materials,
            )
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, PermissionError):
                return Err(SdkError(_permission_hint(exc)))
            return Err(SdkError(_msg("theme.apply.writeFailed", error=exc)))

        gave_input = any(
            x is not None for x in (vars, dark_vars, name, enabled, materials)
        )

        if errors:
            # The valid part has been written; report rejected entries back to the panel as-is for display.
            self._log("warning", f"apply_theme 拒绝 {len(errors)} 条：{errors[:3]}")
            if gave_input and accepted == 0:
                # Nothing accepted = this call did nothing and must report failure,
                # otherwise a caller looking only at status would think the write succeeded.
                return Err(
                    SdkError(
                        _msg(
                            "theme.apply.nothingWritten",
                            errors=_msg("theme.errorSeparator").join(errors),
                        )
                    )
                )
            return Ok({**self._with_material_vars(palette), "rejected": errors})
        return Ok(self._with_material_vars(palette))

    @plugin_entry(
        id="reset_theme",
        name=tr("entries.reset_theme.name", default="恢复默认主题"),
        description=tr(
            "entries.reset_theme.description",
            default="把内置默认调色板写回 theme.json（清空所有自定义颜色）。",
        ),
        input_schema={"type": "object", "properties": {}},
    )
    async def reset_theme(self, **_):
        try:
            with _WRITE_LOCK:
                palette = default_palette()
                # Custom material presets are the user's asset library, not "the current look": reset only clears
                # the look and keeps the preset library (same merge rule as apply_theme / apply_saved_theme).
                # If the content is corrupt (JSON parse failure) the presets are unrecoverable anyway, so reset as
                # usual -- reset is often used to rescue a broken file. But when the file is temporarily unreadable
                # (locked / permissions) it may be intact: writing defaults then would wipe the preset library, so
                # this reset must be refused and the user asked to retry later.
                try:
                    current = self._read_unlocked(strict=True)
                except _ThemeUnreadable as exc:
                    if isinstance(exc.__cause__, OSError):
                        return Err(
                            SdkError(_msg("theme.reset.themeJsonUnreadable"))
                        )
                    current = None
                if current is not None:
                    kept = _materials.normalize_materials(current.get("materials"))[0]
                    custom = kept.get("custom_presets") or {}
                    if custom:
                        materials = dict(palette.get("materials") or _materials.default_materials())
                        materials["custom_presets"] = dict(custom)
                        palette["materials"] = materials
                palette["updated_at"] = _now_iso()
                self._write_unlocked(palette)
            return Ok(self._with_material_vars(palette))
        except Exception as exc:  # noqa: BLE001
            return Err(SdkError(_msg("theme.reset.failed", error=exc)))

    @plugin_entry(
        id="export_theme",
        name=tr("entries.export_theme.name", default="导出主题"),
        description=tr(
            "entries.export_theme.description",
            default="把当前调色板序列化为 JSON 文本，供用户复制保存或分享。",
        ),
        input_schema={"type": "object", "properties": {}},
    )
    async def export_theme(self, **_):
        try:
            raw = self._read_unlocked()
            # theme.json on disk carries derived mirrors (material_vars / material_attrs /
            # material_compile_version), a projection for the loader to read. Exported JSON is for people to read,
            # edit and share: mixing in 250 derived variables would only double the file and mislead users,
            # so only the factual fields are exported here (the materials subtree is the single source of truth).
            clean = self._normalize(raw)
            payload = json.dumps(clean, ensure_ascii=False, indent=2, sort_keys=False)
            return Ok(
                {
                    "payload": payload,
                    "name": clean["name"],
                    "updated_at": clean["updated_at"],
                }
            )
        except Exception as exc:  # noqa: BLE001
            return Err(SdkError(_msg("theme.export.failed", error=exc)))

    @plugin_entry(
        id="theme_json",
        name=tr("entries.theme_json.name", default="复制主题 JSON"),
        description=tr(
            "entries.theme_json.description",
            default=(
                "把仓库里或内置的一套主题导出成可分享的纯文本 JSON；"
                "引用背景图的变量会被摘掉（图片无法放进文本）。"
            ),
        ),
        input_schema={
            "type": "object",
            "properties": {
                "theme_id": {
                    "type": "string",
                    "description": "主题 id（仓库条目或内置主题）",
                },
                "id": {"type": "string", "description": "theme_id 的别名（面板习惯用 id）"},
            },
        },
    )
    async def theme_json(self, theme_id: Any = "", id: Any = "", **_):
        """Serialize an existing theme into JSON text that can be pasted to someone directly.

        Difference from ``export_theme``: that one exports the palette **currently
        in use** (including unsaved state); this one exports **a specific theme
        from the library** (built-in themes too), handy for pasting into chats /
        issues.

        Background images cannot go into text: variables referencing
        ``backgrounds/<name>`` are stripped and their count is reported in the
        return value, so the panel can suggest using a share bundle to include
        images.
        """
        theme_id = theme_id or id
        try:
            record = self._load_library_item(theme_id)
            raw_vars = record.get("vars") if isinstance(record.get("vars"), dict) else {}
            raw_dark = (
                record.get("dark_vars")
                if isinstance(record.get("dark_vars"), dict)
                else {}
            )
            # Images cannot become text: first count which images this theme references (deduplicated file names),
            # then strip the corresponding variables from the exported JSON.
            # Must count from the raw record **before** _validate_palette: built-in themes store the portable
            # url("images/<hash>"), but validation only accepts this machine's backgrounds/ URLs and would drop them
            # as invalid -- validating first would make the panel show "0 images" for themes that have backgrounds.
            image_names = set()
            image_keys = set()
            for source in (raw_vars, raw_dark):
                for key, value in source.items():
                    if not isinstance(value, str):
                        continue
                    match = RE_URL_ANY.search(value)
                    if not match:
                        continue
                    image_keys.add(key)
                    basename = match.group(1).replace("\\", "/").rsplit("/", 1)[-1]
                    if basename:
                        image_names.add(basename)
            clean_vars, clean_dark, _errors = _validate_palette(
                record.get("vars"), record.get("dark_vars")
            )
            for mapping in (clean_vars, clean_dark):
                for key in [k for k in mapping if k in image_keys]:
                    mapping.pop(key, None)
            if not clean_vars and not clean_dark:
                raise SdkError(_msg("theme.shareJson.noShareableVars"))
            payload = {
                "schema": SCHEMA_VERSION,
                "name": _clip_name(record.get("name"), DEFAULT_THEME_NAME),
                "note": _theme_note(record.get("note")),
                "tags": _theme_tags(record.get("tags")),
                "vars": clean_vars,
                "dark_vars": clean_dark,
            }
            # The full materials subtree is shared only when enabled. An entry whose
            # materials are explicitly off is shared as the patch {"enabled": false}
            # (as share bundles do), so importing it turns the recipient's materials
            # off just like applying the entry would; an entry with no materials at
            # all still omits the field, meaning "keep the recipient's materials".
            record_materials = record.get("materials")
            if isinstance(record_materials, dict) and record_materials:
                clean_materials, _material_warnings = _materials.normalize_materials(
                    record_materials
                )
                if clean_materials.get("enabled"):
                    payload["materials"] = clean_materials
                else:
                    payload["materials"] = {"enabled": False}
            text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        except SdkError as exc:
            return Err(exc)
        except Exception as exc:  # noqa: BLE001
            return Err(SdkError(_msg("theme.shareJson.failed", error=exc)))
        return Ok(
            {
                "payload": text,
                "name": payload["name"],
                "light": len(clean_vars),
                "dark": len(clean_dark),
                "images": len(image_names),
                "bytes": len(text.encode("utf-8")),
            }
        )

    @plugin_entry(
        id="import_theme",
        name=tr("entries.import_theme.name", default="导入主题"),
        description=tr(
            "entries.import_theme.description",
            default="解析并校验一段调色板 JSON 文本，合法后整体应用（非法条目一律丢弃）。",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "payload": {
                    "type": "string",
                    "minLength": 2,
                    "maxLength": IMPORT_MAX_CHARS,
                    "description": "调色板 JSON 文本",
                }
            },
            "required": ["payload"],
        },
    )
    async def import_theme(self, payload: str = "", **_):
        if not isinstance(payload, str):
            return Err(SdkError(_msg("theme.import.notString", type=type(payload).__name__)))
        text = payload.strip()
        if not text:
            return Err(SdkError(_msg("theme.import.empty")))
        if len(text) > IMPORT_MAX_CHARS:
            # input_schema's maxLength is only a declaration the host may not enforce -> guard it ourselves.
            return Err(
                SdkError(_msg("theme.import.tooLong", length=len(text), limit=IMPORT_MAX_CHARS))
            )

        try:
            parsed = json.loads(text)
        except (ValueError, TypeError) as exc:
            return Err(SdkError(_msg("theme.import.invalidJson", error=exc)))
        if not isinstance(parsed, dict):
            return Err(SdkError(_msg("theme.import.notObject")))

        payload_vars = parsed.get("vars")
        payload_dark = parsed.get("dark_vars")
        if not isinstance(payload_vars, dict) and not isinstance(payload_dark, dict):
            return Err(SdkError(_msg("theme.import.missingVars")))

        # Import is "wholesale replace", not merge: clear first, then write the valid subset.
        try:
            with _WRITE_LOCK:
                # strict: abort if the old baseline cannot be read; never use defaults as the baseline and overwrite the user's palette.
                palette = self._read_unlocked(strict=True)
                clean_vars, clean_dark, errors = _validate_palette(payload_vars, payload_dark)

                # Same as apply_saved_theme: drop references to deleted images.
                clean_vars, gone_light = self._without_missing_images(clean_vars)
                clean_dark, gone_dark = self._without_missing_images(clean_dark)
                for gone in (gone_light, gone_dark):
                    for key in gone:
                        errors = list(errors) + [
                            _msg("theme.import.backgroundMissing", key=key)
                        ]

                # Everything rejected = this import has no usable content. The old implementation still wrote to disk,
                # so an "import succeeded" wiped the user's existing palette (all variables lost, name changed too).
                # But a theme that "only tunes materials and changes no color" is valid: materials.enabled being true
                # counts as content, otherwise such a theme could never be imported back after export (0.15.39).
                incoming_materials = parsed.get("materials")
                has_material = (
                    isinstance(incoming_materials, dict)
                    # Use the normalized flag: a raw "false" / "off" string is truthy but means off,
                    # and an off-materials bundle with no colors must not wipe the palette.
                    and bool(_materials.normalize_materials(incoming_materials)[0].get("enabled"))
                    # Only allow it when "the bundle carries no colors at all": one that carries colors but has all of
                    # them rejected is a bad bundle and must still be blocked, or one mistaken import wipes the user's palette.
                    and not payload_vars
                    and not payload_dark
                )
                if not clean_vars and not clean_dark and not has_material:
                    raise SdkError(_msg("theme.import.noUsableVars"))

                old_light = set(palette["vars"])
                old_dark = set(palette["dark_vars"])
                palette["vars"] = clean_vars
                palette["dark_vars"] = clean_dark
                cleared = {
                    "light": len(old_light - set(clean_vars)),
                    "dark": len(old_dark - set(clean_dark)),
                }

                name = parsed.get("name")
                if isinstance(name, str) and name.strip():
                    palette["name"] = _clip_name(name, DEFAULT_THEME_NAME)
                if isinstance(parsed.get("enabled"), bool):
                    palette["enabled"] = bool(parsed["enabled"])

                # Materials: import is a wholesale replace. Old themes without this section keep the user's current
                # materials instead of clearing them to defaults -- switching color schemes should not reset materials.
                incoming = parsed.get("materials")
                if (
                    isinstance(incoming, dict)
                    and incoming
                    and set(incoming) <= {"enabled", "lock", "version"}
                ):
                    # A switch-only patch (see theme_json) toggles materials
                    # but keeps the user's tuned sections and custom presets, the
                    # same way applying a library entry treats it.
                    current_materials = _materials.normalize_materials(
                        palette.get("materials")
                    )[0]
                    (
                        palette["materials"],
                        material_warnings,
                    ) = _materials.apply_materials(current_materials, incoming)
                    for message in material_warnings:
                        self._log("warning", f"导入的材质设置被修正 —— {message}")
                elif isinstance(incoming, dict):
                    (
                        imported_materials,
                        material_warnings,
                    ) = _materials.normalize_materials(incoming)
                    # Custom presets are the user's asset library: merge by name as in apply_saved_theme;
                    # local-only presets must be kept, or importing one palette would wipe them all.
                    stored_materials = _materials.normalize_materials(
                        palette.get("materials")
                    )[0]
                    merged_custom = dict(stored_materials.get("custom_presets") or {})
                    merged_custom.update(imported_materials.get("custom_presets") or {})
                    imported_materials["custom_presets"] = merged_custom
                    palette["materials"] = imported_materials
                    for message in material_warnings:
                        self._log("warning", f"导入的材质设置被修正 —— {message}")

                palette["updated_at"] = _now_iso()
                palette["schema"] = SCHEMA_VERSION

                self._write_unlocked(palette)
        except Exception as exc:  # noqa: BLE001
            return Err(SdkError(_msg("theme.import.failed", error=exc)))

        if errors:
            self._log("warning", f"import_theme 拒绝 {len(errors)} 条：{errors[:3]}")
            return Ok(
                {**self._with_material_vars(palette), "rejected": errors, "cleared": cleared}
            )
        return Ok({**self._with_material_vars(palette), "cleared": cleared})

    # ------------------------------------------------------- theme library / background images

    def _library_dir(self) -> Path:
        return self.data_dir / LIBRARY_DIR_NAME

    def _trash_dir(self) -> Path:
        return self.data_dir / TRASH_DIR_NAME

    def _trash_stats(self) -> dict[str, Any]:
        with _WRITE_LOCK:
            return _trash.stats(self._trash_dir(), TRASH_LIMIT)

    def _backgrounds_dir(self) -> Path:
        return self.data_dir / BACKGROUND_DIR_NAME

    def _presets_dir(self) -> Path:
        """Built-in theme directory (shipped with the plugin, read-only)."""
        return PRESETS_DIR

    def _preset_path(self, theme_id: str) -> Path:
        return self._presets_dir() / f"{theme_id}.json"

    # -- "switch theme by time of day" schedule ----------------------------

    def _schedule_path(self) -> Path:
        return self.data_dir / SCHEDULE_FILE_NAME

    def _schedule_defaults(self) -> dict[str, Any]:
        # schema 2: adds weekend_zones (a separate zone table for weekends). When a schema 1 file is read,
        # weekend_zones degrades to an empty table = same as weekdays, so no migration script is needed.
        return {
            "schema": 2,
            "enabled": False,
            "zones": {zone: "" for zone in SCHEDULE_ZONES},
            "weekend_zones": {zone: "" for zone in SCHEDULE_ZONES},
        }

    def _theme_exists(self, theme_id: str) -> bool:
        """Whether a theme id really exists (built-in preset or user library entry)."""
        if not isinstance(theme_id, str) or not RE_THEME_ID.match(theme_id):
            return False
        if self._preset_path(theme_id).is_file():
            return True
        return (self._library_dir() / f"{theme_id}.json").is_file()

    def _normalize_schedule(
        self,
        raw: Any,
        *,
        strict: bool = True,
        dropped: list[dict[str, Any]] | None = None,
        drop_missing: bool = True,
    ) -> tuple[dict[str, Any], list[str]]:
        """Normalize schedule data from any source into {schema,enabled,zones}; returns (schedule, errors).

        With strict=True (save path) any invalid item is recorded as an error
        and the caller rejects the whole thing; with strict=False (read path)
        bad items are dropped with a warning, so a hand-broken schedule.json
        can never crash the plugin entry.
        """
        errors: list[str] = []
        schedule = self._schedule_defaults()
        if raw is None:
            return schedule, errors
        if not isinstance(raw, dict):
            errors.append(_msg("theme.schedule.notObject"))
            return schedule, errors

        flag = _coerce_bool(raw.get("enabled", False))
        if flag is None:
            errors.append(_msg("theme.schedule.enabledNotBool", value=repr(raw.get("enabled"))))
        else:
            schedule["enabled"] = flag

        for field, label in (
            (
                "zones",
                _msg("theme.dayKind.weekday", default=SCHEDULE_DAY_KIND_LABELS["weekday"]),
            ),
            (
                "weekend_zones",
                _msg("theme.dayKind.weekend", default=SCHEDULE_DAY_KIND_LABELS["weekend"]),
            ),
        ):
            raw_zones = raw.get(field, {})
            if raw_zones is None:
                raw_zones = {}
            if not isinstance(raw_zones, dict):
                errors.append(_msg("theme.schedule.fieldNotObject", field=field))
                continue
            unknown = sorted(set(raw_zones) - set(SCHEDULE_ZONES))
            if unknown:
                errors.append(
                    _msg(
                        "theme.schedule.unknownZones",
                        day=label,
                        zones=_msg("theme.listSeparator").join(unknown),
                    )
                )
            target = schedule[field]
            for zone in SCHEDULE_ZONES:
                value = raw_zones.get(zone, "")
                if value is None:
                    value = ""
                if not isinstance(value, str):
                    errors.append(_msg("theme.schedule.zoneNotString", day=label, zone=zone))
                    continue
                value = value.strip()
                if not value:
                    continue
                if not RE_THEME_ID.match(value):
                    errors.append(
                        _msg("theme.schedule.zoneBadId", day=label, zone=zone, value=repr(value))
                    )
                    continue
                if not self._theme_exists(value):
                    # drop_missing=False is only for "writing back runtime stamps" (_update_schedule_stamp):
                    # that read only fetches a baseline; if it dropped stale zones along the way and wrote the whole
                    # file back, the config would be silently wiped without the user doing anything -- tick could
                    # then only debounce via the missing branch and the config would be lost for good. So this path
                    # must keep user values as-is.
                    if not drop_missing and not strict:
                        target[zone] = value
                        continue
                    errors.append(
                        _msg("theme.schedule.zoneMissingTheme", day=label, zone=zone, theme_id=value)
                    )
                    # On a non-strict read (_read_schedule) keep a copy of the "dropped zones";
                    # the panel uses it to tell the user "this theme is no longer in the library".
                    if dropped is not None:
                        dropped.append({"field": field, "zone": zone, "theme_id": value})
                    continue
                target[zone] = value

        # "Which theme was last switched to for which zone" is written back by the background thread to avoid
        # rewriting theme.json every 30s (every write makes the loader repaint, needlessly). Bad values are simply
        # dropped, not treated as errors -- it is only a runtime stamp, not user config.
        last_zone = raw.get("last_zone")
        if isinstance(last_zone, str) and last_zone in SCHEDULE_ZONES:
            schedule["last_zone"] = last_zone
        last_theme = raw.get("last_theme")
        if isinstance(last_theme, str) and RE_THEME_ID.match(last_theme.strip()):
            schedule["last_theme"] = last_theme.strip()
        # Across a day-kind boundary (Sunday 23:59 -> Monday 00:01) the zone name may be the same (both night),
        # but the two sides use different tables, so "what was last switched to" must be recorded with the day kind.
        last_day_kind = raw.get("last_day_kind")
        if isinstance(last_day_kind, str) and last_day_kind in SCHEDULE_DAY_KINDS:
            schedule["last_day_kind"] = last_day_kind
        # "Which day's occurrence" of the zone (night wrapping midnight counts as the day it started). Recording only
        # the zone name would make a morning-only schedule be treated as "already switched" and skipped the next morning.
        last_date = raw.get("last_date")
        if isinstance(last_date, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", last_date):
            schedule["last_date"] = last_date
        # Fingerprint of "last skipped because the theme did not exist" (day_kind|zone|theme_id). Its only purpose
        # is to log the warning once -- previously a missing theme logged one every 30 seconds, 2880 a day, drowning
        # useful logs when something really went wrong. Also a runtime stamp; bad values are dropped.
        last_missing = raw.get("last_missing")
        if isinstance(last_missing, str) and 0 < len(last_missing) <= 200:
            schedule["last_missing"] = last_missing
        return schedule, errors

    def _read_schedule(self, *, drop_missing: bool = True, strict: bool = False) -> dict[str, Any]:
        """Read schedule.json; missing / unreadable / bad JSON are all treated as "disabled".

        :param drop_missing: whether to drop zones referencing themes that no
            longer exist. The panel / diagnostics drop them (so they can report
            stale entries and prompt the user to re-pick); writing back runtime
            stamps must keep them, otherwise a single write-back permanently
            wipes the user's configuration.
        :param strict: for read-modify-write paths. An existing file that cannot
            be read or parsed raises instead of returning the disabled defaults,
            which would otherwise be written back over the real configuration.
            A missing file still yields the defaults.
        """
        path = self._schedule_path()
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return self._schedule_defaults()
        except OSError as exc:
            if strict:
                raise
            self._log("warning", f"读取 schedule.json 失败（{exc}），按未启用处理")
            return self._schedule_defaults()
        try:
            raw = json.loads(text)
        except ValueError as exc:
            if strict:
                raise
            self._log("warning", f"schedule.json 不是合法 JSON（{exc}），按未启用处理")
            return self._schedule_defaults()
        dropped: list[dict[str, Any]] = []
        schedule, errors = self._normalize_schedule(
            raw, strict=False, dropped=dropped, drop_missing=drop_missing
        )
        self._schedule_stale = dropped
        # Debounce: the background thread reads this file every 30 seconds, and stale zones are not rewritten by a
        # "read", so the same set of errors keeps being read. This used to log a warning every 30 seconds (2880 a
        # day), drowning useful logs when something really went wrong. Log each set of errors once; once the errors
        # disappear the signature resets, so a recurrence (the user deletes again) is still visible.
        signature = "|".join(errors)
        if signature != getattr(self, "_schedule_warn_sig", ""):
            self._schedule_warn_sig = signature
            for message in errors:
                self._log("warning", f"schedule.json：{message}（该时段已忽略）")
        return schedule

    def _save_schedule_unlocked(self, schedule: dict[str, Any]) -> None:
        _write_json_atomic(self._schedule_path(), schedule)

    def _update_schedule_stamp(self, **changes: Any) -> None:
        """Update only runtime stamps (last_*); never overwrite user configuration.

        The tick computes outside the lock and writes back afterwards: the
        schedule it read at the start may be a snapshot tens of milliseconds
        old, during which the user may have just saved. The old implementation
        wrote the whole stale snapshot back, clobbering enabled/zones -- users
        saw "saved, then immediately lost, toggle flipped back" with no error
        in the logs. Here we re-read inside the lock and merge only the passed
        keys (value=None deletes the key).
        """
        try:
            with _WRITE_LOCK:
                # Key point: do not drop zones "referencing a deleted theme" when fetching the baseline.
                # Strict: if the existing file cannot be read right now, skip this
                # stamp update (the except below) rather than writing the disabled
                # defaults plus stamps over the user's configuration.
                fresh = self._read_schedule(drop_missing=False, strict=True)
                for key, value in changes.items():
                    if value is None:
                        fresh.pop(key, None)
                    else:
                        fresh[key] = value
                self._save_schedule_unlocked(fresh)
        except Exception as exc:  # noqa: BLE001
            # Failing to write back the stamp at worst re-switches the theme once more; the config itself is unaffected.
            self._log("warning", f"自动换主题：写回 schedule.json 失败（{exc}）")

    # -- "switch theme by time of day" execution ---------------------------

    def _schedule_zone_map(self, schedule: dict[str, Any], day_kind: str) -> dict[str, Any]:
        """Pick the zone table for a day kind; an all-empty weekend table means "same as weekdays"."""
        zones = schedule.get("zones")
        if not isinstance(zones, dict):
            zones = {}
        if day_kind == "weekend":
            weekend = schedule.get("weekend_zones")
            if isinstance(weekend, dict) and any(str(value or "").strip() for value in weekend.values()):
                return weekend
        return zones

    def _now(self) -> datetime:
        """Current time. Unit tests override this to pin the zone -- otherwise a
        test run crossing midnight or a zone boundary (weekday/weekend) would
        randomly fail for reasons unrelated to the feature.
        """
        return datetime.now()

    def _schedule_tick(self, *, force: bool = False) -> dict[str, Any]:
        """Check once which theme the current zone should use and switch if needed. **Never raises.**

        Switching a theme = rewriting theme.json; the loader picks it up by
        polling within 1.5s and all windows change together. So automatic and
        manual switching share one delivery path, and the loader need not
        understand a second preset format (otherwise both sides parse it and
        the contract surface doubles).
        """
        try:
            schedule = self._read_schedule()
        except Exception as exc:  # noqa: BLE001
            return {"action": "error", "error": _msg("theme.schedule.readFailed", error=exc)}
        if not schedule.get("enabled"):
            return {"action": "disabled"}
        stamp = self._now()
        day_kind = _day_kind_for(stamp.weekday())
        zone = _zone_for_hour(stamp.hour)
        theme_id = str(self._schedule_zone_map(schedule, day_kind).get(zone) or "")
        if not theme_id:
            return {"action": "unset", "zone": zone, "day_kind": day_kind}
        occurrence = _zone_occurrence_date(stamp, zone)
        # Skip only if "already switched to this theme last time". But the theme may have been deleted, so confirm it
        # still exists -- otherwise after the user re-imports the theme, unchanged would short-circuit forever and
        # auto-switching could never recover (fixed in v0.4.22).
        # Also compare "which day's occurrence of the zone": with only a morning zone set, if Monday morning switched
        # and the user manually changed themes in the evening, Tuesday morning must switch again. Old schedule.json
        # files without last_date are treated as the same occurrence, so the first check after upgrading does not
        # suddenly change the user's current theme.
        same_stamp = (
            schedule.get("last_zone") == zone
            and schedule.get("last_theme") == theme_id
            and schedule.get("last_day_kind", "weekday") == day_kind
            and schedule.get("last_date", occurrence) == occurrence
        )
        if not force and same_stamp and self._theme_exists(theme_id):
            if "last_date" not in schedule:
                # Old file: treat this occurrence as "the one already switched" and fill in the date. Otherwise a
                # schedule with a single zone would never advance its stamp and would be judged unchanged every day.
                self._update_schedule_stamp(last_date=occurrence)
            return {"action": "unchanged", "zone": zone, "day_kind": day_kind, "theme_id": theme_id}
        if not self._theme_exists(theme_id):
            fingerprint = f"{day_kind}|{zone}|{theme_id}"
            if schedule.get("last_missing") != fingerprint:
                self._log(
                    "warning",
                    f"自动换主题：{day_kind}时段「{zone}」指定的主题 {theme_id} 已被删除，"
                    "先保持当前主题不动（重新导入或改选主题后会自动恢复）",
                )
            # Advance last_* together: on the next round the same_stamp branch sees theme_exists false and lands here
            # again (but the warning is debounced by last_missing); once the theme is restored, same_stamp true +
            # theme_exists true -> it applies before reaching unchanged.
            self._update_schedule_stamp(
                last_zone=zone,
                last_day_kind=day_kind,
                last_theme=theme_id,
                last_date=occurrence,
                last_missing=fingerprint,
            )
            return {"action": "missing", "zone": zone, "day_kind": day_kind, "theme_id": theme_id}
        try:
            # The schedule above was read without the lock; the user may have disabled it since.
            # save_schedule writes under the same (reentrant) lock, so re-check here and keep the
            # lock held through the apply: a stale tick must not switch the theme afterwards.
            # The fresh check, the theme write and the stamp update form one critical
            # section: a tick working from an older schedule must neither apply a
            # theme the current schedule no longer asks for, nor interleave its
            # apply/stamp with a concurrent tick (e.g. save_schedule's forced one),
            # which could leave the stamp naming a theme the window does not show.
            with _WRITE_LOCK:
                fresh = self._read_schedule()
                if not fresh.get("enabled"):
                    return {"action": "disabled"}
                fresh_theme = str(self._schedule_zone_map(fresh, day_kind).get(zone) or "")
                if fresh_theme != theme_id:
                    return {"action": "superseded", "zone": zone, "day_kind": day_kind}
                outcome = self._apply_theme_record(theme_id)
                # Record "already switched to this theme for this zone" to avoid
                # rewriting theme.json every 30s. Only the stamp is merged; the
                # snapshot read at the start is not written back.
                self._update_schedule_stamp(
                    last_zone=zone,
                    last_day_kind=day_kind,
                    last_theme=theme_id,
                    last_date=occurrence,
                    # Actually switched: clear the "missing theme" stamp so a future
                    # miss (theme changed then deleted again) still warns.
                    last_missing=None,
                )
        except Exception as exc:  # noqa: BLE001
            # When the entry itself is broken (e.g. an empty palette saved by an old version) apply keeps failing, and
            # the schedule thread retries every 30s -> 2880 identical warnings a day, drowning useful logs when something
            # really goes wrong. Debounce by fingerprint as with "theme deleted": report only the first time.
            # Note last_zone/last_theme are **not** advanced here: once the entry is fixed the next round must recover
            # automatically (advancing them would make same_stamp judge it unchanged and never retry).
            message = str(exc)
            fingerprint = f"{day_kind}|{zone}|{theme_id}|error:{message[:120]}"
            if schedule.get("last_missing") != fingerprint:
                self._log("warning", f"自动换主题失败（{zone} → {theme_id}）：{message}")
                self._update_schedule_stamp(last_missing=fingerprint)
            return {
                "action": "error",
                "zone": zone,
                "day_kind": day_kind,
                "theme_id": theme_id,
                "error": str(exc),
            }
        name = str((outcome.get("theme") or {}).get("name") or theme_id)
        self._log("info", f"自动换主题：{zone} → {name}")
        return {
            "action": "applied",
            "zone": zone,
            "day_kind": day_kind,
            "theme_id": theme_id,
            "theme": outcome.get("theme"),
        }

    def _run_schedule_worker(
        self, stop: threading.Event, previous: threading.Thread | None = None
    ) -> None:
        """Background thread: check the time zone every SCHEDULE_TICK_SECONDS.

        Each worker owns its own stop event. A replacement started while the
        stopped predecessor is still inside ``_schedule_tick()`` first waits for
        it to exit, so two workers never tick at once and the restart never
        blocks (or gives up on) the caller.
        """
        if previous is not None:
            previous.join()
        while not stop.is_set():
            try:
                state = self._schedule_tick()
                if state.get("action") == "applied":
                    self._schedule_last_result = state
            except Exception as exc:  # noqa: BLE001 - the thread must not die
                self._log("warning", f"自动换主题检查异常（已忽略）：{exc}")
            stop.wait(SCHEDULE_TICK_SECONDS)

    def _start_schedule_worker(self) -> None:
        """Start the schedule thread (idempotent; restartable after shutdown)."""
        with self._schedule_lock:
            # Checked under the same lock _stop_schedule_worker takes, so start and
            # stop cannot interleave around the exit flag.
            gate = self.schedule_allowed
            if callable(gate) and not gate():
                return
            previous = self._schedule_thread
            if previous is not None and previous.is_alive():
                if not self._schedule_stop_event.is_set():
                    return
            else:
                previous = None
            # A stopped predecessor may still be finishing a tick: the new worker
            # joins it before its first tick (see _run_schedule_worker).
            stop = threading.Event()
            thread = threading.Thread(
                target=self._run_schedule_worker,
                args=(stop, previous),
                name="nts-schedule",
                daemon=True,
            )
            self._schedule_stop_event = stop
            self._schedule_thread = thread
            thread.start()

    def _stop_schedule_worker(self) -> None:
        """Signal the schedule thread to exit (does not join)."""
        with self._schedule_lock:
            self._schedule_stop_event.set()

    def _join_schedule_worker(self, timeout: float) -> bool:
        """Wait up to ``timeout`` seconds for the schedule thread to exit.

        Returns True when no worker is left running. Call after
        :meth:`_stop_schedule_worker`; a worker idle in ``wait()`` exits at
        once, one inside ``_schedule_tick()`` exits after finishing its write.
        """
        with self._schedule_lock:
            thread = self._schedule_thread
        if thread is None or not thread.is_alive():
            return True
        thread.join(timeout=max(0.0, timeout))
        return not thread.is_alive()

    def _ensure_dirs(self) -> None:
        """Ensure themes/ and backgrounds/ exist (called once at plugin startup)."""
        for path in (
            self.data_dir,
            self._library_dir(),
            self._backgrounds_dir(),
            self._presets_dir(),
        ):
            try:
                path.mkdir(parents=True, exist_ok=True)
            except OSError as exc:  # pragma: no cover - a disk error must not crash the entry
                self._log("warning", f"创建目录失败 {path}：{exc}")

    def _background_url(self, filename: str) -> str:
        return f"{BACKGROUNDS_URL_BASE}/{filename}"

    @staticmethod
    def _image_filename_in_value(value: Any) -> str:
        """Extract the file name from ``url(".../backgrounds/<name>")``; return "" if not an image value."""
        if not isinstance(value, str):
            return ""
        match = RE_BG_VALUE_CAPTURE.search(value)
        return match.group(1) if match else ""

    def _image_refs(self, mapping: Any) -> list:
        """``[(var_name, background_file_name)]`` -- only files that really exist on disk."""
        refs = []
        if not isinstance(mapping, dict):
            return refs
        for key, value in mapping.items():
            name = self._image_filename_in_value(value)
            if not name or not RE_BG_NAME.match(name):
                continue
            if not (self._backgrounds_dir() / name).is_file():
                continue
            refs.append((str(key), name))
        return refs

    def _without_missing_images(self, mapping: Any) -> tuple:
        """Drop variables that reference background images no longer on disk.

        Returns ``(clean, dropped_keys)``. Leaving a deleted image URL in
        theme.json only makes the app side request a 404, and ``has_image``
        would contradictorily say there is none -- so strip it before writing
        (= fall back to native).
        """
        if not isinstance(mapping, dict):
            return {}, []
        clean = {}
        dropped = []
        for key, value in mapping.items():
            name = self._image_filename_in_value(value)
            if name and RE_BG_NAME.match(name) and not (
                self._backgrounds_dir() / name
            ).is_file():
                dropped.append(str(key))
                continue
            clean[key] = value
        return clean, dropped

    def _gc_backgrounds(self) -> list:
        """Delete background images not referenced by theme.json or any library entry; return deleted names.

        Every upload writes a file keyed by content hash, so deleted themes and
        failed imports leave orphan images. References are scanned in the
        **raw text** (not the validated vars) to avoid deleting images whose
        variable is invalid but whose file is still a user asset.
        """
        bg_dir = self._backgrounds_dir()
        if not bg_dir.is_dir():
            return []
        texts = []
        # P0 (review py-review P0-1): if theme.json cannot be read we **must abort**, never carry on deleting
        # "as if it referenced no images". In a real PermissionError case, the background the user was currently
        # using got deleted with no backup -- the theme just went blank.
        # Better to skip cleanup this round (orphan images only take a few MB) than to delete live data.
        if self.theme_path.exists():
            try:
                texts.append(self.theme_path.read_text(encoding="utf-8", errors="replace"))
            except OSError as exc:
                self._log("warning", f"背景图回收已跳过：theme.json 暂时不可读（{exc}）")
                return []
        # Likewise abort if library/preset entries cannot be read: they may reference images too.
        # Trashed entries count as references: restoring one must bring its background back.
        unreadable = False
        for directory in (self._library_dir(), self._presets_dir(), self._trash_dir()):
            if not directory.is_dir():
                continue
            for item in directory.glob("*.json"):
                try:
                    texts.append(item.read_text(encoding="utf-8", errors="replace"))
                except OSError:
                    unreadable = True
                    break
            if unreadable:
                break
        if unreadable:
            self._log("warning", "背景图回收已跳过：主题仓库/预设暂时不可读")
            return []
        referenced = set()
        for text in texts:
            referenced.update(RE_BG_REF_TEXT.findall(text))
        removed = []
        # Deletion "moves into .trash" instead of unlinking: users can recover mistaken deletions themselves,
        # and it avoids half-deleted states caused by cloud sync / antivirus holding handles.
        trash = bg_dir / ".trash"
        for path in bg_dir.iterdir():
            if not path.is_file() or path.name in referenced:
                continue
            if not RE_BG_NAME.match(path.name):
                continue
            try:
                trash.mkdir(exist_ok=True)
                target = trash / path.name
                if target.exists():
                    target.unlink()
                os.replace(path, target)
                removed.append(path.name)
            except OSError:
                continue
        if removed:
            self._log("info", f"回收了 {len(removed)} 张没被引用的背景图")
        return removed

    def _save_background_bytes(self, data: bytes, *, label: str = "") -> str:
        """Validate magic bytes, write a background image to disk, return its file name (``<hash16>.<ext>``)."""
        if not data:
            raise SdkError(_msg("theme.image.empty"))
        if len(data) > IMAGE_MAX_BYTES:
            raise SdkError(
                _msg("theme.image.tooLarge", size=len(data), limit=IMAGE_MAX_BYTES)
            )
        ext = _image_extension(data)
        if not ext:
            raise SdkError(_msg("theme.image.unsupportedFormat"))
        filename = f"{hashlib.sha256(data).hexdigest()[:16]}.{ext}"
        self._ensure_dirs()
        target = self._backgrounds_dir() / filename
        wrote = False
        try:
            # The same image (a preset's bundled background) passes through here on every list_themes / apply_saved_theme:
            # if the file already exists with the same size there is nothing to do.
            if not target.is_file() or target.stat().st_size != len(data):
                # The tmp name must be unique: the schedule thread's apply and the panel's list_themes/import_background
                # may write the same image concurrently; sharing one <hash>.tmp, after the first finisher replaces it the
                # other cannot find its tmp (reporting "failed to save image") and a stray .tmp file is left behind.
                tmp = target.with_name(
                    f"{target.name}.{os.getpid()}-{threading.get_ident()}.tmp"
                )
                try:
                    tmp.write_bytes(data)
                    # On Windows os.replace can be knocked back by "target is being replaced / antivirus is scanning"
                    # (WinError 5 / 32). The same image may be written by the schedule thread and the panel at once;
                    # only these two errors are retried here, any other OSError is raised immediately.
                    for attempt in range(6):
                        try:
                            os.replace(tmp, target)
                            break
                        except OSError as exc:
                            if getattr(exc, "winerror", None) not in (5, 32) or attempt == 5:
                                raise
                            time.sleep(0.04 * (attempt + 1))
                    wrote = True
                finally:
                    if tmp.exists():
                        try:
                            tmp.unlink()
                        except OSError:
                            pass
        except OSError as exc:
            raise SdkError(_msg("theme.image.saveFailed", error=exc)) from exc
        # Log a line only on an actual write; otherwise every theme listing would log "saved" for every preset image,
        # filling the log with things that never happened (measured: 4 lines per list_themes).
        if wrote:
            self._log("info", f"背景图已保存：{filename}（{len(data)} 字节）{label}")
        return filename

    # ------------------------------------------------------------- library read/write

    def _library_items(self) -> list:
        """Library entries (newest saved first); bad files are skipped and logged."""
        directory = self._library_dir()
        items = []
        if not directory.is_dir():
            return items
        for path in directory.glob("*.json"):
            if not RE_THEME_ID.match(path.stem):
                continue
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError) as exc:
                self._log("warning", f"仓库条目已损坏，跳过 {path.name}：{exc}")
                continue
            if not isinstance(raw, dict):
                continue
            clean_vars, clean_dark, errors = _validate_palette(
                raw.get("vars"), raw.get("dark_vars")
            )
            if errors:
                self._log("warning", f"仓库条目 {path.name} 有 {len(errors)} 条被忽略")
            items.append(
                {
                    "id": path.stem,
                    "name": _clip_name(raw.get("name"), path.stem),
                    "saved_at": _fmt_saved_at(raw.get("saved_at")),
                    "light": len(clean_vars),
                    "dark": len(clean_dark),
                    "has_image": bool(
                        self._image_refs(clean_vars)
                        or self._image_refs(clean_dark)
                    ),
                    "builtin": False,
                    "note": (raw.get("note").strip()[:120]
                             if isinstance(raw.get("note"), str) else ""),
                    # Same normaliser as save / import / edit, so a hand-edited entry
                    # lists exactly what the next edit would write back.
                    "tags": _theme_tags(raw.get("tags")),
                    "family": _preset_family(raw, clean_vars, clean_dark),
                    "preview": _preset_preview(clean_vars, clean_dark),
                    "material": _material_badge(raw.get("materials")),
                    "_sort": str(raw.get("saved_at") or ""),
                }
            )
        items.sort(key=lambda item: item["_sort"], reverse=True)
        for item in items:
            item.pop("_sort", None)
        return items

    def _copy_preset_item(self, item: dict) -> dict:
        """One-level-deeper copy of a cache item: tags is a list and preview a dict, which a shallow copy would share.

        Callers (panel rendering, export, tests) must be free to mutate what
        they get, otherwise one caller appending to tags would pollute every
        later read.
        """
        copy = dict(item)
        tags = item.get("tags")
        copy["tags"] = list(tags) if isinstance(tags, (list, tuple)) else []
        preview = item.get("preview")
        copy["preview"] = dict(preview) if isinstance(preview, dict) else {}
        material = item.get("material")
        if isinstance(material, dict):
            # The cache is shared across requests in different languages, so
            # the display labels are rebuilt for the caller's locale every time.
            material = dict(material)
            material["label"] = _materials.preset_label(str(material.get("preset") or "custom"))
            material["quality_label"] = _materials.quality_label(
                str(material.get("quality") or "balanced")
            )
            copy["material"] = material
        return copy

    def _presets_signature(self, directory: Path):
        """Content fingerprint of the built-in theme directory, used to cache ``_preset_items`` results.

        Every ``list_themes`` used to re-read and re-validate twenty-odd
        built-in themes and recompute bundled background images (measured
        35-55 ms, all on the panel open / refresh critical path), while this
        content only changes when a new version is installed. The fingerprint
        is (file name, mtime_ns, size): upgrades replacing files always
        invalidate it, as does dropping your own files into presets/, so stale
        results are never served.
        """
        try:
            entries = []
            for path in sorted(directory.glob("*.json")):
                stat = path.stat()
                entries.append((path.name, stat.st_mtime_ns, stat.st_size))
            images = directory / "images"
            if images.is_dir():
                for path in sorted(images.iterdir()):
                    try:
                        stat = path.stat()
                    except OSError:
                        continue
                    entries.append((path.name, stat.st_mtime_ns, stat.st_size))
        except OSError:
            return None
        return tuple(entries)

    def _preset_items(self) -> list:
        """Built-in theme list (shipped with the plugin, read-only), same format as library entries.

        Preset background images use the portable ``url("images/<hash>")``,
        while ``_validate_palette`` only accepts absolute URLs pointing at the
        local ``backgrounds/`` -- so ``_rewrite_archive_refs`` first rewrites
        them to local URLs before validation; otherwise that variable would be
        rejected and the entry's has-background flag would falsely report False.
        """
        directory = self._presets_dir()
        items = []
        if not directory.is_dir():
            return items
        signature = self._presets_signature(directory)
        cached = getattr(self, "_preset_items_cache", None)
        if cached is not None and signature is not None and cached[0] == signature:
            return [self._copy_preset_item(item) for item in cached[1]]
        for path in sorted(directory.glob("*.json")):
            if not RE_THEME_ID.match(path.stem):
                continue
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError) as exc:
                self._log("warning", f"内置主题已损坏，跳过 {path.name}：{exc}")
                continue
            if not isinstance(raw, dict):
                continue
            local = self._rewrite_archive_refs(
                raw, {}, label=f"内置主题 {path.stem}", from_presets=True
            )
            clean_vars, clean_dark, errors = _validate_palette(
                local.get("vars"), local.get("dark_vars")
            )
            if errors:
                self._log("warning", f"内置主题 {path.name} 有 {len(errors)} 条被忽略")
            tags = _theme_tags(raw.get("tags"))
            note_raw = raw.get("note")
            note = note_raw.strip()[:120] if isinstance(note_raw, str) else ""
            items.append(
                {
                    "id": path.stem,
                    "name": _clip_name(raw.get("name"), path.stem),
                    "saved_at": "内置",
                    "light": len(clean_vars),
                    "dark": len(clean_dark),
                    "has_image": bool(
                        self._image_refs(clean_vars)
                        or self._image_refs(clean_dark)
                    ),
                    "builtin": True,
                    "note": note,
                    "tags": tags,
                    "family": _preset_family(raw, clean_vars, clean_dark),
                    "preview": _preset_preview(clean_vars, clean_dark),
                    "material": _material_badge(raw.get("materials")),
                }
            )
        if signature is not None:
            self._preset_items_cache = (signature, items)
        return [self._copy_preset_item(item) for item in items]

    def _load_library_item(self, theme_id: Any) -> dict:
        if not isinstance(theme_id, str) or not RE_THEME_ID.match(theme_id.strip()):
            raise SdkError(_msg("library.entry.invalidId"))
        clean_id = theme_id.strip()
        path = self._library_dir() / f"{clean_id}.json"
        if not path.is_file():
            # Not in the library -> check built-in themes; built-ins go through the same apply/export path.
            preset = self._preset_path(clean_id)
            if preset.is_file():
                path = preset
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise SdkError(_msg("library.entry.notFound", theme_id=theme_id)) from exc
        except OSError as exc:
            raise SdkError(_msg("library.entry.readFailed", error=exc)) from exc
        except (ValueError, TypeError) as exc:
            raise SdkError(_msg("library.entry.corrupted", error=exc)) from exc
        if not isinstance(raw, dict):
            raise SdkError(_msg("library.entry.badFormat"))
        # The file name is the library's primary key (_library_items also reports id from path.stem). The "id" in the
        # record is content and can be corrupted by hand; once the two disagree, any code "locating by record id" operates
        # on a different theme (delete_theme("beta") once deleted alpha.json).
        # Normalizing id to the file name at read time eliminates this whole class of ambiguity in one place.
        if path.parent == self._library_dir():
            raw["id"] = path.stem
        return raw

    def _save_library_item(
        self,
        *,
        name: Any,
        vars_map: dict,
        dark_map: dict,
        note: Any = None,
        tags: Any = None,
        materials: Any = None,
    ) -> dict:
        """Write a new library entry; the server generates the id as "name slug + timestamp".

        Holds ``_WRITE_LOCK`` throughout: with concurrent saves (rapid panel
        clicks, two windows), without the lock two requests would compute the
        same id, race on the same ``.tmp``, and the last one would silently
        overwrite the other.
        """
        self._ensure_dirs()
        with _WRITE_LOCK:
            directory = self._library_dir()
            if len(list(directory.glob("*.json"))) >= LIBRARY_MAX_ITEMS:
                raise SdkError(_msg("library.full", limit=LIBRARY_MAX_ITEMS))
            label = _clip_name(name, DEFAULT_THEME_NAME)
            slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-") or "theme"
            base = f"{slug[:32]}-{datetime.now().strftime('%Y%m%d%H%M%S')}"
            # An id still in the trash can be restored and may still be referenced by the
            # schedule, so a new entry must not reuse it.
            try:
                reserved = {theme for _, theme, _, _ in _trash.batches(self._trash_dir())}
            except OSError as exc:
                self._log("warning", f"Could not list the theme trash: {exc}")
                reserved = set()
            theme_id = base
            counter = 2
            while theme_id in reserved or (directory / f"{theme_id}.json").exists():
                theme_id = f"{base}-{counter}"
                counter += 1
            record = {
                "schema": SCHEMA_VERSION,
                "id": theme_id,
                "name": label,
                "saved_at": _now_iso(),
                "vars": dict(vars_map),
                "dark_vars": dict(dark_map),
            }
            # Materials are saved together with the theme: switching back to it must restore "colors + glass texture"
            # together, otherwise users see "switching themes changed the materials too".
            # But when the source has no materials at all (color-only JSON / share bundle, old backups) we must not
            # invent factory materials: applying the theme would then reset the user's current materials to off. When
            # absent, write nothing, and apply takes the "old entry without materials -> keep current materials" branch.
            if isinstance(materials, dict) and materials and set(materials) <= {"enabled", "lock", "version"}:
                # Switch-only patch (e.g. {"enabled": false} from a share bundle): keep
                # it as a patch so applying the entry only toggles materials instead of
                # resetting the user's parameters to factory values.
                enabled_flag = _coerce_bool(materials.get("enabled", False))
                record["materials"] = {"enabled": bool(enabled_flag)}
            elif isinstance(materials, dict):
                clean_materials, _material_warnings = _materials.normalize_materials(
                    materials
                )
                record["materials"] = clean_materials
            # When importing someone else's theme, keep the author's note/tags too (shown in the panel's "Customize").
            clean_note = _theme_note(note)
            if clean_note:
                record["note"] = clean_note
            clean_tags = _theme_tags(tags)
            if clean_tags:
                record["tags"] = clean_tags
            path = directory / f"{theme_id}.json"
            # Unique temp name + fsync: on power loss / crash there is either a complete entry or nothing,
            # never a half-written JSON that makes the list page report "entry corrupted".
            tmp = directory / f".{theme_id}.{os.getpid()}.{threading.get_ident()}.tmp"
            try:
                with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
                    handle.write(json.dumps(record, ensure_ascii=False, indent=2))
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(tmp, path)
            finally:
                try:
                    if tmp.exists():
                        tmp.unlink()
                except OSError:
                    pass
            return record

    @staticmethod
    def _theme_summary(
        record: dict, *, light: int, dark: int, has_image: bool
    ) -> dict:
        return {
            "id": str(record.get("id") or ""),
            "name": _clip_name(record.get("name"), DEFAULT_THEME_NAME),
            "saved_at": _fmt_saved_at(record.get("saved_at")),
            "light": light,
            "dark": dark,
            "has_image": has_image,
            # The summary must include note/tags too: after a successful "save" the panel renders the card from the
            # return value, and without them "the card lacks the note just written".
            "note": _theme_note(record.get("note")),
            "tags": _theme_tags(record.get("tags")),
        }

    # --------------------------------------------------- share bundle (zip) import/export

    def _guard_export_size(self, data: bytes, *, what: str) -> None:
        """Enforce the size cap ourselves before exporting -- export saying Ok while restore says SdkError is the worst mismatch.

        ``_parse_archive`` / ``_parse_library_archive`` both reject bundles
        over ``ARCHIVE_MAX_BYTES``; if export did not check, users would get a
        backup their own plugin refuses to restore, usually discovered only
        when reinstalling / switching machines.
        """
        size = len(data)
        if size <= ARCHIVE_MAX_BYTES:
            return
        limit = ARCHIVE_MAX_BYTES
        raise SdkError(
            _msg(
                "library.export.tooLarge",
                what=what,
                size=f"{size / 1048576:.1f}",
                limit=f"{limit / 1048576:.0f}",
            )
        )

    def _build_archive(
        self,
        *,
        name: str,
        vars_map: dict,
        dark_map: dict,
        note: Any = None,
        tags: Any = None,
        materials: Any = None,
    ) -> tuple:
        """Build a share bundle: ``theme.json`` (image refs rewritten to ``images/<name>``) + ``images/``."""
        refs = self._image_refs(vars_map) + self._image_refs(dark_map)

        def portable(value: Any) -> Any:
            filename = self._image_filename_in_value(value)
            return f'url("images/{filename}")' if filename else value

        record = {
            "schema": SCHEMA_VERSION,
            "name": name,
            "saved_at": _now_iso(),
            "vars": {key: portable(value) for key, value in vars_map.items()},
            "dark_vars": {key: portable(value) for key, value in dark_map.items()},
        }
        # Share bundles carry materials too: recipients import "a complete look", not just colors.
        # A look with materials explicitly off is shared as the patch {"enabled": false}
        # (the same form built-in non-material presets use): applying it turns the
        # recipient's materials off but keeps their tuned parameters. Omitting the
        # field instead would mean "keep whatever the recipient has".
        if isinstance(materials, dict):
            clean_materials, _material_warnings = _materials.normalize_materials(materials)
            if clean_materials.get("enabled"):
                record["materials"] = clean_materials
            else:
                record["materials"] = {"enabled": False}
        # Pack the author's description/tags too: after import, the panel's "Customize" shows the origin
        # instead of an anonymous, undescribed color scheme.
        clean_note = _theme_note(note)
        if clean_note:
            record["note"] = clean_note
        clean_tags = _theme_tags(tags)
        if clean_tags:
            record["tags"] = clean_tags
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "theme.json", json.dumps(record, ensure_ascii=False, indent=2)
            )
            archive.writestr("README.txt", ARCHIVE_README)
            for filename in sorted({item[1] for item in refs}):
                path = self._backgrounds_dir() / filename
                try:
                    archive.write(path, f"images/{filename}")
                except OSError as exc:  # pragma: no cover
                    self._log("warning", f"打包图片失败（已跳过）{filename}：{exc}")
        payload = buffer.getvalue()
        self._guard_export_size(payload, what=_msg("library.export.whatShareBundle"))
        stamp = datetime.now().strftime("%Y%m%d%H%M%S")
        return f"{_safe_archive_name(name)}-{stamp}.zip", payload

    # ------------------------------------------------------- theme library backup / restore

    def _library_records_for_backup(self) -> list:
        """Read all usable library entries; bad entries are skipped with a warning so one bad file can't ruin the backup."""
        records = []
        seen: set[str] = set()
        for path in sorted(self._library_dir().glob("*.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                self._log("warning", f"备份跳过损坏的仓库条目 {path.name}：{exc}")
                continue
            if not isinstance(raw, dict):
                continue
            if not isinstance(raw.get("vars"), dict) or not isinstance(
                raw.get("dark_vars"), dict
            ):
                continue
            # The file name is the entry's real id (lookups, apply and delete all go by it); the id field
            # inside may have been hand-edited or copied from another entry. Use the field only when the
            # file name itself is unusable.
            theme_id = path.stem
            if not RE_THEME_ID.match(theme_id):
                theme_id = str(raw.get("id") or "").strip()
            if not RE_THEME_ID.match(theme_id) or theme_id in seen:
                self._log("warning", f"备份跳过 id 不合法的仓库条目 {path.name}")
                continue
            seen.add(theme_id)
            record = dict(raw)
            record["id"] = theme_id
            records.append(record)
        return records

    @staticmethod
    def _library_archive_files(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
        """Export and restore share the same file-count and uncompressed-size limits."""
        files = [info for info in archive.infolist() if not info.is_dir()]
        if len(files) > LIBRARY_ARCHIVE_MAX_ENTRIES:
            raise SdkError(
                _msg(
                    "library.backup.tooManyEntries",
                    count=len(files),
                    limit=LIBRARY_ARCHIVE_MAX_ENTRIES,
                )
            )
        total = sum(info.file_size for info in files)
        if total > ARCHIVE_MAX_UNCOMPRESSED:
            raise SdkError(
                _msg(
                    "library.backup.uncompressedTooLarge",
                    total=total,
                    limit=ARCHIVE_MAX_UNCOMPRESSED,
                )
            )
        return files

    def _build_library_archive(self, skipped_images: list | None = None) -> tuple:
        """Pack all custom themes into one zip: ``themes/<id>.json`` + ``images/`` + ``manifest.json``.

        Image refs are rewritten to the portable ``url("images/<name>")`` -- a
        backup must restore on another machine, so local absolute URLs like
        ``/plugin/.../ui/backgrounds/...`` must never be written into it.

        :param skipped_images: optional list that receives the file names of
            referenced background images that could not be packed (deleted or
            unreadable), so the caller can tell the user the backup is incomplete.
        """
        records = self._library_records_for_backup()
        if not records:
            raise SdkError(_msg("library.backup.empty"))
        manifest = {
            "format": LIBRARY_ARCHIVE_FORMAT,
            "schema": SCHEMA_VERSION,
            "exported_at": _now_iso(),
            "themes": [],
        }
        images = set()
        missing_images: set = set()

        def portable(value):
            filename = self._image_filename_in_value(value)
            return f'url("images/{filename}")' if filename else value

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for record in records:
                vars_map = {
                    str(key): portable(value)
                    for key, value in record["vars"].items()
                }
                dark_map = {
                    str(key): portable(value)
                    for key, value in record["dark_vars"].items()
                }
                images.update(item[1] for item in self._image_refs(record["vars"]))
                images.update(item[1] for item in self._image_refs(record["dark_vars"]))
                # _image_refs() only returns files that exist; a theme can still
                # reference a background deleted earlier. That image is missing from
                # the backup as well, so report it like a write failure.
                for mapping in (record["vars"], record["dark_vars"]):
                    for value in (mapping or {}).values():
                        name = self._image_filename_in_value(value)
                        if (
                            name
                            and RE_BG_NAME.match(name)
                            and not (self._backgrounds_dir() / name).is_file()
                        ):
                            missing_images.add(name)
                doc = {
                    "schema": SCHEMA_VERSION,
                    "id": record["id"],
                    "name": _clip_name(record.get("name"), DEFAULT_THEME_NAME),
                    "saved_at": _fmt_saved_at(record.get("saved_at")),
                    "vars": vars_map,
                    "dark_vars": dark_map,
                }
                note = _theme_note(record.get("note"))
                if note:
                    doc["note"] = note
                tags = _theme_tags(record.get("tags"))
                if tags:
                    doc["tags"] = tags
                # Backups must include materials, otherwise "the texture is all gone after restoring a backup".
                if isinstance(record.get("materials"), dict):
                    doc["materials"] = record["materials"]
                archive.writestr(
                    f"themes/{record['id']}.json",
                    json.dumps(doc, ensure_ascii=False, indent=2),
                )
                manifest["themes"].append(
                    {
                        "id": record["id"],
                        "name": doc["name"],
                        "light": len(vars_map),
                        "dark": len(dark_map),
                        "has_image": bool(
                            self._image_refs(record["vars"])
                            or self._image_refs(record["dark_vars"])
                        ),
                    }
                )
            for filename in sorted(images):
                try:
                    archive.write(
                        self._backgrounds_dir() / filename, f"images/{filename}"
                    )
                except OSError as exc:
                    self._log("warning", f"备份图片失败（已跳过）{filename}：{exc}")
                    if skipped_images is not None:
                        skipped_images.append(filename)
            if skipped_images is not None:
                for filename in sorted(missing_images - set(skipped_images)):
                    skipped_images.append(filename)
            archive.writestr(
                "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2)
            )
            archive.writestr("README.txt", LIBRARY_ARCHIVE_README)
            self._library_archive_files(archive)
            count = len(manifest["themes"])
        payload = buffer.getvalue()
        self._guard_export_size(payload, what=_msg("library.export.whatLibraryBackup"))
        stamp = datetime.now().strftime("%Y%m%d%H%M%S")
        return f"neko-theme-studio-library-{stamp}.zip", payload, count

    def _parse_library_archive(self, data: bytes) -> tuple:
        """Parse a library backup bundle; returns ``(items, {image_file_name: bytes})``.

        Each item is ``{"theme": dict, "path": str}`` or ``{"error": message}``:
        a single bad entry is just skipped instead of rejecting the whole
        bundle (the user may have hand-broken only one or two entries).
        """
        if len(data) > ARCHIVE_MAX_BYTES:
            raise SdkError(
                _msg("library.backup.tooLarge", size=len(data), limit=ARCHIVE_MAX_BYTES)
            )
        try:
            archive = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as exc:
            raise SdkError(_msg("library.backup.notZip")) from exc
        items = []
        images = {}
        with archive:
            infos = self._library_archive_files(archive)
            for info in infos:
                name = info.filename.replace("\\", "/")
                if name.startswith("images/"):
                    basename = name.rsplit("/", 1)[-1]
                    if RE_BG_NAME.match(basename):
                        images[basename] = archive.read(info)
            for info in infos:
                name = info.filename.replace("\\", "/")
                if not (name.startswith("themes/") and name.endswith(".json")):
                    continue
                try:
                    raw = json.loads(archive.read(info).decode("utf-8-sig"))
                except (ValueError, UnicodeDecodeError) as exc:
                    items.append(
                        {"error": _msg("library.backup.entryInvalidJson", name=name, error=exc)}
                    )
                    continue
                if not isinstance(raw, dict):
                    items.append({"error": _msg("library.backup.entryNotObject", name=name)})
                    continue
                items.append({"theme": raw, "path": name})
        if not items:
            raise SdkError(_msg("library.backup.noThemes"))
        return items, images

    def _rewrite_archive_refs(
        self, raw: dict, images: dict, *, label: str, from_presets: bool = False
    ) -> dict:
        """Write a share bundle's images to local disk and rewrite refs to absolute URLs.

        With ``from_presets=True``, an image missing from the bundle is looked
        up in ``presets/`` as well -- built-in theme images **ship with the
        plugin**, they are not "missing", and must never be dropped under the
        third-party bundle rules (we hit this: after applying a built-in theme
        once its background vanished, ``backgrounds/`` was collected as an
        orphan, and neither reinstall nor restart brought it back).
        """

        def rewrite(value: Any) -> Any:
            if not isinstance(value, str):
                return value
            match = RE_URL_ANY.search(value)
            if not match:
                return value
            basename = match.group(1).replace("\\", "/").rsplit("/", 1)[-1]
            if not basename:
                return ""
            payload = images.get(basename)
            if payload is None and from_presets:
                # Built-in theme images ship with the package under presets/images/ --
                # the same layout as inside share bundles (both images/<hash>).
                for shipped in (
                    self._presets_dir() / "images" / basename,
                    self._presets_dir() / basename,
                ):
                    if not shipped.is_file():
                        continue
                    try:
                        payload = shipped.read_bytes()
                    except OSError:
                        payload = None
                    else:
                        break
            if payload is None:
                # On a bare JSON import (or a zip without this image), if a file with the same name already exists
                # locally -- the name is the content hash, so same name means same content -- reuse it directly.
                # Otherwise "copy the exported JSON and paste-import it" would silently drop the user's own background
                # image, leaving only colors (a real bug, reproduced).
                if RE_BG_NAME.match(basename) and (
                    self._backgrounds_dir() / basename
                ).is_file():
                    return f'url("{self._background_url(basename)}")'
                # The image really is missing -> drop the whole entry (= fall back to native); never write someone else's path into theme.json.
                self._log("warning", f"分享包里缺少图片 {basename}，该引用已丢弃")
                return ""
            filename = self._save_background_bytes(payload, label=label)
            return f'url("{self._background_url(filename)}")'

        vars_in = raw.get("vars")
        dark_in = raw.get("dark_vars")
        out = {
            "schema": SCHEMA_VERSION,
            "name": _clip_name(raw.get("name"), DEFAULT_THEME_NAME),
            "vars": (
                {str(k): rewrite(v) for k, v in vars_in.items()}
                if isinstance(vars_in, dict)
                else {}
            ),
            "dark_vars": (
                {str(k): rewrite(v) for k, v in dark_in.items()}
                if isinstance(dark_in, dict)
                else {}
            ),
        }
        # Carry the materials section over as-is: it holds only colors/numbers and no image references, so no rewrite.
        # Validity before writing is guaranteed by _materials.normalize_materials.
        if isinstance(raw.get("materials"), dict):
            out["materials"] = raw["materials"]
        # The author's description/tags travel with the share: the panel's "Customize" shows them, and dropping them on
        # import makes "a theme someone shared" look like an anonymous color scheme with no origin (happened before).
        note = _theme_note(raw.get("note"))
        if note:
            out["note"] = note
        tags = _theme_tags(raw.get("tags"))
        if tags:
            out["tags"] = tags
        return out

    def _parse_archive(self, data: bytes, *, label: str) -> tuple:
        """Parse a share bundle (zip or bare JSON); returns ``(palette, source_format)``."""
        if len(data) > ARCHIVE_MAX_BYTES:
            raise SdkError(
                _msg("library.archive.tooLarge", size=len(data), limit=ARCHIVE_MAX_BYTES)
            )
        if data[:4] != b"PK\x03\x04":
            # Bare JSON (someone sends theme.json directly / a hand-trimmed {"vars":{...}}):
            # apply the same structural validation as theme.json inside a zip, then rewrite image references.
            parsed = _parse_theme_json(data)
            return self._rewrite_archive_refs(parsed, {}, label=label), "json"

        try:
            archive = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as exc:
            raise SdkError(_msg("library.archive.notZip")) from exc
        with archive:
            infos = archive.infolist()
            if len(infos) > ARCHIVE_MAX_ENTRIES:
                raise SdkError(
                    _msg(
                        "library.archive.tooManyEntries",
                        count=len(infos),
                        limit=ARCHIVE_MAX_ENTRIES,
                    )
                )
            total = sum(info.file_size for info in infos)
            if total > ARCHIVE_MAX_UNCOMPRESSED:
                raise SdkError(
                    _msg(
                        "library.archive.uncompressedTooLarge",
                        total=total,
                        limit=ARCHIVE_MAX_UNCOMPRESSED,
                    )
                )
            entries = {}
            for info in infos:
                name = info.filename.replace("\\", "/")
                if name.startswith("/") or ".." in name.split("/") or ":" in name:
                    raise SdkError(_msg("library.archive.illegalPath", name=name))
                # Duplicate entries (names colliding after case/slash normalization) let the later one silently override
                # the earlier, which an attacker could use to swap "the validated theme.json" for "another same-named entry".
                # Reject outright; legitimate share bundles never contain duplicates.
                if name in entries:
                    raise SdkError(_msg("library.archive.duplicateName", name=name))
                entries[name] = info.filename

            json_names = [n for n in entries if n.lower().endswith(".json")]
            json_names.sort(
                key=lambda item: (
                    0 if item.rsplit("/", 1)[-1].lower() == "theme.json" else 1,
                    item,
                )
            )
            if not json_names:
                raise SdkError(_msg("library.archive.noThemeJson"))
            try:
                parsed = json.loads(
                    archive.read(entries[json_names[0]]).decode("utf-8", "replace")
                )
            except (ValueError, TypeError) as exc:
                raise SdkError(_msg("library.archive.jsonParseFailed", error=exc)) from exc
            if not isinstance(parsed, dict):
                raise SdkError(_msg("library.archive.jsonNotObject"))

            images = {}
            for name in entries:
                if not name.lower().endswith(IMAGE_SUFFIXES):
                    continue
                images[name.rsplit("/", 1)[-1]] = archive.read(entries[name])
            return self._rewrite_archive_refs(parsed, images, label=label), "zip"

    # ---------------------------------------------------------------- entries

    @plugin_entry(
        id="list_themes",
        name=tr("entries.list_themes.name", default="列出主题仓库"),
        description=tr(
            "entries.list_themes.description",
            default="返回主题仓库里的全部条目：名称、保存时间、亮/暗变量条数、是否含背景图。",
        ),
        input_schema={"type": "object", "properties": {}},
    )
    async def list_themes(self, **_):
        try:
            items = self._preset_items() + self._library_items()
        except Exception as exc:  # noqa: BLE001
            return Err(SdkError(_msg("library.list.failed", error=exc)))
        return Ok({"count": len(items), "themes": items})

    @plugin_entry(
        id="get_schedule",
        name=tr("entries.get_schedule.name", default="读取自动换主题排程"),
        description=tr(
            "entries.get_schedule.description",
            default="返回「按时段自动换主题」的设置：是否启用、四个时段各用哪套主题，以及时段边界。",
        ),
        input_schema={"type": "object", "properties": {}},
    )
    async def get_schedule(self, **_):
        try:
            schedule = self._read_schedule()
        except Exception as exc:  # noqa: BLE001
            return Err(SdkError(_msg("library.schedule.readFailed", error=exc)))
        stamp = datetime.now()
        day_kind = _day_kind_for(stamp.weekday())
        zone = _zone_for_hour(stamp.hour)
        zone_map = self._schedule_zone_map(schedule, day_kind)
        # Zones "still in the schedule but no longer in the library": do not silently go empty, let the panel explain.
        stale = [
            {
                "day_kind": "weekend" if item.get("field") == "weekend_zones" else "weekday",
                "zone": item.get("zone"),
                "theme_id": item.get("theme_id"),
            }
            for item in getattr(self, "_schedule_stale", [])
        ]
        return Ok(
            {
                "schedule": schedule,
                "stale": stale,
                "zones": list(SCHEDULE_ZONES),
                "day_kinds": list(SCHEDULE_DAY_KINDS),
                # The panel renders boundary text like "morning 06:00–10:00"; the backend holds the only copy of the
                # boundaries, so hand them over rather than have the frontend rewrite them (a third copy, bound to drift).
                "windows": _schedule_windows(),
                # The panel shows "weekday or weekend now, which zone, which theme applies"; hand over the decision
                # rather than have the frontend recompute the boundaries (a third implementation).
                "now": {
                    "zone": zone,
                    "hour": stamp.hour,
                    "day_kind": day_kind,
                    "weekend": day_kind == "weekend",
                    "theme_id": str(zone_map.get(zone) or ""),
                },
                "last": getattr(self, "_schedule_last_result", None) or None,
            }
        )

    @plugin_entry(
        id="save_schedule",
        name=tr("entries.save_schedule.name", default="保存自动换主题排程"),
        description=tr(
            "entries.save_schedule.description",
            default="保存「按时段自动换主题」的设置。zones 里的主题 id 必须来自内置主题或主题仓库。",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "enabled": {"type": "boolean"},
                "zones": {"type": "object"},
                "weekend_zones": {"type": "object"},
            },
        },
    )
    async def save_schedule(
        self,
        enabled: Any = None,
        zones: Any = None,
        weekend_zones: Any = None,
        **_,
    ):
        raw = {
            "enabled": enabled if enabled is not None else False,
            "zones": zones if isinstance(zones, dict) else {},
        }
        if weekend_zones is not None:
            raw["weekend_zones"] = weekend_zones if isinstance(weekend_zones, dict) else {}
        else:
            # Field absent = "keep the saved weekend table". Old panels (or windows never refreshed since opening)
            # do not send it when saving, and writing an empty table would wipe the user's configured weekend table.
            try:
                keep = self._read_schedule().get("weekend_zones")
            except Exception:  # noqa: BLE001
                keep = None
            raw["weekend_zones"] = keep if isinstance(keep, dict) else {}
        schedule, errors = self._normalize_schedule(raw, strict=True)
        if errors:
            return Err(
                SdkError(_msg("library.schedule.notSaved", errors="；".join(errors)))
            )
        try:
            with _WRITE_LOCK:
                self._save_schedule_unlocked(schedule)
        except Exception as exc:  # noqa: BLE001
            return Err(SdkError(_msg("library.schedule.saveFailed", error=exc)))
        self._log(
            "info",
            "自动换主题排程已保存：enabled="
            + str(schedule["enabled"])
            + " zones="
            + json.dumps(schedule["zones"], ensure_ascii=False)
            + " weekend_zones="
            + json.dumps(schedule.get("weekend_zones") or {}, ensure_ascii=False),
        )
        # Apply once immediately after saving, otherwise after ticking "enable" the user would see no change until
        # the next zone boundary and assume it did not work. force=True: not suppressed by the last_zone stamp.
        applied = None
        if schedule["enabled"]:
            applied = self._schedule_tick(force=True)
            if applied.get("action") == "applied":
                self._schedule_last_result = applied
        return Ok(
            {
                "saved": True,
                "schedule": schedule,
                "windows": _schedule_windows(),
                "now": {
                    "zone": _zone_for_hour(datetime.now().hour),
                    "hour": datetime.now().hour,
                    "day_kind": _day_kind_for(datetime.now().weekday()),
                },
                "applied": applied,
            }
        )

    @plugin_entry(
        id="save_theme",
        name=tr("entries.save_theme.name", default="保存到主题仓库"),
        description=tr(
            "entries.save_theme.description",
            default=(
                "把当前已保存的调色板存成一个仓库条目，便于以后一键切换或打包分享；"
                "可顺便写一句简介（note）与标签（tags），分享包会一起带走。"
            ),
        ),
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "maxLength": 60},
                "note": {"type": "string", "maxLength": 120},
                "tags": {
                    "type": "array",
                    "maxItems": 4,
                    "items": {"type": "string", "maxLength": 12},
                },
            },
        },
    )
    async def save_theme(
        self,
        name: Any = None,
        note: Any = None,
        tags: Any = None,
        **_,
    ):
        try:
            # Do not wrap _WRITE_LOCK here: ``_save_library_item`` already holds it internally, and another layer outside
            # means locking twice on the same thread -- a non-reentrant lock blocks forever (this really happened: after
            # clicking "save" the whole plugin entry queue hung, every entry timing out at 30s).
            palette = self._read_unlocked(strict=True)
            # Empty-entry guard: the default/reset palette is empty, and saving it to the library only yields a trap entry
            # that "wipes the whole color scheme with one click" (older versions could already produce such entries).
            # What is saved here is the **whole color scheme**; with no usable variable there is nothing to save.
            # Users who only want to save material params use "material presets" (save_material_preset), nothing is lost.
            _clean_vars, _clean_dark, _ignored = _validate_palette(
                palette["vars"], palette["dark_vars"]
            )
            if not _clean_vars and not _clean_dark:
                return Err(
                    SdkError(
                        _msg("library.save.noUsableVars")
                    )
                )
            record = self._save_library_item(
                name=(
                    name
                    if isinstance(name, str) and name.strip()
                    else palette["name"]
                ),
                vars_map=palette["vars"],
                dark_map=palette["dark_vars"],
                # Note/tags can be written at save time: the panel's "save" dialog has a "one-line note", so there is
                # no need to save first and then add it via "edit" -- share bundles carry both as-is.
                note=note,
                tags=tags,
                materials=palette.get("materials"),
            )
        except SdkError as exc:
            return Err(exc)
        except Exception as exc:  # noqa: BLE001
            return Err(SdkError(_msg("library.save.failed", error=exc)))
        summary = self._theme_summary(
            record,
            light=len(record["vars"]),
            dark=len(record["dark_vars"]),
            has_image=bool(
                self._image_refs(record["vars"])
                or self._image_refs(record["dark_vars"])
            ),
        )
        self._log("info", f"已保存到主题仓库：{summary['name']}（{summary['id']}）")
        return Ok({"saved": True, "theme": summary})

    def _apply_theme_record(self, theme_id: str) -> dict[str, Any]:
        """Swap a library / built-in theme wholesale into theme.json and return a summary. **May raise.**

        Extracted because "user clicks a theme" and "background zone switch"
        must share one path: if either side diverges by one line (e.g.
        forgetting _without_missing_images), you get bugs like "switching from
        the panel gives a 404 background while auto-switch works", which only
        half the users can reproduce.
        """
        record = self._load_library_item(theme_id)
        # Built-in theme backgrounds use the portable form (images/<hash>); rewrite to this machine's absolute URL first.
        local = self._rewrite_archive_refs(
            record,
            {},
            label=f"主题 {record.get('name') or theme_id}",
            from_presets=True,
        )
        record = dict(record)
        record["vars"] = local["vars"]
        record["dark_vars"] = local["dark_vars"]
        record["name"] = local["name"]
        with _WRITE_LOCK:
            palette = self._read_unlocked(strict=True)
            clean_vars, clean_dark, errors = _validate_palette(
                record.get("vars"), record.get("dark_vars")
            )
            # A library entry may reference a background image deleted later; writing it into theme.json would only 404.
            clean_vars, gone_light = self._without_missing_images(clean_vars)
            clean_dark, gone_dark = self._without_missing_images(clean_dark)
            for gone in (gone_light, gone_dark):
                for key in gone:
                    errors = list(errors) + [
                        _msg("library.apply.missingBackground", key=key)
                    ]
            if not clean_vars and not clean_dark:
                # Older versions allowed saving an empty palette, so such entries may already sit in the library. Applying
                # one would wipe the user's current colors entirely (the panel just shows "applied"); block it before writing.
                raise SdkError(
                    _msg(
                        "library.apply.emptyEntry",
                        name=record.get("name") or theme_id,
                    )
                )
            palette["vars"] = clean_vars
            palette["dark_vars"] = clean_dark
            palette["name"] = _clip_name(record.get("name"), palette["name"])
            # Old entries without a materials field -> keep the user's current materials untouched (do not reset them to defaults).
            record_materials = record.get("materials")
            # Materials locked: keep the user's hand-tuned set; switching themes does not override it (lock is written by the panel's "follow theme" toggle).
            locked_materials = _materials.normalize_materials(
                palette.get("materials")
            )[0]
            material_applied = False
            # Materials carrying only the switch (built-in "non-material" themes are just {"enabled": false}) are treated
            # as a patch: turn advanced materials off but keep the user's hand-tuned five sections -- re-enabling should
            # not go back to factory defaults.
            material_patch = (
                isinstance(record_materials, dict)
                and set(record_materials) <= {"enabled", "lock", "version"}
            )
            if isinstance(record_materials, dict) and locked_materials.get("lock"):
                self._log(
                    "info",
                    f"材质已锁定，保留当前设置（主题 {record.get('name') or theme_id} 的材质未应用）",
                )
            elif material_patch:
                (
                    merged_materials,
                    material_warnings,
                ) = _materials.apply_materials(locked_materials, record_materials)
                palette["materials"] = merged_materials
                material_applied = True
                for message in material_warnings:
                    self._log("warning", f"主题材质设置被修正 —— {message}")
            elif isinstance(record_materials, dict):
                (
                    imported_materials,
                    material_warnings,
                ) = _materials.normalize_materials(record_materials)
                # The preset library is an asset library, not part of "this param set": merge by name when applying an
                # entry, and local-only custom presets must be kept. With wholesale replacement, one click on "apply an
                # older theme with materials" would wipe all the user's saved presets.
                merged_custom = dict(locked_materials.get("custom_presets") or {})
                merged_custom.update(imported_materials.get("custom_presets") or {})
                imported_materials["custom_presets"] = merged_custom
                raw_preset = str(imported_materials.get("preset") or "").strip()
                if (
                    raw_preset
                    and raw_preset not in _materials.PRESET_IDS
                    and raw_preset in merged_custom
                ):
                    imported_materials["preset"] = raw_preset
                palette["materials"] = imported_materials
                material_applied = True
                for message in material_warnings:
                    self._log("warning", f"主题材质设置被修正 —— {message}")
            palette["enabled"] = True
            palette["updated_at"] = _now_iso()
            palette["schema"] = SCHEMA_VERSION
            self._write_unlocked(palette)
        return {
            "theme": self._theme_summary(
                record,
                light=len(clean_vars),
                dark=len(clean_dark),
                has_image=bool(
                    self._image_refs(clean_vars) or self._image_refs(clean_dark)
                ),
            ),
            "light": len(clean_vars),
            "dark": len(clean_dark),
            "rejected": errors,
            # The panel picks its toast text from this: the theme's own materials were applied (True) / skipped due to lock (False).
            "material_applied": material_applied,
            "material_present": isinstance(record_materials, dict),
            "material_locked": bool(locked_materials.get("lock")),
            # Whether advanced materials are on or off after applying: lets the panel distinguish "materials changed" from "materials turned off".
            "material_enabled": bool(
                (palette.get("materials") or {}).get("enabled")
                if isinstance(palette.get("materials"), dict)
                else False
            ),
        }

    @plugin_entry(
        id="apply_saved_theme",
        name=tr("entries.apply_saved_theme.name", default="切换到仓库主题"),
        description=tr(
            "entries.apply_saved_theme.description",
            default="把仓库里某个主题整体替换为当前调色板（含背景图），并写入 theme.json。",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "theme_id": {"type": "string", "maxLength": 64},
                # The panel sends id (more natural), CLI/scripts often use theme_id. Accept both so callers
                # need not guess the param name -- accepting only theme_id once made the panel's
                # "apply/share/delete" all fail with "invalid entry id".
                "id": {"type": "string", "maxLength": 64},
            },
        },
    )
    async def apply_saved_theme(self, theme_id: Any = "", id: Any = "", **_):
        theme_id = theme_id or id
        try:
            outcome = self._apply_theme_record(theme_id)
        except SdkError as exc:
            return Err(exc)
        except Exception as exc:  # noqa: BLE001
            return Err(SdkError(_msg("library.apply.failed", error=exc)))
        rejected = outcome["rejected"]
        if rejected:
            self._log("warning", f"apply_saved_theme 拒绝 {len(rejected)} 条：{rejected[:3]}")
        return Ok(
            {
                "applied": True,
                "theme": outcome["theme"],
                "light": outcome["light"],
                "dark": outcome["dark"],
                "rejected": rejected,
                "material_applied": outcome.get("material_applied", False),
                "material_present": outcome.get("material_present", False),
                "material_locked": outcome.get("material_locked", False),
                "material_enabled": outcome.get("material_enabled", False),
            }
        )

    @plugin_entry(
        id="delete_theme",
        name=tr("entries.delete_theme.name", default="删除仓库主题"),
        description=tr(
            "entries.delete_theme.description",
            default="从主题仓库删除一个条目（不影响当前正在使用的调色板）。",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "theme_id": {"type": "string", "maxLength": 64},
                # The panel sends id (more natural), CLI/scripts often use theme_id. Accept both so callers
                # need not guess the param name -- accepting only theme_id once made the panel's
                # "apply/share/delete" all fail with "invalid entry id".
                "id": {"type": "string", "maxLength": 64},
            },
        },
    )
    async def delete_theme(self, theme_id: Any = "", id: Any = "", **_):
        # Decide/locate by **the id in the request**: the file name is the library's primary key (_library_items uses
        # it too); the record's "id" field is just file content and, if hand-edited, points at another theme.
        requested = str(theme_id or id or "").strip()
        if not RE_THEME_ID.fullmatch(requested):
            return Err(SdkError(_msg("library.delete.invalidId", theme_id=repr(requested))))
        try:
            with _WRITE_LOCK:
                self._trash_library_entry_unlocked(requested)
        except SdkError as exc:
            return Err(exc)
        except Exception as exc:  # noqa: BLE001
            return Err(SdkError(_msg("library.delete.failed", error=exc)))
        return Ok({"deleted": True, "theme_id": requested, "undo": True})

    def _trash_library_entry_unlocked(self, requested: str) -> None:
        """Move one library entry into the recycle bin. Caller holds ``_WRITE_LOCK``.

        Raises ``SdkError`` for refusals (built-in, outside the library, missing)
        and lets IO errors propagate so callers report them per entry.
        """
        if self._preset_path(requested).is_file():
            raise SdkError(_msg("library.delete.builtin"))
        directory = self._library_dir()
        trash = self._trash_dir()
        path = directory / f"{requested}.json"
        if path.is_symlink() or directory.is_symlink() or trash.is_symlink():
            raise SdkError(_msg("library.delete.outsideDir"))
        if not path.is_file():
            raise SdkError(_msg("library.entry.notFound", theme_id=requested))
        # The body is only read for its display name: a damaged entry can still be deleted.
        record = _trash.read_record(path)
        # The schedule is only read to remember which zones used this
        # theme (for restore); this path never writes it, so a corrupt or
        # locked schedule.json must not block the delete.
        try:
            schedule = self._read_schedule(drop_missing=False, strict=True)
            refs = {
                key: {zone: theme for zone, theme in schedule[key].items() if theme == requested}
                for key in ("zones", "weekend_zones")
            }
        except (OSError, ValueError) as exc:
            self._log("warning", f"Schedule unreadable, deleting without zone refs: {exc}")
            refs = {"zones": {}, "weekend_zones": {}}
        trash.mkdir(parents=True, exist_ok=True)
        now = datetime.now()
        # The stamp orders the bin (newest first, pruning drops the oldest). The clock can be
        # coarse (about 15 ms on some Windows builds), so keep it strictly after the newest batch.
        existing = _trash.batches(trash)
        latest = existing[0][0] if existing else ""
        stamp = now.strftime("%Y%m%d-%H%M%S-%f")
        if stamp <= latest:
            fmt = "%Y%m%d-%H%M%S-%f" if latest.count("-") == 2 else "%Y%m%d-%H%M%S"
            try:
                later = datetime.strptime(latest, fmt) + timedelta(microseconds=1)
                stamp = later.strftime("%Y%m%d-%H%M%S-%f")
            except ValueError:
                pass  # A hand-made name that is not a real date; ordering is best effort.
        base = requested + "__" + stamp
        body = trash / (base + ".json")
        sidecar = trash / (base + ".meta.json")
        if body.exists() or sidecar.exists():
            raise FileExistsError(base)
        # Sidecar first: if the move then fails, it is removed again and the entry stays in the library.
        _write_json_atomic(sidecar, {
            "id": requested, "name": _clip_name(record.get("name"), requested),
            "deleted_at": now.isoformat(), "schedule": refs,
        })
        try:
            path.replace(body)
        except OSError:
            sidecar.unlink(missing_ok=True)
            raise
        for _, _, old_body, old_sidecar in _trash.batches(trash)[TRASH_LIMIT:]:
            try:
                _trash.remove_batch(old_body, old_sidecar)
            except OSError as exc:
                self._log("warning", f"Could not prune theme trash: {exc}")

    @plugin_entry(
        id="delete_themes",
        name=tr("entries.delete_themes.name", default="批量删除仓库主题"),
        description=tr(
            "entries.delete_themes.description",
            default="把多个仓库主题一起移进回收站；内置主题和不存在的条目逐个报告失败。",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "theme_ids": {
                    "type": "array",
                    "items": {"type": "string", "maxLength": 64},
                    "maxItems": TRASH_LIMIT,
                },
            },
            "required": ["theme_ids"],
        },
    )
    async def delete_themes(self, theme_ids: Any = None, **_):
        """Move several library entries to the recycle bin in one locked pass.

        Each id is handled like ``delete_theme``; refusals and IO errors are
        reported per id instead of aborting the batch.
        """
        if not isinstance(theme_ids, list) or not theme_ids:
            return Err(SdkError(_msg("library.deleteMany.invalid")))
        # The trash keeps TRASH_LIMIT batches: a larger request would prune part of
        # itself while the panel promises every entry can be restored, so refuse it.
        if len(theme_ids) > TRASH_LIMIT:
            return Err(SdkError(_msg("library.deleteMany.tooMany", limit=TRASH_LIMIT)))
        deleted: list[str] = []
        failed: list[dict[str, str]] = []
        seen: set[str] = set()
        with _WRITE_LOCK:
            for raw in theme_ids:
                requested = str(raw or "").strip() if isinstance(raw, str) else ""
                if requested in seen:
                    continue
                seen.add(requested)
                if not RE_THEME_ID.fullmatch(requested):
                    failed.append({"id": requested, "error": _msg("library.delete.invalidId", theme_id=repr(requested))})
                    continue
                try:
                    self._trash_library_entry_unlocked(requested)
                    deleted.append(requested)
                except SdkError as exc:
                    failed.append({"id": requested, "error": str(exc)})
                except Exception as exc:  # noqa: BLE001
                    failed.append({"id": requested, "error": _msg("library.delete.failed", error=exc)})
        return Ok({"deleted": deleted, "failed": failed, "undo": bool(deleted)})

    @plugin_entry(
        id="restore_theme",
        name=tr("entries.restore_theme.name", default="恢复已删除的主题"),
        description=tr(
            "entries.restore_theme.description",
            default="从回收站恢复仓库主题，并把仍然空着的排程时段重新指向它。",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "theme_id": {"type": "string", "maxLength": 64},
                "id": {"type": "string", "maxLength": 64},
            },
        },
    )
    async def restore_theme(self, theme_id: Any = "", id: Any = "", **_):
        requested = str(theme_id or id or "").strip()
        if not RE_THEME_ID.fullmatch(requested):
            return Err(SdkError(_msg("library.entry.invalidId")))
        restored_zones: list[str] = []
        try:
            with _WRITE_LOCK:
                directory = self._library_dir()
                trash = self._trash_dir()
                target = directory / f"{requested}.json"
                if directory.is_symlink() or target.is_symlink() or trash.is_symlink():
                    raise SdkError(_msg("library.trash.outsideDir"))
                if target.exists() or self._preset_path(requested).exists():
                    raise SdkError(_msg("library.trash.exists"))
                match = next(
                    (
                        (body, sidecar)
                        for _, theme, body, sidecar in _trash.batches(trash)
                        if theme == requested and not body.is_symlink() and body.is_file()
                    ),
                    None,
                )
                if match is None:
                    raise SdkError(_msg("library.trash.missing"))
                directory.mkdir(parents=True, exist_ok=True)
                if len(list(directory.glob("*.json"))) >= LIBRARY_MAX_ITEMS:
                    raise SdkError(_msg("library.full", limit=LIBRARY_MAX_ITEMS))
                body, sidecar = match
                meta = _trash.read_record(sidecar)
                refs = meta.get("schedule")
                schedule = previous = None
                if isinstance(refs, dict) and any(refs.values()):
                    # Read-modify-write: strict, so an unreadable schedule fails the restore
                    # (retryable) instead of being overwritten with defaults.
                    schedule = self._read_schedule(drop_missing=False, strict=True)
                    previous = {
                        **schedule,
                        "zones": dict(schedule["zones"]),
                        "weekend_zones": dict(schedule["weekend_zones"]),
                    }
                    for key in ("zones", "weekend_zones"):
                        bucket = refs.get(key)
                        if not isinstance(bucket, dict):
                            continue
                        for zone in SCHEDULE_ZONES:
                            # Only refill zones that are still free; a zone the user re-assigned meanwhile is kept.
                            if bucket.get(zone) == requested and not schedule[key].get(zone):
                                schedule[key][zone] = requested
                                restored_zones.append(f"{key}.{zone}")
                # Restored references are ignored until the library body reappears.
                # If the process exits between the two writes, the user can retry this same restore.
                if restored_zones:
                    self._save_schedule_unlocked(schedule)
                try:
                    body.replace(target)
                except OSError:
                    if restored_zones:
                        self._save_schedule_unlocked(previous)
                    raise
                # A leftover sidecar is harmless (it is pruned / cleared with its batch); the body is already back.
                try:
                    sidecar.unlink(missing_ok=True)
                except OSError as exc:
                    self._log("warning", f"Could not remove restored theme metadata: {exc}")
        except SdkError as exc:
            return Err(exc)
        except (ValueError, OSError) as exc:
            return Err(SdkError(_msg("library.trash.failed", error=exc)))
        name = meta.get("name")
        return Ok(
            {
                "restored": True,
                "theme_id": requested,
                "name": name if isinstance(name, str) and name else requested,
                "schedule_restored": restored_zones,
            }
        )

    @plugin_entry(
        id="clear_trash",
        name=tr("entries.clear_trash.name", default="清空回收站"),
        description=tr("entries.clear_trash.description", default="永久清除回收站中的主题。"),
        input_schema={"type": "object", "properties": {}},
    )
    async def clear_trash(self, **_):
        removed = total = 0
        try:
            with _WRITE_LOCK:
                for _, _, body, sidecar in _trash.batches(self._trash_dir()):
                    count, size = _trash.remove_batch(body, sidecar)
                    removed += count
                    total += size
        except OSError as exc:
            return Err(SdkError(_msg("library.trash.failed", error=exc)))
        return Ok({"removed": removed, "bytes": total, "count": 0})

    @plugin_entry(
        id="update_theme_meta",
        name=tr("entries.update_theme_meta.name", default="修改主题信息"),
        description=tr(
            "entries.update_theme_meta.description",
            default="改仓库里一套主题的名称 / 备注 / 标签。条目 id 不变（收藏与按时段排程都靠它认主题）。",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "theme_id": {"type": "string", "maxLength": 64},
                "id": {"type": "string", "maxLength": 64},
                "name": {"type": "string", "maxLength": 60},
                "note": {"type": "string", "maxLength": 120},
                "tags": {
                    "type": "array",
                    "items": {"type": "string", "maxLength": 12},
                    "maxItems": 4,
                },
            },
        },
    )
    async def update_theme_meta(
        self,
        theme_id: Any = "",
        id: Any = "",
        name: Any = None,
        note: Any = None,
        tags: Any = None,
        **_,
    ):
        # The whole read-modify-write runs under the lock: reading outside it let a
        # concurrent delete (move to trash) or save land between the read and the
        # write, and the stale payload would then resurrect or clobber the entry.
        with _WRITE_LOCK:
            return self._update_theme_meta_unlocked(theme_id or id, name, note, tags)

    def _update_theme_meta_unlocked(self, theme_id: Any, name: Any, note: Any, tags: Any):
        """Edit only name / note / tags; **never change id, vars, dark_vars or saved_at**.

        Why the id must not follow the name: the id is the key shared by
        favorites (panel localStorage), the zone schedule (schedule.json) and
        the library backup manifest. Changing it on rename would turn the
        favorited theme and the scheduled theme into dangling references --
        users would see "I favorited it, where did it go?" and "the scheduled
        theme does not exist". So once generated the id is fixed; renaming
        only touches display fields.

        Tags: a list of strings (or a comma-separated string from the CLI) is
        normalised by ``_theme_tags`` (trim, dedupe, at most 4 x 12 chars); an
        empty list clears them. Any other shape, or a list holding non-strings,
        is refused instead of being read as "clear all tags".
        """
        if name is None and note is None and tags is None:
            return Err(SdkError(_msg("library.updateMeta.nothingToChange")))
        try:
            record = self._load_library_item(theme_id)
            # Same as the delete entry: the file name is the primary key, the record's "id" is just content.
            record_id = str(theme_id or "").strip()
            if not RE_THEME_ID.match(record_id):
                raise SdkError(
                    _msg("library.updateMeta.invalidId", theme_id=repr(record_id))
                )
            if self._preset_path(record_id).is_file():
                raise SdkError(_msg("library.updateMeta.builtin"))
            directory = self._library_dir().resolve()
            path = (directory / f"{record_id}.json").resolve()
            if path.parent != directory:
                raise SdkError(_msg("library.updateMeta.outsideDir"))
            if not path.is_file():
                raise SdkError(_msg("library.entry.notFound", theme_id=record_id))

            payload = dict(record)
            changed = []

            if name is not None:
                if not isinstance(name, str) or not name.strip():
                    raise SdkError(_msg("library.updateMeta.emptyName"))
                label = _clip_name(name, DEFAULT_THEME_NAME)
                if label != str(payload.get("name") or ""):
                    changed.append("name")
                payload["name"] = label

            if note is not None:
                clean_note = _theme_note(note)
                if clean_note:
                    if clean_note != str(payload.get("note") or ""):
                        changed.append("note")
                    payload["note"] = clean_note
                else:
                    # Empty string = clear the note; no empty field is kept in the entry, so the panel sees "no note".
                    if "note" in payload:
                        changed.append("note")
                    payload.pop("note", None)

            if tags is not None:
                if isinstance(tags, str):
                    # The panel sends an array; a hand-written comma-separated string from the CLI is accepted too, so callers need not guess the format.
                    tags = [part for part in re.split(r"[,，、;；\s]+", tags) if part]
                if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
                    raise SdkError(_msg("library.updateMeta.invalidTags"))
                clean_tags = _theme_tags(tags)
                if clean_tags:
                    if clean_tags != list(payload.get("tags") or []):
                        changed.append("tags")
                    payload["tags"] = clean_tags
                else:
                    if "tags" in payload:
                        changed.append("tags")
                    payload.pop("tags", None)

            if not changed:
                return Ok(
                    {
                        "updated": False,
                        "changed": [],
                        "theme": self._theme_summary(
                            payload,
                            light=len(payload.get("vars") or {}),
                            dark=len(payload.get("dark_vars") or {}),
                            has_image=bool(
                                self._image_refs(payload.get("vars") or {})
                                or self._image_refs(payload.get("dark_vars") or {})
                            ),
                        ),
                    }
                )

            _write_json_atomic(path, payload)
        except SdkError as exc:
            return Err(exc)
        except Exception as exc:  # noqa: BLE001
            return Err(SdkError(_msg("library.updateMeta.failed", error=exc)))

        self._log(
            "info",
            f"主题 {record_id} 已更新：{', '.join(changed)} → {payload.get('name')}",
        )
        return Ok(
            {
                "updated": True,
                "changed": changed,
                "theme_id": record_id,
                "name": payload.get("name"),
                "note": payload.get("note") or "",
                "tags": list(payload.get("tags") or []),
            }
        )

    @plugin_entry(
        id="import_theme_archive",
        name=tr("entries.import_theme_archive.name", default="导入主题分享包"),
        description=tr(
            "entries.import_theme_archive.description",
            default="导入主题分享包（zip：theme.json + images/，也接受裸 JSON），存进仓库。",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "filename": {"type": "string", "maxLength": 200},
                "data": {"type": "string", "description": "分享包内容的 base64"},
            },
            "required": ["data"],
        },
    )
    async def import_theme_archive(self, filename: str = "", data: str = "", **_):
        if not isinstance(data, str) or not data.strip():
            return Err(SdkError(_msg("library.import.emptyData")))
        raw = data.strip()
        if len(raw) > ARCHIVE_MAX_BYTES * 2:
            return Err(SdkError(_msg("library.import.dataTooLarge")))
        if raw.startswith("data:"):
            # Tolerate a data URL prefix (the panel normally strips it, manual calls may include it).
            _, _, raw = raw.partition(",")
        try:
            payload = base64.b64decode(raw, validate=True)
        except Exception:  # noqa: BLE001 - binascii.Error is a subclass of ValueError
            return Err(SdkError(_msg("library.import.invalidBase64")))
        label = f"来自 {filename or '分享包'}"
        try:
            theme, source_format = self._parse_archive(payload, label=label)
            clean_vars, clean_dark, errors = _validate_palette(
                theme.get("vars"), theme.get("dark_vars")
            )
            # Other people's JSON often carries "background image URLs from their machine" (plugin era:
            # http://127.0.0.1:48916/plugin/.../backgrounds/..., now /api/theme_studio/...),
            # which are 404 references here. Drop them before storing, so the entry does not falsely claim "has background".
            clean_vars, gone_light = self._without_missing_images(clean_vars)
            clean_dark, gone_dark = self._without_missing_images(clean_dark)
            for gone in (gone_light, gone_dark):
                for key in gone:
                    errors = list(errors) + [
                        _msg("library.import.missingBackground", key=key)
                    ]
            if not clean_vars and not clean_dark:
                raise SdkError(_msg("library.import.noUsableVars"))
            record = self._save_library_item(
                name=theme.get("name"),
                vars_map=clean_vars,
                dark_map=clean_dark,
                note=theme.get("note"),
                tags=theme.get("tags"),
                materials=theme.get("materials"),
            )
        except SdkError as exc:
            return Err(exc)
        except Exception as exc:  # noqa: BLE001
            return Err(SdkError(_msg("library.import.failed", error=exc)))
        if errors:
            self._log(
                "warning", f"import_theme_archive 拒绝 {len(errors)} 条：{errors[:3]}"
            )
        summary = self._theme_summary(
            record,
            light=len(clean_vars),
            dark=len(clean_dark),
            has_image=bool(
                self._image_refs(clean_vars) or self._image_refs(clean_dark)
            ),
        )
        return Ok(
            {
                "imported": True,
                "theme": summary,
                "source_format": source_format,
                "rejected": errors,
            }
        )

    @plugin_entry(
        id="export_theme_archive",
        name=tr("entries.export_theme_archive.name", default="导出主题分享包"),
        description=tr(
            "entries.export_theme_archive.description",
            default="把仓库条目打成 zip 分享包（theme.json + images/），返回 base64 供面板下载。",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "theme_id": {"type": "string", "maxLength": 64},
                # The panel sends id (more natural), CLI/scripts often use theme_id. Accept both so callers
                # need not guess the param name -- accepting only theme_id once made the panel's
                # "apply/share/delete" all fail with "invalid entry id".
                "id": {"type": "string", "maxLength": 64},
            },
        },
    )
    async def export_theme_archive(self, theme_id: Any = "", id: Any = "", **_):
        theme_id = theme_id or id
        try:
            record = self._load_library_item(theme_id)
            local = self._rewrite_archive_refs(
                record,
                {},
                label=f"主题 {record.get('name') or theme_id}",
                from_presets=True,
            )
            record = dict(record)
            record["vars"] = local["vars"]
            record["dark_vars"] = local["dark_vars"]
            record["name"] = local["name"]
            clean_vars, clean_dark, _errors = _validate_palette(
                record.get("vars"), record.get("dark_vars")
            )
            name = _clip_name(record.get("name"), DEFAULT_THEME_NAME)
            filename, payload = self._build_archive(
                name=name,
                vars_map=clean_vars,
                dark_map=clean_dark,
                note=record.get("note"),
                tags=record.get("tags"),
                materials=local.get("materials"),
            )
        except SdkError as exc:
            return Err(exc)
        except Exception as exc:  # noqa: BLE001
            return Err(SdkError(_msg("library.export.failed", error=exc)))
        images = len(self._image_refs(clean_vars)) + len(self._image_refs(clean_dark))
        return Ok(
            {
                "filename": filename,
                "data": base64.b64encode(payload).decode("ascii"),
                "bytes": len(payload),
                "images": images,
            }
        )

    @staticmethod
    def _download_filename(filename: str) -> str:
        """Sanitised download name that keeps its extension.

        The whitelisted extension is split off the raw name first and only the
        stem goes through ``_safe_archive_name`` (which clips to 40 characters),
        so a long name can never turn ``.json`` into ``.j``.
        """
        raw_stem, raw_suffix = os.path.splitext(str(filename or ""))
        suffix = raw_suffix.lower() if raw_suffix.lower() in (".zip", ".json", ".png") else ""
        stem = _safe_archive_name(raw_stem if suffix else str(filename or ""))
        stem = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._-]+", "-", stem).strip("-") or "theme"
        return stem + suffix

    def _current_or_saved(self, theme_id: str):
        """Use the current palette when theme_id is empty, otherwise the library / built-in entry.

        Returns (name, vars, dark, materials). Materials must come along --
        exporting only colors without materials would give the user half a
        theme.
        """
        if theme_id:
            record = self._load_library_item(theme_id)
            local = self._rewrite_archive_refs(
                record,
                {},
                label=f"主题 {record.get('name') or theme_id}",
                from_presets=True,
            )
            clean_vars, clean_dark, _errors = _validate_palette(
                local.get("vars"), local.get("dark_vars")
            )
            return (
                _clip_name(local.get("name"), DEFAULT_THEME_NAME),
                clean_vars,
                clean_dark,
                local.get("materials"),
            )
        palette = self._normalize(self._read_unlocked())
        clean_vars, clean_dark, _errors = _validate_palette(
            palette.get("vars"), palette.get("dark_vars")
        )
        return (
            _clip_name(palette.get("name"), DEFAULT_THEME_NAME),
            clean_vars,
            clean_dark,
            palette.get("materials"),
        )

    @plugin_entry(
        id="import_library",
        name=tr("entries.import_library.name", default="恢复主题库备份"),
        description=tr(
            "entries.import_library.description",
            default="导入主题库备份包（zip：themes/*.json + images/ + manifest.json），并进仓库。只新增，不覆盖现有主题。",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "filename": {"type": "string", "maxLength": 200},
                "data": {"type": "string", "description": "备份包内容的 base64"},
            },
            "required": ["data"],
        },
    )
    async def import_library(self, filename: str = "", data: str = "", **_):
        if not isinstance(data, str) or not data.strip():
            return Err(SdkError(_msg("library.restore.emptyData")))
        raw = data.strip()
        if raw.startswith("data:"):
            _, _, raw = raw.partition(",")
        if len(raw) > ARCHIVE_MAX_BYTES * 2:
            return Err(SdkError(_msg("library.restore.dataTooLarge")))
        try:
            payload = base64.b64decode(raw, validate=True)
        except Exception:  # noqa: BLE001
            return Err(SdkError(_msg("library.restore.invalidBase64")))
        try:
            items, images = self._parse_library_archive(payload)
        except SdkError as exc:
            return Err(exc)
        except Exception as exc:  # noqa: BLE001
            return Err(SdkError(_msg("library.restore.parseFailed", error=exc)))

        imported = []
        skipped = []
        for item in items:
            if "error" in item:
                skipped.append(
                    {
                        "name": item.get("path") or _msg("library.restore.unknownEntry"),
                        "reason": item["error"],
                    }
                )
                continue
            theme = item["theme"]
            label = (
                _clip_name(theme.get("name"), DEFAULT_THEME_NAME)
                or item.get("path")
                or _msg("library.restore.fallbackName")
            )
            try:
                local = self._rewrite_archive_refs(
                    theme, images, label=f"主题库备份 · {label}"
                )
                clean_vars, clean_dark, _errors = _validate_palette(
                    local.get("vars"), local.get("dark_vars")
                )
                if not clean_vars and not clean_dark:
                    raise SdkError(_msg("library.restore.noUsableVars"))
                record = self._save_library_item(
                    name=label,
                    vars_map=clean_vars,
                    dark_map=clean_dark,
                    note=local.get("note"),
                    tags=local.get("tags"),
                    materials=local.get("materials"),
                )
            except SdkError as exc:
                skipped.append({"name": label, "reason": str(exc)})
                continue
            except Exception as exc:  # noqa: BLE001
                skipped.append(
                    {"name": label, "reason": _msg("library.restore.importFailed", error=exc)}
                )
                continue
            imported.append(
                self._theme_summary(
                    record,
                    light=len(record["vars"]),
                    dark=len(record["dark_vars"]),
                    has_image=bool(
                        self._image_refs(record["vars"])
                        or self._image_refs(record["dark_vars"])
                    ),
                )
            )

        if not imported:
            reason = (
                skipped[0]["reason"]
                if skipped
                else _msg("library.restore.nothingInBundle")
            )
            return Err(SdkError(_msg("library.restore.allFailed", reason=reason)))
        self._log(
            "info",
            f"已恢复主题库备份 {filename or '（无文件名）'}："
            f"{len(imported)} 套成功，{len(skipped)} 套跳过，{len(images)} 张图片",
        )
        return Ok(
            {
                "imported": len(imported),
                "themes": imported,
                "skipped": skipped,
                "images": len(images),
                "filename": filename,
            }
        )

    @plugin_entry(
        id="export_to_file",
        name=tr("entries.export_to_file.name", default="导出到文件"),
        description=tr(
            "entries.export_to_file.description",
            default="把当前主题、仓库条目或整个主题库打包成文件内容（base64）返回，由面板触发浏览器下载；服务端不往桌面 / 下载 / 家目录等位置写导出文件。",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": ["zip", "json", "library"],
                    "description": "zip/json = 一套主题；library = 库里所有自定义主题打成一个备份包",
                },
                "theme_id": {"type": "string", "maxLength": 64},
                "id": {"type": "string", "maxLength": 64},
                "name": {"type": "string", "maxLength": 60},
            },
        },
    )
    async def export_to_file(
        self,
        kind: str = "zip",
        theme_id: Any = "",
        id: Any = "",
        name: str = "",
        **_,
    ):
        theme_id = theme_id or id or ""
        # Param type guard: the panel always sends strings, but the entry is public (scripts can call it too).
        # Calling .strip() on a non-string raises AttributeError -- the error should be an Err, not an uncaught exception.
        for _label, _raw in (("kind", kind), ("name", name)):
            if _raw is not None and not isinstance(_raw, str):
                return Err(
                    SdkError(
                        _msg(
                            "library.exportFile.paramNotString",
                            param=_label,
                            type=type(_raw).__name__,
                        )
                    )
                )
        kind = (kind or "zip").strip().lower()
        if kind not in ("zip", "json", "library"):
            return Err(
                SdkError(_msg("library.exportFile.unsupportedKind", kind=kind))
            )
        # Now built in, the panel is an ordinary top-level page where browser downloads work: the server only produces
        # bytes and the panel triggers the download. Export files are no longer written to the desktop / Downloads /
        # home / exports/ (that was a workaround for the plugin-iframe era without allow-downloads; built in, it would
        # only leave files the user does not know about).
        # When exporting a built-in theme, _current_or_saved may still cache the bundled background into backgrounds/
        # by content hash: that is the app's own data directory, idempotent and subject to orphan cleanup, not an export artifact.
        if kind == "library":
            # A library backup needs no "current theme": one bundle holds all custom themes in the library + shared images.
            skipped_images: list = []
            try:
                filename, payload, count = self._build_library_archive(
                    skipped_images=skipped_images
                )
            except SdkError as exc:
                return Err(exc)
            except Exception as exc:  # noqa: BLE001
                return Err(SdkError(_msg("library.backup.failed", error=exc)))
            filename = self._download_filename(filename)
            self._log("info", f"已打包主题库备份 {filename}（{count} 套 / {len(payload)} 字节）")
            return Ok(
                {
                    "filename": filename,
                    "data": base64.b64encode(payload).decode("ascii"),
                    "mime": "application/zip",
                    "bytes": len(payload),
                    "kind": kind,
                    "themes": count,
                    # Background images that could not be packed: non-zero means the backup is
                    # incomplete and the panel should say so instead of reporting full success.
                    "skipped_images": len(skipped_images),
                }
            )

        try:
            theme_name, vars_map, dark_map, materials_map = self._current_or_saved(
                str(theme_id).strip()
            )
        except SdkError as exc:
            return Err(exc)
        except Exception as exc:  # noqa: BLE001
            return Err(SdkError(_msg("library.exportFile.readFailed", error=exc)))
        if name and name.strip():
            theme_name = _clip_name(name.strip(), theme_name)

        try:
            if kind == "zip":
                filename, payload = self._build_archive(
                    name=theme_name,
                    vars_map=vars_map,
                    dark_map=dark_map,
                    materials=materials_map,
                )
            else:
                # Exporting the current theme keeps its on/off state (the copied
                # JSON from export_theme does too); a library entry exports enabled.
                enabled_flag = True
                if not str(theme_id).strip():
                    enabled_flag = self._read_unlocked().get("enabled") is not False
                doc = {
                    "schema": SCHEMA_VERSION,
                    "name": theme_name,
                    "enabled": enabled_flag,
                    "updated_at": _now_iso(),
                    "vars": vars_map,
                    "dark_vars": dark_map,
                }
                # Same materials shape as theme_json and the ZIP bundle: the full
                # tree only when enabled, the switch-only patch {"enabled": false}
                # when explicitly off (import then keeps the recipient's tuned
                # sections), and nothing when the source has no materials.
                if isinstance(materials_map, dict) and materials_map:
                    clean_materials = _materials.normalize_materials(materials_map)[0]
                    doc["materials"] = (
                        clean_materials if clean_materials.get("enabled") else {"enabled": False}
                    )
                payload = json.dumps(doc, ensure_ascii=False, indent=2).encode("utf-8")
                filename = f"{_safe_archive_name(theme_name)}.json"
        except SdkError as exc:
            return Err(exc)
        except Exception as exc:  # noqa: BLE001
            return Err(SdkError(_msg("library.exportFile.packFailed", error=exc)))

        filename = self._download_filename(filename)
        self._log("info", f"已打包导出文件 {filename}（{len(payload)} 字节）")
        return Ok(
            {
                "filename": filename,
                "data": base64.b64encode(payload).decode("ascii"),
                "mime": "application/zip" if kind == "zip" else "application/json",
                "bytes": len(payload),
                "kind": kind,
                "images": len(self._image_refs(vars_map)) + len(self._image_refs(dark_map)),
            }
        )

    @staticmethod
    def _share_card_png(name: str, vars_map: dict, dark_map: dict, materials: Any) -> bytes:
        """Render the share card PNG; the material badge shows only when materials are on."""
        # Imported lazily so Pillow is only loaded when a card is actually requested.
        from .share_card import render_share_card

        material: dict = {}
        if isinstance(materials, dict) and materials:
            clean = _materials.normalize_materials(materials)[0]
            if clean.get("enabled"):
                material = clean
        return render_share_card(
            name,
            vars_map,
            dark_map,
            preset_label=_materials.preset_label(material["preset"]) if material.get("preset") else "",
            quality_label=_materials.quality_label(material["quality"]) if material.get("quality") else "",
        )

    @plugin_entry(
        id="export_share_card",
        name=tr("entries.export_share_card.name", default="下载主题预览图"),
        description=tr(
            "entries.export_share_card.description",
            default="把当前主题或仓库 / 内置条目渲染成 PNG 预览图，以 base64 返回，由面板触发浏览器下载；服务端不写导出文件。",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "theme_id": {"type": "string", "maxLength": 64},
                "id": {"type": "string", "maxLength": 64},
                "name": {"type": "string", "maxLength": 60},
            },
        },
    )
    async def export_share_card(
        self,
        theme_id: Any = "",
        id: Any = "",
        name: str = "",
        **_,
    ):
        # Same browser-download contract as export_to_file: bytes go back to the panel,
        # nothing is written to the desktop / Downloads / home directory.
        if name is not None and not isinstance(name, str):
            return Err(
                SdkError(
                    _msg(
                        "library.exportFile.paramNotString",
                        param="name",
                        type=type(name).__name__,
                    )
                )
            )
        theme_id = str(theme_id or id or "").strip()
        try:
            theme_name, vars_map, dark_map, materials_map = self._current_or_saved(theme_id)
        except SdkError as exc:
            return Err(exc)
        except Exception as exc:  # noqa: BLE001
            return Err(SdkError(_msg("library.exportFile.readFailed", error=exc)))
        if name and name.strip():
            theme_name = _clip_name(name.strip(), theme_name)

        try:
            from .share_card import CARD_H, CARD_W

            payload = self._share_card_png(theme_name, vars_map, dark_map, materials_map)
        except Exception as exc:  # noqa: BLE001
            return Err(SdkError(_msg("shareCard.renderFailed", error=exc)))

        filename = self._download_filename(f"{_safe_archive_name(theme_name)}.png")
        self._log("info", f"已生成主题预览图 {filename}（{len(payload)} 字节）")
        return Ok(
            {
                "filename": filename,
                "data": base64.b64encode(payload).decode("ascii"),
                "mime": "image/png",
                "bytes": len(payload),
                "kind": "png",
                "width": CARD_W,
                "height": CARD_H,
                "theme": theme_name,
            }
        )

    @plugin_entry(
        id="import_background",
        name=tr("entries.import_background.name", default="上传聊天背景图"),
        description=tr(
            "entries.import_background.description",
            default="保存背景图到插件 static/backgrounds/，返回可写进 --nts-chat-bg-image 的绝对 URL。",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "filename": {"type": "string", "maxLength": 200},
                "data": {"type": "string", "description": "图片内容的 base64"},
            },
            "required": ["data"],
        },
    )
    async def import_background(self, filename: str = "", data: str = "", **_):
        if not isinstance(data, str) or not data.strip():
            return Err(SdkError(_msg("library.background.emptyData")))
        raw = data.strip()
        if raw.startswith("data:"):
            _, _, raw = raw.partition(",")
        if len(raw) > IMAGE_MAX_BYTES * 2:
            return Err(SdkError(_msg("library.background.dataTooLarge")))
        try:
            payload = base64.b64decode(raw, validate=True)
        except Exception:  # noqa: BLE001
            return Err(SdkError(_msg("library.background.invalidBase64")))
        try:
            saved = self._save_background_bytes(
                payload, label=f"来自 {filename or '面板'}"
            )
        except SdkError as exc:
            return Err(exc)
        except Exception as exc:  # noqa: BLE001
            return Err(SdkError(_msg("library.background.saveFailed", error=exc)))
        return Ok(
            {
                "url": self._background_url(saved),
                "filename": saved,
                "bytes": len(payload),
                "kind": _image_extension(payload) or "unknown",
            }
        )
