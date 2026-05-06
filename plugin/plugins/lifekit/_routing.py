"""路线规划抽象层 — 支持多 provider（高德 / 百度 / OSRM）。"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol

import httpx


# ── 数据模型 ─────────────────────────────────────────────────────

@dataclass
class RouteStep:
    """路线中的一步。"""
    instruction: str       # "步行至 陆家嘴站" / "乘坐 地铁2号线 3站"
    distance_m: float      # 米
    duration_s: float      # 秒
    mode: str              # "walk" | "bus" | "subway" | "bike" | "drive"
    line_name: str = ""    # 公交/地铁线路名


@dataclass
class Route:
    """一条完整路线方案。"""
    mode: str              # "transit" | "walking" | "bicycling" | "driving"
    distance_m: float
    duration_s: float
    steps: List[RouteStep] = field(default_factory=list)
    summary: str = ""      # "地铁2号线 → 步行" 之类的概要
    cost: str = ""         # 费用估算


@dataclass
class RoutingResult:
    """路线规划结果。"""
    origin_name: str
    destination_name: str
    routes: List[Route] = field(default_factory=list)
    provider: str = ""
    error: str = ""


# ── Provider 协议 ────────────────────────────────────────────────

class RoutingProvider(Protocol):
    """路线规划 provider 接口。"""
    name: str
    supports_transit: bool

    async def plan_route(
        self,
        origin_lat: float, origin_lon: float,
        dest_lat: float, dest_lon: float,
        mode: str,  # "transit" | "walking" | "bicycling" | "driving"
        timeout: float = 10.0,
    ) -> List[Route]: ...


# ── 工具函数 ─────────────────────────────────────────────────────

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """两点间直线距离（公里）。"""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def format_duration(seconds: float) -> str:
    m = int(seconds / 60)
    if m < 60:
        return f"{m}min"
    h, m = divmod(m, 60)
    return f"{h}h{m}min" if m else f"{h}h"


def format_distance(meters: float) -> str:
    if meters < 1000:
        return f"{int(meters)}m"
    return f"{meters / 1000:.1f}km"


def suggest_modes(distance_km: float) -> List[str]:
    """根据距离建议合理的出行方式。"""
    modes = []
    if distance_km <= 2:
        modes.append("walking")
    if distance_km <= 10:
        modes.append("bicycling")
    if distance_km >= 2:
        modes.append("transit")
    if distance_km >= 5:
        modes.append("driving")
    return modes or ["transit", "driving"]


# ── 高德地图 Provider ────────────────────────────────────────────

class AMapProvider:
    """高德地图路线规划（公交/步行/骑行/驾车全支持）。"""
    name = "amap"
    supports_transit = True

    def __init__(self, api_key: str):
        self.api_key = api_key
        self._base = "https://restapi.amap.com/v3/direction"

    async def plan_route(
        self, origin_lat: float, origin_lon: float,
        dest_lat: float, dest_lon: float,
        mode: str, timeout: float = 10.0,
    ) -> List[Route]:
        # 高德坐标格式: lon,lat
        origin = f"{origin_lon:.6f},{origin_lat:.6f}"
        dest = f"{dest_lon:.6f},{dest_lat:.6f}"

        if mode == "transit":
            return await self._transit(origin, dest, timeout)
        elif mode == "walking":
            return await self._simple(f"{self._base}/walking", origin, dest, "walking", timeout)
        elif mode == "bicycling":
            return await self._bicycling(origin, dest, timeout)
        elif mode == "driving":
            return await self._simple(f"{self._base}/driving", origin, dest, "driving", timeout)
        return []

    async def _transit(self, origin: str, dest: str, timeout: float) -> List[Route]:
        url = f"{self._base}/transit/integrated"
        params = {"key": self.api_key, "origin": origin, "destination": dest, "city": "全国", "strategy": "0"}
        try:
            async with httpx.AsyncClient(timeout=timeout) as c:
                r = await c.get(url, params=params)
                data = r.json()
            if data.get("status") != "1":
                return []
            routes: List[Route] = []
            for transit in (data.get("route", {}).get("transits") or [])[:3]:
                steps: List[RouteStep] = []
                segments = transit.get("segments") or []
                line_names: List[str] = []
                for seg in segments:
                    # 步行段
                    walking = seg.get("walking")
                    if walking:
                        wd = float(walking.get("distance", 0))
                        wt = float(walking.get("duration", 0))
                        if wd > 30:
                            steps.append(RouteStep(instruction=f"步行 {format_distance(wd)}", distance_m=wd, duration_s=wt, mode="walk"))
                    # 公交/地铁段
                    bus = seg.get("bus", {})
                    buslines = bus.get("buslines") or []
                    for bl in buslines[:1]:
                        name = bl.get("name", "")
                        via = bl.get("via_num", 0)
                        bd = float(bl.get("distance", 0))
                        bt = float(bl.get("duration", 0))
                        is_subway = "地铁" in name or "号线" in name
                        m = "subway" if is_subway else "bus"
                        instr = f"乘坐 {name}" + (f" {via}站" if via else "")
                        steps.append(RouteStep(instruction=instr, distance_m=bd, duration_s=bt, mode=m, line_name=name))
                        line_names.append(name)
                dist = float(transit.get("distance", 0))
                dur = float(transit.get("duration", 0))
                cost = transit.get("cost", "")
                summary = " → ".join(line_names[:4]) if line_names else "公交"
                routes.append(Route(mode="transit", distance_m=dist, duration_s=dur, steps=steps, summary=summary, cost=str(cost)))
            return routes
        except Exception:
            return []

    async def _simple(self, url: str, origin: str, dest: str, mode: str, timeout: float) -> List[Route]:
        params = {"key": self.api_key, "origin": origin, "destination": dest}
        try:
            async with httpx.AsyncClient(timeout=timeout) as c:
                r = await c.get(url, params=params)
                data = r.json()
            if data.get("status") != "1":
                return []
            paths = data.get("route", {}).get("paths") or []
            routes: List[Route] = []
            for path in paths[:2]:
                dist = float(path.get("distance", 0))
                dur = float(path.get("duration", 0))
                steps: List[RouteStep] = []
                for s in (path.get("steps") or []):
                    steps.append(RouteStep(
                        instruction=s.get("instruction", ""),
                        distance_m=float(s.get("distance", 0)),
                        duration_s=float(s.get("duration", 0)),
                        mode=mode,
                    ))
                routes.append(Route(mode=mode, distance_m=dist, duration_s=dur, steps=steps))
            return routes
        except Exception:
            return []

    async def _bicycling(self, origin: str, dest: str, timeout: float) -> List[Route]:
        url = "https://restapi.amap.com/v4/direction/bicycling"
        params = {"key": self.api_key, "origin": origin, "destination": dest}
        try:
            async with httpx.AsyncClient(timeout=timeout) as c:
                r = await c.get(url, params=params)
                data = r.json()
            if data.get("errcode") != 0:
                return []
            paths = data.get("data", {}).get("paths") or []
            routes: List[Route] = []
            for path in paths[:2]:
                dist = float(path.get("distance", 0))
                dur = float(path.get("duration", 0))
                routes.append(Route(mode="bicycling", distance_m=dist, duration_s=dur))
            return routes
        except Exception:
            return []


# ── 百度地图 Provider ────────────────────────────────────────────

class BaiduMapProvider:
    """百度地图路线规划。"""
    name = "baidu"
    supports_transit = True

    def __init__(self, api_key: str):
        self.api_key = api_key

    async def plan_route(
        self, origin_lat: float, origin_lon: float,
        dest_lat: float, dest_lon: float,
        mode: str, timeout: float = 10.0,
    ) -> List[Route]:
        # 百度坐标格式: lat,lon
        origin = f"{origin_lat:.6f},{origin_lon:.6f}"
        dest = f"{dest_lat:.6f},{dest_lon:.6f}"
        url_map = {
            "transit": "https://api.map.baidu.com/direction/v2/transit",
            "walking": "https://api.map.baidu.com/directionlite/v1/walking",
            "bicycling": "https://api.map.baidu.com/directionlite/v1/riding",
            "driving": "https://api.map.baidu.com/directionlite/v1/driving",
        }
        url = url_map.get(mode)
        if not url:
            return []
        params: Dict[str, str] = {
            "ak": self.api_key,
            "origin": origin,
            "destination": dest,
            "coord_type": "wgs84",
        }
        try:
            async with httpx.AsyncClient(timeout=timeout) as c:
                r = await c.get(url, params=params)
                data = r.json()
            if data.get("status") != 0:
                return []
            result = data.get("result", {})
            if mode == "transit":
                routes: List[Route] = []
                for plan in (result.get("routes") or [])[:3]:
                    dist = float(plan.get("distance", 0))
                    dur = float(plan.get("duration", 0))
                    price = plan.get("price", "")
                    steps: List[RouteStep] = []
                    line_names: List[str] = []
                    for seg in (plan.get("steps") or []):
                        for item in seg if isinstance(seg, list) else [seg]:
                            veh = item.get("vehicle", {})
                            name = veh.get("name", "")
                            if name:
                                line_names.append(name)
                                steps.append(RouteStep(
                                    instruction=f"乘坐 {name}",
                                    distance_m=float(item.get("distance", 0)),
                                    duration_s=float(item.get("duration", 0)),
                                    mode="subway" if "地铁" in name else "bus",
                                    line_name=name,
                                ))
                    summary = " → ".join(line_names[:4]) if line_names else "公交"
                    routes.append(Route(mode="transit", distance_m=dist, duration_s=dur, steps=steps, summary=summary, cost=str(price)))
                return routes
            else:
                routes_data = result.get("routes") or []
                routes = []
                for rd in routes_data[:2]:
                    dist = float(rd.get("distance", 0))
                    dur = float(rd.get("duration", 0))
                    routes.append(Route(mode=mode, distance_m=dist, duration_s=dur))
                return routes
        except Exception:
            return []


# ── OSRM Provider（免费，无需 key，不支持公交）───────────────────

class OSRMProvider:
    """OSRM 公共实例（驾车/步行/骑行，无公交）。"""
    name = "osrm"
    supports_transit = False

    def __init__(self, base_url: str = "https://router.project-osrm.org"):
        self.base_url = base_url.rstrip("/")

    async def plan_route(
        self, origin_lat: float, origin_lon: float,
        dest_lat: float, dest_lon: float,
        mode: str, timeout: float = 10.0,
    ) -> List[Route]:
        if mode == "transit":
            return []  # OSRM 不支持公交
        profile_map = {"driving": "driving", "bicycling": "cycling", "walking": "walking"}
        profile = profile_map.get(mode, "car")
        coords = f"{origin_lon},{origin_lat};{dest_lon},{dest_lat}"
        url = f"{self.base_url}/route/v1/{profile}/{coords}"
        params = {"overview": "false", "steps": "true"}
        try:
            async with httpx.AsyncClient(timeout=timeout) as c:
                r = await c.get(url, params=params)
                data = r.json()
            if data.get("code") != "Ok":
                return []
            routes: List[Route] = []
            for rd in (data.get("routes") or [])[:2]:
                dist = float(rd.get("distance", 0))
                dur = float(rd.get("duration", 0))
                steps: List[RouteStep] = []
                for leg in (rd.get("legs") or []):
                    for s in (leg.get("steps") or []):
                        name = s.get("name", "")
                        instr = s.get("maneuver", {}).get("type", "")
                        steps.append(RouteStep(
                            instruction=f"{instr} {name}".strip(),
                            distance_m=float(s.get("distance", 0)),
                            duration_s=float(s.get("duration", 0)),
                            mode=mode,
                        ))
                routes.append(Route(mode=mode, distance_m=dist, duration_s=dur, steps=steps))
            return routes
        except Exception:
            return []


# ── 路线规划调度器 ───────────────────────────────────────────────

class RoutingService:
    """根据配置选择 provider，规划多种出行方式。"""

    def __init__(self, cfg: Dict[str, Any]):
        self._providers: List[Any] = []
        # 高德
        amap_key = str(cfg.get("amap_key", "")).strip()
        if amap_key:
            self._providers.append(AMapProvider(amap_key))
        # 百度
        baidu_key = str(cfg.get("baidu_map_key", "")).strip()
        if baidu_key:
            self._providers.append(BaiduMapProvider(baidu_key))
        # OSRM（始终可用作 fallback）
        self._providers.append(OSRMProvider())

    @property
    def has_transit(self) -> bool:
        return any(getattr(p, "supports_transit", False) for p in self._providers)

    async def plan(
        self,
        origin_lat: float, origin_lon: float,
        dest_lat: float, dest_lon: float,
        modes: Optional[List[str]] = None,
    ) -> RoutingResult:
        dist_km = haversine_km(origin_lat, origin_lon, dest_lat, dest_lon)
        if modes is None:
            modes = suggest_modes(dist_km)

        result = RoutingResult(origin_name="", destination_name="")
        for mode in modes:
            for provider in self._providers:
                if mode == "transit" and not getattr(provider, "supports_transit", False):
                    continue
                try:
                    routes = await provider.plan_route(origin_lat, origin_lon, dest_lat, dest_lon, mode)
                    if routes:
                        result.routes.extend(routes)
                        result.provider = provider.name
                        break
                except Exception:
                    continue
        return result
