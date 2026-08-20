"""意图路由：smart_control 的 5 步短路（场景/查询/开关标记/动作动词/属性控制）。

从 MijiaPlugin（plugin/plugins/mijia/__init__.py）原 `smart_control` 的分支
判定逻辑搬出。纯规则判定，不访问 API；设备匹配结果随 RouteResult 一并返回，
由插件主类按 branch 分发执行。
"""

from dataclasses import dataclass, field
from typing import Any, Optional

from .action_verbs import match_action_verb
from .control_parser import ParseResult, parse_control_command
from .device_matcher import MatchResult, match_devices
from . import intent_terms as T


@dataclass
class RouteResult:
    """路由结果，branch 取值：

    - "scene":   场景执行（scene_name）
    - "query":   状态查询（device_hint）
    - "action":  设备动作（device_hint + verb + match）
    - "switch":  二元开关（parsed.action == "switch" + match）
    - "control": 属性/模式控制（parsed + match）
    - "unknown": 无法理解（message 为提示语）
    """

    branch: str
    device_hint: str = ""
    scene_name: str = ""
    verb: str = ""
    match: Optional[MatchResult] = None
    parsed: Optional[ParseResult] = None
    message: str = ""

    def __repr__(self) -> str:
        return f"RouteResult(branch={self.branch!r}, device_hint={self.device_hint!r})"


async def route(
    utterance: str,
    devices: list,
    *,
    api_room_map: Optional[dict[str, str]] = None,
    device_room_map: Optional[dict[str, str]] = None,
) -> RouteResult:
    """5 步短路路由：场景 → 开关标记 → 查询 → 动作动词 → 属性控制。

    Args:
        utterance: 用户原始指令。
        devices: 设备缓存列表（用于动作/控制分支的设备匹配）。
        api_room_map / device_room_map: gethome_merged 房间映射降级（可选）。
    """
    command = utterance.strip() if isinstance(utterance, str) else ""

    # === 场景执行 ===
    scene_m = T.SCENE_RE.match(command)
    if scene_m:
        return RouteResult(branch="scene", scene_name=scene_m.group(1).strip())

    # === 开关指令标记（最高优先级，防止动作动词分支抢先匹配） ===
    is_switch_cmd = bool(T.SWITCH_CMD_RE.match(command)) and not T.ACTION_VERB_PREFIX_RE.match(command)

    # === 查询意图 ===
    query_m = T.QUERY_RE.search(command)
    if query_m:
        # 去掉查询关键词，提取设备名
        device_hint = command[:query_m.start()].strip()
        # 去掉属性名后缀（温度/湿度/亮度/电量等），保留设备名
        device_hint = T.QUERY_PROP_SUFFIX_RE.sub("", device_hint).strip()
        if device_hint:
            return RouteResult(branch="query", device_hint=device_hint)

    # === 设备操作（开始/暂停/停止/回充等），开关指令跳过 ===
    if not is_switch_cmd:
        act = match_action_verb(command)
        if act is not None:
            device_hint, verb = act
            match = match_devices(
                device_hint, devices, api_room_map=api_room_map, device_room_map=device_room_map
            )
            if match.status == "ok" and len(match.devices) == 1:
                return RouteResult(branch="action", device_hint=device_hint, verb=verb, match=match)
            # 动作动词已识别但设备未匹配/歧义：仍归 action 分支，由执行层给出明确错误
            return RouteResult(branch="action", device_hint=device_hint, verb=verb, match=match)

    # === 属性/模式控制 ===
    parsed = parse_control_command(command)
    if parsed is None:
        return RouteResult(branch="unknown")
    branch = "switch" if parsed.action == "switch" else "control"
    match = match_devices(
        parsed.device, devices, api_room_map=api_room_map, device_room_map=device_room_map
    )
    return RouteResult(branch=branch, device_hint=parsed.device, parsed=parsed, match=match)
