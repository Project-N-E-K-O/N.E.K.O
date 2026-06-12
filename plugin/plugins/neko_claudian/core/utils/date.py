# Ported from claudian/src/utils/date.ts
# Original author: Claudian contributors
# License: MIT

"""
Date utilities.
"""

from __future__ import annotations

import time
from datetime import datetime


def format_duration_mm_ss(seconds: float) -> str:
    """Format duration as mm:ss."""
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{minutes:02d}:{secs:02d}"


def get_timestamp() -> float:
    """Get current timestamp."""
    return time.time()


def format_timestamp(timestamp: float) -> str:
    """Format timestamp to ISO string."""
    return datetime.fromtimestamp(timestamp).isoformat()
