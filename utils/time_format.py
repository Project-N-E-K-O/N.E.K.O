# -*- coding: utf-8 -*-
"""轻量级时间格式化工具，供 memory_server / main_logic 等模块共用。"""

from config.prompts_sys import _loc
from config.prompts_memory import (
    ELAPSED_TIME_DH, ELAPSED_TIME_D,
    ELAPSED_TIME_HM, ELAPSED_TIME_H, ELAPSED_TIME_M,
)


def format_elapsed(lang: str, gap_seconds: float) -> str:
    """根据间隔秒数，智能选择时间格式模板（天/时/分，省略零值单位）。"""
    days = int(gap_seconds // 86400)
    hours = int((gap_seconds % 86400) // 3600)
    minutes = int((gap_seconds % 3600) // 60)
    if days > 0:
        if hours > 0:
            return _loc(ELAPSED_TIME_DH, lang).format(d=days, h=hours)
        else:
            return _loc(ELAPSED_TIME_D, lang).format(d=days)
    elif hours > 0 and hours < 3 and minutes > 0:
        return _loc(ELAPSED_TIME_HM, lang).format(h=hours, m=minutes)
    elif hours > 0:
        return _loc(ELAPSED_TIME_H, lang).format(h=hours)
    else:
        return _loc(ELAPSED_TIME_M, lang).format(m=minutes)
