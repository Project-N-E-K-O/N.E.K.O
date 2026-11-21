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
    # Bailian decision model API reference: every question uses type / instructions / criteria.
    assert payload["questions"]["is_urgent"] == {
        "type": "noul",
        "instructions": "Is this urgent?",
        "criteria": {"true": "Urgent", "false": "Not urgent"},
    }
    assert payload["questions"]["route"] == {
        "type": "choice",
        "instructions": "Pick queue",
        "criteria": {"billing": "Invoices", "tech": "Bugs"},
    }
    assert payload["questions"]["severity"] == {
        "type": "score",
        "instructions": "Rate severity",
        "criteria": ["low", "medium", "high"],
    }
    for entry in payload["questions"].values():
        assert set(entry) <= {"type", "instructions", "criteria"}
    # A noul question without descriptions carries no criteria at all (it is optional).
    bare = adapter.build_payload(
        state="x", questions={"q": BoolQuestion(instructions="Yes?")}, model="decision-model-preview",
    )
    assert bare["questions"]["q"] == {"type": "noul", "instructions": "Yes?"}

    # Response shape from the reference: answers keyed by question id, score
    # probabilities keyed by 0-based level index strings, usage.input_tokens only.
    resp = adapter.parse_response(
        {
            "model": "decision-model-preview",
            "request_id": "00000000-0000-0000-0000-000000000000",
            "answers": {
                "is_urgent": {"type": "noul", "noul": 0.92},
                "route": {
                    "type": "choice",
                    "choice": "tech",
                    "probabilities": {"billing": 0.05, "tech": 0.95},
                    "confidence": 0.94,
                },
                "severity": {
                    "type": "score",
                    "score": 1.8,
                    "probabilities": {"0": 0.05, "1": 0.10, "2": 0.85},
                    "legend": {"0": "low", "1": "medium", "2": "high"},
                    "confidence": 0.88,
                },
            },
            "usage": {"input_tokens": 110},
            "latency_ms": 52.9,
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
    # Index-keyed level probabilities are normalized to level labels.
    assert resp.answers["severity"].probabilities == {
        "low": pytest.approx(0.05), "medium": pytest.approx(0.10), "high": pytest.approx(0.85),
    }

    # Without a score field, the expected score comes from the index-keyed probabilities.
    from_probs = adapter.parse_response(
        {"answers": {"severity": {"type": "score", "probabilities": {"0": 0.0, "1": 0.5, "2": 0.5}}}},
        {"severity": qs["severity"]},
        "decision-model-preview",
    )
    assert from_probs.answers["severity"].value == pytest.approx(1.5)


@pytest.mark.asyncio
@pytest.mark.unit
async def test_systemone_adapter_splits_more_than_16_questions_into_concurrent_requests():
    adapter = SystemOneAdapter("systemone")
    assert SystemOneAdapter.MAX_QUESTIONS_PER_REQUEST == 16
    questions = {
        f"q{i}": ScoreQuestion(instructions=f"rate {i}", levels=("no", "yes"))
        for i in range(20)
    }
    sent_payloads: list[dict] = []

    class _FakeResponse:
        status_code = 200

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return {
                "model": "decision-model-preview",
                "answers": {
                    qid: {"type": "score", "score": 1.0, "probabilities": {"0": 0.0, "1": 1.0}}
                    for qid in self._payload["questions"]
                },
                "usage": {"input_tokens": 10},
            }

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None, headers=None):
            sent_payloads.append(json)
            return _FakeResponse(json)

    with patch("utils.decision_client.adapters.httpx.AsyncClient", _FakeAsyncClient):
        resp = await adapter.execute(
            state="shared state",
            questions=questions,
            model="decision-model-preview",
            base_url="",
            api_key="sk-test",
            decision_url="https://llm-abc.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/systemone",
        )

    assert [len(p["questions"]) for p in sent_payloads] == [16, 4]
    assert all(p["state"] == "shared state" for p in sent_payloads)
    sent_ids = [qid for p in sent_payloads for qid in p["questions"]]
    assert sorted(sent_ids) == sorted(questions)
    assert set(resp.answers) == set(questions)
    assert resp.prompt_tokens == 20

    # Exactly 16 questions stays a single request.
    sent_payloads.clear()
    with patch("utils.decision_client.adapters.httpx.AsyncClient", _FakeAsyncClient):
        await adapter.execute(
            state="s",
            questions=dict(list(questions.items())[:16]),
            model="decision-model-preview",
            base_url="",
            api_key="sk-test",
            decision_url="https://llm-abc.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/systemone",
        )
    assert [len(p["questions"]) for p in sent_payloads] == [16]


# ---------------------------------------------------------------------------
# Bailian workspace ID validation and dedicated-host URL building
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("llm-abc123", "llm-abc123"),
        ("  LLM-AbC123  ", "llm-abc123"),
        ("a", "a"),
        ("a" * 63, "a" * 63),
        ("llm-abc\n", "llm-abc"),
        ("", ""),
        ("   ", ""),
        (None, ""),
    ],
)
def test_workspace_id_normalization_accepts_single_dns_labels(raw, expected):
    from utils.decision_client.workspace import normalize_workspace_id, safe_workspace_id

    assert normalize_workspace_id(raw) == expected
    assert safe_workspace_id(raw) == expected


@pytest.mark.unit
@pytest.mark.parametrize(
    "raw",
    [
        "evil.com",
        "llm-abc.evil.com",
        "evil.com/x",
        "evil.com#",
        "llm-abc@evil.com",
        "user:pass@evil",
        "llm-abc:443",
        "llm abc",
        "llm-abc\nevil",
        "-llm",
        "llm-",
        "llm_abc",
        "a" * 64,
        "llm%2eevil",
        "llm․evil",
        "ｌｌｍ",  # full-width letters
        "K",  # Kelvin sign case-folds to ASCII "k"
        "https://llm-abc.cn-beijing.maas.aliyuncs.com",
        123,
        ["llm-abc"],
    ],
)
def test_workspace_id_rejects_anything_that_could_change_the_host(raw):
    from utils.decision_client.workspace import (
        InvalidWorkspaceIdError,
        build_workspace_decision_url,
        normalize_workspace_id,
        safe_workspace_id,
    )

    with pytest.raises(InvalidWorkspaceIdError):
        normalize_workspace_id(raw)
    assert safe_workspace_id(raw) == ""
    assert build_workspace_decision_url(
        "https://{workspace_id}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/systemone", raw,
    ) == ""


@pytest.mark.unit
def test_build_workspace_decision_url_for_both_regions():
    from utils.decision_client.workspace import build_workspace_decision_url
    from utils.api_config_loader import get_assist_api_profiles

    profiles = get_assist_api_profiles(force_reload=True)
    assert build_workspace_decision_url(profiles["qwen"]["DECISION_URL_TEMPLATE"], "LLM-Abc") == (
        "https://llm-abc.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/systemone"
    )
    assert build_workspace_decision_url(profiles["qwen_intl"]["DECISION_URL_TEMPLATE"], "llm-abc") == (
        "https://llm-abc.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1/systemone"
    )
    # No placeholder or no ID -> nothing to call.
    assert build_workspace_decision_url("https://example.com/systemone", "llm-abc") == ""
    assert build_workspace_decision_url(profiles["qwen"]["DECISION_URL_TEMPLATE"], "") == ""
    # Defense in depth: the ID must land as the leading host label of an https URL.
    assert build_workspace_decision_url("https://evil.example/{workspace_id}/systemone", "llm-abc") == ""
    assert build_workspace_decision_url("http://{workspace_id}.cn-beijing.maas.aliyuncs.com/v1", "llm-abc") == ""
    assert build_workspace_decision_url("https://user@{workspace_id}.example/v1", "llm-abc") == ""
    # Only providers whose native endpoint needs a workspace carry a template.
    templated = {key for key, prof in profiles.items() if prof.get("DECISION_URL_TEMPLATE")}
    assert templated == {"qwen", "qwen_intl"}


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

    # Default qwen assist without a Bailian workspace ID -> the native endpoint does
    # not exist on the shared DashScope host, so decide with the chat model instead.
    _set_cfg({"coreApi": "qwen", "assistApi": "qwen", "coreApiKey": "sk-qwen"})
    qwen_dec = cm.get_model_api_config("decision")
    assert qwen_dec["model"] == "qwen3.7-flash"
    assert qwen_dec["decision_protocol"] == "chat_fallback"
    assert qwen_dec["native_unavailable_reason"] == "workspace_missing"
    assert qwen_dec["native_decision_model"] == "decision-model-preview"
    assert "systemone" not in qwen_dec["decision_url"]
    assert qwen_dec["fallback_model"] == "qwen3.7-flash"

    # With a workspace ID -> native systemone on the Beijing workspace host.
    _set_cfg({
        "coreApi": "qwen", "assistApi": "qwen", "coreApiKey": "sk-qwen",
        "assistWorkspaceIdQwen": "LLM-Abc123",
    })
    qwen_ws_dec = cm.get_model_api_config("decision")
    assert qwen_ws_dec["model"] == "decision-model-preview"
    assert qwen_ws_dec["decision_protocol"] == "systemone"
    assert qwen_ws_dec["decision_url"] == (
        "https://llm-abc123.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/systemone"
    )
    assert qwen_ws_dec["native_unavailable_reason"] == ""
    assert qwen_ws_dec["fallback_model"] == "qwen3.7-flash"

    # qwen_intl uses its own ID and the Singapore host; the Beijing ID is not reused.
    _set_cfg({
        "coreApi": "qwen", "assistApi": "qwen_intl", "assistApiKeyQwenIntl": "sk-intl",
        "assistWorkspaceIdQwen": "llm-beijing",
        "assistWorkspaceIdQwenIntl": "llm-sg",
    })
    intl_dec = cm.get_model_api_config("decision")
    assert intl_dec["decision_protocol"] == "systemone"
    assert intl_dec["decision_url"] == (
        "https://llm-sg.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1/systemone"
    )
    _set_cfg({
        "coreApi": "qwen", "assistApi": "qwen_intl", "assistApiKeyQwenIntl": "sk-intl",
        "assistWorkspaceIdQwen": "llm-beijing",
    })
    assert cm.get_model_api_config("decision")["decision_protocol"] == "chat_fallback"

    # qwen_intl resolved to the US host has no documented decision endpoint.
    _set_cfg({
        "coreApi": "qwen", "assistApi": "qwen_intl", "assistApiKeyQwenIntl": "sk-intl",
        "assistWorkspaceIdQwenIntl": "llm-sg",
        "resolvedProviderUrls": {"assist:qwen_intl": "https://dashscope-us.aliyuncs.com/compatible-mode/v1"},
    })
    us_dec = cm.get_model_api_config("decision")
    assert us_dec["base_url"].startswith("https://dashscope-us.")
    assert us_dec["decision_protocol"] == "chat_fallback"
    assert us_dec["native_unavailable_reason"] == "region_unsupported"
    assert "maas.aliyuncs.com" not in us_dec["decision_url"]

    # A hand-edited invalid ID is ignored on read (treated as unset), never put in a host.
    for bad in ("evil.com/x", "llm-abc.evil.com", "llm-abc@evil.com", "-bad"):
        _set_cfg({
            "coreApi": "qwen", "assistApi": "qwen", "coreApiKey": "sk-qwen",
            "assistWorkspaceIdQwen": bad,
        })
        bad_dec = cm.get_model_api_config("decision")
        assert bad_dec["decision_protocol"] == "chat_fallback", bad
        assert "evil" not in bad_dec["decision_url"], bad
        assert cm.get_core_config()["ASSIST_WORKSPACE_ID_QWEN"] == ""

    # A hand-entered custom decision URL on a workspace host still wins.
    _set_cfg({
        "coreApi": "qwen",
        "assistApi": "qwen",
        "coreApiKey": "sk-qwen",
        "enableCustomApi": True,
        "decisionModelProvider": "custom",
        "decisionModelUrl": "https://llm-manual.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
        "decisionModelId": "decision-model-preview",
        "decisionModelApiKey": "sk-manual",
    })
    manual_dec = cm.get_model_api_config("decision")
    assert manual_dec["decision_protocol"] == "systemone"
    assert manual_dec["decision_url"] == (
        "https://llm-manual.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/systemone"
    )
    _set_cfg({
        "coreApi": "qwen",
        "assistApi": "deepseek",
        "assistApiKeyDeepseek": "sk-ds",
        "assistWorkspaceIdQwen": "llm-ignored",
        "enableCustomApi": True,
        "decisionModelProvider": "qwen",
        "decisionModelUrl": "https://llm-manual.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
        "decisionModelId": "decision-model-preview",
        "decisionModelApiKey": "sk-manual",
    })
    manual_named_dec = cm.get_model_api_config("decision")
    assert manual_named_dec["decision_url"] == (
        "https://llm-manual.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/systemone"
    )

    # Custom slot pinned to provider qwen (official URL) uses the saved workspace ID.
    _set_cfg({
        "coreApi": "qwen",
        "assistApi": "deepseek",
        "assistApiKeyDeepseek": "sk-ds",
        "assistApiKeyQwen": "sk-qwen",
        "assistWorkspaceIdQwen": "llm-pinned",
        "enableCustomApi": True,
        "decisionModelProvider": "qwen",
        "decisionModelUrl": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "decisionModelId": "decision-model-preview",
        "decisionModelApiKey": "sk-qwen",
    })
    pinned_dec = cm.get_model_api_config("decision")
    assert pinned_dec["decision_protocol"] == "systemone"
    assert pinned_dec["decision_url"] == (
        "https://llm-pinned.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/systemone"
    )

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
    # on the Bailian workspace host built from the submitted workspace ID.
    with patch(
        "main_routers.config_router.connectivity._test_decision_endpoint",
        AsyncMock(return_value={"success": True}),
    ) as mock_dec_probe:
        scope_res = await test_connectivity(
            ConnectivityTestRequest(
                provider_key="qwen",
                provider_scope="decision",
                api_key="sk-qwen",
                workspace_id="LLM-Abc",
            )
        )
        assert scope_res["success"] is True
        assert "decision_fallback" not in scope_res
        assert mock_dec_probe.await_count == 1
        assert mock_dec_probe.await_args.args[0] == (
            "https://llm-abc.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/systemone"
        )
        assert mock_dec_probe.await_args.kwargs["model"] == "decision-model-preview"
        assert mock_dec_probe.await_args.kwargs["protocol"] == "systemone"

    with patch(
        "main_routers.config_router.connectivity._test_decision_endpoint",
        AsyncMock(return_value={"success": True}),
    ) as mock_dec_probe:
        intl_res = await test_connectivity(
            ConnectivityTestRequest(
                provider_key="qwen_intl",
                provider_scope="decision",
                api_key="sk-intl",
                workspace_id="llm-sg",
            )
        )
        assert intl_res["success"] is True
        assert mock_dec_probe.await_args.args[0] == (
            "https://llm-sg.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1/systemone"
        )

    # No workspace ID -> no doomed native request: probe the chat model the runtime
    # actually decides with, and tell the page it is the chat fallback.
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
        no_ws_res = await test_connectivity(
            ConnectivityTestRequest(provider_key="qwen", provider_scope="decision", api_key="sk-qwen")
        )
        assert no_ws_res["success"] is True
        assert no_ws_res["decision_fallback"] == "workspace_missing"
        assert mock_dec_probe.await_count == 0
        assert mock_oai.await_count == 1
        assert mock_oai.await_args.args[0] == "https://dashscope.aliyuncs.com/compatible-mode/v1"
        assert mock_oai.await_args.kwargs["model"] == "qwen3.7-flash"

    # Injection-style workspace IDs are rejected before any request is made.
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
        for bad in ("evil.com/x", "llm-abc.evil.com", "llm@evil.com", "a" * 64):
            bad_res = await test_connectivity(
                ConnectivityTestRequest(
                    provider_key="qwen", provider_scope="decision", api_key="sk-qwen", workspace_id=bad,
                )
            )
            assert bad_res["success"] is False
            assert bad_res["error_code"] == "invalid_workspace_id"
        assert mock_oai.await_count == 0
        assert mock_dec_probe.await_count == 0

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

    captured: dict = {"batches": []}

    async def _capture_decide(self, *, state, questions, **kwargs):
        captured["batches"].append((state, list(questions.keys())))
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
    all_ids = [qid for _state, ids in captured["batches"] for qid in ids]
    assert 0 < len(all_ids) < len(candidates)
    assert len(all_ids) == len(set(all_ids))
    for state, ids in captured["batches"]:
        for qid in ids:
            assert f"[{qid}]" in state


@pytest.mark.asyncio
@pytest.mark.unit
async def test_memory_recall_native_rerank_batches_16_questions_per_request():
    from memory.recall import MemoryRecallReranker
    from utils.decision_client.schema import DecisionAnswer, DecisionResponse

    cm = MagicMock()
    cm.aget_model_api_config = AsyncMock(return_value={
        "model": "decision-model-preview",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key": "sk-test",
        "decision_protocol": "systemone",
        "decision_url": "https://llm-abc.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/systemone",
    })
    # 40 short candidates all fit the native state budget -> 16 + 16 + 8.
    candidates = [{"id": f"m{i}", "text": f"fact number {i}", "score": float(40 - i)} for i in range(40)]
    batches: list[tuple[str, list[str]]] = []

    async def _decide(self, *, state, questions, **kwargs):
        batches.append((state, list(questions)))
        # Later candidates score higher so the native order is visible in the result.
        return DecisionResponse(
            answers={
                qid: DecisionAnswer(question_type="score", value=0.0, score_norm=int(qid[1:]) / 100)
                for qid in questions
            },
            model="decision-model-preview",
            protocol="systemone",
        )

    with patch.object(DecisionClient, "decide", _decide):
        ranked = await MemoryRecallReranker()._fine_rank(
            candidates,
            query_texts=["which fact?"],
            budget=30,
            config_manager=cm,
            prompt_loader=lambda _lang: "{QUERY}\n{CANDIDATES}\n{BUDGET}",
            lang="en",
        )

    assert [len(ids) for _state, ids in batches] == [16, 16, 8]
    for state, ids in batches:
        assert state.startswith("Query:\n- which fact?")
        # Each batch's state carries only its own candidates.
        own = set(ids)
        for i in range(40):
            assert (f"[m{i}]" in state) == (f"m{i}" in own)
    assert [r["id"] for r in ranked] == [f"m{i}" for i in range(39, 9, -1)]

    # One failed batch fails the whole native pass -> summary chat rerank is used.
    calls = {"n": 0}

    async def _decide_one_fails(self, *, state, questions, **kwargs):
        calls["n"] += 1
        if "[m20]" in state:
            raise DecisionProtocolError("HTTP 500", status_code=500, protocol="systemone")
        return DecisionResponse(
            answers={qid: DecisionAnswer(question_type="score", value=1.0, score_norm=0.5) for qid in questions},
            model="decision-model-preview",
            protocol="systemone",
        )

    summary_cfg = {
        "model": "qwen3.8-flash",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key": "sk-test",
        "provider_type": "openai_compatible",
    }
    decision_cfg = dict(cm.aget_model_api_config.return_value)
    cm.aget_model_api_config = AsyncMock(side_effect=lambda tier: decision_cfg if tier == "decision" else summary_cfg)
    fake_llm = SimpleNamespace(
        ainvoke=AsyncMock(return_value=SimpleNamespace(content='[{"id": "m1"}, {"id": "m0"}]')),
        aclose=AsyncMock(),
    )
    with (
        patch.object(DecisionClient, "decide", _decide_one_fails),
        patch("utils.llm_client.create_chat_llm_async", AsyncMock(return_value=fake_llm)),
    ):
        fallback_ranked = await MemoryRecallReranker()._fine_rank(
            candidates,
            query_texts=["which fact?"],
            budget=2,
            config_manager=cm,
            prompt_loader=lambda _lang: "{QUERY}\n{CANDIDATES}\n{BUDGET}",
            lang="en",
        )
    assert calls["n"] == 3
    assert [r["id"] for r in fallback_ranked] == ["m1", "m0"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_memory_recall_skips_native_without_workspace_id(clean_user_data_dir):
    """Default qwen config without a workspace ID never reaches the native endpoint."""
    from memory.recall import MemoryRecallReranker
    from utils.config_manager import get_config_manager

    cm = get_config_manager("N.E.K.O")
    cm.config_dir.mkdir(parents=True, exist_ok=True)
    cm.get_config_path("core_config.json").write_text(
        json.dumps({"coreApi": "qwen", "assistApi": "qwen", "coreApiKey": "sk-qwen"}), encoding="utf-8",
    )
    cm._core_config_cache = None

    fake_llm = SimpleNamespace(
        ainvoke=AsyncMock(return_value=SimpleNamespace(content='[{"id": "m2"}, {"id": "m1"}]')),
        aclose=AsyncMock(),
    )
    native_execute = AsyncMock(side_effect=AssertionError("native endpoint must not be called"))
    with (
        patch.object(SystemOneAdapter, "execute", native_execute),
        patch("utils.llm_client.create_chat_llm_async", AsyncMock(return_value=fake_llm)),
        patch("memory.recall.logger") as mock_logger,
    ):
        ranked = await MemoryRecallReranker()._fine_rank(
            [{"id": "m1", "text": "a", "score": 1.0}, {"id": "m2", "text": "b", "score": 1.0}],
            query_texts=["q"],
            budget=2,
            config_manager=cm,
            prompt_loader=lambda _lang: "{QUERY}\n{CANDIDATES}\n{BUDGET}",
            lang="en",
        )
    assert native_execute.await_count == 0
    assert mock_logger.warning.call_count == 0
    assert [r["id"] for r in ranked] == ["m2", "m1"]


# ---------------------------------------------------------------------------
# /api/config/core_api: workspace ID save-side and read-side validation
# ---------------------------------------------------------------------------


@pytest.fixture()
def _core_config_router_for_workspace(monkeypatch):
    from main_routers.config_router import core_config

    async def _noop(*args, **kwargs):
        return None

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def post(self, *args, **kwargs):
            return None

    monkeypatch.setattr(core_config, "get_session_manager", lambda: {})
    monkeypatch.setattr(core_config, "get_initialize_character_data", lambda: _noop)
    monkeypatch.setattr(core_config, "ensure_default_yui_voice_for_free_api", _noop)
    monkeypatch.setattr(core_config, "_auto_resolve_provider_urls_for_save", _noop)

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    return core_config


class _FakeJsonRequest:
    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        return self._payload


@pytest.mark.unit
def test_core_api_save_validates_workspace_ids(clean_user_data_dir, _core_config_router_for_workspace):
    import asyncio

    from utils.config_manager import get_config_manager

    router = _core_config_router_for_workspace
    assert router.CORE_CONFIG_WORKSPACE_ID_FIELDS == ("assistWorkspaceIdQwen", "assistWorkspaceIdQwenIntl")
    cm = get_config_manager("N.E.K.O")
    cm.config_dir.mkdir(parents=True, exist_ok=True)
    stored = {"coreApi": "qwen", "assistApi": "qwen", "coreApiKey": "sk-qwen", "assistWorkspaceIdQwen": "llm-old"}
    cm.save_json_config("core_config.json", stored)
    cm._core_config_cache = None

    def _save(extra):
        return asyncio.run(router.update_core_config(_FakeJsonRequest({
            "coreApiKey": "sk-qwen", "coreApi": "qwen", "assistApi": "qwen", "enableCustomApi": False, **extra,
        })))

    # Injection-style values are rejected and nothing is written.
    for bad in ("evil.com", "llm-abc.evil.com", "llm@evil.com", "evil.com/x", "a" * 64, "-x", 7):
        resp = _save({"assistWorkspaceIdQwen": bad})
        assert resp["success"] is False, bad
        assert resp["error"] == "INVALID_WORKSPACE_ID"
        assert cm.load_json_config("core_config.json", {})["assistWorkspaceIdQwen"] == "llm-old"

    # A valid value is stored in canonical form; empty clears it.
    assert _save({"assistWorkspaceIdQwen": "  LLM-New ", "assistWorkspaceIdQwenIntl": "llm-sg"})["success"] is True
    saved = cm.load_json_config("core_config.json", {})
    assert saved["assistWorkspaceIdQwen"] == "llm-new"
    assert saved["assistWorkspaceIdQwenIntl"] == "llm-sg"
    assert _save({"assistWorkspaceIdQwenIntl": ""})["success"] is True
    assert cm.load_json_config("core_config.json", {})["assistWorkspaceIdQwenIntl"] == ""

    # Read side: GET returns the stored ID, and a hand-edited invalid value reads as unset.
    cm.save_json_config("core_config.json", {**stored, "assistWorkspaceIdQwen": "llm-x.evil.com"})
    cm._core_config_cache = None
    got = asyncio.run(router.get_core_config_api())
    assert got["success"] is True
    assert got["assistWorkspaceIdQwen"] == ""
    assert got["assistWorkspaceIdQwenIntl"] == ""


@pytest.mark.asyncio
@pytest.mark.unit
async def test_concurrent_success_does_not_clear_cooldown_set_by_failed_sibling():
    """Recall batches share one cooldown key: a later success must not wipe a sibling's failure."""
    import asyncio

    from utils.decision_client.schema import DecisionAnswer, DecisionResponse

    cfg = {
        "model": "decision-model-preview",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key": "sk-test",
        "decision_protocol": "systemone",
        "decision_url": "https://llm-abc.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/systemone",
    }
    release_slow = asyncio.Event()
    ok_resp = DecisionResponse(
        answers={"slow": DecisionAnswer(question_type="bool", value=True, probability=0.9)},
        model="decision-model-preview",
        protocol="systemone",
    )

    async def _execute(*, questions, **kwargs):
        if "slow" in questions:
            await release_slow.wait()
            return ok_resp
        try:
            raise DecisionProtocolError("HTTP 500", status_code=500, protocol="systemone")
        finally:
            release_slow.set()

    execute = AsyncMock(side_effect=_execute)
    client = DecisionClient()
    with patch.object(SystemOneAdapter, "execute", execute):
        slow = asyncio.create_task(client.decide(
            state="s", questions={"slow": BoolQuestion(instructions="?")}, config=cfg, allow_fallback=False,
        ))
        await asyncio.sleep(0)
        with pytest.raises(DecisionProtocolError):
            await client.decide(
                state="s", questions={"fast": BoolQuestion(instructions="?")}, config=cfg, allow_fallback=False,
            )
        assert (await slow).answers["slow"].value is True
        assert execute.await_count == 2

        # The failure's cooldown survived the sibling's later success.
        with pytest.raises(DecisionProtocolError, match="cooldown"):
            await client.decide(
                state="s", questions={"next": BoolQuestion(instructions="?")}, config=cfg, allow_fallback=False,
            )
        assert execute.await_count == 2

        # A request that starts after the failure (a manual probe bypassing the
        # cooldown) and succeeds does clear it.
        release_slow.set()
        await client.decide(
            state="s", questions={"slow": BoolQuestion(instructions="?")}, config=cfg,
            allow_fallback=False, respect_cooldown=False,
        )
        await client.decide(
            state="s", questions={"slow": BoolQuestion(instructions="?")}, config=cfg, allow_fallback=False,
        )
        assert execute.await_count == 4


@pytest.mark.asyncio
@pytest.mark.unit
async def test_connectivity_decision_probe_follows_resolved_region():
    from main_routers.config_router.connectivity import ConnectivityTestRequest, test_connectivity

    def _patches():
        return (
            patch(
                "main_routers.config_router.connectivity._test_openai_compatible",
                AsyncMock(return_value={"success": True}),
            ),
            patch(
                "main_routers.config_router.connectivity._test_decision_endpoint",
                AsyncMock(return_value={"success": True}),
            ),
        )

    us = "https://dashscope-us.aliyuncs.com/compatible-mode/v1"
    sg = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"

    # Key resolved to the US host: runtime decides with chat there, so probe that.
    p_oai, p_dec = _patches()
    with p_oai as mock_oai, p_dec as mock_dec:
        res = await test_connectivity(ConnectivityTestRequest(
            provider_key="qwen_intl", provider_scope="decision", api_key="sk-us",
            workspace_id="llm-sg", url=us,
        ))
    assert res["success"] is True
    assert res["decision_fallback"] == "region_unsupported"
    assert mock_dec.await_count == 0
    assert mock_oai.await_args.args[0] == us
    assert mock_oai.await_args.kwargs["model"] == "qwen3.7-flash"

    # Primary (Singapore) host with an ID: the workspace decision host.
    p_oai, p_dec = _patches()
    with p_oai as mock_oai, p_dec as mock_dec:
        res = await test_connectivity(ConnectivityTestRequest(
            provider_key="qwen_intl", provider_scope="decision", api_key="sk-sg",
            workspace_id="llm-sg", url=sg,
        ))
    assert res["success"] is True and "decision_fallback" not in res
    assert mock_oai.await_count == 0
    assert mock_dec.await_args.args[0] == (
        "https://llm-sg.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1/systemone"
    )

    # A chat-model override with the resolved URL probes chat on that URL.
    p_oai, p_dec = _patches()
    with p_oai as mock_oai, p_dec as mock_dec:
        res = await test_connectivity(ConnectivityTestRequest(
            provider_key="qwen_intl", provider_scope="decision", api_key="sk-us",
            model="qwen3.8-flash", url=us,
        ))
    assert res["success"] is True
    assert mock_dec.await_count == 0
    assert mock_oai.await_args.args[0] == us

    # A URL that is not one of the provider's own endpoints is refused.
    p_oai, p_dec = _patches()
    with p_oai as mock_oai, p_dec as mock_dec:
        res = await test_connectivity(ConnectivityTestRequest(
            provider_key="qwen_intl", provider_scope="decision", api_key="sk",
            workspace_id="llm-sg", url="https://evil.example/compatible-mode/v1",
        ))
    assert res["success"] is False
    assert mock_oai.await_count == 0 and mock_dec.await_count == 0


@pytest.mark.unit
def test_systemone_score_probabilities_with_digit_labels_are_not_misread_as_indexes():
    adapter = SystemOneAdapter("systemone")
    q = {"s": ScoreQuestion(instructions="rate", levels=("1", "2"))}
    # Label-keyed map whose labels look like digits: "2" is no valid index, so labels win.
    by_label = adapter.parse_response(
        {"answers": {"s": {"type": "score", "probabilities": {"1": 0.2, "2": 0.8}}}}, q, "m",
    )
    assert by_label.answers["s"].probabilities == {"1": pytest.approx(0.2), "2": pytest.approx(0.8)}
    assert by_label.answers["s"].value == pytest.approx(0.8)
    # The documented index-keyed map still maps index -> label.
    by_index = adapter.parse_response(
        {"answers": {"s": {"type": "score", "probabilities": {"0": 0.2, "1": 0.8}}}}, q, "m",
    )
    assert by_index.answers["s"].probabilities == {"1": pytest.approx(0.2), "2": pytest.approx(0.8)}
    assert by_index.answers["s"].value == pytest.approx(0.8)


@pytest.mark.asyncio
@pytest.mark.unit
async def test_memory_recall_cancels_in_flight_batches_after_a_failure():
    import asyncio

    from memory.recall import MemoryRecallReranker

    decision_cfg = {
        "model": "decision-model-preview",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key": "sk-test",
        "decision_protocol": "systemone",
        "decision_url": "https://llm-abc.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/systemone",
    }
    summary_cfg = {
        "model": "qwen3.8-flash",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key": "sk-test",
        "provider_type": "openai_compatible",
    }
    cm = MagicMock()
    cm.aget_model_api_config = AsyncMock(side_effect=lambda tier: decision_cfg if tier == "decision" else summary_cfg)
    candidates = [{"id": f"m{i}", "text": f"fact {i}", "score": float(20 - i)} for i in range(20)]
    slow_cancelled = asyncio.Event()

    async def _decide(self, *, state, questions, **kwargs):
        if "m0" in questions:
            raise DecisionProtocolError("HTTP 500", status_code=500, protocol="systemone")
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            slow_cancelled.set()
            raise
        raise AssertionError("slow batch should have been cancelled")

    fake_llm = SimpleNamespace(
        ainvoke=AsyncMock(return_value=SimpleNamespace(content='[{"id": "m1"}, {"id": "m0"}]')),
        aclose=AsyncMock(),
    )
    with (
        patch.object(DecisionClient, "decide", _decide),
        patch("utils.llm_client.create_chat_llm_async", AsyncMock(return_value=fake_llm)),
    ):
        ranked = await MemoryRecallReranker()._fine_rank(
            candidates,
            query_texts=["q"],
            budget=2,
            config_manager=cm,
            prompt_loader=lambda _lang: "{QUERY}\n{CANDIDATES}\n{BUDGET}",
            lang="en",
        )
    assert slow_cancelled.is_set()
    assert [r["id"] for r in ranked] == ["m1", "m0"]
