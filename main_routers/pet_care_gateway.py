"""Bounded relay from N.E.K.O to the Verse PetCare edge.

This module deliberately contains no PetCare rules or persistence. It reads
the persisted desktop OAuth session and installation credential, then
forwards only the fixed public PetCare protocol. Production uses the configured
community origin; an explicit development override may select only a loopback
Verse edge. The edge remains responsible for account, installation, scope and
service authorization, including fresh token and session-epoch validation.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import sys
import threading
import time
import uuid
from urllib.parse import parse_qsl, urlencode, urlparse

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from main_routers import card_drop_router as C
from main_routers import community_oauth as O


router = APIRouter(prefix="/api/pet-care", tags=["pet-care"])

_MAX_REQUEST_BYTES = 64 * 1024
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_GATEWAY_PREFIX = "/api/pet-care"
_LOCAL_CAPABILITY_HEADER = "X-Neko-Pet-Care-Capability"
_LOCAL_CAPABILITY_TTL_SEC = 30
_LOCAL_CAPABILITY_MAX_ACTIVE = 128
_CHARACTER_UID_RE = re.compile(r"^[0-9a-f]{32}$")
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_OPAQUE_URL_SEGMENT_RE = re.compile(r"^[A-Za-z0-9._~-]{1,4096}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_LOCAL_CAPABILITY_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32,256}$")
_DEV_UPSTREAM_ENV = "NEKO_PET_CARE_DEV_BASE_URL"
_MAX_BEARER_LENGTH = 16 * 1024
_CLIENT_ID_LENGTH = (8, 64)
_CLIENT_PROOF_LENGTH = (32, 256)

# Digest -> one exact local write authorization.  This is deliberately an
# in-memory browser boundary, not device attestation: it blocks cross-site
# browser requests and stale/replayed tickets, but a process running as the same
# OS user that can reach loopback and the saved session is outside this claim.
_local_capabilities: dict[str, dict[str, object]] = {}
_local_capabilities_lock = threading.Lock()


def _error(detail: str, status_code: int) -> JSONResponse:
    return JSONResponse(
        {"detail": detail, "code": detail},
        status_code=status_code,
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


def _pet_care_upstream() -> tuple[str | None, bool]:
    """Resolve the Verse edge origin used only by this PetCare relay.

    Production and ordinary development keep using the configured community
    origin. A local integration run may explicitly select a loopback Verse
    edge without redirecting OAuth, community pages, or any other community
    API. Invalid explicit overrides fail closed instead of falling back.
    """
    raw = os.environ.get(_DEV_UPSTREAM_ENV)
    if raw is None or not raw.strip():
        return C._social_base_url().rstrip("/"), False
    # Official desktop builds must never accept a local PetCare authority.
    # The override exists only for source-tree integration runs; otherwise a
    # stray inherited environment variable could redirect the real OAuth
    # bearer and installation proof to a loopback process.
    if getattr(sys, "frozen", False) or "__compiled__" in globals():
        return None, True
    if raw != raw.strip():
        return None, True
    try:
        parsed = urlparse(raw)
        host = (parsed.hostname or "").lower()
        port = parsed.port
    except ValueError:
        return None, True
    if (
        parsed.scheme not in {"http", "https"}
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        return None, True
    if host != "localhost":
        try:
            if not ipaddress.ip_address(host).is_loopback:
                return None, True
        except ValueError:
            return None, True
    authority = f"[{host}]" if ":" in host else host
    if port is not None:
        authority = f"{authority}:{port}"
    return f"{parsed.scheme}://{authority}", True


def _capability_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalize_capability(value: object) -> str:
    candidate = value.strip() if isinstance(value, str) else ""
    return candidate if _LOCAL_CAPABILITY_TOKEN_RE.fullmatch(candidate) else ""


def _prune_local_capabilities(now: float | None = None) -> None:
    """Drop expired capabilities while holding ``_local_capabilities_lock``."""
    current = time.monotonic() if now is None else now
    expired = [
        digest
        for digest, entry in _local_capabilities.items()
        if float(entry.get("expires_at") or 0) <= current
    ]
    for digest in expired:
        _local_capabilities.pop(digest, None)


def _issue_local_capability(
    *,
    session_fingerprint: str,
    method: str,
    gateway_path: str,
    body_sha256: str,
    now: float | None = None,
) -> str:
    if (
        not session_fingerprint
        or method != "POST"
        or not _canonical_post_gateway_path(gateway_path)
        or not _SHA256_RE.fullmatch(body_sha256)
    ):
        raise ValueError("invalid local PetCare capability binding")
    current = time.monotonic() if now is None else now
    with _local_capabilities_lock:
        _prune_local_capabilities(current)
        while len(_local_capabilities) >= _LOCAL_CAPABILITY_MAX_ACTIVE:
            oldest = min(
                _local_capabilities,
                key=lambda digest: float(
                    _local_capabilities[digest].get("expires_at") or 0
                ),
            )
            _local_capabilities.pop(oldest, None)
        capability = secrets.token_urlsafe(32)
        _local_capabilities[_capability_digest(capability)] = {
            "expires_at": current + _LOCAL_CAPABILITY_TTL_SEC,
            "session_fingerprint": session_fingerprint,
            "method": method,
            "gateway_path": gateway_path,
            "body_sha256": body_sha256,
        }
    return capability


def _discard_local_capability(value: object) -> None:
    capability = _normalize_capability(value)
    if not capability:
        return
    with _local_capabilities_lock:
        _local_capabilities.pop(_capability_digest(capability), None)


def _consume_local_capability(
    value: object,
    *,
    session_fingerprint: str,
    method: str,
    gateway_path: str,
    body_sha256: str,
    now: float | None = None,
) -> bool:
    capability = _normalize_capability(value)
    if not capability or not session_fingerprint:
        return False
    current = time.monotonic() if now is None else now
    with _local_capabilities_lock:
        _prune_local_capabilities(current)
        # A presented capability is single-use even when its binding is wrong.
        # That avoids turning one leaked token into a path/body matching oracle.
        entry = _local_capabilities.pop(_capability_digest(capability), None)
    if not entry:
        return False
    return all(
        (
            hmac.compare_digest(
                str(entry.get("session_fingerprint") or ""), session_fingerprint
            ),
            hmac.compare_digest(str(entry.get("method") or ""), method),
            hmac.compare_digest(
                str(entry.get("gateway_path") or ""), gateway_path
            ),
            hmac.compare_digest(
                str(entry.get("body_sha256") or ""), body_sha256
            ),
        )
    )


def _local_capability_session_fingerprint(snapshot: object) -> str:
    if not isinstance(snapshot, dict):
        return ""
    local_user_id = C.normalize_local_user_id(snapshot.get("local_user_id"))
    auth_source = str(snapshot.get("auth_source") or "").strip().lower()
    base_url = str(snapshot.get("base_url") or "").strip().rstrip("/")
    session_id = str(snapshot.get("session_id") or "").strip()
    if local_user_id and auth_source and base_url and session_id:
        material = json.dumps(
            {
                "auth_source": auth_source,
                "base_url": base_url,
                "client_id": str(snapshot.get("client_id") or "").strip(),
                "local_user_id": local_user_id,
                "session_id": session_id,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()
    # Legacy sessions without a stable session_id remain fail-closed across an
    # OAuth token refresh by reusing the older token-bound fingerprint.
    return C._desktop_session_fingerprint(snapshot)


def _visible_ascii_credential(
    value: object,
    *,
    min_length: int,
    max_length: int,
) -> str:
    """Return a header-safe saved credential without interpreting its authority."""
    if not isinstance(value, str) or not min_length <= len(value) <= max_length:
        return ""
    if value != value.strip() or any(not 0x21 <= ord(char) <= 0x7E for char in value):
        return ""
    return value


def _validated_saved_transport_snapshot(snapshot: object) -> dict | None:
    """Validate only the persisted fields needed to transport a PetCare request.

    Token freshness, revocation, account state and ``session_epoch`` deliberately
    remain Verse edge decisions. This local check only rejects a missing or
    structurally unusable saved session before putting credentials on the wire.
    """
    if not isinstance(snapshot, dict):
        return None
    access_token = _visible_ascii_credential(
        snapshot.get("access_token"),
        min_length=1,
        max_length=_MAX_BEARER_LENGTH,
    )
    local_user_id = C.normalize_local_user_id(snapshot.get("local_user_id"))
    auth_source = C._normalize_auth_source(snapshot.get("auth_source"))
    base_url = str(snapshot.get("base_url") or "").strip().rstrip("/")
    normalized = {
        **snapshot,
        "access_token": access_token,
        "auth_source": auth_source,
        "base_url": base_url,
        "local_user_id": local_user_id,
    }
    if (
        not access_token
        or not local_user_id
        or not auth_source
        or not base_url
        or not _local_capability_session_fingerprint(normalized)
    ):
        return None
    return normalized


def _read_saved_transport_context(
) -> tuple[str, dict | None, tuple[str, str] | None]:
    """Read one fenced local session plus its installation bearer credentials.

    The gateway does not introspect the OAuth bearer. The public Verse edge
    performs the fresh authoritative checks on every forwarded request.
    """
    try:
        with C._social_session_locks(C._social_session_paths()):
            snapshot = _validated_saved_transport_snapshot(
                C._desktop_session_snapshot()
            )
            if snapshot is None:
                return "login-required", None, None
            credentials = C._get_client_credentials()
            if not isinstance(credentials, tuple) or len(credentials) != 2:
                return "client-binding-required", snapshot, None
            client_id = _visible_ascii_credential(
                credentials[0],
                min_length=_CLIENT_ID_LENGTH[0],
                max_length=_CLIENT_ID_LENGTH[1],
            )
            client_proof = _visible_ascii_credential(
                credentials[1],
                min_length=_CLIENT_PROOF_LENGTH[0],
                max_length=_CLIENT_PROOF_LENGTH[1],
            )
            if not client_id or not client_proof:
                return "client-binding-required", snapshot, None
            return "ok", snapshot, (client_id, client_proof)
    except (OSError, TimeoutError):
        return "busy", None, None


def _issue_local_capability_for_verified_session(
    snapshot: dict,
    *,
    method: str,
    gateway_path: str,
    body_sha256: str,
) -> tuple[str, str | None]:
    expected_fingerprint = _local_capability_session_fingerprint(snapshot)
    if not expected_fingerprint:
        return "changed", None
    try:
        # Login, logout and OAuth publication use the same file locks.  The
        # capability therefore belongs either wholly before or wholly after a
        # session replacement, never to a torn two-file snapshot.
        with C._social_session_locks(C._social_session_paths()):
            current_fingerprint = _local_capability_session_fingerprint(
                C._desktop_session_snapshot()
            )
            if not current_fingerprint or not hmac.compare_digest(
                current_fingerprint, expected_fingerprint
            ):
                return "changed", None
            return "ok", _issue_local_capability(
                session_fingerprint=current_fingerprint,
                method=method,
                gateway_path=gateway_path,
                body_sha256=body_sha256,
            )
    except (OSError, TimeoutError):
        return "busy", None


def _consume_local_capability_for_verified_session(
    capability: object,
    snapshot: dict,
    *,
    method: str,
    gateway_path: str,
    body_sha256: str,
) -> str:
    if not _normalize_capability(capability):
        return "invalid"
    expected_fingerprint = _local_capability_session_fingerprint(snapshot)
    if not expected_fingerprint:
        _discard_local_capability(capability)
        return "invalid"
    try:
        with C._social_session_locks(C._social_session_paths()):
            current_fingerprint = _local_capability_session_fingerprint(
                C._desktop_session_snapshot()
            )
            if not current_fingerprint or not hmac.compare_digest(
                current_fingerprint, expected_fingerprint
            ):
                _discard_local_capability(capability)
                return "invalid"
            return (
                "ok"
                if _consume_local_capability(
                    capability,
                    session_fingerprint=current_fingerprint,
                    method=method,
                    gateway_path=gateway_path,
                    body_sha256=body_sha256,
                )
                else "invalid"
            )
    except (OSError, TimeoutError):
        # A presented token is spent even when the session fence is busy.  The
        # durable business request remains unchanged and its retry obtains a
        # fresh transport capability after the lock becomes available.
        _discard_local_capability(capability)
        return "busy"


def _route_allowed(method: str, suffix: str) -> bool:
    # Match only the literal canonical path we are about to forward.  In
    # particular, do not let URL clients collapse dot segments after this
    # whitelist check and thereby target a different upstream endpoint.
    if (
        not suffix.startswith("/")
        or suffix == "/"
        or suffix.endswith("/")
        or "//" in suffix
        or "\\" in suffix
    ):
        return False
    parts = suffix[1:].split("/")
    if any(part in {".", ".."} for part in parts):
        return False
    if method == "GET":
        if suffix == "/context":
            return True
        if len(parts) == 2 and parts[0] == "pets" and _CHARACTER_UID_RE.fullmatch(parts[1]):
            return True
        if (
            len(parts) == 3
            and parts[0] == "pets"
            and _CHARACTER_UID_RE.fullmatch(parts[1])
            and parts[2] == "tasks"
        ):
            return True
        return bool(
            len(parts) == 4
            and parts[0] == "pets"
            and _CHARACTER_UID_RE.fullmatch(parts[1])
            and parts[2] == "explorations"
            and _ID_RE.fullmatch(parts[3])
        )
    if method != "POST":
        return False
    if suffix in {"/session/resume", "/role-aliases/sync", "/bind"}:
        return True
    if not (
        len(parts) >= 3
        and parts[0] == "pets"
        and _CHARACTER_UID_RE.fullmatch(parts[1])
    ):
        return False
    if len(parts) == 3 and parts[2] in {
        "care-actions",
        "explorations",
        "status-recovery",
        "play-leases",
    }:
        return True
    if (
        len(parts) == 5
        and parts[2] == "explorations"
        and _ID_RE.fullmatch(parts[3])
        and parts[4] in {"complete", "recall"}
    ):
        return True
    if (
        len(parts) == 5
        and parts[2] == "tasks"
        and _ID_RE.fullmatch(parts[3])
        and parts[4] in {"claim", "complete"}
    ):
        return True
    return bool(
        len(parts) == 5
        and parts[2] == "play-leases"
        and _OPAQUE_URL_SEGMENT_RE.fullmatch(parts[3])
        and parts[4] == "complete"
    )


def _canonical_post_gateway_path(value: object) -> str:
    path = value if isinstance(value, str) else ""
    if (
        not path.startswith(f"{_GATEWAY_PREFIX}/")
        or len(path) > 8192
        or "?" in path
        or "#" in path
        or "\\" in path
        or "//" in path
        or path.endswith("/")
    ):
        return ""
    suffix = path[len(_GATEWAY_PREFIX) :]
    return path if _route_allowed("POST", suffix) else ""


def _canonical_query(suffix: str, raw_query: str) -> str:
    try:
        pairs = parse_qsl(raw_query, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise ValueError("invalid query") from exc
    if len({key for key, _value in pairs}) != len(pairs):
        raise ValueError("duplicate query")
    query = dict(pairs)
    if suffix == "/context":
        if set(query) != {"character_uid"} or not _CHARACTER_UID_RE.fullmatch(
            query["character_uid"]
        ):
            raise ValueError("invalid context query")
    elif query:
        raise ValueError("query not allowed")
    return urlencode(sorted(query.items()))


async def _read_bounded_body(request: Request) -> bytes:
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared = int(content_length)
        except ValueError:
            declared = -1
        if declared < 0 or declared > _MAX_REQUEST_BYTES:
            raise ValueError("request too large")
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > _MAX_REQUEST_BYTES:
            raise ValueError("request too large")
        body.extend(chunk)
    return bytes(body)


async def _read_bounded_response(response: httpx.Response) -> bytes:
    content_length = response.headers.get("content-length")
    if content_length is not None:
        try:
            declared = int(content_length)
        except ValueError:
            declared = -1
        if declared < 0 or declared > _MAX_RESPONSE_BYTES:
            raise ValueError("response too large")
    body = bytearray()
    async for chunk in response.aiter_bytes():
        if len(body) + len(chunk) > _MAX_RESPONSE_BYTES:
            raise ValueError("response too large")
        body.extend(chunk)
    return bytes(body)


@router.get("/local-capability")
async def issue_local_capability(request: Request) -> Response:
    """Mint one short-lived authorization for one exact local PetCare write."""
    if (
        not C._local_ui_request_source_allowed(request)
        or await O.account_request_identity(request) is None
    ):
        return _error("instance_authorization_required", 403)
    try:
        pairs = parse_qsl(request.url.query, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        return _error("pet_care_local_capability_request_invalid", 422)
    if len({key for key, _value in pairs}) != len(pairs):
        return _error("pet_care_local_capability_request_invalid", 422)
    query = dict(pairs)
    if set(query) != {"method", "path", "body_sha256"}:
        return _error("pet_care_local_capability_request_invalid", 422)
    method = query["method"].strip().upper()
    gateway_path = _canonical_post_gateway_path(query["path"])
    body_sha256 = query["body_sha256"].strip().lower()
    if method != "POST" or not gateway_path or not _SHA256_RE.fullmatch(body_sha256):
        return _error("pet_care_local_capability_request_invalid", 422)

    transport_outcome, snapshot, _credentials = await asyncio.to_thread(
        _read_saved_transport_context
    )
    if transport_outcome == "busy":
        return _error("pet_care_local_session_busy", 503)
    if transport_outcome == "client-binding-required":
        return _error("client_binding_required", 403)
    if transport_outcome != "ok" or snapshot is None:
        return _error("community_login_required", 401)
    outcome, capability = await asyncio.to_thread(
        _issue_local_capability_for_verified_session,
        snapshot,
        method=method,
        gateway_path=gateway_path,
        body_sha256=body_sha256,
    )
    if outcome == "busy":
        return _error("pet_care_local_session_busy", 503)
    if outcome != "ok" or not capability:
        return _error("pet_care_local_session_changed", 409)
    return JSONResponse(
        {
            "capability": capability,
            "expires_in": _LOCAL_CAPABILITY_TTL_SEC,
        },
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


@router.api_route("/{pet_path:path}", methods=["GET", "POST"])
async def pet_care_gateway(pet_path: str, request: Request) -> Response:
    suffix = f"/{pet_path}"
    if not _route_allowed(request.method, suffix):
        return _error("pet_care_route_not_allowed", 404)
    try:
        query = _canonical_query(suffix, request.url.query)
    except ValueError:
        return _error("pet_care_request_contract_invalid", 422)
    if (
        not C._local_request_source_allowed(request)
        or await O.account_request_identity(request) is None
    ):
        return _error("instance_authorization_required", 403)

    transport_outcome, snapshot, credentials = await asyncio.to_thread(
        _read_saved_transport_context
    )
    if transport_outcome == "busy":
        if request.method == "POST":
            for presented_capability in request.headers.getlist(
                _LOCAL_CAPABILITY_HEADER
            ):
                _discard_local_capability(presented_capability)
        return _error("pet_care_local_session_busy", 503)
    if transport_outcome == "client-binding-required":
        return _error("client_binding_required", 403)
    if transport_outcome != "ok" or snapshot is None or credentials is None:
        return _error("community_login_required", 401)
    verified_expected_account = ""
    if suffix == "/role-aliases/sync":
        # The browser captures the account immediately before the local
        # directory mutation.  Fence the later notification against that
        # exact locally persisted OAuth account so a concurrent logout/login
        # cannot retire an equal device-local UID under another account.
        expected_account = C.normalize_local_user_id(
            request.headers.get("X-Neko-Expected-Account")
        )
        current_account = C.normalize_local_user_id(
            snapshot.get("local_user_id") if isinstance(snapshot, dict) else None
        )
        if not expected_account:
            return _error("pet_care_expected_account_required", 422)
        if not current_account or expected_account != current_account:
            return _error("pet_care_account_changed", 409)
        verified_expected_account = current_account
    access_token = snapshot["access_token"]
    client_id, client_proof = credentials

    try:
        raw = await _read_bounded_body(request)
    except ValueError:
        return _error("pet_care_request_too_large", 413)
    if request.method == "POST":
        content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            return _error("pet_care_json_required", 415)
        if not raw:
            return _error("pet_care_invalid_json", 422)
    elif raw:
        return _error("pet_care_request_contract_invalid", 422)

    if request.method == "POST":
        presented = request.headers.getlist(_LOCAL_CAPABILITY_HEADER)
        if len(presented) != 1:
            return _error("pet_care_local_capability_invalid", 403)
        capability_outcome = await asyncio.to_thread(
            _consume_local_capability_for_verified_session,
            presented[0],
            snapshot,
            method="POST",
            gateway_path=f"{_GATEWAY_PREFIX}{suffix}",
            body_sha256=hashlib.sha256(raw).hexdigest(),
        )
        if capability_outcome == "busy":
            return _error("pet_care_local_session_busy", 503)
        if capability_outcome != "ok":
            return _error("pet_care_local_capability_invalid", 403)

    headers = {
        "Authorization": f"Bearer {access_token}",
        "X-Client-Id": client_id,
        "X-Client-Proof": client_proof,
        "Accept": "application/json",
        "X-Correlation-Id": request.headers.get("X-Correlation-Id")
        or str(uuid.uuid4()),
    }
    if request.method == "POST":
        headers["Content-Type"] = "application/json"
    if verified_expected_account:
        # Forward only the value re-derived from the verified local OAuth
        # snapshot, never the browser header itself.  The Verse edge fences it
        # once more against its freshly authenticated user.
        headers["X-Neko-Expected-Account"] = verified_expected_account
    idempotency_key = request.headers.get("Idempotency-Key")
    if idempotency_key:
        if idempotency_key != idempotency_key.strip() or len(idempotency_key) > 200:
            return _error("invalid_idempotency_key", 422)
        headers["Idempotency-Key"] = idempotency_key

    base_url, explicit_dev_upstream = _pet_care_upstream()
    if base_url is None:
        return _error("pet_care_dev_upstream_invalid", 503)
    if (
        not explicit_dev_upstream
        and not C.client_registration.proof_transport_allowed(base_url)
    ):
        # This relay carries both the OAuth bearer and the installation proof.
        # Normal routing reuses the client-registration transport policy. The
        # explicit development branch was already restricted to a loopback
        # origin by _pet_care_upstream(), so its HTTP transport stays local.
        return _error("pet_care_transport_not_secure", 503)
    if not explicit_dev_upstream:
        saved_base_url = str(snapshot.get("base_url") or "").strip().rstrip("/")
        if not C._same_originish(saved_base_url, base_url):
            # Keep credentials in their original community environment.  The
            # explicit loopback development branch is intentionally exempt so
            # source-tree integration can exercise the real edge contract.
            return _error("pet_care_session_environment_mismatch", 409)
    target = f"{base_url}/api/pet-care{suffix}"
    if query:
        target = f"{target}?{query}"
    upstream_correlation_id = ""
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(15.0, connect=5.0),
            follow_redirects=False,
            proxy=None,
            trust_env=False,
        ) as client:
            async with client.stream(
                request.method,
                target,
                content=raw or None,
                headers=headers,
            ) as upstream:
                media_type = (
                    upstream.headers.get("content-type", "")
                    .split(";", 1)[0]
                    .strip()
                    .lower()
                )
                if media_type not in {"application/json", "application/problem+json"}:
                    return _error("pet_care_upstream_content_type_invalid", 502)
                try:
                    content = await _read_bounded_response(upstream)
                except ValueError:
                    return _error("pet_care_upstream_response_too_large", 502)
                status_code = upstream.status_code
                candidate_correlation_id = upstream.headers.get("X-Correlation-Id", "")
                try:
                    parsed_correlation_id = str(uuid.UUID(candidate_correlation_id))
                except (AttributeError, TypeError, ValueError):
                    parsed_correlation_id = ""
                if candidate_correlation_id == parsed_correlation_id:
                    upstream_correlation_id = parsed_correlation_id
    except httpx.HTTPError:
        return _error("pet_care_service_unavailable", 503)
    response_headers = {"Cache-Control": "no-store", "Pragma": "no-cache"}
    if upstream_correlation_id:
        response_headers["X-Correlation-Id"] = upstream_correlation_id
    return Response(
        content=content,
        status_code=status_code,
        media_type=media_type,
        headers=response_headers,
    )


__all__ = ["router"]
