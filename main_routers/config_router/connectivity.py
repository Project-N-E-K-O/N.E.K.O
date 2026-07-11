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

"""Provider connectivity test endpoint (/test_connectivity) and
per-provider probe helpers.

Split out of the former monolithic ``main_routers/config_router.py``.
"""

from ._shared import logger, router

import asyncio
import ssl
import urllib.parse
from typing import Any, Optional
from pydantic import BaseModel


_MIMO_TOKEN_PLAN_HOSTS = {
    "token-plan-cn.xiaomimimo.com",
    "token-plan-sgp.xiaomimimo.com",
    "token-plan-ams.xiaomimimo.com",
}


# ---------------------------------------------------------------------------
# Connectivity Test Models & Endpoint
# ---------------------------------------------------------------------------

class ConnectivityTestRequest(BaseModel):
    """Request model for connectivity testing.

    Two modes:
    1. Built-in provider: provide provider_key + provider_scope + api_key.
       Backend resolves url/model/provider_type from api_providers.json.
    2. Custom API: provide url + api_key + model (+ provider_type).
       Frontend passes all details directly.
    """
    # Built-in provider mode
    provider_key: Optional[str] = None       # e.g. "qwen", "openai", "glm"
    provider_scope: Optional[str] = None     # "core" or "assist"
    # Custom / fallback mode
    url: Optional[str] = ""
    api_key: Optional[str] = ""
    model: Optional[str] = ""
    provider_type: Optional[str] = "openai_compatible"
    # Currently the only recognized value is 'vllm_omni_tts'; vLLM-Omni's WebSocket
    # speech endpoint does NOT speak the OpenAI Realtime protocol (no session.update),
    # so it requires a handshake-only probe instead of _test_websocket. (#1764 review 第六轮)
    sub_type: Optional[str] = ""
    voice_id: Optional[str] = ""
    is_free: Optional[bool] = False


class ConnectivityTestResponse(BaseModel):
    success: bool
    error: Optional[str] = None
    error_code: Optional[str] = None
    resolved_url: Optional[str] = None


async def _test_openai_compatible(url: str, api_key: str, model: str = "gpt-3.5-turbo", is_free: bool = False) -> dict:
    """Test an OpenAI-compatible REST API endpoint.

    Uses the project's ChatOpenAI client (same as actual conversations) to send
    a minimal chat completion request (bounded by CONNECTIVITY_TEST_MAX_TOKENS).
    This ensures the test exercises the exact same auth and request path as
    real usage.

    Args:
        url: Base URL for the API endpoint.
        api_key: API key for authentication (optional for keyless services).
        model: Model name to use for the test request. For built-in providers,
               this comes from api_providers.json (e.g. "qwen3.6-plus", "glm-4.5-air").
               For custom APIs, this comes from the frontend.
        is_free: If True, 400 Bad Request is treated as success.

    Future: TTS/GPT-SoVITS connectivity testing is not yet supported here;
    those use different protocols and will need dedicated test paths.
    """
    from utils.llm_client import ChatOpenAI as _ChatOpenAI
    from config import CONNECTIVITY_TEST_MAX_TOKENS

    try:
        client = _ChatOpenAI(
            model=model,
            base_url=url,
            api_key=api_key or "sk-placeholder",
            max_completion_tokens=CONNECTIVITY_TEST_MAX_TOKENS,
            timeout=10.0,
            max_retries=0,
        )
        try:
            await client.ainvoke([{"role": "user", "content": "hi"}])
            return {"success": True}
        finally:
            await client.aclose()

    except Exception as e:
        return _classify_openai_error(e, is_free=is_free)


def _classify_openai_error(e: Exception, is_free: bool = False) -> dict:
    """Classify an OpenAI client exception into a connectivity test result."""
    from openai import AuthenticationError, APITimeoutError, APIConnectionError, APIStatusError, RateLimitError

    # Auth errors (401, 403)
    if isinstance(e, AuthenticationError):
        return {"success": False, "error": "API Key无效或已过期", "error_code": "auth_failed"}

    # Rate limit (429) — key is valid but temporarily throttled, treat as success
    if isinstance(e, RateLimitError):
        return {"success": True}

    # Timeout
    if isinstance(e, (APITimeoutError, TimeoutError, asyncio.TimeoutError)):
        return {"success": False, "error": "请求超时（10秒）", "error_code": "timeout"}

    # Connection errors (DNS, refused, etc.)
    if isinstance(e, APIConnectionError):
        err_str = str(e).lower()
        if "getaddrinfo" in err_str or "name or service not known" in err_str or "nodename nor servname" in err_str:
            return {"success": False, "error": "域名解析失败", "error_code": "dns_error"}
        if "connection refused" in err_str or "connect call failed" in err_str:
            return {"success": False, "error": "无法连接到目标服务器", "error_code": "connection_refused"}
        return {"success": False, "error": f"连接失败: {e}", "error_code": "connection_refused"}

    # SSL errors
    if isinstance(e, ssl.SSLError):
        return {"success": False, "error": "SSL证书验证失败", "error_code": "ssl_error"}

    # HTTP status errors (400, 500, etc.)
    if isinstance(e, APIStatusError):
        status = e.status_code
        if status in (401, 403):
            return {"success": False, "error": "API Key无效或已过期", "error_code": "auth_failed"}
        # 免费版 API：400 = 服务可达，Key 未被拒绝
        if is_free and status == 400:
            return {"success": True}
        return {"success": False, "error": f"HTTP {status}", "error_code": "unknown"}

    # Fallback
    return {"success": False, "error": str(e), "error_code": "unknown"}


async def _test_anthropic(url: str, api_key: str, model: str = "", is_free: bool = False) -> dict:
    """Test an Anthropic Messages API endpoint (Kimi Code / Anthropic)."""
    from utils.llm_client import ChatAnthropic as _ChatAnthropic, _is_kimi_code_anthropic_base_url
    from config import CONNECTIVITY_TEST_MAX_TOKENS

    try:
        test_model = str(model or "").strip()
        if not test_model:
            if _is_kimi_code_anthropic_base_url(url):
                test_model = "kimi-for-coding"
            else:
                return {"success": False, "error": "缺少模型 ID", "error_code": "missing_params"}
        default_headers = (
            {"User-Agent": "claude-code/0.1.0"}
            if _is_kimi_code_anthropic_base_url(url)
            else None
        )
        client = _ChatAnthropic(
            model=test_model,
            base_url=url,
            api_key=api_key or "sk-placeholder",
            max_tokens=CONNECTIVITY_TEST_MAX_TOKENS,
            timeout=10.0,
            max_retries=0,
            default_headers=default_headers,
        )
        try:
            await client.ainvoke([{"role": "user", "content": "hi"}])
            return {"success": True}
        finally:
            await client.aclose()
    except Exception as e:
        return _classify_anthropic_error(e, is_free=is_free)


def _classify_anthropic_error(e: Exception, is_free: bool = False) -> dict:
    """Classify an Anthropic SDK exception into a connectivity test result."""
    try:
        from anthropic import AuthenticationError, APITimeoutError, APIConnectionError, APIStatusError, RateLimitError
    except Exception:
        return {"success": False, "error": str(e), "error_code": "unknown"}

    if isinstance(e, AuthenticationError):
        return {"success": False, "error": "API Key无效或已过期", "error_code": "auth_failed"}
    if isinstance(e, RateLimitError):
        return {"success": True}
    if isinstance(e, (APITimeoutError, TimeoutError, asyncio.TimeoutError)):
        return {"success": False, "error": "请求超时（10秒）", "error_code": "timeout"}
    if isinstance(e, APIConnectionError):
        err_str = str(e).lower()
        if "getaddrinfo" in err_str or "name or service" in err_str or "nodename" in err_str:
            return {"success": False, "error": "域名解析失败", "error_code": "dns_error"}
        if "connection refused" in err_str or "connect call failed" in err_str:
            return {"success": False, "error": "无法连接到目标服务器", "error_code": "connection_refused"}
        return {"success": False, "error": f"连接失败: {e}", "error_code": "connection_refused"}
    if isinstance(e, ssl.SSLError):
        return {"success": False, "error": "SSL证书验证失败", "error_code": "ssl_error"}
    if isinstance(e, APIStatusError):
        status = e.status_code
        if status in (401, 403):
            return {"success": False, "error": "API Key无效或已过期", "error_code": "auth_failed"}
        if is_free and status == 400:
            return {"success": True}
        return {"success": False, "error": f"HTTP {status}", "error_code": "unknown"}
    return {"success": False, "error": str(e), "error_code": "unknown"}


async def _test_websocket(url: str, api_key: str, model: str = "") -> dict:
    """Test a WebSocket endpoint by performing a handshake AND a minimal session.update.

    Mirrors the project's OmniRealtimeClient.connect() behavior:
    - Appends ?model={model} to the URL (same as omni_realtime_client.py)
    - Sends Authorization header with Bearer token
    - After handshake, sends a minimal session.update and waits for server response
    - If server responds with any non-error event → key is valid and model is accessible
    - If server responds with error or closes connection → key/model issue

    This goes beyond a simple handshake to verify key permissions at the model level,
    ensuring "green = 100% usable, red = 100% not usable".
    """
    import websockets
    import json as _json

    try:
        # Build WebSocket URL with model parameter (same as OmniRealtimeClient.connect)
        ws_url = url.rstrip("/")
        if model and model.lower() != "free-model":
            separator = "&" if "?" in ws_url else "?"
            ws_url = f"{ws_url}{separator}model={urllib.parse.quote(model, safe='')}"

        # Authorization header only (same as OmniRealtimeClient.connect — no api_key in URL)
        if api_key:
            extra_headers = {"Authorization": f"Bearer {api_key}"}
        else:
            extra_headers = {}

        async with asyncio.timeout(10):
            async with websockets.connect(
                ws_url,
                additional_headers=extra_headers,
                open_timeout=10,
                close_timeout=5,
            ) as ws:
                # For free-model: handshake-only test is sufficient
                # (key is pre-configured "free-access", no need to verify permissions)
                if model and model.lower() == "free-model":
                    return {"success": True}

                # For paid models: send a minimal session.update to verify
                # key permissions at the model level (same as OmniRealtimeClient)
                session_update = {
                    "type": "session.update",
                    "session": {
                        "modalities": ["text"],
                        "instructions": "connectivity test",
                    }
                }
                await ws.send(_json.dumps(session_update))

                # Wait for first server response (with 5s inner timeout)
                try:
                    response = await asyncio.wait_for(ws.recv(), timeout=5.0)
                    event = _json.loads(response)
                    event_type = event.get("type", "")

                    if event_type == "error":
                        # Realtime protocol: event["error"] can be dict or string
                        raw_error = event.get("error", "")
                        if isinstance(raw_error, dict):
                            error_code = str(raw_error.get("code", "")).lower()
                            error_message = str(raw_error.get("message", ""))
                            error_msg = error_message or str(raw_error)
                        else:
                            error_code = ""
                            error_msg = str(raw_error)
                            error_message = error_msg
                        error_lower = (error_code + " " + error_message).lower()
                        if any(kw in error_lower for kw in ("401", "403", "auth", "unauthorized", "invalid api key", "invalid key", "api key", "invalid_api_key", "authentication_error")):
                            return {"success": False, "error": "API Key无效或已过期", "error_code": "auth_failed"}
                        return {"success": False, "error": f"服务端错误: {error_msg[:200]}", "error_code": "unknown"}

                    # Any non-error response (session.created, session.updated, etc.) = success
                    return {"success": True}

                except asyncio.TimeoutError:
                    # Handshake succeeded but no response to session.update within 5s
                    # This is still a partial success — service is reachable
                    return {"success": True}

    except (TimeoutError, asyncio.TimeoutError):
        return {"success": False, "error": "请求超时（10秒）", "error_code": "timeout"}
    except ssl.SSLError:
        return {"success": False, "error": "SSL证书验证失败", "error_code": "ssl_error"}
    except OSError as e:
        err_str = str(e).lower()
        if "getaddrinfo" in err_str or "name or service not known" in err_str or "nodename nor servname" in err_str:
            return {"success": False, "error": "域名解析失败", "error_code": "dns_error"}
        if "connection refused" in err_str or "connect call failed" in err_str:
            return {"success": False, "error": "无法连接到目标服务器", "error_code": "connection_refused"}
        return {"success": False, "error": f"WebSocket连接失败: {e}", "error_code": "ws_error"}
    except Exception as e:
        err_str = str(e).lower()
        # websockets library raises InvalidStatus for HTTP 401/403 during handshake
        # websockets 15.0.1: status code is at e.response.status_code, not e.status_code
        status_code = getattr(e, "status_code", None)
        if status_code is None:
            _resp = getattr(e, "response", None)
            status_code = getattr(_resp, "status_code", None)
        if status_code in (401, 403):
            return {"success": False, "error": "API Key无效或已过期", "error_code": "auth_failed"}
        return {"success": False, "error": f"WebSocket连接失败: {e}", "error_code": "ws_error"}


async def _test_vllm_omni_ws_handshake(url: str, api_key: str) -> dict:
    """vLLM-Omni TTS WebSocket handshake-only probe (no application frames).

    Background: vLLM-Omni's /v1/audio/speech/stream speaks the Qwen custom
    protocol (session.config / input.text / input.done) and does not understand
    OpenAI Realtime's session.update. Reusing _test_websocket would cause vLLM
    to drop the connection and produce a false connectivity failure. This
    function performs the WebSocket handshake and immediately closes — a
    successful handshake proves the endpoint is reachable and (empty) auth
    passes; HTTP 401/403 is mapped to auth_failed exactly like _test_websocket
    so the frontend handles them uniformly (#1764 review round 6).

    When api_key is empty no Authorization header is sent (vLLM self-hosted
    deployments commonly run without auth).
    """
    import websockets

    # 兼容旧版 websockets：<12 用 extra_headers，>=12 用 additional_headers
    # 与 vllm_omni_tts_worker._connect_and_config 保持一致 (#1764 review 第六轮+)
    ws_kwargs = {"open_timeout": 10, "close_timeout": 5}
    if api_key:
        ws_kwargs["additional_headers"] = {"Authorization": f"Bearer {api_key}"}

    try:
        async with asyncio.timeout(10):
            try:
                async with websockets.connect(url, **ws_kwargs) as ws:
                    _ = ws  # 防止未使用告警
                    return {"success": True}
            except TypeError:
                if "additional_headers" in ws_kwargs:
                    ws_kwargs["extra_headers"] = ws_kwargs.pop("additional_headers")
                async with websockets.connect(url, **ws_kwargs) as ws:
                    _ = ws
                    return {"success": True}

    except (TimeoutError, asyncio.TimeoutError):
        return {"success": False, "error": "请求超时（10秒）", "error_code": "timeout"}
    except ssl.SSLError:
        return {"success": False, "error": "SSL证书验证失败", "error_code": "ssl_error"}
    except OSError as e:
        err_str = str(e).lower()
        if "getaddrinfo" in err_str or "name or service not known" in err_str or "nodename nor servname" in err_str:
            return {"success": False, "error": "域名解析失败", "error_code": "dns_error"}
        if "connection refused" in err_str or "connect call failed" in err_str:
            return {"success": False, "error": "无法连接到目标服务器", "error_code": "connection_refused"}
        return {"success": False, "error": f"WebSocket连接失败: {e}", "error_code": "ws_error"}
    except Exception as e:
        # websockets 15.0.1: 401/403 走 e.response.status_code；与 _test_websocket 对齐。
        status_code = getattr(e, "status_code", None)
        if status_code is None:
            _resp = getattr(e, "response", None)
            status_code = getattr(_resp, "status_code", None)
        if status_code in (401, 403):
            return {"success": False, "error": "API Key无效或已过期", "error_code": "auth_failed"}
        return {"success": False, "error": f"WebSocket连接失败: {e}", "error_code": "ws_error"}


async def _test_doubao_tts_connectivity(url: str, api_key: str, model: str = "", voice_id: str = "") -> dict:
    import httpx
    from utils.doubao_tts import (
        DoubaoTtsError,
        build_doubao_tts_payload,
        doubao_api_headers,
        doubao_tts_url,
        extract_doubao_audio_bytes,
        DOUBAO_TTS_DEFAULT_BASE_URL,
        DOUBAO_TTS_DEFAULT_RESOURCE_ID,
    )

    speaker = (voice_id or "").strip()
    if not speaker:
        return {"success": False, "error": "缺少 Voice ID", "error_code": "missing_params"}

    base_url = (url or DOUBAO_TTS_DEFAULT_BASE_URL).strip()
    resource_id = (model or DOUBAO_TTS_DEFAULT_RESOURCE_ID).strip()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                doubao_tts_url(base_url),
                headers=doubao_api_headers(api_key, resource_id),
                json=build_doubao_tts_payload("测试", speaker, context_texts=()),
            )
        if resp.status_code in (401, 403):
            return {"success": False, "error": "API Key无效或已过期", "error_code": "auth_failed"}
        if resp.status_code == 429:
            return {"success": True}
        if resp.status_code != 200:
            return {"success": False, "error": f"HTTP {resp.status_code}", "error_code": "unknown"}
        if not extract_doubao_audio_bytes(resp.content):
            return {"success": False, "error": "豆包 TTS 未返回音频数据", "error_code": "empty_response"}
        return {"success": True}
    except DoubaoTtsError as exc:
        return {"success": False, "error": str(exc), "error_code": "unknown"}
    except httpx.TimeoutException:
        return {"success": False, "error": "请求超时（10秒）", "error_code": "timeout"}
    except httpx.ConnectError as exc:
        err_str = str(exc).lower()
        if "getaddrinfo" in err_str or "name or service" in err_str or "nodename" in err_str:
            return {"success": False, "error": "域名解析失败", "error_code": "dns_error"}
        return {"success": False, "error": "无法连接到目标服务器", "error_code": "connection_refused"}
    except ssl.SSLError:
        return {"success": False, "error": "SSL证书验证失败", "error_code": "ssl_error"}
    except Exception as exc:
        return {"success": False, "error": str(exc), "error_code": "unknown"}


def _normalize_provider_url_candidates(profile: dict[str, Any], primary_field: str) -> list[str]:
    """Read the provider's primary URL and candidate URLs, removing blanks and duplicates while preserving order."""
    raw_candidates: list[Any] = [profile.get(primary_field)]
    list_field = f"{primary_field}s"
    configured_candidates = profile.get(list_field)
    if isinstance(configured_candidates, list):
        raw_candidates.extend(configured_candidates)
    elif isinstance(configured_candidates, str):
        raw_candidates.append(configured_candidates)

    result: list[str] = []
    seen: set[str] = set()
    for raw_url in raw_candidates:
        url = str(raw_url or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        result.append(url)
    return result


def _looks_like_anthropic_messages_url(url: str | None) -> bool:
    if not url:
        return False
    try:
        parsed = urllib.parse.urlsplit(str(url))
    except Exception:
        return False
    host = (parsed.hostname or "").lower()
    path = parsed.path.rstrip("/").lower()
    if host == "api.anthropic.com":
        return True
    return host == "api.kimi.com" and path == "/coding"


def _normalize_provider_type(profile: dict[str, Any] | None, url: str | None = None) -> str:
    provider_type = str((profile or {}).get("provider_type") or "openai_compatible").strip().lower()
    if provider_type not in ("openai_compatible", "anthropic", "websocket", "tts"):
        return "openai_compatible"
    if provider_type == "openai_compatible" and _looks_like_anthropic_messages_url(url):
        return "anthropic"
    return provider_type


async def _test_connectivity_candidates(
    urls: list[str],
    api_key: str,
    model: str,
    provider_type: str,
    is_free: bool,
    sub_type: str = "",
    voice_id: str = "",
) -> dict:
    """Probe the candidate URLs concurrently; return the first that succeeds.

    When sub_type='vllm_omni_tts' the OpenAI Realtime session.update probe in
    _test_websocket is bypassed in favour of a lightweight handshake-and-close
    probe, because vLLM-Omni's /v1/audio/speech/stream does not understand
    Realtime protocol frames — sending session.update would trigger an early
    server-side disconnect and produce a false negative (#1764 review round 6).
    """
    if not urls:
        return {"success": False, "error": "缺少必要参数", "error_code": "missing_params"}

    async def _run_one(candidate_url: str) -> tuple[str, dict]:
        if provider_type == "websocket":
            if sub_type == "vllm_omni_tts":
                result = await _test_vllm_omni_ws_handshake(candidate_url, api_key)
            else:
                result = await _test_websocket(candidate_url, api_key, model=model)
        elif provider_type == "tts" and sub_type == "doubao_tts":
            result = await _test_doubao_tts_connectivity(
                candidate_url,
                api_key,
                model=model,
                voice_id=voice_id,
            )
        elif provider_type == "anthropic":
            result = await _test_anthropic(candidate_url, api_key, model=model, is_free=is_free)
        else:
            result = await _test_openai_compatible(candidate_url, api_key, model=model, is_free=is_free)
        return candidate_url, result

    tasks = [asyncio.create_task(_run_one(url)) for url in urls]
    results: list[tuple[str, dict]] = []
    try:
        for task in asyncio.as_completed(tasks):
            try:
                candidate_url, result = await task
            except Exception as exc:
                candidate_url = ""
                result = {"success": False, "error": str(exc), "error_code": "unknown"}
            results.append((candidate_url, result))
            if result.get("success"):
                for pending in tasks:
                    if not pending.done():
                        pending.cancel()
                resolved = dict(result)
                resolved["resolved_url"] = candidate_url
                return resolved
    finally:
        await asyncio.gather(*tasks, return_exceptions=True)

    first_url, first_result = results[0] if results else (urls[0], {"success": False, "error_code": "unknown"})
    failed_urls = [url for url, _ in results if url]
    result = dict(first_result)
    result.setdefault("success", False)
    result["resolved_url"] = None
    if len(urls) > 1:
        result["error"] = result.get("error") or "所有候选 URL 均不可用"
        logger.info(
            "[ConnectivityTest] 候选 URL 均未通过: %s",
            ", ".join(_redact_url_for_log(url) for url in failed_urls or [first_url]),
        )
    return result


def _get_save_provider_api_key(core_cfg: dict, api_config: dict, provider_key: str) -> str:
    """Extract the provider's API key from the config being saved."""
    provider_key = str(provider_key or "").strip()
    if provider_key == "free":
        return "free-access"

    core_provider = str(core_cfg.get("coreApi") or "").strip()
    core_key = str(core_cfg.get("coreApiKey") or "").strip()

    registry_entry = (api_config.get("api_key_registry") or {}).get(provider_key, {})
    field_name = registry_entry.get("config_field") if isinstance(registry_entry, dict) else ""
    provider_key_value = str(core_cfg.get(field_name) or "").strip() if field_name else ""

    if provider_key_value:
        return provider_key_value
    if provider_key == core_provider and core_key:
        return core_key
    # 不能把 coreApiKey 当成 assist provider 的 fallback：core/assist 是不同
    # provider 时（比如 coreApi=openai + assistApi=qwen_intl），coreApiKey 是
    # OpenAI 的 key，拿去打 qwen_intl 的 candidate URL 必然 401 →
    # _auto_resolve_provider_urls_for_save 误判连通性失败 → 把之前测通的
    # qwen_intl 区域 pin 顺手 pop 掉 (Codex P2 #3258802582)。
    # 唯一应该回退 coreApiKey 的 case 是 provider_key == core_provider，
    # 上面那条已经处理；这里返回空字符串让 _build_save_connectivity_targets
    # 把这个 target 过滤掉，跳过本次 probe，保留 resolvedProviderUrls 旧值。
    return ""


def _build_save_connectivity_targets(core_cfg: dict, api_config: dict) -> dict[str, dict[str, Any]]:
    """Collect the built-in providers that need auto-detection on save."""
    targets: dict[str, dict[str, Any]] = {}
    core_providers = api_config.get("core_api_providers", {}) or {}
    assist_providers = api_config.get("assist_api_providers", {}) or {}

    def _add(scope: str, provider_key: str) -> None:
        provider_key = str(provider_key or "").strip()
        if not provider_key:
            return

        if scope == "core":
            profile = core_providers.get(provider_key)
            if not isinstance(profile, dict):
                return
            urls = _normalize_provider_url_candidates(profile, "core_url")
            model = profile.get("core_model", "")
            provider_type = "websocket"
        else:
            profile = assist_providers.get(provider_key)
            if not isinstance(profile, dict):
                return
            urls = _normalize_provider_url_candidates(profile, "openrouter_url")
            model = profile.get("conversation_model", "")
            provider_type = _normalize_provider_type(profile, urls[0] if urls else profile.get("openrouter_url", ""))

        # 单 URL 不需要解析候选地域；页面全量检测会负责常规连通性状态。
        if len(urls) < 2:
            return

        api_key = _get_save_provider_api_key(core_cfg, api_config, provider_key)
        if not api_key and not profile.get("is_free_version", False):
            return

        targets[f"{scope}:{provider_key}"] = {
            "scope": scope,
            "provider_key": provider_key,
            "urls": urls,
            "api_key": api_key,
            "model": model,
            "provider_type": provider_type,
            "is_free": profile.get("is_free_version", False),
            "label": profile.get("name", provider_key),
        }

    core_provider = str(core_cfg.get("coreApi") or "qwen").strip()
    # 显式选择的 assistApi 一律被尊重（free 与付费可双向组合）；
    # 仅在缺失时沿用 coreApi 偏好做默认：core=free 默认 free，其他默认 qwen。
    # 与 ConfigManager.get_core_config() 的解析规则保持一致。
    assist_provider = str(core_cfg.get("assistApi") or "").strip()
    if not assist_provider:
        assist_provider = "free" if core_provider == "free" else "qwen"

    _add("core", core_provider)
    _add("assist", assist_provider)

    if core_cfg.get("enableCustomApi", False):
        model_types = [
            "conversation", "summary", "gameMain", "gameSummary", "correction", "emotion",
            "vision", "agent", "omni", "tts",
        ]
        def _add_model_target(model_type: str, provider: str, seen: set[str] | None = None) -> None:
            seen = seen or set()
            provider = str(provider or "").strip()
            if not provider or provider == "custom":
                return
            seen_key = f"{model_type}:{provider}"
            if seen_key in seen:
                return
            seen.add(seen_key)
            if provider == "follow_core":
                _add("core" if model_type == "omni" else "assist", core_provider)
            elif provider == "follow_assist":
                _add("assist", assist_provider)
            elif provider == "follow_conversation":
                _add_model_target("conversation", str(core_cfg.get("conversationModelProvider") or "follow_assist"), seen)
            elif provider == "follow_summary":
                _add_model_target("summary", str(core_cfg.get("summaryModelProvider") or "follow_assist"), seen)
            else:
                _add("core" if model_type == "omni" else "assist", provider)

        for model_type in model_types:
            provider = str(core_cfg.get(f"{model_type}ModelProvider") or "").strip()
            _add_model_target(model_type, provider)

    return targets


async def _auto_resolve_provider_urls_for_save(
    core_cfg: dict,
    checked_resolved_urls: dict[str, str] | None = None,
) -> dict[str, Any]:
    """When saving the API config, auto-detect candidate URLs and persist the regional URL that passes."""
    from utils.api_config_loader import get_config as _get_api_config

    api_config = _get_api_config()
    targets = _build_save_connectivity_targets(core_cfg, api_config)
    summary: dict[str, Any] = {
        "total": len(targets),
        "success": 0,
        "failed": 0,
        "resolved_urls": {},
        "results": {},
    }
    # 起点用 core_cfg 里已经存的 resolved 快照（前端这一次保存连带传上来的
    # _resolvedProviderUrls + 上一次落盘的值），auto-resolve 只动本次 targets
    # 里的 provider。其它 provider 之前测通的 URL 留着别扔——比如核心用 GPT
    # 但 CosyVoice intl 还在用 assist:qwen_intl 的 US 端点，保存非 Qwen 设置
    # 不该顺手清掉 intl 的地域记忆。
    existing_resolved: dict[str, str] = {
        str(k): str(v)
        for k, v in (core_cfg.get("resolvedProviderUrls") or {}).items()
        if isinstance(k, str) and isinstance(v, str)
    }
    if not targets:
        core_cfg["resolvedProviderUrls"] = existing_resolved
        return summary

    resolved_urls: dict[str, str] = dict(existing_resolved)

    pending_targets: dict[str, dict[str, Any]] = {}
    checked_resolved_urls = checked_resolved_urls if isinstance(checked_resolved_urls, dict) else {}
    for target_key, target in targets.items():
        checked_url = str(checked_resolved_urls.get(target_key) or "").strip()
        if checked_url and checked_url in target["urls"]:
            resolved_urls[target_key] = checked_url
            summary["success"] += 1
            summary["resolved_urls"][target_key] = checked_url
            summary["results"][target_key] = {
                "success": True,
                "error": None,
                "error_code": None,
                "resolved_url": checked_url,
            }
        else:
            pending_targets[target_key] = target

    if not pending_targets:
        core_cfg["resolvedProviderUrls"] = resolved_urls
        return summary

    async def _run_target(target_key: str, target: dict[str, Any]) -> tuple[str, dict]:
        result = await _test_connectivity_candidates(
            target["urls"],
            target["api_key"],
            target["model"],
            target["provider_type"],
            target["is_free"],
        )
        return target_key, result

    task_results = await asyncio.gather(
        *(_run_target(key, target) for key, target in pending_targets.items()),
        return_exceptions=True,
    )

    for item in task_results:
        if isinstance(item, Exception):
            summary["failed"] += 1
            continue
        target_key, result = item
        clean_result = {
            "success": bool(result.get("success")),
            "error": result.get("error"),
            "error_code": result.get("error_code"),
            "resolved_url": result.get("resolved_url"),
        }
        summary["results"][target_key] = clean_result
        if result.get("success") and result.get("resolved_url"):
            summary["success"] += 1
            resolved_urls[target_key] = result["resolved_url"]
            summary["resolved_urls"][target_key] = result["resolved_url"]
        else:
            summary["failed"] += 1
            # 本次测失败的 target 必须把旧 resolved 也丢掉，避免下次继续打不通的旧 URL
            # (CodeRabbit #3258131687 已要求过的语义)。其它没被 test 到的 provider
            # 由 existing_resolved 保留，互不影响。
            resolved_urls.pop(target_key, None)
            summary["resolved_urls"].pop(target_key, None)

    core_cfg["resolvedProviderUrls"] = resolved_urls
    logger.info(
        "[ConnectivityTest] 保存前候选 URL 自动检测完成: success=%s failed=%s",
        summary["success"],
        summary["failed"],
    )
    return summary


@router.post("/test_connectivity")
async def test_connectivity(req: ConnectivityTestRequest) -> dict:
    """Test API connectivity.

    Two modes:
    1. Built-in provider: pass provider_key + provider_scope + api_key;
       the backend reads url/model/provider_type from api_providers.json.
    2. Custom API: pass url + api_key + model (+ provider_type);
       the frontend sends the full parameters and the backend uses them directly.

    The test strategy is chosen by provider_type:
    - openai_compatible (default): send a minimal chat completion request via ChatOpenAI (max_completion_tokens governed by CONNECTIVITY_TEST_MAX_TOKENS)
    - websocket: WebSocket handshake, closed immediately on success
    - anthropic: send a minimal Anthropic Messages request

    All requests have a 10-second timeout. The endpoint is async, so it naturally supports concurrent requests without blocking.
    """
    api_key_stripped = (req.api_key or "").strip()

    # --- Mode 1: Built-in provider (resolve config from api_providers.json) ---
    if req.provider_key and req.provider_scope:
        from utils.api_config_loader import get_config as _get_api_config

        api_config = _get_api_config()
        provider_key = req.provider_key.strip()
        scope = req.provider_scope.strip().lower()
        url_candidates: list[str] = []

        if scope == "core":
            providers = api_config.get("core_api_providers", {})
            profile = providers.get(provider_key, {})
            url_stripped = profile.get("core_url", "")
            url_candidates = _normalize_provider_url_candidates(profile, "core_url")
            model = profile.get("core_model", "")
            provider_type = "websocket"
            is_free = profile.get("is_free_version", False)
            _source_label = profile.get("name", provider_key)
        elif scope == "assist":
            providers = api_config.get("assist_api_providers", {})
            profile = providers.get(provider_key, {})
            url_stripped = profile.get("openrouter_url", "")
            url_candidates = _normalize_provider_url_candidates(profile, "openrouter_url")
            # Use conversation_model as the test model (most representative)
            model = profile.get("conversation_model", "")
            provider_type = _normalize_provider_type(profile, url_stripped)
            is_free = profile.get("is_free_version", False)
            _source_label = profile.get("name", provider_key)
        else:
            return {"success": False, "error": "无效的 provider_scope", "error_code": "missing_params"}

        if not url_stripped:
            # Provider has no core_url (e.g. Gemini uses SDK, not raw WebSocket).
            # Fall back to the assist profile's OpenAI-compatible endpoint to verify the key.
            assist_providers = api_config.get("assist_api_providers", {})
            assist_profile = assist_providers.get(provider_key, {})
            fallback_url = assist_profile.get("openrouter_url", "")
            fallback_model = assist_profile.get("conversation_model", "")
            if fallback_url and fallback_model:
                url_stripped = fallback_url
                url_candidates = _normalize_provider_url_candidates(assist_profile, "openrouter_url")
                model = fallback_model
                provider_type = _normalize_provider_type(assist_profile, url_stripped)
                _source_label = assist_profile.get("name", profile.get("name", provider_key)) + "（通过辅助端点验证）"
            else:
                return {"success": False, "error": f"供应商 {_source_label} 暂不支持连通测试", "error_code": "missing_params"}
        elif req.url and req.url.strip():
            override_url = req.url.strip()
            override_host = (urllib.parse.urlsplit(override_url).hostname or "").lower()
            if scope != "assist" or provider_key != "mimo" or override_host not in _MIMO_TOKEN_PLAN_HOSTS:
                return {"success": False, "error": "无效的 provider URL override", "error_code": "missing_params"}
            url_stripped = override_url
            url_candidates = [url_stripped]

    # --- Mode 2: Custom API (use frontend-provided params directly) ---
    else:
        if not req.url or not req.url.strip():
            return {"success": False, "error": "缺少必要参数", "error_code": "missing_params"}

        url_stripped = req.url.strip()
        url_candidates = [url_stripped]
        model = (req.model or "gpt-3.5-turbo").strip()
        provider_type = _normalize_provider_type({"provider_type": req.provider_type}, url_stripped)
        is_free = bool(req.is_free)
        _source_label = _identify_provider_label(url_stripped, is_free)

    # sub_type 仅 Mode 2 (custom URL) 允许使用；Mode 1 built-in provider 由
    # api_providers.json 决定 provider_type，不应被前端 sub_type 覆盖
    # (#1764 review 第六轮)。
    sub_type = ""
    if not (req.provider_key and req.provider_scope):
        sub_type = (req.sub_type or "").strip().lower()

    try:
        result = await _test_connectivity_candidates(
            url_candidates or [url_stripped],
            api_key_stripped,
            model,
            provider_type,
            is_free,
            sub_type=sub_type,
            voice_id=(req.voice_id or "").strip(),
        )
    except Exception as e:
        logger.exception("[ConnectivityTest] 未预期的异常")
        result = {"success": False, "error": str(e), "error_code": "unknown"}

    # 单条结果日志：供应商/自定义 + 成功/失败
    if result.get("success"):
        logger.info("[ConnectivityTest] %s 连通", _source_label)
    else:
        logger.info("[ConnectivityTest] %s 失败: %s", _source_label, result.get("error_code", "unknown"))

    return result


def _identify_provider_label(url: str, is_free: bool) -> str:
    """Identify which provider a URL belongs to and return a human-readable label.
    Known providers show their name; custom ones show the full URL.
    """
    _KNOWN_PROVIDERS = {
        "lanlan.tech": "免费版",
        "dashscope.aliyuncs.com": "阿里百炼",
        "dashscope-intl.aliyuncs.com": "阿里国际版",
        "dashscope-us.aliyuncs.com": "阿里国际版（美国）",
        "api.openai.com": "OpenAI",
        "open.bigmodel.cn": "智谱",
        "api.stepfun.com": "阶跃星辰",
        "api.siliconflow.cn": "硅基流动",
        "generativelanguage.googleapis.com": "Gemini",
        "api.moonshot.cn": "Kimi",
        "api.kimi.com": "Kimi Code",
        "api.xiaomimimo.com": "MiMo",
        "token-plan-cn.xiaomimimo.com": "MiMo Token Plan",
        "token-plan-sgp.xiaomimimo.com": "MiMo Token Plan",
        "token-plan-ams.xiaomimimo.com": "MiMo Token Plan",
    }
    url_lower = url.lower()
    for domain, name in _KNOWN_PROVIDERS.items():
        if domain in url_lower:
            if is_free:
                return f"{name}(免费)"
            return name
    # 自定义 URL：脱敏后显示（移除敏感 query 参数）
    return f"自定义({_redact_url_for_log(url)})"


def _redact_url_for_log(url: str) -> str:
    """Redact sensitive query parameters and userinfo before logging a custom endpoint URL."""
    try:
        parsed = urllib.parse.urlsplit(url)

        # Redact userinfo (https://user:pass@host/ → https://***:***@host/)
        netloc = parsed.netloc
        if '@' in netloc:
            host_part = netloc.split('@', 1)[1]
            netloc = f"***:***@{host_part}"

        # Redact sensitive query parameters
        sensitive_keys = {
            "api_key", "apikey", "key", "token", "access_token", "authorization",
            "signature", "sig", "client_secret", "password", "jwt", "bearer",
        }
        if parsed.query:
            query_pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
            redacted_pairs = [
                (k, "***" if k.lower() in sensitive_keys else v)
                for k, v in query_pairs
            ]
            redacted_query = urllib.parse.urlencode(redacted_pairs)
        else:
            redacted_query = ""

        return urllib.parse.urlunsplit((parsed.scheme, netloc, parsed.path, redacted_query, parsed.fragment))
    except Exception:
        return url
