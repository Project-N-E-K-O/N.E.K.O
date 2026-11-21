from __future__ import annotations

import pytest

from plugin.core.host import _resolve_persist_mode
from plugin.sdk.shared.core.base import NekoPluginBase
from plugin.sdk.shared.storage.state import PluginStatePersistence


class _BasePlugin(NekoPluginBase):
    pass


class _AutoBasePlugin(NekoPluginBase):
    __persist_mode__ = "auto"


class _FreezeModeBasePlugin(NekoPluginBase):
    __freeze_mode__ = "manual"


def _Plugin() -> NekoPluginBase:
    # Real plugins subclass NekoPluginBase; skip __init__, which needs a host context.
    return object.__new__(_BasePlugin)


def _AutoPlugin() -> NekoPluginBase:
    return object.__new__(_AutoBasePlugin)


class _NonBaseFreezePlugin:
    # Objects without __persist_mode__ fall back to __freeze_mode__.
    __freeze_mode__ = "manual"


@pytest.mark.parametrize(
    ("instance", "effective_cfg", "expected"),
    [
        # An explicit [plugin_state] "off" is final; the legacy table must not turn it back on.
        (_Plugin(), {"plugin_state": {"persist_mode": "off"}, "plugin_checkpoint": {"freeze_mode": "auto"}}, "off"),
        # Only the legacy table: still honoured.
        (_Plugin(), {"plugin_checkpoint": {"freeze_mode": "auto"}}, "auto"),
        # [plugin_state] present without persist_mode: falls back to the legacy table.
        (_Plugin(), {"plugin_state": {"backend": "file"}, "plugin_checkpoint": {"freeze_mode": "manual"}}, "manual"),
        # Invalid [plugin_state].persist_mode: falls back to the legacy table.
        (_Plugin(), {"plugin_state": {"persist_mode": "always"}, "plugin_checkpoint": {"freeze_mode": "auto"}}, "auto"),
        # Neither table: the default.
        (_Plugin(), {}, "off"),
        # New table auto.
        (_Plugin(), {"plugin_state": {"persist_mode": "auto"}}, "auto"),
        (_Plugin(), {"plugin_state": {"persist_mode": "auto"}, "plugin_checkpoint": {"freeze_mode": "off"}}, "auto"),
        # Invalid legacy value is ignored.
        (_Plugin(), {"plugin_checkpoint": {"freeze_mode": "always"}}, "off"),
        # Instance defaults still apply when config is silent, and config overrides them.
        (_AutoPlugin(), {}, "auto"),
        (_AutoPlugin(), {"plugin_checkpoint": {"freeze_mode": "off"}}, "auto"),
        (_AutoPlugin(), {"plugin_state": {"persist_mode": "off"}}, "off"),
        (_NonBaseFreezePlugin(), {}, "manual"),
        # NekoPluginBase defines __persist_mode__ = "off", so __freeze_mode__ is not read there.
        (object.__new__(_FreezeModeBasePlugin), {}, "off"),
        # Non-table sections are ignored.
        (_Plugin(), {"plugin_state": "off", "plugin_checkpoint": {"freeze_mode": "auto"}}, "auto"),
        (_Plugin(), {"plugin_checkpoint": "auto"}, "off"),
    ],
)
def test_resolve_persist_mode(instance: object, effective_cfg: dict, expected: str) -> None:
    assert _resolve_persist_mode(instance, effective_cfg) == expected


@pytest.mark.parametrize("mode", PluginStatePersistence.VALID_PERSIST_MODES)
def test_host_applies_the_persist_modes_check_accepts(mode: str) -> None:
    # check validates against the same constant; every value it accepts must take effect.
    assert PluginStatePersistence.VALID_PERSIST_MODES == ("auto", "manual", "off")
    assert _resolve_persist_mode(_Plugin(), {"plugin_state": {"persist_mode": mode}}) == mode
    assert _resolve_persist_mode(_Plugin(), {"plugin_checkpoint": {"freeze_mode": mode}}) == mode
