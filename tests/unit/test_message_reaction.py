"""Rule reactions reuse outward emotion results without extra inference."""

import asyncio
import importlib
import json
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
    emotion, monkeypatch, response, analysis
):
    state, run = analysis
    # Avatar heuristics can still recover an emotion, but an invalid model
    # decision must never gain a reaction through that fallback.
    monkeypatch.setattr(emotion, "_infer_emotion_from_text", lambda text: ("happy", 4))
    result = run(response)
    assert len(state.calls) == 1
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


@pytest.fixture
def analysis(emotion, monkeypatch):
    state = SimpleNamespace(response="", error=None, calls=[], settings=[], updates=[])
    state.infer_emotion = emotion._infer_emotion_from_text

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
            state.calls.append(messages)
            if state.error is not None:
                raise state.error
            return SimpleNamespace(content=state.response)

    async def factory(*args, **kwargs):
        state.settings.append(kwargs)
        return Client()

    monkeypatch.setattr(emotion, "get_config_manager", lambda: Config())
    monkeypatch.setattr(emotion, "create_chat_llm_async", factory)
    monkeypatch.setattr(emotion, "_resolve_emotion_prompt_language", lambda *args: "en")
    monkeypatch.setattr(emotion, "_infer_emotion_from_text", lambda text: (None, 0))
    monkeypatch.setattr(emotion, "_push_emotion_update", lambda *args: state.updates.append(args))

    def run(response, name="NEKO", text="hello"):
        state.response = response if isinstance(response, str) else json.dumps(response)

        class Request:
            async def json(self):
                return {"text": text, "lanlan_name": name}

        return asyncio.run(emotion.emotion_analysis(Request()))

    return state, run


@pytest.mark.parametrize("label", ["happy", "sad", "surprised", "angry"])
def test_model_can_choose_every_candidate_across_emotion_categories(emotion, monkeypatch, label):
    def unexpected_random_choice(items):
        pytest.fail("valid model emoji must bypass random selection")

    monkeypatch.setattr(emotion.random, "choice", unexpected_random_choice)
    for candidates in emotion.MESSAGE_REACTION_EMOJIS_BY_EMOTION.values():
        for emoji in candidates:
            assert emotion._emotion_response(label, 0.72, "NEKO", emoji) == {
                "emotion": label, "confidence": 0.72,
                "reaction": {"emoji": emoji, "author": "NEKO"},
            }


@pytest.mark.parametrize("emoji", [None, True, 1, ["😊"], {}, "", "😀", "😊😄", "😊 ", "✨️"])
def test_unusable_model_emoji_uses_existing_rule(emotion, monkeypatch, emoji):
    monkeypatch.setattr(emotion.random, "choice", lambda items: items[-1])
    result = emotion._emotion_response("sad", 0.9, "NEKO", emoji)
    assert result["reaction"] == {"emoji": "💧", "author": "NEKO"}


@pytest.mark.parametrize("label,confidence,name", [
    ("neutral", 1, "NEKO"), ("happy", 0.719, "NEKO"),
    ("unknown", 1, "NEKO"), ("happy", float("nan"), "NEKO"),
    ("happy", float("inf"), "NEKO"), ("happy", 1, None),
])
def test_model_emoji_cannot_bypass_reaction_gate(emotion, label, confidence, name):
    assert emotion._emotion_response(label, confidence, name, "😊")["reaction"] is None


def test_missing_rule_candidates_do_not_break_avatar_response(emotion, monkeypatch):
    monkeypatch.setattr(emotion, "MESSAGE_REACTION_EMOJIS_BY_EMOTION", {"happy": ()})
    assert emotion._emotion_response("happy", 0.9, "NEKO") == {
        "emotion": "happy", "confidence": 0.9, "reaction": None,
    }


def test_selection_error_preserves_avatar_update_and_emotion(emotion, analysis, monkeypatch, capsys):
    state, run = analysis

    def fail_choice(items):
        raise RuntimeError("private conversation must not be logged")

    monkeypatch.setattr(emotion.random, "choice", fail_choice)
    result = run({"emotion": "happy", "confidence": 0.9, "emoji": None})
    assert result == {"emotion": "happy", "confidence": 0.9, "reaction": None}
    assert state.updates == [("NEKO", "happy", 0.9)]
    output = capsys.readouterr().out
    assert "RuntimeError" in output
    assert "private conversation" not in output


@pytest.mark.parametrize("wrapper", ["{}", "```json\n{}\n```"])
def test_endpoint_uses_model_emoji_with_existing_single_call(analysis, wrapper):
    state, run = analysis
    result = run(wrapper.format(json.dumps({"emotion": "sad", "confidence": 0.9, "emoji": "🥰"})))
    assert result == {
        "emotion": "sad", "confidence": 0.9,
        "reaction": {"emoji": "🥰", "author": "NEKO"},
    }
    assert len(state.calls) == len(state.settings) == 1
    assert state.settings[0]["max_completion_tokens"] == 64
    assert state.updates == [("NEKO", "sad", 0.9)]


@pytest.mark.parametrize("confidence", [None, True, "bad", -1, 2, float("nan"), float("inf")])
def test_bad_confidence_cannot_gain_model_reaction_through_heuristics(
    emotion, analysis, monkeypatch, confidence
):
    _, run = analysis
    monkeypatch.setattr(emotion, "_infer_emotion_from_text", lambda text: ("happy", 6))
    result = run({"emotion": "happy", "confidence": confidence, "emoji": "🎉"})
    assert result["reaction"] is None


def test_missing_confidence_cannot_gain_reaction_through_heuristics(emotion, analysis, monkeypatch):
    _, run = analysis
    monkeypatch.setattr(emotion, "_infer_emotion_from_text", lambda text: ("happy", 6))
    result = run({"emotion": "neutral", "emoji": "🎉"})
    assert result["emotion"] == "happy"
    assert result["confidence"] >= 0.72
    assert result["reaction"] is None


@pytest.mark.parametrize("label", [None, 1, [], {}, "unknown", "happi"])
def test_invalid_model_label_cannot_gain_reaction(emotion, analysis, monkeypatch, label):
    _, run = analysis
    monkeypatch.setattr(emotion, "_infer_emotion_from_text", lambda text: ("happy", 6))
    result = run({"emotion": label, "confidence": 0.9, "emoji": "🎉"})
    assert result["reaction"] is None


@pytest.mark.parametrize("response", [
    "not json", "null", "[]", '{"emotion":"happy","confidence":0.9,"emoji":"😊"',
])
def test_broken_response_never_recovers_an_emoji(emotion, analysis, monkeypatch, response):
    _, run = analysis
    monkeypatch.setattr(emotion, "_infer_emotion_from_text", lambda text: ("happy", 6))
    assert run(response)["reaction"] is None


def test_provider_error_does_not_return_reaction(analysis):
    state, run = analysis
    state.error = TimeoutError("provider unavailable")
    result = run({"emotion": "happy", "confidence": 0.9, "emoji": "😊"})
    assert result.get("reaction") is None
    assert result["emotion"] == "neutral"


@pytest.mark.parametrize("emoji", [None, "🎉", "invalid"])
def test_emoji_does_not_change_existing_heuristic_correction(emotion, analysis, monkeypatch, emoji):
    state, run = analysis
    monkeypatch.setattr(emotion, "_infer_emotion_from_text", lambda text: ("sad", 4))
    monkeypatch.setattr(emotion.random, "choice", lambda items: items[0])
    original = run({"emotion": "happy", "confidence": 0.7})
    result = run({"emotion": "happy", "confidence": 0.7, "emoji": emoji})
    assert result["emotion"] == original["emotion"] == "sad"
    assert result["confidence"] == original["confidence"]
    assert state.updates[0] == state.updates[1]
    assert original["reaction"] is result["reaction"] is None


@pytest.mark.parametrize("emoji", [None, "🎉", "invalid"])
@pytest.mark.parametrize("label,confidence,heuristic,score,expected", [
    ("happy", 0.75, "angry", 4, "angry"),  # strong override
    ("happy", 0.75, "sad", 2, "sad"),  # sad override
    ("neutral", 0.55, "happy", 3, "happy"),  # neutral recovery
    ("angry", 0.1, "happy", 1, "happy"),  # low-confidence recovery
])
def test_heuristic_decisions_update_avatar_but_never_gain_reactions(
    emotion, analysis, monkeypatch, emoji, label, confidence, heuristic, score, expected
):
    state, run = analysis
    # Even a lowered configurable reaction threshold cannot authorize fallback.
    monkeypatch.setattr(emotion, "MESSAGE_REACTION_CONFIDENCE_THRESHOLD", 0.4)
    monkeypatch.setattr(emotion, "_infer_emotion_from_text", lambda text: (heuristic, score))
    result = run({"emotion": label, "confidence": confidence, "emoji": emoji})
    assert result["emotion"] == expected
    assert result["confidence"] >= 0.4
    assert state.updates == [("NEKO", expected, result["confidence"])]
    assert len(state.calls) == 1
    assert result["reaction"] is None


@pytest.mark.parametrize("confidence", [0.8, 0.95, 1.0])
def test_confident_own_reaction_survives_third_party_sad_keywords(
    emotion, analysis, monkeypatch, confidence
):
    state, run = analysis
    text = '他喊着「想哭、委屈」，但我听完笑得很开心。'
    assert state.infer_emotion(text)[0] == "sad"
    monkeypatch.setattr(emotion, "_infer_emotion_from_text", state.infer_emotion)
    result = run({"emotion": "happy", "confidence": confidence, "emoji": "😊"}, text=text)
    assert result == {"emotion": "happy", "confidence": confidence,
                      "reaction": {"emoji": "😊", "author": "NEKO"}}
    assert state.updates == [("NEKO", "happy", confidence)]
    assert len(state.calls) == 1
    assert state.calls[0][1]["content"] == text


@pytest.mark.parametrize("confidence", [0.6, 0.79])
def test_uncertain_happy_still_allows_existing_sad_correction(
    emotion, analysis, monkeypatch, confidence
):
    _, run = analysis
    monkeypatch.setattr(emotion, "_infer_emotion_from_text", lambda text: ("sad", 2))
    assert run({"emotion": "happy", "confidence": confidence})["emotion"] == "sad"


@pytest.mark.parametrize("lang", ["zh", "zh-TW", "en", "ja", "ko", "ru", "es", "pt"])
def test_every_prompt_exposes_configured_union(emotion, lang):
    from config.prompts.prompts_emotion import get_outward_emotion_analysis_prompt

    prompt = get_outward_emotion_analysis_prompt(lang)
    configured = {
        emoji for candidates in emotion.MESSAGE_REACTION_EMOJIS_BY_EMOTION.values()
        for emoji in candidates
    }
    assert '{reaction_emojis}' not in prompt
    assert '"emoji": null' in prompt
    assert all(emoji in prompt for emoji in configured)


def test_prompt_candidates_follow_configuration_without_manual_translation_updates(monkeypatch):
    from config import proactive_settings
    from config.prompts.prompts_emotion import get_outward_emotion_analysis_prompt

    monkeypatch.setattr(proactive_settings, "MESSAGE_REACTION_EMOJIS_BY_EMOTION", {
        "happy": ("😊", "🤗"), "sad": ("🤗", "😢"),
    })
    for lang in ("zh", "zh-TW", "en", "ja", "ko", "ru", "es", "pt"):
        prompt = get_outward_emotion_analysis_prompt(lang)
        assert "😊 🤗 😢" in prompt
        assert "🎉" not in prompt
        assert prompt.count("🤗") == 1
