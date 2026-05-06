from __future__ import annotations

from plugin.plugins.lifekit._chat import blocks_to_text, push_lifekit_content


class _Plugin:
    plugin_id = "lifekit"

    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []

    def push_message(self, **kwargs: object) -> dict[str, object]:
        self.messages.append(kwargs)
        return {"ok": True}


def test_blocks_to_text_formats_supported_blocks() -> None:
    text = blocks_to_text(
        [
            {"type": "text", "text": "hello"},
            {"type": "image", "url": "https://example.test/a.png", "alt": "A"},
            {"type": "url", "url": "https://example.test", "title": "Example"},
            {"type": "unknown", "text": "ignored"},
        ]
    )

    assert text == "hello\n\n![A](https://example.test/a.png)\n\n[Example](https://example.test)"


def test_push_lifekit_content_uses_existing_push_message_passthrough() -> None:
    plugin = _Plugin()

    result = push_lifekit_content(plugin, [{"type": "text", "text": "weather"}])

    assert result == {"ok": True}
    assert plugin.messages == [
        {
            "source": "lifekit",
            "visibility": ["chat"],
            "ai_behavior": "blind",
            "parts": [{"type": "text", "text": "weather"}],
            "metadata": {"context_type": "lifekit_content"},
            "target_lanlan": None,
        }
    ]
