"""路线规划抽象层 — 支持多 provider（高德 / 百度 / OSRM）。"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol

import httpx

from ._advice_policy import DEFAULT_ADVICE_POLICY
from ._coordinates import wgs84_to_gcj02
from ._geodesy import haversine_km
from ._hedged import ordered_hedged_first

logger = logging.getLogger(__name__)
ROUTING_TOTAL_TIMEOUT_SECONDS = 8.0
ROUTING_PROVIDER_HEDGE_SECONDS = 0.1


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


class RoutingProviderError(RuntimeError):
    """Provider-level route planning failure."""

    def __init__(self, provider: str, detail: str):
        self.provider = provider
        self.detail = _sanitize_error_detail(detail)
        super().__init__(f"{provider}: {self.detail}")


def _sanitize_error_detail(detail: str) -> str:
    text = " ".join(str(detail).split())
    return text[:160] or "provider error"


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


def _transit_mode(provider_type: object) -> str:
    token = str(provider_type or "").strip().casefold()
    if token in {"subway", "metro", "underground"}:
        return "subway"
    if token in {"bus", "coach"}:
        return "bus"
    return "transit"


def suggest_modes(distance_km: float) -> List[str]:
    """根据距离建议合理的出行方式。"""
    return list(DEFAULT_ADVICE_POLICY.route_modes(distance_km))


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
        # 高德 API expects GCJ-02 while LifeKit's location boundary is WGS84.
        origin_gcj_lat, origin_gcj_lon = wgs84_to_gcj02(origin_lat, origin_lon)
        dest_gcj_lat, dest_gcj_lon = wgs84_to_gcj02(dest_lat, dest_lon)
        origin = f"{origin_gcj_lon:.6f},{origin_gcj_lat:.6f}"
        dest = f"{dest_gcj_lon:.6f},{dest_gcj_lat:.6f}"

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
                if r.status_code >= 400:
                    raise RoutingProviderError(self.name, f"HTTP {r.status_code}")
                data = r.json()
            if data.get("status") != "1":
                raise RoutingProviderError(self.name, data.get("info") or data.get("infocode") or "API status error")
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
                            steps.append(RouteStep(instruction="", distance_m=wd, duration_s=wt, mode="walk"))
                    # 公交/地铁段
                    bus = seg.get("bus", {})
                    buslines = bus.get("buslines") or []
                    for bl in buslines[:1]:
                        name = bl.get("name", "")
                        bd = float(bl.get("distance", 0))
                        bt = float(bl.get("duration", 0))
                        m = _transit_mode(bl.get("type"))
                        steps.append(RouteStep(instruction="", distance_m=bd, duration_s=bt, mode=m, line_name=name))
                        line_names.append(name)
                dist = float(transit.get("distance", 0))
                dur = float(transit.get("duration", 0))
                cost = transit.get("cost", "")
                summary = " → ".join(line_names[:4])
                routes.append(Route(mode="transit", distance_m=dist, duration_s=dur, steps=steps, summary=summary, cost=str(cost)))
            return routes
        except RoutingProviderError:
            raise
        except Exception as exc:
            raise RoutingProviderError(self.name, f"{type(exc).__name__}: {exc}") from exc

    async def _simple(self, url: str, origin: str, dest: str, mode: str, timeout: float) -> List[Route]:
        params = {"key": self.api_key, "origin": origin, "destination": dest}
        try:
            async with httpx.AsyncClient(timeout=timeout) as c:
                r = await c.get(url, params=params)
                if r.status_code >= 400:
                    raise RoutingProviderError(self.name, f"HTTP {r.status_code}")
                data = r.json()
            if data.get("status") != "1":
                raise RoutingProviderError(self.name, data.get("info") or data.get("infocode") or "API status error")
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
        except RoutingProviderError:
            raise
        except Exception as exc:
            raise RoutingProviderError(self.name, f"{type(exc).__name__}: {exc}") from exc

    async def _bicycling(self, origin: str, dest: str, timeout: float) -> List[Route]:
        url = "https://restapi.amap.com/v4/direction/bicycling"
        params = {"key": self.api_key, "origin": origin, "destination": dest}
        try:
            async with httpx.AsyncClient(timeout=timeout) as c:
                r = await c.get(url, params=params)
                if r.status_code >= 400:
                    raise RoutingProviderError(self.name, f"HTTP {r.status_code}")
                data = r.json()
            if data.get("errcode") != 0:
                raise RoutingProviderError(self.name, data.get("errmsg") or f"API errcode {data.get('errcode')}")
            paths = data.get("data", {}).get("paths") or []
            routes: List[Route] = []
            for path in paths[:2]:
                dist = float(path.get("distance", 0))
                dur = float(path.get("duration", 0))
                routes.append(Route(mode="bicycling", distance_m=dist, duration_s=dur))
            return routes
        except RoutingProviderError:
            raise
        except Exception as exc:
            raise RoutingProviderError(self.name, f"{type(exc).__name__}: {exc}") from exc


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
        # 输入坐标来自 Open-Meteo / Nominatim，是 WGS84；百度 Direction Lite 默认按 bd09ll 解析，
        # 不显式传 coord_type 会让 walking/bicycling/driving 也按 bd09ll 起算，规划出从偏移点出发的路线喵。
        params: Dict[str, str] = {
            "ak": self.api_key,
            "origin": origin,
            "destination": dest,
            "coord_type": "wgs84",
        }
        try:
            async with httpx.AsyncClient(timeout=timeout) as c:
                r = await c.get(url, params=params)
                if r.status_code >= 400:
                    raise RoutingProviderError(self.name, f"HTTP {r.status_code}")
                data = r.json()
            if data.get("status") != 0:
                raise RoutingProviderError(self.name, data.get("message") or f"API status {data.get('status')}")
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
                                    instruction="",
                                    distance_m=float(item.get("distance", 0)),
                                    duration_s=float(item.get("duration", 0)),
                                    mode=_transit_mode(veh.get("type")),
                                    line_name=name,
                                ))
                    summary = " → ".join(line_names[:4])
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
        except RoutingProviderError:
            raise
        except Exception as exc:
            raise RoutingProviderError(self.name, f"{type(exc).__name__}: {exc}") from exc


# ── OSRM Provider（免费，无需 key，不支持公交）───────────────────

class OSRMProvider:
    """OSRM 公共实例（驾车/步行/骑行，无公交）。"""
    name = "osrm"
    supports_transit = False
    _DEFAULT_BASE_URL = "https://router.project-osrm.org"
    _PUBLIC_MODE_BASES = {
        "driving": _DEFAULT_BASE_URL,
        "walking": "https://routing.openstreetmap.de/routed-foot",
        "bicycling": "https://routing.openstreetmap.de/routed-bike",
    }

    def __init__(self, base_url: str = _DEFAULT_BASE_URL):
        self.base_url = base_url.rstrip("/")

    async def plan_route(
        self, origin_lat: float, origin_lon: float,
        dest_lat: float, dest_lon: float,
        mode: str, timeout: float = 10.0,
    ) -> List[Route]:
        if mode == "transit":
            return []  # OSRM 不支持公交
        # OSRM's profile segment names the server's loaded dataset, not a
        # universal travel mode. The default car demo cannot produce honest
        # foot/bike routes, so use the OSM.de mode-specific public instances.
        if self.base_url == self._DEFAULT_BASE_URL:
            base_url = self._PUBLIC_MODE_BASES.get(mode, self._DEFAULT_BASE_URL)
            profile = "driving"
        else:
            base_url = self.base_url
            profile = {"driving": "driving", "bicycling": "bike", "walking": "foot"}.get(
                mode, "driving",
            )
        coords = f"{origin_lon},{origin_lat};{dest_lon},{dest_lat}"
        url = f"{base_url}/route/v1/{profile}/{coords}"
        params = {"overview": "false", "steps": "true"}
        try:
            async with httpx.AsyncClient(timeout=timeout) as c:
                r = await c.get(url, params=params)
                if r.status_code >= 400:
                    raise RoutingProviderError(self.name, f"HTTP {r.status_code}")
                data = r.json()
            code = data.get("code")
            if code == "NoRoute":
                return []
            if code != "Ok":
                raise RoutingProviderError(self.name, str(code or "API code error"))
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
        except RoutingProviderError:
            raise
        except Exception as exc:
            raise RoutingProviderError(self.name, f"{type(exc).__name__}: {exc}") from exc


# ── 路线规划调度器 ───────────────────────────────────────────────

class RoutingService:
    """根据配置选择 provider，规划多种出行方式。"""

    def __init__(self, cfg: Dict[str, Any]):
        self._providers: List[RoutingProvider] = []
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
        if not modes:
            modes = suggest_modes(dist_km)

        async def plan_mode(mode: str) -> tuple[list[Route], str, list[str]]:
            providers = [
                provider
                for provider in self._providers
                if mode != "transit" or provider.supports_transit
            ]
            errors: list[str] = []
            if not providers:
                return [], "", [f"no_provider:{mode}"]

            async def call_provider(
                provider: RoutingProvider,
            ) -> tuple[RoutingProvider, list[Route], str]:
                try:
                    routes = await provider.plan_route(
                        origin_lat,
                        origin_lon,
                        dest_lat,
                        dest_lon,
                        mode,
                        timeout=ROUTING_TOTAL_TIMEOUT_SECONDS,
                    )
                    return provider, routes, ""
                except RoutingProviderError as exc:
                    logger.debug("Routing provider failed: provider=%s mode=%s detail=%s", exc.provider, mode, exc.detail, exc_info=True)
                    return provider, [], f"{exc.provider}:{mode}:{exc.detail}"

            outcome = await ordered_hedged_first(
                tuple(
                    lambda provider=provider: call_provider(provider)
                    for provider in providers
                ),
                accept=lambda value: bool(value[1]),
                hedge_delay=ROUTING_PROVIDER_HEDGE_SECONDS,
                total_timeout=ROUTING_TOTAL_TIMEOUT_SECONDS,
            )
            if outcome.winner is not None:
                provider, routes, _ = outcome.winner
                return routes, provider.name, errors
            for _, (_, _, error) in outcome.completed:
                if error:
                    errors.append(error)
            errors.extend(
                f"{providers[index].name}:{mode}:timeout"
                for index in outcome.timed_out_indices
            )
            return [], "", errors

        result = RoutingResult(origin_name="", destination_name="")
        tasks = [asyncio.create_task(plan_mode(mode)) for mode in modes]
        done: set[asyncio.Task[tuple[list[Route], str, list[str]]]] = set()
        pending: set[asyncio.Task[tuple[list[Route], str, list[str]]]] = set(tasks)
        try:
            done, pending = await asyncio.wait(
                tasks,
                timeout=ROUTING_TOTAL_TIMEOUT_SECONDS,
            )
            errors: list[str] = []
            providers: list[str] = []
            for task in tasks:
                if task not in done:
                    continue
                routes, provider_name, mode_errors = task.result()
                result.routes.extend(routes)
                errors.extend(mode_errors)
                if provider_name and provider_name not in providers:
                    providers.append(provider_name)
            result.provider = ",".join(providers)
            if pending and not result.routes:
                result.error = "timeout:routing budget exceeded"
            elif not result.routes and errors:
                result.error = f"provider_error:{','.join(errors)}"
        finally:
            for task in pending:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        return result
