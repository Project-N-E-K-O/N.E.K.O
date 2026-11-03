from __future__ import annotations

from datetime import datetime, timezone


def now_iso() -> str:
    """Get the current UTC time as an ISO-formatted string
    
    Returns:
        An ISO 8601 timestamp, for example: \"2024-01-24T12:00:00Z\"
    """
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
