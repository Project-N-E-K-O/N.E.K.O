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


@pytest.mark.plugin_unit
@pytest.mark.parametrize("bad_name", ["../plugin", "..\\plugin", "nested/name", ".", ".."])
def test_profile_writes_reject_path_traversal_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad_name: str
) -> None:
    from fastapi import HTTPException

    plugin_dir = tmp_path / "demo"
    plugin_dir.mkdir(parents=True)
    config_path = plugin_dir / "plugin.toml"
    manifest_text = "[plugin]\nid='demo'\nentry='demo:Plugin'\n"
    config_path.write_text(manifest_text, encoding="utf-8")
    monkeypatch.setattr(module, "get_plugin_config_path", lambda _pid: config_path)

    with pytest.raises(HTTPException) as upsert_exc:
        module.upsert_profile_config(
            plugin_id="demo",
            profile_name=bad_name,
            config={"feature": {"x": 1}},
            make_active=False,
        )
    assert upsert_exc.value.status_code == 400

    with pytest.raises(HTTPException) as update_exc:
        module.update_profile_config(
            plugin_id="demo",
            profile_name=bad_name,
            updates={"feature": {"x": 1}},
        )
    assert update_exc.value.status_code == 400
    assert config_path.read_text(encoding="utf-8") == manifest_text


@pytest.mark.plugin_unit
def test_update_profile_config_rejects_unknown_profile_with_404(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi import HTTPException

    plugin_dir = tmp_path / "demo"
    profiles_dir = plugin_dir / "profiles"
    profiles_dir.mkdir(parents=True)
    config_path = plugin_dir / "plugin.toml"
    config_path.write_text(
        "[plugin]\nid='demo'\nentry='demo:Plugin'\n"
        "[plugin.config_profiles]\nactive='prod'\n"
        "[plugin.config_profiles.files]\nprod='profiles/prod.toml'\n",
        encoding="utf-8",
    )
    (profiles_dir / "prod.toml").write_text("[feature]\nx=1\n", encoding="utf-8")
    monkeypatch.setattr(module, "get_plugin_config_path", lambda _pid: config_path)

    with pytest.raises(HTTPException) as exc_info:
        module.update_profile_config(
            plugin_id="demo",
            profile_name="prd",
            updates={"feature": {"x": 2}},
        )

    assert exc_info.value.status_code == 404
    assert not (profiles_dir / "prd.toml").exists()
    assert not (plugin_dir / "profiles.toml").exists()
    assert (profiles_dir / "prod.toml").read_text(encoding="utf-8") == "[feature]\nx=1\n"


@pytest.mark.plugin_unit
def test_update_profile_config_rejects_deleted_profile_with_leftover_file_and_upsert_recreates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tomllib
    from fastapi import HTTPException

    plugin_dir = tmp_path / "demo"
    profiles_dir = plugin_dir / "profiles"
    profiles_dir.mkdir(parents=True)
    config_path = plugin_dir / "plugin.toml"
    config_path.write_text("[plugin]\nid='demo'\nentry='demo:Plugin'\n", encoding="utf-8")
    monkeypatch.setattr(module, "get_plugin_config_path", lambda _pid: config_path)

    module.upsert_profile_config(
        plugin_id="demo",
        profile_name="work",
        config={"feature": {"x": 1}},
        make_active=False,
    )
    deleted = module.delete_profile_config(plugin_id="demo", profile_name="work")
    assert deleted["removed"] is True

    work_file = profiles_dir / "work.toml"
    profiles_toml = plugin_dir / "profiles.toml"
    assert work_file.exists()
    work_before = work_file.read_bytes()
    profiles_before = profiles_toml.read_bytes()

    with pytest.raises(HTTPException) as exc_info:
        module.update_profile_config(
            plugin_id="demo",
            profile_name="work",
            updates={"feature": {"x": 2}},
        )

    assert exc_info.value.status_code == 404
    assert work_file.read_bytes() == work_before
    assert profiles_toml.read_bytes() == profiles_before
    assert "work" not in tomllib.loads(profiles_toml.read_text(encoding="utf-8"))["config_profiles"]["files"]

    recreated = module.upsert_profile_config(
        plugin_id="demo",
        profile_name="work",
        config={"feature": {"x": 42}},
        make_active=False,
    )
    assert recreated["config"] == {"feature": {"x": 42}}
    assert "work" in tomllib.loads(profiles_toml.read_text(encoding="utf-8"))["config_profiles"]["files"]


@pytest.mark.plugin_unit
def test_profile_writes_numeric_alias_fallback_precedence_and_affected_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tomllib

    plugin_dir = tmp_path / "demo"
    profiles_dir = plugin_dir / "profiles"
    profiles_dir.mkdir(parents=True)
    config_path = plugin_dir / "plugin.toml"
    config_path.write_text("[plugin]\nid='demo'\nentry='demo:Plugin'\n", encoding="utf-8")
    monkeypatch.setattr(module, "get_plugin_config_path", lambda _pid: config_path)

    one_path = profiles_dir / "one.toml"
    one_path.write_text("[feature]\nx=1\n", encoding="utf-8")
    zero_one_path = profiles_dir / "zero_one.toml"
    zero_one_path.write_text("[feature]\nx=100\n", encoding="utf-8")
    profiles_toml = plugin_dir / "profiles.toml"

    # Case 1: env forces active="01" while only "1" is registered -> both update and upsert of "1" affect active.
    profiles_toml.write_text(
        "[config_profiles]\nactive='1'\n[config_profiles.files]\n'1'='profiles/one.toml'\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("NEKO_PLUGIN_DEMO_PROFILE", "01")

    upd_one = module.update_profile_config(
        plugin_id="demo",
        profile_name="1",
        updates={"feature": {"x": 2}},
    )
    assert upd_one["active_profile"] == "01"
    assert upd_one["affected_active"] is True

    ups_one = module.upsert_profile_config(
        plugin_id="demo",
        profile_name="1",
        config={"feature": {"x": 3}},
        make_active=False,
    )
    assert ups_one["active_profile"] == "01"
    assert ups_one["affected_active"] is True

    # Case 2: active="1", update called with "01" -> resolves via "1" without registering extra alias "01".
    monkeypatch.delenv("NEKO_PLUGIN_DEMO_PROFILE", raising=False)
    upd_alias = module.update_profile_config(
        plugin_id="demo",
        profile_name="01",
        updates={"feature": {"x": 4}},
    )
    assert upd_alias["affected_active"] is True
    assert tomllib.loads(one_path.read_text(encoding="utf-8")) == {"feature": {"x": 4}}
    assert set(tomllib.loads(profiles_toml.read_text(encoding="utf-8"))["config_profiles"]["files"]) == {"1"}

    # Case 3: exact key "01" points to a distinct valid file -> updating "1" keeps affected_active=False.
    profiles_toml.write_text(
        "[config_profiles]\nactive='01'\n"
        "[config_profiles.files]\n'01'='profiles/zero_one.toml'\n'1'='profiles/one.toml'\n",
        encoding="utf-8",
    )
    upd_other = module.update_profile_config(
        plugin_id="demo",
        profile_name="1",
        updates={"feature": {"x": 5}},
    )
    assert upd_other["affected_active"] is False
    assert tomllib.loads(zero_one_path.read_text(encoding="utf-8")) == {"feature": {"x": 100}}

    upd_exact = module.update_profile_config(
        plugin_id="demo",
        profile_name="01",
        updates={"feature": {"x": 101}},
    )
    assert upd_exact["affected_active"] is True
    assert tomllib.loads(zero_one_path.read_text(encoding="utf-8")) == {"feature": {"x": 101}}

    # Case 4: exact key "01" is whitespace-only/empty -> falls back to "1" cleanly.
    profiles_toml.write_text(
        "[config_profiles]\nactive='01'\n"
        "[config_profiles.files]\n'01'='   '\n'1'='profiles/one.toml'\n",
        encoding="utf-8",
    )
    upd_blank = module.update_profile_config(
        plugin_id="demo",
        profile_name="1",
        updates={"feature": {"x": 6}},
    )
    assert upd_blank["affected_active"] is True


@pytest.mark.plugin_unit
def test_get_profile_config_and_delete_profile_config_numeric_alias_rules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from plugin.server.infrastructure.config_profiles import get_profile_config

    plugin_dir = tmp_path / "demo"
    profiles_dir = plugin_dir / "profiles"
    profiles_dir.mkdir(parents=True)
    config_path = plugin_dir / "plugin.toml"
    config_path.write_text("[plugin]\nid='demo'\nentry='demo:Plugin'\n", encoding="utf-8")
    monkeypatch.setattr(module, "get_plugin_config_path", lambda _pid: config_path)

    one_path = profiles_dir / "one.toml"
    one_path.write_text("[feature]\nx=1\n", encoding="utf-8")
    zero_one_path = profiles_dir / "zero_one.toml"
    zero_one_path.write_text("[feature]\nx=99\n", encoding="utf-8")
    profiles_toml = plugin_dir / "profiles.toml"

    # 1) get_profile_config("01") falls back to registered "1" without adding alias entry,
    #    and respects exact "01" precedence when present.
    profiles_toml.write_text(
        "[config_profiles]\nactive='1'\n[config_profiles.files]\n'1'='profiles/one.toml'\n",
        encoding="utf-8",
    )
    module.update_profile_config(
        plugin_id="demo",
        profile_name="01",
        updates={"feature": {"x": 7}},
    )
    fetched_alias = get_profile_config(
        plugin_id="demo",
        profile_name="01",
        config_path=config_path,
    )
    assert fetched_alias["profile"]["exists"] is True
    assert fetched_alias["config"] == {"feature": {"x": 7}}

    profiles_toml.write_text(
        "[config_profiles]\nactive='01'\n"
        "[config_profiles.files]\n'01'='profiles/zero_one.toml'\n'1'='profiles/one.toml'\n",
        encoding="utf-8",
    )
    fetched_exact = get_profile_config(
        plugin_id="demo",
        profile_name="01",
        config_path=config_path,
    )
    assert fetched_exact["config"] == {"feature": {"x": 99}}

    # 2) delete_profile_config("1") with NEKO_PLUGIN_DEMO_PROFILE=01:
    #    affected_active is False when "01" has its own valid registration,
    #    True when only "1" (or "01" whitespace + "1" fallback) served active "01".
    monkeypatch.setenv("NEKO_PLUGIN_DEMO_PROFILE", "01")
    deleted_inactive = module.delete_profile_config(plugin_id="demo", profile_name="1")
    assert deleted_inactive["removed"] is True
    assert deleted_inactive["affected_active"] is False

    profiles_toml.write_text(
        "[config_profiles]\nactive='1'\n[config_profiles.files]\n'1'='profiles/one.toml'\n",
        encoding="utf-8",
    )
    deleted_active = module.delete_profile_config(plugin_id="demo", profile_name="1")
    assert deleted_active["removed"] is True
    assert deleted_active["affected_active"] is True

    profiles_toml.write_text(
        "[config_profiles]\nactive='01'\n"
        "[config_profiles.files]\n'01'='   '\n'1'='profiles/one.toml'\n",
        encoding="utf-8",
    )
    deleted_blank_fallback = module.delete_profile_config(plugin_id="demo", profile_name="1")
    assert deleted_blank_fallback["removed"] is True
    assert deleted_blank_fallback["affected_active"] is True
