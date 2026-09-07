import asyncio
from types import SimpleNamespace

import pytest

from plugin.sdk.shared.core.cards import CardSubmissionError
from plugin.sdk.shared.core.context import SdkContext


def test_create_update_snapshot_and_target_binding():
    sent = []
    host = SimpleNamespace(_current_lanlan="Alice", push_message=lambda **kw: sent.append(kw) or {"submitted": True})
    ctx = SdkContext(host)

    async def run():
        actions = {"go": {"entry": "play", "args": {"id": 1}}}
        card = await ctx.create_card(html="<button>Play</button>", summary="Play", actions=actions)
        actions["go"]["args"]["id"] = 2
        host._current_lanlan = "Bob"
        await card.update(html="Playing")
        await card.update(actions={})
        return card

    card = asyncio.run(run())
    assert all(item["target_lanlan"] == "Alice" for item in sent)
    assert all(item["ai_behavior"] == "blind" and item["visibility"] == ["chat"] for item in sent)
    assert sent[0]["parts"][0]["actions"]["go"]["args"]["id"] == 1
    assert sent[1]["parts"][0] == {"type": "html_card", "card_id": card.id, "operation": "update", "html": "Playing"}
    assert sent[2]["parts"][0]["actions"] == {}


def test_recover_handle_and_submission_error():
    sent = []
    host = SimpleNamespace(push_message=lambda **kw: sent.append(kw) or {"submitted": False, "reason": "backpressure"})
    ctx = SdkContext(host)
    card = ctx.get_card("existing", target_lanlan="Alice")
    with pytest.raises(CardSubmissionError, match="backpressure"):
        asyncio.run(card.update(summary="Updated"))
    assert sent[0]["target_lanlan"] == "Alice"
    assert sent[0]["parts"][0]["card_id"] == "existing"
    with pytest.raises(CardSubmissionError):
        asyncio.run(ctx.create_card(html="hi", summary="hi"))


def test_invalid_payload_is_rejected_before_submission():
    ctx = SdkContext(SimpleNamespace(push_message=lambda **kw: pytest.fail("invalid payload sent")))
    with pytest.raises(TypeError):
        asyncio.run(ctx.create_card(html=123, summary="bad"))
    with pytest.raises(ValueError):
        asyncio.run(ctx.create_card(html="", summary="", actions={"play": {}}))
    with pytest.raises(TypeError):
        asyncio.run(ctx.get_card("card").update(target_lanlan="Bob"))
