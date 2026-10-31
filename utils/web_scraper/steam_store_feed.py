# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Steam store top sellers and new releases for the non-China news source.

Two public, login-free requests:

1. ``featuredcategories`` lists top sellers and new releases, localized by the
   user's language. It carries no maturity data, and hardware such as VR
   headsets is listed with the same item type as games.
2. ``IStoreBrowseService/GetItems`` resolves those app ids in one batch to the
   real item type, content descriptors, and the store's short description.

Items are kept only when step 2 confirms a game without adult-only or frequent
sexual content, and each kept item carries the store's own description so the
model has facts to talk about. If step 2 fails, nothing is returned: unvetted
titles are worse than no Steam topic at all.
"""

from __future__ import annotations

import json
from itertools import zip_longest
from typing import Any

from utils.external_http_client import get_external_http_client
from utils.language_utils import get_global_language_full

from ._shared import get_random_user_agent, logger


_FEATURED_CATEGORIES_URL = "https://store.steampowered.com/api/featuredcategories"
_GET_ITEMS_URL = "https://api.steampowered.com/IStoreBrowseService/GetItems/v1/"
_DEFAULT_LANGUAGE = ("english", "us")
# UI language -> (Steam store language, store country code)
_LANGUAGE_TO_STORE = {
    "en": ("english", "us"),
    "ja": ("japanese", "jp"),
    "ko": ("koreana", "kr"),
    "ru": ("russian", "ru"),
    "es": ("spanish", "es"),
    "pt": ("brazilian", "br"),
    "zh-TW": ("tchinese", "tw"),
    "zh": ("schinese", "us"),
}
# Featured category key -> label stored on each item
_CATEGORIES = (("top_sellers", "top_seller"), ("new_releases", "new_release"))
_CATEGORY_LABELS = {"top_seller": "Top seller", "new_release": "New release"}
_STEAM_TYPE_GAME = 0
# Steam content descriptor ids: 3 = adult only sexual content,
# 4 = frequent nudity or sexual content.
_BLOCKED_CONTENT_DESCRIPTORS = frozenset({3, 4})
# Vet more candidates than requested so filtering still leaves enough games.
_CANDIDATE_MULTIPLIER = 3
_MAX_CANDIDATES = 30
_DESCRIPTION_MAX_CHARS = 300


def steam_store_locale_for_language(language: str | None) -> tuple[str, str]:
    """Map a UI language code to the Steam store (language, country) pair."""
    code = str(language or "").strip()
    if code in _LANGUAGE_TO_STORE:
        return _LANGUAGE_TO_STORE[code]
    return _LANGUAGE_TO_STORE.get(code.split("-", 1)[0].lower(), _DEFAULT_LANGUAGE)


def steam_game_details(game: dict[str, Any]) -> str:
    """Category and discount of a store item, e.g. "Top seller, 25% off"."""
    details: list[str] = []
    label = _CATEGORY_LABELS.get(str(game.get("category") or ""))
    if label:
        details.append(label)
    discount = game.get("discount_percent") or 0
    if discount:
        details.append(f"{discount}% off")
    return ", ".join(details)


def steam_game_summary(game: dict[str, Any]) -> str:
    """Details plus the store's own description, used as candidate evidence."""
    details = steam_game_details(game)
    description = str(game.get("description") or "")
    if details and description:
        return f"{details}. {description}"
    return details or description


def _store_item(value: Any, category: str) -> dict[str, Any] | None:
    if not isinstance(value, dict) or value.get("type") != _STEAM_TYPE_GAME:
        return None
    name = " ".join(str(value.get("name") or "").split())[:120]
    try:
        app_id = int(value.get("id"))
    except (TypeError, ValueError):
        return None
    if not name or app_id <= 0:
        return None
    try:
        discount = max(0, int(value.get("discount_percent") or 0))
    except (TypeError, ValueError):
        discount = 0
    return {
        "app_id": str(app_id),
        "title": name,
        "url": f"https://store.steampowered.com/app/{app_id}/",
        "category": category,
        "discount_percent": discount,
    }


def normalize_steam_featured(payload: Any, *, limit: int = 10) -> list[dict[str, Any]]:
    """Interleave top sellers and new releases, dropping duplicates and non-games."""
    if not isinstance(payload, dict):
        return []
    groups: list[list[dict[str, Any]]] = []
    for key, category in _CATEGORIES:
        section = payload.get(key)
        raw_items = section.get("items") if isinstance(section, dict) else None
        items = [
            item
            for item in (_store_item(raw, category) for raw in (raw_items or []))
            if item is not None
        ]
        groups.append(items)
    games: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in zip_longest(*groups):
        for item in row:
            if item is None or item["app_id"] in seen:
                continue
            seen.add(item["app_id"])
            games.append(item)
            if len(games) >= limit:
                return games
    return games


def parse_steam_item_details(payload: Any) -> dict[str, dict[str, Any]]:
    """Map app id -> {type, content_descriptors, description} from GetItems."""
    response = payload.get("response") if isinstance(payload, dict) else None
    store_items = response.get("store_items") if isinstance(response, dict) else None
    details: dict[str, dict[str, Any]] = {}
    if store_items and not any(
        isinstance(item, dict) and "content_descriptorids" in item for item in store_items
    ):
        # A batch of top sellers normally includes some mature titles. If none
        # carries the field, Steam may have renamed it and the adult filter
        # would silently pass everything.
        logger.warning("Steam GetItems 返回中没有任何 content_descriptorids 字段，成人内容过滤可能失效")
    for item in store_items or []:
        if not isinstance(item, dict) or item.get("success", 1) != 1:
            continue
        app_id = str(item.get("appid") or item.get("id") or "").strip()
        if not app_id:
            continue
        basic_info = item.get("basic_info") if isinstance(item.get("basic_info"), dict) else {}
        descriptors = item.get("content_descriptorids") or []
        details[app_id] = {
            "type": item.get("type"),
            "content_descriptors": {
                int(value) for value in descriptors if isinstance(value, int)
            },
            "description": " ".join(
                str(basic_info.get("short_description") or "").split()
            )[:_DESCRIPTION_MAX_CHARS],
        }
    return details


def vet_steam_games(
    games: list[dict[str, Any]],
    details: dict[str, dict[str, Any]],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    """Keep confirmed games without blocked content, attaching their description."""
    vetted: list[dict[str, Any]] = []
    for game in games:
        info = details.get(game["app_id"])
        if info is None or info["type"] != _STEAM_TYPE_GAME:
            continue
        if info["content_descriptors"] & _BLOCKED_CONTENT_DESCRIPTORS:
            continue
        vetted.append({**game, "description": info["description"]})
        if len(vetted) >= limit:
            break
    return vetted


def _failure(error: str) -> dict[str, Any]:
    return {"success": False, "source": "steam", "games": [], "error": error}


async def fetch_steam_featured(limit: int = 10) -> dict[str, Any]:
    """Fetch vetted Steam store top sellers and new releases."""
    store_language, country = steam_store_locale_for_language(get_global_language_full())
    client = get_external_http_client()
    headers = {"User-Agent": get_random_user_agent()}
    candidate_limit = min(_MAX_CANDIDATES, max(limit, 1) * _CANDIDATE_MULTIPLIER)
    try:
        response = await client.get(
            _FEATURED_CATEGORIES_URL,
            params={"l": store_language, "cc": country},
            headers=headers,
            timeout=10.0,
        )
        response.raise_for_status()
        candidates = normalize_steam_featured(response.json(), limit=candidate_limit)
        if not candidates:
            return _failure("Steam returned no games")
        details_request = {
            "ids": [{"appid": int(game["app_id"])} for game in candidates],
            "context": {
                "language": store_language,
                "country_code": country.upper(),
                "steam_realm": 1,
            },
            "data_request": {"include_basic_info": True},
        }
        details_response = await client.get(
            _GET_ITEMS_URL,
            params={"input_json": json.dumps(details_request)},
            headers=headers,
            timeout=10.0,
        )
        details_response.raise_for_status()
        games = vet_steam_games(
            candidates, parse_steam_item_details(details_response.json()), limit=limit
        )
    except Exception as error:
        message = f"{type(error).__name__}: {error}"
        logger.warning(f"Steam 商店热门获取失败: l={store_language} cc={country} error={message}")
        return _failure(f"Steam: {message}")
    if not games:
        return _failure("Steam returned no vetted games")
    return {"success": True, "source": "steam", "games": games}
