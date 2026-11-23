"""Plugin used by the real-process ctx.permissions tests."""

from __future__ import annotations

from plugin.sdk.plugin import NekoPluginBase, lifecycle, plugin_entry

MEMORY = "plugin_sdk.main_agent.memory:read"


def _state(ctx, permission: str) -> dict:
    state = ctx.permissions.get(permission)
    return {"status": state.status, "background": state.background, "enforced": state.enforced}


class PermissionsFixturePlugin(NekoPluginBase):
    def __init__(self, ctx) -> None:
        super().__init__(ctx)
        self.at_startup: dict | None = None
        self.changes: list[list] = []

    @lifecycle(id="startup")
    async def on_startup(self) -> None:
        self.at_startup = _state(self.ctx, MEMORY)
        self.ctx.permissions.on_change(
            lambda permission, state: self.changes.append([permission, state.status, state.enforced])
        )

    @plugin_entry(id="perm_state")
    async def perm_state(self, permission: str = MEMORY, **_: object) -> dict:
        return {"at_startup": self.at_startup, "now": _state(self.ctx, permission), "changes": self.changes}
