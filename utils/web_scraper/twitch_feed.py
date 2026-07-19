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

"""Public Twitch category discovery for the proactive video source.

The official Helix endpoint uses the encrypted Twitch Device Code credential
managed by the local media-credentials page. The shared external HTTP client
respects HTTP(S)_PROXY, ALL_PROXY, and NO_PROXY.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from utils.external_http_client import get_external_http_client
from utils.twitch_auth import TwitchAuthService


_TOP_GAMES_URL = "https://api.twitch.tv/helix/games/top"
_auth_service = TwitchAuthService()


def _category_item(value: Any) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    game_id = str(value.get("id") or "").strip()[:64]
    name = " ".join(str(value.get("name") or "").split())[:120]
    if not game_id or not name:
        return None
    return {
        "title": name,
        "author": "Twitch",
        "url": f"https://www.twitch.tv/directory/category/{quote(name, safe='')}",
        "source": "Twitch",
        "category_id": game_id,
    }


async def fetch_twitch_top_categories(limit: int = 10) -> dict[str, Any]:
    """Fetch public top Twitch categories without exposing credentials."""

    client_id, access_token = await _auth_service.access_token()
    if not client_id or not access_token:
        return {
            "success": False,
            "source": "twitch",
            "videos": [],
            "error": "Twitch source is not configured",
        }

    bounded_limit = max(1, min(int(limit) if isinstance(limit, int) else 10, 20))
    headers = {
        "Client-ID": client_id,
        "Authorization": f"Bearer {access_token}",
    }
    try:
        response = await get_external_http_client().get(
            _TOP_GAMES_URL,
            params={"first": bounded_limit},
            headers=headers,
            timeout=10.0,
        )
        if response.status_code == 401:
            client_id, access_token = await _auth_service.access_token(force_refresh=True)
            if not client_id or not access_token:
                raise RuntimeError("Twitch credential refresh failed")
            response = await get_external_http_client().get(
                _TOP_GAMES_URL,
                params={"first": bounded_limit},
                headers={"Client-ID": client_id, "Authorization": f"Bearer {access_token}"},
                timeout=10.0,
            )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        return {
            "success": False,
            "source": "twitch",
            "videos": [],
            "error": f"Twitch category fetch failed: {type(exc).__name__}",
        }

    raw_items = payload.get("data") if isinstance(payload, dict) else []
    categories = [item for item in (_category_item(raw) for raw in raw_items or []) if item]
    if not categories:
        return {
            "success": False,
            "source": "twitch",
            "videos": [],
            "error": "Twitch returned no usable categories",
        }
    return {"success": True, "source": "twitch", "videos": categories[:bounded_limit]}
