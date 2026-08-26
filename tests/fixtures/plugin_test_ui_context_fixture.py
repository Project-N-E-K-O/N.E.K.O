from __future__ import annotations

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
