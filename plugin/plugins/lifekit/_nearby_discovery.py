"""Natural-language nearby discovery across one or more search centers."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Sequence

from ._poi import POIResult, POIService


MAX_SEARCH_TERMS = 4
MAX_CENTER_TERM_SEARCHES = 8
MAX_CONCURRENT_SEARCHES = 4
DISCOVERY_TIMEOUT_SECONDS = 10.0


def normalize_search_terms(values: Sequence[object] | None) -> tuple[str, ...]:
    terms: list[str] = []
    seen: set[str] = set()
    for value in values or ():
        term = str(value).strip()
        key = term.casefold()
        if term and key not in seen:
            terms.append(term)
            seen.add(key)
    return tuple(terms[:MAX_SEARCH_TERMS])


@dataclass(frozen=True)
class DiscoveryRequest:
    search_terms: tuple[str, ...]
    radius: int


@dataclass(frozen=True)
class SearchCenter:
    latitude: float
    longitude: float


class NearbyDiscovery:
    """Execute a caller-provided semantic retrieval plan without reclassifying it."""

    def __init__(
        self,
        poi_service: POIService,
        *,
        timeout_seconds: float = DISCOVERY_TIMEOUT_SECONDS,
    ) -> None:
        self._poi_service = poi_service
        self._timeout_seconds = timeout_seconds

    async def discover(
        self,
        request: DiscoveryRequest,
        centers: tuple[SearchCenter, ...],
        *,
        limit_per_center: int = 10,
    ) -> tuple[POIResult, ...]:
        search_terms = normalize_search_terms(request.search_terms)
        if not centers:
            return ()

        searchable_center_count = min(len(centers), MAX_CENTER_TERM_SEARCHES)
        base_terms_per_center, extra_term_centers = divmod(
            MAX_CENTER_TERM_SEARCHES,
            searchable_center_count,
        )
        term_plans = tuple(
            search_terms[:
                min(
                    len(search_terms),
                    base_terms_per_center + (center_index < extra_term_centers),
                )
            ]
            if center_index < searchable_center_count
            else ()
            for center_index in range(len(centers))
        )
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_SEARCHES)

        async def search_center(
            center: SearchCenter,
            center_terms: tuple[str, ...],
        ) -> POIResult:
            if not center_terms:
                return POIResult(
                    query="",
                    error="nearby discovery search budget exhausted",
                    searched_terms=(),
                )
            try:
                return await asyncio.wait_for(
                    self._poi_service.search_many(
                        center_terms,
                        center.latitude,
                        center.longitude,
                        radius=request.radius,
                        limit=limit_per_center,
                        semaphore=semaphore,
                    ),
                    timeout=self._timeout_seconds,
                )
            except TimeoutError:
                return POIResult(
                    query=" / ".join(center_terms),
                    error="nearby discovery timed out",
                    searched_terms=center_terms,
                )

        results = await asyncio.gather(
            *(
                search_center(center, center_terms)
                for center, center_terms in zip(centers, term_plans)
            )
        )
        return tuple(results)
