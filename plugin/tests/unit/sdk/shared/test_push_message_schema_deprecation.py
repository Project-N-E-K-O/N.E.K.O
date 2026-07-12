from __future__ import annotations

import warnings

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
