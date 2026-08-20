"""控制命令解析：把一句属性/开关控制命令解析为结构化意图。

从 MijiaPlugin（plugin/plugins/mijia/__init__.py）原 `_parse_control_command`
搬出，不改任何解析规则（含"单字调分界""属性后缀推断"等原有行为）。
"""

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from . import intent_terms as T
from .value_resolver import (
    CN_DIGIT_BOUNDARY_RE,
    infer_prop_from_unit,
    parse_delta,
    parse_prop_value,
)


@dataclass
class ParseResult:
    """解析结果。

    action: "switch"（二元开关）| "set_prop"（属性/模式设定）| "adjust_prop"（相对调整）
    direction/delta: 仅 adjust_prop 使用（1=增大，-1=减小）
    """

    device: str
    action: str
    prop: Optional[str] = None
    value: Any = None
    direction: int = 1
    delta: Optional[float] = None

    def __repr__(self) -> str:
        return (
            f"ParseResult(device={self.device!r}, action={self.action!r}, "
            f"prop={self.prop!r}, value={self.value!r})"
        )


def _infer_prop_from_intent(intent: str) -> str:
    """从意图文本推断属性名（原 adjust/extreme 分支的无属性名推断逻辑）。"""
    if re.search(r"度|热|冷", intent):
        return "温度"
    if re.search(r"亮|暗|光", intent):
        return "亮度"
    if re.search(r"风|速|档", intent):
        return "风速"
    if re.search(r"音|声", intent):
        return "音量"
    if re.search(r"湿", intent):
        return "湿度"
    return "亮度"


def parse_control_command(command: str) -> Optional[ParseResult]:
    """解析控制命令，返回 ParseResult；无法解析返回 None。

    与原 `_parse_control_command` 完全一致：
    1. 场景动词开头 → None（场景由 router 先行处理）
    2. 开关命令：动词前缀直接切
    3. 属性/模式命令：四种分界线找"设备 | 意图"，再解析模式/数值/相对量/极值/颜色
    """
    cmd = command.strip()

    # 场景命令不在此处理
    if re.match(r"(?:执行|运行|触发)", cmd):
        return None

    # === 开关命令：动词在最前面，直接切 ===
    for kw in ["打开", "开启", "开"]:
        if cmd.startswith(kw):
            device = cmd[len(kw):].strip()
            return ParseResult(device=device, action="switch", value=True) if device else None
    for kw in ["关闭", "关掉", "关"]:
        if cmd.startswith(kw):
            device = cmd[len(kw):].strip()
            return ParseResult(device=device, action="switch", value=False) if device else None

    # === 属性/模式命令：找分界线 ===
    device_ref = None
    intent = None

    # 1) 动词分界："空调调到26度" → "空调" | "调到26度"
    verb_m = re.search(T.SPLIT_VERBS, cmd)
    if verb_m:
        device_ref = cmd[:verb_m.start()].strip()
        intent = cmd[verb_m.start():]

    # 1.5) 单字"调"分界（仅后跟数字或模式词时）：
    #      "空调调26度" → "空调" | "调26度"，"空调调制冷" → "空调" | "调制冷"
    if not device_ref:
        tiao_m = re.search(r"调(?=\d|(?:" + T.MODE_TERMS + r"))", cmd)
        if tiao_m:
            device_ref = cmd[:tiao_m.start()].strip()
            intent = cmd[tiao_m.start():]

    # 2) 属性/模式词分界："灯亮度50%" → "灯" | "亮度50%"
    if not device_ref:
        pm_m = re.search(r"(?:" + T.PROP_TERMS + r"|" + T.MODE_TERMS + r")", cmd)
        if pm_m:
            device_ref = cmd[:pm_m.start()].strip()
            intent = cmd[pm_m.start():]

    # 3) 纯数字分界（仅当前一位是中文时，避免误切含数字的设备名）：
    #    "卧室灯50%" → "卧室灯" | "50%"
    if not device_ref:
        num_m = CN_DIGIT_BOUNDARY_RE.search(cmd)
        if num_m:
            device_ref = cmd[:num_m.start()].strip()
            intent = cmd[num_m.start():]

    if not device_ref or not intent:
        return None

    # === 解析意图 ===

    # 模式命令："制冷" / "自动模式" / "调制冷"
    mode_m = re.match(r"(?:(?:" + T.SPLIT_VERBS + r"))?\s*(" + T.MODE_TERMS + r")(?:模式)?$", intent)
    if mode_m:
        return ParseResult(device=device_ref, action="set_prop", prop="模式", value=mode_m.group(1))

    # 属性 + 数值："亮度50%" / "调到50%" / "调到26度" / "温度26" / "50%"
    prop_val = parse_prop_value(intent)
    if prop_val is not None:
        prop_name, value, unit = prop_val
        if not prop_name:
            prop_name = infer_prop_from_unit(unit, value)
            if prop_name is None:
                return None
        return ParseResult(device=device_ref, action="set_prop", prop=prop_name, value=value)

    # 相对值调整："调亮一点" / "温度高一点" / "风速调大一点"
    adj_m = re.search(
        r"(" + T.PROP_TERMS + r")?\s*(?:调|设|切)?\s*"
        r"(" + T.ADJUST_UP + r"|" + T.ADJUST_DOWN + r")"
        r"(?:一?点|一些|一?些|一?些|少许)?",
        intent,
    )
    if adj_m:
        prop_name = adj_m.group(1)
        direction_word = adj_m.group(2)
        direction = 1 if re.match(T.ADJUST_UP, direction_word) else -1
        if not prop_name:
            prop_name = _infer_prop_from_intent(intent)
        delta = parse_delta(intent)
        return ParseResult(
            device=device_ref, action="adjust_prop",
            prop=prop_name, direction=direction, delta=delta,
        )

    # 极值："调到最高" / "调到最低" / "最亮" / "最暗"
    ext_m = re.search(
        r"(" + T.PROP_TERMS + r")?\s*(?:调到|调成|调为|设为)?\s*(" + T.EXTREME_TERMS + r")",
        intent,
    )
    if ext_m:
        prop_name = ext_m.group(1)
        extreme_word = ext_m.group(2)
        extreme = "max" if extreme_word in T.EXTREME_MAX else "min"
        if not prop_name:
            prop_name = _infer_prop_from_intent(intent)
        return ParseResult(device=device_ref, action="set_prop", prop=prop_name, value=extreme)

    # 颜色控制："灯调到红色" / "灯设成蓝色"
    color_m = re.search(
        r"(?:调到|调成|设为|设成|切换到|切换至)\s*(.+?)(?:模式)?$",
        intent,
    )
    if color_m:
        color_word = color_m.group(1).strip()
        rgb = T.COLOR_MAP.get(color_word)
        if rgb is not None:
            return ParseResult(device=device_ref, action="set_prop", prop="颜色", value=rgb)

    return None
