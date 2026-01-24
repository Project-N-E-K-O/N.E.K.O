from __future__ import annotations

from datetime import datetime, timezone


def now_iso() -> str:
    """获取当前UTC时间的ISO格式字符串
    
    Returns:
        ISO 8601格式的时间字符串,例如: "2024-01-24T12:00:00Z"
    """
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
