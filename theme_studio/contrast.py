"""Offline WCAG contrast audit for the built-in theme presets.

Each entry in ``PAIRS`` (and ``TEXT_GRADIENT_PAIRS``) names a text colour
and the surface it is actually painted on. The surface is rebuilt by
compositing (CSS source-over) the same layers, in the same order, that
``static/theme-studio/loader/neko-theme-studio.css`` paints them:

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
  - Recommendation card (full chat): ``--nts-rec-bg`` over the message list,
    under the host's own ``--recommendation-text`` (the loader does not
    recolour the card text), so the fixed host text colour
    (``REC_CARD_TEXT``) is checked against the theme's card background.
  - Chip (``.system-chip``, ``--nts-chip-text``): ``--nts-chip-bg`` over the
    message list, where system messages render. The same colours also paint
    composer chips (galgame options, visit bar badge) over the window.
  - Status (``.message-block-status``, ``--nts-status-text``):
    ``--nts-status-bg`` over the assistant bubble, since status blocks render
    inside message bubbles.

* Text gradient (``--nts-text-gradient``): the loader clips this gradient to
  the glyphs of ``.message-block-text``, ``.message-block-text-plain`` and
  ``.message-block-markdown`` (``background-clip: text`` with a transparent
  text fill; ``pre`` / ``code`` inside them keep their solid colour). Those
  blocks render in every message container, so the gradient replaces the
  solid text colour on the user and assistant bubbles, the compact history
  bubbles, tool bubbles and system chips. Each of those surfaces is checked
  in ``TEXT_GRADIENT_PAIRS`` against every colour stop of the gradient (a
  translucent stop is composited over the surface first), and the row
  reports the worst stop: any part of a line can land on any stop.

Background gradients are reduced to the mean of their colour stops, because
text runs across the whole gradient. This is an approximation: background
images, the bubble veil, backdrop filters and the material (liquid glass)
layers are not modelled, and a translucent ``--nts-chat-bg`` is treated as if
nothing were behind it.

A pair is skipped when its text or background variable is unset: the loader
then leaves the host stylesheet's own colour in place, which this audit cannot
see. Text gradient pairs are likewise skipped when ``--nts-text-gradient`` is
unset (the solid text pairs above then apply) or when their surface's
background variable is unset.
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

# --recommendation-text of the full-chat .proactive-recommendation card in the
# React chat stylesheet (light #344b5d; dark #e2e9ee). The loader paints the
# card with --nts-rec-bg but leaves this text colour alone.
REC_CARD_TEXT = {
    "light": (52, 75, 93, 1.0),
    "dark": (226, 233, 238, 1.0),
}

# (label, text key, background key, surface kind, minimum contrast ratio).
# A text key of None means the host's fixed text colour for that surface
# (see FIXED_TEXT); a background key of None means a fixed host surface.
PAIRS: list[tuple[str, str | None, str | None, str, float]] = [
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
    ("recommendation-card", None, "--nts-rec-bg", MESSAGE_LIST, 4.5),
    ("chip-text", "--nts-chip-text", "--nts-chip-bg", MESSAGE_LIST, 4.5),
    ("status-text", "--nts-status-text", "--nts-status-bg", AI_BUBBLE_BLOCK, 3.0),
    ("tool-bubble", "--nts-tool-bubble-text", "--nts-tool-bubble-bg", MESSAGE_LIST, 4.5),
    ("code-text", "--nts-code-text", "--nts-code-bg", WINDOW, 4.5),
]

# Host text colours for pairs whose text key is None, keyed by pair label.
FIXED_TEXT = {"recommendation-card": REC_CARD_TEXT}

TEXT_GRADIENT = "--nts-text-gradient"

# Surfaces the text gradient is painted on: (label, background key, surface
# kind, minimum contrast ratio). Every colour stop is checked separately.
TEXT_GRADIENT_PAIRS: list[tuple[str, str, str, float]] = [
    ("text-gradient-user", "--nts-user-bubble-bg", USER_BUBBLE, 4.5),
    ("text-gradient-ai", "--nts-ai-bubble-bg", AI_BUBBLE, 4.5),
    ("text-gradient-compact-user", "--nts-user-bubble-bg", COMPACT_USER_BUBBLE, 4.5),
    ("text-gradient-compact-ai", "--nts-ai-bubble-bg", COMPACT_AI_BUBBLE, 4.5),
    ("text-gradient-tool", "--nts-tool-bubble-bg", MESSAGE_LIST, 4.5),
    ("text-gradient-chip", "--nts-chip-bg", MESSAGE_LIST, 4.5),
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


def color_stops(value):
    """Return every concrete colour stop in ``value``; non-colour tokens are ignored."""
    if not value or not isinstance(value, str):
        return []
    return [p for p in (parse_color(tok) for tok in COLOR_RE.findall(value)) if p]


def average_color(value):
    """Return a single colour, or the mean of all colour stops of a gradient."""
    if not value or not isinstance(value, str):
        return None
    parsed = color_stops(value)
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
    with no background variable (``bg_key`` None) are always checked. The
    ``TEXT_GRADIENT_PAIRS`` rows follow, in the same shape, with the worst
    colour stop's ratio.
    """
    rows = []
    for label, fg_key, bg_key, kind, floor in PAIRS:
        if fg_key is None:
            fixed = FIXED_TEXT.get(label) or {}
            fg = fixed.get(mode, fixed.get("light"))
        else:
            fg = average_color(vars_map.get(fg_key))
        bg = average_color(vars_map.get(bg_key)) if bg_key else True
        if fg is None or bg is None:
            rows.append({"pair": label, "skip": True})
            continue
        surface = resolve_surface(vars_map, kind, bg_key, mode)
        rows.append(_row(label, fg_key, bg_key, surface, contrast(fg, surface), floor))
    stops = color_stops(vars_map.get(TEXT_GRADIENT))
    for label, bg_key, kind, floor in TEXT_GRADIENT_PAIRS:
        if not stops or average_color(vars_map.get(bg_key)) is None:
            rows.append({"pair": label, "skip": True})
            continue
        surface = resolve_surface(vars_map, kind, bg_key, mode)
        ratio = min(contrast(over(stop, surface), surface) for stop in stops)
        rows.append(_row(label, TEXT_GRADIENT, bg_key, surface, ratio, floor))
    return rows


def _row(label, fg_key, bg_key, surface, ratio, floor):
    return {
        "pair": label,
        "fg": fg_key,
        "bg": bg_key,
        # Hex of the resolved surface, so callers can show a swatch
        # even for fixed host surfaces that have no variable.
        "surface": "#%02x%02x%02x" % tuple(surface[:3]),
        "ratio": round(ratio, 2),
        "floor": floor,
        "ok": ratio + 1e-9 >= floor,
    }


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
