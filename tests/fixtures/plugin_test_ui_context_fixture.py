from __future__ import annotations

import asyncio
import threading
from collections.abc import Mapping

from plugin.sdk.plugin import NekoPluginBase, custom_event, plugin_entry, ui
from plugin.sdk.shared.core.context import SdkContext


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


class ScopedCardTargetFixturePlugin(NekoPluginBase):
    """Real host dispatch and SDK handles, with only the message sink replaced."""

    def __init__(self, ctx):
        super().__init__(ctx)
        self._sdk_context = SdkContext(ctx)
        self.first_waiting = asyncio.Event()
        self.peer_finished = asyncio.Event()
        self.sent = []
        ctx.push_message = self._record_push

    def _record_push(self, **payload):
        self.sent.append(payload)
        return {"submitted": True}

    async def _emit(self, label):
        for context in (self._sdk_context, self._host_ctx):
            await context.create_card(html=label, summary=label)
            await context.create_view(title=label, html=label)
            await context.get_card(label).update(html=label)
            await context.get_view(label).update(html=label)
            await context.create_card(html=label, summary=label, target_lanlan="Explicit")
        return [payload["target_lanlan"] for payload in self.sent if payload["parts"][0].get("html") == label]

    @plugin_entry(id="emit")
    async def emit(self, label: str, wait: bool = False, peer: bool = False, _ctx=None):
        if wait:
            self.first_waiting.set()
            await self.peer_finished.wait()
        if peer:
            await self.first_waiting.wait()
        result = await self._emit(label)
        if peer:
            self.peer_finished.set()
        return result

    @custom_event(event_type="test", id="emit_custom")
    async def emit_custom(self, label: str, _ctx=None):
        return await self._emit(label)
