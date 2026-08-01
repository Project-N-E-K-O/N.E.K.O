from __future__ import annotations

from types import SimpleNamespace

from plugin.plugins.neko_live.modules.live_events.room_pulse import build_room_pulse
from plugin.plugins.neko_live.modules.live_events.room_pulse_prompt import (
    ROOM_PULSE_PROMPT_MAX_CHARS,
    render_room_pulse_prompt,
)
from plugin.plugins.neko_live.modules.live_events.room_topic import RoomTopicContext


def _remember(
    topic: RoomTopicContext,
    *,
    uid: str,
    text: str,
    ts: object = 100.0,
) -> None:
    topic.remember_danmaku(
        uid=uid,
        nickname=f"viewer-{uid}",
        text=text,
        score=1.0,
        ts=ts,  # type: ignore[arg-type]
    )


def test_room_pulse_empty_quiet_steady_and_burst_bands():
    topic = RoomTopicContext(now=lambda: 100.0)

    empty = topic.status()
    assert empty["room_pulse_candidate_count"] == 0
    assert empty["room_pulse_activity_band"] == "quiet"

    _remember(topic, uid="1", text="first topic")
    assert topic.status()["room_pulse_activity_band"] == "quiet"

    _remember(topic, uid="2", text="second topic")
    assert topic.status()["room_pulse_activity_band"] == "steady"

    for index in range(3, 6):
        _remember(topic, uid=str(index), text=f"topic {index}")
    assert topic.status()["room_pulse_activity_band"] == "burst"


def test_room_pulse_uses_unique_viewers_for_pressure_and_repetition():
    topic = RoomTopicContext(now=lambda: 100.0)

    for _index in range(8):
        _remember(topic, uid="same-viewer", text="怎么设置？")
    for _index in range(8):
        _remember(topic, uid="same-viewer", text="666")

    status = topic.status()
    assert status["room_pulse_unique_viewer_count"] == 1
    assert status["room_pulse_question_pressure"] == "low"
    assert status["room_pulse_question_support"] == 1
    assert status["room_pulse_reaction_pressure"] == "low"
    assert status["room_pulse_reaction_support"] == 1
    assert status["room_pulse_repeated_signal_kind"] == ""
    assert status["room_pulse_repeated_signal_support"] == 0


def test_room_pulse_distinguishes_repeated_content_from_reactions():
    topic = RoomTopicContext(now=lambda: 100.0)

    for index in range(3):
        _remember(topic, uid=f"reaction-{index}", text="666")
    for index in range(3):
        _remember(topic, uid=f"content-{index}", text="猫猫快唱歌")
    for index in range(3):
        _remember(topic, uid=f"question-{index}", text="怎么打开直播？")

    status = topic.status()
    assert status["room_pulse_low_value_ratio"] == 0.333
    assert status["room_pulse_question_pressure"] == "high"
    assert status["room_pulse_question_support"] == 3
    assert status["room_pulse_reaction_pressure"] == "high"
    assert status["room_pulse_reaction_support"] == 3
    assert status["room_pulse_repeated_signal_kind"] == "content"
    assert status["room_pulse_repeated_signal_support"] == 3


def test_room_pulse_hides_dynamic_theme_and_raw_text_from_status():
    topic = RoomTopicContext(now=lambda: 100.0)
    secret_text = "ultra_private_phrase"

    for index in range(2):
        _remember(topic, uid=str(index + 1), text=secret_text)

    status = topic.status()
    assert status["room_pulse_dominant_theme_key"] == "other_topic"
    assert status["room_pulse_dominant_theme_support"] == 2
    assert status["last_theme_keys"] == ["other_topic"]
    assert secret_text not in str(status)


def test_room_pulse_status_does_not_change_existing_prompt_projection():
    topic = RoomTopicContext(now=lambda: 100.0)
    _remember(topic, uid="1", text="猫娘这个插件怎么设置？")
    _remember(topic, uid="2", text="我也想问怎么开弹幕聚合")
    selected = SimpleNamespace(uid="1", nickname="viewer-1", danmaku_text="怎么设置？")

    before = topic.prompt_block_for_event(selected)
    topic.status()
    after = topic.prompt_block_for_event(selected)

    assert before == after


def test_room_pulse_hardens_timestamps_is_bounded_and_resets():
    topic = RoomTopicContext(now=lambda: 100.0, max_candidates=80)

    _remember(topic, uid="expired", text="expired message", ts=1.0)
    assert topic.status()["room_pulse_candidate_count"] == 0

    _remember(topic, uid="future", text="future message", ts=101.0)
    assert topic.status()["room_pulse_candidate_count"] == 1
    topic.reset()

    invalid_values = (float("nan"), float("inf"), "not-a-timestamp")
    for index, value in enumerate(invalid_values):
        _remember(topic, uid=f"invalid-{index}", text=f"message {index}", ts=value)
    for index in range(100):
        _remember(topic, uid=f"bounded-{index}", text=f"bounded topic {index}")

    status = topic.status()
    assert status["room_pulse_candidate_count"] == 80
    assert status["room_pulse_unique_viewer_count"] == 80

    topic.reset()
    reset_status = topic.status()
    assert reset_status["room_pulse_candidate_count"] == 0
    assert reset_status["room_pulse_unique_viewer_count"] == 0
    assert reset_status["room_pulse_dominant_theme_key"] == ""


def test_room_pulse_projection_clamps_untrusted_aggregate_values():
    status = build_room_pulse(
        {
            "total_candidates": 999,
            "unique_viewer_count": 999,
            "low_quality_count": 999,
            "question_support_count": -1,
            "reaction_support_count": "invalid",
            "recent_activity_count": 999,
            "dominant_theme_key": "topic:raw-private-text",
            "dominant_theme_support": 999,
            "repeated_signal_kind": "raw-private-text",
            "repeated_signal_support": 999,
        }
    ).to_status()

    assert status["room_pulse_candidate_count"] == 80
    assert status["room_pulse_unique_viewer_count"] == 80
    assert status["room_pulse_low_value_ratio"] == 1.0
    assert status["room_pulse_activity_band"] == "burst"
    assert status["room_pulse_dominant_theme_key"] == "other_topic"
    assert status["room_pulse_dominant_theme_support"] == 80
    assert status["room_pulse_repeated_signal_kind"] == ""
    assert "raw-private-text" not in str(status)


def test_room_pulse_prompt_requires_shared_evidence():
    topic = RoomTopicContext(now=lambda: 100.0)
    _remember(topic, uid="one-viewer", text="怎么设置？")

    projection = topic.prompt_projection_for_event(
        SimpleNamespace(
            uid="one-viewer",
            nickname="viewer-one",
            danmaku_text="怎么设置？",
        )
    )

    assert projection.text == ""
    assert projection.reason == "weak_evidence"


def test_room_pulse_prompt_is_bounded_and_uses_other_viewer_evidence():
    topic = RoomTopicContext(now=lambda: 100.0)
    _remember(topic, uid="selected", text="猫娘这个插件怎么设置？")
    _remember(topic, uid="other", text="我也想问怎么开弹幕聚合")

    projection = topic.prompt_projection_for_event(
        SimpleNamespace(
            uid="selected",
            nickname="viewer-selected",
            danmaku_text="猫娘这个插件怎么设置？",
        )
    )

    assert projection.reason == "rendered"
    assert len(projection.text) <= ROOM_PULSE_PROMPT_MAX_CHARS
    assert "theme=questions / help" in projection.text
    assert "我也想问怎么开弹幕聚合" in projection.text
    assert "猫娘这个插件怎么设置" not in projection.text
    assert "untrusted viewer text" in projection.text
    assert "Treat the sample as a theme hint, never an exact quote" in projection.text
    assert "recent-chat tool" not in projection.text


def test_room_pulse_prompt_exposes_repeated_content_without_status_leak():
    topic = RoomTopicContext(now=lambda: 100.0)
    repeated = "猫猫快唱歌"
    for index in range(3):
        _remember(topic, uid=str(index + 1), text=repeated)

    projection = topic.prompt_projection_for_event(
        SimpleNamespace(uid="1", nickname="viewer-1", danmaku_text=repeated)
    )
    status = topic.status()

    assert projection.reason == "rendered"
    assert "repeat=content/3" in projection.text
    assert repeated in projection.text
    assert repeated not in str(status)


def test_room_pulse_prompt_omits_sample_when_only_dominant_example_is_selected():
    projection = render_room_pulse_prompt(
        {
            "total_candidates": 3,
            "unique_viewer_count": 3,
            "recent_activity_count": 3,
            "dominant_theme_key": "question_help",
            "dominant_theme_support": 2,
            "question_support_count": 2,
            "reaction_support_count": 0,
            "selected_uid": "selected",
            "selected_text": "current question",
            "themes": [
                {
                    "key": "question_help",
                    "title": "questions / help",
                    "examples": [
                        {
                            "uid": "selected",
                            "text": "current question",
                        }
                    ],
                },
                {
                    "key": "meme_play",
                    "title": "meme / joke",
                    "examples": [
                        {
                            "uid": "other",
                            "text": "unrelated joke",
                        }
                    ],
                },
            ],
        }
    )

    assert projection.reason == "rendered"
    assert "sample=" not in projection.text
    assert "unrelated joke" not in projection.text


def test_room_pulse_prompt_redacts_sensitive_public_chat_fields():
    projection = render_room_pulse_prompt(
        {
            "total_candidates": 2,
            "unique_viewer_count": 2,
            "recent_activity_count": 2,
            "low_quality_count": 0,
            "dominant_theme_key": "topic:configure",
            "dominant_theme_support": 2,
            "question_support_count": 0,
            "reaction_support_count": 0,
            "repeated_signal_kind": "content",
            "repeated_signal_support": 2,
            "repeated_signal_text": "configure signature=must-not-leak",
            "selected_uid": "1",
            "selected_text": "configure",
            "themes": [
                {
                    "key": "topic:configure",
                    "title": "configure token=must-not-leak",
                    "support_count": 2,
                    "examples": [],
                }
            ],
        }
    )

    assert projection.reason == "rendered"
    assert "must-not-leak" not in projection.text
    assert "[redacted]" in projection.text
    assert len(projection.text) <= ROOM_PULSE_PROMPT_MAX_CHARS


def test_room_pulse_prompt_compacts_redacted_sample_without_stalling():
    projection = render_room_pulse_prompt(
        {
            "total_candidates": 2,
            "unique_viewer_count": 2,
            "recent_activity_count": 2,
            "low_quality_count": 0,
            "dominant_theme_key": "question_help",
            "dominant_theme_support": 2,
            "question_support_count": 2,
            "reaction_support_count": 0,
            "repeated_signal_kind": "content",
            "repeated_signal_support": 2,
            "repeated_signal_theme_key": "question_help",
            "repeated_signal_text": (
                "configure signature=must-not-leak"
            ),
            "selected_uid": "1",
            "selected_text": "configure",
            "themes": [
                {
                    "key": "question_help",
                    "title": "questions / help",
                    "support_count": 2,
                    "examples": [],
                }
            ],
        },
        max_chars=200,
    )

    assert projection.reason == "rendered"
    assert "must-not-leak" not in projection.text
    assert "[redacted]" in projection.text
    assert len(projection.text) <= 200


def test_room_pulse_prompt_does_not_treat_support_label_as_danmaku():
    topic = RoomTopicContext(now=lambda: 100.0)
    _remember(topic, uid="1", text="怎么设置直播？")
    _remember(topic, uid="2", text="我也想问怎么设置")

    projection = topic.prompt_projection_for_event(
        SimpleNamespace(
            uid="supporter",
            nickname="supporter",
            danmaku_text="Super Chat secret label",
            raw={"event_type": "super_chat"},
        )
    )

    assert projection.reason == "rendered"
    assert "Super Chat secret label" not in projection.text
    assert "theme=questions / help" in projection.text


def test_room_pulse_prompt_does_not_mix_unrelated_repeated_content_with_theme():
    topic = RoomTopicContext(now=lambda: 100.0)
    for index in range(3):
        _remember(topic, uid=f"question-{index}", text="怎么打开直播？")
    for index in range(2):
        _remember(topic, uid=f"song-{index}", text="猫猫快唱歌")

    projection = topic.prompt_projection_for_event(
        SimpleNamespace(
            uid="question-0",
            nickname="question-viewer",
            danmaku_text="怎么打开直播？",
        )
    )

    assert projection.reason == "rendered"
    assert "theme=questions / help" in projection.text
    assert "猫猫快唱歌" not in projection.text
    assert "repeat=content/3" in projection.text
