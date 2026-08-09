"""POI 搜索抽象层 — 支持高德 / 百度 / Overpass(OSM)。"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Optional, Sequence
import unicodedata

import httpx

from ._routing import haversine_km
from ._coordinates import gcj02_to_wgs84, wgs84_to_gcj02

logger = logging.getLogger(__name__)


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
    matched_term: str = ""     # 哪个召回词命中了该结果


@dataclass
class POIResult:
    """POI 搜索结果。"""
    query: str
    items: List[POIItem] = field(default_factory=list)
    provider: str = ""
    error: str = ""
    searched_terms: tuple[str, ...] = ()


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
        gcj_lat, gcj_lon = wgs84_to_gcj02(lat, lon)
        params = {
            "key": self.api_key,
            "keywords": query,
            "location": f"{gcj_lon:.6f},{gcj_lat:.6f}",
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
                    plat, plon = gcj02_to_wgs84(plat, plon)
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
                plat = float(loc.get("lat", 0))
                plon = float(loc.get("lng", 0))
                if plat or plon:
                    plat, plon = gcj02_to_wgs84(plat, plon)
                items.append(POIItem(
                    name=poi.get("name", ""),
                    address=poi.get("address", ""),
                    type_name=poi.get("detail_info", {}).get("tag", "") if isinstance(detail, dict) else "",
                    distance_m=float(detail.get("distance", 0)) if isinstance(detail, dict) else 0,
                    lat=plat,
                    lon=plon,
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

    _PUBLIC_ENDPOINTS = (
        "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
        "https://overpass.private.coffee/api/interpreter",
        "https://overpass-api.de/api/interpreter",
    )

    # 地图召回词 → 完整 OSM tag filter。存在性查询不能伪装成 ``shop=yes``。
    _TAG_FILTERS: Dict[str, str] = {
        "商店": '["shop"]', "店铺": '["shop"]', "shop": '["shop"]',
        "shops": '["shop"]', "store": '["shop"]', "stores": '["shop"]',
        "餐厅": '["amenity"="restaurant"]', "餐饮": '["amenity"="restaurant"]',
        "火锅": '["amenity"="restaurant"]', "烧烤": '["amenity"="restaurant"]',
        "咖啡": '["amenity"="cafe"]', "咖啡厅": '["amenity"="cafe"]',
        "咖啡馆": '["amenity"="cafe"]', "cafe": '["amenity"="cafe"]',
        "超市": '["shop"="supermarket"]', "便利店": '["shop"="convenience"]',
        "购物中心": '["shop"="mall"]', "商场": '["shop"="mall"]',
        "药店": '["amenity"="pharmacy"]', "医院": '["amenity"="hospital"]',
        "银行": '["amenity"="bank"]', "ATM": '["amenity"="atm"]',
        "酒店": '["tourism"="hotel"]', "宾馆": '["tourism"="hotel"]',
        "景点": '["tourism"="attraction"]', "公园": '["leisure"="park"]',
        "学校": '["amenity"="school"]', "大学": '["amenity"="university"]',
        "加油站": '["amenity"="fuel"]', "停车场": '["amenity"="parking"]',
        "地铁站": '["station"="subway"]', "公交站": '["highway"="bus_stop"]',
        "restaurant": '["amenity"="restaurant"]', "hotel": '["tourism"="hotel"]',
        "park": '["leisure"="park"]',
    }

    def __init__(
        self,
        *,
        endpoints: Optional[Sequence[str]] = None,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self._endpoints = tuple(endpoints or self._PUBLIC_ENDPOINTS)
        self._http_client = http_client

    async def search(
        self, query: str, lat: float, lon: float,
        radius: int = 3000, limit: int = 10, timeout: float = 10.0,
    ) -> List[POIItem]:
        tag_filter = self._TAG_FILTERS.get(query, "")
        if not tag_filter:
            # 通用搜索：用 name 匹配 — 转义正则、Overpass QL 特殊字符和控制字符
            import re
            sanitized = re.sub(r'[\x00-\x1f\x7f]', '', query)  # strip control chars
            escaped = re.sub(r'(["\\\.\*\+\?\(\)\[\]\{\}\|^$])', r'\\\1', sanitized)
            tag_filter = f'["name"~"{escaped}",i]'
        request_timeout = max(3.0, min(timeout + 2.0, 6.0))
        query_timeout = max(1, min(int(timeout), int(request_timeout) - 1))
        overpass_query = f"""
        [out:json][timeout:{query_timeout}];
        (
          node{tag_filter}(around:{radius},{lat},{lon});
          way{tag_filter}(around:{radius},{lat},{lon});
        );
        out center {limit};
        """
        if self._http_client is not None:
            data = await self._request_first_available(
                self._http_client,
                overpass_query,
            )
        else:
            async with httpx.AsyncClient(
                timeout=request_timeout,
                headers={"User-Agent": "N.E.K.O-LifeKit/1.0"},
            ) as client:
                data = await self._request_first_available(client, overpass_query)
        items: List[POIItem] = []
        for el in (data.get("elements") or []):
            try:
                tags = el.get("tags", {})
                name = tags.get("name", "")
                if not name:
                    continue
                raw_lat = el.get("lat")
                raw_lon = el.get("lon")
                if raw_lat is None or raw_lon is None:
                    center = el.get("center", {}) or {}
                    raw_lat = raw_lat if raw_lat is not None else center.get("lat")
                    raw_lon = raw_lon if raw_lon is not None else center.get("lon")
                # 合法坐标可能是 0.0（赤道/本初子午线），所以用显式 None 判定喵
                if raw_lat is None or raw_lon is None:
                    continue
                plat = float(raw_lat)
                plon = float(raw_lon)
                dist = haversine_km(lat, lon, plat, plon) * 1000
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
        return _deduplicate_items(items)[:limit]

    async def _request_first_available(
        self,
        client: httpx.AsyncClient,
        overpass_query: str,
    ) -> dict[str, Any]:
        errors: list[str] = []
        for endpoint_index, endpoint in enumerate(self._endpoints):
            try:
                response = await client.post(endpoint, data={"data": overpass_query})
                response.raise_for_status()
                data = response.json()
                if not isinstance(data, dict):
                    raise ValueError("Overpass response is not an object")
                return data
            except Exception as exc:
                message = f"{endpoint}: {type(exc).__name__}: {exc}"
                errors.append(message)
                logger.warning(
                    "Overpass instance failed: endpoint_index=%s error_type=%s",
                    endpoint_index,
                    type(exc).__name__,
                )
        raise RuntimeError("all Overpass instances failed: " + "; ".join(errors))


def _deduplicate_items(items: List[POIItem]) -> List[POIItem]:
    """Collapse duplicate OSM geometries without merging nearby branches."""
    unique: List[POIItem] = []
    for item in items:
        name_key = _normalise_poi_name(item.name)
        if not _is_duplicate_item(item, unique, name_key=name_key):
            unique.append(item)
    return unique


def _normalise_poi_name(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).strip().casefold().split())


def _is_duplicate_item(
    item: POIItem,
    existing_items: Sequence[POIItem],
    *,
    name_key: str | None = None,
) -> bool:
    candidate_key = name_key if name_key is not None else _normalise_poi_name(item.name)
    return any(
        candidate_key
        and candidate_key == _normalise_poi_name(existing.name)
        and haversine_km(item.lat, item.lon, existing.lat, existing.lon) <= 0.03
        for existing in existing_items
    )


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
        result = POIResult(query=query)
        errors: list[str] = []
        successful_provider = ""
        for provider in self._providers:
            try:
                items = await provider.search(query, lat, lon, radius=radius, limit=limit)
                successful_provider = provider.name
                if items:
                    result.items = items
                    result.provider = provider.name
                    return result
            except Exception as exc:
                message = f"{provider.name}: {type(exc).__name__}: {exc}"
                errors.append(message)
                logger.debug(
                    "POI provider failed: provider=%s error_type=%s",
                    provider.name,
                    type(exc).__name__,
                )
                continue
        if successful_provider:
            result.provider = successful_provider
        elif errors:
            result.error = "; ".join(errors)
        return result

    async def search_many(
        self,
        queries: Sequence[str],
        lat: float,
        lon: float,
        *,
        radius: int = 3000,
        limit: int = 10,
        semaphore: asyncio.Semaphore | None = None,
    ) -> POIResult:
        """Search a semantic retrieval plan and merge results across its terms."""
        clean_queries = tuple(queries)
        result = POIResult(
            query=" / ".join(clean_queries),
            searched_terms=clean_queries,
        )
        if not clean_queries:
            result.error = "search plan contains no usable terms"
            return result

        async def search_term(query: str) -> POIResult:
            if semaphore is None:
                return await self.search(
                    query,
                    lat,
                    lon,
                    radius=radius,
                    limit=min(limit, 8),
                )
            async with semaphore:
                return await self.search(
                    query,
                    lat,
                    lon,
                    radius=radius,
                    limit=min(limit, 8),
                )

        term_results = await asyncio.gather(
            *(
                search_term(query)
                for query in clean_queries
            )
        )
        buckets = [
            [
                replace(item, matched_term=query)
                for item in sorted(term_result.items, key=lambda value: value.distance_m)
            ]
            for query, term_result in zip(clean_queries, term_results)
        ]
        result.items = _balanced_merge(buckets, limit)
        providers = tuple(
            dict.fromkeys(
                term_result.provider
                for term_result in term_results
                if term_result.provider
            )
        )
        result.provider = ",".join(providers)
        errors = [term_result.error for term_result in term_results if term_result.error]
        if not result.items and len(errors) == len(term_results):
            result.error = "; ".join(errors)
        return result


def _balanced_merge(buckets: Sequence[Sequence[POIItem]], limit: int) -> List[POIItem]:
    """Round-robin term buckets so one broad query cannot consume the result set."""
    merged: List[POIItem] = []
    offsets = [0] * len(buckets)
    while len(merged) < limit:
        made_progress = False
        for bucket_index, bucket in enumerate(buckets):
            while offsets[bucket_index] < len(bucket):
                item = bucket[offsets[bucket_index]]
                offsets[bucket_index] += 1
                if not _is_duplicate_item(item, merged):
                    merged.append(item)
                    made_progress = True
                    break
            if len(merged) >= limit:
                break
        if not made_progress:
            break
    return merged
