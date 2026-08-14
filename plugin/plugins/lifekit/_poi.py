"""POI 搜索抽象层 — 支持高德 / 百度 / Overpass(OSM)。"""

from __future__ import annotations

import asyncio
import logging
import unicodedata
from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Optional, Protocol, Sequence

import httpx

from ._coordinates import gcj02_to_wgs84, wgs84_to_gcj02
from ._geodesy import haversine_km
from ._hedged import ordered_hedged_first

logger = logging.getLogger(__name__)

UPSTREAM_TIMEOUT = "UPSTREAM_TIMEOUT"
UPSTREAM_UNAVAILABLE = "UPSTREAM_UNAVAILABLE"
OVERPASS_TOTAL_TIMEOUT_SECONDS = 8.0
OVERPASS_HEDGE_DELAY_SECONDS = 0.25
POI_TOTAL_TIMEOUT_SECONDS = 8.0
POI_PROVIDER_HEDGE_SECONDS = 0.25


class POIProviderError(RuntimeError):
    """Expected failure at a POI provider boundary."""

    def __init__(self, message: str, *, code: str = UPSTREAM_UNAVAILABLE) -> None:
        super().__init__(message)
        self.code = code


def _text_value(value: Any) -> str:
    """Return provider text only when the JSON value is actually textual."""
    return value.strip() if isinstance(value, str) else ""


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
    error_code: str = ""
    searched_terms: tuple[str, ...] = ()


class POIProvider(Protocol):
    name: str

    async def search(
        self,
        query: str,
        lat: float,
        lon: float,
        radius: int = 3000,
        limit: int = 10,
        timeout: float = 8.0,
    ) -> List[POIItem]: ...


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
        if not isinstance(data, dict):
            raise POIProviderError("AMap response is not an object")
        if data.get("status") != "1":
            raise POIProviderError(data.get("info") or "AMap POI search failed")
        items: List[POIItem] = []
        pois = data.get("pois")
        if not isinstance(pois, list):
            raise POIProviderError("AMap response has no POI list")
        for poi in pois:
            if not isinstance(poi, dict):
                continue
            try:
                loc_str = _text_value(poi.get("location"))
                plon, plat = 0.0, 0.0
                if "," in loc_str:
                    parts = loc_str.split(",")
                    plon, plat = float(parts[0]), float(parts[1])
                    plat, plon = gcj02_to_wgs84(plat, plon)
                items.append(POIItem(
                    name=_text_value(poi.get("name")),
                    address=_text_value(poi.get("address")),
                    type_name=_text_value(poi.get("type")).split(";")[0],
                    distance_m=float(poi.get("distance", 0)),
                    lat=plat, lon=plon,
                    tel=_text_value(poi.get("tel")),
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
        if not isinstance(data, dict):
            raise POIProviderError("Baidu response is not an object")
        if data.get("status") != 0:
            raise POIProviderError(data.get("message") or "Baidu POI search failed")
        items: List[POIItem] = []
        pois = data.get("results")
        if not isinstance(pois, list):
            raise POIProviderError("Baidu response has no POI list")
        for poi in pois:
            if not isinstance(poi, dict):
                continue
            try:
                loc = poi.get("location", {})
                detail = poi.get("detail_info", {})
                if not isinstance(loc, dict):
                    continue
                plat = float(loc.get("lat", 0))
                plon = float(loc.get("lng", 0))
                if plat or plon:
                    plat, plon = gcj02_to_wgs84(plat, plon)
                items.append(POIItem(
                    name=_text_value(poi.get("name")),
                    address=_text_value(poi.get("address")),
                    type_name=_text_value(detail.get("tag")) if isinstance(detail, dict) else "",
                    distance_m=float(detail.get("distance", 0)) if isinstance(detail, dict) else 0,
                    lat=plat,
                    lon=plon,
                    tel=_text_value(detail.get("phone")) if isinstance(detail, dict) else "",
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
        "https://overpass.private.coffee/api/interpreter",
        "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
        "https://overpass-api.de/api/interpreter",
    )

    # 地图召回词 → 完整 OSM tag filter。存在性查询不能伪装成 ``shop=yes``。
    _TAG_FILTERS: Dict[str, str] = {
        "商店": '["shop"]', "店铺": '["shop"]', "shop": '["shop"]',
        "shops": '["shop"]', "store": '["shop"]', "stores": '["shop"]',
        "餐厅": '["amenity"="restaurant"]', "餐饮": '["amenity"="restaurant"]',
        "火锅": (
            '["amenity"~"^(restaurant|fast_food)$"]'
            '["cuisine"~"^(hot_pot|hotpot)$"]'
        ),
        "烧烤": (
            '["amenity"~"^(restaurant|fast_food)$"]'
            '["cuisine"~"^(barbecue|grill)$"]'
        ),
        "日料": (
            '["amenity"="restaurant"]'
            '["cuisine"~"^(japanese|sushi)$"]'
        ),
        "素食餐厅": (
            '["amenity"~"^(restaurant|cafe|fast_food)$"]'
            '["diet:vegetarian"~"^(yes|only)$"]'
        ),
        "咖啡": '["amenity"="cafe"]', "咖啡厅": '["amenity"="cafe"]',
        "咖啡馆": '["amenity"="cafe"]', "cafe": '["amenity"="cafe"]',
        "甜品店": '["shop"~"^(confectionery|pastry)$"]',
        "冷饮": '["shop"~"^(ice_cream|confectionery)$"]',
        "冰淇淋": '["shop"="ice_cream"]',
        "沙拉": '["amenity"="restaurant"]["cuisine"~"salad"]',
        "甜品": '["shop"~"^(confectionery|pastry)$"]',
        "面包": '["shop"="bakery"]',
        "快餐": '["amenity"="fast_food"]',
        "超市": '["shop"="supermarket"]', "便利店": '["shop"="convenience"]',
        "购物中心": '["shop"="mall"]', "商场": '["shop"="mall"]',
        "书店": '["shop"="books"]',
        "药店": '["amenity"="pharmacy"]', "医院": '["amenity"="hospital"]',
        "银行": '["amenity"="bank"]', "ATM": '["amenity"="atm"]',
        "酒店": '["tourism"="hotel"]', "宾馆": '["tourism"="hotel"]',
        "景点": '["tourism"="attraction"]', "公园": '["leisure"="park"]',
        "博物馆": '["tourism"="museum"]', "美术馆": '["tourism"="gallery"]',
        "室内游乐场": '["leisure"="indoor_play"]',
        "酒吧": '["amenity"="bar"]', "夜店": '["amenity"="nightclub"]',
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
        request_timeout = max(0.01, min(float(timeout), OVERPASS_TOTAL_TIMEOUT_SECONDS))
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
                timeout=request_timeout,
            )
        else:
            async with httpx.AsyncClient(
                timeout=request_timeout,
                headers={"User-Agent": "N.E.K.O-LifeKit/0.3"},
            ) as client:
                data = await self._request_first_available(
                    client,
                    overpass_query,
                    timeout=request_timeout,
                )
        items: List[POIItem] = []
        for el in (data.get("elements") or []):
            if not isinstance(el, dict):
                continue
            try:
                tags = el.get("tags", {})
                if not isinstance(tags, dict):
                    continue
                name = _text_value(tags.get("name"))
                if not name:
                    continue
                raw_lat = el.get("lat")
                raw_lon = el.get("lon")
                if raw_lat is None or raw_lon is None:
                    center = el.get("center", {}) or {}
                    if not isinstance(center, dict):
                        continue
                    raw_lat = raw_lat if raw_lat is not None else center.get("lat")
                    raw_lon = raw_lon if raw_lon is not None else center.get("lon")
                # 合法坐标可能是 0.0（赤道/本初子午线），所以用显式 None 判定喵
                if raw_lat is None or raw_lon is None:
                    continue
                plat = float(raw_lat)
                plon = float(raw_lon)
                dist = haversine_km(lat, lon, plat, plon) * 1000
                addr_parts = [
                    _text_value(tags.get("addr:street")),
                    _text_value(tags.get("addr:housenumber")),
                ]
                items.append(POIItem(
                    name=name,
                    address=" ".join(p for p in addr_parts if p).strip(),
                    type_name=_text_value(
                        tags.get("cuisine", tags.get("shop", tags.get("amenity", "")))
                    ),
                    distance_m=dist,
                    lat=plat, lon=plon,
                    tel=_text_value(tags.get("phone")),
                ))
            except (ValueError, TypeError, KeyError):
                continue
        items.sort(key=lambda x: x.distance_m)
        return _deduplicate_items(items)[:limit]

    async def _request_first_available(
        self,
        client: httpx.AsyncClient,
        overpass_query: str,
        *,
        timeout: float,
    ) -> dict[str, Any]:
        errors: list[str] = []
        error_codes: list[str] = []

        async def request_endpoint(
            endpoint_index: int,
            endpoint: str,
        ) -> tuple[int, dict[str, Any] | None, str, str]:
            try:
                response = await client.post(endpoint, data={"data": overpass_query})
                response.raise_for_status()
                data = response.json()
                if not isinstance(data, dict):
                    raise ValueError("Overpass response is not an object")
                remark = data.get("remark")
                if remark:
                    code = (
                        UPSTREAM_TIMEOUT
                        if "timeout" in str(remark).casefold()
                        else UPSTREAM_UNAVAILABLE
                    )
                    raise POIProviderError("Overpass returned a runtime error", code=code)
                if not isinstance(data.get("elements"), list):
                    raise POIProviderError("Overpass response has no elements list")
                return endpoint_index, data, "", ""
            except (httpx.HTTPError, ValueError, POIProviderError) as exc:
                message = f"{endpoint}: {type(exc).__name__}: {exc}"
                error_code = (
                    UPSTREAM_TIMEOUT
                    if isinstance(exc, (httpx.TimeoutException, TimeoutError))
                    else getattr(exc, "code", UPSTREAM_UNAVAILABLE)
                )
                logger.debug(
                    "Overpass instance failed: endpoint_index=%s error_type=%s",
                    endpoint_index,
                    type(exc).__name__,
                )
                return endpoint_index, None, message, error_code

        attempts = tuple(
            lambda index=index, endpoint=endpoint: request_endpoint(index, endpoint)
            for index, endpoint in enumerate(self._endpoints)
        )
        outcome = await ordered_hedged_first(
            attempts,
            accept=lambda value: value[1] is not None,
            hedge_delay=OVERPASS_HEDGE_DELAY_SECONDS,
            total_timeout=timeout,
        )
        if outcome.winner is not None:
            return outcome.winner[1] or {"elements": []}
        for _, (_, _, error, error_code) in outcome.completed:
            errors.append(error)
            error_codes.append(error_code)
        for endpoint_index in outcome.timed_out_indices:
            endpoint = self._endpoints[endpoint_index]
            errors.append(f"{endpoint}: TimeoutError: total request budget exceeded")
            error_codes.append(UPSTREAM_TIMEOUT)
            logger.debug(
                "Overpass instance failed: endpoint_index=%s error_type=TimeoutError",
                endpoint_index,
            )

        code = (
            UPSTREAM_TIMEOUT
            if error_codes and all(value == UPSTREAM_TIMEOUT for value in error_codes)
            else UPSTREAM_UNAVAILABLE
        )
        logger.warning(
            "Overpass search failed: endpoint_count=%s error_code=%s",
            len(errors),
            code,
        )
        raise POIProviderError(
            "all Overpass instances failed: " + "; ".join(errors),
            code=code,
        )


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
        self._providers: list[POIProvider] = []
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
        async def search_provider(
            provider: POIProvider,
        ) -> tuple[POIProvider, list[POIItem] | None, str, str]:
            try:
                items = await provider.search(query, lat, lon, radius=radius, limit=limit)
                return provider, items, "", ""
            except (httpx.HTTPError, RuntimeError, ValueError, POIProviderError) as exc:
                message = f"{provider.name}: {type(exc).__name__}: {exc}"
                error_code = (
                    UPSTREAM_TIMEOUT
                    if isinstance(exc, (httpx.TimeoutException, TimeoutError))
                    else getattr(exc, "code", UPSTREAM_UNAVAILABLE)
                )
                logger.debug(
                    "POI provider failed: provider=%s error_type=%s",
                    provider.name,
                    type(exc).__name__,
                )
                return provider, None, message, error_code

        result = POIResult(query=query)
        errors: list[str] = []
        error_codes: list[str] = []
        successful_provider = ""
        outcome = await ordered_hedged_first(
            tuple(
                lambda provider=provider: search_provider(provider)
                for provider in self._providers
            ),
            accept=lambda value: bool(value[1]),
            hedge_delay=POI_PROVIDER_HEDGE_SECONDS,
            total_timeout=POI_TOTAL_TIMEOUT_SECONDS,
        )
        if outcome.winner is not None:
            provider, items, _, _ = outcome.winner
            result.items = items or []
            result.provider = provider.name
            return result
        for _, (provider, items, error, error_code) in outcome.completed:
            if items is not None:
                successful_provider = successful_provider or provider.name
            else:
                errors.append(error)
                error_codes.append(error_code)
        for provider_index in outcome.timed_out_indices:
            errors.append(f"{self._providers[provider_index].name}: timeout")
            error_codes.append(UPSTREAM_TIMEOUT)

        if successful_provider:
            result.provider = successful_provider
        elif errors:
            result.error = "; ".join(errors)
            result.error_code = (
                UPSTREAM_TIMEOUT
                if all(value == UPSTREAM_TIMEOUT for value in error_codes)
                else UPSTREAM_UNAVAILABLE
            )
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
        timeout_seconds: float | None = None,
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
            async def perform_search() -> POIResult:
                return await self.search(
                    query,
                    lat,
                    lon,
                    radius=radius,
                    limit=min(limit, 8),
                )

            async def execute() -> POIResult:
                if semaphore is None:
                    return await perform_search()
                async with semaphore:
                    return await perform_search()

            try:
                if timeout_seconds is None:
                    return await execute()
                return await asyncio.wait_for(execute(), timeout=timeout_seconds)
            except TimeoutError:
                return POIResult(
                    query=query,
                    error="nearby discovery timed out",
                    error_code=UPSTREAM_TIMEOUT,
                    searched_terms=(query,),
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
        errors = list(dict.fromkeys(
            term_result.error
            for term_result in term_results
            if term_result.error
        ))
        if not result.items and all(term_result.error for term_result in term_results):
            result.error = "; ".join(errors)
            result.error_code = (
                UPSTREAM_TIMEOUT
                if all(
                    term_result.error_code == UPSTREAM_TIMEOUT
                    for term_result in term_results
                )
                else UPSTREAM_UNAVAILABLE
            )
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
