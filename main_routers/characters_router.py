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

"""
Characters Router

Handles character (catgirl) management endpoints including:
- Character CRUD operations
- Voice settings
- Microphone settings

URL convention
--------------
Every endpoint here is declared WITHOUT a trailing slash (e.g.
``@router.get('')`` not ``@router.get('/')``, ``@router.get('/voices')`` not
``@router.get('/voices/')``). Frontend callers must match exactly — never
``fetch('/api/characters/')``. Triggering Starlette's slash-redirect 307
returns an absolute ``Location`` built from the request ``Host`` and breaks
under reverse proxies that don't transparently forward ``Host``. See
``.agent/rules/neko-guide.md`` (§"API URL 末尾不带斜杠") for the full
rationale and the PR #938 incident; ``scripts/check_api_trailing_slash.py``
+ ``scripts/check_frontend_api_trailing_slash.py`` enforce this in CI.
"""
import re
import json
import io
import os
import shutil
import asyncio
import copy
import base64
import hashlib
import math
import struct
import tempfile
import wave
import zlib
import socket
import inspect
from dataclasses import dataclass
from urllib.parse import urlparse, urljoin
from datetime import datetime, timezone
from pathlib import Path
from fastapi import APIRouter, Request, File, UploadFile, Form
from fastapi.responses import JSONResponse, Response
import aiohttp
import httpx
import websockets
# dashscope 仅用于声音克隆 TTS 预览（_do_preview_synthesize），import 偏重
# （~0.1s）且不在 greeting/启动链上，改成用到时再 import；由 module_warmup 预热。

from config.prompts.prompts_sys import _loc
from config.prompts.prompts_voice import VOICE_PREVIEW_TEXTS
from .shared_state import (
    get_config_manager,
    get_session_manager,
    get_initialize_character_data,
    get_switch_current_catgirl_fast,
    get_init_one_catgirl,
    get_remove_one_catgirl,
)
from .workshop_router import _ugc_sync_lock
from main_logic.tts_client import (
    get_custom_tts_voices,
    CustomTTSVoiceFetchError,
)
from utils.elevenlabs_tts_voices import (
    ELEVENLABS_TTS_DEFAULT_MODEL,
    ELEVENLABS_TTS_VOICE_PREFIX,
)
from .agent_router import force_disable_agent_for_character_switch
from utils.character_memory import (
    delete_character_memory_storage,
    list_character_memory_paths,
    rename_character_memory_storage,
)
from utils.config_manager import (
    delete_reserved,
    ensure_default_yui_voice_for_free_api,
    flatten_reserved,
    get_reserved,
    set_reserved,
    strip_generated_persona_selection_prompt,
)
from utils.dashscope_region import DASHSCOPE_GLOBAL_LOCK, configure_dashscope_sdk_urls
from utils.voice_config import read_legacy_voice_id
from utils.native_voice_registry import (
    get_active_realtime_native_provider_for_ui,
    get_native_voice_catalog_for_ui,
    normalize_native_voice,
    resolve_native_voice_for_routing,
)
from utils import tts_provider_registry
from utils.audio import normalize_voice_clone_api_audio, validate_audio_file
from utils.character_name import PROFILE_NAME_MAX_UNITS, validate_character_name
from utils.doubao_tts import (
    DOUBAO_TTS_DEFAULT_BASE_URL,
    DOUBAO_TTS_DEFAULT_CONTEXT_TEXTS,
    DOUBAO_TTS_DEFAULT_RESOURCE_ID,
    DOUBAO_VOICE_CLONE_RESOURCE_ID,
    DOUBAO_VOICE_STORAGE_KEY,
    DoubaoTtsError,
    DoubaoVoiceCloneClient,
    build_doubao_tts_payload,
    doubao_api_headers,
    doubao_tts_url,
    extract_doubao_audio_bytes,
)
from utils.initial_personality_state import (
    clear_manual_personality_reselect,
    load_initial_personality_state,
    mark_manual_personality_reselect,
    mark_initial_personality_state,
)
from utils.voice_clone import (
    MinimaxVoiceCloneClient,
    MinimaxVoiceCloneError,
    minimax_normalize_language,
    sanitize_minimax_voice_prefix,
    MINIMAX_VOICE_STORAGE_KEY,
    MINIMAX_INTL_VOICE_STORAGE_KEY,
    MINIMAX_PREFIX_MAX_LENGTH,
    get_minimax_base_url,
    get_minimax_storage_prefix,
    MimoVoiceCloneClient,
    MimoVoiceCloneError,
    MIMO_VOICE_STORAGE_KEY,
    QwenVoiceCloneClient,
    QwenVoiceCloneError,
    qwen_language_hints,
)
from utils.file_utils import atomic_write_json_async, read_json_async
from utils.frontend_utils import find_models, find_model_directory, is_user_imported_model
from utils.language_utils import is_supported_language_code, normalize_language_code
from utils.logger_config import get_module_logger
from utils.new_character_greeting_state import (
    mark_pending as mark_new_character_greeting_pending,
    remove_pending as remove_new_character_greeting_pending,
    rename_pending as rename_new_character_greeting_pending,
)
from utils.persona_presets import (
    build_persona_override_payload,
    get_persona_preset,
    list_persona_presets,
)
from utils.url_utils import encode_url_path
from utils.cloudsave_runtime import MaintenanceModeError, assert_cloudsave_writable, is_cloudsave_disabled
from config import (
    MEMORY_SERVER_PORT,
    TFLINK_UPLOAD_URL,
    CHARACTER_RESERVED_FIELDS,
    DEFAULT_LIVE2D_MODEL_NAME,
)

router = APIRouter(prefix="/api/characters", tags=["characters"])
logger = get_module_logger(__name__, "Main")


CHARACTER_RESERVED_FIELD_SET = set(CHARACTER_RESERVED_FIELDS)
VOICE_SESSION_STARTING_ERROR = "语音会话正在启动，请稍后再切换音色"
DEFAULT_NEW_CATGIRL_FREE_VOICE_ID = "voice-tone-PGLiyZt65w"
_DIRECT_LINK_MAX_REDIRECTS = 10
_DIRECT_LINK_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_PNGTUBER_CARD_MODEL_DIR = "pngtuber"
_PNGTUBER_IMAGE_KEYS = (
    "idle_image",
    "talking_image",
    "drag_image",
    "click_image",
    "happy_image",
    "sad_image",
    "angry_image",
    "surprised_image",
)
_PNGTUBER_PACKABLE_KEYS = (*_PNGTUBER_IMAGE_KEYS, "layered_metadata", "metadata")


def _strip_url_suffix(path: str) -> str:
    return str(path or "").split("?", 1)[0].split("#", 1)[0]


def _pngtuber_user_rel_from_url(value: str) -> str:
    normalized = _strip_url_suffix(str(value or "").strip().replace("\\", "/"))
    prefix = "/user_pngtuber/"
    if not normalized.startswith(prefix):
        return ""
    rel = normalized[len(prefix):]
    rel_path = Path(rel)
    if rel_path.is_absolute() or ".." in rel_path.parts:
        return ""
    return rel


def _collect_pngtuber_user_asset_refs(pngtuber_config: dict) -> dict[str, str]:
    refs: dict[str, str] = {}
    if not isinstance(pngtuber_config, dict):
        return refs
    for key in _PNGTUBER_PACKABLE_KEYS:
        rel = _pngtuber_user_rel_from_url(str(pngtuber_config.get(key) or ""))
        if rel:
            refs[key] = rel
    return refs


def _pngtuber_package_roots_from_refs(refs: dict[str, str]) -> list[str]:
    roots: list[str] = []
    seen: set[str] = set()
    for rel in refs.values():
        parts = Path(rel).parts
        if not parts:
            continue
        root = parts[0]
        if root in ("", ".", "..") or root in seen:
            continue
        roots.append(root)
        seen.add(root)
    return roots


def _with_pngtuber_model_path_rewrites(data, rewrites: dict[str, str]):
    if not rewrites:
        return data
    if isinstance(data, dict):
        return {
            key: _with_pngtuber_model_path_rewrites(value, rewrites)
            for key, value in data.items()
        }
    if isinstance(data, list):
        return [_with_pngtuber_model_path_rewrites(item, rewrites) for item in data]
    if isinstance(data, str):
        rel = _pngtuber_user_rel_from_url(data)
        if rel in rewrites:
            suffix = ""
            for marker in ("?", "#"):
                index = data.find(marker)
                if index >= 0:
                    suffix = data[index:]
                    break
            return rewrites[rel] + suffix
    return data


def _add_pngtuber_assets_to_character_zip(zf, catgirl_data: dict, config_manager) -> bool:
    pngtuber_config = get_reserved(catgirl_data, "avatar", "pngtuber", default={})
    refs = _collect_pngtuber_user_asset_refs(pngtuber_config)
    if not refs:
        return False
    added = False
    added_arcs: set[str] = set()
    for root_name in _pngtuber_package_roots_from_refs(refs):
        source_root = config_manager.pngtuber_dir / root_name
        if source_root.is_file():
            rel = source_root.relative_to(config_manager.pngtuber_dir).as_posix()
            arc_name = f"model/{_PNGTUBER_CARD_MODEL_DIR}/{rel}"
            if arc_name not in added_arcs:
                zf.write(source_root, arc_name)
                added_arcs.add(arc_name)
                added = True
            continue

        if not source_root.is_dir():
            logger.warning(f"PNGTuber export asset missing, skipping: {source_root}")
            continue

        for file_path in sorted(source_root.rglob("*")):
            if not file_path.is_file():
                continue
            rel = file_path.relative_to(config_manager.pngtuber_dir).as_posix()
            arc_name = f"model/{_PNGTUBER_CARD_MODEL_DIR}/{rel}"
            if arc_name in added_arcs:
                continue
            zf.write(file_path, arc_name)
            added_arcs.add(arc_name)
            added = True
    return added


def _rewrite_imported_pngtuber_refs(character_data: dict, rel_map: dict[str, str]) -> dict:
    rewrites = {
        rel: f"/user_pngtuber/{new_rel}"
        for rel, new_rel in rel_map.items()
    }
    return _with_pngtuber_model_path_rewrites(character_data, rewrites)


def _restore_imported_pngtuber_avatar_config(character_data: dict, source_data: dict, rel_map: dict[str, str]) -> dict:
    if not isinstance(character_data, dict) or not isinstance(source_data, dict):
        return character_data

    model_type = get_reserved(
        source_data,
        "avatar",
        "model_type",
        default="",
        legacy_keys=("model_type",),
    )
    pngtuber_config = get_reserved(source_data, "avatar", "pngtuber", default={})
    if model_type != "pngtuber" or not isinstance(pngtuber_config, dict):
        return character_data

    restored = {"_reserved": {"avatar": {"pngtuber": copy.deepcopy(pngtuber_config)}}}
    if rel_map:
        restored = _rewrite_imported_pngtuber_refs(restored, rel_map)

    avatar = character_data.setdefault("_reserved", {}).setdefault("avatar", {})
    avatar["model_type"] = "pngtuber"
    avatar["live3d_sub_type"] = ""
    avatar["pngtuber"] = restored["_reserved"]["avatar"]["pngtuber"]
    avatar["asset_source"] = "local_imported"
    avatar["asset_source_id"] = ""
    return character_data


def _copy_imported_pngtuber_assets(model_dir: Path, config_manager) -> dict[str, str]:
    pngtuber_model_dir = model_dir / _PNGTUBER_CARD_MODEL_DIR
    if not pngtuber_model_dir.exists() or not pngtuber_model_dir.is_dir():
        return {}

    config_manager.pngtuber_dir.mkdir(parents=True, exist_ok=True)
    rel_map: dict[str, str] = {}

    for item in pngtuber_model_dir.iterdir():
        if item.name in ("", ".", ".."):
            continue
        target_name = item.name
        target_path = config_manager.pngtuber_dir / target_name
        if target_path.exists():
            counter = 1
            stem = item.stem
            suffix = item.suffix
            while target_path.exists():
                target_name = f"{stem}({counter}){suffix}" if item.is_file() else f"{item.name}({counter})"
                target_path = config_manager.pngtuber_dir / target_name
                counter += 1

        if item.is_dir():
            shutil.copytree(item, target_path)
            for copied in item.rglob("*"):
                if copied.is_file():
                    old_rel = str(copied.relative_to(pngtuber_model_dir)).replace("\\", "/")
                    new_rel = str((Path(target_name) / copied.relative_to(item)).as_posix())
                    rel_map[old_rel] = new_rel
        elif item.is_file():
            shutil.copy2(item, target_path)
            rel_map[item.name] = target_name

    return rel_map


class DirectLinkSecurityError(Exception):
    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


class ElevenLabsUpstreamError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class DirectLinkValidatedTarget:
    url: str
    hostname: str
    port: int
    addr_info: list


class _DirectLinkPinnedResolver(aiohttp.abc.AbstractResolver):
    def __init__(self, target: DirectLinkValidatedTarget):
        self._hostname = target.hostname.casefold()
        self._addr_info = list(target.addr_info)

    async def resolve(self, host, port=0, family=socket.AF_INET):
        if (host or "").casefold() != self._hostname:
            raise OSError(f"unexpected direct_link DNS host: {host}")

        records = []
        for addr_family, socktype, proto, _canonname, sockaddr in self._addr_info:
            ip = sockaddr[0]
            resolved_port = sockaddr[1] if len(sockaddr) > 1 else port
            records.append({
                "hostname": host,
                "host": ip,
                "port": resolved_port,
                "family": addr_family,
                "proto": proto,
                "flags": 0,
            })
        return records

    async def close(self):
        return None


class _DirectLinkProbeResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code

    async def aclose(self) -> None:
        return None


def _direct_link_hostname(target_url: str) -> str:
    parsed_url = urlparse(target_url)
    if parsed_url.scheme not in ("http", "https"):
        raise DirectLinkSecurityError("direct_link 必须是有效的HTTP/HTTPS链接", "INVALID_DIRECT_LINK")

    hostname = parsed_url.hostname
    if not hostname:
        raise DirectLinkSecurityError("direct_link 缺少主机名", "INVALID_DIRECT_LINK")
    if hostname.lower() == "localhost":
        raise DirectLinkSecurityError("direct_link 不能指向 localhost", "PRIVATE_IP_NOT_ALLOWED")
    return hostname


def _direct_link_port(target_url: str) -> int:
    parsed_url = urlparse(target_url)
    try:
        explicit_port = parsed_url.port
    except ValueError as exc:
        raise DirectLinkSecurityError("direct_link 端口无效", "INVALID_DIRECT_LINK") from exc
    if explicit_port is not None:
        return explicit_port
    return 443 if parsed_url.scheme == "https" else 80


def _assert_direct_link_addresses_safe(addr_info) -> None:
    import ipaddress

    for _, _, _, _, sockaddr in addr_info:
        ip = sockaddr[0]
        try:
            ip_obj = ipaddress.ip_address(ip)
        except ValueError:
            continue
        if (
            ip_obj.is_loopback
            or ip_obj.is_private
            or ip_obj.is_link_local
            or ip_obj.is_multicast
            or ip_obj.is_unspecified
            or ip_obj.is_reserved
        ):
            raise DirectLinkSecurityError("direct_link 指向受限地址，已拒绝", "PRIVATE_IP_NOT_ALLOWED")


async def _validate_direct_link_target(target_url: str) -> DirectLinkValidatedTarget:
    hostname = _direct_link_hostname(target_url)
    port = _direct_link_port(target_url)

    try:
        loop = asyncio.get_running_loop()
        addr_info = await loop.getaddrinfo(
            hostname,
            port,
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as exc:
        raise DirectLinkSecurityError(
            f"direct_link 主机无法解析: {hostname}",
            "DIRECT_LINK_DNS_FAILED",
        ) from exc

    _assert_direct_link_addresses_safe(addr_info)
    return DirectLinkValidatedTarget(
        url=target_url,
        hostname=hostname,
        port=port,
        addr_info=addr_info,
    )


async def _redirect_target_from_response(response) -> DirectLinkValidatedTarget:
    location = response.headers.get("location")
    if not location:
        raise DirectLinkSecurityError("直链重定向响应缺少 Location 头", "DIRECT_LINK_REDIRECT_INVALID")

    next_url = urljoin(str(response.url), location)
    return await _validate_direct_link_target(next_url)


def _open_pinned_direct_link_session(target: DirectLinkValidatedTarget, *, timeout: float):
    resolver = _DirectLinkPinnedResolver(target)
    connector = aiohttp.TCPConnector(
        resolver=resolver,
        use_dns_cache=False,
        ttl_dns_cache=0,
    )
    return aiohttp.ClientSession(
        connector=connector,
        connector_owner=True,
        timeout=aiohttp.ClientTimeout(total=timeout),
        trust_env=False,
    )


async def _request_direct_link_follow_redirects(
    method: str,
    direct_link: str,
    *,
    stream: bool = False,
    headers: dict[str, str] | None = None,
) -> _DirectLinkProbeResponse:
    target = await _validate_direct_link_target(direct_link)
    for _ in range(_DIRECT_LINK_MAX_REDIRECTS + 1):
        async with _open_pinned_direct_link_session(target, timeout=30) as session:
            async with session.request(
                method,
                target.url,
                headers=headers,
                allow_redirects=False,
            ) as response:
                status_code = response.status
                if stream:
                    response.release()
                else:
                    await response.read()
                if status_code in _DIRECT_LINK_REDIRECT_STATUSES:
                    target = await _redirect_target_from_response(response)
                    continue
                return _DirectLinkProbeResponse(status_code)
    raise DirectLinkSecurityError("直链重定向次数过多", "TOO_MANY_REDIRECTS")


async def _download_direct_link_audio(
    direct_link: str,
    *,
    max_file_size: int,
) -> tuple[str, bytes]:
    target = await _validate_direct_link_target(direct_link)
    for _ in range(_DIRECT_LINK_MAX_REDIRECTS + 1):
        async with _open_pinned_direct_link_session(target, timeout=60) as session:
            async with session.get(target.url, allow_redirects=False) as download_resp:
                if download_resp.status in _DIRECT_LINK_REDIRECT_STATUSES:
                    target = await _redirect_target_from_response(download_resp)
                    download_resp.release()
                    continue

                if download_resp.status != 200:
                    raise DirectLinkSecurityError(
                        f"直链下载失败，状态码: {download_resp.status}",
                        "DOWNLOAD_FAILED",
                    )

                filename = "audio.wav"
                content_disposition = download_resp.headers.get("content-disposition", "")
                if "filename=" in content_disposition:
                    match = re.search(r'filename=["\']?([^"\';]+)', content_disposition)
                    if match:
                        filename = match.group(1)
                else:
                    parsed = urlparse(str(download_resp.url))
                    path_filename = parsed.path.split("/")[-1]
                    if path_filename and "." in path_filename:
                        filename = path_filename

                audio_buffer = io.BytesIO()
                total_size = 0
                async for chunk in download_resp.content.iter_chunked(8192):
                    total_size += len(chunk)
                    if total_size > max_file_size:
                        raise DirectLinkSecurityError("音频文件超过100MB限制", "FILE_TOO_LARGE")
                    audio_buffer.write(chunk)

                return filename, audio_buffer.getvalue()

    raise DirectLinkSecurityError("直链重定向次数过多", "TOO_MANY_REDIRECTS")


def _voice_session_starting_response():
    return JSONResponse(
        {
            "success": False,
            "code": "VOICE_SESSION_STARTING",
            "error": VOICE_SESSION_STARTING_ERROR,
            "retryable": True,
        },
        status_code=409,
    )


def _is_current_catgirl_voice_session_starting(name: str, characters, session_manager) -> bool:
    if name != characters.get("当前猫娘", ""):
        return False
    mgr = session_manager.get(name) if session_manager else None
    if not mgr:
        return False
    return bool(
        getattr(mgr, "is_starting", False)
        and not getattr(mgr, "is_active", False)
        and (getattr(mgr, "starting_input_mode", None) or getattr(mgr, "input_mode", "")) == "audio"
    )


def _get_new_catgirl_default_voice_id() -> str:
    """Get the default voice for a newly created character, tolerating legacy/custom configs missing free_voices."""
    from utils.api_config_loader import get_free_voices

    free_voices = get_free_voices() or {}
    return (
        free_voices.get('cuteGirl')
        or next((voice_id for voice_id in free_voices.values() if voice_id), '')
        or DEFAULT_NEW_CATGIRL_FREE_VOICE_ID
    )


async def _mark_new_character_greeting_pending_safe(config_manager, character_name: str, source: str) -> tuple[bool, str]:
    try:
        await mark_new_character_greeting_pending(config_manager, character_name, source=source)
        return True, ""
    except Exception as exc:
        logger.exception("mark new character greeting pending failed: %s", character_name)
        return False, str(exc)


def _build_profile_rename_event(old_name: str, new_name: str) -> dict:
    old_name = str(old_name or "").strip()
    new_name = str(new_name or "").strip()
    return {
        "type": "profile_rename",
        "old_name": old_name,
        "new_name": new_name,
        "renamed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def _append_profile_rename_event(character_payload: dict, old_name: str, new_name: str) -> None:
    """Write the rename event into the hidden AI context; the character manager page does not render `_reserved` as a regular field."""
    if not isinstance(character_payload, dict):
        return

    old_name = str(old_name or "").strip()
    new_name = str(new_name or "").strip()
    if old_name == new_name:
        return

    existing = get_reserved(
        character_payload,
        "ai_context",
        "rename_events",
        default=[],
    )
    events = [event for event in existing if isinstance(event, dict)] if isinstance(existing, list) else []
    new_event = _build_profile_rename_event(old_name, new_name)

    # 防止同一次请求重放时连续写入完全相同的改名事件。
    if events:
        last = events[-1]
        if (
            last.get("type") == new_event["type"]
            and str(last.get("old_name") or "") == new_event["old_name"]
            and str(last.get("new_name") or "") == new_event["new_name"]
        ):
            return

    events.append(new_event)
    set_reserved(character_payload, "ai_context", "rename_events", events[-20:])


def _json_no_store_response(content, *, status_code: int = 200):
    return JSONResponse(
        content=content,
        status_code=status_code,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
        },
    )


def _build_persona_selection_payload(character_payload: dict) -> dict:
    override = get_reserved(character_payload, "persona_override", default=None)
    if not isinstance(override, dict):
        return {
            "mode": "default",
            "preset_id": "",
            "source": "",
            "selected_at": "",
            "profile": {},
        }

    profile = override.get("profile")
    return {
        "mode": "override",
        "preset_id": str(override.get("preset_id") or "").strip(),
        "source": str(override.get("source") or "").strip(),
        "selected_at": str(override.get("selected_at") or "").strip(),
        "profile": dict(profile) if isinstance(profile, dict) else {},
    }


def _normalize_persona_request_language(raw_language: object) -> str | None:
    """Normalize the UI language carried by a persona selection request; invalid values stay None so downstream keeps its existing fallback."""
    raw = str(raw_language or "").strip()
    if not raw or not is_supported_language_code(raw):
        return None
    return normalize_language_code(raw, format="full")


def _get_persona_request_language(request: Request) -> str | None:
    """Extract the persona preset language from query params or Accept-Language."""
    language = request.query_params.get("language") or request.query_params.get("i18n_language")
    if language:
        normalized = _normalize_persona_request_language(language)
        if normalized is not None:
            return normalized
    accept_lang = request.headers.get("Accept-Language", "")
    if accept_lang:
        for language_part in accept_lang.split(","):
            normalized = _normalize_persona_request_language(language_part.split(";")[0].strip())
            if normalized is not None:
                return normalized
    return None


def _get_persona_payload_request_language(payload: object, request: Request) -> str | None:
    """Prefer the request-body language; fall back to query params and headers when invalid or missing."""
    body_language = None
    if isinstance(payload, dict):
        body_language = payload.get("i18n_language") or payload.get("language")
    if body_language:
        normalized = _normalize_persona_request_language(body_language)
        if normalized is not None:
            return normalized
    return _get_persona_request_language(request)


def _normalize_voice_preview_language(raw_language: object) -> str | None:
    """Normalize the voice preview language; returns None for invalid values so other sources can be tried."""
    raw = str(raw_language or "").strip()
    if not raw or not is_supported_language_code(raw):
        return None
    normalized = normalize_language_code(raw, format="full")
    if normalized in VOICE_PREVIEW_TEXTS:
        return normalized
    return None


def _get_voice_preview_language(request: Request, language: object = None, i18n_language: object = None) -> str:
    """Pick the preview text by the frontend i18n language; defaults to the legacy Chinese preview."""
    for candidate in (language, i18n_language):
        normalized = _normalize_voice_preview_language(candidate)
        if normalized:
            return normalized

    accept_lang = request.headers.get("Accept-Language", "")
    if accept_lang:
        for language_part in accept_lang.split(","):
            normalized = _normalize_voice_preview_language(language_part.split(";")[0].strip())
            if normalized:
                return normalized

    return "zh-CN"


def _is_free_preset_voice_id(voice_id: object) -> bool:
    """Check whether this is a runtime free preset voice."""
    normalized = str(voice_id or "").strip()
    if not normalized:
        return False
    try:
        from utils.api_config_loader import get_free_voices
        free_voice_ids = set((get_free_voices() or {}).values())
    except Exception:
        return False
    return normalized in free_voice_ids


def _get_active_native_preview_provider(config_manager, voice_id: object) -> str | None:
    """Decide whether the voice_id should take the current realtime provider's native preview path."""
    normalized = str(voice_id or "").strip()
    if not normalized:
        return None
    active_provider = get_active_realtime_native_provider_for_ui(config_manager)
    if not active_provider:
        return None
    _, uses_provider_native_voice = resolve_native_voice_for_routing(
        active_provider,
        normalized,
        config_manager.voice_id_exists_in_any_storage,
    )
    if uses_provider_native_voice:
        return active_provider
    return None


def _is_unpreviewable_selected_preset_voice(config_manager, core_config, voice_id, voice_data) -> bool:
    """Whether voice_id is a built-in preset of the currently selected hosted/local
    provider (e.g. MiMo) that has no dedicated preview path yet — and is NOT a user clone.

    A cloned voice whose id collides with a preset name must still preview through its
    clone path: runtime dispatch selects clone providers (priority 30/40/50) ahead of a
    static-catalog provider like MiMo (60), so the clone wins. Any id present in a voice
    storage bucket is therefore never treated as an unpreviewable preset (dual to the
    native-preview collision guard, which passes voice_id_exists_in_any_storage)."""
    if voice_data:
        return False
    try:
        if config_manager.voice_id_exists_in_any_storage(voice_id):
            return False
    except Exception:
        # 存储桶查询异常（极少见的 IO 错误）：按「无法确认是克隆」继续走下方预制判定，
        # 不因一次查询失败改变结论；留一条带堆栈的 debug 便于排查（同 _grok 撞名查模式）。
        logger.debug("voice_id_exists_in_any_storage 查询失败，按非克隆继续判定", exc_info=True)
    return tts_provider_registry.is_selected_preset_voice(core_config or {}, config_manager, voice_id)


def _read_wav_payload(audio_bytes: bytes) -> tuple[bytes, int, int, int]:
    """Read the WAV returned by upstream; returns PCM plus channel count, sample width and sample rate."""
    with io.BytesIO(audio_bytes) as wav_io:
        with wave.open(wav_io, "rb") as wav_file:
            pcm_data = wav_file.readframes(wav_file.getnframes())
            return (
                pcm_data,
                wav_file.getnchannels(),
                wav_file.getsampwidth(),
                wav_file.getframerate(),
            )


def _build_wav_payload(pcm_chunks: list[bytes], channels: int, sample_width: int, sample_rate: int) -> bytes:
    """Wrap multiple PCM chunks into a single WAV playable directly by the frontend Audio element."""
    out = io.BytesIO()
    with wave.open(out, "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(sample_width)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(b"".join(pcm_chunks))
    return out.getvalue()


async def _synthesize_step_voice_preview(
    voice_id: str,
    preview_line: str,
    preview_language: str,
    audio_api_key: str = "",
    *,
    free_mode: bool = False,
) -> bytes:
    """Generate a preview WAV using the StepFun/free TTS WebSocket."""
    import websockets

    from main_logic.tts_client import _adjust_free_tts_url, _build_step_tts_create_data

    tts_url = (
        _adjust_free_tts_url("wss://www.lanlan.tech/tts")
        if free_mode
        else "wss://api.stepfun.com/v1/realtime/audio?model=step-tts-2"
    )
    headers = {"Authorization": f"Bearer {audio_api_key or ''}"}
    lang_hint = "ja" if preview_language == "ja" else None
    is_lanlan_app = "lanlan.app" in tts_url
    session_id = ""
    pcm_chunks: list[bytes] = []
    wav_meta: tuple[int, int, int] | None = None

    async with asyncio.timeout(20):
        async with websockets.connect(tts_url, additional_headers=headers) as ws:
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                if isinstance(raw, bytes):
                    continue
                event = json.loads(raw)
                event_type = event.get("type")
                if event_type == "tts.connection.done":
                    session_id = event.get("data", {}).get("session_id") or ""
                    break
                if event_type == "tts.response.error":
                    raise RuntimeError(str(event.get("data") or event))

            if not session_id:
                raise RuntimeError("TTS 连接未返回 session_id")

            create_data = _build_step_tts_create_data(session_id, voice_id, lang_hint, is_lanlan_app)

            await ws.send(json.dumps({"type": "tts.create", "data": create_data}))
            await ws.send(json.dumps({
                "type": "tts.text.delta",
                "data": {"session_id": session_id, "text": preview_line},
            }))
            await ws.send(json.dumps({
                "type": "tts.text.done",
                "data": {"session_id": session_id},
            }))

            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=12.0)
                if isinstance(raw, bytes):
                    continue
                event = json.loads(raw)
                event_type = event.get("type")
                if event_type == "tts.response.error":
                    raise RuntimeError(str(event.get("data") or event))
                if event_type == "tts.response.audio.delta":
                    audio_b64 = event.get("data", {}).get("audio", "")
                    if audio_b64:
                        pcm_data, channels, sample_width, sample_rate = _read_wav_payload(base64.b64decode(audio_b64))
                        pcm_chunks.append(pcm_data)
                        wav_meta = wav_meta or (channels, sample_width, sample_rate)
                elif event_type in ("tts.response.done", "tts.response.audio.done"):
                    break

    if not pcm_chunks or wav_meta is None:
        raise RuntimeError("TTS 未返回音频")

    channels, sample_width, sample_rate = wav_meta
    return _build_wav_payload(pcm_chunks, channels, sample_width, sample_rate)


async def _synthesize_free_voice_preview(voice_id: str, preview_line: str, preview_language: str, audio_api_key: str = "") -> bytes:
    """Generate a preview WAV for a free preset voice using the free TTS WebSocket."""
    return await _synthesize_step_voice_preview(
        voice_id=voice_id,
        preview_line=preview_line,
        preview_language=preview_language,
        audio_api_key=audio_api_key,
        free_mode=True,
    )


async def _synthesize_gemini_native_voice_preview(voice_id: str, preview_line: str, audio_api_key: str) -> bytes:
    """Generate a preview WAV using Gemini native TTS."""
    from utils.gemini_tts_voices import GEMINI_TTS_MODEL, normalize_gemini_tts_voice

    normalized_voice_id, recognized = normalize_gemini_tts_voice(voice_id)
    if not recognized:
        raise ValueError(f"不支持的 Gemini 原生音色: {voice_id}")

    url = (
        "https://generativelanguage.googleapis.com/v1beta/"
        f"models/{GEMINI_TTS_MODEL}:generateContent?key={audio_api_key}"
    )
    payload = {
        "contents": [{"parts": [{"text": f"Say the text with a proper tone, don't omit or add any words:\n\"{preview_line}\""}]}],
        "generationConfig": {
            "response_modalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {
                    "prebuiltVoiceConfig": {"voiceName": normalized_voice_id}
                }
            },
        },
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(14, connect=10)) as client:
        response = await client.post(url, json=payload)
        response.raise_for_status()
        data = response.json()

    candidates = data.get("candidates", [])
    if candidates:
        parts = candidates[0].get("content", {}).get("parts", [])
        if parts:
            audio_b64 = parts[0].get("inlineData", {}).get("data")
            if audio_b64:
                return _build_wav_payload([base64.b64decode(audio_b64)], 1, 2, 24000)
    raise RuntimeError("Gemini TTS 未返回音频")


def _has_generated_persona_selection_prompt(prompt_text: object) -> bool:
    return isinstance(prompt_text, str) and "<NEKO_PERSONA_SELECTION>" in prompt_text


def _clear_stale_generated_persona_prompt(character_payload: dict) -> None:
    if not isinstance(character_payload, dict):
        return
    stored_prompt = get_reserved(
        character_payload,
        "system_prompt",
        default=None,
        legacy_keys=("system_prompt",),
    )
    if _has_generated_persona_selection_prompt(stored_prompt):
        cleaned_prompt = strip_generated_persona_selection_prompt(stored_prompt)
        if cleaned_prompt:
            set_reserved(character_payload, "system_prompt", cleaned_prompt)
        else:
            delete_reserved(character_payload, "system_prompt")


async def _read_json_object_or_400(request: Request):
    try:
        payload = await request.json()
    except Exception:
        return None, JSONResponse({'success': False, 'error': '请求体必须是合法的JSON格式'}, status_code=400)
    return payload if isinstance(payload, dict) else {}, None


async def _clear_character_recent_history(config_manager, character_name: str) -> None:
    recent_path = Path(config_manager.memory_dir) / character_name / "recent.json"
    assert_cloudsave_writable(
        config_manager,
        operation="save",
        target=f"memory/{character_name}/recent.json",
    )
    await asyncio.to_thread(recent_path.parent.mkdir, parents=True, exist_ok=True)
    await atomic_write_json_async(recent_path, [], ensure_ascii=False, indent=2)


def _normalize_prompt_synced_field_value(value):
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    if isinstance(value, list):
        if not value:
            return None
        return '、'.join(str(item) for item in value)
    if isinstance(value, (dict, set, tuple)):
        return None
    return str(value)


def _prompt_synced_catgirl_fields(catgirl_payload: dict) -> dict:
    if not isinstance(catgirl_payload, dict):
        return {}
    result = {}
    for key, value in catgirl_payload.items():
        if key in CHARACTER_RESERVED_FIELD_SET:
            continue
        normalized = _normalize_prompt_synced_field_value(value)
        if normalized is not None:
            result[key] = normalized
    return result


def _catgirl_prompt_fields_changed(previous_payload: dict, current_payload: dict) -> bool:
    return _prompt_synced_catgirl_fields(previous_payload) != _prompt_synced_catgirl_fields(current_payload)


async def _refresh_catgirl_context_after_profile_change(
    config_manager,
    name: str,
    characters: dict,
    *,
    is_new: bool = False,
    reload_message: str = "角色设定已更新，页面即将刷新",
) -> dict:
    result = {
        "context_refreshed": True,
        "recent_history_cleared": False,
        "reload_notified": False,
        "session_restarted": False,
    }

    try:
        await _clear_character_recent_history(config_manager, name)
        result["recent_history_cleared"] = True
    except MaintenanceModeError:
        raise
    except Exception as exc:
        logger.warning("清理角色近期上下文失败: name=%s err=%s", name, exc, exc_info=True)
        result.update({
            "success": False,
            "partial_success": True,
            "context_refreshed": False,
            "context_refresh_failed": True,
            "recent_history_clear_failed": True,
            "recent_history_clear_error": str(exc),
            "recent_history_clear_error_type": type(exc).__name__,
            "recent_history_clear_target": f"memory/{name}/recent.json",
            "session_reset_skipped": True,
            "init_skipped": True,
            "error": "角色设定已保存，但近期上下文清理失败，设定未完全刷新",
        })
        return result

    session_manager = get_session_manager()
    is_current_catgirl = name == (characters or {}).get('当前猫娘', '')
    mgr = session_manager.get(name) if is_current_catgirl and session_manager else None
    expected_session = getattr(mgr, "session", None) if mgr and getattr(mgr, "is_active", False) else None

    if expected_session is not None:
        result["reload_notified"] = await send_reload_page_notice(mgr, reload_message)
        try:
            await mgr.end_session(by_server=True, expected_session=expected_session)
            result["session_restarted"] = True
        except Exception as exc:
            logger.error("角色设定更新后结束 session 失败: name=%s err=%s", name, exc)
        reset_circuit = getattr(mgr, "reset_session_start_circuit", None)
        if callable(reset_circuit):
            reset_circuit()

    init_one_catgirl = get_init_one_catgirl()
    await init_one_catgirl(name, is_new=is_new)
    return result


async def _rollback_character_persona_selection_change(config_manager, previous_characters: dict) -> None:
    await config_manager.asave_characters(previous_characters)


def _derive_live2d_model_name(model_ref: str) -> str:
    raw_ref = str(model_ref or "").strip()
    if not raw_ref:
        return ""
    parsed_ref = urlparse(raw_ref)
    is_http_url = parsed_ref.scheme in {"http", "https"} and bool(parsed_ref.netloc)
    model_ref_source = parsed_ref.path if is_http_url and parsed_ref.path else raw_ref
    normalized_ref = model_ref_source.strip().replace("\\", "/")
    if not normalized_ref:
        return ""
    if normalized_ref.endswith(".model3.json"):
        parts = [part for part in normalized_ref.split("/") if part]
        if len(parts) >= 2:
            return parts[-2]
        filename = parts[-1] if parts else normalized_ref
        return filename[:-len(".model3.json")]
    return normalized_ref.rsplit("/", 1)[-1]


def _normalize_live2d_catalog_path(model_path: str) -> str:
    normalized_path = str(model_path or "").strip().replace("\\", "/")
    if not normalized_path:
        return ""
    if normalized_path.startswith("/workshop/"):
        parts = [part for part in normalized_path.split("/") if part]
        return "/".join(parts[2:]) if len(parts) >= 3 else ""
    for prefix in ("/user_live2d/", "/user_live2d_local/", "/static/"):
        if normalized_path.startswith(prefix):
            return normalized_path[len(prefix):]
    return normalized_path.lstrip("/")


def _is_same_live2d_catalog_model_path(candidate_path: str, target_path: str) -> bool:
    candidate_normalized = _normalize_live2d_catalog_path(candidate_path)
    target_normalized = _normalize_live2d_catalog_path(target_path)
    if not candidate_normalized or not target_normalized:
        return False
    if candidate_normalized == target_normalized:
        return True
    candidate_tail = "/".join(candidate_normalized.split("/")[-2:])
    target_tail = "/".join(target_normalized.split("/")[-2:])
    return bool(candidate_tail and candidate_tail == target_tail)


def _derive_live2d_asset_source(model_path: str) -> str:
    normalized_path = str(model_path or "").strip().replace("\\", "/")
    if normalized_path.startswith(("http://", "https://")):
        return "manual_external"
    if normalized_path.startswith("/workshop/"):
        return "steam_workshop"
    if normalized_path.startswith("/static/"):
        return "builtin"
    if normalized_path.startswith(("/user_live2d/", "/user_live2d_local/")):
        return "local_imported"
    return ""


def _derive_model_asset_binding(model_path: str, *, item_id: str = "") -> tuple[str, str]:
    normalized_path = str(model_path or "").strip().replace("\\", "/")
    normalized_item_id = str(item_id or "").strip()

    if not normalized_item_id and normalized_path.startswith("/workshop/"):
        parts = normalized_path.split("/")
        if len(parts) >= 3:
            normalized_item_id = str(parts[2] or "").strip()

    if normalized_item_id or normalized_path.startswith("/workshop/"):
        return "steam_workshop", normalized_item_id
    if normalized_path.startswith(("/user_live2d/", "/user_live2d_local/", "/user_vrm/", "/user_mmd/")):
        return "local_imported", ""
    if normalized_path.startswith(("http://", "https://")):
        return "manual_external", ""
    if normalized_path.startswith("/static/") or (normalized_path and not normalized_path.startswith("/")):
        return "builtin", ""
    return "", ""


def _find_live2d_model_catalog_entry(
    all_models: list[dict],
    *,
    model_name: str = "",
    model_path: str = "",
    asset_source: str = "",
    item_id: str = "",
):
    normalized_name = str(model_name or "").strip()
    normalized_path = _normalize_live2d_catalog_path(model_path)
    normalized_source = str(asset_source or "").strip().lower()
    normalized_item_id = str(item_id or "").strip()

    if normalized_item_id:
        item_matches = [
            model
            for model in all_models
            if str(model.get("item_id") or "").strip() == normalized_item_id
        ]
        item_name_matches = item_matches
        if normalized_name:
            item_name_matches = [
                model
                for model in item_matches
                if str(model.get("name") or "").strip() == normalized_name
            ]

        if normalized_path:
            strict_candidates = item_name_matches if item_name_matches else item_matches
            strict_item_match = next(
                (
                    model
                    for model in strict_candidates
                    if _is_same_live2d_catalog_model_path(
                        str(model.get("path") or "").strip().replace("\\", "/"),
                        normalized_path,
                    )
                ),
                None,
            )
            if strict_item_match is not None:
                return strict_item_match

        if len(item_name_matches) == 1:
            return item_name_matches[0]
        if len(item_matches) == 1:
            return item_matches[0]

    if normalized_path:
        expected_prefixes: tuple[str, ...] = ()
        if normalized_source == "builtin":
            expected_prefixes = ("/static/",)
        elif normalized_source in {"local", "local_imported"}:
            expected_prefixes = ("/user_live2d/", "/user_live2d_local/")
        elif normalized_source == "steam_workshop":
            expected_prefixes = ("/workshop/",)

        for model in all_models:
            candidate_path = str(model.get("path") or "").strip().replace("\\", "/")
            if expected_prefixes and not candidate_path.startswith(expected_prefixes):
                continue
            if _is_same_live2d_catalog_model_path(candidate_path, normalized_path):
                return model

    if normalized_name:
        return next(
            (model for model in all_models if str(model.get("name") or "").strip() == normalized_name),
            None,
        )

    return None


def _resolve_live2d_model_binding(model_identifier: str, *, item_id: str = "") -> tuple[str, str, str]:
    normalized_model = str(model_identifier or "").strip().replace("\\", "/")
    normalized_item_id = str(item_id or "").strip()
    live2d_name = _derive_live2d_model_name(normalized_model)

    resolved_model_path = _normalize_live2d_catalog_path(normalized_model)
    if not resolved_model_path and live2d_name:
        resolved_model_path = f"{live2d_name}/{live2d_name}.model3.json"

    resolved_source = "steam_workshop" if normalized_item_id else (_derive_live2d_asset_source(normalized_model) or "local_imported")
    resolved_source_id = normalized_item_id

    # 外部链接保持原始绑定，不回绑到本地目录/创意工坊目录。
    if resolved_source == "manual_external":
        return normalized_model or resolved_model_path, resolved_source_id, resolved_source

    try:
        all_models = find_models()
        matching_model = _find_live2d_model_catalog_entry(
            all_models,
            model_name=live2d_name,
            model_path=normalized_model,
            asset_source=resolved_source,
            item_id=normalized_item_id,
        )
        if matching_model is not None:
            matched_path = str(matching_model.get("path") or "").strip().replace("\\", "/")
            resolved_model_path = _normalize_live2d_catalog_path(matched_path) or resolved_model_path
            resolved_source = _derive_live2d_asset_source(matched_path) or resolved_source
            resolved_source_id = normalized_item_id or str(matching_model.get("item_id") or "").strip()
    except Exception as exc:
        logger.debug("解析 Live2D 模型绑定时查找模型目录失败: %s", exc)

    return resolved_model_path, resolved_source_id, resolved_source


def _embed_zip_in_png_chunk(png_data: bytes, zip_data: bytes) -> bytes:
    """Embed ZIP data into a PNG ancillary private chunk (the neKo chunk), inserted before IEND.

    The resulting file is still a valid PNG; any image viewer / Electron can preview it normally.
    """
    # PNG IEND 块固定 12 字节: 00 00 00 00  49 45 4E 44  AE 42 60 82
    if len(png_data) < 12 or png_data[-12:-4] != b'\x00\x00\x00\x00IEND':
        raise ValueError("Invalid PNG: IEND chunk not found at end of file")

    iend = png_data[-12:]
    before_iend = png_data[:-12]

    # 构建 neKo 块: length(4B, big-endian) + type(4B) + data + CRC32(4B)
    chunk_type = b'neKo'
    chunk_length = struct.pack('>I', len(zip_data))
    chunk_crc = struct.pack('>I', zlib.crc32(chunk_type + zip_data) & 0xFFFFFFFF)

    neko_chunk = chunk_length + chunk_type + zip_data + chunk_crc
    return before_iend + neko_chunk + iend


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


def _filter_mutable_catgirl_fields(data: dict) -> dict:
    """Filter out reserved fields that the generic character edit API must not write."""
    if not isinstance(data, dict):
        logger.warning(
            "_filter_mutable_catgirl_fields expected dict, got %s: %r",
            type(data).__name__,
            data,
        )
        return {}
    return {
        key: value
        for key, value in data.items()
        if key not in CHARACTER_RESERVED_FIELD_SET
    }


def _normalize_catgirl_field_order(order, available_fields: list[str]) -> list[str]:
    """Order regular profile fields by the explicit order, appending omitted fields in their current stored order."""
    available = {str(key) for key in available_fields}
    result: list[str] = []
    seen: set[str] = set()

    if isinstance(order, list):
        for raw_key in order:
            key = str(raw_key or "").strip()
            if not key or key in seen or key not in available:
                continue
            result.append(key)
            seen.add(key)

    for raw_key in available_fields:
        key = str(raw_key or "").strip()
        if key and key not in seen:
            result.append(key)
            seen.add(key)
    return result


def _extract_catgirl_field_order_payload(raw_data: dict) -> list[str] | None:
    """Read the field order submitted by the frontend; returns None when no explicit order is given."""
    if not isinstance(raw_data, dict):
        return None
    raw_order = raw_data.get("_field_order")
    if isinstance(raw_order, list):
        return [str(item or "").strip() for item in raw_order]
    reserved = raw_data.get("_reserved")
    if isinstance(reserved, dict) and isinstance(reserved.get("field_order"), list):
        return [str(item or "").strip() for item in reserved["field_order"]]
    return None


def _sync_catgirl_field_order(catgirl_data: dict, requested_order: list[str] | None = None) -> None:
    """Maintain the creation order of regular profile fields, preventing numeric keys from being reordered first by JS enumeration rules."""
    if not isinstance(catgirl_data, dict):
        return
    available_fields = [
        str(key)
        for key in catgirl_data.keys()
        if key not in CHARACTER_RESERVED_FIELD_SET
    ]
    if requested_order is None:
        # 也认顶层 _field_order：工坊上传卡的顺序存在顶层（上传时 _reserved 被剥离），
        # 只读 _reserved.field_order 会漏掉它而退回 JSON key 枚举顺序（数字 key 被提前）。
        requested_order = _extract_catgirl_field_order_payload(catgirl_data)
    field_order = _normalize_catgirl_field_order(requested_order, available_fields)
    set_reserved(catgirl_data, "field_order", field_order)


def _flatten_catgirl_for_response(catgirl_data: dict) -> dict:
    """Prepend the field order before flattening reserved fields, so the frontend renders in creation order."""
    if not isinstance(catgirl_data, dict):
        return catgirl_data
    data = copy.deepcopy(catgirl_data)
    _sync_catgirl_field_order(data)
    return flatten_reserved(data)


def _build_minimax_request_prefix(prefix: str, provider_label: str) -> tuple[str, str]:
    """Normalize the user-entered prefix into a safe prefix that MiniMax accepts."""
    import uuid

    original_prefix = str(prefix or '').strip()
    safe_prefix = sanitize_minimax_voice_prefix(
        original_prefix,
        max_length=MINIMAX_PREFIX_MAX_LENGTH,
    )
    if safe_prefix != original_prefix:
        logger.info(
            "%s 音色前缀已规范化: %r -> %r",
            provider_label,
            original_prefix,
            safe_prefix,
        )
    return original_prefix, f"{safe_prefix}{uuid.uuid4().hex[:8]}"


def _normalize_doubao_voice_clone_speaker_id(value: str) -> str:
    speaker_id = str(value or '').strip()
    if not re.fullmatch(r"S_[A-Za-z0-9]+", speaker_id):
        raise ValueError("豆包声音复刻需要填写 S_ 开头的 Speaker ID")
    return speaker_id


async def _get_elevenlabs_base_url(config_manager) -> str:
    return "https://api.elevenlabs.io"


def _config_value_is_enabled(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {'1', 'true', 'yes', 'on'}:
            return True
        if normalized in {'0', 'false', 'no', 'off', ''}:
            return False
    return bool(value)


def _prefixed_elevenlabs_voice_id(raw_voice_id: str) -> str:
    raw = (raw_voice_id or '').strip()
    if raw.startswith(ELEVENLABS_TTS_VOICE_PREFIX):
        return raw
    return f'{ELEVENLABS_TTS_VOICE_PREFIX}{raw}'


def _raw_elevenlabs_voice_id(voice_id: str) -> str:
    raw = (voice_id or '').strip()
    if raw.startswith(ELEVENLABS_TTS_VOICE_PREFIX):
        return raw[len(ELEVENLABS_TTS_VOICE_PREFIX):].strip()
    return raw


def _raise_for_elevenlabs_response(resp: httpx.Response, action: str) -> None:
    if resp.status_code < 400:
        return
    message = f"ElevenLabs {action} API error ({resp.status_code}): {resp.text[:300]}"
    if resp.status_code >= 500:
        raise ElevenLabsUpstreamError(resp.status_code, message)
    raise ValueError(message)


async def _elevenlabs_clone_voice(
    *,
    api_key: str,
    base_url: str,
    audio_buffer: io.BytesIO,
    filename: str,
    name: str,
) -> str:
    audio_buffer.seek(0)
    safe_name = (name or 'NEKO Voice').strip()[:100] or 'NEKO Voice'
    url = f"{base_url.rstrip('/')}/v1/voices/add"
    headers = {"xi-api-key": api_key}
    data = {
        "name": safe_name,
        "description": "Created from NEKO voice clone",
        "labels": json.dumps({"source": "NEKO"}),
    }
    files = [("files", (filename or "voice.wav", audio_buffer, "application/octet-stream"))]
    async with httpx.AsyncClient(timeout=60, proxy=None, trust_env=False) as client:
        resp = await client.post(url, headers=headers, data=data, files=files)
    _raise_for_elevenlabs_response(resp, "voice clone")
    try:
        payload = resp.json()
    except Exception as exc:
        raise ElevenLabsUpstreamError(502, "ElevenLabs returned invalid JSON while adding voice") from exc
    raw_voice_id = payload.get("voice_id") or payload.get("voiceId") or ""
    if not raw_voice_id:
        raise ElevenLabsUpstreamError(502, "ElevenLabs did not return voice_id")
    return _prefixed_elevenlabs_voice_id(raw_voice_id)


# ── ElevenLabs voice design (text description → generated voice) ──────────────
# Voice design is the third voice source (besides preset/clone): a text prompt is
# turned into voice previews, the user picks one, and create-from-preview lands it
# as a normal ElevenLabs voice_id (stored with source='design'). Dispatch then
# reuses the existing ElevenLabs clone path (voice_meta.provider=='elevenlabs'),
# so no separate worker is needed (design doc §7).
ELEVENLABS_VOICE_DESIGN_DESC_MIN = 20
ELEVENLABS_VOICE_DESIGN_DESC_MAX = 1000
# ElevenLabs voice-design previews require a ``text`` between 100 and 1000 chars to
# synthesize audible samples. ``auto_generate_text`` only returns generated voice ids
# (no audio), which would yield empty/unplayable previews — so we always pass a fixed
# preview line instead (must stay ≥ 100 chars).
ELEVENLABS_VOICE_DESIGN_PREVIEW_TEXT = (
    "Hello! This is a preview of your designed voice. I can read your stories, chat "
    "with you about your day, and keep you company whenever you would like a friendly "
    "voice nearby. How do I sound to you so far?"
)


async def _elevenlabs_design_previews(
    *,
    api_key: str,
    base_url: str,
    voice_description: str,
) -> list[dict]:
    """Call POST /v1/text-to-voice/design — returns the list of voice previews.

    Each preview has ``generated_voice_id`` (the handle for create-from-preview)
    and ``audio_base_64`` (an mp3 sample for the user to audition). We let
    ElevenLabs auto-generate the preview text so the caller only supplies a
    description.
    """
    url = f"{base_url.rstrip('/')}/v1/text-to-voice/design"
    headers = {"xi-api-key": api_key, "Content-Type": "application/json"}
    payload = {
        "voice_description": voice_description,
        # 显式给 text（≥100 chars）而非 auto_generate_text，确保返回可试听的 audio_base_64。
        "text": ELEVENLABS_VOICE_DESIGN_PREVIEW_TEXT,
    }
    async with httpx.AsyncClient(timeout=60, proxy=None, trust_env=False) as client:
        resp = await client.post(url, headers=headers, json=payload)
    _raise_for_elevenlabs_response(resp, "voice design")
    try:
        data = resp.json()
    except Exception as exc:
        raise ElevenLabsUpstreamError(502, "ElevenLabs returned invalid JSON while designing voice") from exc
    previews = data.get("previews") if isinstance(data, dict) else None
    if not isinstance(previews, list) or not previews:
        raise ElevenLabsUpstreamError(502, "ElevenLabs did not return voice previews")
    return previews


async def _elevenlabs_create_voice_from_preview(
    *,
    api_key: str,
    base_url: str,
    voice_name: str,
    voice_description: str,
    generated_voice_id: str,
) -> str:
    """Call POST /v1/text-to-voice — persist a designed preview into a voice_id."""
    safe_name = (voice_name or 'NEKO Designed Voice').strip()[:100] or 'NEKO Designed Voice'
    url = f"{base_url.rstrip('/')}/v1/text-to-voice"
    headers = {"xi-api-key": api_key, "Content-Type": "application/json"}
    payload = {
        "voice_name": safe_name,
        "voice_description": voice_description,
        "generated_voice_id": generated_voice_id,
        "labels": {"source": "NEKO"},
    }
    async with httpx.AsyncClient(timeout=60, proxy=None, trust_env=False) as client:
        resp = await client.post(url, headers=headers, json=payload)
    _raise_for_elevenlabs_response(resp, "voice design create")
    try:
        payload_resp = resp.json()
    except Exception as exc:
        raise ElevenLabsUpstreamError(502, "ElevenLabs returned invalid JSON while creating designed voice") from exc
    raw_voice_id = payload_resp.get("voice_id") or payload_resp.get("voiceId") or ""
    if not raw_voice_id:
        raise ElevenLabsUpstreamError(502, "ElevenLabs did not return voice_id")
    return _prefixed_elevenlabs_voice_id(raw_voice_id)


def _is_local_voice_clone_tts_config(tts_config: dict, core_config: dict | None = None) -> bool:
    provider = str((core_config or {}).get('ttsModelProvider') or '').strip()
    if provider == 'vllm_omni':
        return False
    base_url = _local_voice_clone_tts_base_url(tts_config, core_config)
    return bool(tts_config.get('is_custom') and base_url.startswith(('ws://', 'wss://')))


def _local_voice_clone_tts_base_url(tts_config: dict, core_config: dict | None = None) -> str:
    return str(
        tts_config.get('base_url')
        or tts_config.get('url')
        or (core_config or {}).get('ttsModelUrl')
        or (core_config or {}).get('TTS_MODEL_URL')
        or ''
    ).strip()


async def _elevenlabs_synthesize_preview(
    config_manager,
    voice_id: str,
    text: str,
    *,
    base_url: str | None = None,
) -> tuple[bytes, str]:
    api_key = config_manager.get_tts_api_key('elevenlabs')
    if not api_key:
        return b'', 'ELEVENLABS_API_KEY_MISSING'
    raw_voice_id = _raw_elevenlabs_voice_id(voice_id)
    if not raw_voice_id:
        return b'', 'TTS_VOICE_ID_MISSING'

    # 优先使用传入的 base_url，否则获取默认值并去除末尾斜杠
    base_url = (base_url or await _get_elevenlabs_base_url(config_manager)).rstrip('/')

    payload = {
        "text": text,
        "model_id": ELEVENLABS_TTS_DEFAULT_MODEL,
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75,
            "style": 0.0,
            "use_speaker_boost": True,
        },
    }
    url = f"{base_url}/v1/text-to-speech/{raw_voice_id}"
    headers = {
        "xi-api-key": api_key,
        "Accept": "audio/mpeg",
        "Content-Type": "application/json",
    }
    params = {"output_format": "mp3_44100_128"}
    async with httpx.AsyncClient(timeout=30, proxy=None, trust_env=False) as client:
        resp = await client.post(url, headers=headers, params=params, json=payload)
    _raise_for_elevenlabs_response(resp, "preview")
    return resp.content, ''


def _resolve_reload_page_notice_code(message_text: str, message_code: str | None = None) -> str:
    if message_code:
        return message_code

    text = str(message_text or "")
    if "云存档" in text:
        return "RELOAD_PAGE_CLOUDSAVE_CHARACTER"
    if "人格" in text:
        return "RELOAD_PAGE_PERSONA"
    if "角色设定" in text or "设定" in text:
        return "RELOAD_PAGE_CHARACTER_SETTINGS"
    if "音色" in text:
        return "RELOAD_PAGE_VOICE_STYLE"
    if "语音" in text:
        return "RELOAD_PAGE_VOICE"
    return "RELOAD_PAGE"


async def send_reload_page_notice(
    session,
    message_text: str = "语音已更新，页面即将刷新",
    message_code: str | None = None,
):
    """
    Send a page-reload notice to the frontend (via WebSocket).

    Args:
        session: LLMSessionManager instance
        message_text: message text to send (auto-translated)
        message_code: explicit localized message code; inferred from the message text when empty

    Returns:
        bool: whether the notice was sent successfully
    """
    if not session or not session.websocket:
        return False

    # 检查 WebSocket 连接状态
    if not hasattr(session.websocket, 'client_state') or session.websocket.client_state != session.websocket.client_state.CONNECTED:
        return False

    try:
        notice_code = _resolve_reload_page_notice_code(message_text, message_code)
        await session.websocket.send_text(json.dumps({
            "type": "reload_page",
            "message": json.dumps({"code": notice_code, "details": {"message": message_text}})
        }))
        logger.info("已通知前端刷新页面")
        return True
    except Exception as e:
        logger.warning(f"通知前端刷新页面失败: {e}")
        return False


async def notify_memory_server_reload(*, reason: str = "") -> bool:
    try:
        async with httpx.AsyncClient(proxy=None, trust_env=False) as client:
            response = await client.post(
                f"http://127.0.0.1:{MEMORY_SERVER_PORT}/reload",
                timeout=5.0,
            )
        if response.status_code != 200:
            logger.warning(
                "⚠️ 记忆服务器重新加载失败，status=%s, reason=%s",
                response.status_code,
                reason,
            )
            return False

        payload = response.json()
        if payload.get("status") == "success":
            logger.info("✅ 已通知记忆服务器重新加载配置（%s）", reason or "角色数据更新")
            return True

        logger.warning(
            "⚠️ 记忆服务器重新加载返回非成功状态，payload=%s, reason=%s",
            payload,
            reason,
        )
    except Exception as exc:
        logger.warning("⚠️ 通知记忆服务器重新加载配置时出错: %s（reason=%s）", exc, reason)
    return False


async def release_memory_server_character(character_name: str, *, reason: str = "") -> bool:
    from urllib.parse import quote
    from utils.internal_http_client import get_internal_http_client

    try:
        encoded_name = quote(character_name, safe="")
        # 复用进程级单例避免 per-call SSLContext 冷启动（实测 ~1.1s/次）。
        # 单例在 on_shutdown 末尾由 aclose_internal_http_client 统一关闭，
        # release/upload 阶段之前都可安全共享；无需 async with。
        client = get_internal_http_client()
        response = await client.post(
            f"http://127.0.0.1:{MEMORY_SERVER_PORT}/release_character/{encoded_name}",
            timeout=5.0,
        )
        if response.status_code != 200:
            logger.warning(
                "⚠️ 释放记忆服务器角色句柄失败，status=%s, character=%s, reason=%s",
                response.status_code,
                character_name,
                reason,
            )
            return False

        payload = response.json()
        if payload.get("status") == "success":
            logger.info("✅ 已释放角色 %s 的记忆服务器句柄（%s）", character_name, reason or "角色文件操作前")
            return True

        logger.warning(
            "⚠️ 释放记忆服务器角色句柄返回非成功状态，payload=%s, character=%s, reason=%s",
            payload,
            character_name,
            reason,
        )
    except Exception as exc:
        logger.warning(
            "⚠️ 调用记忆服务器释放角色句柄时出错: %s（character=%s, reason=%s）",
            exc,
            character_name,
            reason,
        )
    return False


def _snapshot_existing_paths(targets: list[Path], backup_root: Path):
    records = []
    seen: set[str] = set()

    for index, target_path in enumerate(sorted(targets, key=lambda item: (len(item.parts), str(item)))):
        normalized_path = str(target_path)
        if normalized_path in seen:
            continue
        seen.add(normalized_path)

        backup_path = None
        if target_path.exists():
            backup_path = backup_root / f"{index:02d}" / target_path.name
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            if target_path.is_dir():
                shutil.copytree(target_path, backup_path, dirs_exist_ok=True)
            else:
                shutil.copy2(target_path, backup_path)

        records.append({
            "target": target_path,
            "backup": backup_path,
        })

    return records


def _create_character_operation_backup_dir(config_manager, prefix: str):
    backup_root = Path(getattr(config_manager, "app_docs_dir", "")) / ".rollback_tmp"
    backup_root.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(prefix=prefix, dir=str(backup_root))


def _restore_snapshot_paths(records) -> None:
    for record in sorted(records, key=lambda item: len(item["target"].parts), reverse=True):
        target_path = record["target"]
        backup_path = record.get("backup")

        if target_path.exists():
            if target_path.is_dir():
                shutil.rmtree(target_path)
            else:
                target_path.unlink()

        if backup_path is None or not backup_path.exists():
            continue

        if backup_path.is_dir():
            shutil.copytree(backup_path, target_path, dirs_exist_ok=True)
        else:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup_path, target_path)


def _build_character_tombstones_state(config_manager, character_name: str) -> dict:
    if is_cloudsave_disabled():
        return config_manager.build_default_character_tombstones_state()

    cloud_state = config_manager.load_cloudsave_local_state()
    sequence_number = max(1, int(cloud_state.get("next_sequence_number") or 1))
    tombstone_state = config_manager.load_character_tombstones_state()
    normalized_entries = {}
    for entry in tombstone_state.get("tombstones") or []:
        if not isinstance(entry, dict):
            continue
        existing_name = str(entry.get("character_name") or "").strip()
        if not existing_name:
            continue
        normalized_entries[existing_name] = entry

    normalized_entries[character_name] = {
        "character_name": character_name,
        "deleted_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "sequence_number": sequence_number,
    }
    return {
        "version": config_manager.CHARACTER_TOMBSTONES_STATE_VERSION,
        "tombstones": [
            normalized_entries[existing_name]
            for existing_name in sorted(normalized_entries)
        ],
    }


async def _rollback_character_operation(
    config_manager,
    *,
    characters_snapshot: dict,
    memory_snapshot_records,
    tombstone_snapshot: dict | None = None,
    reason: str,
) -> str:
    rollback_errors: list[str] = []

    try:
        await asyncio.to_thread(_restore_snapshot_paths, memory_snapshot_records)
    except Exception as exc:
        rollback_errors.append(f"memory restore failed: {exc}")

    try:
        await asyncio.to_thread(
            config_manager.save_characters,
            characters_snapshot,
            bypass_write_fence=True,
        )
    except Exception as exc:
        rollback_errors.append(f"characters restore failed: {exc}")

    if tombstone_snapshot is not None:
        try:
            await asyncio.to_thread(
                config_manager.save_character_tombstones_state, tombstone_snapshot
            )
        except Exception as exc:
            rollback_errors.append(f"tombstones restore failed: {exc}")

    try:
        initialize_character_data = get_initialize_character_data()
        await initialize_character_data()
    except Exception as exc:
        rollback_errors.append(f"initialize_character_data failed: {exc}")

    try:
        reload_notified = await notify_memory_server_reload(reason=reason)
        if not reload_notified:
            rollback_errors.append("notify_memory_server_reload failed: returned False")
    except Exception as exc:
        rollback_errors.append(f"notify_memory_server_reload failed: {exc}")

    return "; ".join(rollback_errors)


@router.get('')
async def get_characters(request: Request):
    """Get character data, with persona auto-translation based on the user language."""
    _config_manager = get_config_manager()
    # 创建深拷贝，避免修改原始配置数据
    characters_data = copy.deepcopy(await _config_manager.aload_characters())
    if isinstance(characters_data.get('猫娘'), dict):
        # COMPAT(v1->v2): 前端仍依赖旧平铺字段，接口层按需展开。
        for cat_name, cat_data in list(characters_data['猫娘'].items()):
            if isinstance(cat_data, dict):
                characters_data['猫娘'][cat_name] = _flatten_catgirl_for_response(cat_data)

    # 尝试从请求参数或请求头获取用户语言
    user_language = request.query_params.get('language')
    if not user_language:
        accept_lang = request.headers.get('Accept-Language', 'zh-CN')
        # Accept-Language 可能包含多个语言，取第一个
        user_language = accept_lang.split(',')[0].split(';')[0].strip()
    # 使用公共函数归一化语言代码
    user_language = normalize_language_code(user_language, format='full')

    # 如果语言是中文，不需要翻译
    if user_language == 'zh-CN':
        return _json_no_store_response(characters_data)

    # 需要翻译：翻译人设数据（在深拷贝上进行，不影响原始配置）
    try:
        from utils.language_utils import get_translation_service
        translation_service = get_translation_service(_config_manager)

        # 翻译主人数据
        if '主人' in characters_data and isinstance(characters_data['主人'], dict):
            characters_data['主人'] = await translation_service.translate_dict(
                characters_data['主人'],
                user_language,
                fields_to_translate=['昵称']
            )

        # 翻译猫娘数据（并行翻译以提升性能）
        if '猫娘' in characters_data and isinstance(characters_data['猫娘'], dict):
            async def translate_catgirl(name, data):
                if isinstance(data, dict):
                    return name, await translation_service.translate_dict(
                        data, user_language,
                        fields_to_translate=['昵称', '性别']  # 注意：不翻译档案名和 system_prompt
                    )
                return name, data

            results = await asyncio.gather(*[
                translate_catgirl(name, data)
                for name, data in characters_data['猫娘'].items()
            ])
            characters_data['猫娘'] = dict(results)

        return _json_no_store_response(characters_data)
    except Exception as e:
        logger.error(f"翻译人设数据失败: {e}，返回原始数据")
        return _json_no_store_response(characters_data)


@router.get('/current_live2d_model')
async def get_current_live2d_model(catgirl_name: str = "", item_id: str = ""):
    """Get Live2D model info for the specified or current character.

    Args:
        catgirl_name: character name
        item_id: optional item ID to directly specify the model
    """
    try:
        _config_manager = get_config_manager()
        characters = await _config_manager.aload_characters()

        # 如果没有指定角色名称，使用当前猫娘
        if not catgirl_name:
            catgirl_name = characters.get('当前猫娘', '')

        # 查找指定角色的Live2D模型
        live2d_model_name = None
        model_info = None
        saved_model_path = ""
        saved_asset_source = ""
        saved_item_id = ""

        # 首先尝试通过item_id查找模型
        if item_id:
            try:
                logger.debug(f"尝试通过item_id {item_id} 查找模型")
                # 获取所有模型
                all_models = find_models()
                # 查找匹配item_id的模型
                matching_model = next((m for m in all_models if m.get('item_id') == item_id), None)

                if matching_model:
                    logger.debug(f"通过item_id找到模型: {matching_model['name']}")
                    # 复制模型信息
                    model_info = matching_model.copy()
                    live2d_model_name = model_info['name']
            except Exception as e:
                logger.warning(f"通过item_id查找模型失败: {e}")

        # 如果没有通过item_id找到模型，再通过角色名称查找
        if not model_info and catgirl_name:
            # 在猫娘列表中查找
            if '猫娘' in characters and catgirl_name in characters['猫娘']:
                catgirl_data = characters['猫娘'][catgirl_name]
                saved_model_path = get_reserved(
                    catgirl_data,
                    'avatar',
                    'live2d',
                    'model_path',
                    default='',
                    legacy_keys=('live2d',),
                )
                live2d_model_name = _derive_live2d_model_name(saved_model_path)
                saved_asset_source = get_reserved(
                    catgirl_data,
                    'avatar',
                    'asset_source',
                    default='',
                )

                # 检查是否有保存的item_id
                saved_item_id = get_reserved(
                    catgirl_data,
                    'avatar',
                    'asset_source_id',
                    default='',
                    legacy_keys=('live2d_item_id', 'item_id'),
                )
                if saved_item_id:
                    logger.debug(f"发现角色 {catgirl_name} 保存的item_id: {saved_item_id}")
                    try:
                        # 尝试通过保存的item_id查找模型
                        all_models = find_models()
                        matching_model = _find_live2d_model_catalog_entry(
                            all_models,
                            model_name=live2d_model_name,
                            model_path=saved_model_path,
                            asset_source=saved_asset_source,
                            item_id=saved_item_id,
                        )
                        if matching_model:
                            logger.debug(f"通过保存的item_id找到模型: {matching_model['name']}")
                            model_info = matching_model.copy()
                            live2d_model_name = model_info['name']
                    except Exception as e:
                        logger.warning(f"通过保存的item_id查找模型失败: {e}")

        # 如果找到了模型名称，获取模型信息
        if live2d_model_name:
            try:
                # 先从完整的模型列表中查找，这样可以获取到item_id等完整信息
                all_models = find_models()

                # 同时获取工坊模型列表，确保能找到工坊模型
                try:
                    from .workshop_router import get_subscribed_workshop_items
                    workshop_result = await get_subscribed_workshop_items()
                    if isinstance(workshop_result, dict) and workshop_result.get('success', False):
                        for item in workshop_result.get('items', []):
                            installed_folder = item.get('installedFolder')
                            workshop_item_id = item.get('publishedFileId')
                            if installed_folder and os.path.exists(installed_folder) and os.path.isdir(installed_folder) and workshop_item_id:
                                # 检查安装目录下是否有.model3.json文件
                                for filename in os.listdir(installed_folder):
                                    if filename.endswith('.model3.json'):
                                        model_name = os.path.splitext(os.path.splitext(filename)[0])[0]
                                        if model_name not in [m['name'] for m in all_models]:
                                            all_models.append({
                                                'name': model_name,
                                                'path': f'/workshop/{workshop_item_id}/{filename}',
                                                'source': 'steam_workshop',
                                                'item_id': workshop_item_id
                                            })
                                # 检查子目录
                                for subdir in os.listdir(installed_folder):
                                    subdir_path = os.path.join(installed_folder, subdir)
                                    if os.path.isdir(subdir_path):
                                        model_name = subdir
                                        model3_files = [f for f in os.listdir(subdir_path) if f.endswith('.model3.json')]
                                        if model3_files:
                                            model_file = model3_files[0]
                                            if model_name not in [m['name'] for m in all_models]:
                                                all_models.append({
                                                    'name': model_name,
                                                    'path': encode_url_path(f'/workshop/{workshop_item_id}/{model_name}/{model_file}'),
                                                    'source': 'steam_workshop',
                                                    'item_id': workshop_item_id
                                                })
                except Exception as we:
                    logger.debug(f"获取工坊模型列表时出错（非关键）: {we}")

                matching_model = model_info.copy() if model_info else None
                if matching_model is None:
                    # 保留前面已命中的 item_id 结果；仅在没有现成匹配时再做目录级回退查找。
                    matching_model = _find_live2d_model_catalog_entry(
                        all_models,
                        model_name=live2d_model_name,
                        model_path=saved_model_path,
                        asset_source=saved_asset_source,
                        item_id=saved_item_id,
                    )
                elif not item_id and not saved_item_id:
                    fallback_model = _find_live2d_model_catalog_entry(
                        all_models,
                        model_name=live2d_model_name,
                        model_path=saved_model_path,
                        asset_source=saved_asset_source,
                        item_id='',
                    )
                    if fallback_model is not None:
                        matching_model = fallback_model

                if matching_model:
                    # 使用完整的模型信息，包含item_id
                    model_info = matching_model.copy()
                    logger.debug(f"从完整模型列表获取模型信息: {model_info}")
                else:
                    # 如果在完整列表中找不到，回退到原来的逻辑
                    model_dir, url_prefix = find_model_directory(live2d_model_name)
                    if model_dir and os.path.exists(model_dir):
                        # 查找模型配置文件
                        model_files = [f for f in os.listdir(model_dir) if f.endswith('.model3.json')]
                        if model_files:
                            model_file = model_files[0]

                            # 使用保存的item_id构建model_path，从之前的逻辑中获取saved_item_id
                            saved_item_id = (
                                get_reserved(
                                    catgirl_data,
                                    'avatar',
                                    'asset_source_id',
                                    default='',
                                    legacy_keys=('live2d_item_id', 'item_id'),
                                ) if 'catgirl_data' in locals() else ''
                            )

                            # 如果有保存的item_id，使用它构建路径
                            if saved_item_id:
                                if url_prefix == '/workshop':
                                    model_subdir = os.path.basename(model_dir.rstrip('/\\'))
                                    model_path = encode_url_path(f'{url_prefix}/{saved_item_id}/{model_subdir}/{model_file}')
                                else:
                                    model_path = encode_url_path(f'{url_prefix}/{saved_item_id}/{model_file}')
                                logger.debug(f"使用保存的item_id构建模型路径: {model_path}")
                            else:
                                # 原始路径构建逻辑
                                model_path = encode_url_path(f'{url_prefix}/{live2d_model_name}/{model_file}')
                                logger.debug(f"使用模型名称构建路径: {model_path}")

                            model_info = {
                                'name': live2d_model_name,
                                'item_id': saved_item_id,
                                'path': model_path
                            }
            except Exception as e:
                logger.warning(f"获取模型信息失败: {e}")

        # 回退机制：如果没有找到模型，使用默认模型 (DEFAULT_LIVE2D_MODEL_NAME)
        if not live2d_model_name or not model_info:
            logger.info(
                f"猫娘 {catgirl_name} 未设置Live2D模型，回退到默认模型 "
                f"{DEFAULT_LIVE2D_MODEL_NAME}"
            )
            live2d_model_name = DEFAULT_LIVE2D_MODEL_NAME
            try:
                # 先从完整的模型列表中查找内置/static 默认模型，避免误匹配用户/工坊同名模型
                all_models = find_models()
                matching_model = next(
                    (
                        m for m in all_models
                        if m.get('name') == DEFAULT_LIVE2D_MODEL_NAME
                        and m.get('source') in ('static', 'builtin')
                    ),
                    None,
                )
                if matching_model is None:
                    matching_model = next(
                        (m for m in all_models if m.get('name') == DEFAULT_LIVE2D_MODEL_NAME),
                        None,
                    )

                if matching_model:
                    model_info = matching_model.copy()
                    model_info['is_fallback'] = True
                else:
                    # 如果找不到，回退到原来的逻辑
                    model_dir, url_prefix = find_model_directory(DEFAULT_LIVE2D_MODEL_NAME)
                    if model_dir and os.path.exists(model_dir):
                        model_files = [f for f in os.listdir(model_dir) if f.endswith('.model3.json')]
                        if model_files:
                            model_file = model_files[0]
                            model_path = f'{url_prefix}/{DEFAULT_LIVE2D_MODEL_NAME}/{model_file}'
                            model_info = {
                                'name': DEFAULT_LIVE2D_MODEL_NAME,
                                'path': model_path,
                                'is_fallback': True  # 标记这是回退模型
                            }
            except Exception as e:
                logger.error(
                    f"获取默认模型 {DEFAULT_LIVE2D_MODEL_NAME} 失败: {e}"
                )

        if model_info and isinstance(model_info.get('path'), str):
            model_info['path'] = encode_url_path(model_info['path'])

        if not model_info or not model_info.get('path'):
            error_message = f"默认Live2D模型 {DEFAULT_LIVE2D_MODEL_NAME} 不可用"
            logger.error(error_message)
            return JSONResponse(content={
                'success': False,
                'catgirl_name': catgirl_name,
                'model_name': live2d_model_name or DEFAULT_LIVE2D_MODEL_NAME,
                'model_info': None,
                'error': error_message,
            })

        return JSONResponse(content={
            'success': True,
            'catgirl_name': catgirl_name,
            'model_name': live2d_model_name,
            'model_info': model_info
        })

    except Exception as e:
        logger.error(f"获取角色Live2D模型失败: {e}")
        return JSONResponse(content={
            'success': False,
            'error': str(e)
        })

@router.put('/catgirl/l2d/{name}')
async def update_catgirl_l2d(name: str, request: Request):
    """Update the specified catgirl's model settings (supports Live2D and VRM)."""
    try:
        data = await request.json()
        live2d_model = data.get('live2d')
        vrm_model = data.get('vrm')
        mmd_model = data.get('mmd')
        model_type = data.get('model_type', 'live2d')  # 默认为live2d以保持兼容性
        item_id = data.get('item_id')  # 获取可选的item_id
        vrm_animation = data.get('vrm_animation')  # 获取可选的VRM动作
        idle_animation = data.get('idle_animation')  # 获取可选的VRM待机动作
        mmd_animation = data.get('mmd_animation')  # 获取可选的MMD动作
        mmd_idle_animation = data.get('mmd_idle_animation')  # 获取可选的MMD待机动作

        # 根据model_type检查相应的模型字段
        model_type_str = str(model_type).lower() if model_type else 'live2d'

        # 【修复】model_type 只允许 {live2d, vrm, live3d, pngtuber}，否则 400
        if model_type_str not in ['live2d', 'vrm', 'live3d', 'pngtuber']:
            return JSONResponse(
                content={
                    'success': False,
                    'error': f'无效的模型类型: {model_type}，只允许 live2d、vrm、live3d 或 pngtuber'
                },
                status_code=400
            )

        # 归一化：旧客户端发送的 'vrm' 统一为 'live3d'（走 Live3D VRM 子分支处理）
        if model_type_str == 'vrm':
            model_type_str = 'live3d'

        if model_type_str == 'pngtuber':
            raw_pngtuber = data.get('pngtuber') if isinstance(data.get('pngtuber'), dict) else {}
            pngtuber_payload = dict(raw_pngtuber)
            for key in ('idle_image', 'talking_image', 'drag_image', 'click_image', 'happy_image', 'sad_image', 'angry_image', 'surprised_image'):
                if key not in pngtuber_payload and key in data:
                    pngtuber_payload[key] = data.get(key)
            allowed_prefixes = ('/user_pngtuber/', '/static/', '/workshop/')
            allowed_exts = ('.png', '.gif', '.jpg', '.jpeg', '.webp')
            idle_image = str(pngtuber_payload.get('idle_image') or '').strip().replace('\\', '/')
            if not idle_image:
                return JSONResponse(content={'success': False, 'error': '未提供PNGTuber idle_image'}, status_code=400)
            for key in ('idle_image', 'talking_image', 'drag_image', 'click_image', 'happy_image', 'sad_image', 'angry_image', 'surprised_image'):
                image_path = str(pngtuber_payload.get(key) or '').strip().replace('\\', '/')
                if not image_path:
                    pngtuber_payload[key] = ''
                    continue
                if image_path.startswith('data:'):
                    return JSONResponse(content={'success': False, 'error': f'PNGTuber图片路径不能使用data URL: {key}'}, status_code=400)
                if '..' in image_path:
                    return JSONResponse(content={'success': False, 'error': f'PNGTuber图片路径不能包含路径遍历（..）: {key}'}, status_code=400)
                is_remote_image = image_path.startswith('http://') or image_path.startswith('https://')
                if not is_remote_image and not any(image_path.startswith(prefix) for prefix in allowed_prefixes):
                    return JSONResponse(content={'success': False, 'error': f'PNGTuber图片路径必须以 /user_pngtuber/、/static/ 或 /workshop/ 开头: {key}'}, status_code=400)
                extension_path = image_path.lower().split('?', 1)[0].split('#', 1)[0]
                if not extension_path.endswith(allowed_exts):
                    return JSONResponse(content={'success': False, 'error': f'PNGTuber图片格式必须是 PNG/GIF/JPG/JPEG/WebP: {key}'}, status_code=400)
                pngtuber_payload[key] = image_path

            metadata_path = str(
                pngtuber_payload.get('layered_metadata')
                or pngtuber_payload.get('metadata')
                or ''
            ).strip().replace('\\', '/')

            def _infer_pngtuber_metadata_from_idle(idle_path: str) -> str:
                parts = [part for part in idle_path.split('/') if part]
                if len(parts) < 3:
                    return ''
                source_prefix = parts[0]
                model_folder = parts[1]
                try:
                    config_manager = get_config_manager()
                    if source_prefix == 'user_pngtuber':
                        root = config_manager.pngtuber_dir / model_folder
                        url_prefix = '/user_pngtuber'
                    elif source_prefix == 'static':
                        root = config_manager.project_root / 'static' / model_folder
                        url_prefix = '/static'
                    elif source_prefix == 'workshop':
                        root = config_manager.workshop_dir / model_folder
                        url_prefix = '/workshop'
                    else:
                        return ''
                except Exception:
                    return ''
                for filename in (
                    'metadata.pngtube-remix.json',
                    'metadata.pngtuber-plus.json',
                    'metadata.json',
                ):
                    if (root / filename).is_file():
                        return f'{url_prefix}/{model_folder}/{filename}'
                return ''

            if not metadata_path:
                metadata_path = _infer_pngtuber_metadata_from_idle(idle_image)

            if metadata_path:
                if metadata_path.startswith('data:'):
                    return JSONResponse(content={'success': False, 'error': 'PNGTuber分层metadata路径不能使用data URL'}, status_code=400)
                if '..' in metadata_path:
                    return JSONResponse(content={'success': False, 'error': 'PNGTuber分层metadata路径不能包含路径遍历（..）'}, status_code=400)
                is_remote_metadata = metadata_path.startswith('http://') or metadata_path.startswith('https://')
                if not is_remote_metadata and not any(metadata_path.startswith(prefix) for prefix in allowed_prefixes):
                    return JSONResponse(content={'success': False, 'error': 'PNGTuber分层metadata路径必须以 /user_pngtuber/、/static/ 或 /workshop/ 开头'}, status_code=400)
                metadata_ext_path = metadata_path.lower().split('?', 1)[0].split('#', 1)[0]
                if not metadata_ext_path.endswith('.json'):
                    return JSONResponse(content={'success': False, 'error': 'PNGTuber分层metadata必须是 JSON 文件'}, status_code=400)
                pngtuber_payload['layered_metadata'] = metadata_path
                pngtuber_payload['adapter'] = 'layered_canvas_v1'
            else:
                pngtuber_payload['layered_metadata'] = ''
                pngtuber_payload['adapter'] = ''

            for key in ('source_type', 'source_format'):
                value = str(pngtuber_payload.get(key) or '').strip()
                pngtuber_payload[key] = value

            def _bounded_number(value, default, min_value, max_value):
                try:
                    parsed = float(value)
                except (TypeError, ValueError):
                    return default
                if not math.isfinite(parsed):
                    raise ValueError('数值字段必须是有限值')
                return max(min_value, min(max_value, parsed))

            try:
                pngtuber_payload['scale'] = _bounded_number(pngtuber_payload.get('scale'), 1, 0.1, 5)
                pngtuber_payload['offset_x'] = _bounded_number(pngtuber_payload.get('offset_x'), 0, -5000, 5000)
                pngtuber_payload['offset_y'] = _bounded_number(pngtuber_payload.get('offset_y'), 0, -5000, 5000)
                pngtuber_payload['mobile_scale'] = _bounded_number(
                    pngtuber_payload.get('mobile_scale'),
                    min(pngtuber_payload['scale'], 1),
                    0.1,
                    5,
                )
                pngtuber_payload['mobile_offset_x'] = _bounded_number(pngtuber_payload.get('mobile_offset_x'), 0, -5000, 5000)
                pngtuber_payload['mobile_offset_y'] = _bounded_number(pngtuber_payload.get('mobile_offset_y'), 0, -5000, 5000)
            except ValueError as exc:
                return JSONResponse(content={'success': False, 'error': str(exc)}, status_code=400)
            pngtuber_payload['mirror'] = _config_value_is_enabled(pngtuber_payload.get('mirror'))

        if model_type_str == 'live3d':
            # Live3D 模式：接受 VRM 或 MMD 模型
            if vrm_model and mmd_model:
                return JSONResponse(content={'success': False, 'error': '不能同时提供VRM和MMD模型，请选择其中一个'}, status_code=400)
            if vrm_model:
                # 验证 VRM 路径
                vrm_model_str = str(vrm_model).strip()
                if '://' in vrm_model_str or vrm_model_str.startswith('data:'):
                    return JSONResponse(content={'success': False, 'error': 'VRM模型路径不能包含URL方案'}, status_code=400)
                if '..' in vrm_model_str:
                    return JSONResponse(content={'success': False, 'error': 'VRM模型路径不能包含路径遍历（..）'}, status_code=400)
                allowed_prefixes = ['/user_vrm/', '/static/vrm/', '/workshop/']
                if not any(vrm_model_str.startswith(prefix) for prefix in allowed_prefixes):
                    return JSONResponse(content={'success': False, 'error': 'VRM模型路径必须以 /user_vrm/、/static/vrm/ 或 /workshop/ 开头'}, status_code=400)
                vrm_model = vrm_model_str
            elif mmd_model:
                # 验证 MMD 路径
                mmd_model_str = str(mmd_model).strip()
                if '://' in mmd_model_str or mmd_model_str.startswith('data:'):
                    return JSONResponse(content={'success': False, 'error': 'MMD模型路径不能包含URL方案'}, status_code=400)
                if '..' in mmd_model_str:
                    return JSONResponse(content={'success': False, 'error': 'MMD模型路径不能包含路径遍历（..）'}, status_code=400)
                allowed_mmd_prefixes = ['/user_mmd/', '/static/mmd/', '/workshop/']
                if not any(mmd_model_str.startswith(prefix) for prefix in allowed_mmd_prefixes):
                    return JSONResponse(content={'success': False, 'error': 'MMD模型路径必须以 /user_mmd/、/static/mmd/ 或 /workshop/ 开头'}, status_code=400)
                mmd_model = mmd_model_str
            else:
                return JSONResponse(content={'success': False, 'error': '未提供VRM或MMD模型路径'}, status_code=400)
        elif model_type_str != 'pngtuber':
            if not live2d_model:
                return JSONResponse(
                    content={
                        'success': False,
                        'error': '未提供Live2D模型名称'
                    },
                    status_code=400
                )

        # 加载当前角色配置
        _config_manager = get_config_manager()
        characters = await _config_manager.aload_characters()

        # 确保猫娘配置存在
        if '猫娘' not in characters:
            characters['猫娘'] = {}

        # 确保指定猫娘的配置存在
        if name not in characters['猫娘']:
            return JSONResponse(
                {'success': False, 'error': '猫娘不存在'},
                status_code=404
            )

        # 切换模型类型时保留非当前模型配置，避免来回切换后丢失待机动作/光照等设置
        if model_type_str == 'live3d':
            set_reserved(characters['猫娘'][name], 'avatar', 'model_type', 'live3d')
            active_model_binding_path = ""

            if vrm_model:
                # Live3D + VRM：更新当前激活的 VRM 配置，保留 MMD 配置便于切回
                set_reserved(characters['猫娘'][name], 'avatar', 'live3d_sub_type', 'vrm')
                set_reserved(characters['猫娘'][name], 'avatar', 'vrm', 'model_path', vrm_model)
                active_model_binding_path = vrm_model

                # 处理 VRM 动画（复用同样的验证逻辑）
                if 'vrm_animation' in data:
                    if vrm_animation is None or vrm_animation == '':
                        set_reserved(characters['猫娘'][name], 'avatar', 'vrm', 'animation', None)
                    else:
                        vrm_animation_str = str(vrm_animation).strip()
                        if '://' in vrm_animation_str or vrm_animation_str.startswith('data:'):
                            return JSONResponse(content={'success': False, 'error': 'VRM动画路径不能包含URL方案'}, status_code=400)
                        if '..' in vrm_animation_str:
                            return JSONResponse(content={'success': False, 'error': 'VRM动画路径不能包含路径遍历（..）'}, status_code=400)
                        allowed_animation_prefixes = ['/user_vrm/animation/', '/static/vrm/animation/']
                        if not any(vrm_animation_str.startswith(prefix) for prefix in allowed_animation_prefixes):
                            return JSONResponse(content={'success': False, 'error': 'VRM动画路径必须以 /user_vrm/animation/ 或 /static/vrm/animation/ 开头'}, status_code=400)
                        set_reserved(characters['猫娘'][name], 'avatar', 'vrm', 'animation', vrm_animation_str)

                if 'idle_animation' in data:
                    if idle_animation is None or idle_animation == '' or idle_animation == []:
                        set_reserved(characters['猫娘'][name], 'avatar', 'vrm', 'idle_animation', [])
                    elif isinstance(idle_animation, str):
                        idle_list = [idle_animation]
                    elif isinstance(idle_animation, list):
                        idle_list = idle_animation
                    else:
                        return JSONResponse(content={'success': False, 'error': 'idle_animation must be a string or list of strings'}, status_code=400)
                    if isinstance(idle_animation, (str, list)) and idle_animation:
                        allowed_animation_prefixes = ['/user_vrm/animation/', '/static/vrm/animation/']
                        for item in idle_list:
                            item_str = str(item).strip()
                            if '://' in item_str or item_str.startswith('data:'):
                                return JSONResponse(content={'success': False, 'error': '待机动作路径不能包含URL方案'}, status_code=400)
                            if '..' in item_str:
                                return JSONResponse(content={'success': False, 'error': '待机动作路径不能包含路径遍历（..）'}, status_code=400)
                            if not any(item_str.startswith(prefix) for prefix in allowed_animation_prefixes):
                                return JSONResponse(content={'success': False, 'error': '待机动作路径必须以 /user_vrm/animation/ 或 /static/vrm/animation/ 开头'}, status_code=400)
                        set_reserved(characters['猫娘'][name], 'avatar', 'vrm', 'idle_animation', [str(x).strip() for x in idle_list])

                logger.debug(f"已保存角色 {name} 的Live3D(VRM)模型 {vrm_model}")
            elif mmd_model:
                # Live3D + MMD：更新当前激活的 MMD 配置，保留 VRM 配置便于切回
                set_reserved(characters['猫娘'][name], 'avatar', 'live3d_sub_type', 'mmd')
                set_reserved(characters['猫娘'][name], 'avatar', 'mmd', 'model_path', mmd_model)
                active_model_binding_path = mmd_model

                # 处理 MMD 动画
                if 'mmd_animation' in data:
                    if mmd_animation is None or mmd_animation == '':
                        set_reserved(characters['猫娘'][name], 'avatar', 'mmd', 'animation', None)
                    else:
                        mmd_animation_str = str(mmd_animation).strip()
                        if '://' in mmd_animation_str or mmd_animation_str.startswith('data:'):
                            return JSONResponse(content={'success': False, 'error': 'MMD动画路径不能包含URL方案'}, status_code=400)
                        if '..' in mmd_animation_str:
                            return JSONResponse(content={'success': False, 'error': 'MMD动画路径不能包含路径遍历（..）'}, status_code=400)
                        allowed_mmd_anim_prefixes = ['/user_mmd/animation/', '/static/mmd/animation/']
                        if not any(mmd_animation_str.startswith(prefix) for prefix in allowed_mmd_anim_prefixes):
                            return JSONResponse(content={'success': False, 'error': 'MMD动画路径必须以 /user_mmd/animation/ 或 /static/mmd/animation/ 开头'}, status_code=400)
                        set_reserved(characters['猫娘'][name], 'avatar', 'mmd', 'animation', mmd_animation_str)

                if 'mmd_idle_animation' in data:
                    if mmd_idle_animation is None or mmd_idle_animation == '' or mmd_idle_animation == []:
                        set_reserved(characters['猫娘'][name], 'avatar', 'mmd', 'idle_animation', [])
                    elif isinstance(mmd_idle_animation, str):
                        mmd_idle_list = [mmd_idle_animation]
                    elif isinstance(mmd_idle_animation, list):
                        mmd_idle_list = mmd_idle_animation
                    else:
                        return JSONResponse(content={'success': False, 'error': 'mmd_idle_animation must be a string or list of strings'}, status_code=400)
                    if isinstance(mmd_idle_animation, (str, list)) and mmd_idle_animation:
                        allowed_mmd_anim_prefixes = ['/user_mmd/animation/', '/static/mmd/animation/']
                        for item in mmd_idle_list:
                            mmd_idle_str = str(item).strip()
                            if '://' in mmd_idle_str or mmd_idle_str.startswith('data:'):
                                return JSONResponse(content={'success': False, 'error': 'MMD待机动作路径不能包含URL方案'}, status_code=400)
                            if '..' in mmd_idle_str:
                                return JSONResponse(content={'success': False, 'error': 'MMD待机动作路径不能包含路径遍历（..）'}, status_code=400)
                            if not any(mmd_idle_str.startswith(prefix) for prefix in allowed_mmd_anim_prefixes):
                                return JSONResponse(content={'success': False, 'error': 'MMD待机动作路径必须以 /user_mmd/animation/ 或 /static/mmd/animation/ 开头'}, status_code=400)
                        set_reserved(characters['猫娘'][name], 'avatar', 'mmd', 'idle_animation', [str(x).strip() for x in mmd_idle_list])

                logger.debug(f"已保存角色 {name} 的Live3D(MMD)模型 {mmd_model}")

            current_asset_source, current_asset_source_id = _derive_model_asset_binding(
                active_model_binding_path,
                item_id=str(item_id or ""),
            )
            set_reserved(characters['猫娘'][name], 'avatar', 'asset_source_id', current_asset_source_id)
            set_reserved(
                characters['猫娘'][name],
                'avatar',
                'asset_source',
                current_asset_source or 'local_imported',
            )
        elif model_type_str == 'pngtuber':
            set_reserved(characters['猫娘'][name], 'avatar', 'model_type', 'pngtuber')
            set_reserved(characters['猫娘'][name], 'avatar', 'live3d_sub_type', '')
            set_reserved(characters['猫娘'][name], 'avatar', 'pngtuber', pngtuber_payload)
            pngtuber_binding_path = str(
                idle_image
                or pngtuber_payload.get('layered_metadata')
                or ''
            ).strip()
            pngtuber_binding_item_id = str(item_id or "").strip()
            if not pngtuber_binding_path.startswith('/workshop/'):
                pngtuber_binding_item_id = ''
            current_asset_source, current_asset_source_id = _derive_model_asset_binding(
                pngtuber_binding_path,
                item_id=pngtuber_binding_item_id,
            )
            set_reserved(characters['猫娘'][name], 'avatar', 'asset_source_id', current_asset_source_id)
            set_reserved(characters['猫娘'][name], 'avatar', 'asset_source', current_asset_source or 'local_imported')
            logger.debug(f"已保存角色 {name} 的PNGTuber配置")
        else:
            # 更新Live2D模型设置，同时保存item_id（如果有）
            live2d_model_path, resolved_item_id, resolved_asset_source = _resolve_live2d_model_binding(
                live2d_model,
                item_id=str(item_id or ""),
            )
            set_reserved(
                characters['猫娘'][name],
                'avatar',
                'live2d',
                'model_path',
                live2d_model_path,
            )
            set_reserved(characters['猫娘'][name], 'avatar', 'model_type', 'live2d')

            if 'live2d_idle_animation' in data:
                live2d_idle_animation = data.get('live2d_idle_animation')
                logger.info(f"[Live2D Save] 收到 live2d_idle_animation 请求: {live2d_idle_animation}")

                if live2d_idle_animation is None:
                    set_reserved(characters['猫娘'][name], 'avatar', 'live2d', 'idle_animation', None)
                    logger.info(f"[Live2D Save] 已清空 idle_animation")
                elif isinstance(live2d_idle_animation, str):
                    live2d_idle_str = live2d_idle_animation.strip()
                    if not live2d_idle_str:
                        set_reserved(characters['猫娘'][name], 'avatar', 'live2d', 'idle_animation', None)
                        logger.info(f"[Live2D Save] 已清空 idle_animation")
                    else:
                        if '://' in live2d_idle_str or live2d_idle_str.startswith('data:'):
                            return JSONResponse(content={'success': False, 'error': 'Live2D待机动作路径不能包含URL方案'}, status_code=400)
                        if '..' in live2d_idle_str:
                            return JSONResponse(content={'success': False, 'error': 'Live2D待机动作路径不能包含路径遍历（..）'}, status_code=400)
                        if live2d_idle_str.startswith('/') or live2d_idle_str.startswith('\\') or re.match(r'^[A-Za-z]:', live2d_idle_str):
                            return JSONResponse(content={'success': False, 'error': 'Live2D待机动作路径必须是相对路径，不能是绝对路径'}, status_code=400)
                        if not live2d_idle_str.lower().endswith('.motion3.json'):
                            return JSONResponse(content={'success': False, 'error': 'Live2D待机动作必须是 .motion3.json 文件'}, status_code=400)
                        set_reserved(characters['猫娘'][name], 'avatar', 'live2d', 'idle_animation', live2d_idle_str)
                        logger.info(f"[Live2D Save] 已保存 idle_animation: {live2d_idle_str}")
                else:
                    return JSONResponse(content={'success': False, 'error': 'live2d_idle_animation 必须是字符串或 null'}, status_code=400)
            else:
                logger.info(f"[Live2D Save] 请求中未包含 live2d_idle_animation 字段, data keys: {list(data.keys())}")

            if resolved_item_id:
                set_reserved(characters['猫娘'][name], 'avatar', 'asset_source_id', str(resolved_item_id))
                set_reserved(characters['猫娘'][name], 'avatar', 'asset_source', 'steam_workshop')
                logger.debug(f"已保存角色 {name} 的模型 {live2d_model} 和item_id {resolved_item_id}")
            else:
                set_reserved(characters['猫娘'][name], 'avatar', 'asset_source_id', '')
                set_reserved(characters['猫娘'][name], 'avatar', 'asset_source', resolved_asset_source or 'local_imported')
                logger.debug(f"已保存角色 {name} 的模型 {live2d_model}，asset_source={resolved_asset_source or 'local_imported'}")

        # 保存配置
        await _config_manager.asave_characters(characters)
        # Fast path：只刷新被编辑角色的 session_manager（avatar 配置），不遍历其它 N-1 个。
        init_one_catgirl = get_init_one_catgirl()
        await init_one_catgirl(name, is_new=False)


        if model_type_str == 'live3d':
            active_model = vrm_model or mmd_model
            sub_type = 'VRM' if vrm_model else 'MMD'
            message = f'已更新角色 {name} 的Live3D({sub_type})模型为 {active_model}'
        elif model_type_str == 'pngtuber':
            message = f'已更新角色 {name} 的PNGTuber配置'
        else:
            message = f'已更新角色 {name} 的Live2D模型为 {live2d_model}'

        return JSONResponse(content={
            'success': True,
            'message': message
        })

    except MaintenanceModeError:
        raise
    except Exception as e:
        logger.exception("更新角色模型设置失败")
        return JSONResponse(content={
            'success': False,
            'error': str(e)
        })


@router.patch('/catgirl/{name}/touch_set')
async def update_catgirl_touch_set(name: str, request: Request):
    """Fully replace the touch animation config of the specified catgirl's current model.

    Request body format:
    {
        "model_name": "model name",
        "touch_set": {
            "default": {"motions": [], "expressions": []},
            "HitArea1": {"motions": ["motion1"], "expressions": ["exp1"]}
        }
    }
    """
    try:
        data = await request.json()

        model_name = data.get('model_name')
        touch_set_data = data.get('touch_set')

        if not isinstance(model_name, str) or not model_name.strip():
            return JSONResponse(
                content={'success': False, 'error': 'model_name 必须是非空字符串'},
                status_code=400
            )
        model_name = model_name.strip()

        if touch_set_data is None:
            return JSONResponse(
                content={'success': False, 'error': '缺少 touch_set 参数'},
                status_code=400
            )

        if not isinstance(touch_set_data, dict):
            return JSONResponse(
                content={'success': False, 'error': 'touch_set 必须是对象'},
                status_code=400
            )

        _config_manager = get_config_manager()
        characters = await _config_manager.aload_characters()

        if '猫娘' not in characters or name not in characters['猫娘']:
            return JSONResponse(
                content={'success': False, 'error': '角色不存在'},
                status_code=404
            )

        existing_touch_set = get_reserved(characters['猫娘'][name], 'touch_set', default={})

        if not existing_touch_set:
            existing_touch_set = {}

        existing_touch_set[model_name] = touch_set_data

        set_reserved(characters['猫娘'][name], 'touch_set', existing_touch_set)
        await _config_manager.asave_characters(characters)

        # Fast path：只刷新被编辑角色的 session_manager（touch_set），不遍历其它 N-1 个。
        init_one_catgirl = get_init_one_catgirl()
        if init_one_catgirl:
            await init_one_catgirl(name, is_new=False)

        logger.debug(f"已更新角色 {name} 模型 {model_name} 的触摸配置")

        return JSONResponse(content={
            'success': True,
            'message': f'已更新角色 {name} 的触摸配置',
            'touch_set': existing_touch_set
        })

    except Exception as e:
        logger.exception("更新触摸配置失败")
        return JSONResponse(content={
            'success': False,
            'error': str(e)
        }, status_code=500)


@router.put('/catgirl/{name}/lighting')
async def update_catgirl_lighting(name: str, request: Request):
    """Update the specified catgirl's VRM lighting config.

    Args:
        name: character name
        request: body containing lighting (dict) and an optional apply_runtime (bool);
                 apply_runtime can also be passed as a query param, which takes precedence
    """
    try:
        data = await request.json()
        lighting = data.get('lighting')

        apply_runtime = data.get('apply_runtime', False)
        query_params = request.query_params
        if 'apply_runtime' in query_params:
            apply_runtime = query_params.get('apply_runtime', '').lower() in ('true', '1', 'yes')

        _config_manager = get_config_manager()
        characters = await _config_manager.aload_characters()

        if '猫娘' not in characters or name not in characters['猫娘']:
            return JSONResponse(content={
                'success': False,
                'error': '角色不存在'
            }, status_code=404)

        model_type = get_reserved(
            characters['猫娘'][name],
            'avatar',
            'model_type',
            default='live2d',
            legacy_keys=('model_type',),
        )
        # 统一做 .lower() 处理，避免大小写/空值导致误判
        model_type_normalized = str(model_type).lower() if model_type else 'live2d'
        if model_type_normalized not in ('vrm', 'live3d'):
            logger.warning(f"角色 {name} 不是VRM/Live3D模型，但仍保存打光配置")

        from config import get_default_vrm_lighting
        existing_lighting = get_reserved(
            characters['猫娘'][name],
            'avatar',
            'vrm',
            'lighting',
            default=None,
            legacy_keys=('lighting',),
        )
        if isinstance(existing_lighting, dict):
            base_lighting = existing_lighting
        else:
            base_lighting = get_default_vrm_lighting()

        if not isinstance(lighting, dict):
            return JSONResponse(content={
                'success': False,
                'error': 'lighting 必须是对象'
            }, status_code=400)

        lighting = {**base_lighting, **lighting}

        from config import VRM_LIGHTING_RANGES
        lighting_ranges = VRM_LIGHTING_RANGES

        for key, (min_val, max_val) in lighting_ranges.items():
            if key not in lighting:
                return JSONResponse(content={
                    'success': False,
                    'error': f'缺少打光参数: {key}'
                }, status_code=400)

            val = lighting[key]
            if not isinstance(val, (int, float)) or not (min_val <= val <= max_val):
                return JSONResponse(content={
                    'success': False,
                    'error': f'打光参数 {key} 超出范围 ({min_val}-{max_val})'
                }, status_code=400)


        set_reserved(
            characters['猫娘'][name],
            'avatar',
            'vrm',
            'lighting',
            {key: float(lighting[key]) for key in lighting_ranges.keys()},
        )



        logger.info(
            "已保存角色 %s 的打光配置: %s",
            name,
            get_reserved(characters['猫娘'][name], 'avatar', 'vrm', 'lighting', default=None),
        )

        await _config_manager.asave_characters(characters)

        if apply_runtime:
            # Fast path：只刷新被编辑角色的 session_manager（lighting），不遍历其它 N-1 个。
            init_one_catgirl = get_init_one_catgirl()
            if init_one_catgirl:
                await init_one_catgirl(name, is_new=False)
                logger.info(f"已应用到运行时（角色 {name} 的打光配置）")
        else:
            logger.debug("跳过运行时刷新（apply_runtime=False），配置已保存到磁盘，需要刷新页面或调用重载才能生效")

        if apply_runtime:
            message = f'已保存角色 {name} 的打光配置并已应用到运行时'
        else:
            message = f'已保存角色 {name} 的打光配置到磁盘（需要刷新页面或调用重载才能生效）'

        return JSONResponse(content={
            'success': True,
            'message': message,
            'applied_runtime': apply_runtime,
            'needs_reload': not apply_runtime
        })

    except Exception as e:
        logger.error(f"保存打光配置失败: {e}")
        return JSONResponse(content={
            'success': False,
            'error': str(e)
        }, status_code=500)


@router.put('/catgirl/{name}/mmd_settings')
async def update_catgirl_mmd_settings(name: str, request: Request):
    """Update the specified character's MMD model settings (lighting, rendering, physics, mouse tracking)."""
    def _to_bool(val):
        if isinstance(val, bool):
            return val
        if isinstance(val, str):
            return val.lower() in ('true', '1', 'yes')
        return bool(val)

    try:
        data = await request.json()

        _config_manager = get_config_manager()
        characters = await _config_manager.aload_characters()

        if '猫娘' not in characters or name not in characters['猫娘']:
            return JSONResponse(content={
                'success': False,
                'error': '角色不存在'
            }, status_code=404)

        from config import (
            get_default_mmd_settings,
            MMD_LIGHTING_RANGES,
            MMD_RENDERING_RANGES,
            MMD_PHYSICS_RANGES,
            MMD_CURSOR_FOLLOW_RANGES,
        )

        defaults = get_default_mmd_settings()

        # --- 光照 ---
        if 'lighting' in data and isinstance(data['lighting'], dict):
            lighting = {**defaults['lighting'], **data['lighting']}
            for key, (min_val, max_val) in MMD_LIGHTING_RANGES.items():
                if key in lighting:
                    val = lighting[key]
                    if isinstance(val, (int, float)):
                        lighting[key] = max(min_val, min(max_val, float(val)))
            set_reserved(characters['猫娘'][name], 'avatar', 'mmd', 'lighting', lighting)

        # --- 渲染 ---
        if 'rendering' in data and isinstance(data['rendering'], dict):
            rendering = {**defaults['rendering'], **data['rendering']}
            for key, (min_val, max_val) in MMD_RENDERING_RANGES.items():
                if key in rendering:
                    val = rendering[key]
                    if isinstance(val, (int, float)):
                        rendering[key] = max(min_val, min(max_val, float(val)))
            if 'outline' in rendering:
                rendering['outline'] = _to_bool(rendering['outline'])
            set_reserved(characters['猫娘'][name], 'avatar', 'mmd', 'rendering', rendering)

        # --- 物理 ---
        if 'physics' in data and isinstance(data['physics'], dict):
            physics = {**defaults['physics'], **data['physics']}
            if 'enabled' in physics:
                physics['enabled'] = _to_bool(physics['enabled'])
            for key, (min_val, max_val) in MMD_PHYSICS_RANGES.items():
                if key in physics:
                    val = physics[key]
                    if isinstance(val, (int, float)):
                        physics[key] = max(min_val, min(max_val, float(val)))
            set_reserved(characters['猫娘'][name], 'avatar', 'mmd', 'physics', physics)

        # --- 鼠标跟踪 ---
        # 前端发送 camelCase（cursorFollow），兼容 snake_case（cursor_follow）
        cursor_follow_data = data.get('cursorFollow') or data.get('cursor_follow')
        if cursor_follow_data and isinstance(cursor_follow_data, dict):
            cursor_follow = {**defaults['cursor_follow'], **cursor_follow_data}
            for key, (min_val, max_val) in MMD_CURSOR_FOLLOW_RANGES.items():
                if key in cursor_follow:
                    val = cursor_follow[key]
                    if isinstance(val, (int, float)):
                        cursor_follow[key] = max(min_val, min(max_val, float(val)))
            if 'enabled' in cursor_follow:
                cursor_follow['enabled'] = _to_bool(cursor_follow['enabled'])
            set_reserved(characters['猫娘'][name], 'avatar', 'mmd', 'cursor_follow', cursor_follow)

        await _config_manager.asave_characters(characters)

        logger.info("已保存角色 %s 的MMD模型设置", name)
        return JSONResponse(content={
            'success': True,
            'message': f'已保存角色 {name} 的MMD模型设置'
        })

    except Exception as e:
        logger.error(f"保存MMD设置失败: {e}")
        return JSONResponse(content={
            'success': False,
            'error': str(e)
        }, status_code=500)


@router.get('/catgirl/{name}/mmd_settings')
async def get_catgirl_mmd_settings(name: str):
    """Get the specified character's MMD model settings."""
    try:
        _config_manager = get_config_manager()
        characters = await _config_manager.aload_characters()

        if '猫娘' not in characters or name not in characters['猫娘']:
            return JSONResponse(content={
                'success': False,
                'error': '角色不存在'
            }, status_code=404)

        from config import get_default_mmd_settings
        defaults = get_default_mmd_settings()

        lighting = get_reserved(characters['猫娘'][name], 'avatar', 'mmd', 'lighting', default=None)
        rendering = get_reserved(characters['猫娘'][name], 'avatar', 'mmd', 'rendering', default=None)
        physics = get_reserved(characters['猫娘'][name], 'avatar', 'mmd', 'physics', default=None)
        cursor_follow = get_reserved(characters['猫娘'][name], 'avatar', 'mmd', 'cursor_follow', default=None)

        return JSONResponse(content={
            'success': True,
            'settings': {
                'lighting': lighting if isinstance(lighting, dict) else defaults['lighting'],
                'rendering': rendering if isinstance(rendering, dict) else defaults['rendering'],
                'physics': physics if isinstance(physics, dict) else defaults['physics'],
                # 使用 camelCase 与前端保持一致
                'cursorFollow': cursor_follow if isinstance(cursor_follow, dict) else defaults['cursor_follow'],
            }
        })

    except Exception as e:
        logger.error(f"获取MMD设置失败: {e}")
        return JSONResponse(content={
            'success': False,
            'error': str(e)
        }, status_code=500)


@router.put('/catgirl/voice_id/{name}')
async def update_catgirl_voice_id(name: str, request: Request):
    data = await request.json()
    if not data:
        return JSONResponse({'success': False, 'error': '无数据'}, status_code=400)
    if 'voice_id' not in data:
        logger.debug("猫娘 %s 的 voice_id 更新请求缺少字段，按无变更处理", name)
        return {"success": True, "session_restarted": False, "voice_id_changed": False}
    _config_manager = get_config_manager()
    session_manager = get_session_manager()
    characters = await _config_manager.aload_characters()
    if name not in characters.get('猫娘', {}):
        return JSONResponse({'success': False, 'error': '猫娘不存在'}, status_code=404)
    voice_id = str(data.get('voice_id') or '').strip()
    old_voice_id = read_legacy_voice_id(get_reserved(
        characters['猫娘'][name],
        'voice_id',
        default='',
        legacy_keys=('voice_id',)
    ))

    # 幂等保护：提交同值时直接返回，避免无实际变更触发会话重置。
    if old_voice_id == voice_id:
        logger.info("猫娘 %s 的 voice_id 未变化，跳过会话重置流程", name)
        return {"success": True, "session_restarted": False, "voice_id_changed": False}

    if _is_current_catgirl_voice_session_starting(name, characters, session_manager):
        return _voice_session_starting_response()

    # 验证voice_id是否在voice_storage中
    if not _config_manager.validate_voice_id(voice_id):
        voices = _config_manager.get_voices_for_current_api()
        available_voices = list(voices.keys())
        return JSONResponse({
            'success': False,
            'error': f'voice_id "{voice_id}" 在当前API的音色库中不存在',
            'available_voices': available_voices
        }, status_code=400)

    # 用户设音色：惰性迁移这一条到结构对象（用到哪条迁哪条，见 voice_id_to_storage_value）。
    set_reserved(characters['猫娘'][name], 'voice_id', _config_manager.voice_id_to_storage_value(voice_id))
    await _config_manager.asave_characters(characters)

    # 如果是当前活跃的猫娘，需要先通知前端，再关闭session
    is_current_catgirl = (name == characters.get('当前猫娘', ''))
    session_ended = False

    if is_current_catgirl and name in session_manager:
        # 检查是否有活跃的session
        if session_manager[name].is_active:
            logger.info(f"检测到 {name} 的voice_id已更新（{old_voice_id} -> {voice_id}），准备结束当前语音会话...")

            # 1. 通知前端按 session 结束路径收口，避免 Electron 为音色切换整页重载。
            notify_session_ended = getattr(session_manager[name], "send_session_ended_by_server", None)
            if callable(notify_session_ended):
                await notify_session_ended()

            # 2. 立刻关闭session（这会断开WebSocket）
            try:
                await session_manager[name].end_session(by_server=True)
                session_ended = True
                logger.info(f"{name} 的session已结束")
            except Exception as e:
                logger.error(f"结束session时出错: {e}")
            # 切音色后，前一会话累计的失败计数 / 熔断不再适用：
            # 用户的下一次 start_session 应是全新尝试，否则
            # 这条 SessionManager 实例还会被旧失败计数 / 熔断继续静默拦截。
            session_manager[name].reset_session_start_circuit()

    # Fast path：只刷新被编辑角色的 session_manager（voice_id），不遍历其它 N-1 个。
    # 非当前角色分支也要显式刷 session_manager[name]：以前靠下次 switch 的全量 init 顺带
    # rescue，但 set_current_catgirl 已切到 switch_current_catgirl_fast，rescue 不再发生，
    # 必须在这里就把 voice_id 写进 session_manager[name]（init_one_catgirl 只写该 key，
    # 不会影响当前 session）。
    init_one_catgirl = get_init_one_catgirl()
    await init_one_catgirl(name, is_new=False)
    if is_current_catgirl:
        logger.info("配置已重新加载，新的voice_id已生效")
    else:
        logger.info(f"非当前猫娘 {name} 的音色已更新并同步到 session_manager")

    return {"success": True, "session_restarted": session_ended, "voice_id_changed": True}

@router.get('/catgirl/{name}/voice_mode_status')
async def get_catgirl_voice_mode_status(name: str):
    """Check whether the specified character is in voice mode."""
    if _validate_existing_character_path_name(name):
        return _json_no_store_response({
            'is_voice_mode': False,
            'is_current': False,
            'is_active': False,
            'is_starting': False,
            'is_voice_starting': False,
            'invalid_name': True,
        })
    _config_manager = get_config_manager()
    session_manager = get_session_manager()
    characters = await _config_manager.aload_characters()
    is_current = characters.get('当前猫娘') == name

    if name not in session_manager:
        return _json_no_store_response({
            'is_voice_mode': False,
            'is_current': is_current,
            'is_active': False,
            'is_starting': False,
            'is_voice_starting': False,
        })

    mgr = session_manager[name]
    is_active = mgr.is_active if mgr else False
    is_starting = bool(getattr(mgr, 'is_starting', False)) if mgr else False
    is_audio_starting = _is_current_catgirl_voice_session_starting(name, characters, session_manager)

    is_voice_mode = is_audio_starting
    if is_active and mgr:
        # 检查是否是语音模式（通过session类型判断）
        from main_logic.omni_realtime_client import OmniRealtimeClient
        is_voice_mode = is_voice_mode or bool(
            getattr(mgr, 'input_mode', '') == 'audio'
            or (mgr.session and isinstance(mgr.session, OmniRealtimeClient))
        )

    return _json_no_store_response({
        'is_voice_mode': is_voice_mode,
        'is_current': is_current,
        'is_active': is_active,
        'is_starting': is_starting,
        'is_voice_starting': is_audio_starting,
    })


@router.post('/catgirl/{old_name}/rename')
async def rename_catgirl(old_name: str, request: Request):
    _config_manager = get_config_manager()
    session_manager = get_session_manager()
    try:
        data = await request.json()
    except Exception as e:
        logger.warning(f"解析猫娘重命名请求体失败: {e}")
        return JSONResponse({'success': False, 'error': '请求体必须是合法的JSON格式'}, status_code=400)
    new_name = data.get('new_name') if data else None
    if not new_name:
        return JSONResponse({'success': False, 'error': '新档案名不能为空'}, status_code=400)

    new_name = str(new_name).strip()
    err = _validate_profile_name(new_name)
    if err:
        return JSONResponse({'success': False, 'error': err.replace('档案名', '新档案名')}, status_code=400)
    characters = await _config_manager.aload_characters()
    if old_name not in characters.get('猫娘', {}):
        return JSONResponse({'success': False, 'error': '原猫娘不存在'}, status_code=404)
    if new_name in characters['猫娘']:
        return JSONResponse({'success': False, 'error': '新档案名已存在'}, status_code=400)

    # 如果当前猫娘是被重命名的猫娘，先缓存 WebSocket，
    # 只有在持久化和重载全部成功后才发送通知，避免前端先切换到未提交状态。
    is_current_catgirl = characters.get('当前猫娘') == old_name
    rename_notification_ws = None
    rename_notification_message = None

    # 检查当前角色是否有活跃的语音session
    if is_current_catgirl and old_name in session_manager:
        mgr = session_manager[old_name]
        if mgr.is_active:
            # 检查是否是语音模式（通过session类型判断）
            from main_logic.omni_realtime_client import OmniRealtimeClient
            is_voice_mode = mgr.session and isinstance(mgr.session, OmniRealtimeClient)

            if is_voice_mode:
                return JSONResponse({
                    'success': False,
                    'error': '语音状态下无法修改角色名称，请先停止语音对话后再修改'
                }, status_code=400)
    if is_current_catgirl and old_name in session_manager:
        rename_notification_ws = session_manager[old_name].websocket
        if rename_notification_ws:
            rename_notification_message = json.dumps({
                "type": "catgirl_switched",
                "new_catgirl": new_name,
                "old_catgirl": old_name
            })

    assert_cloudsave_writable(
        _config_manager,
        operation="rename",
        target=f"characters/{old_name} -> {new_name}",
    )

    released_memory_handle = await release_memory_server_character(
        old_name,
        reason=f"角色重命名前释放 SQLite 句柄: {old_name} -> {new_name}",
    )
    if not released_memory_handle:
        logger.warning("角色重命名前释放记忆服务器句柄失败，已阻止重命名: %s -> %s", old_name, new_name)
        return JSONResponse(
            {
                "success": False,
                "code": "MEMORY_SERVER_RELEASE_FAILED",
                "error": "释放角色记忆句柄失败，已阻止重命名，请稍后重试",
                "memory_server_released": False,
            },
            status_code=503,
        )

    characters_snapshot = copy.deepcopy(characters)
    memory_targets = list_character_memory_paths(_config_manager, old_name)
    memory_targets.extend(list_character_memory_paths(_config_manager, new_name))
    memory_targets.append(Path(_config_manager.memory_dir) / new_name)
    # 卡面文件纳入 snapshot，使迁移失败也能回滚
    old_face = _config_manager.card_faces_dir / f"{old_name}.png"
    new_face = _config_manager.card_faces_dir / f"{new_name}.png"
    old_meta = _config_manager.card_face_meta_path(old_name)
    new_meta = _config_manager.card_face_meta_path(new_name)
    memory_targets.append(old_face)
    memory_targets.append(new_face)
    memory_targets.append(old_meta)
    memory_targets.append(new_meta)
    memory_server_reloaded = False

    with _create_character_operation_backup_dir(_config_manager, "neko-rename-character-") as temp_dir:
        memory_snapshot_records = await asyncio.to_thread(
            _snapshot_existing_paths, memory_targets, Path(temp_dir)
        )
        try:
            rename_character_memory_storage(_config_manager, old_name, new_name)

            # 重命名角色真源
            characters['猫娘'][new_name] = characters['猫娘'].pop(old_name)
            _append_profile_rename_event(characters['猫娘'][new_name], old_name, new_name)
            # 如果当前猫娘是被重命名的猫娘，也需要更新
            if is_current_catgirl:
                characters['当前猫娘'] = new_name
            await _config_manager.asave_characters(characters)

            # Fast path：移除旧名 + 以新名启动一个 catgirl slot。
            # 等价于"删除旧 + 新增新"，不遍历其它 N-1 个。
            remove_one_catgirl = get_remove_one_catgirl()
            init_one_catgirl = get_init_one_catgirl()
            await remove_one_catgirl(old_name)
            await init_one_catgirl(new_name, is_new=True)

            # 迁移卡面 PNG 与 sidecar JSON（纳入同一事务）
            from datetime import datetime as _dt
            _ts = _dt.now().strftime('%Y%m%d%H%M%S')
            if old_face.exists():
                if new_face.exists():
                    backup_face = _config_manager.card_faces_dir / f"{new_name}.png.conflict-{_ts}.bak"
                    await asyncio.to_thread(new_face.rename, backup_face)
                    logger.info(f"[重命名卡面] 冲突备份: {new_face} -> {backup_face}")
                await asyncio.to_thread(old_face.rename, new_face)
                logger.info(f"[重命名卡面] 已迁移: {old_face} -> {new_face}")
            if old_meta.exists():
                if new_meta.exists():
                    backup_meta = _config_manager.card_face_meta_path(f"{new_name}.conflict-{_ts}.bak")
                    await asyncio.to_thread(new_meta.rename, backup_meta)
                    logger.info(f"[重命名卡面元数据] 冲突备份: {new_meta} -> {backup_meta}")
                await asyncio.to_thread(old_meta.rename, new_meta)
                logger.info(f"[重命名卡面元数据] 已迁移: {old_meta} -> {new_meta}")

            memory_server_reloaded = await notify_memory_server_reload(
                reason=f"角色重命名: {old_name} -> {new_name}",
            )
            if not memory_server_reloaded:
                rollback_error = await _rollback_character_operation(
                    _config_manager,
                    characters_snapshot=characters_snapshot,
                    memory_snapshot_records=memory_snapshot_records,
                    reason=f"角色重命名回滚（memory_server 重载失败）: {old_name} -> {new_name}",
                )
                logger.error(
                    "重命名角色后 notify_memory_server_reload 返回 False，已尝试回滚: %s -> %s",
                    old_name,
                    new_name,
                )
                error_message = "重命名角色失败: notify_memory_server_reload returned False"
                if rollback_error:
                    error_message = f"{error_message}; 回滚失败: {rollback_error}"
                return JSONResponse(
                    {
                        "success": False,
                        "error": error_message,
                    },
                    status_code=500,
                )

        except MaintenanceModeError as exc:
            rollback_error = await _rollback_character_operation(
                _config_manager,
                characters_snapshot=characters_snapshot,
                memory_snapshot_records=memory_snapshot_records,
                reason=f"维护模式：角色重命名回滚 {old_name} -> {new_name}",
            )
            if rollback_error:
                raise exc from RuntimeError(rollback_error)
            raise
        except Exception as exc:
            rollback_error = await _rollback_character_operation(
                _config_manager,
                characters_snapshot=characters_snapshot,
                memory_snapshot_records=memory_snapshot_records,
                reason=f"角色重命名回滚: {old_name} -> {new_name}",
            )
            logger.exception("重命名角色失败，已尝试回滚: %s -> %s", old_name, new_name)
            error_message = f"重命名角色失败: {exc}"
            if rollback_error:
                error_message = f"{error_message}; 回滚失败: {rollback_error}"
            return JSONResponse({"success": False, "error": error_message}, status_code=500)

    # 数据更新+重载+卡面迁移完成后再通知前端
    if memory_server_reloaded and rename_notification_ws and rename_notification_message:
        try:
            await rename_notification_ws.send_text(rename_notification_message)
            logger.info(f"已向 {old_name} 发送重命名通知")
        except Exception as e:
            logger.warning(f"发送重命名通知给 {old_name} 失败: {e}")

    pending_rename_ok = True
    pending_rename_error = ""
    try:
        await rename_new_character_greeting_pending(_config_manager, old_name, new_name)
    except Exception as exc:
        pending_rename_ok = False
        pending_rename_error = str(exc)
        logger.exception("rename new character greeting pending failed: %s -> %s", old_name, new_name)

    result = {
        "success": True,
        "memory_renamed": True,
        "memory_server_reloaded": memory_server_reloaded,
    }
    if not pending_rename_ok:
        result["partial_success"] = True
        result["pending_rename_ok"] = False
        result["pending_rename_failed"] = True
        result["pending_rename_error"] = pending_rename_error
    return result


@router.post('/catgirl/{name}/unregister_voice')
async def unregister_voice(name: str):
    """Unregister the catgirl's voice."""
    try:
        _config_manager = get_config_manager()
        session_manager = get_session_manager()
        characters = await _config_manager.aload_characters()
        if name not in characters.get('猫娘', {}):
            return JSONResponse({'success': False, 'error': '猫娘不存在'}, status_code=404)

        # 检查是否已有voice_id
        old_voice_id = read_legacy_voice_id(get_reserved(characters['猫娘'][name], 'voice_id', default='', legacy_keys=('voice_id',)))
        if not old_voice_id:
            return JSONResponse({'success': False, 'error': 'TTS_VOICE_NOT_REGISTERED', 'code': 'TTS_VOICE_NOT_REGISTERED'}, status_code=400)

        if _is_current_catgirl_voice_session_starting(name, characters, session_manager):
            return _voice_session_starting_response()

        # COMPAT(v1->v2): 统一落到 _reserved.voice_id，旧平铺 voice_id 不再写入/删除。
        set_reserved(characters['猫娘'][name], 'voice_id', '')
        await _config_manager.asave_characters(characters)

        # 如果是当前活跃的猫娘，需要先通知前端，再关闭session
        is_current_catgirl = (name == characters.get('当前猫娘', ''))
        session_ended = False

        if is_current_catgirl and name in session_manager:
            if session_manager[name].is_active:
                logger.info(f"检测到 {name} 的voice_id已清空（{old_voice_id} -> ''），准备结束当前语音会话...")
                notify_session_ended = getattr(session_manager[name], "send_session_ended_by_server", None)
                if callable(notify_session_ended):
                    await notify_session_ended()
                try:
                    await session_manager[name].end_session(by_server=True)
                    session_ended = True
                    logger.info(f"{name} 的session已结束")
                except Exception as e:
                    logger.error(f"结束session时出错: {e}")
                # 与 set_voice_id 路径对偶：清掉前一会话的失败计数 / 熔断，
                # 否则下一次 start_session 会被旧熔断静默拦截。
                session_manager[name].reset_session_start_circuit()

        # Fast path：只刷新被编辑角色的 session_manager（voice_id），不遍历其它 N-1 个。
        # 非当前角色分支也要走 init_one_catgirl：以前靠下次 switch 的全量 init 顺带 rescue，
        # 但 set_current_catgirl 已切到 switch_current_catgirl_fast，rescue 不再发生。
        init_one_catgirl = get_init_one_catgirl()
        await init_one_catgirl(name, is_new=False)

        logger.info(f"已解除猫娘 '{name}' 的声音注册")
        return {"success": True, "message": "声音注册已解除", "session_restarted": session_ended, "voice_id_changed": True}

    except Exception as e:
        logger.error(f"解除声音注册时出错: {e}")
        return JSONResponse({'success': False, 'error': f'解除注册失败: {str(e)}'}, status_code=500)

@router.get('/current_catgirl')
async def get_current_catgirl():
    """Get the name of the currently active catgirl."""
    _config_manager = get_config_manager()
    characters = await _config_manager.aload_characters()
    current_catgirl = characters.get('当前猫娘', '')
    return _json_no_store_response({'current_catgirl': current_catgirl})


@router.get('/persona-presets')
async def list_persona_presets_route(request: Request):
    return _json_no_store_response({
        "success": True,
        "presets": list_persona_presets(lang=_get_persona_request_language(request)),
    })


@router.get('/persona-onboarding-state')
async def get_persona_onboarding_state():
    config_manager = get_config_manager()
    state = await asyncio.to_thread(load_initial_personality_state, config_manager)
    return _json_no_store_response({
        "success": True,
        "state": state,
    })


@router.post('/persona-onboarding-state')
async def set_persona_onboarding_state(request: Request):
    payload, error_response = await _read_json_object_or_400(request)
    if error_response is not None:
        return error_response
    config_manager = get_config_manager()
    status_in = str((payload or {}).get("status") or "").strip()
    state = await asyncio.to_thread(
        mark_initial_personality_state,
        status_in,
        config_manager=config_manager,
    )
    # Telemetry：onboarding 漏斗的关键节点。**用归一化后的 state["status"]**
    # 而非请求体原值 status_in：mark_initial_personality_state 会把状态收敛成
    # 小枚举（pending / completed / skipped 等），客户端可以传任意 status 字符串
    # 但存储 fallback 成 pending。直接用 raw 会让任意输入变成不同的
    # onboarding_step dim，污染 funnel 切片 + 吃 instrument key 预算（同
    # lanlan_name 教训：raw 客户端输入不进 dim）（Codex）。
    _norm_status = (state.get("status") if isinstance(state, dict) else None) or "unknown"
    try:
        from utils.instrument import event as _instr_event, counter as _instr_counter
        _instr_event("onboarding_step", status=str(_norm_status)[:32])
        _instr_counter("onboarding_step", status=str(_norm_status)[:32])
    except Exception:
        # 埋点失败绝不影响 onboarding endpoint —— 一条 telemetry 走丢比让
        # 用户卡在角色选择失败重要多了。日志也省，防 import 故障刷屏。
        pass
    return {
        "success": True,
        "state": state,
    }


@router.post('/persona-reselect-current')
async def request_current_character_persona_reselect():
    config_manager = get_config_manager()
    characters = await config_manager.aload_characters()
    current_character_name = str(characters.get('当前猫娘') or '').strip()
    if not current_character_name:
        return JSONResponse({'success': False, 'error': '当前没有可用角色'}, status_code=400)

    state = await asyncio.to_thread(
        mark_manual_personality_reselect,
        current_character_name,
        config_manager=config_manager,
    )
    return {
        "success": True,
        "state": state,
    }


@router.delete('/persona-reselect-current')
async def clear_current_character_persona_reselect():
    config_manager = get_config_manager()
    state = await asyncio.to_thread(
        clear_manual_personality_reselect,
        config_manager=config_manager,
    )
    return {
        "success": True,
        "state": state,
    }


@router.get('/character/{name}/persona-selection')
async def get_character_persona_selection(name: str):
    config_manager = get_config_manager()
    characters = await config_manager.aload_characters()
    character_payload = (characters.get('猫娘') or {}).get(name)
    if not isinstance(character_payload, dict):
        return JSONResponse({'success': False, 'error': '角色不存在'}, status_code=404)

    return _json_no_store_response({
        "success": True,
        "selection": _build_persona_selection_payload(character_payload),
    })


@router.put('/character/{name}/persona-selection')
async def update_character_persona_selection(name: str, request: Request):
    payload, error_response = await _read_json_object_or_400(request)
    if error_response is not None:
        return error_response
    preset_id = str((payload or {}).get("preset_id") or "").strip()
    source = str((payload or {}).get("source") or "").strip()
    preset = get_persona_preset(preset_id)
    if preset is None:
        return JSONResponse({'success': False, 'error': '无效的人格预设'}, status_code=400)

    config_manager = get_config_manager()
    characters = await config_manager.aload_characters()
    character_payload = (characters.get('猫娘') or {}).get(name)
    if not isinstance(character_payload, dict):
        return JSONResponse({'success': False, 'error': '角色不存在'}, status_code=404)

    selected_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    request_language = _get_persona_payload_request_language(payload, request)
    override_payload = build_persona_override_payload(
        preset_id,
        source=source,
        selected_at=selected_at,
        lang=request_language,
    )
    if override_payload is None:
        return JSONResponse({'success': False, 'error': '无效的人格预设'}, status_code=400)

    previous_characters = copy.deepcopy(characters)
    set_reserved(character_payload, "persona_override", override_payload)
    _clear_stale_generated_persona_prompt(character_payload)
    try:
        await config_manager.asave_characters(characters)
        await _clear_character_recent_history(config_manager, name)
        session_manager = get_session_manager()
        is_current_catgirl = (name == characters.get('当前猫娘', ''))
        mgr = session_manager[name] if is_current_catgirl and name in session_manager else None
        expected_session = getattr(mgr, "session", None) if mgr and mgr.is_active else None
        if expected_session is not None:
            await send_reload_page_notice(mgr, "人格设定已更新，页面即将刷新")
            try:
                await mgr.end_session(by_server=True, expected_session=expected_session)
            except Exception as e:
                logger.error(f"结束 session 时出错: {e}")

        initialize_one_character = get_init_one_catgirl()
        await initialize_one_character(name, is_new=False)
        if source == "manual_reselect":
            await asyncio.to_thread(
                clear_manual_personality_reselect,
                config_manager=config_manager,
            )
        elif source == "onboarding":
            await asyncio.to_thread(
                mark_initial_personality_state,
                "completed",
                config_manager=config_manager,
            )
    except Exception:
        await _rollback_character_persona_selection_change(config_manager, previous_characters)
        raise

    return {
        "success": True,
        "selection": _build_persona_selection_payload(character_payload),
    }


@router.delete('/character/{name}/persona-selection')
async def clear_character_persona_selection(name: str):
    config_manager = get_config_manager()
    characters = await config_manager.aload_characters()
    character_payload = (characters.get('猫娘') or {}).get(name)
    if not isinstance(character_payload, dict):
        return JSONResponse({'success': False, 'error': '角色不存在'}, status_code=404)

    previous_characters = copy.deepcopy(characters)
    delete_reserved(character_payload, "persona_override")
    _clear_stale_generated_persona_prompt(character_payload)
    try:
        await config_manager.asave_characters(characters)
        await _clear_character_recent_history(config_manager, name)

        session_manager = get_session_manager()
        is_current_catgirl = (name == characters.get('当前猫娘', ''))
        mgr = session_manager[name] if is_current_catgirl and name in session_manager else None
        expected_session = getattr(mgr, "session", None) if mgr and mgr.is_active else None
        if expected_session is not None:
            await send_reload_page_notice(mgr, "人格设定已更新，页面即将刷新")
            try:
                await mgr.end_session(by_server=True, expected_session=expected_session)
            except Exception as e:
                logger.error(f"结束 session 时出错: {e}")

        initialize_one_character = get_init_one_catgirl()
        await initialize_one_character(name, is_new=False)
    except Exception:
        await _rollback_character_persona_selection_change(config_manager, previous_characters)
        raise

    return {
        "success": True,
        "selection": _build_persona_selection_payload(character_payload),
    }

@router.post('/current_catgirl')
async def set_current_catgirl(request: Request):
    """Set the currently active catgirl."""
    data = await request.json()
    catgirl_name = data.get('catgirl_name', '') if data else ''

    if not catgirl_name:
        return JSONResponse({'success': False, 'error': '猫娘名称不能为空'}, status_code=400)
    if _validate_existing_character_path_name(catgirl_name):
        return JSONResponse({'success': False, 'error': '猫娘名称无效'}, status_code=400)

    _config_manager = get_config_manager()
    session_manager = get_session_manager()
    characters = await _config_manager.aload_characters()
    if catgirl_name not in characters.get('猫娘', {}):
        return JSONResponse({'success': False, 'error': '指定的猫娘不存在'}, status_code=404)

    old_catgirl = characters.get('当前猫娘', '')

    # 检查当前角色是否有活跃的语音session
    if old_catgirl and old_catgirl in session_manager:
        mgr = session_manager[old_catgirl]
        if mgr.is_active:
            # 检查是否是语音模式（通过session类型判断）
            from main_logic.omni_realtime_client import OmniRealtimeClient
            is_voice_mode = mgr.session and isinstance(mgr.session, OmniRealtimeClient)

            if is_voice_mode:
                return JSONResponse({
                    'success': False,
                    'error': '语音状态下无法切换角色，请先停止语音对话后再切换'
                }, status_code=400)
    characters['当前猫娘'] = catgirl_name
    await _config_manager.asave_characters(characters)
    # Fast path：切换只改变 `当前猫娘` 字段，per-k 的 prompt / voice_id / thread 都不变，
    # 只需刷新 globals 即可。N=20 只猫娘时从 O(N) 降到 O(1)。
    switch_current_catgirl_fast = get_switch_current_catgirl_fast()
    await switch_current_catgirl_fast()

    # 角色卡切换会复用同一个前端猫爪面板和工具服务全局状态。
    # 这里先把旧状态归零，避免新角色刷新后继承上一张卡的开关状态。
    if old_catgirl != catgirl_name:
        await force_disable_agent_for_character_switch(catgirl_name, old_catgirl)

    # B8: if the previous character had an active game route, finalize it
    # immediately. Otherwise the heartbeat-based timeout (10-60s) would
    # leave a stale ``OmniOfflineClient`` consuming game events under the
    # outgoing character's name and keep the SessionManager takeover
    # muting the incoming character's ordinary chat output.
    if old_catgirl and old_catgirl != catgirl_name:
        try:
            from main_routers.game_router import finalize_game_routes_for_character
            finalized = await finalize_game_routes_for_character(old_catgirl)
            if finalized:
                logger.info(
                    "角色切换：已收尾 %d 个旧角色 %s 的游戏路由",
                    finalized,
                    old_catgirl,
                )
        except Exception as exc:
            # Swallow — character switch must not fail because of
            # game-route cleanup; the heartbeat sweep will eventually
            # clean up if this hook misses.
            logger.warning("角色切换游戏路由收尾失败: lanlan=%s err=%s", old_catgirl, exc)

    # 通过WebSocket通知所有连接的客户端
    # 使用session_manager中的websocket，但需要确保websocket已设置
    notification_count = 0
    logger.info(f"开始通知WebSocket客户端：猫娘从 {old_catgirl} 切换到 {catgirl_name}")

    message = json.dumps({
        "type": "catgirl_switched",
        "new_catgirl": catgirl_name,
        "old_catgirl": old_catgirl
    })

    # 并行通知所有 session_manager —— 每个 send_text 独立，per-mgr 失败时只清自己的 ws，
    # 串行版本里一个慢/卡的 ws 会拖累后面的通知。
    snapshot = list(session_manager.items())
    for lanlan_name, mgr in snapshot:
        logger.info(f"检查 {lanlan_name} 的WebSocket: websocket存在={mgr.websocket is not None}")

    async def _notify_one(lanlan_name, mgr):
        ws = mgr.websocket
        if not ws:
            return False
        try:
            await ws.send_text(message)
            logger.info(f"✅ 已通过WebSocket通知 {lanlan_name} 的连接：猫娘已从 {old_catgirl} 切换到 {catgirl_name}")
            return True
        except Exception as e:
            logger.warning(f"❌ 通知 {lanlan_name} 的连接失败: {e}")
            # 如果发送失败，可能是连接已断开，清空websocket引用
            if mgr.websocket == ws:
                mgr.websocket = None
            return False

    _notify_results = await asyncio.gather(
        *(_notify_one(n, m) for n, m in snapshot),
        return_exceptions=True,
    )
    notification_count = sum(1 for r in _notify_results if r is True)

    if notification_count > 0:
        logger.info(f"✅ 已通过WebSocket通知 {notification_count} 个连接的客户端：猫娘已从 {old_catgirl} 切换到 {catgirl_name}")
    else:
        logger.warning("⚠️ 没有找到任何活跃的WebSocket连接来通知猫娘切换")
        logger.warning("提示：请确保前端页面已打开并建立了WebSocket连接，且已调用start_session")

    return {"success": True}


@router.post('/reload')
async def reload_character_config():
    """Reload the character config (hot reload)."""
    try:
        initialize_character_data = get_initialize_character_data()
        await initialize_character_data()
        return {"success": True, "message": "角色配置已重新加载"}
    except Exception as e:
        logger.error(f"重新加载角色配置失败: {e}")
        return JSONResponse(
            {'success': False, 'error': f'重新加载失败: {str(e)}'},
            status_code=500
        )


@router.post('/master')
async def update_master(request: Request):
    try:
        data = await request.json()
    except Exception as e:
        logger.warning(f"解析主人更新请求体失败: {e}")
        return JSONResponse({'success': False, 'error': '请求体必须是合法的JSON格式'}, status_code=400)
    if not isinstance(data, dict):
        return JSONResponse({'success': False, 'error': '请求体必须是JSON对象'}, status_code=400)
    _config_manager = get_config_manager()
    initialize_character_data = get_initialize_character_data()
    characters = await _config_manager.aload_characters()
    previous_master = characters.get('主人') if isinstance(characters.get('主人'), dict) else {}
    previous_profile_name = ""
    if isinstance(previous_master, dict):
        previous_profile_name = str(previous_master.get('档案名') or '').strip()
    requested_profile_name = str(data.get('档案名') or '').strip()
    profile_name = previous_profile_name or requested_profile_name
    renamed_via_body_fallback = False
    if (
        previous_profile_name
        and requested_profile_name
        and requested_profile_name != previous_profile_name
        and _profile_name_contains_path_separator(previous_profile_name)
    ):
        profile_name = requested_profile_name
        renamed_via_body_fallback = True
    err = _validate_profile_name(profile_name)
    if err:
        return JSONResponse({'success': False, 'error': err}, status_code=400)
    next_master = {
        k: v
        for k, v in data.items()
        if v and k not in CHARACTER_RESERVED_FIELD_SET and k != '档案名'
    }
    next_master['档案名'] = profile_name
    if isinstance(previous_master, dict) and isinstance(previous_master.get('_reserved'), dict):
        next_master['_reserved'] = copy.deepcopy(previous_master['_reserved'])
    if renamed_via_body_fallback:
        _append_profile_rename_event(next_master, previous_profile_name, profile_name)
    characters['主人'] = next_master
    await _config_manager.asave_characters(characters)
    # 自动重新加载配置
    await initialize_character_data()
    return {"success": True}


@router.post('/master/{old_name}/rename')
async def rename_master(old_name: str, request: Request):
    """Rename the master profile."""
    _config_manager = get_config_manager()
    try:
        data = await request.json()
    except Exception as e:
        logger.warning(f"解析主人重命名请求体失败: {e}")
        return JSONResponse({'success': False, 'error': '请求体必须是合法的JSON格式'}, status_code=400)
    new_name = data.get('new_name') if data else None
    if not new_name:
        return JSONResponse({'success': False, 'error': '新档案名不能为空'}, status_code=400)

    new_name = str(new_name).strip()
    err = _validate_profile_name(new_name)
    if err:
        return JSONResponse({'success': False, 'error': err.replace('档案名', '新档案名')}, status_code=400)

    async with _ugc_sync_lock:
        characters = await _config_manager.aload_characters()
        if '主人' not in characters or not characters['主人']:
            return JSONResponse({'success': False, 'error': '我的档案不存在'}, status_code=404)

        current_master = characters['主人'].get('档案名', '')
        if current_master != old_name:
            return JSONResponse({'success': False, 'error': '原档案名不匹配'}, status_code=400)

        characters['主人']['档案名'] = new_name
        _append_profile_rename_event(characters['主人'], old_name, new_name)
        await _config_manager.asave_characters(characters)

    try:
        initialize_character_data = get_initialize_character_data()
        await initialize_character_data()
    except Exception as e:
        logger.error(f"重命名后重新加载配置失败: {e}")
        return JSONResponse({
            'success': True,
            'partial_success': True,
            'renamed': True,
            'reload_error': str(e)
        }, status_code=200)

    return {"success": True}


@router.post('/catgirl')
async def add_catgirl(request: Request):
    try:
        raw_data = await request.json()
    except Exception as e:
        logger.warning(f"解析添加猫娘请求体失败: {e}")
        return JSONResponse({'success': False, 'error': '请求体必须是合法的JSON格式'}, status_code=400)
    if not raw_data:
        return JSONResponse({'success': False, 'error': '档案名为必填项'}, status_code=400)

    profile_name = raw_data.get('档案名')
    err = _validate_profile_name(profile_name)
    if err:
        return JSONResponse({'success': False, 'error': err}, status_code=400)
    data = _filter_mutable_catgirl_fields(raw_data)
    requested_field_order = _extract_catgirl_field_order_payload(raw_data)
    data['档案名'] = str(profile_name).strip()

    _config_manager = get_config_manager()
    characters = await _config_manager.aload_characters()
    key = data['档案名']

    # 检查是否已存在同名角色，使用 Windows 风格的命名 (x)
    if key in characters.get('猫娘', {}):
        base_name = key
        counter = 1
        while f"{base_name}({counter})" in characters.get('猫娘', {}):
            counter += 1
        key = f"{base_name}({counter})"
        data['档案名'] = key
        logger.info(f'猫娘名称冲突，已重命名为: {key}')

    if '猫娘' not in characters:
        characters['猫娘'] = {}

    # 创建猫娘数据，只保存非空字段
    catgirl_data = {}
    for k, v in data.items():
        if k != '档案名':
            if v:  # 只保存非空字段
                catgirl_data[k] = v

    characters['猫娘'][key] = catgirl_data
    _sync_catgirl_field_order(catgirl_data, requested_field_order)
    # 默认走 free preset：非 free / 非 lanlan.tech 通道由 LLMSessionManager 现有 gate 清空 self.voice_id，不会泄漏给其他 TTS provider。
    # 从 free_voices['cuteGirl'] 读以避免硬编码漂移；缺失时回退到首个非空预设，再回退到旧版默认值。
    default_free_voice_id = _get_new_catgirl_default_voice_id()
    set_reserved(catgirl_data, 'voice_id', default_free_voice_id)
    await _config_manager.asave_characters(characters)
    pending_mark_ok, pending_mark_error = await _mark_new_character_greeting_pending_safe(_config_manager, key, "create")

    # Fast path：新增只需为 `key` 这一个 catgirl 分配资源 + 启动线程，不影响其它角色。
    init_one_catgirl = get_init_one_catgirl()
    await init_one_catgirl(key, is_new=True)

    memory_server_reloaded = await notify_memory_server_reload(reason=f"新角色: {key}")

    response: dict = {
        "success": True,
        "character_name": key,
        "memory_server_reloaded": memory_server_reloaded,
    }
    if not pending_mark_ok:
        response["partial_success"] = True
        response["pending_mark_ok"] = False
        response["pending_mark_failed"] = True
        response["pending_mark_error"] = pending_mark_error
    return response


@router.put('/catgirl/{name}')
async def update_catgirl(name: str, request: Request):
    try:
        raw_data = await request.json()
    except Exception as e:
        logger.warning(f"解析更新猫娘请求体失败: {e}")
        return JSONResponse({'success': False, 'error': '请求体必须是合法的JSON格式'}, status_code=400)
    if not raw_data:
        return JSONResponse({'success': False, 'error': '无数据'}, status_code=400)

    # COMPAT(v1->v2): 兼容旧客户端仍通过通用接口提交 voice_id。
    # 通用字段仍按保留字段规则过滤，voice_id 走独立检测与应用逻辑。
    voice_id_in_payload = 'voice_id' in raw_data
    requested_voice_id = ''
    if voice_id_in_payload:
        requested_voice_id = str(raw_data.get('voice_id') or '').strip()

    # 兼容前端自动修复：允许通过通用接口修改 model_type 保留字段。
    model_type_in_payload = 'model_type' in raw_data
    requested_model_type = ''
    if model_type_in_payload:
        requested_model_type = str(raw_data.get('model_type') or '').strip().lower()
        if requested_model_type == 'vrm':
            requested_model_type = 'live3d'
        if requested_model_type and requested_model_type not in ('live2d', 'live3d', 'pngtuber'):
            return JSONResponse(
                {'success': False, 'error': f'无效的模型类型: {requested_model_type}，只允许 live2d、live3d 或 pngtuber'},
                status_code=400,
            )

    data = _filter_mutable_catgirl_fields(raw_data)
    requested_field_order = _extract_catgirl_field_order_payload(raw_data)
    _config_manager = get_config_manager()
    characters = await _config_manager.aload_characters()
    if name not in characters.get('猫娘', {}):
        return JSONResponse({'success': False, 'error': '猫娘不存在'}, status_code=404)
    previous_catgirl_data = copy.deepcopy(characters['猫娘'][name])

    old_voice_id = read_legacy_voice_id(get_reserved(characters['猫娘'][name], 'voice_id', default='', legacy_keys=('voice_id',)))
    voice_id_will_change = voice_id_in_payload and old_voice_id != requested_voice_id
    if voice_id_will_change:
        session_manager = get_session_manager()
        if _is_current_catgirl_voice_session_starting(name, characters, session_manager):
            return _voice_session_starting_response()

    if voice_id_in_payload and requested_voice_id:
        # 验证 voice_id 是否在 voice_storage 中
        if not _config_manager.validate_voice_id(requested_voice_id):
            voices = _config_manager.get_voices_for_current_api()
            available_voices = list(voices.keys())
            return JSONResponse({
                'success': False,
                'error': f'voice_id "{requested_voice_id}" 在当前API的音色库中不存在',
                'available_voices': available_voices
            }, status_code=400)

    # 只更新前端传来的普通字段，未传字段删除；保留字段始终交由专用接口管理
    removed_fields = []
    for k in characters['猫娘'][name]:
        if k not in data and k not in CHARACTER_RESERVED_FIELD_SET:
            removed_fields.append(k)
    for k in removed_fields:
        characters['猫娘'][name].pop(k)

    # 更新普通字段
    for k, v in data.items():
        if k != '档案名' and v:
            characters['猫娘'][name][k] = v

    # 兼容旧接口：若请求中带有 voice_id，则同步写入保留字段（惰性迁移成结构对象）。
    if voice_id_in_payload:
        set_reserved(characters['猫娘'][name], 'voice_id', _config_manager.voice_id_to_storage_value(requested_voice_id))

    # 兼容前端自动修复：若请求中带有 model_type，则同步写入保留字段。
    if model_type_in_payload and requested_model_type:
        set_reserved(characters['猫娘'][name], 'avatar', 'model_type', requested_model_type)

    _sync_catgirl_field_order(characters['猫娘'][name], requested_field_order)

    await _config_manager.asave_characters(characters)

    new_voice_id = read_legacy_voice_id(get_reserved(characters['猫娘'][name], 'voice_id', default='', legacy_keys=('voice_id',)))
    voice_id_changed = voice_id_in_payload and old_voice_id != new_voice_id
    prompt_fields_changed = _catgirl_prompt_fields_changed(previous_catgirl_data, characters['猫娘'][name])

    # 显式记录被过滤的保留字段，避免“被吞掉”无感知。
    ignored_reserved_fields = sorted(
        (set(raw_data.keys()) & CHARACTER_RESERVED_FIELD_SET) - {'voice_id', 'model_type'}
    )
    if ignored_reserved_fields:
        logger.info(
            "update_catgirl ignored reserved fields for %s: %s",
            name,
            ", ".join(ignored_reserved_fields),
        )

    session_ended = False
    context_refresh_result = {
        "context_refreshed": False,
        "recent_history_cleared": False,
        "reload_notified": False,
        "session_restarted": False,
    }
    if prompt_fields_changed:
        context_refresh_result = await _refresh_catgirl_context_after_profile_change(
            _config_manager,
            name,
            characters,
            is_new=False,
        )
        session_ended = context_refresh_result["session_restarted"]
    elif voice_id_changed:
        session_manager = get_session_manager()
        is_current_catgirl = (name == characters.get('当前猫娘', ''))

        # 如果是当前活跃的猫娘，只结束当前语音会话；voice_id 会在下方刷新到 session_manager。
        if is_current_catgirl and name in session_manager and session_manager[name].is_active:
            logger.info(f"检测到 {name} 的voice_id已变更（{old_voice_id} -> {new_voice_id}），准备结束当前语音会话...")
            notify_session_ended = getattr(session_manager[name], "send_session_ended_by_server", None)
            if callable(notify_session_ended):
                await notify_session_ended()
            try:
                await session_manager[name].end_session(by_server=True)
                session_ended = True
                logger.info(f"{name} 的session已结束")
            except Exception as e:
                logger.error(f"结束session时出错: {e}")
            # 与 set_voice_id 路径对偶：清掉前一会话的失败计数 / 熔断，
            # 否则下一次 start_session 会被旧熔断静默拦截。
            session_manager[name].reset_session_start_circuit()

        if is_current_catgirl:
            # Fast path：只刷新被编辑角色的 session_manager（prompt/voice_id），
            # 其它 N-1 个 catgirl 不动。
            init_one_catgirl = get_init_one_catgirl()
            await init_one_catgirl(name, is_new=False)
            logger.info("配置已重新加载，新的voice_id已生效")
        else:
            # 非当前猫娘：原来靠下次 switch 的全量 init 顺带 rescue。切换改走 fast path
            # 后 rescue 不再发生，所以这里必须显式刷 session_manager[name]。
            # init_one_catgirl 只写 session_manager[name] 的 prompt/voice_id，不碰当前 session。
            init_one_catgirl = get_init_one_catgirl()
            await init_one_catgirl(name, is_new=False)
            logger.info(f"非当前猫娘 {name} 的音色已更新并同步到 session_manager")
    else:
        # Fast path：普通字段编辑，只刷新被编辑角色。
        init_one_catgirl = get_init_one_catgirl()
        await init_one_catgirl(name, is_new=False)

    return {
        "success": True,
        **context_refresh_result,
        "voice_id_changed": voice_id_changed,
        "session_restarted": session_ended,
        "ignored_reserved_fields": ignored_reserved_fields,
    }


@router.post('/catgirl/delete')
async def delete_catgirl_by_body(request: Request):
    """Delete a character by JSON body.

    This is the rescue path for historical unsafe names such as "." that cannot
    be represented safely as a URL path segment.
    """
    try:
        data = await request.json()
    except Exception as e:
        logger.warning(f"解析删除猫娘请求体失败: {e}")
        return JSONResponse({'success': False, 'error': '请求体必须是合法的JSON格式'}, status_code=400)
    if not isinstance(data, dict):
        return JSONResponse({'success': False, 'error': '请求体必须是合法的JSON格式'}, status_code=400)
    name = str((data or {}).get('name') or '').strip()
    if not name:
        return JSONResponse({'success': False, 'error': '猫娘名称不能为空'}, status_code=400)
    return await _delete_catgirl_by_name(name)


@router.delete('/catgirl/{name}')
async def delete_catgirl(name: str):
    return await _delete_catgirl_by_name(name)


async def _delete_catgirl_by_name(name: str):
    _config_manager = get_config_manager()
    characters = await _config_manager.aload_characters()
    if name not in characters.get('猫娘', {}):
        return JSONResponse({'success': False, 'error': '猫娘不存在'}, status_code=404)

    # 检查是否是当前正在使用的猫娘
    current_catgirl = characters.get('当前猫娘', '')
    if name == current_catgirl:
        return JSONResponse({'success': False, 'error': '不能删除当前正在使用的猫娘！请先切换到其他猫娘后再删除。'}, status_code=400)

    safe_path_name = _validate_existing_character_path_name(name) is None
    assert_cloudsave_writable(
        _config_manager,
        operation="delete",
        target=f"characters/{name}",
    )

    if not safe_path_name:
        logger.warning("正在执行历史非法角色名救援删除，仅移除配置，不触碰角色文件路径: %s", name)
        characters_snapshot = copy.deepcopy(characters)
        try:
            del characters['猫娘'][name]
            await _config_manager.asave_characters(characters)

            remove_one_catgirl = get_remove_one_catgirl()
            await remove_one_catgirl(name)

            memory_server_reloaded = await notify_memory_server_reload(reason=f"救援删除非法角色名: {name}")
            if not memory_server_reloaded:
                rollback_error = await _rollback_character_operation(
                    _config_manager,
                    characters_snapshot=characters_snapshot,
                    memory_snapshot_records=[],
                    reason=f"救援删除非法角色名回滚: {name}",
                )
                error_message = "救援删除非法角色名失败: notify_memory_server_reload returned False"
                if rollback_error:
                    error_message = f"{error_message}; 回滚失败: {rollback_error}"
                return JSONResponse({"success": False, "error": error_message}, status_code=500)
        except MaintenanceModeError:
            raise
        except Exception as exc:
            rollback_error = await _rollback_character_operation(
                _config_manager,
                characters_snapshot=characters_snapshot,
                memory_snapshot_records=[],
                reason=f"救援删除非法角色名回滚: {name}",
            )
            logger.exception("救援删除非法角色名失败，已尝试回滚: %s", name)
            error_message = f"救援删除非法角色名失败: {exc}"
            if rollback_error:
                error_message = f"{error_message}; 回滚失败: {rollback_error}"
            return JSONResponse({"success": False, "error": error_message}, status_code=500)

        return {
            "success": True,
            "unsafe_name_rescue": True,
            "memory_deleted": False,
            "card_face_deleted": False,
            "memory_server_reloaded": memory_server_reloaded,
        }

    released_memory_handle = await release_memory_server_character(
        name,
        reason=f"角色删除前释放 SQLite 句柄: {name}",
    )
    if not released_memory_handle:
        logger.warning("角色删除前释放记忆服务器句柄失败，已阻止删除: %s", name)
        return JSONResponse(
            {
                "success": False,
                "code": "MEMORY_SERVER_RELEASE_FAILED",
                "error": "释放角色记忆句柄失败，已阻止删除，请稍后重试",
                "memory_server_released": False,
            },
            status_code=503,
        )

    characters_snapshot = copy.deepcopy(characters)
    memory_targets = list_character_memory_paths(_config_manager, name)
    face_path = _config_manager.card_faces_dir / f"{name}.png"
    meta_path = _config_manager.card_face_meta_path(name)
    memory_targets.append(face_path)
    memory_targets.append(meta_path)

    with _create_character_operation_backup_dir(_config_manager, "neko-delete-character-") as temp_dir:
        memory_snapshot_records = await asyncio.to_thread(
            _snapshot_existing_paths, memory_targets, Path(temp_dir)
        )
        tombstone_snapshot = None
        memory_server_reloaded = False
        try:
            if not is_cloudsave_disabled():
                tombstone_snapshot = copy.deepcopy(_config_manager.load_character_tombstones_state())

            removed_memory_paths = await asyncio.to_thread(
                delete_character_memory_storage, _config_manager, name
            )
            for entry_path in removed_memory_paths:
                logger.info(f"已删除: {entry_path}")

            # 同步删除卡面 PNG 与 sidecar JSON（纳入同一事务以便回滚）
            if face_path.exists():
                await asyncio.to_thread(face_path.unlink)
            if meta_path.exists():
                await asyncio.to_thread(meta_path.unlink)

            if not is_cloudsave_disabled():
                await asyncio.to_thread(
                    _config_manager.save_character_tombstones_state,
                    _build_character_tombstones_state(_config_manager, name),
                )

            # 删除角色配置
            del characters['猫娘'][name]
            await _config_manager.asave_characters(characters)
            # Fast path：只停该角色的线程 + 清 dict + 刷 globals，不遍历其它 N-1 个。
            remove_one_catgirl = get_remove_one_catgirl()
            await remove_one_catgirl(name)
            memory_server_reloaded = await notify_memory_server_reload(reason=f"删除角色: {name}")
            if not memory_server_reloaded:
                raise RuntimeError("notify_memory_server_reload returned False")
            if is_cloudsave_disabled():
                try:
                    from main_routers.workshop_router import mark_session_deleted_character_name

                    mark_session_deleted_character_name(name)
                except Exception as exc:
                    logger.warning("记录本会话工坊删除标记失败: %s", exc)
        except MaintenanceModeError as exc:
            rollback_error = await _rollback_character_operation(
                _config_manager,
                characters_snapshot=characters_snapshot,
                memory_snapshot_records=memory_snapshot_records,
                tombstone_snapshot=tombstone_snapshot,
                reason=f"维护模式：删除角色回滚 {name}",
            )
            if rollback_error:
                raise exc from RuntimeError(rollback_error)
            raise
        except Exception as exc:
            rollback_error = await _rollback_character_operation(
                _config_manager,
                characters_snapshot=characters_snapshot,
                memory_snapshot_records=memory_snapshot_records,
                tombstone_snapshot=tombstone_snapshot,
                reason=f"删除角色回滚: {name}",
            )
            logger.exception("删除角色失败，已尝试回滚: %s", name)
            error_message = f"删除角色失败: {exc}"
            if rollback_error:
                error_message = f"{error_message}; 回滚失败: {rollback_error}"
            return JSONResponse(
                {
                    "success": False,
                    "error": error_message,
                    "memory_server_released": released_memory_handle,
                },
                status_code=500,
            )

    pending_remove_ok = True
    pending_remove_error = ""
    try:
        await remove_new_character_greeting_pending(_config_manager, name)
    except Exception as exc:
        pending_remove_ok = False
        pending_remove_error = str(exc)
        logger.exception("remove new character greeting pending failed: %s", name)

    result = {"success": True, "memory_server_reloaded": memory_server_reloaded}
    if not pending_remove_ok:
        result["partial_success"] = True
        result["pending_remove_ok"] = False
        result["pending_remove_failed"] = True
        result["pending_remove_error"] = pending_remove_error
    return result

@router.post('/clear_voice_ids')
async def clear_voice_ids():
    """Clear all characters' local voice ID records."""
    try:
        _config_manager = get_config_manager()
        characters = await _config_manager.aload_characters()
        cleared_count = 0

        # 清除所有猫娘的voice_id
        if '猫娘' in characters:
            for name in characters['猫娘']:
                if read_legacy_voice_id(get_reserved(characters['猫娘'][name], 'voice_id', default='', legacy_keys=('voice_id',))):
                    set_reserved(characters['猫娘'][name], 'voice_id', '')
                    cleared_count += 1

        await _config_manager.asave_characters(characters)
        await ensure_default_yui_voice_for_free_api(_config_manager)
        # 自动重新加载配置
        initialize_character_data = get_initialize_character_data()
        await initialize_character_data()

        return JSONResponse({
            'success': True,
            'message': f'已清除 {cleared_count} 个角色的Voice ID记录',
            'cleared_count': cleared_count
        })
    except Exception as e:
        return JSONResponse({
            'success': False,
            'error': f'清除Voice ID记录时出错: {str(e)}'
        }, status_code=500)


@router.get('/custom_tts_voices')
async def list_custom_tts_voices_for_characters(provider: str = ''):
    """Return custom GPT-SoVITS voices for the character UI."""
    try:
        _config_manager = get_config_manager()
        core_config = await _config_manager.aget_core_config()
        tts_config = _config_manager.get_model_api_config('tts_custom')

        base_url = (
            tts_config.get('base_url')
            or tts_config.get('url')
            or core_config.get('ttsModelUrl')
            or core_config.get('TTS_MODEL_URL')
            or ''
        )
        if tts_config.get('is_enabled') is False or core_config.get('GPTSOVITS_ENABLED') is False:
            return JSONResponse({
                'success': False,
                'error': 'GPTSOVITS_NOT_ENABLED',
                'code': 'GPTSOVITS_NOT_ENABLED',
                'voices': []
            }, status_code=400)
        if not tts_config.get('is_custom'):
            return JSONResponse({
                'success': False,
                'error': 'CUSTOM_API_NOT_ENABLED',
                'code': 'CUSTOM_API_NOT_ENABLED',
                'voices': []
            }, status_code=400)
        if not base_url or not (base_url.startswith('http://') or base_url.startswith('https://')):
            return JSONResponse({
                'success': False,
                'error': 'TTS_CUSTOM_URL_NOT_CONFIGURED',
                'code': 'TTS_CUSTOM_URL_NOT_CONFIGURED',
                'voices': []
            }, status_code=400)

        from urllib.parse import urlparse
        import ipaddress
        parsed = urlparse(base_url)
        host = parsed.hostname or ''
        try:
            if not ipaddress.ip_address(host).is_loopback:
                return JSONResponse({'success': False, 'error': 'TTS_CUSTOM_URL_LOCALHOST_ONLY', 'code': 'TTS_CUSTOM_URL_LOCALHOST_ONLY', 'voices': []}, status_code=400)
        except ValueError:
            if host not in ('localhost',):
                return JSONResponse({'success': False, 'error': 'TTS_CUSTOM_URL_LOCALHOST_ONLY', 'code': 'TTS_CUSTOM_URL_LOCALHOST_ONLY', 'voices': []}, status_code=400)

        voices = await get_custom_tts_voices(base_url, provider='gptsovits')
        return JSONResponse({
            'success': True,
            'provider': 'gptsovits',
            'voices': voices,
            'api_url': base_url
        })
    except (CustomTTSVoiceFetchError, ValueError) as e:
        error_text = str(e)
        return JSONResponse({
            'success': False,
            'error': f'连接 GPT-SoVITS API 失败: {error_text}',
            'voices': []
        }, status_code=502)
    except Exception as e:
        return JSONResponse({
            'success': False,
            'error': f'获取 GPT-SoVITS 声音列表失败: {str(e)}',
            'voices': []
        }, status_code=500)
@router.post('/set_microphone')
async def set_microphone(request: Request):
    try:
        data = await request.json()
        microphone_id = data.get('microphone_id')

        # 使用标准的load/save函数
        _config_manager = get_config_manager()
        characters_data = await _config_manager.aload_characters()

        # 添加或更新麦克风选择
        characters_data['当前麦克风'] = microphone_id

        # 保存配置
        await _config_manager.asave_characters(characters_data)
        # 麦克风 ID 是纯前端读取的字段（仅 get_microphone 读），不影响任何 catgirl
        # 的 prompt / voice_id / session_manager，无需触发任何 init。

        return {"success": True}
    except Exception as e:
        logger.error(f"保存麦克风选择失败: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


@router.get('/get_microphone')
async def get_microphone():
    try:
        _config_manager = get_config_manager()
        # 使用配置管理器加载角色配置
        characters_data = await _config_manager.aload_characters()

        # 获取保存的麦克风选择
        microphone_id = characters_data.get('当前麦克风')

        return {"microphone_id": microphone_id}
    except Exception as e:
        logger.error(f"获取麦克风选择失败: {e}")
        return {"microphone_id": None}


def _build_free_intl_voice_pins(native_catalog: dict, voice_id_exists=None) -> list[dict]:
    """The two pinned voices at the top of the overseas free (free_intl) list.

    - yui: the initial/default character voice; sends the literal "yui" (the
      server maps it to yui's dedicated voice).
    - default: synonymous with Leda, sends "Leda". It still remains a normal
      entry in the long Gemini list (no dedup); this merely pins an extra copy
      on top relabeled as "default".

    Display names are localized by the frontend via i18n_key; this only provides
    voice_id / i18n_key / fallback prefix.

    voice_id_exists: if a pin's voice_id collides with a user-registered/cloned
    voice (e.g. a local-TTS user created a voice whose ID is "yui"/"Leda"), the
    runtime routing prefers the cloned path on collision and no longer treats it
    as native (see the collision branch of NativeVoiceProvider.resolve_for_routing),
    so clicking the pinned entry would never reach Gemini — hide it outright to
    avoid misleading the user.
    """
    def _pin(voice_id: str, i18n_key: str, fallback: str) -> dict | None:
        if callable(voice_id_exists) and voice_id_exists(voice_id):
            return None
        meta = native_catalog.get(voice_id) or {}
        return {
            "voice_id": voice_id,
            "i18n_key": i18n_key,
            "prefix": meta.get("prefix") or fallback,
            "provider": "free_intl",
            "builtin": True,
        }

    pins = [
        _pin("yui", "voice.freeVoice.yui", "Yui"),
        _pin("Leda", "voice.freeVoice.default", "Default"),
    ]
    return [pin for pin in pins if pin is not None]


@router.get('/voices')
async def get_voices():
    """Get all registered voices for the current API key."""
    _config_manager = get_config_manager()
    result = {"voices": _config_manager.get_voices_for_current_api(for_listing=True)}

    core_config = await _config_manager.aget_core_config()
    # 先看有没有自带静态预制目录的 provider 被选中（如 MiMo，hosted）。与 dispatch
    # 同一优先级判定：选中的 provider 若有 preset_catalog 就用它，并压过 core-native
    # （assistApi=mimo 在 dispatch 里 priority 60 也先于 native 命中）；GPT-SoVITS /
    # vLLM 这类先命中、无静态目录的 provider 则不出目录（preset 为 None）。复用
    # native_voices 通道——前端 source-first 选声器按 entry 的 provider/provider_label
    # 自动分组成「<Provider> · 预制」，无需新增来源通道。
    # 选声目录与 dispatch 同一优先级：先看哪个注册表 provider 赢得当前配置。
    #  - 赢家有静态预制目录（如 MiMo）→ 出该目录，压过 core-native（用 is not None，
    #    空目录也算命中，不误回退）。
    #  - 赢家无静态目录（vLLM-Omni / GPT-SoVITS，用户自填/自部署音色）→ 既不出目录、
    #    也不回退 core-native：dispatch 会路由到该赢家，露出 gemini 等原生音色会让用户
    #    选中后被误传给赢家触发 unsupported-voice（PR #1848 Codex review）。
    #  - 无注册表 provider 赢 → 回退 core-native（gemini/step/...）。
    # 复用 native_voices 通道——前端 source-first 选声器按 entry 的 provider/provider_label
    # 自动分组成「<Provider> · 预制」，无需新增来源通道。
    winning_provider_key = tts_provider_registry.selected_provider_key(
        core_config or {}, _config_manager
    )
    selected_preset_catalog = (
        tts_provider_registry.preset_catalog_for_ui(winning_provider_key)
        if winning_provider_key else None
    )
    active_native_provider = (
        get_active_realtime_native_provider_for_ui(_config_manager)
        if winning_provider_key is None else None
    )
    if selected_preset_catalog is not None:
        result["native_voices"] = selected_preset_catalog
    elif active_native_provider:
        native_catalog = get_native_voice_catalog_for_ui(active_native_provider) or {}
        if active_native_provider == 'free_intl':
            # 海外免费（lanlan.app/Gemini）：yui + default(=Leda) 两个置顶 pin，
            # 其后是 Gemini 全量目录。yui 从长列表里挪到 pin（不重复展示）；
            # Leda 不去重，仍作为普通条目留在长列表里（= default pin 的目标）。
            # pin 的展示名由前端按 i18n_key 本地化。
            voice_exists = getattr(_config_manager, "voice_id_exists_in_any_storage", None)
            result["pinned_voices"] = _build_free_intl_voice_pins(
                native_catalog,
                voice_id_exists=voice_exists,
            )
            # 撞名（跨 api-key 桶存在同名克隆/自定义音色）的条目也从长列表里去掉：
            # runtime 路由/preview 用 any-storage 撞名判定会拒绝当 native，展示了
            # 点选也到不了 Gemini（与 pin 的撞名隐藏对偶）。前端只按当前 api 的
            # voices 去重，跨桶撞名漏网，故在后端按同一谓词收口。
            def _free_intl_keep(voice_id: str) -> bool:
                if voice_id == 'yui':
                    return False
                if callable(voice_exists) and voice_exists(voice_id):
                    return False
                return True
            native_catalog = {
                voice_id: meta
                for voice_id, meta in native_catalog.items()
                if _free_intl_keep(voice_id)
            }
        result["native_voices"] = native_catalog

    # 免费预设音色只在 core=free 运行时可用（与 assist 无关）；core_url 仍须指向
    # lanlan.tech 免费端点，海外 lanlan.app 路由由 should_block_free_voice_for_route 兜底。
    # 此处已持有 core_config，直接读 CORE_API_TYPE（等价 is_free_voice()），省一次读取。
    # CORE_URL 用 `or ''` 归一化：key 存在但值为 None 时 `.get(k, '')` 仍返回 None，
    # `in None` 会抛 TypeError 让 /voices 500。
    if core_config.get('CORE_API_TYPE') == 'free' and 'lanlan.tech' in (core_config.get('CORE_URL') or ''):
        from utils.api_config_loader import get_free_voices
        free_voices = get_free_voices()
        if free_voices:
            result["free_voices"] = free_voices

    # 构建 voice_id → 使用该音色的角色名列表，用于前端显示
    characters = await _config_manager.aload_characters()
    voice_owners = {}
    for catgirl_name, catgirl_config in characters.get('猫娘', {}).items():
        if not isinstance(catgirl_config, dict):
            logger.warning(f"角色配置格式异常，已跳过 voice_owners 统计: {catgirl_name}")
            continue
        vid = read_legacy_voice_id(get_reserved(catgirl_config, 'voice_id', default='', legacy_keys=('voice_id',)))
        if vid:
            voice_owners.setdefault(vid, []).append(catgirl_name)
    result["voice_owners"] = voice_owners

    return result


@router.get('/voice_preview')
async def get_voice_preview(
    request: Request,
    voice_id: str,
    language: str | None = None,
    i18n_language: str | None = None,
):
    """Get the voice preview audio."""
    try:
        _config_manager = get_config_manager()
        voices = _config_manager.get_voices_for_current_api()
        voice_data = voices.get(voice_id) if isinstance(voices, dict) else None
        provider = (voice_data or {}).get('provider', '')
        is_free_preset_voice = _is_free_preset_voice_id(voice_id)

        # 优先尝试从 tts_custom 获取 API Key
        try:
            tts_custom_config = _config_manager.get_model_api_config('tts_custom')
            audio_api_key = tts_custom_config.get('api_key', '')
        except Exception:
            audio_api_key = ''

        # 如果没有，则回退到核心配置
        # Codex review: 原先这里顶上还有一个 `core_config = ...`，从未被读取（死代码）。
        # 全仓 async 化时把死读也改成了 await，反而白跑一次 IO，删。
        if not audio_api_key:
            core_config = await _config_manager.aget_core_config()
            audio_api_key = core_config.get('AUDIO_API_KEY', '')

        cosyvoice_base_url = ''
        if provider in ('cosyvoice', 'cosyvoice_intl'):
            cosyvoice_runtime = _config_manager.get_cosyvoice_clone_runtime(provider)
            runtime_key = (cosyvoice_runtime.get('api_key') or '').strip()
            if runtime_key:
                audio_api_key = runtime_key
            elif provider == 'cosyvoice_intl':
                # intl key 缺失时不要继续用顶上从 tts_custom/AUDIO_API_KEY 拿到的
                # 国内 key 去打 intl DashScope 端点，必然 401，错误现象比明确缺 key
                # 难排查。和 minimax/native step/gemini 分支一致显式返回缺 key。
                return JSONResponse({
                    'success': False,
                    'error': 'TTS_AUDIO_API_KEY_MISSING',
                    'code': 'TTS_AUDIO_API_KEY_MISSING'
                }, status_code=400)
            cosyvoice_base_url = (
                (voice_data or {}).get('dashscope_base_url')
                or cosyvoice_runtime.get('base_url', '')
            )

        logger.info(f"正在为音色 {voice_id} 生成预览音频...")

        preview_language = _get_voice_preview_language(request, language, i18n_language)
        text = _loc(VOICE_PREVIEW_TEXTS, preview_language)

        # hosted/local provider 的预制音色（如选中 MiMo 时的预制声线）经 native_voices
        # 通道露给前端会渲染试听按钮，但其试听需走该 provider 自己的合成路径（尚未接）。
        # 在此显式拦下返回「暂不支持试听」，避免落到下方 DashScope/CosyVoice 通用分支拿着
        # 该 provider 的 key/voice_id 误合成（PR #1848 Codex review；真试听留作后续）。
        # 与预制同名的克隆音色不拦（dispatch 克隆 provider 先于 MiMo 命中），仍走克隆试听。
        preview_core_config = await _config_manager.aget_core_config()
        if _is_unpreviewable_selected_preset_voice(
            _config_manager, preview_core_config, voice_id, voice_data
        ):
            return JSONResponse({
                'success': False,
                'error': f'当前预制音色暂不支持试听: {voice_id}',
                'code': 'PRESET_VOICE_PREVIEW_UNSUPPORTED',
            }, status_code=400)

        # MiMo 克隆音色（provider=='mimo'）试听：读 voice_meta 里的参考样本 base64，用
        # voiceclone 模型一次性合成预览句（对偶 MiniMax 的克隆试听；避免落到下方
        # CosyVoice/DashScope 通用分支拿着 mimo-clone-* 的 id 误合成）。
        if provider == 'mimo':
            sample_b64 = (voice_data or {}).get('clone_sample_b64') or ''
            if not sample_b64:
                return JSONResponse({
                    'success': False,
                    'error': f'MiMo 克隆音色缺少参考样本，无法试听: {voice_id}',
                    'code': 'MIMO_VOICE_SAMPLE_MISSING',
                }, status_code=400)
            mimo_api_key = _config_manager.get_tts_api_key('mimo')
            if not mimo_api_key:
                return JSONResponse({
                    'success': False,
                    'error': 'MIMO_API_KEY_MISSING',
                    'code': 'MIMO_API_KEY_MISSING',
                }, status_code=400)
            # base_url 与 dispatch 同源：assistApi=mimo（Token Plan 唯一场景）用 OPENROUTER_URL，
            # 否则用 voice_meta 存的 mimo_base_url，缺省默认端点。
            if str(preview_core_config.get('assistApi') or '').strip().lower() == 'mimo':
                mimo_base_url = (preview_core_config.get('OPENROUTER_URL') or '').strip()
            else:
                mimo_base_url = str((voice_data or {}).get('mimo_base_url') or '').strip()
            try:
                sample_bytes = base64.b64decode(sample_b64)
            except ValueError:  # binascii.Error 是 ValueError 子类
                return JSONResponse({
                    'success': False,
                    'error': f'MiMo 克隆音色样本损坏，无法试听: {voice_id}',
                    'code': 'MIMO_VOICE_SAMPLE_CORRUPT',
                }, status_code=400)
            try:
                mimo_client = MimoVoiceCloneClient(api_key=mimo_api_key, base_url=mimo_base_url or None)
                audio_data = await mimo_client.synthesize_preview(
                    sample_bytes,
                    (voice_data or {}).get('clone_sample_mime') or 'audio/wav',
                    text=text,
                )
                audio_base64 = base64.b64encode(audio_data).decode('utf-8')
                logger.info(f"MiMo 克隆音色 {voice_id} 预览音频生成成功，大小: {len(audio_data)} 字节")
                return {'success': True, 'audio': audio_base64, 'mime_type': 'audio/wav'}
            except MimoVoiceCloneError as e:
                logger.error(f"MiMo 克隆音色 {voice_id} 预览失败: {e}")
                return JSONResponse({
                    'success': False,
                    'error': f'MiMo 预览生成失败: {str(e)}',
                    'code': 'MIMO_VOICE_PREVIEW_FAILED',
                }, status_code=502)
            except Exception as e:
                logger.error(f"MiMo 克隆音色 {voice_id} 预览异常: {e}")
                return JSONResponse({
                    'success': False,
                    'error': f'MiMo 预览生成失败: {str(e)}',
                }, status_code=500)

        if provider == 'doubao_tts':
            doubao_api_key = _config_manager.get_tts_api_key('doubao_tts')
            if not doubao_api_key:
                return JSONResponse({
                    'success': False,
                    'error': 'DOUBAO_TTS_API_KEY_MISSING',
                    'code': 'DOUBAO_TTS_API_KEY_MISSING',
                }, status_code=400)
            doubao_base_url = (
                (voice_data or {}).get('doubao_base_url')
                or DOUBAO_TTS_DEFAULT_BASE_URL
            )
            doubao_resource_id = (
                (voice_data or {}).get('doubao_resource_id')
                or DOUBAO_VOICE_CLONE_RESOURCE_ID
            )
            try:
                async with httpx.AsyncClient(timeout=60) as client:
                    resp = await client.post(
                        doubao_tts_url(doubao_base_url),
                        headers=doubao_api_headers(doubao_api_key, doubao_resource_id),
                        json=build_doubao_tts_payload(
                            text,
                            voice_id,
                            context_texts=(DOUBAO_TTS_DEFAULT_CONTEXT_TEXTS,),
                        ),
                    )
                if resp.status_code != 200:
                    return JSONResponse({
                        'success': False,
                        'error': f'豆包语音预览失败: HTTP {resp.status_code}, {resp.text[:200]}',
                        'code': 'DOUBAO_TTS_PREVIEW_FAILED',
                    }, status_code=502)
                audio_data = extract_doubao_audio_bytes(await resp.aread())
                if not audio_data:
                    return JSONResponse({
                        'success': False,
                        'error': '豆包语音预览未返回音频',
                        'code': 'DOUBAO_TTS_PREVIEW_NO_AUDIO',
                    }, status_code=502)
                audio_base64 = base64.b64encode(audio_data).decode('utf-8')
                logger.info(f"豆包语音音色 {voice_id} 预览音频生成成功，大小: {len(audio_data)} 字节")
                return {'success': True, 'audio': audio_base64, 'mime_type': 'audio/wav'}
            except DoubaoTtsError as e:
                logger.error(f"豆包语音音色 {voice_id} 预览失败: {e}")
                return JSONResponse({
                    'success': False,
                    'error': f'豆包语音预览失败: {str(e)}',
                    'code': 'DOUBAO_TTS_PREVIEW_FAILED',
                }, status_code=502)
            except Exception as e:
                logger.error(f"豆包语音音色 {voice_id} 预览异常: {e}")
                return JSONResponse({
                    'success': False,
                    'error': f'豆包语音预览失败: {str(e)}',
                }, status_code=500)

        # vLLM-Omni 克隆音色（provider=='vllm_omni'）试听：读 voice_meta 里的参考样本
        # base64，通过 vLLM-Omni WebSocket 合成预览句（对偶 MiMo 的克隆试听；避免落到
        # 下方 CosyVoice/DashScope 通用分支拿着无效 API key 误合成报 401）。
        if provider == 'vllm_omni':
            import numpy as np
            import soxr

            clone_sample_b64 = str((voice_data or {}).get('clone_sample_b64') or '').strip()
            if not clone_sample_b64:
                return JSONResponse({
                    'success': False,
                    'error': f'vLLM-Omni 克隆音色缺少参考样本，无法试听: {voice_id}',
                    'code': 'VLLM_OMNI_VOICE_SAMPLE_MISSING',
                }, status_code=400)
            # 构建 data: URI：复用 worker 的 _build_vllm_omni_clone_data_uri，
            # 与 dispatch 路径同一封装（含 MIME 默认值 audio/wav 与空 b64 保护），
            # 避免预览/dispatch 两处独立维护拼接逻辑时悄悄产生行为差异。
            from main_logic.tts_client.workers.vllm_omni import _build_vllm_omni_clone_data_uri
            from main_logic.tts_client._infra import _resample_audio
            clone_data_uri = _build_vllm_omni_clone_data_uri(voice_data)
            ref_text = str((voice_data or {}).get('clone_ref_text') or '').strip()
            # base_url：优先 voice_meta 存的 vllm_omni_base_url，缺省回落
            # preview_core_config 的 ttsModelUrl。无配置 URL 时返回 400 —— 不硬编码
            # 任何内网端点（旧实现 fallback 到固定 IP，推到公共仓库后必失败且泄漏拓扑）。
            base_url = str((voice_data or {}).get('vllm_omni_base_url') or '').strip()
            if not base_url:
                base_url = str(
                    (preview_core_config or {}).get('ttsModelUrl')
                    or (preview_core_config or {}).get('TTS_MODEL_URL')
                    or ''
                ).strip()
            if not base_url:
                return JSONResponse({
                    'success': False,
                    'error': 'vLLM-Omni 服务地址未配置，请先在 TTS 设置中填写端点 URL',
                    'code': 'VLLM_OMNI_URL_MISSING',
                }, status_code=400)
            # model：从 preview_core_config 的 ttsModelId 或默认 Qwen3-TTS
            model = str((preview_core_config or {}).get('ttsModelId') or '').strip() or 'Qwen3-TTS'
            # URL 规整复用 worker 的 _vllm_omni_normalize_ws_endpoint（http→ws / 补 /v1 /
            # 幂等 endpoint 拼接）——避免 preview 复制粘贴时漏掉协议转换导致用户配 http://
            # 端点必失败（dual to vllm_omni_tts_worker 的 URL 规整）。
            from main_logic.tts_client import _vllm_omni_normalize_ws_endpoint
            ws_endpoint = _vllm_omni_normalize_ws_endpoint(base_url)
            # API key 鉴权（对齐 worker 的 _connect_and_config 双路径：WS handshake
            # Authorization header + session.config.api_key）。有认证的 vLLM 端点克隆
            # 预览也需要传 key，否则 401 失败（C6 fix）。
            vllm_api_key = str((preview_core_config or {}).get('ttsModelApiKey') or '').strip()
            ws_kwargs = {"max_size": None, "open_timeout": 10, "close_timeout": 5}
            if vllm_api_key:
                # 兼容旧版本 websockets：用 inspect 探测参数名，避免 try/except TypeError
                # 过宽吞掉 WS 通信中的合法 TypeError，也避免 await connect() + async with ws:
                # 导致 websockets 16.0 的 connect.__aenter__ 再次 await self 重连（C6-1 fix）。
                # NOTE: preview 用 inspect 探测参数名，worker 仍用 try/except TypeError。
                # 两条路径策略不同是因为 preview 是短连接（async with 自动管理），
                # worker 是长连接（手动管理）。worker 的 try/except TypeError 在
                # connect() 阶段执行，send/recv 在块外，吞没风险较低。
                try:
                    _ws_sig = inspect.signature(websockets.connect)
                    _header_key = "additional_headers" if "additional_headers" in _ws_sig.parameters else "extra_headers"
                except (ValueError, TypeError):
                    _header_key = "additional_headers"  # 新版本优先
                ws_kwargs[_header_key] = [
                    ("Authorization", f"Bearer {vllm_api_key}"),
                ]
            try:
                # 整体超时 30s：vLLM-Omni 预览无服务端超时，若服务端接受连接但不发
                # session.done（半开/挂起），async for 会永久阻塞占用 worker。open_timeout/
                # close_timeout 与 worker 的 _connect_and_config 对齐。
                async with asyncio.timeout(30):
                    async with websockets.connect(ws_endpoint, **ws_kwargs) as ws:
                        # 发送 session.config
                        config_msg = {
                            "type": "session.config",
                            "model": model,
                            "voice": "default",
                            "response_format": "pcm",
                            "speed": 1.0,
                            "stream_audio": True,
                            "split_granularity": "sentence",
                            "ref_audio": clone_data_uri,
                        }
                        if ref_text:
                            config_msg["ref_text"] = ref_text
                        # session 层鉴权（部分自建服务端从 config 读 api_key）
                        if vllm_api_key:
                            config_msg["api_key"] = vllm_api_key
                        await ws.send(json.dumps(config_msg))
                        # 发送预览文本
                        await ws.send(json.dumps({"type": "input.text", "text": text}))
                        await ws.send(json.dumps({"type": "input.done"}))
                        # 接收 PCM 二进制帧（24kHz/16bit/mono）和 JSON 事件
                        pcm_chunks: list[bytes] = []
                        # 流式重采样器：维护 chunk 边界滤波器状态，避免无状态 soxr.resample()
                        # 在每个分片边界重置导致杂音（与 worker 路径的 ResampleStream 一致）。
                        resampler = soxr.ResampleStream(24000, 48000, 1, dtype='float32')
                        async for message in ws:
                            if isinstance(message, bytes):
                                if len(message) < 2:
                                    continue
                                audio_array = np.frombuffer(message, dtype=np.int16)
                                pcm_chunks.append(_resample_audio(audio_array, 24000, 48000, resampler))
                            else:
                                try:
                                    event = json.loads(message)
                                except json.JSONDecodeError:
                                    continue
                                event_type = event.get("type", "")
                                if event_type == "session.done":
                                    break
                                elif event_type == "error":
                                    error_msg = event.get("message", event.get("error", str(event)))
                                    logger.error(f"vLLM-Omni 克隆音色 {voice_id} 预览服务端错误: {error_msg}")
                                    return JSONResponse({
                                        'success': False,
                                        'error': f'vLLM-Omni 预览生成失败: {error_msg}',
                                        'code': 'VLLM_OMNI_VOICE_PREVIEW_FAILED',
                                    }, status_code=502)
                # 空帧守卫：vLLM-Omni 在显式送入 response_format: pcm 时，返回的是
                # 裸 PCM 流（本身不含 44 字节 WAV 头，由下面 _build_wav_payload 补头）。
                # 若 session.done 后没收到任何 PCM 帧（服务端合成失败却没发 error 事件，
                # 或帧长 < 2 被全部跳过），_build_wav_payload([]) 只会产出一个仅含 44 字节
                # WAV 头的空文件。直接以 success:True 回前端会让用户点试听毫无声音、无任何
                # 错误提示（哑弹）。正常路径下 error 事件已能拦住多数失败，此处是额外兜底。
                if not pcm_chunks:
                    logger.error(f"vLLM-Omni 克隆音色 {voice_id} 预览未返回任何音频帧")
                    return JSONResponse({
                        'success': False,
                        'error': 'vLLM-Omni 预览未返回任何音频帧',
                        'code': 'VLLM_OMNI_VOICE_PREVIEW_NO_AUDIO',
                    }, status_code=502)
                # 封装 WAV（给裸 PCM 补 44 字节头）
                audio_data = _build_wav_payload(pcm_chunks, 1, 2, 48000)
                audio_base64 = base64.b64encode(audio_data).decode('utf-8')
                logger.info(f"vLLM-Omni 克隆音色 {voice_id} 预览音频生成成功，大小: {len(audio_data)} 字节")
                return {'success': True, 'audio': audio_base64, 'mime_type': 'audio/wav'}
            except (TimeoutError, asyncio.TimeoutError):
                logger.error(f"vLLM-Omni 克隆音色 {voice_id} 预览超时（30秒）")
                return JSONResponse({
                    'success': False,
                    'error': 'vLLM-Omni 预览生成失败: 服务端响应超时（30秒）',
                    'code': 'VLLM_OMNI_VOICE_PREVIEW_TIMEOUT',
                }, status_code=504)
            except websockets.exceptions.ConnectionClosed as e:
                logger.error(f"vLLM-Omni 克隆音色 {voice_id} 预览 WebSocket 连接关闭: {e}")
                return JSONResponse({
                    'success': False,
                    'error': f'vLLM-Omni 预览生成失败: WebSocket 连接异常',
                    'code': 'VLLM_OMNI_VOICE_PREVIEW_FAILED',
                }, status_code=502)
            except (OSError, ConnectionError) as e:
                logger.error(f"vLLM-Omni 克隆音色 {voice_id} 预览连接失败: {e}")
                return JSONResponse({
                    'success': False,
                    'error': f'vLLM-Omni 预览生成失败: 连接失败',
                    'code': 'VLLM_OMNI_VOICE_PREVIEW_FAILED',
                }, status_code=502)
            except Exception as e:
                logger.error(f"vLLM-Omni 克隆音色 {voice_id} 预览异常: {e}")
                return JSONResponse({
                    'success': False,
                    'error': f'vLLM-Omni 预览生成失败: {str(e)}',
                }, status_code=500)

        native_preview_provider = _get_active_native_preview_provider(_config_manager, voice_id)
        if native_preview_provider:
            native_voice_id, _ = normalize_native_voice(native_preview_provider, voice_id)
            try:
                if native_preview_provider in ('step', 'free', 'free_intl'):
                    # 只读 tts_default.api_key —— 跟 step_realtime_tts_worker 走的 key 对偶；
                    # 不能回退到 audio_api_key（顶上从 tts_custom / AUDIO_API_KEY 取的，都是
                    # GPT-SoVITS / CosyVoice 这种别家 provider 的 bearer，把它透给
                    # api.stepfun.com 一律 401，错误现象比明确缺 key 难排查。
                    # free_intl（海外免费 Gemini 代理）预览同 free，走 www.lanlan.app/tts
                    # 流式合成（StepFun-shape，proxy 把 voice_id 透传给 Gemini）。
                    try:
                        native_tts_config = _config_manager.get_model_api_config('tts_default')
                        native_audio_api_key = native_tts_config.get('api_key', '') or ''
                    except Exception:
                        native_audio_api_key = ''
                    if native_preview_provider == 'step' and not native_audio_api_key:
                        return JSONResponse({
                            'success': False,
                            'error': 'TTS_AUDIO_API_KEY_MISSING',
                            'code': 'TTS_AUDIO_API_KEY_MISSING'
                        }, status_code=400)
                    audio_data = await _synthesize_step_voice_preview(
                        voice_id=native_voice_id,
                        preview_line=text,
                        preview_language=preview_language,
                        audio_api_key=native_audio_api_key,
                        free_mode=(native_preview_provider in ('free', 'free_intl')),
                    )
                elif native_preview_provider == 'gemini':
                    core_config = await _config_manager.aget_core_config()
                    native_audio_api_key = (core_config or {}).get('CORE_API_KEY', '')
                    if not native_audio_api_key:
                        return JSONResponse({
                            'success': False,
                            'error': 'TTS_AUDIO_API_KEY_MISSING',
                            'code': 'TTS_AUDIO_API_KEY_MISSING'
                        }, status_code=400)
                    audio_data = await _synthesize_gemini_native_voice_preview(
                        voice_id=native_voice_id,
                        preview_line=text,
                        audio_api_key=native_audio_api_key,
                    )
                else:
                    return JSONResponse({
                        'success': False,
                        'error': f'当前原生音色暂不支持预览: {native_preview_provider}'
                    }, status_code=400)

                logger.info(f"原生音色 {native_voice_id} 预览音频生成成功，大小: {len(audio_data)} 字节")
                audio_base64 = base64.b64encode(audio_data).decode('utf-8')
                return {
                    'success': True,
                    'audio': audio_base64,
                    'mime_type': 'audio/wav'
                }
            except Exception as e:
                logger.error(f"原生音色 {native_voice_id} 预览生成失败: {e}")
                return JSONResponse({
                    'success': False,
                    'error': f'原生音色预览生成失败: {str(e)}'
                }, status_code=500)

        if provider == 'elevenlabs' or voice_id.startswith(ELEVENLABS_TTS_VOICE_PREFIX):
            try:
                # 从 voice_data 中提取克隆时持久化的 base_url 传给 helper
                audio_data, error_code = await _elevenlabs_synthesize_preview(
                    _config_manager,
                    voice_id,
                    text,
                    base_url=(voice_data or {}).get('elevenlabs_base_url')
                )
                if error_code:
                    return JSONResponse({
                        'success': False,
                        'error': error_code,
                        'code': error_code
                    }, status_code=400)
                audio_base64 = base64.b64encode(audio_data).decode('utf-8')
                logger.info(f"ElevenLabs 音色 {voice_id} 预览音频生成成功，大小: {len(audio_data)} 字节")
                return {
                    'success': True,
                    'audio': audio_base64,
                    'mime_type': 'audio/mpeg'
                }
            except ElevenLabsUpstreamError as e:
                logger.error(f"ElevenLabs 预览上游服务错误 ({e.status_code}): {e}")
                return JSONResponse({
                    'success': False,
                    'error': f'ElevenLabs 上游服务错误: {str(e)}',
                    'code': 'ELEVENLABS_UPSTREAM_ERROR'
                }, status_code=502)
            except ValueError as e:
                # 新增专门的客户端错误 4xx 捕获分支
                logger.error(f"ElevenLabs 预览请求失败 (4xx): {e}")
                return JSONResponse({
                    'success': False,
                    'error': f'ElevenLabs 请求参数或验证错误: {str(e)}',
                    'code': 'ELEVENLABS_API_ERROR'
                }, status_code=400)
            except Exception as e:
                logger.error(f"ElevenLabs 预览生成失败: {e}")
                return JSONResponse({
                    'success': False,
                    'error': f'ElevenLabs预览生成失败: {str(e)}'
                }, status_code=500)
        if is_free_preset_voice:
            try:
                audio_data = await _synthesize_free_voice_preview(
                    voice_id=voice_id,
                    preview_line=text,
                    preview_language=preview_language,
                    audio_api_key=audio_api_key or '',
                )
                logger.info(f"免费预设音色 {voice_id} 预览音频生成成功，大小: {len(audio_data)} 字节")
                audio_base64 = base64.b64encode(audio_data).decode('utf-8')
                return {
                    'success': True,
                    'audio': audio_base64,
                    'mime_type': 'audio/wav'
                }
            except Exception as e:
                logger.error(f"免费预设音色 {voice_id} 预览生成失败: {e}")
                return JSONResponse({
                    'success': False,
                    'error': f'免费预设音色预览生成失败: {str(e)}'
                }, status_code=500)

        if provider in ('minimax', 'minimax_intl'):
            minimax_api_key = _config_manager.get_tts_api_key(provider)
            if not minimax_api_key:
                return JSONResponse({
                    'success': False,
                    'error': 'MINIMAX_API_KEY_MISSING',
                    'code': 'MINIMAX_API_KEY_MISSING'
                }, status_code=400)

            minimax_base_url = (voice_data or {}).get('minimax_base_url') or get_minimax_base_url(provider)
            provider_label = 'MiniMax国际服' if provider == 'minimax_intl' else 'MiniMax国服'

            try:
                minimax_client = MinimaxVoiceCloneClient(api_key=minimax_api_key, base_url=minimax_base_url)
                audio_data = await minimax_client.synthesize_preview(voice_id=voice_id, text=text)
                logger.info(f"{provider_label} 音色 {voice_id} 预览音频生成成功，大小: {len(audio_data)} 字节")
                audio_base64 = base64.b64encode(audio_data).decode('utf-8')
                return {
                    'success': True,
                    'audio': audio_base64,
                    'mime_type': 'audio/mpeg'
                }
            except MinimaxVoiceCloneError as e:
                logger.error(f"{provider_label} 预览生成失败: {e}")
                return JSONResponse({
                    'success': False,
                    'error': f'{provider_label}预览生成失败: {str(e)}'
                }, status_code=500)

        if not audio_api_key:
            return JSONResponse({'success': False, 'error': 'TTS_AUDIO_API_KEY_MISSING', 'code': 'TTS_AUDIO_API_KEY_MISSING'}, status_code=400)

        # 生成音频
        try:
            tts_api_config = _config_manager.get_model_api_config('tts_custom')
        except Exception as e:
            logger.warning("DashScope 预览地域 URL 读取失败，回退到默认地域: %s", e, exc_info=True)
            tts_api_config = {}
        preview_base_url = cosyvoice_base_url or tts_api_config.get('base_url', '')

        from utils.api_config_loader import get_cosyvoice_clone_model
        clone_model = (voice_data or {}).get('clone_model') or get_cosyvoice_clone_model(provider)

        def _do_preview_synthesize():
            import dashscope
            from dashscope.audio.tts_v2 import SpeechSynthesizer
            # 写 module-global + 构造 SpeechSynthesizer + synthesizer.call 全程
            # 拿 DASHSCOPE_GLOBAL_LOCK：dashscope.api_key / base_*_api_url 是
            # 同进程多流程共享的写点，并发跑会互相覆盖、拿别人的 key/地域请求。
            # 这里把整个 call 都圈进锁，因为 SpeechSynthesizer.call 是同步的
            # 一次性请求，锁持续时间 ~ 几秒，不会卡 event loop（在 to_thread 里跑）。
            with DASHSCOPE_GLOBAL_LOCK:
                dashscope.api_key = audio_api_key
                try:
                    configure_dashscope_sdk_urls(
                        dashscope,
                        preview_base_url,
                        websocket_path="inference",
                    )
                except Exception as e:
                    logger.warning("DashScope 预览地域 URL 配置失败，已重置为默认地域: %s", e, exc_info=True)
                    configure_dashscope_sdk_urls(dashscope, "", websocket_path="inference")
                synthesizer = SpeechSynthesizer(model=clone_model, voice=voice_id)
                return synthesizer, synthesizer.call(text)

        try:
            synthesizer, audio_data = await asyncio.to_thread(_do_preview_synthesize)

            if not audio_data:
                request_id = getattr(synthesizer, 'get_last_request_id', lambda: 'unknown')()
                logger.error(f"生成音频失败: audio_data 为空. Request ID: {request_id}")
                return JSONResponse({
                    'success': False,
                    'error': f'生成音频失败 (Request ID: {request_id})。请检查 API Key 额度或音色 ID 是否有效。'
                }, status_code=500)

            logger.info(f"音色 {voice_id} 预览音频生成成功，大小: {len(audio_data)} 字节")

            # 将音频数据转换为 Base64 字符串
            audio_base64 = base64.b64encode(audio_data).decode('utf-8')

            return {
                "success": True,
                "audio": audio_base64,
                "mime_type": "audio/mpeg"
            }
        except Exception as e:
            error_msg = str(e)
            logger.error(f"SpeechSynthesizer 调用异常: {error_msg}")
            return JSONResponse({
                'success': False,
                'error': f'语音合成异常: {error_msg}'
            }, status_code=500)
    except Exception as e:
        logger.error(f"生成音色预览失败: {e}")
        return JSONResponse({'success': False, 'error': f'系统错误: {str(e)}'}, status_code=500)


@router.post('/voices')
async def register_voice(request: Request):
    """Register a new voice."""
    try:
        data = await request.json()
        voice_id = data.get('voice_id')
        voice_data = data.get('voice_data')

        if not voice_id or not voice_data:
            return JSONResponse({
                'success': False,
                'error': 'TTS_VOICE_REGISTER_MISSING_PARAMS',
                'code': 'TTS_VOICE_REGISTER_MISSING_PARAMS'
            }, status_code=400)

        # 准备音色数据
        complete_voice_data = {
            **voice_data,
            'voice_id': voice_id,
            'created_at': datetime.now().isoformat()
        }

        try:
            _config_manager = get_config_manager()
            _config_manager.save_voice_for_current_api(voice_id, complete_voice_data)
        except Exception as e:
            logger.warning(f"保存音色配置失败: {e}")
            return JSONResponse({
                'success': False,
                'error': f'保存音色配置失败: {str(e)}'
            }, status_code=500)

        return {"success": True, "message": "音色注册成功"}
    except Exception as e:
        return JSONResponse({
            'success': False,
            'error': str(e)
        }, status_code=500)


@router.delete('/voices/{voice_id}')
async def delete_voice(voice_id: str):
    """Delete the specified voice."""
    try:
        _config_manager = get_config_manager()
        deleted = _config_manager.delete_voice_for_current_api(voice_id)

        if deleted:
            # 清理所有角色中使用该音色的引用
            _config_manager = get_config_manager()
            session_manager = get_session_manager()
            characters = await _config_manager.aload_characters()
            cleaned_count = 0
            affected_active_names = []

            if '猫娘' in characters:
                for name in characters['猫娘']:
                    if read_legacy_voice_id(get_reserved(characters['猫娘'][name], 'voice_id', default='', legacy_keys=('voice_id',))) == voice_id:
                        set_reserved(characters['猫娘'][name], 'voice_id', '')
                        cleaned_count += 1

                        # 检查该角色是否是当前活跃的 session
                        if name in session_manager and session_manager[name].is_active:
                            affected_active_names.append(name)

            if cleaned_count > 0:
                await _config_manager.asave_characters(characters)

                # 对于受影响的活跃角色，并行通知 + 结束 session（每个 end_session ≈ 1s）
                async def _refresh_one(name):
                    logger.info(f"检测到活跃角色 {name} 的 voice_id 已被删除，准备结束当前语音会话...")
                    # 1. 通知前端按 session 结束路径收口，避免 Electron 为音色切换整页重载。
                    notify_session_ended = getattr(session_manager[name], "send_session_ended_by_server", None)
                    if callable(notify_session_ended):
                        await notify_session_ended()
                    # 2. 结束 session
                    try:
                        await session_manager[name].end_session(by_server=True)
                        logger.info(f"已结束受影响角色 {name} 的 session")
                    except Exception as e:
                        logger.error(f"结束受影响角色 {name} 的 session 时出错: {e}")
                    # 与 set_voice_id 路径对偶：清掉前一会话的失败计数 / 熔断，
                    # 否则下一次 start_session 会被旧熔断静默拦截。
                    session_manager[name].reset_session_start_circuit()

                if affected_active_names:
                    await asyncio.gather(
                        *(_refresh_one(name) for name in affected_active_names),
                        return_exceptions=True,
                    )

                # 自动重新加载配置
                initialize_character_data = get_initialize_character_data()
                await initialize_character_data()

            logger.info(f"已删除音色 '{voice_id}'，并清理了 {cleaned_count} 个角色的引用")
            return {
                "success": True,
                "message": f"音色已删除，已清理 {cleaned_count} 个角色的引用"
            }
        else:
            return JSONResponse({
                'success': False,
                'error': '音色不存在或删除失败'
            }, status_code=404)
    except Exception as e:
        logger.error(f"删除音色时出错: {e}")
        return JSONResponse({
            'success': False,
            'error': f'删除音色失败: {str(e)}'
        }, status_code=500)


# ==================== 智能静音移除 ====================
# 用于存储裁剪任务状态的全局字典
_trim_tasks: dict[str, dict] = {}

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


@router.post('/audio/analyze_silence')
async def analyze_silence(file: UploadFile = File(...)):
    """
    Analyze silence segments in the uploaded audio.

    Returns:
        - original_duration / original_duration_ms: total duration of the original audio
        - silence_duration / silence_duration_ms: total detected silence (total_silence_ms)
        - removable_silence / removable_silence_ms: silence that can actually be removed
        - estimated_duration / estimated_duration_ms: estimated remaining duration after processing
        - saving_percentage: savings percentage (based on the actually removable amount)
        - silence_segments: list of silence segments [{start_ms, end_ms, duration_ms}]
        - has_silence: whether removable silence was detected
    """
    from utils.audio_silence_remover import (
        detect_silence, convert_to_wav_if_needed, format_duration_mmss
    )

    try:
        file_buffer = await _read_limited_stream(file, MAX_UPLOAD_SIZE)
    except _UploadTooLargeError as e:
        return JSONResponse({'error': str(e)}, status_code=413)
    except Exception as e:
        logger.error(f"读取音频文件失败: {e}")
        return JSONResponse({'error': f'读取文件失败: {e}'}, status_code=500)

    try:
        # 转换为 WAV（如果需要）— 阻塞操作，放到线程中执行
        wav_buffer, _ = await asyncio.to_thread(convert_to_wav_if_needed, file_buffer, file.filename)

        # 执行静音检测
        analysis = await asyncio.to_thread(detect_silence, wav_buffer)

        return JSONResponse({
            'success': True,
            'original_duration': format_duration_mmss(analysis.original_duration_ms),
            'original_duration_ms': round(analysis.original_duration_ms, 1),
            'silence_duration': format_duration_mmss(analysis.total_silence_ms),
            'silence_duration_ms': round(analysis.total_silence_ms, 1),
            'removable_silence': format_duration_mmss(analysis.removable_silence_ms),
            'removable_silence_ms': round(analysis.removable_silence_ms, 1),
            'estimated_duration': format_duration_mmss(analysis.estimated_duration_ms),
            'estimated_duration_ms': round(analysis.estimated_duration_ms, 1),
            'saving_percentage': analysis.saving_percentage,
            'silence_segments': [
                {
                    'start_ms': round(s.start_ms, 1),
                    'end_ms': round(s.end_ms, 1),
                    'duration_ms': round(s.duration_ms, 1),
                }
                for s in analysis.silence_segments
            ],
            'has_silence': len(analysis.silence_segments) > 0,
            'sample_rate': analysis.sample_rate,
            'sample_width': analysis.sample_width,
            'channels': analysis.channels,
        })
    except ValueError as e:
        return JSONResponse({'error': str(e)}, status_code=400)
    except Exception as e:
        logger.error(f"静音分析失败: {e}")
        return JSONResponse({'error': f'静音分析失败: {str(e)}'}, status_code=500)


@router.post('/audio/trim_silence')
async def trim_silence_endpoint(file: UploadFile = File(...), task_id: str | None = Form(default=None)):
    """
    Perform silence trimming and return the processed audio.

    Analyzes silence segments first, then shrinks over-long silences down to 200ms (cut from the middle).
    Returns the processed WAV file (base64-encoded) plus an MD5 checksum.
    """
    import uuid
    import base64 as b64
    from utils.audio_silence_remover import (
        detect_silence, trim_silence, convert_to_wav_if_needed,
        format_duration_mmss, CancelledError
    )

    if task_id:
        try:
            uuid.UUID(task_id)
        except ValueError:
            return JSONResponse({'error': '无效的 task_id 格式'}, status_code=400)
        if task_id in _trim_tasks:
            return JSONResponse({'error': '该 task_id 已存在'}, status_code=409)
    else:
        task_id = str(uuid.uuid4())

    # 立即占位，防止 TOCTOU 竞态
    _trim_tasks[task_id] = {'progress': 0, 'cancelled': False, 'phase': 'queued'}

    try:
        file_buffer = await _read_limited_stream(file, MAX_UPLOAD_SIZE)
    except _UploadTooLargeError as e:
        _trim_tasks.pop(task_id, None)
        return JSONResponse({'error': str(e)}, status_code=413)
    except Exception as e:
        _trim_tasks.pop(task_id, None)
        logger.error(f"读取音频文件失败: {e}")
        return JSONResponse({'error': f'读取文件失败: {e}'}, status_code=500)

    try:
        # 文件读取完成，切换到分析阶段
        _trim_tasks[task_id]['phase'] = 'analyzing'

        def progress_cb(pct: int):
            task = _trim_tasks.get(task_id)
            if task is None:
                return
            if task.get('phase', 'analyzing') == 'analyzing':
                # 分析阶段占 0-40%
                task['progress'] = int(pct * 0.4)
            else:
                # 裁剪阶段占 40-100%
                task['progress'] = 40 + int(pct * 0.6)

        def cancel_check() -> bool:
            return _trim_tasks.get(task_id, {}).get('cancelled', False)

        # 转换为 WAV — 阻塞操作，放到线程中执行
        wav_buffer, _ = await asyncio.to_thread(convert_to_wav_if_needed, file_buffer, file.filename)

        # 分析静音
        analysis = await asyncio.to_thread(
            detect_silence, wav_buffer,
            progress_callback=progress_cb, cancel_check=cancel_check,
        )

        if not analysis.silence_segments:
            # 没有可移除的静音
            _trim_tasks.pop(task_id, None)
            return JSONResponse({
                'success': True,
                'has_changes': False,
                'message': '未检测到可移除的静音段',
                'task_id': task_id,
            })

        # 切换到裁剪阶段
        if task_id in _trim_tasks:
            _trim_tasks[task_id]['phase'] = 'trimming'

        # 执行裁剪
        result = await asyncio.to_thread(
            trim_silence, wav_buffer, analysis,
            progress_callback=progress_cb, cancel_check=cancel_check,
        )

        # 编码为 base64
        audio_b64 = b64.b64encode(result.audio_data).decode('ascii')

        # 清理任务
        _trim_tasks.pop(task_id, None)

        return JSONResponse({
            'success': True,
            'has_changes': True,
            'task_id': task_id,
            'audio_base64': audio_b64,
            'md5': result.md5,
            'original_duration': format_duration_mmss(result.original_duration_ms),
            'original_duration_ms': round(result.original_duration_ms, 1),
            'trimmed_duration': format_duration_mmss(result.trimmed_duration_ms),
            'trimmed_duration_ms': round(result.trimmed_duration_ms, 1),
            'removed_silence_ms': round(result.removed_silence_ms, 1),
            'sample_rate': result.sample_rate,
            'sample_width': result.sample_width,
            'channels': result.channels,
            'filename': f"trimmed_{file.filename}",
        })

    except CancelledError:
        _trim_tasks.pop(task_id, None)
        return JSONResponse({
            'success': False,
            'cancelled': True,
            'message': '任务已被用户取消',
            'task_id': task_id,
        })
    except ValueError as e:
        _trim_tasks.pop(task_id, None)
        return JSONResponse({'error': str(e)}, status_code=400)
    except Exception as e:
        _trim_tasks.pop(task_id, None)
        logger.error(f"静音裁剪失败: {e}")
        return JSONResponse({'error': f'静音裁剪失败: {str(e)}'}, status_code=500)


@router.get('/audio/trim_progress/{task_id}')
async def get_trim_progress(task_id: str):
    """Get trim task progress."""
    task = _trim_tasks.get(task_id)
    if not task:
        return JSONResponse({'exists': False, 'progress': 100, 'phase': 'done'})
    return JSONResponse({
        'exists': True,
        'progress': task.get('progress', 0),
        'phase': task.get('phase', 'unknown'),
        'cancelled': task.get('cancelled', False),
    })


@router.post('/audio/trim_cancel/{task_id}')
async def cancel_trim_task(task_id: str):
    """Cancel a trim task."""
    task = _trim_tasks.get(task_id)
    if task:
        task['cancelled'] = True
        return JSONResponse({'success': True, 'message': '取消请求已发送'})
    return JSONResponse({'success': False, 'message': '任务不存在或已完成'})


@router.post('/voice_clone')
async def voice_clone(
    file: UploadFile = File(...),
    prefix: str = Form(...),
    ref_language: str = Form(default="ch"),
    provider: str = Form(default="cosyvoice"),
    ref_text: str = Form(default=""),
):
    """
    Voice cloning endpoint.

    Parameters:
        file: audio file
        prefix: voice prefix name
        ref_language: language of the reference audio; one of: ch, en, fr, de, ja, ko, ru
                      Note: this is the language of the reference audio, not the target voice
        provider: service provider; one of: cosyvoice (Alibaba Bailian), cosyvoice_intl (Alibaba international), minimax (China), minimax_intl (international), elevenlabs, mimo, vllm_omni
        ref_text: transcript of the reference audio (vLLM-Omni inline clone only; must
                  correspond strictly to the audio content)
    """
    # 流式读取上传文件（带大小限制）并增量计算 MD5
    try:
        file_buffer = await _read_limited_stream(file, MAX_UPLOAD_SIZE)
    except _UploadTooLargeError as e:
        return JSONResponse({'error': str(e)}, status_code=413)
    except Exception as e:
        logger.error(f"读取文件到内存失败: {e}")
        return JSONResponse({'error': f'读取文件失败: {e}'}, status_code=500)

    audio_md5 = hashlib.md5(file_buffer.getvalue()).hexdigest()

    # 提前规范化 provider 和 ref_language
    provider = provider.lower().strip() if provider else 'cosyvoice'
    valid_languages = ['ch', 'en', 'fr', 'de', 'ja', 'ko', 'ru']
    ref_language = ref_language.lower().strip() if ref_language else 'ch'
    if ref_language not in valid_languages:
        ref_language = 'ch'

    # vLLM-Omni 克隆必填 ref_text：vLLM-Omni 服务端要求 ref_audio 与 ref_text 严格对应，
    # 缺失 ref_text 会导致合成失败（服务端 ValueError）。前端 voice_clone.js 也做了
    # 同步校验（L1484-1491），后端补上防止绕过前端直接调 API。
    vllm_ref_text = ref_text.strip() if ref_text else ''
    if provider == 'vllm_omni':
        if not vllm_ref_text:
            return JSONResponse(
                {'error': 'vLLM-Omni 克隆必须填写参考音频原文（ref_text）', 'provider': provider},
                status_code=400,
            )
        if len(vllm_ref_text) > 100:
            return JSONResponse(
                {'error': 'vLLM-Omni 参考音频原文过长，请控制在 100 字以内', 'provider': provider},
                status_code=400,
            )

    # 检测是否使用本地 TTS（ws/wss 协议）
    _config_manager = get_config_manager()
    tts_config = _config_manager.get_model_api_config('tts_custom')
    try:
        core_config = await _config_manager.aget_core_config() or {}
    except Exception:
        core_config = {}
    base_url = _local_voice_clone_tts_base_url(tts_config, core_config)
    is_local_tts = _is_local_voice_clone_tts_config(tts_config, core_config)

    if is_local_tts:
        # ==================== 本地 TTS 注册流程 ====================
        # MD5 + ref_language 去重：检查是否已有相同音频 + 相同语言注册过的音色
        existing = _config_manager.find_voice_by_audio_md5('__LOCAL_TTS__', audio_md5, ref_language)
        if existing:
            voice_id, voice_data = existing
            logger.info(f"本地 TTS 音频 MD5 命中，复用 voice_id: {voice_id}")
            return JSONResponse({
                'voice_id': voice_id,
                'message': '已复用现有音色，跳过上传',
                'reused': True,
                'is_local': True
            })

        # 将 ws(s):// 转换为 http(s):// 用于 REST API 调用
        if base_url.startswith('wss://'):
            http_base = 'https://' + base_url[6:]
        else:
            http_base = 'http://' + base_url[5:]

        # 移除可能的 /v1/audio/speech/stream 路径，只保留主机部分
        # 例如: ws://127.0.0.1:50000/v1/audio/speech/stream -> http://127.0.0.1:50000
        if '/v1/' in http_base:
            http_base = http_base.split('/v1/')[0]

        register_url = f"{http_base}/v1/speakers/register"
        logger.info(f"使用本地 TTS 注册: {register_url}")

        try:
            file_buffer.seek(0)

            # 根据用户 demo，API 格式：
            # POST /v1/speakers/register
            # multipart/form-data: speaker_id, prompt_text, prompt_audio
            files = {
                'prompt_audio': (file.filename, file_buffer, 'audio/wav')
            }
            data = {
                'speaker_id': prefix,
                'prompt_text': f"<|{ref_language}|>" if ref_language != 'ch' else "希望你以后能够做的比我还好呦。"
            }

            # per-call AsyncClient: 用户手动上传音色文件触发，冷路径
            async with httpx.AsyncClient(timeout=60, proxy=None, trust_env=False) as client:
                resp = await client.post(register_url, data=data, files=files)

                if resp.status_code == 200:
                    result = resp.json()
                    voice_id = prefix  # 本地 TTS 使用 speaker_id 作为 voice_id

                    # 保存到本地音色库（使用特殊的 key 标识本地 TTS）
                    voice_data = {
                        'voice_id': voice_id,
                        'prefix': prefix,
                        'provider': 'local',
                        'is_local': True,
                        'audio_md5': audio_md5,
                        'ref_language': ref_language,
                        'created_at': datetime.now().isoformat()
                    }
                    try:
                        local_tts_key = '__LOCAL_TTS__'
                        _config_manager.save_voice_for_api_key(local_tts_key, voice_id, voice_data)
                        logger.info(f"本地 TTS voice_id 已保存: {voice_id}")
                    except Exception as save_error:
                        logger.warning(f"保存 voice_id 到音色库失败（本地 TTS 仍可用）: {save_error}")

                    return JSONResponse({
                        'voice_id': voice_id,
                        'message': result.get('message', '本地音色注册成功'),
                        'is_local': True
                    })
                else:
                    error_text = resp.text
                    logger.error(f"本地 TTS 注册失败: {error_text}")
                    return JSONResponse({
                        'error': f'本地 TTS 注册失败: {error_text[:200]}'
                    }, status_code=resp.status_code)

        except httpx.ConnectError as e:
            logger.error(f"无法连接本地 TTS 服务器: {e}")
            return JSONResponse({
                'error': f'无法连接本地 TTS 服务器: {http_base}，请确保服务器已启动'
            }, status_code=503)
        except Exception as e:
            logger.error(f"本地 TTS 注册时发生错误: {e}")
            return JSONResponse({
                'error': f'本地 TTS 注册失败: {str(e)}'
            }, status_code=500)

    # ==================== 云端语音克隆：按 provider 对偶分支 ====================

    # 统一通过 config_manager 获取 API Key
    cosyvoice_runtime = None
    api_key = _config_manager.get_tts_api_key(provider)

    if provider in ('minimax', 'minimax_intl'):
        # ---------- MiniMax（国服 / 国际服）----------
        if not api_key:
            return JSONResponse({
                'error': 'MINIMAX_API_KEY_MISSING',
                'code': 'MINIMAX_API_KEY_MISSING',
                'message': '未配置 MiniMax API Key，请先在设置中填写'
            }, status_code=400)
        base_url = get_minimax_base_url(provider)
        storage_key = f'{get_minimax_storage_prefix(provider)}{api_key[-8:]}'
        provider_label = 'MiniMax国际服' if provider == 'minimax_intl' else 'MiniMax国服'

    elif provider in ('cosyvoice', 'cosyvoice_intl'):
        # ---------- 阿里 CosyVoice（国内 / 国际）----------
        cosyvoice_runtime = _config_manager.get_cosyvoice_clone_runtime(provider)
        api_key = (cosyvoice_runtime.get('api_key') or '').strip()
        if not api_key:
            return JSONResponse({
                'error': 'TTS_AUDIO_API_KEY_MISSING',
                'code': 'TTS_AUDIO_API_KEY_MISSING'
            }, status_code=400)
        base_url = cosyvoice_runtime.get('base_url', '')
        storage_key = cosyvoice_runtime.get('storage_key') or api_key
        provider_label = cosyvoice_runtime.get('provider_label') or '阿里百炼CosyVoice'

    elif provider == 'elevenlabs':
        if not api_key:
            return JSONResponse({
                'error': 'ELEVENLABS_API_KEY_MISSING',
                'code': 'ELEVENLABS_API_KEY_MISSING',
                'message': '未配置 ElevenLabs API Key，请先在设置中填写'
            }, status_code=400)
        base_url = await _get_elevenlabs_base_url(_config_manager)
        storage_key = f'__ELEVENLABS__{api_key[-8:]}'
        provider_label = 'ElevenLabs'

    elif provider == 'mimo':
        if not api_key:
            return JSONResponse({
                'error': 'MIMO_API_KEY_MISSING',
                'code': 'MIMO_API_KEY_MISSING',
                'message': '未配置 MiMo API Key，请先在设置中填写'
            }, status_code=400)
        # base_url 须与 api_key 同源：assistApi=mimo（含 Token Plan）时 get_core_config 已把
        # OPENROUTER_URL 解析成对应端点（普通 / token-plan-*），get_tts_api_key('mimo') 也据此
        # 返回配套 key；否则用默认 xiaomimimo 端点。和 _mimo_resolve 的 base_url 规则对偶。
        if str(core_config.get('assistApi') or '').strip().lower() == 'mimo':
            base_url = (core_config.get('OPENROUTER_URL') or '').strip()
        else:
            base_url = ''
        storage_key = f'{MIMO_VOICE_STORAGE_KEY}{api_key[-8:]}'
        provider_label = 'MiMo'

    elif provider == 'vllm_omni':
        # vLLM-Omni 是本地 self-hosted 服务，没有 API key、也没有远端音色注册接口。克隆走
        # 「内联参考音频」范式（对偶 MiMo）：参考音频 base64 + ref_text 整段落进 voice_storage
        # 的 voice_meta，每次合成时内联进 session.config 的 ref_audio/ref_text。桶名固定
        # __VLLM_OMNI__（无 key 后缀，因本地服务无 key 可分桶）。base_url 取当前配置的
        # ttsModelUrl（与 _vllm_omni_resolve 同源），仅存档备查，dispatch 仍按当前配置重解析。
        base_url = (core_config.get('ttsModelUrl') or core_config.get('TTS_MODEL_URL') or '').strip()
        storage_key = '__VLLM_OMNI__'
        provider_label = 'vLLM-Omni'

    elif provider == 'doubao_tts':
        if not api_key:
            return JSONResponse({
                'error': 'DOUBAO_TTS_API_KEY_MISSING',
                'code': 'DOUBAO_TTS_API_KEY_MISSING',
                'message': '未配置豆包语音 API Key，请先在设置中填写'
            }, status_code=400)
        base_url = DOUBAO_TTS_DEFAULT_BASE_URL
        storage_key = f'{DOUBAO_VOICE_STORAGE_KEY}{api_key[-8:]}'
        provider_label = '豆包语音'

    else:
        return JSONResponse({'error': f'不支持的 provider: {provider}'}, status_code=400)

    # ---------- 公共流程：MD5 去重 ----------
    if provider in ('cosyvoice', 'cosyvoice_intl'):
        existing = _config_manager.find_cosyvoice_voice_by_audio_md5(provider, audio_md5, ref_language)
    else:
        existing = _config_manager.find_voice_by_audio_md5(storage_key, audio_md5, ref_language)
    if existing:
        voice_id_ex, voice_data_ex = existing
        # vLLM-Omni：同音频 + 同语言但不同 ref_text 视为不同音色（转录修正场景），
        # 不命中去重，允许用户用修正后的 ref_text 重新注册。
        if provider == 'vllm_omni':
            existing_ref_text = str((voice_data_ex or {}).get('clone_ref_text') or '').strip()
            if existing_ref_text != vllm_ref_text:
                # 清理旧条目：否则 find_voice_by_audio_md5 按插入顺序总是先返回
                # 最旧的匹配条目，旧 voice 永远占位，新注册无限重复创建；
                # 旧 voice 也仍出现在音色列表中，用户可能选到错误音色。
                try:
                    _config_manager.delete_voice_for_current_api(voice_id_ex)
                    logger.info(
                        f"vLLM-Omni 克隆音色 {voice_id_ex} ref_text 变更"
                        f"（旧: {existing_ref_text!r} → 新: {vllm_ref_text!r}），已删除旧条目")
                except Exception:
                    logger.warning(
                        "vLLM-Omni 旧条目 %s 删除失败，可能导致下次去重仍命中旧条目",
                        voice_id_ex, exc_info=True)
                existing = None
    if existing:
        voice_id, voice_data = existing
        logger.info(f"{provider_label} 音频 MD5 命中，复用 voice_id: {voice_id}")
        return JSONResponse({
            'voice_id': voice_id,
            'message': f'已复用现有{provider_label}音色，跳过上传',
            'reused': True,
            'provider': provider
        })

    # ---------- 公共流程：音频规范化 ----------
    try:
        if provider in ('cosyvoice', 'cosyvoice_intl'):
            mime_type, error_msg = validate_audio_file(file_buffer, file.filename)
            if not mime_type:
                return JSONResponse({'error': error_msg}, status_code=400)
        normalized_buffer, normalized_filename, audio_meta = await asyncio.to_thread(
            normalize_voice_clone_api_audio,
            file_buffer,
            file.filename or 'prompt_audio.wav',
        )
    except ValueError as e:
        return JSONResponse({'error': str(e)}, status_code=400)
    logger.info(
        "%s 语音克隆参考音频已规范化: %sHz/%sch -> %sHz/mono",
        provider_label,
        audio_meta['original']['sample_rate'],
        audio_meta['original']['channels'],
        audio_meta['normalized']['sample_rate'],
    )

    # ---------- 按 provider 调用对应克隆 API ----------
    try:
        if provider in ('minimax', 'minimax_intl'):
            original_prefix, minimax_prefix = _build_minimax_request_prefix(prefix, provider_label)

            minimax_lang = minimax_normalize_language(ref_language)
            client = MinimaxVoiceCloneClient(api_key=api_key, base_url=base_url)
            voice_id = await client.clone_voice(
                audio_buffer=normalized_buffer,
                filename=normalized_filename,
                prefix=minimax_prefix,
                language=minimax_lang,
            )
            voice_data = {
                'voice_id': voice_id,
                'prefix': original_prefix,  # 保存原始前缀用于显示
                'minimax_prefix': minimax_prefix,  # 保存实际提交给 MiniMax 的安全前缀
                'audio_md5': audio_md5,
                'ref_language': ref_language,
                'minimax_language': minimax_lang,
                'provider': provider,
                'minimax_base_url': base_url,
                'created_at': datetime.now().isoformat()
            }

        elif provider == 'elevenlabs':
            voice_id = await _elevenlabs_clone_voice(
                api_key=api_key,
                base_url=base_url,
                audio_buffer=normalized_buffer,
                filename=normalized_filename,
                name=prefix,
            )
            voice_data = {
                'voice_id': voice_id,
                'raw_voice_id': _raw_elevenlabs_voice_id(voice_id),
                'prefix': prefix,
                'audio_md5': audio_md5,
                'ref_language': ref_language,
                'provider': 'elevenlabs',
                'source': 'clone',
                'elevenlabs_base_url': base_url,
                'created_at': datetime.now().isoformat()
            }

        elif provider == 'mimo':
            # MiMo 没有远端注册接口（已核实官方文档：voiceclone 只能每次内联参考音频，无
            # create-voice / 远端 voice_id）。所以严格对偶 MiniMax 的做法是：把克隆身份整段
            # 落进 voice_storage.json 的 voice_meta——MiniMax 那里存的是远端 voice_id，这里存
            # 参考音频本身（base64）。不另起本地文件存储，voice_meta 随 voice_storage.json 一起
            # 云同步（与 MiniMax 同构）。校验样本可用后再落库。
            client = MimoVoiceCloneClient(api_key=api_key, base_url=base_url or None)
            sample_bytes = normalized_buffer.getvalue()
            await client.validate_sample(sample_bytes, mime_type='audio/wav')
            # voice_id 维度必须与 MD5 去重键 (storage_key, audio_md5, ref_language) 一致：
            #  - 含 key 末 8 位：同一音频在不同 MiMo key 下落不同 voice_id，避免跨 __MIMO__ 桶
            #    同名被 delete_voice_for_current_api 按 id 扫桶误删（Codex review #1851）。
            #  - 含 ref_language：去重带 ref_language，若 id 不带则「同音频换语言」绕过去重却又
            #    生成同名 id，覆盖掉前一条 voice_data（CodeRabbit review #1851）。
            voice_id = f'mimo-clone-{api_key[-8:]}-{ref_language}-{audio_md5[:12]}'
            voice_data = {
                'voice_id': voice_id,
                'prefix': prefix,
                'audio_md5': audio_md5,
                'ref_language': ref_language,
                'provider': 'mimo',
                'source': 'clone',
                # 克隆身份：参考音频 base64（对偶 MiniMax 的远端 voice_id），dispatch/preview
                # 读它内联进 voiceclone 请求。存进 voice_meta 即随 voice_storage.json 云同步。
                'clone_sample_b64': base64.b64encode(sample_bytes).decode('ascii'),
                'clone_sample_mime': 'audio/wav',
                # base_url 存进 voice_meta（对偶 minimax_base_url）；dispatch 在 assistApi=mimo
                # （Token Plan 的唯一场景）时仍按当前配置重解析，保证 key/端点配套。
                'mimo_base_url': base_url or '',
                'created_at': datetime.now().isoformat()
            }

        elif provider == 'vllm_omni':
            # vLLM-Omni 内联克隆（对偶 MiMo）：无远端注册接口，参考音频 base64 + ref_text 整段
            # 落进 voice_storage 的 voice_meta，dispatch 时读出来内联进 session.config 的
            # ref_audio/ref_text。vLLM-Omni 无远端校验接口（不像 MiMo 有 validate_sample），
            # 参考音频运行时才用，所以这里跳过校验直接落库。
            sample_bytes = normalized_buffer.getvalue()
            # voice_id 维度与去重键一致：含 ref_language 避免同音频换语言覆盖，含
            # ref_text hash 避免同音频不同转录命中旧 voice（转录修正场景）。
            # 无 key 后缀（本地服务无 key），桶 __VLLM_OMNI__ 是全局唯一分区。
            ref_text_hash = hashlib.md5(vllm_ref_text.encode('utf-8')).hexdigest()[:8]
            voice_id = f'vllm-omni-clone-{ref_language}-{audio_md5[:12]}-{ref_text_hash}'
            voice_data = {
                'voice_id': voice_id,
                'prefix': prefix,
                'audio_md5': audio_md5,
                'ref_language': ref_language,
                'provider': 'vllm_omni',
                'source': 'clone',
                # 克隆身份：参考音频 base64（对偶 MiMo 的 clone_sample_b64），dispatch/preview
                # 读它内联进 session.config 的 ref_audio。存进 voice_meta 即随 voice_storage 云同步。
                'clone_sample_b64': base64.b64encode(sample_bytes).decode('ascii'),
                'clone_sample_mime': 'audio/wav',
                # 参考音频原文：vLLM-Omni 克隆要求 ref_text 与音频严格对应，作 session.config.ref_text。
                'clone_ref_text': vllm_ref_text,
                # base_url 存进 voice_meta（对偶 mimo_base_url）；dispatch 仍按当前配置重解析。
                'vllm_omni_base_url': base_url or '',
                'created_at': datetime.now().isoformat()
            }

        elif provider == 'doubao_tts':
            try:
                speaker_id = _normalize_doubao_voice_clone_speaker_id(prefix)
            except ValueError as exc:
                return JSONResponse({
                    'error': 'DOUBAO_SPEAKER_ID_REQUIRED',
                    'code': 'DOUBAO_SPEAKER_ID_REQUIRED',
                    'message': str(exc),
                }, status_code=400)
            resource_id = DOUBAO_TTS_DEFAULT_RESOURCE_ID
            client = DoubaoVoiceCloneClient(
                api_key=api_key,
                base_url=base_url,
                resource_id=resource_id,
            )
            voice_id = await client.clone_voice(
                normalized_buffer,
                speaker_id=speaker_id,
                display_name=speaker_id,
                audio_format='wav',
            )
            voice_data = {
                'voice_id': voice_id,
                'prefix': prefix,
                'audio_md5': audio_md5,
                'ref_language': ref_language,
                'provider': 'doubao_tts',
                'source': 'clone',
                'doubao_base_url': base_url,
                'doubao_resource_id': resource_id,
                'clone_model': resource_id,
                'created_at': datetime.now().isoformat()
            }

        else:  # cosyvoice / cosyvoice_intl
            from utils.api_config_loader import get_cosyvoice_clone_model
            clone_model = get_cosyvoice_clone_model(provider)
            language_hints = qwen_language_hints(ref_language)
            dashscope_base_url = (cosyvoice_runtime or {}).get('base_url', '')
            client = QwenVoiceCloneClient(
                api_key=api_key,
                tflink_upload_url=TFLINK_UPLOAD_URL,
                dashscope_base_url=dashscope_base_url,
            )
            voice_id, tmp_url, _request_id = await client.clone_voice(
                audio_buffer=normalized_buffer,
                filename=normalized_filename,
                prefix=prefix,
                language_hints=language_hints,
                target_model=clone_model,
            )
            voice_data = {
                'voice_id': voice_id,
                'prefix': prefix,
                'file_url': tmp_url,
                'audio_md5': audio_md5,
                'ref_language': ref_language,
                'provider': provider,
                'dashscope_base_url': dashscope_base_url,
                'clone_model': clone_model,
                'created_at': datetime.now().isoformat()
            }

        logger.info(f"{provider_label} 音色注册成功，voice_id: {voice_id}")

    except ElevenLabsUpstreamError as e:
        logger.error(f"ElevenLabs 音色注册上游服务错误 ({e.status_code}): {e}")
        return JSONResponse({
            'error': f'ElevenLabs上游服务错误: {str(e)}',
            'code': 'ELEVENLABS_UPSTREAM_ERROR',
            'provider': provider,
        }, status_code=502)
    except (MinimaxVoiceCloneError, QwenVoiceCloneError, MimoVoiceCloneError, DoubaoTtsError) as e:
        logger.error(f"{provider_label} 音色注册失败: {e}")
        error_detail = str(e)
        if '超时' in error_detail:
            return JSONResponse({'error': error_detail, 'provider': provider}, status_code=408)
        elif '下载' in error_detail:
            return JSONResponse({'error': error_detail, 'provider': provider}, status_code=415)
        return JSONResponse({'error': f'{provider_label}音色注册失败: {error_detail}', 'provider': provider}, status_code=500)
    except ValueError as e:
        return JSONResponse({'error': str(e)}, status_code=400)
    except Exception as e:
        logger.error(f"{provider_label} 音色注册时发生错误: {e}")
        return JSONResponse({'error': f'{provider_label}音色注册失败: {str(e)}', 'provider': provider}, status_code=500)

    # ---------- 公共流程：保存到本地音色库 ----------
    try:
        _config_manager.save_voice_for_api_key(storage_key, voice_id, voice_data)
        logger.info(f"{provider_label} voice_id 已保存到音色库: {voice_id}")
    except Exception as save_error:
        logger.error(f"保存 {provider_label} voice_id 到音色库失败: {save_error}")
        # MiMo 与其它家不同：它没有远端音色资源（克隆身份 = voice_meta 里的样本 base64，
        # save 失败＝什么都没落库，voice_id 是本地生成、此刻指向空）。返回 200+local_save_failed
        # 会给用户一个根本不存在的 voice_id。而且 MiMo 不存在"重试会重复创建远端资源"的代价
        # （validate 不创建任何东西），重试是安全的——所以这里返回真失败，让客户端知道并可重试
        # （Codex review #1851；与 PR #528「远端已创建→200 partial」规则的前提相反）。
        if provider in ('mimo', 'vllm_omni'):
            return JSONResponse({
                'error': f'{provider_label}音色保存失败: {str(save_error)}',
                'code': 'TTS_VOICE_SAVE_FAILED',
                'provider': provider,
            }, status_code=500)
        # 其它 provider（cosyvoice/minimax/elevenlabs）远端音色已创建，本地保存失败仍返回
        # 200+local_save_failed，避免客户端重试重复创建远端资源、浪费配额（PR #528 既定规则）。
        return JSONResponse({
            'voice_id': voice_id,
            'message': f'{provider_label}音色注册成功，但本地保存失败',
            'local_save_failed': True,
            'error': str(save_error),
            'provider': provider,
        }, status_code=200)

    return JSONResponse({
        'voice_id': voice_id,
        'message': f'{provider_label}音色注册成功并已保存到音色库',
        'provider': provider,
    })


def _validate_voice_design_description(raw: object) -> tuple[str, JSONResponse | None]:
    """Validate a voice-design description against ElevenLabs' 20–1000 char window."""
    description = str(raw or '').strip()
    if len(description) < ELEVENLABS_VOICE_DESIGN_DESC_MIN:
        return description, JSONResponse({
            'error': 'VOICE_DESIGN_DESCRIPTION_TOO_SHORT',
            'code': 'VOICE_DESIGN_DESCRIPTION_TOO_SHORT',
            'min': ELEVENLABS_VOICE_DESIGN_DESC_MIN,
        }, status_code=400)
    if len(description) > ELEVENLABS_VOICE_DESIGN_DESC_MAX:
        return description, JSONResponse({
            'error': 'VOICE_DESIGN_DESCRIPTION_TOO_LONG',
            'code': 'VOICE_DESIGN_DESCRIPTION_TOO_LONG',
            'max': ELEVENLABS_VOICE_DESIGN_DESC_MAX,
        }, status_code=400)
    return description, None


@router.post('/voice_design_preview')
async def voice_design_preview(request: Request):
    """Generate ElevenLabs voice-design previews from a text description.

    Returns a list of previews ``[{generated_voice_id, audio (base64 mp3),
    media_type, duration_secs}]`` for the user to audition; nothing is persisted
    yet — :func:`voice_design_create` lands the chosen preview as a voice.
    """
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({'error': 'INVALID_JSON', 'code': 'INVALID_JSON'}, status_code=400)
    # request.json() 也可能返回数组/字符串/null（合法 JSON 但非对象）——直接 .get 会 500。
    if not isinstance(data, dict):
        return JSONResponse({'error': 'INVALID_JSON', 'code': 'INVALID_JSON'}, status_code=400)

    description, err = _validate_voice_design_description(data.get('description'))
    if err is not None:
        return err

    _config_manager = get_config_manager()
    api_key = _config_manager.get_tts_api_key('elevenlabs')
    if not api_key:
        return JSONResponse({
            'error': 'ELEVENLABS_API_KEY_MISSING',
            'code': 'ELEVENLABS_API_KEY_MISSING',
            'message': '未配置 ElevenLabs API Key，请先在设置中填写',
        }, status_code=400)
    base_url = await _get_elevenlabs_base_url(_config_manager)

    try:
        previews = await _elevenlabs_design_previews(
            api_key=api_key, base_url=base_url, voice_description=description,
        )
    except ElevenLabsUpstreamError as e:
        logger.error(f"ElevenLabs voice design 上游错误 ({e.status_code}): {e}")
        return JSONResponse({
            'error': f'ElevenLabs上游服务错误: {str(e)}',
            'code': 'ELEVENLABS_UPSTREAM_ERROR',
        }, status_code=502)
    except ValueError as e:
        return JSONResponse({'error': str(e)}, status_code=400)
    except Exception as e:
        logger.error(f"ElevenLabs voice design 失败: {e}")
        return JSONResponse({'error': f'语音设计失败: {str(e)}'}, status_code=500)

    # 只保留既有 generated_voice_id 又带 audio_base_64 的可试听项——预览接口的契约是给前端
    # 可试听的选项。若过滤后为空（上游没回可用音频），按上游异常返回 502，不伪装成 success。
    result_previews = [
        {
            'generated_voice_id': p.get('generated_voice_id', ''),
            'audio': p.get('audio_base_64', ''),
            'media_type': p.get('media_type', 'audio/mpeg'),
            'duration_secs': p.get('duration_secs'),
        }
        for p in previews
        if isinstance(p, dict) and p.get('generated_voice_id') and p.get('audio_base_64')
    ]
    if not result_previews:
        return JSONResponse({
            'error': 'ElevenLabs 未返回可试听的语音预览',
            'code': 'ELEVENLABS_PREVIEWS_EMPTY',
        }, status_code=502)
    return JSONResponse({'success': True, 'previews': result_previews})


@router.post('/voice_design_create')
async def voice_design_create(request: Request):
    """Persist a chosen ElevenLabs design preview into a reusable voice.

    The voice lands as a normal ElevenLabs voice (``source='design'``) in the
    ElevenLabs voice_storage bucket, so dispatch reuses the existing ElevenLabs
    clone path (``voice_meta.provider=='elevenlabs'``).
    """
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({'error': 'INVALID_JSON', 'code': 'INVALID_JSON'}, status_code=400)
    if not isinstance(data, dict):
        return JSONResponse({'error': 'INVALID_JSON', 'code': 'INVALID_JSON'}, status_code=400)

    description, err = _validate_voice_design_description(data.get('description'))
    if err is not None:
        return err
    generated_voice_id = str(data.get('generated_voice_id') or '').strip()
    if not generated_voice_id:
        return JSONResponse({
            'error': 'VOICE_DESIGN_PREVIEW_MISSING',
            'code': 'VOICE_DESIGN_PREVIEW_MISSING',
        }, status_code=400)
    name = str(data.get('name') or data.get('prefix') or '').strip()

    _config_manager = get_config_manager()
    api_key = _config_manager.get_tts_api_key('elevenlabs')
    if not api_key:
        return JSONResponse({
            'error': 'ELEVENLABS_API_KEY_MISSING',
            'code': 'ELEVENLABS_API_KEY_MISSING',
            'message': '未配置 ElevenLabs API Key，请先在设置中填写',
        }, status_code=400)
    base_url = await _get_elevenlabs_base_url(_config_manager)

    try:
        voice_id = await _elevenlabs_create_voice_from_preview(
            api_key=api_key,
            base_url=base_url,
            voice_name=name or 'NEKO Designed Voice',
            voice_description=description,
            generated_voice_id=generated_voice_id,
        )
    except ElevenLabsUpstreamError as e:
        logger.error(f"ElevenLabs voice design create 上游错误 ({e.status_code}): {e}")
        return JSONResponse({
            'error': f'ElevenLabs上游服务错误: {str(e)}',
            'code': 'ELEVENLABS_UPSTREAM_ERROR',
        }, status_code=502)
    except ValueError as e:
        return JSONResponse({'error': str(e)}, status_code=400)
    except Exception as e:
        logger.error(f"ElevenLabs voice design create 失败: {e}")
        return JSONResponse({'error': f'语音设计保存失败: {str(e)}'}, status_code=500)

    voice_data = {
        'voice_id': voice_id,
        'raw_voice_id': _raw_elevenlabs_voice_id(voice_id),
        'prefix': name or 'Designed Voice',
        'provider': 'elevenlabs',
        'source': 'design',
        'design_description': description,
        'elevenlabs_base_url': base_url,
        'created_at': datetime.now().isoformat(),
    }
    storage_key = f'__ELEVENLABS__{api_key[-8:]}'
    try:
        _config_manager.save_voice_for_api_key(storage_key, voice_id, voice_data)
    except Exception as save_error:
        logger.error(f"保存 ElevenLabs 设计音色到音色库失败: {save_error}")
        return JSONResponse({
            'voice_id': voice_id,
            'message': 'ElevenLabs 设计音色创建成功，但本地保存失败',
            'local_save_failed': True,
            'error': str(save_error),
            'provider': 'elevenlabs',
        }, status_code=200)

    return JSONResponse({
        'voice_id': voice_id,
        'message': 'ElevenLabs 设计音色创建成功并已保存到音色库',
        'provider': 'elevenlabs',
        'source': 'design',
    })


@router.post('/voice_clone_direct')
async def voice_clone_direct(request: Request):
    """
    Direct-link voice cloning endpoint — skips the audio upload step and registers the voice directly from the provided direct URL.

    Supports the CosyVoice, MiniMax and ElevenLabs providers:
    - CosyVoice: registers the voice directly with the direct-link URL
    - MiniMax: downloads the audio file first, then uploads it to the MiniMax server to register the voice

    Request body:
        {
            "direct_link": "https://example.com/audio.wav",  // direct audio URL
            "prefix": "custom_prefix",                        // voice prefix name
            "ref_language": "ch",                             // reference audio language
            "provider": "cosyvoice"                           // provider: cosyvoice / cosyvoice_intl / minimax / minimax_intl / elevenlabs
        }
    """
    try:
        data = await request.json()
    except Exception as e:
        return JSONResponse({'error': f'请求体解析失败: {e}'}, status_code=400)

    direct_link = data.get('direct_link', '').strip()
    prefix = data.get('prefix', '').strip()
    ref_language = data.get('ref_language', 'ch').lower().strip()
    provider = data.get('provider', 'cosyvoice').lower().strip()

    # 参数验证
    if not direct_link:
        return JSONResponse({'error': '缺少 direct_link 参数'}, status_code=400)
    if not prefix:
        return JSONResponse({'error': '缺少 prefix 参数'}, status_code=400)
    try:
        await _validate_direct_link_target(direct_link)
    except DirectLinkSecurityError as e:
        return JSONResponse({
            'error': str(e),
            'code': e.code,
        }, status_code=400)
    except Exception as e:
        logger.warning(f"SSRF检查失败: {e}")
        return JSONResponse({'error': '直链安全检查失败'}, status_code=400)

    # 验证语言参数
    valid_languages = ['ch', 'en', 'fr', 'de', 'ja', 'ko', 'ru']
    if ref_language not in valid_languages:
        ref_language = 'ch'

    # 验证服务商参数
    valid_providers = ['minimax', 'minimax_intl', 'cosyvoice', 'cosyvoice_intl', 'elevenlabs']
    if provider not in valid_providers:
        return JSONResponse({
            'error': f'无效的服务商: {provider}',
            'code': 'TTS_PROVIDER_INVALID',
            'message': f'支持的服务商: {", ".join(valid_providers)}',
            'details': {'provider': provider, 'valid_providers': ', '.join(valid_providers)},
        }, status_code=400)

    # 获取 API Key
    _config_manager = get_config_manager()
    cosyvoice_runtime = None
    if provider in ('cosyvoice', 'cosyvoice_intl'):
        cosyvoice_runtime = _config_manager.get_cosyvoice_clone_runtime(provider)
        api_key = (cosyvoice_runtime.get('api_key') or '').strip()
    else:
        api_key = _config_manager.get_tts_api_key(provider)
    if not api_key:
        if provider in ('minimax', 'minimax_intl'):
            return JSONResponse({
                'error': 'MINIMAX_API_KEY_MISSING',
                'code': 'MINIMAX_API_KEY_MISSING',
                'message': '未配置 MiniMax API Key，请先在设置中填写'
            }, status_code=400)
        if provider == 'elevenlabs':
            return JSONResponse({
                'error': 'ELEVENLABS_API_KEY_MISSING',
                'code': 'ELEVENLABS_API_KEY_MISSING',
                'message': '未配置 ElevenLabs API Key，请先在设置中填写'
            }, status_code=400)
        else:
            return JSONResponse({
                'error': 'TTS_AUDIO_API_KEY_MISSING',
                'code': 'TTS_AUDIO_API_KEY_MISSING'
            }, status_code=400)

    # 导入所有可能用到的异常类（用于后面的异常捕获）
    from utils.voice_clone import MinimaxVoiceCloneError, QwenVoiceCloneError

    # 设置服务商相关参数
    if provider in ('minimax', 'minimax_intl'):
        from utils.voice_clone import (
            MinimaxVoiceCloneClient,
            minimax_normalize_language,
            get_minimax_base_url,
            get_minimax_storage_prefix
        )
        base_url = get_minimax_base_url(provider)
        storage_key = f'{get_minimax_storage_prefix(provider)}{api_key[-8:]}'
        provider_label = 'MiniMax国际服' if provider == 'minimax_intl' else 'MiniMax国服'
    elif provider == 'elevenlabs':
        base_url = await _get_elevenlabs_base_url(_config_manager)
        storage_key = f'__ELEVENLABS__{api_key[-8:]}'
        provider_label = 'ElevenLabs'
    else:  # cosyvoice / cosyvoice_intl
        from utils.voice_clone import QwenVoiceCloneClient, qwen_language_hints
        base_url = (cosyvoice_runtime or {}).get('base_url', '')
        storage_key = (cosyvoice_runtime or {}).get('storage_key') or api_key
        provider_label = (cosyvoice_runtime or {}).get('provider_label') or '阿里百炼CosyVoice'

    # 验证直链是否可访问（HEAD失败时回退到GET）
    # 每一跳都固定到已校验的解析结果，避免校验后请求阶段被 DNS rebinding 绕过。
    try:
        head_resp = await _request_direct_link_follow_redirects("HEAD", direct_link)
        try:
            if head_resp.status_code >= 400:
                # HEAD失败，尝试GET
                logger.warning(f"HEAD请求失败({head_resp.status_code})，尝试GET请求: {direct_link}")
                get_resp = await _request_direct_link_follow_redirects(
                    "GET",
                    direct_link,
                    stream=True,
                    headers={"Range": "bytes=0-0"},
                )
                try:
                    if get_resp.status_code >= 400:
                        return JSONResponse({
                            'error': f'直链无法访问，状态码: {get_resp.status_code}',
                            'code': 'DIRECT_LINK_INACCESSIBLE'
                        }, status_code=400)
                finally:
                    await get_resp.aclose()
        finally:
            await head_resp.aclose()
    except DirectLinkSecurityError as e:
        logger.warning(f"直链安全校验失败: {e}")
        return JSONResponse({
            'error': str(e),
            'code': e.code,
        }, status_code=400)
    except Exception as e:
        logger.warning(f"直链验证失败: {e}")
        # 不阻断流程，只是警告

    # 根据服务商类型执行不同的克隆逻辑
    try:
        if provider in ('minimax', 'minimax_intl'):
            # ========== MiniMax 直链克隆流程 ==========
            # 1. 下载音频文件（使用流式读取避免内存问题）
            logger.info(f"开始下载直链音频: {direct_link}")
            MAX_FILE_SIZE = 100 * 1024 * 1024  # 100MB限制

            filename, audio_bytes = await _download_direct_link_audio(
                direct_link,
                max_file_size=MAX_FILE_SIZE,
            )

            logger.info(f"音频下载完成: {filename}, 大小: {len(audio_bytes)} bytes")

            # 2. 计算音频内容的 MD5 用于去重（与文件上传路径保持一致）
            import hashlib
            audio_md5 = hashlib.md5(audio_bytes).hexdigest()

            # 3. MD5 去重检查
            existing = _config_manager.find_cosyvoice_voice_by_audio_md5(provider, audio_md5, ref_language)
            if existing:
                voice_id, voice_data = existing
                logger.info(f"{provider_label} 直链 MD5 命中，复用 voice_id: {voice_id}")
                return JSONResponse({
                    'voice_id': voice_id,
                    'message': f'已复用现有{provider_label}音色，跳过注册',
                    'reused': True,
                    'provider': provider
                })

            # 2. 音频归一化处理（与文件上传路径保持一致）
            from utils.audio import normalize_voice_clone_api_audio
            original_buffer = io.BytesIO(audio_bytes)
            normalized_buffer, normalized_filename, _ = await asyncio.to_thread(
                normalize_voice_clone_api_audio,
                original_buffer, filename
            )

            original_prefix, minimax_prefix = _build_minimax_request_prefix(prefix, provider_label)

            # 4. 使用 MinimaxVoiceCloneClient 上传并注册音色
            minimax_lang = minimax_normalize_language(ref_language)
            client = MinimaxVoiceCloneClient(api_key=api_key, base_url=base_url)

            voice_id = await client.clone_voice(
                audio_buffer=normalized_buffer,
                filename=normalized_filename,
                prefix=minimax_prefix,
                language=minimax_lang,
            )

            voice_data = {
                'voice_id': voice_id,
                'prefix': original_prefix,  # 保存原始前缀用于显示
                'minimax_prefix': minimax_prefix,  # 保存实际提交给 MiniMax 的安全前缀
                'direct_link': direct_link,
                'audio_md5': audio_md5,
                'ref_language': ref_language,
                'minimax_language': minimax_lang,
                'provider': provider,
                'minimax_base_url': base_url,
                'created_at': datetime.now().isoformat(),
                'is_direct_link': True
            }

            logger.info(f"{provider_label} 直链音色注册成功，voice_id: {voice_id}")

        elif provider == 'elevenlabs':
            MAX_FILE_SIZE = 100 * 1024 * 1024

            filename, audio_bytes = await _download_direct_link_audio(
                direct_link,
                max_file_size=MAX_FILE_SIZE,
            )

            audio_md5 = hashlib.md5(audio_bytes).hexdigest()

            existing = _config_manager.find_voice_by_audio_md5(storage_key, audio_md5, ref_language)
            if existing:
                voice_id, voice_data = existing
                logger.info(f"{provider_label} 直链 MD5 命中，复用 voice_id: {voice_id}")
                return JSONResponse({
                    'voice_id': voice_id,
                    'message': f'已复用现有{provider_label}音色，跳过注册',
                    'reused': True,
                    'provider': provider
                })

            normalized_buffer, normalized_filename, _ = await asyncio.to_thread(
                normalize_voice_clone_api_audio,
                io.BytesIO(audio_bytes),
                filename,
            )
            voice_id = await _elevenlabs_clone_voice(
                api_key=api_key,
                base_url=base_url,
                audio_buffer=normalized_buffer,
                filename=normalized_filename,
                name=prefix,
            )
            voice_data = {
                'voice_id': voice_id,
                'raw_voice_id': _raw_elevenlabs_voice_id(voice_id),
                'prefix': prefix,
                'direct_link': direct_link,
                'audio_md5': audio_md5,
                'ref_language': ref_language,
                'provider': 'elevenlabs',
                'elevenlabs_base_url': base_url,
                'created_at': datetime.now().isoformat(),
                'is_direct_link': True
            }

            logger.info(f"{provider_label} 直链音色注册成功，voice_id: {voice_id}")

        else:  # cosyvoice / cosyvoice_intl
            # ========== CosyVoice 直链克隆流程 ==========
            # 1. 下载音频文件以计算内容MD5（使用流式读取避免内存问题）
            logger.info(f"开始下载直链音频用于CosyVoice: {direct_link}")
            MAX_FILE_SIZE = 100 * 1024 * 1024  # 100MB限制

            _, audio_bytes = await _download_direct_link_audio(
                direct_link,
                max_file_size=MAX_FILE_SIZE,
            )

            logger.info(f"音频下载完成，大小: {len(audio_bytes)} bytes")

            # 2. 计算音频内容的 MD5 用于去重
            import hashlib
            audio_md5 = hashlib.md5(audio_bytes).hexdigest()

            # 3. MD5 去重检查
            existing = _config_manager.find_voice_by_audio_md5(storage_key, audio_md5, ref_language)
            if existing:
                voice_id, voice_data = existing
                logger.info(f"{provider_label} 直链 MD5 命中，复用 voice_id: {voice_id}")
                return JSONResponse({
                    'voice_id': voice_id,
                    'message': f'已复用现有{provider_label}音色，跳过注册',
                    'reused': True,
                    'provider': provider
                })

            # 4. 使用直链注册音色
            language_hints = qwen_language_hints(ref_language)
            client = QwenVoiceCloneClient(
                api_key=api_key,
                tflink_upload_url=TFLINK_UPLOAD_URL,
                dashscope_base_url=base_url,
            )

            from utils.api_config_loader import get_cosyvoice_clone_model
            clone_model = get_cosyvoice_clone_model(provider)
            voice_id, _ = await asyncio.to_thread(
                client.create_voice,
                prefix=prefix,
                url=direct_link,
                language_hints=language_hints,
                target_model=clone_model,
            )

            voice_data = {
                'voice_id': voice_id,
                'prefix': prefix,
                'file_url': direct_link,
                'audio_md5': audio_md5,
                'ref_language': ref_language,
                'provider': provider,
                'dashscope_base_url': base_url,
                'clone_model': clone_model,
                'created_at': datetime.now().isoformat(),
                'is_direct_link': True
            }

            logger.info(f"{provider_label} 直链音色注册成功，voice_id: {voice_id}")

    except DirectLinkSecurityError as e:
        logger.warning(f"{provider_label} 直链安全校验失败: {e}")
        return JSONResponse({
            'error': str(e),
            'code': e.code,
            'provider': provider,
        }, status_code=400)
    except ElevenLabsUpstreamError as e:
        logger.error(f"ElevenLabs 直链音色注册上游服务错误 ({e.status_code}): {e}")
        return JSONResponse({
            'error': f'ElevenLabs上游服务错误: {str(e)}',
            'code': 'ELEVENLABS_UPSTREAM_ERROR',
            'provider': provider,
        }, status_code=502)
    except (MinimaxVoiceCloneError, QwenVoiceCloneError) as e:
        logger.error(f"{provider_label} 直链音色注册失败: {e}")
        error_detail = str(e)
        if '超时' in error_detail:
            return JSONResponse({'error': error_detail, 'provider': provider}, status_code=408)
        elif '下载' in error_detail:
            return JSONResponse({'error': error_detail, 'provider': provider}, status_code=415)
        return JSONResponse({
            'error': f'{provider_label}音色注册失败: {error_detail}',
            'provider': provider
        }, status_code=500)
    except Exception as e:
        logger.error(f"{provider_label} 直链音色注册时发生错误: {e}")
        return JSONResponse({
            'error': f'{provider_label}音色注册失败: {str(e)}',
            'provider': provider
        }, status_code=500)

    # 保存到本地音色库
    try:
        _config_manager.save_voice_for_api_key(storage_key, voice_id, voice_data)
        logger.info(f"{provider_label} 直链 voice_id 已保存到音色库: {voice_id}")
    except Exception as save_error:
        logger.error(f"保存 {provider_label} 直链 voice_id 到音色库失败: {save_error}")
        return JSONResponse({
            'voice_id': voice_id,
            'message': f'{provider_label}直链音色注册成功，但本地保存失败',
            'local_save_failed': True,
            'error': str(save_error),
            'provider': provider,
        }, status_code=200)

    return JSONResponse({
        'voice_id': voice_id,
        'message': f'{provider_label}直链音色注册成功并已保存到音色库',
        'provider': provider,
        'is_direct_link': True
    })


@router.get('/character-card/list')
async def get_character_cards():
    """Get all character cards in the character_cards folder."""
    try:
        # 获取config_manager实例
        config_mgr = get_config_manager()

        # 确保character_cards目录存在
        config_mgr.ensure_chara_directory()

        # 遍历 character_cards 目录下的所有 .chara.json 文件，并行读取
        # （角色卡多时串行 await 会 N 次线程切换 + JSON 解析，整条接口延迟线性增长）
        candidate_filenames = [f for f in os.listdir(config_mgr.chara_dir) if f.endswith('.chara.json')]

        async def _read_one_card(filename: str):
            file_path = os.path.join(config_mgr.chara_dir, filename)
            try:
                data = await read_json_async(file_path)
                if data and data.get('name'):
                    _sync_catgirl_field_order(data)
                    return {
                        'id': filename[:-11],  # 去掉 .chara.json 后缀
                        'name': data['name'],
                        'description': data.get('description', ''),
                        'tags': data.get('tags', []),
                        'rawData': data,
                        'path': file_path,
                    }
            except Exception as e:
                logger.error(f"读取角色卡文件 {filename} 时出错: {e}")
            return None

        results = await asyncio.gather(
            *(_read_one_card(fn) for fn in candidate_filenames),
            return_exceptions=False,
        )
        character_cards = [r for r in results if r is not None]

        logger.info(f"已加载 {len(character_cards)} 个角色卡")
        return {"success": True, "character_cards": character_cards}
    except Exception as e:
        logger.error(f"获取角色卡列表失败: {e}")
        return {"success": False, "error": str(e)}


@router.post('/catgirl/save-to-model-folder')
async def save_catgirl_to_model_folder(request: Request):
    """Save the character card into the model's folder."""
    try:
        data = await request.json()
        chara_data = data.get('charaData')
        model_name = data.get('modelName')  # 接收模型名称而不是路径
        file_name = data.get('fileName')

        if not chara_data or not model_name or not file_name:
            return JSONResponse({"success": False, "error": "缺少必要参数"}, status_code=400)

        # 使用find_model_directory函数查找模型的实际文件系统路径
        model_folder_path, _ = find_model_directory(model_name)

        # 检查模型目录是否存在
        if not model_folder_path:
            return JSONResponse({"success": False, "error": f"无法找到模型目录: {model_name}"}, status_code=404)

        # 检查是否是用户导入的模型，只允许写入用户目录的模型，不允许写入 workshop/static
        config_mgr = get_config_manager()
        is_user_model = is_user_imported_model(model_folder_path, config_mgr)

        if not is_user_model:
            return JSONResponse(
                status_code=403,
                content={
                    "success": False,
                    "error": "只能保存到用户导入的模型目录。请先导入模型到用户模型目录后再保存。"
                }
            )

        # 确保模型文件夹存在
        if not os.path.exists(model_folder_path):
            os.makedirs(model_folder_path, exist_ok=True)
            logger.info(f"已创建模型文件夹: {model_folder_path}")

        # 防路径穿越：只允许文件名，不允许路径
        safe_name = os.path.basename(file_name)
        if safe_name != file_name or ".." in safe_name or safe_name.startswith(("/", "\\")):
            return JSONResponse({"success": False, "error": "非法文件名"}, status_code=400)

        # 保存角色卡到模型文件夹
        file_path = os.path.join(model_folder_path, safe_name)
        await atomic_write_json_async(file_path, chara_data, ensure_ascii=False, indent=2)

        logger.info(f"角色卡已成功保存到模型文件夹: {file_path}")
        return {"success": True, "path": file_path, "modelFolderPath": model_folder_path}
    except Exception as e:
        logger.error(f"保存角色卡到模型文件夹失败: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@router.post('/character-card/save')
async def save_character_card(request: Request):
    """Save the character card to characters.json."""
    try:
        data = await request.json()
        chara_data = data.get('charaData')
        character_card_name = data.get('character_card_name')

        if not chara_data or not character_card_name:
            return JSONResponse({"success": False, "error": "缺少必要参数"}, status_code=400)

        # 获取config_manager实例
        _config_manager = get_config_manager()

        # 加载现有的characters.json
        characters = await _config_manager.aload_characters()

        # 确保'猫娘'键存在
        if '猫娘' not in characters:
            characters['猫娘'] = {}

        # 获取角色卡名称（档案名）
        # 兼容中英文字段名
        chara_name = chara_data.get('档案名') or chara_data.get('name') or character_card_name
        name_error = _validate_profile_name(chara_name)
        if name_error:
            return JSONResponse({"success": False, "error": f"角色名称无效: {name_error}"}, status_code=400)
        chara_name = str(chara_name).strip()
        is_new_character = chara_name not in characters['猫娘']
        previous_catgirl_data = copy.deepcopy(characters['猫娘'].get(chara_name, {}))
        filtered_chara_data = _filter_mutable_catgirl_fields(chara_data)

        # 创建猫娘数据，只保存非空字段
        catgirl_data = {}
        for k, v in filtered_chara_data.items():
            if k != '档案名' and k != 'name':
                if v:  # 只保存非空字段
                    catgirl_data[k] = v

        # 更新或创建猫娘数据
        characters['猫娘'][chara_name] = catgirl_data

        # 保存到characters.json
        await _config_manager.asave_characters(characters)
        prompt_fields_changed = (
            is_new_character
            or _catgirl_prompt_fields_changed(previous_catgirl_data, catgirl_data)
        )

        if is_new_character:
            pending_mark_ok, pending_mark_error = await _mark_new_character_greeting_pending_safe(_config_manager, chara_name, "character_card_save")
        else:
            pending_mark_ok = True
            pending_mark_error = ""

        if prompt_fields_changed:
            context_refresh_result = await _refresh_catgirl_context_after_profile_change(
                _config_manager,
                chara_name,
                characters,
                is_new=is_new_character,
            )
        else:
            init_one_catgirl = get_init_one_catgirl()
            await init_one_catgirl(chara_name, is_new=is_new_character)
            context_refresh_result = {
                "context_refreshed": False,
                "recent_history_cleared": False,
                "reload_notified": False,
                "session_restarted": False,
            }

        logger.info(f"角色卡已成功保存到characters.json: {chara_name}")
        result: dict = {
            "success": True,
            "character_card_name": chara_name,
            **context_refresh_result,
        }
        if not pending_mark_ok:
            result["partial_success"] = True
            result["pending_mark_ok"] = False
            result["pending_mark_failed"] = True
            result["pending_mark_error"] = pending_mark_error
        return result
    except MaintenanceModeError:
        raise
    except Exception as e:
        logger.error(f"保存角色卡到characters.json失败: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@router.get('/catgirl/{name}/export')
async def export_catgirl_card(name: str):
    """Export a catgirl character card as a PNG image (with embedded archive data of the model and profile).

    Export flow:
    1. Fetch the catgirl's profile data
    2. If a non-default model is in use, pack the model files into the archive
    3. Append the archive data to the PNG image
    4. Return the PNG image for download

    Note: the default model (DEFAULT_LIVE2D_MODEL_NAME) is never included in the export.
    """
    import zipfile
    import tempfile
    import shutil
    from pathlib import Path
    from urllib.parse import quote

    try:
        _config_manager = get_config_manager()
        characters = await _config_manager.aload_characters()

        if name not in characters.get('猫娘', {}):
            return JSONResponse({'success': False, 'error': '猫娘不存在'}, status_code=404)

        catgirl_data = characters['猫娘'][name]

        # 创建临时目录
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            zip_path = temp_path / 'character_data.zip'

            # 创建压缩包（使用UTF-8编码支持中文文件名）
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                # 1. 添加角色设定JSON（包含所有字段，但省略指定字段）
                # 定义要省略的字段
                FIELDS_TO_EXCLUDE = {'cursor_follow', 'physics', 'voice_id'}

                def filter_excluded_fields(data):
                    """Recursively filter out the specified fields."""
                    if isinstance(data, dict):
                        return {
                            k: filter_excluded_fields(v)
                            for k, v in data.items()
                            if k not in FIELDS_TO_EXCLUDE
                        }
                    elif isinstance(data, list):
                        return [filter_excluded_fields(item) for item in data]
                    else:
                        return data

                chara_json = {
                    '档案名': name,
                    **filter_excluded_fields(catgirl_data)
                }
                zf.writestr('character.json', json.dumps(chara_json, ensure_ascii=False, indent=2))

                # 2. 检查并添加模型文件
                model_type = get_reserved(catgirl_data, 'avatar', 'model_type', default='live2d', legacy_keys=('model_type',))
                model_added = False

                if model_type == 'live2d':
                    # 获取Live2D模型路径
                    live2d_path = get_reserved(
                        catgirl_data,
                        'avatar',
                        'live2d',
                        'model_path',
                        default='',
                        legacy_keys=('live2d',)
                    )

                    if live2d_path and live2d_path.strip():
                        # 解析模型名称
                        live2d_name = live2d_path.replace('\\', '/').rstrip('/')
                        if live2d_name.endswith('.model3.json'):
                            live2d_name = live2d_name.split('/')[-2] if '/' in live2d_name else live2d_name.replace('.model3.json', '')
                        else:
                            live2d_name = live2d_name.split('/')[-1]

                        # 检查是否是默认模型
                        if live2d_name == DEFAULT_LIVE2D_MODEL_NAME:
                            logger.info(
                                f'猫娘 {name} 使用的是默认模型 '
                                f'{DEFAULT_LIVE2D_MODEL_NAME}，跳过模型打包'
                            )
                        else:
                            # 查找模型目录
                            model_dir, _ = find_model_directory(live2d_name)
                            if model_dir and os.path.exists(model_dir):
                                # 检查是否是用户导入的模型
                                if is_user_imported_model(model_dir, _config_manager):
                                    # 添加模型文件到压缩包
                                    model_files_added = 0
                                    for root, _dirs, files in os.walk(model_dir):
                                        for file in files:
                                            file_path = Path(root) / file
                                            arc_name = f"model/{live2d_name}/{file_path.relative_to(model_dir)}"
                                            zf.write(file_path, arc_name)
                                            model_files_added += 1
                                    logger.info(f'已添加模型 {live2d_name} 的 {model_files_added} 个文件到压缩包')
                                    model_added = True
                                else:
                                    logger.warning(f'模型 {live2d_name} 不是用户导入的模型，跳过打包')
                            else:
                                logger.warning(f'找不到模型目录: {live2d_name}')

                elif model_type in ('vrm', 'live3d'):
                    # 处理VRM/MMD模型
                    vrm_path = get_reserved(catgirl_data, 'avatar', 'vrm', 'model_path', default='')
                    mmd_path = get_reserved(catgirl_data, 'avatar', 'mmd', 'model_path', default='')

                    # 优先处理MMD模型（需要导出整个文件夹）
                    if mmd_path and mmd_path.strip():
                        # 解析MMD模型路径
                        mmd_path = mmd_path.replace('\\', '/')
                        if mmd_path.startswith('/user_mmd/'):
                            model_file_name = mmd_path.replace('/user_mmd/', '')
                            model_full_path = _config_manager.mmd_dir / model_file_name

                            if model_full_path and model_full_path.exists():
                                # 对于MMD模型，导出整个文件夹（包含贴图等依赖文件）
                                model_parent_dir = model_full_path.parent
                                model_folder_name = model_parent_dir.name

                                # 添加整个模型文件夹到压缩包
                                model_files_added = 0
                                for root, _dirs, files in os.walk(model_parent_dir):
                                    for file in files:
                                        file_path = Path(root) / file
                                        arc_name = f"model/{model_folder_name}/{file_path.relative_to(model_parent_dir)}"
                                        zf.write(file_path, arc_name)
                                        model_files_added += 1
                                logger.info(f'已添加MMD模型文件夹 {model_folder_name} 的 {model_files_added} 个文件到压缩包')
                                model_added = True
                            else:
                                logger.warning(f'找不到MMD模型文件: {mmd_path}')

                    # 处理VRM模型（单个文件）
                    elif vrm_path and vrm_path.strip():
                        vrm_path = vrm_path.replace('\\', '/')
                        if vrm_path.startswith('/user_vrm/'):
                            model_file_name = vrm_path.replace('/user_vrm/', '')
                            model_full_path = _config_manager.vrm_dir / model_file_name

                            if model_full_path and model_full_path.exists():
                                arc_name = f"model/{model_full_path.name}"
                                zf.write(model_full_path, arc_name)
                                logger.info(f'已添加VRM模型到压缩包: {model_full_path.name}')
                                model_added = True
                            else:
                                logger.warning(f'找不到VRM模型文件: {vrm_path}')

                elif model_type == 'pngtuber':
                    if _add_pngtuber_assets_to_character_zip(zf, catgirl_data, _config_manager):
                        model_added = True

                # 3. 读取卡面元数据 sidecar（作者 / 创建时间）
                _sidecar_meta_path = _config_manager.card_face_meta_path(name)
                _sidecar_existed = await asyncio.to_thread(_sidecar_meta_path.exists)
                _sidecar_meta = await asyncio.to_thread(_read_card_meta, _sidecar_meta_path)
                now_iso = datetime.now().isoformat(timespec='seconds')
                # 优先使用已有的创建时间；未设置时使用当前时间并回写 sidecar，
                # 以确保后续导出不会重复刷新。
                _author = str(_sidecar_meta.get('author') or '').strip()[:64]
                _existing_created_at = str(_sidecar_meta.get('created_at') or '').strip()
                _created_at = _existing_created_at or now_iso
                if not _existing_created_at:
                    try:
                        _config_manager.ensure_card_faces_directory()
                        _new_meta = dict(_sidecar_meta)
                        _new_meta['created_at'] = _created_at
                        if not _new_meta.get('updated_at'):
                            _new_meta['updated_at'] = now_iso
                        # 仅当 sidecar 原本不存在且 origin 为默认值（None/空/'self'）时才推断来源，
                        # 避免覆盖已有的 self 默认值或用户设定。
                        if not _sidecar_existed and _new_meta.get('origin') in (None, '', 'self'):
                            _new_meta['origin'] = _detect_card_origin_from_character(catgirl_data or {})
                        await asyncio.to_thread(_write_card_meta, _sidecar_meta_path, _new_meta)
                    except Exception as _meta_persist_err:
                        logger.warning(f"[导出角色卡] 回写创建时间到 sidecar 失败: {_meta_persist_err}")

                # 4. 添加元数据文件
                metadata = {
                    'version': '1.0',
                    'export_time': now_iso,
                    'character_name': name,
                    'author': _author,
                    'created_at': _created_at,
                    'model_included': model_added,
                    'model_type': model_type
                }
                zf.writestr('metadata.json', json.dumps(metadata, ensure_ascii=False, indent=2))

            # 5. 获取卡面图：优先使用保存的 card_faces/{name}.png，
            #    不存在时才回退到合成图。
            from utils.screenshot_utils import _validate_image_data
            saved_face_path = _config_manager.card_faces_dir / f"{name}.png"
            png_data = None
            if saved_face_path.exists():
                try:
                    png_data = await asyncio.to_thread(saved_face_path.read_bytes)
                    validated = await asyncio.to_thread(_validate_image_data, png_data)
                    if validated is None:
                        logger.warning(f"[导出角色卡] 已保存卡面验证失败，回退到合成图")
                        png_data = None
                    elif not png_data.startswith(b'\x89PNG\r\n\x1a\n'):
                        try:
                            from PIL import Image
                            from io import BytesIO
                            img = await asyncio.to_thread(Image.open, BytesIO(png_data))
                            buf = BytesIO()
                            await asyncio.to_thread(img.save, buf, format='PNG')
                            png_data = buf.getvalue()
                        except Exception as _conv_err:
                            logger.warning(f"[导出角色卡] 卡面非 PNG 且重新编码失败，回退到合成图: {_conv_err}")
                            png_data = None
                    if png_data is not None:
                        png_data = await asyncio.to_thread(_strip_legacy_card_face_header, png_data)
                except Exception as _read_err:
                    logger.warning(f"[导出角色卡] 读取已保存卡面失败，回退到合成图: {_read_err}")
                    png_data = None

            if png_data is None:
                # 回退：合成一张默认长方形角色卡图片
                from PIL import Image
                width, height = 600, 800
                img = Image.new('RGB', (width, height), color='#E8F4F8')
                png_path = temp_path / 'character_card.png'
                img.save(png_path, 'PNG')
                with open(png_path, 'rb') as f:
                    png_data = f.read()

            # 6. 将压缩包数据嵌入 PNG 的 neKo 块（合法 PNG chunk，Electron 可正常预览）
            with open(zip_path, 'rb') as f:
                zip_data = f.read()

            combined_data = _embed_zip_in_png_chunk(png_data, zip_data)

            # 7. 返回文件下载
            from urllib.parse import quote

            safe_name = "".join(c for c in name if c.isalnum() or c in (' ', '-', '_', '·', '•') or '\u4e00' <= c <= '\u9fff').strip()
            if not safe_name:
                safe_name = "character_card"
            original_filename = f"{safe_name}.png"
            encoded_filename = quote(original_filename, safe='')
            content_disposition = f"attachment; filename*=UTF-8''{encoded_filename}"
            try:
                ascii_filename = original_filename.encode('ascii').decode('ascii')
            except UnicodeEncodeError:
                ascii_filename = "character_card.png"

            return Response(
                content=combined_data,
                # 用 octet-stream 避免浏览器将响应作为图片在新标签中预览，
                # 配合前端 <a download> 开启下载流程。
                media_type='application/octet-stream',
                headers={
                    'Content-Disposition': content_disposition,
                    'X-Filename': ascii_filename,
                    'Cache-Control': 'no-store',
                }
            )

    except Exception as e:
        logger.exception(f"导出角色卡失败: {e}")
        return JSONResponse({'success': False, 'error': f'导出失败: {str(e)}'}, status_code=500)


@router.get('/catgirl/{name}/export-settings')
async def export_catgirl_settings_only(name: str):
    """Export only the catgirl profile (obfuscated, without model files).

    Export flow:
    1. Fetch the catgirl's profile data
    2. Filter out the specified fields
    3. Apply simple XOR obfuscation
    4. Return the obfuscated JSON file directly
    """
    from urllib.parse import quote

    # XOR混淆密钥（仅用于防止意外编辑，非安全加密）
    XOR_KEY = b'NEKOCHARA2024'

    def xor_obfuscate(data: bytes, key: bytes) -> bytes:
        """Simple XOR data obfuscation/restoration (only to prevent accidental edits; not secure encryption).

        Note: this is not real encryption, just simple reversible obfuscation to
        keep users from accidentally editing the data. Use a proper encryption
        scheme if real security protection is needed.
        """
        return bytes(data[i] ^ key[i % len(key)] for i in range(len(data)))

    try:
        _config_manager = get_config_manager()
        characters = await _config_manager.aload_characters()

        if name not in characters.get('猫娘', {}):
            return JSONResponse({'success': False, 'error': '猫娘不存在'}, status_code=404)

        catgirl_data = characters['猫娘'][name]

        # 定义要省略的字段（仅导出设定时不包含模型相关信息）
        FIELDS_TO_EXCLUDE = {'cursor_follow', 'physics', 'voice_id', '_reserved'}

        def filter_excluded_fields(data):
            """Recursively filter out the specified fields."""
            if isinstance(data, dict):
                return {
                    k: filter_excluded_fields(v)
                    for k, v in data.items()
                    if k not in FIELDS_TO_EXCLUDE
                }
            elif isinstance(data, list):
                return [filter_excluded_fields(item) for item in data]
            else:
                return data

        # 准备角色设定JSON（过滤字段，不包含模型信息）
        chara_json = {
            '档案名': name,
            **filter_excluded_fields(catgirl_data)
        }
        json_data = json.dumps(chara_json, ensure_ascii=False, indent=2).encode('utf-8')

        # 加密JSON数据
        encrypted_data = xor_obfuscate(json_data, XOR_KEY)

        # 构建文件名
        safe_name = "".join(c for c in name if c.isalnum() or c in (' ', '-', '_', '·', '•') or '\u4e00' <= c <= '\u9fff').strip()
        if not safe_name:
            safe_name = "character_card"
        original_filename = f"{safe_name}_设定.nekocfg"
        encoded_filename = quote(original_filename, safe='')
        content_disposition = f"attachment; filename*=UTF-8''{encoded_filename}"

        try:
            ascii_filename = original_filename.encode('ascii').decode('ascii')
        except UnicodeEncodeError:
            ascii_filename = "character_settings.nekocfg"

        return Response(
            content=encrypted_data,
            media_type='application/octet-stream',
            headers={
                'Content-Disposition': content_disposition,
                'X-Filename': ascii_filename
            }
        )

    except Exception as e:
        logger.exception(f"导出设定失败: {e}")
        return JSONResponse({'success': False, 'error': f'导出失败: {str(e)}'}, status_code=500)


@router.post('/import-card')
async def import_character_card(
    zip_file: UploadFile = File(...),
    card_image: UploadFile = File(None),
):
    """Import a character card (a ZIP extracted from a PNG image).

    Optional parameters:
      - card_image: the original carrier PNG. If provided and no card face of the
        same name exists locally yet, it is stored directly as the character's
        card-face, following the legacy convention that the cover image is the card face.
    """
    import zipfile
    import tempfile
    import shutil
    from pathlib import Path

    # XOR混淆密钥（与导出时相同，用于防止意外编辑）
    XOR_KEY = b'NEKOCHARA2024'

    def xor_deobfuscate(data: bytes, key: bytes) -> bytes:
        """XOR data restoration (the same operation as xor_obfuscate; named for consistency).

        Note: this is not real decryption, just reversal of the simple reversible obfuscation.
        """
        return bytes(data[i] ^ key[i % len(key)] for i in range(len(data)))

    temp_dir = None
    try:
        # 创建临时目录
        temp_dir = tempfile.mkdtemp()
        temp_path = Path(temp_dir)
        zip_path = temp_path / 'imported.zip'

        # 保存上传的文件（使用流式读取并限制大小）
        try:
            file_buffer = await _read_limited_stream(zip_file, MAX_UPLOAD_SIZE)
            with open(zip_path, 'wb') as f:
                f.write(file_buffer.getvalue())
        except _UploadTooLargeError as e:
            logger.warning(f"[导入角色卡] 文件过大: {e}")
            return JSONResponse({'success': False, 'error': str(e)}, status_code=400)

        # 检查是否是加密的 .nekocfg 文件（直接是加密数据，不是ZIP）
        is_neko_file = zip_file.filename and zip_file.filename.endswith('.nekocfg')

        if is_neko_file:
            # 直接解密 .nekocfg 文件
            with open(zip_path, 'rb') as f:
                encrypted_data = f.read()
            try:
                decrypted_data = xor_deobfuscate(encrypted_data, XOR_KEY)
                character_data = json.loads(decrypted_data.decode('utf-8'))
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                logger.warning(f"[导入角色卡] 解析 .nekocfg 文件失败: {e}")
                return JSONResponse({'success': False, 'error': f'角色卡解析失败: {str(e)}'}, status_code=400)
            if not isinstance(character_data, dict):
                return JSONResponse({'success': False, 'error': '角色卡数据格式无效'}, status_code=400)
            imported_card_character_data = copy.deepcopy(character_data)
            character_data = _filter_mutable_catgirl_fields(character_data)
            character_name = str(character_data.get('档案名', '')).strip()
            character_data['档案名'] = character_name
            name_error = _validate_profile_name(character_name)
            if name_error:
                return JSONResponse({'success': False, 'error': f'角色名称无效: {name_error}'}, status_code=400)
            metadata = {'encrypted': True, 'model_included': False}
        else:
            # 解压ZIP文件（PNG角色卡格式）- 使用安全的解压方式防止 Zip Slip 攻击
            MAX_TOTAL_UNCOMPRESSED = 500 * 1024 * 1024  # 500 MB 总解压大小限制
            MAX_MEMBER_UNCOMPRESSED = 100 * 1024 * 1024  # 100 MB 单个文件大小限制
            extract_path = temp_path / 'extracted'
            extract_path.mkdir()

            total_uncompressed_size = 0
            with zipfile.ZipFile(zip_path, 'r') as zf:
                for member in zf.namelist():
                    member_path = Path(member)
                    if member_path.is_absolute() or '..' in member_path.parts or '\\' in member:
                        logger.warning(f"[导入角色卡] 跳过不安全的路径: {member}")
                        continue

                    zip_info = zf.getinfo(member)
                    member_size = zip_info.file_size
                    if total_uncompressed_size + member_size > MAX_TOTAL_UNCOMPRESSED:
                        logger.warning(f"[导入角色卡] 跳过文件，大小超出总限制: {member}")
                        continue
                    if member_size > MAX_MEMBER_UNCOMPRESSED:
                        logger.warning(f"[导入角色卡] 跳过文件，单文件大小超限: {member}")
                        continue

                    dest_path = extract_path / member_path
                    try:
                        dest_path.resolve().relative_to(extract_path.resolve())
                    except ValueError:
                        logger.warning(f"[导入角色卡] 跳过路径验证失败: {member}")
                        continue
                    if member.endswith('/'):
                        dest_path.mkdir(parents=True, exist_ok=True)
                    else:
                        dest_path.parent.mkdir(parents=True, exist_ok=True)
                        total_uncompressed_size += member_size
                        with zf.open(member) as src, open(dest_path, 'wb') as dst:
                            await asyncio.to_thread(shutil.copyfileobj, src, dst, length=8192)

            # 读取角色设定（支持加密和非加密格式）
            character_json_path = extract_path / 'character.json'
            character_json_encrypted_path = extract_path / 'character.json.encrypted'
            imported_card_character_data = {}

            if character_json_path.exists():
                # 非加密格式
                try:
                    character_data = await read_json_async(character_json_path)
                except json.JSONDecodeError as e:
                    logger.warning(f"[导入角色卡] 解析 character.json 失败: {e}")
                    return JSONResponse({'success': False, 'error': f'角色卡解析失败: {str(e)}'}, status_code=400)
                if not isinstance(character_data, dict):
                    return JSONResponse({'success': False, 'error': '角色卡数据格式无效'}, status_code=400)
                imported_card_character_data = copy.deepcopy(character_data)
                character_data = _filter_mutable_catgirl_fields(character_data)
                character_name = str(character_data.get('档案名', '')).strip()
                character_data['档案名'] = character_name
                name_error = _validate_profile_name(character_name)
                if name_error:
                    return JSONResponse({'success': False, 'error': f'角色名称无效: {name_error}'}, status_code=400)
            elif character_json_encrypted_path.exists():
                # 加密格式，需要解密
                try:
                    with open(character_json_encrypted_path, 'rb') as f:
                        encrypted_data = f.read()
                    decrypted_data = xor_deobfuscate(encrypted_data, XOR_KEY)
                    character_data = json.loads(decrypted_data.decode('utf-8'))
                except (json.JSONDecodeError, UnicodeDecodeError) as e:
                    logger.warning(f"[导入角色卡] 解析加密 character.json 失败: {e}")
                    return JSONResponse({'success': False, 'error': f'角色卡解析失败: {str(e)}'}, status_code=400)
                if not isinstance(character_data, dict):
                    return JSONResponse({'success': False, 'error': '角色卡数据格式无效'}, status_code=400)
                imported_card_character_data = copy.deepcopy(character_data)
                character_data = _filter_mutable_catgirl_fields(character_data)
                character_name = str(character_data.get('档案名', '')).strip()
                character_data['档案名'] = character_name
                name_error = _validate_profile_name(character_name)
                if name_error:
                    return JSONResponse({'success': False, 'error': f'角色名称无效: {name_error}'}, status_code=400)
            else:
                return JSONResponse({'success': False, 'error': '角色卡文件损坏：缺少character.json'}, status_code=400)

            # 读取元数据
            metadata_path = extract_path / 'metadata.json'
            metadata = {}
            if metadata_path.exists():
                metadata = await read_json_async(metadata_path)

        character_name = character_data.get('档案名', '未命名角色')

        _config_manager = get_config_manager()

        async with _ugc_sync_lock:
            characters = await _config_manager.aload_characters()

            # 检查是否已存在同名角色，使用 Windows 风格的命名 (x)
            if character_name in characters.get('猫娘', {}):
                # 生成新名称
                base_name = character_name
                counter = 1
                while f"{base_name}({counter})" in characters.get('猫娘', {}):
                    counter += 1
                character_name = f"{base_name}({counter})"
                character_data['档案名'] = character_name

            # 处理模型文件（仅当不是 .nekocfg 文件时）
            imported_model_info = None  # 记录导入的模型信息，用于自动使用
            pngtuber_rel_map: dict[str, str] = {}

            def _find_model3_json(directory):
                """Recursively find the .model3.json file."""
                for item in directory.iterdir():
                    if item.is_file() and item.name.lower().endswith('.model3.json'):
                        return item
                    elif item.is_dir():
                        result = _find_model3_json(item)
                        if result:
                            return result
                return None

            if not is_neko_file:
                model_dir = extract_path / 'model'
                if model_dir.exists() and model_dir.is_dir():
                    model_type = metadata.get('model_type', 'live2d')
                    pngtuber_rel_map = await asyncio.to_thread(
                        _copy_imported_pngtuber_assets,
                        model_dir,
                        _config_manager,
                    )
                    if pngtuber_rel_map:
                        character_data = _rewrite_imported_pngtuber_refs(character_data, pngtuber_rel_map)

                    for model_item in model_dir.iterdir():
                        if model_item.name == _PNGTUBER_CARD_MODEL_DIR:
                            continue
                        if model_item.is_dir():
                            # 检查是 Live2D 还是 MMD 模型文件夹
                            # MMD 模型文件夹通常包含 .pmx, .pmd 文件
                            has_mmd_file = any(f.suffix.lower() in ('.pmx', '.pmd') for f in model_item.iterdir() if f.is_file())
                            # Live2D 模型文件夹通常包含 .model3.json 文件（递归搜索）
                            model3_file = _find_model3_json(model_item)
                            has_live2d_file = model3_file is not None

                            if has_mmd_file:
                                # MMD 模型（文件夹形式，包含贴图等依赖文件）
                                original_model_name = model_item.name

                                # 检查模型是否已存在，如果存在则使用 Windows 风格的命名 (x)
                                model_name = original_model_name
                                target_model_dir = _config_manager.mmd_dir / model_name
                                counter = 1

                                while target_model_dir.exists():
                                    model_name = f"{original_model_name}({counter})"
                                    target_model_dir = _config_manager.mmd_dir / model_name
                                    counter += 1

                                # 复制整个模型文件夹
                                await asyncio.to_thread(shutil.copytree, model_item, target_model_dir)
                                logger.info(f'已导入MMD模型文件夹: {original_model_name} -> {model_name}')

                                # 查找文件夹中的主模型文件（.pmx 或 .pmd）
                                main_model_file = None
                                for f in target_model_dir.iterdir():
                                    if f.is_file() and f.suffix.lower() in ('.pmx', '.pmd'):
                                        main_model_file = f
                                        break

                                if main_model_file:
                                    imported_model_info = {
                                        'type': 'mmd',
                                        'name': model_name,
                                        'original_name': original_model_name,
                                        'path': f'/user_mmd/{model_name}/{main_model_file.name}'
                                    }
                                else:
                                    logger.warning(f'MMD模型文件夹中没有找到主模型文件: {model_name}')

                            elif has_live2d_file:
                                # Live2D 模型（文件夹形式）
                                original_model_name = model_item.name

                                # 检查模型是否已存在，如果存在则使用 Windows 风格的命名 (x)
                                model_name = original_model_name
                                target_model_dir = _config_manager.live2d_dir / model_name
                                counter = 1

                                while target_model_dir.exists():
                                    model_name = f"{original_model_name}({counter})"
                                    target_model_dir = _config_manager.live2d_dir / model_name
                                    counter += 1

                                # 复制模型文件
                                await asyncio.to_thread(shutil.copytree, model_item, target_model_dir)
                                logger.info(f'已导入Live2D模型: {original_model_name} -> {model_name}')

                                # 查找复制后的 .model3.json 文件，保留相对路径
                                model3_file = _find_model3_json(target_model_dir)
                                if model3_file:
                                    model3_filename = str(model3_file.relative_to(target_model_dir))
                                else:
                                    model3_filename = f'{model_name}.model3.json'
                                logger.info(f'找到 Live2D 模型文件: {model3_filename}')

                                # 记录导入的模型信息
                                imported_model_info = {
                                    'type': 'live2d',
                                    'name': model_name,
                                    'original_name': original_model_name,
                                    'model3_filename': model3_filename
                                }

                        elif model_item.is_file():
                            # VRM 模型（文件形式）
                            model_file = model_item
                            original_model_name = model_file.stem  # 不含扩展名的文件名
                            model_ext = model_file.suffix.lower()

                            if model_ext == '.vrm':
                                # VRM 模型
                                # 检查模型是否已存在，如果存在则使用 Windows 风格的命名 (x)
                                model_name = original_model_name
                                target_model_path = _config_manager.vrm_dir / f"{model_name}{model_ext}"
                                counter = 1

                                while target_model_path.exists():
                                    model_name = f"{original_model_name}({counter})"
                                    target_model_path = _config_manager.vrm_dir / f"{model_name}{model_ext}"
                                    counter += 1

                                await asyncio.to_thread(shutil.copy2, model_file, target_model_path)
                                logger.info(f'已导入VRM模型: {original_model_name} -> {model_name}')

                                # 记录导入的模型信息
                                imported_model_info = {
                                    'type': 'vrm',
                                    'name': model_name,
                                    'original_name': original_model_name,
                                    'path': f'/user_vrm/{model_name}{model_ext}'
                                }
                else:
                    logger.warning(f"[导入角色卡] model 目录不存在或不是目录: {model_dir}")

                # 自动给猫娘使用导入的模型
                # 使用 _reserved 字段存储模型配置（这是系统内部使用的字段）
                if imported_model_info:
                    character_data['_reserved'] = character_data.get('_reserved', {})
                    character_data['_reserved']['avatar'] = character_data['_reserved'].get('avatar', {})

                    if imported_model_info['type'] == 'live2d':
                        model_name = imported_model_info['name']
                        model3_filename = imported_model_info.get('model3_filename', f'{model_name}.model3.json')
                        # 保留现有的 live2d 设置，只更新 model_path
                        character_data['_reserved']['avatar']['live2d'] = character_data['_reserved']['avatar'].get('live2d', {})
                        character_data['_reserved']['avatar']['live2d']['model_path'] = f'{model_name}/{model3_filename}'
                        character_data['_reserved']['avatar']['model_type'] = 'live2d'
                        logger.info(f'已自动为角色 {character_name} 设置Live2D模型: {model_name}, 文件: {model3_filename}')

                    elif imported_model_info['type'] == 'vrm':
                        character_data['_reserved']['avatar']['vrm'] = character_data['_reserved']['avatar'].get('vrm', {})
                        character_data['_reserved']['avatar']['vrm']['model_path'] = imported_model_info['path']
                        character_data['_reserved']['avatar']['model_type'] = 'live3d'
                        logger.info(f'已自动为角色 {character_name} 设置VRM模型: {imported_model_info["name"]}')

                    elif imported_model_info['type'] == 'mmd':
                        # 保留现有的 mmd 设置（捏脸、动画等），只更新 model_path
                        character_data['_reserved']['avatar']['mmd'] = character_data['_reserved']['avatar'].get('mmd', {})
                        character_data['_reserved']['avatar']['mmd']['model_path'] = imported_model_info['path']
                        character_data['_reserved']['avatar']['model_type'] = 'live3d'
                        logger.info(f'已自动为角色 {character_name} 设置MMD模型: {imported_model_info["name"]}')
                elif not pngtuber_rel_map:
                    logger.warning("[导入角色卡] 没有找到可导入的模型")

            character_data = _restore_imported_pngtuber_avatar_config(
                character_data,
                imported_card_character_data,
                pngtuber_rel_map,
            )

            # 添加角色到characters.json
            if '猫娘' not in characters:
                characters['猫娘'] = {}

            # 移除档案名键（因为已经用作字典键）
            chara_data_to_save = {k: v for k, v in character_data.items() if k != '档案名'}
            characters['猫娘'][character_name] = chara_data_to_save

            # 保存到文件
            await _config_manager.asave_characters(characters)
            pending_mark_ok, pending_mark_error = await _mark_new_character_greeting_pending_safe(_config_manager, character_name, "import")

            # 刷新内存中的角色数据，确保磁盘和内存同步
            initialize_character_data = get_initialize_character_data()
            if initialize_character_data:
                await initialize_character_data()

            # 写入卡面元数据 sidecar（origin=imported）
            try:
                _config_manager.ensure_card_faces_directory()
                meta_path = _config_manager.card_face_meta_path(character_name)
                imported_author = ''
                imported_created_at = ''
                if isinstance(metadata, dict):
                    imported_author = str(metadata.get('author', '') or '').strip()[:64]
                    imported_created_at = str(metadata.get('created_at', '') or '').strip()[:32]
                    if not imported_created_at:
                        imported_created_at = str(metadata.get('export_time', '') or '').strip()[:32]
                now_iso = datetime.now().isoformat(timespec='seconds')
                # 优先使用源卡中的创建时间，未提供时才赋为当前时间
                created_at = imported_created_at or now_iso
                meta = {
                    'author': imported_author,
                    'origin': 'imported',
                    'created_at': created_at,
                    'updated_at': now_iso,
                }
                await asyncio.to_thread(_write_card_meta, meta_path, meta)
            except Exception as meta_err:
                logger.warning(f"[导入角色卡] 写入卡面元数据失败: {meta_err}")
                partial_result = {
                    "success": True,
                    "partial_success": True,
                    "error": f"角色数据已导入，但卡面元数据写入失败: {meta_err}",
                    "card_meta_saved": False,
                    "character_name": character_name,
                    "pending_mark_ok": pending_mark_ok,
                }
                if not pending_mark_ok:
                    partial_result["pending_mark_failed"] = True
                    partial_result["pending_mark_error"] = pending_mark_error
                return JSONResponse(partial_result, status_code=200)

            # 老角色卡兼容：如果前端上传了载体 PNG，且本地还没有同名卡面，
            # 则直接使用该 PNG 作为卡面（带 neKo chunk 不影响质量）。
            try:
                if card_image is not None and card_image.filename:
                    face_path = _config_manager.card_faces_dir / f"{character_name}.png"
                    if not face_path.exists():
                        try:
                            # 先用更大的上传限制读取载体 PNG（可能嵌入 ZIP）
                            face_buffer = await _read_limited_stream(card_image, MAX_UPLOAD_SIZE)
                            face_bytes = face_buffer.getvalue()
                        except _UploadTooLargeError as e:
                            logger.warning(f"[导入角色卡] 载体 PNG 超过上传限制，跳过保存: {e}")
                            face_bytes = b''
                        if face_bytes:
                            try:
                                from utils.screenshot_utils import _validate_image_data
                                from PIL import Image as PILImage

                                validated = await asyncio.to_thread(_validate_image_data, face_bytes)
                                if validated is None:
                                    logger.warning(f"[导入角色卡] 载体 PNG 验证失败，跳过保存")
                                else:
                                    if validated.mode not in ('RGB', 'RGBA', 'L'):
                                        validated = validated.convert('RGB')
                                    out = io.BytesIO()
                                    validated.save(out, format='PNG')
                                    valid_png = out.getvalue()
                                    if len(valid_png) > MAX_CARD_FACE_SIZE:
                                        logger.warning(f"[导入角色卡] 重编码后的卡面图 ({len(valid_png)} bytes) 超过最大限制 ({MAX_CARD_FACE_SIZE} bytes)，跳过保存")
                                    else:
                                        await asyncio.to_thread(face_path.write_bytes, valid_png)
                                        logger.info(f"[导入角色卡] 已将载体 PNG 存为卡面: {face_path}")
                            except Exception as pil_err:
                                logger.warning(f"[导入角色卡] 卡面图 PNG 处理失败，跳过保存: {pil_err}")
            except Exception as face_err:
                logger.warning(f"[导入角色卡] 保存载体 PNG 为卡面失败: {face_err}")

        import_result: dict = {
            'success': True,
            'character_name': character_name,
            'message': f'角色卡 "{character_name}" 导入成功',
        }
        if not pending_mark_ok:
            import_result['partial_success'] = True
            import_result['pending_mark_ok'] = False
            import_result['pending_mark_failed'] = True
            import_result['pending_mark_error'] = pending_mark_error
        return JSONResponse(import_result)

    except zipfile.BadZipFile:
        logger.error("导入角色卡失败：无效的ZIP文件")
        return JSONResponse({'success': False, 'error': '无效的角色卡文件格式'}, status_code=400)
    except Exception as e:
        logger.exception(f"导入角色卡失败: {e}")
        return JSONResponse({'success': False, 'error': f'导入失败: {str(e)}'}, status_code=500)
    finally:
        # 清理临时目录
        if temp_dir and os.path.exists(temp_dir):
            await asyncio.to_thread(shutil.rmtree, temp_dir, ignore_errors=True)


# ====== 角色卡卡面（Card Face）存储 ======

# 卡面元数据 sidecar 默认结构
def _default_card_meta(origin: str = 'self') -> dict:
    """Return the default card-face metadata."""
    return {
        'author': '',
        'origin': origin,  # self / imported / steam
        'created_at': None,
        'updated_at': None,
    }


def _read_card_meta(meta_path) -> dict:
    """Read the sidecar JSON; returns defaults when the file is missing or corrupted."""
    try:
        if meta_path.exists():
            with open(meta_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if not isinstance(data, dict):
                logger.warning(f"卡面元数据内容无效（非字典）{meta_path}: {type(data).__name__}")
                return _default_card_meta(origin=None)
            # 合并默认字段，保证字段完整
            merged = _default_card_meta()
            merged.update({k: v for k, v in data.items() if k in merged})
            return merged
    except Exception as e:
        logger.warning(f"读取卡面元数据失败 {meta_path}: {e}")
        return _default_card_meta(origin=None)
    return _default_card_meta()


def _write_card_meta(meta_path, meta: dict) -> None:
    """Write the sidecar JSON (atomic write). The caller must run ensure_card_faces_directory() first."""
    from utils.file_utils import atomic_write_json
    atomic_write_json(meta_path, meta, ensure_ascii=False, indent=2)


def _detect_card_origin_from_character(catgirl_data: dict) -> str:
    """Infer origin from the catgirl config (fallback when no sidecar exists).
    Based on the card's own source (character_origin.source), not the model source (avatar.asset_source),
    so swapping models never changes the card's origin label."""
    try:
        char_source = get_reserved(catgirl_data, 'character_origin', 'source', default='')
        if char_source == 'steam_workshop':
            return 'steam'
    except Exception:
        pass
    return 'self'


@router.get('/card-faces')
async def list_card_faces():
    """Return the names of all catgirls with a custom card face set (lets the frontend avoid pointless 404 requests)."""
    _config_manager = get_config_manager()
    faces_dir = _config_manager.card_faces_dir
    names: list[str] = []
    orphans: list[str] = []
    try:
        characters = await _config_manager.aload_characters()
        valid_names = set(characters.get('猫娘', {}).keys())
        if faces_dir.exists():
            for p in await asyncio.to_thread(lambda: list(faces_dir.glob('*.png'))):
                stem = p.stem
                if stem in valid_names:
                    names.append(stem)
                else:
                    orphans.append(stem)
        if orphans:
            logger.info(f"[list_card_faces] 孤儿卡面文件（无对应角色）: {orphans}")
    except Exception:
        logger.exception("list_card_faces failed")
        return JSONResponse({'success': False, 'error': '读取卡面列表失败'}, status_code=500)

    return JSONResponse({'success': True, 'names': names}, status_code=200)


@router.get('/card-metas')
async def list_card_metas():
    """Return card-face metadata for all catgirls in bulk.

    For legacy character cards without a sidecar JSON, the origin is inferred
    from the catgirl config and defaults are returned, so the frontend still
    shows card-face info after upgrading from older versions.
    """
    _config_manager = get_config_manager()
    faces_dir = _config_manager.card_faces_dir
    metas: dict = {}
    try:
        # 先加载角色列表，构建有效名称集合
        characters = await _config_manager.aload_characters()
        valid_names = set((characters.get('猫娘', {}) or {}).keys())
        # 只读取属于有效角色的 sidecar
        if faces_dir.exists():
            json_files = await asyncio.to_thread(lambda: list(faces_dir.glob('*.json')))
            for p in json_files:
                if p.stem in valid_names:
                    meta = await asyncio.to_thread(_read_card_meta, p)
                    if meta.get('origin') is None:
                        # sidecar 损坏：当作缺失处理，重新推断 origin
                        meta = _default_card_meta(_detect_card_origin_from_character(characters['猫娘'][p.stem]))
                    metas[p.stem] = meta
        # 补齐缺失 sidecar 的猫娘：按配置推断 origin，返回默认值
        for cname, cdata in (characters.get('猫娘', {}) or {}).items():
            if cname in metas:
                continue
            inferred = _default_card_meta(_detect_card_origin_from_character(cdata or {}))
            metas[cname] = inferred
    except Exception as e:
        logger.warning(f"批量读取卡面元数据失败: {e}")
        return JSONResponse({'success': False, 'error': '批量读取卡面元数据失败', 'details': str(e)}, status_code=500)

    return JSONResponse({'success': True, 'metas': metas})


@router.get('/catgirl/{name}/card-meta')
async def get_card_meta(name: str):
    """Get a single catgirl's card-face metadata. Without a sidecar, infers origin from the catgirl config and returns defaults."""
    _config_manager = get_config_manager()
    name_error = _validate_existing_character_path_name(name)
    if name_error:
        return JSONResponse({'success': False, 'error': f'无效的角色名: {name_error}'}, status_code=400)

    characters = await _config_manager.aload_characters()
    if name not in characters.get('猫娘', {}):
        return JSONResponse({'success': False, 'error': '猫娘不存在'}, status_code=404)

    meta_path = _config_manager.card_face_meta_path(name)
    meta = await asyncio.to_thread(_read_card_meta, meta_path)
    if not meta_path.exists() or meta.get('origin') is None:
        # 无 sidecar 或读取失败：根据猫娘配置推断 origin
        meta['origin'] = _detect_card_origin_from_character(characters['猫娘'][name])
    return JSONResponse({'success': True, 'meta': meta})


@router.put('/catgirl/{name}/card-meta')
async def put_card_meta(name: str, request: Request):
    """Update card-face metadata (currently only the author field, and only when origin=self)."""
    _config_manager = get_config_manager()
    name_error = _validate_existing_character_path_name(name)
    if name_error:
        return JSONResponse({'success': False, 'error': f'无效的角色名: {name_error}'}, status_code=400)

    characters = await _config_manager.aload_characters()
    if name not in characters.get('猫娘', {}):
        return JSONResponse({'success': False, 'error': '猫娘不存在'}, status_code=404)

    try:
        data = await request.json()
    except Exception:
        return JSONResponse({'success': False, 'error': '请求体必须是合法的JSON格式'}, status_code=400)

    new_author = data.get('author') if isinstance(data, dict) else None
    if new_author is None:
        return JSONResponse({'success': False, 'error': '缺少 author 字段'}, status_code=400)
    new_author = str(new_author).strip()
    if len(new_author) > 64:
        return JSONResponse({'success': False, 'error': '作者名称过长（最长64字符）'}, status_code=400)

    meta_path = _config_manager.card_face_meta_path(name)
    existing = await asyncio.to_thread(_read_card_meta, meta_path)
    if not meta_path.exists() or existing.get('origin') is None:
        existing['origin'] = _detect_card_origin_from_character(characters['猫娘'][name])

    if existing.get('origin') != 'self':
        return JSONResponse({'success': False, 'error': '仅本地创作的卡面可修改作者'}, status_code=403)

    existing['author'] = new_author
    now_iso = datetime.now().isoformat(timespec='seconds')
    existing['updated_at'] = now_iso
    if not existing.get('created_at'):
        existing['created_at'] = now_iso

    _config_manager.ensure_card_faces_directory()
    await asyncio.to_thread(_write_card_meta, meta_path, existing)
    return JSONResponse({'success': True, 'meta': existing})


def _strip_legacy_card_face_header(image_data: bytes) -> bytes:
    """Return old saved card faces without the obsolete blue name header."""
    try:
        from PIL import Image

        with Image.open(io.BytesIO(image_data)) as img:
            img.load()
            width, height = img.size
            header_height = height // 6
            if width <= 0 or header_height <= 0:
                return image_data

            rgb = img.convert('RGB')
            header_region = rgb.crop((0, 0, width, header_height))
            top_mean = header_region.resize((1, 1), Image.Resampling.BOX).getpixel((0, 0))
            header_color = (64, 197, 241)
            if max(abs(top_mean[i] - header_color[i]) for i in range(3)) > 24:
                return image_data

            # Avoid mistaking an ordinary blue illustration/background for the
            # legacy solid-color name header.
            sample = header_region.resize((16, 16), Image.Resampling.BOX)
            pixels = list(sample.getdata())
            channel_spread = max(
                max(px[i] for px in pixels) - min(px[i] for px in pixels)
                for i in range(3)
            )
            if channel_spread > 28:
                return image_data

            # The old header usually has a visible color break at the body.
            # If the next band is effectively the same blue, keep the image.
            body_band = rgb.crop((0, header_height, width, min(height, header_height * 2)))
            if body_band.size[1] > 0:
                body_mean = body_band.resize((1, 1), Image.Resampling.BOX).getpixel((0, 0))
                if max(abs(body_mean[i] - header_color[i]) for i in range(3)) < 10:
                    return image_data

            cropped = img.convert('RGBA').crop((0, header_height, width, height))
            normalized = cropped.resize((width, height), Image.Resampling.LANCZOS)
            out = io.BytesIO()
            normalized.save(out, 'PNG')
            return out.getvalue()
    except Exception as exc:
        logger.warning("legacy card face normalization failed: %s", exc)
        return image_data


@router.get('/catgirl/{name}/card-face')
async def get_card_face(name: str):
    """Get the character's custom card-face image."""
    _config_manager = get_config_manager()
    name_error = _validate_existing_character_path_name(name)
    if name_error:
        return JSONResponse({'success': False, 'error': f'无效的角色名: {name_error}'}, status_code=400)

    face_path = _config_manager.card_faces_dir / f"{name}.png"
    if not face_path.exists():
        return JSONResponse({'success': False, 'error': '卡面不存在'}, status_code=404)

    image_data = await asyncio.to_thread(face_path.read_bytes)
    image_data = await asyncio.to_thread(_strip_legacy_card_face_header, image_data)
    return Response(content=image_data, media_type='image/png', headers={'Cache-Control': 'no-store'})


@router.put('/catgirl/{name}/card-face')
async def put_card_face(name: str, image: UploadFile = File(...)):
    """Save the character's custom card-face image."""
    _config_manager = get_config_manager()
    name_error = _validate_existing_character_path_name(name)
    if name_error:
        return JSONResponse({'success': False, 'error': f'无效的角色名: {name_error}'}, status_code=400)

    characters = await _config_manager.aload_characters()
    if name not in characters.get('猫娘', {}):
        return JSONResponse({'success': False, 'error': '猫娘不存在'}, status_code=404)

    # 验证文件类型
    content_type = image.content_type or ''
    if not content_type.startswith('image/'):
        return JSONResponse({'success': False, 'error': '文件类型无效，请上传图片'}, status_code=400)

    # 流式读取并限制大小
    try:
        image_buffer = await _read_limited_stream(image, MAX_CARD_FACE_SIZE)
    except _UploadTooLargeError:
        return JSONResponse({'success': False, 'error': '图片文件过大（最大 10MB）'}, status_code=400)

    # 在线程中验证并重新编码为 PNG
    try:
        from utils.screenshot_utils import _validate_image_data
        from PIL import Image as PILImage

        image_buffer.seek(0)
        validated_img = await asyncio.to_thread(_validate_image_data, image_buffer.getvalue())
        if validated_img is None:
            return JSONResponse({'success': False, 'error': '无效的图片文件'}, status_code=400)

        def _reencode(img) -> bytes:
            if img.mode not in ('RGB', 'RGBA', 'L'):
                img = img.convert('RGB')
            out = io.BytesIO()
            img.save(out, format='PNG')
            return out.getvalue()

        png_bytes = await asyncio.to_thread(_reencode, validated_img)
    except Exception:
        return JSONResponse({'success': False, 'error': '无效的图片文件'}, status_code=400)

    # 重编码后再次校验大小（压缩后仍可能超过限制）
    if len(png_bytes) > MAX_CARD_FACE_SIZE:
        return JSONResponse({'success': False, 'error': '文件过大（重编码后超过10MB）'}, status_code=413)

    # 确保目录存在
    _config_manager.ensure_card_faces_directory()

    face_path = _config_manager.card_faces_dir / f"{name}.png"
    await asyncio.to_thread(face_path.write_bytes, png_bytes)

    # 同步更新 sidecar 元数据
    meta_path = _config_manager.card_face_meta_path(name)
    try:
        meta = await asyncio.to_thread(_read_card_meta, meta_path)
        now_iso = datetime.now().isoformat(timespec='seconds')
        # 上传即视为本地创作；若此前是导入的，刷新创建时间
        previous_origin = meta.get('origin')
        meta['origin'] = 'self'
        if previous_origin != 'self' or not meta.get('created_at'):
            meta['created_at'] = now_iso
        meta['updated_at'] = now_iso
        await asyncio.to_thread(_write_card_meta, meta_path, meta)
    except Exception as meta_err:
        logger.warning(f"[上传卡面] 写入 sidecar 元数据失败: {meta_err}")
        return JSONResponse({
            'success': True,
            'partial_success': True,
            'error': f"卡面已保存，但元数据写入失败: {meta_err}",
        }, status_code=200)

    return JSONResponse({'success': True})


class _InvalidPortraitError(ValueError):
    """Raised by the character-card render helper when the user-supplied
    portrait fails PIL's verify() check. Caught at the endpoint to map
    to a 400 response (vs. 500 for genuine render errors)."""


@router.post('/catgirl/{name}/export-with-portrait')
async def export_catgirl_with_portrait(
    name: str,
    portrait: UploadFile = File(...),
    include_model: bool = Form(True)
):
    """Export a character card (including the portrait image).

    Export flow:
    1. Receive the portrait image from the frontend
    2. Composite the portrait onto the character card template
    3. Pack the character profile and model files (optional)
    4. Return the composited PNG character card
    """
    import zipfile
    import tempfile
    from pathlib import Path
    from urllib.parse import quote
    from PIL import Image

    temp_dir = None
    try:
        _config_manager = get_config_manager()
        characters = await _config_manager.aload_characters()

        if name not in characters.get('猫娘', {}):
            return JSONResponse({'success': False, 'error': '猫娘不存在'}, status_code=404)

        catgirl_data = characters['猫娘'][name]

        # 创建临时目录
        temp_dir = tempfile.mkdtemp()
        temp_path = Path(temp_dir)
        zip_path = temp_path / 'character_data.zip'

        # 1. 创建ZIP压缩包（包含角色设定和模型）
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            # 准备角色设定JSON
            export_data = {'档案名': name, **catgirl_data}

            # 过滤掉运行时字段
            def _filter_export_fields(data, keep_model_paths=False):
                """Filter fields on export."""
                result = {}
                for key, value in data.items():
                    if key in ('cursor_follow', 'physics', 'voice_id'):
                        continue
                    if key == '_reserved' and isinstance(value, dict):
                        reserved_copy = copy.deepcopy(value)
                        avatar = reserved_copy.get('avatar', {})
                        if not keep_model_paths:
                            for model_type in ('live2d', 'vrm', 'mmd', 'live3d'):
                                if model_type in avatar and isinstance(avatar[model_type], dict):
                                    avatar[model_type].pop('model_path', None)
                        result[key] = _filter_export_fields(reserved_copy, keep_model_paths)
                    elif isinstance(value, dict):
                        result[key] = _filter_export_fields(value, keep_model_paths)
                    elif isinstance(value, list):
                        result[key] = [
                            _filter_export_fields(item, keep_model_paths) if isinstance(item, dict) else item
                            for item in value
                        ]
                    else:
                        result[key] = value
                return result

            chara_json = _filter_export_fields(export_data, keep_model_paths=include_model)
            zf.writestr('character.json', json.dumps(chara_json, ensure_ascii=False, indent=2))

            # 如果需要包含模型，添加模型文件
            model_added = False
            model_type = get_reserved(catgirl_data, 'avatar', 'model_type', default='live2d')

            if include_model:
                if model_type == 'live2d':
                    live2d_path = get_reserved(catgirl_data, 'avatar', 'live2d', 'model_path', default='')
                    if live2d_path and live2d_path.strip():
                        live2d_name = live2d_path.split('/')[0] if '/' in live2d_path else live2d_path.replace('.model3.json', '')
                        if live2d_name and live2d_name != DEFAULT_LIVE2D_MODEL_NAME:
                            model_dir, _ = find_model_directory(live2d_name)
                            if model_dir and os.path.exists(model_dir):
                                if is_user_imported_model(model_dir, _config_manager):
                                    model_files_added = 0
                                    for root, _dirs, files in os.walk(model_dir):
                                        for file in files:
                                            file_path = Path(root) / file
                                            arc_name = f"model/{live2d_name}/{file_path.relative_to(model_dir)}"
                                            zf.write(file_path, arc_name)
                                            model_files_added += 1
                                    logger.info(f'已添加模型 {live2d_name} 的 {model_files_added} 个文件到压缩包')
                                    model_added = True

                elif model_type in ('vrm', 'live3d'):
                    vrm_path = get_reserved(catgirl_data, 'avatar', 'vrm', 'model_path', default='')
                    mmd_path = get_reserved(catgirl_data, 'avatar', 'mmd', 'model_path', default='')

                    if mmd_path and mmd_path.strip():
                        mmd_path = mmd_path.replace('\\', '/')
                        if mmd_path.startswith('/user_mmd/'):
                            model_file_name = mmd_path.replace('/user_mmd/', '')
                            model_full_path = _config_manager.mmd_dir / model_file_name
                            if model_full_path and model_full_path.exists():
                                model_parent_dir = model_full_path.parent
                                model_folder_name = model_parent_dir.name
                                model_files_added = 0
                                for root, _dirs, files in os.walk(model_parent_dir):
                                    for file in files:
                                        file_path = Path(root) / file
                                        arc_name = f"model/{model_folder_name}/{file_path.relative_to(model_parent_dir)}"
                                        zf.write(file_path, arc_name)
                                        model_files_added += 1
                                logger.info(f'已添加MMD模型文件夹 {model_folder_name} 的 {model_files_added} 个文件到压缩包')
                                model_added = True

                    elif vrm_path and vrm_path.strip():
                        vrm_path = vrm_path.replace('\\', '/')
                        if vrm_path.startswith('/user_vrm/'):
                            model_file_name = vrm_path.replace('/user_vrm/', '')
                            model_full_path = _config_manager.vrm_dir / model_file_name
                            if model_full_path and model_full_path.exists():
                                arc_name = f"model/{model_full_path.name}"
                                zf.write(model_full_path, arc_name)
                                logger.info(f'已添加VRM模型到压缩包: {model_full_path.name}')
                                model_added = True

                elif model_type == 'pngtuber':
                    if _add_pngtuber_assets_to_character_zip(zf, catgirl_data, _config_manager):
                        model_added = True

            # 添加元数据文件
            metadata = {
                'version': '1.0',
                'export_time': datetime.now().isoformat(),
                'character_name': name,
                'model_included': model_added,
                'model_type': model_type,
                'has_portrait': True
            }
            zf.writestr('metadata.json', json.dumps(metadata, ensure_ascii=False, indent=2))

        # 2. 读取立绘图片（带大小限制和验证）
        MAX_PORTRAIT_SIZE = 50 * 1024 * 1024  # 50 MB
        portrait_data = await portrait.read(MAX_PORTRAIT_SIZE + 1)
        if len(portrait_data) > MAX_PORTRAIT_SIZE:
            return JSONResponse({'success': False, 'error': f'图片大小超过限制 ({MAX_PORTRAIT_SIZE // (1024 * 1024)} MB)'}, status_code=400)

        logger.info(f"[导出角色卡] 接收到立绘图片，大小: {len(portrait_data)} bytes")

        png_path = temp_path / 'character_card.png'

        # 整段 PIL 渲染链（图片校验 + 卡片合成 + 字体扫描 + PNG 编码）放进 worker
        # 线程，避免阻塞事件循环。校验失败用专属异常，让外层回 400 而不是 500。
        def _render_card_png(_portrait_data: bytes, _name: str, _png_path) -> None:
            try:
                Image.MAX_IMAGE_PIXELS = 100_000_000  # 限制最大像素数防止解压炸弹
                portrait_img = Image.open(io.BytesIO(_portrait_data))
                portrait_img.verify()
                portrait_img = Image.open(io.BytesIO(_portrait_data))
                portrait_img.load()  # 强制解码：把截断/损坏的像素错误提前到这里，与 _InvalidPortraitError 一起回 400 而不是后续 resize/save 时回 500
            except Exception as exc:
                raise _InvalidPortraitError(str(exc)) from exc

            logger.info(f"[导出角色卡] 立绘图片尺寸: {portrait_img.size}, 模式: {portrait_img.mode}")

            if portrait_img.mode != 'RGBA':
                portrait_img = portrait_img.convert('RGBA')

            width, height = 600, 800
            card_img = Image.new('RGBA', (width, height), color='#E8F4F8')

            portrait_area_y = 0
            portrait_area_width = width
            portrait_area_height = height

            # 前端已按完整卡面尺寸渲染立绘，直接缩放到目标尺寸后粘贴
            portrait_resized = portrait_img.resize((portrait_area_width, portrait_area_height), Image.Resampling.LANCZOS)
            logger.info(f"[导出角色卡] 立绘调整后尺寸: {portrait_resized.size}, 粘贴位置: (0, {portrait_area_y})")

            # 粘贴立绘（使用alpha通道）
            card_img.paste(portrait_resized, (0, portrait_area_y), portrait_resized)
            logger.info("[导出角色卡] 立绘粘贴完成")

            final_img = Image.new('RGB', (width, height), color='#E8F4F8')
            final_img.paste(card_img, (0, 0), card_img)

            final_img.save(_png_path, 'PNG')

        try:
            await asyncio.to_thread(_render_card_png, portrait_data, name, png_path)
        except _InvalidPortraitError as e:
            logger.warning(f"[导出角色卡] 图片验证失败: {e}")
            return JSONResponse({'success': False, 'error': f'无效的图片文件: {str(e)}'}, status_code=400)

        # 6. 将压缩包数据嵌入 PNG 的 neKo 块（合法 PNG chunk，Electron 可正常预览）
        with open(png_path, 'rb') as f:
            png_data = f.read()

        with open(zip_path, 'rb') as f:
            zip_data = f.read()

        combined_data = _embed_zip_in_png_chunk(png_data, zip_data)

        # 7. 返回图片文件
        safe_name = "".join(c for c in name if c.isalnum() or c in (' ', '-', '_', '·', '•') or '\u4e00' <= c <= '\u9fff').strip()
        if not safe_name:
            safe_name = "character_card"
        original_filename = f"{safe_name}.png"
        encoded_filename = quote(original_filename, safe='')
        content_disposition = f"attachment; filename*=UTF-8''{encoded_filename}"

        try:
            ascii_filename = original_filename.encode('ascii').decode('ascii')
        except UnicodeEncodeError:
            ascii_filename = "character_card.png"

        return Response(
            content=combined_data,
            media_type='image/png',
            headers={
                'Content-Disposition': content_disposition,
                'X-Filename': ascii_filename
            }
        )

    except Exception as e:
        logger.exception(f"导出带立绘的角色卡失败: {e}")
        return JSONResponse({'success': False, 'error': f'导出失败: {str(e)}'}, status_code=500)
    finally:
        if temp_dir and os.path.exists(temp_dir):
            await asyncio.to_thread(shutil.rmtree, temp_dir, ignore_errors=True)
