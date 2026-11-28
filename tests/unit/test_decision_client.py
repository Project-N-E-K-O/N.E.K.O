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
    from utils.decision_client import adapters
    from utils.decision_client.openrouter_catalog import clear_openrouter_catalog

    clear_native_cooldown()
    clear_openrouter_catalog()
    adapters._http_clients.clear()
    try:
        yield
    finally:
        clear_native_cooldown()
        clear_openrouter_catalog()
        adapters._http_clients.clear()


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
        # SiliconFlow serves Kev-4B / SemIf on /v1/systemone; the bare IDs mean nothing elsewhere.
        ("Kev-4B", "https://api.siliconflow.cn/v1", "systemone", "Kev-4B", "systemone"),
        ("SemIf", "https://api.siliconflow.cn/v1", "systemone", "Kev-4B", "systemone"),
        ("kev-4b", "https://api.siliconflow.cn/v1", None, None, "systemone"),
        ("Qwen/Qwen3-8B", "https://api.siliconflow.cn/v1", "systemone", "Kev-4B", "chat_fallback"),
        ("Kev-4B", "https://api.deepseek.com/v1", None, None, "chat_fallback"),
        # Bailian's released decision model is the bare ID.
        ("decision-model", "https://dashscope.aliyuncs.com/compatible-mode/v1", "systemone", "decision-model", "systemone"),
        ("decision-model", "https://my-gateway.example/v1", None, None, "systemone"),
        ("decision-model-x", "https://my-gateway.example/v1", None, None, "systemone"),
        ("decision-modeller", "https://my-gateway.example/v1", None, None, "chat_fallback"),
        # The System One routers serve the OpenRouter catalog on their own /systemone route.
        ("typesafe/jev-1.14", "https://api.orcarouter.ai/v1", "systemone", "typesafe/jev-1.14", "systemone"),
        ("typesafe/jev-1.13", "https://api.orcarouter.ai/v1", "systemone", "typesafe/jev-1.14", "systemone"),
        ("typesafe/jev-1.14.0", "https://router.requesty.ai/v1", None, None, "systemone"),
        ("typesafe/jev-1.14.0", "https://router.eu.requesty.ai/v1", None, None, "systemone"),
        ("typesafe/jev-1.14.0", "https://api.opper.ai/v3/compat", "systemone", "typesafe/jev-1.14.0", "systemone"),
        ("liquid/d1.2-ultra", "https://openrouter.ai/api/v1", "openrouter_decisions", "liquid/d1.2-ultra", "openrouter_decisions"),
        ("anthropic/claude-haiku-4.5", "https://api.orcarouter.ai/v1", "systemone", "typesafe/jev-1.14", "chat_fallback"),
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
        "qwen": ("decision-model", "systemone"),
        "qwen_intl": ("decision-model", "systemone"),
        "openai": ("gpt-6-luna", "openai_decisions"),
        "gemini": ("gemini-decision-1", "gemini_decisions"),
        "openrouter": ("liquid/d1.2-ultra", "openrouter_decisions"),
        "silicon": ("Kev-4B", "systemone"),
        "orcarouter": ("typesafe/jev-1.14", "systemone"),
        "requesty": ("typesafe/jev-1.14.0", "systemone"),
        "opper": ("typesafe/jev-1.14.0", "systemone"),
        "doubao": ("doubao-seed-2-1-lite-260915", "systemone"),
    }
    assert set(native_expected.keys()).issubset(providers.keys())
    for key, profile in providers.items():
        if profile.get("model_slot_only"):
            # Never the assist provider and not offered to the decision slot
            # (ChatGPT subscription), so no decision tier resolves through it.
            assert key not in native_expected, f"{key} must keep its decision fields"
            assert "decision" not in profile.get("supported_model_types", [])
            continue
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

        async def post(self, url, json=None, headers=None, **kwargs):
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
# Bailian workspace API Host parsing and dedicated-host URL building
# ---------------------------------------------------------------------------

_BJ_CHAT = "https://dashscope.aliyuncs.com/compatible-mode/v1"
_SG_CHAT = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
_US_CHAT = "https://dashscope-us.aliyuncs.com/compatible-mode/v1"
_HK_CHAT = "https://cn-hongkong.dashscope.aliyuncs.com/compatible-mode/v1"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # A bare workspace ID (current ws- form and the older llm- form).
        ("ws-lp9lqfo9ycxyrv5m", "ws-lp9lqfo9ycxyrv5m"),
        ("llm-abc123", "llm-abc123"),
        ("  LLM-AbC123  ", "llm-abc123"),
        ("a", "a"),
        ("a" * 63, "a" * 63),
        ("llm-abc\n", "llm-abc"),
        # The API Host as the console shows it, and URLs on it.
        ("ws-abc.cn-beijing.maas.aliyuncs.com", "ws-abc.cn-beijing.maas.aliyuncs.com"),
        ("WS-Abc.AP-Southeast-1.MAAS.Aliyuncs.com", "ws-abc.ap-southeast-1.maas.aliyuncs.com"),
        ("https://ws-abc.cn-beijing.maas.aliyuncs.com", "ws-abc.cn-beijing.maas.aliyuncs.com"),
        ("https://ws-abc.cn-beijing.maas.aliyuncs.com/compatible-mode/v1", "ws-abc.cn-beijing.maas.aliyuncs.com"),
        ("http://ws-abc.us-east-1.maas.aliyuncs.com/?x=1#y", "ws-abc.us-east-1.maas.aliyuncs.com"),
        ("", ""),
        ("   ", ""),
        (None, ""),
    ],
)
def test_workspace_host_normalization_accepts_ids_hosts_and_urls(raw, expected):
    from utils.decision_client.workspace import normalize_workspace_host, safe_workspace_host

    assert normalize_workspace_host(raw) == expected
    assert safe_workspace_host(raw) == expected


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
        "ws-abc.cn-beijing.maas.aliyuncs.com.evil.com",
        "ws-abc.cn-beijing-maas-aliyuncs-com",  # look-alike suffix of the same length
        "ws-abc.cn-beijing.maas.aliyuncs.com:443",
        "ws-abc.cn-beijing.maas.aliyuncs.com.",
        "evil.ws-abc.cn-beijing.maas.aliyuncs.com",
        "ws-abc.maas.aliyuncs.com",
        "ws_abc.cn-beijing.maas.aliyuncs.com",
        "ws-abc.cn_beijing.maas.aliyuncs.com",
        "user@ws-abc.cn-beijing.maas.aliyuncs.com",
        "https://user@ws-abc.cn-beijing.maas.aliyuncs.com/",
        "https://evil.com/ws-abc.cn-beijing.maas.aliyuncs.com",
        "ftp://ws-abc.cn-beijing.maas.aliyuncs.com",
        "a" * 64 + ".cn-beijing.maas.aliyuncs.com",
        "ws-abc.cn-beijing.maas.aliyuncs.com/" + "x" * 600,
        123,
        ["llm-abc"],
    ],
)
def test_workspace_host_rejects_anything_that_could_change_the_host(raw):
    from utils.api_config_loader import get_assist_api_profiles
    from utils.decision_client.workspace import (
        InvalidWorkspaceHostError,
        normalize_workspace_host,
        resolve_workspace_decision_url,
        safe_workspace_host,
    )

    with pytest.raises(InvalidWorkspaceHostError):
        normalize_workspace_host(raw)
    assert safe_workspace_host(raw) == ""
    template = get_assist_api_profiles(force_reload=True)["qwen"]["DECISION_URL_TEMPLATE"]
    assert resolve_workspace_decision_url(template, raw, _BJ_CHAT) == ("", "workspace_missing")


@pytest.mark.unit
def test_resolve_workspace_decision_url_takes_region_from_host_or_chat_endpoint():
    from utils.api_config_loader import get_assist_api_profiles
    from utils.decision_client.workspace import resolve_workspace_decision_url as resolve

    profiles = get_assist_api_profiles(force_reload=True)
    tpl = profiles["qwen"]["DECISION_URL_TEMPLATE"]
    assert profiles["qwen_intl"]["DECISION_URL_TEMPLATE"] == tpl

    def url(host):
        return f"https://{host}/compatible-mode/v1/systemone"

    # A bare ID takes the region of the chat endpoint the key resolved to.
    assert resolve(tpl, "LLM-Abc", _BJ_CHAT) == (url("llm-abc.cn-beijing.maas.aliyuncs.com"), "")
    assert resolve(tpl, "llm-abc", _SG_CHAT) == (url("llm-abc.ap-southeast-1.maas.aliyuncs.com"), "")
    assert resolve(tpl, "llm-abc", _US_CHAT) == ("", "region_unsupported")
    assert resolve(tpl, "llm-abc", _HK_CHAT) == ("", "region_unsupported")
    assert resolve(tpl, "llm-abc", "https://gw.example/v1") == ("", "region_unsupported")
    # A full API Host keeps its own region whatever the chat endpoint is.
    assert resolve(tpl, "ws-x.ap-southeast-1.maas.aliyuncs.com", _US_CHAT) == (
        url("ws-x.ap-southeast-1.maas.aliyuncs.com"), "",
    )
    assert resolve(tpl, "ws-x.cn-beijing.maas.aliyuncs.com", "") == (url("ws-x.cn-beijing.maas.aliyuncs.com"), "")
    assert resolve(tpl, "ws-x.us-east-1.maas.aliyuncs.com", _BJ_CHAT) == ("", "region_unsupported")
    assert resolve(tpl, "ws-x.eu-central-1.maas.aliyuncs.com", _SG_CHAT) == ("", "region_unsupported")
    # Nothing saved: say the region has no decision model before asking for a host.
    assert resolve(tpl, "", _BJ_CHAT) == ("", "workspace_missing")
    assert resolve(tpl, "", "") == ("", "workspace_missing")
    assert resolve(tpl, "", _US_CHAT) == ("", "region_unsupported")
    # Defense in depth: the host must land as the whole hostname of an https URL.
    for bad_tpl in (
        "https://example.com/systemone",
        "http://{workspace_host}/v1",
        "https://user@{workspace_host}/v1",
        "https://{workspace_host}.evil.example/v1",
        "https://evil.example/{workspace_host}/v1",
        "https://{workspace_host}:8443/v1",
        "https://{workspace_host}:0/v1",
        # Malformed or out-of-range ports make urlsplit().port raise.
        "https://{workspace_host}:bad/v1",
        "https://{workspace_host}:65536/v1",
    ):
        assert resolve(bad_tpl, "llm-abc", _BJ_CHAT) == ("", "workspace_missing"), bad_tpl
    # Only providers whose native endpoint needs a workspace carry a template.
    templated = {key for key, prof in profiles.items() if prof.get("DECISION_URL_TEMPLATE")}
    assert templated == {"qwen", "qwen_intl"}


@pytest.mark.unit
def test_region_from_chat_url_knows_shared_and_workspace_hosts():
    from utils.decision_client.workspace import region_from_chat_url

    assert region_from_chat_url(_BJ_CHAT) == "cn-beijing"
    assert region_from_chat_url(_SG_CHAT) == "ap-southeast-1"
    assert region_from_chat_url(_US_CHAT) == "us-east-1"
    assert region_from_chat_url(_HK_CHAT) == "cn-hongkong"
    assert region_from_chat_url("https://ws-a.eu-central-1.maas.aliyuncs.com/compatible-mode/v1") == "eu-central-1"
    assert region_from_chat_url("https://dashscope.aliyuncs.com.evil.example/v1") == ""
    assert region_from_chat_url("https://x.y.cn-beijing.maas.aliyuncs.com/v1") == ""
    assert region_from_chat_url("") == ""


@pytest.mark.unit
def test_stored_workspace_host_prefers_host_field_over_legacy_id():
    from utils.decision_client.workspace import stored_workspace_host

    host = "ws-a.cn-beijing.maas.aliyuncs.com"
    assert stored_workspace_host({"h": host, "l": "llm-old"}, "h", "l") == host
    assert stored_workspace_host({"l": "LLM-Old"}, "h", "l") == "llm-old"
    assert stored_workspace_host({"h": "  ", "l": "llm-old"}, "h", "l") == "llm-old"
    # An invalid host field is unset; it never falls back to a stale legacy ID.
    assert stored_workspace_host({"h": "evil.com", "l": "llm-old"}, "h", "l") == ""
    assert stored_workspace_host({"l": "evil.com"}, "h", "l") == ""
    assert stored_workspace_host({}, "h", "l") == ""


@pytest.mark.unit
def test_openai_decisions_adapter_roundtrip():
    adapter = OpenAIDecisionsAdapter()
    qs = _sample_questions()
    payload = adapter.build_payload(state="App crashes on export", questions=qs, model="gpt-6-luna")
    assert payload["model"] == "gpt-6-luna"
    assert payload["input"] == "App crashes on export"
    assert isinstance(payload["questions"], list)
    by_name = {q["name"]: q for q in payload["questions"]}
    # Wire shapes the live endpoint accepts (2026-11-25): predicates fold the
    # true/false descriptions into instructions, choices and levels are objects.
    assert by_name["is_urgent"] == {
        "name": "is_urgent",
        "type": "predicate",
        "instructions": "Is this urgent?\nTrue: Urgent\nFalse: Not urgent",
    }
    assert by_name["route"]["choices"] == [
        {"value": "billing", "description": "Invoices"},
        {"value": "tech", "description": "Bugs"},
    ]
    assert by_name["route"]["instructions"] == "Pick queue"
    assert by_name["severity"]["levels"] == [{"label": "low"}, {"label": "medium"}, {"label": "high"}]
    no_desc = adapter.build_payload(
        state="s",
        questions={"c": ChoiceQuestion(instructions="Pick", criteria={"a": "", "b": ""})},
        model="gpt-6-luna",
    )
    assert no_desc["questions"][0]["choices"] == [{"value": "a"}, {"value": "b"}]

    # Response shape the live endpoint returns.
    resp = adapter.parse_response(
        {
            "model": "gpt-6-luna",
            "answers": [
                {"type": "predicate", "name": "is_urgent", "probability": 0.97},
                {
                    "type": "choice",
                    "name": "route",
                    "choice": "tech",
                    "probabilities": [
                        {"value": "billing", "probability": 0.0},
                        {"value": "tech", "probability": 1.0},
                    ],
                    "confidence": 1.0,
                },
                {
                    "type": "score",
                    "name": "severity",
                    "score": 0.86,
                    "probabilities": [
                        {"value": 0, "label": "low", "probability": 0.21},
                        {"value": 1, "label": "medium", "probability": 0.72},
                        {"value": 2, "label": "high", "probability": 0.07},
                    ],
                    "confidence": 0.58,
                },
            ],
            "usage": {
                "input_tokens": 128,
                "input_tokens_details": {"cached_tokens": 5, "cache_write_tokens": 0},
                "output_tokens": 0,
                "total_tokens": 128,
            },
        },
        qs,
        "gpt-6-luna",
    )
    assert resp.protocol == "openai_decisions"
    assert resp.answers["is_urgent"].value is True
    assert resp.answers["is_urgent"].probability == pytest.approx(0.97)
    assert resp.answers["route"].value == "tech"
    assert resp.answers["route"].probabilities == {"billing": 0.0, "tech": 1.0}
    assert resp.answers["route"].confidence == pytest.approx(1.0)
    assert resp.answers["severity"].value == pytest.approx(0.86)
    assert resp.answers["severity"].score_norm == pytest.approx(0.43)
    assert resp.answers["severity"].probabilities == {"low": 0.21, "medium": 0.72, "high": 0.07}
    assert (resp.prompt_tokens, resp.completion_tokens, resp.cached_tokens) == (128, 0, 5)

    # Without a score, the expectation over index-only probabilities is used.
    index_only = adapter.parse_response(
        {"answers": [{
            "name": "severity",
            "probabilities": [{"value": 0, "probability": 0.0}, {"value": 2, "probability": 1.0}],
        }]},
        {"severity": qs["severity"]},
        "gpt-6-luna",
    )
    assert index_only.answers["severity"].value == pytest.approx(2.0)
    assert index_only.answers["severity"].probabilities == {"low": 0.0, "high": 1.0}

    # Older map-shaped probabilities and selected/score_index keys still parse.
    legacy = adapter.parse_response(
        {
            "answers": [
                {"name": "is_urgent", "value": True, "probability": 0.91},
                {"name": "route", "selected": "tech", "probabilities": {"billing": 0.08, "tech": 0.92}},
                {"name": "severity", "score_index": 2, "probabilities": {"low": 0.01, "medium": 0.09, "high": 0.90}},
            ],
            "usage": {"prompt_tokens": 64, "completion_tokens": 0},
        },
        qs,
        "gpt-6-luna",
    )
    assert legacy.answers["route"].value == "tech"
    assert legacy.answers["severity"].value == pytest.approx(2.0)
    assert legacy.prompt_tokens == 64


@pytest.mark.unit
def test_gemini_decisions_adapter_roundtrip():
    adapter = GeminiDecisionsAdapter()
    qs = _sample_questions()
    payload = adapter.build_payload(state="App crashes on export", questions=qs, model="gemini-decision-1")
    assert payload["model"] == "gemini-decision-1"
    assert payload["input"] == "App crashes on export"
    assert "state" not in payload
    # noul criteria rejects every key, so the descriptions go into the instructions.
    assert payload["questions"]["is_urgent"] == {
        "type": "noul",
        "instructions": "Is this urgent?\nTrue: Urgent\nFalse: Not urgent",
    }
    assert payload["questions"]["route"]["criteria"] == {"billing": "Invoices", "tech": "Bugs"}
    # Score levels go in criteria (0-based), not a legend.
    assert payload["questions"]["severity"] == {
        "type": "score",
        "instructions": "Rate severity",
        "criteria": ["low", "medium", "high"],
    }
    # An object state is sent as JSON text: the endpoint rejects plain objects.
    obj_payload = adapter.build_payload(state={"user": "累"}, questions={"x": qs["is_urgent"]}, model="m")
    assert obj_payload["input"] == '{"user": "累"}'

    # Response shape the live endpoint returns (score and probability keys 0-based).
    resp = adapter.parse_response(
        {
            "answers": {
                "is_urgent": {"confidence": 0.97, "noul": 0.97, "type": "noul"},
                "route": {
                    "confidence": 0.99,
                    "choice": "tech",
                    "probabilities": {"billing": 0.01, "tech": 0.99},
                    "type": "choice",
                },
                "severity": {
                    "confidence": 0.55,
                    "score": 0.9,
                    "probabilities": {"2": 0.18, "0": 0.27, "1": 0.55},
                    "legend": {"1": "medium", "0": "low", "2": "high"},
                    "type": "score",
                },
            },
            "usage": {
                "total_tokens": 105,
                "total_input_tokens": 105,
                "input_tokens_by_modality": [{"modality": "text", "tokens": 105}],
                "total_output_tokens": 0,
            },
        },
        qs,
        "gemini-decision-1",
    )
    assert resp.protocol == "gemini_decisions"
    assert resp.model == "gemini-decision-1"
    assert resp.prompt_tokens == 105
    assert resp.completion_tokens == 0
    assert resp.answers["is_urgent"].value is True
    assert resp.answers["route"].value == "tech"
    assert resp.answers["severity"].value == pytest.approx(0.9)
    assert resp.answers["severity"].score_norm == pytest.approx(0.45)
    assert resp.answers["severity"].probabilities == {"low": 0.27, "medium": 0.55, "high": 0.18}

    # Older usage metadata spellings still count.
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


class _RecordingAsyncClient:
    """Stands in for httpx.AsyncClient and answers every decision question."""

    sent: list[dict] = []

    def __init__(self, *args, **kwargs):
        self.is_closed = False

    async def post(self, url, json=None, headers=None, **kwargs):
        type(self).sent.append({"url": url, "json": json, "headers": headers, **kwargs})
        questions = json["questions"]
        names = [q["name"] for q in questions] if isinstance(questions, list) else list(questions)
        return SimpleNamespace(
            status_code=200,
            json=lambda: {"answers": {qid: {"noul": 0.9} for qid in names}, "usage": {"total_input_tokens": 7}},
        )


@pytest.mark.asyncio
@pytest.mark.unit
async def test_gemini_adapter_sends_key_header_and_batches_64_questions():
    _RecordingAsyncClient.sent = []
    questions = {f"q{i}": BoolQuestion(instructions=f"q {i}?") for i in range(65)}
    with patch("utils.decision_client.adapters.httpx.AsyncClient", _RecordingAsyncClient):
        resp = await GeminiDecisionsAdapter().execute(
            state="s", questions=questions, model="gemini-decision-1",
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/", api_key="AIza-test",
        )
    sent = _RecordingAsyncClient.sent
    assert [len(s["json"]["questions"]) for s in sent] == [64, 1]
    assert {s["url"] for s in sent} == {"https://generativelanguage.googleapis.com/v1beta/decisions"}
    assert all(s["headers"]["x-goog-api-key"] == "AIza-test" for s in sent)
    assert all("Authorization" not in s["headers"] for s in sent)
    assert all(s["timeout"] == 8.0 for s in sent)
    assert set(resp.answers) == set(questions)
    assert resp.prompt_tokens == 14


@pytest.mark.asyncio
@pytest.mark.unit
async def test_decision_requests_reuse_one_pooled_client_per_event_loop():
    import asyncio

    from utils.decision_client import adapters

    created: list[object] = []

    class _CountingClient(_RecordingAsyncClient):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            created.append(self)

    _RecordingAsyncClient.sent = []
    q = {"t": BoolQuestion(instructions="ok?")}
    with patch("utils.decision_client.adapters.httpx.AsyncClient", _CountingClient):
        for _ in range(3):
            await SystemOneAdapter("systemone").execute(
                state="s", questions=q, model="decision-model-preview", base_url="",
                api_key="sk", decision_url="https://ws-a.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/systemone",
            )
        assert len(created) == 1
        # A closed client is replaced instead of reused.
        created[0].is_closed = True
        await SystemOneAdapter("systemone").execute(
            state="s", questions=q, model="decision-model-preview", base_url="",
            api_key="sk", decision_url="https://ws-a.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/systemone",
        )
        assert len(created) == 2
        assert adapters._http_clients[asyncio.get_running_loop()] is created[1]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_systemone_workspace_mismatch_is_reported_with_its_own_code():
    class _Forbidden(_RecordingAsyncClient):
        async def post(self, url, json=None, headers=None, **kwargs):
            body = {"error": {"message": "Workspace endpoint access denied.", "type": "access_denied", "code": "access_denied"}}
            return SimpleNamespace(status_code=403, json=lambda: body, text="")

    with patch("utils.decision_client.adapters.httpx.AsyncClient", _Forbidden):
        with pytest.raises(DecisionProtocolError) as info:
            await SystemOneAdapter("systemone").execute(
                state="s", questions={"t": BoolQuestion(instructions="ok?")}, model="decision-model-preview",
                base_url="", api_key="sk-ws-x",
                decision_url="https://ws-other.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/systemone",
            )
    assert info.value.status_code == 403
    assert info.value.error_code == "workspace_mismatch"
    assert info.value.permanent_route_error is True
    from utils.decision_client import adapters

    adapters._http_clients.clear()

    # Other 403s (e.g. a model the workspace may not use) keep no special code.
    class _ModelDenied(_RecordingAsyncClient):
        async def post(self, url, json=None, headers=None, **kwargs):
            body = {"error": {"message": "Access denied", "type": "access_denied", "code": "access_denied"}}
            return SimpleNamespace(status_code=403, json=lambda: body, text="")

    with patch("utils.decision_client.adapters.httpx.AsyncClient", _ModelDenied):
        with pytest.raises(DecisionProtocolError) as info:
            await SystemOneAdapter("systemone").execute(
                state="s", questions={"t": BoolQuestion(instructions="ok?")}, model="decision-model",
                base_url="", api_key="sk-ws-x",
                decision_url="https://ws-a.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/systemone",
            )
    assert info.value.error_code == ""


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
                "zero": {"score": 0, "confidence": 0.8},  # 0-based, like System One
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
    assert qwen_dec["native_decision_model"] == "decision-model"
    assert "systemone" not in qwen_dec["decision_url"]
    assert qwen_dec["fallback_model"] == "qwen3.7-flash"

    # With a workspace ID -> native systemone on the Beijing workspace host.
    _set_cfg({
        "coreApi": "qwen", "assistApi": "qwen", "coreApiKey": "sk-qwen",
        "assistWorkspaceIdQwen": "LLM-Abc123",
    })
    qwen_ws_dec = cm.get_model_api_config("decision")
    assert qwen_ws_dec["model"] == "decision-model"
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
        assert cm.get_core_config()["ASSIST_WORKSPACE_HOST_QWEN"] == ""

    # The saved API Host (current field) wins over a leftover legacy ID and
    # carries its own region.
    _set_cfg({
        "coreApi": "qwen", "assistApi": "qwen", "coreApiKey": "sk-qwen",
        "assistWorkspaceIdQwen": "llm-old",
        "assistWorkspaceHostQwen": "WS-New.cn-beijing.maas.aliyuncs.com",
    })
    host_dec = cm.get_model_api_config("decision")
    assert host_dec["decision_protocol"] == "systemone"
    assert host_dec["decision_url"] == (
        "https://ws-new.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/systemone"
    )
    assert cm.get_core_config()["ASSIST_WORKSPACE_HOST_QWEN"] == "ws-new.cn-beijing.maas.aliyuncs.com"
    # qwen_intl on the US endpoint with a Singapore API Host uses that host.
    _set_cfg({
        "coreApi": "qwen", "assistApi": "qwen_intl", "assistApiKeyQwenIntl": "sk-intl",
        "assistWorkspaceHostQwenIntl": "ws-sg.ap-southeast-1.maas.aliyuncs.com",
        "resolvedProviderUrls": {"assist:qwen_intl": "https://dashscope-us.aliyuncs.com/compatible-mode/v1"},
    })
    sg_host_dec = cm.get_model_api_config("decision")
    assert sg_host_dec["decision_protocol"] == "systemone"
    assert sg_host_dec["decision_url"] == (
        "https://ws-sg.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1/systemone"
    )
    # An API Host in a region without a decision endpoint decides with chat.
    _set_cfg({
        "coreApi": "qwen", "assistApi": "qwen_intl", "assistApiKeyQwenIntl": "sk-intl",
        "assistWorkspaceHostQwenIntl": "ws-us.us-east-1.maas.aliyuncs.com",
    })
    us_host_dec = cm.get_model_api_config("decision")
    assert us_host_dec["decision_protocol"] == "chat_fallback"
    assert us_host_dec["native_unavailable_reason"] == "region_unsupported"
    assert us_host_dec["model"] == "qwen3.7-flash"
    # An invalid host field is ignored; it does not fall back to the legacy ID.
    _set_cfg({
        "coreApi": "qwen", "assistApi": "qwen", "coreApiKey": "sk-qwen",
        "assistWorkspaceIdQwen": "llm-old",
        "assistWorkspaceHostQwen": "ws-new.cn-beijing.maas.aliyuncs.com.evil.com",
    })
    bad_host_dec = cm.get_model_api_config("decision")
    assert bad_host_dec["decision_protocol"] == "chat_fallback"
    assert "evil" not in bad_host_dec["decision_url"]

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

    # Default silicon assist -> native systemone on SiliconFlow's own host (Kev-4B),
    # with the recognition chat model standing by.
    _set_cfg({"coreApi": "qwen", "assistApi": "silicon", "assistApiKeySilicon": "sk-sf"})
    sf_dec = cm.get_model_api_config("decision")
    assert sf_dec["model"] == "Kev-4B"
    assert sf_dec["decision_protocol"] == "systemone"
    assert sf_dec["decision_url"] == "https://api.siliconflow.cn/v1/systemone"
    assert sf_dec["native_unavailable_reason"] == ""
    assert sf_dec["fallback_model"] == "Qwen/Qwen3-VL-30B-A3B-Instruct"

    # The System One routers and Volcengine Ark decide natively on their own /systemone route.
    for provider, key_field, model, url in (
        ("orcarouter", "assistApiKeyOrcarouter", "typesafe/jev-1.14", "https://api.orcarouter.ai/v1/systemone"),
        ("requesty", "assistApiKeyRequesty", "typesafe/jev-1.14.0", "https://router.requesty.ai/v1/systemone"),
        ("opper", "assistApiKeyOpper", "typesafe/jev-1.14.0", "https://api.opper.ai/v3/compat/v1/systemone"),
        ("doubao", "assistApiKeyDoubao", "doubao-seed-2-1-lite-260915", "https://ark.cn-beijing.volces.com/api/v1/systemone"),
    ):
        _set_cfg({"coreApi": "qwen", "assistApi": provider, key_field: "sk-router"})
        router_dec = cm.get_model_api_config("decision")
        assert router_dec["model"] == model, provider
        assert router_dec["decision_protocol"] == "systemone", provider
        assert router_dec["decision_url"] == url, provider

    _set_cfg({"coreApi": "qwen", "assistApi": "openrouter", "assistApiKeyOpenrouter": "sk-or"})
    or_dec = cm.get_model_api_config("decision")
    assert or_dec["model"] == "liquid/d1.2-ultra"
    assert or_dec["decision_protocol"] == "openrouter_decisions"
    assert or_dec["decision_url"] == "https://openrouter.ai/api/alpha/decisions"

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

    # Custom decision slot must keep fallback_model empty instead of borrowing assist's RECOGNITION_MODEL
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
    # on the Bailian workspace host built from the submitted workspace ID (legacy field name).
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
        assert mock_dec_probe.await_args.kwargs["model"] == "decision-model"
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

    # The full API Host (what the page sends now) keeps its own region, even a
    # Singapore host entered for qwen; a URL on it is reduced to the host.
    for submitted, expected in (
        ("https://WS-Abc.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
         "https://ws-abc.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/systemone"),
        ("ws-sg.ap-southeast-1.maas.aliyuncs.com",
         "https://ws-sg.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1/systemone"),
    ):
        with patch(
            "main_routers.config_router.connectivity._test_decision_endpoint",
            AsyncMock(return_value={"success": True}),
        ) as mock_dec_probe:
            host_res = await test_connectivity(
                ConnectivityTestRequest(
                    provider_key="qwen", provider_scope="decision", api_key="sk-ws", workspace_host=submitted,
                )
            )
        assert host_res["success"] is True and "decision_fallback" not in host_res
        assert mock_dec_probe.await_args.args[0] == expected

    # A host in a region without a decision endpoint probes the chat fallback.
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
        us_res = await test_connectivity(
            ConnectivityTestRequest(
                provider_key="qwen_intl", provider_scope="decision", api_key="sk-us",
                workspace_host="ws-us.us-east-1.maas.aliyuncs.com",
            )
        )
        assert us_res["decision_fallback"] == "region_unsupported"
        assert mock_dec_probe.await_count == 0
        assert mock_oai.await_args.kwargs["model"] == "qwen3.7-flash"

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
        for field in ("workspace_id", "workspace_host"):
            for bad in (
                "evil.com/x", "llm-abc.evil.com", "llm@evil.com", "a" * 64,
                "ws-a.cn-beijing.maas.aliyuncs.com.evil.com", "user@ws-a.cn-beijing.maas.aliyuncs.com",
            ):
                bad_res = await test_connectivity(
                    ConnectivityTestRequest(
                        provider_key="qwen", provider_scope="decision", api_key="sk-qwen", **{field: bad},
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


_RECALL_DECISION_CFG = {
    "model": "decision-model",
    "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "api_key": "sk-test",
    "decision_protocol": "systemone",
    "decision_url": "https://llm-abc.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/systemone",
}
_RECALL_SUMMARY_CFG = {
    "model": "qwen3.8-flash",
    "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "api_key": "sk-test",
    "provider_type": "openai_compatible",
}


def _recall_cm(decision_cfg=None):
    cm = MagicMock()
    dec = decision_cfg or _RECALL_DECISION_CFG
    cm.aget_model_api_config = AsyncMock(side_effect=lambda tier: dec if tier == "decision" else _RECALL_SUMMARY_CFG)
    return cm


def _recall_fake_chat(content='[{"id": "m1"}, {"id": "m0"}]'):
    return SimpleNamespace(ainvoke=AsyncMock(return_value=SimpleNamespace(content=content)), aclose=AsyncMock())


async def _recall_rank(candidates, *, budget, cm, query="which fact?"):
    from memory.recall import MemoryRecallReranker

    return await MemoryRecallReranker()._fine_rank(
        candidates,
        query_texts=[query],
        budget=budget,
        config_manager=cm,
        prompt_loader=lambda _lang: "{QUERY}\n{CANDIDATES}\n{BUDGET}",
        lang="en",
    )


@pytest.mark.asyncio
@pytest.mark.unit
async def test_memory_recall_asks_groups_of_16_in_one_request_and_ranks_against_none():
    # Owner decision D18: 16 candidates to a single-choice question, each with "none".
    import re

    from utils.decision_client.schema import DecisionAnswer, DecisionResponse

    candidates = [{"id": f"fact.m.{i}", "text": f"fact number {i}", "score": float(40 - i)} for i in range(40)]
    calls: list = []

    async def _decide(self, *, state, questions, **kwargs):
        calls.append((state, questions, kwargs))
        return DecisionResponse(
            answers={
                # Group 0: m3 is clearly affected; group 1: m20 faintly beats a strong none;
                # group 2: only none.
                "g0": DecisionAnswer(question_type="choice", value="m3", probabilities={"m3": 0.8, "m5": 0.1, "none": 0.1}),
                "g1": DecisionAnswer(question_type="choice", value="none", probabilities={"m20": 0.3, "none": 0.7}),
                "g2": DecisionAnswer(question_type="choice", value="none", probabilities={"none": 1.0}),
            },
            model="decision-model",
            protocol="systemone",
        )

    with patch.object(DecisionClient, "decide", _decide):
        ranked = await _recall_rank(candidates, budget=5, cm=_recall_cm())

    assert len(calls) == 1
    state, questions, kwargs = calls[0]
    assert state == "New facts just extracted from the recent conversation:\n- which fact?"
    assert list(questions) == ["g0", "g1", "g2"]
    assert [len(q.criteria) for q in questions.values()] == [17, 17, 9]
    assert all(re.fullmatch(r"[A-Za-z0-9_-]{1,64}", k) for q in questions.values() for k in q.criteria)
    assert questions["g0"].criteria["m3"] == "fact number 3" and questions["g2"].criteria["none"]
    assert kwargs["allow_fallback"] is False and kwargs["timeout"] == 2.0
    # m3: 0.8/(0.8+0.1); m5: 0.5; m20: 0.3; then the coarse prior keeps coarse order.
    assert [r["id"] for r in ranked] == ["fact.m.3", "fact.m.5", "fact.m.20", "fact.m.0", "fact.m.1"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_memory_recall_on_doubao_uses_the_ark_decision_model():
    from utils.decision_client.schema import DecisionAnswer, DecisionResponse

    ark = {
        "model": "doubao-seed-2-1-lite-260915",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "api_key": "ak",
        "decision_protocol": "systemone",
        "decision_url": "https://ark.cn-beijing.volces.com/api/v1/systemone",
    }
    decide = AsyncMock(return_value=DecisionResponse(
        answers={"g0": DecisionAnswer(question_type="choice", value="m1", probabilities={"m1": 0.9, "none": 0.1})},
        model=ark["model"], protocol="systemone"))
    with patch.object(DecisionClient, "decide", decide):
        ranked = await _recall_rank([{"id": "a", "text": "x"}, {"id": "b", "text": "y"}], budget=2, cm=_recall_cm(ark))
    assert decide.await_args.kwargs["config"] is ark
    assert [r["id"] for r in ranked] == ["b", "a"]


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.parametrize("outcome", ["error", "missing_group", "no_probabilities", "slow"])
async def test_memory_recall_falls_back_to_the_chat_rerank(outcome):
    import asyncio

    from utils.decision_client.schema import DecisionAnswer, DecisionResponse

    candidates = [{"id": f"m{i}", "text": f"fact {i}", "score": float(20 - i)} for i in range(20)]
    cancelled = asyncio.Event()

    async def _decide(self, *, state, questions, **kwargs):
        if outcome == "error":
            raise DecisionProtocolError("HTTP 500", status_code=500, protocol="systemone")
        if outcome == "no_probabilities":
            return DecisionResponse(
                answers={g: DecisionAnswer(question_type="choice", value="none") for g in questions},
                model="decision-model", protocol="systemone")
        if outcome == "missing_group":
            # Only the first of the two groups answered: no half-native order.
            return DecisionResponse(
                answers={"g0": DecisionAnswer(question_type="choice", value="m3", probabilities={"m3": 1.0})},
                model="decision-model", protocol="systemone")
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    fake_llm = _recall_fake_chat()
    with (
        patch.object(DecisionClient, "decide", _decide),
        patch("utils.llm_client.create_chat_llm_async", AsyncMock(return_value=fake_llm)) as mock_create,
    ):
        ranked = await _recall_rank(candidates, budget=2, cm=_recall_cm())
    assert mock_create.await_count == 1
    assert [r["id"] for r in ranked] == ["m1", "m0"]
    if outcome == "slow":
        assert cancelled.is_set()


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.parametrize("decision_cfg", [
    {"model": "free-mini-model", "base_url": "https://www.lanlan.tech/text/v1", "decision_protocol": "chat_fallback"},
    {"model": "deepseek-flash", "base_url": "https://api.deepseek.com/v1", "decision_protocol": "chat_fallback"},
])
async def test_memory_recall_without_native_decision_uses_the_chat_rerank(decision_cfg):
    decide = AsyncMock()
    with (
        patch.object(DecisionClient, "decide", decide),
        patch("utils.llm_client.create_chat_llm_async", AsyncMock(return_value=_recall_fake_chat())),
    ):
        ranked = await _recall_rank([{"id": "m0", "text": "a"}, {"id": "m1", "text": "b"}], budget=2,
                                    cm=_recall_cm(decision_cfg))
    decide.assert_not_awaited()
    assert [r["id"] for r in ranked] == ["m1", "m0"]




@pytest.mark.asyncio
@pytest.mark.unit
async def test_memory_recall_reranker_uses_emotion_tier_for_non_native_providers():
    from memory.recall import MemoryRecallReranker

    async def _get_cfg(tier: str):
        if tier == "decision":
            return {
                "model": "free-mini-model",
                "base_url": "https://www.lanlan.tech/text/v1",
                "api_key": "free-access",
                "decision_protocol": "chat_fallback",
            }
        if tier == "recognition":
            return {
                "model": "user-custom-emotion-model",
                "base_url": "https://local-emotion.example/v1",
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
    assert mock_create_llm.await_args.args[0] == "user-custom-emotion-model"
    assert mock_create_llm.await_args.args[1] == "https://local-emotion.example/v1"






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
# /api/config/core_api: workspace host save-side and read-side validation
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
        from types import SimpleNamespace
        from starlette.datastructures import Headers
        from main_routers.system_router._shared import AUTOSTART_CSRF_TOKEN

        self._payload = payload
        self.method = "POST"
        self.base_url = "http://testserver/"
        self.url = SimpleNamespace(path="/api/config/core_api")
        self.headers = Headers({
            "content-type": "application/json",
            "origin": "http://testserver",
            "x-csrf-token": AUTOSTART_CSRF_TOKEN,
        })

    async def json(self):
        return self._payload


@pytest.mark.unit
def test_core_api_save_validates_workspace_hosts(clean_user_data_dir, _core_config_router_for_workspace):
    import asyncio

    from utils.config_manager import get_config_manager

    router = _core_config_router_for_workspace
    assert router.CORE_CONFIG_WORKSPACE_HOST_FIELDS == (
        ("assistWorkspaceHostQwen", "assistWorkspaceIdQwen"),
        ("assistWorkspaceHostQwenIntl", "assistWorkspaceIdQwenIntl"),
    )
    cm = get_config_manager("N.E.K.O")
    cm.config_dir.mkdir(parents=True, exist_ok=True)
    host = "ws-old.cn-beijing.maas.aliyuncs.com"
    stored = {"coreApi": "qwen", "assistApi": "qwen", "coreApiKey": "sk-qwen", "assistWorkspaceHostQwen": host}
    cm.save_json_config("core_config.json", stored)
    cm._core_config_cache = None

    def _save(extra):
        return asyncio.run(router.update_core_config(_FakeJsonRequest({
            "coreApiKey": "sk-qwen", "coreApi": "qwen", "assistApi": "qwen", "enableCustomApi": False, **extra,
        })))

    # Injection-style values are rejected and nothing is written.
    for bad in (
        "evil.com", "llm-abc.evil.com", "llm@evil.com", "evil.com/x", "a" * 64, "-x", 7,
        "ws-a.cn-beijing.maas.aliyuncs.com.evil.com", "ws-a.cn-beijing.maas.aliyuncs.com:443",
    ):
        resp = _save({"assistWorkspaceHostQwen": bad})
        assert resp["success"] is False, bad
        assert resp["error"] == "INVALID_WORKSPACE_HOST"
        assert resp["field"] == "assistWorkspaceHostQwen"
        assert cm.load_json_config("core_config.json", {})["assistWorkspaceHostQwen"] == host

    # Valid values are stored in canonical form (a URL keeps only its host); empty clears.
    assert _save({
        "assistWorkspaceHostQwen": "  https://WS-New.cn-beijing.maas.aliyuncs.com/compatible-mode/v1 ",
        "assistWorkspaceHostQwenIntl": "LLM-SG",
    })["success"] is True
    saved = cm.load_json_config("core_config.json", {})
    assert saved["assistWorkspaceHostQwen"] == "ws-new.cn-beijing.maas.aliyuncs.com"
    assert saved["assistWorkspaceHostQwenIntl"] == "llm-sg"
    assert _save({"assistWorkspaceHostQwenIntl": ""})["success"] is True
    assert cm.load_json_config("core_config.json", {})["assistWorkspaceHostQwenIntl"] == ""

    # A page loaded before the host field existed still saves through the legacy
    # name; the value lands in the host field and the legacy field goes away.
    cm.save_json_config("core_config.json", {**stored, "assistWorkspaceIdQwen": "llm-older"})
    cm._core_config_cache = None
    assert _save({"assistWorkspaceIdQwen": "LLM-Legacy"})["success"] is True
    saved = cm.load_json_config("core_config.json", {})
    assert saved["assistWorkspaceHostQwen"] == "llm-legacy"
    assert "assistWorkspaceIdQwen" not in saved
    resp = _save({"assistWorkspaceIdQwen": "evil.com"})
    assert resp["success"] is False and resp["field"] == "assistWorkspaceIdQwen"

    # Read side: a file saved by an older version (ID-only field) reads through,
    # and a hand-edited invalid value reads as unset.
    cm.save_json_config("core_config.json", {
        "coreApi": "qwen", "assistApi": "qwen", "coreApiKey": "sk-qwen", "assistWorkspaceIdQwen": "llm-legacy",
    })
    cm._core_config_cache = None
    got = asyncio.run(router.get_core_config_api())
    assert got["success"] is True
    assert got["assistWorkspaceHostQwen"] == "llm-legacy"
    assert got["assistWorkspaceHostQwenIntl"] == ""
    assert got["assistWorkspaceIdQwen"] == "llm-legacy"
    assert got["assistWorkspaceIdQwenIntl"] == ""

    # A settings page loaded before the upgrade reads the ID-only fields and posts
    # them back unchanged on every save: that must not clear or shorten the host.
    full = "ws-full.ap-southeast-1.maas.aliyuncs.com"
    cm.save_json_config("core_config.json", {**stored, "assistWorkspaceHostQwen": full})
    cm._core_config_cache = None
    got = asyncio.run(router.get_core_config_api())
    assert got["assistWorkspaceIdQwen"] == "ws-full"
    for _ in range(2):
        assert _save({"assistWorkspaceIdQwen": got["assistWorkspaceIdQwen"],
                      "assistWorkspaceIdQwenIntl": got["assistWorkspaceIdQwenIntl"]})["success"] is True
        saved = cm.load_json_config("core_config.json", {})
        assert saved["assistWorkspaceHostQwen"] == full
        assert saved["assistWorkspaceHostQwenIntl"] == ""
        got = asyncio.run(router.get_core_config_api())
    # A full host sent under the legacy name is saved as sent, even with the same ID.
    same_id_other_region = "ws-full.cn-beijing.maas.aliyuncs.com"
    assert _save({"assistWorkspaceIdQwen": same_id_other_region})["success"] is True
    assert cm.load_json_config("core_config.json", {})["assistWorkspaceHostQwen"] == same_id_other_region
    # A different ID typed there replaces it; clearing it there clears it.
    assert _save({"assistWorkspaceIdQwen": "ws-other"})["success"] is True
    assert cm.load_json_config("core_config.json", {})["assistWorkspaceHostQwen"] == "ws-other"
    assert _save({"assistWorkspaceIdQwen": ""})["success"] is True
    assert cm.load_json_config("core_config.json", {})["assistWorkspaceHostQwen"] == ""
    cm.save_json_config("core_config.json", {**stored, "assistWorkspaceHostQwen": "ws-x.evil.com"})
    cm._core_config_cache = None
    got = asyncio.run(router.get_core_config_api())
    assert got["assistWorkspaceHostQwen"] == ""


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

    # A URL that is not one of the provider's own endpoints (e.g. stale after the
    # candidate list changed) is never requested: it is ignored and the probe
    # uses the provider defaults.
    p_oai, p_dec = _patches()
    with p_oai as mock_oai, p_dec as mock_dec:
        res = await test_connectivity(ConnectivityTestRequest(
            provider_key="qwen_intl", provider_scope="decision", api_key="sk",
            workspace_id="llm-sg", url="https://evil.example/compatible-mode/v1",
        ))
    assert res["success"] is True
    assert mock_oai.await_count == 0
    assert mock_dec.await_args.args[0] == (
        "https://llm-sg.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1/systemone"
    )
    p_oai, p_dec = _patches()
    with p_oai as mock_oai, p_dec as mock_dec:
        res = await test_connectivity(ConnectivityTestRequest(
            provider_key="qwen_intl", provider_scope="decision", api_key="sk",
            url="https://evil.example/compatible-mode/v1",
        ))
    assert res["success"] is True
    assert res["decision_fallback"] == "workspace_missing"
    assert mock_dec.await_count == 0
    assert "evil.example" not in mock_oai.await_args.args[0]


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






@pytest.mark.unit
def test_auth_errors_are_permanent_only_for_bailian_systemone():
    from utils.decision_client.adapters import _http_error_is_permanent

    for status in (400, 404, 405, 422):
        assert _http_error_is_permanent(status, "systemone")
        assert _http_error_is_permanent(status, "openrouter_decisions")
        assert _http_error_is_permanent(status)
    # Bailian: rejected key / model not enabled for the workspace stays rejected.
    assert _http_error_is_permanent(401, "systemone")
    assert _http_error_is_permanent(403, "systemone")
    # OpenRouter answers 403 for a single moderated input: no global pause.
    assert not _http_error_is_permanent(403, "openrouter_decisions")
    assert not _http_error_is_permanent(401, "openrouter_decisions")
    assert not _http_error_is_permanent(403)
    assert not _http_error_is_permanent(500, "systemone")


# ---------------------------------------------------------------------------
# OpenRouter decision model catalog
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("model", "expected"),
    [
        # On OpenRouter gpt-6-luna is a chat model; its decision variant has its own ID.
        ("openai/gpt-6-luna", "chat_fallback"),
        ("openai/gpt-6-luna-pro", "chat_fallback"),
        ("openai/gpt-6-luna-decisions", "openrouter_decisions"),
        ("google/gemini-decision-1", "openrouter_decisions"),
        ("perplexity/pplx-decider-v1.3:free", "openrouter_decisions"),
        ("~perplexity/pplx-decider-latest", "openrouter_decisions"),
        ("baseten/joey-1", "openrouter_decisions"),
        ("respan/span-02", "openrouter_decisions"),
        # Long-lived families match versions newer than the snapshot.
        ("typesafe/jev-1.99", "openrouter_decisions"),
        ("liquid/d1.9-ultra", "openrouter_decisions"),
        ("cloudflare/clef-mini", "openrouter_decisions"),
        # Retired from OpenRouter, and chat models of decision vendors.
        ("inception/mercury-decide", "chat_fallback"),
        ("typesafe/jev-router", "chat_fallback"),
        ("anthropic/claude-haiku-4.5", "chat_fallback"),
    ],
)
def test_openrouter_decision_models_are_recognized_by_id(model, expected):
    assert infer_decision_protocol(model, "https://openrouter.ai/api/v1") == expected


@pytest.mark.unit
def test_openrouter_catalog_remembers_fetched_models():
    from utils.decision_client import openrouter_catalog as catalog

    assert infer_decision_protocol("newlab/decider-7", "https://openrouter.ai/api/v1") == "chat_fallback"
    catalog._remember([{"id": "NewLab/Decider-7"}], 0.0)
    assert catalog.is_openrouter_decision_model("newlab/decider-7")
    assert infer_decision_protocol("newlab/decider-7", "https://openrouter.ai/api/v1") == "openrouter_decisions"
    catalog.clear_openrouter_catalog()
    assert not catalog.is_openrouter_decision_model("newlab/decider-7")


@pytest.mark.asyncio
@pytest.mark.unit
async def test_openrouter_catalog_fetch_parses_public_list_without_a_key():
    from utils.decision_client import openrouter_catalog as catalog

    seen: list[dict] = []

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, **kwargs):
            seen.append({"url": url, **kwargs})
            body = {"data": [
                {"id": "newlab/decider-7", "name": "NewLab: Decider 7"},
                {"id": "baseten/joey-1", "name": "baseten/joey-1"},
                {"name": "missing id"},
                "junk",
            ]}
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: body)

    with patch("utils.decision_client.openrouter_catalog.httpx.AsyncClient", _Client):
        entries = await catalog.fetch_openrouter_decision_models()
    assert entries == [{"id": "newlab/decider-7", "name": "NewLab: Decider 7"}, {"id": "baseten/joey-1"}]
    assert seen == [{"url": "https://openrouter.ai/api/v1/models?output_modalities=decisions"}]
    assert catalog.is_openrouter_decision_model("newlab/decider-7")
    assert catalog.catalog_is_stale() is False


@pytest.mark.asyncio
@pytest.mark.unit
async def test_openrouter_catalog_refresh_runs_once_in_background_and_backs_off_after_failure():
    import asyncio

    from utils.decision_client import openrouter_catalog as catalog

    calls = 0
    release = asyncio.Event()

    async def _fail():
        nonlocal calls
        calls += 1
        catalog._last_attempt_at = catalog.time.monotonic()
        await release.wait()
        raise RuntimeError("offline")

    with patch.object(catalog, "fetch_openrouter_decision_models", _fail):
        catalog.schedule_refresh_if_stale()
        catalog.schedule_refresh_if_stale()  # already in flight
        await asyncio.sleep(0)
        assert calls == 1
        release.set()
        for _ in range(3):
            await asyncio.sleep(0)
        assert catalog._refresh_in_flight is False
        # A failure waits CATALOG_RETRY_SECONDS before the next attempt.
        catalog.schedule_refresh_if_stale()
        await asyncio.sleep(0)
        assert calls == 1
    now = catalog.time.monotonic()
    assert catalog.catalog_is_stale(now + catalog.CATALOG_RETRY_SECONDS + 1) is True
    catalog._remember([{"id": "x/y"}], now)
    assert catalog.catalog_is_stale(now + catalog.CATALOG_TTL_SECONDS - 1) is False
    assert catalog.catalog_is_stale(now + catalog.CATALOG_TTL_SECONDS + catalog.CATALOG_RETRY_SECONDS + 1) is True


@pytest.mark.asyncio
@pytest.mark.unit
async def test_decision_client_refreshes_openrouter_catalog_only_for_openrouter():
    resp = SimpleNamespace(answers={}, model="m", protocol="chat_fallback")
    with (
        patch("utils.decision_client.client.schedule_refresh_if_stale") as mock_refresh,
        patch.object(DecisionClient, "_decide_in_context", AsyncMock(return_value=resp)),
    ):
        q = {"t": BoolQuestion(instructions="ok?")}
        await DecisionClient().decide(
            state="s", questions=q,
            config={"model": "baseten/joey-1", "base_url": "https://openrouter.ai/api/v1", "api_key": "k"},
        )
        assert mock_refresh.call_count == 1
        await DecisionClient().decide(
            state="s", questions=q,
            config={"model": "deepseek-flash", "base_url": "https://api.deepseek.com/v1", "api_key": "k"},
        )
        assert mock_refresh.call_count == 1


@pytest.mark.asyncio
@pytest.mark.unit
async def test_model_list_adds_openrouter_decision_models_first_for_the_decision_slot():
    from main_routers.config_router import model_catalog

    chat = {
        "success": True,
        "resolved_url": "https://openrouter.ai/api/v1",
        "models": [{"id": "anthropic/claude-haiku-4.5"}, {"id": "openai/gpt-6-luna"}],
    }
    fetched = [{"id": "newlab/decider-7", "name": "NewLab: Decider 7"}, {"id": "baseten/joey-1"}]
    from utils.decision_client import openrouter_catalog as catalog

    catalog._remember(fetched, catalog.time.monotonic())
    merged = model_catalog._with_openrouter_decision_models(chat, "https://openrouter.ai/api/v1", fetched)
    assert merged["success"] is True
    assert [m["id"] for m in merged["models"]] == [
        "baseten/joey-1", "newlab/decider-7", "anthropic/claude-haiku-4.5", "openai/gpt-6-luna",
    ]
    assert [m.get("decision", False) for m in merged["models"]] == [True, True, False, False]
    assert merged["models"][1]["name"] == "NewLab: Decider 7"

    # Decision list unreachable: keep the chat list, tag only what the snapshot knows.
    with patch(
        "utils.decision_client.openrouter_catalog.fetch_openrouter_decision_models",
        AsyncMock(side_effect=RuntimeError("offline")),
    ):
        assert await model_catalog._fetch_openrouter_decision_entries() == []
    offline = model_catalog._with_openrouter_decision_models(
        {**chat, "models": [{"id": "openai/gpt-6-luna-decisions"}, {"id": "openai/gpt-6-luna"}]},
        "https://openrouter.ai/api/v1",
        [],
    )
    assert [(m["id"], m.get("decision", False)) for m in offline["models"]] == [
        ("openai/gpt-6-luna-decisions", True), ("openai/gpt-6-luna", False),
    ]
    # Both lists failed: the original failure is returned unchanged.
    failure = {"success": False, "error_code": "timeout", "error": "t"}
    assert model_catalog._with_openrouter_decision_models(failure, "https://openrouter.ai/api/v1", []) is failure


@pytest.mark.asyncio
@pytest.mark.unit
async def test_model_list_fetches_openrouter_decision_models_alongside_the_chat_list(
    clean_user_data_dir, monkeypatch,
):
    """Both lists share one time budget below the page's 20 s request timeout."""
    import asyncio
    import time

    from main_routers.config_router import model_catalog

    monkeypatch.setattr(model_catalog, "_MODEL_LIST_TIMEOUT_SECONDS", 0.6)
    monkeypatch.setattr(model_catalog, "_resolve_provider_target", lambda *_a: {
        "urls": ["https://openrouter.ai/api/v1"], "api_key": "k", "provider_type": "openai_compatible",
    })

    async def _chat(url, api_key, provider_type):
        await asyncio.sleep(0.4)
        return {"success": True, "models": [{"id": "openai/gpt-6-luna"}], "resolved_url": url}

    monkeypatch.setattr(model_catalog, "_fetch_models", _chat)

    async def _decisions(*_a, **_kw):
        await asyncio.sleep(0.4)
        return [{"id": "baseten/joey-1"}]

    req = model_catalog.ModelListRequest(provider_key="openrouter", model_type="decision")
    with patch("utils.decision_client.openrouter_catalog.fetch_openrouter_decision_models", _decisions):
        started = time.perf_counter()
        result = await model_catalog.list_models(req)
        elapsed = time.perf_counter() - started
    assert elapsed < 0.7, elapsed  # sequential would take 0.8 s
    assert [(m["id"], m.get("decision", False)) for m in result["models"]] == [
        ("baseten/joey-1", True), ("openai/gpt-6-luna", False),
    ]

    # A decision list slower than the budget is dropped; the chat list still arrives in time.
    async def _hang(*_a, **_kw):
        await asyncio.sleep(30)

    with patch("utils.decision_client.openrouter_catalog.fetch_openrouter_decision_models", _hang):
        started = time.perf_counter()
        result = await model_catalog.list_models(req)
        elapsed = time.perf_counter() - started
    assert elapsed < 1.0, elapsed
    assert result["success"] is True
    assert [m["id"] for m in result["models"]] == ["openai/gpt-6-luna"]

    # Other slots, and other providers, never fetch the decision list.
    calls = AsyncMock(return_value=[])
    with patch("utils.decision_client.openrouter_catalog.fetch_openrouter_decision_models", calls):
        await model_catalog.list_models(model_catalog.ModelListRequest(provider_key="openrouter", model_type="memory"))
        monkeypatch.setattr(model_catalog, "_resolve_provider_target", lambda *_a: {
            "urls": ["https://api.deepseek.com/v1"], "api_key": "k", "provider_type": "openai_compatible",
        })
        await model_catalog.list_models(req)
    assert calls.await_count == 0


@pytest.mark.asyncio
@pytest.mark.unit
async def test_decision_config_lookup_schedules_openrouter_catalog_refresh(clean_user_data_dir):
    """Memory recall reads the decision config before it ever calls the decision client."""
    from utils.config_manager import get_config_manager

    cm = get_config_manager("N.E.K.O")
    cm.config_dir.mkdir(parents=True, exist_ok=True)
    cfg_path = cm.get_config_path("core_config.json")

    def _set_cfg(data):
        cfg_path.write_text(json.dumps(data), encoding="utf-8")
        cm._core_config_cache = None

    with patch("utils.decision_client.openrouter_catalog.schedule_refresh_if_stale") as mock_refresh:
        _set_cfg({"coreApi": "qwen", "assistApi": "openrouter", "assistApiKeyOpenrouter": "sk-or"})
        cfg = await cm.aget_model_api_config("decision")
        assert cfg["decision_protocol"] == "openrouter_decisions"
        assert mock_refresh.call_count == 1
        await cm.aget_model_api_config("decision", core_config=cm.get_core_config())
        assert mock_refresh.call_count == 2
        await cm.aget_model_api_config("memory")
        assert mock_refresh.call_count == 2
        _set_cfg({"coreApi": "qwen", "assistApi": "deepseek", "assistApiKeyDeepseek": "sk-ds"})
        await cm.aget_model_api_config("decision")
        assert mock_refresh.call_count == 2


@pytest.mark.asyncio
@pytest.mark.unit
async def test_connectivity_reports_workspace_mismatch():
    from main_routers.config_router.connectivity import _test_decision_endpoint

    mismatch = DecisionProtocolError(
        "systemone HTTP 403: Workspace endpoint access denied.",
        status_code=403, protocol="systemone", permanent_route_error=True, error_code="workspace_mismatch",
    )
    with patch.object(DecisionClient, "decide", AsyncMock(side_effect=mismatch)):
        res = await _test_decision_endpoint(
            "https://ws-a.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/systemone",
            "sk-ws-other", model="decision-model-preview", protocol="systemone",
        )
    assert res["success"] is False
    assert res["error_code"] == "workspace_mismatch"
    plain_403 = DecisionProtocolError("systemone HTTP 403: Access denied", status_code=403, protocol="systemone")
    with patch.object(DecisionClient, "decide", AsyncMock(side_effect=plain_403)):
        res = await _test_decision_endpoint(
            "https://ws-a.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/systemone",
            "sk-ws-a", model="decision-model-preview", protocol="systemone",
        )
    assert res["error_code"] == "auth_failed"


@pytest.mark.unit
def test_model_list_tags_native_decision_models_for_the_decision_slot():
    from main_routers.config_router import model_catalog

    def _listed(ids, url):
        result = {"success": True, "resolved_url": url, "models": [{"id": i} for i in ids]}
        return [(m["id"], m.get("decision", False)) for m in model_catalog._tag_decision_models(result, url)["models"]]

    assert _listed(["Qwen/Qwen3-8B", "SemIf", "Kev-4B"], "https://api.siliconflow.cn/v1") == [
        ("Kev-4B", True), ("SemIf", True), ("Qwen/Qwen3-8B", False),
    ]
    assert _listed(["gpt-5-nano", "gpt-6-luna"], "https://api.openai.com/v1") == [
        ("gpt-6-luna", True), ("gpt-5-nano", False),
    ]
    assert _listed(["Kev-4B", "deepseek-flash"], "https://api.deepseek.com/v1") == [
        ("deepseek-flash", False), ("Kev-4B", False),
    ]
    failure = {"success": False, "error_code": "timeout"}
    assert model_catalog._tag_decision_models(failure, "https://api.siliconflow.cn/v1") is failure


@pytest.mark.asyncio
@pytest.mark.unit
async def test_model_list_tags_decision_models_only_for_the_decision_slot(clean_user_data_dir, monkeypatch):
    from main_routers.config_router import model_catalog

    monkeypatch.setattr(model_catalog, "_resolve_provider_target", lambda *_a: {
        "urls": ["https://api.siliconflow.cn/v1"], "api_key": "k", "provider_type": "openai_compatible",
    })

    async def _chat(url, api_key, provider_type):
        return {"success": True, "models": [{"id": "Qwen/Qwen3-8B"}, {"id": "Kev-4B"}], "resolved_url": url}

    monkeypatch.setattr(model_catalog, "_fetch_models", _chat)
    decision = await model_catalog.list_models(model_catalog.ModelListRequest(provider_key="silicon", model_type="decision"))
    assert [(m["id"], m.get("decision", False)) for m in decision["models"]] == [
        ("Kev-4B", True), ("Qwen/Qwen3-8B", False),
    ]
    memory = await model_catalog.list_models(model_catalog.ModelListRequest(provider_key="silicon", model_type="memory"))
    assert [m for m in memory["models"] if m.get("decision")] == []


@pytest.mark.asyncio
@pytest.mark.unit
async def test_connectivity_decision_probe_uses_siliconflow_systemone():
    from main_routers.config_router.connectivity import ConnectivityTestRequest, test_connectivity

    with patch(
        "main_routers.config_router.connectivity._test_decision_endpoint",
        AsyncMock(return_value={"success": True}),
    ) as mock_dec:
        res = await test_connectivity(ConnectivityTestRequest(
            provider_key="silicon", provider_scope="decision", api_key="sk-sf",
        ))
    assert res["success"] is True
    assert mock_dec.await_args.args[0] == "https://api.siliconflow.cn/v1/systemone"
    assert mock_dec.await_args.kwargs["model"] == "Kev-4B"
    assert mock_dec.await_args.kwargs["protocol"] == "systemone"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_memory_recall_options_stay_within_the_decision_budget(monkeypatch):
    # The options of all groups together are bounded like the old per-candidate state.
    import config

    from utils.decision_client.schema import DecisionAnswer, DecisionResponse

    monkeypatch.setattr(config, "DECISION_STATE_MAX_TOKENS", 300)
    candidates = [{"id": f"m{i}", "text": "word " * 60, "score": float(40 - i)} for i in range(40)]
    seen: dict = {}

    async def _decide(self, *, state, questions, **kwargs):
        seen["options"] = sum(len([k for k in q.criteria if k != "none"]) for q in questions.values())
        return DecisionResponse(
            answers={g: DecisionAnswer(question_type="choice", value="none", probabilities={"none": 1.0}) for g in questions},
            model="decision-model", protocol="systemone")

    with patch.object(DecisionClient, "decide", _decide):
        ranked = await _recall_rank(candidates, budget=30, cm=_recall_cm())
    assert 0 < seen["options"] < 40
    # Candidates past the budget keep their coarse place behind the asked ones.
    assert [r["id"] for r in ranked] == [f"m{i}" for i in range(30)]

