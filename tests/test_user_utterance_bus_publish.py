"""Tests for the user-utterance → plugin bus publishing helper.

Covers ``LLMSessionManager._publish_user_utterance_to_plugin_bus`` — the
bridge that calls ``state.add_user_context_event`` so plugins can read
user history via ``ctx.bus.memory.get(bucket_id=...)``.

We don't construct a real ``LLMSessionManager`` (heavy: needs config,
websocket, sessions). Instead, we call the unbound method with a tiny
SimpleNamespace that carries only the attributes the helper touches
(``lanlan_name``).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from main_logic.core import LLMSessionManager
from plugin.core.state import state as plugin_state


def _make_fake_core(lanlan_name: str = "皖萱") -> SimpleNamespace:
    """Minimal stand-in for LLMSessionManager — only the attributes the helper reads."""
    return SimpleNamespace(lanlan_name=lanlan_name)


def _drain_bucket(bucket_id: str) -> list[dict]:
    """Snapshot a bucket's events without affecting state."""
    return plugin_state.get_user_context(bucket_id=bucket_id, limit=500)


@pytest.fixture(autouse=True)
def _isolate_user_context():
    """Each test starts with empty user-context stores. Restore after."""
    snapshot_default = _drain_bucket("default")
    snapshot_lanlan = _drain_bucket("皖萱")
    plugin_state._user_context_store.clear()  # noqa: SLF001
    yield
    plugin_state._user_context_store.clear()  # noqa: SLF001
    # The state is module-global; we don't restore the prior snapshot to
    # keep tests independent (no test should depend on bucket contents
    # from prior tests).
    _ = (snapshot_default, snapshot_lanlan)  # silence lint


@pytest.mark.unit
def test_publish_writes_to_default_and_lanlan_buckets():
    fake = _make_fake_core(lanlan_name="皖萱")
    LLMSessionManager._publish_user_utterance_to_plugin_bus(
        fake, "你好啊", is_voice_source=True,
    )

    default_events = _drain_bucket("default")
    lanlan_events = _drain_bucket("皖萱")

    assert len(default_events) == 1
    assert len(lanlan_events) == 1
    for ev in (default_events[0], lanlan_events[0]):
        assert ev["type"] == "user_message"
        assert ev["content"] == "你好啊"
        assert ev["lanlan"] == "皖萱"
        assert ev["is_voice"] is True
        assert ev["source"] == "main_logic.core"
        assert isinstance(ev["_ts"], float)


@pytest.mark.unit
def test_publish_strips_whitespace_and_skips_empty():
    fake = _make_fake_core()
    LLMSessionManager._publish_user_utterance_to_plugin_bus(
        fake, "  hi  ", is_voice_source=False,
    )
    LLMSessionManager._publish_user_utterance_to_plugin_bus(
        fake, "   ", is_voice_source=False,
    )
    LLMSessionManager._publish_user_utterance_to_plugin_bus(
        fake, "", is_voice_source=False,
    )
    LLMSessionManager._publish_user_utterance_to_plugin_bus(
        fake, None, is_voice_source=False,
    )

    events = _drain_bucket("default")
    assert len(events) == 1
    assert events[0]["content"] == "hi"
    assert events[0]["is_voice"] is False


@pytest.mark.unit
def test_publish_handles_missing_lanlan_name_gracefully():
    """Empty/None lanlan_name should still write to default bucket."""
    fake = _make_fake_core(lanlan_name="")
    LLMSessionManager._publish_user_utterance_to_plugin_bus(
        fake, "ping", is_voice_source=True,
    )
    assert len(_drain_bucket("default")) == 1
    # No second bucket should be created when lanlan_name is empty.
    assert plugin_state._user_context_store.get("") is None  # noqa: SLF001


@pytest.mark.unit
def test_publish_non_string_input_is_ignored():
    fake = _make_fake_core()
    LLMSessionManager._publish_user_utterance_to_plugin_bus(
        fake, 12345, is_voice_source=True,  # type: ignore[arg-type]
    )
    LLMSessionManager._publish_user_utterance_to_plugin_bus(
        fake, ["not", "a", "string"], is_voice_source=True,  # type: ignore[arg-type]
    )
    assert _drain_bucket("default") == []


@pytest.mark.unit
def test_repeated_publishes_accumulate_in_chronological_order():
    fake = _make_fake_core(lanlan_name="兰兰")
    for i, msg in enumerate(["first", "second", "third"]):
        LLMSessionManager._publish_user_utterance_to_plugin_bus(
            fake, msg, is_voice_source=(i % 2 == 0),
        )

    contents = [ev["content"] for ev in _drain_bucket("兰兰")]
    assert contents == ["first", "second", "third"]
