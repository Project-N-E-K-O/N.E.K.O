"""动作动词表：ACTION_VERBS 正则 + VERB_TO_ACTION 映射 + 动词匹配。

从 MijiaPlugin（plugin/plugins/mijia/__init__.py）原 `_ACTION_VERBS` /
`_VERB_TO_ACTION` 及 smart_control 内动作分支的两种词序匹配逻辑搬出。
"""

import re
from typing import Optional

ACTION_VERBS = (
    r"开始|启动|继续|暂停|停止|回充|回去充电|"
    r"出舱|集尘|洗拖布|烘干|建图|召唤清洁"
)

# 中文动词 → 设备 spec action 名称候选（原样搬自 __init__.py）
VERB_TO_ACTION = {
    "开始": ["start", "start_sweep", "start_wash", "start_cook", "start-work", "start-drying"],
    "启动": ["start", "start_sweep", "start_wash", "start_cook", "start-work"],
    "继续": ["start", "resume", "continue"],
    "暂停": ["pause", "pause-sweeping", "stop-sweeping"],
    "停止": ["stop", "stop-sweeping", "stop-wash", "stop-working", "cancel_cooking"],
    "关闭": ["stop", "stop-working", "cancel_cooking"],
    "回充": ["start-charge", "start_charge"],
    "回去充电": ["start-charge", "start_charge"],
    "出舱": ["start-eject"],
    "集尘": ["start-dust-arrest"],
    "洗拖布": ["start-mop-wash"],
    "烘干": ["start-dry"],
    "建图": ["start-build-map"],
    "召唤清洁": ["start-call-clean"],
}

# 动词在尾部："扫地机开始扫地" → (扫地机, 开始)
_VERB_LAST_RE = re.compile(r"(.+?)(?:的|把|让)?\s*(" + ACTION_VERBS + r")(?:.+)?$")
# 动词在开头："开始扫地" → (扫地, 开始)
_VERB_FIRST_RE = re.compile(r"(" + ACTION_VERBS + r")\s*(.+)")


def match_action_verb(command: str) -> Optional[tuple[str, str]]:
    """匹配动作动词，返回 (device_hint, verb)；未命中返回 None。

    与原逻辑等价：动词在尾部优先，其次动词开头；两种词序下设备名为空都视为未命中。
    """
    cmd = command.strip()
    m = _VERB_LAST_RE.match(cmd)
    if m:
        device_hint = (m.group(1) or "").strip()
        verb = (m.group(2) or "").strip()
        if device_hint and verb:
            return device_hint, verb
    m2 = _VERB_FIRST_RE.match(cmd)
    if m2:
        verb = (m2.group(1) or "").strip()
        device_hint = (m2.group(2) or "").strip()
        if device_hint and verb:
            return device_hint, verb
    return None
