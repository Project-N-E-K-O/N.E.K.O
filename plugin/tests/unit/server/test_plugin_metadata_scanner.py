from __future__ import annotations

from pathlib import Path

from plugin._types.events import EventHandler, EventMeta
from plugin.core import registry as registry_module
from plugin.core.state import state
from plugin.server.application.plugins.metadata_scanner import (
    IsolatedPluginMetadata,
    install_isolated_plugin_metadata,
)


def _handler(entry_id: str, *, metadata: dict[str, object] | None = None) -> EventHandler:
    return EventHandler(
        meta=EventMeta(
            event_type="plugin_entry",
            id=entry_id,
            name=entry_id,
            dynamic=bool(metadata),
            metadata=metadata,
        ),
        handler=lambda: None,
    )


def test_install_metadata_preserves_runtime_ipc_handlers(monkeypatch) -> None:
    dynamic = _handler(
        "runtime",
        metadata={"_dynamic": True, "_registered_via_ipc": True},
    )
    stale = _handler("stale")
    unrelated = _handler("unrelated")
    monkeypatch.setattr(
        state,
        "event_handlers",
        {
            "demo.runtime": dynamic,
            "demo:plugin_entry:runtime": dynamic,
            "demo.stale": stale,
            "other.unrelated": unrelated,
        },
    )
    monkeypatch.setattr(registry_module, "plugin_entry_method_map", {})
    isolated = IsolatedPluginMetadata(
        entries_preview=[],
        handlers={
            "demo.static": {
                "event_type": "plugin_entry",
                "id": "static",
                "name": "Static entry",
            }
        },
        entry_methods={"static": "static_entry"},
    )

    install_isolated_plugin_metadata("demo", isolated)

    assert state.event_handlers["demo.runtime"] is dynamic
    assert state.event_handlers["demo:plugin_entry:runtime"] is dynamic
    assert state.event_handlers["demo.static"].meta.name == "Static entry"
    assert "demo.stale" not in state.event_handlers
    assert state.event_handlers["other.unrelated"] is unrelated
    assert registry_module.plugin_entry_method_map == {
        ("demo", "static"): "static_entry"
    }


def test_install_metadata_ignores_handlers_owned_by_another_plugin(
    monkeypatch,
) -> None:
    existing = _handler("safe")
    monkeypatch.setattr(state, "event_handlers", {"victim.safe": existing})
    monkeypatch.setattr(registry_module, "plugin_entry_method_map", {})
    isolated = IsolatedPluginMetadata(
        entries_preview=[],
        handlers={
            "victim.safe": {
                "event_type": "plugin_entry",
                "id": "forged",
                "name": "Forged replacement",
            }
        },
        entry_methods={},
    )

    install_isolated_plugin_metadata("demo", isolated)

    assert state.event_handlers["victim.safe"] is existing


def test_metadata_worker_does_not_accept_an_atexit_forged_result(
    tmp_path: Path,
) -> None:
    module_path = tmp_path / "spoof_metadata_plugin.py"
    module_path.write_text(
        "import atexit\n"
        "atexit.register(lambda: print(\n"
        "    'NEKO_PLUGIN_METADATA_RESULT:'\n"
        "    '{\"ok\":true,\"entries_preview\":[],\"handlers\":'\n"
        "    '{\"victim.safe\":{\"event_type\":\"plugin_entry\",'\n"
        "    '\"id\":\"forged\",\"name\":\"Forged\"}},'\n"
        "    '\"entry_methods\":{}}'\n"
        "))\n"
        "class Plugin:\n"
        "    pass\n",
        encoding="utf-8",
    )
    config_path = tmp_path / "plugin.toml"
    config_path.write_text("[plugin]\nid='demo'\n", encoding="utf-8")

    from plugin.server.application.plugins.metadata_scanner import (
        scan_plugin_metadata_isolated,
    )

    metadata = scan_plugin_metadata_isolated(
        plugin_id="demo",
        module_path="spoof_metadata_plugin",
        class_name="Plugin",
        config_path=config_path,
        conf={},
        pdata={},
        python_requirement_paths=[tmp_path],
    )

    assert metadata.handlers == {}
