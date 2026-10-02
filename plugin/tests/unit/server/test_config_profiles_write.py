from __future__ import annotations

import multiprocessing
import os
from pathlib import Path

import pytest

from plugin.server.infrastructure import config_profiles_write as module
from plugin.server.infrastructure import config_locking, config_paths


def _resolve_profile_in_other_process(manifest: str, runtime: str, connection) -> None:
    lock_path = Path(runtime).with_name("plugin.toml.lock")
    with lock_path.open("a+b") as stream:
        try:
            if config_locking._msvcrt is not None:
                config_locking._msvcrt.locking(stream.fileno(), config_locking._msvcrt.LK_NBLCK, 1)
            else:
                config_locking._fcntl.flock(stream.fileno(), config_locking._fcntl.LOCK_EX | config_locking._fcntl.LOCK_NB)
        except OSError:
            connection.send("blocked")
        else:
            if config_locking._msvcrt is not None:
                stream.seek(0, os.SEEK_SET)
                config_locking._msvcrt.locking(stream.fileno(), config_locking._msvcrt.LK_UNLCK, 1)
            else:
                config_locking._fcntl.flock(stream.fileno(), config_locking._fcntl.LOCK_UN)
            connection.send("unprotected")
    from plugin.server.infrastructure.config_resolver import resolve_plugin_config_from_path

    result = resolve_plugin_config_from_path("demo", config_path=Path(manifest))
    connection.send((result["profiles_state"]["config_profiles"]["active"], result["effective_config"]["feature"]["value"]))
    connection.close()


@pytest.mark.plugin_unit
@pytest.mark.parametrize("operation", ["upsert", "activate", "delete"])
def test_profile_writes_exclude_child_snapshot_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str,
) -> None:
    if config_locking._msvcrt is None and config_locking._fcntl is None:
        pytest.skip("OS file locking unavailable")
    installed = tmp_path / "installed" / "demo"
    installed.mkdir(parents=True)
    manifest = installed / "plugin.toml"
    manifest.write_text("[plugin]\nid='demo'\nentry='demo:Plugin'\n[feature]\nvalue=0\n", encoding="utf-8")
    (installed / "profiles.toml").write_text(
        "[config_profiles]\nactive='old'\n[config_profiles.files]\nold='old.toml'\nnew='new.toml'\n", encoding="utf-8",
    )
    (installed / "old.toml").write_text("[feature]\nvalue=1\n", encoding="utf-8")
    (installed / "new.toml").write_text("[feature]\nvalue=2\n", encoding="utf-8")
    monkeypatch.setattr(module, "get_plugin_config_path", lambda _plugin_id: manifest)
    runtime = config_paths.ensure_plugin_runtime_config("demo", manifest_path=manifest)
    spawn = multiprocessing.get_context("spawn")
    parent, child = spawn.Pipe()
    process = spawn.Process(target=_resolve_profile_in_other_process, args=(str(manifest), str(runtime), child))
    atomic_dump = module._atomic_dump_toml
    started = False

    def _write_and_start_reader(**kwargs):
        nonlocal started
        atomic_dump(**kwargs)
        if not started:
            started = True
            process.start()
            child.close()
            assert parent.poll(15), "child did not attempt snapshot lock"
            assert parent.recv() == "blocked"
            assert not parent.poll(0.1), "reader escaped the writer's transaction"

    monkeypatch.setattr(module, "_atomic_dump_toml", _write_and_start_reader)
    try:
        if operation == "upsert":
            module.upsert_profile_config(plugin_id="demo", profile_name="new", config={"feature": {"value": 3}}, make_active=True)
            expected = ("new", 3)
        elif operation == "activate":
            module.set_active_profile(plugin_id="demo", profile_name="new")
            expected = ("new", 2)
        else:
            module.delete_profile_config(plugin_id="demo", profile_name="old")
            expected = (None, 0)
        assert parent.poll(15), "child did not complete snapshot after writer released lock"
        assert parent.recv() == expected
        process.join(timeout=5)
        assert process.exitcode == 0
        assert not (runtime.parent / "profiles.toml").exists()
    finally:
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
        parent.close()
        child.close()


@pytest.mark.plugin_unit
def test_delete_profile_config_removes_active_key_in_payload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plugin_dir = tmp_path / "demo"
    plugin_dir.mkdir(parents=True, exist_ok=True)

    config_path = plugin_dir / "plugin.toml"
    config_path.write_text("[plugin]\nid='demo'\n", encoding="utf-8")

    profiles_path = plugin_dir / "profiles.toml"
    profiles_path.write_text(
        "[config_profiles]\nactive='dev'\n[config_profiles.files]\ndev='profiles/dev.toml'\n",
        encoding="utf-8",
    )

    captured_payloads: list[dict[str, object]] = []

    def _fake_atomic_dump_toml(*, target_path: Path, payload: dict[str, object], prefix: str) -> None:
        if target_path.name == "profiles.toml":
            captured_payloads.append(payload)

    monkeypatch.setattr(module, "tomli_w", object())
    monkeypatch.setattr(module, "get_plugin_config_path", lambda plugin_id: config_path)
    monkeypatch.setattr(module, "_atomic_dump_toml", _fake_atomic_dump_toml)

    result = module.delete_profile_config(plugin_id="demo", profile_name="dev")

    assert result["removed"] is True
    assert captured_payloads, "profiles payload was not persisted"
    persisted_cfg = captured_payloads[-1]["config_profiles"]
    assert isinstance(persisted_cfg, dict)
    assert "active" not in persisted_cfg
