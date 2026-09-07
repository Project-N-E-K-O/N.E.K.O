import asyncio

from main_logic.plugin_cards import _targets, deliver_plugin_card


class Manager:
    def __init__(self):
        self.frames = []

    async def render_chat_blocks(self, blocks, **kwargs):
        self.frames.append((blocks, kwargs))
        return True


def test_untargeted_updates_remain_with_original_character():
    _targets.clear()
    alice, bob = Manager(), Manager()
    managers = {"Alice": alice, "Bob": bob}
    event = {"plugin_id": "demo", "card": {"type": "html_card", "card_id": "one",
             "operation": "create", "html": "Hello", "summary": "Hello"}}
    assert asyncio.run(deliver_plugin_card(event, managers, "Alice"))
    event["card"] = {"card_id": "one", "operation": "update", "html": "Updated"}
    assert asyncio.run(deliver_plugin_card(event, managers, "Bob"))
    assert len(alice.frames) == 2 and not bob.frames
    assert alice.frames[1][0][0]["targetLanlan"] == "Alice"
    assert "summary" not in alice.frames[1][0][0]
    # Missing original character must not cause a cross-character fallback.
    assert not asyncio.run(deliver_plugin_card(event, {"Bob": bob}, "Bob"))
    _targets.clear()


def test_targeted_create_and_unknown_update_do_not_fallback():
    _targets.clear()
    bob = Manager()
    event = {"plugin_id": "demo", "lanlan_name": "Alice", "card": {
        "card_id": "two", "operation": "create", "html": "Hi", "summary": "Hi"}}
    assert not asyncio.run(deliver_plugin_card(event, {"Bob": bob}, "Bob"))
    event.pop("lanlan_name")
    event["card"]["operation"] = "update"
    assert not asyncio.run(deliver_plugin_card(event, {"Bob": bob}, "Bob"))
