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

"""Shared infrastructure for the system_router package: the APIRouter
instance, logger, request-origin / CSRF / loopback validation helpers and
path-containment utilities used by several route domains.

Split out of the former monolithic ``main_routers/system_router.py``.
"""

import os
import sys
import ipaddress
import secrets
from typing import Any
from urllib.parse import urlparse, urlsplit
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from ..shared_state import get_config_manager
from main_logic.activity.system_signals import is_remote_backend_deployment
from config import (
    AUTOSTART_ALLOWED_ORIGINS,
    AUTOSTART_CSRF_TOKEN,
)
from utils.logger_config import get_module_logger
from utils.config_manager import get_config_manager as get_runtime_config_manager
from config import APP_NAME


router = APIRouter(prefix="/api", tags=["system"])


logger = get_module_logger(__name__, "Main")


_AUTOSTART_CSRF_HEADER = "X-CSRF-Token"


def _set_no_store_headers(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"


def _is_loopback_request(request: Request) -> bool:
    client_host = request.client.host if request.client else ""
    if client_host == "localhost":
        return True
    normalized_host = str(client_host or "").removeprefix("::ffff:")
    try:
        return ipaddress.ip_address(normalized_host).is_loopback
    except ValueError:
        return False


# /screenshot 和 /screenshot/interactive 都是在后端机器上抓屏的，部署到
# 远程服务器时抓出来的是服务器自己的桌面而不是用户的。loopback 校验
# 会被反向代理 / 隧道绕过，``NEKO_ACTIVITY_TRACKER_REMOTE`` 是运维显式
# 声明"后端不在用户本机"的硬开关，命中就直接拒绝本地截图。
#
# 真正的实现在 ``main_logic/activity/system_signals.is_remote_backend_deployment``
# —— PR #1015 给 activity tracker 用的，这里直接复用避免再发明一套部署变量。
# 私有别名保留是为了 ``tests/unit/test_system_screenshot_router.py`` 还
# 在调 ``system_router_module._is_remote_backend_deployment()``。
_is_remote_backend_deployment = is_remote_backend_deployment


def _json_no_store_response(content: dict, status_code: int = 200) -> JSONResponse:
    response = JSONResponse(content, status_code=status_code)
    _set_no_store_headers(response)
    return response


def _build_public_error_response(
    *,
    error_code: str,
    status_code: int,
    result: dict | None = None,
    defaults: dict | None = None,
):
    public_messages = {
        "status_failed": "Failed to read autostart status",
        "enable_failed": "Failed to enable autostart",
        "disable_failed": "Failed to disable autostart",
        "unsupported_platform": "Autostart is not supported on this platform",
        "launch_command_unavailable": "Autostart launch command is unavailable",
        "csrf_validation_failed": "Request could not be verified",
    }

    content = {}
    if defaults:
        content.update(defaults)
    if result:
        content.update(result)

    content["ok"] = False
    content["error_code"] = error_code
    content["error"] = public_messages.get(error_code, "Operation failed")
    return JSONResponse(status_code=status_code, content=content)


def _normalize_origin_value(raw_value: str | None) -> str:
    if not raw_value:
        return ""

    try:
        parsed = urlsplit(raw_value.strip())
    except ValueError:
        return ""

    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""

    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}".rstrip("/")


def _get_request_origin(request: Request) -> str:
    origin = _normalize_origin_value(request.headers.get("origin"))
    if origin:
        return origin
    return _normalize_origin_value(request.headers.get("referer"))


def _get_system_config_manager():
    try:
        return get_config_manager()
    except RuntimeError:
        # The storage bootstrap sentinel must keep working during limited startup
        # even if main_server shared_state is not fully published yet.
        return get_runtime_config_manager(APP_NAME, migrate=False)


def _get_allowed_local_origins(request: Request) -> set[str]:
    allowed_origins = {
        normalized_origin
        for origin in AUTOSTART_ALLOWED_ORIGINS
        if isinstance(origin, str)
        if (normalized_origin := _normalize_origin_value(origin))
    }
    request_origin = _normalize_origin_value(str(request.base_url))
    if request_origin:
        allowed_origins.add(request_origin)
    return allowed_origins


def _validate_local_mutation_request(
    request: Request,
    *,
    payload: dict[str, Any] | None = None,
    error_defaults: dict[str, Any] | None = None,
) -> JSONResponse | None:
    csrf_token = request.headers.get(_AUTOSTART_CSRF_HEADER, "")
    if not csrf_token and payload:
        body_token = payload.get("_csrf_token")
        csrf_token = body_token if isinstance(body_token, str) else ""
    has_valid_csrf = bool(
        csrf_token
        and AUTOSTART_CSRF_TOKEN
        and secrets.compare_digest(csrf_token, AUTOSTART_CSRF_TOKEN)
    )
    request_origin = _get_request_origin(request)
    allowed_origins = _get_allowed_local_origins(request)
    has_valid_origin = bool(request_origin and request_origin in allowed_origins)

    if has_valid_csrf and has_valid_origin:
        return None

    # 端口无关的 hostname 降级匹配：Docker 端口映射下内部端口（如 48911）
    # 与浏览器 Origin 端口（如 1081）不一致，CSRF token 有效时放宽检查
    if has_valid_csrf and request_origin:
        parsed_origin = urlparse(request_origin)
        origin_host = parsed_origin.hostname
        if origin_host:
            for allowed in allowed_origins:
                parsed_allowed = urlparse(allowed)
                if parsed_allowed.hostname == origin_host:
                    return None

    logger.warning(
        "Rejected local mutation request due to failed CSRF/origin validation: "
        "method=%r path=%r origin=%r allowed_origins=%r has_csrf=%s referer=%r",
        request.method,
        request.url.path,
        request_origin,
        sorted(allowed_origins),
        has_valid_csrf,
        request.headers.get("referer"),
    )
    return _build_public_error_response(
        error_code="csrf_validation_failed",
        status_code=403,
        defaults=error_defaults,
    )


async def _read_json_object(request: Request) -> dict[str, object]:
    """Read a JSON request body and normalize non-object payloads to {}."""
    try:
        payload = await request.json()
    except Exception:
        return {}

    return payload if isinstance(payload, dict) else {}


def _is_path_within_base(base_dir: str, candidate_path: str) -> bool:
    """
    
    Safety check that candidate_path is inside base_dir.
    Must use os.path.commonpath to prevent path traversal attacks.
    Before calling, both paths (candidate_path and base_dir) must be converted to
    absolute paths and resolved via os.path.realpath (resolving symlinks and ./..
    relative segments).
    args:
    - base_dir: base directory (absolute path)
    - candidate_path: candidate path (absolute path)
    returns:
    - bool: True if candidate_path is inside base_dir, False otherwise
    """
    try:
        # Normalize both paths for case-insensitivity on Windows
        norm_base = os.path.normcase(os.path.realpath(base_dir))
        norm_candidate = os.path.normcase(os.path.realpath(candidate_path))
        
        # os.path.commonpath raises ValueError if paths are on different drives (Windows)
        common = os.path.commonpath([norm_base, norm_candidate])
        return common == norm_base
    except (ValueError, TypeError):
        # Different drives or invalid paths
        return False


def _get_app_root():
    """
    Get the application root directory, compatible with both dev environments and PyInstaller-packaged builds.
    """
    if getattr(sys, 'frozen', False):
        if hasattr(sys, '_MEIPASS'):
            return sys._MEIPASS
        else:
            return os.path.dirname(sys.executable)
    else:
        return os.getcwd()
