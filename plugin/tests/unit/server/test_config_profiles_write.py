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


@pytest.mark.plugin_unit
def test_upsert_profile_config_drops_reserved_permissions_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(tmp_path / "storage"))
    plugin_dir = tmp_path / "demo"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    config_path = plugin_dir / "plugin.toml"
    config_path.write_text("[plugin]\nid='demo'\n", encoding="utf-8")

    written: dict[str, dict[str, object]] = {}

    def _fake_atomic_dump_toml(*, target_path: Path, payload: dict[str, object], prefix: str) -> None:
        written[target_path.name] = payload

    monkeypatch.setattr(module, "tomli_w", object())
    monkeypatch.setattr(module, "get_plugin_config_path", lambda plugin_id: config_path)
    monkeypatch.setattr(module, "_atomic_dump_toml", _fake_atomic_dump_toml)
    monkeypatch.setattr(module, "get_profile_config", lambda **kwargs: {})

    module.upsert_profile_config(
        plugin_id="demo",
        profile_name="dev",
        config={"runtime": {"level": 1}, "permissions": {"ask": {}}},
        make_active=None,
    )

    assert written["dev.toml"] == {"runtime": {"level": 1}}


@pytest.mark.plugin_unit
def test_update_profile_config_merges_updates_and_preserves_active_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tomllib
    from fastapi import HTTPException

    plugin_dir = tmp_path / "demo"
    profiles_dir = plugin_dir / "profiles"
    profiles_dir.mkdir(parents=True)
    config_path = plugin_dir / "plugin.toml"
    config_path.write_text(
        "[plugin]\nid='demo'\n[plugin.config_profiles]\nactive='home'\n"
        "[plugin.config_profiles.files]\nhome='profiles/home.toml'\nwork='profiles/work.toml'\n",
        encoding="utf-8",
    )
    (profiles_dir / "work.toml").write_text(
        "[feature]\nkeep=1\nremove_me=2\noverride='old'\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "get_plugin_config_path", lambda _pid: config_path)

    with pytest.raises(HTTPException) as exc_info:
        module.update_profile_config(
            plugin_id="demo",
            profile_name="work",
            updates={"plugin": {"id": "hijack"}},
        )
    assert exc_info.value.status_code == 400

    res = module.update_profile_config(
        plugin_id="demo",
        profile_name="work",
        updates={"feature": {"override": "new", "remove_me": "__DELETE__", "added": 3}},
    )
    assert res["profile"] == "work"
    assert res["active_profile"] == "home"
    assert res["config"] == {"feature": {"keep": 1, "override": "new", "added": 3}}

    persisted_work = tomllib.loads((profiles_dir / "work.toml").read_text(encoding="utf-8"))
    assert persisted_work == {"feature": {"keep": 1, "override": "new", "added": 3}}
    persisted_profiles = tomllib.loads((plugin_dir / "profiles.toml").read_text(encoding="utf-8"))
    assert persisted_profiles["config_profiles"]["active"] == "home"
    assert set(persisted_profiles["config_profiles"]["files"]) == {"home", "work"}


@pytest.mark.plugin_unit
def test_update_profile_config_preserves_manifest_profiles_and_extra_keys_when_table_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tomllib

    plugin_dir = tmp_path / "demo"
    profiles_dir = plugin_dir / "profiles"
    profiles_dir.mkdir(parents=True)
    config_path = plugin_dir / "plugin.toml"
    config_path.write_text(
        "[plugin]\nid='demo'\n[plugin.config_profiles]\nactive='home'\n"
        "[plugin.config_profiles.files]\nhome='profiles/home.toml'\nwork='profiles/work.toml'\n",
        encoding="utf-8",
    )
    (plugin_dir / "profiles.toml").write_text("[extra]\nnote='keep'\n", encoding="utf-8")
    monkeypatch.setattr(module, "get_plugin_config_path", lambda _pid: config_path)

    res = module.update_profile_config(
        plugin_id="demo",
        profile_name="work",
        updates={"feature": {"x": 1}},
    )
    assert res["active_profile"] == "home"
    persisted_profiles = tomllib.loads((plugin_dir / "profiles.toml").read_text(encoding="utf-8"))
    assert persisted_profiles["extra"] == {"note": "keep"}
    assert persisted_profiles["config_profiles"]["active"] == "home"
    assert set(persisted_profiles["config_profiles"]["files"]) == {"home", "work"}


@pytest.mark.plugin_unit
def test_profile_writes_respect_cancelled_commit_guard_and_clean_temp_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import time
    from plugin.server.infrastructure.config_storage import ConfigCommitCancelled, ConfigCommitGuard

    plugin_dir = tmp_path / "demo"
    plugin_dir.mkdir(parents=True)
    config_path = plugin_dir / "plugin.toml"
    config_path.write_text("[plugin]\nid='demo'\nentry='demo:Plugin'\n", encoding="utf-8")
    (plugin_dir / "profiles.toml").write_text(
        "[config_profiles]\nactive='home'\n[config_profiles.files]\nhome='profiles/home.toml'\n",
        encoding="utf-8",
    )
    (plugin_dir / "profiles").mkdir()
    (plugin_dir / "profiles" / "home.toml").write_text("[feature]\nx=1\n", encoding="utf-8")
    monkeypatch.setattr(module, "get_plugin_config_path", lambda _pid: config_path)

    for op in ("upsert", "delete", "activate"):
        guard = ConfigCommitGuard(deadline=time.monotonic() + 10.0)
        assert guard.cancel_if_pending() is True
        with pytest.raises(ConfigCommitCancelled):
            if op == "upsert":
                module.upsert_profile_config(
                    plugin_id="demo",
                    profile_name="work",
                    config={"feature": {"x": 2}},
                    make_active=True,
                    commit_guard=guard,
                )
            elif op == "delete":
                module.delete_profile_config(
                    plugin_id="demo",
                    profile_name="home",
                    commit_guard=guard,
                )
            else:
                module.set_active_profile(
                    plugin_id="demo",
                    profile_name="home",
                    commit_guard=guard,
                )
        assert not list(plugin_dir.glob(".profile*"))
        assert not list((plugin_dir / "profiles").glob(".profile*"))
    assert "active='home'" in (plugin_dir / "profiles.toml").read_text(encoding="utf-8")


@pytest.mark.plugin_unit
def test_profile_writes_detect_shared_file_alias_with_active_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin_dir = tmp_path / "demo"
    (plugin_dir / "profiles").mkdir(parents=True)
    config_path = plugin_dir / "plugin.toml"
    config_path.write_text("[plugin]\nid='demo'\nentry='demo:Plugin'\n", encoding="utf-8")
    (plugin_dir / "profiles.toml").write_text(
        "[config_profiles]\nactive='home'\n"
        "[config_profiles.files]\nhome='profiles/shared.toml'\nwork='profiles/shared.toml'\nother='profiles/other.toml'\n",
        encoding="utf-8",
    )
    (plugin_dir / "profiles" / "shared.toml").write_text("[feature]\nx=1\n", encoding="utf-8")
    (plugin_dir / "profiles" / "other.toml").write_text("[feature]\nx=9\n", encoding="utf-8")
    monkeypatch.setattr(module, "get_plugin_config_path", lambda _pid: config_path)

    upd_alias = module.update_profile_config(
        plugin_id="demo",
        profile_name="work",
        updates={"feature": {"x": 2}},
    )
    assert upd_alias["active_profile"] == "home"
    assert upd_alias["affected_active"] is True

    upd_other = module.update_profile_config(
        plugin_id="demo",
        profile_name="other",
        updates={"feature": {"x": 10}},
    )
    assert upd_other["affected_active"] is False

    ups_alias = module.upsert_profile_config(
        plugin_id="demo",
        profile_name="work",
        config={"feature": {"x": 3}},
        make_active=False,
    )
    assert ups_alias["active_profile"] == "home"
    assert ups_alias["affected_active"] is True

    ups_other = module.upsert_profile_config(
        plugin_id="demo",
        profile_name="other",
        config={"feature": {"x": 11}},
        make_active=False,
    )
    assert ups_other["affected_active"] is False


@pytest.mark.plugin_unit
def test_upsert_profile_config_rolls_back_first_file_if_second_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os
    import time
    from fastapi import HTTPException
    from plugin.server.infrastructure.config_storage import ConfigCommitGuard

    plugin_dir = tmp_path / "demo"
    (plugin_dir / "profiles").mkdir(parents=True)
    config_path = plugin_dir / "plugin.toml"
    config_path.write_text("[plugin]\nid='demo'\nentry='demo:Plugin'\n", encoding="utf-8")
    (plugin_dir / "profiles.toml").write_text(
        "[config_profiles]\nactive='work'\n[config_profiles.files]\nwork='profiles/work.toml'\n",
        encoding="utf-8",
    )
    work_path = plugin_dir / "profiles" / "work.toml"
    work_path.write_text("[feature]\nx=1\n", encoding="utf-8")
    monkeypatch.setattr(module, "get_plugin_config_path", lambda _pid: config_path)

    real_replace = os.replace

    def _fail_on_profiles(src, dst):
        if Path(dst).name == "profiles.toml":
            raise OSError("simulated disk error")
        return real_replace(src, dst)

    monkeypatch.setattr(module.os, "replace", _fail_on_profiles)
    guard = ConfigCommitGuard(deadline=time.monotonic() + 10.0)
    with pytest.raises(HTTPException) as exc_info:
        module.upsert_profile_config(
            plugin_id="demo",
            profile_name="work",
            config={"feature": {"x": 999}},
            make_active=True,
            commit_guard=guard,
        )
    assert exc_info.value.status_code == 500
    assert work_path.read_text(encoding="utf-8") == "[feature]\nx=1\n"
