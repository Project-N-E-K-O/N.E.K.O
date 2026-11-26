"""Theme palette contrast checks for built-in presets."""

from __future__ import annotations

import glob
import json
import os
import re

COLOR_RE = re.compile(r"#[0-9a-fA-F]{3,8}|rgba?\([^)]*\)")
NUM_RE = re.compile(r"-?\d*\.?\d+")

# (标签, 文字键, 底色键, 附加底层, 门槛)
PAIRS: list[tuple[str, str, str, str, float]] = [
    ("顶栏", "--nts-topbar-text", "--nts-topbar-bg", "chat", 4.5),
    ("输入框", "--nts-input-text", "--nts-input-bg", "chat", 4.5),
    ("输入占位", "--nts-input-placeholder", "--nts-input-bg", "chat", 3.0),
    ("按钮", "--nts-button-text", "--nts-button-bg", "chat", 4.5),
    ("发送键", "--nts-send-text", "--nts-send-bg", "chat", 4.5),
    ("用户气泡", "--nts-user-bubble-text", "--nts-user-bubble-bg", "chat+usergrad", 4.5),
    ("助手气泡", "--nts-ai-bubble-text", "--nts-ai-bubble-bg", "chat+aigrad", 4.5),
    ("面板正文", "--nts-panel-text", "--nts-panel-bg", "chat", 4.5),
    ("面板次要", "--nts-panel-text-sub", "--nts-panel-bg", "chat", 3.0),
    ("链接", "--nts-link", "--nts-ai-bubble-bg", "chat+aigrad", 4.5),
    ("弹窗文字", "--nts-modal-text", "--nts-panel-bg", "chat", 4.5),
    ("浮层文字", "--nts-popover-text", "--nts-panel-bg", "chat", 4.5),
    ("推荐文字", "--nts-rec-text", "--nts-panel-bg", "chat", 4.5),
    ("胶囊文字", "--nts-chip-text", "--nts-panel-bg", "chat", 4.5),
    ("状态文字", "--nts-status-text", "--nts-panel-bg", "chat", 3.0),
    ("工具气泡", "--nts-tool-bubble-text", "--nts-tool-bubble-bg", "chat", 4.5),
    ("代码文字", "--nts-code-text", "--nts-code-bg", "chat", 4.5),
]


def parse_color(value):
    """'#rrggbb' / 'rgba(...)' → (r, g, b, a)；无法解析返回 None。"""
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
    """渐变取所有色标平均（文字会跨过整段渐变，平均是最稳的代理）；单色直接返回。"""
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
    """src-over：top 压 bottom。"""
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
    def channel(v):
        v = v / 255.0
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4

    return 0.2126 * channel(color[0]) + 0.7152 * channel(color[1]) + 0.0722 * channel(color[2])


def contrast(fg, bg):
    l1, l2 = lum(fg), lum(bg)
    if l1 < l2:
        l1, l2 = l2, l1
    return (l1 + 0.05) / (l2 + 0.05)


def mode_vars(doc, mode):
    merged = dict(doc.get("vars") or {})
    if mode == "dark":
        merged.update({k: v for k, v in (doc.get("dark_vars") or {}).items() if v})
    return merged


def resolve_base(vars_map, kind):
    chat = average_color(vars_map.get("--nts-chat-bg", "#ffffff")) or (255, 255, 255, 1.0)
    if kind == "chat":
        return chat
    key = "--nts-user-bubble-gradient" if kind == "chat+usergrad" else "--nts-ai-bubble-gradient"
    grad = average_color(vars_map.get(key, ""))
    return over(grad, chat) if grad else chat


def audit_vars(vars_map):
    rows = []
    for label, fg_key, bg_key, extra, floor in PAIRS:
        fg = average_color(vars_map.get(fg_key))
        bg = average_color(vars_map.get(bg_key))
        if fg is None or bg is None:
            rows.append({"pair": label, "skip": True})
            continue
        ratio = contrast(fg, over(bg, resolve_base(vars_map, extra)))
        rows.append(
            {
                "pair": label,
                "fg": fg_key,
                "bg": bg_key,
                "ratio": round(ratio, 2),
                "floor": floor,
                "ok": ratio + 1e-9 >= floor,
            }
        )
    return rows


def themes_from_dir(path, limit=None):
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
    """返回 (失败列表, 检查对数)；失败项是 (theme_id, mode, row)。"""
    fails = []
    pair_checks = 0
    for theme_id, doc in themes:
        for mode in ("light", "dark"):
            for row in audit_vars(mode_vars(doc, mode)):
                if row.get("skip"):
                    continue
                pair_checks += 1
                if not row["ok"]:
                    fails.append((theme_id, mode, row))
    return fails, pair_checks


def audit_dir(path, limit=None):
    themes = themes_from_dir(path, limit)
    fails, pair_checks = failing(themes)
    return themes, fails, pair_checks