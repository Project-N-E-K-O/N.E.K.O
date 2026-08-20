"""设备匹配：从设备列表（devices_cache.json 内容）中按名称匹配设备。

从 MijiaPlugin（plugin/plugins/mijia/__init__.py）原 `_match_devices` /
`_format_ambiguous_message` 搬出。纯函数，不访问 API/文件：
gethome_merged 房间映射降级由调用方构建后经 api_room_map / device_room_map
传入（原实现里该 API 调用保留在插件主类中，仅在设备缓存缺房间数据时触发）。
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class MatchResult:
    """设备匹配结果。

    status: "ok"（唯一命中）| "ambiguous"（多命中）| "not_found"（无匹配）
    """

    status: str
    devices: list = field(default_factory=list)
    message: str = ""

    @property
    def device(self) -> Optional[dict]:
        """唯一命中时返回设备 dict，否则 None。"""
        return self.devices[0] if self.devices else None


def _normalize(name: str) -> str:
    return name.lower().strip().replace("的", "")


def _alias_list(device: dict) -> list[str]:
    alias = device.get("alias", "")
    if not alias:
        return []
    return [a.strip().lower() for a in alias.split(",") if a.strip()]


def format_ambiguous_message(query: str, devices: list[dict]) -> str:
    """格式化多设备歧义提示，按房间分组（原 _format_ambiguous_message）。"""
    lines = [f"找到 {len(devices)} 个匹配 '{query}' 的设备："]
    for i, d in enumerate(devices, 1):
        rn = d.get("room_name", "")
        dn = d.get("name", "未知")
        alias = d.get("alias", "")
        status = "🟢" if d.get("is_online") else "🔴"
        label = f"{rn} {dn}" if rn else dn
        if alias:
            label += f" (别名: {alias})"
        lines.append(f"  {i}. {status} {label}")
    lines.append("请用房间名+设备名精确指定，如 '卧室灯'")
    return "\n".join(lines)


def _list_all_devices(devices: list[dict]) -> str:
    all_names = []
    for d in devices:
        rn = d.get("room_name", "")
        dn = d.get("name", "未知")
        alias = d.get("alias", "")
        label = f"{rn} {dn}" if rn else dn
        if alias:
            label += f" (别名: {alias})"
        all_names.append(f"  • {label}")
    return "\n".join(all_names)


def match_devices(
    name: str,
    devices: list[dict],
    *,
    api_room_map: Optional[dict[str, str]] = None,
    device_room_map: Optional[dict[str, str]] = None,
) -> MatchResult:
    """统一设备匹配，匹配优先级：精确别名 > 精确设备名 > 区域+设备名拆分 > 模糊匹配。

    Args:
        name: 用户输入的设备名/别名/房间名+设备名。
        devices: 设备缓存列表（含 room_name / alias / did / properties 等字段）。
        api_room_map: room_id→room_name 映射（可选，gethome_merged API 降级）。
        device_room_map: device_did→room_name 映射（可选，用于注入缺失的房间名）。
    """
    if not devices:
        return MatchResult("not_found", [], "设备列表为空，请先获取设备列表")

    name_lower = _normalize(name)

    # === 精确匹配 ===
    exact = []
    for d in devices:
        if name_lower in _alias_list(d):
            exact.append(d)
            continue
        if d.get("name", "").lower() == name_lower:
            exact.append(d)

    if len(exact) == 1:
        return MatchResult("ok", exact)
    if len(exact) > 1:
        return MatchResult("ambiguous", exact, format_ambiguous_message(name, exact))

    # === 区域+设备名拆分 ===
    room_map: dict[str, str] = {}
    for d in devices:
        rn = d.get("room_name", "")
        if rn:
            room_map[rn.lower()] = rn

    # 降级：设备无房间数据时，用 API 房间名做拆分，并把 DID→房间映射注入设备数据
    if not room_map and api_room_map:
        for rn_original in api_room_map.values():
            rn_lower = rn_original.lower().strip()
            if rn_lower:
                room_map[rn_lower] = rn_original
        if device_room_map:
            for d in devices:
                did = d.get("did", "")
                if did in device_room_map and not d.get("room_name"):
                    d["room_name"] = device_room_map[did]

    room_matched = []
    # 检查是否有设备有房间数据；如果全空则丢弃房间限定走模糊匹配
    has_room_data = any(d.get("room_name") for d in devices)
    for rn_lower, rn_original in room_map.items():
        device_part = None
        if name_lower.startswith(rn_lower):
            device_part = name_lower[len(rn_lower):].strip()
        elif name_lower.endswith(rn_lower):
            device_part = name_lower[:-len(rn_lower)].strip()

        if device_part:
            if has_room_data:
                for d in devices:
                    if d.get("room_name", "").lower() != rn_lower:
                        continue
                    dname = d.get("name", "").lower()
                    if device_part in dname or device_part in _alias_list(d):
                        room_matched.append(d)
            else:
                # 无房间数据时不能确认设备归属房间，丢掉房间限定走模糊匹配
                break

    if len(room_matched) == 1:
        return MatchResult("ok", room_matched)
    if len(room_matched) > 1:
        return MatchResult("ambiguous", room_matched, format_ambiguous_message(name, room_matched))

    # === 模糊匹配（子串，双向） ===
    fuzzy = []
    for d in devices:
        dname = d.get("name", "").lower()
        if dname and (name_lower in dname or dname in name_lower):
            fuzzy.append(d)
            continue
        if any(name_lower in a or a in name_lower for a in _alias_list(d)):
            fuzzy.append(d)

    if len(fuzzy) == 1:
        return MatchResult("ok", fuzzy)
    if len(fuzzy) > 1:
        return MatchResult("ambiguous", fuzzy, format_ambiguous_message(name, fuzzy))

    # === 完全无匹配，列出所有设备 ===
    return MatchResult(
        "not_found",
        [],
        f"未找到匹配 '{name}' 的设备。当前设备列表：\n" + _list_all_devices(devices),
    )
