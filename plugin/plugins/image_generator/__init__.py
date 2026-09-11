"""Secure, provider-portable image generation for N.E.K.O.

The plugin talks to an OpenAI-compatible Images API, persists its credential
and mutable settings in PluginStore, and exposes generated files through a
bounded writable copy of the plugin's static UI.  Chat delivery is deliberately
text-only Markdown: the current host renders Markdown image links in blind chat
passthroughs, while URL image parts are not rendered on that path.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import io
import ipaddress
import json
import math
import os
import re
import threading
import time
import zlib
from collections import OrderedDict
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import ParseResult, quote, urlparse
from uuid import uuid4

import httpx

try:
    from PIL import Image as _PIL_Image
except ImportError:  # Frozen hosts may contain extension stubs only.
    _PIL_Image = None

try:
    # The Steam frozen runtime ships pycryptodomex (complete with pure-Python
    # sources) but strips cryptography/Pillow down to extension stubs without
    # their .py files, so `from cryptography...` / `from PIL import ...` fail
    # there even though the host declares them as dependencies. pycryptodomex
    # works in both the dev venv and the frozen build.
    from Cryptodome.Cipher import AES as _CD_AES
    from Cryptodome.Cipher import PKCS1_OAEP as _CD_PKCS1_OAEP
    from Cryptodome.Hash import SHA256 as _CD_SHA256
    from Cryptodome.PublicKey import RSA as _CD_RSA
except ImportError:  # pragma: no cover - packaged dependency failure
    _CD_AES = None  # type: ignore[assignment]
    _CD_PKCS1_OAEP = None  # type: ignore[assignment]
    _CD_SHA256 = None  # type: ignore[assignment]
    _CD_RSA = None  # type: ignore[assignment]

from plugin.sdk.plugin import (
    Err,
    NekoPluginBase,
    Ok,
    SdkError,
    lifecycle,
    llm_tool,
    neko_plugin,
    plugin_entry,
)

PLUGIN_VERSION = "0.1.0"
USER_AGENT = (
    f"N.E.K.O-Image-Generator/{PLUGIN_VERSION} "
    "(+https://github.com/Project-N-E-K-O/N.E.K.O)"
)

_DEFAULT_PLUGIN_SERVER_PORT = 48916
_SETTINGS_STORE_KEY = "settings"
_API_KEY_STORE_KEY = "api_key"
_HISTORY_STORE_KEY = "recent_generations"
_GENERATED_SUBDIR = "generated"

_PROMPT_MAX_CHARS = 4_000
_PROMPT_EXCERPT_MAX_CHARS = 180
_REVISED_PROMPT_MAX_CHARS = 2_000
_MODEL_MAX_CHARS = 128
_URL_MAX_CHARS = 4_096
_API_KEY_MAX_CHARS = 4_096
_API_KEY_MAX_BYTES = 16_384
_ENCRYPTED_DOCUMENT_MAX_BYTES = 32_768
_ENCRYPTED_PAYLOAD_MAX_CHARS = 65_536
_SECRET_ENVELOPE_TTL_SECONDS = 300
_SECRET_ENVELOPE_MAX_PENDING = 8
_KNOWN_SECRET_MAX_COUNT = 8
_MAX_IMAGE_DIMENSION = 8_192
_MAX_IMAGE_PIXELS = 33_554_432
_SUPPORTED_IMAGE_FORMATS: dict[str, tuple[str, str]] = {
    "PNG": ("image/png", "png"),
    "JPEG": ("image/jpeg", "jpg"),
    "WEBP": ("image/webp", "webp"),
}

PROVIDER_PRESETS: dict[str, dict[str, Any]] = {
    "openai": {
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-image-1",
        "allow_local_base_url": False,
        "allow_custom_base_url": False,
    },
    "volcengine_ark": {
        "label": "火山方舟",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "default_model": "doubao-seedream-4-0-250828",
        "allow_local_base_url": False,
        "allow_custom_base_url": False,
    },
    "aliyun_bailian": {
        "label": "阿里云百炼",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "regional_base_urls": [
            "https://dashscope.aliyuncs.com",
            "https://dashscope-intl.aliyuncs.com",
            "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        ],
        "default_model": "wanx2.1-t2i-turbo",
        "allow_local_base_url": False,
        "allow_custom_base_url": False,
        # Wanx 文生图在 OpenAI 兼容模式下不可用（compatible-mode 的
        # /images/generations 对其返回 404），必须走 DashScope 原生异步
        # 任务接口：POST 创建任务 → 轮询 task 状态 → 下载结果 URL。
        # 结果 URL 是签名的 OSS 地址，由插件服务端在受控大小限制内下载，
        # 与百炼返回 b64_json 的兼容端点行为对齐。
        "api_flavor": "dashscope_native",
    },
    "siliconflow": {
        "label": "SiliconFlow",
        "base_url": "https://api.siliconflow.cn/v1",
        "default_model": "Kwai-Kolors/Kolors",
        "allow_local_base_url": False,
        "allow_custom_base_url": False,
    },
    "openrouter": {
        "label": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "default_model": "openai/gpt-5-image-mini",
        "allow_local_base_url": False,
        "allow_custom_base_url": False,
    },
    "gemini_openai_compatible": {
        "label": "Gemini（OpenAI 兼容）",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "default_model": "imagen-3.0-generate-002",
        "allow_local_base_url": False,
        "allow_custom_base_url": False,
    },
    "local_compatible": {
        "label": "本地兼容服务",
        "base_url": "http://127.0.0.1:1234/v1",
        "default_model": "local-image-model",
        "allow_local_base_url": True,
        "allow_custom_base_url": True,
    },
    "custom": {
        "label": "自定义兼容服务",
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-image-1",
        "allow_local_base_url": False,
        "allow_custom_base_url": True,
    },
}

_DEFAULT_PROVIDER = "openai"

DEFAULT_SETTINGS: dict[str, Any] = {
    "provider": _DEFAULT_PROVIDER,
    "api_base_url": PROVIDER_PRESETS[_DEFAULT_PROVIDER]["base_url"],
    "model": PROVIDER_PRESETS[_DEFAULT_PROVIDER]["default_model"],
    "default_size": "1024x1024",
    "default_quality": "auto",
    "default_style": "",
    "allowed_sizes": [
        "auto",
        "1024x1024",
        "1536x1024",
        "1024x1536",
    ],
    "allowed_qualities": ["auto", "low", "medium", "high"],
    "allowed_styles": ["", "auto", "vivid", "natural"],
    # Exact OpenAI image file format; "auto" lets the provider choose.
    "output_format": "auto",
    # Security policy: remote provider URLs are never fetched server-side.
    "response_format": "b64_json",
    "timeout_seconds": 120.0,
    "max_download_bytes": 20 * 1024 * 1024,
    "cache_max_count": 20,
    "cache_max_bytes": 100 * 1024 * 1024,
    "history_limit": 30,
    "auto_show_in_chat": True,
}

GENERATE_IMAGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "prompt": {
            "type": "string",
            "minLength": 1,
            "maxLength": _PROMPT_MAX_CHARS,
            "description": (
                "要生成的图片描述。保留用户要求的主体、构图、氛围、文字和风格；"
                "过长内容会安全截断。"
            ),
        },
        "size": {
            "type": "string",
            "maxLength": 32,
            "description": "可选尺寸；必须属于管理面板配置的允许尺寸列表。",
        },
        "quality": {
            "type": "string",
            "maxLength": 32,
            "description": "可选质量；必须属于管理面板配置的允许质量列表。",
        },
        "style": {
            "type": "string",
            "maxLength": 32,
            "description": "可选风格；必须属于管理面板配置的允许风格列表。",
        },
    },
    "required": ["prompt"],
    "additionalProperties": False,
}

_EMPTY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}

_RECENT_HISTORY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 100,
            "default": 20,
        }
    },
    "additionalProperties": False,
}

_TEST_GENERATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "prompt": {
            "type": "string",
            "minLength": 1,
            "maxLength": _PROMPT_MAX_CHARS,
            "description": "用于付费测试生成的提示词。",
        }
    },
    "required": ["prompt"],
    "additionalProperties": False,
}

_SAVE_SETTINGS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "encrypted_payload": {
            "type": "string",
            "minLength": 1,
            "maxLength": _ENCRYPTED_PAYLOAD_MAX_CHARS,
            "writeOnly": True,
            "x-sensitive": True,
            "description": "一次性 RSA-OAEP + AES-GCM 加密的完整设置文档。",
        },
        "key_id": {
            "type": "string",
            "minLength": 32,
            "maxLength": 32,
            "pattern": "^[0-9a-f]{32}$",
        },
    },
    "required": ["encrypted_payload", "key_id"],
    "additionalProperties": False,
}

_SIZE_PATTERN = re.compile(r"^(?:auto|[1-9][0-9]{1,4}x[1-9][0-9]{1,4})$")
_OPTION_PATTERN = re.compile(r"^[A-Za-z0-9_.\-]{1,32}$")
_MODEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/\-]{0,127}$")
_GENERATED_FILE_PATTERN = re.compile(r"^[0-9a-f]{32}\.(?:png|jpg|webp)$")
_GENERATED_TEMP_FILE_PATTERN = re.compile(
    r"^\.[0-9a-f]{32}\.(?:png|jpg|webp)\.[0-9a-f]{32}\.tmp$"
)
# 280px chat-preview thumbnails live next to their originals and must be
# accounted for by cache statistics, pruning and startup cleanup exactly
# like the UUID-only generated files, otherwise repeated generations grow
# the plugin directory without respecting cache_max_count/cache_max_bytes.
_GENERATED_THUMB_FILE_PATTERN = re.compile(
    r"^thumb_[0-9a-f]{32}\.(?:png|jpg|webp)$"
)
_BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=\-]{8,}")
_KEY_LIKE_PATTERN = re.compile(r"(?i)\bsk-[A-Za-z0-9_\-]{8,}")
_CONTROL_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class _GenerationFailure(Exception):
    """Internal exception carrying only a pre-sanitized user message."""

    def __init__(self, message: str, failure_class: str):
        super().__init__(message)
        self.message = message
        self.failure_class = failure_class


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _clean_text(
    value: Any,
    *,
    label: str,
    max_chars: int,
    allow_empty: bool = False,
) -> str:
    if not isinstance(value, str):
        raise SdkError(f"{label}必须是文本")
    cleaned = _CONTROL_PATTERN.sub(" ", value).strip()
    if not cleaned and not allow_empty:
        raise SdkError(f"{label}不能为空")
    return cleaned[:max_chars]


def _secret_values(
    values: str | Iterable[str] | None,
) -> tuple[str, ...]:
    if isinstance(values, str):
        candidates = (values,)
    elif values is None:
        candidates = ()
    else:
        candidates = tuple(item for item in values if isinstance(item, str))
    return tuple(
        sorted(
            {item for item in candidates if item},
            key=len,
            reverse=True,
        )
    )


def _redact_text(
    value: Any,
    secrets: str | Iterable[str] | None = None,
    *,
    max_chars: int,
) -> str:
    text = str(value or "")
    for secret in _secret_values(secrets):
        text = text.replace(secret, "[REDACTED]")
    text = _BEARER_PATTERN.sub("Bearer [REDACTED]", text)
    text = _KEY_LIKE_PATTERN.sub("[REDACTED]", text)
    text = _CONTROL_PATTERN.sub(" ", text).strip()
    return text[:max_chars]


def _validate_api_key(value: Any) -> str:
    if isinstance(value, str) and (not value.isascii() or any(ord(ch) < 32 or ord(ch) == 127 for ch in value)):
        raise SdkError("API 密钥必须使用可打印的 ASCII 字符")
    if isinstance(value, str) and (
        len(value) > _API_KEY_MAX_CHARS
        or len(value.encode("utf-8")) > _API_KEY_MAX_BYTES
    ):
        raise SdkError("API 密钥过长")
    key = _clean_text(
        value,
        label="API 密钥",
        max_chars=_API_KEY_MAX_CHARS,
    )
    if len(key) < 8:
        raise SdkError("API 密钥长度过短")
    if any(ch.isspace() for ch in key):
        raise SdkError("API 密钥不能包含空白字符")
    if len(key.encode("utf-8")) > _API_KEY_MAX_BYTES:
        raise SdkError("API 密钥编码后过长")
    return key


def _value_contains_secret(
    value: Any,
    secrets: str | Iterable[str] | None,
) -> bool:
    candidates = _secret_values(secrets)
    if not candidates:
        return False
    if isinstance(value, str):
        return any(secret in value for secret in candidates)
    if isinstance(value, Mapping):
        return any(
            (key not in DEFAULT_SETTINGS and _value_contains_secret(key, candidates))
            or _value_contains_secret(item, candidates)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple, set, frozenset)):
        return any(_value_contains_secret(item, candidates) for item in value)
    return False


def _settings_contain_secret(
    settings: Mapping[str, Any],
    secrets: str | Iterable[str] | None,
) -> bool:
    return _value_contains_secret(settings, secrets)


def _redact_structure(
    value: Any,
    secrets: str | Iterable[str] | None,
) -> Any:
    if isinstance(value, str):
        return _redact_text(value, secrets, max_chars=_URL_MAX_CHARS)
    if isinstance(value, Mapping):
        return {
            key: _redact_structure(
                item,
                secrets,
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_structure(item, secrets) for item in value[:100]]
    if isinstance(value, tuple):
        return [_redact_structure(item, secrets) for item in value[:100]]
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    return _redact_text(value, secrets, max_chars=256)


def _parse_http_url(value: str) -> ParseResult | None:
    try:
        parsed = urlparse(value)
        port = parsed.port
    except (TypeError, ValueError):
        return None
    del port
    if parsed.scheme.lower() not in {"http", "https"}:
        return None
    if (
        not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None
    return parsed


def _is_loopback_hostname(value: Any) -> bool:
    hostname = str(value or "").strip().lower()
    if hostname == "localhost" or hostname.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _is_private_or_loopback_hostname(value: Any) -> bool:
    hostname = str(value or "").strip().lower().strip("[]")
    if _is_loopback_hostname(hostname):
        return True
    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        return False
    return ip.is_private or ip.is_link_local or ip.is_multicast or ip.is_reserved


def _normalize_api_base_url(value: Any) -> str:
    if not isinstance(value, str):
        raise SdkError("API Base URL 必须是文本")
    if len(value) > _URL_MAX_CHARS:
        raise SdkError(f"API Base URL 不能超过 {_URL_MAX_CHARS} 个字符")
    text = _clean_text(
        value,
        label="API Base URL",
        max_chars=_URL_MAX_CHARS,
    )
    parsed = _parse_http_url(text)
    if parsed is None or parsed.query or parsed.fragment or parsed.params:
        raise SdkError("API Base URL 必须是无账号、查询参数或片段的 http(s) 地址")
    if parsed.scheme.lower() == "http":
        if not _is_loopback_hostname(parsed.hostname):
            raise SdkError("公开 API Base URL 必须使用 HTTPS；HTTP 仅允许回环开发地址")
    path = parsed.path.rstrip("/")
    normalized = parsed._replace(
        scheme=parsed.scheme.lower(),
        path=path,
        params="",
        query="",
        fragment="",
    ).geturl()
    return normalized.rstrip("/")


def _origin_tuple(url: str) -> tuple[str, str, int | None] | None:
    parsed = _parse_http_url(url)
    if parsed is None:
        return None
    default_port = 443 if parsed.scheme.lower() == "https" else 80
    return (
        parsed.scheme.lower(),
        str(parsed.hostname or "").lower(),
        parsed.port or default_port,
    )


def _normalize_option_list(
    value: Any,
    *,
    label: str,
    allow_empty: bool,
    size_values: bool = False,
) -> list[str]:
    if isinstance(value, (str, bytes)) or not isinstance(value, list):
        raise SdkError(f"{label}必须是数组")
    if not 1 <= len(value) <= 24:
        raise SdkError(f"{label}必须包含 1 到 24 项")
    normalized: list[str] = []
    for raw in value:
        if not isinstance(raw, str):
            raise SdkError(f"{label}只能包含文本")
        item = raw.strip().lower()
        if not item and not allow_empty:
            raise SdkError(f"{label}不能包含空值")
        if item:
            if size_values:
                if not _SIZE_PATTERN.fullmatch(item):
                    raise SdkError(f"{label}包含无效尺寸：{item[:32]}")
                if item != "auto":
                    width, height = (int(part) for part in item.split("x", 1))
                    if width > 8192 or height > 8192:
                        raise SdkError("允许尺寸不能超过 8192x8192")
                    if width * height > _MAX_IMAGE_PIXELS:
                        raise SdkError("允许尺寸超过图片像素上限")
            elif not _OPTION_PATTERN.fullmatch(item):
                raise SdkError(f"{label}包含无效选项：{item[:32]}")
        if item not in normalized:
            normalized.append(item)
    if not normalized:
        raise SdkError(f"{label}不能为空")
    return normalized


def _bounded_int(
    value: Any,
    *,
    label: str,
    minimum: int,
    maximum: int,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SdkError(f"{label}必须是整数")
    if not minimum <= value <= maximum:
        raise SdkError(f"{label}必须在 {minimum} 到 {maximum} 之间")
    return value


def _bounded_float(
    value: Any,
    *,
    label: str,
    minimum: float,
    maximum: float,
) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
    ):
        raise SdkError(f"{label}必须是有效数字")
    result = float(value)
    if not minimum <= result <= maximum:
        raise SdkError(f"{label}必须在 {minimum:g} 到 {maximum:g} 之间")
    return result


def _validate_settings(
    raw: Any,
    *,
    base: Mapping[str, Any],
    require_all: bool,
) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise SdkError("设置必须是对象")

    known = set(DEFAULT_SETTINGS)
    unknown = sorted(str(key) for key in raw if key not in known)
    if unknown:
        raise SdkError(f"包含未知设置：{', '.join(unknown)}")
    if require_all:
        missing = sorted(known.difference(raw))
        if missing:
            raise SdkError(f"缺少设置：{', '.join(missing)}")

    result = {
        key: (list(value) if isinstance(value, list) else value)
        for key, value in base.items()
    }

    if "provider" in raw:
        provider = _clean_text(
            raw["provider"],
            label="供应商",
            max_chars=64,
        ).lower()
        if provider not in PROVIDER_PRESETS:
            raise SdkError("供应商不在支持列表中")
        result["provider"] = provider
    provider = str(result.get("provider") or _DEFAULT_PROVIDER)
    preset = PROVIDER_PRESETS[provider]

    if "api_base_url" in raw:
        result["api_base_url"] = _normalize_api_base_url(raw["api_base_url"])
    elif provider != str(base.get("provider") or _DEFAULT_PROVIDER):
        result["api_base_url"] = str(preset["base_url"])
    if "model" in raw:
        if len(str(raw["model"])) > _MODEL_MAX_CHARS:
            raise SdkError("模型名称过长")
        model = _clean_text(
            raw["model"],
            label="模型",
            max_chars=_MODEL_MAX_CHARS,
        )
        if not _MODEL_PATTERN.fullmatch(model):
            raise SdkError("模型名称包含不支持的字符")
        result["model"] = model
    elif provider != str(base.get("provider") or _DEFAULT_PROVIDER):
        result["model"] = str(preset["default_model"])

    base_url = _normalize_api_base_url(result["api_base_url"])
    parsed_base_url = _parse_http_url(base_url)
    if parsed_base_url is None:
        raise SdkError("API Base URL 无效")
    if _is_private_or_loopback_hostname(parsed_base_url.hostname):
        if not preset["allow_local_base_url"]:
            raise SdkError("本地或内网 Base URL 仅允许在“本地兼容服务”供应商下使用")
    elif parsed_base_url.scheme.lower() != "https":
        raise SdkError("公开 API Base URL 必须使用 HTTPS")
    if (
        not preset["allow_custom_base_url"]
        and base_url != str(preset["base_url"])
        and base_url not in preset.get("regional_base_urls", [])
    ):
        raise SdkError("该供应商的 Base URL 不允许自定义；请选择自定义或本地兼容服务")

    if "allowed_sizes" in raw:
        result["allowed_sizes"] = _normalize_option_list(
            raw["allowed_sizes"],
            label="允许尺寸",
            allow_empty=False,
            size_values=True,
        )
    if "allowed_qualities" in raw:
        result["allowed_qualities"] = _normalize_option_list(
            raw["allowed_qualities"],
            label="允许质量",
            allow_empty=False,
        )
    if "allowed_styles" in raw:
        result["allowed_styles"] = _normalize_option_list(
            raw["allowed_styles"],
            label="允许风格",
            allow_empty=True,
        )

    for field, label, allow_empty in (
        ("default_size", "默认尺寸", False),
        ("default_quality", "默认质量", False),
        ("default_style", "默认风格", True),
    ):
        if field not in raw:
            continue
        value = _clean_text(
            raw[field],
            label=label,
            max_chars=32,
            allow_empty=allow_empty,
        ).lower()
        if field == "default_size":
            if not _SIZE_PATTERN.fullmatch(value):
                raise SdkError("默认尺寸格式无效")
        elif value and not _OPTION_PATTERN.fullmatch(value):
            raise SdkError(f"{label}格式无效")
        result[field] = value

    if "output_format" in raw:
        output_format = _clean_text(
            raw["output_format"],
            label="输出格式",
            max_chars=16,
        ).lower()
        if output_format not in {"auto", "png", "jpeg", "webp"}:
            raise SdkError("图片输出格式必须是 auto、png、jpeg 或 webp")
        result["output_format"] = output_format
    if "response_format" in raw:
        response_format = _clean_text(
            raw["response_format"],
            label="响应格式",
            max_chars=16,
        ).lower()
        if response_format != "b64_json":
            raise SdkError("为防止服务端 URL 抓取风险，响应格式必须是 b64_json")
        result["response_format"] = response_format

    if "timeout_seconds" in raw:
        result["timeout_seconds"] = _bounded_float(
            raw["timeout_seconds"],
            label="超时秒数",
            minimum=5,
            maximum=240,
        )
    if "max_download_bytes" in raw:
        result["max_download_bytes"] = _bounded_int(
            raw["max_download_bytes"],
            label="最大下载字节数",
            minimum=1024,
            maximum=52_428_800,
        )
    if "cache_max_count" in raw:
        result["cache_max_count"] = _bounded_int(
            raw["cache_max_count"],
            label="缓存文件上限",
            minimum=1,
            maximum=100,
        )
    if "cache_max_bytes" in raw:
        result["cache_max_bytes"] = _bounded_int(
            raw["cache_max_bytes"],
            label="缓存总字节上限",
            minimum=1024,
            maximum=1_073_741_824,
        )
    if "history_limit" in raw:
        result["history_limit"] = _bounded_int(
            raw["history_limit"],
            label="历史记录上限",
            minimum=1,
            maximum=100,
        )
    if "auto_show_in_chat" in raw:
        if not isinstance(raw["auto_show_in_chat"], bool):
            raise SdkError("自动显示开关必须是布尔值")
        result["auto_show_in_chat"] = raw["auto_show_in_chat"]

    for default_field, allowed_field, label in (
        ("default_size", "allowed_sizes", "默认尺寸"),
        ("default_quality", "allowed_qualities", "默认质量"),
        ("default_style", "allowed_styles", "默认风格"),
    ):
        if result[default_field] not in result[allowed_field]:
            raise SdkError(f"{label}必须包含在对应允许列表中")
    if result["cache_max_bytes"] < result["max_download_bytes"]:
        raise SdkError("缓存总字节上限不能小于单张图片下载上限")
    return result


def _normalize_manifest_settings(raw: Any) -> dict[str, Any]:
    defaults = {
        key: (list(value) if isinstance(value, list) else value)
        for key, value in DEFAULT_SETTINGS.items()
    }
    if raw is None:
        return defaults
    # Validate the subsection as one unit so coupled changes such as a new
    # default size plus its matching allowlist are accepted together.
    return _validate_settings(
        raw,
        base=defaults,
        require_all=False,
    )


def _validate_png_scanlines(data: bytes, width: int, height: int,
                            depth: int, color: int, interlace: int) -> None:
    if (width < 1 or height < 1 or width > _MAX_IMAGE_DIMENSION
            or height > _MAX_IMAGE_DIMENSION or width * height > _MAX_IMAGE_PIXELS):
        raise _GenerationFailure("生成图片的尺寸或像素数量超过安全上限", "ImagePixelLimit")
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color]
    passes = ([(0, 0, 1, 1)] if not interlace else
              [(0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8),
               (2, 0, 4, 4), (0, 2, 2, 4), (1, 0, 2, 2), (0, 1, 1, 2)])
    decoder = zlib.decompressobj()
    pending = data
    try:
        for x, y, dx, dy in passes:
            columns = max(0, (width - x + dx - 1) // dx)
            rows = max(0, (height - y + dy - 1) // dy)
            if not columns:
                continue
            row_size = 1 + (columns * channels * depth + 7) // 8
            for _ in range(rows):
                row = decoder.decompress(pending, row_size)
                pending = decoder.unconsumed_tail
                if len(row) != row_size or row[0] > 4:
                    raise ValueError("invalid PNG scanline")
        extra = decoder.decompress(pending, 1)
        if extra or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
            raise ValueError("invalid PNG stream length")
    except (ValueError, zlib.error) as exc:
        raise _GenerationFailure("图片扫描行数据无效", "InvalidImageData") from exc


def _read_png_geometry(data: bytes) -> tuple[int, int, bool]:
    """Return (width, height, animated) for a PNG byte string."""
    if len(data) < 33 or data[:8] != b"\x89PNG\r\n\x1a\n":
        raise _GenerationFailure(
            "图片服务返回了损坏、截断或尺寸不安全的图片",
            "InvalidImageData",
        )
    ihdr_len = int.from_bytes(data[8:12], "big")
    if ihdr_len != 13 or data[12:16] != b"IHDR":
        raise _GenerationFailure(
            "图片服务返回了损坏、截断或尺寸不安全的图片",
            "InvalidImageData",
        )
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    depths = {0: {1, 2, 4, 8, 16}, 2: {8, 16}, 3: {1, 2, 4, 8},
              4: {8, 16}, 6: {8, 16}}
    if (data[24] not in depths.get(data[25], set())
            or data[26] != 0 or data[27] != 0 or data[28] not in (0, 1)):
        raise _GenerationFailure("图片头字段无效", "InvalidImageData")
    offset = 8
    animated = False
    has_image_data = False
    seen_header = False
    seen_palette = False
    seen_idat = False
    ended_idat = False
    image_chunks = []
    while offset + 12 <= len(data):
        length = int.from_bytes(data[offset:offset + 4], "big")
        end = offset + 12 + length
        if end > len(data):
            break
        kind = data[offset + 4:offset + 8]
        if not (kind[0] & 0x20) and kind not in {b"IHDR", b"PLTE", b"IDAT", b"IEND"}:
            raise _GenerationFailure("图片包含未知关键数据块", "InvalidImageData")
        expected_crc = int.from_bytes(data[end - 4:end], "big")
        if binascii.crc32(data[offset + 4:end - 4]) & 0xffffffff != expected_crc:
            raise _GenerationFailure("图片数据校验失败", "InvalidImageData")
        if kind == b"IHDR":
            if seen_header or offset != 8:
                break
            seen_header = True
        elif kind == b"PLTE":
            if (seen_palette or seen_idat or data[25] in (0, 4)
                    or not length or length % 3 or length > 768
                    or (data[25] == 3 and length // 3 > 2 ** data[24])):
                break
            seen_palette = True
        elif kind == b"IDAT":
            if ended_idat or (data[25] == 3 and not seen_palette):
                break
            seen_idat = True
        if seen_idat and kind != b"IDAT":
            ended_idat = True
        if kind == b"acTL":
            animated = True
        if kind == b"IDAT" and length > 0:
            has_image_data = True
            image_chunks.append(data[offset + 8:end - 4])
        if kind == b"IEND" and length == 0:
            if not has_image_data or end != len(data):
                break
            _validate_png_scanlines(b"".join(image_chunks), width, height,
                                    data[24], data[25], data[28])
            return width, height, animated
        offset = end
    raise _GenerationFailure("图片数据不完整", "InvalidImageData")


_JPEG_SOF_MARKERS = frozenset(
    {
        0xC0,  # baseline
        0xC1,  # extended sequential
        0xC2,  # progressive
        0xC3,  # lossless sequential
        0xC5,
        0xC6,
        0xC7,
        0xC9,
        0xCA,
        0xCB,
        0xCD,
        0xCE,
        0xCF,
    }
)


def _read_jpeg_geometry(data: bytes) -> tuple[int, int]:
    """Return (width, height) for a JPEG byte string."""
    if len(data) < 4 or data[:2] != b"\xff\xd8":
        raise _GenerationFailure(
            "图片服务返回了损坏、截断或尺寸不安全的图片",
            "InvalidImageData",
        )
    offset = 2
    limit = len(data)
    geometry = None
    frame_components: set[int] = set()
    quantization_tables: set[int] = set()
    required_tables: set[int] = set()
    in_scan = False
    has_image_data = False
    while offset + 2 <= limit:
        if data[offset] != 0xFF:
            if not in_scan:
                break
            has_image_data = True
            next_marker = data.find(b"\xff", offset + 1)
            if next_marker == -1:
                break
            offset = next_marker
            continue
        marker = data[offset + 1]
        if marker == 0xFF:
            offset += 1
            continue
        if marker == 0x00 and in_scan:
            has_image_data = True
            offset += 2
            continue
        if marker == 0xD9:
            if geometry is not None and has_image_data:
                return geometry
            break
        if marker in (0x01,) or 0xD0 <= marker <= 0xD7:
            offset += 2
            continue
        if marker in (0x00, 0xD8) or offset + 4 > limit:
            break
        in_scan = False
        segment_length = int.from_bytes(data[offset + 2 : offset + 4], "big")
        if segment_length < 2 or offset + 2 + segment_length > limit:
            raise _GenerationFailure(
                "图片服务返回了损坏、截断或尺寸不安全的图片",
                "InvalidImageData",
            )
        if marker in _JPEG_SOF_MARKERS:
            if segment_length < 8:
                raise _GenerationFailure(
                    "图片服务返回了损坏、截断或尺寸不安全的图片",
                    "InvalidImageData",
                )
            height = int.from_bytes(data[offset + 5 : offset + 7], "big")
            width = int.from_bytes(data[offset + 7 : offset + 9], "big")
            geometry = width, height
            count = data[offset + 9]
            precision = data[offset + 4]
            if (not count or segment_length != 8 + 3 * count
                    or precision not in ({8} if marker == 0xC0 else {8, 12, 16})):
                break
            frame_components = set(data[offset + 10:offset + 2 + segment_length:3])
            if len(frame_components) != count:
                break
            required_tables = (set() if marker in {0xC3, 0xC7, 0xCB, 0xCF}
                               else set(data[offset + 12:offset + 2 + segment_length:3]))
        if marker == 0xDB:
            cursor, end = offset + 4, offset + 2 + segment_length
            while cursor < end:
                info = data[cursor]
                precision, table_id = info >> 4, info & 15
                table_end = cursor + 1 + 64 * (precision + 1)
                if precision > 1 or table_id > 3 or table_end > end:
                    raise _GenerationFailure("JPEG 量化表无效", "InvalidImageData")
                quantization_tables.add(table_id)
                cursor = table_end
        if marker == 0xDA:
            if (geometry is None or segment_length < 6
                    or not required_tables.issubset(quantization_tables)):
                break
            count = data[offset + 4]
            components = data[offset + 5:offset + 5 + 2 * count:2]
            if (not count or count > 4 or segment_length != 6 + 2 * count
                    or len(set(components)) != count
                    or not set(components).issubset(frame_components)):
                break
            in_scan = True
        offset += 2 + segment_length
    raise _GenerationFailure(
        "图片服务返回了损坏、截断或尺寸不安全的图片",
        "InvalidImageData",
    )


def _read_webp_geometry(data: bytes) -> tuple[int, int, bool]:
    """Validate RIFF chunk bounds and require an actual WebP image payload."""
    def invalid():
        return _GenerationFailure(
            "图片服务返回了损坏、截断或尺寸不安全的图片", "InvalidImageData"
        )

    if (len(data) < 20 or data[:4] != b"RIFF" or data[8:12] != b"WEBP"
            or int.from_bytes(data[4:8], "little") + 8 != len(data)):
        raise invalid()
    offset = 12
    canvas = None
    geometry = None
    animated = False
    while offset < len(data):
        if offset + 8 > len(data):
            raise invalid()
        kind = data[offset:offset + 4]
        length = int.from_bytes(data[offset + 4:offset + 8], "little")
        start = offset + 8
        end = start + length
        offset = end + (length & 1)
        if offset > len(data):
            raise invalid()
        body = data[start:end]
        if kind == b"VP8X":
            if length != 10 or canvas is not None:
                raise invalid()
            animated = bool(body[0] & 0x02)
            canvas = (1 + int.from_bytes(body[4:7], "little"),
                      1 + int.from_bytes(body[7:10], "little"))
        elif kind == b"VP8L":
            if length <= 5 or body[0] != 0x2f or body[4] & 0xe0:
                raise invalid()
            bits = int.from_bytes(body[1:5], "little")
            geometry = ((bits & 0x3fff) + 1, ((bits >> 14) & 0x3fff) + 1)
        elif kind == b"VP8 ":
            if length <= 10 or body[3:6] != b"\x9d\x01\x2a" or body[0] & 1:
                raise invalid()
            partition_length = int.from_bytes(body[:3], "little") >> 5
            if partition_length + 10 > length:
                raise invalid()
            geometry = (int.from_bytes(body[6:8], "little") & 0x3fff,
                        int.from_bytes(body[8:10], "little") & 0x3fff)
        elif kind in (b"ANIM", b"ANMF"):
            # Animated images are rejected by the caller, never cached.
            animated = True
    if animated and canvas is not None:
        return *canvas, True
    if geometry is None or (canvas is not None and canvas != geometry):
        raise invalid()
    return *geometry, False

def _verified_image_format(data: bytes) -> str:
    if not data:
        raise _GenerationFailure(
            "图片服务返回了空图片",
            "InvalidImageData",
        )
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        image_format = "PNG"
        width, height, animated = _read_png_geometry(data)
    elif data[:2] == b"\xff\xd8":
        image_format = "JPEG"
        width, height = _read_jpeg_geometry(data)
        animated = False
    elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        image_format = "WEBP"
        width, height, animated = _read_webp_geometry(data)
    else:
        raise _GenerationFailure(
            "图片服务返回的图片格式不受支持；仅允许 PNG、JPEG 或 WebP",
            "UnsupportedImageFormat",
        )
    if (
        width < 1
        or height < 1
        or width > _MAX_IMAGE_DIMENSION
        or height > _MAX_IMAGE_DIMENSION
        or width * height > _MAX_IMAGE_PIXELS
    ):
        raise _GenerationFailure(
            "生成图片的尺寸或像素数量超过安全上限",
            "ImagePixelLimit",
        )
    if animated:
        raise _GenerationFailure(
            "暂不支持动画图片",
            "AnimatedImageUnsupported",
        )
    if image_format in {"WEBP", "JPEG"}:
        if _PIL_Image is None:
            raise _GenerationFailure(
                "当前宿主缺少图片解码器，请使用 PNG 输出",
                "UnsupportedImageFormat",
            )
        try:
            with _PIL_Image.open(io.BytesIO(data)) as decoded:
                decoded.load()
                if decoded.format != image_format or decoded.size != (width, height):
                    raise ValueError("image geometry mismatch")
        except Exception as exc:
            raise _GenerationFailure(
                "图片服务返回了无法解码的图片", "InvalidImageData"
            ) from exc
    return image_format

def _image_geometry(data: bytes) -> tuple[int, int] | None:
    """Best-effort (width, height) for an already-verified image.

    The image has passed _verified_image_format by the time this runs, so any
    parse failure here means a corrupt-but-passing container; return None and
    let the chat renderer fall back to its size-less path instead of failing
    the whole generation."""
    try:
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            width, height, _ = _read_png_geometry(data)
        elif data[:2] == b"\xff\xd8":
            width, height = _read_jpeg_geometry(data)
        elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            width, height, _ = _read_webp_geometry(data)
        else:
            return None
    except Exception:
        return None
    if width < 1 or height < 1:
        return None
    return width, height


def _image_type(data: bytes) -> tuple[str, str]:
    image_format = _verified_image_format(data)
    return _SUPPORTED_IMAGE_FORMATS[image_format]


def _sanitize_image(
    data: bytes,
    *,
    max_bytes: int,
) -> tuple[bytes, str, str]:
    if len(data) > max_bytes:
        raise _GenerationFailure(
            "生成图片超过了配置的最大字节数",
            "ImageTooLarge",
        )
    image_format = _verified_image_format(data)
    mime, extension = _SUPPORTED_IMAGE_FORMATS[image_format]
    return data, mime, extension


def _decode_b64_image(value: Any, *, max_bytes: int) -> tuple[bytes, str, str]:
    if not isinstance(value, str) or not value.strip():
        raise _GenerationFailure(
            "图片服务返回了空的 Base64 图片",
            "MalformedResponse",
        )
    encoded = value.strip()
    max_encoded_chars = ((max_bytes + 2) // 3) * 4 + 4
    # Bound the raw string before whitespace normalization, which otherwise
    # creates a second potentially large copy. OpenAI-compatible b64_json is
    # normally unwrapped; the small allowance covers a data-URL prefix and
    # incidental surrounding whitespace.
    if len(encoded) > max_encoded_chars + 4_096:
        raise _GenerationFailure(
            "生成图片超过了配置的最大字节数",
            "ImageTooLarge",
        )
    if encoded.startswith("data:"):
        match = re.match(
            r"^data:image/(?:png|jpeg|jpg|gif|webp);base64,",
            encoded,
            flags=re.IGNORECASE,
        )
        if match is None:
            raise _GenerationFailure(
                "图片服务返回了不支持的数据 URL",
                "MalformedResponse",
            )
        encoded = encoded[match.end() :]
    encoded = "".join(encoded.split())
    if len(encoded) > max_encoded_chars:
        raise _GenerationFailure(
            "生成图片超过了配置的最大字节数",
            "ImageTooLarge",
        )
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise _GenerationFailure(
            "图片服务返回了无效的 Base64 图片",
            "InvalidBase64",
        ) from None
    if len(decoded) > max_bytes:
        raise _GenerationFailure(
            "生成图片超过了配置的最大字节数",
            "ImageTooLarge",
        )
    return _sanitize_image(decoded, max_bytes=max_bytes)


def _safe_history_record(
    value: Any,
    secrets: str | Iterable[str] | None = None,
) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    status = value.get("status")
    if status not in {"succeeded", "failed"}:
        return None
    record_id = _redact_text(
        value.get("id"),
        secrets,
        max_chars=32,
    )
    timestamp = _redact_text(
        value.get("timestamp"),
        secrets,
        max_chars=40,
    )
    model = _redact_text(
        value.get("model"),
        secrets,
        max_chars=_MODEL_MAX_CHARS,
    )
    prompt_excerpt = _redact_text(
        value.get("prompt_excerpt"),
        secrets,
        max_chars=_PROMPT_EXCERPT_MAX_CHARS,
    )
    result_url = _redact_text(
        value.get("result_url"),
        secrets,
        max_chars=_URL_MAX_CHARS,
    )
    if not record_id or not timestamp or not model:
        return None
    if result_url and _parse_http_url(result_url) is None:
        result_url = ""
    return {
        "id": record_id,
        "timestamp": timestamp,
        "model": model,
        "prompt_excerpt": prompt_excerpt,
        "result_url": result_url,
        "status": status,
    }


def _new_http_client(*, trust_env: bool = True) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        follow_redirects=False,
        headers={"User-Agent": USER_AGENT},
        trust_env=trust_env,
    )


def _anchored_asset_io_supported() -> bool:
    supports_dir_fd = getattr(os, "supports_dir_fd", set())
    return (
        hasattr(os, "O_DIRECTORY")
        and hasattr(os, "O_NOFOLLOW")
        and os.open in supports_dir_fd
        and os.mkdir in supports_dir_fd
        and os.rename in supports_dir_fd
        and os.unlink in supports_dir_fd
    )


def _open_anchored_root(
    writable_ui: Path,
    expected_root_identity: tuple[int, int],
) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    root_fd = os.open(writable_ui, flags)
    try:
        root_stat = os.fstat(root_fd)
        if (int(root_stat.st_dev), int(root_stat.st_ino)) != (
            int(expected_root_identity[0]),
            int(expected_root_identity[1]),
        ):
            raise OSError("writable static UI root identity changed")
    except BaseException:
        os.close(root_fd)
        raise
    return root_fd


def _open_anchored_asset_dir(
    writable_ui: Path,
    expected_root_identity: tuple[int, int],
) -> tuple[int, int]:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    root_fd = _open_anchored_root(
        writable_ui,
        expected_root_identity,
    )
    try:
        asset_fd = os.open(
            _GENERATED_SUBDIR,
            flags,
            dir_fd=root_fd,
        )
    except BaseException:
        os.close(root_fd)
        raise
    return root_fd, asset_fd


def _open_windows_directory_guard(path: Path) -> int:
    if os.name != "nt":
        raise OSError("Windows directory guards are unavailable")
    import ctypes
    from ctypes import wintypes

    class FileInformation(ctypes.Structure):
        _fields_ = [
            ("attributes", wintypes.DWORD),
            ("creation_time", wintypes.FILETIME),
            ("last_access_time", wintypes.FILETIME),
            ("last_write_time", wintypes.FILETIME),
            ("volume_serial_number", wintypes.DWORD),
            ("file_size_high", wintypes.DWORD),
            ("file_size_low", wintypes.DWORD),
            ("number_of_links", wintypes.DWORD),
            ("file_index_high", wintypes.DWORD),
            ("file_index_low", wintypes.DWORD),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    get_information = kernel32.GetFileInformationByHandle
    get_information.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(FileInformation),
    ]
    get_information.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL

    file_read_attributes = 0x0080
    file_share_read = 0x0001
    file_share_write = 0x0002
    open_existing = 3
    file_flag_backup_semantics = 0x02000000
    file_flag_open_reparse_point = 0x00200000
    file_attribute_directory = 0x0010
    file_attribute_reparse_point = 0x0400
    invalid_handle_value = ctypes.c_void_p(-1).value

    handle = create_file(
        str(path),
        file_read_attributes,
        file_share_read | file_share_write,
        None,
        open_existing,
        file_flag_backup_semantics | file_flag_open_reparse_point,
        None,
    )
    handle_value = int(handle) if handle is not None else 0
    if not handle_value or handle_value == invalid_handle_value:
        raise OSError(ctypes.get_last_error(), "unable to guard directory")
    information = FileInformation()
    if not get_information(handle, ctypes.byref(information)):
        error_code = ctypes.get_last_error()
        close_handle(handle)
        raise OSError(error_code, "unable to inspect guarded directory")
    if (
        not information.attributes & file_attribute_directory
        or information.attributes & file_attribute_reparse_point
    ):
        close_handle(handle)
        raise OSError("guarded path is not a real directory")
    return handle_value


def _close_windows_handle(handle: int) -> None:
    if os.name != "nt" or not handle:
        return
    import ctypes
    from ctypes import wintypes

    close_handle = ctypes.WinDLL(
        "kernel32",
        use_last_error=True,
    ).CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    close_handle(wintypes.HANDLE(handle))


def _open_windows_asset_guards(
    writable_ui: Path,
    expected_root_identity: tuple[int, int],
) -> tuple[int, int]:
    root_handle = _open_windows_directory_guard(writable_ui)
    try:
        root_stat = writable_ui.stat()
        if (int(root_stat.st_dev), int(root_stat.st_ino)) != (
            int(expected_root_identity[0]),
            int(expected_root_identity[1]),
        ):
            raise OSError("writable static UI root identity changed")
        asset_handle = _open_windows_directory_guard(
            writable_ui / _GENERATED_SUBDIR
        )
    except BaseException:
        _close_windows_handle(root_handle)
        raise
    return root_handle, asset_handle


def _atomic_write_ui_index(
    writable_ui: Path,
    expected_root_identity: tuple[int, int],
    data: bytes,
) -> None:
    target_name = "index.html"
    temp_name = f".index.html.{uuid4().hex}.tmp"
    if _anchored_asset_io_supported():
        root_fd = _open_anchored_root(
            writable_ui,
            expected_root_identity,
        )
        temp_created = False
        try:
            temp_fd = os.open(
                temp_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=root_fd,
            )
            temp_created = True
            try:
                with os.fdopen(temp_fd, "wb", closefd=True) as handle:
                    temp_fd = -1
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
            finally:
                if temp_fd >= 0:
                    os.close(temp_fd)
            os.rename(
                temp_name,
                target_name,
                src_dir_fd=root_fd,
                dst_dir_fd=root_fd,
            )
            temp_created = False
            try:
                os.fsync(root_fd)
            except OSError:
                pass
        finally:
            if temp_created:
                try:
                    os.unlink(temp_name, dir_fd=root_fd)
                except OSError:
                    pass
            os.close(root_fd)
        return
    if os.name == "nt":
        root_handle = _open_windows_directory_guard(writable_ui)
        try:
            root_stat = writable_ui.stat()
            if (int(root_stat.st_dev), int(root_stat.st_ino)) != (
                int(expected_root_identity[0]),
                int(expected_root_identity[1]),
            ):
                raise OSError("writable static UI root identity changed")
            temp_path = writable_ui / temp_name
            try:
                with temp_path.open("xb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp_path, writable_ui / target_name)
            finally:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass
        finally:
            _close_windows_handle(root_handle)
        return
    raise OSError("secure directory-anchored UI writes are unsupported")


def _ensure_generated_asset_dir(
    writable_ui: Path,
    expected_root_identity: tuple[int, int],
) -> None:
    if _anchored_asset_io_supported():
        root_fd = _open_anchored_root(
            writable_ui,
            expected_root_identity,
        )
        try:
            try:
                os.mkdir(_GENERATED_SUBDIR, 0o700, dir_fd=root_fd)
            except FileExistsError:
                pass
            asset_fd = os.open(
                _GENERATED_SUBDIR,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=root_fd,
            )
            os.close(asset_fd)
        finally:
            os.close(root_fd)
        return
    if os.name == "nt":
        root_handle = _open_windows_directory_guard(writable_ui)
        asset_handle = 0
        try:
            root_stat = writable_ui.stat()
            if (int(root_stat.st_dev), int(root_stat.st_ino)) != (
                int(expected_root_identity[0]),
                int(expected_root_identity[1]),
            ):
                raise OSError("writable static UI root identity changed")
            asset_dir = writable_ui / _GENERATED_SUBDIR
            asset_dir.mkdir(exist_ok=True)
            asset_handle = _open_windows_directory_guard(asset_dir)
        finally:
            _close_windows_handle(asset_handle)
            _close_windows_handle(root_handle)
        return
    raise OSError("secure generated asset directories are unsupported")


def _atomic_write_bytes(
    writable_ui: Path,
    expected_root_identity: tuple[int, int],
    temp_name: str,
    target_name: str,
    data: bytes,
) -> None:
    if (
        not _GENERATED_TEMP_FILE_PATTERN.fullmatch(temp_name)
        or not (_GENERATED_FILE_PATTERN.fullmatch(target_name)
                or _GENERATED_THUMB_FILE_PATTERN.fullmatch(target_name))
    ):
        raise OSError("unsafe generated asset filename")

    if _anchored_asset_io_supported():
        root_fd, asset_fd = _open_anchored_asset_dir(
            writable_ui,
            expected_root_identity,
        )
        temp_created = False
        try:
            temp_fd = os.open(
                temp_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=asset_fd,
            )
            temp_created = True
            try:
                with os.fdopen(temp_fd, "wb", closefd=True) as handle:
                    temp_fd = -1
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
            finally:
                if temp_fd >= 0:
                    os.close(temp_fd)
            os.rename(
                temp_name,
                target_name,
                src_dir_fd=asset_fd,
                dst_dir_fd=asset_fd,
            )
            temp_created = False
            try:
                os.fsync(asset_fd)
            except OSError:
                pass
        finally:
            if temp_created:
                try:
                    os.unlink(temp_name, dir_fd=asset_fd)
                except OSError:
                    pass
            os.close(asset_fd)
            os.close(root_fd)
        return

    if os.name == "nt":
        root_handle, asset_handle = _open_windows_asset_guards(
            writable_ui,
            expected_root_identity,
        )
        asset_dir = writable_ui / _GENERATED_SUBDIR
        temp_path = asset_dir / temp_name
        target_path = asset_dir / target_name
        try:
            with temp_path.open("xb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, target_path)
        finally:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
            _close_windows_handle(asset_handle)
            _close_windows_handle(root_handle)
        return

    raise OSError("secure directory-anchored asset writes are unsupported")


def _unlink_asset_file(
    writable_ui: Path,
    expected_root_identity: tuple[int, int],
    filename: str,
) -> bool:
    if not (
        _GENERATED_FILE_PATTERN.fullmatch(filename)
        or _GENERATED_TEMP_FILE_PATTERN.fullmatch(filename)
        or _GENERATED_THUMB_FILE_PATTERN.fullmatch(filename)
    ):
        raise OSError("unsafe generated asset filename")
    if _anchored_asset_io_supported():
        root_fd, asset_fd = _open_anchored_asset_dir(
            writable_ui,
            expected_root_identity,
        )
        try:
            try:
                os.unlink(filename, dir_fd=asset_fd)
            except FileNotFoundError:
                return False
            return True
        finally:
            os.close(asset_fd)
            os.close(root_fd)
    if os.name == "nt":
        root_handle, asset_handle = _open_windows_asset_guards(
            writable_ui,
            expected_root_identity,
        )
        try:
            try:
                (writable_ui / _GENERATED_SUBDIR / filename).unlink()
            except FileNotFoundError:
                return False
            return True
        finally:
            _close_windows_handle(asset_handle)
            _close_windows_handle(root_handle)
    raise OSError("secure directory-anchored asset deletion is unsupported")


@neko_plugin
class ImageGeneratorPlugin(NekoPluginBase):
    """Generate images from normal chat or the management panel."""

    def __init__(self, ctx: Any):
        super().__init__(ctx)
        self._state_lock = threading.Lock()
        self._history_lock = threading.Lock()
        self._cache_lock = threading.Lock()
        # One async lock serializes every settings/key snapshot and mutation.
        # Plugin entry dispatch uses one persistent command event loop.
        self._config_lock = asyncio.Lock()
        self._envelope_lock = threading.Lock()
        self._secret_lock = threading.Lock()
        self._manifest_settings = {
            key: (list(value) if isinstance(value, list) else value)
            for key, value in DEFAULT_SETTINGS.items()
        }
        self._settings = dict(self._manifest_settings)
        self._running = False
        self._api_state = "idle"
        self._configuration_warning: str | None = None
        self._settings_available = True
        self._manifest_available = True
        self._active_generations = 0
        self._last_request: dict[str, Any] = {
            "status": "not_requested",
            "time": None,
            "action": None,
            "failure_class": None,
        }
        self._secret_envelopes: OrderedDict[str, tuple[Any, float]] = OrderedDict()
        self._known_secrets: list[str] = []
        self._writable_ui_dir: Path | None = None
        self._asset_dir: Path | None = None
        self._writable_ui_identity: tuple[int, int] | None = None
        # In-flight generation dedup: the host may dispatch the same request
        # twice (llm_tool + plugin_entry both broadcast to the model).
        # Each real call costs money, so concurrent requests collapse to one.
        self._inflight: dict[str, asyncio.Task] = {}

    # ------------------------------------------------------------------
    # Lifecycle, Store, and loop-local HTTP client
    # ------------------------------------------------------------------

    @lifecycle(id="startup")
    async def startup(self, **_: Any):
        manifest_available = True
        try:
            config = await self.config.dump(timeout=5.0)
            manifest_available = isinstance(config, Mapping)
        except Exception as exc:
            self.logger.warning(
                "ImageGenerator config load failed: failure_class={}",
                type(exc).__name__,
            )
            config = {}
            manifest_available = False

        plugin_section = config.get("plugin") if isinstance(config, Mapping) else None
        store_section = (
            plugin_section.get("store") if isinstance(plugin_section, Mapping) else None
        )
        if (
            isinstance(store_section, Mapping)
            and store_section.get("enabled") is True
            and not bool(getattr(self.store, "enabled", False))
        ):
            self.store.enabled = True
            self.logger.info("ImageGenerator store enabled from effective config")

        raw_defaults = (
            config.get("image_generator") if isinstance(config, Mapping) else None
        )
        configuration_warning: str | None = None
        try:
            manifest_settings = _normalize_manifest_settings(raw_defaults)
        except SdkError:
            manifest_available = False
            manifest_settings = {
                key: (list(value) if isinstance(value, list) else value)
                for key, value in DEFAULT_SETTINGS.items()
            }
            configuration_warning = "plugin.toml 中的图片生成设置无效，已使用安全默认值"
            self.logger.warning(
                "ImageGenerator manifest settings ignored: "
                "failure_class=ValidationError"
            )
        settings_available, stored_settings = await self._store_get_checked(
            _SETTINGS_STORE_KEY, None
        )
        settings_available = settings_available and manifest_available
        if not settings_available:
            configuration_warning = "无法安全读取已保存设置，请重新启动插件后重试"
        effective_settings = manifest_settings
        if stored_settings is not None:
            try:
                effective_settings = _validate_settings(
                    stored_settings,
                    base=manifest_settings,
                    require_all=False,
                )
            except SdkError:
                settings_available = False
                configuration_warning = "已保存的图片生成设置无效，已使用安全默认值"
                self.logger.warning(
                    "ImageGenerator stored settings ignored: "
                    "failure_class=ValidationError"
                )

        with self._state_lock:
            self._manifest_settings = manifest_settings
            self._settings = effective_settings
            self._settings_available = settings_available
            self._manifest_available = manifest_available
            self._configuration_warning = configuration_warning
            self._running = True

        ui_registered = self._register_writable_static_ui()
        asset_cache_available = self._asset_dir is not None
        if not asset_cache_available:
            configuration_warning = "生成图片缓存不可用；管理面板可能可读，但生成已降级"
            with self._state_lock:
                self._configuration_warning = configuration_warning
        key_read_ok, raw_key, key = await self._load_api_key_checked()
        if not key_read_ok:
            self._settings_available = False
        self._remember_secrets(raw_key, key)
        if _settings_contain_secret(
            self._settings_snapshot(),
            self._known_secrets_snapshot(raw_key, key),
        ):
            safe_settings = (
                manifest_settings
                if not _settings_contain_secret(manifest_settings, key)
                else {
                    setting_key: (
                        list(setting_value)
                        if isinstance(setting_value, list)
                        else setting_value
                    )
                    for setting_key, setting_value in DEFAULT_SETTINGS.items()
                }
            )
            configuration_warning = "检测到设置中包含 API 密钥，已改用安全默认值"
            with self._state_lock:
                self._settings = safe_settings
                self._settings_available = False
                self._configuration_warning = configuration_warning
            self.logger.warning(
                "ImageGenerator secret-bearing settings ignored: "
                "failure_class=SecretInSettings"
            )
        try:
            if self._settings_available:
                await self._prune_cache()
        except Exception as exc:
            self.logger.warning(
                "ImageGenerator cache startup sweep failed: failure_class={}",
                type(exc).__name__,
            )

        dependencies_available = (
            _CD_RSA is not None
            and _CD_PKCS1_OAEP is not None
            and _CD_SHA256 is not None
            and _CD_AES is not None
        )
        if not dependencies_available:
            configuration_warning = "密钥加密组件不可用；请重新安装插件依赖"
            with self._state_lock:
                self._configuration_warning = configuration_warning
        configured = bool(key)
        lifecycle_status = (
            "running"
            if ui_registered and asset_cache_available and dependencies_available and self._settings_available
            else "degraded"
        )
        status_payload: dict[str, Any] = {
            "status": lifecycle_status,
            "api_key_configured": configured,
            "ui_registered": ui_registered,
            "asset_cache_available": asset_cache_available,
        }
        if configuration_warning:
            status_payload["configuration_warning"] = configuration_warning
        self.report_status(status_payload)
        self.logger.info(
            "ImageGenerator started: store_enabled={} key_configured={} "
            "ui_registered={}",
            bool(getattr(self.store, "enabled", False)),
            configured,
            ui_registered,
        )
        result_payload: dict[str, Any] = {
            "status": lifecycle_status,
            "store_enabled": bool(getattr(self.store, "enabled", False)),
            "api_key_configured": configured,
            "ui_registered": ui_registered,
            "asset_cache_available": asset_cache_available,
        }
        if configuration_warning:
            result_payload["configuration_warning"] = configuration_warning
        return Ok(result_payload)

    @lifecycle(id="shutdown")
    async def shutdown(self, **_: Any):
        with self._envelope_lock:
            envelope_count = len(self._secret_envelopes)
            self._secret_envelopes.clear()
        with self._secret_lock:
            self._known_secrets.clear()

        with self._state_lock:
            self._running = False
        self.report_status({"status": "shutdown"})
        self.logger.info("ImageGenerator shutdown")
        return Ok(
            {
                "status": "shutdown",
                "clients_seen": 0,
                "close_failures": 0,
                "secret_envelopes_discarded": envelope_count,
            }
        )

    def _get_client(self, *, trust_env: bool = True) -> httpx.AsyncClient:
        # Per-call clients avoid cross-event-loop ownership and any retained
        # retired-client collection. Callers close them best-effort.
        return _new_http_client(trust_env=trust_env)

    @staticmethod
    async def _acquire_lock(lock: threading.Lock) -> None:
        while not lock.acquire(blocking=False):
            await asyncio.sleep(0.01)

    async def _store_get(self, key: str, default: Any = None) -> Any:
        success, value = await self._store_get_checked(key, default)
        return value if success else default

    async def _store_get_checked(
        self,
        key: str,
        default: Any = None,
    ) -> tuple[bool, Any]:
        if not bool(getattr(self.store, "enabled", False)):
            return False, default
        try:
            result = await self.store.get(key, default)
        except Exception as exc:
            self.logger.warning(
                "ImageGenerator store read failed: key={} failure_class={}",
                key,
                type(exc).__name__,
            )
            return False, default
        if isinstance(result, Ok):
            return True, result.value
        self.logger.warning(
            "ImageGenerator store read failed: key={} failure_class=StoreError",
            key,
        )
        return False, default

    async def _store_set(self, key: str, value: Any) -> bool:
        if not bool(getattr(self.store, "enabled", False)):
            return False
        try:
            result = await self.store.set(key, value)
        except Exception as exc:
            self.logger.warning(
                "ImageGenerator store write failed: key={} failure_class={}",
                key,
                type(exc).__name__,
            )
            return False
        if isinstance(result, Ok):
            return True
        self.logger.warning(
            "ImageGenerator store write failed: key={} failure_class=StoreError",
            key,
        )
        return False

    async def _store_delete(self, key: str) -> tuple[bool, bool]:
        if not bool(getattr(self.store, "enabled", False)):
            return False, False
        try:
            result = await self.store.delete(key)
        except Exception as exc:
            self.logger.warning(
                "ImageGenerator store delete failed: key={} failure_class={}",
                key,
                type(exc).__name__,
            )
            return False, False
        if isinstance(result, Ok):
            return True, bool(result.value)
        self.logger.warning(
            "ImageGenerator store delete failed: key={} failure_class=StoreError",
            key,
        )
        return False, False

    async def _load_api_key(self) -> str:
        raw = await self._store_get(_API_KEY_STORE_KEY, "")
        if not isinstance(raw, str):
            return ""
        try:
            return _validate_api_key(raw)
        except SdkError:
            return ""

    async def _load_api_key_checked(self) -> tuple[bool, str, str]:
        success, raw = await self._store_get_checked(_API_KEY_STORE_KEY, "")
        if not success:
            return False, "", ""
        raw_text = raw if isinstance(raw, str) else ""
        try:
            validated = _validate_api_key(raw_text) if raw_text else ""
        except SdkError:
            validated = ""
        return True, raw_text, validated

    def _remember_secrets(self, *values: Any) -> None:
        candidates: list[str] = []
        for value in values:
            if isinstance(value, str) and value:
                candidates.append(value)
        if not candidates:
            return
        with self._secret_lock:
            for candidate in candidates:
                if candidate in self._known_secrets:
                    self._known_secrets.remove(candidate)
                self._known_secrets.append(candidate)
            if len(self._known_secrets) > _KNOWN_SECRET_MAX_COUNT:
                del self._known_secrets[
                    : len(self._known_secrets) - _KNOWN_SECRET_MAX_COUNT
                ]

    def _known_secrets_snapshot(self, *extra: Any) -> tuple[str, ...]:
        with self._secret_lock:
            values = list(self._known_secrets)
        values.extend(item for item in extra if isinstance(item, str) and item)
        return _secret_values(values)

    def _prune_secret_envelopes_locked(self, now: float) -> None:
        expired = [
            key_id
            for key_id, (_private_key, expires_at) in self._secret_envelopes.items()
            if expires_at <= now
        ]
        for key_id in expired:
            self._secret_envelopes.pop(key_id, None)
        while len(self._secret_envelopes) >= _SECRET_ENVELOPE_MAX_PENDING:
            self._secret_envelopes.popitem(last=False)

    async def _issue_secret_envelope(self) -> dict[str, Any]:
        if (
            _CD_RSA is None
            or _CD_PKCS1_OAEP is None
            or _CD_SHA256 is None
            or _CD_AES is None
        ):
            raise SdkError("密钥加密组件不可用，请重新安装插件依赖")

        try:
            private_key = await asyncio.to_thread(_CD_RSA.generate, 2048)
            public_bytes = private_key.public_key().export_key(format="DER")
            key_id = uuid4().hex
            now = time.monotonic()
            expires_at = datetime.now(timezone.utc) + timedelta(
                seconds=_SECRET_ENVELOPE_TTL_SECONDS
            )
            envelope = {
                "key_id": key_id,
                "public_key_spki_b64": base64.b64encode(public_bytes).decode("ascii"),
                "algorithm": "RSA-OAEP-256+A256GCM",
                "expires_at": expires_at.isoformat(timespec="seconds"),
                "max_plaintext_bytes": _ENCRYPTED_DOCUMENT_MAX_BYTES,
            }
            with self._envelope_lock:
                self._prune_secret_envelopes_locked(now)
                self._secret_envelopes[key_id] = (
                    private_key,
                    now + _SECRET_ENVELOPE_TTL_SECONDS,
                )
            return envelope
        except Exception:
            raise SdkError("无法创建一次性密钥加密信封，请稍后重试") from None

    async def _consume_encrypted_settings(
        self,
        *,
        encrypted_payload: Any,
        key_id: Any,
    ) -> dict[str, Any]:
        if (
            not isinstance(key_id, str)
            or not re.fullmatch(r"[0-9a-f]{32}", key_id)
            or not isinstance(encrypted_payload, str)
            or not 1 <= len(encrypted_payload) <= _ENCRYPTED_PAYLOAD_MAX_CHARS
        ):
            raise SdkError("加密设置载荷格式无效，请刷新面板后重试")

        now = time.monotonic()
        with self._envelope_lock:
            self._prune_secret_envelopes_locked(now)
            envelope = self._secret_envelopes.pop(key_id, None)
        if envelope is None or envelope[1] <= now:
            raise SdkError("加密设置载荷已过期或已使用，请刷新面板后重试")
        private_key = envelope[0]

        try:
            outer_bytes = base64.b64decode(encrypted_payload, validate=True)
            if len(outer_bytes) > 48_000:
                raise ValueError
            outer = json.loads(outer_bytes)
            if not isinstance(outer, Mapping) or set(outer) != {
                "v",
                "wrapped_key",
                "iv",
                "ciphertext",
            }:
                raise ValueError
            if outer.get("v") != 1:
                raise ValueError
            wrapped_value = outer.get("wrapped_key")
            iv_value = outer.get("iv")
            ciphertext_value = outer.get("ciphertext")
            if not all(
                isinstance(item, str)
                for item in (wrapped_value, iv_value, ciphertext_value)
            ):
                raise ValueError
            wrapped_key = base64.b64decode(wrapped_value, validate=True)
            iv = base64.b64decode(iv_value, validate=True)
            ciphertext = base64.b64decode(ciphertext_value, validate=True)
            if (
                not 128 <= len(wrapped_key) <= 512
                or len(iv) != 12
                or not 17 <= len(ciphertext) <= 48_000
            ):
                raise ValueError
            binding = f"image_generator:{key_id}".encode("utf-8")

            def decrypt_payload() -> bytes:
                oaep = _CD_PKCS1_OAEP.new(
                    private_key,
                    hashAlgo=_CD_SHA256,
                    label=binding,
                )
                aes_key = oaep.decrypt(wrapped_key)
                if len(aes_key) != 32:
                    raise ValueError
                # WebCrypto AES-GCM appends the 16-byte tag to the ciphertext.
                body, tag = ciphertext[:-16], ciphertext[-16:]
                cipher = _CD_AES.new(aes_key, _CD_AES.MODE_GCM, nonce=iv)
                cipher.update(binding)
                return cipher.decrypt_and_verify(body, tag)

            plaintext = await asyncio.to_thread(decrypt_payload)
            if len(plaintext) > _ENCRYPTED_DOCUMENT_MAX_BYTES:
                raise ValueError
            document = json.loads(plaintext)
            if not isinstance(document, Mapping):
                raise ValueError
            return {str(key): value for key, value in document.items()}
        except Exception:
            raise SdkError(
                "无法解密设置载荷；它可能已损坏、过期或不属于当前面板"
            ) from None

    async def _generation_config_snapshot(
        self,
    ) -> tuple[dict[str, Any], str]:
        async with self._config_lock:
            if not self._settings_available:
                raise SdkError("已保存设置不可用，请重新启动插件后重试")
            settings = self._settings_snapshot()
            if not bool(getattr(self.store, "enabled", False)):
                raise SdkError("插件存储已禁用，无法安全读取 API 密钥")
            key_read_ok, raw_api_key, api_key = await self._load_api_key_checked()
            if not key_read_ok:
                raise SdkError("无法安全读取 API 密钥（StoreError），请稍后重试")
            self._remember_secrets(raw_api_key, api_key)
            secrets = self._known_secrets_snapshot(raw_api_key, api_key)
            if _settings_contain_secret(settings, secrets):
                raise SdkError(
                    "检测到设置字段包含 API 密钥；请在管理面板重新保存安全设置"
                )
            if settings.get("output_format") in {"webp", "jpeg"} and _PIL_Image is None:
                raise SdkError("当前宿主缺少 JPEG/WebP 解码器，请使用 PNG 输出")
            return settings, api_key

    def _settings_snapshot(self) -> dict[str, Any]:
        with self._state_lock:
            return {
                key: (list(value) if isinstance(value, list) else value)
                for key, value in self._settings.items()
            }

    def _set_request_state(
        self,
        *,
        action: str,
        status: str,
        failure_class: str | None = None,
    ) -> None:
        with self._state_lock:
            if status == "running" or self._active_generations > 1:
                self._api_state = "generating"
            elif status == "success":
                self._api_state = "ok"
            elif status == "error":
                self._api_state = "error"
            self._last_request = {
                "status": status,
                "time": _now_iso(),
                "action": action[:32],
                "failure_class": (str(failure_class)[:64] if failure_class else None),
            }

    # ------------------------------------------------------------------
    # Writable static UI and bounded generated-asset cache
    # ------------------------------------------------------------------

    @property
    def _source_static_dir(self) -> Path:
        return self.config_dir / "static"

    def _copy_static_ui_assets(
        self,
        target_dir: Path,
        expected_root_identity: tuple[int, int],
    ) -> None:
        source_index = self._source_static_dir / "index.html"
        if source_index.is_symlink() or not source_index.is_file():
            raise OSError("bundled static UI index is unavailable")
        index_bytes = source_index.read_bytes()
        if not index_bytes or len(index_bytes) > 2 * 1024 * 1024:
            raise OSError("bundled static UI index has an invalid size")
        _atomic_write_ui_index(
            target_dir,
            expected_root_identity,
            index_bytes,
        )

    def _register_writable_static_ui(self) -> bool:
        # Serve generated assets from the INSTALLED plugin tree's static/,
        # not the data directory. The frozen Steam runtime (2026-07-24
        # Nuitka build) resolves /plugin/{id}/ui/{path} exclusively from the
        # install-dir static/ inference and ignores STATIC_UI_REGISTER
        # directory overrides — assets written under data/static_ui 404 on
        # that host even though register_static_ui reported success. Newer
        # hosts honour the override, and install-dir static/ works for both,
        # so it is the compatible primary.
        installed_static = self._source_static_dir
        try:
            if installed_static.is_symlink() or not installed_static.is_dir():
                raise OSError("installed static directory is unavailable")
            resolved_static = installed_static.resolve()
            root_stat = resolved_static.stat()
            root_identity = (int(root_stat.st_dev), int(root_stat.st_ino))
            _ensure_generated_asset_dir(resolved_static, root_identity)
            asset_dir = resolved_static / _GENERATED_SUBDIR
            self._writable_ui_dir = resolved_static
            self._asset_dir = asset_dir
            self._writable_ui_identity = root_identity
            if not self._asset_dir_is_safe():
                raise OSError("installed static directory changed during setup")
            registered = self.register_static_ui(
                str(resolved_static),
                cache_control="no-cache",
            )
            if registered and self._asset_dir_is_safe():
                return True
            if registered:
                raise OSError("installed static directory changed during registration")
        except Exception as exc:
            self.logger.warning(
                "ImageGenerator installed-static UI setup failed: failure_class={}",
                type(exc).__name__,
            )
        self._asset_dir = None
        self._writable_ui_dir = None
        self._writable_ui_identity = None

        # Legacy fallback: data-directory static_ui. Reached only when the
        # installed tree is read-only; newer hosts serve this fine. Frozen
        # hosts ignore STATIC_UI_REGISTER directory overrides and keep
        # serving the install-tree static/, so files written here would 404
        # even though generation and panel state report success — detect
        # that case and fail the asset cache instead of reporting a live
        # one (surfaced to the panel as asset_cache_available=False).
        if self._frozen_static_ui_overrides_ignored():
            self.logger.warning(
                "ImageGenerator data-directory static UI is unserved on this "
                "frozen host; generated-asset cache disabled"
            )
            self._asset_dir = None
            self._writable_ui_dir = None
            self._writable_ui_identity = None
            return False
        raw_writable_ui = self.data_path("static_ui")
        expected_writable_ui = self.data_path().resolve() / "static_ui"
        self._writable_ui_identity = None
        try:
            if raw_writable_ui.is_symlink():
                raise OSError("writable static UI root must not be a symlink")
            raw_writable_ui.mkdir(parents=True, exist_ok=True)
            if raw_writable_ui.is_symlink():
                raise OSError("writable static UI root must not be a symlink")
            writable_ui = raw_writable_ui.resolve()
            if writable_ui != expected_writable_ui:
                raise OSError("writable static UI root escaped plugin data directory")
            root_stat = writable_ui.stat()
            root_identity = (
                int(root_stat.st_dev),
                int(root_stat.st_ino),
            )
            self._copy_static_ui_assets(
                writable_ui,
                root_identity,
            )
            _ensure_generated_asset_dir(
                writable_ui,
                root_identity,
            )
            asset_dir = writable_ui / _GENERATED_SUBDIR
            self._writable_ui_dir = writable_ui
            self._asset_dir = asset_dir
            self._writable_ui_identity = root_identity
            if not self._asset_dir_is_safe():
                raise OSError("writable static UI root changed during setup")
            registered = self.register_static_ui(
                str(writable_ui),
                cache_control="no-cache",
            )
            if registered and self._asset_dir_is_safe():
                return True
            if registered:
                raise OSError("writable static UI root changed during registration")
        except Exception as exc:
            self.logger.warning(
                "ImageGenerator writable UI setup failed: failure_class={}",
                type(exc).__name__,
            )
        self._asset_dir = None
        self._writable_ui_dir = None
        self._writable_ui_identity = None

        try:
            fallback_registered = self.register_static_ui(
                "static",
                cache_control="no-cache",
            )
        except Exception as exc:
            self.logger.warning(
                "ImageGenerator fallback UI registration failed: failure_class={}",
                type(exc).__name__,
            )
            return False
        if fallback_registered:
            # Keep the installed plugin tree immutable. The bundled UI can
            # still explain the data-directory failure, but generation must
            # fail cleanly rather than writing assets into source/package
            # files.
            self._asset_dir = None
            self._writable_ui_dir = self._source_static_dir
            self._writable_ui_identity = None
        return fallback_registered

    def _frozen_static_ui_overrides_ignored(self) -> bool:
        """Disable directory fallback unless the host serves the probe."""
        probe = None
        index = None
        try:
            probe_root = Path(self.data_path())
            if probe_root.is_symlink():
                return True
            probe_dir = probe_root / f".static_ui_probe_{uuid4().hex}"
            probe_dir.mkdir(parents=True, exist_ok=False)
            index = probe_dir / "index.html"
            index.write_text("<!doctype html><title>Static UI probe</title>", encoding="utf-8")
            probe = probe_dir / f"{uuid4().hex}.txt"
            probe.write_text("ok", encoding="utf-8")
            if not self.register_static_ui(str(probe_dir), cache_control="no-cache"):
                # An unverified override must not enable generated assets.
                return True
            url = (
                f"{self._resolve_public_origin().rstrip('/')}"
                f"/plugin/{quote(self.plugin_id, safe='')}/ui/{probe.name}"
            )
            # _register_writable_static_ui is synchronous (called from the
            # async lifecycle without to_thread), so probe with a blocking
            # client here — it runs once at startup and times out fast.
            with httpx.Client(timeout=1.0, trust_env=False, follow_redirects=False) as client:
                # Registration is queued. Give the host time to consume it
                # before treating a missing probe as an ignored override.
                for attempt in range(10):
                    response = client.get(url)
                    if response.status_code == 200 and response.text == "ok":
                        return False
                    if attempt < 9:
                        time.sleep(0.2)
            return True
        except Exception:
            return True
        finally:
            if probe is not None:
                # Registration changes host state even when HTTP probing fails.
                try:
                    self.register_static_ui(str(self._source_static_dir), cache_control="no-cache")
                except Exception as exc:
                    self.logger.warning("Static UI restore failed: failure_class={}", type(exc).__name__)
                try:
                    probe.unlink(missing_ok=True)
                    if index is not None:
                        index.unlink(missing_ok=True)
                    probe.parent.rmdir()
                except OSError:
                    pass

    def _asset_dir_is_safe(self) -> bool:
        asset_dir = self._asset_dir
        writable_ui = self._writable_ui_dir
        expected_identity = self._writable_ui_identity
        if (
            asset_dir is None
            or writable_ui is None
            or expected_identity is None
        ):
            return False
        try:
            root_stat = writable_ui.stat()
            return (
                not writable_ui.is_symlink()
                and not asset_dir.is_symlink()
                and (int(root_stat.st_dev), int(root_stat.st_ino))
                == expected_identity
                and asset_dir.resolve() == writable_ui.resolve() / _GENERATED_SUBDIR
            )
        except OSError:
            return False

    def _unlink_cached_file(self, filename: str) -> bool:
        writable_ui = self._writable_ui_dir
        expected_identity = self._writable_ui_identity
        if writable_ui is None or expected_identity is None:
            raise OSError("generated asset cache is unavailable")
        return _unlink_asset_file(
            writable_ui,
            expected_identity,
            filename,
        )

    def _cache_files(self) -> list[Path]:
        asset_dir = self._asset_dir
        if asset_dir is None or not self._asset_dir_is_safe() or not asset_dir.is_dir():
            return []
        files: list[Path] = []
        try:
            candidates = list(asset_dir.iterdir())
        except OSError:
            return []
        for path in candidates:
            if (
                path.is_file()
                and not path.is_symlink()
                and (
                    _GENERATED_FILE_PATTERN.fullmatch(path.name)
                    or _GENERATED_TEMP_FILE_PATTERN.fullmatch(path.name)
                    or _GENERATED_THUMB_FILE_PATTERN.fullmatch(path.name)
                )
            ):
                files.append(path)
        return files

    def _generated_files(self) -> list[Path]:
        return [
            path
            for path in self._cache_files()
            if _GENERATED_FILE_PATTERN.fullmatch(path.name)
        ]

    def _prune_cache_sync(self, settings: Mapping[str, Any]) -> dict[str, int]:
        files_with_stats: list[tuple[Path, int, float]] = []
        for path in self._cache_files():
            if _GENERATED_TEMP_FILE_PATTERN.fullmatch(path.name):
                try:
                    self._unlink_cached_file(path.name)
                except OSError:
                    pass
                else:
                    continue
            try:
                stat = path.stat()
            except OSError:
                continue
            files_with_stats.append((path, stat.st_size, stat.st_mtime))
        files_with_stats.sort(key=lambda item: (item[2], item[0].name), reverse=True)

        # Group each original with its chat-preview thumbnail: the pair is
        # one logical generation result and must share a single
        # cache_max_count slot, otherwise a thumbnail can evict its own
        # original (or vice versa) and leave an orphaned file behind.
        groups: dict[str, dict[str, Any]] = {}
        for path, size, mtime in files_with_stats:
            name = path.name
            key = name.removeprefix("thumb_").rsplit(".", 1)[0]
            group = groups.setdefault(
                key,
                {"paths": [], "size": 0, "mtime": 0.0},
            )
            group["paths"].append(path)
            group["size"] += size
            group["mtime"] = max(group["mtime"], mtime)
        ordered_groups = sorted(
            groups.values(),
            key=lambda group: group["mtime"],
            reverse=True,
        )

        kept_count = 0
        kept_bytes = 0
        max_count = int(settings["cache_max_count"])
        max_bytes = int(settings["cache_max_bytes"])
        for group in ordered_groups:
            size = group["size"]
            keep = kept_count < max_count and kept_bytes + size <= max_bytes
            if keep:
                kept_count += 1
                kept_bytes += size
                continue
            for path in group["paths"]:
                try:
                    self._unlink_cached_file(path.name)
                except OSError:
                    pass
        # Report actual on-disk state, including files whose deletion failed,
        # instead of optimistic counters from the intended pruning plan.
        return self._cache_stats_sync()

    async def _prune_cache(self) -> dict[str, int]:
        await self._acquire_lock(self._cache_lock)
        try:
            return await asyncio.to_thread(
                self._prune_cache_sync,
                self._settings_snapshot(),
            )
        finally:
            self._cache_lock.release()

    def _cache_stats_sync(self) -> dict[str, int]:
        # ``count`` is measured in generation groups (an original plus its
        # chat-preview thumbnail), matching the pruning unit in
        # _prune_cache_sync and the cache_max_count semantics enforced by
        # _save_asset's post-write check. Bytes are summed over all files.
        count = 0
        total_bytes = 0
        seen_groups: set[str] = set()
        for path in self._cache_files():
            name = path.name
            if _GENERATED_TEMP_FILE_PATTERN.fullmatch(name):
                continue
            key = name.removeprefix("thumb_").rsplit(".", 1)[0]
            try:
                total_bytes += path.stat().st_size
            except OSError:
                continue
            if key not in seen_groups:
                seen_groups.add(key)
                count += 1
        return {"count": count, "total_bytes": total_bytes}

    async def _cache_stats(self) -> dict[str, int]:
        await self._acquire_lock(self._cache_lock)
        try:
            return await asyncio.to_thread(self._cache_stats_sync)
        finally:
            self._cache_lock.release()

    def _resolve_public_origin(self) -> str:
        for env_name in (
            "NEKO_PLUGIN_SERVER_ORIGIN",
            "NEKO_USER_PLUGIN_SERVER_ORIGIN",
            "NEKO_SERVER_ORIGIN",
        ):
            raw = str(os.getenv(env_name, "") or "").strip().rstrip("/")
            if len(raw) > _URL_MAX_CHARS:
                continue
            parsed = _parse_http_url(raw)
            if (
                parsed is not None
                and not parsed.query
                and not parsed.fragment
                and not parsed.params
            ):
                return f"{parsed.scheme.lower()}://{parsed.netloc}"
        try:
            port = int(str(os.getenv("NEKO_USER_PLUGIN_SERVER_PORT", "")).strip())
            if 1 <= port <= 65535:
                return f"http://127.0.0.1:{port}"
        except (TypeError, ValueError):
            pass
        try:
            from config import USER_PLUGIN_SERVER_PORT

            port = int(USER_PLUGIN_SERVER_PORT)
            if 1 <= port <= 65535:
                return f"http://127.0.0.1:{port}"
        except Exception:
            pass
        return f"http://127.0.0.1:{_DEFAULT_PLUGIN_SERVER_PORT}"

    def _asset_url(self, filename: str) -> str:
        safe_plugin_id = quote(self.plugin_id, safe="")
        safe_filename = quote(filename, safe="")
        path = f"/plugin/{safe_plugin_id}/ui/{_GENERATED_SUBDIR}/{safe_filename}"
        return f"{self._resolve_public_origin().rstrip('/')}{path}"

    async def _save_asset(
        self,
        data: bytes,
        *,
        extension: str,
        secrets: str | Iterable[str] | None = None,
    ) -> tuple[str, str, str | None]:
        asset_dir = self._asset_dir
        writable_ui = self._writable_ui_dir
        expected_identity = self._writable_ui_identity
        if (
            asset_dir is None
            or writable_ui is None
            or expected_identity is None
            or not self._asset_dir_is_safe()
        ):
            raise _GenerationFailure(
                "本地图片缓存不可用，请检查插件数据目录权限",
                "AssetCacheUnavailable",
            )
        filename = f"{uuid4().hex}.{extension}"
        if not _GENERATED_FILE_PATTERN.fullmatch(filename):
            raise _GenerationFailure(
                "无法创建安全的图片文件名",
                "AssetCacheError",
            )
        secret_values = _secret_values(secrets)
        image_url = self._asset_url(filename)
        for _attempt in range(7):
            if not _value_contains_secret(image_url, secret_values):
                break
            filename = f"{uuid4().hex}.{extension}"
            image_url = self._asset_url(filename)
        else:
            raise _GenerationFailure(
                "无法创建不含凭据的安全图片链接，请更换 API 密钥或公开服务地址",
                "SecretUrlCollision",
            )
        await self._acquire_lock(self._cache_lock)
        target = asset_dir / filename
        temp_name = f".{filename}.{uuid4().hex}.tmp"
        try:
            if not self._asset_dir_is_safe():
                raise _GenerationFailure(
                    "本地图片缓存路径不安全，已拒绝写入",
                    "AssetCacheUnsafe",
                )
            settings = self._settings_snapshot()

            def write_and_prune() -> dict[str, int]:
                _atomic_write_bytes(
                    writable_ui,
                    expected_identity,
                    temp_name,
                    filename,
                    data,
                )
                if not self._asset_dir_is_safe():
                    raise _GenerationFailure(
                        "本地图片缓存路径在写入期间发生变化，已拒绝结果",
                        "AssetCacheUnsafe",
                    )
                return self._prune_cache_sync(settings)

            worker = asyncio.create_task(
                asyncio.to_thread(write_and_prune)
            )
            try:
                stats = await asyncio.shield(worker)
            except asyncio.CancelledError:
                # to_thread cannot stop a filesystem write. Keep the cache
                # lock until that worker has also enforced the configured
                # bounds, then propagate cancellation to the caller.
                while not worker.done():
                    try:
                        await asyncio.shield(worker)
                    except asyncio.CancelledError:
                        continue
                    except BaseException:
                        break
                if worker.done() and not worker.cancelled():
                    try:
                        worker.exception()
                    except BaseException:
                        pass
                raise
            if (
                not self._asset_dir_is_safe()
                or not target.is_file()
                or target.is_symlink()
                or stats["count"] > int(settings["cache_max_count"])
                or stats["total_bytes"] > int(settings["cache_max_bytes"])
            ):
                try:
                    self._unlink_cached_file(filename)
                except OSError:
                    pass
                await asyncio.to_thread(self._prune_cache_sync, settings)
                raise _GenerationFailure(
                    "无法在当前文件系统上执行图片缓存容量限制",
                    "AssetCacheLimit",
                )

            async def thumbnail_and_prune():
                preview = await self._generate_thumbnail(target, filename, extension)
                stats = self._cache_stats_sync()
                # The preview is optional: keep the paid original when the
                # additional thumbnail would exceed the byte budget.
                if stats["total_bytes"] > int(settings["cache_max_bytes"]):
                    self._unlink_cached_file(f"thumb_{filename.rsplit('.', 1)[0]}.png")
                    preview = None
                stats = self._prune_cache_sync(settings)
                if (not target.is_file() or stats["count"] > int(settings["cache_max_count"])
                        or stats["total_bytes"] > int(settings["cache_max_bytes"])):
                    raise _GenerationFailure("无法执行图片缓存容量限制", "AssetCacheLimit")
                return preview

            thumbnail_worker = asyncio.create_task(thumbnail_and_prune())
            try:
                thumb_url = await asyncio.shield(thumbnail_worker)
            except asyncio.CancelledError:
                while not thumbnail_worker.done():
                    try:
                        await asyncio.shield(thumbnail_worker)
                    except asyncio.CancelledError:
                        continue
                    except Exception:
                        break
                if not thumbnail_worker.cancelled():
                    thumbnail_worker.exception()
                raise
        except _GenerationFailure:
            raise
        except Exception:
            try:
                self._unlink_cached_file(filename)
            except OSError:
                pass
            try:
                self._unlink_cached_file(f"thumb_{filename.rsplit('.', 1)[0]}.png")
            except OSError:
                pass
            raise _GenerationFailure(
                "保存生成图片失败，请检查插件数据目录权限",
                "AssetCacheError",
            ) from None
        finally:
            self._cache_lock.release()

        return image_url, filename, thumb_url

    # ------------------------------------------------------------------
    # Safe provider request and output handling
    # ------------------------------------------------------------------

    @staticmethod
    def _build_request_body(
        *,
        settings: Mapping[str, Any],
        prompt: str,
        size: str,
        quality: str,
        style: str,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": settings["model"],
            "prompt": prompt,
            "n": 1,
        }
        if size and size != "auto":
            body["size"] = size
        if quality and quality != "auto":
            body["quality"] = quality
        if style and style != "auto":
            body["style"] = style
        output_format = settings["output_format"]
        if output_format != "auto":
            body["output_format"] = output_format
        # GPT Image models always return Base64 and reject the legacy
        # response_format field. Other OpenAI-compatible models need an
        # explicit b64_json request so URL-only output can be refused without
        # introducing a server-side download path.
        model_name = str(settings["model"]).lower().rsplit("/", 1)[-1]
        if not any(part.startswith(("gpt-image-", "chatgpt-image-")) for part in model_name.split(":")):
            body["response_format"] = "b64_json"
        return body

    async def _request_generation(
        self,
        *,
        settings: Mapping[str, Any],
        api_key: str,
        prompt: str,
        size: str,
        quality: str,
        style: str,
    ) -> tuple[bytes, str, str, str]:
        flavor = str(
            PROVIDER_PRESETS.get(str(settings.get("provider") or ""), {}).get(
                "api_flavor", "openai_compatible"
            )
        )
        if flavor == "dashscope_native":
            return await self._request_generation_dashscope(
                settings=settings,
                api_key=api_key,
                prompt=prompt,
                size=size,
            )
        return await self._request_generation_openai(
            settings=settings,
            api_key=api_key,
            prompt=prompt,
            size=size,
            quality=quality,
            style=style,
        )

    @staticmethod
    def _dashscope_size(size: str) -> str:
        # OpenAI style "1024x1024" -> DashScope style "1024*1024".
        return size.lower().replace("x", "*")

    async def _request_task_json(self, client, method: str, url: str, **kwargs):
        try:
            async with client.stream(
                method, url, follow_redirects=False, **kwargs
            ) as response:
                if response.status_code in (401, 403):
                    raise _GenerationFailure("图片服务拒绝了凭据", f"ProviderHttp{response.status_code}")
                if not 200 <= response.status_code < 300:
                    raise _GenerationFailure(
                        f"图片服务请求失败（HTTP {response.status_code}）",
                        f"ProviderHttp{response.status_code}",
                    )
                body = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=65536):
                    if len(body) + len(chunk) > 1_048_576:
                        raise _GenerationFailure("图片服务响应过大", "ProviderResponseTooLarge")
                    body.extend(chunk)
            payload = json.loads(body)
            if not isinstance(payload, dict):
                raise _GenerationFailure("图片服务响应格式无效", "MalformedResponse")
            return payload
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise _GenerationFailure("图片服务响应格式无效", "InvalidProviderJson") from None
        except httpx.TimeoutException:
            raise _GenerationFailure("生成图片超时，请稍后重试", "ProviderTimeout") from None
        except httpx.RequestError:
            raise _GenerationFailure("无法连接图片服务", "ProviderNetworkError") from None

    async def _request_generation_dashscope(
        self,
        *,
        settings: Mapping[str, Any],
        api_key: str,
        prompt: str,
        size: str,
    ) -> tuple[bytes, str, str, str]:
        max_bytes = int(settings["max_download_bytes"])
        timeout_seconds = float(settings["timeout_seconds"])
        deadline = time.monotonic() + timeout_seconds
        parsed_base = _parse_http_url(str(settings["api_base_url"]))
        if (parsed_base is None or parsed_base.scheme != "https"
                or parsed_base.hostname not in {"dashscope.aliyuncs.com", "dashscope-intl.aliyuncs.com"}
                or parsed_base.port not in (None, 443)):
            raise _GenerationFailure("百炼 API 地址无效", "InvalidProviderUrl")
        origin = f"https://{parsed_base.hostname}"
        create_endpoint = (
            f"{origin}/api/v1/services/aigc/"
            "text2image/image-synthesis"
        )
        create_body: dict[str, Any] = {
            "model": settings["model"],
            "input": {"prompt": prompt},
            "parameters": {"n": 1},
        }
        if size and size != "auto":
            create_body["parameters"]["size"] = self._dashscope_size(size)

        async def post_create() -> dict[str, Any]:
            client = self._get_client()
            try:
                payload = await self._request_task_json(
                    client, "POST", create_endpoint,
                    json=create_body,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                        "X-DashScope-Async": "enable",
                        "User-Agent": USER_AGENT,
                    },
                    timeout=30,
                )
            except httpx.RequestError:
                raise _GenerationFailure(
                    "无法连接图片生成服务，请检查 API 地址和网络",
                    "ProviderNetworkError",
                ) from None
            finally:
                try:
                    await client.aclose()
                except Exception:
                    pass
            return dict(payload)

        created = await post_create()
        output = created.get("output")
        task_id = output.get("task_id") if isinstance(output, Mapping) else None
        if not isinstance(task_id, str) or not task_id.strip():
            raise _GenerationFailure(
                "图片服务没有返回任务编号",
                "MalformedResponse",
            )
        task_id = task_id.strip()
        if not re.fullmatch(r"[0-9A-Za-z-]{1,128}", task_id):
            raise _GenerationFailure(
                "图片服务返回的任务编号格式无效",
                "MalformedResponse",
            )
        task_endpoint = f"{origin}/api/v1/tasks/{task_id}"

        poll_client = self._get_client()
        try:
            while True:
                if time.monotonic() >= deadline:
                    raise _GenerationFailure(
                        "生成图片超时，请稍后重试或提高超时设置",
                        "ProviderTimeout",
                    )
                try:
                    payload = await self._request_task_json(
                        poll_client, "GET", task_endpoint,
                        headers={
                            "Authorization": f"Bearer {api_key}",
                            "Accept": "application/json",
                            "User-Agent": USER_AGENT,
                        },
                        timeout=30,
                    )
                except httpx.RequestError:
                    raise _GenerationFailure(
                        "无法连接图片生成服务，请检查 API 地址和网络",
                        "ProviderNetworkError",
                    ) from None
                task_output = payload.get("output") if isinstance(payload, Mapping) else None
                if not isinstance(task_output, Mapping):
                    raise _GenerationFailure(
                        "图片服务返回的数据格式无效",
                        "MalformedResponse",
                    )
                status = str(task_output.get("task_status") or "").upper()
                if status == "SUCCEEDED":
                    results = task_output.get("results")
                    first = results[0] if isinstance(results, list) and results else None
                    image_url = first.get("url") if isinstance(first, Mapping) else None
                    if not isinstance(image_url, str) or not image_url.strip():
                        raise _GenerationFailure(
                            "图片服务没有返回图片",
                            "MalformedResponse",
                        )
                    image_url = image_url.strip()
                    parsed_image = _parse_http_url(image_url)
                    if (
                        parsed_image is None
                        or parsed_image.scheme.lower() != "https"
                        or _is_private_or_loopback_hostname(parsed_image.hostname)
                        or not re.fullmatch(
                            r"[a-z0-9-]+\.oss-[a-z0-9-]+\.aliyuncs\.com",
                            parsed_image.hostname or "",
                        )
                        or "-internal." in (parsed_image.hostname or "")
                    ):
                        raise _GenerationFailure(
                            "图片服务返回了不安全的图片地址",
                            "ProviderUrlOutputRejected",
                        )
                    return await self._download_provider_image(
                        image_url,
                        max_bytes=max_bytes,
                        timeout_seconds=max(5.0, deadline - time.monotonic()),
                    )
                if status in {"FAILED", "CANCELED", "UNKNOWN"}:
                    raise _GenerationFailure(
                        "图片服务生成失败，请检查模型、尺寸或账户额度",
                        "ProviderTaskFailed",
                    )
                await asyncio.sleep(2)
        finally:
            try:
                await poll_client.aclose()
            except Exception:
                pass

    async def _download_provider_image(
        self,
        image_url: str,
        *,
        max_bytes: int,
        timeout_seconds: float,
    ) -> tuple[bytes, str, str, str]:
        client = self._get_client(trust_env=False)
        try:
            async with client.stream(
                "GET",
                image_url,
                headers={
                    "Accept": "image/png,image/jpeg,image/webp",
                    "Accept-Encoding": "identity",
                    "User-Agent": USER_AGENT,
                },
                timeout=timeout_seconds,
                follow_redirects=False,
            ) as response:
                status = int(getattr(response, "status_code", 0) or 0)
                if status < 200 or status >= 300:
                    raise _GenerationFailure(
                        f"下载生成图片失败（HTTP {status}）",
                        f"ProviderHttp{status}",
                    )
                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_bytes():
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > max_bytes:
                        raise _GenerationFailure(
                            "生成图片超过了配置的最大字节数",
                            "ImageTooLarge",
                        )
                    chunks.append(bytes(chunk))
                raw = b"".join(chunks)
        except _GenerationFailure:
            raise
        except httpx.TimeoutException:
            raise _GenerationFailure(
                "生成图片超时，请稍后重试或提高超时设置",
                "ProviderTimeout",
            ) from None
        except httpx.RequestError:
            raise _GenerationFailure(
                "无法连接图片生成服务，请检查 API 地址和网络",
                "ProviderNetworkError",
            ) from None
        finally:
            try:
                await client.aclose()
            except Exception:
                pass
        decoded, mime, extension = await asyncio.to_thread(
            _sanitize_image,
            raw,
            max_bytes=max_bytes,
        )
        return decoded, mime, extension, ""

    async def _request_generation_openai(
        self,
        *,
        settings: Mapping[str, Any],
        api_key: str,
        prompt: str,
        size: str,
        quality: str,
        style: str,
    ) -> tuple[bytes, str, str, str]:
        endpoint = f"{str(settings['api_base_url']).rstrip('/')}/images/generations"
        body = self._build_request_body(
            settings=settings,
            prompt=prompt,
            size=size,
            quality=quality,
            style=style,
        )
        parsed_endpoint = _parse_http_url(endpoint)
        endpoint_is_loopback = (
            parsed_endpoint is not None
            and _is_loopback_hostname(parsed_endpoint.hostname)
        )
        client = self._get_client(trust_env=not endpoint_is_loopback)
        max_bytes = int(settings["max_download_bytes"])
        max_json_bytes = ((max_bytes + 2) // 3) * 4 + 1_048_576
        try:
            async with client.stream(
                "POST",
                endpoint,
                json=body,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "Accept-Encoding": "identity",
                    "User-Agent": USER_AGENT,
                },
                timeout=float(settings["timeout_seconds"]),
                follow_redirects=False,
            ) as response:
                status = int(getattr(response, "status_code", 0) or 0)
                if status < 200 or status >= 300:
                    if status in {401, 403}:
                        message = "图片服务拒绝了凭据，请检查 API 密钥"
                    elif status in {400, 422}:
                        message = (
                            "图片服务拒绝了生成参数，请检查模型、尺寸、质量、"
                            "风格和格式设置"
                        )
                    elif status == 404:
                        message = "图片服务端点不存在，请检查 API 地址和模型"
                    elif status == 429:
                        message = "图片服务请求过于频繁或额度不足，请稍后重试"
                    elif status >= 500:
                        message = f"图片服务暂时不可用（HTTP {status}）"
                    else:
                        message = f"图片服务请求失败（HTTP {status}）"
                    raise _GenerationFailure(
                        message,
                        f"ProviderHttp{status}",
                    )

                response_headers = getattr(response, "headers", {})
                content_encoding = (
                    response_headers.get("content-encoding")
                    if isinstance(response_headers, Mapping)
                    else None
                )
                encodings = {
                    item.strip().lower()
                    for item in str(content_encoding or "").split(",")
                    if item.strip()
                }
                if encodings.difference({"identity"}):
                    raise _GenerationFailure(
                        "图片服务返回了不支持的压缩响应",
                        "UnsupportedContentEncoding",
                    )
                content_length = (
                    response_headers.get("content-length")
                    if isinstance(response_headers, Mapping)
                    else None
                )
                if content_length:
                    try:
                        declared_length = int(content_length)
                    except ValueError:
                        declared_length = -1
                    if declared_length < 0:
                        raise _GenerationFailure(
                            "图片服务返回了无效的响应长度",
                            "MalformedResponse",
                        )
                    if declared_length > max_json_bytes:
                        raise _GenerationFailure(
                            "图片服务返回的数据超过了配置的最大字节数",
                            "ImageTooLarge",
                        )

                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_bytes():
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > max_json_bytes:
                        raise _GenerationFailure(
                            "图片服务返回的数据超过了配置的最大字节数",
                            "ImageTooLarge",
                        )
                    chunks.append(bytes(chunk))
                raw_response = b"".join(chunks)
        except _GenerationFailure:
            raise
        except httpx.TimeoutException:
            raise _GenerationFailure(
                "生成图片超时，请稍后重试或提高超时设置",
                "ProviderTimeout",
            ) from None
        except httpx.RequestError:
            raise _GenerationFailure(
                "无法连接图片生成服务，请检查 API 地址和网络",
                "ProviderNetworkError",
            ) from None
        except Exception:
            raise _GenerationFailure(
                "请求图片生成服务时发生错误",
                "ProviderRequestError",
            ) from None
        finally:
            try:
                await client.aclose()
            except Exception:
                self.logger.warning(
                    "ImageGenerator HTTP client close failed: "
                    "failure_class=ClientCloseError"
                )

        try:
            payload = json.loads(raw_response)
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
            raise _GenerationFailure(
                "图片服务返回了无法解析的数据",
                "InvalidProviderJson",
            ) from None
        if not isinstance(payload, Mapping):
            raise _GenerationFailure(
                "图片服务返回的数据格式无效",
                "MalformedResponse",
            )
        data_items = payload.get("data")
        if not isinstance(data_items, list) or not data_items:
            raise _GenerationFailure(
                "图片服务没有返回图片",
                "MalformedResponse",
            )
        first = data_items[0]
        if not isinstance(first, Mapping):
            raise _GenerationFailure(
                "图片服务返回的数据格式无效",
                "MalformedResponse",
            )
        revised_prompt = _redact_text(
            first.get("revised_prompt"),
            self._known_secrets_snapshot(api_key),
            max_chars=_REVISED_PROMPT_MAX_CHARS,
        )
        b64_value = first.get("b64_json")
        url_value = first.get("url")
        if isinstance(b64_value, str) and b64_value.strip():
            decoded, mime, extension = await asyncio.to_thread(
                _decode_b64_image,
                b64_value,
                max_bytes=max_bytes,
            )
            return decoded, mime, extension, revised_prompt
        if isinstance(url_value, str) and url_value.strip():
            raise _GenerationFailure(
                "图片服务只返回了远程 URL；为防止服务端请求伪造，"
                "本插件仅接受 b64_json 图片数据",
                "ProviderUrlOutputRejected",
            )
        raise _GenerationFailure(
            "图片服务未返回 b64_json 图片数据",
            "MalformedResponse",
        )

    def _resolve_generation_options(
        self,
        *,
        settings: Mapping[str, Any],
        prompt: Any,
        size: Any,
        quality: Any,
        style: Any,
    ) -> tuple[str, str, str, str]:
        cleaned_prompt = _clean_text(
            prompt,
            label="图片描述",
            max_chars=_PROMPT_MAX_CHARS,
        )
        values: list[str] = []
        for supplied, default_field, allowed_field, label in (
            (size, "default_size", "allowed_sizes", "尺寸"),
            (quality, "default_quality", "allowed_qualities", "质量"),
            (style, "default_style", "allowed_styles", "风格"),
        ):
            if supplied is None:
                resolved = str(settings[default_field])
            else:
                resolved = _clean_text(
                    supplied,
                    label=label,
                    max_chars=32,
                    allow_empty=(label == "风格"),
                ).lower()
            if resolved not in settings[allowed_field]:
                raise SdkError(
                    f"{label}不在已配置的允许列表中，请使用管理面板中列出的有效选项"
                )
            values.append(resolved)
        return cleaned_prompt, values[0], values[1], values[2]

    # ------------------------------------------------------------------
    # History and user-visible Markdown delivery
    # ------------------------------------------------------------------

    def _project_history_record(
        self,
        record: dict[str, Any],
    ) -> dict[str, Any]:
        projected = dict(record)
        result_url = str(projected.get("result_url") or "")
        if not result_url:
            return projected
        parsed = _parse_http_url(result_url)
        prefix = f"/plugin/{quote(self.plugin_id, safe='')}/ui/{_GENERATED_SUBDIR}/"
        if (
            parsed is None
            or not parsed.path.startswith(prefix)
            or _origin_tuple(result_url) != _origin_tuple(self._resolve_public_origin())
        ):
            projected["result_url"] = ""
            return projected
        filename = parsed.path[len(prefix) :]
        asset_dir = self._asset_dir
        if (
            "/" in filename
            or not _GENERATED_FILE_PATTERN.fullmatch(filename)
            or asset_dir is None
            or not self._asset_dir_is_safe()
            or not (asset_dir / filename).is_file()
            or (asset_dir / filename).is_symlink()
        ):
            projected["result_url"] = ""
        else:
            thumb_name = f"thumb_{filename.rsplit('.', 1)[0]}.png"
            thumb = asset_dir / thumb_name
            if thumb.is_file() and not thumb.is_symlink():
                projected["preview_url"] = self._asset_url(thumb_name)
        return projected

    async def _load_history(
        self,
        *,
        api_key: str = "",
        checked: bool = False,
        project: bool = True,
    ) -> list[dict[str, Any]]:
        success, raw = await self._store_get_checked(_HISTORY_STORE_KEY, [])
        if checked and not success:
            raise SdkError("无法安全读取生成历史（StoreError）")
        if not isinstance(raw, list):
            return []
        secrets = self._known_secrets_snapshot(api_key)
        history: list[dict[str, Any]] = []
        for item in raw:
            normalized = _safe_history_record(item, secrets)
            if normalized is not None:
                history.append(self._project_history_record(normalized) if project else normalized)
        return history

    async def _record_history(
        self,
        *,
        prompt: str,
        model: str,
        status: str,
        result_url: str,
        api_key: str,
        history_limit: int | None = None,
    ) -> None:
        if not bool(getattr(self.store, "enabled", False)):
            return
        await self._acquire_lock(self._history_lock)
        try:
            try:
                history = await self._load_history(api_key=api_key, checked=True, project=False)
            except SdkError:
                return
            secrets = self._known_secrets_snapshot(api_key)
            history.insert(
                0,
                {
                    "id": uuid4().hex,
                    "timestamp": _now_iso(),
                    "model": _redact_text(
                        model,
                        secrets,
                        max_chars=_MODEL_MAX_CHARS,
                    ),
                    "prompt_excerpt": _redact_text(
                        prompt,
                        secrets,
                        max_chars=_PROMPT_EXCERPT_MAX_CHARS,
                    ),
                    "result_url": (
                        result_url[:_URL_MAX_CHARS]
                        if _parse_http_url(result_url) is not None
                        else ""
                    ),
                    "status": status,
                },
            )
            current_limit = int(self._settings_snapshot()["history_limit"])
            limit = current_limit
            if not await self._drain_on_cancel(self._store_set(
                _HISTORY_STORE_KEY,
                history[:limit],
            )):
                self.logger.warning(
                    "ImageGenerator history write failed: failure_class=StoreError"
                )
        finally:
            self._history_lock.release()

    # ------------------------------------------------------------------
    # Thumbnail generation (chat preview)
    # ------------------------------------------------------------------

    async def _generate_thumbnail(
        self,
        target: Path,
        filename: str,
        extension: str,
    ) -> str | None:
        """Generate a 280px thumbnail next to the original so the chat preview
        does not blow up the dialog.

        The Steam frozen runtime ships no PIL, so we shell out to PowerShell
        System.Drawing on Windows.  If anything fails we simply return None
        and the caller falls back to the original URL — the preview is a
        convenience, not a hard dependency.
        """
        thumb_name = f"thumb_{filename.rsplit('.', 1)[0]}.png"
        # Windows-only: System.Drawing is the most reliable built-in image
        # resizer on the Steam deck (no PIL in the frozen runtime).
        ps_script = (
            "Add-Type -AssemblyName System.Drawing; "
            "$img = [System.Drawing.Image]::FromFile('{src}'); "
            "$ratio = [Math]::Min(280 / $img.Width, 280 / $img.Height); "
            "$w = [int]($img.Width * $ratio); $h = [int]($img.Height * $ratio); "
            "$bmp = New-Object System.Drawing.Bitmap($w, $h); "
            "$g = [System.Drawing.Graphics]::FromImage($bmp); "
            "$g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic; "
            "$g.DrawImage($img, 0, 0, $w, $h); "
            "$ms = New-Object System.IO.MemoryStream; "
            "$bmp.Save($ms, [System.Drawing.Imaging.ImageFormat]::Png); "
            "[Console]::Write([Convert]::ToBase64String($ms.ToArray())); "
            "$ms.Dispose(); $g.Dispose(); $bmp.Dispose(); $img.Dispose()"
        ).format(src=str(target).replace("'", "''"))
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                "powershell", "-NoProfile", "-Command", ps_script,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            output, _ = await asyncio.wait_for(proc.communicate(), timeout=15.0)
            if proc.returncode == 0 and output:
                data = base64.b64decode(output, validate=True)
                if _verified_image_format(data) != "PNG":
                    return None
                _atomic_write_bytes(
                    self._writable_ui_dir, self._writable_ui_identity,
                    f".{filename}.{uuid4().hex}.tmp", thumb_name, data,
                )
                return self._asset_url(thumb_name)
        except Exception:
            pass
        finally:
            if proc is not None and proc.returncode is None:
                proc.kill()
                await proc.wait()
        return None

    def _display_markdown(self, image_url: str, thumb_url: str | None = None) -> str:
        # The chat frontend renders Markdown images without any size constraint,
        # which would let a 1–2MB generated image blow up the dialog.  Use the
        # thumbnail (if available) for the inline preview, and link the full
        # original separately.  The image part is still pushed alongside for
        # future hosts that render native image parts with proper bounds.
        preview_url = thumb_url or image_url
        return (
            f"### 图片已生成\n\n![AI 生成图片]({preview_url})\n\n[打开原图]({image_url})"
        )

    def _push_chat_image(
        self,
        *,
        image_url: str,
        geometry: tuple[int, int] | None,
        fallback_markdown: str,
    ) -> bool:
        """Push the generated image to the chat.

        The v2 ``parts`` schema supports native ``image`` parts, but the
        current host pipeline drops URL-based media parts before they reach
        the chat frontend.  To ensure the user actually sees the result we
        send a Markdown text part containing the image URL as the primary
        payload, and keep the native image part alongside it for future
        hosts that learn to render image parts directly.

        The Markdown text drives the ``passthrough_to_chat_bubble`` path
        (visibility=["chat"], ai_behavior="blind"), which renders the
        Markdown verbatim — including the ``![...](url)`` image tag — in
        the chat bubble.
        """
        image_part: dict[str, Any] = {"type": "image", "url": image_url}
        if geometry is not None:
            image_part["width"], image_part["height"] = geometry
        try:
            receipt = self.push_message(
                visibility=["chat"],
                ai_behavior="blind",
                parts=[
                    {"type": "text", "text": fallback_markdown},
                    image_part,
                ],
                source="image_generator",
                priority=2,
                metadata={"event_type": "image_generated"},
            )
            if not isinstance(receipt, Mapping) or receipt.get("submitted", True):
                return True
            # The message plane accepted the call but refused submission
            # (backpressure, transport unavailable, ...). Do not report
            # success — fall through to the text-only retry so the paid
            # result still reaches the chat.
            self.logger.warning(
                "ImageGenerator chat image push rejected: failure_class=MessagePlaneRefused"
            )
        except Exception as exc:
            self.logger.warning(
                "ImageGenerator chat image push failed: failure_class={}",
                type(exc).__name__,
            )
        # Older hosts may reject multi-part messages; fall back to a
        # text-only push so the paid result is never silently lost.
        try:
            receipt = self.push_message(
                visibility=["chat"],
                ai_behavior="blind",
                parts=[{"type": "text", "text": fallback_markdown}],
                source="image_generator",
                priority=2,
                metadata={"event_type": "image_generated"},
            )
            if not isinstance(receipt, Mapping) or receipt.get("submitted", True):
                return True
            self.logger.warning(
                "ImageGenerator chat display rejected: failure_class=MessagePlaneRefused"
            )
            return False
        except Exception as exc:
            self.logger.warning(
                "ImageGenerator chat display failed: failure_class={}",
                type(exc).__name__,
            )
            return False

    async def _generate(
        self,
        *,
        prompt: Any,
        size: Any = None,
        quality: Any = None,
        style: Any = None,
        action: str,
        auto_show_override: bool | None,
    ):
        # Collapse duplicate dispatches onto one real API call. The host may
        # route the same user request through both the llm_tool and the
        # plugin_entry registration concurrently.
        # Dedup only applies to the LLM-facing action: panel test_generation
        # is user-driven and must always run fresh.
        dedup_key: str | None = None
        if action == "generate_image":
            try:
                settings, api_key = await self._generation_config_snapshot()
                prompt, size, quality, style = self._resolve_generation_options(
                    settings=settings, prompt=prompt, size=size,
                    quality=quality, style=style,
                )
            except SdkError as exc:
                return Err(exc)
            candidate_key = hashlib.sha256(
                json.dumps(
                    [prompt, size, quality, style,
                     settings["auto_show_in_chat"] if auto_show_override is None
                     else auto_show_override, settings, api_key],
                    ensure_ascii=False, separators=(",", ":"), sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()
            # Check-and-register must be atomic: a lookup here followed by
            # registration after an await would let two identical concurrent
            # calls both observe an empty `_inflight` and each start their own
            # generation, defeating the double-billing protection exactly for
            # the dual-dispatch scenario it exists for. If another identical
            # call arrives while `_run_dedup_generation` (started below,
            # await-free) is still in its synchronous prefix, that call sees
            # the in-flight task and collapses onto it instead of billing a
            # second generation.
            existing = self._inflight.get(candidate_key)
            if existing is not None:
                self.logger.info(
                    "ImageGenerator duplicate request collapsed: action={} "
                    "prompt_len={}",
                    action,
                    len(str(prompt)),
                )
                try:
                    return await asyncio.shield(existing)
                except Exception:
                    # The in-flight attempt failed; fall through to a fresh
                    # run so the caller still gets a real error/result rather
                    # than a stale exception.
                    pass
            else:
                dedup_key = candidate_key
                # Reserve synchronously, before a queued settings save resumes.
                self._active_generations += 1
                self._inflight[dedup_key] = asyncio.ensure_future(
                    self._run_dedup_generation(
                        prompt=prompt,
                        size=size,
                        quality=quality,
                        style=style,
                        action=action,
                        auto_show_override=auto_show_override,
                        config_snapshot=(settings, api_key),
                        reserved=True,
                    )
                )
                self.logger.info(
                    "ImageGenerator request registered: action={} prompt_len={}",
                    action,
                    len(str(prompt)),
                )
                task = self._inflight[dedup_key]

                def discard(completed: asyncio.Task) -> None:
                    self._release_generation()
                    if self._inflight.get(candidate_key) is completed:
                        self._inflight.pop(candidate_key, None)
                    if not completed.cancelled():
                        completed.exception()

                task.add_done_callback(discard)
                return await asyncio.shield(task)
        return await self._run_dedup_generation(
            prompt=prompt,
            size=size,
            quality=quality,
            style=style,
            action=action,
            auto_show_override=auto_show_override,
        )

    async def _run_dedup_generation(self, reserved: bool = False, **kwargs: Any):
        if not reserved:
            self._active_generations += 1
        try:
            return await self._execute_generation(**kwargs)
        finally:
            if not reserved:
                self._release_generation()

    def _release_generation(self) -> None:
        self._active_generations -= 1
        if self._active_generations == 0 and self._api_state == "generating":
            with self._state_lock:
                failed = self._last_request.get("status") == "error"
                self._api_state = "error" if failed else "ok"
            self.report_status({"status": "error" if failed else "running"})

    def _cache_limits_decrease(self, settings: Mapping[str, Any]) -> bool:
        current = self._settings_snapshot()
        return bool(self._active_generations) and any(
            settings[key] < current[key]
            for key in ("cache_max_bytes", "cache_max_count", "max_download_bytes")
        )

    async def _execute_generation(
        self,
        *,
        prompt: Any,
        size: Any = None,
        quality: Any = None,
        style: Any = None,
        action: str,
        auto_show_override: bool | None,
        config_snapshot: tuple[dict[str, Any], str] | None = None,
    ):
        """Run one real (deduplicated) generation end to end."""
        try:
            settings, api_key = config_snapshot or await self._generation_config_snapshot()
            cleaned_prompt, resolved_size, resolved_quality, resolved_style = (
                self._resolve_generation_options(
                    settings=settings,
                    prompt=prompt,
                    size=size,
                    quality=quality,
                    style=style,
                )
            )
        except SdkError as exc:
            return Err(exc)

        if not api_key:
            await self._record_history(
                prompt=cleaned_prompt,
                model=str(settings["model"]),
                status="failed",
                result_url="",
                api_key="",
                history_limit=int(settings["history_limit"]),
            )
            return Err(
                SdkError("尚未配置 API 密钥，请在 image_generator 管理面板中设置")
            )
        if _settings_contain_secret(settings, api_key):
            return Err(
                SdkError("检测到设置字段包含 API 密钥；请在管理面板重新保存安全设置")
            )

        if not self._asset_dir_is_safe():
            return Err(SdkError("生成图片缓存不可用，无法安全保存生成结果"))

        self._set_request_state(action=action, status="running")
        self.report_status({"status": "generating"})
        self.logger.info(
            "ImageGenerator request started: action={} prompt_len={} "
            "size_configured={} quality_configured={} style_configured={}",
            action,
            len(cleaned_prompt),
            bool(resolved_size and resolved_size != "auto"),
            bool(resolved_quality and resolved_quality != "auto"),
            bool(resolved_style),
        )

        async def _run_generation():
            try:
                try:
                    async with asyncio.timeout(float(settings["timeout_seconds"])):
                        (
                            image_bytes,
                            _mime,
                            extension,
                            revised_prompt,
                        ) = await self._request_generation(
                            settings=settings,
                            api_key=api_key,
                            prompt=cleaned_prompt,
                            size=resolved_size,
                            quality=resolved_quality,
                            style=resolved_style,
                        )
                except TimeoutError:
                    raise _GenerationFailure(
                        "生成图片超过了配置的总超时时间，请稍后重试",
                        "GenerationTimeout",
                    ) from None
                image_url, _filename, thumb_url = await self._save_asset(
                    image_bytes,
                    extension=extension,
                    secrets=self._known_secrets_snapshot(api_key),
                )
            except _GenerationFailure as exc:
                self._set_request_state(
                    action=action,
                    status="error",
                    failure_class=exc.failure_class,
                )
                self._report_generation_completion(
                    {
                        "status": "error",
                        "failure_class": exc.failure_class,
                    }
                )
                await self._record_history(
                    prompt=cleaned_prompt,
                    model=str(settings["model"]),
                    status="failed",
                    result_url="",
                    api_key=api_key,
                    history_limit=int(settings["history_limit"]),
                )
                self.logger.warning(
                    "ImageGenerator request failed: action={} failure_class={}",
                    action,
                    exc.failure_class,
                )
                return Err(SdkError(exc.message))
            except Exception as exc:
                failure_class = type(exc).__name__
                self._set_request_state(
                    action=action,
                    status="error",
                    failure_class=failure_class,
                )
                self._report_generation_completion({"status": "error", "failure_class": failure_class})
                await self._record_history(
                    prompt=cleaned_prompt,
                    model=str(settings["model"]),
                    status="failed",
                    result_url="",
                    api_key=api_key,
                    history_limit=int(settings["history_limit"]),
                )
                self.logger.warning(
                    "ImageGenerator unexpected failure: action={} failure_class={}",
                    action,
                    failure_class,
                )
                return Err(SdkError("生成图片时发生内部错误，请稍后重试"))

            return await self._finalize_success(
                settings=settings,
                api_key=api_key,
                cleaned_prompt=cleaned_prompt,
                action=action,
                image_bytes=image_bytes,
                image_url=image_url,
                thumb_url=thumb_url,
                revised_prompt=revised_prompt,
                auto_show_override=auto_show_override,
            )

        try:
            return await _run_generation()
        except asyncio.CancelledError:
            self._set_request_state(action=action, status="error", failure_class="CancelledError")
            self._report_generation_completion({"status": "error", "failure_class": "CancelledError"})
            raise

    def _report_generation_completion(self, payload: dict[str, Any]) -> None:
        self.report_status({"status": "generating"} if self._active_generations > 1 else payload)

    async def _finalize_success(
        self,
        *,
        settings: dict[str, Any],
        api_key: str,
        cleaned_prompt: str,
        action: str,
        image_bytes: bytes,
        image_url: str,
        thumb_url: str | None,
        revised_prompt: Any,
        auto_show_override: bool | None,
    ):
        markdown = self._display_markdown(image_url, thumb_url=thumb_url)
        should_show = (
            bool(settings["auto_show_in_chat"])
            if auto_show_override is None
            else auto_show_override
        )
        geometry = _image_geometry(image_bytes)
        push_attempted = False
        if should_show:
            push_attempted = self._push_chat_image(
                image_url=image_url,
                geometry=geometry,
                fallback_markdown=markdown,
            )
        if push_attempted:
            message = "图片已生成，已提交聊天显示请求"
            instruction = (
                "图片已提交聊天显示，但尚未确认送达。"
                "请在回复中附上 image_url 的可点击链接，确保 {MASTER_NAME} 能打开图片。"
            )
            result_fields: dict[str, Any] = {
                "message": message,
                "image_url": image_url,
                "display_markdown": markdown,
                "display_instruction": instruction,
                "revised_prompt": revised_prompt,
            }
        else:
            message = "图片已生成，可通过返回的链接查看"
            instruction = (
                "请在回复中附上 display_markdown 的原样内容，确保 "
                "{MASTER_NAME} 能直接看到并打开生成图片；再用角色口吻简短说明"
                "已经画好。"
            )
            result_fields = {
                "message": message,
                "image_url": image_url,
                "display_markdown": markdown,
                "display_instruction": instruction,
                "revised_prompt": revised_prompt,
            }

        if thumb_url:
            result_fields["preview_url"] = thumb_url
        await self._record_history(
            prompt=cleaned_prompt,
            model=str(settings["model"]),
            status="succeeded",
            result_url=image_url,
            api_key=api_key,
            history_limit=int(settings["history_limit"]),
        )
        self._set_request_state(action=action, status="success")
        self._report_generation_completion({"status": "running"})
        self.logger.info(
            "ImageGenerator request succeeded: action={} bytes={} "
            "chat_push_attempted={}",
            action,
            len(image_bytes),
            push_attempted,
        )
        return Ok(_redact_structure(result_fields, self._known_secrets_snapshot(api_key)))

    # ------------------------------------------------------------------
    # Primary capability — TWO registrations, one per caller, deliberately.
    #
    # The host has two independent dispatch paths and a plugin must register
    # for BOTH or "draw X" silently does nothing on one of them:
    #
    #   1. Dialog LLM direct tool call -> @llm_tool. Canonical path: the
    #      dialog model gets the full JSON schema, passes typed args, runs
    #      synchronously. api_runtime.py strips ``__llm_tool__*`` entries from
    #      the TaskExecutor's view, so this registration is invisible to the
    #      automatic router.
    #
    #   2. Agent TaskExecutor routing -> @plugin_entry. The router LLM picks an
    #      entry_id from the plugin's *agent-visible* entries. Because (1) is
    #      stripped from that view, an llm_tool-only registration is
    #      unreachable here — the router's validator rejects
    #      "entry_id 'generate_image' does not exist" and the request dies.
    #
    # Both entries below share ``_generate`` and its in-flight dedup lock, so
    # if the two paths ever fire for the same request the second call is
    # collapsed into the first instead of double-billing. Earlier I dropped
    # the plugin_entry to fix a double-render; the real fix for that was the
    # push/display_instruction change (c0aa5828), not removing the entry.
    # ------------------------------------------------------------------

    @llm_tool(
        name="generate_image",
        description=(
            "根据用户描述生成一张图片。用户说“画一张……”“生成图片”、"
            "“帮我画”“绘制插画/海报/头像”或其他明确图像创作请求时调用。"
            "prompt 必填；size、quality、style 可省略并使用管理面板默认值，"
            "提供时必须符合面板允许列表。每次用户请求只调用一次；"
            "成功后图片会自动展示在聊天中，无需重复调用。"
        ),
        parameters=GENERATE_IMAGE_SCHEMA,
        timeout=300.0,
    )
    @plugin_entry(
        id="generate_image",
        name="生成图片",
        description=(
            "根据用户描述生成一张图片并发送到聊天。用于“画一张……”“生成图片”"
            "“帮我画”等明确图像创作请求；prompt 必填，size/quality/style 可省略"
            "并用面板默认值。"
        ),
        input_schema=GENERATE_IMAGE_SCHEMA,
        timeout=300.0,
    )
    async def generate_image(
        self,
        prompt: str,
        size: str | None = None,
        quality: str | None = None,
        style: str | None = None,
        **_: Any,
    ):
        return await self._generate(
            prompt=prompt,
            size=size,
            quality=quality,
            style=style,
            action="generate_image",
            auto_show_override=None,
        )

    # ------------------------------------------------------------------
    # Management-panel entries (not exposed as LLM tools)
    # ------------------------------------------------------------------

    @plugin_entry(
        id="get_secret_envelope",
        name="创建图片生成器密钥信封",
        description="为下一次设置保存创建短时、一次性的公钥加密信封。",
        input_schema=_EMPTY_SCHEMA,
        metadata={"agent_hidden": True},
    )
    async def get_secret_envelope(self, **_: Any):
        try:
            secret_envelope = await self._issue_secret_envelope()
        except SdkError as exc:
            return Err(exc)
        return Ok({"secret_envelope": secret_envelope})

    @plugin_entry(
        id="get_panel_state",
        name="读取图片生成器面板状态",
        description="读取安全设置、运行状态、缓存统计和最近生成记录。",
        input_schema=_EMPTY_SCHEMA,
        metadata={"agent_hidden": True},
    )
    async def get_panel_state(self, **_: Any):
        async with self._config_lock:
            settings = self._settings_snapshot()
            if bool(getattr(self.store, "enabled", False)):
                key_read_ok, raw_api_key, api_key = await self._load_api_key_checked()
            else:
                key_read_ok, raw_api_key, api_key = True, "", ""
            if not key_read_ok:
                return Err(
                    SdkError("无法安全读取 API 密钥（StoreError），请稍后刷新面板")
                )
            with self._state_lock:
                defaults = {
                    key: (list(value) if isinstance(value, list) else value)
                    for key, value in self._manifest_settings.items()
                }
        self._remember_secrets(raw_api_key, api_key)
        secrets = self._known_secrets_snapshot(raw_api_key, api_key)
        secret_warning: str | None = None
        if _settings_contain_secret(settings, secrets):
            settings = {
                key: (list(value) if isinstance(value, list) else value)
                for key, value in DEFAULT_SETTINGS.items()
            }
            secret_warning = "检测到设置中包含 API 密钥；面板已隐藏这些设置"
        history = await self._load_history(api_key=api_key)
        cache = await self._cache_stats()
        with self._state_lock:
            running = self._running
            api_state = self._api_state
            last_request = dict(self._last_request)
            configuration_warning = secret_warning or self._configuration_warning
        if _settings_contain_secret(defaults, secrets):
            defaults = {
                key: (list(value) if isinstance(value, list) else value)
                for key, value in DEFAULT_SETTINGS.items()
            }
            configuration_warning = (
                secret_warning or "检测到默认设置中包含 API 密钥；面板已隐藏这些设置"
            )
        cache.update(
            {
                "max_count": settings["cache_max_count"],
                "max_bytes": settings["cache_max_bytes"],
            }
        )
        payload = {
            "running": running,
            "api_state": api_state,
            "configuration_warning": configuration_warning,
            "store_enabled": bool(getattr(self.store, "enabled", False)),
            "asset_cache_available": self._asset_dir is not None,
            "settings_available": self._settings_available,
            "api_key_configured": bool(api_key),
            "settings": settings,
            "defaults": defaults,
            "history": history[:20],
            "cache": cache,
            "last_request": last_request,
        }
        return Ok(_redact_structure(payload, secrets))

    async def _sanitize_history_before_secret_change(
        self,
        *,
        secrets: tuple[str, ...],
        history_limit: int,
    ) -> bool:
        read_ok, raw_history = await self._store_get_checked(
            _HISTORY_STORE_KEY,
            [],
        )
        if not read_ok:
            return False
        if not isinstance(raw_history, list):
            return await self._store_set(_HISTORY_STORE_KEY, [])
        sanitized: list[dict[str, Any]] = []
        for item in raw_history:
            record = _safe_history_record(item, secrets)
            if record is not None:
                sanitized.append(record)
        return await self._store_set(
            _HISTORY_STORE_KEY,
            sanitized[:history_limit],
        )

    @plugin_entry(
        id="save_settings",
        name="保存图片生成器设置",
        description=(
            "消费一次性 RSA-OAEP + AES-GCM 加密载荷，原子保存设置和 API 密钥。"
        ),
        input_schema=_SAVE_SETTINGS_SCHEMA,
        metadata={"agent_hidden": True},
    )
    async def save_settings(
        self,
        encrypted_payload: str = "",
        key_id: str = "",
        **extra: Any,
    ):
        if not self._manifest_available:
            return Err(SdkError("无法安全读取插件配置，请重新启动插件后重试"))
        unexpected = sorted(key for key in extra if key != "_ctx")
        if unexpected:
            return Err(SdkError("保存设置仅接受一次性加密载荷，请刷新管理面板"))
        if not bool(getattr(self.store, "enabled", False)):
            return Err(SdkError("插件存储已禁用，无法保存设置或 API 密钥"))
        try:
            document = await self._consume_encrypted_settings(
                encrypted_payload=encrypted_payload,
                key_id=key_id,
            )
        except SdkError as exc:
            return Err(exc)

        expected_fields = set(DEFAULT_SETTINGS) | {"api_key", "clear_api_key"}
        if set(document) != expected_fields:
            return Err(SdkError("加密设置文档字段不完整或包含未知字段"))
        raw_new_key = document.get("api_key")
        clear_api_key = document.get("clear_api_key")
        if not isinstance(raw_new_key, str):
            return Err(SdkError("加密设置文档中的 API 密钥格式无效"))
        if not isinstance(clear_api_key, bool):
            return Err(SdkError("加密设置文档中的清除密钥开关无效"))
        if clear_api_key and raw_new_key.strip():
            return Err(SdkError("不能同时提供新 API 密钥并清除密钥"))
        raw_settings = {key: document[key] for key in DEFAULT_SETTINGS}

        key_changed = False
        async with self._config_lock:
            with self._state_lock:
                old_runtime_settings = {
                    key: (list(value) if isinstance(value, list) else value)
                    for key, value in self._settings.items()
                }
                old_configuration_warning = self._configuration_warning
            settings_read_ok, old_settings = await self._store_get_checked(
                _SETTINGS_STORE_KEY,
                None,
            )
            key_read_ok, raw_old_key = await self._store_get_checked(
                _API_KEY_STORE_KEY,
                "",
            )
            if not settings_read_ok or not key_read_ok:
                return Err(
                    SdkError("无法在保存前安全读取当前设置或 API 密钥（StoreError）")
                )
            old_key = raw_old_key if isinstance(raw_old_key, str) else ""
            candidate_new_key = raw_new_key.strip()
            self._remember_secrets(
                old_key,
                candidate_new_key if len(candidate_new_key) >= 8 else "",
            )
            secrets = self._known_secrets_snapshot(
                old_key,
                candidate_new_key,
            )
            if _value_contains_secret(
                [raw_settings, self._manifest_settings],
                secrets,
            ):
                return Err(
                    SdkError("API 密钥不能出现在 API 地址、模型、默认值或允许列表中")
                )
            try:
                validated = _validate_settings(
                    raw_settings,
                    base=self._manifest_settings,
                    require_all=True,
                )
                if self._cache_limits_decrease(validated):
                    return Err(SdkError("图片正在生成，请完成后再降低缓存或下载限额"))
                new_api_key = (
                    ""
                    if clear_api_key
                    else (
                        _validate_api_key(candidate_new_key)
                        if candidate_new_key
                        else ""
                    )
                )
            except SdkError as exc:
                return Err(exc)
            try:
                validated_old_key = _validate_api_key(old_key) if old_key else ""
            except SdkError:
                validated_old_key = ""

            effective_api_key = (
                "" if clear_api_key else (new_api_key or validated_old_key)
            )
            if (validated_old_key and not new_api_key and not clear_api_key
                    and (not self._settings_available or _origin_tuple(validated["api_base_url"])
                         != _origin_tuple(old_runtime_settings["api_base_url"]))):
                return Err(SdkError("切换服务地址时请提供新 API 密钥或明确清除原密钥"))
            await self._acquire_lock(self._history_lock)
            try:
                # Snapshot the stored history BEFORE any mutation so a
                # mid-transaction Store failure can roll it back: the
                # sanitize below rewrites and truncates with the *proposed*
                # limit, and restore_previous_configuration() only covers
                # settings and the credential.
                history_snapshot_ok, old_history = await self._store_get_checked(
                    _HISTORY_STORE_KEY,
                    None,
                )
                if not history_snapshot_ok:
                    return Err(
                        SdkError("无法在保存前安全读取生成历史（StoreError）")
                    )
                async def restore_history() -> bool:
                    if old_history is None:
                        restored, _ = await self._store_delete(_HISTORY_STORE_KEY)
                        return restored
                    return await self._store_set(_HISTORY_STORE_KEY, old_history)

                async def restore_previous_configuration() -> tuple[bool, bool]:
                    history_restored = await restore_history()
                    key_removed, _ = await self._store_delete(_API_KEY_STORE_KEY)
                    if not key_removed:
                        return False, False
                    if old_settings is None:
                        settings_restored, _ = await self._store_delete(
                            _SETTINGS_STORE_KEY
                        )
                    else:
                        settings_restored = await self._store_set(
                            _SETTINGS_STORE_KEY,
                            old_settings,
                        )
                    if not settings_restored:
                        return False, False
                    # Roll the history back alongside settings and the
                    # credential: the sanitize above already persisted a
                    # rewritten, possibly truncated history using the proposed
                    # limit, so without this the save reports failure while
                    # older generation records are permanently lost.
                    if not history_restored:
                        self.logger.warning("ImageGenerator history rollback failed: failure_class=StoreError")
                    # The rollback credential may become durable in a worker
                    # thread just as this task is cancelled. Publish the old
                    # runtime settings before awaiting that write so the old
                    # key can never be observed with the new endpoint.
                    with self._state_lock:
                        self._settings = old_runtime_settings
                        self._configuration_warning = old_configuration_warning
                    if old_key:
                        key_restored = await self._store_set(
                            _API_KEY_STORE_KEY,
                            old_key,
                        )
                    else:
                        key_restored = True
                    return settings_restored and history_restored, key_restored

                async def commit_configuration():
                    nonlocal key_changed
                    history_safe = await self._sanitize_history_before_secret_change(
                        secrets=secrets,
                        history_limit=int(validated["history_limit"]),
                    )
                    if not history_safe:
                        await restore_history()
                        return Err(
                            SdkError("无法在更新密钥前安全清理历史记录（StoreError）")
                        )

                    # Fail closed across process loss: remove the authoritative key
                    # before changing settings, then restore/write it only after the
                    # settings commit. Every crash-visible intermediate state has no
                    # usable credential instead of a key paired with the wrong URL.
                    key_staged, key_existed = await self._store_delete(
                        _API_KEY_STORE_KEY
                    )
                    if not key_staged:
                        await restore_history()
                        return Err(
                            SdkError(
                                "无法在保存前安全暂存 API 密钥（StoreError），"
                                "请稍后重试"
                            )
                        )

                    if not await self._store_set(_SETTINGS_STORE_KEY, validated):
                        settings_restored, key_restored = (
                            await restore_previous_configuration()
                        )
                        if not settings_restored or not key_restored:
                            self.logger.warning(
                                "ImageGenerator settings rollback incomplete: "
                                "failure_class=StoreError"
                            )
                        return Err(SdkError("保存设置失败（StoreError），请稍后重试"))

                    # Publish the matching settings before the credential write.
                    # PluginStore uses a worker thread, so task cancellation can
                    # arrive after a durable key commit. At every await boundary,
                    # runtime settings must therefore already match any new key
                    # that may have reached the Store.
                    with self._state_lock:
                        self._settings = validated
                        self._configuration_warning = (
                            None
                            if self._asset_dir is not None
                            else "生成图片缓存不可用；管理面板可能可读，但生成已降级"
                        )

                    if effective_api_key and not await self._store_set(
                        _API_KEY_STORE_KEY,
                        effective_api_key,
                    ):
                        settings_restored, key_restored = (
                            await restore_previous_configuration()
                        )
                        if not settings_restored or not key_restored:
                            self.logger.warning(
                                "ImageGenerator settings rollback incomplete: "
                                "failure_class=StoreError"
                            )
                        return Err(SdkError("保存 API 密钥失败（StoreError）"))

                    key_changed = (
                        key_existed
                        if clear_api_key
                        else effective_api_key != validated_old_key
                    )

                async def guarded_commit():
                    try:
                        return await commit_configuration()
                    except BaseException as exc:
                        # Propagate on the owner task, including process-loss
                        # signals, rather than terminating the event loop here.
                        return exc

                worker = asyncio.create_task(guarded_commit())
                try:
                    outcome = await asyncio.shield(worker)
                    if isinstance(outcome, BaseException):
                        raise outcome
                except asyncio.CancelledError:
                    # Store operations use worker threads. First drain the
                    # transaction, then roll back while both locks remain held.
                    while not worker.done():
                        try:
                            await asyncio.shield(worker)
                        except asyncio.CancelledError:
                            continue
                    if not worker.cancelled():
                        worker.exception()
                    rollback = asyncio.create_task(restore_previous_configuration())
                    while not rollback.done():
                        try:
                            await asyncio.shield(rollback)
                        except asyncio.CancelledError:
                            continue
                    if rollback.cancelled() or not all(rollback.result()):
                        self._settings_available = False
                    raise
                if isinstance(outcome, Err):
                    return outcome
                self._settings_available = True
            finally:
                self._history_lock.release()

        try:
            await self._prune_cache()
        except Exception as exc:
            self.logger.warning(
                "ImageGenerator cache prune after save failed: failure_class={}",
                type(exc).__name__,
            )
        key_configured = bool(effective_api_key)
        self.logger.info(
            "ImageGenerator settings saved: output_format={} "
            "response_format={} key_changed={} key_configured={} auto_show={}",
            validated["output_format"],
            validated["response_format"],
            key_changed,
            key_configured,
            validated["auto_show_in_chat"],
        )
        return Ok(
            _redact_structure(
                {
                    "saved": True,
                    "settings": self._settings_snapshot(),
                    "api_key_configured": key_configured,
                },
                self._known_secrets_snapshot(effective_api_key),
            )
        )

    @plugin_entry(
        id="reset_settings",
        name="恢复图片生成器默认设置",
        description="恢复 plugin.toml 中的非秘密默认设置；不会清除 API 密钥。",
        input_schema=_EMPTY_SCHEMA,
        metadata={"agent_hidden": True},
    )
    async def reset_settings(self, **_: Any):
        if not self._manifest_available:
            return Err(SdkError("无法安全读取插件配置，请重新启动插件后重试"))
        if not bool(getattr(self.store, "enabled", False)):
            return Err(SdkError("插件存储已禁用，无法恢复默认设置"))
        async with self._config_lock:
            key_read_ok, raw_api_key = await self._store_get_checked(
                _API_KEY_STORE_KEY,
                "",
            )
            if not key_read_ok:
                return Err(SdkError("无法在恢复设置前安全读取 API 密钥（StoreError）"))
            api_key = raw_api_key if isinstance(raw_api_key, str) else ""
            self._remember_secrets(api_key)
            secrets = self._known_secrets_snapshot(api_key)
            target_settings = {
                key: (list(value) if isinstance(value, list) else value)
                for key, value in self._manifest_settings.items()
            }
            if self._cache_limits_decrease(target_settings):
                return Err(SdkError("图片正在生成，请完成后再降低缓存或下载限额"))
            if (api_key and (not self._settings_available or _origin_tuple(target_settings["api_base_url"])
                    != _origin_tuple(self._settings_snapshot()["api_base_url"]))):
                return Err(SdkError("恢复默认设置会切换服务地址，请先清除 API 密钥"))
            if _settings_contain_secret(target_settings, secrets):
                return Err(
                    SdkError(
                        "默认设置包含当前或历史 API 密钥，已拒绝恢复；"
                        "请检查 plugin.toml"
                    )
                )
            async def commit_reset():
                await self._acquire_lock(self._history_lock)
                try:
                    read_ok, old_history = await self._store_get_checked(
                        _HISTORY_STORE_KEY, None
                    )
                    if not read_ok:
                        return False
                    history_safe = await self._sanitize_history_before_secret_change(
                        secrets=secrets,
                        history_limit=int(target_settings["history_limit"]),
                    )
                    if not history_safe:
                        return False
                    deleted_ok, _existed = await self._store_delete(_SETTINGS_STORE_KEY)
                    if not deleted_ok:
                        if old_history is None:
                            await self._store_delete(_HISTORY_STORE_KEY)
                        else:
                            await self._store_set(_HISTORY_STORE_KEY, old_history)
                        return False
                    with self._state_lock:
                        self._settings = target_settings
                        self._settings_available = True
                        self._configuration_warning = (
                            None
                            if self._asset_dir is not None
                            else "生成图片缓存不可用；管理面板可能可读，但生成已降级"
                        )
                    return True
                finally:
                    self._history_lock.release()

            if not await self._drain_on_cancel(commit_reset()):
                return Err(SdkError("恢复默认设置失败（StoreError）"))
            try:
                key_configured = bool(_validate_api_key(api_key)) if api_key else False
            except SdkError:
                key_configured = False
        try:
            await self._prune_cache()
        except Exception as exc:
            self.logger.warning(
                "ImageGenerator cache prune after reset failed: failure_class={}",
                type(exc).__name__,
            )
        return Ok(
            _redact_structure(
                {
                    "reset": True,
                    "settings": self._settings_snapshot(),
                    "api_key_configured": key_configured,
                },
                secrets,
            )
        )

    @plugin_entry(
        id="clear_api_key",
        name="清除图片生成 API 密钥",
        description="显式删除 PluginStore 中保存的 API 密钥。",
        input_schema=_EMPTY_SCHEMA,
        metadata={"agent_hidden": True},
    )
    async def clear_api_key(self, **_: Any):
        if not bool(getattr(self.store, "enabled", False)):
            return Err(SdkError("插件存储已禁用，无法清除 API 密钥"))
        async with self._config_lock:
            key_read_ok, raw_api_key = await self._store_get_checked(
                _API_KEY_STORE_KEY,
                "",
            )
            settings_read_ok, stored_settings = await self._store_get_checked(
                _SETTINGS_STORE_KEY,
                None,
            )
            if not key_read_ok or not settings_read_ok:
                return Err(
                    SdkError("无法在清除前安全读取当前设置或 API 密钥（StoreError）")
                )
            api_key = raw_api_key if isinstance(raw_api_key, str) else ""
            current_settings = self._settings_snapshot()
            self._remember_secrets(api_key)
            secrets = self._known_secrets_snapshot(api_key)
            if _value_contains_secret(
                [
                    stored_settings,
                    current_settings,
                    self._manifest_settings,
                ],
                secrets,
            ):
                return Err(
                    SdkError(
                        "设置中仍包含 API 密钥；请先通过加密面板保存安全设置，"
                        "再清除密钥"
                    )
                )
            await self._acquire_lock(self._history_lock)
            try:
                history_safe = await self._drain_on_cancel(self._sanitize_history_before_secret_change(
                    secrets=secrets,
                    history_limit=int(current_settings["history_limit"]),
                ))
            finally:
                self._history_lock.release()
            if not history_safe:
                return Err(SdkError("无法在清除密钥前安全清理历史记录（StoreError）"))
            deleted_ok, existed = await self._drain_on_cancel(
                self._store_delete(_API_KEY_STORE_KEY)
            )
        if not deleted_ok:
            return Err(SdkError("清除 API 密钥失败（StoreError）"))
        self.logger.info("ImageGenerator API key cleared: existed={}", existed)
        return Ok(
            {
                "cleared": existed,
                "api_key_configured": False,
            }
        )

    @plugin_entry(
        id="get_recent_history",
        name="读取最近图片生成记录",
        description="读取不含密钥和 Base64 图片的有界最近生成记录。",
        input_schema=_RECENT_HISTORY_SCHEMA,
        metadata={"agent_hidden": True},
    )
    async def get_recent_history(self, limit: int = 20, **_: Any):
        try:
            resolved_limit = _bounded_int(
                limit,
                label="历史记录数量",
                minimum=1,
                maximum=100,
            )
        except SdkError as exc:
            return Err(exc)
        async with self._config_lock:
            if bool(getattr(self.store, "enabled", False)):
                key_read_ok, raw_api_key, api_key = await self._load_api_key_checked()
                if not key_read_ok:
                    return Err(
                        SdkError("无法安全读取 API 密钥（StoreError），请稍后重试")
                    )
            else:
                raw_api_key = api_key = ""
            self._remember_secrets(raw_api_key, api_key)
        history = (await self._load_history(api_key=api_key))[:resolved_limit]
        return Ok(
            _redact_structure(
                {"history": history, "count": len(history)},
                self._known_secrets_snapshot(api_key),
            )
        )

    @staticmethod
    async def _drain_on_cancel(operation):
        """Finish a durable mutation before its caller releases the owning lock."""
        worker = asyncio.ensure_future(operation)
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            while not worker.done():
                try:
                    await asyncio.shield(worker)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if not worker.cancelled():
                worker.exception()
            raise

    @plugin_entry(
        id="clear_history",
        name="清除图片生成历史",
        description="清除最近生成记录；不会清除 API 密钥或已生成文件缓存。",
        input_schema=_EMPTY_SCHEMA,
        metadata={"agent_hidden": True},
    )
    async def clear_history(self, **_: Any):
        if not bool(getattr(self.store, "enabled", False)):
            return Err(SdkError("插件存储已禁用，无法清除历史记录"))
        await self._acquire_lock(self._history_lock)
        try:
            deleted_ok, existed = await self._drain_on_cancel(
                self._store_delete(_HISTORY_STORE_KEY)
            )
        finally:
            self._history_lock.release()
        if not deleted_ok:
            return Err(SdkError("清除历史记录失败（StoreError）"))
        return Ok({"cleared": True, "had_history": existed, "count": 0})

    @plugin_entry(
        id="test_generation",
        name="测试生成图片",
        description="使用当前配置立即生成一张测试图片；可能产生提供商费用。",
        input_schema=_TEST_GENERATION_SCHEMA,
        timeout=300.0,
        # This entry exists ONLY for the management panel's "测试生成" button,
        # which calls it directly via /runs. It must never be picked by the
        # Agent's automatic task router — otherwise a user's "draw X" request
        # gets dispatched here (auto_show_override=False) and the image is
        # written to history without ever being pushed into the chat, which
        # looks like "猫娘画了但没发出来". Hide it from agent routing; the
        # panel's direct /runs call is unaffected.
        metadata={"agent_hidden": True},
    )
    async def test_generation(self, prompt: str, **_: Any):
        return await self._generate(
            prompt=prompt,
            action="test_generation",
            auto_show_override=False,
        )


__all__ = [
    "DEFAULT_SETTINGS",
    "GENERATE_IMAGE_SCHEMA",
    "ImageGeneratorPlugin",
    "PLUGIN_VERSION",
    "USER_AGENT",
    "_decode_b64_image",
    "_image_type",
    "_new_http_client",
    "_normalize_api_base_url",
    "_sanitize_image",
    "_validate_settings",
]
