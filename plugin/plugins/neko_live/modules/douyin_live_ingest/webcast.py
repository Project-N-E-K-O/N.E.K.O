"""Douyin webcast page fetch and safe metadata extraction."""

from __future__ import annotations

import html
import json
import math
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, unquote

from ...core.contracts import LiveRoomStatus
from .public_projection import safe_public_bool, safe_public_text, safe_room_ref, safe_webcast_room_id


_RENDER_DATA_RE = re.compile(
    r"<script[^>]+id=[\"']RENDER_DATA[\"'][^>]*>(?P<body>.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)
_ROOM_ID_KEYS = ("room_id", "roomId", "webcast_room_id", "webcastRoomId")
_USER_UNIQUE_ID_KEYS = ("user_unique_id", "userUniqueId", "webcast_user_id", "webcastUserId")
_DEFAULT_FETCH_TIMEOUT_SECONDS = 8.0
_MAX_FETCH_TIMEOUT_SECONDS = 15.0
_MAX_PAGE_BYTES = 4 * 1024 * 1024
_ESCAPED_ROOM_RE = re.compile(
    r'\\?"room\\?":\{\\?"id_str\\?":\\?"(?P<room_id>\d+)\\?",'
    r'\s*\\?"status\\?":(?P<status>\d+),'
    r'\s*\\?"status_str\\?":\\?"(?P<status_str>\d+)\\?",'
    r'\s*\\?"title\\?":\\?"(?P<title>(?:\\\\.|[^\\"])*)\\?"'
)
_ESCAPED_WEB_RID_RE = re.compile(r'\\?"web_rid\\?":\\?"(?P<web_rid>[A-Za-z0-9_-]{2,128})\\?"')
_ESCAPED_USER_UNIQUE_ID_RE = re.compile(r'\\?"user_unique_id\\?":\\?"(?P<user_unique_id>\d+)\\?"')
_ESCAPED_ANCHOR_RE = re.compile(
    r'\\?"(?:anchor|owner)\\?":\{.{0,4000}?\\?"nickname\\?":\\?"(?P<nickname>(?:\\\\.|[^\\"])*)\\?"',
    re.DOTALL,
)


@dataclass(frozen=True, slots=True)
class DouyinWebcastInfo:
    ok: bool
    room_ref: str = ""
    webcast_room_id: str = ""
    user_unique_id: str = ""
    title: str = ""
    anchor_name: str = ""
    live_status: str = "unknown"
    message: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "room_ref", safe_room_ref(self.room_ref))
        object.__setattr__(self, "webcast_room_id", safe_webcast_room_id(self.webcast_room_id))
        object.__setattr__(self, "user_unique_id", safe_webcast_room_id(self.user_unique_id))
        object.__setattr__(self, "ok", safe_public_bool(self.ok) and bool(self.webcast_room_id))
        object.__setattr__(self, "title", safe_public_text(self.title, limit=120))
        object.__setattr__(self, "anchor_name", safe_public_text(self.anchor_name, limit=80))
        object.__setattr__(self, "live_status", _normalize_live_status(self.live_status))
        object.__setattr__(self, "message", safe_public_text(self.message, limit=160))

    def to_live_room_status(self) -> LiveRoomStatus:
        room_id = int(self.webcast_room_id) if self.webcast_room_id else 0
        return LiveRoomStatus(
            room_id=room_id,
            ok=self.ok,
            title=self.title,
            anchor_name=self.anchor_name,
            live_status=self.live_status,
            message=self.message,
        )

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "room_ref": self.room_ref,
            "webcast_room_id": self.webcast_room_id,
            "user_unique_id": self.user_unique_id,
            "title": self.title,
            "anchor_name": self.anchor_name,
            "live_status": self.live_status,
            "message": self.message,
        }


def room_page_url(room_ref: Any) -> str:
    if isinstance(room_ref, bool):
        token = ""
    elif isinstance(room_ref, int):
        token = str(room_ref) if room_ref > 0 else ""
    elif isinstance(room_ref, str):
        token = room_ref.strip()
    else:
        token = ""
    token = quote(token, safe="")
    return f"https://live.douyin.com/{token}"


def fetch_webcast_info(room_ref: Any, *, cookie: str = "", timeout: float = _DEFAULT_FETCH_TIMEOUT_SECONDS) -> DouyinWebcastInfo:
    request = urllib.request.Request(room_page_url(room_ref), headers=_headers(cookie))
    try:
        with urllib.request.urlopen(request, timeout=_safe_timeout(timeout)) as response:
            raw_body = response.read(_MAX_PAGE_BYTES + 1)
            if len(raw_body) > _MAX_PAGE_BYTES:
                return DouyinWebcastInfo(
                    ok=False,
                    room_ref=safe_room_ref(room_ref),
                    message="douyin room page exceeds size limit",
                )
            body = raw_body.decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return DouyinWebcastInfo(
            ok=False,
            room_ref=safe_room_ref(room_ref),
            live_status="unknown",
            message=_http_error_message(exc),
        )
    except (urllib.error.URLError, TimeoutError):
        return DouyinWebcastInfo(
            ok=False,
            room_ref=safe_room_ref(room_ref),
            live_status="unknown",
            message="douyin room page fetch failed",
        )
    return parse_webcast_info(body, room_ref=room_ref)


def parse_webcast_info(page_html: Any, *, room_ref: str = "") -> DouyinWebcastInfo:
    public_room_ref = safe_room_ref(room_ref)
    for data in _iter_json_payloads(page_html):
        candidate = _best_room_candidate(data, room_ref=public_room_ref)
        if candidate:
            return _info_from_candidate(candidate, room_ref=public_room_ref)
    escaped_info = _info_from_escaped_room_store(page_html, room_ref=public_room_ref)
    if escaped_info is not None:
        return escaped_info
    return DouyinWebcastInfo(
        ok=False,
        room_ref=public_room_ref,
        message="douyin room metadata not found",
    )


def _headers(cookie: str) -> dict[str, str]:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://live.douyin.com/",
    }
    cleaned = _safe_cookie_header(cookie)
    if cleaned:
        headers["Cookie"] = cleaned
    return headers


def _safe_cookie_header(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if "\r" in text or "\n" in text:
        return ""
    return text


def _safe_timeout(value: Any) -> float:
    if isinstance(value, bool):
        return _DEFAULT_FETCH_TIMEOUT_SECONDS
    if isinstance(value, (int, float)):
        seconds = float(value)
    elif isinstance(value, str):
        try:
            seconds = float(value.strip())
        except ValueError:
            return _DEFAULT_FETCH_TIMEOUT_SECONDS
    else:
        return _DEFAULT_FETCH_TIMEOUT_SECONDS
    if not math.isfinite(seconds) or seconds <= 0:
        return _DEFAULT_FETCH_TIMEOUT_SECONDS
    return min(seconds, _MAX_FETCH_TIMEOUT_SECONDS)


def _http_error_message(exc: urllib.error.HTTPError) -> str:
    code = getattr(exc, "code", 0)
    if isinstance(code, int) and 100 <= code <= 599:
        return f"douyin room page fetch failed: HTTP {code}"
    return "douyin room page fetch failed"


def _iter_json_payloads(page_html: Any):
    if not isinstance(page_html, str):
        return
    text = page_html.strip()
    if not text:
        return
    direct = _loads_json(text)
    if direct is not None:
        yield direct
    for match in _RENDER_DATA_RE.finditer(text):
        body = html.unescape(match.group("body").strip())
        for candidate in (body, unquote(body)):
            data = _loads_json(candidate)
            if data is not None:
                yield data


def _loads_json(text: str) -> Any | None:
    try:
        return json.loads(text)
    except Exception:
        return None


def _best_room_candidate(data: Any, *, room_ref: str = "") -> dict[str, Any] | None:
    best: tuple[int, dict[str, Any]] | None = None
    for item in _walk_dicts(data):
        candidate_refs = _candidate_room_refs(item)
        if room_ref and candidate_refs and room_ref not in candidate_refs:
            continue
        score = _candidate_score(item)
        if score <= 0:
            continue
        if best is None or score > best[0]:
            best = (score, item)
    return best[1] if best else None


def _candidate_room_refs(item: dict[str, Any]) -> set[str]:
    keys = ("web_rid", "webRid", "room_ref", "roomRef", "room_slug", "roomSlug")
    return {value for key in keys if (value := _first_text(item, (key,)))}


def _walk_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def _candidate_score(item: dict[str, Any]) -> int:
    if not _first_room_id(item):
        return 0
    score = 0
    score += 4
    if _first_text(item, ("title", "room_title", "roomTitle")):
        score += 2
    if _first_text(item, ("status", "live_status", "liveStatus")):
        score += 1
    owner = item.get("owner") or item.get("anchor") or item.get("user")
    if isinstance(owner, dict):
        score += 1
    return score


def _info_from_candidate(candidate: dict[str, Any], *, room_ref: str) -> DouyinWebcastInfo:
    owner = candidate.get("owner") or candidate.get("anchor") or candidate.get("user")
    owner_data = owner if isinstance(owner, dict) else {}
    webcast_room_id = _first_room_id(candidate)
    user_unique_id = _first_text(candidate, _USER_UNIQUE_ID_KEYS)
    title = _first_text(candidate, ("title", "room_title", "roomTitle"))
    anchor_name = _first_text(owner_data, ("nickname", "name", "user_name", "userName"))
    status = _normalize_live_status(_first_text(candidate, ("status", "live_status", "liveStatus")))
    message = "douyin room metadata found" if webcast_room_id else "douyin room metadata missing room id"
    return DouyinWebcastInfo(
        ok=bool(webcast_room_id),
        room_ref=safe_room_ref(room_ref),
        webcast_room_id=webcast_room_id,
        user_unique_id=user_unique_id,
        title=title,
        anchor_name=anchor_name,
        live_status=status,
        message=message,
    )


def _info_from_escaped_room_store(page_html: Any, *, room_ref: str) -> DouyinWebcastInfo | None:
    if not isinstance(page_html, str):
        return None
    room_match = _ESCAPED_ROOM_RE.search(page_html)
    if room_match is None:
        return None
    web_rid_match = _ESCAPED_WEB_RID_RE.search(page_html)
    web_rid = web_rid_match.group("web_rid") if web_rid_match else ""
    if room_ref and web_rid and web_rid != room_ref:
        return None
    anchor_match = _ESCAPED_ANCHOR_RE.search(page_html)
    user_match = _ESCAPED_USER_UNIQUE_ID_RE.search(page_html)
    anchor_name = _decode_escaped_json_string(anchor_match.group("nickname")) if anchor_match else ""
    title = _decode_escaped_json_string(room_match.group("title"))
    return DouyinWebcastInfo(
        ok=True,
        room_ref=safe_room_ref(room_ref or web_rid),
        webcast_room_id=room_match.group("room_id"),
        user_unique_id=user_match.group("user_unique_id") if user_match else "",
        title=title,
        anchor_name=anchor_name,
        live_status=_normalize_live_status(room_match.group("status_str") or room_match.group("status")),
        message="douyin room metadata found",
    )


def _decode_escaped_json_string(value: Any) -> str:
    if not isinstance(value, str) or not value:
        return ""
    try:
        decoded = json.loads(f'"{value}"')
    except Exception:
        decoded = value
    return decoded if isinstance(decoded, str) else ""


def _first_text(item: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = item.get(key)
        if value is None:
            continue
        if isinstance(value, bool) or isinstance(value, (dict, list, tuple, set, bytes, bytearray)):
            continue
        if isinstance(value, str):
            text = value.strip()
        elif isinstance(value, int):
            text = str(value)
        else:
            continue
        if text:
            return text
    return ""


def _first_room_id(item: dict[str, Any]) -> str:
    text = _first_text(item, _ROOM_ID_KEYS)
    return text if text.isdigit() else ""


def _normalize_live_status(value: Any) -> str:
    if isinstance(value, bool):
        return "unknown"
    if isinstance(value, int):
        text = str(value)
    elif isinstance(value, str):
        text = value.strip().lower()
    else:
        return "unknown"
    if text in {"1", "2", "live", "living", "online"}:
        return "live"
    if text in {"0", "4", "offline", "ended", "finish", "finished"}:
        return "offline"
    return "unknown"
