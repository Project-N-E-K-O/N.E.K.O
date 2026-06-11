"""系统时区检测 + VPN 矛盾检测。"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo


def get_system_timezone() -> Optional[str]:
    """获取系统本地时区名称（IANA 格式）。"""
    try:
        local_tz = datetime.now().astimezone().tzinfo
        if local_tz is None:
            return None
        tz_name = str(local_tz)
        if "/" not in tz_name:
            tz_name = _read_system_tz_iana() or tz_name
        return tz_name if "/" in tz_name else None
    except Exception:
        return None


def _read_system_tz_iana() -> Optional[str]:
    tz_env = os.environ.get("TZ", "").strip()
    if tz_env and "/" in tz_env:
        return tz_env.lstrip(":")
    try:
        with open("/etc/timezone", "r") as f:
            val = f.read().strip()
            if "/" in val:
                return val
    except Exception:
        pass
    try:
        link = os.readlink("/etc/localtime")
        idx = link.find("zoneinfo/")
        if idx >= 0:
            return link[idx + len("zoneinfo/"):]
    except Exception:
        pass
    return None


def _tz_offset_hours(tz_name: str) -> Optional[float]:
    try:
        zi = ZoneInfo(tz_name)
        offset = datetime.now(zi).utcoffset()
        if offset is not None:
            return offset.total_seconds() / 3600.0
    except Exception:
        pass
    return None


def detect_vpn_conflict(ip_timezone: str, system_tz: Optional[str]) -> bool:
    """IP 时区与系统时区偏差 ≥ 2h 视为 VPN。"""
    if not ip_timezone or not system_tz:
        return False
    ip_off = _tz_offset_hours(ip_timezone)
    sys_off = _tz_offset_hours(system_tz)
    if ip_off is None or sys_off is None:
        return False
    return abs(ip_off - sys_off) >= 2.0
