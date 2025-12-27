import os
from typing import Any


def format_log_text(value: Any) -> str:
    s = "" if value is None else str(value)

    try:
        max_len = int(os.getenv("NEKO_PLUGIN_LOG_CONTENT_MAX", "200"))
    except Exception:
        max_len = 200
    if max_len <= 0:
        max_len = 200

    truncated = False
    if len(s) > max_len:
        s = s[:max_len]
        truncated = True

    try:
        wrap = int(os.getenv("NEKO_PLUGIN_LOG_WRAP", "0"))
    except Exception:
        wrap = 0

    if wrap and wrap > 0:
        s = "\n".join(s[i : i + wrap] for i in range(0, len(s), wrap))

    if truncated:
        s = s + "...(truncated)"

    return s
