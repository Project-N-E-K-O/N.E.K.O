# -*- coding: utf-8 -*-
"""Output caps for thinking-on memory calls and the providers that ignore them.

Live probes (2026-10-07) behind these tests:
  * DeepSeek / GLM / SiliconFlow silently ignore ``max_completion_tokens``;
    only ``max_tokens`` caps their output.
  * Thinking models exhaust the shared 4096 guard before writing the JSON
    answer (#3319), so thinking-on memory calls ask for 8192 and fall back
    once when an endpoint rejects it.
"""
from __future__ import annotations

import ast
import pathlib
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


# ── provider registry ─────────────────────────────────────────────


@pytest.mark.parametrize("endpoint,field", [
    ("https://api.deepseek.com/v1", "max_tokens"),
    ("https://open.bigmodel.cn/api/paas/v4", "max_tokens"),
    ("https://api.z.ai/api/paas/v4", "max_tokens"),
    ("https://api.siliconflow.cn/v1", "max_tokens"),
    ("https://api.siliconflow.com/v1", "max_tokens"),
    ("https://dashscope.aliyuncs.com/compatible-mode/v1", "max_completion_tokens"),
    ("https://ark.cn-beijing.volces.com/api/v3", "max_completion_tokens"),
    ("https://api.openai.com/v1", "max_completion_tokens"),
    ("https://llm.example.com/v1", "max_completion_tokens"),
    (None, "max_completion_tokens"),
])
def test_token_limit_field_per_host(endpoint, field):
    from config.providers import get_token_limit_field

    assert get_token_limit_field(endpoint) == field


def _params_for(base_url: str) -> dict:
    from utils.llm_client import ChatOpenAI

    client = ChatOpenAI(
        model="any-model", base_url=base_url, api_key="sk-test",
        max_completion_tokens=321,
    )
    return client._params([{"role": "user", "content": "hi"}])


def test_client_sends_max_tokens_to_hosts_that_ignore_completion_cap():
    params = _params_for("https://api.deepseek.com/v1")

    assert params["max_tokens"] == 321
    assert "max_completion_tokens" not in params


def test_client_keeps_completion_cap_for_other_hosts():
    params = _params_for("https://dashscope.aliyuncs.com/compatible-mode/v1")

    assert params["max_completion_tokens"] == 321
    assert "max_tokens" not in params


def test_client_per_call_override_follows_host_field():
    from utils.llm_client import ChatOpenAI

    client = ChatOpenAI(
        model="glm-5.1", base_url="https://open.bigmodel.cn/api/paas/v4",
        api_key="sk-test", max_completion_tokens=100,
    )
    params = client._params(
        [{"role": "user", "content": "hi"}], max_completion_tokens=900,
    )

    assert params["max_tokens"] == 900
    assert "max_completion_tokens" not in params


def test_new_models_use_existing_dialect_constants():
    from config import providers as P

    # identity, not equality: the focus pairing is keyed by id().
    assert P.MODELS_EXTRA_BODY_MAP["qwen3.8-omni-flash"] is P.EXTRA_BODY_OPENAI
    assert P.MODELS_EXTRA_BODY_MAP["gemini-3.6-flash"] is P.EXTRA_BODY_GEMINI_3
    assert P.MODELS_EXTRA_BODY_MAP["gemini-3.8-flash"] is P.EXTRA_BODY_GEMINI_3
    for model in (
        "google/gemini-3.5-flash", "google/gemini-3.6-flash", "google/gemini-3.8-flash",
    ):
        assert P.MODELS_EXTRA_BODY_MAP[model] is P.EXTRA_BODY_OPENROUTER_MINIMAL
        assert P.MODELS_FOCUS_EXTRA_BODY_MAP[model] is P.EXTRA_BODY_OPENROUTER_THINKING
    # OpenRouter Gemini models that still accept effort=none keep it.
    assert P.MODELS_EXTRA_BODY_MAP["google/gemini-3.1-flash-lite"] is P.EXTRA_BODY_OPENROUTER


def test_omni_streams_thinking_outside_content():
    """Focus turns on qwen3.8-omni-flash need no stream stripper (reasoning_content only)."""
    from config.providers import leaks_thinking_in_content

    assert leaks_thinking_in_content("qwen3.8-omni-flash") is False


@pytest.mark.parametrize("endpoint,form", [
    ("https://api.deepseek.com/v1", "EXTRA_BODY_DEEPSEEK_MEMORY_THINKING"),
    # Same DeepSeek models on DashScope ignore reasoning_effort; only
    # thinking_budget bounds them (and qwen) there.
    ("https://dashscope.aliyuncs.com/compatible-mode/v1", "EXTRA_BODY_DASHSCOPE_MEMORY_THINKING"),
    ("https://dashscope-intl.aliyuncs.com/compatible-mode/v1", "EXTRA_BODY_DASHSCOPE_MEMORY_THINKING"),
    ("https://dashscope-us.aliyuncs.com/compatible-mode/v1", "EXTRA_BODY_DASHSCOPE_MEMORY_THINKING"),
    ("https://api.siliconflow.cn/v1", "EXTRA_BODY_SILICON_MEMORY_THINKING"),
    ("https://api.siliconflow.com/v1", "EXTRA_BODY_SILICON_MEMORY_THINKING"),
    ("https://open.bigmodel.cn/api/paas/v4", None),
    ("https://llm.example.com/v1", None),
    (None, None),
])
def test_memory_thinking_extra_body_is_endpoint_dialect(endpoint, form):
    from config import providers as P

    expected = getattr(P, form) if form else None
    assert P.memory_thinking_extra_body(endpoint) == expected


def test_memory_thinking_budgets_match_shared_guard_and_are_copies():
    from config import LLM_OUTPUT_GUARD_MAX_TOKENS
    from config import providers as P

    # Budgets are documented as "same as the shared guard".
    assert P.EXTRA_BODY_SILICON_MEMORY_THINKING["thinking_budget"] == LLM_OUTPUT_GUARD_MAX_TOKENS
    assert P.EXTRA_BODY_DASHSCOPE_MEMORY_THINKING["thinking_budget"] == LLM_OUTPUT_GUARD_MAX_TOKENS

    body = P.memory_thinking_extra_body("https://api.deepseek.com/v1")
    body["reasoning_effort"] = "high"
    assert P.EXTRA_BODY_DEEPSEEK_MEMORY_THINKING == {"reasoning_effort": "low"}


# ── memory.thinking_llm ───────────────────────────────────────────


class _CapRejected(Exception):
    status_code = 400

    def __str__(self) -> str:
        return "Error code: 400 - max_tokens must be <= 4096"


def _api_config(
    model: str = "qwen3.8-flash", base_url: str = "https://llm.example.com/v1",
) -> dict:
    return {
        "model": model, "base_url": base_url,
        "api_key": "sk-test", "provider_type": "openai",
    }


def _fake_llm(result):
    llm = MagicMock()

    async def _ainvoke(*_a, **_k):
        if isinstance(result, BaseException):
            raise result
        return result

    async def _aclose():
        return None

    llm.ainvoke = _ainvoke
    llm.aclose = _aclose
    return llm


def _response(content: str = '{"ok": true}', **metadata):
    return SimpleNamespace(content=content, response_metadata=metadata)


@pytest.mark.asyncio
async def test_ainvoke_thinking_requests_thinking_cap_and_bounded_extra_body():
    from config import MEMORY_THINKING_OUTPUT_MAX_TOKENS
    from config.providers import EXTRA_BODY_DASHSCOPE_MEMORY_THINKING
    from memory.thinking_llm import ainvoke_thinking

    response = _response()
    with patch("utils.llm_client.create_chat_llm", return_value=_fake_llm(response)) as factory:
        got, cap = await ainvoke_thinking(
            _api_config("deepseek-v4-flash-0731", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
            "prompt", timeout=90, call_label="t",
        )

    assert got is response
    assert cap == MEMORY_THINKING_OUTPUT_MAX_TOKENS
    kwargs = factory.call_args.kwargs
    assert kwargs["max_completion_tokens"] == MEMORY_THINKING_OUTPUT_MAX_TOKENS
    # A dated DashScope snapshot still gets the endpoint's thinking budget.
    assert kwargs["extra_body"] == EXTRA_BODY_DASHSCOPE_MEMORY_THINKING
    assert kwargs["timeout"] == 90


@pytest.mark.asyncio
async def test_ainvoke_thinking_keeps_native_thinking_for_other_models():
    from memory.thinking_llm import ainvoke_thinking

    with patch("utils.llm_client.create_chat_llm", return_value=_fake_llm(_response())) as factory:
        await ainvoke_thinking(_api_config(), "prompt", timeout=60, call_label="t")

    # extra_body=None overrides the factory's thinking-off default.
    assert "extra_body" in factory.call_args.kwargs
    assert factory.call_args.kwargs["extra_body"] is None


@pytest.mark.asyncio
async def test_ainvoke_thinking_retries_once_at_shared_guard_when_cap_rejected():
    from config import LLM_OUTPUT_GUARD_MAX_TOKENS, MEMORY_THINKING_OUTPUT_MAX_TOKENS
    from memory.thinking_llm import ainvoke_thinking

    response = _response()
    llms = [_fake_llm(_CapRejected()), _fake_llm(response)]
    with patch("utils.llm_client.create_chat_llm", side_effect=llms) as factory:
        got, cap = await ainvoke_thinking(_api_config(), "prompt", timeout=60, call_label="t")

    assert got is response
    assert cap == LLM_OUTPUT_GUARD_MAX_TOKENS
    assert [c.kwargs["max_completion_tokens"] for c in factory.call_args_list] == [
        MEMORY_THINKING_OUTPUT_MAX_TOKENS, LLM_OUTPUT_GUARD_MAX_TOKENS,
    ]


@pytest.mark.asyncio
async def test_ainvoke_thinking_propagates_unrelated_errors_without_retry():
    from memory.thinking_llm import ainvoke_thinking

    class _Other(Exception):
        status_code = 400

    with patch("utils.llm_client.create_chat_llm", return_value=_fake_llm(_Other("bad prompt"))) as factory:
        with pytest.raises(_Other):
            await ainvoke_thinking(_api_config(), "prompt", timeout=60, call_label="t")

    assert factory.call_count == 1


@pytest.mark.asyncio
async def test_ainvoke_thinking_close_failure_does_not_mask_response():
    from memory.thinking_llm import ainvoke_thinking

    response = _response()
    llm = _fake_llm(response)

    async def _boom():
        raise RuntimeError("close boom")

    llm.aclose = _boom
    with patch("utils.llm_client.create_chat_llm", return_value=llm):
        got, _ = await ainvoke_thinking(_api_config(), "prompt", timeout=60, call_label="t")

    assert got is response


@pytest.mark.asyncio
async def test_ainvoke_thinking_logs_exhausted_output():
    from config import MEMORY_THINKING_OUTPUT_MAX_TOKENS
    from memory import thinking_llm

    response = _response(
        "", finish_reason="length",
        token_usage={"completion_tokens": MEMORY_THINKING_OUTPUT_MAX_TOKENS},
    )
    with patch("utils.llm_client.create_chat_llm", return_value=_fake_llm(response)), \
         patch.object(thinking_llm.logger, "warning") as warning:
        got, _ = await thinking_llm.ainvoke_thinking(
            _api_config(), "prompt", timeout=60, call_label="小天 memory_signal_detection",
        )

    assert got is response
    assert warning.call_count == 1
    message = warning.call_args.args[0]
    assert "memory_signal_detection" in message
    assert "finish_reason='length'" in message


# ── call sites ────────────────────────────────────────────────────


def _extra_body_none_sites() -> list[str]:
    hits = []
    for path in sorted((REPO_ROOT / "memory").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for kw in node.keywords:
                if (
                    kw.arg == "extra_body"
                    and isinstance(kw.value, ast.Constant)
                    and kw.value.value is None
                ):
                    hits.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
    return hits


def test_memory_thinking_calls_go_through_shared_budget():
    """A literal ``extra_body=None`` in memory/ is a thinking-on call that
    bypasses ``memory.thinking_llm`` and is back under the 4096 guard."""
    assert _extra_body_none_sites() == []


@pytest.mark.asyncio
async def test_retry_helper_thinking_path_uses_thinking_cap():
    from config import MEMORY_THINKING_OUTPUT_MAX_TOKENS
    from memory.facts import FactStore

    fs = object.__new__(FactStore)
    fs._config_manager = MagicMock()

    async def _cfg(_tier, **_kw):
        return _api_config()

    fs._config_manager.aget_model_api_config = _cfg
    with patch("utils.llm_client.create_chat_llm", return_value=_fake_llm(_response('{"signals": []}'))) as factory:
        parsed = await fs._allm_call_with_retries(
            "prompt", "小天", tier="summary", call_type="memory_signal_detection",
            timeout=90, thinking=True,
        )

    assert parsed == {"signals": []}
    assert factory.call_args.kwargs["max_completion_tokens"] == MEMORY_THINKING_OUTPUT_MAX_TOKENS
    assert factory.call_args.kwargs["extra_body"] is None


@pytest.mark.asyncio
async def test_retry_helper_default_path_keeps_shared_guard_and_factory_dialect():
    from config import LLM_OUTPUT_GUARD_MAX_TOKENS
    from memory.facts import FactStore

    fs = object.__new__(FactStore)
    fs._config_manager = MagicMock()

    async def _cfg(_tier, **_kw):
        return _api_config()

    fs._config_manager.aget_model_api_config = _cfg
    with patch("utils.llm_client.create_chat_llm", return_value=_fake_llm(_response("[]"))) as factory:
        await fs._allm_call_with_retries(
            "prompt", "小天", tier="summary", call_type="memory_fact_extraction",
        )

    assert factory.call_args.kwargs["max_completion_tokens"] == LLM_OUTPUT_GUARD_MAX_TOKENS
    # No extra_body → the factory resolves the thinking-off dialect itself.
    assert "extra_body" not in factory.call_args.kwargs


@pytest.mark.asyncio
async def test_retry_helper_json_failure_log_names_output_state():
    from memory import facts as facts_module
    from memory.facts import FactStore

    fs = object.__new__(FactStore)
    fs._config_manager = MagicMock()

    async def _cfg(_tier, **_kw):
        return _api_config()

    fs._config_manager.aget_model_api_config = _cfg
    empty = _response("", finish_reason="length", token_usage={"completion_tokens": 8192})
    with patch("utils.llm_client.create_chat_llm", return_value=_fake_llm(empty)), \
         patch.object(facts_module.logger, "warning") as warning, \
         patch("memory.facts.asyncio.sleep"):
        parsed = await fs._allm_call_with_retries(
            "prompt", "小天", tier="summary", call_type="memory_signal_detection",
            max_retries=1, thinking=True,
        )

    assert parsed is None
    messages = [c.args[0] for c in warning.call_args_list]
    assert any("JSON 解析失败" in m and "finish_reason='length'" in m for m in messages)


def test_signal_detection_prompt_limits_reasoning_in_every_language():
    from config.prompts.prompts_memory import HISTORY_REVIEW_PROMPT, SIGNAL_DETECTION_PROMPT

    assert set(SIGNAL_DETECTION_PROMPT) == set(HISTORY_REVIEW_PROMPT)
    for lang, template in SIGNAL_DETECTION_PROMPT.items():
        review_limit = HISTORY_REVIEW_PROMPT[lang].split("\n", 1)[0]
        expected = review_limit.replace("explanation", "reason")
        assert template.split("\n", 1)[0] == expected, lang
