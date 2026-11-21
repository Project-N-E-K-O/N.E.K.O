# -*- coding: utf-8 -*-
"""Unit tests for the `decision` model slot, dual-mode DecisionClient, and pilot integrations."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
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


# ---------------------------------------------------------------------------
# Protocol inference & endpoint resolution
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("model", "base_url", "prof_proto", "prof_model", "expected"),
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
def test_infer_decision_protocol(model, base_url, prof_proto, prof_model, expected):
    assert (
        infer_decision_protocol(
            model,
            base_url,
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


@pytest.mark.unit
def test_chat_fallback_parser_strips_thinking_and_fences():
    qs = _sample_questions()
    raw = (
        "<think>reasoning here</think>\n"
        "```json\n"
        '{"is_urgent": {"value": true, "probability": 0.88}, '
        '"route": {"choice": "tech", "confidence": 0.9}, '
        '"severity": {"score": 2, "confidence": 0.85}}\n'
        "```"
    )
    resp = ChatFallbackDecisionAdapter._parse_chat_json(raw, qs, "qwen3.6-flash")
    assert resp.protocol == "chat_fallback"
    assert resp.answers["is_urgent"].value is True
    assert resp.answers["is_urgent"].probability == pytest.approx(0.88)
    assert resp.answers["route"].value == "tech"
    assert resp.answers["severity"].value == pytest.approx(2.0)
    assert resp.answers["severity"].score_norm == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# DecisionClient: native dispatch + fallback + permanent-error cooldown
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_decision_client_native_failure_falls_back_and_caches_cooldown():
    clear_native_cooldown()
    qs = {"ok": BoolQuestion(instructions="Is it ok?")}
    native_cfg = {
        "model": "decision-model-preview",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key": "sk-test",
        "decision_protocol": "systemone",
        "decision_url": "https://dashscope.aliyuncs.com/compatible-mode/v1/systemone",
        "fallback_model": "qwen3.6-flash",
    }

    native_calls = 0

    async def _fake_native_execute(self, **kwargs):
        nonlocal native_calls
        native_calls += 1
        raise DecisionProtocolError("HTTP 404 model retired", status_code=404, protocol="systemone", permanent_route_error=True)

    fake_llm = SimpleNamespace(
        ainvoke=AsyncMock(return_value=SimpleNamespace(content='{"ok": {"value": true, "probability": 0.9}}')),
        aclose=AsyncMock(),
    )

    with (
        patch.object(SystemOneAdapter, "execute", _fake_native_execute),
        patch("utils.llm_client.create_chat_llm_async", AsyncMock(return_value=fake_llm)),
    ):
        client = DecisionClient()
        r1 = await client.decide(state="hello", questions=qs, config=native_cfg)
        assert r1.used_fallback is True
        assert r1.protocol == "chat_fallback"
        assert r1.answers["ok"].value is True
        assert native_calls == 1

        # Second call hits negative cooldown -> skips doomed native HTTP call immediately
        r2 = await client.decide(state="hello", questions=qs, config=native_cfg)
        assert r2.used_fallback is True
        assert native_calls == 1

    clear_native_cooldown()


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


# ---------------------------------------------------------------------------
# Connectivity probe for native decision endpoints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_connectivity_decision_probe_success_and_auth_error():
    from main_routers.config_router.connectivity import _test_decision_endpoint

    with patch.object(
        DecisionClient,
        "decide",
        AsyncMock(return_value=SimpleNamespace(answers={"ok": SimpleNamespace(value=True)})),
    ):
        ok = await _test_decision_endpoint(
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "sk-ok",
            model="decision-model-preview",
            protocol="systemone",
        )
        assert ok == {"success": True}

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


# ---------------------------------------------------------------------------
# Pilot 1: MemoryRecallReranker._llm_rerank native fast path + chat fallback
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
    assert [r["id"] for r in ranked] == ["m2", "m3"]
