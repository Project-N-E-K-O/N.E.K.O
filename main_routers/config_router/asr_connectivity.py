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

"""Connectivity test for the speech-recognition slot (/test_asr_connectivity).

Sends half a second of silence through the exact request the slot makes at
runtime (``request_slot_transcript``), so a passing test means the URL, key,
model and response format all work. An empty transcript is a success.
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from pydantic import BaseModel

from ._shared import logger, router
from .connectivity import _get_save_provider_api_key
from .core_config import is_core_config_secret_placeholder


_ASR_TEST_TIMEOUT_SECONDS = 60.0
# 0.5 s of 16 kHz mono PCM16 silence.
_ASR_TEST_PCM16 = b"\x00\x00" * 8_000

_FAILURE_KIND_ERRORS: dict[str, tuple[str, str]] = {
    "auth": ("auth_failed", "API Key无效或已过期"),
    "rate_limited": ("rate_limited", "请求过于频繁，请稍后再试"),
    "timeout": ("timeout", "请求超时"),
    "invalid_response": ("invalid_response", "返回格式不是可识别的转写结果"),
    "rejected": ("rejected", "服务拒绝了这次识别请求，请检查 URL 和模型 ID"),
    "failed": ("connection_refused", "无法完成识别请求，请检查 URL 是否可达"),
}


class AsrConnectivityTestRequest(BaseModel):
    """One speech-recognition slot to test.

    ``provider_key`` is the slot vendor (``custom``, ``openai``, ``qwen``...).
    A named vendor always uses its fixed endpoint; only ``custom`` takes
    ``url``. A masked ``api_key`` means "the saved key": for a vendor that is
    its Key Book entry, for ``custom`` the slot key, and only while ``url`` is
    still the saved endpoint.
    """

    provider_key: Optional[str] = ""
    url: Optional[str] = ""
    api_key: Optional[str] = ""
    model: Optional[str] = ""


def _failure(error_code: str, error: str) -> dict[str, Any]:
    return {"success": False, "error": error, "error_code": error_code}


def _resolve_test_endpoint(
    req: AsrConnectivityTestRequest, core_cfg: dict, api_config: dict
) -> Any:
    from main_logic.asr_client.slot import (
        ASR_SLOT_CUSTOM,
        ASR_SLOT_DEFAULT_TRANSPORT,
        ASR_SLOT_ROUTES,
        ASR_SLOT_VENDORS,
        AsrSlotEndpoint,
        normalize_custom_asr_url,
    )
    from utils.http.url import same_endpoint

    vendor = str(req.provider_key or "").strip().lower()
    spec = ASR_SLOT_VENDORS.get(vendor)
    provider_key = ASR_SLOT_ROUTES.get((vendor, ASR_SLOT_DEFAULT_TRANSPORT))
    if spec is None or provider_key is None:
        return _failure("unsupported", "该服务商不支持语音识别")

    submitted_key = req.api_key if isinstance(req.api_key, str) else ""
    masked = is_core_config_secret_placeholder(submitted_key)
    model = str(req.model or "").strip() or spec.default_model

    if vendor == ASR_SLOT_CUSTOM:
        url = normalize_custom_asr_url(req.url)
        if not url:
            return _failure("missing_params", "请填写 http(s) 开头的 API URL")
        if masked:
            stored_url = normalize_custom_asr_url(core_cfg.get("asrModelUrl"))
            stored_key = str(core_cfg.get("asrModelApiKey") or "").strip()
            # A saved key belongs only to its saved endpoint.
            if not (stored_url and same_endpoint(url, stored_url)):
                return _failure("key_required", "已保存的 Key 只能用于保存时的地址，请重新填写 API Key")
            api_key = stored_key
        else:
            api_key = submitted_key.strip()
    else:
        url = spec.default_url
        if submitted_key.strip() and not masked:
            api_key = submitted_key.strip()
        else:
            api_key = _get_save_provider_api_key(core_cfg, api_config, vendor)
        if not api_key:
            return _failure("key_required", "请先在 API Key 设置里填写该服务商的 Key")

    return AsrSlotEndpoint(
        vendor=vendor,
        transport=ASR_SLOT_DEFAULT_TRANSPORT,
        provider_key=provider_key,
        protocol=spec.protocol,
        url=url,
        model=model,
        api_key=api_key,
    )


@router.post("/test_asr_connectivity")
async def test_asr_connectivity(req: AsrConnectivityTestRequest) -> dict:
    """Send a short silent clip through the slot's request and report the result."""
    from main_logic.asr_client.workers._shared import AsrRequestFailure, encode_pcm16_wav
    from main_logic.asr_client.workers.slot_request import (
        create_slot_client,
        request_slot_transcript,
    )
    from utils.api_config_loader import get_config as _get_api_config
    from utils.config_manager import get_config_manager

    api_config = await asyncio.to_thread(_get_api_config)
    if not isinstance(api_config, dict):
        api_config = {}
    try:
        core_cfg = await asyncio.to_thread(
            get_config_manager().load_json_config, "core_config.json", {}
        )
    except Exception:
        core_cfg = {}
    if not isinstance(core_cfg, dict):
        core_cfg = {}

    endpoint = _resolve_test_endpoint(req, core_cfg, api_config)
    if isinstance(endpoint, dict):
        return endpoint

    client: Any = None
    try:
        client = create_slot_client(endpoint, endpoint.api_key)
        await asyncio.wait_for(
            request_slot_transcript(
                client,
                endpoint,
                endpoint.api_key,
                encode_pcm16_wav(_ASR_TEST_PCM16),
            ),
            _ASR_TEST_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        return _failure("timeout", "请求超时")
    except AsrRequestFailure as exc:
        error_code, message = _FAILURE_KIND_ERRORS.get(
            exc.kind, _FAILURE_KIND_ERRORS["failed"]
        )
        logger.info("[ASRConnectivity] %s failed: %s", endpoint.vendor, exc.code)
        return _failure(error_code, message)
    except Exception as exc:
        logger.info("[ASRConnectivity] %s failed: %s", endpoint.vendor, type(exc).__name__)
        return _failure("unknown", "识别请求失败")
    finally:
        if client is not None:
            for owner in (client, getattr(client, "aio", None)):
                close = getattr(owner, "aclose", None)
                if callable(close):
                    try:
                        await close()
                    except Exception:
                        pass
                    break
    logger.info("[ASRConnectivity] %s ok", endpoint.vendor)
    return {"success": True}
