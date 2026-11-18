"""Plugin log identities shared by the runner, readers and SDK paths."""
from __future__ import annotations

import hashlib

def sanitize_log_identifier(plugin_id: str, max_len: int = 64) -> str:
    raw = str(plugin_id)
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in raw).strip("_-")
    if not safe:
        safe = hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()[:16]
    if len(safe) > max_len:
        digest = hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()[:12]
        safe = f"{safe[:max_len - 13]}_{digest}"
    return safe


def plugin_log_service_name(plugin_id: str) -> str:
    return f"Plugin_{sanitize_log_identifier(plugin_id)}"
