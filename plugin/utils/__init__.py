from __future__ import annotations

from typing import Any

from .time_utils import now_iso


def parse_bool_config(value: Any, default: bool = True) -> bool:
    """将配置值解析为 bool，支持 bool / str / None 类型。

    str 支持: "true"/"yes"/"on"/"1" → True, "false"/"no"/"off"/"0" → False（不区分大小写）。
    其他类型或无法识别的字符串返回 *default*。
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        s = value.strip().lower()
        if s in ("1", "true", "yes", "on"):
            return True
        if s in ("0", "false", "no", "off"):
            return False
    if value is None:
        return default
    return default


__all__ = ["now_iso", "parse_bool_config"]
