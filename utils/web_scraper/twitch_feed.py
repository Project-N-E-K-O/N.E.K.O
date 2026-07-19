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

"""Public Twitch live-stream discovery for the proactive video source.

The official Helix endpoint uses the encrypted Twitch Device Code credential
managed by the local media-credentials page. The shared external HTTP client
respects HTTP(S)_PROXY, ALL_PROXY, and NO_PROXY.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from utils.external_http_client import get_external_http_client
from utils.twitch_auth import TwitchAuthService


_FOLLOWED_STREAMS_URL = "https://api.twitch.tv/helix/streams/followed"
_auth_service = TwitchAuthService()


def _stream_item(value: Any) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    login = str(value.get("user_login") or "").strip().lower()[:25]
    title = " ".join(str(value.get("title") or "").split())[:180]
    author = " ".join(str(value.get("user_name") or "").split())[:80]
    game_name = " ".join(str(value.get("game_name") or "").split())[:120]
    if not login or not title or not author:
        return None
    try:
        viewers = max(0, int(value.get("viewer_count")))
    except (TypeError, ValueError):
        viewers = 0
    return {
        "stream_id": str(value.get("id") or "").strip()[:64],
        "title": title,
        "author": author,
        "url": f"https://www.twitch.tv/{quote(login, safe='')}",
        "source": "Twitch",
        "game_name": game_name,
        "viewer_count": str(viewers),
    }


async def fetch_twitch_live_streams(limit: int = 10) -> dict[str, Any]:
    """Fetch live streams from the authorized account's followed channels."""

    client_id, access_token, user_id = await _auth_service.followed_stream_access()
    if not client_id or not access_token or not user_id:
        return {
            "success": False,
            "source": "twitch",
            "videos": [],
            "error": "Twitch followed-stream access requires reauthorization",
        }

    bounded_limit = max(1, min(int(limit) if isinstance(limit, int) else 10, 20))
    headers = {
        "Client-ID": client_id,
        "Authorization": f"Bearer {access_token}",
    }
    try:
        response = await get_external_http_client().get(
            _FOLLOWED_STREAMS_URL,
            params={"user_id": user_id, "first": bounded_limit},
            headers=headers,
            timeout=10.0,
        )
        if response.status_code == 401:
            client_id, access_token, user_id = await _auth_service.followed_stream_access(force_refresh=True)
            if not client_id or not access_token or not user_id:
                raise RuntimeError("Twitch credential refresh failed")
            response = await get_external_http_client().get(
                _FOLLOWED_STREAMS_URL,
                params={"user_id": user_id, "first": bounded_limit},
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
            "error": f"Twitch live stream fetch failed: {type(exc).__name__}",
        }

    raw_items = payload.get("data") if isinstance(payload, dict) else []
    streams = [item for item in (_stream_item(raw) for raw in raw_items or []) if item]
    if not streams:
        return {
            "success": False,
            "source": "twitch",
            "videos": [],
            "error": "No followed Twitch live streams are currently online",
        }
    return {"success": True, "source": "twitch", "videos": streams[:bounded_limit]}
