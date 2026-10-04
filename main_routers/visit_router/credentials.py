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

"""N.E.K.O. Servers client for catgirl visits (design §5 PR-07, contract §4.7).

Five calls, all through ``get_external_http_client()`` with redirects off:

* :func:`fetch_visit_credentials` -- ``POST /api/visit/credentials`` (bearer):
  room binding / invite code / transport / vendor grant / identity ticket.
* :func:`cancel_visit_room` -- ``POST /api/visit/rooms/{visit_id}/cancel``
  (bearer): the host gives up before the peer ``hello`` verified.
* :func:`fetch_pubkeys` -- ``GET /api/visit/pubkeys`` (public): ticket keys
  plus the revocation list, cached ``VISIT_PUBKEYS_CACHE_S``.
* :func:`fetch_invite_preview` -- ``GET /api/visit/invites/{code}/preview``
  (bearer, read-only): data of the guest's confirmation dialog.
* :class:`VisitGrant` -- renews the 10 min vendor grant while keeping the
  identity ticket (``credentials{refresh:true}``, §3.2.1 / §4.3).

Errors are dispatched on BOTH the HTTP status and the response ``code``
(``CREDENTIALS_ERROR_CONTRACT`` / ``PREVIEW_ERROR_CONTRACT`` list every
variant of §4.7; anything else is ``servers_unreachable`` plus a diagnostic
log line). The bearer, the vendor grant, the identity ticket and invite codes
never reach a log line or ``repr``; the bearer never leaves the backend.
The region hint only reads ``ConfigManager._region_cache`` (after at most a
1.5 s wait for an in-flight probe); ``_check_non_mainland()`` is never called.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import math
import re
import time
from dataclasses import dataclass, field, replace
from typing import Any, Awaitable, Callable, Iterable, Mapping, Optional
from urllib.parse import urlsplit

import httpx

import config.visit_settings as visit_settings
from config.application import APP_VERSION
from config.visit_settings import (
    VISIT_BANNED_CACHE_S,
    VISIT_TICKET_AUD,
    VISIT_TICKET_ISS,
    VISIT_TICKET_VERSION,
    VISIT_INVITE_CODE_TTL_S,
    VISIT_PUBKEYS_CACHE_S,
    VISIT_TICKET_CLOCK_TOLERANCE_S,
    VISIT_TIERS,
    VISIT_VENDOR_GRANT_TTL_S,
    VISIT_VENDOR_REFRESH_MARGIN_S,
    VISIT_VIDEO_TIER_DEFAULT,
)
from main_logic.visit.identity import (
    FetchedPubkeys,
    MalformedTicket,
    PubkeySet,
    parse_pubkeys_response,
    peek_ticket_claims,
    ticket_ttl_s,
)
from main_logic.visit.sanitize import neutralize_display_name
from utils.config_manager.reserved_schema import is_valid_character_uid
from utils.http.external_client import get_external_http_client
from utils.logger_config import get_module_logger
from utils.social_base import social_base_url
from utils.visit_wire import require_visit_id

logger = get_module_logger(__name__, "Main")

_INVITE_PATH_MARK = "/api/visit/invites/"


class _InviteUrlLogFilter(logging.Filter):
    """Drop httpx request log lines whose URL carries an invite code.

    The preview path embeds the one-time code; httpx logs every request URL
    at INFO. Installed on the ``httpx`` logger itself, so it holds in every
    process layout (the main-server entry point's own httpx filter does not
    run in merged mode).
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            return _INVITE_PATH_MARK not in record.getMessage()
        except Exception:  # noqa: BLE001 - 格式化失败的记录不拦
            return True


def _install_httpx_invite_filter() -> None:
    httpx_logger = logging.getLogger("httpx")
    if not any(isinstance(f, _InviteUrlLogFilter) for f in httpx_logger.filters):
        httpx_logger.addFilter(_InviteUrlLogFilter())


_install_httpx_invite_filter()

ROLES = ("host", "guest")
TRANSPORTS = ("trtc", "livekit")

INVITE_CODE_RE = re.compile(r"^[A-Z2-7]{10}$")
"""Invite code format: 10 base32 characters (50 bit)."""

_VISIT_UID_RE = re.compile(r"^[0-9a-f]{24}$")
_VID_RE = re.compile(r"^[hg]_[0-9a-f]{24}$")
_TRTC_USER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
_SERVERS_CODE_RE = re.compile(r"^[a-z0-9_]{1,40}$")

_CREDENTIALS_TIMEOUT_S = 10.0
_PREVIEW_TIMEOUT_S = 8.0
_CANCEL_TIMEOUT_S = 8.0
_PUBKEYS_TIMEOUT_S = 5.0
# 非强制刷新失败后的最小重试间隔：拉不到时不要每次核验都打一遍 Servers。
_PUBKEYS_RETRY_MIN_S = 30.0
_CANCEL_BACKOFF_S = (1, 2, 4, 8)
_REGION_WAIT_S = 1.5
_DISPLAY_NAME_MAX_CHARS = 64
# vendor 凭证字段上限：留够实际长度（UserSig / privateMapKey 数百字节、LiveKit JWT 约 1 KB），
# 同时保证校验通过的凭证拼成下行 credentials 后一定不超过 8 KB
_TRTC_SIG_MAX_CHARS = 2048
_LIVEKIT_TOKEN_MAX_CHARS = 4096
_ROOM_ID_MAX_CHARS = 64
_ENTITLEMENT_MAX_KEYS = 16

# 测试注入点：退避等待。
_sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep


# ── 错误 ───────────────────────────────────────────────────────────────


class VisitServersError(Exception):
    """Base class of every Servers-call failure.

    ``code`` is the local error code surfaced to the frontend, ``http_status``
    the local HTTP status the routers answer with, ``reason`` the Servers
    ``code`` (or a local diagnostic token) that the toast copy is picked by.
    Messages never contain tokens, tickets or invite codes.
    """

    code = "servers_unreachable"
    http_status = 503

    def __init__(
        self,
        reason: str | None = None,
        *,
        retry_after_s: int | None = None,
        servers_status: int | None = None,
    ) -> None:
        self.reason = reason
        self.retry_after_s = retry_after_s
        self.servers_status = servers_status
        super().__init__(f"{self.code}: {reason}" if reason else self.code)

    def to_local_error(self) -> tuple[int, dict[str, Any]]:
        """Return ``(http_status, body)`` of the local endpoint answering with this error."""
        body: dict[str, Any] = {"code": self.code}
        if self.reason:
            body["details"] = {"reason": self.reason}
        if self.retry_after_s is not None:
            body["retry_after_s"] = self.retry_after_s
        return self.http_status, body


class VisitServersUnreachable(VisitServersError):
    """Network error, 5xx, an uncontracted reply or a malformed response."""


class VisitLivekitHostRejected(VisitServersUnreachable):
    """The LiveKit URL host is not in ``VISIT_LIVEKIT_HOSTS``."""


class VisitLoginRequired(VisitServersError):
    """No usable community OAuth session (or Servers answered 401)."""

    code = "VISIT_LOGIN_REQUIRED"
    http_status = 409


class VisitInviteInvalid(VisitServersError):
    """Invite cannot be redeemed; ``reason`` is the Servers code.

    Covers ``invite_invalid / invite_expired / room_full / self_invite``
    (403), ``role_taken`` (409) and ``invite_expiring`` (410).
    """

    code = "VISIT_INVITE_INVALID"
    http_status = 409


class VisitInviteFormat(VisitServersError):
    """The invite code does not match ``^[A-Z2-7]{10}$`` (rejected before any network)."""

    code = "invite_code_format"
    http_status = 400


class VisitRoomEnded(VisitServersError):
    """``410 room_ended``: Servers already force-ended the room (renewal / reconnect).

    Not an invite problem: the runtime finalizes with ``finalize_reason``,
    the same exit as a vendor ``room_disband``.
    """

    code = "room_ended"
    http_status = 410
    finalize_reason = "kicked"


class VisitBanned(VisitServersError):
    """``403 banned`` (cached for ``VISIT_BANNED_CACHE_S``)."""

    code = "VISIT_BANNED"
    http_status = 403


class VisitTierNotEntitled(VisitServersError):
    """``403 tier_not_entitled``."""

    code = "tier_not_entitled"
    http_status = 403


class VisitCrossRegionUnsupported(VisitServersError):
    """``403 cross_region_unsupported`` (fail closed, D.2)."""

    code = "cross_region_unsupported"
    http_status = 403


class VisitQuotaExceeded(VisitServersError):
    """``429 quota_exceeded`` with ``retry_after_s``."""

    code = "VISIT_QUOTA_EXCEEDED"
    http_status = 429


class VisitInviteNotFound(VisitServersError):
    """Preview ``404 invite_invalid``."""

    code = "invite_invalid"
    http_status = 404


class VisitInviteExpired(VisitServersError):
    """Preview ``410 invite_expired``."""

    code = "invite_expired"
    http_status = 410


class VisitRateLimited(VisitServersError):
    """Preview ``429 rate_limited`` (30 per minute per account) with ``retry_after_s``."""

    code = "rate_limited"
    http_status = 429


ErrorFactory = Callable[..., VisitServersError]

CREDENTIALS_ERROR_CONTRACT: Mapping[tuple[int, str], ErrorFactory] = {
    (401, "unauthenticated"): VisitLoginRequired,
    (403, "banned"): VisitBanned,
    (403, "tier_not_entitled"): VisitTierNotEntitled,
    (403, "cross_region_unsupported"): VisitCrossRegionUnsupported,
    (403, "invite_invalid"): VisitInviteInvalid,
    (403, "invite_expired"): VisitInviteInvalid,
    (403, "room_full"): VisitInviteInvalid,
    (403, "self_invite"): VisitInviteInvalid,
    (409, "role_taken"): VisitInviteInvalid,
    (410, "invite_expiring"): VisitInviteInvalid,
    (410, "room_ended"): VisitRoomEnded,
    (429, "quota_exceeded"): VisitQuotaExceeded,
}
"""Every non-2xx, non-5xx ``(status, code)`` of ``POST /api/visit/credentials`` (§4.7)."""

PREVIEW_ERROR_CONTRACT: Mapping[tuple[int, str], ErrorFactory] = {
    (401, "unauthenticated"): VisitLoginRequired,
    (404, "invite_invalid"): VisitInviteNotFound,
    (410, "invite_expired"): VisitInviteExpired,
    (403, "banned"): VisitBanned,
    (429, "rate_limited"): VisitRateLimited,
}
"""Every non-2xx, non-5xx ``(status, code)`` of ``GET /api/visit/invites/{code}/preview`` (§4.7)."""

CANCEL_DONE_REPLIES: frozenset[tuple[int, str]] = frozenset({
    (409, "guest_joined"),
    (403, "invite_invalid"),
})
"""Non-2xx cancel replies that still mean "nothing left to cancel"."""


# 被封缓存（§4.6 rooms / join：上次 Servers 403 banned 的 60 s 内本机直接 403）。
# 按社区账号（local_user_id）分开记：同一台机器 60 s 内换成未被封的账号不受牵连
_banned_until: dict[str, float] = {}


def _note_banned(account: str) -> None:
    now = time.monotonic()
    for key in [k for k, until in _banned_until.items() if until <= now]:
        del _banned_until[key]
    _banned_until[account] = now + VISIT_BANNED_CACHE_S


def banned_recently(account: str) -> bool:
    """True within ``VISIT_BANNED_CACHE_S`` of the last Servers ``403 banned`` for this account.

    ``account`` is the community ``local_user_id`` of the current session.
    """
    return time.monotonic() < _banned_until.get(account, 0.0)


def _reset_for_tests() -> None:
    """Clear module caches (unit tests only)."""
    global _banned_until, _pubkeys_fetched, _pubkeys_failed_at, _pubkeys_inflight
    _banned_until = {}
    _pubkeys_fetched = None
    _pubkeys_failed_at = None
    _pubkeys_inflight = None


def _body_json(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except (ValueError, TypeError):
        return None


def _body_code(body: Any) -> str | None:
    code = body.get("code") if isinstance(body, Mapping) else None
    return code if isinstance(code, str) else None


def _retry_after(body: Any, resp: httpx.Response) -> int | None:
    raw = body.get("retry_after_s") if isinstance(body, Mapping) else None
    if type(raw) is int and raw >= 0:
        return raw
    header = resp.headers.get("retry-after", "")
    return int(header) if header.isdigit() else None


def _diag_code(code: str | None) -> str:
    # 契约外的 code 只记形状安全的短串，防止把对端回显的任意文本写进日志
    if code is None:
        return "-"
    return code if _SERVERS_CODE_RE.fullmatch(code) else "<unprintable>"


def _map_error(
    resp: httpx.Response, table: Mapping[tuple[int, str], ErrorFactory], *, op: str, account: str,
) -> VisitServersError:
    status = resp.status_code
    if status >= 500:
        return VisitServersUnreachable("servers_5xx", servers_status=status)
    body = _body_json(resp)
    code = _body_code(body)
    factory = table.get((status, code)) if code is not None else None
    if factory is None and status == 401:
        factory = VisitLoginRequired
    if factory is None:
        logger.warning(
            "visit servers %s: uncontracted reply status=%s code=%s", op, status, _diag_code(code),
        )
        return VisitServersUnreachable("uncontracted_reply", servers_status=status)
    err = factory(code, retry_after_s=_retry_after(body, resp), servers_status=status)
    if isinstance(err, VisitBanned):
        _note_banned(account)
    return err


# ── 会话与出网 ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _ServersSession:
    base_url: str
    access_token: str = field(repr=False)
    client_id: str
    account: str
    """Community ``local_user_id`` of the session (keys the banned cache)."""

    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}", "X-Client-Id": self.client_id}


async def _servers_session() -> _ServersSession:
    """Resolve the community OAuth session (refreshing it when needed) for one Servers call.

    The bearer is the one in the snapshot the resolver just validated, never
    a re-read of the session file. Not logged in → :class:`VisitLoginRequired`; a saved session the cloud
    could not verify right now → :class:`VisitServersUnreachable`. A login
    that belongs to another community origin is never sent to the configured
    one (same posture as the card-drop routes).
    """
    from main_routers import card_drop_router as card_drop
    from main_routers import community_oauth

    status = await community_oauth.resolve_saved_oauth_status()
    if not status.get("logged_in"):
        if community_oauth.status_session_saved(status):
            raise VisitServersUnreachable("session_unverified")
        raise VisitLoginRequired()
    # 只用 resolver 刚校验过（必要时刷新过）的那份快照：再从磁盘重读可能读到并发换号后的另一个账号
    snapshot = status.get("snapshot")
    if not isinstance(snapshot, Mapping) or not snapshot or not snapshot.get("local_user_id") or not snapshot.get("access_token"):
        raise VisitLoginRequired()
    base = social_base_url().strip().rstrip("/")
    snapshot_base = str(snapshot.get("base_url") or "").strip().rstrip("/")
    if snapshot_base and not card_drop._same_originish(snapshot_base, base):
        raise VisitLoginRequired()
    client_id = await asyncio.to_thread(card_drop._get_client_id)
    if not client_id:
        raise VisitServersUnreachable("client_not_registered")
    return _ServersSession(
        base_url=base,
        access_token=str(snapshot["access_token"]),
        client_id=client_id,
        account=str(snapshot["local_user_id"]),
    )


async def _send(
    method: str,
    url: str,
    *,
    op: str,
    headers: Mapping[str, str] | None = None,
    json_body: Any = None,
    timeout: float,
) -> httpx.Response:
    client = get_external_http_client()
    try:
        return await client.request(
            method,
            url,
            headers=dict(headers or {}),
            json=json_body,
            timeout=timeout,
            # bearer 只发给 Servers 自己：任何跳转都不跟
            follow_redirects=False,
        )
    except (httpx.HTTPError, OSError) as exc:
        logger.warning("visit servers %s: request failed: %s", op, type(exc).__name__)
        raise VisitServersUnreachable("network") from None


async def _region_hint() -> str:
    """``'cn' | 'global' | 'unknown'`` from the cached GeoIP verdict only.

    Reads ``ConfigManager._region_cache``; when it is still None waits at
    most 1.5 s for an in-flight probe (``aensure_region_resolved``). Never
    calls ``_check_non_mainland()`` (that would start a blocking probe).
    """
    from utils.config_manager import ConfigManager, get_config_manager

    cached = ConfigManager._region_cache
    if cached is None:
        try:
            await get_config_manager().aensure_region_resolved(timeout=_REGION_WAIT_S)
        except Exception as exc:  # noqa: BLE001 - 区域只是提示，拿不到按 unknown
            logger.debug("visit servers: region wait failed: %s", type(exc).__name__)
        cached = ConfigManager._region_cache
    if cached is None:
        return "unknown"
    return "global" if cached else "cn"


def _app_version() -> str:
    """``major.minor`` of the running build (§4.7 ``app_version``)."""
    parts = str(APP_VERSION).split(".")
    return ".".join(parts[:2]) if len(parts) >= 2 else str(APP_VERSION)


def allowed_livekit_hosts() -> frozenset[str]:
    """Exact LiveKit host names accepted: ``VISIT_LIVEKIT_HOSTS`` plus the dev loopback host."""
    hosts = {h.lower() for h in visit_settings.VISIT_LIVEKIT_HOSTS}
    dev = _dev_livekit_host()
    if dev:
        hosts.add(dev)
    return frozenset(hosts)


def _dev_livekit_host() -> str | None:
    raw = visit_settings.NEKO_VISIT_DEV_LIVEKIT_URL
    if not raw:
        return None
    try:
        host = urlsplit(raw).hostname
    except ValueError:
        return None
    return host.lower() if host else None


def _require_invite_code(invite_code: Any) -> str:
    if not isinstance(invite_code, str) or INVITE_CODE_RE.fullmatch(invite_code) is None:
        raise VisitInviteFormat("invite_code_format")
    return invite_code


# ── 凭证 ───────────────────────────────────────────────────────────────


@dataclass(frozen=True, kw_only=True)
class VisitCredentials:
    """One successful ``POST /api/visit/credentials`` (validated).

    ``expires_at`` is the identity ticket expiry (guest ``iat+2400``, host
    ``iat+3000``); ``vendor_expires_at`` the vendor grant expiry
    (``iat+600``). ``vendor`` holds only the selected transport:
    ``{'trtc': {...}}`` or ``{'livekit': {...}}``. ``visit_uid`` / ``vid``
    are this side's own ids (``visit_uid`` derives ``pair_id`` and isolates
    memory). ``peer_vid`` exists for the guest only; ``invite_code`` /
    ``invite_expires_at`` (Servers time, forwarded as is in
    ``visit_state_change{invite_ready}``) for the host only -- a re-issue
    may omit them once the invite was redeemed, so a first host issue
    without them is the caller's error to raise. Secrets are kept out of
    ``repr``.
    """

    role: str
    visit_id: str
    char_tag: str
    transport: str
    expires_at: float
    vendor_expires_at: float
    vendor: Mapping[str, Mapping[str, Any]] = field(repr=False)
    identity_ticket: str = field(repr=False)
    visit_uid: str
    vid: str
    peer_vid: Optional[str] = None
    invite_code: Optional[str] = field(default=None, repr=False)
    invite_expires_at: Optional[float] = None
    cross_region: bool = False
    entitlement: Optional[Mapping[str, Any]] = None

    def vendor_remaining_s(self, now: float) -> float:
        """Seconds left on the vendor grant (negative once expired)."""
        return self.vendor_expires_at - now

    def ticket_expired(self, now: float) -> bool:
        """True once the identity ticket ``expires_at`` passed (no new first ``credentials``)."""
        return now > self.expires_at

    def with_renewed_vendor(self, renewed: "VisitCredentials") -> "VisitCredentials":
        """Take the vendor grant of ``renewed``; keep the identity ticket and invite fields.

        The ticket is replayed as is on reconnects (same ``jti``), so a
        renewal only swaps what the iframe needs to re-enter the room.
        """
        return replace(self, vendor=renewed.vendor, vendor_expires_at=renewed.vendor_expires_at)


class _BadResponse(ValueError):
    pass


def _need(ok: bool, what: str) -> None:
    if not ok:
        raise _BadResponse(what)


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _short_str(value: Any, limit: int) -> bool:
    return isinstance(value, str) and 0 < len(value) <= limit


def _grant_ttl_ok(value: Any) -> bool:
    return type(value) is int and 0 < value <= VISIT_VENDOR_GRANT_TTL_S


def _parse_trtc(raw: Any, *, vid: str, visit_id: str) -> dict[str, Any]:
    _need(isinstance(raw, Mapping), "vendor.trtc")
    sdk_app_id = raw.get("sdk_app_id")
    _need(type(sdk_app_id) is int and sdk_app_id > 0, "vendor.trtc.sdk_app_id")
    user_id = raw.get("user_id")
    _need(isinstance(user_id, str) and _TRTC_USER_ID_RE.fullmatch(user_id) is not None, "vendor.trtc.user_id")
    _need(user_id == vid, "vendor.trtc.user_id")
    _need(_short_str(raw.get("user_sig"), _TRTC_SIG_MAX_CHARS), "vendor.trtc.user_sig")
    _need(_short_str(raw.get("private_map_key"), _TRTC_SIG_MAX_CHARS), "vendor.trtc.private_map_key")
    room = raw.get("str_room_id")
    _need(_short_str(room, _ROOM_ID_MAX_CHARS) and room == visit_id, "vendor.trtc.str_room_id")
    _need(_grant_ttl_ok(raw.get("expire")), "vendor.trtc.expire")
    return {
        "sdk_app_id": sdk_app_id,
        "user_id": user_id,
        "user_sig": raw["user_sig"],
        "private_map_key": raw["private_map_key"],
        "str_room_id": room,
        "expire": raw["expire"],
    }


def _parse_livekit(raw: Any) -> dict[str, Any]:
    _need(isinstance(raw, Mapping), "vendor.livekit")
    url = raw.get("url")
    _need(_short_str(url, 512), "vendor.livekit.url")
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        _ = parsed.port  # 非法端口（wss://host:bad）在这里抛 ValueError
    except ValueError:
        raise _BadResponse("vendor.livekit.url") from None
    allowed = allowed_livekit_hosts()
    if not host or host not in allowed:
        raise VisitLivekitHostRejected("livekit_host_not_allowed")
    secure_ok = parsed.scheme == "wss" or (parsed.scheme == "ws" and host == _dev_livekit_host())
    if not secure_ok or parsed.username or parsed.password:
        raise VisitLivekitHostRejected("livekit_host_not_allowed")
    _need(_short_str(raw.get("token"), _LIVEKIT_TOKEN_MAX_CHARS), "vendor.livekit.token")
    _need(_grant_ttl_ok(raw.get("ttl_s")), "vendor.livekit.ttl_s")
    return {"url": url, "token": raw["token"], "ttl_s": raw["ttl_s"]}


def _parse_credentials(
    payload: Any, *, role: str, visit_id: str, char_tag: str, now: float,
) -> VisitCredentials:
    _need(isinstance(payload, Mapping), "body")
    transport = payload.get("transport")
    _need(transport in TRANSPORTS, "transport")
    visit_uid = payload.get("visit_uid")
    _need(isinstance(visit_uid, str) and _VISIT_UID_RE.fullmatch(visit_uid) is not None, "visit_uid")
    vid = payload.get("vid")
    _need(isinstance(vid, str) and _VID_RE.fullmatch(vid) is not None and vid[0] == role[0], "vid")

    expires_at = payload.get("expires_at")
    vendor_expires_at = payload.get("vendor_expires_at")
    _need(_finite(expires_at), "expires_at")
    _need(_finite(vendor_expires_at), "vendor_expires_at")
    # 已经过期（含时钟容差）的凭证没法入房：当坏响应处理，不当成功
    _need(expires_at + VISIT_TICKET_CLOCK_TOLERANCE_S > now, "expires_at")
    _need(vendor_expires_at + VISIT_TICKET_CLOCK_TOLERANCE_S > now, "vendor_expires_at")
    # vendor 凭证只有 10 min：比这更长的一律不收（短凭证续期与强制结束后的暴露上限靠它）
    _need(
        vendor_expires_at - now <= VISIT_VENDOR_GRANT_TTL_S + VISIT_TICKET_CLOCK_TOLERANCE_S,
        "vendor_expires_at",
    )

    vendor_raw = payload.get("vendor")
    _need(isinstance(vendor_raw, Mapping), "vendor")
    if transport == "trtc":
        vendor = {"trtc": _parse_trtc(vendor_raw.get("trtc"), vid=vid, visit_id=visit_id)}
    else:
        vendor = {"livekit": _parse_livekit(vendor_raw.get("livekit"))}
    # 相对时长与绝对到期要对得上：到期时刻不能比「现在 + 授权时长」更晚
    grant_ttl = vendor[transport]["expire" if transport == "trtc" else "ttl_s"]
    _need(vendor_expires_at - now <= grant_ttl + VISIT_TICKET_CLOCK_TOLERANCE_S, "vendor_expires_at")

    ticket = payload.get("identity_ticket")
    _need(isinstance(ticket, str), "identity_ticket")
    try:
        claims = peek_ticket_claims(ticket)
    except MalformedTicket:
        raise _BadResponse("identity_ticket") from None
    _need(claims.v == VISIT_TICKET_VERSION, "identity_ticket.v")
    _need(claims.iss == VISIT_TICKET_ISS, "identity_ticket.iss")
    _need(claims.aud == VISIT_TICKET_AUD, "identity_ticket.aud")
    # 签发时刻不能在未来（含时钟容差）；过期由下面的 expires_at 检查兜住
    _need(claims.iat - VISIT_TICKET_CLOCK_TOLERANCE_S <= now, "identity_ticket.iat")
    _need(claims.role == role, "identity_ticket.role")
    _need(claims.visit_id == visit_id, "identity_ticket.visit_id")
    _need(claims.vid == vid, "identity_ticket.vid")
    _need(claims.sub == visit_uid, "identity_ticket.sub")
    _need(claims.transport == transport, "identity_ticket.transport")
    _need(claims.char_tag == char_tag, "identity_ticket.char_tag")
    # 身份票按侧位：guest 40 min、host 50 min（等客 10 + 硬顶 30 + 余量 10）
    _need(claims.exp - claims.iat == ticket_ttl_s(role), "identity_ticket.lifetime")
    _need(abs(claims.exp - expires_at) <= 1, "expires_at")

    peer_vid = payload.get("peer_vid")
    invite_code = payload.get("invite_code")
    invite_expires_at = payload.get("invite_expires_at")
    if role == "guest":
        _need(isinstance(peer_vid, str) and _VID_RE.fullmatch(peer_vid) is not None and peer_vid[0] == "h",
              "peer_vid")
        invite_code = None
        invite_expires_at = None
    else:
        # host 的对端 vid 只认 hello 核验结果（media{peer_vid}），Servers 给了也不用
        _need(peer_vid is None or (isinstance(peer_vid, str) and _VID_RE.fullmatch(peer_vid) is not None),
              "peer_vid")
        peer_vid = None
        # 换发（续期 / 重连）时邀请可能已被兑换、Servers 不再回邀请码：有就校验，没有不算坏响应；
        # 首发缺邀请码由 runtime 判（没有可推给前端的 invite_ready）
        if invite_code is not None or invite_expires_at is not None:
            _need(isinstance(invite_code, str) and INVITE_CODE_RE.fullmatch(invite_code) is not None,
                  "invite_code")
            _need(_finite(invite_expires_at), "invite_expires_at")

    cross_region = payload.get("cross_region", False)
    _need(isinstance(cross_region, bool), "cross_region")
    entitlement = payload.get("entitlement")
    if entitlement is not None:
        _need(isinstance(entitlement, Mapping) and len(entitlement) <= _ENTITLEMENT_MAX_KEYS, "entitlement")
        entitlement = dict(entitlement)

    return VisitCredentials(
        role=role,
        visit_id=visit_id,
        char_tag=char_tag,
        transport=transport,
        expires_at=float(expires_at),
        vendor_expires_at=float(vendor_expires_at),
        vendor=vendor,
        identity_ticket=ticket,
        visit_uid=visit_uid,
        vid=vid,
        peer_vid=peer_vid,
        invite_code=invite_code,
        invite_expires_at=float(invite_expires_at) if invite_expires_at is not None else None,
        cross_region=cross_region,
        entitlement=entitlement,
    )


async def fetch_visit_credentials(
    *,
    role: str,
    visit_id: str,
    char_tag: str,
    tier: str = VISIT_VIDEO_TIER_DEFAULT,
    display_name: str | None = None,
    invite_code: str | None = None,
) -> VisitCredentials:
    """``POST {social_base}/api/visit/credentials`` and validate the reply (§4.7).

    ``char_tag`` is the character's stable ``character_uid`` (see
    :func:`resolve_char_tag`), never derived from the name. A guest must pass
    ``invite_code`` (format-checked before any network); a host must not.
    Also refreshes the pubkey cache (``force_refresh=True``) alongside.
    Raises a :class:`VisitServersError` subclass on every failure.
    """
    if role not in ROLES:
        raise ValueError("role must be 'host' or 'guest'")
    require_visit_id(visit_id)
    if not is_valid_character_uid(char_tag):
        raise ValueError("char_tag must be the character's 32-hex character_uid")
    if not VISIT_TIERS.get(tier, {}).get("enabled"):
        raise ValueError("tier is not enabled")
    if role == "guest":
        invite_code = _require_invite_code(invite_code)
    elif invite_code is not None:
        raise ValueError("a host does not redeem an invite code")

    session = await _servers_session()
    body: dict[str, Any] = {
        "role": role,
        "visit_id": visit_id,
        "char_tag": char_tag,
        "tier": tier,
        "region_hint": await _region_hint(),
        "app_version": _app_version(),
    }
    if display_name:
        body["display_name"] = str(display_name)[:_DISPLAY_NAME_MAX_CHARS]
    if invite_code is not None:
        body["invite_code"] = invite_code

    # 每次领凭证顺带刷新公钥与吊销名单（此时必然连得上 Servers）；刷新失败不影响凭证
    post, _ = await asyncio.gather(
        _send(
            "POST",
            f"{session.base_url}/api/visit/credentials",
            op="credentials",
            headers=session.headers(),
            json_body=body,
            timeout=_CREDENTIALS_TIMEOUT_S,
        ),
        fetch_pubkeys(force_refresh=True),
        return_exceptions=True,
    )
    if isinstance(post, BaseException):
        raise post
    resp = post
    if not 200 <= resp.status_code < 300:
        raise _map_error(resp, CREDENTIALS_ERROR_CONTRACT, op="credentials", account=session.account)
    try:
        return _parse_credentials(
            _body_json(resp), role=role, visit_id=visit_id, char_tag=char_tag, now=time.time(),
        )
    except _BadResponse as exc:
        logger.warning("visit servers credentials: malformed reply field=%s", exc)
        raise VisitServersUnreachable("invalid_response") from None


async def resolve_char_tag(lanlan_name: str) -> str:
    """Return the stable ``character_uid`` of ``lanlan_name`` (the ``char_tag`` of §4.7).

    A character still missing one gets it through the PR-01 backfill
    (``abackfill_character_uids``); the name itself is never used, so the
    tag survives renames. Raises ``LookupError`` when the character does
    not exist or no id could be issued.
    """
    from utils.config_manager import get_config_manager
    from utils.config_manager.reserved_schema import get_character_uid

    cm = get_config_manager()
    for attempt in range(2):
        data = await cm.aload_characters()
        catgirls = data.get("猫娘") if isinstance(data, Mapping) else None
        entry = catgirls.get(lanlan_name) if isinstance(catgirls, Mapping) else None
        if not isinstance(entry, Mapping):
            raise LookupError("unknown character")
        uid = get_character_uid(dict(entry))
        if uid:
            return uid
        if attempt == 0:
            await cm.abackfill_character_uids()
    raise LookupError("character_uid unavailable")


class VisitGrant:
    """This side's current credentials plus vendor-grant renewal (§3.2.1 / §4.3).

    The vendor grant lives ``VISIT_VENDOR_GRANT_TTL_S`` (10 min); once less
    than ``refresh_margin_s`` remains (or before a page-reload re-entry)
    :meth:`renew` re-calls ``POST /api/visit/credentials`` (Servers re-issues
    idempotently for the same account / role / visit) and keeps the identity
    ticket. A renewal that answers ``410 room_ended`` raises
    :class:`VisitRoomEnded` (finalize ``'kicked'``).
    """

    def __init__(
        self,
        credentials: VisitCredentials,
        *,
        tier: str = VISIT_VIDEO_TIER_DEFAULT,
        display_name: str | None = None,
        invite_code: str | None = None,
        refresh_margin_s: float = VISIT_VENDOR_REFRESH_MARGIN_S,
        fetch: Callable[..., Awaitable[VisitCredentials]] | None = None,
    ) -> None:
        self._current = credentials
        self._tier = tier
        self._display_name = display_name
        self._invite_code = invite_code if credentials.role == "guest" else None
        self._margin = float(refresh_margin_s)
        self._fetch = fetch or fetch_visit_credentials
        self._renewing: asyncio.Task | None = None

    @property
    def current(self) -> VisitCredentials:
        """The credentials in force (identity ticket of the first issue, latest vendor grant)."""
        return self._current

    def refresh_due(self, now: float) -> bool:
        """True when the vendor grant has less than the margin left (or expired)."""
        return self._current.vendor_remaining_s(now) < self._margin

    async def renew(self) -> VisitCredentials:
        """Fetch a fresh vendor grant; concurrent callers share one Servers call."""
        task = self._renewing
        if task is None or task.done():
            task = asyncio.ensure_future(self._renew_once())
            self._renewing = task
        return await asyncio.shield(task)

    async def _renew_once(self) -> VisitCredentials:
        cur = self._current
        fresh = await self._fetch(
            role=cur.role,
            visit_id=cur.visit_id,
            char_tag=cur.char_tag,
            tier=self._tier,
            display_name=self._display_name,
            invite_code=self._invite_code,
        )
        if (fresh.vid, fresh.visit_uid, fresh.transport) != (cur.vid, cur.visit_uid, cur.transport):
            logger.warning("visit servers credentials: renewal changed the room binding")
            raise VisitServersUnreachable("renewal_mismatch")
        self._current = cur.with_renewed_vendor(fresh)
        return self._current

    async def ensure_fresh(self, now: float) -> bool:
        """Renew when due; return True when a renewal happened."""
        if not self.refresh_due(now):
            return False
        await self.renew()
        return True


# ── 取消邀请 ───────────────────────────────────────────────────────────


async def cancel_visit_room(
    visit_id: str,
    *,
    invite_expires_at: float | None = None,
) -> bool:
    """``POST /api/visit/rooms/{visit_id}/cancel`` until done or the invite expires.

    Called in the background by the host runtime when it ends a visit before
    the peer ``hello`` verified (phase ``pending / invite_ready / joining``).
    200, ``409 guest_joined`` and ``403 invite_invalid`` all count as done
    (Servers decides by the guest's ``joined_at``). Network errors and 5xx
    retry with 1 / 2 / 4 / 8 s back-off until ``invite_expires_at`` (Servers
    time; default now + ``VISIT_INVITE_CODE_TTL_S``). Returns True when done,
    False when it gave up (expired, logged out, uncontracted reply).
    """
    require_visit_id(visit_id)
    deadline = (
        float(invite_expires_at) if invite_expires_at is not None
        else time.time() + VISIT_INVITE_CODE_TTL_S
    )
    delays = itertools.chain(_CANCEL_BACKOFF_S, itertools.repeat(_CANCEL_BACKOFF_S[-1]))
    while True:
        try:
            session = await _servers_session()
            resp = await _send(
                "POST",
                f"{session.base_url}/api/visit/rooms/{visit_id}/cancel",
                op="cancel",
                headers=session.headers(),
                timeout=_CANCEL_TIMEOUT_S,
            )
        except VisitLoginRequired:
            logger.warning("visit servers cancel: no login, invite left to expire")
            return False
        except VisitServersUnreachable:
            resp = None
        if resp is not None:
            status = resp.status_code
            if 200 <= status < 300:
                return True
            code = _body_code(_body_json(resp))
            if (status, code) in CANCEL_DONE_REPLIES:
                return True
            if status < 500:
                logger.warning(
                    "visit servers cancel: uncontracted reply status=%s code=%s", status, _diag_code(code),
                )
                return False
        delay = next(delays)
        if time.time() + delay >= deadline:
            logger.warning("visit servers cancel: gave up at invite expiry")
            return False
        await _sleep(delay)


# ── 公钥 ───────────────────────────────────────────────────────────────

_pubkeys_fetched: FetchedPubkeys | None = None
_pubkeys_failed_at: float | None = None
_pubkeys_inflight: asyncio.Task | None = None


def _pubkeys_cache_valid(now: float) -> bool:
    cached = _pubkeys_fetched
    if cached is None:
        return False
    return now < cached.fetched_at + min(cached.ttl_s, VISIT_PUBKEYS_CACHE_S)


async def _refresh_pubkeys() -> None:
    global _pubkeys_fetched, _pubkeys_failed_at
    base = social_base_url().strip().rstrip("/")
    try:
        resp = await _send("GET", f"{base}/api/visit/pubkeys", op="pubkeys", timeout=_PUBKEYS_TIMEOUT_S)
    except VisitServersUnreachable:
        _pubkeys_failed_at = time.time()
        return
    if resp.status_code != 200:
        logger.warning("visit servers pubkeys: status=%s", resp.status_code)
        _pubkeys_failed_at = time.time()
        return
    try:
        fetched = parse_pubkeys_response(_body_json(resp), fetched_at=time.time())
    except ValueError as exc:
        logger.warning("visit servers pubkeys: malformed reply: %s", exc)
        _pubkeys_failed_at = time.time()
        return
    _pubkeys_fetched = fetched
    _pubkeys_failed_at = None


async def fetch_pubkeys(*, force_refresh: bool = False) -> PubkeySet:
    """Return the verification keys: built-in table + last fetch + dev key, minus ``revoked``.

    ``GET /api/visit/pubkeys`` is cached ``min(ttl_s, VISIT_PUBKEYS_CACHE_S)``;
    ``force_refresh`` (every credentials fetch) refetches regardless. When
    the cache expired and the refresh fails the result is ``stale`` (the
    keys stay filled but ``identity.verify_identity_ticket`` fails closed: an
    unknown revocation list means no kid is trusted, built-in ones included).
    Never raises for network errors.
    """
    global _pubkeys_inflight
    now = time.time()
    recently_failed = _pubkeys_failed_at is not None and now - _pubkeys_failed_at < _PUBKEYS_RETRY_MIN_S
    if force_refresh or (not _pubkeys_cache_valid(now) and not recently_failed):
        task = _pubkeys_inflight
        if task is None or task.done() or task.get_loop() is not asyncio.get_running_loop():
            task = asyncio.ensure_future(_refresh_pubkeys())
            _pubkeys_inflight = task
        await asyncio.shield(task)
    return await asyncio.to_thread(PubkeySet.from_runtime, now=time.time(), fetched=_pubkeys_fetched)


# ── 邀请预览 ───────────────────────────────────────────────────────────


@dataclass(frozen=True, kw_only=True)
class InvitePreview:
    """Read-only invite preview (§4.7); backend only.

    ``host_visit_uid`` is compared with the local blocklist and dropped by
    :meth:`to_public` before anything reaches the frontend.
    ``host_display_name`` is already cleaned (OD-23).
    """

    visit_id: str
    host_display_name: str
    host_short_code: str
    cross_region: bool
    expires_at: float
    host_visit_uid: str = field(repr=False)

    def to_public(self, *, locally_blocked: bool) -> dict[str, Any]:
        """The frontend payload: the five fields plus ``locally_blocked``, never the uid."""
        return {
            "visit_id": self.visit_id,
            "host_display_name": self.host_display_name,
            "host_short_code": self.host_short_code,
            "cross_region": self.cross_region,
            "expires_at": self.expires_at,
            "locally_blocked": bool(locally_blocked),
        }


def is_locally_blocked(preview: InvitePreview, blocklist: Any) -> bool:
    """Blocklist verdict for the inviting host; an unreadable list counts as blocked (fail closed)."""
    try:
        return bool(blocklist.is_blocked(preview.host_visit_uid))
    except Exception as exc:  # noqa: BLE001 - 黑名单读不出来按命中处理
        logger.warning("visit invite preview: blocklist unavailable: %s", type(exc).__name__)
        return True


def _parse_preview(
    payload: Any, *, protected_names: Iterable[str], generic_label: str,
) -> InvitePreview:
    _need(isinstance(payload, Mapping), "body")
    visit_id = payload.get("visit_id")
    try:
        require_visit_id(visit_id)
    except ValueError:
        raise _BadResponse("visit_id") from None
    host_uid = payload.get("host_visit_uid")
    _need(isinstance(host_uid, str) and _VISIT_UID_RE.fullmatch(host_uid) is not None, "host_visit_uid")
    short_code = payload.get("host_short_code")
    _need(short_code == host_uid[:6].upper(), "host_short_code")
    cross_region = payload.get("cross_region", False)
    _need(isinstance(cross_region, bool), "cross_region")
    expires_at = payload.get("expires_at")
    _need(_finite(expires_at), "expires_at")
    raw_name = payload.get("host_display_name")
    _need(raw_name is None or isinstance(raw_name, str), "host_display_name")
    name = neutralize_display_name(
        raw_name, protected_names=protected_names, generic_label=generic_label, short_code=short_code,
    )
    return InvitePreview(
        visit_id=visit_id,
        host_display_name=name,
        host_short_code=short_code,
        cross_region=cross_region,
        expires_at=float(expires_at),
        host_visit_uid=host_uid,
    )


async def fetch_invite_preview(
    invite_code: str,
    *,
    generic_label: str,
    protected_names: Iterable[str] = (),
) -> InvitePreview:
    """``GET {social_base}/api/visit/invites/{code}/preview`` (read-only, §4.7).

    The code is format-checked before any network. Nothing is cached or
    written; Servers does not consume the code. ``generic_label`` /
    ``protected_names`` feed the OD-23 display-name cleaning
    (``sanitize.neutralize_display_name``).
    """
    invite_code = _require_invite_code(invite_code)
    session = await _servers_session()
    resp = await _send(
        "GET",
        f"{session.base_url}/api/visit/invites/{invite_code}/preview",
        op="invite_preview",
        headers=session.headers(),
        timeout=_PREVIEW_TIMEOUT_S,
    )
    if not 200 <= resp.status_code < 300:
        raise _map_error(resp, PREVIEW_ERROR_CONTRACT, op="invite_preview", account=session.account)
    try:
        return _parse_preview(
            _body_json(resp), protected_names=protected_names, generic_label=generic_label,
        )
    except _BadResponse as exc:
        logger.warning("visit servers invite_preview: malformed reply field=%s", exc)
        raise VisitServersUnreachable("invalid_response") from None
