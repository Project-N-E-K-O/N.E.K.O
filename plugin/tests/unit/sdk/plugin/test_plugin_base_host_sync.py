from __future__ import annotations

import tempfile
from pathlib import Path

from plugin.sdk.plugin.base import NekoPluginBase


class _Queue:
    def __init__(self) -> None:
        self.items: list[dict[str, object]] = []

    def put_nowait(self, obj):
        self.items.append(obj)


class _Ctx:
    plugin_id = "demo"
    logger = None
    metadata = {}
    bus = None

    def __init__(self, config_path: Path) -> None:
        self.config_path = config_path
        self.message_queue = _Queue()
        self._effective_config = {
            "plugin": {"store": {"enabled": True}, "database": {"enabled": False}},
            "plugin_state": {"backend": "memory"},
        }


class _Plugin(NekoPluginBase):
    pass


def test_register_static_ui_notifies_host() -> None:
    root = Path(tempfile.mkdtemp())
    static_dir = root / "static"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<html></html>", encoding="utf-8")
    ctx = _Ctx(root / "plugin.toml")
    plugin = _Plugin(ctx)

    assert plugin.register_static_ui("static") is True
    assert ctx.message_queue.items[-1]["type"] == "STATIC_UI_REGISTER"


def test_dynamic_entry_updates_notify_host() -> None:
    ctx = _Ctx(Path(tempfile.mkdtemp()) / "plugin.toml")
    plugin = _Plugin(ctx)

    plugin.register_dynamic_entry("dyn", lambda **_: {"ok": True}, name="Dyn")
    plugin.disable_entry("dyn")
    plugin.enable_entry("dyn")
    plugin.unregister_dynamic_entry("dyn")

    seen = {(item.get("type"), item.get("action"), item.get("entry_id")) for item in ctx.message_queue.items}
    assert ("ENTRY_UPDATE", "register", "dyn") in seen
    assert ("ENTRY_UPDATE", "unregister", "dyn") in seen
