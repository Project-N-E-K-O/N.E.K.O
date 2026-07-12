"""Small client helpers for the data-layer /api/identity seam.

The data layer owns raw player identity facts. The plugin only forwards the
manual identity request and exposes a small metadata summary to Hosted UI.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

from .text_safety import sanitize_display_name


IdentityFetcher = Callable[[str, float], dict[str, Any] | None]


def build_identity_url(base_url: str, *, name: str | None = None, clear: bool = False) -> str:
    root = base_url.rstrip("/")
    if clear or not (name or "").strip():
        return f"{root}/api/identity?clear=1"
    query = urllib.parse.urlencode({"name": str(name).strip()})
    return f"{root}/api/identity?{query}"


def fetch_identity(url: str, timeout: float) -> dict[str, Any] | None:
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        return data if isinstance(data, dict) else None
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        return None


def set_identity(
    base_url: str,
    timeout: float,
    *,
    name: str | None = None,
    clear: bool = False,
    fetcher: IdentityFetcher = fetch_identity,
) -> dict[str, Any]:
    url = build_identity_url(base_url, name=name, clear=clear)
    data = fetcher(url, timeout)
    if not isinstance(data, dict):
        return {"ok": False, "error": "identity request failed"}
    result = dict(data)
    result.setdefault("ok", True)
    return result


def identity_summary_from_combat(combat: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(combat, dict):
        combat = {}
    self_info = combat.get("self") if isinstance(combat.get("self"), dict) else None
    active_players = combat.get("active_players") if isinstance(combat.get("active_players"), list) else []
    return {
        "player_name": combat.get("player_name"),
        "self": _identity_self_summary(self_info),
        "requested": combat.get("requested"),
        "active_players_count": len(active_players),
        "active_players": _active_player_summaries(active_players),
    }


def _identity_self_summary(self_info: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(self_info, dict):
        return None
    return {
        "name": self_info.get("name"),
        "source": self_info.get("source"),
        "confidence": self_info.get("confidence"),
    }


def _active_player_summaries(active_players: list[Any], *, limit: int = 12) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in active_players[:limit]:
        if not isinstance(item, dict):
            continue
        safe = sanitize_display_name(item.get("name"), fallback="player")
        summary: dict[str, Any] = {
            "display_name": safe.text,
            "selectable": safe.level == "safe",
        }
        if safe.level == "safe":
            summary["name"] = safe.text
        result.append(summary)
    return result
