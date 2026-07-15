from scripts.soniox_realtime_smoke import _render_tokens


def test_smoke_waits_for_end_and_filters_control_tokens():
    final_tokens = []
    text, saw_text, saw_end = _render_tokens(
        {
            "tokens": [
                {"text": "hello ", "is_final": True},
                {"text": "wor", "is_final": False},
            ]
        },
        final_tokens,
    )
    assert (text, saw_text, saw_end) == ("hello wor", True, False)

    text, saw_text, saw_end = _render_tokens(
        {
            "tokens": [
                {"text": "world", "is_final": True},
                {"text": "<end>", "is_final": True},
                {"text": "<fin>", "is_final": True},
            ]
        },
        final_tokens,
    )
    assert (text, saw_text, saw_end) == ("hello world", True, True)
    assert "<end>" not in text and "<fin>" not in text


def test_smoke_preview_can_be_provisional_but_endpoint_uses_stable_tokens():
    final_tokens = ["stable"]
    text, saw_text, saw_end = _render_tokens(
        {
            "tokens": [
                {"text": " temporary", "is_final": False},
                {"text": "<end>", "is_final": True},
            ]
        },
        final_tokens,
    )
    assert (text, saw_text, saw_end) == ("stable temporary", True, True)
    assert "".join(final_tokens).strip() == "stable"
