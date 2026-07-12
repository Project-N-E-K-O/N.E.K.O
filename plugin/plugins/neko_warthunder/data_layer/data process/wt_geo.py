"""战雷地图坐标换算与态势派生。

map_obj.json 中物体坐标是 0~1 归一化值（相对地图，x 向右=东，y 向下=南）。
配合 map_info 的 grid_size（网格区域的世界尺寸，米）可换算出真实距离。

提供：
    - 归一化坐标 -> 米坐标 / 两点距离（米）
    - 方位角（正北为 0，顺时针 0~360）
    - 朝向向量(dx,dy) -> 航向角
    - 网格坐标标签（如 B4），用 grid_zero / grid_steps
    - analyze_situation：以“自己”为中心，算出最近敌机、敌我距离方位等态势
"""

from __future__ import annotations

import math
from typing import Any

_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _world_span(map_info: Any) -> tuple[float, float] | None:
    """地图世界跨度（米）= map_max - map_min。

    归一化坐标 0~1 对应的真实世界范围是 [map_min, map_max]，故换算系数必须用
    (map_max - map_min)。注意：map_info.grid_size 是“网格参考区域”尺寸（实测仅约
    地图的 1/2.5），**不能**用作整图换算，否则距离会被系统性低估。
    缺 map_min/map_max 时回退 grid_size（可能不准）。
    """
    mn = getattr(map_info, "map_min", None)
    mx = getattr(map_info, "map_max", None)
    if mn and mx:
        sx, sy = mx[0] - mn[0], mx[1] - mn[1]
        if sx and sy:
            return (sx, sy)
    gs = getattr(map_info, "grid_size", None)
    if gs and gs[0] and gs[1]:
        return (gs[0], gs[1])
    return None


def to_meters(nx: float, ny: float, map_info: Any) -> tuple[float, float] | None:
    """归一化坐标 -> 世界米坐标（以 map_min 为原点）。缺换算参数返回 None。"""
    span = _world_span(map_info)
    if not span:
        return None
    mn = getattr(map_info, "map_min", None) or (0.0, 0.0)
    return (mn[0] + nx * span[0], mn[1] + ny * span[1])


def distance_m(
    x1: float, y1: float, x2: float, y2: float, map_info: Any
) -> float | None:
    """两个归一化点之间的真实距离（米）。缺换算参数返回 None。"""
    span = _world_span(map_info)
    if not span:
        return None
    east = (x2 - x1) * span[0]
    south = (y2 - y1) * span[1]
    return math.hypot(east, south)


def bearing_deg(x1: float, y1: float, x2: float, y2: float) -> float:
    """从点1指向点2的方位角：正北=0，顺时针，0~360。"""
    east = x2 - x1
    north = -(y2 - y1)  # 屏幕 y 向下为南，取反得正北分量
    ang = math.degrees(math.atan2(east, north))
    return ang % 360.0


def heading_from_vector(dx: float | None, dy: float | None) -> float | None:
    """朝向单位向量(dx 向东, dy 向南) -> 航向角（正北=0，顺时针）。"""
    if dx is None or dy is None:
        return None
    if dx == 0 and dy == 0:
        return None
    ang = math.degrees(math.atan2(dx, -dy))
    return ang % 360.0


def relative_bearing(target_bearing: float, own_heading: float | None) -> float | None:
    """目标方位相对自身航向的夹角：-180~180，负=偏左，正=偏右，0=正前。"""
    if own_heading is None:
        return None
    rel = (target_bearing - own_heading + 180.0) % 360.0 - 180.0
    return rel


_COMPASS_8 = ["北", "东北", "东", "东南", "南", "西南", "西", "西北"]


def compass_8(bearing: float | None) -> str | None:
    """绝对方位角 -> 八方位中文（北/东北/...）。"""
    if bearing is None:
        return None
    return _COMPASS_8[int((bearing % 360) / 45 + 0.5) % 8]


def clock_position(relative: float | None) -> int | None:
    """相对方位角(-180~180) -> 时钟钟点(1~12)，12=正前，3=正右，6=正后，9=正左。"""
    if relative is None:
        return None
    c = round(relative / 30.0) % 12
    return 12 if c == 0 else c


def grid_cell(nx: float, ny: float, map_info: Any) -> str | None:
    """归一化坐标 -> 网格标签（如 B4）。缺参数返回 None。"""
    span = _world_span(map_info)
    steps = getattr(map_info, "grid_steps", None)
    zero = getattr(map_info, "grid_zero", None)
    if not span or not steps or not zero or steps[0] == 0 or steps[1] == 0:
        return None
    mn = getattr(map_info, "map_min", None) or (0.0, 0.0)
    # 归一化 -> 世界米坐标，相对网格原点 grid_zero 再按格距取整
    world_x = mn[0] + nx * span[0]
    world_y = mn[1] + ny * span[1]
    col = int((world_x - zero[0]) // steps[0])
    row = int((world_y - zero[1]) // steps[1])
    if not (0 <= col < len(_LETTERS)):
        return None
    return f"{_LETTERS[col]}{row + 1}"


_GROUND_CATEGORY = {
    "LightTank": "坦克",
    "MediumTank": "坦克",
    "HeavyTank": "坦克",
    "TankDestroyer": "反坦克车",
    "SPG": "反坦克车",
    "SPAA": "防空车",
    "Wheeled": "轮式车辆",
    "Truck": "运输车",
    "Structure": "建筑/工事",
}
_AIR_CATEGORY = {
    "Fighter": "战斗机",
    "Bomber": "轰炸机",
    "Assault": "攻击机",
    "Attacker": "攻击机",
}


def unit_category(obj_type: str | None, icon: str | None) -> str | None:
    """把 map_obj 的 (type, icon) 归并成便于展示的中文类别。

    陆战载具按 icon 细分：坦克(轻/中/重) / 反坦克车(坦歼·突击炮) / 防空车(SPAA) /
    轮式车辆 / 运输车 / 建筑工事；飞机按 icon 分战斗机/轰炸机/攻击机。
    无法识别的地面单位回退“未知地面载具”，飞机回退“未知飞机”，其余(机场/占领区等)返回 None。
    """
    icon = icon or ""
    if obj_type == "aircraft":
        return _AIR_CATEGORY.get(icon, "未知飞机")
    if obj_type == "ground_model":
        return _GROUND_CATEGORY.get(icon, "未知地面载具")
    return None


def _has_pos(obj: Any) -> bool:
    return getattr(obj, "x", None) is not None and getattr(obj, "y", None) is not None


# 计入敌我统计的作战单位类型；bombing_point 等是任务标记，不算敌军单位
_UNIT_TYPES = {"aircraft", "ground_model"}
# 对地任务目标点类型 -> 中文标签
_OBJECTIVE_TYPES = {"bombing_point": "轰炸点"}


def analyze_situation(map_objects: list[Any], map_info: Any) -> dict[str, Any]:
    """以“自己(Player 图标)”为中心，产出态势：最近敌机、敌我距离方位等。

    无地图数据/未找到自己时，返回 has_player=False 的最小结构（不抛异常）。
    """
    player = next((o for o in map_objects if getattr(o, "icon", "") == "Player"), None)
    enemies = [
        o for o in map_objects
        if getattr(o, "faction", "") == "enemy"
        and getattr(o, "type", "") in _UNIT_TYPES
        and _has_pos(o)
    ]
    allies = [
        o for o in map_objects
        if getattr(o, "faction", "") == "ally"
        and getattr(o, "type", "") in _UNIT_TYPES
        and _has_pos(o)
    ]
    objectives = [
        o for o in map_objects
        if getattr(o, "type", "") in _OBJECTIVE_TYPES and _has_pos(o)
    ]

    result: dict[str, Any] = {
        "has_player": player is not None,
        "enemy_count": len(enemies),
        "ally_count": len(allies),
        "player": None,
        "nearest_enemy": None,
        "nearest_air_threat": None,  # 最近的敌方飞机（来袭预警，海/陆通用）
        "air_threat_count": 0,
        "enemies": [],
        "ground_targets": [],  # 对地任务目标点（轰炸点等），含坐标/网格，有自己时附距离方位
    }
    px = py = None
    own_heading = None
    if player is not None and _has_pos(player):
        px, py = player.x, player.y
        own_heading = heading_from_vector(getattr(player, "dx", None), getattr(player, "dy", None))
        result["player"] = {
            "x": px, "y": py,
            "heading_deg": round(own_heading, 1) if own_heading is not None else None,
            "grid": grid_cell(px, py, map_info),
        }

    # 对地任务目标点：无论是否找到自己都上报（找到自己时附距离/方位）
    targets: list[dict[str, Any]] = []
    for t in objectives:
        item: dict[str, Any] = {
            "kind": getattr(t, "type", "unknown"),
            "label": _OBJECTIVE_TYPES.get(getattr(t, "type", ""), None),
            "x": t.x, "y": t.y,
            "grid": grid_cell(t.x, t.y, map_info),
            "distance_m": None,
            "bearing_deg": None,
            "relative_deg": None,
        }
        if px is not None:
            dist = distance_m(px, py, t.x, t.y, map_info)
            brg = bearing_deg(px, py, t.x, t.y)
            rel = relative_bearing(brg, own_heading)
            item["distance_m"] = round(dist) if dist is not None else None
            item["bearing_deg"] = round(brg, 1)
            item["relative_deg"] = round(rel, 1) if rel is not None else None
        targets.append(item)
    # 有距离的按距离升序
    targets.sort(key=lambda d: (d["distance_m"] is None, d["distance_m"] or 0))
    result["ground_targets"] = targets

    if player is None or px is None:
        return result

    entries: list[dict[str, Any]] = []
    for e in enemies:
        dist = distance_m(px, py, e.x, e.y, map_info)
        brg = bearing_deg(px, py, e.x, e.y)
        rel = relative_bearing(brg, own_heading)
        e_type = getattr(e, "type", "unknown")
        e_icon = getattr(e, "icon", "none")
        entries.append({
            "icon": e_icon,
            "type": e_type,
            "category": unit_category(e_type, e_icon),
            "x": e.x, "y": e.y,
            "distance_m": round(dist) if dist is not None else None,
            "bearing_deg": round(brg, 1),
            "relative_deg": round(rel, 1) if rel is not None else None,
            "heading_deg": (
                lambda h: round(h, 1) if h is not None else None
            )(heading_from_vector(getattr(e, "dx", None), getattr(e, "dy", None))),
        })

    # 有距离的排前面并按距离升序
    entries.sort(key=lambda d: (d["distance_m"] is None, d["distance_m"] or 0))
    result["enemies"] = entries
    if entries:
        result["nearest_enemy"] = entries[0]
    air = [e for e in entries if e["type"] == "aircraft"]
    result["air_threat_count"] = len(air)
    if air:
        result["nearest_air_threat"] = air[0]
    return result
