# -*- coding: utf-8 -*-
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

"""Shared APIRouter instance and logger for the config_router package.

Split out of the former monolithic ``main_routers/config_router.py``.
"""

import secrets

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from utils.host_origin_guard import is_http_browser_origin_allowed
from utils.logger_config import get_module_logger


logger = get_module_logger(__name__, "Main")


_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_BODY_METHODS = frozenset({"POST", "PUT", "PATCH"})
# Fetch Metadata values a browser sends for a request this UI made itself
# ("none" is a user-initiated navigation). Native clients send no header.
_ALLOWED_FETCH_SITES = frozenset({"same-origin", "none"})


def _is_json_content_type(value: str | None) -> bool:
    media_type = (value or "").split(";", 1)[0].strip().lower()
    return media_type == "application/json"


def _csrf_header_accepted(request: Request) -> bool:
    """A valid X-CSRF-Token keeps the existing local-mutation origin rules.

    The token is only handed to this app's own pages and the desktop shell, so
    such a request keeps the wider allowance of ``_validate_local_mutation_request``
    (configured AUTOSTART_ALLOWED_ORIGINS, same hostname on another port for
    Docker port mapping) instead of being narrowed by this guard.
    """
    from main_routers.system_router import _shared as system_shared

    token = request.headers.get(system_shared._AUTOSTART_CSRF_HEADER, "")
    expected = system_shared.AUTOSTART_CSRF_TOKEN
    # compare_digest raises TypeError on non-ASCII str; such a token is invalid.
    if not (
        token
        and expected
        and token.isascii()
        and secrets.compare_digest(token, expected)
    ):
        return False
    # Without Origin/Referer the original check always fails; skip it so a
    # token holder's request does not leave a spurious rejection warning.
    if not system_shared._get_request_origin(request):
        return False
    return system_shared._validate_local_mutation_request(request) is None


def _request_origin_allowed(request: Request) -> bool:
    from main_routers.system_router._shared import _normalize_origin_value
    from utils.instance_access import _same_origin

    # Same-origin, loopback-to-loopback, or a configured trusted origin;
    # requests without Origin/Referer (native clients) pass here.
    if is_http_browser_origin_allowed(request.scope):
        return True
    # A page origin (Origin, else Referer: windows that omit Origin still send
    # it) naming this deployment's own entry, as the instance access layer
    # defines it: the pinned public origin, or an https page on this Host
    # behind a TLS-terminating proxy.
    # Referer is consulted only when Origin is absent; an Origin that is
    # present but opaque or malformed ("null") is never replaced by Referer.
    if "origin" in request.headers:
        page_origin = request.headers.get("origin", "").strip()
    else:
        page_origin = _normalize_origin_value(request.headers.get("referer"))
    return bool(page_origin) and _same_origin(request, page_origin)


def _rejection(status_code: int, error_code: str, error: str) -> JSONResponse:
    # Same shape as the config handlers' own failures, which the UI reads.
    return JSONResponse(
        status_code=status_code,
        content={"success": False, "error": error, "error_code": error_code},
    )


def check_config_mutation_request(request: Request) -> JSONResponse | None:
    """Reject cross-site or non-JSON state-changing requests to /api/config/*.

    Browsers cannot send ``application/json`` cross-origin without a CORS
    preflight, which this server never grants, so requiring it together with
    an Origin / Fetch Metadata check limits writes to the app's own pages and
    native local clients (which send neither Origin nor Sec-Fetch-Site).
    Electron windows with webSecurity disabled may omit Origin; their
    same-origin fetches still report ``Sec-Fetch-Site: same-origin``.
    Returns None when the request may proceed.
    """
    method = request.method.upper()
    if method in _SAFE_METHODS:
        return None

    fetch_site = (request.headers.get("sec-fetch-site") or "").strip().lower()
    if fetch_site and fetch_site not in _ALLOWED_FETCH_SITES:
        source_rejection = "cross_site_fetch"
    elif not _request_origin_allowed(request):
        source_rejection = "origin_not_allowed"
    else:
        source_rejection = None
    # The CSRF fallback is consulted only for requests the general rule rejects.
    if source_rejection is not None and not _csrf_header_accepted(request):
        _log_rejection(request, source_rejection)
        return _rejection(403, "cross_origin_request_rejected", "Request origin not allowed")
    if method in _BODY_METHODS and not _is_json_content_type(
        request.headers.get("content-type")
    ):
        _log_rejection(request, "unsupported_content_type")
        return _rejection(
            415, "unsupported_content_type", "Content-Type must be application/json"
        )
    return None


def _log_rejection(request: Request, reason: str) -> None:
    # Debug level: any page can trigger rejections, so they must not flood logs.
    logger.debug(
        "Rejected config mutation: reason=%s method=%s path=%s origin=%r sec_fetch_site=%r content_type=%r",
        reason,
        request.method,
        request.url.path,
        request.headers.get("origin"),
        request.headers.get("sec-fetch-site"),
        request.headers.get("content-type"),
    )


class _ConfigMutationGuardRoute(APIRoute):
    """Run ``check_config_mutation_request`` before body parsing and the handler."""

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def guarded_handler(request: Request):
            rejection = check_config_mutation_request(request)
            if rejection is not None:
                return rejection
            return await handler(request)

        return guarded_handler


router = APIRouter(
    prefix="/api/config",
    tags=["config"],
    route_class=_ConfigMutationGuardRoute,
)
