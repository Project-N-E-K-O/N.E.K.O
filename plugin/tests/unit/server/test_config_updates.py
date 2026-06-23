from __future__ import annotations

from contextlib import contextmanager
import threading
from pathlib import Path
from typing import BinaryIO, Iterator

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
def test_update_plugin_config_atomic_write_after_file_lock_released(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "plugin.toml"
    config_path.write_text("[plugin]\nid='demo'\nentry='plugin.main:Main'\n", encoding="utf-8")

    current_config = {"plugin": {"id": "demo", "entry": "plugin.main:Main"}, "runtime": {"enabled": True}}
    merged_config = {"plugin": {"id": "demo", "entry": "plugin.main:Main"}, "runtime": {"enabled": False}}
    write_lock_path = config_path.with_name(f"{config_path.name}.lock")
    state: dict[str, object] = {
        "target_locked": False,
        "write_locked": False,
        "target_stream": None,
    }

    @contextmanager
    def _file_lock(stream: BinaryIO) -> Iterator[None]:
        stream_path = Path(stream.name)
        if stream_path == write_lock_path:
            state["write_locked"] = True
            try:
                yield
            finally:
                state["write_locked"] = False
            return
        assert stream_path == config_path
        state["target_locked"] = True
        state["target_stream"] = stream
        try:
            yield
        finally:
            state["target_locked"] = False

    def _atomic_write_bytes(**kwargs: object) -> None:
        stream = state["target_stream"]
        assert state["target_locked"] is False
        assert state["write_locked"] is True
        assert stream is not None
        assert getattr(stream, "closed") is True

    monkeypatch.setattr(module, "get_plugin_update_lock", lambda plugin_id: threading.Lock())
    monkeypatch.setattr(module, "get_plugin_config_path", lambda plugin_id: config_path)
    monkeypatch.setattr(module, "file_lock", _file_lock)
    monkeypatch.setattr(module, "load_toml_from_stream", lambda stream, context: current_config)
    monkeypatch.setattr(module, "deep_merge", lambda base, updates: merged_config)
    monkeypatch.setattr(module, "dump_toml_bytes", lambda payload: b"ok")
    monkeypatch.setattr(module, "atomic_write_bytes", _atomic_write_bytes)
    monkeypatch.setattr(module, "load_plugin_config", lambda plugin_id: {"config": merged_config})

    result = module.update_plugin_config("demo", {"runtime": {"enabled": False}})

    assert result["success"] is True


@pytest.mark.plugin_unit
def test_replace_plugin_config_atomic_write_after_file_lock_released(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "plugin.toml"
    config_path.write_text("[plugin]\nid='demo'\nentry='plugin.main:Main'\n", encoding="utf-8")

    current_config = {"plugin": {"id": "demo", "entry": "plugin.main:Main"}, "runtime": {"enabled": True}}
    replacement = {"plugin": {"id": "demo", "entry": "plugin.main:Main"}, "runtime": {"enabled": False}}
    write_lock_path = config_path.with_name(f"{config_path.name}.lock")
    state: dict[str, object] = {
        "target_locked": False,
        "write_locked": False,
        "target_stream": None,
    }

    @contextmanager
    def _file_lock(stream: BinaryIO) -> Iterator[None]:
        stream_path = Path(stream.name)
        if stream_path == write_lock_path:
            state["write_locked"] = True
            try:
                yield
            finally:
                state["write_locked"] = False
            return
        assert stream_path == config_path
        state["target_locked"] = True
        state["target_stream"] = stream
        try:
            yield
        finally:
            state["target_locked"] = False

    def _atomic_write_bytes(**kwargs: object) -> None:
        stream = state["target_stream"]
        assert state["target_locked"] is False
        assert state["write_locked"] is True
        assert stream is not None
        assert getattr(stream, "closed") is True

    monkeypatch.setattr(module, "get_plugin_update_lock", lambda plugin_id: threading.Lock())
    monkeypatch.setattr(module, "get_plugin_config_path", lambda plugin_id: config_path)
    monkeypatch.setattr(module, "file_lock", _file_lock)
    monkeypatch.setattr(module, "load_toml_from_stream", lambda stream, context: current_config)
    monkeypatch.setattr(module, "dump_toml_bytes", lambda payload: b"ok")
    monkeypatch.setattr(module, "atomic_write_bytes", _atomic_write_bytes)
    monkeypatch.setattr(module, "load_plugin_config", lambda plugin_id: {"config": replacement})

    result = module.replace_plugin_config("demo", replacement)

    assert result["success"] is True


@pytest.mark.plugin_unit
def test_update_plugin_config_toml_rejects_none() -> None:
    with pytest.raises(HTTPException) as exc_info:
        module.update_plugin_config_toml("demo", None)  # type: ignore[arg-type]

    assert exc_info.value.status_code == 400


@pytest.mark.plugin_unit
def test_update_plugin_config_toml_atomic_write_after_file_lock_released(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "plugin.toml"
    config_path.write_text("[plugin]\nid='demo'\nentry='plugin.main:Main'\n", encoding="utf-8")

    toml_text = "[plugin]\nid='demo'\nentry='plugin.main:Main'\n[runtime]\nenabled=false\n"
    current_config = {"plugin": {"id": "demo", "entry": "plugin.main:Main"}, "runtime": {"enabled": True}}
    updated_config = {"plugin": {"id": "demo", "entry": "plugin.main:Main"}, "runtime": {"enabled": False}}
    write_lock_path = config_path.with_name(f"{config_path.name}.lock")
    state: dict[str, object] = {
        "target_locked": False,
        "write_locked": False,
        "target_stream": None,
    }

    @contextmanager
    def _file_lock(stream: BinaryIO) -> Iterator[None]:
        stream_path = Path(stream.name)
        if stream_path == write_lock_path:
            state["write_locked"] = True
            try:
                yield
            finally:
                state["write_locked"] = False
            return
        assert stream_path == config_path
        state["target_locked"] = True
        state["target_stream"] = stream
        try:
            yield
        finally:
            state["target_locked"] = False

    def _atomic_write_text(**kwargs: object) -> None:
        stream = state["target_stream"]
        assert state["target_locked"] is False
        assert state["write_locked"] is True
        assert stream is not None
        assert getattr(stream, "closed") is True

    monkeypatch.setattr(module, "get_plugin_update_lock", lambda plugin_id: threading.Lock())
    monkeypatch.setattr(module, "get_plugin_config_path", lambda plugin_id: config_path)
    monkeypatch.setattr(module, "file_lock", _file_lock)
    monkeypatch.setattr(module, "load_toml_from_stream", lambda stream, context: current_config)
    monkeypatch.setattr(module, "atomic_write_text", _atomic_write_text)
    monkeypatch.setattr(module, "load_plugin_config", lambda plugin_id: {"config": updated_config})

    result = module.update_plugin_config_toml("demo", toml_text)

    assert result["success"] is True


@pytest.mark.plugin_unit
def test_update_plugin_config_open_failure_reports_path_and_stage(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "missing" / "plugin.toml"

    monkeypatch.setattr(module, "get_plugin_update_lock", lambda plugin_id: threading.Lock())
    monkeypatch.setattr(module, "get_plugin_config_path", lambda plugin_id: config_path)

    with pytest.raises(HTTPException) as exc_info:
        module.update_plugin_config("demo", {"runtime": {"enabled": False}})

    detail = str(exc_info.value.detail)
    assert exc_info.value.status_code == 500
    assert str(config_path) in detail
    assert "while open" in detail
    assert "FileNotFoundError" in detail
