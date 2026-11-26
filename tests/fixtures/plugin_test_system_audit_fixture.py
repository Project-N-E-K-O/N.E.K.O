"""Plugin used by the real-process system.* audit tests."""

from __future__ import annotations

import socket

from plugin.sdk.plugin import NekoPluginBase, plugin_entry


class SystemAuditFixturePlugin(NekoPluginBase):
    @plugin_entry(id="touch")
    async def touch(self, path: str = "", port: int = 0, **_: object) -> dict:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("x")
        connection = socket.create_connection(("127.0.0.1", port), timeout=5)
        connection.close()
        # In the plugin's own data directory: never recorded.
        self.data_path("own.txt").parent.mkdir(parents=True, exist_ok=True)
        self.data_path("own.txt").write_text("x", encoding="utf-8")
        return {"ok": True}
