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

import copy
from dataclasses import dataclass, field
from typing import Any


# ────────────────────────────────────────────────────────────────
# Extra-body constants & mappings (formerly config/__init__.py).
# ────────────────────────────────────────────────────────────────

# Each provider's thinking settings come in pairs: above is default (thinking off, used by MODELS_EXTRA_BODY_MAP),
# below enables focused cognition (_THINKING suffix, consumed by focus_extra_body). Pair by each API's own
# semantics rather than mechanically flipping booleans; see _THINKING_ENABLE_FORM and focus_extra_body.
EXTRA_BODY_OPENAI = {"enable_thinking": False}
EXTRA_BODY_OPENAI_THINKING = {"enable_thinking": True}

# Warning: OPENAI above means OpenAI-compatible shape: enable_thinking is DashScope /
# Silicon-compatible gateway dialect, unrecognized by OpenAI itself. The next two groups are native api.openai.com forms.
# Observed (2026-08-07, Chat Completions):
#   - Nested {"reasoning": {"effort": ...}} belongs to OpenRouter / Responses API; Chat
#     Completions returns 400 Unknown parameter: 'reasoning'; native accepts top-level reasoning_effort only.
#   - Effort levels vary by model; there is no universal minimum:
#       gpt-5.6-luna / gpt-5.6-terra → none / low / medium / high / xhigh (no minimal).
#       gpt-5-nano                   → minimal / low / medium / high (no none).
#     Thus thinking-off requires two constant groups; focused cognition uses low for both, supported by both families.
EXTRA_BODY_OPENAI_NATIVE = {"reasoning_effort": "none"}
EXTRA_BODY_OPENAI_NATIVE_THINKING = {"reasoning_effort": "low"}

EXTRA_BODY_OPENAI_NATIVE_MINIMAL = {"reasoning_effort": "minimal"}
EXTRA_BODY_OPENAI_NATIVE_MINIMAL_THINKING = {"reasoning_effort": "low"}

EXTRA_BODY_CLAUDE = {"thinking": {"type": "disabled"}}
EXTRA_BODY_CLAUDE_THINKING = {"thinking": {"type": "enabled"}}

# Native Anthropic claude (thinking passed through OpenAI compatibility) differs from GLM/Kimi/Doubao's
# thinking.type dialect: Opus 4.7+ removed {type:enabled,budget_tokens}, returning
# 400; enabling requires {type:adaptive}. Opus 4.6/Sonnet 4.6 also use adaptive; Haiku 4.5
# still needs individual OpenAI-compat verification. This PR uses only disabled (ordinary thinking off) and
# intentionally has no enable pair: focused cognition keeps claude thinking off (safe degradation without 400),
# leaving correct per-model adaptive integration for follow-up.
# Haiku 5.5 still accepts disabled (effort ≤ high only; default medium; project sends no effort).
EXTRA_BODY_ANTHROPIC = {"thinking": {"type": "disabled"}}
# Since Sonnet 5.5, {type:disabled} returns 400; thinking off requires {type:between_tools} (also
# effort ≤ high only, default high; no display / budget_tokens or other fields).
# Opus 5.5 / Fable 5.1 rejects any explicit thinking except adaptive with 400; thinking cannot be disabled,
# so do not register them (get_extra_body → {} → omit thinking, using native adaptive).
EXTRA_BODY_ANTHROPIC_BETWEEN_TOOLS = {"thinking": {"type": "between_tools"}}

EXTRA_BODY_GEMINI = {"extra_body": {"google": {"thinking_config": {"thinking_budget": 0}}}}
# Gemini 2.5: budget 0 disables; focused cognition gets fixed low 800-token budget (thinking without deep reasoning).
EXTRA_BODY_GEMINI_THINKING = {"extra_body": {"google": {"thinking_config": {"thinking_budget": 800}}}}

EXTRA_BODY_GEMINI_3 = {"extra_body": {"google": {"thinking_config": {"thinking_level": "low", "include_thoughts": False}}}}
# Gemini 3: keep lowest thinking level (low), exposing thoughts separately without mixing into content.
EXTRA_BODY_GEMINI_3_THINKING = {"extra_body": {"google": {"thinking_config": {"thinking_level": "low", "include_thoughts": True}}}}

EXTRA_BODY_OPENROUTER = {"reasoning": {"effort": "none"}}
# OpenRouter: effort none→low (enable thinking at minimum effort).
EXTRA_BODY_OPENROUTER_THINKING = {"reasoning": {"effort": "low"}}

# OpenRouter Gemini 3.5+ cannot disable thinking; effort=none returns 400.
# "Reasoning is mandatory for this endpoint and cannot be disabled."。
# Observed minimal uses 0 thinking tokens, replacing none; focused cognition still switches to low.
# Observed (2026-10-07): google/gemini-3.5-flash / 3.6-flash / 3.8-flash reject none;
# 3.1-flash-lite / 3-flash-preview still accept none, remaining in EXTRA_BODY_OPENROUTER.
EXTRA_BODY_OPENROUTER_MINIMAL = {"reasoning": {"effort": "minimal"}}

# MiniMax reasoning_split controls only thinking output format, not enablement; M2.x always
# reasons internally and cannot disable it. True puts thoughts in reasoning_details; False/omitted embeds <think>
# tags in content. Focused cognition retains True (omitted from _THINKING_ENABLE_FORM means no change):
# thinking already stays on; True keeps CoT separate from content so TTS does not read it as dialogue
# (protecting against the same issue as leaks_thinking_in_content).
# M3 supports thinking on/off (thinking.type=disabled/adaptive), so mappings below use
# EXTRA_BODY_CLAUDE for M3, switching to enabled for focused cognition rather than this constant.
# Documentation: https://platform.minimax.io/docs/guides/text-m3-function-call
#      https://platform.minimax.io/docs/api-reference/text-openai-api
EXTRA_BODY_MINIMAX = {"reasoning_split": True}

# Step reasoning_effort does not support none: three-level models accept low / medium / high,
# while step-3.5-flash-2603 accepts low / high only. Flash family and step-5-preview cannot disable
# thinking; use minimum low both normally and for focused cognition, replacing none. Omit from
# _THINKING_ENABLE_FORM (no focused-cognition switch).
# Documentation: https://platform.stepfun.ai/docs/en/api-reference/chat/chat-completion-create
EXTRA_BODY_STEP_LOW = {"reasoning_effort": "low"}

# Unified agent-call switch: whether to load extra_body.
# Enabled by default; with MODELS_EXTRA_BODY_MAP, disables thinking by default.
AGENT_USE_EXTRA_BODY = True

# Model-to-extra_body mapping.
MODELS_EXTRA_BODY_MAP: dict[str, dict] = {
    # Native OpenAI family uses top-level reasoning_effort; do not use EXTRA_BODY_OPENAI /
    # EXTRA_BODY_OPENROUTER: both shapes return 400 (see observations beside constants).
    "gpt-5.6-luna": EXTRA_BODY_OPENAI_NATIVE,
    "gpt-5.6-terra": EXTRA_BODY_OPENAI_NATIVE,
    "gpt-5.6-sol": EXTRA_BODY_OPENAI_NATIVE,
    "gpt-5-nano": EXTRA_BODY_OPENAI_NATIVE_MINIMAL,
    # Qwen family.
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
    "qwen3.7-max": EXTRA_BODY_OPENAI,
    "qwen3.7-flash": EXTRA_BODY_OPENAI,
    "qwen3.7-flash-2026-07-15": EXTRA_BODY_OPENAI,
    "qwen3.8-flash": EXTRA_BODY_OPENAI,
    # Non-realtime omni models (realtime has -realtime suffix and WebSocket, bypassing this table):
    # same protocol as ordinary text models with additional audio/video input and text-only output; thinking defaults on,
    # and enable_thinking=False works (observed 2026-10-07).
    "qwen3.8-omni-flash": EXTRA_BODY_OPENAI,
    # GLM family.
    "glm-4.5-air": EXTRA_BODY_CLAUDE,
    "glm-4.6v-flash": EXTRA_BODY_CLAUDE,
    "glm-4.7-flash": EXTRA_BODY_CLAUDE,
    "glm-4.6v": EXTRA_BODY_CLAUDE,
    "glm-5v-turbo": EXTRA_BODY_CLAUDE,
    "glm-5.1": EXTRA_BODY_CLAUDE,
    "glm-5.2": EXTRA_BODY_CLAUDE,
    # Kimi family.
    "kimi-k2-0905-preview": EXTRA_BODY_CLAUDE,
    "kimi-k2.5": EXTRA_BODY_CLAUDE,
    "kimi-k2.6": EXTRA_BODY_CLAUDE,
    "kimi-k3": EXTRA_BODY_CLAUDE,
    # MiniMax family.
    "MiniMax-M2.5": EXTRA_BODY_MINIMAX,
    "MiniMax-M2.7": EXTRA_BODY_MINIMAX,
    "MiniMax-M3": EXTRA_BODY_CLAUDE,
    "MiniMax-Text-01": EXTRA_BODY_MINIMAX,
    # Silicon
    "zai-org/GLM-4.6V": EXTRA_BODY_OPENAI,
    "deepseek-ai/DeepSeek-V3.2": EXTRA_BODY_OPENAI,
    "deepseek-ai/DeepSeek-V4-Flash": EXTRA_BODY_OPENAI,
    "Qwen/Qwen3.5-397B-A17B": EXTRA_BODY_OPENAI,
    "Qwen/Qwen3.5-122B-A10B": EXTRA_BODY_OPENAI,
    # Official DeepSeek (api.deepseek.com): V4 defaults thinking on, using thinking.type dialect,
    # matching GLM/Kimi/Doubao, so reuse EXTRA_BODY_CLAUDE. Reseller gateways use different
    # model keys (SiliconFlow deepseek-ai/…, OpenRouter deepseek/…) and their own dialects,
    # unaffected here. vision-exp is the same-generation visual model on the same endpoint, with identical dialect.
    # From V4.1, official call name is deepseek-flash (vision included); v4-flash / vision-exp
    # remain accepted and route server-side to V4.1 Flash. Pro has no 4.1 version and remains deepseek-v4-pro.
    # Documentation: https://api-docs.deepseek.com/quick_start/pricing
    "deepseek-flash": EXTRA_BODY_CLAUDE,
    "deepseek-v4-flash": EXTRA_BODY_CLAUDE,
    "deepseek-v4-flash-vision-exp": EXTRA_BODY_CLAUDE,
    "deepseek-v4-pro": EXTRA_BODY_CLAUDE,
    # Step
    "step-2-mini": {"tools": [{"type": "web_search", "function": {"description": "这个web_search用来搜索互联网的信息"}}]},
    # reasoning_effort lacks none; thinking cannot disable, so ordinary and focused modes both use low.
    "step-5-preview": EXTRA_BODY_STEP_LOW,
    "step-3.7-flash": EXTRA_BODY_STEP_LOW,
    "step-3.5-flash": EXTRA_BODY_STEP_LOW,
    "step-3.5-flash-2603": EXTRA_BODY_STEP_LOW,
    # Free service (lanlan.tech / lanlan.app, fixed free-model name) uses thinking.type style,
    # normally sending disabled; focus_extra_body flips it to enabled for focused cognition.
    "free-model": EXTRA_BODY_CLAUDE,
    # Claude family (native Anthropic: enabling requires adaptive; no switch in this PR, see EXTRA_BODY_ANTHROPIC).
    "claude-sonnet-5-5": EXTRA_BODY_ANTHROPIC_BETWEEN_TOOLS,
    "claude-haiku-5-5": EXTRA_BODY_ANTHROPIC,
    "claude-sonnet-5": EXTRA_BODY_ANTHROPIC,
    "claude-sonnet-4-6": EXTRA_BODY_ANTHROPIC,
    "claude-haiku-4-5-20251001": EXTRA_BODY_ANTHROPIC,
    "claude-opus-4-7": EXTRA_BODY_ANTHROPIC,
    "claude-opus-4-6": EXTRA_BODY_ANTHROPIC,
    # Doubao Seed 2.0 family.
    "doubao-seed-2-0-lite-260215": EXTRA_BODY_CLAUDE,
    "doubao-seed-2-0-mini-260215": EXTRA_BODY_CLAUDE,
    "doubao-seed-2-0-pro-260215": EXTRA_BODY_CLAUDE,
    "doubao-seed-2-0-lite-260428": EXTRA_BODY_CLAUDE,
    "doubao-seed-2-0-mini-260428": EXTRA_BODY_CLAUDE,
    # Gemini family.
    "gemini-2.5-flash": EXTRA_BODY_GEMINI,
    "gemini-2.5-flash-lite": EXTRA_BODY_GEMINI,
    "gemini-3-flash-preview": EXTRA_BODY_GEMINI_3,
    "gemini-3.1-flash-lite": EXTRA_BODY_GEMINI_3,
    "gemini-3.5-flash": EXTRA_BODY_GEMINI_3,
    "gemini-3.6-flash": EXTRA_BODY_GEMINI_3,
    "gemini-3.8-flash": EXTRA_BODY_GEMINI_3,
    # OpenRouter format (provider/model): unified reasoning parameter.
    "google/gemini-2.5-flash": EXTRA_BODY_OPENROUTER,
    "google/gemini-2.5-flash-lite": EXTRA_BODY_OPENROUTER,
    "google/gemini-3-flash-preview": EXTRA_BODY_OPENROUTER,
    "google/gemini-3.1-flash-lite": EXTRA_BODY_OPENROUTER,
    "google/gemini-3.5-flash": EXTRA_BODY_OPENROUTER_MINIMAL,
    "google/gemini-3.6-flash": EXTRA_BODY_OPENROUTER_MINIMAL,
    "google/gemini-3.8-flash": EXTRA_BODY_OPENROUTER_MINIMAL,
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


# Focused cognition (thinking on) converts each provider's thinking-off extra_body to its thinking-on form.
# Key = off-constant id; value = matching on-constant, paired by API semantics rather than boolean flipping.
# Unlisted off constants (e.g. MiniMax reasoning_split, Step reasoning_effort=low)
# retain their values under focused cognition; non-thinking extras (e.g. step-2-mini's
# web_search tools) naturally remain outside this table and fall back to original values in MODELS_FOCUS_EXTRA_BODY_MAP,
# preserved unchanged.
_THINKING_ENABLE_FORM: dict[int, dict] = {
    id(EXTRA_BODY_OPENAI): EXTRA_BODY_OPENAI_THINKING,
    id(EXTRA_BODY_OPENAI_NATIVE): EXTRA_BODY_OPENAI_NATIVE_THINKING,
    id(EXTRA_BODY_OPENAI_NATIVE_MINIMAL): EXTRA_BODY_OPENAI_NATIVE_MINIMAL_THINKING,
    id(EXTRA_BODY_CLAUDE): EXTRA_BODY_CLAUDE_THINKING,
    id(EXTRA_BODY_GEMINI): EXTRA_BODY_GEMINI_THINKING,
    id(EXTRA_BODY_GEMINI_3): EXTRA_BODY_GEMINI_3_THINKING,
    id(EXTRA_BODY_OPENROUTER): EXTRA_BODY_OPENROUTER_THINKING,
    id(EXTRA_BODY_OPENROUTER_MINIMAL): EXTRA_BODY_OPENROUTER_THINKING,
}

# model → focused-cognition extra_body, derived from MODELS_EXTRA_BODY_MAP with one shared model list
# to prevent drift: use paired on form if present, else original value (preserving web_search / MiniMax
# and Step low).
# Relies on MODELS_EXTRA_BODY_MAP values referencing module-level constants, allowing id pairing keys.
MODELS_FOCUS_EXTRA_BODY_MAP: dict[str, dict] = {
    model: _THINKING_ENABLE_FORM.get(id(body), body)
    for model, body in MODELS_EXTRA_BODY_MAP.items()
}


def focus_extra_body(model: str) -> dict | None:
    """extra_body for a Focus (thinking-on) turn.

    Regular turns send the provider's thinking-DISABLED extra_body (see
    ``MODELS_EXTRA_BODY_MAP``). A Focus turn flips each provider's thinking knob
    to its semantic ENABLED form — NOT by blindly dropping keys, but per the
    provider's own dialect (see ``_THINKING_ENABLE_FORM`` and the paired
    ``EXTRA_BODY_*_THINKING`` constants):
      - enable_thinking: False -> True                  (Qwen / Silicon / free OpenAI-shape)
      - thinking.type: disabled -> enabled              (GLM / Kimi / Doubao / Claude / free)
      - thinking_budget: 0 -> 800 (low fixed budget)    (Gemini 2.5)
      - thinking_level low (kept minimal), include_thoughts->True (Gemini 3)
      - reasoning.effort: none|minimal -> low           (OpenRouter; minimal
        for Gemini 3.5+ there, which rejects none)
      - reasoning_effort: none|minimal -> low           (OpenAI native; the
        floor differs per model, low is the one both generations accept)
      - reasoning_effort: low kept (cannot disable)     (Step flash / step-5-preview)

    Provider extras that are NOT thinking knobs (e.g. ``step-2-mini``'s built-in
    ``web_search`` tools, MiniMax's reasoning_split, or Step's always-low
    reasoning_effort) are preserved unchanged.
    Returns ``None`` when the model has no registered extra_body."""
    if not model:
        return None
    enabled = MODELS_FOCUS_EXTRA_BODY_MAP.get(model)
    # deepcopy: enabled is a shared module-level constant, possibly nested
    # (Gemini's thinking_config); a caller mutating the result must not poison
    # the registry. Cheap — focus_extra_body runs once per focus turn.
    return copy.deepcopy(enabled) if enabled else None


# Background memory calls intentionally enabling thinking (review, signal detection, refine, reflection synthesis, etc.) normally omit
# extra_body to use native reasoning, which often runs unbounded until output limits or timeout; therefore
# explicitly reduce effort per endpoint (CacheProviderConfig.memory_thinking_extra_body).
# Thinking controls use **endpoint** dialect rather than model dialect: the same deepseek-v4-flash accepts
# reasoning_effort officially, while Bailian recognizes only thinking_budget (reasoning_effort is ignored, increasing thinking
# to 6.4k–7.6k). Endpoint registration also naturally matches snapshots such as deepseek-v4-flash-0731.
# Observed (2026-10-07/08, same reasoning problem / signal-detection prompt):
#   - Official DeepSeek: default thinking 5.4k–6.5k; reasoning_effort=low reduces to 2.0k–2.4k
#     (minimal instead gives unstable 3.4k–4.2k, unused). Thinking defaults on; no need to send thinking again.
#   - Bailian (DashScope): thinking_budget works precisely for qwen3.7/3.8, omni, max, and hosted
#     DeepSeek; it has no effect or error on non-thinking models (qwen-plus), nor does it
#     enable thinking. Without parameters, qwen3.7-flash reaches 8192 thinking tokens and DeepSeek snapshots 12k.
#   - SiliconFlow: max_tokens caps content only; absent controls, thinking runs until request timeout. Require
#     enable_thinking + thinking_budget for a cap (thinking_budget alone disables DeepSeek
#     thinking). Non-thinking models (Ling-mini-2.0 / Qwen2.5-7B-Instruct /
#     GLM-4-9B-0414 / DeepSeek-V3) ignore enable_thinking without errors or reasoning;
#     tested hybrid models (Qwen3-8B, DeepSeek-V3.2 / V4-Flash, Qwen3.5) natively default thinking on,
#     so endpoint-level parameters do not enable previously disabled thinking (2026-10-08).
# Use the shared output-guard budget, reserving at least half of 8192 tokens for JSON content.
# Unregistered endpoints remain unchanged (None = omit extra_body = native thinking).
EXTRA_BODY_DEEPSEEK_MEMORY_THINKING = {"reasoning_effort": "low"}
EXTRA_BODY_DASHSCOPE_MEMORY_THINKING = {"thinking_budget": 4096}
EXTRA_BODY_SILICON_MEMORY_THINKING = {"enable_thinking": True, "thinking_budget": 4096}


def memory_thinking_extra_body(base_url: str | None, output_cap: int) -> dict | None:
    """extra_body for a memory call that deliberately keeps thinking on.

    Resolved per endpoint (see ``CacheProviderConfig.memory_thinking_extra_body``).
    ``None`` means "send no extra_body" (the model's native thinking), which is
    what those call sites did before. A ``thinking_budget`` is clamped to half
    of ``output_cap`` so the answer keeps room when the request falls back to
    a smaller cap on endpoints where the cap bounds thinking + answer."""
    provider = resolve_cache_provider(base_url)
    body = provider.memory_thinking_extra_body if provider is not None else None
    if not body:
        return None
    body = copy.deepcopy(body)
    if "thinking_budget" in body:
        body["thinking_budget"] = min(body["thinking_budget"], max(1, output_cap // 2))
    return body


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
    # qwen3.8 is deliberately absent: on DashScope it streams reasoning via
    # ``reasoning_content`` with a clean ``content`` (checked 2026-09-27), and
    # the stripper would hold a clean Focus answer until the end of the stream.
    # qwen3.8-omni-flash behaves the same with thinking on + streaming (checked
    # 2026-10-08: reasoning only in ``reasoning_content``, no think tags in content).
    return any(tag in m for tag in ("qwen3.5", "qwen3.6", "qwen3.7"))


# ────────────────────────────────────────────────────────────────
# Cache Provider configuration (formerly tests/test_cco_capacity.py PROVIDER_CACHE_CONFIG).
# ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CacheProviderConfig:
    """Context Cache behavior description for a single provider."""

    provider_id: str
    name: str
    base_url: str                   # Typical complete URL (tests/documentation).
    base_url_pattern: str           # Used for substring matching.
    cache_mode: str                 # "session" | "auto" | "upstream"
    requires_header: bool
    # Whether request body needs body-level cache markers (Anthropic-style
    # cache_control: {"type": "ephemeral"}). Independent of requires_header:
    # providers may need neither, either one, or both. get_cache_kwargs uses this
    # to pass enable_cache_control=True to ChatOpenAI, consumed by _params().
    #
    # Warning: never enable this for Anthropic's own OpenAI-compatible endpoint (api.anthropic.com via OpenAI
    # SDK; see utils.llm_client.create_chat_llm's anthropic branch): that compatibility
    # layer lacks prompt caching and silently ignores injected cache_control, reporting caching enabled
    # with zero hits. For body-level caching use native Messages API, or a gateway explicitly supporting
    # this OpenAI-wire cache_control shape (Anthropic-compat / OpenRouter, etc.) before enabling.
    requires_body_flag: bool = False
    header_name: str | None = None
    header_value: str | None = None
    min_cache_tokens: int = 1024
    cached_token_field: str = "prompt_tokens_details.cached_tokens"
    auto_cache: bool = True
    cache_price: float = 0.10
    creation_price: float = 0.10
    # Output-limit field for OpenAI-compatible requests; provider semantics differ between the two fields
    # (observed 2026-10-07, thinking enabled):
    #   - max_completion_tokens: new OpenAI specification caps thinking + content together.
    #     Official DeepSeek / GLM / SiliconFlow **silently ignore** it, rendering the cap ineffective.
    #   - max_tokens: legacy field. DeepSeek / GLM cap total output; DashScope, Doubao,
    #     SiliconFlow cap content only, leaving thinking unlimited; newer OpenAI models return 400.
    # Therefore default to max_completion_tokens, using max_tokens only for providers that reject/ignore it.
    # Never send both: Doubao / Gemini / OpenAI return 400.
    # Observed for both Qwen and DeepSeek on Bailian (DashScope): max_completion_tokens caps total output
    # (2026-10-08), so default is correct without registration.
    token_limit_field: str = "max_completion_tokens"
    # Memory-system thinking-enabled calls use this extra_body to reduce thinking (endpoint dialect; see
    # observations above memory_thinking_extra_body); None = native thinking.
    memory_thinking_extra_body: dict | None = None

    # Support dictionary-style config["xxx"] access in tests.
    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)


CACHE_PROVIDERS: dict[str, CacheProviderConfig] = {
    # qwen_intl / qwen_us must precede qwen: resolve_cache_provider matches substrings
    # in dict order, so regional domains must first match their own configuration.
    "qwen_intl": CacheProviderConfig(
        provider_id="qwen_intl",
        memory_thinking_extra_body=EXTRA_BODY_DASHSCOPE_MEMORY_THINKING,
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
        memory_thinking_extra_body=EXTRA_BODY_DASHSCOPE_MEMORY_THINKING,
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
        memory_thinking_extra_body=EXTRA_BODY_DASHSCOPE_MEMORY_THINKING,
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
        token_limit_field="max_tokens",
    ),
    # Zhipu international (Z.ai): same API as open.bigmodel.cn, also silently ignores
    # max_completion_tokens (observed 2026-10-08: glm-5.1 emitted 1,434 with cap 300).
    "glm_intl": CacheProviderConfig(
        provider_id="glm_intl",
        name="Z.ai GLM (Intl)",
        base_url="https://api.z.ai/api/paas/v4",
        base_url_pattern="api.z.ai",
        cache_mode="auto",
        requires_header=False,
        min_cache_tokens=1024,
        cached_token_field="cached_tokens",
        token_limit_field="max_tokens",
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
        memory_thinking_extra_body=EXTRA_BODY_SILICON_MEMORY_THINKING,
        name="硅基流动 Silicon",
        base_url="https://api.siliconflow.cn/v1",
        base_url_pattern="api.siliconflow.cn",
        cache_mode="upstream",
        requires_header=False,
        min_cache_tokens=1024,
        cached_token_field="prompt_cache_hit_tokens",
        token_limit_field="max_tokens",
    ),
    # SiliconFlow international: assume same implementation as domestic (untested, no available key).
    "silicon_intl": CacheProviderConfig(
        provider_id="silicon_intl",
        memory_thinking_extra_body=EXTRA_BODY_SILICON_MEMORY_THINKING,
        name="SiliconFlow (Intl)",
        base_url="https://api.siliconflow.com/v1",
        base_url_pattern="api.siliconflow.com",
        cache_mode="upstream",
        requires_header=False,
        min_cache_tokens=1024,
        cached_token_field="prompt_cache_hit_tokens",
        token_limit_field="max_tokens",
    ),
    "deepseek": CacheProviderConfig(
        provider_id="deepseek",
        memory_thinking_extra_body=EXTRA_BODY_DEEPSEEK_MEMORY_THINKING,
        name="DeepSeek",
        base_url="https://api.deepseek.com/v1",
        base_url_pattern="api.deepseek.com",
        cache_mode="auto",
        requires_header=False,
        min_cache_tokens=64,
        cached_token_field="prompt_cache_hit_tokens",
        token_limit_field="max_tokens",
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
        cached_token_field="prompt_tokens_details.cached_tokens",  # Same as OpenAI.
    ),
    "doubao": CacheProviderConfig(
        provider_id="doubao",
        name="豆包大模型(火山方舟)",
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

    Two *orthogonal* cache-engagement mechanisms, keyed off the resolved
    provider — a provider may need neither, either, or both:

      - ``requires_header`` → inject the provider's session-cache header
        (e.g. DashScope ``x-dashscope-session-cache: enable``). Header-only
        providers (qwen) DO NOT get ``enable_cache_control``; their caching is
        engaged entirely by the header.
      - ``requires_body_flag`` → set ``enable_cache_control`` so
        ``ChatOpenAI._params`` stamps an Anthropic-style
        ``cache_control: {"type": "ephemeral"}`` marker onto the cache
        breakpoint message (Anthropic direct / Anthropic-compat gateways).

    Returns:
        {"default_headers": dict, "enable_cache_control": bool}
    """
    provider = resolve_cache_provider(base_url)
    if provider is None:
        return {"default_headers": {}, "enable_cache_control": False}
    headers: dict[str, str] = {}
    if provider.requires_header and provider.header_name is not None:
        headers[provider.header_name] = provider.header_value or ""
    return {
        "default_headers": headers,
        "enable_cache_control": provider.requires_body_flag,
    }


def get_token_limit_field(base_url: str | None) -> str:
    """Return the output-cap field an OpenAI-compatible endpoint honours.

    Unknown endpoints keep ``max_completion_tokens`` (the OpenAI spec field);
    see ``CacheProviderConfig.token_limit_field`` for the providers that ignore
    it. Anthropic-protocol clients do not consult this."""
    provider = resolve_cache_provider(base_url)
    if provider is None:
        return "max_completion_tokens"
    return provider.token_limit_field
