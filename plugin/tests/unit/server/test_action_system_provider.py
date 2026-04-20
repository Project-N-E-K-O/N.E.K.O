"""Unit tests for plugin.server.application.actions.system_provider."""
from __future__ import annotations

import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from plugin.server.application.actions import system_provider as module
from plugin.server.domain.action_models import ActionDescriptor


# ── Helpers ──────────────────────────────────────────────────────────

def _make_plugin_meta(
    pid: str = "demo",
    name: str = "Demo Plugin",
    **extra: Any,
) -> dict[str, Any]:
    return {"id": pid, "name": name, **extra}


def _make_handler(
    entry_id: str,
    name: str = "",
    kind: str = "action",
    enabled: bool = True,
) -> Any:
    """Create a fake event handler with meta attributes."""
    from types import SimpleNamespace

    meta = SimpleNamespace(
        id=entry_id,
        name=name or entry_id,
        kind=kind,
        metadata={"enabled": enabled},
    )
    return SimpleNamespace(meta=meta)


class _FakeState:
    """Minimal stand-in for plugin.core.state.state."""

    def __init__(
        self,
        plugins: dict[str, Any] | None = None,
        hosts: dict[str, Any] | None = None,
        handlers: dict[str, Any] | None = None,
    ) -> None:
        self.plugins = plugins or {}
        self.plugin_hosts = hosts or {}
        self.event_handlers = handlers or {}
        self._lock = threading.RLock()

    def get_plugins_snapshot_cached(self, **_kw: Any) -> dict[str, Any]:
        return dict(self.plugins)

    def acquire_plugin_hosts_read_lock(self) -> Any:
        return self._lock

    def get_event_handlers_snapshot_cached(self, **_kw: Any) -> dict[str, Any]:
        return dict(self.event_handlers)


# ── Tests ────────────────────────────────────────────────────────────

@pytest.mark.plugin_unit
class TestCollectSystemActions:
    """Tests for _collect_system_actions_sync."""

    def _collect(self, fake_state: _FakeState, plugin_id: str | None = None) -> list[ActionDescriptor]:
        with patch("plugin.core.state.state", fake_state):
            return module._collect_system_actions_sync(plugin_id)

    def test_empty_plugins(self) -> None:
        actions = self._collect(_FakeState())
        assert actions == []

    def test_stopped_plugin_produces_lifecycle_action(self) -> None:
        state = _FakeState(plugins={"demo": _make_plugin_meta()})
        actions = self._collect(state)

        lifecycle = [a for a in actions if a.control == "plugin_lifecycle"]
        assert len(lifecycle) == 1
        assert lifecycle[0].action_id == "system:demo:toggle"
        assert lifecycle[0].current_value is False  # not running

    def test_running_plugin_lifecycle_shows_running(self) -> None:
        state = _FakeState(
            plugins={"demo": _make_plugin_meta()},
            hosts={"demo": object()},
        )
        actions = self._collect(state)

        lifecycle = [a for a in actions if a.control == "plugin_lifecycle"]
        assert len(lifecycle) == 1
        assert lifecycle[0].current_value is True

    def test_no_start_stop_reload_buttons_emitted(self) -> None:
        """The old separate start/stop/reload buttons should no longer exist."""
        state = _FakeState(
            plugins={"demo": _make_plugin_meta()},
            hosts={"demo": object()},
        )
        actions = self._collect(state)
        ids = {a.action_id for a in actions}
        assert "system:demo:start" not in ids
        assert "system:demo:stop" not in ids
        assert "system:demo:reload" not in ids

    def test_plugin_id_filter(self) -> None:
        state = _FakeState(plugins={
            "a": _make_plugin_meta("a", "A"),
            "b": _make_plugin_meta("b", "B"),
        })
        actions = self._collect(state, plugin_id="a")
        assert all(a.plugin_id == "a" for a in actions)

    def test_service_entry_gets_entry_toggle(self) -> None:
        state = _FakeState(
            plugins={"demo": _make_plugin_meta()},
            hosts={"demo": object()},
            handlers={"demo.my_service": _make_handler("my_service", kind="service")},
        )
        actions = self._collect(state)
        entry_actions = [a for a in actions if "entry:" in a.action_id]
        assert len(entry_actions) == 1
        assert entry_actions[0].control == "entry_toggle"
        assert entry_actions[0].current_value is True

    def test_action_entry_gets_button(self) -> None:
        state = _FakeState(
            plugins={"demo": _make_plugin_meta()},
            hosts={"demo": object()},
            handlers={"demo.do_thing": _make_handler("do_thing", kind="action")},
        )
        actions = self._collect(state)
        entry_actions = [a for a in actions if "entry:" in a.action_id]
        assert len(entry_actions) == 1
        assert entry_actions[0].control == "button"

    def test_entries_only_for_running_plugins(self) -> None:
        state = _FakeState(
            plugins={"demo": _make_plugin_meta()},
            hosts={},  # not running
            handlers={"demo.do_thing": _make_handler("do_thing")},
        )
        actions = self._collect(state)
        entry_actions = [a for a in actions if "entry:" in a.action_id]
        assert len(entry_actions) == 0

    def test_non_mapping_meta_skipped(self) -> None:
        state = _FakeState(plugins={"bad": "not-a-dict"})
        actions = self._collect(state)
        assert actions == []


@pytest.mark.plugin_unit
class TestGetEntriesForPlugin:
    def test_dot_key_format(self) -> None:
        handlers = {"demo.entry_a": _make_handler("entry_a", kind="action")}
        entries = module._get_entries_for_plugin("demo", handlers)
        assert len(entries) == 1
        assert entries[0]["id"] == "entry_a"
        assert entries[0]["kind"] == "action"

    def test_colon_key_format(self) -> None:
        handlers = {"demo:plugin_entry:entry_b": _make_handler("entry_b", kind="service")}
        entries = module._get_entries_for_plugin("demo", handlers)
        assert len(entries) == 1
        assert entries[0]["kind"] == "service"

    def test_deduplication(self) -> None:
        h = _make_handler("dup", kind="action")
        handlers = {"demo.dup": h, "demo:plugin_entry:dup": h}
        entries = module._get_entries_for_plugin("demo", handlers)
        assert len(entries) == 1

    def test_ignores_other_plugins(self) -> None:
        handlers = {"other.entry_x": _make_handler("entry_x")}
        entries = module._get_entries_for_plugin("demo", handlers)
        assert len(entries) == 0

    def test_disabled_entry(self) -> None:
        handlers = {"demo.off": _make_handler("off", enabled=False)}
        entries = module._get_entries_for_plugin("demo", handlers)
        assert entries[0]["enabled"] is False


@pytest.mark.plugin_unit
class TestHasStaticUi:
    def test_no_config(self) -> None:
        assert module._has_static_ui({}) is False

    def test_disabled(self) -> None:
        assert module._has_static_ui({"static_ui_config": {"enabled": False}}) is False

    def test_enabled_but_no_directory(self) -> None:
        assert module._has_static_ui({"static_ui_config": {"enabled": True}}) is False

    def test_enabled_with_missing_dir(self, tmp_path: Path) -> None:
        meta = {"static_ui_config": {"enabled": True, "directory": str(tmp_path / "nope")}}
        assert module._has_static_ui(meta) is False

    def test_enabled_with_valid_dir(self, tmp_path: Path) -> None:
        (tmp_path / "index.html").write_text("<html></html>")
        meta = {"static_ui_config": {"enabled": True, "directory": str(tmp_path)}}
        assert module._has_static_ui(meta) is True
