"""A mid-turn model switch must not drop the turn's raised token budget.

``switch_model`` runs during a turn when a tool returns an image and the host
moves to the vision model. ``stream_text`` may already have raised
``llm.max_completion_tokens`` — to the long-response summary floor, or by the
focus-thinking bump — and the replacement client was built from
``max_response_length`` alone, so the raise was silently lost. The turn's
``finally`` then wrote the *old* client's saved value onto the replacement.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest


def _client(monkeypatch: pytest.MonkeyPatch, live_budget, derived: int):
    from main_logic.omni_offline_client import _streaming

    created: dict = {}

    async def _fake_create(model, base_url, api_key, **kwargs):
        created.update(kwargs)
        return SimpleNamespace(
            max_completion_tokens=kwargs.get("max_completion_tokens")
        )

    monkeypatch.setattr(_streaming, "create_chat_llm_async", _fake_create)
    monkeypatch.setattr(_streaming, "_budget_to_max_tokens", lambda _v: derived)

    obj = _streaming._StreamingMixin.__new__(_streaming._StreamingMixin)
    obj.model = "conversation-model"
    obj.max_response_length = 200
    obj.base_url = "http://x"
    obj.api_key = "k"
    obj.vision_base_url = "http://x"
    obj.vision_api_key = "k"
    obj.llm = SimpleNamespace(max_completion_tokens=live_budget)
    obj._conversation_history = []
    obj._genai_client = None
    obj.provider_type = None
    obj.vision_provider_type = None
    return obj, created


@pytest.mark.parametrize(
    ("live", "derived", "expected"),
    [
        (3000, 512, 3000),  # summary floor raised mid-turn -> keep it
        (512, 512, 512),  # nothing raised -> baseline
        (128, 512, 512),  # stale lower value -> do not resurrect it
        (None, 512, 512),  # no live client yet
        # unlimited sentinel: _budget_to_max_tokens returns None so the request
        # omits the field. That is already the highest budget there is, so a
        # finite live value must not overwrite it — and comparing int > None
        # would raise, blocking the very switch this carry-over protects.
        (3000, None, None),
        (None, None, None),
    ],
)
def test_the_replacement_client_keeps_the_higher_budget(
    monkeypatch: pytest.MonkeyPatch, live, derived: int, expected: int
) -> None:
    """Mutation: build the new client from ``max_response_length`` alone."""
    obj, created = _client(monkeypatch, live, derived)

    asyncio.run(obj.switch_model("vision-model", use_vision_config=True))

    assert created.get("max_completion_tokens") == expected
