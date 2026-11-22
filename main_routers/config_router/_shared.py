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

from fastapi import APIRouter, Depends, HTTPException, Request

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


def _request_origin_allowed(request: Request) -> bool:
    if request.scope.get("neko.instance_identity"):
        # Authorized remote instance sessions: accept the instance's own and
        # pinned public origins, mirroring other cookie-authenticated routes.
        from utils.instance_access import _same_origin

        return _same_origin(request)
    # Same-origin, loopback-to-loopback, or a configured trusted origin;
    # requests without Origin/Referer (native clients) pass here.
    return is_http_browser_origin_allowed(request.scope)


async def require_same_origin_json_mutation(request: Request) -> None:
    """Reject cross-site or non-JSON state-changing requests to /api/config/*.

    Browsers cannot send ``application/json`` cross-origin without a CORS
    preflight, which this server never grants, so requiring it together with
    an Origin / Fetch Metadata check limits writes to the app's own pages and
    native local clients (which send neither Origin nor Sec-Fetch-Site).
    Electron windows with webSecurity disabled may omit Origin; their
    same-origin fetches still report ``Sec-Fetch-Site: same-origin``.
    """
    method = request.method.upper()
    if method in _SAFE_METHODS:
        return

    fetch_site = (request.headers.get("sec-fetch-site") or "").strip().lower()
    if fetch_site and fetch_site not in _ALLOWED_FETCH_SITES:
        _log_rejection(request, "cross_site_fetch")
        raise HTTPException(status_code=403, detail="cross_origin_request_rejected")
    if not _request_origin_allowed(request):
        _log_rejection(request, "origin_not_allowed")
        raise HTTPException(status_code=403, detail="cross_origin_request_rejected")
    if method in _BODY_METHODS and not _is_json_content_type(
        request.headers.get("content-type")
    ):
        _log_rejection(request, "unsupported_content_type")
        raise HTTPException(status_code=415, detail="content_type_must_be_application_json")


def _log_rejection(request: Request, reason: str) -> None:
    logger.warning(
        "Rejected config mutation: reason=%s method=%s path=%s origin=%r sec_fetch_site=%r content_type=%r",
        reason,
        request.method,
        request.url.path,
        request.headers.get("origin"),
        request.headers.get("sec-fetch-site"),
        request.headers.get("content-type"),
    )


router = APIRouter(
    prefix="/api/config",
    tags=["config"],
    dependencies=[Depends(require_same_origin_json_mutation)],
)
