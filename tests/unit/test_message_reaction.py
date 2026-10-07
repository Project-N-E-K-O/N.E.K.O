"""Rule reactions reuse outward emotion results without extra inference."""

import asyncio
import importlib
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture
def emotion(monkeypatch):
    module = importlib.import_module("main_routers.system_router.emotion")
    monkeypatch.setattr(
        module, "_validate_local_mutation_request", lambda request: None
    )
    monkeypatch.setattr(module, "_push_emotion_update", lambda *args: None)
    return module


@pytest.mark.parametrize("label", ["happy", "sad", "surprised", "angry"])
@pytest.mark.parametrize("confidence", [0.72, 0.9, 1.0])
def test_rule_returns_configured_candidate_and_preserves_emotion(
    emotion, label, confidence
):
    result = emotion._emotion_response(label, confidence, "NEKO")
    assert result["emotion"] == label
    assert result["confidence"] == confidence
    assert result["reaction"]["author"] == "NEKO"
    assert (
        result["reaction"]["emoji"] in emotion.MESSAGE_REACTION_EMOJIS_BY_EMOTION[label]
    )


@pytest.mark.parametrize(
    "label,confidence,name",
    [
        ("neutral", 1, "NEKO"),
        ("happy", 0.719, "NEKO"),
        ("happy", 0, "NEKO"),
        ("happy", float("nan"), "NEKO"),
        ("happy", float("inf"), "NEKO"),
        ("unknown", 1, "NEKO"),
        ("happy", 1, None),
    ],
)
def test_no_reaction_below_threshold_or_without_valid_decision(
    emotion, label, confidence, name
):
    assert emotion._emotion_response(label, confidence, name)["reaction"] is None


def test_threshold_and_random_choice_are_configurable(emotion, monkeypatch):
    monkeypatch.setattr(emotion, "MESSAGE_REACTION_CONFIDENCE_THRESHOLD", 0.8)
    monkeypatch.setattr(emotion.random, "choice", lambda items: items[-1])
    assert emotion._emotion_response("happy", 0.79, "NEKO")["reaction"] is None
    assert emotion._emotion_response("happy", 0.8, "NEKO")["reaction"]["emoji"] == "🎉"


@pytest.mark.parametrize(
    "response",
    [
        '{"emotion":"happy","confidence":0.9}',
        "not json",
        '{"emotion":"neutral","confidence":1}',
        '{"emotion":"happy","confidence":"bad"}',
        '{"confidence":0.7}',
        '{"emotion":"unknown","confidence":0.7}',
        '{"emotion":null,"confidence":0.7}',
        '{"emotion":123,"confidence":0.7}',
        '{"emotion":["happy"],"confidence":0.7}',
    ],
)
def test_endpoint_invokes_existing_model_once_and_degrades_safely(
    emotion, monkeypatch, response
):
    calls = []

    class Config:
        async def aget_model_api_config(self, tier):
            assert tier == "emotion"
            return {"api_key": "test", "model": "test", "base_url": "http://invalid"}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def ainvoke(self, messages):
            calls.append(messages)
            return SimpleNamespace(content=response)

    async def factory(*args, **kwargs):
        return Client()

    class Request:
        async def json(self):
            return {"text": "hello", "lanlan_name": "NEKO"}

    monkeypatch.setattr(emotion, "get_config_manager", lambda: Config())
    monkeypatch.setattr(emotion, "create_chat_llm_async", factory)
    monkeypatch.setattr(emotion, "_resolve_emotion_prompt_language", lambda *args: "en")
    # Avatar heuristics can still recover an emotion, but an invalid model
    # decision must never gain a reaction through that fallback.
    monkeypatch.setattr(emotion, "_infer_emotion_from_text", lambda text: ("happy", 4))
    result = asyncio.run(emotion.emotion_analysis(Request()))
    assert len(calls) == 1
    assert bool(result["reaction"]) == (
        response == '{"emotion":"happy","confidence":0.9}'
    )


def test_all_rule_candidates_are_accepted_by_react_schema(emotion):
    from pathlib import Path

    schema = (
        Path(__file__).resolve().parents[2]
        / "frontend/react-neko-chat/src/message-schema.ts"
    ).read_text(encoding="utf-8")
    for candidates in emotion.MESSAGE_REACTION_EMOJIS_BY_EMOTION.values():
        for emoji in candidates:
            assert repr(emoji) in schema


def test_removed_reaction_route_is_not_registered(emotion):
    assert not any(
        route.path.endswith("/chat/reaction") for route in emotion.router.routes
    )
