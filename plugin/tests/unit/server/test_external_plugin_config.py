import asyncio
from pathlib import Path
import threading
import time
import tomllib

import pytest

from plugin.server.infrastructure import config_paths
from plugin.server.infrastructure import config_storage
from plugin.server.infrastructure.config_resolver import resolve_plugin_config_from_path
from plugin.server.infrastructure.config_updates import update_plugin_config
from plugin.server.messaging.handlers.plugin_config import (
    handle_plugin_config_replace,
    handle_plugin_config_update,
)


def test_resolve_plugin_config_initializes_external_config_from_example(
    monkeypatch,
    tmp_path: Path,
) -> None:
    storage_root = tmp_path / "runtime-storage"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(storage_root))
    installed_dir = tmp_path / "plugins" / "demo"
    installed_dir.mkdir(parents=True)
    manifest_path = installed_dir / "plugin.toml"
    manifest_path.write_text(
        "[plugin]\nid = 'demo'\nversion = '2.0.0'\nentry = 'plugins.demo:Demo'\n",
        encoding="utf-8",
    )
    example_text = "[plugin_runtime]\nenabled = false\n\n[demo]\nmessage = 'hello'\n"
    (installed_dir / "config.example.toml").write_text(example_text, encoding="utf-8")

    resolved = resolve_plugin_config_from_path(
        "demo",
        config_path=manifest_path,
        include_effective_config=True,
        validate_schema=False,
    )

    external_config = storage_root / "plugins" / "demo" / "config" / "plugin.toml"
    assert external_config.read_text(encoding="utf-8") == example_text
    assert resolved["config_path"] == str(external_config)
    assert resolved["base_config"] == {
        "plugin_runtime": {"enabled": False},
        "demo": {"message": "hello"},
    }
    assert resolved["effective_config"] == {
        "plugin_runtime": {"enabled": False},
        "demo": {"message": "hello"},
        "plugin": {
            "id": "demo",
            "version": "2.0.0",
            "entry": "plugins.demo:Demo",
        },
    }


def test_resolve_plugin_config_applies_manifest_profile_to_external_config(
    monkeypatch,
    tmp_path: Path,
) -> None:
    storage_root = tmp_path / "runtime-storage"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(storage_root))
    installed_dir = tmp_path / "plugins" / "demo"
    installed_dir.mkdir(parents=True)
    manifest_path = installed_dir / "plugin.toml"
    manifest_path.write_text(
        (
            "[plugin]\n"
            "id = 'demo'\n"
            "version = '2.0.0'\n"
            "entry = 'plugins.demo:Demo'\n"
            "\n[plugin.config_profiles]\n"
            "active = 'dev'\n"
            "\n[plugin.config_profiles.files]\n"
            "dev = 'dev.toml'\n"
        ),
        encoding="utf-8",
    )
    (installed_dir / "config.example.toml").write_text(
        "[runtime]\nenabled = false\n",
        encoding="utf-8",
    )
    (installed_dir / "dev.toml").write_text(
        "[runtime]\nenabled = true\n",
        encoding="utf-8",
    )

    resolved = resolve_plugin_config_from_path(
        "demo",
        config_path=manifest_path,
        include_effective_config=True,
        validate_schema=False,
    )

    assert resolved["profiles_state"]["config_profiles"]["active"] == "dev"
    assert resolved["base_config"] == {"runtime": {"enabled": False}}
    assert resolved["effective_config"]["runtime"] == {"enabled": True}


def test_resolve_plugin_config_keeps_manifest_tables_with_runtime_and_profile_overrides(
    monkeypatch,
    tmp_path: Path,
) -> None:
    storage_root = tmp_path / "runtime-storage"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(storage_root))
    installed_dir = tmp_path / "plugins" / "demo"
    installed_dir.mkdir(parents=True)
    manifest_path = installed_dir / "plugin.toml"
    manifest_path.write_text(
        (
            "[plugin]\n"
            "id = 'demo'\n"
            "version = '2.0.0'\n"
            "entry = 'plugins.demo:Demo'\n"
            "\n[plugin.config_profiles]\n"
            "active = 'dev'\n"
            "\n[plugin.config_profiles.files]\n"
            "dev = 'dev.toml'\n"
            "\n[adapter]\n"
            "mode = 'gateway'\n"
            "priority = 1\n"
            "label = 'manifest'\n"
            "\n[plugin_state]\n"
            "backend = 'file'\n"
        ),
        encoding="utf-8",
    )
    (installed_dir / "config.example.toml").write_text(
        "[adapter]\npriority = 2\nlabel = 'runtime'\n\n[plugin_state]\npersist_mode = 'auto'\n",
        encoding="utf-8",
    )
    (installed_dir / "dev.toml").write_text(
        "[adapter]\npriority = 3\n",
        encoding="utf-8",
    )

    resolved = resolve_plugin_config_from_path(
        "demo",
        config_path=manifest_path,
        include_effective_config=True,
        validate_schema=False,
    )

    assert resolved["effective_config"] == {
        "plugin": {
            "id": "demo",
            "version": "2.0.0",
            "entry": "plugins.demo:Demo",
            "config_profiles": {
                "active": "dev",
                "files": {"dev": "dev.toml"},
            },
        },
        "adapter": {"mode": "gateway", "priority": 3, "label": "runtime"},
        "plugin_state": {"backend": "file", "persist_mode": "auto"},
    }


def test_update_plugin_config_writes_only_external_runtime_config(
    monkeypatch,
    tmp_path: Path,
) -> None:
    storage_root = tmp_path / "runtime-storage"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(storage_root))
    plugin_root = tmp_path / "plugins"
    installed_dir = plugin_root / "demo"
    installed_dir.mkdir(parents=True)
    manifest_path = installed_dir / "plugin.toml"
    manifest_text = (
        "[plugin]\nid = 'demo'\nversion = '2.0.0'\nentry = 'plugins.demo:Demo'\n"
        "\n[plugin_runtime]\nenabled = true\n\n[demo]\nmessage = 'original'\n"
    )
    manifest_path.write_text(manifest_text, encoding="utf-8")
    monkeypatch.setattr(config_paths, "PLUGIN_CONFIG_ROOTS", (plugin_root,))

    resolve_plugin_config_from_path(
        "demo",
        config_path=manifest_path,
        include_effective_config=True,
        validate_schema=False,
    )
    result = update_plugin_config("demo", {"demo": {"message": "changed"}})

    external_config = storage_root / "plugins" / "demo" / "config" / "plugin.toml"
    assert manifest_path.read_text(encoding="utf-8") == manifest_text
    with external_config.open("rb") as stream:
        external_data = tomllib.load(stream)
    assert external_data["demo"]["message"] == "changed"
    assert result["config"]["plugin"]["version"] == "2.0.0"


def test_legacy_manifest_is_copied_without_rewriting(
    monkeypatch,
    tmp_path: Path,
) -> None:
    storage_root = tmp_path / "runtime-storage"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(storage_root))
    installed_dir = tmp_path / "plugins" / "demo"
    installed_dir.mkdir(parents=True)
    manifest_path = installed_dir / "plugin.toml"
    legacy_text = (
        "# Keep this user-facing comment.\n"
        "[plugin]\nid = 'demo'\nversion = '1.0.0'\nentry = 'plugins.demo:Demo'\n"
        "\n[demo]\nmessage = 'legacy value'\n"
    )
    manifest_path.write_text(legacy_text, encoding="utf-8")

    resolve_plugin_config_from_path(
        "demo",
        config_path=manifest_path,
        include_effective_config=True,
        validate_schema=False,
    )

    external_config = storage_root / "plugins" / "demo" / "config" / "plugin.toml"
    assert external_config.read_text(encoding="utf-8") == legacy_text


def test_existing_runtime_config_is_preserved_and_manifest_identity_wins(
    monkeypatch,
    tmp_path: Path,
) -> None:
    storage_root = tmp_path / "runtime-storage"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(storage_root))
    installed_dir = tmp_path / "plugins" / "demo"
    installed_dir.mkdir(parents=True)
    manifest_path = installed_dir / "plugin.toml"
    manifest_path.write_text(
        "[plugin]\nid = 'demo'\nversion = '2.0.0'\nentry = 'plugins.demo:NewDemo'\n",
        encoding="utf-8",
    )
    external_config = storage_root / "plugins" / "demo" / "config" / "plugin.toml"
    external_config.parent.mkdir(parents=True)
    runtime_text = (
        "# Existing user config must remain byte-for-byte unchanged.\n"
        "[plugin]\nid = 'demo'\nversion = '1.0.0'\nentry = 'plugins.demo:OldDemo'\n"
        "\n[demo]\nmessage = 'user value'\n"
    )
    external_config.write_text(runtime_text, encoding="utf-8")

    resolved = resolve_plugin_config_from_path(
        "demo",
        config_path=manifest_path,
        include_effective_config=True,
        validate_schema=False,
    )

    assert external_config.read_text(encoding="utf-8") == runtime_text
    assert resolved["effective_config"]["demo"] == {"message": "user value"}
    assert resolved["effective_config"]["plugin"] == {
        "id": "demo",
        "version": "2.0.0",
        "entry": "plugins.demo:NewDemo",
    }


@pytest.mark.asyncio
async def test_plugin_config_replace_handler_removes_stale_runtime_keys(
    monkeypatch,
    tmp_path: Path,
) -> None:
    storage_root = tmp_path / "runtime-storage"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(storage_root))
    plugin_root = tmp_path / "plugins"
    installed_dir = plugin_root / "demo"
    installed_dir.mkdir(parents=True)
    manifest_path = installed_dir / "plugin.toml"
    manifest_path.write_text(
        "[plugin]\nid = 'demo'\nversion = '2.0.0'\nentry = 'plugins.demo:Demo'\n",
        encoding="utf-8",
    )
    (installed_dir / "config.example.toml").write_text(
        "[stale]\ntop = true\n\n[feature]\nstale_nested = true\nkeep = false\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(config_paths, "PLUGIN_CONFIG_ROOTS", (plugin_root,))
    resolve_plugin_config_from_path(
        "demo",
        config_path=manifest_path,
        include_effective_config=True,
        validate_schema=False,
    )
    responses: list[tuple[object, object]] = []

    def _send_response(
        _to_plugin: str,
        _request_id: str,
        result: object,
        error: object,
        timeout: float = 10.0,
    ) -> None:
        responses.append((result, error))

    await handle_plugin_config_replace(
        {
            "from_plugin": "demo",
            "request_id": "replace-root",
            "config": {"feature": {"keep": True}},
        },
        _send_response,
    )

    assert responses[-1][1] is None
    resolved = resolve_plugin_config_from_path(
        "demo",
        config_path=manifest_path,
        include_effective_config=True,
        validate_schema=False,
    )
    assert resolved["base_config"] == {
        "plugin": {},
        "feature": {"keep": True},
    }
    assert resolved["effective_config"] == {
        "plugin": {
            "id": "demo",
            "version": "2.0.0",
            "entry": "plugins.demo:Demo",
        },
        "feature": {"keep": True},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ("replace", "update"))
async def test_plugin_config_write_timeout_cannot_commit_after_response(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    operation: str,
) -> None:
    storage_root = tmp_path / "runtime-storage"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(storage_root))
    plugin_root = tmp_path / "plugins"
    installed_dir = plugin_root / "demo"
    installed_dir.mkdir(parents=True)
    manifest_path = installed_dir / "plugin.toml"
    manifest_path.write_text(
        "[plugin]\nid = 'demo'\nversion = '2.0.0'\nentry = 'plugins.demo:Demo'\n",
        encoding="utf-8",
    )
    external_config = storage_root / "plugins" / "demo" / "config" / "plugin.toml"
    external_config.parent.mkdir(parents=True)
    original_runtime = "[feature]\nenabled = false\n"
    external_config.write_text(original_runtime, encoding="utf-8")
    monkeypatch.setattr(config_paths, "PLUGIN_CONFIG_ROOTS", (plugin_root,))

    temp_file_fsync_started = threading.Event()
    allow_temp_file_fsync = threading.Event()
    real_fsync = config_storage.os.fsync

    def _block_first_fsync(file_descriptor: int) -> None:
        if not temp_file_fsync_started.is_set():
            temp_file_fsync_started.set()
            if not allow_temp_file_fsync.wait(timeout=2):
                raise TimeoutError("test did not release config temp-file fsync")
        real_fsync(file_descriptor)

    monkeypatch.setattr(config_storage.os, "fsync", _block_first_fsync)
    responses: list[tuple[object, object]] = []

    def _send_response(
        _to_plugin: str,
        _request_id: str,
        result: object,
        error: object,
        timeout: float = 10.0,
    ) -> None:
        responses.append((result, error))

    request = {
        "from_plugin": "demo",
        "request_id": f"{operation}-timeout",
        "timeout": 0.05,
        "config" if operation == "replace" else "updates": {
            "feature": {"enabled": True}
        },
    }
    handler = (
        handle_plugin_config_replace
        if operation == "replace"
        else handle_plugin_config_update
    )
    handler_task = asyncio.create_task(handler(request, _send_response))
    await asyncio.wait_for(
        asyncio.to_thread(temp_file_fsync_started.wait, 1),
        timeout=1.5,
    )

    handler_returned_before_release = False
    try:
        await asyncio.wait_for(asyncio.shield(handler_task), timeout=0.5)
        handler_returned_before_release = True
    except TimeoutError:
        pass
    finally:
        allow_temp_file_fsync.set()
        await asyncio.wait_for(handler_task, timeout=1)

    assert handler_returned_before_release is True
    if operation == "replace":
        assert responses == [(None, "Config persistence timed out; replacement was not applied")]
    else:
        assert responses == [
            (
                {
                    "success": False,
                    "plugin_id": "demo",
                    "config": {"feature": {"enabled": True}},
                    "requires_reload": False,
                    "persisted": False,
                    "message": "Config persistence timed out; update is applied in plugin memory only",
                },
                None,
            )
        ]
    assert external_config.read_text(encoding="utf-8") == original_runtime
    for _ in range(100):
        if not list(external_config.parent.glob(".plugin_config_*.toml")):
            break
        await asyncio.sleep(0.01)
    assert list(external_config.parent.glob(".plugin_config_*.toml")) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ("replace", "update"))
async def test_plugin_config_write_honors_client_deadline_after_queue_delay(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    operation: str,
) -> None:
    storage_root = tmp_path / "runtime-storage"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(storage_root))
    plugin_root = tmp_path / "plugins"
    installed_dir = plugin_root / "demo"
    installed_dir.mkdir(parents=True)
    (installed_dir / "plugin.toml").write_text(
        "[plugin]\nid = 'demo'\nversion = '2.0.0'\nentry = 'plugins.demo:Demo'\n",
        encoding="utf-8",
    )
    external_config = storage_root / "plugins" / "demo" / "config" / "plugin.toml"
    external_config.parent.mkdir(parents=True)
    original_runtime = "[feature]\nenabled = false\n"
    external_config.write_text(original_runtime, encoding="utf-8")
    monkeypatch.setattr(config_paths, "PLUGIN_CONFIG_ROOTS", (plugin_root,))
    responses: list[tuple[object, object]] = []

    handler = (
        handle_plugin_config_replace
        if operation == "replace"
        else handle_plugin_config_update
    )
    await handler(
        {
            "from_plugin": "demo",
            "request_id": f"delayed-{operation}",
            "timeout": 10.0,
            "_request_deadline_monotonic": time.monotonic() - 1.0,
            "config" if operation == "replace" else "updates": {
                "feature": {"enabled": True}
            },
        },
        lambda _plugin, _request, result, error, **_kwargs: responses.append(
            (result, error)
        ),
    )

    assert external_config.read_text(encoding="utf-8") == original_runtime
    if operation == "replace":
        assert responses == [(None, "Config persistence timed out; replacement was not applied")]
    else:
        assert responses[0][0]["persisted"] is False  # type: ignore[index]


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ("replace", "update"))
async def test_plugin_config_write_returns_real_result_when_commit_wins_deadline_race(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    operation: str,
) -> None:
    storage_root = tmp_path / "runtime-storage"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(storage_root))
    plugin_root = tmp_path / "plugins"
    installed_dir = plugin_root / "demo"
    installed_dir.mkdir(parents=True)
    (installed_dir / "plugin.toml").write_text(
        "[plugin]\nid = 'demo'\nversion = '2.0.0'\nentry = 'plugins.demo:Demo'\n",
        encoding="utf-8",
    )
    external_config = storage_root / "plugins" / "demo" / "config" / "plugin.toml"
    external_config.parent.mkdir(parents=True)
    external_config.write_text("[feature]\nenabled = false\n", encoding="utf-8")
    monkeypatch.setattr(config_paths, "PLUGIN_CONFIG_ROOTS", (plugin_root,))

    replace_finished = threading.Event()
    allow_parent_fsync = threading.Event()
    real_replace = config_storage.os.replace

    def _replace_then_signal(source: object, target: object) -> None:
        real_replace(source, target)
        replace_finished.set()

    def _block_parent_fsync(_path: Path) -> None:
        if not allow_parent_fsync.wait(timeout=2):
            raise TimeoutError("test did not release post-commit fsync")

    monkeypatch.setattr(config_storage.os, "replace", _replace_then_signal)
    monkeypatch.setattr(config_storage, "_fsync_parent_dir", _block_parent_fsync)
    responses: list[tuple[object, object]] = []
    handler = (
        handle_plugin_config_replace
        if operation == "replace"
        else handle_plugin_config_update
    )
    task = asyncio.create_task(
        handler(
            {
                "from_plugin": "demo",
                "request_id": f"commit-wins-{operation}",
                "timeout": 0.8,
                "config" if operation == "replace" else "updates": {
                    "feature": {"enabled": True}
                },
            },
            lambda _plugin, _request, result, error, **_kwargs: responses.append(
                (result, error)
            ),
        )
    )
    assert await asyncio.wait_for(
        asyncio.to_thread(replace_finished.wait, 1),
        timeout=1.5,
    )
    await asyncio.sleep(0.65)
    assert responses == []

    allow_parent_fsync.set()
    await asyncio.wait_for(task, timeout=1)

    assert responses[-1][1] is None
    assert responses[-1][0]["success"] is True  # type: ignore[index]
    with external_config.open("rb") as stream:
        assert tomllib.load(stream)["feature"] == {"enabled": True}


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ("replace", "update"))
async def test_cancelled_plugin_config_write_cannot_commit_before_next_write(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    operation: str,
) -> None:
    storage_root = tmp_path / "runtime-storage"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(storage_root))
    plugin_root = tmp_path / "plugins"
    installed_dir = plugin_root / "demo"
    installed_dir.mkdir(parents=True)
    (installed_dir / "plugin.toml").write_text(
        "[plugin]\nid = 'demo'\nversion = '2.0.0'\nentry = 'plugins.demo:Demo'\n",
        encoding="utf-8",
    )
    external_config = storage_root / "plugins" / "demo" / "config" / "plugin.toml"
    external_config.parent.mkdir(parents=True)
    external_config.write_text("[feature]\ngeneration = 0\n", encoding="utf-8")
    monkeypatch.setattr(config_paths, "PLUGIN_CONFIG_ROOTS", (plugin_root,))

    first_temp_file_fsync_started = threading.Event()
    allow_first_temp_file_fsync = threading.Event()
    real_fsync = config_storage.os.fsync
    real_replace = config_storage.os.replace
    replace_calls = 0

    def _block_first_fsync(file_descriptor: int) -> None:
        if not first_temp_file_fsync_started.is_set():
            first_temp_file_fsync_started.set()
            if not allow_first_temp_file_fsync.wait(timeout=2):
                raise TimeoutError("test did not release cancelled config write")
        real_fsync(file_descriptor)

    def _record_replace(source: object, target: object) -> None:
        nonlocal replace_calls
        replace_calls += 1
        real_replace(source, target)

    monkeypatch.setattr(config_storage.os, "fsync", _block_first_fsync)
    monkeypatch.setattr(config_storage.os, "replace", _record_replace)

    handler = (
        handle_plugin_config_replace
        if operation == "replace"
        else handle_plugin_config_update
    )
    cancelled_task = asyncio.create_task(
        handler(
            {
                "from_plugin": "demo",
                "request_id": f"cancelled-{operation}",
                "config" if operation == "replace" else "updates": {
                    "feature": {"generation": 1}
                },
                "timeout": 10.0,
            },
            lambda *_args, **_kwargs: None,
        )
    )
    await asyncio.wait_for(
        asyncio.to_thread(first_temp_file_fsync_started.wait, 1),
        timeout=1.5,
    )
    cancelled_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled_task

    allow_first_temp_file_fsync.set()
    responses: list[tuple[object, object]] = []

    def _send_response(
        _to_plugin: str,
        _request_id: str,
        result: object,
        error: object,
        timeout: float = 10.0,
    ) -> None:
        responses.append((result, error))

    await asyncio.wait_for(
        handler(
            {
                "from_plugin": "demo",
                "request_id": f"next-{operation}",
                "config" if operation == "replace" else "updates": {
                    "feature": {"generation": 2}
                },
                "timeout": 2.0,
            },
            _send_response,
        ),
        timeout=1.5,
    )

    assert responses[-1][1] is None
    assert replace_calls == 1
    with external_config.open("rb") as stream:
        assert tomllib.load(stream)["feature"] == {"generation": 2}
