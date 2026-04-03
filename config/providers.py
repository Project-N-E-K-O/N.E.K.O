# -*- coding: utf-8 -*-
"""Provider 统一注册表。

集中管理所有 LLM 供应商的：
  - extra_body 配置（禁用 thinking 等）
  - Context Cache 行为（header、token 字段、阈值）

其他模块通过本文件获取 provider 特化参数，而非各自硬编码。
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

# Agent 调用统一开关：是否加载 extra_body。
# 默认开启，配合 MODELS_EXTRA_BODY_MAP 实现默认关闭 thinking。
AGENT_USE_EXTRA_BODY = True

# 模型到 extra_body 的映射
MODELS_EXTRA_BODY_MAP: dict[str, dict] = {
    # Qwen 系列
    "qwen-flash": EXTRA_BODY_OPENAI,
    "qwen3-vl-plus-2025-09-23": EXTRA_BODY_OPENAI,
    "qwen3-vl-plus": EXTRA_BODY_OPENAI,
    "qwen3-vl-flash": EXTRA_BODY_OPENAI,
    "qwen3.5-plus": EXTRA_BODY_OPENAI,
    "qwen3.6-plus": EXTRA_BODY_OPENAI,
    "qwen-plus": EXTRA_BODY_OPENAI,
    "deepseek-ai/DeepSeek-V3.2": EXTRA_BODY_OPENAI,
    # GLM 系列
    "glm-4.5-air": EXTRA_BODY_CLAUDE,
    "glm-4.6v-flash": EXTRA_BODY_CLAUDE,
    "glm-4.7-flash": EXTRA_BODY_CLAUDE,
    "glm-4.6v": EXTRA_BODY_CLAUDE,
    # Silicon (zai-org) - 使用 Qwen 格式
    "zai-org/GLM-4.6V": EXTRA_BODY_OPENAI,
    # Step
    "step-2-mini": {"tools": [{"type": "web_search", "function": {"description": "这个web_search用来搜索互联网的信息"}}]},
    # Claude 系列
    "claude-sonnet-4-6": EXTRA_BODY_CLAUDE,
    "claude-haiku-4-5-20251001": EXTRA_BODY_CLAUDE,
    "claude-opus-4-6": EXTRA_BODY_CLAUDE,
    # Doubao Seed 2.0 系列
    "doubao-seed-2-0-lite-260215": EXTRA_BODY_CLAUDE,
    "doubao-seed-2-0-mini": EXTRA_BODY_CLAUDE,
    "doubao-seed-2-0-pro-260215": EXTRA_BODY_CLAUDE,
    # Gemini 系列
    "gemini-2.5-flash": EXTRA_BODY_GEMINI,
    "gemini-2.5-flash-lite": EXTRA_BODY_GEMINI,
    "gemini-3-flash-preview": EXTRA_BODY_GEMINI_3,
}


def get_extra_body(model: str) -> dict | None:
    """根据模型名称返回对应的 extra_body 配置。

    Returns:
        对应的 extra_body dict；模型不需要特殊配置时返回空 dict；
        model 为空时返回 None。
    """
    if not model:
        return None
    return MODELS_EXTRA_BODY_MAP.get(model, {})


def get_agent_extra_body(model: str) -> dict | None:
    """Return extra_body for Agent calls based on a single global switch."""
    if not AGENT_USE_EXTRA_BODY:
        return None
    return get_extra_body(model)


# ────────────────────────────────────────────────────────────────
# Cache Provider 配置（原 tests/test_cco_capacity.py PROVIDER_CACHE_CONFIG）
# ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CacheProviderConfig:
    """单个供应商的 Context Cache 行为描述。"""

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
}


def resolve_cache_provider(base_url: str | None) -> CacheProviderConfig | None:
    """通过 base_url substring 匹配识别 provider。"""
    if not base_url:
        return None
    for provider in CACHE_PROVIDERS.values():
        if provider.base_url_pattern in base_url:
            return provider
    return None


def get_cache_kwargs(base_url: str | None) -> dict[str, Any]:
    """返回构造 ChatOpenAI 时需要的 cache 相关参数。

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
