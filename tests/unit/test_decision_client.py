# -*- coding: utf-8 -*-
"""Unit tests for the `decision` model slot, dual-mode DecisionClient, and pilot integrations."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from utils.decision_client import (
    BoolQuestion,
    ChatFallbackDecisionAdapter,
    ChoiceQuestion,
    DecisionClient,
    DecisionProtocolError,
    GeminiDecisionsAdapter,
    OpenAIDecisionsAdapter,
    ScoreQuestion,
    SystemOneAdapter,
    clear_native_cooldown,
    infer_decision_protocol,
    is_native_decision_protocol,
    resolve_decision_endpoint,
)


@pytest.fixture(autouse=True)
def _reset_native_cooldown():
    clear_native_cooldown()
    try:
        yield
    finally:
        clear_native_cooldown()


# ---------------------------------------------------------------------------
# Protocol inference & endpoint resolution
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("model", "endpoint_url", "prof_proto", "prof_model", "expected"),
    [
        ("decision-model-preview", "https://dashscope.aliyuncs.com/compatible-mode/v1", "systemone", "decision-model-preview", "systemone"),
        ("decision-model-preview", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1", "systemone", "decision-model-preview", "systemone"),
        ("qwen3.6-flash", "https://dashscope.aliyuncs.com/compatible-mode/v1", "systemone", "decision-model-preview", "chat_fallback"),
        ("gpt-6-luna", "https://api.openai.com/v1", "openai_decisions", "gpt-6-luna", "openai_decisions"),
        ("gemini-decision-1", "https://generativelanguage.googleapis.com/v1beta/openai/", "gemini_decisions", "gemini-decision-1", "gemini_decisions"),
        ("google/gemini-decision-1", "https://openrouter.ai/api/v1", "openrouter_decisions", "google/gemini-decision-1", "openrouter_decisions"),
        ("custom-decider", "https://my-gateway.example/compatible-mode/v1/systemone", None, None, "systemone"),
        ("custom-decider", "https://my-gateway.example/v1/decisions", None, None, "openai_decisions"),
        ("deepseek-v4-flash", "https://api.deepseek.com/v1", "chat_fallback", "deepseek-v4-flash", "chat_fallback"),
        ("free-mini-model", "https://www.lanlan.tech/text/v1", "chat_fallback", "free-mini-model", "chat_fallback"),
    ],
)
def test_infer_decision_protocol(model, endpoint_url, prof_proto, prof_model, expected):
    assert (
        infer_decision_protocol(
            model,
            endpoint_url,
            profile_protocol=prof_proto,
            profile_model=prof_model,
        )
        == expected
    )


@pytest.mark.unit
def test_resolve_decision_endpoint():
    assert (
        resolve_decision_endpoint("systemone", "https://dashscope.aliyuncs.com/compatible-mode/v1")
        == "https://dashscope.aliyuncs.com/compatible-mode/v1/systemone"
    )
    assert (
        resolve_decision_endpoint("systemone", "https://dashscope.aliyuncs.com/compatible-mode/v1/systemone")
        == "https://dashscope.aliyuncs.com/compatible-mode/v1/systemone"
    )
    assert (
        resolve_decision_endpoint("openai_decisions", "https://api.openai.com/v1")
        == "https://api.openai.com/v1/decisions"
    )
    assert (
        resolve_decision_endpoint("gemini_decisions", "https://generativelanguage.googleapis.com/v1beta/openai/")
        == "https://generativelanguage.googleapis.com/v1beta/decisions"
    )
    assert (
        resolve_decision_endpoint("openrouter_decisions", "https://openrouter.ai/api/v1")
        == "https://openrouter.ai/api/alpha/decisions"
    )
    assert (
        resolve_decision_endpoint("chat_fallback", "https://api.deepseek.com/v1")
        == "https://api.deepseek.com/v1"
    )
    # Hostname match for Google Gemini must be exact, never substring-based
    assert (
        resolve_decision_endpoint(
            "gemini_decisions",
            "https://generativelanguage.googleapis.com.gw.example/v1",
        )
        == "https://generativelanguage.googleapis.com.gw.example/v1/decisions"
    )


# ---------------------------------------------------------------------------
# All 19 assist_api_providers carry decision metadata
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_all_assist_providers_have_decision_fields():
    from utils.api_config_loader import get_config

    cfg = get_config(force_reload=True)
    providers = cfg.get("assist_api_providers", {})
    assert len(providers) >= 18

    native_expected = {
        "qwen": ("decision-model-preview", "systemone"),
        "qwen_intl": ("decision-model-preview", "systemone"),
        "openai": ("gpt-6-luna", "openai_decisions"),
        "gemini": ("gemini-decision-1", "gemini_decisions"),
        "openrouter": ("google/gemini-decision-1", "openrouter_decisions"),
    }
    assert set(native_expected.keys()).issubset(providers.keys())
    for key, profile in providers.items():
        assert "decision_model" in profile, f"{key} missing decision_model"
        assert "decision_protocol" in profile, f"{key} missing decision_protocol"
        if key in native_expected:
            exp_model, exp_proto = native_expected[key]
            assert profile["decision_model"] == exp_model
            assert profile["decision_protocol"] == exp_proto
            assert is_native_decision_protocol(profile["decision_protocol"])
        else:
            assert profile["decision_protocol"] == "chat_fallback"


# ---------------------------------------------------------------------------
# Protocol adapter payload building & response parsing
# ---------------------------------------------------------------------------


def _sample_questions():
    return {
        "is_urgent": BoolQuestion(instructions="Is this urgent?", true_description="Urgent", false_description="Not urgent"),
        "route": ChoiceQuestion(
            instructions="Pick queue",
            criteria={"billing": "Invoices", "tech": "Bugs"},
        ),
        "severity": ScoreQuestion(
            instructions="Rate severity",
            levels=("low", "medium", "high"),
        ),
    }


@pytest.mark.unit
def test_systemone_adapter_roundtrip():
    adapter = SystemOneAdapter("systemone")
    qs = _sample_questions()
    payload = adapter.build_payload(state="App crashes on export", questions=qs, model="decision-model-preview")
    assert payload["model"] == "decision-model-preview"
    assert payload["state"] == "App crashes on export"
    assert payload["questions"]["is_urgent"]["type"] == "noul"
    assert payload["questions"]["route"]["type"] == "choice"
    assert payload["questions"]["severity"]["type"] == "score"

    resp = adapter.parse_response(
        {
            "model": "decision-model-preview",
            "results": {
                "is_urgent": {"type": "noul", "noul": 0.92, "confidence": 0.96},
                "route": {
                    "type": "choice",
                    "choice": "tech",
                    "probabilities": {"billing": 0.05, "tech": 0.95},
                    "confidence": 0.94,
                },
                "severity": {
                    "type": "score",
                    "score": 1.8,
                    "probabilities": {"low": 0.05, "medium": 0.15, "high": 0.80},
                    "confidence": 0.88,
                },
            },
            "usage": {"prompt_tokens": 110, "completion_tokens": 0},
        },
        qs,
        "decision-model-preview",
    )
    assert resp.protocol == "systemone"
    assert resp.prompt_tokens == 110
    assert resp.completion_tokens == 0
    assert resp.answers["is_urgent"].value is True
    assert resp.answers["is_urgent"].probability == pytest.approx(0.92)
    assert resp.answers["route"].value == "tech"
    assert resp.answers["severity"].value == pytest.approx(1.8)
    assert resp.answers["severity"].score_norm == pytest.approx(0.9)


@pytest.mark.unit
def test_openai_decisions_adapter_roundtrip():
    adapter = OpenAIDecisionsAdapter()
    qs = _sample_questions()
    payload = adapter.build_payload(state="App crashes on export", questions=qs, model="gpt-6-luna")
    assert payload["model"] == "gpt-6-luna"
    assert payload["input"] == "App crashes on export"
    assert isinstance(payload["questions"], list)
    by_name = {q["name"]: q for q in payload["questions"]}
    assert by_name["is_urgent"]["type"] == "predicate"
    assert by_name["route"]["choices"] == ["billing", "tech"]
    assert by_name["severity"]["levels"] == ["low", "medium", "high"]

    resp = adapter.parse_response(
        {
            "model": "gpt-6-luna",
            "answers": [
                {"name": "is_urgent", "type": "predicate", "value": True, "probability": 0.91},
                {
                    "name": "route",
                    "type": "choice",
                    "selected": "tech",
                    "probabilities": {"billing": 0.08, "tech": 0.92},
                },
                {
                    "name": "severity",
                    "type": "score",
                    "score_index": 2,
                    "probabilities": {"low": 0.01, "medium": 0.09, "high": 0.90},
                },
            ],
            "usage": {"prompt_tokens": 64, "completion_tokens": 0},
        },
        qs,
        "gpt-6-luna",
    )
    assert resp.protocol == "openai_decisions"
    assert resp.answers["is_urgent"].value is True
    assert resp.answers["is_urgent"].probability == pytest.approx(0.91)
    assert resp.answers["route"].value == "tech"
    assert resp.answers["severity"].value == pytest.approx(2.0)
    assert resp.answers["severity"].score_norm == pytest.approx(1.0)


@pytest.mark.unit
def test_gemini_decisions_adapter_roundtrip():
    adapter = GeminiDecisionsAdapter()
    qs = _sample_questions()
    payload = adapter.build_payload(state="App crashes on export", questions=qs, model="gemini-decision-1")
    assert payload["model"] == "gemini-decision-1"
    assert payload["input"] == "App crashes on export"
    assert payload["questions"]["severity"]["legend"] == {"1": "low", "2": "medium", "3": "high"}

    resp = adapter.parse_response(
        {
            "answers": {
                "is_urgent": {"noul": 0.87, "confidence": 0.93},
                "route": {
                    "choice": "tech",
                    "probabilities": {"billing": 0.02, "tech": 0.98},
                    "confidence": 0.97,
                },
                "severity": {
                    "score": 2.6,  # 1-based in [1, 3] -> 1.6 in [0, 2] -> 0.8 norm
                    "probabilities": {"1": 0.05, "2": 0.30, "3": 0.65},
                    "confidence": 0.90,
                },
            },
            "usage_metadata": {"input_token_count": 72, "output_token_count": 0},
        },
        qs,
        "gemini-decision-1",
    )
    assert resp.protocol == "gemini_decisions"
    assert resp.prompt_tokens == 72
    assert resp.completion_tokens == 0
    assert resp.answers["is_urgent"].value is True
    assert resp.answers["route"].value == "tech"
    assert resp.answers["severity"].value == pytest.approx(1.6)
    assert resp.answers["severity"].score_norm == pytest.approx(0.8)
    assert resp.answers["severity"].probabilities["high"] == pytest.approx(0.65)

    # Also verify Gemini camelCase usageMetadata parsing
    camel_resp = adapter.parse_response(
        {
            "answers": {"is_urgent": {"noul": 0.9}},
            "usageMetadata": {
                "promptTokenCount": 55,
                "candidatesTokenCount": 0,
                "cachedContentTokenCount": 12,
            },
        },
        {"is_urgent": qs["is_urgent"]},
        "gemini-decision-1",
    )
    assert camel_resp.prompt_tokens == 55
    assert camel_resp.completion_tokens == 0
    assert camel_resp.cached_tokens == 12


@pytest.mark.unit
def test_chat_fallback_parser_strips_thinking_and_fences():
    qs = _sample_questions()
    raw = (
        "<think>reasoning here</think>\n"
        "```json\n"
        '{"is_urgent": {"value": true, "probability": 0.88}, '
        '"route": {"choice": "tech", "confidence": 0.9, "probabilities": {"tech": 0.0, "billing": 1.0}}, '
        '"severity": {"score": 2, "confidence": 0.85}}\n'
        "```"
    )
    resp = ChatFallbackDecisionAdapter._parse_chat_json(raw, qs, "qwen3.6-flash")
    assert resp.protocol == "chat_fallback"
    assert resp.answers["is_urgent"].value is True
    assert resp.answers["is_urgent"].probability == pytest.approx(0.88)
    assert resp.answers["route"].value == "tech"
    # Explicit 0.0 probability must be preserved rather than replaced by confidence
    assert resp.answers["route"].probability == pytest.approx(0.0)
    assert resp.answers["severity"].value == pytest.approx(2.0)
    assert resp.answers["severity"].score_norm == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# DecisionClient: native dispatch + fallback + permanent-error cooldown
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_score_question_missing_score_is_omitted_while_explicit_zero_is_preserved():
    qs = {
        "missing": ScoreQuestion(instructions="Rate", levels=("low", "med", "high")),
        "zero": ScoreQuestion(instructions="Rate", levels=("low", "med", "high")),
    }

    sys_resp = SystemOneAdapter("systemone").parse_response(
        {
            "results": {
                "missing": {"confidence": 0.9},
                "zero": {"score": 0, "confidence": 0.9},
            }
        },
        qs,
        "decision-model-preview",
    )
    assert "missing" not in sys_resp.answers
    assert sys_resp.answers["zero"].value == pytest.approx(0.0)
    assert sys_resp.answers["zero"].score_norm == pytest.approx(0.0)

    oai_resp = OpenAIDecisionsAdapter().parse_response(
        {
            "answers": [
                {"name": "missing", "confidence": 0.8},
                {"name": "zero", "score_index": 0, "confidence": 0.8},
            ]
        },
        qs,
        "gpt-6-luna",
    )
    assert "missing" not in oai_resp.answers
    assert oai_resp.answers["zero"].value == pytest.approx(0.0)
    assert oai_resp.answers["zero"].score_norm == pytest.approx(0.0)

    gem_resp = GeminiDecisionsAdapter().parse_response(
        {
            "answers": {
                "missing": {"confidence": 0.8},
                "zero": {"score": 1, "confidence": 0.8},  # 1-based -> 0.0
            }
        },
        qs,
        "gemini-decision-1",
    )
    assert "missing" not in gem_resp.answers
    assert gem_resp.answers["zero"].value == pytest.approx(0.0)
    assert gem_resp.answers["zero"].score_norm == pytest.approx(0.0)


@pytest.mark.unit
def test_score_question_overflow_and_partial_probability_weights():
    qs = {
        "overflow": ScoreQuestion(instructions="Rate", levels=("low", "med", "high")),
        "partial": ScoreQuestion(instructions="Rate", levels=("low", "med", "high")),
        "zero_weight": ScoreQuestion(instructions="Rate", levels=("low", "med", "high")),
        "valid_zero": ScoreQuestion(instructions="Rate", levels=("low", "med", "high")),
    }
    huge_int = 10**400
    resp = SystemOneAdapter("systemone").parse_response(
        {
            "results": {
                "overflow": {"score": huge_int},
                "partial": {"probabilities": {"high": 0.4}},
                "zero_weight": {"probabilities": {"low": 0.0, "med": 0.0, "high": 0.0}},
                "valid_zero": {"score": 0},
            }
        },
        qs,
        "decision-model-preview",
    )
    assert "overflow" not in resp.answers
    assert "zero_weight" not in resp.answers
    assert resp.answers["partial"].value == pytest.approx(2.0)
    assert resp.answers["partial"].score_norm == pytest.approx(1.0)
    assert resp.answers["valid_zero"].value == pytest.approx(0.0)


@pytest.mark.asyncio
@pytest.mark.unit
async def test_decision_client_native_failure_falls_back_and_caches_cooldown():
    qs = {"ok": BoolQuestion(instructions="Is it ok?")}
    native_cfg = {
        "model": "decision-model-preview",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key": "sk-test",
        "decision_protocol": "systemone",
        "decision_url": "https://dashscope.aliyuncs.com/compatible-mode/v1/systemone",
        "fallback_model": "qwen3.6-flash",
    }

    fake_native = AsyncMock(
        side_effect=DecisionProtocolError(
            "HTTP 404 model retired",
            status_code=404,
            protocol="systemone",
            permanent_route_error=True,
        )
    )
    fake_llm = SimpleNamespace(
        ainvoke=AsyncMock(return_value=SimpleNamespace(content='{"ok": {"value": true, "probability": 0.9}}')),
        aclose=AsyncMock(),
    )

    with (
        patch.object(SystemOneAdapter, "execute", fake_native),
        patch("utils.llm_client.create_chat_llm_async", AsyncMock(return_value=fake_llm)),
    ):
        client = DecisionClient()
        r1 = await client.decide(state="hello", questions=qs, config=native_cfg)
        assert r1.used_fallback is True
        assert r1.protocol == "chat_fallback"
        assert r1.answers["ok"].value is True
        assert fake_native.await_count == 1

        # Second call hits negative cooldown -> skips doomed native HTTP call immediately
        r2 = await client.decide(state="hello", questions=qs, config=native_cfg)
        assert r2.used_fallback is True
        assert fake_native.await_count == 1

        # Business callers passing allow_fallback=False still honor cooldown by default
        with pytest.raises(DecisionProtocolError):
            await client.decide(state="hello", questions=qs, config=native_cfg, allow_fallback=False)
        assert fake_native.await_count == 1

        # Manual probes passing respect_cooldown=False bypass cooldown and hit the wire
        with pytest.raises(DecisionProtocolError):
            await client.decide(
                state="hello",
                questions=qs,
                config=native_cfg,
                allow_fallback=False,
                respect_cooldown=False,
            )
        assert fake_native.await_count == 2


@pytest.mark.asyncio
@pytest.mark.unit
async def test_decision_client_records_native_usage_via_real_token_tracker(clean_user_data_dir):
    from utils.decision_client.schema import DecisionAnswer, DecisionResponse
    from utils.token_tracker import TokenTracker

    tracker = TokenTracker.get_instance()
    qs = {"ok": BoolQuestion(instructions="Is it ok?")}
    native_cfg = {
        "model": "decision-model-preview",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key": "sk-test",
        "decision_protocol": "systemone",
        "decision_url": "https://dashscope.aliyuncs.com/compatible-mode/v1/systemone",
    }
    fake_resp = DecisionResponse(
        answers={"ok": DecisionAnswer(question_type="bool", value=True, probability=0.95)},
        model="decision-model-preview",
        protocol="systemone",
        prompt_tokens=88,
        completion_tokens=0,
        cached_tokens=16,
    )

    with (
        patch.object(SystemOneAdapter, "execute", AsyncMock(return_value=fake_resp)),
        patch.object(tracker, "record", wraps=tracker.record) as spy_record,
    ):
        client = DecisionClient()
        resp = await client.decide(
            state="hello",
            questions=qs,
            config=native_cfg,
            call_type="memory_recall_rerank",
        )

    assert resp.answers["ok"].value is True
    spy_record.assert_called_once_with(
        model="decision-model-preview",
        prompt_tokens=88,
        completion_tokens=0,
        total_tokens=88,
        cached_tokens=16,
        call_type="memory_recall_rerank",
        source="decision:systemone",
        success=True,
    )


@pytest.mark.asyncio
@pytest.mark.unit
async def test_decision_client_fallback_avoids_complete_native_endpoint_url():
    qs = {"ok": BoolQuestion(instructions="Is it ok?")}
    native_cfg = {
        "model": "decision-model-preview",
        "base_url": "https://my-gw.example/v1/systemone",
        "api_key": "sk-native",
        "decision_protocol": "systemone",
        "decision_url": "https://my-gw.example/v1/systemone",
        "fallback_model": "qwen3.7-flash",
    }
    fake_cm = MagicMock()
    fake_cm.aget_model_api_config = AsyncMock(return_value={
        "model": "emotion-chat-model",
        "base_url": "https://chat-gw.example/v1",
        "api_key": "sk-chat",
        "provider_type": "openai_compatible",
    })
    fake_llm = SimpleNamespace(
        ainvoke=AsyncMock(return_value=SimpleNamespace(content='{"ok": {"value": true, "probability": 0.9}}')),
        aclose=AsyncMock(),
    )

    with (
        patch.object(
            SystemOneAdapter,
            "execute",
            AsyncMock(side_effect=DecisionProtocolError("HTTP 404", status_code=404, protocol="systemone", permanent_route_error=True)),
        ),
        patch("utils.llm_client.create_chat_llm_async", AsyncMock(return_value=fake_llm)) as mock_create_llm,
    ):
        client = DecisionClient(config_manager=fake_cm)
        resp = await client.decide(state="hello", questions=qs, config=native_cfg)

    assert resp.used_fallback is True
    assert mock_create_llm.await_args.kwargs["base_url"] == "https://chat-gw.example/v1"
    assert mock_create_llm.await_args.kwargs["model"] == "emotion-chat-model"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_decision_client_call_type_context_restored_and_empty_probe_keeps_cooldown():
    from utils.decision_client.client import _derive_chat_base_url_from_native, _is_complete_native_decision_url
    from utils.decision_client.schema import DecisionResponse
    from utils.token_tracker import _current_call_type

    assert _is_complete_native_decision_url("https://my-gw.example/v1/systemone?api-version=1") is True
    assert (
        _derive_chat_base_url_from_native("https://my-gw.example/v1/systemone?api-version=1")
        == "https://my-gw.example/v1"
    )

    qs = {"ok": BoolQuestion(instructions="Is it ok?")}
    native_cfg = {
        "model": "decision-model-preview",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key": "sk-test",
        "decision_protocol": "systemone",
        "decision_url": "https://dashscope.aliyuncs.com/compatible-mode/v1/systemone",
    }

    token = _current_call_type.set("previous_task")
    try:
        client = DecisionClient()
        # First call fails permanently -> enters cooldown
        with patch.object(
            SystemOneAdapter,
            "execute",
            AsyncMock(
                side_effect=DecisionProtocolError(
                    "HTTP 404", status_code=404, protocol="systemone", permanent_route_error=True
                )
            ),
        ):
            with pytest.raises(DecisionProtocolError):
                await client.decide(
                    state="ping",
                    questions=qs,
                    config=native_cfg,
                    allow_fallback=False,
                    call_type="connectivity_test",
                )
        # Context must be restored after decide() exits (even on exception)
        assert _current_call_type.get() == "previous_task"

        # Forced probe returning empty answers must NOT clear cooldown
        with patch.object(
            SystemOneAdapter,
            "execute",
            AsyncMock(return_value=DecisionResponse(answers={}, model="decision-model-preview", protocol="systemone")),
        ):
            empty_resp = await client.decide(
                state="ping",
                questions=qs,
                config=native_cfg,
                allow_fallback=False,
                respect_cooldown=False,
                call_type="connectivity_test",
            )
            assert empty_resp.answers == {}
        assert _current_call_type.get() == "previous_task"

        # Next normal call must still observe cooldown and skip hitting the adapter
        spy_exec = AsyncMock()
        with patch.object(SystemOneAdapter, "execute", spy_exec):
            with pytest.raises(DecisionProtocolError):
                await client.decide(state="ping", questions=qs, config=native_cfg, allow_fallback=False)
        assert spy_exec.await_count == 0
    finally:
        _current_call_type.reset(token)


@pytest.mark.asyncio
@pytest.mark.unit
async def test_decision_client_timeout_and_5xx_trigger_cooldown_and_api_key_change_escapes():
    import asyncio

    qs = {"ok": BoolQuestion(instructions="Is it ok?")}
    native_cfg = {
        "model": "decision-model-preview",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key": "sk-key-a",
        "decision_protocol": "systemone",
        "decision_url": "https://dashscope.aliyuncs.com/compatible-mode/v1/systemone",
    }

    slow_exec = AsyncMock(side_effect=asyncio.TimeoutError())
    client = DecisionClient()
    with patch.object(SystemOneAdapter, "execute", slow_exec):
        with pytest.raises(asyncio.TimeoutError):
            await client.decide(state="ping", questions=qs, config=native_cfg, timeout=0.5, allow_fallback=False)
        assert slow_exec.await_count == 1

        # Second call with same key hits 60s transient cooldown -> skips native immediately
        with pytest.raises(DecisionProtocolError):
            await client.decide(state="ping", questions=qs, config=native_cfg, timeout=0.5, allow_fallback=False)
        assert slow_exec.await_count == 1

        # Fixing API key immediately bypasses previous key's cooldown
        updated_cfg = dict(native_cfg, api_key="sk-key-b")
        with pytest.raises(asyncio.TimeoutError):
            await client.decide(state="ping", questions=qs, config=updated_cfg, timeout=0.5, allow_fallback=False)
        assert slow_exec.await_count == 2

    # HTTP 503 also triggers transient cooldown
    clear_native_cooldown()
    err_503 = AsyncMock(
        side_effect=DecisionProtocolError("HTTP 503", status_code=503, protocol="systemone", permanent_route_error=False)
    )
    with patch.object(SystemOneAdapter, "execute", err_503):
        with pytest.raises(DecisionProtocolError):
            await client.decide(state="ping", questions=qs, config=native_cfg, allow_fallback=False)
        with pytest.raises(DecisionProtocolError):
            await client.decide(state="ping", questions=qs, config=native_cfg, allow_fallback=False)
    assert err_503.await_count == 1


# ---------------------------------------------------------------------------
# ConfigManager integration for `decision` slot
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_config_manager_decision_slot_defaults_and_cross_provider(clean_user_data_dir):
    from utils.config_manager import get_config_manager

    cm = get_config_manager("N.E.K.O")
    cm.config_dir.mkdir(parents=True, exist_ok=True)
    cfg_path = cm.get_config_path("core_config.json")

    def _set_cfg(data: dict) -> None:
        cfg_path.write_text(json.dumps(data), encoding="utf-8")
        cm._core_config_cache = None

    # Default qwen assist -> native systemone decision-model-preview + qwen3.7-flash fallback
    _set_cfg({"coreApi": "qwen", "assistApi": "qwen", "coreApiKey": "sk-qwen"})
    qwen_dec = cm.get_model_api_config("decision")
    assert qwen_dec["model"] == "decision-model-preview"
    assert qwen_dec["decision_protocol"] == "systemone"
    assert qwen_dec["decision_url"].endswith("/compatible-mode/v1/systemone")
    assert qwen_dec["fallback_model"] == "qwen3.7-flash"

    # Default gemini assist -> native gemini_decisions + /v1beta/decisions
    _set_cfg({"coreApi": "qwen", "assistApi": "gemini", "assistApiKeyGemini": "sk-gem"})
    gem_dec = cm.get_model_api_config("decision")
    assert gem_dec["model"] == "gemini-decision-1"
    assert gem_dec["decision_protocol"] == "gemini_decisions"
    assert gem_dec["decision_url"] == "https://generativelanguage.googleapis.com/v1beta/decisions"

    # Default deepseek assist -> chat_fallback
    _set_cfg({"coreApi": "qwen", "assistApi": "deepseek", "assistApiKeyDeepseek": "sk-ds"})
    ds_dec = cm.get_model_api_config("decision")
    assert ds_dec["model"] == "deepseek-flash"
    assert ds_dec["decision_protocol"] == "chat_fallback"

    # Cross-provider mix: deepseek assist + custom decision slot pinned to gemini-decision-1
    _set_cfg({
        "coreApi": "qwen",
        "assistApi": "deepseek",
        "assistApiKeyDeepseek": "sk-ds",
        "enableCustomApi": True,
        "decisionModelProvider": "gemini",
        "decisionModelUrl": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "decisionModelId": "gemini-decision-1",
        "decisionModelApiKey": "sk-gem-custom",
    })
    mixed_dec = cm.get_model_api_config("decision")
    assert mixed_dec["model"] == "gemini-decision-1"
    assert mixed_dec["decision_protocol"] == "gemini_decisions"
    assert mixed_dec["decision_url"] == "https://generativelanguage.googleapis.com/v1beta/decisions"
    assert mixed_dec["api_key"] == "sk-gem-custom"

    # Empty decisionModelId under follow_core resolves the core provider's DECISION_MODEL
    _set_cfg({
        "coreApi": "openai",
        "assistApi": "qwen",
        "coreApiKey": "sk-oai-core",
        "assistApiKeyQwen": "sk-qwen",
        "enableCustomApi": True,
        "decisionModelProvider": "follow_core",
        "decisionModelId": "",
    })
    follow_core_dec = cm.get_model_api_config("decision")
    assert follow_core_dec["model"] == "gpt-6-luna"
    assert follow_core_dec["decision_protocol"] == "openai_decisions"
    assert follow_core_dec["decision_url"] == "https://api.openai.com/v1/decisions"

    # Custom gateway URL under named provider must NOT leak requests to the official host
    _set_cfg({
        "coreApi": "qwen",
        "assistApi": "deepseek",
        "assistApiKeyDeepseek": "sk-ds",
        "enableCustomApi": True,
        "decisionModelProvider": "gemini",
        "decisionModelUrl": "https://my-gateway.internal/v1",
        "decisionModelId": "gemini-decision-1",
        "decisionModelApiKey": "sk-gateway",
    })
    gw_dec = cm.get_model_api_config("decision")
    assert gw_dec["decision_protocol"] == "gemini_decisions"
    assert gw_dec["decision_url"] == "https://my-gateway.internal/v1/decisions"
    assert "generativelanguage.googleapis.com" not in gw_dec["decision_url"]
    # Gateway override must NOT pair another provider's fallback_model with the custom host
    assert gw_dec["fallback_model"] == ""

    # Custom decision slot must keep fallback_model empty instead of borrowing assist's EMOTION_MODEL
    _set_cfg({
        "coreApi": "qwen",
        "assistApi": "deepseek",
        "assistApiKeyDeepseek": "sk-ds",
        "enableCustomApi": True,
        "decisionModelProvider": "custom",
        "decisionModelUrl": "https://private-host.internal/v1",
        "decisionModelId": "decision-model-preview",
        "decisionModelApiKey": "sk-private",
    })
    custom_dec = cm.get_model_api_config("decision")
    assert custom_dec["fallback_model"] == ""

    _set_cfg({
        "coreApi": "qwen",
        "assistApi": "deepseek",
        "assistApiKeyDeepseek": "sk-ds",
        "enableCustomApi": True,
        "decisionModelProvider": "openrouter",
        "decisionModelUrl": "https://my-gateway.internal/api/v1",
        "decisionModelId": "google/gemini-decision-1",
        "decisionModelApiKey": "sk-gateway",
    })
    or_gw_dec = cm.get_model_api_config("decision")
    assert or_gw_dec["decision_protocol"] == "openrouter_decisions"
    assert or_gw_dec["decision_url"] == "https://my-gateway.internal/api/alpha/decisions"
    assert "openrouter.ai" not in or_gw_dec["decision_url"]


# ---------------------------------------------------------------------------
# Connectivity probe for native decision endpoints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_connectivity_decision_probe_success_and_auth_error():
    from main_routers.config_router.connectivity import (
        ConnectivityTestRequest,
        _test_connectivity_candidates,
        _test_decision_endpoint,
        test_connectivity,
    )

    with patch.object(
        DecisionClient,
        "decide",
        AsyncMock(return_value=SimpleNamespace(answers={"ok": SimpleNamespace(value=True)})),
    ) as mock_decide:
        ok = await _test_decision_endpoint(
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "sk-ok",
            model="decision-model-preview",
            protocol="systemone",
        )
        assert ok == {"success": True}
        assert mock_decide.await_args.kwargs["respect_cooldown"] is False

    # False is still a valid boolean answer
    with patch.object(
        DecisionClient,
        "decide",
        AsyncMock(return_value=SimpleNamespace(answers={"ok": SimpleNamespace(value=False)})),
    ):
        ok_false = await _test_decision_endpoint(
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "sk-ok",
            model="decision-model-preview",
            protocol="systemone",
        )
        assert ok_false == {"success": True}

    # Empty answers dict must fail connectivity probe
    with patch.object(
        DecisionClient,
        "decide",
        AsyncMock(return_value=SimpleNamespace(answers={})),
    ):
        empty_res = await _test_decision_endpoint(
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "sk-ok",
            model="decision-model-preview",
            protocol="systemone",
        )
        assert empty_res["success"] is False
        assert empty_res["error_code"] == "invalid_response"

    with patch.object(
        DecisionClient,
        "decide",
        AsyncMock(side_effect=DecisionProtocolError("HTTP 401", status_code=401, protocol="systemone")),
    ):
        bad = await _test_decision_endpoint(
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "sk-bad",
            model="decision-model-preview",
            protocol="systemone",
        )
        assert bad["success"] is False
        assert bad["error_code"] == "auth_failed"

    # Regular openai_compatible chat slots must NOT be redirected to native decision probe by prefix
    with (
        patch(
            "main_routers.config_router.connectivity._test_openai_compatible",
            AsyncMock(return_value={"success": True}),
        ) as mock_oai,
        patch(
            "main_routers.config_router.connectivity._test_decision_endpoint",
            AsyncMock(return_value={"success": True}),
        ) as mock_dec_probe,
    ):
        chat_res = await _test_connectivity_candidates(
            ["https://my-gw.example/v1"],
            "sk-chat",
            model="gpt-6-luna-chat",
            provider_type="openai_compatible",
            is_free=False,
        )
        assert chat_res["success"] is True
        assert mock_oai.await_count == 1
        assert mock_dec_probe.await_count == 0

    # Built-in Mode 1 provider_scope='decision' tests the provider's decision model & protocol
    with patch(
        "main_routers.config_router.connectivity._test_decision_endpoint",
        AsyncMock(return_value={"success": True}),
    ) as mock_dec_probe:
        scope_res = await test_connectivity(
            ConnectivityTestRequest(
                provider_key="qwen",
                provider_scope="decision",
                api_key="sk-qwen",
            )
        )
        assert scope_res["success"] is True
        assert mock_dec_probe.await_count == 1
        assert mock_dec_probe.await_args.kwargs["model"] == "decision-model-preview"
        assert mock_dec_probe.await_args.kwargs["protocol"] == "systemone"

    # Mode 1 provider_scope='decision' overridden with a regular chat model must use chat probe
    with (
        patch(
            "main_routers.config_router.connectivity._test_openai_compatible",
            AsyncMock(return_value={"success": True}),
        ) as mock_oai,
        patch(
            "main_routers.config_router.connectivity._test_decision_endpoint",
            AsyncMock(return_value={"success": True}),
        ) as mock_dec_probe,
    ):
        chat_override_res = await test_connectivity(
            ConnectivityTestRequest(
                provider_key="gemini",
                provider_scope="decision",
                api_key="sk-gem",
                model="gemini-2.5-flash",
            )
        )
        assert chat_override_res["success"] is True
        assert mock_oai.await_count == 1
        assert mock_dec_probe.await_count == 0

    # Custom decision slot pointing to Anthropic Messages API must route to _test_anthropic
    with (
        patch(
            "main_routers.config_router.connectivity._test_anthropic",
            AsyncMock(return_value={"success": True}),
        ) as mock_anth,
        patch(
            "main_routers.config_router.connectivity._test_openai_compatible",
            AsyncMock(return_value={"success": True}),
        ) as mock_oai,
    ):
        anth_res = await _test_decision_endpoint(
            "https://api.anthropic.com/v1/messages",
            "sk-ant",
            model="claude-haiku-4-5",
            protocol="",
        )
        assert anth_res["success"] is True
        assert mock_anth.await_count == 1
        assert mock_oai.await_count == 0


# ---------------------------------------------------------------------------
# Pilot 1: MemoryRecallReranker._fine_rank native fast path + summary fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_memory_recall_reranker_uses_native_decision_and_ranks_by_score():
    from memory.recall import MemoryRecallReranker
    from utils.decision_client.schema import DecisionAnswer, DecisionResponse

    cm = MagicMock()
    cm.aget_model_api_config = AsyncMock(return_value={
        "model": "decision-model-preview",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key": "sk-test",
        "decision_protocol": "systemone",
        "decision_url": "https://dashscope.aliyuncs.com/compatible-mode/v1/systemone",
    })
    candidates = [
        {"id": "m1", "text": "likes apples", "score": 1.0},
        {"id": "m2", "text": "allergic to peanuts", "score": 1.0},
        {"id": "m3", "text": "plays guitar", "score": 1.0},
    ]
    fake_dec_resp = DecisionResponse(
        answers={
            "m1": DecisionAnswer(question_type="score", value=0.3, score_norm=0.1),
            "m2": DecisionAnswer(question_type="score", value=2.9, score_norm=0.97),
            "m3": DecisionAnswer(question_type="score", value=1.5, score_norm=0.5),
        },
        model="decision-model-preview",
        protocol="systemone",
    )
    with patch.object(DecisionClient, "decide", AsyncMock(return_value=fake_dec_resp)) as mock_decide:
        reranker = MemoryRecallReranker()
        ranked = await reranker._fine_rank(
            candidates,
            query_texts=["what food allergies does the user have?"],
            budget=2,
            config_manager=cm,
            prompt_loader=lambda _lang: "{QUERY}\n{CANDIDATES}\n{BUDGET}",
            lang="en",
        )
    assert mock_decide.await_count == 1
    assert mock_decide.await_args.kwargs["respect_cooldown"] is True
    assert [r["id"] for r in ranked] == ["m2", "m3"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_memory_recall_reranker_keeps_summary_tier_for_non_native_providers():
    from memory.recall import MemoryRecallReranker

    async def _get_cfg(tier: str):
        if tier == "decision":
            return {
                "model": "free-mini-model",
                "base_url": "https://www.lanlan.tech/text/v1",
                "api_key": "free-access",
                "decision_protocol": "chat_fallback",
            }
        if tier == "summary":
            return {
                "model": "user-custom-summary-model",
                "base_url": "https://local-summary.example/v1",
                "api_key": "sk-local",
                "provider_type": "openai_compatible",
            }
        raise AssertionError(f"unexpected model tier requested: {tier}")

    cm = MagicMock()
    cm.aget_model_api_config = AsyncMock(side_effect=_get_cfg)
    fake_llm = SimpleNamespace(
        ainvoke=AsyncMock(return_value=SimpleNamespace(content='[{"id": "m2"}, {"id": "m1"}]')),
        aclose=AsyncMock(),
    )
    candidates = [
        {"id": "m1", "text": "likes apples", "score": 1.0},
        {"id": "m2", "text": "allergic to peanuts", "score": 1.0},
    ]

    with patch("utils.llm_client.create_chat_llm_async", AsyncMock(return_value=fake_llm)) as mock_create_llm:
        reranker = MemoryRecallReranker()
        ranked = await reranker._fine_rank(
            candidates,
            query_texts=["allergies?"],
            budget=2,
            config_manager=cm,
            prompt_loader=lambda _lang: "{QUERY}\n{CANDIDATES}\n{BUDGET}",
            lang="en",
        )

    assert [r["id"] for r in ranked] == ["m2", "m1"]
    assert mock_create_llm.await_args.args[0] == "user-custom-summary-model"
    assert mock_create_llm.await_args.args[1] == "https://local-summary.example/v1"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_memory_recall_reranker_aligns_questions_with_bounded_native_state():
    from memory.recall import MemoryRecallReranker
    from utils.decision_client.schema import DecisionAnswer, DecisionResponse

    cm = MagicMock()
    cm.aget_model_api_config = AsyncMock(return_value={
        "model": "decision-model-preview",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key": "sk-test",
        "decision_protocol": "systemone",
        "decision_url": "https://dashscope.aliyuncs.com/compatible-mode/v1/systemone",
    })
    # Build 80 long candidates (~180 tokens each -> >14,000 tokens total, > 6,000 native state cap)
    long_text = " ".join(["memorydetail"] * 120)
    candidates = [
        {"id": f"m{i}", "text": f"entry {i} {long_text}", "score": float(80 - i)}
        for i in range(80)
    ]

    captured: dict = {}

    async def _capture_decide(self, *, state, questions, **kwargs):
        captured["state"] = state
        captured["question_ids"] = list(questions.keys())
        return DecisionResponse(
            answers={
                qid: DecisionAnswer(question_type="score", value=2.0, score_norm=0.67)
                for qid in questions
            },
            model="decision-model-preview",
            protocol="systemone",
        )

    with patch.object(DecisionClient, "decide", _capture_decide):
        reranker = MemoryRecallReranker()
        ranked = await reranker._fine_rank(
            candidates,
            query_texts=["what is entry 0?"],
            budget=5,
            config_manager=cm,
            prompt_loader=lambda _lang: "{QUERY}\n{CANDIDATES}\n{BUDGET}",
            lang="en",
        )

    assert len(ranked) == 5
    assert 0 < len(captured["question_ids"]) < len(candidates)
    for qid in captured["question_ids"]:
        assert f"[{qid}]" in captured["state"]
