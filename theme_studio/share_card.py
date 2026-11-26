"""Render a portable PNG preview from a validated theme palette."""

from __future__ import annotations

import io
from datetime import datetime
from typing import Any, Iterable

from PIL import Image, ImageDraw, ImageFont

from .contrast import average_color
from .i18n import msg

CARD_W = 1200
CARD_H = 630

# Prefer system fonts with broad character coverage before falling back to Pillow.
_FONT_CANDIDATES = (
    (r"C:\Windows\Fonts\msyhbd.ttc", 0),
    (r"C:\Windows\Fonts\msyh.ttc", 0),
    (r"C:\Windows\Fonts\simhei.ttf", 0),
    (r"C:\Windows\Fonts\arialbd.ttf", 0),
    (r"C:\Windows\Fonts\arial.ttf", 0),
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 0),
    ("/System/Library/Fonts/PingFang.ttc", 0),
    ("DejaVuSans.ttf", 0),
)

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
    t = max(0.0, min(1.0, t))
    return (
        int(round(a[0] + (b[0] - a[0]) * t)),
        int(round(a[1] + (b[1] - a[1]) * t)),
        int(round(a[2] + (b[2] - a[2]) * t)),
        int(round(a[3] + (b[3] - a[3]) * t)),
    )


def _pick(vars_map: dict[str, Any], keys: Iterable[str], fallback: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    for key in keys:
        got = average_color(vars_map.get(key))
        if got is not None:
            r, g, b, alpha = got
            return r, g, b, round(255 * alpha)
    return fallback


def _font(size: int) -> ImageFont.FreeTypeFont:
    for path, index in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size, index=index)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def _text_w(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    box = draw.textbbox((0, 0), text, font=font)
    return box[2] - box[0]


def _fit_font(draw: ImageDraw.ImageDraw, text: str, start: int, floor: int, max_w: int):
    size = start
    while size > floor:
        font = _font(size)
        if _text_w(draw, text, font) <= max_w:
            return font
        size -= 2
    return _font(floor)


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
    """Render the light palette, falling back to dark when only dark colors exist."""
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
    _rounded(draw, card_box, 40, card_fill, outline=mix(rim, accent, 0.25), width=2)
    draw.rounded_rectangle(
        (card_box[0] + 26, card_box[1] + 3, card_box[2] - 26, card_box[1] + 6),
        radius=3,
        fill=(255, 255, 255, 150),
    )

    title = _clip(name or msg("share.title"), 16)
    title_font = _fit_font(draw, title, 54, 30, 540)
    draw.text((116, 152), title, font=title_font, fill=ai_text)

    bits = [msg("share.title")]
    if preset_label:
        bits.append(preset_label)
    if quality_label:
        bits.append(quality_label)
    sub_font = _font(22)
    draw.text((118, 224), " · ".join(bits), font=sub_font, fill=mix(ai_text, card_fill, 0.35))
    draw.line([(116, 268), (676, 268)], fill=mix(rim, card_fill, 0.45), width=2)

    chips = [
        (msg("share.base"), chat_bg),
        (msg("share.panel"), panel_bg),
        (msg("share.accent"), accent),
        (msg("share.assistant"), blend(ai_bg, chat_bg)),
        (msg("share.user"), blend(user_bg, chat_bg)),
        (msg("share.rim"), blend(rim, chat_bg)),
    ]
    x = 116
    y = 300
    for label, color in chips:
        _rounded(draw, (x, y, x + 78, y + 78), 18, color, outline=mix(color, ai_text, 0.28), width=2)
        chip_font = _fit_font(draw, label, 17, 9, 88)
        label_w = _text_w(draw, label, chip_font)
        draw.text((x + (78 - label_w) / 2, y + 88), label, font=chip_font, fill=mix(ai_text, card_fill, 0.42))
        x += 94

    cx, cy, r = 952, 300, 132
    for step in range(18):
        rr = r - step * 3
        alpha = int(52 * (1 - step / 18.0) + 12)
        draw.ellipse(
            (cx - rr, cy - rr, cx + rr, cy + rr),
            outline=(accent[0], accent[1], accent[2], alpha),
            width=2,
        )
    draw.ellipse(
        (cx - r + 42, cy - r + 34, cx - r + 106, cy - r + 96),
        fill=(255, 255, 255, 120),
    )
    ball = Image.new("RGBA", (CARD_W, CARD_H), (0, 0, 0, 0))
    bdraw = ImageDraw.Draw(ball)
    bdraw.ellipse((cx - r + 18, cy - r + 18, cx + r - 18, cy + r - 18), fill=(accent[0], accent[1], accent[2], 34))
    img = Image.alpha_composite(img, ball)
    draw = ImageDraw.Draw(img)

    pill_text = _clip(preset_label or msg("share.noMaterial"), 18)
    pill_font = _font(21)
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
    draw.text((pill_x0 + 24, 489), pill_text, font=pill_font, fill=(255, 255, 255, 255))

    foot_font = _font(16)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    footer = stamp + " · " + msg("share.counts", light=len(vars_map or {}), dark=len(dark_map))
    draw.text((74, 566), footer, font=foot_font, fill=mix(ai_text, bottom, 0.45))
    mark = "N.E.K.O"
    mark_w = _text_w(draw, mark, foot_font)
    draw.text((CARD_W - 74 - mark_w, 566), mark, font=foot_font, fill=mix(ai_text, bottom, 0.30))

    mask = Image.new("L", (CARD_W, CARD_H), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, CARD_W - 1, CARD_H - 1), radius=28, fill=255)
    out = Image.new("RGB", (CARD_W, CARD_H), (255, 255, 255))
    out.paste(img.convert("RGB"), (0, 0), mask)

    buf = io.BytesIO()
    out.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
