from __future__ import annotations

import pytest

from plugin.sdk.adapter.decorators import ADAPTER_EVENT_META, on_mcp_resource
from plugin.sdk.hooks import HookMeta
from plugin.sdk.router import PluginRouterError


@pytest.mark.plugin_unit
def test_hook_meta_properties() -> None:
    m1 = HookMeta(target="*", timing="before")
    assert m1.is_cross_plugin is False
    assert m1.target_plugin is None
    assert m1.target_entry == "*"

    m2 = HookMeta(target="plugin_a.entry_x", timing="after")
    assert m2.is_cross_plugin is True
    assert m2.target_plugin == "plugin_a"
    assert m2.target_entry == "entry_x"


@pytest.mark.plugin_unit
def test_plugin_router_error_helpers() -> None:
    assert "not bound" in str(PluginRouterError.not_bound("R")).lower()
    assert "already bound" in str(PluginRouterError.already_bound("R", "P")).lower()
    assert "requires dependency" in str(PluginRouterError.dependency_missing("R", "cache")).lower()
    assert "cannot change prefix" in str(PluginRouterError.prefix_change_after_bound("R")).lower()


@pytest.mark.plugin_unit
def test_on_mcp_resource_decorator_sets_meta() -> None:
    @on_mcp_resource("res_*", priority=3)
    async def handler():
        return None

    meta = getattr(handler, ADAPTER_EVENT_META)
    assert meta.protocol == "mcp"
    assert meta.action == "resource_read"
    assert meta.pattern == "res_*"
    assert meta.priority == 3
