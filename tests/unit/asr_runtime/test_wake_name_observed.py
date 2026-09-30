"""Observed wake-name errors, including ordinary-word and activation boundaries."""

import pytest

from main_logic.voice_input.wake_word.transcript import correct_wake_name_prefix
from main_logic.voice_turn.contracts import VoicePartialEvent
from tests.unit.asr_runtime.test_wake_name_transcript import (
    _final, _runtime, _status, _texts, _token,
)


# 2026-09-29 live user confirmation, recording replays and labelled TTS replays.
# No synthetic measurement is treated as microphone recall evidence.
OBSERVED_CORRECTIONS = (
    ("欢迎优依。", "悠怡悠怡。"),
    ("欢迎悠怡", "悠怡悠怡"),
    ("有有有。", "悠怡悠怡。"),
    ("忧郁。", "悠怡。"),
    ("忧郁忧郁。", "悠怡悠怡。"),
    ("由于。", "悠怡。"),
    ("英语。", "悠怡。"),
    ("刘怡。", "悠怡。"),
    ("又一又一。", "悠怡悠怡。"),
    ("优姨今天有什么安排？", "悠怡今天有什么安排？"),
    ("悠移。", "悠怡。"),
    ("悠移悠移。", "悠怡悠怡。"),
    ("优仪。", "悠怡。"),
    ("优仪优仪。", "悠怡悠怡。"),
    ("优矣。", "悠怡。"),
    ("悠矣。", "悠怡。"),
    ("悠矣悠矣。", "悠怡悠怡。"),
)


@pytest.mark.parametrize(("raw", "expected"), OBSERVED_CORRECTIONS)
def test_observed_spelling_preserves_surrounding_whitespace_and_quotes(raw, expected):
    assert correct_wake_name_prefix(raw) == expected
    assert correct_wake_name_prefix(f' \t“{raw}”\n') == f' \t“{expected}”\n'


@pytest.mark.parametrize(("raw", "expected"), [
    ("欢迎优依，帮我打开灯。", "欢迎优依，帮我打开灯。"),
    ("欢迎悠怡帮我打开灯。", "欢迎悠怡帮我打开灯。"),
    ("有有有，今天有什么安排？", "有有有，今天有什么安排？"),
    ("优姨，解释一下优姨。", "悠怡，解释一下优姨。"),
    ("忧郁", "悠怡"),
    ("忧郁？！", "悠怡？！"),
    ("忧郁忧郁", "悠怡悠怡"),
    ("忧郁忧郁？！", "悠怡悠怡？！"),
    ("由于", "悠怡"),
    ("由于？！", "悠怡？！"),
    ("英语", "悠怡"),
    ("英语？！", "悠怡？！"),
    ("刘怡", "悠怡"),
    ("刘怡？！", "悠怡？！"),
    ("又一又一", "悠怡悠怡"),
    ("又一又一？！", "悠怡悠怡？！"),
])
def test_known_prefix_keeps_command_and_standalone_word_allows_only_endings(raw, expected):
    assert correct_wake_name_prefix(raw) == expected


@pytest.mark.parametrize("text", [
    "忧郁症怎么治疗？", "忧郁的人", "忧郁，怎么办？", "忧郁。帮我打开灯。",
    "忧郁、", "欢迎回来。", "悠怡和大家一起玩。",
    "忧郁忧郁的人。", "忧郁忧郁，怎么办？", "忧郁忧郁。帮我打开灯。",
    "我说忧郁忧郁。", "忧郁忧郁、",
    "由于天气不好，取消出门。", "这是由于网络问题。", "由于，嗯，天气不好。",
    "由于。帮我打开灯。", "由于、", "悠怡，由于天气不好，取消出门。",
    "英语怎么学？", "英语。帮我翻译一下。", "英语，怎么说？",
    "我说英语。", "悠怡，教我英语。", "英语、",
    "刘怡是谁？", "我叫刘怡。", "刘怡，帮我打开灯。", "刘怡。帮我打开灯。",
    "刘怡、", "悠怡，刘怡是谁？", "刘怡刘怡。",
    "又一又一地出现。", "又一又一，帮我打开灯。", "又一又一。帮我打开灯。",
    "我说又一又一。", "又一。", "又一又一、",
    "我说有有有。", "有有。", "我刚才喊了优姨。", "悠怡，欢迎优依。",
    "悠怡悠怡。", "有人吗？", "有意义。",
])
def test_unobserved_or_interior_words_are_preserved(text):
    assert correct_wake_name_prefix(text) == text


@pytest.mark.asyncio
@pytest.mark.runtime
@pytest.mark.parametrize("spelling", ["有有有", "欢迎悠怡", "欢迎优依"])
@pytest.mark.parametrize("suffix", [
    "，今天有什么安排？", "帮我打开灯。", "。欢迎回来。", "？真的吗？",
    "、", "，", "；",
])
async def test_ambiguous_wake_spellings_preserve_following_content(spelling, suffix):
    raw = f' \t“{spelling}{suffix}”\n'
    assert correct_wake_name_prefix(raw) == raw
    runtime = _runtime()
    _status(runtime)
    token = _token(runtime, 1)
    assert await runtime._prepare_voice_input_turn(token)
    await _final(runtime, token, raw)
    assert _texts(runtime) == [raw]
    runtime.session.create_response.assert_awaited_once_with(raw)


@pytest.mark.asyncio
@pytest.mark.runtime
@pytest.mark.parametrize(("raw", "expected"), OBSERVED_CORRECTIONS)
async def test_only_wake_first_final_changes_history_and_model(raw, expected):
    runtime = _runtime()
    _status(runtime)
    first = _token(runtime, 1)
    assert await runtime._prepare_voice_input_turn(first)
    await runtime._dispatch_voice_input_partial(VoicePartialEvent(turn_token=first, text=raw))
    assert any(
        call.args[0].get("type") == "user_transcript_preview"
        and call.args[0].get("text") == raw
        for call in runtime.websocket.send_json.await_args_list
    )
    await _final(runtime, first, raw)
    assert _texts(runtime) == [expected]
    runtime.session.create_response.assert_awaited_once_with(expected)
    second = _token(runtime, 2)
    assert await runtime._prepare_voice_input_turn(second)
    await _final(runtime, second, raw)
    assert _texts(runtime) == [expected, raw]
    assert runtime.session.create_response.await_args.args == (raw,)


@pytest.mark.asyncio
@pytest.mark.runtime
@pytest.mark.parametrize("reason", [None, "owner_confirmed"])
@pytest.mark.parametrize(("raw", "expected"), OBSERVED_CORRECTIONS)
async def test_ordinary_and_voiceprint_turns_keep_observed_text(reason, raw, expected):
    runtime = _runtime()
    if reason:
        _status(runtime, reason)
    token = _token(runtime, 1)
    assert await runtime._prepare_voice_input_turn(token)
    await _final(runtime, token, raw)
    assert _texts(runtime) == [raw]
    runtime.session.create_response.assert_awaited_once_with(raw)
