"""接近/态势检测器共用的取值与几何判定。

这些函数原本在 situation.py 与 proximity.py 里各存一份逐字相同的副本，后半球判定
甚至有 `_is_rear` / `_is_behind` 两个名字。两处的尾随确认阈值也各写各的
（1500m/5s/2帧 与 900m/8s/2事件），真机调参时很容易只改其中一边。
"""

from __future__ import annotations

from typing import Any

# 后半球时钟位。视野正后方为 6 点，5/7 点是相邻扇区。
BEHIND_CLOCKS = frozenset({5, 6, 7})
# 相对方位角超过这个度数即视为后半球（时钟位缺失时的回退判据）。
BEHIND_RELATIVE_DEG = 135.0


def as_float(value: Any) -> float | None:
    """安全转 float。bool 是 int 的子类，必须先排除，否则 True 会变成 1.0。"""
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def as_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def safe_short_text(value: Any) -> str | None:
    """只接受短的、非空的文本；超长值一律丢弃而不是截断。

    这些字段会进入安全摘要，宁可缺项也不要放进不可控长度的原文。
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text or len(text) > 32:
        return None
    return text


def is_rear(item: dict[str, Any]) -> bool:
    """接触点是否位于后半球。时钟位优先，缺失时回退到相对方位角。"""
    clock = as_int(item.get("clock"))
    if clock in BEHIND_CLOCKS:
        return True
    rel = as_float(item.get("relative_deg"))
    return rel is not None and abs(rel) >= BEHIND_RELATIVE_DEG


# --------------------------------------------------------------------------
# 尾随（tailing_risk）确认阈值
#
# 两条来源路径的默认值刻意不同，别在调参时把它们对齐成一个数：
#
#   situation 路径 —— 消费连续态势摘要，每个遥测帧都能给出一次观测，因此窗口短
#     (5s)、距离宽 (1500m)：帧率高，短窗内凑满 2 帧即可确认，宽距离用来更早发现
#     正在咬上来的目标。
#   proximity 路径 —— 消费数据层的边沿事件流，只在跨越阈值时才产生一条，观测稀疏，
#     因此窗口长 (8s)、距离窄 (900m)：稀疏事件需要更长窗口才凑得满，而能连续产生
#     边沿事件本身已说明贴得很近。
#
# 集中在这里是为了让"改了一处忘了另一处"变成显式选择而不是意外。
# --------------------------------------------------------------------------

# 连续态势帧驱动
SITUATION_TAIL_DISTANCE_M = 1500.0
SITUATION_TAIL_WINDOW_SECONDS = 5.0
SITUATION_TAIL_CONFIRM_FRAMES = 2

# 边沿事件驱动
PROXIMITY_TAIL_DISTANCE_M = 900.0
PROXIMITY_TAIL_WINDOW_SECONDS = 8.0
PROXIMITY_TAIL_CONFIRM_EVENTS = 2
