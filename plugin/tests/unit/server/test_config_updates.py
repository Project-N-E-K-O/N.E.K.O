from __future__ import annotations

import threading
from pathlib import Path

import pytest
from fastapi import HTTPException

from plugin.server.infrastructure import config_updates as module


@pytest.mark.plugin_unit
def test_fill_plugin_protected_fields_backfills_id_and_entry() -> None:
    current = {"plugin": {"id": "demo", "entry": "plugin.main:Main"}}
    incoming = {"runtime": {"enabled": True}}

    filled = module._fill_plugin_protected_fields(current_config=current, incoming_config=incoming)

    assert filled["plugin"]["id"] == "demo"
    assert filled["plugin"]["entry"] == "plugin.main:Main"


@pytest.mark.plugin_unit
def test_update_plugin_config_validates_protected_fields(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config_path = tmp_path / "plugin.toml"
    config_path.write_text("[plugin]\nid='demo'\nentry='plugin.main:Main'\n", encoding="utf-8")

    current_config = {"plugin": {"id": "demo", "entry": "plugin.main:Main"}, "runtime": {"enabled": True}}
    merged_config = {"plugin": {"id": "demo", "entry": "plugin.main:Main"}, "runtime": {"enabled": False}}

    captured: dict[str, object] = {}

    monkeypatch.setattr(module, "get_plugin_update_lock", lambda plugin_id: threading.Lock())
    monkeypatch.setattr(module, "get_plugin_config_path", lambda plugin_id: config_path)
    monkeypatch.setattr(module, "load_toml_from_stream", lambda stream, context: current_config)
    monkeypatch.setattr(module, "deep_merge", lambda base, updates: merged_config)

    def _capture_validate(*, current_config: dict[str, object], new_config: dict[str, object]) -> None:
        captured["current"] = current_config
        captured["new"] = new_config

    monkeypatch.setattr(module, "validate_protected_fields_unchanged", _capture_validate)
    monkeypatch.setattr(module, "dump_toml_bytes", lambda payload: b"ok")
    monkeypatch.setattr(module, "atomic_write_bytes", lambda **kwargs: None)
    monkeypatch.setattr(module, "load_plugin_config", lambda plugin_id: {"config": merged_config})

    result = module.update_plugin_config("demo", {"runtime": {"enabled": False}})

    assert result["success"] is True
    assert captured["current"] == current_config
    assert captured["new"] == merged_config


@pytest.mark.plugin_unit
def test_update_plugin_config_toml_rejects_none() -> None:
    with pytest.raises(HTTPException) as exc_info:
        module.update_plugin_config_toml("demo", None)  # type: ignore[arg-type]

    assert exc_info.value.status_code == 400
