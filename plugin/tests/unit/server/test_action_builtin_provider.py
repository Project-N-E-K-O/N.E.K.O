"""Unit tests for plugin.server.application.actions.builtin_provider."""
from __future__ import annotations

import threading
from typing import Any
from unittest.mock import patch

import pytest

from plugin.server.application.actions import builtin_provider as module
from plugin.server.domain.action_models import ActionDescriptor


class _FakeState:
    def __init__(
        self,
        plugins: dict[str, Any] | None = None,
        hosts: dict[str, Any] | None = None,
    ) -> None:
        self.plugins = plugins or {}
        self.plugin_hosts = hosts or {}
        self._lock = threading.RLock()

    def get_plugins_snapshot_cached(self, **_kw: Any) -> dict[str, Any]:
        return dict(self.plugins)

    def acquire_plugin_hosts_read_lock(self) -> Any:
        return self._lock


def _collect(fake_state: _FakeState, plugin_id: str | None = None) -> list[ActionDescriptor]:
    with patch("plugin.core.state.state", fake_state):
        return module._collect_builtin_actions_sync(plugin_id)


@pytest.mark.plugin_unit
class TestBuiltinActions:
    def test_running_plugin_emits_stop_and_reload(self) -> None:
        state = _FakeState(
            plugins={"demo": {"name": "Demo"}},
            hosts={"demo": object()},
        )
        actions = _collect(state)
        action_ids = {a.action_id for a in actions}
        assert action_ids == {"system:demo:stop", "system:demo:reload"}

    def test_stopped_plugin_emits_start_only(self) -> None:
        state = _FakeState(
            plugins={"demo": {"name": "Demo"}},
            hosts={},
        )
        actions = _collect(state)
        action_ids = {a.action_id for a in actions}
        assert action_ids == {"system:demo:start"}

    def test_plugin_name_i18n_ref_is_resolved(self, tmp_path) -> None:
        """``$i18n`` refs in plugin meta ``name`` must be resolved before they
        flow into labels/keywords — otherwise the command palette shows raw
        ``{'$i18n': '...'}`` reprs in builtin lifecycle commands."""
        plugin_dir = tmp_path / "i18n_demo"
        plugin_dir.mkdir()
        translations = plugin_dir / "i18n"
        translations.mkdir()
        (translations / "en.json").write_text(
            '{"plugin.name": "Resolved Demo"}', encoding="utf-8"
        )
        config_path = plugin_dir / "config.toml"
        config_path.write_text("", encoding="utf-8")

        state = _FakeState(
            plugins={"demo": {
                "name": {"$i18n": "plugin.name", "default": "Resolved Demo"},
                "config_path": str(config_path),
            }},
            hosts={"demo": object()},
        )
        actions = _collect(state)
        assert actions
        for a in actions:
            assert "Resolved Demo" in a.label
            assert "$i18n" not in a.label
            assert all("$i18n" not in str(k) for k in a.keywords)

    def test_plugin_id_filter(self) -> None:
        state = _FakeState(
            plugins={"a": {"name": "A"}, "b": {"name": "B"}},
            hosts={"a": object(), "b": object()},
        )
        actions = _collect(state, plugin_id="b")
        # Non-empty guard: an `all(...)` over `[]` would trivially pass and
        # mask a regression that drops the filter result entirely.
        assert actions
        assert all(a.plugin_id == "b" for a in actions)

    def test_non_mapping_meta_skipped(self) -> None:
        state = _FakeState(
            plugins={"bad": "not-a-dict"},
            hosts={"bad": object()},
        )
        assert _collect(state) == []
