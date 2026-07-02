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

"""Shared APIRouter instance, logger, reserved-field set, profile name
validation and small request/response helpers for the characters_router
package.

Split out of the former monolithic ``main_routers/characters_router.py``.
"""

import io
from fastapi import APIRouter, Request, UploadFile
from fastapi.responses import JSONResponse
from utils.character_name import PROFILE_NAME_MAX_UNITS, validate_character_name
from utils.logger_config import get_module_logger
from config import (
    CHARACTER_RESERVED_FIELDS,
)


router = APIRouter(prefix="/api/characters", tags=["characters"])


logger = get_module_logger(__name__, "Main")


CHARACTER_RESERVED_FIELD_SET = set(CHARACTER_RESERVED_FIELDS)


def _json_no_store_response(content, *, status_code: int = 200):
    return JSONResponse(
        content=content,
        status_code=status_code,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
        },
    )


async def _read_json_object_or_400(request: Request):
    try:
        payload = await request.json()
    except Exception:
        return None, JSONResponse({'success': False, 'error': '请求体必须是合法的JSON格式'}, status_code=400)
    return payload if isinstance(payload, dict) else {}, None


def _profile_name_units(name: str) -> int:
    # 计数规则与前端保持一致：ASCII(<=0x7F) 计 1，其它字符计 2
    return sum(1 if ord(ch) <= 0x7F else 2 for ch in name)


def _validate_profile_name(name: str) -> str | None:
    result = validate_character_name(name, max_units=PROFILE_NAME_MAX_UNITS)
    if result.code == 'empty':
        return '档案名为必填项'
    if result.code in {'contains_path_separator', 'path_traversal'}:
        return '档案名不能包含路径分隔符(/或\\)'
    if result.code == 'unsafe_dot':
        return '档案名不能仅由点号组成或以点号结尾'
    if result.code == 'contains_dot':
        return '档案名不能包含点号(.)'
    if result.code == 'reserved_device_name':
        return '档案名不能使用 Windows 保留设备名'
    if result.code == 'reserved_route_name':
        return '此名称是系统保留的路由名称，不能用作档案名'
    if result.code == 'invalid_character':
        return '档案名只能包含文字、数字、空格、下划线、连字符、括号、间隔号(·/・)和撇号'
    if result.code == 'too_long_units':
        return f'档案名长度不能超过{PROFILE_NAME_MAX_UNITS}单位（ASCII=1，其他=2；PROFILE_NAME_MAX_UNITS={PROFILE_NAME_MAX_UNITS}）'
    if result.code:
        return '档案名无效'
    return None


def _is_safe_profile_name(name: str) -> bool:
    return _validate_profile_name(name) is None


def _validate_existing_character_path_name(name: str) -> str | None:
    result = validate_character_name(name, allow_dots=True, max_units=PROFILE_NAME_MAX_UNITS)
    if result.code == 'empty':
        return '角色名不能为空'
    if result.code in {'contains_path_separator', 'path_traversal'}:
        return '角色名不能包含路径分隔符(/或\\)'
    if result.code == 'unsafe_dot':
        return '角色名不能仅由点号组成或以点号结尾'
    if result.code == 'reserved_route_name':
        return None
    if result.code == 'reserved_device_name':
        return '角色名不能使用 Windows 保留设备名'
    if result.code == 'invalid_character':
        return '角色名只能包含文字、数字、空格、点号、下划线、连字符、括号、间隔号(·/・)和撇号'
    if result.code == 'too_long_units':
        return f'角色名长度不能超过{PROFILE_NAME_MAX_UNITS}单位（ASCII=1，其他=2；PROFILE_NAME_MAX_UNITS={PROFILE_NAME_MAX_UNITS}）'
    if result.code:
        return '角色名无效'
    return None


def _profile_name_contains_path_separator(name: str) -> bool:
    return validate_character_name(
        str(name or "").strip(),
        max_units=PROFILE_NAME_MAX_UNITS,
    ).code == 'contains_path_separator'


MAX_UPLOAD_SIZE = 100 * 1024 * 1024  # 100 MB


MAX_CARD_FACE_SIZE = 10 * 1024 * 1024  # 10 MB


class _UploadTooLargeError(Exception):
    """Uploaded file size exceeds the limit."""


async def _read_limited_stream(stream: UploadFile, max_size: int) -> io.BytesIO:
    """Read an uploaded file with a size-limit check, returning BytesIO (positioned at 0).

    Raises:
        _UploadTooLargeError: file size exceeds max_size.
    """
    buf = io.BytesIO()
    total = 0
    while True:
        chunk = await stream.read(8192)
        if not chunk:
            break
        total += len(chunk)
        if total > max_size:
            raise _UploadTooLargeError(
                f'文件大小超过限制 ({max_size // (1024 * 1024)} MB)'
            )
        buf.write(chunk)
    buf.seek(0)
    return buf
