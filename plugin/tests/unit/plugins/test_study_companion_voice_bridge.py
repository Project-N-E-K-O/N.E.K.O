from __future__ import annotations

import asyncio

import pytest

from plugin.plugins.study_companion import StudyCompanionPlugin
from plugin.plugins.study_companion.models import STATUS_READY
from plugin.plugins.study_companion.state import build_initial_state
from plugin.plugins.study_companion.voice_filter import VoiceFilter
from plugin.sdk.plugin import Ok

pytestmark = pytest.mark.unit


def _plugin_with_voice_state(*, screen_text: str) -> StudyCompanionPlugin:
    plugin = StudyCompanionPlugin.__new__(StudyCompanionPlugin)
    plugin._lock = asyncio.Lock()
    plugin._voice_filter = VoiceFilter(names=["Yui"])
    state = build_initial_state()
    state.status = STATUS_READY
    state.last_ocr_text = screen_text
    state.last_screen_classification = {"screen_type": "question"}
    state.session_summary_seed = {"last_topic": "derivative"}
    plugin._state = state
    return plugin


@pytest.mark.asyncio
async def test_voice_transcript_name_call_returns_prime_context() -> None:
    plugin = _plugin_with_voice_state(
        screen_text="f(x)=x^3 derivative, explain where 3x^2 comes from"
    )

    result = await plugin.handle_voice_transcript("Yui why is it 3x^2")

    assert isinstance(result, Ok)
    payload = result.value
    assert payload["action"] == "prime_context"
    assert payload["skipped"] is True
    assert "f(x)=x^3" in payload["context"]
    assert "why is it 3x^2" in payload["context"]
    assert payload["filter"]["method"] == "name_call"


@pytest.mark.asyncio
async def test_voice_transcript_empty_text_returns_noop() -> None:
    plugin = _plugin_with_voice_state(
        screen_text="f(x)=x^3 derivative, explain where 3x^2 comes from"
    )

    result = await plugin.handle_voice_transcript("")

    assert isinstance(result, Ok)
    payload = result.value
    assert payload["action"] == "noop"
    assert payload["reason"] == "empty_transcript"
    assert payload["filter"]["method"] == "empty_transcript"


@pytest.mark.asyncio
async def test_voice_transcript_not_ready_returns_noop() -> None:
    plugin = StudyCompanionPlugin.__new__(StudyCompanionPlugin)
    plugin._lock = asyncio.Lock()
    plugin._voice_filter = VoiceFilter(names=["Yui"])
    plugin._state = build_initial_state()

    result = await plugin.handle_voice_transcript("Yui why is it 3x^2")

    assert isinstance(result, Ok)
    payload = result.value
    assert payload["action"] == "noop"
    assert payload["reason"] == "not_ready"
    assert payload["filter"]["method"] == "not_ready"


@pytest.mark.asyncio
async def test_voice_transcript_empty_context_returns_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plugin.plugins import study_companion

    plugin = _plugin_with_voice_state(screen_text="f(x)=x^3 derivative")
    monkeypatch.setattr(study_companion, "build_context_for_catgirl", lambda *_args, **_kwargs: "")

    result = await plugin.handle_voice_transcript("Yui why is it 3x^2")

    assert isinstance(result, Ok)
    payload = result.value
    assert payload["action"] == "noop"
    assert payload["reason"] == "empty_context"
    assert payload["filter"]["method"] == "empty_context"
    assert payload["filter"]["source_method"] == "name_call"


@pytest.mark.asyncio
async def test_voice_transcript_uses_active_lanlan_name_as_wake_word() -> None:
    plugin = _plugin_with_voice_state(
        screen_text="f(x)=x^3 derivative, explain where 3x^2 comes from"
    )

    result = await plugin.handle_voice_transcript(
        "Mika why is it 3x^2",
        lanlan_name="Mika",
    )

    assert isinstance(result, Ok)
    payload = result.value
    assert payload["action"] == "prime_context"
    assert "why is it 3x^2" in payload["context"]
    assert payload["filter"]["method"] == "name_call"
    assert payload["filter"]["name"] == "Mika"


@pytest.mark.asyncio
async def test_voice_transcript_self_talk_returns_cancel_response() -> None:
    plugin = _plugin_with_voice_state(screen_text="f(x)=x^3 derivative answer is 3x^2")

    result = await plugin.handle_voice_transcript("f(x)=x^3 derivative answer is 3x^2")

    assert isinstance(result, Ok)
    payload = result.value
    assert payload["action"] == "cancel_response"
    assert payload["filter"]["method"] == "ocr_overlap"


@pytest.mark.asyncio
async def test_voice_transcript_name_window_is_isolated_by_lanlan_name() -> None:
    plugin = _plugin_with_voice_state(screen_text="f(x)=x^3 derivative answer is 3x^2")

    first = await plugin.handle_voice_transcript(
        "Yui help me",
        lanlan_name="Yui",
    )
    second = await plugin.handle_voice_transcript(
        "嗯",
        lanlan_name="Mika",
    )

    assert isinstance(first, Ok)
    assert first.value["action"] == "prime_context"
    assert isinstance(second, Ok)
    assert second.value["action"] == "cancel_response"
    assert second.value["filter"]["method"] == "too_short"


@pytest.mark.asyncio
async def test_voice_transcript_name_window_uses_metadata_session_key() -> None:
    plugin = _plugin_with_voice_state(screen_text="f(x)=x^3 derivative answer is 3x^2")

    first = await plugin.handle_voice_transcript(
        "Yui help me",
        lanlan_name="Yui",
        metadata={"voice_session_id": "voice-a"},
    )
    second = await plugin.handle_voice_transcript(
        "嗯",
        lanlan_name="Yui",
        metadata={"voice_session_id": "voice-b"},
    )
    third = await plugin.handle_voice_transcript(
        "嗯",
        lanlan_name="Yui",
        metadata={"voice_session_id": "voice-a"},
    )

    assert isinstance(first, Ok)
    assert first.value["action"] == "prime_context"
    assert isinstance(second, Ok)
    assert second.value["action"] == "cancel_response"
    assert second.value["filter"]["method"] == "too_short"
    assert isinstance(third, Ok)
    assert third.value["action"] == "prime_context"
    assert third.value["filter"]["method"] == "name_window"
