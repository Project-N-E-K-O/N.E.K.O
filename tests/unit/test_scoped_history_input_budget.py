from types import SimpleNamespace

from config import (
    SCOPED_HISTORY_BATCH_CONTENT_MAX_TOKENS,
    SCOPED_HISTORY_PER_MESSAGE_MAX_TOKENS,
)
from config.prompts.prompts_memory import (
    SCOPED_BATCH_MIDDLE_OMISSION_MARKER,
    get_scoped_batch_middle_omission_marker,
)
from memory.facts import FactStore
from utils.tokenize import count_tokens


def _segment(messages: list[str]) -> dict:
    return {
        "speaker_label": "Alice(1001)",
        "messages": [
            SimpleNamespace(type="human", content=message) for message in messages
        ],
    }


def _single_line_bodies(rendered: str) -> list[str]:
    prefix = "Alice(1001) | "
    return [
        line.removeprefix(prefix)
        for line in rendered.splitlines()
        if line.startswith(prefix)
    ]


def test_scoped_batch_omission_marker_covers_every_supported_locale():
    assert set(SCOPED_BATCH_MIDDLE_OMISSION_MARKER) == {
        "zh",
        "zh-TW",
        "en",
        "ja",
        "ko",
        "ru",
        "es",
        "pt",
    }
    assert all(SCOPED_BATCH_MIDDLE_OMISSION_MARKER.values())


def test_scoped_batch_message_budget_preserves_normal_text_and_both_long_ends():
    normal = "普通消息 stays exactly intact: [] | punctuation!"
    oversized = "BEGIN-important " + ("界" * 2000) + " END-important"
    marker = get_scoped_batch_middle_omission_marker("en")

    rendered = FactStore._format_speaker_segments(
        [_segment([normal, oversized])],
        nonce="abcd1234",
        lang="en",
    )
    bodies = _single_line_bodies(rendered)

    assert bodies[0] == normal
    assert bodies[1].startswith("BEGIN-important ")
    assert bodies[1].endswith(" END-important")
    assert marker in bodies[1]
    assert count_tokens(bodies[1]) <= SCOPED_HISTORY_PER_MESSAGE_MAX_TOKENS
    assert oversized not in rendered


def test_scoped_batch_total_budget_is_bounded_without_starving_late_messages():
    messages = [
        f"head-{index} " + ("界" * 1000) + f" tail-{index}" for index in range(200)
    ]
    marker = get_scoped_batch_middle_omission_marker("en")

    rendered = FactStore._format_speaker_segments(
        [_segment(messages)],
        nonce="abcd1234",
        lang="en",
    )
    bodies = _single_line_bodies(rendered)

    assert len(bodies) == len(messages)
    assert sum(count_tokens(body) for body in bodies) <= (
        SCOPED_HISTORY_BATCH_CONTENT_MAX_TOKENS
    )
    assert all(marker in body for body in bodies)
    assert all(body.startswith(f"head-{index} ") for index, body in enumerate(bodies))
    assert all(body.endswith(f" tail-{index}") for index, body in enumerate(bodies))
