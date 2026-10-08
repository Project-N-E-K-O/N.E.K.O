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
# Extra-body 常量 & 映射（原 config/__init__.py）
# ────────────────────────────────────────────────────────────────

# 每个 provider 的 thinking 旋钮成对定义：上为默认（关思考，进 MODELS_EXTRA_BODY_MAP），
# 下为凝神启用形式（_THINKING 后缀，由 focus_extra_body 取用）。对偶按各家 API 的
# 自身语义，不是机械翻 bool —— 见 _THINKING_ENABLE_FORM 与 focus_extra_body。
EXTRA_BODY_OPENAI = {"enable_thinking": False}
EXTRA_BODY_OPENAI_THINKING = {"enable_thinking": True}

# ⚠️ 上面那个 OPENAI 指的是「OpenAI-compatible 形状」—— enable_thinking 是 DashScope /
# Silicon 这类兼容网关的方言，OpenAI 自己不认。下面这两组才是 api.openai.com 原生的。
# 实测（2026-08-07，Chat Completions）：
#   - 嵌套 {"reasoning": {"effort": ...}} 是 OpenRouter / Responses API 的形状，Chat
#     Completions 直接 400 Unknown parameter: 'reasoning'；原生只收顶层 reasoning_effort。
#   - 档位按模型分裂，没有一个全家通用的「最低档」：
#       gpt-5.6-luna / gpt-5.6-terra → none / low / medium / high / xhigh（无 minimal）
#       gpt-5-nano                   → minimal / low / medium / high（无 none）
#     所以关思考要分两组常量，凝神侧都收敛到 low（两家都支持）。
EXTRA_BODY_OPENAI_NATIVE = {"reasoning_effort": "none"}
EXTRA_BODY_OPENAI_NATIVE_THINKING = {"reasoning_effort": "low"}

EXTRA_BODY_OPENAI_NATIVE_MINIMAL = {"reasoning_effort": "minimal"}
EXTRA_BODY_OPENAI_NATIVE_MINIMAL_THINKING = {"reasoning_effort": "low"}

EXTRA_BODY_CLAUDE = {"thinking": {"type": "disabled"}}
EXTRA_BODY_CLAUDE_THINKING = {"thinking": {"type": "enabled"}}

# Anthropic 原生 claude（经 OpenAI-compat 透传 thinking）跟 GLM/Kimi/Doubao 的
# thinking.type 方言不一样：Opus 4.7+ 已移除 {type:enabled,budget_tokens}，发它直接
# 400，启用思考必须用 {type:adaptive}；Opus 4.6/Sonnet 4.6 也用 adaptive；Haiku 4.5
# 是否支持还要逐个验证 OpenAI-compat 行为。本 PR 暂只用 disabled（平时关思考），且
# 故意不配 enable 对偶 —— 凝神对 claude 保持 thinking-off（安全退化，绝不 400），
# adaptive 的正确 per-model 接入留 follow-up。
EXTRA_BODY_ANTHROPIC = {"thinking": {"type": "disabled"}}

EXTRA_BODY_GEMINI = {"extra_body": {"google": {"thinking_config": {"thinking_budget": 0}}}}
# Gemini 2.5: budget 0 = 关；凝神给一个低固定预算 800 token（开思考但不深思）。
EXTRA_BODY_GEMINI_THINKING = {"extra_body": {"google": {"thinking_config": {"thinking_budget": 800}}}}

EXTRA_BODY_GEMINI_3 = {"extra_body": {"google": {"thinking_config": {"thinking_level": "low", "include_thoughts": False}}}}
# Gemini 3: 思考档位保持最低(low)，只把思考过程透出(thoughts 走独立字段，不混进 content)。
EXTRA_BODY_GEMINI_3_THINKING = {"extra_body": {"google": {"thinking_config": {"thinking_level": "low", "include_thoughts": True}}}}

EXTRA_BODY_OPENROUTER = {"reasoning": {"effort": "none"}}
# OpenRouter: effort none→low（开思考但取最低努力档）。
EXTRA_BODY_OPENROUTER_THINKING = {"reasoning": {"effort": "low"}}

# OpenRouter 上的 Gemini 3.5 起思考不能关：发 effort=none 直接 400
# "Reasoning is mandatory for this endpoint and cannot be disabled."。
# minimal 实测思考 0 token，作为 none 的替代；凝神照样翻成 low。
# 实测（2026-10-07）：google/gemini-3.5-flash / 3.6-flash / 3.8-flash 拒 none；
# 3.1-flash-lite / 3-flash-preview 仍接受 none，留在 EXTRA_BODY_OPENROUTER。
EXTRA_BODY_OPENROUTER_MINIMAL = {"reasoning": {"effort": "minimal"}}
EXTRA_BODY_OPENROUTER_MINIMAL_THINKING = {"reasoning": {"effort": "low"}}

# MiniMax 的 reasoning_split 只控制思考的「输出格式」，不是 on/off 开关：M2.x 始终
# 内部推理、无法关闭；True=思考走独立 reasoning_details 字段，False/省略=思考以 <think>
# 标签嵌进 content。凝神保持 True（不收录进下方 _THINKING_ENABLE_FORM 即「不翻」）：
# 思考本就常开、无需动它；且 True 让 CoT 留在独立字段、不混进 content 被 TTS 当台词
# 念出（与 leaks_thinking_in_content 防的是同一类问题）。
# M3 可以关思考（thinking.type=disabled/adaptive），所以下方映射表里 M3 走
# EXTRA_BODY_CLAUDE，凝神翻成 enabled，不用本常量。
# 文档 https://platform.minimax.io/docs/guides/text-m3-function-call
#      https://platform.minimax.io/docs/api-reference/text-openai-api
EXTRA_BODY_MINIMAX = {"reasoning_split": True}

# Step 的 reasoning_effort 不支持 none：三档模型只收 low / medium / high，
# step-3.5-flash-2603 只收 low / high。flash 系列与 step-5-preview 无法关闭
# 思考，关思考与凝神都填最低档 low，作为 none 的替代。不收录进下方
# _THINKING_ENABLE_FORM（凝神不翻）。
# 文档 https://platform.stepfun.ai/docs/en/api-reference/chat/chat-completion-create
EXTRA_BODY_STEP_LOW = {"reasoning_effort": "low"}

# Agent 调用统一开关：是否加载 extra_body。
# 默认开启，配合 MODELS_EXTRA_BODY_MAP 实现默认关闭 thinking。
AGENT_USE_EXTRA_BODY = True

# 模型到 extra_body 的映射
MODELS_EXTRA_BODY_MAP: dict[str, dict] = {
    # OpenAI 原生系列 —— 走顶层 reasoning_effort，别拿 EXTRA_BODY_OPENAI /
    # EXTRA_BODY_OPENROUTER 来填，那两个形状都会被 400（见常量处的实测记录）。
    "gpt-5.6-luna": EXTRA_BODY_OPENAI_NATIVE,
    "gpt-5.6-terra": EXTRA_BODY_OPENAI_NATIVE,
    "gpt-5.6-sol": EXTRA_BODY_OPENAI_NATIVE,
    "gpt-5-nano": EXTRA_BODY_OPENAI_NATIVE_MINIMAL,
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
    "qwen3.7-max": EXTRA_BODY_OPENAI,
    "qwen3.7-flash": EXTRA_BODY_OPENAI,
    "qwen3.7-flash-2026-07-15": EXTRA_BODY_OPENAI,
    "qwen3.8-flash": EXTRA_BODY_OPENAI,
    # 全模态非实时版（实时版带 -realtime 后缀，走 WebSocket，不经这张表）：
    # 协议跟普通文本模型一样，只是输入多了音视频，输出只有文本；默认开思考，
    # enable_thinking=False 生效（实测 2026-10-07）。
    "qwen3.8-omni-flash": EXTRA_BODY_OPENAI,
    # GLM 系列
    "glm-4.5-air": EXTRA_BODY_CLAUDE,
    "glm-4.6v-flash": EXTRA_BODY_CLAUDE,
    "glm-4.7-flash": EXTRA_BODY_CLAUDE,
    "glm-4.6v": EXTRA_BODY_CLAUDE,
    "glm-5v-turbo": EXTRA_BODY_CLAUDE,
    "glm-5.1": EXTRA_BODY_CLAUDE,
    "glm-5.2": EXTRA_BODY_CLAUDE,
    # Kimi系列
    "kimi-k2-0905-preview": EXTRA_BODY_CLAUDE,
    "kimi-k2.5": EXTRA_BODY_CLAUDE,
    "kimi-k2.6": EXTRA_BODY_CLAUDE,
    "kimi-k3": EXTRA_BODY_CLAUDE,
    # MiniMax系列
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
    # DeepSeek 官方（api.deepseek.com）：V4 默认开思考，且用的是 thinking.type 方言，
    # 跟 GLM/Kimi/Doubao 同形状，直接复用 EXTRA_BODY_CLAUDE。转售同款的网关是另外的
    # 键名（SiliconFlow 的 deepseek-ai/…、OpenRouter 的 deepseek/…），各走各的方言，
    # 不会被这几行波及。vision-exp 是同端口同代的视觉版，方言一致。
    # V4.1 起官方调用名是 deepseek-flash（自带视觉）；v4-flash / vision-exp
    # 仍被接受，服务端转给 V4.1 Flash。Pro 没有 4.1 版，仍叫 deepseek-v4-pro。
    # 文档 https://api-docs.deepseek.com/quick_start/pricing
    "deepseek-flash": EXTRA_BODY_CLAUDE,
    "deepseek-v4-flash": EXTRA_BODY_CLAUDE,
    "deepseek-v4-flash-vision-exp": EXTRA_BODY_CLAUDE,
    "deepseek-v4-pro": EXTRA_BODY_CLAUDE,
    # Step
    "step-2-mini": {"tools": [{"type": "web_search", "function": {"description": "这个web_search用来搜索互联网的信息"}}]},
    # reasoning_effort 无 none，思考无法关闭，平时与凝神都走 low。
    "step-5-preview": EXTRA_BODY_STEP_LOW,
    "step-3.7-flash": EXTRA_BODY_STEP_LOW,
    "step-3.5-flash": EXTRA_BODY_STEP_LOW,
    "step-3.5-flash-2603": EXTRA_BODY_STEP_LOW,
    # 免费版（lanlan.tech / lanlan.app，模型名固定 free-model）：用 thinking.type 风格，
    # 平时下发 disabled、凝神由 focus_extra_body flip 成 enabled。
    "free-model": EXTRA_BODY_CLAUDE,
    # Claude 系列（Anthropic 原生：enable 须用 adaptive，本 PR 暂不翻，见 EXTRA_BODY_ANTHROPIC）
    "claude-sonnet-5": EXTRA_BODY_ANTHROPIC,
    "claude-sonnet-4-6": EXTRA_BODY_ANTHROPIC,
    "claude-haiku-4-5-20251001": EXTRA_BODY_ANTHROPIC,
    "claude-opus-4-7": EXTRA_BODY_ANTHROPIC,
    "claude-opus-4-6": EXTRA_BODY_ANTHROPIC,
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
    "gemini-3.1-flash-lite": EXTRA_BODY_GEMINI_3,
    "gemini-3.5-flash": EXTRA_BODY_GEMINI_3,
    "gemini-3.6-flash": EXTRA_BODY_GEMINI_3,
    "gemini-3.8-flash": EXTRA_BODY_GEMINI_3,
    # OpenRouter 格式 (provider/model) — OpenRouter 使用统一的 reasoning 参数
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


# 凝神（thinking-on）时把各 provider 的「关思考」extra_body 翻成「开思考」形式。
# 键 = 「关」常量的 id，值 = 对应「开」常量；按各家 API 语义一一对偶，不机械翻 bool。
# 未收录的「关」常量（如 MiniMax 的 reasoning_split、Step 的 reasoning_effort=low）
# 表示「凝神保持原值不翻」；非 thinking 的 provider extra（如 step-2-mini 的
# web_search tools）天然不在此表，在 MODELS_FOCUS_EXTRA_BODY_MAP 里回退为原值、
# 原样保留。
_THINKING_ENABLE_FORM: dict[int, dict] = {
    id(EXTRA_BODY_OPENAI): EXTRA_BODY_OPENAI_THINKING,
    id(EXTRA_BODY_OPENAI_NATIVE): EXTRA_BODY_OPENAI_NATIVE_THINKING,
    id(EXTRA_BODY_OPENAI_NATIVE_MINIMAL): EXTRA_BODY_OPENAI_NATIVE_MINIMAL_THINKING,
    id(EXTRA_BODY_CLAUDE): EXTRA_BODY_CLAUDE_THINKING,
    id(EXTRA_BODY_GEMINI): EXTRA_BODY_GEMINI_THINKING,
    id(EXTRA_BODY_GEMINI_3): EXTRA_BODY_GEMINI_3_THINKING,
    id(EXTRA_BODY_OPENROUTER): EXTRA_BODY_OPENROUTER_THINKING,
    id(EXTRA_BODY_OPENROUTER_MINIMAL): EXTRA_BODY_OPENROUTER_MINIMAL_THINKING,
}

# model → 凝神 extra_body，与 MODELS_EXTRA_BODY_MAP 同源派生（共用 model 列表，不会
# 漂移）：命中对偶则取「开」形式，否则回退原值（保留 web_search / 不翻 MiniMax
# 与 Step low）。
# 依赖 MODELS_EXTRA_BODY_MAP 的值是模块级常量引用，故可用 id 做配对键。
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


# 记忆系统里有意开思考的后台调用（审阅、信号检测、refine、反思合成…）默认不发
# extra_body，让模型走原生思考。下面这些模型的原生思考没有边：输出上限压不住，
# 或者思考量大到撞超时，所以显式压一档。实测（2026-10-07，同一条信号检测提示词）：
#   - DeepSeek 官方：默认思考 5.4k~6.5k token；reasoning_effort=low 降到 2.0k~2.4k
#     （minimal 反而 3.4k~4.2k，不稳定，不用）。思考默认就开，不必再发 thinking。
#   - 硅基流动：max_tokens 只封正文，不发参数时思考跑到请求超时；必须
#     enable_thinking + thinking_budget 才封得住（只发 thinking_budget 会让 DeepSeek
#     系直接不思考）。预算取共享输出护栏同值。
# 不在表里的模型保持原样（返回 None = 不发 extra_body = 原生思考）。
EXTRA_BODY_DEEPSEEK_MEMORY_THINKING = {"reasoning_effort": "low"}
EXTRA_BODY_SILICON_MEMORY_THINKING = {"enable_thinking": True, "thinking_budget": 4096}

MODELS_MEMORY_THINKING_EXTRA_BODY: dict[str, dict] = {
    "deepseek-flash": EXTRA_BODY_DEEPSEEK_MEMORY_THINKING,
    "deepseek-v4-flash": EXTRA_BODY_DEEPSEEK_MEMORY_THINKING,
    "deepseek-v4-flash-vision-exp": EXTRA_BODY_DEEPSEEK_MEMORY_THINKING,
    "deepseek-v4-pro": EXTRA_BODY_DEEPSEEK_MEMORY_THINKING,
    "deepseek-ai/DeepSeek-V3.2": EXTRA_BODY_SILICON_MEMORY_THINKING,
    "deepseek-ai/DeepSeek-V4-Flash": EXTRA_BODY_SILICON_MEMORY_THINKING,
    "Qwen/Qwen3.5-397B-A17B": EXTRA_BODY_SILICON_MEMORY_THINKING,
    "Qwen/Qwen3.5-122B-A10B": EXTRA_BODY_SILICON_MEMORY_THINKING,
}


def memory_thinking_extra_body(model: str) -> dict | None:
    """extra_body for a memory call that deliberately keeps thinking on.

    ``None`` means "send no extra_body" (the model's native thinking), which is
    what those call sites did before; only models whose native thinking cannot
    be bounded by the output cap get an explicit, lower-effort form here."""
    body = MODELS_MEMORY_THINKING_EXTRA_BODY.get(model or "")
    return copy.deepcopy(body) if body else None


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
    # 是否需要在请求体里打 body 级缓存标记（Anthropic 风格
    # cache_control: {"type": "ephemeral"}）。与 requires_header 正交：
    # provider 可以两个都不要、只要其一、或两个都要。get_cache_kwargs 据此
    # 决定是否给 ChatOpenAI 传 enable_cache_control=True，由 _params() 消费。
    #
    # ⚠️ 切勿给 Anthropic 自家的 OpenAI 兼容端点（api.anthropic.com 走 OpenAI
    # SDK，见 utils.llm_client.create_chat_llm 的 anthropic 分支）置 True：该兼容
    # 层不支持 prompt caching，注入的 cache_control 会被静默忽略，结果是"报告开了
    # 缓存却零命中"。需要 body 级缓存时，走原生 Messages API，或换一个明确支持该
    # OpenAI-wire cache_control 形态的网关（Anthropic-compat / OpenRouter 等）再开。
    requires_body_flag: bool = False
    header_name: str | None = None
    header_value: str | None = None
    min_cache_tokens: int = 1024
    cached_token_field: str = "prompt_tokens_details.cached_tokens"
    auto_cache: bool = True
    cache_price: float = 0.10
    creation_price: float = 0.10
    # OpenAI 兼容请求里输出上限用哪个字段。各家对两个字段的语义不一致
    # （实测 2026-10-07，开思考）：
    #   - max_completion_tokens：OpenAI 新规范，封「思考 + 正文」总量。
    #     DeepSeek 官方 / GLM 官方 / 硅基流动不认，**静默忽略**，上限形同虚设。
    #   - max_tokens：老字段。DeepSeek / GLM 当总量上限用；DashScope、Doubao、
    #     硅基只拿它封正文，思考不受限；OpenAI 新模型直接 400。
    # 所以默认留在 max_completion_tokens，只有不认它的厂商改发 max_tokens。
    # 两个字段不能同时发：Doubao / Gemini / OpenAI 会 400。
    token_limit_field: str = "max_completion_tokens"

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
        token_limit_field="max_tokens",
    ),
    # 智谱国际站（Z.ai）：与 open.bigmodel.cn 同一套 API，同样静默忽略
    # max_completion_tokens（实测 2026-10-08，glm-5.1 上限 300 实出 1,434）。
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
        name="硅基流动 Silicon",
        base_url="https://api.siliconflow.cn/v1",
        base_url_pattern="api.siliconflow.cn",
        cache_mode="upstream",
        requires_header=False,
        min_cache_tokens=1024,
        cached_token_field="prompt_cache_hit_tokens",
        token_limit_field="max_tokens",
    ),
    "deepseek": CacheProviderConfig(
        provider_id="deepseek",
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
        cached_token_field="prompt_tokens_details.cached_tokens",  # 与 OpenAI 相同
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
