from __future__ import annotations

import asyncio
import threading
from collections.abc import Mapping

from plugin.sdk.plugin import NekoPluginBase, plugin_entry, ui


class MissingUiContextFixturePlugin(NekoPluginBase):
    """Declares UI actions but no @ui.context provider at all."""

    @ui.action(id="ping", label="Ping")
    @plugin_entry(id="ping", name="Ping")
    async def ping(self, **_: object) -> dict[str, object]:
        return {"ok": True}


class FailingUiContextFixturePlugin(NekoPluginBase):
    """Has a provider, but it raises every time."""

    @ui.action(id="ping", label="Ping")
    @plugin_entry(id="ping", name="Ping")
    async def ping(self, **_: object) -> dict[str, object]:
        return {"ok": True}

    @ui.context(id="main")
    async def main_context(self, **_: object) -> dict[str, object]:
        raise RuntimeError("provider exploded")


class HealthyUiContextFixturePlugin(NekoPluginBase):
    @ui.action(id="ping", label="Ping")
    @plugin_entry(id="ping", name="Ping")
    async def ping(self, **_: object) -> dict[str, object]:
        return {"ok": True}

    @ui.context(id="main")
    async def main_context(self, **_: object) -> dict[str, object]:
        return {"greeting": "hi"}


class HangingUiContextFixturePlugin(NekoPluginBase):
    """Has a provider that never returns."""

    @ui.action(id="ping", label="Ping")
    @plugin_entry(id="ping", name="Ping")
    async def ping(self, **_: object) -> dict[str, object]:
        return {"ok": True}

    @ui.context(id="main")
    async def main_context(self, **_: object) -> dict[str, object]:
        await asyncio.sleep(3600)
        return {}


class UncancellableUiContextFixturePlugin(NekoPluginBase):
    """Has a provider that swallows cancellation and keeps holding the loop."""

    @ui.action(id="ping", label="Ping")
    @plugin_entry(id="ping", name="Ping")
    async def ping(self, **_: object) -> dict[str, object]:
        return {"ok": True}

    @ui.context(id="main")
    async def main_context(self, **_: object) -> dict[str, object]:
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            await asyncio.sleep(3600)
        return {}


class CancellingUiContextFixturePlugin(NekoPluginBase):
    """Provider whose own body lets a CancelledError escape."""

    @ui.action(id="ping", label="Ping")
    @plugin_entry(id="ping", name="Ping")
    async def ping(self, **_: object) -> dict[str, object]:
        return {"ok": True}

    @ui.context(id="main")
    async def main_context(self, **_: object) -> dict[str, object]:
        inner = asyncio.get_running_loop().create_future()
        inner.cancel()
        await inner
        return {}


class UnserializableUiContextFixturePlugin(NekoPluginBase):
    """Provider state AND an action label that cannot cross the process boundary."""

    @ui.action(id="ping", label=threading.Lock())
    @plugin_entry(id="ping", name="Ping")
    async def ping(self, **_: object) -> dict[str, object]:
        return {"ok": True}

    @ui.context(id="main")
    async def main_context(self, **_: object) -> dict[str, object]:
        return {"lock": threading.Lock(), "tags": {"a", "b"}}


class _BareDumpState:
    """A hand-rolled model_dump that does not accept a mode keyword."""

    def model_dump(self) -> dict[str, object]:
        return {"greeting": "hi"}


class BareModelDumpUiContextFixturePlugin(NekoPluginBase):
    @ui.action(id="ping", label="Ping")
    @plugin_entry(id="ping", name="Ping")
    async def ping(self, **_: object) -> dict[str, object]:
        return {"ok": True}

    @ui.context(id="main")
    async def main_context(self, **_: object) -> object:
        return _BareDumpState()


class _ConfirmMapping(Mapping):
    """The SDK declares confirm as Mapping; a plugin may pass a non-dict one."""

    def __init__(self, data: dict) -> None:
        self._data = data

    def __getitem__(self, key):
        return self._data[key]

    def __iter__(self):
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)


class MappingConfirmUiContextFixturePlugin(NekoPluginBase):
    @ui.action(id="ping", label="Ping", confirm=_ConfirmMapping({"title": "sure?"}))
    @plugin_entry(id="ping", name="Ping")
    async def ping(self, **_: object) -> dict[str, object]:
        return {"ok": True}

    @ui.context(id="main")
    async def main_context(self, **_: object) -> dict[str, object]:
        return {}
