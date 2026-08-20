"""NLP 规则引擎子包：意图路由 / 控制解析 / 设备匹配 / 数值解析。

全部为纯函数模块（无 IO、无 API、无外部依赖），供 MijiaPlugin 的
smart_control / query_device_state 调用。
"""

from .control_parser import ParseResult, parse_control_command
from .device_matcher import MatchResult, format_ambiguous_message, match_devices
from .router import RouteResult, route
from .value_resolver import (
    extract_number,
    infer_prop_from_unit,
    parse_delta,
    parse_prop_value,
    resolve_adjust_target,
)

__all__ = [
    "ParseResult",
    "parse_control_command",
    "MatchResult",
    "format_ambiguous_message",
    "match_devices",
    "RouteResult",
    "route",
    "extract_number",
    "infer_prop_from_unit",
    "parse_delta",
    "parse_prop_value",
    "resolve_adjust_target",
]
