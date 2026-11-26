"""Render a portable PNG share card from a validated theme palette.

The card is a 1200x630 preview (a common social-preview size) showing the
theme name, its material preset / quality tier, six key colour swatches and a
small accent orb. It is rendered entirely with Pillow, so it needs no browser
and no network access.

Fonts: the card prefers the font bundled with the chat UI (Yozai), then the
usual CJK-capable system fonts of Windows, macOS and Linux, and finally
Pillow's built-in font. Each piece of text uses the first available font that
actually has glyphs for all of its characters, so a Korean or Russian label is
not drawn as boxes just because the first font is Chinese-only. When no font
covers the text the first available one is used anyway (missing glyphs then
render as boxes); drawing never raises because of the text.

Callers run this synchronously; Theme Studio entries already execute in a
worker thread (``asyncio.to_thread``), so the event loop is never blocked.
"""

from __future__ import annotations

import functools
import io
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from PIL import Image, ImageDraw, ImageFont

from .contrast import average_color
from .i18n import msg

CARD_W = 1200
CARD_H = 630

_RENDER_LOCK = threading.Lock()

_APP_ROOT = Path(__file__).resolve().parent.parent
_WIN_FONTS = Path(os.environ.get("WINDIR") or r"C:\Windows") / "Fonts"


def _build_font_candidates() -> tuple[tuple[str, int], ...]:
    """Font files to try, in priority order, as ``(path, ttc_index)``.

    Paths that do not exist on the current platform are simply skipped, so
    one list serves every OS.
    """
    paths = [
        # Bundled with the React chat UI: built output (shipped under static/)
        # first, then the source copy for development checkouts.
        _APP_ROOT / "static" / "react" / "neko-chat" / "assets" / "Yozai-Medium.ttf",
        _APP_ROOT / "frontend" / "react-neko-chat" / "public" / "assets" / "Yozai-Medium.ttf",
        # Windows
        _WIN_FONTS / "msyhbd.ttc",
        _WIN_FONTS / "msyh.ttc",
        _WIN_FONTS / "simhei.ttf",
        _WIN_FONTS / "malgun.ttf",
        _WIN_FONTS / "segoeui.ttf",
        _WIN_FONTS / "arialbd.ttf",
        _WIN_FONTS / "arial.ttf",
        # macOS
        Path("/System/Library/Fonts/PingFang.ttc"),
        Path("/System/Library/Fonts/Hiragino Sans GB.ttc"),
        Path("/System/Library/Fonts/STHeiti Medium.ttc"),
        Path("/System/Library/Fonts/STHeiti Light.ttc"),
        Path("/System/Library/Fonts/AppleSDGothicNeo.ttc"),
        Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
        Path("/Library/Fonts/Arial Unicode.ttf"),
        # Linux (Debian/Ubuntu, Fedora, Arch layouts)
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/google-noto-cjk/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"),
        Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
        Path("/usr/share/fonts/wenquanyi/wqy-microhei/wqy-microhei.ttc"),
        Path("/usr/share/fonts/wenquanyi/wqy-zenhei/wqy-zenhei.ttc"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/TTF/DejaVuSans.ttf"),
        Path("/usr/share/fonts/dejavu/DejaVuSans.ttf"),
    ]
    return tuple((str(path), 0) for path in paths)


_FONT_CANDIDATES: tuple[tuple[str, int], ...] = _build_font_candidates()

#: A code point no candidate font maps; it renders as the font's ".notdef" glyph.
_MISSING_PROBE = "\U0010fffd"


@functools.lru_cache(maxsize=1)
def _available_fonts() -> tuple[tuple[str, int], ...]:
    """Candidates that exist and load. Resolved once per process (see :func:`reset_font_cache`)."""
    found = []
    for path, index in _FONT_CANDIDATES:
        try:
            if not os.path.isfile(path):
                continue
            ImageFont.truetype(path, 16, index=index)
        except Exception:  # noqa: BLE001 - an unreadable font file is just skipped
            continue
        found.append((path, index))
    return tuple(found)


def reset_font_cache() -> None:
    """Forget resolved fonts (tests swap the candidate list)."""
    _available_fonts.cache_clear()
    _load_font.cache_clear()
    _face_for.cache_clear()


@functools.lru_cache(maxsize=128)
def _load_font(path: str, index: int, size: int):
    return ImageFont.truetype(path, size, index=index)


def _default_font(size: int):
    """Pillow's own font: scalable when FreeType is available, else the bitmap font."""
    try:
        return ImageFont.load_default(size=size)
    except Exception:  # noqa: BLE001 - Pillow built without FreeType
        return ImageFont.load_default()


def _glyph_bytes(font, ch: str) -> bytes:
    side = max(8, int(getattr(font, "size", 16)) * 2)
    canvas = Image.new("L", (side, side), 0)
    ImageDraw.Draw(canvas).text((0, 0), ch, font=font, fill=255)
    return canvas.tobytes()


def _covers(font, text: str) -> bool:
    """Whether ``font`` has a real glyph for every visible character of ``text``.

    Pillow exposes no cmap lookup, so a character counts as missing when it
    renders exactly like the ``.notdef`` glyph of an unmapped code point.
    """
    try:
        missing = _glyph_bytes(font, _MISSING_PROBE)
        for ch in set(text):
            if ch.isspace():
                continue
            if _glyph_bytes(font, ch) == missing:
                return False
        return True
    except Exception:  # noqa: BLE001 - treat any rendering trouble as "not covered"
        return False


@functools.lru_cache(maxsize=256)
def _face_for(text: str) -> tuple[str, int] | None:
    """First available font file that covers ``text`` (or the first available one)."""
    fonts = _available_fonts()
    for path, index in fonts:
        try:
            if _covers(_load_font(path, index, 24), text):
                return path, index
        except Exception:  # noqa: BLE001
            continue
    return fonts[0] if fonts else None


def _font(size: int, text: str = ""):
    face = _face_for(text)
    if face is not None:
        try:
            return _load_font(face[0], face[1], size)
        except Exception:  # noqa: BLE001
            pass
    return _default_font(size)


def _safe_text(font, text: str) -> str:
    """Text the font can be asked to draw; the bitmap fallback only takes Latin-1."""
    if isinstance(font, ImageFont.FreeTypeFont):
        return text
    return text.encode("latin-1", "replace").decode("latin-1")


def _text_w(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    try:
        box = draw.textbbox((0, 0), _safe_text(font, text), font=font)
    except Exception:  # noqa: BLE001
        box = draw.textbbox((0, 0), text.encode("ascii", "replace").decode("ascii"), font=font)
    return box[2] - box[0]


def _draw_text(draw: ImageDraw.ImageDraw, xy, text: str, font, fill) -> None:
    try:
        draw.text(xy, _safe_text(font, text), font=font, fill=fill)
    except Exception:  # noqa: BLE001 - never let a glyph problem fail the whole card
        draw.text(xy, text.encode("ascii", "replace").decode("ascii"), font=font, fill=fill)


def blend(top: tuple[int, int, int, int], bottom: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """Composite a translucent foreground onto an opaque swatch."""
    a = top[3] / 255.0
    return (
        int(round(top[0] * a + bottom[0] * (1 - a))),
        int(round(top[1] * a + bottom[1] * (1 - a))),
        int(round(top[2] * a + bottom[2] * (1 - a))),
        255,
    )


def mix(a: tuple[int, int, int, int], b: tuple[int, int, int, int], t: float) -> tuple[int, int, int, int]:
    """Linear interpolation between two RGBA colours (``t`` clamped to 0..1)."""
    t = max(0.0, min(1.0, t))
    return (
        int(round(a[0] + (b[0] - a[0]) * t)),
        int(round(a[1] + (b[1] - a[1]) * t)),
        int(round(a[2] + (b[2] - a[2]) * t)),
        int(round(a[3] + (b[3] - a[3]) * t)),
    )


def _clamp8(value: float) -> int:
    return max(0, min(255, int(round(value))))


def _pick(vars_map: dict[str, Any], keys: Iterable[str], fallback: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """First variable among ``keys`` that resolves to a colour (gradients use their mean stop)."""
    for key in keys:
        got = average_color(vars_map.get(key))
        if got is not None:
            r, g, b, alpha = got
            return _clamp8(r), _clamp8(g), _clamp8(b), _clamp8(255 * alpha)
    return fallback


def _fit_font(draw: ImageDraw.ImageDraw, text: str, start: int, floor: int, max_w: int):
    size = start
    while size > floor:
        font = _font(size, text)
        if _text_w(draw, text, font) <= max_w:
            return font
        size -= 2
    return _font(floor, text)


def _rounded(draw: ImageDraw.ImageDraw, box, radius: int, fill, outline=None, width: int = 1) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def _clip(text: str, limit: int = 22) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def render_share_card(
    name: str,
    vars_map: dict[str, Any],
    dark_map: dict[str, Any] | None = None,
    *,
    quality_label: str = "",
    preset_label: str = "",
) -> bytes:
    """Render the card as PNG bytes.

    Uses the light palette, falling back to the dark one when the theme only
    defines dark colours. Renders are serialised: the cached FreeType faces are
    shared between worker threads and FreeType faces are not thread-safe.
    """
    with _RENDER_LOCK:
        return _render(
            name, vars_map, dark_map, quality_label=quality_label, preset_label=preset_label
        )


def _render(
    name: str,
    vars_map: dict[str, Any],
    dark_map: dict[str, Any] | None,
    *,
    quality_label: str,
    preset_label: str,
) -> bytes:
    dark_map = dark_map or {}
    base_vars = {k: v for k, v in (vars_map or {}).items() if v is not None}
    if not base_vars:
        base_vars = {k: v for k, v in dark_map.items() if v is not None}

    accent = _pick(base_vars, ("--nts-accent", "--nts-panel-glow-color"), (95, 184, 232, 255))
    chat_bg = _pick(base_vars, ("--nts-chat-bg",), (242, 250, 255, 255))
    panel_bg = _pick(base_vars, ("--nts-panel-bg",), (250, 253, 255, 255))
    ai_bg = _pick(base_vars, ("--nts-ai-bubble-bg",), (255, 255, 255, 199))
    ai_text = _pick(base_vars, ("--nts-ai-bubble-text", "--nts-input-text"), (35, 68, 89, 255))
    rim = _pick(base_vars, ("--nts-glass-rim", "--nts-panel-border"), (168, 216, 240, 179))
    user_bg = _pick(base_vars, ("--nts-user-bubble-bg",), accent)
    # Swatches are painted onto opaque layers; a translucent window colour would
    # otherwise leave the card background see-through.
    chat_bg = blend(chat_bg, (255, 255, 255, 255))
    panel_bg = blend(panel_bg, chat_bg)
    accent = blend(accent, chat_bg)
    ai_text = blend(ai_text, chat_bg)

    img = Image.new("RGBA", (CARD_W, CARD_H), chat_bg)
    draw = ImageDraw.Draw(img)

    top = mix(chat_bg, accent, 0.10)
    bottom = mix(chat_bg, accent, 0.36)
    for y in range(CARD_H):
        draw.line([(0, y), (CARD_W, y)], fill=mix(top, bottom, y / float(CARD_H - 1)))
    glow = Image.new("RGBA", (CARD_W, CARD_H), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(glow)
    for step in range(26):
        radius = 420 - step * 14
        if radius <= 0:
            break
        alpha = int(16 * (1 - step / 26.0))
        gdraw.ellipse(
            [CARD_W - 120 - radius, 120 - radius, CARD_W - 120 + radius, 120 + radius],
            fill=(accent[0], accent[1], accent[2], alpha),
        )
    img = Image.alpha_composite(img, glow)
    draw = ImageDraw.Draw(img)

    card_box = (72, 104, 720, 540)
    card_fill = blend((255, 255, 255, 184), top)
    _rounded(draw, card_box, 40, card_fill, outline=mix(blend(rim, top), accent, 0.25), width=2)
    draw.rounded_rectangle(
        (card_box[0] + 26, card_box[1] + 3, card_box[2] - 26, card_box[1] + 6),
        radius=3,
        fill=blend((255, 255, 255, 150), card_fill),
    )

    studio = msg("shareCard.title")
    title = _clip(name or studio, 16)
    title_font = _fit_font(draw, title, 54, 30, 540)
    _draw_text(draw, (116, 152), title, title_font, ai_text)

    bits = [studio]
    if preset_label:
        bits.append(preset_label)
    if quality_label:
        bits.append(quality_label)
    subtitle = " · ".join(bits)
    sub_font = _fit_font(draw, subtitle, 22, 14, 560)
    _draw_text(draw, (118, 224), subtitle, sub_font, mix(ai_text, card_fill, 0.35))
    draw.line([(116, 268), (676, 268)], fill=mix(blend(rim, card_fill), card_fill, 0.45), width=2)

    chips = [
        (msg("shareCard.base"), chat_bg),
        (msg("shareCard.panel"), panel_bg),
        (msg("shareCard.accent"), accent),
        (msg("shareCard.assistant"), blend(ai_bg, chat_bg)),
        (msg("shareCard.user"), blend(user_bg, chat_bg)),
        (msg("shareCard.rim"), blend(rim, chat_bg)),
    ]
    x = 116
    y = 300
    for label, color in chips:
        _rounded(draw, (x, y, x + 78, y + 78), 18, color, outline=mix(color, ai_text, 0.28), width=2)
        chip_font = _fit_font(draw, label, 17, 9, 88)
        label_w = _text_w(draw, label, chip_font)
        _draw_text(draw, (x + (78 - label_w) / 2, y + 88), label, chip_font, mix(ai_text, card_fill, 0.42))
        x += 94

    cx, cy, r = 952, 300, 132
    rings = Image.new("RGBA", (CARD_W, CARD_H), (0, 0, 0, 0))
    rdraw = ImageDraw.Draw(rings)
    for step in range(18):
        rr = r - step * 3
        alpha = int(52 * (1 - step / 18.0) + 12)
        rdraw.ellipse(
            (cx - rr, cy - rr, cx + rr, cy + rr),
            outline=(accent[0], accent[1], accent[2], alpha),
            width=2,
        )
    rdraw.ellipse((cx - r + 18, cy - r + 18, cx + r - 18, cy + r - 18), fill=(accent[0], accent[1], accent[2], 34))
    rdraw.ellipse(
        (cx - r + 42, cy - r + 34, cx - r + 106, cy - r + 96),
        fill=(255, 255, 255, 120),
    )
    img = Image.alpha_composite(img, rings)
    draw = ImageDraw.Draw(img)

    pill_text = _clip(preset_label or msg("shareCard.noMaterial"), 18)
    pill_font = _font(21, pill_text)
    pill_w = _text_w(draw, pill_text, pill_font) + 48
    pill_x0 = cx - pill_w / 2
    _rounded(
        draw,
        (pill_x0, 478, pill_x0 + pill_w, 526),
        24,
        mix(accent, (255, 255, 255, 255), 0.14),
        outline=mix(accent, (255, 255, 255, 255), 0.45),
        width=2,
    )
    _draw_text(draw, (pill_x0 + 24, 489), pill_text, pill_font, (255, 255, 255, 255))

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    footer = stamp + " · " + msg("shareCard.counts", light=len(vars_map or {}), dark=len(dark_map))
    foot_font = _font(16, footer)
    _draw_text(draw, (74, 566), footer, foot_font, mix(ai_text, bottom, 0.45))
    mark = "N.E.K.O"
    mark_font = _font(16, mark)
    mark_w = _text_w(draw, mark, mark_font)
    _draw_text(draw, (CARD_W - 74 - mark_w, 566), mark, mark_font, mix(ai_text, bottom, 0.30))

    mask = Image.new("L", (CARD_W, CARD_H), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, CARD_W - 1, CARD_H - 1), radius=28, fill=255)
    out = Image.new("RGB", (CARD_W, CARD_H), (255, 255, 255))
    out.paste(img.convert("RGB"), (0, 0), mask)

    buf = io.BytesIO()
    out.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
