"""数值抽取 + 相对量计算 + 钳值。

从 MijiaPlugin（plugin/plugins/mijia/__init__.py）原 `_parse_control_command`
的数值正则与 smart_control 内 adjust_prop 分支的目标值计算逻辑搬出，不改规则。
"""

import re
from typing import Any, Optional

from .intent_terms import PROP_TERMS, SPLIT_VERBS

# 数字 + 可选单位（原 val_m 正则的核心部分）
_NUMBER_WITH_UNIT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(%|度|℃|°)?")
# 相对量 delta 提取（原 delta_m 正则）
_DELTA_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(%|度|℃)?")
# 中文后紧跟数字的分界（原纯数字分界正则）
CN_DIGIT_BOUNDARY_RE = re.compile(r"(?<=[一-鿿])(\d)")


def extract_number(text: str) -> Optional[tuple[Any, str]]:
    """从文本中抽取第一个 (数值, 单位)；无数字返回 None。

    与原逻辑一致：含小数按 float，否则按 int。
    """
    m = _NUMBER_WITH_UNIT_RE.search(text)
    if not m:
        return None
    num_str = m.group(1)
    unit = m.group(2) or ""
    value = float(num_str) if "." in num_str else int(num_str)
    return value, unit


def infer_prop_from_unit(unit: str, value: Any) -> Optional[str]:
    """无属性名时按单位推断属性：度/℃/°→温度；% 或 0-100 整数→亮度。"""
    if unit in ("度", "℃", "°"):
        return "温度"
    if unit == "%" or (isinstance(value, int) and 0 <= value <= 100):
        return "亮度"
    return None


def parse_prop_value(intent: str) -> Optional[tuple[Optional[str], Any, str]]:
    """解析"属性+数值"意图，返回 (prop_name, value, unit)。

    与原 val_m 正则等价：可选设置动词前缀 + 可选属性词 + 数字 + 可选单位，
    属性词必须紧邻数字（同一匹配位置）。
    """
    m = re.search(
        r"(?:" + SPLIT_VERBS + r")?\s*(" + PROP_TERMS + r")?(\d+(?:\.\d+)?)\s*(%|度|℃|°)?",
        intent,
    )
    if not m:
        return None
    prop_name = m.group(1)
    num_str = m.group(2)
    unit = m.group(3) or ""
    value = float(num_str) if "." in num_str else int(num_str)
    return prop_name, value, unit


def parse_delta(text: str) -> Optional[float]:
    """从文本中提取相对调整的 delta 数值；无数字返回 None。"""
    m = _DELTA_RE.search(text)
    if not m:
        return None
    return float(m.group(1))


def resolve_adjust_target(
    cur_value: float, direction: int, delta: Optional[float], value_range: list
) -> float:
    """计算相对调整的目标值（原 adjust_prop 分支逻辑）。

    - 显式 delta：cur + direction * delta
    - 无 delta：默认步长为 max(step, 范围*10%)
    - 结果 clamp 到 [v_min, v_max]，并按 step 对齐（step>1 时）
    """
    v_min = value_range[0] if len(value_range) >= 1 else 0
    v_max = value_range[1] if len(value_range) >= 2 else 100
    step = value_range[2] if len(value_range) >= 3 else 1

    if delta is not None:
        target = cur_value + direction * delta
    else:
        range_size = v_max - v_min
        default_step = max(step, range_size * 0.1)
        target = cur_value + direction * default_step

    target = max(v_min, min(v_max, target))
    if step > 1:
        target = round((target - v_min) / step) * step + v_min
    return target
