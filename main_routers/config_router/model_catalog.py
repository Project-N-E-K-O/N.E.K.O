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

"""Upstream model list endpoint (/list_models) for the API settings page."""

from ._shared import logger, router
from .connectivity import (
    _classify_anthropic_error,
    _classify_openai_error,
    _get_save_provider_api_key,
    _identify_provider_label,
    _is_mimo_token_plan_url,
    _normalize_provider_type,
    _normalize_provider_url_candidates,
)
from .core_config import CORE_CONFIG_MODEL_TYPES, is_core_config_secret_placeholder

import asyncio
import urllib.parse
from typing import Any, Optional
from pydantic import BaseModel

from utils.http.url import same_endpoint


_MODEL_LIST_TIMEOUT_SECONDS = 15.0
# OpenRouter 一类聚合平台有数百个模型，前端下拉只需要 id，封顶防止响应失控。
_MODEL_LIST_MAX_ITEMS = 2000
# Gemini 的 OpenAI 兼容层列表 id 形如 models/gemini-2.5-flash，请求时用裸名；别家端点的 id 原样保留。
_GEMINI_OPENAI_HOST = "generativelanguage.googleapis.com"
_GEMINI_MODEL_ID_PREFIX = "models/"


class ModelListRequest(BaseModel):
    """Request model for listing upstream models.

    Two modes, mirroring the connectivity test:
    1. Built-in provider: provide provider_key (+ api_key). The endpoint comes
       from api_providers.json only, so a stored key never leaves for a URL the
       page supplied.
    2. Custom endpoint: provide url + api_key (+ model_type). A masked api_key
       is resolved to the slot's stored key only while url is the slot's saved
       endpoint.
    """
    provider_key: Optional[str] = None
    url: Optional[str] = ""
    api_key: Optional[str] = ""
    provider_type: Optional[str] = "openai_compatible"
    model_type: Optional[str] = ""


def _failure(error_code: str, error: str) -> dict[str, Any]:
    return {"success": False, "error": error, "error_code": error_code}


def _is_http_url(url: str) -> bool:
    try:
        return urllib.parse.urlsplit(url).scheme.lower() in ("http", "https")
    except Exception:
        return False


def _is_gemini_openai_url(url: str) -> bool:
    try:
        return (urllib.parse.urlsplit(url).hostname or "").lower() == _GEMINI_OPENAI_HOST
    except Exception:
        return False


def _resolve_provider_target(req: ModelListRequest, core_cfg: dict, api_config: dict) -> dict[str, Any]:
    """Resolve urls/key/protocol for a built-in assist provider."""
    provider_key = (req.provider_key or "").strip()
    profile = (api_config.get("assist_api_providers") or {}).get(provider_key)
    if not isinstance(profile, dict):
        return _failure("unsupported", "未知的服务商")
    if profile.get("is_free_version") or profile.get("fixed_model"):
        return _failure("unsupported", "该服务商的模型是固定的")

    urls = _normalize_provider_url_candidates(profile, "openrouter_url")
    resolved_url_key = f"assist:{provider_key}"
    use_token_plan = False
    override_url = (req.url or "").strip()
    # 前端只能把 MiMo 切到 HTTPS 的 Token Plan 节点；其他服务商的地址一律以 api_providers.json 为准。
    if override_url and provider_key == "mimo" and _is_mimo_token_plan_url(override_url):
        urls = [override_url]
        use_token_plan = True
        resolved_url_key = "assist:mimo_token_plan"

    resolved_urls = core_cfg.get("resolvedProviderUrls")
    saved_url = (
        str(resolved_urls.get(resolved_url_key) or "").strip()
        if isinstance(resolved_urls, dict)
        else ""
    )
    if saved_url in urls:
        urls = [saved_url] + [url for url in urls if url != saved_url]
    urls = [url for url in urls if _is_http_url(url)]
    if not urls:
        return _failure("unsupported", "该服务商没有可拉取模型列表的 HTTP 端点")

    submitted_key = req.api_key if isinstance(req.api_key, str) else ""
    if submitted_key.strip() and not is_core_config_secret_placeholder(submitted_key):
        api_key = submitted_key.strip()
    elif use_token_plan:
        api_key = str(core_cfg.get("assistApiKeyMimoTokenPlan") or "").strip()
    else:
        api_key = _get_save_provider_api_key(core_cfg, api_config, provider_key)

    return {
        "urls": urls,
        "api_key": api_key,
        "provider_type": _normalize_provider_type(profile, urls[0]),
    }


def _resolve_custom_target(req: ModelListRequest, core_cfg: dict) -> dict[str, Any]:
    """Resolve url/key/protocol for a user-typed endpoint."""
    url = (req.url or "").strip()
    if not url:
        return _failure("missing_params", "缺少 API URL")
    if not _is_http_url(url):
        return _failure("unsupported", "只有 HTTP(S) 端点支持拉取模型列表")

    submitted_key = req.api_key if isinstance(req.api_key, str) else ""
    if is_core_config_secret_placeholder(submitted_key):
        model_type = (req.model_type or "").strip()
        stored_url = ""
        stored_key = ""
        if model_type in CORE_CONFIG_MODEL_TYPES:
            stored_url = str(core_cfg.get(f"{model_type}ModelUrl") or "").strip()
            stored_key = str(core_cfg.get(f"{model_type}ModelApiKey") or "").strip()
        # 已保存的 Key 只能配保存时的端点：页面改了 URL 还想复用旧 Key，就是把凭证发给别家。
        if not (stored_url and stored_key and same_endpoint(url, stored_url)):
            return _failure("key_required", "已保存的 Key 只能用于保存时的地址，请重新填写 API Key")
        api_key = stored_key
    else:
        api_key = submitted_key.strip()

    provider_type = _normalize_provider_type({"provider_type": req.provider_type}, url)
    return {
        "urls": [url],
        "api_key": api_key,
        "provider_type": "anthropic" if provider_type == "anthropic" else "openai_compatible",
    }


def _normalize_model_entries(
    raw_models: list[dict[str, str]], *, strip_gemini_prefix: bool
) -> list[dict[str, str]]:
    """Strip, de-duplicate and sort the upstream entries by id."""
    entries: dict[str, dict[str, str]] = {}
    for item in raw_models:
        model_id = str(item.get("id") or "").strip()
        if strip_gemini_prefix and model_id.startswith(_GEMINI_MODEL_ID_PREFIX):
            model_id = model_id[len(_GEMINI_MODEL_ID_PREFIX):]
        if not model_id or model_id in entries:
            continue
        entry = {"id": model_id}
        name = str(item.get("name") or "").strip()
        if name and name != model_id:
            entry["name"] = name
        entries[model_id] = entry
    return sorted(entries.values(), key=lambda entry: entry["id"].lower())


def _classify_model_list_error(exc: Exception, provider_type: str) -> dict[str, Any]:
    status = getattr(exc, "status_code", None)
    if status in (404, 405):
        return _failure("unsupported", "该端点不提供模型列表，请手动填写模型 ID")
    if status == 429:
        return _failure("rate_limited", "请求过于频繁，请稍后再试")
    classify = _classify_anthropic_error if provider_type == "anthropic" else _classify_openai_error
    result = classify(exc)
    # 连通性测试把 429 等视为「可达」，拉列表没拿到数据就是失败。
    if result.get("success"):
        return _failure("unknown", str(exc))
    return result


async def _fetch_models(url: str, api_key: str, provider_type: str) -> dict[str, Any]:
    from utils.llm_client import ChatAnthropic, ChatOpenAI

    try:
        if provider_type == "anthropic":
            client = ChatAnthropic(
                base_url=url,
                api_key=api_key or None,
                timeout=_MODEL_LIST_TIMEOUT_SECONDS,
                max_retries=0,
            )
        else:
            client = ChatOpenAI(  # noqa: LLM_OUTPUT_BUDGET  # 只调 GET /models，不产生补全
                base_url=url,
                api_key=api_key or None,
                timeout=_MODEL_LIST_TIMEOUT_SECONDS,
                max_retries=0,
            )
        try:
            raw_models = await client.alist_models(limit=_MODEL_LIST_MAX_ITEMS)
        finally:
            await client.aclose()
    except Exception as exc:
        return _classify_model_list_error(exc, provider_type)

    models = _normalize_model_entries(raw_models, strip_gemini_prefix=_is_gemini_openai_url(url))
    if not models:
        return _failure("empty", "上游没有返回任何模型")
    return {"success": True, "models": models, "resolved_url": url}


@router.post("/list_models")
async def list_models(req: ModelListRequest) -> dict:
    """List the models an upstream endpoint offers, for the model ID pickers."""
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

    if (req.provider_key or "").strip():
        target = _resolve_provider_target(req, core_cfg, api_config)
    else:
        target = _resolve_custom_target(req, core_cfg)
    if "urls" not in target:
        return target

    result: dict[str, Any] = _failure("unknown", "没有可用的端点")
    deadline = asyncio.get_running_loop().time() + _MODEL_LIST_TIMEOUT_SECONDS
    for index, url in enumerate(target["urls"]):
        remaining = max(0, deadline - asyncio.get_running_loop().time())
        # Reserve a fair share of the total budget for each remaining fallback.
        candidate_timeout = remaining / (len(target["urls"]) - index)
        try:
            result = await asyncio.wait_for(
                _fetch_models(url, target["api_key"], target["provider_type"]),
                timeout=candidate_timeout,
            )
        except asyncio.TimeoutError:
            result = _failure("timeout", "拉取模型列表超时")
            continue
        if result.get("success"):
            logger.info(
                "[ModelList] %s 拉取到 %d 个模型",
                _identify_provider_label(url, False),
                len(result["models"]),
            )
            return result
        if result.get("error_code") == "auth_failed":
            break
    logger.info(
        "[ModelList] %s 拉取失败: %s",
        _identify_provider_label(target["urls"][0], False),
        result.get("error_code", "unknown"),
    )
    return result
