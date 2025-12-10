"""
服务器工具函数
"""
from datetime import datetime, timezone


def now_iso() -> str:
    """生成 ISO 格式的时间戳"""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

