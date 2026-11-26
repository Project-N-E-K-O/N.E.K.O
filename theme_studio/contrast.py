"""Offline WCAG contrast audit for the built-in theme presets.

Each entry in ``PAIRS`` names a text colour and the surface it is actually
painted on. The surface is rebuilt by compositing (CSS source-over) the same
layers, in the same order, that ``static/theme-studio/loader/neko-theme-studio.css``
paints them:

* Window surfaces (top bar, input, buttons, panels, code): the element's own
  background colour over ``--nts-chat-bg`` (``.chat-window`` /
  ``#react-chat-window-root``).
* Message list (``.chat-body`` / ``.message-list``): ``--nts-chat-panel-bg`` as
  background-color over the window, then ``--nts-chat-gradient`` as
  background-image above it (the "J Gradient" rules).
* Chat bubbles (``.message-bubble-user`` / ``.message-bubble-assistant``),
  stacked over the message list:

  1. background-color: ``--nts-bubble-glass-tint`` when set (the "J Liquid
     glass" rules outrank the plain bubble rules), otherwise
     ``--nts-user-bubble-bg`` / ``--nts-ai-bubble-bg``;
  2. background-image: ``--nts-user-bubble-gradient`` /
     ``--nts-ai-bubble-gradient`` painted above that colour. The
     "background-image kill" rules for a set bubble bg or glass tint only
     clear the chat window's built-in default gradient; their selectors are
     less specific than the gradient rules, so a theme gradient still paints.

* Compact chat history bubbles (``.compact-export-history-bubble``): the
  same text colour, but the glass tint rules do not reach them, so the stack
  is always ``--nts-*-bubble-bg`` then ``--nts-*-bubble-gradient`` over the
  message list. A preset must read well in both layouts.

* Tool bubbles: ``--nts-tool-bubble-bg`` over the message list.

* Floating and in-message elements, each on its own background variable:

  - Modal (``.modal-dialog``, ``--nts-modal-text``): ``--nts-modal-bg`` over
    ``--nts-overlay-bg`` (``.modal-overlay``) over the window. Dialogs open
    over the host page, whose colour is unknown, so the window background
    stands in for it.
  - Popover (``.composer-overflow-popover``, ``--nts-popover-text``):
    ``--nts-popover-bg`` over the window (it floats above the composer).
  - Recommendation (``--nts-rec-text``): the loader only gives this colour
    to the compact prompt (``.proactive-recommendation-bubble``), while
    ``--nts-rec-bg`` only paints the full-chat card; the React chat renders
    one presentation or the other, never both. The prompt sits in
    ``.compact-music-recommendation-prompt``, whose background no theme
    variable reaches, so the text is checked on that fixed host colour
    (``COMPACT_PROMPT_BG``, from frontend/react-neko-chat/src/styles.css).
  - Chip (``.system-chip``, ``--nts-chip-text``): ``--nts-chip-bg`` over the
    message list, where system messages render. The same colours also paint
    composer chips (galgame options, visit bar badge) over the window.
  - Status (``.message-block-status``, ``--nts-status-text``):
    ``--nts-status-bg`` over the assistant bubble, since status blocks render
    inside message bubbles.

Gradients are reduced to the mean of their colour stops, because text runs
across the whole gradient. This is an approximation: background images, the
bubble veil, backdrop filters and the material (liquid glass) layers are not
modelled, and a translucent ``--nts-chat-bg`` is treated as if nothing were
behind it.

A pair is skipped when its text or background variable is unset: the loader
then leaves the host stylesheet's own colour in place, which this audit cannot
see.
"""

from __future__ import annotations

import glob
import json
import os
import re

COLOR_RE = re.compile(r"#[0-9a-fA-F]{3,8}|rgba?\([^)]*\)")
NUM_RE = re.compile(r"-?\d*\.?\d+")

WHITE = (255, 255, 255, 1.0)

# Surface kinds used in PAIRS; see the module docstring for the layer stacks.
WINDOW = "window"
MESSAGE_LIST = "message-list"
USER_BUBBLE = "user-bubble"
AI_BUBBLE = "ai-bubble"
COMPACT_USER_BUBBLE = "compact-user-bubble"
COMPACT_AI_BUBBLE = "compact-ai-bubble"
MODAL = "modal"
AI_BUBBLE_BLOCK = "ai-bubble-block"
COMPACT_PROMPT = "compact-prompt"

# Background of .compact-music-recommendation-prompt in the React chat
# stylesheet (light: rgba(255, 255, 255, .96); dark: #252d3d). Themes do not
# restyle it, so it is a fixed surface per mode.
COMPACT_PROMPT_BG = {
    "light": (255, 255, 255, 0.96),
    "dark": (37, 45, 61, 1.0),
}

# (label, text key, background key, surface kind, minimum contrast ratio)
PAIRS: list[tuple[str, str, str | None, str, float]] = [
    ("topbar", "--nts-topbar-text", "--nts-topbar-bg", WINDOW, 4.5),
    ("input", "--nts-input-text", "--nts-input-bg", WINDOW, 4.5),
    ("input-placeholder", "--nts-input-placeholder", "--nts-input-bg", WINDOW, 3.0),
    ("button", "--nts-button-text", "--nts-button-bg", WINDOW, 4.5),
    ("send-button", "--nts-send-text", "--nts-send-bg", WINDOW, 4.5),
    ("user-bubble", "--nts-user-bubble-text", "--nts-user-bubble-bg", USER_BUBBLE, 4.5),
    ("ai-bubble", "--nts-ai-bubble-text", "--nts-ai-bubble-bg", AI_BUBBLE, 4.5),
    ("compact-user-bubble", "--nts-user-bubble-text", "--nts-user-bubble-bg",
     COMPACT_USER_BUBBLE, 4.5),
    ("compact-ai-bubble", "--nts-ai-bubble-text", "--nts-ai-bubble-bg", COMPACT_AI_BUBBLE, 4.5),
    ("panel-text", "--nts-panel-text", "--nts-panel-bg", WINDOW, 4.5),
    ("panel-subtext", "--nts-panel-text-sub", "--nts-panel-bg", WINDOW, 3.0),
    ("link", "--nts-link", "--nts-ai-bubble-bg", AI_BUBBLE, 4.5),
    ("modal-text", "--nts-modal-text", "--nts-modal-bg", MODAL, 4.5),
    ("popover-text", "--nts-popover-text", "--nts-popover-bg", WINDOW, 4.5),
    ("recommendation-text", "--nts-rec-text", None, COMPACT_PROMPT, 4.5),
    ("chip-text", "--nts-chip-text", "--nts-chip-bg", MESSAGE_LIST, 4.5),
    ("status-text", "--nts-status-text", "--nts-status-bg", AI_BUBBLE_BLOCK, 3.0),
    ("tool-bubble", "--nts-tool-bubble-text", "--nts-tool-bubble-bg", MESSAGE_LIST, 4.5),
    ("code-text", "--nts-code-text", "--nts-code-bg", WINDOW, 4.5),
]


def parse_color(value):
    """Parse ``#rgb[a]`` / ``#rrggbb[aa]`` / ``rgb[a](...)`` into ``(r, g, b, a)``.

    Returns None for anything that is not a concrete colour.
    """
    if not value or not isinstance(value, str):
        return None
    text = value.strip().lower()
    if text in ("none", "transparent", "inherit", "currentcolor", "auto"):
        return None
    if text.startswith("#"):
        body = text[1:]
        if len(body) in (3, 4):
            body = "".join(ch * 2 for ch in body)
        if len(body) not in (6, 8):
            return None
        try:
            r, g, b = int(body[0:2], 16), int(body[2:4], 16), int(body[4:6], 16)
            a = int(body[6:8], 16) / 255 if len(body) == 8 else 1.0
        except ValueError:
            return None
        return (r, g, b, a)
    if text.startswith("rgb"):
        nums = NUM_RE.findall(text.split("(", 1)[-1])
        if len(nums) < 3:
            return None
        r, g, b = float(nums[0]), float(nums[1]), float(nums[2])
        a = 1.0
        if len(nums) >= 4:
            a = float(nums[3])
        elif "/" in text:
            tail = text.rsplit("/", 1)[-1].strip().rstrip(")")
            try:
                a = float(tail)
            except ValueError:
                a = 1.0
        if a > 1.0:
            a /= 100.0
        return (int(round(r)), int(round(g)), int(round(b)), max(0.0, min(1.0, a)))
    return None


def average_color(value):
    """Return a single colour, or the mean of all colour stops of a gradient."""
    if not value or not isinstance(value, str):
        return None
    parsed = [p for p in (parse_color(tok) for tok in COLOR_RE.findall(value)) if p]
    if not parsed:
        return None
    if len(parsed) == 1 and "gradient" not in value:
        return parsed[0]
    r = sum(x[0] for x in parsed) / len(parsed)
    g = sum(x[1] for x in parsed) / len(parsed)
    b = sum(x[2] for x in parsed) / len(parsed)
    a = sum(x[3] for x in parsed) / len(parsed)
    return (int(round(r)), int(round(g)), int(round(b)), a)


def over(top, bottom):
    """Composite ``top`` over ``bottom`` (CSS source-over)."""
    if bottom is None:
        return top
    ta = top[3]
    if ta >= 0.999:
        return top
    ba = bottom[3]
    out_a = ta + ba * (1 - ta)
    if out_a <= 0:
        return (0, 0, 0, 0.0)
    return (
        int(round((top[0] * ta + bottom[0] * ba * (1 - ta)) / out_a)),
        int(round((top[1] * ta + bottom[1] * ba * (1 - ta)) / out_a)),
        int(round((top[2] * ta + bottom[2] * ba * (1 - ta)) / out_a)),
        min(1.0, out_a),
    )


def lum(color):
    """WCAG relative luminance of an sRGB colour."""

    def channel(v):
        v = v / 255.0
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4

    return 0.2126 * channel(color[0]) + 0.7152 * channel(color[1]) + 0.0722 * channel(color[2])


def contrast(fg, bg):
    """WCAG contrast ratio between two colours (1.0 to 21.0)."""
    l1, l2 = lum(fg), lum(bg)
    if l1 < l2:
        l1, l2 = l2, l1
    return (l1 + 0.05) / (l2 + 0.05)


def mode_vars(doc, mode):
    """Light and dark declarations are scoped to mutually exclusive loader selectors."""
    return dict(doc.get("dark_vars" if mode == "dark" else "vars") or {})


def _layer(vars_map, key, base):
    """Paint the colour (or averaged gradient) under ``key`` over ``base``, if set."""
    color = average_color(vars_map.get(key))
    return over(color, base) if color else base


def window_base(vars_map):
    """The chat window background, ``--nts-chat-bg`` (white when unset)."""
    return average_color(vars_map.get("--nts-chat-bg", "#ffffff")) or WHITE


def message_list_base(vars_map):
    """The message list: chat panel colour, then the chat gradient, over the window."""
    base = _layer(vars_map, "--nts-chat-panel-bg", window_base(vars_map))
    return _layer(vars_map, "--nts-chat-gradient", base)


def resolve_surface(vars_map, kind, bg_key, mode="light"):
    """Composite the full layer stack the text of a pair is painted on."""
    if kind == COMPACT_PROMPT:
        # The prompt floats over the page; its own near-opaque colour decides.
        return over(COMPACT_PROMPT_BG.get(mode, COMPACT_PROMPT_BG["light"]), WHITE)
    if kind == WINDOW:
        return _layer(vars_map, bg_key, window_base(vars_map))
    if kind == MESSAGE_LIST:
        return _layer(vars_map, bg_key, message_list_base(vars_map))
    if kind == MODAL:
        base = _layer(vars_map, "--nts-overlay-bg", window_base(vars_map))
        return _layer(vars_map, bg_key, base)
    if kind == AI_BUBBLE_BLOCK:
        return _layer(vars_map, bg_key, resolve_surface(vars_map, AI_BUBBLE, "--nts-ai-bubble-bg"))
    role = "user" if kind in (USER_BUBBLE, COMPACT_USER_BUBBLE) else "ai"
    base = message_list_base(vars_map)
    if kind in (COMPACT_USER_BUBBLE, COMPACT_AI_BUBBLE):
        base = _layer(vars_map, f"--nts-{role}-bubble-bg", base)
        return _layer(vars_map, f"--nts-{role}-bubble-gradient", base)
    tint = average_color(vars_map.get("--nts-bubble-glass-tint"))
    base = over(tint, base) if tint else _layer(vars_map, f"--nts-{role}-bubble-bg", base)
    return _layer(vars_map, f"--nts-{role}-bubble-gradient", base)


def audit_vars(vars_map, mode="light"):
    """Check every pair in ``PAIRS`` against one resolved variable map.

    ``mode`` selects the fixed host surfaces (``COMPACT_PROMPT_BG``); pairs
    with no background variable (``bg_key`` None) are always checked.
    """
    rows = []
    for label, fg_key, bg_key, kind, floor in PAIRS:
        fg = average_color(vars_map.get(fg_key))
        bg = average_color(vars_map.get(bg_key)) if bg_key else True
        if fg is None or bg is None:
            rows.append({"pair": label, "skip": True})
            continue
        ratio = contrast(fg, resolve_surface(vars_map, kind, bg_key, mode))
        rows.append(
            {
                "pair": label,
                "fg": fg_key,
                "bg": bg_key or kind,
                "ratio": round(ratio, 2),
                "floor": floor,
                "ok": ratio + 1e-9 >= floor,
            }
        )
    return rows


def themes_from_dir(path, limit=None):
    """Load ``(theme_id, document)`` for every preset JSON in ``path``."""
    files = sorted(glob.glob(os.path.join(path, "*.json")))
    if limit:
        files = files[: int(limit)]
    out = []
    for file_path in files:
        with open(file_path, "r", encoding="utf-8-sig") as fh:
            doc = json.load(fh)
        out.append((doc.get("id") or os.path.basename(file_path), doc))
    return out


def failing(themes):
    """Return ``(fails, pair_checks)``; each failure is ``(theme_id, mode, row)``."""
    fails = []
    pair_checks = 0
    for theme_id, doc in themes:
        for mode in ("light", "dark"):
            for row in audit_vars(mode_vars(doc, mode), mode):
                if row.get("skip"):
                    continue
                pair_checks += 1
                if not row["ok"]:
                    fails.append((theme_id, mode, row))
    return fails, pair_checks


def audit_dir(path, limit=None):
    """Audit every preset in ``path``; returns ``(themes, fails, pair_checks)``."""
    themes = themes_from_dir(path, limit)
    fails, pair_checks = failing(themes)
    return themes, fails, pair_checks
