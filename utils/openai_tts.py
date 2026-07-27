# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Protocol helpers for OpenAI-compatible ``/v1/audio/speech`` endpoints.

This module deliberately covers only the HTTP(S) Speech API contract shared by
the runtime worker and the connectivity probe. WS(S) duplex providers have
different request/stream semantics and remain in their provider-specific
workers; accepting a WebSocket URL here would blur that routing boundary.

Keep vendor-specific fields out of the common payload. A compatible provider
should receive the standard OpenAI body unless it is explicitly recognized by
``openai_tts_extra_body``.
"""

# 本模块只负责 HTTP(S) Speech API；WS(S) 双向流继续由各 provider 的专用
# worker 处理。供应商专属字段也必须与标准 OpenAI 请求体隔离。

from __future__ import annotations

from urllib.parse import urlparse, urlunparse


OPENAI_TTS_DEFAULT_BASE_URL = "https://api.openai.com/v1"
OPENAI_TTS_DEFAULT_MODEL = "gpt-4o-mini-tts"
OPENAI_TTS_DEFAULT_VOICE = "marin"
# Speech API 返回的原始 PCM 会进入项目的 24 kHz -> 48 kHz 流式重采样器。
# 对于支持配置采样率的供应商，必须固定相同的源采样率，否则播放速度和音调会出错。
OPENAI_TTS_PCM_SAMPLE_RATE = 24000

# 使用精确域名匹配，避免把硅基流动专属字段发送给域名中仅碰巧包含相同文本的
# 其他 OpenAI-compatible 服务（例如用户可控的子域名）。
_SILICONFLOW_TTS_HOSTS = frozenset({
    "api.siliconflow.cn",
    "api.siliconflow.com",
})


class OpenAITtsConfigError(ValueError):
    """Raised when an OpenAI-compatible TTS configuration is incomplete."""


def openai_tts_base_url(base_url: str) -> str:
    """Normalize a configured URL into an OpenAI-compatible API base URL.

    Accepts a server root, a versioned base URL, or the already-complete speech
    endpoint. Only HTTP(S) is valid: WebSocket TTS providers use their own
    protocol-specific workers.
    """

    raw = str(base_url or "").strip().rstrip("/")
    if not raw:
        raise OpenAITtsConfigError("缺少 OpenAI-compatible TTS URL")

    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise OpenAITtsConfigError("OpenAI-compatible TTS URL 必须使用 http:// 或 https://")

    path = (parsed.path or "").rstrip("/")
    if not path:
        # 只填写服务 origin 时，按照 OpenAI 的常规 API 结构补上 /v1。
        path = "/v1"
    if path.endswith("/audio/speech"):
        # AsyncOpenAI 需要的是 API Base URL，而不是具体资源 endpoint。
        # 配置页允许两种形式，因此只移除已知后缀，保留供应商在它之前的路径前缀。
        path = path[:-len("/audio/speech")].rstrip("/") or "/v1"

    # 使用解析后的 URL 组件重建，避免直接拼接原始字符串导致 query 跑到路径中间。
    # 某些带签名或代理的 endpoint 依赖 query 始终位于完整路径之后。
    return urlunparse(
        (parsed.scheme, parsed.netloc, path, parsed.params, parsed.query, parsed.fragment)
    )


def openai_tts_speech_url(base_url: str) -> str:
    """Return the full OpenAI-compatible speech endpoint."""

    parsed = urlparse(openai_tts_base_url(base_url))
    path = f"{parsed.path.rstrip('/')}/audio/speech"
    # 连通性探测会直接请求这个完整 URL。按组件组装可以避免生成
    # ``...?token=x/audio`` 之类 query 与路径顺序错误的地址。
    return urlunparse(
        (parsed.scheme, parsed.netloc, path, parsed.params, parsed.query, parsed.fragment)
    )


def openai_tts_extra_body(base_url: str) -> dict[str, int | bool]:
    """Return vendor extensions needed to preserve the PCM wire contract.

    OpenAI-compatible PCM is 24 kHz. SiliconFlow exposes a configurable sample
    rate and does not guarantee that its default matches that contract, so pin
    it explicitly while leaving strict OpenAI-compatible providers untouched.
    """

    parsed = urlparse(str(base_url or "").strip())
    if (parsed.hostname or "").lower() in _SILICONFLOW_TTS_HOSTS:
        # 硅基流动通过 OpenAI-compatible HTTP endpoint 返回流式 PCM，但将采样率
        # 与流式开关作为扩展字段。这里显式固定二者，使运行时播放和连通性探测
        # 使用相同的传输格式；这些字段不会发送给其他通用兼容供应商。
        return {"sample_rate": OPENAI_TTS_PCM_SAMPLE_RATE, "stream": True}
    return {}


def build_openai_tts_payload(text: str, model: str, voice: str) -> dict[str, str]:
    """Build the strict OpenAI speech request used by runtime and probes.

    Vendor extensions intentionally live in ``openai_tts_extra_body`` so the
    baseline request remains portable across OpenAI-compatible operators.
    """

    effective_model = str(model or "").strip()
    effective_voice = str(voice or "").strip()
    if not effective_model:
        raise OpenAITtsConfigError("缺少 OpenAI-compatible TTS Model ID")
    if not effective_voice:
        raise OpenAITtsConfigError("缺少 OpenAI-compatible TTS Voice ID")
    return {
        "model": effective_model,
        "input": str(text or ""),
        "voice": effective_voice,
        "response_format": "pcm",
    }


def openai_tts_headers(api_key: str) -> dict[str, str]:
    """Return OpenAI-compatible request headers, allowing auth-free local APIs.

    Do not invent an Authorization header when the key is empty: self-hosted
    compatible endpoints commonly disable authentication, and the HTTP probe
    should mirror the user's actual configuration.
    """

    headers = {"Content-Type": "application/json", "Accept": "audio/*"}
    key = str(api_key or "").strip()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers
