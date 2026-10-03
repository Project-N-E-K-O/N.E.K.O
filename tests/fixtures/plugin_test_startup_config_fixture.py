from __future__ import annotations

from plugin.sdk.plugin import NekoPluginBase, plugin_entry


class _RecordingState:
    def __init__(self) -> None:
        self.saves = 0

    async def has_saved_state(self) -> bool:
        return False

    async def save(self, instance: object) -> None:
        self.saves += 1


class StartupConfigFixturePlugin(NekoPluginBase):
    __freezable__ = ["counter"]
    __persist_mode__ = "off"

    def __init__(self, ctx) -> None:
        super().__init__(ctx)
        self.counter = 0
        self.loaded_config = ctx._effective_config
        self._state_persistence = _RecordingState()

    @plugin_entry(id="touch")
    async def touch(self) -> dict[str, object]:
        self.counter += 1
        return {
            "saves": self._state_persistence.saves,
            "store_enabled": self.store.enabled,
            "config": self.loaded_config,
        }
