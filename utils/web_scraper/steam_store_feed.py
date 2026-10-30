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

Reads the public, login-free featured-categories endpoint, localized by the
user's language so game names and prices match what the user sees on Steam.
"""

from __future__ import annotations

from itertools import zip_longest
from typing import Any

from utils.external_http_client import get_external_http_client
from utils.language_utils import get_global_language_full

from ._shared import get_random_user_agent, logger


_FEATURED_CATEGORIES_URL = "https://store.steampowered.com/api/featuredcategories"
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
_STEAM_TYPE_GAME = 0
_CATEGORY_LABELS = {"top_seller": "Top seller", "new_release": "New release"}


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


async def fetch_steam_featured(limit: int = 10) -> dict[str, Any]:
    """Fetch Steam store top sellers and new releases."""
    store_language, country = steam_store_locale_for_language(get_global_language_full())
    try:
        response = await get_external_http_client().get(
            _FEATURED_CATEGORIES_URL,
            params={"l": store_language, "cc": country},
            headers={"User-Agent": get_random_user_agent()},
            timeout=10.0,
        )
        response.raise_for_status()
        games = normalize_steam_featured(response.json(), limit=limit)
    except Exception as error:
        logger.warning(f"Steam 商店热门获取失败: l={store_language} cc={country} error={error}")
        return {"success": False, "source": "steam", "games": [], "error": f"Steam: {error}"}
    if not games:
        return {"success": False, "source": "steam", "games": [], "error": "Steam returned no games"}
    return {"success": True, "source": "steam", "games": games}
