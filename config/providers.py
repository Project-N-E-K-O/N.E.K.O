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

"""Unified provider registry.

Centralizes, for every LLM provider:
  - extra_body config (disabling thinking, etc.)
  - Context Cache behavior (header, token field, thresholds)

Other modules obtain provider-specific parameters from this file instead of
hard-coding their own.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ────────────────────────────────────────────────────────────────
# Extra-body 常量 & 映射（原 config/__init__.py）
# ────────────────────────────────────────────────────────────────

EXTRA_BODY_OPENAI = {"enable_thinking": False}
EXTRA_BODY_CLAUDE = {"thinking": {"type": "disabled"}}
EXTRA_BODY_GEMINI = {"extra_body": {"google": {"thinking_config": {"thinking_budget": 0}}}}
EXTRA_BODY_GEMINI_3 = {"extra_body": {"google": {"thinking_config": {"thinking_level": "low", "include_thoughts": False}}}}
EXTRA_BODY_OPENROUTER = {"reasoning": {"effort": "none"}}
EXTRA_BODY_MINIMAX = {"reasoning_split": True}

# Agent 调用统一开关：是否加载 extra_body。
# 默认开启，配合 MODELS_EXTRA_BODY_MAP 实现默认关闭 thinking。
AGENT_USE_EXTRA_BODY = True

# 模型到 extra_body 的映射
MODELS_EXTRA_BODY_MAP: dict[str, dict] = {
    # Qwen 系列
    "qwen-flash": EXTRA_BODY_OPENAI,
    "qwen3.6-flash": EXTRA_BODY_OPENAI,
    "qwen3.6-flash-2026-04-16": EXTRA_BODY_OPENAI,
    "qwen3-vl-plus-2025-09-23": EXTRA_BODY_OPENAI,
    "qwen3-vl-plus": EXTRA_BODY_OPENAI,
    "qwen3-vl-flash": EXTRA_BODY_OPENAI,
    "qwen3.5-plus": EXTRA_BODY_OPENAI,
    "qwen3.6-plus": EXTRA_BODY_OPENAI,
    "qwen3.6-plus-2026-04-02": EXTRA_BODY_OPENAI,
    "qwen-plus": EXTRA_BODY_OPENAI,
    "qwen3.7-plus-2026-05-26": EXTRA_BODY_OPENAI,
    "qwen3.7-plus": EXTRA_BODY_OPENAI,
    # GLM 系列
    "glm-4.5-air": EXTRA_BODY_CLAUDE,
    "glm-4.6v-flash": EXTRA_BODY_CLAUDE,
    "glm-4.7-flash": EXTRA_BODY_CLAUDE,
    "glm-4.6v": EXTRA_BODY_CLAUDE,
    "glm-5v-turbo": EXTRA_BODY_CLAUDE,
    "glm-5.1": EXTRA_BODY_CLAUDE,
    # Kimi系列
    "kimi-k2-0905-preview": EXTRA_BODY_CLAUDE,
    "kimi-k2.5": EXTRA_BODY_CLAUDE,
    # MiniMax系列
    "MiniMax-M2.5": EXTRA_BODY_MINIMAX,
    "MiniMax-M2.7": EXTRA_BODY_MINIMAX,
    "MiniMax-Text-01": EXTRA_BODY_MINIMAX,
    # Silicon
    "zai-org/GLM-4.6V": EXTRA_BODY_OPENAI,
    "deepseek-ai/DeepSeek-V3.2": EXTRA_BODY_OPENAI,
    "deepseek-ai/DeepSeek-V4-Flash": EXTRA_BODY_OPENAI,
    "Qwen/Qwen3.5-397B-A17B": EXTRA_BODY_OPENAI,
    # Step
    "step-2-mini": {"tools": [{"type": "web_search", "function": {"description": "这个web_search用来搜索互联网的信息"}}]},
    # Claude 系列
    "claude-sonnet-4-6": EXTRA_BODY_CLAUDE,
    "claude-haiku-4-5-20251001": EXTRA_BODY_CLAUDE,
    "claude-opus-4-7": EXTRA_BODY_CLAUDE,
    "claude-opus-4-6": EXTRA_BODY_CLAUDE,
    # Doubao Seed 2.0 系列
    "doubao-seed-2-0-lite-260215": EXTRA_BODY_CLAUDE,
    "doubao-seed-2-0-mini-260215": EXTRA_BODY_CLAUDE,
    "doubao-seed-2-0-pro-260215": EXTRA_BODY_CLAUDE,
    "doubao-seed-2-0-lite-260428": EXTRA_BODY_CLAUDE,
    "doubao-seed-2-0-mini-260428": EXTRA_BODY_CLAUDE,
    # Gemini 系列
    "gemini-2.5-flash": EXTRA_BODY_GEMINI,
    "gemini-2.5-flash-lite": EXTRA_BODY_GEMINI,
    "gemini-3-flash-preview": EXTRA_BODY_GEMINI_3,
    # OpenRouter 格式 (provider/model) — OpenRouter 使用统一的 reasoning 参数
    "google/gemini-2.5-flash": EXTRA_BODY_OPENROUTER,
    "google/gemini-2.5-flash-lite": EXTRA_BODY_OPENROUTER,
    "google/gemini-3-flash-preview": EXTRA_BODY_OPENROUTER,
    "qwen/qwen3.5-9b": EXTRA_BODY_OPENROUTER,
}


def get_extra_body(model: str) -> dict | None:
    """Return the extra_body config for the given model name.

    Returns:
        The matching extra_body dict; an empty dict when the model needs no
        special config; None when model is empty.
    """
    if not model:
        return None
    return MODELS_EXTRA_BODY_MAP.get(model, {})


def get_agent_extra_body(model: str) -> dict | None:
    """Return extra_body for Agent calls based on a single global switch."""
    if not AGENT_USE_EXTRA_BODY:
        return None
    return get_extra_body(model)


# Top-level extra_body keys that *disable* thinking (one per provider family in
# MODELS_EXTRA_BODY_MAP). Gemini nests its thinking_config under an "extra_body"
# wrapper key whose only payload here is the thinking config, so the whole key is
# thinking-related. Anything NOT in this set (e.g. step-2-mini's "tools" with the
# built-in web_search) is unrelated provider config that must survive Focus.
_THINKING_EXTRA_BODY_KEYS = frozenset({
    "enable_thinking",   # EXTRA_BODY_OPENAI (qwen / silicon …)
    "thinking",          # EXTRA_BODY_CLAUDE (claude / glm / kimi / doubao …)
    "reasoning",         # EXTRA_BODY_OPENROUTER
    "reasoning_split",   # EXTRA_BODY_MINIMAX
    "extra_body",        # EXTRA_BODY_GEMINI/_3 (google.thinking_config wrapper)
})


def focus_extra_body(model: str) -> dict | None:
    """extra_body for a Focus (thinking-on) turn: the model's resolved extra_body
    with only the *thinking-disable* keys removed.

    Focus wants thinking to run free (drop the disable knob), but must NOT
    silently nuke unrelated provider config — e.g. ``step-2-mini`` ships a
    built-in ``web_search`` tool in its extra_body, which a blunt
    ``extra_body=None`` would drop. Returns the surviving non-thinking dict, or
    ``None`` when nothing remains (the body was purely thinking-disable)."""
    resolved = get_extra_body(model) or {}
    kept = {k: v for k, v in resolved.items() if k not in _THINKING_EXTRA_BODY_KEYS}
    return kept or None


def leaks_thinking_in_content(model: str) -> bool:
    """True for models that stream chain-of-thought into ``content`` (not the
    separate ``reasoning_content`` field), which a Focus (thinking-on) turn
    would otherwise speak aloud.

    Only the Qwen3.5/3.6/3.7 *hybrid* models do this — they emit the whole CoT
    into ``content`` terminated by a lone ``</think>`` (see the leak note in
    ``utils.llm_client``). The ``qwen3-vl-*`` vision models route reasoning to
    ``reasoning_content`` and stay clean, so they are excluded. Used to gate
    ``utils.llm_client.ThinkingStreamStripper`` onto the streaming path so clean
    providers keep streaming untouched."""
    m = (model or "").lower()
    if "vl" in m:
        return False
    return any(tag in m for tag in ("qwen3.5", "qwen3.6", "qwen3.7"))


# ────────────────────────────────────────────────────────────────
# Cache Provider 配置（原 tests/test_cco_capacity.py PROVIDER_CACHE_CONFIG）
# ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CacheProviderConfig:
    """Context Cache behavior description for a single provider."""

    provider_id: str
    name: str
    base_url: str                   # 典型完整 URL（用于测试/文档）
    base_url_pattern: str           # 用于 substring match
    cache_mode: str                 # "session" | "auto" | "upstream"
    requires_header: bool
    header_name: str | None = None
    header_value: str | None = None
    min_cache_tokens: int = 1024
    cached_token_field: str = "prompt_tokens_details.cached_tokens"
    auto_cache: bool = True
    cache_price: float = 0.10
    creation_price: float = 0.10

    # 兼容测试里 config["xxx"] 字典式访问
    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)


CACHE_PROVIDERS: dict[str, CacheProviderConfig] = {
    # qwen_intl / qwen_us 必须排在 qwen 前面：resolve_cache_provider 按
    # dict 顺序做 substring 匹配，区域域名需要先命中自己的配置。
    "qwen_intl": CacheProviderConfig(
        provider_id="qwen_intl",
        name="阿里云 DashScope (Intl)",
        base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        base_url_pattern="dashscope-intl.aliyuncs.com",
        cache_mode="session",
        requires_header=True,
        header_name="x-dashscope-session-cache",
        header_value="enable",
        min_cache_tokens=1024,
        auto_cache=True,
        cache_price=0.10,
        creation_price=0.125,
        cached_token_field="prompt_tokens_details.cached_tokens",
    ),
    "qwen_us": CacheProviderConfig(
        provider_id="qwen_us",
        name="阿里云 DashScope (US)",
        base_url="https://dashscope-us.aliyuncs.com/compatible-mode/v1",
        base_url_pattern="dashscope-us.aliyuncs.com",
        cache_mode="session",
        requires_header=True,
        header_name="x-dashscope-session-cache",
        header_value="enable",
        min_cache_tokens=1024,
        auto_cache=True,
        cache_price=0.10,
        creation_price=0.125,
        cached_token_field="prompt_tokens_details.cached_tokens",
    ),
    "qwen": CacheProviderConfig(
        provider_id="qwen",
        name="阿里云 DashScope",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        base_url_pattern="dashscope.aliyuncs.com",
        cache_mode="session",
        requires_header=True,
        header_name="x-dashscope-session-cache",
        header_value="enable",
        min_cache_tokens=1024,
        auto_cache=True,
        cache_price=0.10,
        creation_price=0.125,
        cached_token_field="prompt_tokens_details.cached_tokens",
    ),
    "openai": CacheProviderConfig(
        provider_id="openai",
        name="OpenAI",
        base_url="https://api.openai.com/v1",
        base_url_pattern="api.openai.com",
        cache_mode="auto",
        requires_header=False,
        min_cache_tokens=1024,
        cached_token_field="prompt_tokens_details.cached_tokens",
    ),
    "glm": CacheProviderConfig(
        provider_id="glm",
        name="智谱 GLM",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        base_url_pattern="open.bigmodel.cn",
        cache_mode="auto",
        requires_header=False,
        min_cache_tokens=1024,
        cached_token_field="cached_tokens",
    ),
    "step": CacheProviderConfig(
        provider_id="step",
        name="阶跃星辰 Step",
        base_url="https://api.stepfun.com/v1",
        base_url_pattern="api.stepfun.com",
        cache_mode="auto",
        requires_header=False,
        min_cache_tokens=1024,
        cached_token_field="cached_tokens",
    ),
    "silicon": CacheProviderConfig(
        provider_id="silicon",
        name="硅基流动 Silicon",
        base_url="https://api.siliconflow.cn/v1",
        base_url_pattern="api.siliconflow.cn",
        cache_mode="upstream",
        requires_header=False,
        min_cache_tokens=1024,
        cached_token_field="prompt_cache_hit_tokens",
    ),
    "gemini": CacheProviderConfig(
        provider_id="gemini",
        name="Google Gemini",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        base_url_pattern="generativelanguage.googleapis.com",
        cache_mode="auto",
        requires_header=False,
        min_cache_tokens=2048,
        cached_token_field="cached_content_token_count",
    ),
    "kimi": CacheProviderConfig(
        provider_id="kimi",
        name="Moonshot Kimi",
        base_url="https://api.moonshot.cn/v1",
        base_url_pattern="api.moonshot.cn",
        cache_mode="auto",
        requires_header=False,
        min_cache_tokens=1024,
        cached_token_field="prompt_cache_hit_tokens",
    ),
    "grok": CacheProviderConfig(
        provider_id="grok",
        name="xAI Grok",
        base_url="https://api.x.ai/v1",
        base_url_pattern="api.x.ai",
        cache_mode="auto",
        requires_header=False,
        min_cache_tokens=1024,
        cached_token_field="prompt_tokens_details.cached_tokens",  # 与 OpenAI 相同
    ),
    "doubao": CacheProviderConfig(
        provider_id="doubao",
        name="豆包（字节跳动）",
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        base_url_pattern="ark.cn-beijing.volces.com",
        cache_mode="auto",
        requires_header=False,
        min_cache_tokens=1024,
        cached_token_field="prompt_tokens_details.cached_tokens",
    ),
}


def resolve_cache_provider(base_url: str | None) -> CacheProviderConfig | None:
    """Identify the provider by base_url substring matching."""
    if not base_url:
        return None
    for provider in CACHE_PROVIDERS.values():
        if provider.base_url_pattern in base_url:
            return provider
    return None


def get_cache_kwargs(base_url: str | None) -> dict[str, Any]:
    """Return the cache-related kwargs needed when constructing ChatOpenAI.

    Returns:
        {"default_headers": dict, "enable_cache_control": bool}
    """
    provider = resolve_cache_provider(base_url)
    if provider and provider.requires_header:
        return {
            "default_headers": {provider.header_name: provider.header_value},
            "enable_cache_control": True,
        }
    return {
        "default_headers": {},
        "enable_cache_control": False,
    }
