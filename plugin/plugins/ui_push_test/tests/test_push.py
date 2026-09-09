"""Exercise plugin actions through real SDK handles; only the IPC sink is fake."""
import asyncio
import json
import logging
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from plugin.plugins.ui_push_test import UiPushTestPlugin
from plugin.sdk.plugin import Err

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def instance(tmp_path, monkeypatch):
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(tmp_path))
    sent = []
    host = SimpleNamespace(
        plugin_id="ui_push_test", config_path=ROOT / "plugin.toml", metadata={},
        logger=logging.getLogger("ui_push_test.tests"), _effective_config={},
        push_message=lambda **payload: sent.append(payload) or {"submitted": True},
    )
    return UiPushTestPlugin(host), sent, host


@pytest.mark.parametrize("surface", ["chat", "agent"])
def test_create_push_and_callback(instance, surface):
    plugin, sent, _ = instance

    async def run():
        assert isinstance(await getattr(plugin, f"{surface}_push")("Alice", "too early"), Err)
        assert sent == []
        created = (await getattr(plugin, f"{surface}_create")(" Alice ", title="<test>")).unwrap()
        updated = (await getattr(plugin, f"{surface}_push")(
            "Alice", '<p>New body</p>', css="p {color: red}", title="Updated", summary="new summary",
        )).unwrap()
        assert updated["display_id"] == created["display_id"]
        part = sent[-1]["parts"][0]
        callback = part["actions"]["echo"]
        context = {"card_id": part["card_id"], "lanlan_name": "Alice", "run_id": "test-run"}
        if surface == "agent":
            context["view_id"] = part["card_id"]
        reply = (await getattr(plugin, callback["entry"])(**callback["args"], _ctx=context)).unwrap()
        assert reply["operation"] == f"{surface}_callback"
        assert reply["display_id"] == created["display_id"]
        state = await plugin.dashboard()
        assert state["targets"][0][f"{surface}_updates"] == 1
        assert state["events"][0]["run_id"] == "test-run"

    asyncio.run(run())
    assert len(sent) == 2  # The echo records a callback without another push.
    assert all(p["target_lanlan"] == "Alice" and p["ai_behavior"] == "blind" for p in sent)
    first, last = [p["parts"][0] for p in sent]
    assert first["operation"] == "create" and "&lt;test&gt;" in first["html"]
    assert last["operation"] == "update" and "<p>New body</p>" in last["html"]
    assert last["css"] == "p {color: red}" and last["summary"] == "new summary"
    assert last.get("presentation", "chat") == surface
    if surface == "agent":
        assert last["title"] == "Updated"


def test_multiple_targets_and_recreation(instance):
    plugin, sent, _ = instance

    async def run():
        old = (await plugin.chat_create("Alice")).unwrap()["display_id"]
        bob = (await plugin.chat_create("Bob")).unwrap()["display_id"]
        latest = (await plugin.chat_create("Alice")).unwrap()["display_id"]
        assert len({old, bob, latest}) == 3
        await plugin.chat_push("Alice", "latest only")
        assert sent[-1]["parts"][0]["card_id"] == latest
        await plugin.chat_push("Bob", "for Bob")
        assert sent[-1]["parts"][0]["card_id"] == bob
        assert sent[-1]["target_lanlan"] == "Bob"
        assert len((await plugin.dashboard())["targets"]) == 2

    asyncio.run(run())


def test_close_and_failed_submission_keep_retryable_state(instance):
    plugin, sent, host = instance

    async def run():
        created = (await plugin.agent_create("Alice")).unwrap()["display_id"]
        accept = host.push_message
        host.push_message = lambda **_: {"submitted": False, "reason": "backpressure"}
        failed = await plugin.agent_push("Alice", "retry this")
        assert isinstance(failed, Err)
        assert "backpressure" in str(failed.error)
        assert isinstance(await plugin.agent_close("Alice"), Err)
        state = await plugin.dashboard()
        assert state["targets"][0]["agent_id"] == created
        assert state["targets"][0]["agent_updates"] == 0
        host.push_message = accept
        await plugin.agent_push("Alice", "retried")
        assert (await plugin.agent_close("Alice")).unwrap()["submitted"] is True
        assert sent[-1]["parts"][0]["operation"] == "close"
        assert sent[-1]["parts"][0]["card_id"] == created
        assert isinstance(await plugin.agent_push("Alice", "closed"), Err)
        assert (await plugin.dashboard())["targets"][0]["agent_id"] == ""
        assert (await plugin.agent_create("Alice")).unwrap()["display_id"] != created

    asyncio.run(run())


def test_no_automatic_push_and_invalid_inputs(instance):
    plugin, sent, _ = instance

    async def run():
        await plugin.startup()
        assert (await plugin.dashboard())["events"] == []
        assert isinstance(await plugin.chat_create(" "), Err)
        assert isinstance(await plugin.agent_create("Alice", title=123), Err)
        assert isinstance(await plugin.echo("chat"), Err)
        await plugin.shutdown()
        assert sent == []

    asyncio.run(run())


def test_events_are_bounded(instance):
    plugin, _, _ = instance

    async def run():
        for _ in range(35):
            await plugin.echo("chat", _ctx={"card_id": "card", "lanlan_name": "Alice"})
        events = (await plugin.dashboard())["events"]
        assert len(events) == 30
        assert events[0]["sequence"] == 35 and events[-1]["sequence"] == 6

    asyncio.run(run())


def test_locales_have_all_used_keys(instance):
    plugin, _, _ = instance
    keys = set(re.findall(r'\b(?:t|_text|tr)\("([\w.]+)"',
                          (ROOT / "ui/panel.tsx").read_text() + (ROOT / "__init__.py").read_text()))
    locales = list((ROOT / "i18n").glob("*.json"))
    assert len(locales) == 8
    for path in locales:
        bundle = json.loads(path.read_text())
        assert keys <= bundle.keys(), path.name
        assert all(isinstance(value, str) and value for value in bundle.values())
        assert "detail-test" in plugin.i18n.t("error.submit", locale=path.stem, detail="detail-test")
