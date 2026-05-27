from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from plugin.plugins.study_companion.voice_filter import (
    CATGIRL_NAMES,
    OCR_TRUNCATION,
    VoiceFilter,
    _derive_subject,
    _find_earliest_name,
    _has_question_intent,
    _number_sequence_match,
    _text_overlap_ratio,
    build_context_for_catgirl,
    safe_cancel_response,
)

pytestmark = pytest.mark.unit


def test_voice_filter_defaults_to_yui_names() -> None:
    voice_filter = VoiceFilter()

    assert voice_filter.names == CATGIRL_NAMES


def test_voice_filter_loads_current_catgirl_name_and_nicknames_from_config_manager() -> None:
    class ConfigManager:
        def get_character_data(self) -> tuple[object, ...]:
            return (
                "master",
                "Mika",
                {},
                {"Mika": {"昵称": "米卡, 小M", "nicknames": ["Sensei"]}},
            )

    voice_filter = VoiceFilter(config_manager=ConfigManager())

    assert voice_filter.names == ("Mika", "米卡", "小M", "Sensei")


def test_find_earliest_name_is_case_insensitive_and_uses_first_position() -> None:
    name, pos = _find_earliest_name("先想一下 NEKO 这里怎么做 yui", ["yui", "neko"])

    assert name == "neko"
    assert pos == 5


def test_find_earliest_name_uses_word_boundaries_for_ascii_names() -> None:
    text = "I was buying time"
    name, pos = _find_earliest_name(text, ["yui"])

    assert name is None
    assert pos == len(text)

    name, pos = _find_earliest_name("I was buying time before asking Yui.", ["yui"])

    assert name == "yui"
    assert pos == 32


def test_find_earliest_name_keeps_substring_matching_for_cjk_names() -> None:
    name, pos = _find_earliest_name("这个题猫娘帮我看一下", ["猫娘"])

    assert name == "猫娘"
    assert pos == 3


def test_name_call_relay_splits_pre_context_and_question() -> None:
    voice_filter = VoiceFilter(names=["猫娘"])

    result = voice_filter.filter("这个求导我懂，猫娘为什么这里是3x²", "f(x)=x³")

    assert result is not None
    assert result["should_relay"] is True
    assert result["method"] == "name_call"
    assert result["pre_context"] == "这个求导我懂，"
    assert result["question"] == "为什么这里是3x²"


def test_name_call_uses_extra_names_for_current_voice_event() -> None:
    voice_filter = VoiceFilter(names=["Yui"])

    result = voice_filter.filter(
        "Mika why is this step valid",
        "this step is valid",
        extra_names=["Mika"],
    )

    assert result is not None
    assert result["should_relay"] is True
    assert result["method"] == "name_call"
    assert result["name"] == "Mika"
    assert result["question"] == "why is this step valid"


def test_short_transcript_without_name_is_dropped() -> None:
    result = VoiceFilter(names=["猫娘"]).filter("嗯?", screen_text="函数求导")

    assert result == {"should_relay": False, "method": "too_short"}


def test_ocr_overlap_drops_reading_aloud_in_default_subject() -> None:
    result = VoiceFilter(names=["猫娘"]).filter(
        "已知函数fx等于三次方求导",
        screen_text="已知函数 f(x) 等于三次方，求导",
        subject="default",
    )

    assert result is not None
    assert result["should_relay"] is False
    assert result["method"] == "ocr_overlap"
    assert result["overlap"] > 0.6


def test_science_subject_number_sequence_drops_symbolic_math_reading() -> None:
    result = VoiceFilter(names=["猫娘"]).filter(
        "三次方加三x平方减九x",
        screen_text="f(x)=x³+3x²-9x+1，求 f'(x)",
        subject="math",
    )

    assert result is not None
    assert result["should_relay"] is False
    assert result["method"] == "ocr_overlap"
    assert result["number_match"] > 0.6


def test_name_window_protects_short_or_high_overlap_followup() -> None:
    now = 100.0

    def clock() -> float:
        return now

    voice_filter = VoiceFilter(names=["Yui"], clock=clock)
    assert voice_filter.filter("Yui 这一步", "这一步")["should_relay"] is True  # type: ignore[index]

    now = 102.0
    result = voice_filter.filter("嗯", "嗯")

    assert result is not None
    assert result["should_relay"] is True
    assert result["method"] == "name_window"


def test_name_window_is_scoped_by_session_key() -> None:
    now = 100.0

    def clock() -> float:
        return now

    voice_filter = VoiceFilter(names=["Yui"], clock=clock)
    assert (
        voice_filter.filter("Yui help here", "help here", session_key="session-a")[
            "should_relay"
        ]
        is True
    )

    now = 102.0
    other_session = voice_filter.filter("嗯", "嗯", session_key="session-b")
    same_session = voice_filter.filter("嗯", "嗯", session_key="session-a")

    assert other_session == {"should_relay": False, "method": "too_short"}
    assert same_session is not None
    assert same_session["should_relay"] is True
    assert same_session["method"] == "name_window"


@pytest.mark.asyncio
async def test_name_window_scoping_survives_concurrent_voice_events() -> None:
    now = 100.0

    def clock() -> float:
        return now

    voice_filter = VoiceFilter(names=["Yui"], clock=clock)

    first_a, first_b = await asyncio.gather(
        asyncio.to_thread(
            voice_filter.filter, "Yui help here", "", session_key="session-a"
        ),
        asyncio.to_thread(
            voice_filter.filter, "Yui check this", "", session_key="session-b"
        ),
    )
    assert first_a is not None and first_a["method"] == "name_call"
    assert first_b is not None and first_b["method"] == "name_call"

    now = 102.0
    follow_a, follow_b, unrelated = await asyncio.gather(
        asyncio.to_thread(voice_filter.filter, "嗯", "", session_key="session-a"),
        asyncio.to_thread(voice_filter.filter, "嗯", "", session_key="session-b"),
        asyncio.to_thread(voice_filter.filter, "嗯", "", session_key="session-c"),
    )

    assert follow_a is not None and follow_a["method"] == "name_window"
    assert follow_b is not None and follow_b["method"] == "name_window"
    assert unrelated == {"should_relay": False, "method": "too_short"}


def test_name_window_expires_and_short_audio_drops() -> None:
    now = 100.0

    def clock() -> float:
        return now

    voice_filter = VoiceFilter(names=["Yui"], clock=clock)
    assert voice_filter.filter("Yui 帮我看看", "") is not None

    now = 104.1
    result = voice_filter.filter("嗯", "")

    assert result == {"should_relay": False, "method": "too_short"}


def test_unknown_non_overlapping_transcript_relays_by_returning_none() -> None:
    result = VoiceFilter(names=["猫娘"]).filter(
        "整理例题结构",
        screen_text="已知函数 f(x)=x³+3x²-9x+1",
        subject="math",
    )

    assert result is None


def test_question_intent_relays_before_ocr_overlap() -> None:
    result = VoiceFilter(names=["猫娘"]).filter(
        "how should I start solving this equation",
        screen_text="Start solving this equation by isolating x.",
        subject="math",
    )

    assert result is not None
    assert result["should_relay"] is True
    assert result["method"] == "question_intent"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("why does this step work", True),
        ("帮我看看这一步", True),
        ("the passage compares two views", False),
        ("the prompt asks what caused the change", False),
    ],
)
def test_has_question_intent(text: str, expected: bool) -> None:
    assert _has_question_intent(text) is expected


def test_empty_transcript_is_ignored() -> None:
    assert VoiceFilter(names=["猫娘"]).filter("   ", screen_text="题目") is None


@pytest.mark.parametrize(
    ("ocr_text", "expected"),
    [
        ("f(x)=x³+3x²-9x+1，求导数", "math"),
        ("速度 v 和重力加速度 g 的关系", "physics"),
        ("H2O 和 NaCl 的化学反应", "chemistry"),
        ("CO2 与 H2O 的比例", "chemistry"),
        ("fe", "default"),
        ("safe reading material", "default"),
        ("long-term reading material", "default"),
        ("re-entry reading material", "default"),
        ("x-ray reading material", "default"),
        ("grade a - excellent", "default"),
        ("part a - 1", "default"),
        ("x - y = 3", "math"),
        ("x-1=0", "math"),
        ("普通阅读材料", "default"),
    ],
)
def test_derive_subject_from_ocr_text(ocr_text: str, expected: str) -> None:
    assert _derive_subject(ocr_text) == expected


def test_overlap_and_number_helpers_handle_empty_and_chinese_numbers() -> None:
    assert _text_overlap_ratio("", "abc") == 0
    assert _number_sequence_match("三次方加九", "x³-9x+1") == 1.0


def test_build_context_for_catgirl_includes_screen_state_prelude_and_question() -> None:
    stale_time = datetime.now(timezone.utc) - timedelta(seconds=10)
    state = SimpleNamespace(
        last_ocr_text="f(x)=x³+3x²-9x+1，求导数",
        last_ocr_at=stale_time.isoformat(),
        last_screen_classification={"screen_type": "question"},
        active_mode="teaching",
    )
    result = {
        "pre_context": "我知道幂函数",
        "question": "为什么 x³ 求导是 3x²",
        "subject": "math",
    }

    context = build_context_for_catgirl(
        "猫娘为什么 x³ 求导是 3x²",
        state,
        {"topic": "derivative", "subject": "math"},
        result,
    )

    assert "[屏幕OCR-数学符号可能有识别误差，请以常识判断]" in context
    assert "[注意] 屏幕内容可能已切换" in context
    assert "[状态] derivative | teaching" in context
    assert "[铺垫] 我知道幂函数" in context
    assert "[问题] 为什么 x³ 求导是 3x²" in context


def test_build_context_for_catgirl_omits_empty_state_separator() -> None:
    state = SimpleNamespace(
        last_ocr_text="",
        last_screen_classification={},
        active_mode="teaching",
    )

    context = build_context_for_catgirl(
        "猫娘讲一下",
        state,
        {"topic": ""},
        {"question": "讲一下这个知识点"},
    )

    assert "[状态] teaching" in context
    assert "[状态]  | teaching" not in context


@pytest.mark.parametrize(
    ("screen_type", "ocr_len", "expected_len"),
    [
        ("question", OCR_TRUNCATION["question"], OCR_TRUNCATION["question"]),
        ("question", OCR_TRUNCATION["question"] + 5, OCR_TRUNCATION["question"]),
        ("answering", OCR_TRUNCATION["answering"] + 5, OCR_TRUNCATION["answering"]),
        ("idle", OCR_TRUNCATION["idle"] + 5, OCR_TRUNCATION["idle"]),
        ("unknown", 505, 500),
    ],
)
def test_build_context_for_catgirl_truncates_screen_text_by_screen_type(
    screen_type: str, ocr_len: int, expected_len: int
) -> None:
    ocr_text = "x" * ocr_len
    state = SimpleNamespace(
        last_ocr_text=ocr_text,
        last_screen_classification={"screen_type": screen_type},
        active_mode="teaching",
    )

    context = build_context_for_catgirl(
        "Yui explain this",
        state,
        {"topic": "algebra", "subject": "default"},
        {"question": "explain this"},
    )

    screen_line = next(line for line in context.splitlines() if line.startswith("[屏幕] "))
    screen_payload = screen_line.removeprefix("[屏幕] ")
    assert screen_payload == ocr_text[:expected_len]
    assert len(screen_payload) == expected_len


class ResourceNotFound(Exception):
    pass


class InvalidStateError(Exception):
    pass


@dataclass
class CancelSession:
    exc: Exception | None = None
    calls: int = 0

    async def cancel_response(self) -> None:
        self.calls += 1
        if self.exc is not None:
            raise self.exc


@pytest.mark.asyncio
async def test_safe_cancel_response_returns_true_after_cancel() -> None:
    session = CancelSession()

    assert await safe_cancel_response(session) is True
    assert session.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("exc", [ResourceNotFound(), InvalidStateError()])
async def test_safe_cancel_response_swallows_known_race_exceptions(exc: Exception) -> None:
    session = CancelSession(exc=exc)

    assert await safe_cancel_response(session) is False
    assert session.calls == 1


@pytest.mark.asyncio
async def test_safe_cancel_response_reraises_unexpected_errors() -> None:
    session = CancelSession(exc=ValueError("network is down"))

    with pytest.raises(ValueError, match="network is down"):
        await safe_cancel_response(session)


@pytest.mark.asyncio
async def test_safe_cancel_response_handles_missing_session() -> None:
    assert await safe_cancel_response(None) is False
