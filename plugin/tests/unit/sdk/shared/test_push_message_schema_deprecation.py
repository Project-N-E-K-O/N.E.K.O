from __future__ import annotations

import warnings

import pytest

from plugin.sdk.shared.core.push_message_schema import translate_push_message


def test_every_active_v1_field_warns_even_when_v2_fields_shadow_it() -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", DeprecationWarning)
        payload = translate_push_message(
            visibility=["chat"],
            ai_behavior="blind",
            parts=[{"type": "text", "text": "canonical"}],
            message_type="text",
            description="legacy label",
            content="shadowed",
            binary_data=b"shadowed",
            binary_url="https://example.test/shadowed.png",
            mime="image/png",
            delivery="passive",
            reply=False,
            unsafe=True,
            fast_mode=True,
        )

    messages = [str(item.message) for item in caught]
    expected_fields = (
        "message_type",
        "description",
        "content",
        "binary_data",
        "binary_url",
        "mime",
        "delivery",
        "reply",
        "unsafe",
        "fast_mode",
    )
    assert len(messages) == len(expected_fields)
    for field in expected_fields:
        warning_prefix = (
            "push_message: 'message_type="
            if field == "message_type"
            else f"push_message: '{field}' is deprecated"
        )
        assert sum(message.startswith(warning_prefix) for message in messages) == 1
    assert payload["parts"] == [{"type": "text", "text": "canonical"}]
    assert payload["visibility"] == ["chat"]
    assert payload["ai_behavior"] == "blind"
    assert payload["_legacy_call"] is True


def test_inactive_v1_defaults_do_not_warn_or_mark_call_legacy() -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", DeprecationWarning)
        payload = translate_push_message(
            parts=[],
            message_type=None,
            content=None,
            unsafe=False,
            fast_mode=False,
        )

    assert caught == []
    assert payload["_legacy_call"] is False


@pytest.mark.parametrize("message_type", ["music_play_url", "music_allowlist_add"])
def test_explicit_v2_fields_override_known_legacy_message_type(message_type: str) -> None:
    canonical_parts = [{"type": "text", "text": "canonical"}]

    with pytest.warns(DeprecationWarning):
        payload = translate_push_message(
            parts=canonical_parts,
            visibility=["hud"],
            ai_behavior="read",
            message_type=message_type,
            metadata={"url": "https://example.test/song.mp3", "domains": ["example.test"]},
        )

    assert payload["parts"] == canonical_parts
    assert payload["visibility"] == ["hud"]
    assert payload["ai_behavior"] == "read"


@pytest.mark.parametrize(
    ("message_type", "expected_action", "expected_visibility"),
    [
        ("music_play_url", "media_play_url", ["chat"]),
        ("music_allowlist_add", "media_allowlist_add", []),
    ],
)
def test_known_legacy_message_type_keeps_defaults_when_v2_fields_are_absent(
    message_type: str,
    expected_action: str,
    expected_visibility: list[str],
) -> None:
    with pytest.warns(DeprecationWarning):
        payload = translate_push_message(
            message_type=message_type,
            metadata={"url": "https://example.test/song.mp3", "domains": ["example.test"]},
        )

    assert payload["parts"][0]["action"] == expected_action
    assert payload["visibility"] == expected_visibility
    assert payload["ai_behavior"] == "blind"


@pytest.mark.parametrize(
    ("message_type", "expected_visibility"),
    [
        ("music_play_url", ["chat"]),
        ("music_allowlist_add", []),
    ],
)
def test_explicit_v2_axes_override_legacy_defaults_independently(
    message_type: str,
    expected_visibility: list[str],
) -> None:
    with pytest.warns(DeprecationWarning):
        explicit_visibility = translate_push_message(
            visibility=["hud"],
            message_type=message_type,
        )
    with pytest.warns(DeprecationWarning):
        explicit_ai_behavior = translate_push_message(
            ai_behavior="read",
            message_type=message_type,
        )

    assert explicit_visibility["visibility"] == ["hud"]
    assert explicit_visibility["ai_behavior"] == "blind"
    assert explicit_ai_behavior["visibility"] == expected_visibility
    assert explicit_ai_behavior["ai_behavior"] == "read"
