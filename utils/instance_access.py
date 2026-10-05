"""Instance access credentials, separate from community OAuth and CSRF.

Loopback native traffic remains compatible. Remote HTTP/WebSocket callers
authenticate with a deployment key or an expiring, host-bound browser cookie.
Proxy metadata alone never grants access. Rotating the key revokes cookies.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import html
import ipaddress
import json
import logging
import os
import re
import secrets
import tempfile
import time
from functools import lru_cache
from pathlib import Path
from typing import NamedTuple
from urllib.parse import parse_qs, urlsplit

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse
from filelock import FileLock, Timeout as FileLockTimeout

from utils.deployment import has_forwarding_metadata, is_behind_proxy, is_remote_backend_deployment, requires_https

logger = logging.getLogger(__name__)

COOKIE = "neko_instance_access"
CHALLENGE_COOKIE = "neko_instance_challenge"
# Plaintext transports use distinct names: browsers refuse to let an HTTP
# response overwrite a Secure cookie of the same name (cookies ignore ports),
# which would otherwise trap a host paired over HTTPS in an HTTP login loop.
INSECURE_COOKIE = COOKIE + "_http"
INSECURE_CHALLENGE_COOKIE = CHALLENGE_COOKIE + "_http"
SESSION_TTL = 30 * 24 * 3600
LOGIN_PATH = "/instance-access/login"


def _key_path() -> Path:
    root = os.environ.get("NEKO_STORAGE_SELECTED_ROOT", "").strip()
    if root:
        return Path(root) / "instance_access.key"
    from utils.config_manager import get_config_manager

    return Path(get_config_manager().memory_dir).parent / "instance_access.key"


def instance_key() -> str:
    """Load one shared key; create it privately without printing credentials."""
    configured = os.environ.get("NEKO_INSTANCE_ACCESS_KEY", "").strip()
    if configured:
        if len(configured) < 32:
            raise ValueError("instance key must contain at least 32 characters")
        return configured
    path = _key_path()
    try:
        existing = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        existing = ""
    if existing:
        if len(existing) < 32:
            raise ValueError("instance key file is incomplete")
        # Published keys are atomically replaced. Normal reads need no lock;
        # missing/empty repair still serializes creation across all services.
        return existing
    path.parent.mkdir(parents=True, exist_ok=True)
    # Publish complete bytes under a cross-process lock. A crash cannot leave
    # a new empty credential visible; repair old zero-byte creation artifacts.
    with FileLock(str(path) + ".lock", timeout=5):
        key = path.read_text(encoding="utf-8").strip() if path.exists() else ""
        if not key:
            key = secrets.token_urlsafe(32)
            fd, temporary = tempfile.mkstemp(prefix=".instance-key-", dir=path.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    stream.write(key)
                    stream.flush()
                    os.fsync(stream.fileno())
                # Windows readers may briefly deny replacement of a legacy empty file.
                # Retain atomic publication and retry only sharing/access conflicts.
                for attempt in range(8):
                    try:
                        os.replace(temporary, path)
                        break
                    except PermissionError as exc:
                        if getattr(exc, "winerror", None) not in {5, 32, 33} or attempt == 7:
                            raise
                        time.sleep(0.025)
            finally:
                Path(temporary).unlink(missing_ok=True)
    if len(key) < 32:
        raise ValueError("instance key file is incomplete")
    return key


def _local_native(request: Request) -> bool:
    """Exempt actual local calls, never forwarded or non-loopback Host traffic."""
    remote = is_remote_backend_deployment()
    if is_behind_proxy() or remote:
        # Even a browser running on the Docker host needs the instance session.
        # Keep headerless loopback service-to-service calls compatible; do not
        # accidentally exempt the login page or an explicitly supplied session.
        browser_or_session = (request.headers.get("origin") or request.headers.get("sec-fetch-site")
                              or "text/html" in request.headers.get("accept", "")
                              or request.headers.get("authorization") or _session_cookies(request))
        if has_forwarding_metadata(request.headers) or browser_or_session:
            return False
    try:
        peer = ipaddress.ip_address(request.client.host if request.client else "")
        host = request.url.hostname or ""
        host_local = host == "localhost" or ipaddress.ip_address(host).is_loopback
        return (getattr(peer, "ipv4_mapped", None) or peer).is_loopback and host_local
    except ValueError:
        return False


def _signed(key: str, purpose: str, host: str, identifier: str, expires: int) -> str:
    payload = f"{identifier}.{expires}"
    signature = hmac.new(key.encode(), f"{purpose}:{host}:{payload}".encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def _verified(key: str, purpose: str, host: str, token: str) -> str | None:
    try:
        identifier, expiration, _signature = token.split(".")
        expires = int(expiration)
        if not identifier or not time.time() < expires <= time.time() + SESSION_TTL + 60:
            return None
        expected = _signed(key, purpose, host, identifier, expires)
        return identifier if _equal(token, expected) else None
    except (ValueError, TypeError):
        return None


def _session_identity(key: str, host: str, token: str, strict: bool) -> str | None:
    """Verify a session token; plaintext-minted ones carry their own purpose.

    Cookie names are client-controlled, so the signing purpose (not the name)
    records how a session was issued. Strict HTTPS mode rejects every token
    minted over plaintext, however it is presented.
    """
    identity = _verified(key, "session", host, token)
    if identity or strict:
        return identity
    return _verified(key, "session-http", host, token)


def _strict(request: Request) -> bool:
    """Read NEKO_REQUIRE_HTTPS once per request/connection, not per check.

    Cached on the scope (the middleware copies it onto a WebSocket's original
    scope too); a WebSocket keeps the value it was admitted under while its
    frames are revalidated.
    """
    scope = request.scope
    if "neko.require_https" not in scope:
        scope["neko.require_https"] = requires_https()
    return scope["neko.require_https"]


def _session_cookies(request: Request) -> list[str]:
    # A cookie minted over plaintext may have been observed in transit; strict
    # deployments must not keep honouring it after switching to HTTPS-only.
    names = (COOKIE,) if _strict(request) else (COOKIE, INSECURE_COOKIE)
    return [value for name in names if (value := request.cookies.get(name, ""))]


def remote_instance_identity(request: Request, *, key: str | None = None) -> str | None:
    """Verify explicit remote authorization; locality is not an account grant."""
    if not _transport_allowed(request):
        return None
    strict = _strict(request)
    bearer = request.headers.get("authorization", "")
    cookies = _session_cookies(request)
    if not bearer and not cookies:
        return None
    key = key or instance_key()
    if bearer.startswith("Bearer ") and _equal(bearer[7:], key):
        return "native:" + hashlib.sha256(key.encode()).hexdigest()
    if bearer.startswith("Bearer "):
        identity = _session_identity(key, request.url.hostname or "", bearer[7:], strict)
        if identity:
            return identity
    for cookie in cookies:
        identity = _session_identity(key, request.url.hostname or "", cookie, strict)
        if identity:
            return identity
    return None


def _equal(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def _pinned_origin() -> str:
    """NEKO_INSTANCE_PUBLIC_ORIGIN as scheme://host[:port]; "" if unset or not a bare origin."""
    raw = os.environ.get("NEKO_INSTANCE_PUBLIC_ORIGIN", "").strip()
    try:
        public = urlsplit(raw)
        public.port  # noqa: B018 - raises ValueError on a malformed port
    except ValueError:
        return ""
    if (public.scheme not in {"http", "https"} or not public.netloc or public.username or public.password
            or public.query or public.fragment or public.path not in {"", "/"}):
        return ""
    return f"{public.scheme}://{public.netloc}"


def _host_is(request: Request, origin: str) -> bool:
    """Whether the Host header names origin's host and effective port (":443" equals none)."""
    try:
        host, target = urlsplit("//" + request.headers.get("host", "")), urlsplit(origin)
        default = 443 if target.scheme == "https" else 80
        return bool(host.hostname) and host.hostname == target.hostname and (host.port or default) == (target.port or default)
    except ValueError:
        return False


def _secure_transport(request: Request) -> bool:
    """Allow TLS or an explicitly pinned TLS gateway with private HTTP upstreams."""
    scope = request.scope
    if "neko.secure_transport" not in scope:
        pinned = _pinned_origin()
        scope["neko.secure_transport"] = request.url.scheme in {"https", "wss"} or bool(
            is_behind_proxy() and pinned.startswith("https://") and _host_is(request, pinned))
    return scope["neko.secure_transport"]


class _Transport(NamedTuple):
    """Pairing policy and cookie names for one request's transport."""
    secure: bool
    allowed: bool
    challenge_cookie: str
    session_cookie: str
    session_purpose: str


def _transport(request: Request) -> _Transport:
    """Plaintext remote access is allowed unless NEKO_REQUIRE_HTTPS opts out."""
    if _secure_transport(request):
        return _Transport(True, True, CHALLENGE_COOKIE, COOKIE, "session")
    return _Transport(False, not _strict(request), INSECURE_CHALLENGE_COOKIE, INSECURE_COOKIE, "session-http")


def _transport_allowed(request: Request) -> bool:
    return _transport(request).allowed


def _own_origin(request: Request) -> str:
    return str(request.base_url).rstrip("/").replace("wss://", "https://", 1).replace("ws://", "http://", 1)


def request_public_origin(request: Request) -> str:
    """The origin the browser is on, for callbacks and Market proofs.

    A browser-sent Origin that passes _same_origin is authoritative. Otherwise
    requests through the pinned TLS gateway resolve to the pinned origin, and
    a direct entry (LAN IP, second HTTP port) to its own origin, so the
    host-bound session cookie is present when the browser returns there.
    """
    origin = request.headers.get("origin", "").rstrip("/")
    if origin and _same_origin(request):
        return origin
    pinned = _pinned_origin()
    if pinned and _host_is(request, pinned):
        return pinned
    return _own_origin(request)


def _same_origin(request: Request) -> bool:
    """Authenticate cookies without permitting cross-site mutations.

    Every entry the deployment serves is accepted: the request's own
    (Host-derived) origin and the pinned public origin. Pinning therefore
    does not narrow Origin to one value; Host-bound cookies, not Origin,
    keep a rebinding hostname from reusing an existing session.
    """
    origin = request.headers.get("origin", "").rstrip("/")
    if not origin:
        return request.headers.get("sec-fetch-site", "").lower() not in {"cross-site", "same-site"}
    own = _own_origin(request)
    pinned = _pinned_origin()
    if origin == own or (pinned and origin == pinned):
        return True
    # TLS ended at an outer gateway that forwards no trusted X-Forwarded-Proto:
    # the page is https:// on this very Host, which is not a foreign origin.
    # Ports compare as effective HTTPS ports, so Host ":443" equals no port.
    if not (is_behind_proxy() and own.startswith("http://") and origin.startswith("https://")):
        return False
    try:
        page, entry = urlsplit(origin), urlsplit(own)
        return (page.username is None and not page.path and not entry.path
                and page.hostname == entry.hostname and (page.port or 443) == (entry.port or 443))
    except ValueError:
        return False


@lru_cache(maxsize=8)
def _locale_strings(locale: str) -> dict:
    """Read only once per shipped locale, retaining just the pairing strings."""
    path = Path(__file__).resolve().parents[1] / "static" / "locales" / f"{locale}.json"
    return json.loads(path.read_text(encoding="utf-8"))["instanceAccess"]


def _strings(request: Request) -> dict:
    locale = request.headers.get("accept-language", "en").split(",")[0].split(";")[0]
    choices = {"en", "ja", "ko", "zh-CN", "zh-TW", "ru", "pt", "es"}
    locale = locale if locale in choices else locale.split("-")[0]
    locale = locale if locale in choices else "en"
    return _locale_strings(locale)


async def _login_verification_pause(seconds: float) -> None:
    """Apply bounded failure backoff before comparing the instance credential."""
    await asyncio.sleep(seconds)


def market_internal_proof(key: str, method: str, path: str, public_origin: str = "") -> str:
    """Bind a service handoff to its Market route and public origin for 60 seconds."""
    return _signed(key, "market-internal-remote", method + ":" + path + ":" + public_origin,
                   "market", int(time.time()) + 60)


def _market_internal_identity(request: Request, key: str) -> str | None:
    path = request.url.path
    if path != "/market" and not path.startswith("/market/"):
        return None
    try:
        local_peer = request.client and ipaddress.ip_address(request.client.host).is_loopback
    except ValueError:
        return None
    if not local_peer:
        return None
    public_origin = request.headers.get("x-neko-market-public-origin", "")
    if public_origin:
        origin = urlsplit(public_origin)
        if (origin.scheme not in ({"https"} if _strict(request) else {"https", "http"}) or not origin.netloc or origin.username or origin.password
                or origin.path or origin.query or origin.fragment):
            return None
    return _verified(key, "market-internal-remote", request.method + ":" + path + ":" + public_origin,
                     request.headers.get("x-neko-market-internal", ""))


class InstanceAccessMiddleware:
    """Gate all service routes before body parsing, including WebSockets."""

    def __init__(self, app, community_handoff_authorizer=None):
        self.app = app
        self.community_handoff_authorizer = community_handoff_authorizer
        self.attempts: dict[str, tuple[float, int]] = {}
        self.unlabelled_tls_warned_at: float | None = None

    async def __call__(self, scope, receive, send):
        if scope["type"] not in {"http", "websocket"}:
            return await self.app(scope, receive, send)
        request_scope = scope if scope["type"] == "http" else {**scope, "type": "http", "method": "GET"}
        request = Request(request_scope, receive=receive)
        if request_scope is not scope:
            # Downstream WebSocket handlers see the original scope, not this
            # copy; share the per-connection transport decisions with them.
            scope["neko.require_https"] = _strict(request)
            scope["neko.secure_transport"] = _secure_transport(request)
        if _local_native(request):
            return await self.app(scope, receive, send)
        if scope["type"] == "http" and self.community_handoff_authorizer and _transport_allowed(request):
            authorized, replay_body = await self.community_handoff_authorizer(request)
            if scope.get("neko.community_auth_rate_limited"):
                return await self._deny(scope, receive, send, "identity_verification_rate_limited", 429)
            if scope.get("neko.community_auth_unavailable"):
                return await self._deny(scope, receive, send, "identity_verification_unavailable", 503)
            if authorized:
                if replay_body is not None:
                    original_receive = receive
                    pending_body = True

                    async def replay_receive():
                        nonlocal pending_body
                        if pending_body:
                            pending_body = False
                            return {"type": "http.request", "body": replay_body, "more_body": False}
                        return await original_receive()

                    receive = replay_receive
                # Route-specific tickets/delegates remain an independent,
                # narrow authorization contract; never grant instance identity.
                return await self.app(scope, receive, send)
        path = scope.get("path", "")
        login_page = scope["type"] == "http" and (path == LOGIN_PATH or (request.method == "GET" and "text/html" in request.headers.get("accept", "")))
        if not login_page and not request.headers.get("authorization") and not _session_cookies(request) and not request.headers.get("x-neko-market-internal"):
            return await self._deny(scope, receive, send, "instance_authorization_required", 401)
        try:
            key = await asyncio.to_thread(instance_key)
            internal_identity = _market_internal_identity(request, key)
            identity = internal_identity or remote_instance_identity(request, key=key)
        except (OSError, ValueError, FileLockTimeout):
            return await self._deny(scope, receive, send, "instance_access_unavailable", 503)
        if scope["type"] == "http" and path == LOGIN_PATH:
            return await self._login(request, key, scope, receive, send)
        if not identity:
            if scope["type"] == "http" and request.method == "GET" and "text/html" in request.headers.get("accept", ""):
                return await (await self._page(request, key))(scope, receive, send)
            return await self._deny(scope, receive, send, "instance_authorization_required", 401)
        oauth_callback = request.method == "GET" and path in {"/oauth/callback", "/api/card-drop/oauth/callback", "/oauth/relay", "/market/oauth/callback"}
        # Only entry documents may be opened from another site. Account reads,
        # arbitrary GET routes, frames and mutations retain the origin guard.
        entry_navigation = (scope["type"] == "http" and request.method in {"GET", "HEAD"}
                            and path in {"/", "/chat", "/subtitle"}
                            and request.headers.get("sec-fetch-mode") == "navigate"
                            and request.headers.get("sec-fetch-dest") == "document"
                            and not request.headers.get("origin"))
        if not internal_identity and not oauth_callback and not entry_navigation and not _same_origin(request):
            return await self._deny(scope, receive, send, "origin_not_allowed", 403)
        scope["neko.instance_identity"] = identity
        if internal_identity:
            scope["neko.market_public_origin"] = request.headers.get("x-neko-market-public-origin", "")
            # The signed purpose includes remote; changing the network hop to
            # loopback must never grant native-only Market token privileges.
            scope["neko.market_remote_authorized"] = True
        revoked = False
        started = False
        active_key = key
        next_key_check = time.monotonic() + 1

        async def still_authorized():
            # Revalidate before each socket message or HTTP stream chunk so a
            # rotated key/expired cookie cannot leave an account stream open.
            nonlocal active_key, next_key_check
            configured = os.environ.get("NEKO_INSTANCE_ACCESS_KEY", "").strip()
            if configured:
                if len(configured) < 32:
                    return False
                active_key = configured
            elif time.monotonic() >= next_key_check:
                for attempt in range(3):
                    try:
                        active_key = await asyncio.to_thread(instance_key)
                        break
                    except (OSError, FileLockTimeout):
                        if attempt == 2:
                            return False
                        await asyncio.sleep(.05)
                    except ValueError:
                        return False
                next_key_check = time.monotonic() + 1
            try:
                # The hop proof deadline limits admission of new requests. Once
                # admitted, long downloads/installations retain that grant,
                # while key rotation still revokes the in-flight request.
                if internal_identity:
                    return _equal(active_key, key)
                return remote_instance_identity(request, key=active_key) == identity
            except ValueError:
                return False

        async def private_send(message):
            nonlocal revoked, started
            if revoked:
                raise OSError("instance authorization revoked")
            if not await still_authorized():
                revoked = True
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 4401})
                elif started:
                    await send({"type": "http.response.body", "body": b"", "more_body": False})
                else:
                    await self._deny(scope, receive, send, "instance_authorization_required", 401)
                raise OSError("instance authorization revoked")
            if message["type"] == "http.response.start":
                started = True
                if scope.get("path", "").startswith(("/static/", "/assets/", "/user_live2d/",
                                                      "/user_live2d_local/", "/user_vrm/", "/user_mmd/",
                                                      "/user_pngtuber/", "/user_avatar_tools/", "/user_mods/",
                                                      "/workshop/")):
                    # Authenticated assets may keep browser caching, never
                    # public/shared caching that would bypass the entry guard.
                    headers = [(k, re.sub(rb"\bpublic\b", b"private", v, flags=re.I) if k.lower() == b"cache-control" else v)
                               for k, v in message.get("headers", [])]
                    if not any(k.lower() == b"cache-control" for k, _v in headers):
                        headers.append((b"cache-control", b"private"))
                    headers.append((b"vary", b"Cookie, Authorization"))
                    message = {**message, "headers": headers}
                else:
                    message = {**message, "headers": [(k, v) for k, v in message.get("headers", []) if k.lower() != b"cache-control"] + [(b"cache-control", b"no-store")]}
            await send(message)

        async def private_receive():
            nonlocal revoked
            message = await receive()
            if revoked:
                return ({"type": "websocket.disconnect", "code": 4401}
                        if scope["type"] == "websocket" else {"type": "http.disconnect"})
            if not await still_authorized():
                revoked = True
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 4401})
                    return {"type": "websocket.disconnect", "code": 4401}
                return {"type": "http.disconnect"}
            return message

        try:
            return await self.app(scope, private_receive, private_send)
        except OSError:
            if not revoked:
                raise

    def _warn_unlabelled_tls(self):
        # The browser is on https:// but nothing tells this server so: pairing
        # proceeds conservatively as plaintext (warning, *_http cookie, and
        # refused under NEKO_REQUIRE_HTTPS). Only the operator can fix that.
        # Hourly rather than once, so an unauthenticated request that reaches
        # this first cannot suppress the hint for the life of the process.
        now = time.monotonic()
        if self.unlabelled_tls_warned_at is None or now - self.unlabelled_tls_warned_at >= 3600:
            self.unlabelled_tls_warned_at = now
            logger.warning(
                "instance access: browser pairs over https:// but this request arrived as plaintext; "
                "set NEKO_INSTANCE_PUBLIC_ORIGIN to the public https origin or forward a trusted "
                "X-Forwarded-Proto so the session is issued as HTTPS (required for NEKO_REQUIRE_HTTPS=1)")

    async def _deny(self, scope, receive, send, detail, status):
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 4401 if status == 401 else 4403})
        else:
            headers = {"Cache-Control": "no-store"}
            community_origin = scope.get("neko.community_cors_origin")
            if community_origin:
                headers.update({"Access-Control-Allow-Origin": community_origin, "Vary": "Origin"})
            await JSONResponse({"detail": detail}, status_code=status, headers=headers)(scope, receive, send)

    async def _page(self, request: Request, key: str, *, failed=False):
        strings = await asyncio.to_thread(_strings, request)
        transport = _transport(request)
        secure, allowed = transport.secure, transport.allowed
        challenge = request.cookies.get(transport.challenge_cookie, "")
        if not _verified(key, "challenge", request.url.hostname or "", challenge):
            challenge = _signed(key, "challenge", request.url.hostname or "", secrets.token_hex(16), int(time.time()) + 600)
        message = strings["failed"] if failed else strings["description"]
        if not allowed:
            message = strings["httpsRequired"]
        # Plaintext pairing is permitted, but the owner must be told the key
        # and session cookie travel unencrypted on this connection.
        warning = "" if secure or not allowed else f'<p role="alert">{html.escape(strings["insecureWarning"])}</p>'
        response = HTMLResponse(
            '<!doctype html><html><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
            f'<title>{html.escape(strings["title"])}</title><main><h1>{html.escape(strings["title"])}</h1>'
            f'<p>{html.escape(message)}</p>{warning}<form method="post" action="{LOGIN_PATH}">'
            f'<input type="hidden" name="challenge" value="{challenge}">'
            f'<input type="hidden" name="return_path" value="{html.escape((request.url.path + ("?" + request.url.query if request.url.query else "")) if request.url.path != LOGIN_PATH else "/", quote=True)}">'
            f'<label>{html.escape(strings["key"])} <input type="password" name="key" required autocomplete="current-password"></label>'
            f'<button {"disabled" if not allowed else ""}>{html.escape(strings["connect"])}</button></form></main></html>',
            status_code=401,
            # no-referrer makes Chromium's native form POST Origin opaque
            # ("null"). same-origin retains the required own-origin signal
            # while preventing a referrer from being sent to another site.
            headers={"Cache-Control": "no-store", "Content-Security-Policy": "default-src 'none'; form-action 'self'; frame-ancestors 'none'", "Referrer-Policy": "same-origin"},
        )
        response.set_cookie(transport.challenge_cookie, challenge, httponly=True, secure=secure, samesite="strict", max_age=600)
        return response

    async def _login(self, request, key, scope, receive, send):
        if request.method != "POST":
            return await (await self._page(request, key))(scope, receive, send)
        transport = _transport(request)
        same_origin = _same_origin(request)
        # Before the deny: under NEKO_REQUIRE_HTTPS this is exactly the refusal
        # the operator needs explained. Same-origin only, so a cross-site
        # https Origin cannot use up the hourly hint.
        if same_origin and not transport.secure and request.headers.get("origin", "").startswith("https://"):
            self._warn_unlabelled_tls()
        if not transport.allowed or not same_origin:
            return await self._deny(scope, receive, send, "secure_same_origin_required", 403)
        peer = request.client.host if request.client else "unknown"
        now = time.time()
        self.attempts = {ip: item for ip, item in self.attempts.items() if item[0] > now - 60}
        started, count = self.attempts.get(peer, (now, 0))
        # Delay verification itself, including a correct guess, rather than
        # merely returning 429 after an unlimited fast key-comparison oracle.
        # A shared gateway can delay its owner by at most two seconds, never
        # permanently reject a valid credential because another client failed.
        failures = max(count, sum(item[1] for item in self.attempts.values()) // 10)
        if failures:
            await _login_verification_pause(min(.25 * failures, 2.0))
        data = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            data.extend(message.get("body", b""))
            if len(data) > 4096:
                return await self._deny(scope, receive, send, "instance_login_body_too_large", 413)
            if not message.get("more_body"):
                break
        fields = parse_qs(data.decode("utf-8", errors="replace"))
        challenge = fields.get("challenge", [""])[0]
        supplied = fields.get("key", [""])[0]
        valid = _verified(key, "challenge", request.url.hostname or "", challenge)
        if not valid or not _equal(challenge, request.cookies.get(transport.challenge_cookie, "")) or not _equal(key, supplied):
            # Re-read after awaits so simultaneous failures do not overwrite
            # each other's increments with the same stale admission count.
            started, count = self.attempts.get(peer, (time.time(), 0))
            # Gateway clients may share one peer IP. Failed attempts must never
            # prevent an owner holding both the challenge and correct key.
            if count >= 10:
                return await self._deny(scope, receive, send, "instance_login_rate_limited", 429)
            if peer not in self.attempts and len(self.attempts) >= 1024:
                self.attempts.pop(min(self.attempts, key=lambda ip: self.attempts[ip][0]))
            self.attempts[peer] = (started, count + 1)
            return await (await self._page(request, key, failed=True))(scope, receive, send)
        self.attempts.pop(peer, None)
        target = fields.get("return_path", ["/"])[0]
        if not target.startswith("/") or target.startswith("//") or "\\" in target or "\r" in target or "\n" in target:
            target = "/"
        response = RedirectResponse(target, status_code=303, headers={"Cache-Control": "no-store"})
        cookie = _signed(key, transport.session_purpose, request.url.hostname or "",
                         secrets.token_hex(16), int(now) + SESSION_TTL)
        # Only an encrypted transport may mint the Secure cookie; plaintext
        # pairing uses its own name so neither can shadow the other.
        response.set_cookie(transport.session_cookie, cookie, max_age=SESSION_TTL,
                            httponly=True, secure=transport.secure, samesite="lax")
        response.delete_cookie(transport.challenge_cookie, secure=transport.secure, httponly=True, samesite="strict")
        await response(scope, receive, send)


if __name__ == "__main__":
    # Explicit administrator action; never emit this credential from server logs.
    print(instance_key())
