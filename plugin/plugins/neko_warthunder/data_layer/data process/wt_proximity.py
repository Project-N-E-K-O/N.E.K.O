"""敌军接近告警（边沿触发）。

map_obj.json 中的敌军单位没有唯一 ID，本模块通过【跨帧位置最近邻 + 同图标】
把相邻两帧的敌军关联为同一单位，从而实现“边沿触发”：
只在某敌军【从告警距离外进入距离内】或【在距离内首次出现】时报告一次，
而不是只要在范围内就每帧重复报。

距离阈值随【我方兵种 × 敌方类型】变化，由 resolve_proximity_thresholds 依据
vehicle_profiles.json 解析为 (对空中敌人, 对地面/海面敌人) 两个阈值；某项为 None
表示对该类敌人不告警（如陆/海军对来袭飞机不在此告警）。

输入复用 analyze_situation 的 enemies 列表（每项含 x,y,distance_m,bearing_deg,
relative_deg,icon,type），避免重复计算。
"""

from __future__ import annotations

import math
from typing import Any

from wt_geo import clock_position, compass_8
from wt_processor import _merge_profile


def _pos_num(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and v > 0 else None


def resolve_proximity_thresholds(
    profiles: dict[str, Any] | None,
    domain: str | None,
    vtype: str | None,
    family_rules: list[dict[str, Any]] | None = None,
) -> tuple[float | None, float | None]:
    """解析接近告警距离，返回 (对空中敌人, 对地面/海面敌人) 两个阈值(米)。

    任一为 None 表示对该类敌人不告警。规则（随我方兵种与敌方类型变化）：
      - 我方空军/直升机：仅对空中敌人告警，距离 = 型号 proximity_warn_m
        （螺旋桨/早期喷气 2km、现代喷气 5km），未识别则回退 _proximity.air_default
        （直升机回退 _proximity.heli_default）；对地面/海面单位不告警
        （map_obj 中坦克与舰船同为 ground_model 无法可靠区分，接近告警核心是防空中偷袭）。
      - 我方陆军：对地面敌人用 _proximity.ground.vs_ground(500m)，对空中敌人不告警。
      - 我方海军：对水面敌人用 _proximity.naval.vs_ground(2km)，对空中敌人不告警。
    """
    if not isinstance(profiles, dict):
        return (None, None)
    prox = profiles.get("_proximity", {})
    prox = prox if isinstance(prox, dict) else {}

    if domain in ("air", "heli"):
        cfg, matched, _source, _family = _merge_profile(
            profiles, vtype, "air" if domain == "air" else None, family_rules or []
        )
        t = _pos_num(cfg.get("proximity_warn_m")) if matched else None
        if t is None:
            key = "heli_default" if domain == "heli" else "air_default"
            t = _pos_num(prox.get(key)) or _pos_num(prox.get("air_default")) or 3000.0
        return (t, None)

    sub = prox.get(domain, {}) if domain else {}
    sub = sub if isinstance(sub, dict) else {}
    return (_pos_num(sub.get("vs_air")), _pos_num(sub.get("vs_ground")))


class ProximityTracker:
    """跨帧追踪敌军并产出“接近”边沿事件。

    assoc_dist 为跨帧关联的最大归一化位移（0~1 坐标系）：两帧间同一单位的
    位移应小于此值才会被关联；过大易误关联不同单位，过小会漏关联快速目标。
    map 组轮询约 0.5s，0.06 对应地图边长的 6%，足以覆盖高速目标的一帧位移。
    """

    def __init__(self, assoc_dist: float = 0.06) -> None:
        self.assoc_dist = assoc_dist
        self.reset()

    def reset(self) -> None:
        self._tracks: list[dict[str, Any]] = []
        self._primed = False  # 首次 update 仅建立基线，不报（避免进场/刷新刷屏）
        self._seq = 0

    def update(
        self,
        enemies: list[dict[str, Any]],
        thr_air: float | None,
        thr_ground: float | None,
        now: float,
    ) -> list[dict[str, Any]]:
        """喂入本帧敌军列表，返回本帧新触发的接近事件（边沿触发）。

        enemies: analyze_situation 产出的 enemies（含 x,y,distance_m,bearing_deg...）。
        thr_air: 对空中敌人(type==aircraft)的告警距离；None 表示不告警。
        thr_ground: 对地面/海面敌人的告警距离；None 表示不告警。
        两者均为 None/越界时仍会更新轨迹基线，只是不产生事件。
        """
        events: list[dict[str, Any]] = []
        used: set[int] = set()
        new_tracks: list[dict[str, Any]] = []

        for e in enemies:
            ex, ey = e.get("x"), e.get("y")
            dist = e.get("distance_m")
            icon = e.get("icon")
            is_air = e.get("type") == "aircraft"
            thr = thr_air if is_air else thr_ground
            in_range = thr is not None and dist is not None and dist <= thr

            # 关联：在未匹配的旧轨迹中，找同图标且位移最小者
            best_i: int | None = None
            best_d = self.assoc_dist
            if ex is not None and ey is not None:
                for i, t in enumerate(self._tracks):
                    if i in used or t["icon"] != icon:
                        continue
                    if t["x"] is None or t["y"] is None:
                        continue
                    d = math.hypot(ex - t["x"], ey - t["y"])
                    if d < best_d:
                        best_d = d
                        best_i = i

            if best_i is not None:
                used.add(best_i)
                prev_in = self._tracks[best_i]["in_range"]
                if in_range and not prev_in:  # 边沿：范围外 -> 范围内
                    events.append(self._make_event(e, thr, now, "enter"))
            else:
                # 新单位：仅在已建立基线后、且一出现就在范围内时报告
                if self._primed and in_range:
                    events.append(self._make_event(e, thr, now, "appear"))

            new_tracks.append({"x": ex, "y": ey, "icon": icon, "in_range": in_range})

        self._tracks = new_tracks
        self._primed = True
        return events

    def _make_event(
        self, e: dict[str, Any], threshold_m: float | None, now: float, kind: str
    ) -> dict[str, Any]:
        self._seq += 1
        rel = e.get("relative_deg")
        brg = e.get("bearing_deg")
        return {
            "id": self._seq,
            "ts": now,
            "kind": kind,  # enter=穿越进入 / appear=范围内新出现
            "icon": e.get("icon"),
            "type": e.get("type"),
            "category": e.get("category"),  # 中文兵种类别（坦克/反坦克车/防空车/...）
            "is_air": e.get("type") == "aircraft",
            "distance_m": e.get("distance_m"),
            "bearing_deg": brg,          # 绝对方位角（正北=0，顺时针）
            "compass": compass_8(brg),   # 八方位中文
            "relative_deg": rel,         # 相对自身航向（-180~180，负左正右）
            "clock": clock_position(rel),  # 时钟方位（12=正前）
            "threshold_m": round(threshold_m) if threshold_m else None,
        }
