"""Anonymous, narrowly scoped NetEase song search and media resolution."""

from __future__ import annotations

import asyncio
import ipaddress
import re
import unicodedata
from collections.abc import Iterable, Mapping
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
from pyncm_async import Session as NeteaseSession
from pyncm_async.apis.track import GetTrackAudio

from .credentials import normalize_netease_cookies
from .models import PlayRequest, ResolvedMedia, SongCandidate

SEARCH_URL = "https://music.163.com/api/search/get/web"
OUTER_MEDIA_URL = "https://music.163.com/song/media/outer/url?id={song_id}.mp3"
MAX_CANDIDATES = 5
MAX_REDIRECTS = 5

_DISPLAY_LIMIT = 200
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_HOST_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_SURROUNDING_QUOTES = (
    " \t\r\n'\"\u2018\u2019\u201c\u201d\u300c\u300d\u300e\u300f\u300a\u300b"
)
_USER_AGENT = "N.E.K.O NetEase Music Plugin/0.1"
_TIMEOUT = httpx.Timeout(connect=3.0, read=5.0, write=5.0, pool=3.0)
_AUTH_COOKIE_NAMES = frozenset({"MUSIC_U", "MUSIC_A", "__csrf"})


class ProviderError(RuntimeError):
    """The provider could not safely complete an upstream operation."""


class ProviderSecurityError(ProviderError):
    """An upstream URL or redirect violated the fixed NetEase trust boundary."""


class MediaUnavailableError(ProviderError):
    """The selected song has no anonymously playable media response."""


def _sanitize_display(value: object, *, limit: int = _DISPLAY_LIMIT) -> str:
    if not isinstance(value, str):
        return ""

    normalized = unicodedata.normalize("NFKC", value)
    kept: list[str] = []
    for char in normalized:
        if char.isspace():
            kept.append(" ")
            continue
        if char == "\ufffd" or unicodedata.category(char) in {"Cc", "Cf", "Co", "Cs"}:
            continue
        kept.append(char)

    cleaned = " ".join("".join(kept).split())
    return cleaned[:limit].rstrip()


def _normalize_match(value: str) -> str:
    return _sanitize_display(value).casefold().strip(_SURROUNDING_QUOTES)


def _artist_names(candidate: SongCandidate) -> tuple[str, ...]:
    return candidate.artist_names or ((candidate.artist,) if candidate.artist else ())


def select_first_exact_match(
    query: str,
    candidates: Iterable[SongCandidate],
) -> SongCandidate | None:
    """Return the first exact title/title+artist match; never rank fuzzy results."""

    normalized_query = _normalize_match(query)
    if not normalized_query:
        return None

    for candidate in candidates:
        title = _normalize_match(candidate.name)
        if not title:
            continue
        signatures = {title}
        for raw_artist in _artist_names(candidate):
            artist = _normalize_match(raw_artist)
            if artist:
                signatures.add(f"{title} {artist}")
                signatures.add(f"{artist} {title}")
        if normalized_query in signatures:
            return candidate

    return None


def _is_valid_domain(hostname: str) -> bool:
    if not hostname or len(hostname) > 253 or hostname.endswith("."):
        return False
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        return all(_HOST_LABEL_RE.fullmatch(label) for label in hostname.split("."))
    return False


def _is_media_hostname(hostname: str) -> bool:
    return (
        hostname == "music.163.com"
        or hostname == "music.126.net"
        or hostname.endswith(".music.126.net")
    )


def _is_cdn_hostname(hostname: str) -> bool:
    return hostname == "music.126.net" or hostname.endswith(".music.126.net")


def _validated_media_url(url: str, *, allow_http_cdn_upgrade: bool) -> tuple[str, str]:
    try:
        parsed = urlsplit(url)
        hostname = (parsed.hostname or "").lower()
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise ProviderSecurityError("NetEase returned an invalid media URL") from exc

    if parsed.username is not None or parsed.password is not None:
        raise ProviderSecurityError("NetEase media URL contains user information")
    if not _is_valid_domain(hostname) or not _is_media_hostname(hostname):
        raise ProviderSecurityError("NetEase media redirect left the trusted domains")

    if parsed.scheme == "https":
        if port not in (None, 443):
            raise ProviderSecurityError("NetEase HTTPS media URL uses a forbidden port")
        return urlunsplit(
            ("https", hostname, parsed.path or "/", parsed.query, "")
        ), hostname

    if (
        parsed.scheme == "http"
        and allow_http_cdn_upgrade
        and _is_cdn_hostname(hostname)
        and port in (None, 80)
    ):
        # NetEase currently emits HTTP CDN redirects.  Upgrade locally before
        # the next request; no clear-text request is ever sent.
        return urlunsplit(
            ("https", hostname, parsed.path or "/", parsed.query, "")
        ), hostname

    raise ProviderSecurityError("NetEase media URL is not HTTPS")


def _positive_song_id(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and value.isascii() and value.isdecimal():
        parsed = int(value)
        return parsed if parsed > 0 else None
    return None


def _optional_fee(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _parse_candidate(raw: object) -> SongCandidate | None:
    if not isinstance(raw, Mapping):
        return None

    song_id = _positive_song_id(raw.get("id"))
    name = _sanitize_display(raw.get("name"))
    if song_id is None or not name:
        return None

    raw_artists = raw.get("artists", raw.get("ar", []))
    artist_names: list[str] = []
    if isinstance(raw_artists, list):
        for item in raw_artists:
            if not isinstance(item, Mapping):
                continue
            artist_name = _sanitize_display(item.get("name"))
            if artist_name:
                artist_names.append(artist_name)
    if not artist_names:
        return None

    raw_album = raw.get("album", raw.get("al", {}))
    album = (
        _sanitize_display(raw_album.get("name"))
        if isinstance(raw_album, Mapping)
        else ""
    )
    return SongCandidate(
        song_id=song_id,
        name=name,
        artist=" / ".join(artist_names)[:_DISPLAY_LIMIT].rstrip(),
        album=album,
        fee=_optional_fee(raw.get("fee")),
        artist_names=tuple(artist_names),
    )


class NeteaseMusicProvider:
    """Fixed-endpoint anonymous NetEase provider with manual redirect checks."""

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        music_u: str = "",
        cookies: Mapping[str, str] | None = None,
    ) -> None:
        self._cookies = normalize_netease_cookies(
            cookies if cookies is not None else music_u
        )
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=_TIMEOUT,
            follow_redirects=False,
            proxy=None,
            trust_env=False,
            headers={"User-Agent": _USER_AGENT},
        )

    async def aclose(self) -> None:
        if self._owns_client and not self._client.is_closed:
            await self._client.aclose()

    async def __aenter__(self) -> NeteaseMusicProvider:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    async def search(self, query: str) -> list[SongCandidate]:
        normalized_query = PlayRequest(query=query).query
        try:
            response = await self._client.post(
                SEARCH_URL,
                data={
                    "s": normalized_query,
                    "type": "1",
                    "offset": "0",
                    "limit": str(MAX_CANDIDATES),
                },
                headers={"Cookie": ""},
                follow_redirects=False,
            )
        except httpx.HTTPError as exc:
            raise ProviderError("NetEase search request failed") from exc

        if 300 <= response.status_code < 400:
            raise ProviderError("NetEase search unexpectedly redirected")
        if response.status_code != 200:
            raise ProviderError(f"NetEase search returned HTTP {response.status_code}")
        try:
            payload: Any = response.json()
        except ValueError as exc:
            raise ProviderError("NetEase search returned invalid JSON") from exc
        if not isinstance(payload, Mapping) or payload.get("code") != 200:
            raise ProviderError("NetEase search returned an invalid API response")

        result = payload.get("result")
        raw_songs = result.get("songs", []) if isinstance(result, Mapping) else []
        if not isinstance(raw_songs, list):
            raise ProviderError("NetEase search returned an invalid song list")

        candidates: list[SongCandidate] = []
        seen_ids: set[int] = set()
        for raw_song in raw_songs:
            candidate = _parse_candidate(raw_song)
            if candidate is None or candidate.song_id in seen_ids:
                continue
            candidates.append(candidate)
            seen_ids.add(candidate.song_id)
            if len(candidates) >= MAX_CANDIDATES:
                break
        return candidates

    async def resolve_media(self, song_id: int) -> ResolvedMedia:
        normalized_id = _positive_song_id(song_id)
        if normalized_id is None:
            raise ProviderSecurityError("Song ID must be a positive decimal integer")

        if self._cookies:
            try:
                authenticated_url = await self._resolve_authenticated_url(normalized_id)
                if authenticated_url:
                    return await self._probe_media_url(authenticated_url)
            except asyncio.CancelledError:
                raise
            except Exception:
                # Match the built-in player: an expired login or unavailable
                # authenticated URL must not prevent anonymous playback.
                pass

        return await self._probe_media_url(
            OUTER_MEDIA_URL.format(song_id=normalized_id),
            allow_http_cdn_upgrade=False,
        )

    async def _resolve_authenticated_url(self, song_id: int) -> str | None:
        session = NeteaseSession(
            timeout=_TIMEOUT,
            follow_redirects=False,
            proxy=None,
            trust_env=False,
        )
        self._sync_session_cookies(session, self._cookies)
        try:
            payload = await GetTrackAudio([song_id], session=session)
        except httpx.HTTPError as exc:
            raise ProviderError("NetEase authenticated media request failed") from exc
        finally:
            await session.aclose()

        if not isinstance(payload, Mapping):
            return None
        data = payload.get("data")
        if not isinstance(data, list) or not data or not isinstance(data[0], Mapping):
            return None
        url = data[0].get("url")
        return url if isinstance(url, str) and url else None

    @staticmethod
    def _sync_session_cookies(
        session: NeteaseSession,
        cookies: Mapping[str, str],
    ) -> None:
        """Apply plugin cookies to pyncm the same way as the built-in player."""

        jars = [getattr(session, "cookies", None)]
        client = getattr(session, "client", None)
        jars.append(getattr(client, "cookies", None))

        seen: set[int] = set()
        for jar in jars:
            if jar is None or id(jar) in seen:
                continue
            seen.add(id(jar))
            deleter = getattr(jar, "delete", None)
            for name in _AUTH_COOKIE_NAMES - set(cookies):
                if callable(deleter):
                    try:
                        deleter(name)
                    except KeyError:
                        pass
                else:
                    jar.set(name, "")
            for name, value in cookies.items():
                jar.set(name, value)
        session.csrf_token = cookies.get("__csrf", "")

    async def _probe_media_url(
        self,
        initial_url: str,
        *,
        allow_http_cdn_upgrade: bool = True,
    ) -> ResolvedMedia:
        current_url, current_hostname = _validated_media_url(
            initial_url,
            allow_http_cdn_upgrade=allow_http_cdn_upgrade,
        )
        visited: set[str] = set()
        redirect_count = 0

        while True:
            if current_url in visited:
                raise MediaUnavailableError("NetEase media redirect loop detected")
            visited.add(current_url)
            try:
                async with self._client.stream(
                    "GET",
                    current_url,
                    headers={
                        "Range": "bytes=0-0",
                        "Accept-Encoding": "identity",
                        "Cookie": "",
                    },
                    follow_redirects=False,
                ) as response:
                    if response.status_code in _REDIRECT_STATUSES:
                        if redirect_count >= MAX_REDIRECTS:
                            raise MediaUnavailableError(
                                "NetEase media redirected too many times"
                            )
                        location = response.headers.get("location")
                        if not location:
                            raise MediaUnavailableError(
                                "NetEase media redirect had no location"
                            )
                        next_url = urljoin(current_url, location)
                        current_url, current_hostname = _validated_media_url(
                            next_url,
                            allow_http_cdn_upgrade=True,
                        )
                        redirect_count += 1
                        continue

                    if response.status_code not in (200, 206):
                        raise MediaUnavailableError(
                            f"NetEase media returned HTTP {response.status_code}"
                        )
                    content_type = response.headers.get("content-type", "")
                    media_type = content_type.partition(";")[0].strip().lower()
                    if not (
                        media_type.startswith("audio/")
                        or media_type == "application/octet-stream"
                    ):
                        raise MediaUnavailableError(
                            "NetEase media response was not audio"
                        )
                    return ResolvedMedia(url=current_url, hostname=current_hostname)
            except ProviderError:
                raise
            except httpx.HTTPError as exc:
                raise ProviderError("NetEase media request failed") from exc


__all__ = [
    "MAX_CANDIDATES",
    "MAX_REDIRECTS",
    "MediaUnavailableError",
    "NeteaseMusicProvider",
    "ProviderError",
    "ProviderSecurityError",
    "select_first_exact_match",
]
