"""POI 搜索抽象层 — 支持高德 / 百度 / Overpass(OSM)。"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx

from ._routing import haversine_km

logger = logging.getLogger(__name__)

_EARTH_A = 6378245.0
_EARTH_EE = 0.00669342162296594323


@dataclass
class POIItem:
    """一个 POI 结果。"""
    name: str
    address: str = ""
    type_name: str = ""       # "餐饮" / "咖啡厅" / "景点"
    distance_m: float = 0     # 距搜索中心的距离（米）
    lat: float = 0
    lon: float = 0
    tel: str = ""
    rating: str = ""          # 评分（如果有）


@dataclass
class POIResult:
    """POI 搜索结果。"""
    query: str
    items: List[POIItem] = field(default_factory=list)
    provider: str = ""
    error: str = ""


def _outside_china(lat: float, lon: float) -> bool:
    return lon < 72.004 or lon > 137.8347 or lat < 0.8293 or lat > 55.8271


def _transform_lat(x: float, y: float) -> float:
    ret = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(y * math.pi) + 40.0 * math.sin(y / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (160.0 * math.sin(y / 12.0 * math.pi) + 320 * math.sin(y * math.pi / 30.0)) * 2.0 / 3.0
    return ret


def _transform_lon(x: float, y: float) -> float:
    ret = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(x * math.pi) + 40.0 * math.sin(x / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (150.0 * math.sin(x / 12.0 * math.pi) + 300.0 * math.sin(x / 30.0 * math.pi)) * 2.0 / 3.0
    return ret


def _wgs84_to_gcj02(lat: float, lon: float) -> tuple[float, float]:
    if _outside_china(lat, lon):
        return lat, lon
    dlat = _transform_lat(lon - 105.0, lat - 35.0)
    dlon = _transform_lon(lon - 105.0, lat - 35.0)
    radlat = lat / 180.0 * math.pi
    magic = math.sin(radlat)
    magic = 1 - _EARTH_EE * magic * magic
    sqrt_magic = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((_EARTH_A * (1 - _EARTH_EE)) / (magic * sqrt_magic) * math.pi)
    dlon = (dlon * 180.0) / (_EARTH_A / sqrt_magic * math.cos(radlat) * math.pi)
    return lat + dlat, lon + dlon


# ── 高德 POI 搜索 ───────────────────────────────────────────────

class AMapPOI:
    name = "amap"

    def __init__(self, api_key: str):
        self.api_key = api_key

    async def search(
        self, query: str, lat: float, lon: float,
        radius: int = 3000, limit: int = 10, timeout: float = 8.0,
    ) -> List[POIItem]:
        url = "https://restapi.amap.com/v3/place/around"
        center_lat, center_lon = _wgs84_to_gcj02(lat, lon)
        params = {
            "key": self.api_key,
            "keywords": query,
            "location": f"{center_lon:.6f},{center_lat:.6f}",
            "radius": str(min(radius, 50000)),
            "offset": str(min(limit, 25)),
            "sortrule": "distance",
        }
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.get(url, params=params)
            r.raise_for_status()
            data = r.json()
        if data.get("status") != "1":
            raise RuntimeError(data.get("info") or "AMap POI search failed")
        items: List[POIItem] = []
        for poi in (data.get("pois") or []):
            try:
                loc_str = poi.get("location", "")
                plon, plat = 0.0, 0.0
                if "," in loc_str:
                    parts = loc_str.split(",")
                    plon, plat = float(parts[0]), float(parts[1])
                items.append(POIItem(
                    name=poi.get("name", ""),
                    address=poi.get("address", "") if isinstance(poi.get("address"), str) else "",
                    type_name=poi.get("type", "").split(";")[0] if poi.get("type") else "",
                    distance_m=float(poi.get("distance", 0)),
                    lat=plat, lon=plon,
                    tel=poi.get("tel", "") if isinstance(poi.get("tel"), str) else "",
                ))
            except (ValueError, TypeError, KeyError):
                continue
        return items


# ── 百度 POI 搜索 ───────────────────────────────────────────────

class BaiduPOI:
    name = "baidu"

    def __init__(self, api_key: str):
        self.api_key = api_key

    async def search(
        self, query: str, lat: float, lon: float,
        radius: int = 3000, limit: int = 10, timeout: float = 8.0,
    ) -> List[POIItem]:
        url = "https://api.map.baidu.com/place/v2/search"
        params = {
            "ak": self.api_key,
            "query": query,
            "location": f"{lat:.6f},{lon:.6f}",
            "radius": str(min(radius, 50000)),
            "page_size": str(min(limit, 20)),
            "output": "json",
            "scope": "2",
            "coord_type": "1",  # input coords are WGS84
            "ret_coordtype": "gcj02ll",  # output in GCJ-02 (closest to WGS84 available from Baidu)
        }
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.get(url, params=params)
            r.raise_for_status()
            data = r.json()
        if data.get("status") != 0:
            raise RuntimeError(data.get("message") or "Baidu POI search failed")
        items: List[POIItem] = []
        for poi in (data.get("results") or []):
            try:
                loc = poi.get("location", {})
                detail = poi.get("detail_info", {})
                items.append(POIItem(
                    name=poi.get("name", ""),
                    address=poi.get("address", ""),
                    type_name=poi.get("detail_info", {}).get("tag", "") if isinstance(detail, dict) else "",
                    distance_m=float(detail.get("distance", 0)) if isinstance(detail, dict) else 0,
                    lat=float(loc.get("lat", 0)),
                    lon=float(loc.get("lng", 0)),
                    tel=detail.get("phone", "") if isinstance(detail, dict) else "",
                    rating=str(detail.get("overall_rating", "")) if isinstance(detail, dict) else "",
                ))
            except (ValueError, TypeError, KeyError):
                continue
        return items


# ── Overpass (OpenStreetMap) POI 搜索 — 免费无 key ──────────────

class OverpassPOI:
    """Overpass API 搜索 — 免费，无需 key，数据来自 OpenStreetMap。"""
    name = "osm"

    # 常见查询词 → OSM tag 映射
    _TAG_MAP: Dict[str, str] = {
        "餐厅": "amenity=restaurant", "餐饮": "amenity=restaurant",
        "火锅": "amenity=restaurant", "烧烤": "amenity=restaurant",
        "咖啡": "amenity=cafe", "咖啡厅": "amenity=cafe", "cafe": "amenity=cafe",
        "超市": "shop=supermarket", "便利店": "shop=convenience",
        "药店": "amenity=pharmacy", "医院": "amenity=hospital",
        "银行": "amenity=bank", "ATM": "amenity=atm",
        "酒店": "tourism=hotel", "宾馆": "tourism=hotel",
        "景点": "tourism=attraction", "公园": "leisure=park",
        "学校": "amenity=school", "大学": "amenity=university",
        "加油站": "amenity=fuel", "停车场": "amenity=parking",
        "地铁站": "station=subway", "公交站": "highway=bus_stop",
        "restaurant": "amenity=restaurant", "hotel": "tourism=hotel",
        "park": "leisure=park", "shop": "shop=yes",
    }

    async def search(
        self, query: str, lat: float, lon: float,
        radius: int = 3000, limit: int = 10, timeout: float = 10.0,
    ) -> List[POIItem]:
        tag = self._TAG_MAP.get(query, "")
        if not tag:
            # 通用搜索：用 name 匹配 — 转义正则、Overpass QL 特殊字符和控制字符
            import re
            sanitized = re.sub(r'[\x00-\x1f\x7f]', '', query)  # strip control chars
            escaped = re.sub(r'(["\\\.\*\+\?\(\)\[\]\{\}\|^$])', r'\\\1', sanitized)
            tag_filter = f'["name"~"{escaped}",i]'
        else:
            k, v = tag.split("=", 1)
            tag_filter = f'["{k}"="{v}"]'

        overpass_query = f"""
        [out:json][timeout:{int(timeout)}];
        (
          node{tag_filter}(around:{radius},{lat},{lon});
          way{tag_filter}(around:{radius},{lat},{lon});
        );
        out center {limit};
        """
        async with httpx.AsyncClient(timeout=timeout + 2) as c:
            r = await c.post(
                "https://overpass-api.de/api/interpreter",
                data={"data": overpass_query},
            )
            r.raise_for_status()
            data = r.json()
        items: List[POIItem] = []
        for el in (data.get("elements") or []):
            try:
                tags = el.get("tags", {})
                name = tags.get("name", "")
                if not name:
                    continue
                plat = float(el.get("lat") or el.get("center", {}).get("lat", 0))
                plon = float(el.get("lon") or el.get("center", {}).get("lon", 0))
                dist = haversine_km(lat, lon, plat, plon) * 1000 if plat and plon else 0
                addr_parts = [tags.get("addr:street", ""), tags.get("addr:housenumber", "")]
                items.append(POIItem(
                    name=name,
                    address=" ".join(p for p in addr_parts if p).strip(),
                    type_name=tags.get("cuisine", tags.get("shop", tags.get("amenity", ""))),
                    distance_m=dist,
                    lat=plat, lon=plon,
                    tel=tags.get("phone", ""),
                ))
            except (ValueError, TypeError, KeyError):
                continue
        items.sort(key=lambda x: x.distance_m)
        return items[:limit]


# ── POI 搜索调度器 ──────────────────────────────────────────────

class POIService:
    """根据配置选择 provider 搜索 POI。"""

    def __init__(self, cfg: Dict[str, Any]):
        self._providers: list = []
        amap_key = str(cfg.get("amap_key", "")).strip()
        if amap_key:
            self._providers.append(AMapPOI(amap_key))
        baidu_key = str(cfg.get("baidu_map_key", "")).strip()
        if baidu_key:
            self._providers.append(BaiduPOI(baidu_key))
        self._providers.append(OverpassPOI())

    async def search(
        self, query: str, lat: float, lon: float,
        radius: int = 3000, limit: int = 10,
    ) -> POIResult:
        if radius <= 0:
            raise ValueError("radius must be positive")
        if limit <= 0:
            raise ValueError("limit must be positive")
        radius = min(int(radius), 50000)
        limit = min(int(limit), 50)
        result = POIResult(query=query)
        errors: list[str] = []
        for provider in self._providers:
            try:
                items = await provider.search(query, lat, lon, radius=radius, limit=limit)
                if items:
                    if provider.name == "osm":
                        for item in items:
                            item.lat, item.lon = _wgs84_to_gcj02(item.lat, item.lon)
                    result.items = items
                    result.provider = provider.name
                    return result
            except Exception as exc:
                message = f"{provider.name}: provider error"
                errors.append(message)
                logger.debug("POI provider failed: %s", provider.name, exc_info=True)
                continue
        if errors:
            result.error = "; ".join(errors)
        return result
