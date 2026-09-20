from __future__ import annotations


import inspect


import hashlib


import json


import os


import stat


import threading


import time


from contextlib import contextmanager


from pathlib import Path


from types import SimpleNamespace


import pytest


import main_routers.card_drop_router as C


import main_routers.community_oauth as O


from utils import config_manager as config_manager_module


from utils import storage_migration as storage_migration_module


from utils.storage import community_private_state as private_state


from utils.storage import policy as storage_policy_module


from utils.storage.community_private_state import probe_retained_community_state


pytestmark = pytest.mark.unit


USER_ID = "11111111-1111-4111-8111-111111111111"


HAS_SAFE_DIR_FD = hasattr(os, "O_DIRECTORY") and os.open in os.supports_dir_fd


def test_fixed_anchor_private_read_treats_operating_system_error_as_unreadable(
    tmp_path, monkeypatch
):
    credential = tmp_path / "anchor" / "state" / "community_auth.json"
    _install_roots(
        monkeypatch,
        anchor_state=credential.parent,
        selected_root=tmp_path / "selected",
    )
    credential.parent.mkdir(parents=True)
    credential.write_text('{"access_token":"kept"}', encoding="utf-8")

    def deny_read(*_args, **_kwargs):
        raise PermissionError("credential is locked")

    monkeypatch.setattr(C, "read_fixed_anchor_state_json", deny_read)

    assert C._read_fixed_anchor_private_json_state(credential) == (
        "unreadable",
        None,
    )
    assert C._load_auth() is None
    assert credential.read_text(encoding="utf-8") == '{"access_token":"kept"}'


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO files are unavailable")
def test_private_json_reader_rejects_fifo_without_opening_it(tmp_path):
    fifo_path = tmp_path / "private-state.json"
    os.mkfifo(fifo_path)

    state, payload = C._read_private_json_state(fifo_path)

    assert state == "unsafe"
    assert payload is None


def _install_roots(monkeypatch, *, anchor_state: Path, selected_root: Path, retained_root: Path | None = None):
    manager = SimpleNamespace(
        app_name="N.E.K.O",
        local_state_dir=anchor_state,
        memory_dir=selected_root / "memory",
    )
    monkeypatch.setattr(config_manager_module, "get_config_manager", lambda *_args, **_kwargs: manager)
    migration = None
    if retained_root is not None:
        migration = {
            "status": "completed",
            "source_root": str(retained_root),
            "target_root": str(selected_root),
            "retained_source_root": str(retained_root),
        }

    def _load_migration(*_args, **_kwargs):
        if migration is None:
            return None
        payload = dict(migration)
        try:
            identity = retained_root.lstat()
        except OSError:
            return payload
        payload["retained_source_identity"] = {
            "device": int(identity.st_dev),
            "inode": int(identity.st_ino),
        }
        payload["source_root_identity"] = [
            int(identity.st_dev),
            int(identity.st_ino),
        ]
        return payload

    monkeypatch.setattr(
        storage_migration_module,
        "load_storage_migration",
        _load_migration,
    )
    return manager


@pytest.mark.parametrize(
    "platform_layout",
    (
        "windows/AppData/Roaming/N.E.K.O/state",
        "macos/Library/Application Support/N.E.K.O/state",
        "linux/.local/share/N.E.K.O/state",
    ),
)
def test_community_private_paths_follow_fixed_anchor_on_all_platform_layouts(
    tmp_path,
    monkeypatch,
    platform_layout,
):
    anchor_state = tmp_path / platform_layout
    selected_root = tmp_path / "external-volume" / "N.E.K.O"
    _install_roots(monkeypatch, anchor_state=anchor_state, selected_root=selected_root)
    monkeypatch.delenv("NEKO_USER_DATA_DIR", raising=False)

    assert C._auth_path() == anchor_state / "community_auth.json"
    assert C._social_session_path() == anchor_state / "social_session.json"
    assert C._steam_pending_path() == anchor_state / "community_steam_pending.json"
    assert O._oauth_pending_path() == anchor_state / "community_oauth_pending.json"
    assert C._auth_path().parent != selected_root


def test_electron_social_session_remains_host_owned_while_backend_state_is_anchored(
    tmp_path,
    monkeypatch,
):
    anchor_state = tmp_path / "anchor" / "state"
    selected_root = tmp_path / "selected" / "N.E.K.O"
    electron_root = tmp_path / "electron-user-data"
    _install_roots(monkeypatch, anchor_state=anchor_state, selected_root=selected_root)
    monkeypatch.setenv("NEKO_USER_DATA_DIR", str(electron_root))

    assert C._social_session_path() == electron_root / "social_session.json"
    assert C._auth_path() == anchor_state / "community_auth.json"
    assert O._oauth_pending_path() == anchor_state / "community_oauth_pending.json"


def test_completed_storage_migration_copies_credentials_from_retained_root_once(
    tmp_path,
    monkeypatch,
):
    anchor_state = tmp_path / "anchor" / "state"
    selected_root = tmp_path / "target" / "N.E.K.O"
    retained_root = tmp_path / "source" / "N.E.K.O"
    _install_roots(
        monkeypatch,
        anchor_state=anchor_state,
        selected_root=selected_root,
        retained_root=retained_root,
    )
    monkeypatch.delenv("NEKO_USER_DATA_DIR", raising=False)
    retained_root.mkdir(parents=True)
    auth = {
        "access_token": "retained-token",
        "refresh_token": "retained-refresh",
        "local_user_id": USER_ID,
        "auth_source": "oauth",
    }
    social = {
        "schema_version": 2,
        "baseUrl": "https://community.example",
        "token": "retained-token",
        "refresh_token": "retained-refresh",
        "local_user_id": USER_ID,
        "auth_source": "oauth",
    }
    (retained_root / "community_auth.json").write_text(json.dumps(auth), encoding="utf-8")
    (retained_root / "social_session.json").write_text(json.dumps(social), encoding="utf-8")

    snapshot = C._desktop_session_snapshot()

    assert snapshot and snapshot["access_token"] == "retained-token"
    assert json.loads((anchor_state / "community_auth.json").read_text(encoding="utf-8")) == auth
    assert json.loads((anchor_state / "social_session.json").read_text(encoding="utf-8")) == social
    assert (retained_root / "community_auth.json").exists()
    assert (retained_root / "social_session.json").exists()




def test_offline_committed_target_makes_logout_zero_delete_until_remount(
    tmp_path,
    monkeypatch,
):
    anchor_state = tmp_path / "anchor" / "state"
    effective_root = tmp_path / "anchor" / "N.E.K.O"
    target_root = tmp_path / "offline-volume" / "N.E.K.O"
    old_source = tmp_path / "old-source" / "N.E.K.O"
    manager = _install_roots(
        monkeypatch,
        anchor_state=anchor_state,
        selected_root=target_root,
        retained_root=old_source,
    )
    manager.memory_dir = effective_root / "memory"
    manager.committed_selected_root = target_root
    manager.recovery_committed_root_unavailable = True
    effective_root.mkdir(parents=True)
    anchor_state.mkdir(parents=True)
    canonical_auth = anchor_state / "community_auth.json"
    canonical_social = anchor_state / "social_session.json"
    canonical_auth.write_text(json.dumps({"access_token": "active"}), encoding="utf-8")
    canonical_social.write_text(json.dumps({"token": "active"}), encoding="utf-8")
    old_source.mkdir(parents=True)
    reused_source_auth = old_source / "community_auth.json"
    reused_source_auth.write_text(json.dumps({"access_token": "unrelated"}), encoding="utf-8")
    monkeypatch.setattr(
        storage_migration_module,
        "load_storage_migration",
        lambda *_args, **_kwargs: {
            "status": "completed",
            "source_root": str(old_source),
            "target_root": str(target_root),
            "retained_source_mode": "cleaned",
        },
    )

    assert C._clear_auth() is False
    assert canonical_auth.exists()
    assert canonical_social.exists()
    assert reused_source_auth.exists()

    target_root.mkdir(parents=True)
    target_auth = target_root / "community_auth.json"
    target_social = target_root / "social_session.json"
    target_auth.write_text(json.dumps({"access_token": "old-target"}), encoding="utf-8")
    target_social.write_text(json.dumps({"token": "old-target"}), encoding="utf-8")
    manager.recovery_committed_root_unavailable = False

    assert C._clear_auth() is True
    assert not canonical_auth.exists()
    assert not canonical_social.exists()
    assert not target_auth.exists()
    assert not target_social.exists()
    assert reused_source_auth.exists()


def test_malformed_canonical_credential_fails_closed_without_legacy_fallback(
    tmp_path,
    monkeypatch,
):
    anchor_state = tmp_path / "anchor" / "state"
    selected_root = tmp_path / "target" / "N.E.K.O"
    retained_root = tmp_path / "source" / "N.E.K.O"
    _install_roots(
        monkeypatch,
        anchor_state=anchor_state,
        selected_root=selected_root,
        retained_root=retained_root,
    )
    anchor_state.mkdir(parents=True)
    retained_root.mkdir(parents=True)
    canonical = anchor_state / "community_auth.json"
    legacy = retained_root / "community_auth.json"
    canonical.write_text("{bad-json", encoding="utf-8")
    legacy.write_text(json.dumps({"access_token": "stale-token"}), encoding="utf-8")

    assert C._load_auth() is None
    assert canonical.read_text(encoding="utf-8") == "{bad-json"
    assert legacy.exists()
    C.prepare_retained_community_state_cleanup(
        retained_root,
        config_manager=SimpleNamespace(local_state_dir=anchor_state),
    )
    assert legacy.exists()


def test_lazy_private_state_migration_keeps_legacy_source(tmp_path, monkeypatch):
    anchor_state = tmp_path / "anchor" / "state"
    selected_root = tmp_path / "target" / "N.E.K.O"
    retained_root = tmp_path / "source" / "N.E.K.O"
    _install_roots(
        monkeypatch,
        anchor_state=anchor_state,
        selected_root=selected_root,
        retained_root=retained_root,
    )
    anchor_state.mkdir(parents=True)
    retained_root.mkdir(parents=True)
    canonical = anchor_state / "community_auth.json"
    legacy = retained_root / "community_auth.json"
    legacy_record = {"access_token": "legacy-token"}
    legacy.write_text(json.dumps(legacy_record), encoding="utf-8")

    migrated = C._load_or_migrate_private_json(
        canonical,
        [legacy],
        validator=lambda value: bool(value.get("access_token")),
        retain_legacy_source=True,
    )

    assert migrated == legacy_record
    assert json.loads(canonical.read_text(encoding="utf-8")) == legacy_record
    assert json.loads(legacy.read_text(encoding="utf-8")) == legacy_record


@pytest.mark.parametrize(
    "checkpoint_error",
    (ValueError("malformed checkpoint"), PermissionError("checkpoint denied")),
    ids=("malformed", "unreadable"),
)
def test_checkpoint_failure_never_falls_back_to_current_root_private_state(
    tmp_path,
    monkeypatch,
    checkpoint_error,
):
    anchor_state = tmp_path / "anchor" / "state"
    selected_root = tmp_path / "selected" / "N.E.K.O"
    _install_roots(
        monkeypatch,
        anchor_state=anchor_state,
        selected_root=selected_root,
    )
    selected_root.mkdir(parents=True)
    records = {
        "community_auth.json": {"access_token": "must-not-import"},
        "social_session.json": {"token": "must-not-import"},
        "community_oauth_pending.json": {
            "state": "oauth-state",
            "code_verifier": "verifier",
            "expires_at": time.time() + 300,
        },
        "community_steam_pending.json": {
            "state": "steam-state",
            "code_verifier": "verifier",
            "ts": time.time(),
        },
    }
    for filename, payload in records.items():
        (selected_root / filename).write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(
        storage_migration_module,
        "load_storage_migration",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(checkpoint_error),
    )

    assert C._legacy_root_candidates() == ([], [])
    assert C._load_auth() is None
    assert C._load_social_session() is None
    assert O._load_oauth_pending() == (anchor_state / "community_oauth_pending.json", None)
    assert C._consume_steam_pending("steam-state") == (False, None)
    assert not any(anchor_state.glob("*.json"))
    for filename, payload in records.items():
        assert json.loads((selected_root / filename).read_text(encoding="utf-8")) == payload


def test_oauth_and_steam_pending_move_to_anchor_without_copying_lock_files(
    tmp_path,
    monkeypatch,
):
    anchor_state = tmp_path / "anchor" / "state"
    selected_root = tmp_path / "target" / "N.E.K.O"
    retained_root = tmp_path / "source" / "N.E.K.O"
    _install_roots(
        monkeypatch,
        anchor_state=anchor_state,
        selected_root=selected_root,
        retained_root=retained_root,
    )
    retained_root.mkdir(parents=True)
    oauth_payload = {
        "state": "oauth-state",
        "code_verifier": "oauth-verifier",
        "expires_at": time.time() + 300,
    }
    (retained_root / "community_oauth_pending.json").write_text(
        json.dumps(oauth_payload),
        encoding="utf-8",
    )
    steam_payload = {
        "ts": time.time(),
        "state": "steam-state",
        "code_verifier": "steam-verifier",
    }
    (retained_root / "community_steam_pending.json").write_text(
        json.dumps(steam_payload),
        encoding="utf-8",
    )

    oauth_path, loaded_oauth = O._load_oauth_pending()
    steam_ok, steam_verifier = C._consume_steam_pending("steam-state")

    assert oauth_path == anchor_state / "community_oauth_pending.json"
    assert loaded_oauth == oauth_payload
    assert not (retained_root / "community_oauth_pending.json").exists()
    assert steam_ok is True
    assert steam_verifier == "steam-verifier"
    assert not (retained_root / "community_steam_pending.json").exists()
    assert not (anchor_state / "community_steam_pending.json").exists(), "consumed pending is one-shot"


def test_private_inventory_distinguishes_absent_active_lock_unsafe_and_unreadable(
    tmp_path,
    monkeypatch,
):
    retained_root = tmp_path / "retained" / "N.E.K.O"
    retained_root.mkdir(parents=True)
    assert probe_retained_community_state(retained_root).state == "absent"

    lock = retained_root / "social_session.json.lock"
    lock.write_text("writer", encoding="utf-8")
    inventory = probe_retained_community_state(retained_root)
    assert inventory.state == "active_lock"
    assert inventory.has_managed_content is True
    assert inventory.cleanup_blocked is True
    old = time.time() - 86400
    os.utime(lock, (old, old))
    assert probe_retained_community_state(retained_root).state == "active_lock"

    lock.unlink()
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    try:
        (retained_root / "community_auth.json").symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links are unavailable on this platform")
    assert probe_retained_community_state(retained_root).state == "unsafe"

    (retained_root / "community_auth.json").unlink()
    original_lstat = Path.lstat

    def _lstat_with_denied_private_file(path):
        if path == retained_root / "community_auth.json":
            raise PermissionError("denied")
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", _lstat_with_denied_private_file)
    assert probe_retained_community_state(retained_root).state == "unreadable"


@pytest.mark.parametrize("use_dir_fd", (False, True), ids=("path", "dirfd"))
def test_old_social_lock_is_never_automatically_broken(
    tmp_path,
    monkeypatch,
    use_dir_fd,
):
    if use_dir_fd and not HAS_SAFE_DIR_FD:
        pytest.skip("POSIX dirfd locks are unavailable")
    root = tmp_path / "root"
    root.mkdir()
    lock_name = "social_session.json.lock"
    lock_path = root / lock_name
    lock_path.write_text(
        json.dumps({"token": "stale-owner", "created_at": 1}),
        encoding="utf-8",
    )
    old = time.time() - 86400
    os.utime(lock_path, (old, old))
    monkeypatch.setattr(C, "_SOCIAL_SESSION_LOCK_TIMEOUT_SEC", 0)
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY) if use_dir_fd else None
    try:
        lock_context = (
            C._social_session_lock_at(root_fd)
            if use_dir_fd
            else C._social_session_lock(root / "social_session.json")
        )
        with pytest.raises(TimeoutError), lock_context:
            pass
        assert json.loads(lock_path.read_text(encoding="utf-8")) == {
            "token": "stale-owner",
            "created_at": 1,
        }
    finally:
        if root_fd is not None:
            os.close(root_fd)


@pytest.mark.parametrize("use_dir_fd", (False, True), ids=("path", "dirfd"))
def test_dead_social_lock_owner_is_reclaimed(
    tmp_path,
    monkeypatch,
    use_dir_fd,
):
    if use_dir_fd and not HAS_SAFE_DIR_FD:
        pytest.skip("POSIX dirfd locks are unavailable")
    root = tmp_path / "root"
    root.mkdir()
    lock_name = "social_session.json.lock"
    lock_path = root / lock_name
    orphan_pid = 999999
    lock_path.write_text(
        json.dumps(
            {
                "token": f"{orphan_pid}:orphaned-owner",
                "pid": orphan_pid,
                "created_at": 1,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("NEKO_LAUNCHER_SINGLE_INSTANCE_PROVEN", "test-owner")
    monkeypatch.setattr(
        C,
        "classify_social_lock_owner",
        lambda owner: "orphaned" if owner and owner.get("pid") == orphan_pid else "active",
    )
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY) if use_dir_fd else None
    try:
        lock_context = (
            C._social_session_lock_at(root_fd)
            if use_dir_fd
            else C._social_session_lock(root / "social_session.json")
        )
        with lock_context:
            current = json.loads(lock_path.read_text(encoding="utf-8"))
            assert current["pid"] == os.getpid()
            assert current["token"].startswith(f"{os.getpid()}:")
    finally:
        if root_fd is not None:
            os.close(root_fd)

    assert not lock_path.exists()
