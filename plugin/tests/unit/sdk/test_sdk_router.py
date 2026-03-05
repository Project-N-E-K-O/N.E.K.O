from __future__ import annotations

from types import SimpleNamespace

import pytest

from plugin.sdk.decorators import plugin_entry
from plugin.sdk.router import PluginRouter, PluginRouterError


class DemoRouter(PluginRouter):
    @plugin_entry(id="hello")
    def hello(self, **kwargs):
        return {"ok": True}


class NeedsDependencyRouter(PluginRouter):
    __requires__ = ["cache"]


def _dummy_plugin() -> object:
    return SimpleNamespace(
        ctx=SimpleNamespace(logger=None),
        file_logger=None,
        _plugin_id="demo",
    )


@pytest.mark.plugin_unit
def test_collect_entries_adds_prefix_and_metadata() -> None:
    router = DemoRouter(prefix="x_")
    entries = router.collect_entries()

    assert "x_hello" in entries
    meta = entries["x_hello"].meta
    assert meta.metadata is not None
    assert meta.metadata["_router"] == "DemoRouter"
    assert meta.metadata["_original_id"] == "hello"
    assert "x_hello" in router.entry_ids


@pytest.mark.plugin_unit
def test_prefix_cannot_change_after_bind() -> None:
    router = DemoRouter(prefix="a_")
    router._bind(_dummy_plugin())

    with pytest.raises(PluginRouterError):
        router.prefix = "b_"


@pytest.mark.plugin_unit
def test_router_cannot_bind_twice() -> None:
    router = DemoRouter()
    router._bind(_dummy_plugin())

    with pytest.raises(PluginRouterError):
        router._bind(_dummy_plugin())


@pytest.mark.plugin_unit
def test_router_dependency_missing_raises() -> None:
    router = NeedsDependencyRouter()

    with pytest.raises(PluginRouterError) as exc_info:
        router._bind(_dummy_plugin())

    assert "requires dependency" in str(exc_info.value)
