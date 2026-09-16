from __future__ import annotations

import inspect
import hashlib
import json
import os
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
from utils.storage.community_private_state import probe_retained_community_state


pytestmark = pytest.mark.unit

USER_ID = "11111111-1111-4111-8111-111111111111"
HAS_SAFE_DIR_FD = hasattr(os, "O_DIRECTORY") and os.open in os.supports_dir_fd


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO files are unavailable")
def test_private_json_reader_rejects_fifo_without_opening_it(tmp_path):
    fifo_path = tmp_path / "private-state.json"
    os.mkfifo(fifo_path)

    state, payload = C._read_private_json_state(fifo_path)

    assert state == "unsafe"
    assert payload is None


@pytest.mark.skipif(
    not hasattr(os, "mkfifo") or not hasattr(os, "O_NONBLOCK"),
    reason="POSIX non-blocking FIFO reads are unavailable",
)
@pytest.mark.parametrize("reader_kind", ("path", "dirfd", "dict"))
def test_private_json_readers_do_not_block_when_regular_file_becomes_fifo(
    tmp_path,
    monkeypatch,
    reader_kind,
):
    if reader_kind == "dirfd" and not HAS_SAFE_DIR_FD:
        pytest.skip("POSIX dirfd reads are unavailable")
    credential = tmp_path / "private-state.json"
    credential.write_text('{"access_token":"regular"}', encoding="utf-8")
    real_open = private_state.os.open
    opened_flags = []
    replaced = False

    def replace_with_fifo_before_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal replaced
        opened_path = tmp_path / path if dir_fd is not None else Path(path)
        if opened_path == credential and not replaced:
            replaced = True
            credential.unlink()
            os.mkfifo(credential)
            opened_flags.append(flags)
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(private_state.os, "open", replace_with_fifo_before_open)
    root_fd = (
        real_open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
        if reader_kind == "dirfd"
        else None
    )
    outcome = []

    def read_raced_credential():
        if reader_kind == "dirfd":
            outcome.append(C._read_private_json_state_at(root_fd, credential.name))
        elif reader_kind == "dict":
            outcome.append(C._read_json_dict(credential))
        else:
            outcome.append(C._read_private_json_state(credential))

    worker = threading.Thread(target=read_raced_credential, daemon=True)
    worker.start()
    worker.join(timeout=1)
    if worker.is_alive():
        writer_fd = real_open(credential, os.O_WRONLY | os.O_NONBLOCK)
        os.close(writer_fd)
        worker.join(timeout=1)
    if root_fd is not None:
        os.close(root_fd)

    assert not worker.is_alive(), "raced private-state FIFO read must not block"
    assert outcome == ([None] if reader_kind == "dict" else [("unsafe", None)])
    assert opened_flags and opened_flags[0] & os.O_NONBLOCK
    assert opened_flags[0] & os.O_NOFOLLOW


def test_private_json_reader_rejects_oversized_file_before_reading(
    tmp_path,
    monkeypatch,
):
    credential = tmp_path / "private-state.json"
    with credential.open("wb") as handle:
        handle.truncate(private_state.COMMUNITY_PRIVATE_STATE_SNAPSHOT_MAX_BYTES + 1)
    real_read = private_state.os.read

    def reject_read(fd, size):
        if os.path.samestat(os.fstat(fd), credential.stat()):
            pytest.fail("oversized private JSON must be rejected before reading")
        return real_read(fd, size)

    monkeypatch.setattr(private_state.os, "read", reject_read)

    assert private_state.read_private_json_state(credential) == ("unsafe", None)


def test_private_json_reader_allows_atomic_replace_but_rejects_stale_snapshot(
    tmp_path,
    monkeypatch,
):
    credential = tmp_path / "private-state.json"
    replacement = tmp_path / "replacement.json"
    credential.write_text('{"access_token":"old"}', encoding="utf-8")
    replacement.write_text('{"access_token":"new"}', encoding="utf-8")
    real_read = private_state.os.read
    replaced = False

    def replace_after_first_read(fd, size):
        nonlocal replaced
        chunk = real_read(fd, size)
        if chunk and not replaced:
            replaced = True
            os.replace(replacement, credential)
        return chunk

    monkeypatch.setattr(private_state.os, "read", replace_after_first_read)

    assert private_state.read_private_json_state(credential) == ("unsafe", None)
    assert replaced is True
    assert json.loads(credential.read_text(encoding="utf-8")) == {
        "access_token": "new"
    }


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
    monkeypatch.setattr(storage_migration_module, "load_storage_migration", lambda *_args, **_kwargs: migration)
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


def test_completed_storage_migration_moves_credentials_from_retained_root_once(
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
    assert not (retained_root / "community_auth.json").exists()
    assert not (retained_root / "social_session.json").exists()


def test_old_social_lock_blocks_legacy_migration_without_deletion(tmp_path, monkeypatch):
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
    social = {
        "schema_version": 2,
        "baseUrl": "https://community.example",
        "token": "retained-token",
        "refresh_token": "retained-refresh",
        "local_user_id": USER_ID,
        "auth_source": "oauth",
    }
    legacy = retained_root / "social_session.json"
    lock = retained_root / "social_session.json.lock"
    legacy.write_text(json.dumps(social), encoding="utf-8")
    lock.write_text(json.dumps({"token": "old-owner"}), encoding="utf-8")
    old = time.time() - 86400
    os.utime(lock, (old, old))
    monkeypatch.setattr(C, "_SOCIAL_SESSION_LOCK_TIMEOUT_SEC", 0)

    with pytest.raises(TimeoutError, match="social session lock is busy"):
        C._desktop_session_snapshot()
    assert json.loads(legacy.read_text(encoding="utf-8")) == social
    assert json.loads(lock.read_text(encoding="utf-8")) == {"token": "old-owner"}
    assert not (anchor_state / "social_session.json").exists()


def test_existing_canonical_credential_preserves_conflicting_legacy_on_cleanup(
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
    canonical.write_text(json.dumps({"access_token": "new-token"}), encoding="utf-8")
    legacy.write_text(json.dumps({"access_token": "old-token"}), encoding="utf-8")

    assert C._load_auth() == {"access_token": "new-token"}
    assert json.loads(canonical.read_text(encoding="utf-8"))["access_token"] == "new-token"
    assert legacy.exists(), "a conflicting legacy record is preserved for explicit recovery"

    with pytest.raises(OSError, match="canonical community_auth.json"):
        C.prepare_retained_community_state_cleanup(
            retained_root,
            config_manager=SimpleNamespace(local_state_dir=anchor_state),
        )
    assert legacy.exists(), "conflicting credentials require recovery rather than guessed deletion"


def test_conflicting_target_and_retained_records_fail_closed_for_all_private_state(
    tmp_path,
    monkeypatch,
):
    anchor_state = tmp_path / "anchor" / "state"
    target_root = tmp_path / "target" / "N.E.K.O"
    retained_root = tmp_path / "source" / "N.E.K.O"
    manager = _install_roots(
        monkeypatch,
        anchor_state=anchor_state,
        selected_root=target_root,
        retained_root=retained_root,
    )
    monkeypatch.delenv("NEKO_USER_DATA_DIR", raising=False)
    target_root.mkdir(parents=True)
    retained_root.mkdir(parents=True)
    now = time.time()
    source_records = {
        "community_auth.json": {"access_token": "source-token"},
        "social_session.json": {"token": "source-token"},
        "community_oauth_pending.json": {
            "state": "source-oauth",
            "code_verifier": "source-verifier",
            "expires_at": now + 300,
        },
        "community_steam_pending.json": {
            "state": "source-steam",
            "code_verifier": "source-verifier",
            "ts": now,
        },
    }
    target_records = {
        "community_auth.json": {"access_token": "target-token"},
        "social_session.json": {"token": "target-token"},
        "community_oauth_pending.json": {
            "state": "target-oauth",
            "code_verifier": "target-verifier",
            "expires_at": now + 300,
        },
        "community_steam_pending.json": {
            "state": "target-steam",
            "code_verifier": "target-verifier",
            "ts": now,
        },
    }
    for filename, payload in source_records.items():
        (retained_root / filename).write_text(json.dumps(payload), encoding="utf-8")
    for filename, payload in target_records.items():
        (target_root / filename).write_text(json.dumps(payload), encoding="utf-8")

    assert C._legacy_selected_roots() == [retained_root]
    assert C._legacy_conflict_witness_roots() == [retained_root, target_root]
    assert C._load_auth() is None
    assert C._load_social_session() is None
    assert O._load_oauth_pending()[1] is None
    assert C._consume_steam_pending("source-steam") == (False, None)
    with pytest.raises(OSError, match="conflicting legacy"):
        C.prepare_retained_community_state_cleanup(
            retained_root,
            config_manager=manager,
        )
    assert not anchor_state.exists() or not any(anchor_state.glob("*.json"))
    for filename in source_records:
        assert (retained_root / filename).exists()
        assert (target_root / filename).exists()

    assert C._clear_auth() is True
    for filename in source_records:
        assert not (retained_root / filename).exists()
        assert not (target_root / filename).exists()


def test_completed_checkpoint_uses_retained_authority_while_target_is_offline(
    tmp_path,
    monkeypatch,
):
    anchor_state = tmp_path / "anchor" / "state"
    effective_root = tmp_path / "anchor" / "N.E.K.O"
    target_root = tmp_path / "offline-volume" / "N.E.K.O"
    retained_root = tmp_path / "source" / "N.E.K.O"
    manager = _install_roots(
        monkeypatch,
        anchor_state=anchor_state,
        selected_root=target_root,
        retained_root=retained_root,
    )
    manager.memory_dir = effective_root / "memory"
    manager.committed_selected_root = target_root
    manager.recovery_committed_root_unavailable = True
    retained_root.mkdir(parents=True)
    retained_auth = {"access_token": "retained-token"}
    (retained_root / "community_auth.json").write_text(
        json.dumps(retained_auth),
        encoding="utf-8",
    )

    assert C._legacy_selected_roots() == [retained_root]
    assert C._load_auth() == retained_auth
    assert json.loads((anchor_state / "community_auth.json").read_text(encoding="utf-8")) == retained_auth


def test_single_legacy_root_allows_rotated_tokens_for_same_credential_identity(
    tmp_path,
    monkeypatch,
):
    anchor_state = tmp_path / "anchor" / "state"
    target_root = tmp_path / "target" / "N.E.K.O"
    retained_root = tmp_path / "source" / "N.E.K.O"
    _install_roots(
        monkeypatch,
        anchor_state=anchor_state,
        selected_root=target_root,
        retained_root=retained_root,
    )
    retained_root.mkdir(parents=True)
    auth = {
        "access_token": "auth-token",
        "local_user_id": USER_ID,
    }
    social = {
        "token": "different-social-token",
        "local_user_id": USER_ID,
    }
    (retained_root / "community_auth.json").write_text(json.dumps(auth), encoding="utf-8")
    (retained_root / "social_session.json").write_text(json.dumps(social), encoding="utf-8")

    assert C._load_auth() == auth
    assert C._load_social_session() == social
    assert json.loads((anchor_state / "community_auth.json").read_text(encoding="utf-8")) == auth
    assert json.loads((anchor_state / "social_session.json").read_text(encoding="utf-8")) == social
    assert not (retained_root / "community_auth.json").exists()
    assert not (retained_root / "social_session.json").exists()


def test_single_legacy_root_rejects_different_credential_identities(
    tmp_path,
    monkeypatch,
):
    anchor_state = tmp_path / "anchor" / "state"
    target_root = tmp_path / "target" / "N.E.K.O"
    retained_root = tmp_path / "source" / "N.E.K.O"
    _install_roots(
        monkeypatch,
        anchor_state=anchor_state,
        selected_root=target_root,
        retained_root=retained_root,
    )
    retained_root.mkdir(parents=True)
    auth = {"access_token": "shared-token", "local_user_id": USER_ID}
    social = {
        "token": "shared-token",
        "local_user_id": "00000000-0000-4000-8000-000000000002",
    }
    (retained_root / "community_auth.json").write_text(json.dumps(auth), encoding="utf-8")
    (retained_root / "social_session.json").write_text(json.dumps(social), encoding="utf-8")

    assert C._load_auth() is None
    assert C._load_social_session() is None
    assert not anchor_state.exists() or not any(anchor_state.glob("*.json"))
    assert (retained_root / "community_auth.json").exists()
    assert (retained_root / "social_session.json").exists()


def test_retained_cleanup_preserves_refreshed_social_token_for_same_identity(
    tmp_path,
    monkeypatch,
):
    anchor_state = tmp_path / "anchor" / "state"
    electron_root = tmp_path / "electron-user-data"
    selected_root = tmp_path / "target" / "N.E.K.O"
    retained_root = tmp_path / "source" / "N.E.K.O"
    manager = _install_roots(
        monkeypatch,
        anchor_state=anchor_state,
        selected_root=selected_root,
        retained_root=retained_root,
    )
    monkeypatch.setenv("NEKO_USER_DATA_DIR", str(electron_root))
    anchor_state.mkdir(parents=True)
    electron_root.mkdir(parents=True)
    retained_root.mkdir(parents=True)
    retained_auth = {"access_token": "old-token", "local_user_id": USER_ID}
    retained_social = {"token": "old-token", "local_user_id": USER_ID}
    refreshed_social = {"token": "new-token", "local_user_id": USER_ID}
    (anchor_state / "community_auth.json").write_text(
        json.dumps(retained_auth),
        encoding="utf-8",
    )
    (electron_root / "social_session.json").write_text(
        json.dumps(refreshed_social),
        encoding="utf-8",
    )
    (retained_root / "community_auth.json").write_text(
        json.dumps(retained_auth),
        encoding="utf-8",
    )
    (retained_root / "social_session.json").write_text(
        json.dumps(retained_social),
        encoding="utf-8",
    )

    C.prepare_retained_community_state_cleanup(retained_root, config_manager=manager)

    assert json.loads(
        (electron_root / "social_session.json").read_text(encoding="utf-8")
    ) == refreshed_social
    assert not (retained_root / "community_auth.json").exists()
    assert not (retained_root / "social_session.json").exists()


def test_legacy_social_cannot_pair_with_different_canonical_auth(
    tmp_path,
    monkeypatch,
):
    anchor_state = tmp_path / "anchor" / "state"
    target_root = tmp_path / "target" / "N.E.K.O"
    retained_root = tmp_path / "source" / "N.E.K.O"
    _install_roots(
        monkeypatch,
        anchor_state=anchor_state,
        selected_root=target_root,
        retained_root=retained_root,
    )
    anchor_state.mkdir(parents=True)
    retained_root.mkdir(parents=True)
    (anchor_state / "community_auth.json").write_text(
        json.dumps({"access_token": "account-a"}),
        encoding="utf-8",
    )
    legacy_social = retained_root / "social_session.json"
    legacy_social.write_text(json.dumps({"token": "account-b"}), encoding="utf-8")

    assert C._load_social_session() is None
    assert not (anchor_state / "social_session.json").exists()
    assert legacy_social.exists()


@pytest.mark.parametrize("source_pending", ("oauth", "steam"))
def test_completed_target_auth_is_only_a_witness_when_retained_has_pending_only(
    tmp_path,
    monkeypatch,
    source_pending,
):
    anchor_state = tmp_path / "anchor" / "state"
    target_root = tmp_path / "target" / "N.E.K.O"
    retained_root = tmp_path / "source" / "N.E.K.O"
    _install_roots(
        monkeypatch,
        anchor_state=anchor_state,
        selected_root=target_root,
        retained_root=retained_root,
    )
    target_root.mkdir(parents=True)
    retained_root.mkdir(parents=True)
    target_auth = target_root / "community_auth.json"
    target_auth.write_text(json.dumps({"access_token": "stale-target"}), encoding="utf-8")
    if source_pending == "oauth":
        source_file = retained_root / "community_oauth_pending.json"
        source_file.write_text(
            json.dumps(
                {
                    "state": "source-oauth",
                    "code_verifier": "source-verifier",
                    "expires_at": time.time() + 300,
                }
            ),
            encoding="utf-8",
        )
    else:
        source_file = retained_root / "community_steam_pending.json"
        source_file.write_text(
            json.dumps(
                {
                    "state": "source-steam",
                    "code_verifier": "source-verifier",
                    "ts": time.time(),
                }
            ),
            encoding="utf-8",
        )

    assert C._load_auth() is None
    assert not (anchor_state / "community_auth.json").exists()
    assert target_auth.exists()
    assert source_file.exists()


@pytest.mark.parametrize("target_pending", ("oauth", "steam"))
def test_completed_target_pending_is_never_imported_when_retained_has_auth_only(
    tmp_path,
    monkeypatch,
    target_pending,
):
    anchor_state = tmp_path / "anchor" / "state"
    target_root = tmp_path / "target" / "N.E.K.O"
    retained_root = tmp_path / "source" / "N.E.K.O"
    _install_roots(
        monkeypatch,
        anchor_state=anchor_state,
        selected_root=target_root,
        retained_root=retained_root,
    )
    target_root.mkdir(parents=True)
    retained_root.mkdir(parents=True)
    source_auth = retained_root / "community_auth.json"
    source_auth.write_text(json.dumps({"access_token": "source-token"}), encoding="utf-8")
    if target_pending == "oauth":
        target_file = target_root / "community_oauth_pending.json"
        target_file.write_text(
            json.dumps(
                {
                    "state": "target-oauth",
                    "code_verifier": "target-verifier",
                    "expires_at": time.time() + 300,
                }
            ),
            encoding="utf-8",
        )
        assert O._load_oauth_pending()[1] is None
        canonical = anchor_state / "community_oauth_pending.json"
    else:
        target_file = target_root / "community_steam_pending.json"
        target_file.write_text(
            json.dumps(
                {
                    "state": "target-steam",
                    "code_verifier": "target-verifier",
                    "ts": time.time(),
                }
            ),
            encoding="utf-8",
        )
        assert C._consume_steam_pending("target-steam") == (False, None)
        canonical = anchor_state / "community_steam_pending.json"

    assert not canonical.exists()
    assert target_file.exists()
    assert source_auth.exists()


def test_cleaned_or_stale_empty_retained_checkpoint_never_revives_target_credentials(
    tmp_path,
    monkeypatch,
):
    anchor_state = tmp_path / "anchor" / "state"
    target_root = tmp_path / "target" / "N.E.K.O"
    retained_root = tmp_path / "source" / "N.E.K.O"
    manager = _install_roots(
        monkeypatch,
        anchor_state=anchor_state,
        selected_root=target_root,
        retained_root=retained_root,
    )
    manager.committed_selected_root = target_root
    retained_root.mkdir(parents=True)
    (retained_root / "personal-notes.txt").write_text("keep", encoding="utf-8")
    target_root.mkdir(parents=True)
    target_auth = target_root / "community_auth.json"
    target_auth.write_text(json.dumps({"access_token": "stale-target"}), encoding="utf-8")

    assert C._legacy_selected_roots() == [retained_root]
    assert C._load_auth() is None
    assert target_auth.exists()

    monkeypatch.setattr(
        storage_migration_module,
        "load_storage_migration",
        lambda *_args, **_kwargs: {
            "status": "completed",
            "source_root": str(retained_root),
            "target_root": str(target_root),
            "retained_source_mode": "cleaned",
        },
    )
    assert C._legacy_selected_roots() == []
    assert C._load_auth() is None
    assert target_auth.exists()


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


def test_target_unlink_failure_preserves_canonical_login_for_retry(tmp_path, monkeypatch):
    anchor_state = tmp_path / "anchor" / "state"
    selected_root = tmp_path / "target" / "N.E.K.O"
    manager = _install_roots(
        monkeypatch,
        anchor_state=anchor_state,
        selected_root=selected_root,
    )
    manager.committed_selected_root = selected_root
    anchor_state.mkdir(parents=True)
    selected_root.mkdir(parents=True)
    canonical_auth = anchor_state / "community_auth.json"
    canonical_social = anchor_state / "social_session.json"
    target_auth = selected_root / "community_auth.json"
    target_social = selected_root / "social_session.json"
    canonical_auth.write_text(json.dumps({"access_token": "active"}), encoding="utf-8")
    canonical_social.write_text(json.dumps({"token": "active"}), encoding="utf-8")
    target_auth.write_text(json.dumps({"access_token": "old"}), encoding="utf-8")
    target_social.write_text(json.dumps({"token": "old"}), encoding="utf-8")
    original_unlink = Path.unlink

    def _deny_target_auth(path, *args, **kwargs):
        if path == target_auth:
            raise PermissionError("target credential is busy")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", _deny_target_auth)

    assert C._clear_auth() is False
    assert canonical_auth.exists()
    assert canonical_social.exists()
    assert target_auth.exists()
    assert target_social.exists()

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
    with pytest.raises(OSError, match="credential"):
        C.prepare_retained_community_state_cleanup(
            retained_root,
            config_manager=SimpleNamespace(local_state_dir=anchor_state),
        )
    assert legacy.exists()


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


def test_anchor_permission_failure_keeps_last_legacy_credential_usable(
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
    legacy = retained_root / "community_auth.json"
    legacy.write_text(json.dumps({"access_token": "only-copy"}), encoding="utf-8")
    monkeypatch.setattr(
        C,
        "_write_private_json_no_replace",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(PermissionError("anchor denied")),
    )

    assert C._load_auth() == {"access_token": "only-copy"}
    assert legacy.exists()
    assert not (anchor_state / "community_auth.json").exists()


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


def test_steam_pending_consumption_removes_all_identical_accepted_copies(tmp_path, monkeypatch):
    anchor_state = tmp_path / "anchor" / "state"
    selected_root = tmp_path / "target" / "N.E.K.O"
    retained_root = tmp_path / "source" / "N.E.K.O"
    _install_roots(
        monkeypatch,
        anchor_state=anchor_state,
        selected_root=selected_root,
        retained_root=retained_root,
    )
    payload = {
        "ts": time.time(),
        "state": "steam-state",
        "code_verifier": "steam-verifier",
    }
    for root in (selected_root, retained_root):
        root.mkdir(parents=True, exist_ok=True)
        (root / "community_steam_pending.json").write_text(
            json.dumps(payload),
            encoding="utf-8",
        )

    assert C._consume_steam_pending("steam-state") == (True, "steam-verifier")
    assert C._consume_steam_pending("steam-state") == (False, None)
    assert not (anchor_state / "community_steam_pending.json").exists()
    assert not (selected_root / "community_steam_pending.json").exists()
    assert not (retained_root / "community_steam_pending.json").exists()


def test_steam_pending_can_claim_legacy_when_anchor_publish_fails(tmp_path, monkeypatch):
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
    legacy = retained_root / "community_steam_pending.json"
    legacy.write_text(
        json.dumps(
            {
                "ts": time.time(),
                "state": "steam-state",
                "code_verifier": "steam-verifier",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        C,
        "_write_private_json_no_replace",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(PermissionError("anchor denied")),
    )

    assert C._consume_steam_pending("steam-state") == (True, "steam-verifier")
    assert not legacy.exists()


def test_steam_pending_reports_failure_when_accepted_copy_cannot_be_deleted(tmp_path, monkeypatch):
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
    legacy = retained_root / "community_steam_pending.json"
    legacy.write_text(
        json.dumps({"ts": time.time(), "state": "steam-state"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        C,
        "_write_private_json_no_replace",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(PermissionError("anchor denied")),
    )
    real_unlink = Path.unlink

    def _deny_legacy_unlink(path, *args, **kwargs):
        if path == legacy:
            raise PermissionError("legacy denied")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", _deny_legacy_unlink)

    assert C._consume_steam_pending("steam-state") == (False, None)
    assert legacy.exists()


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


def test_private_inventory_allows_only_a_proven_orphan_lock_for_explicit_cleanup(
    tmp_path,
    monkeypatch,
):
    retained_root = tmp_path / "retained" / "N.E.K.O"
    retained_root.mkdir(parents=True)
    orphan_pid = 999999
    (retained_root / "social_session.json.lock").write_text(
        json.dumps({
            "schema_version": 2,
            "token": f"{orphan_pid}:orphan",
            "pid": orphan_pid,
            "start_token": "old",
            "start_token_scheme": "test",
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        private_state,
        "probe_social_lock_process",
        lambda pid: ("orphaned", "", "test") if pid == orphan_pid else ("unknown", "", ""),
    )

    inventory = probe_retained_community_state(retained_root)

    assert inventory.state == "orphaned_lock"
    assert inventory.orphaned_social_lock is True
    assert inventory.active_social_lock is False
    assert inventory.cleanup_blocked is False


def test_private_inventory_keeps_a_pc_orphan_blocked_for_backend_cleanup(
    tmp_path,
    monkeypatch,
):
    retained_root = tmp_path / "retained" / "N.E.K.O"
    retained_root.mkdir(parents=True)
    orphan_pid = 999999
    (retained_root / "social_session.json.lock").write_text(
        json.dumps({
            "schema_version": 2,
            "owner_kind": "pc",
            "token": f"{orphan_pid}:pc-orphan",
            "pid": orphan_pid,
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        private_state,
        "probe_social_lock_process",
        lambda pid: ("orphaned", "", "test") if pid == orphan_pid else ("unknown", "", ""),
    )

    inventory = probe_retained_community_state(retained_root)

    assert inventory.state == "active_lock"
    assert inventory.cleanup_blocked is True
    assert inventory.orphaned_social_lock is False


def test_metadata_only_private_inventory_never_probes_a_lock_process(
    tmp_path,
    monkeypatch,
):
    retained_root = tmp_path / "retained" / "N.E.K.O"
    retained_root.mkdir(parents=True)
    (retained_root / "social_session.json.lock").write_text(
        json.dumps({"token": "123:owner", "pid": 123}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        private_state,
        "probe_social_lock_process",
        lambda _pid: (_ for _ in ()).throw(AssertionError("must not probe")),
    )

    inventory = probe_retained_community_state(
        retained_root,
        classify_social_lock_process=False,
    )

    assert inventory.state == "active_lock"
    assert inventory.cleanup_blocked is True


@pytest.mark.skipif(
    not HAS_SAFE_DIR_FD or not hasattr(os, "mkfifo") or not hasattr(os, "O_NONBLOCK"),
    reason="POSIX non-blocking dirfd reads are unavailable",
)
@pytest.mark.parametrize("use_dir_fd", (False, True))
def test_retained_snapshot_does_not_block_when_credential_becomes_fifo(
    tmp_path,
    monkeypatch,
    use_dir_fd,
):
    retained_root = tmp_path / "retained" / "N.E.K.O"
    retained_root.mkdir(parents=True)
    credential = retained_root / private_state.COMMUNITY_AUTH_FILENAME
    credential.write_bytes(b"regular-before-open")
    real_open = private_state.os.open
    opened_flags = []
    replaced = False

    def _replace_with_fifo_before_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal replaced
        opened_path = retained_root / path if dir_fd is not None else Path(path)
        if opened_path == credential and not replaced:
            replaced = True
            credential.unlink()
            os.mkfifo(credential)
            opened_flags.append(flags)
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(private_state.os, "open", _replace_with_fifo_before_open)
    outcome = []
    root_fd = (
        real_open(retained_root, os.O_RDONLY | os.O_DIRECTORY)
        if use_dir_fd
        else None
    )

    def _snapshot_raced_credential():
        try:
            private_state.snapshot_retained_community_state(
                retained_root,
                dir_fd=root_fd,
            )
        except BaseException as exc:
            outcome.append(exc)

    worker = threading.Thread(target=_snapshot_raced_credential, daemon=True)
    worker.start()
    worker.join(timeout=1)
    if worker.is_alive():
        # Release a regressed blocking reader so it cannot leak into later tests.
        writer_fd = real_open(credential, os.O_WRONLY | os.O_NONBLOCK)
        os.close(writer_fd)
        worker.join(timeout=1)
    if root_fd is not None:
        os.close(root_fd)

    assert not worker.is_alive(), "opening a raced credential FIFO must be non-blocking"
    assert len(outcome) == 1
    assert isinstance(outcome[0], OSError)
    assert "changed while snapshotting" in str(outcome[0])
    assert opened_flags and opened_flags[0] & os.O_NONBLOCK
    assert opened_flags[0] & os.O_NOFOLLOW


def test_retained_snapshot_rejects_oversized_credential_before_reading(
    tmp_path,
    monkeypatch,
):
    retained_root = tmp_path / "retained"
    retained_root.mkdir()
    credential = retained_root / private_state.COMMUNITY_AUTH_FILENAME
    with credential.open("wb") as handle:
        handle.truncate(
            private_state.COMMUNITY_PRIVATE_STATE_SNAPSHOT_MAX_BYTES + 1
        )

    real_read = private_state.os.read

    def _reject_credential_read(fd, size):
        if os.path.samestat(os.fstat(fd), credential.stat()):
            pytest.fail("oversized credential must be rejected before reading")
        return real_read(fd, size)

    monkeypatch.setattr(private_state.os, "read", _reject_credential_read)

    with pytest.raises(OSError, match="changed while snapshotting"):
        private_state.snapshot_retained_community_state(retained_root)


def test_retained_snapshot_accepts_bounded_regular_file_short_reads(
    tmp_path,
    monkeypatch,
):
    retained_root = tmp_path / "retained"
    retained_root.mkdir()
    credential = retained_root / private_state.COMMUNITY_AUTH_FILENAME
    content = b'{"credential":"short-read-safe"}'
    credential.write_bytes(content)
    real_read = private_state.os.read

    def short_read(fd, size):
        return real_read(fd, min(size, 3))

    monkeypatch.setattr(private_state.os, "read", short_read)

    snapshot = private_state.snapshot_retained_community_state(retained_root)

    assert snapshot == {
        private_state.COMMUNITY_AUTH_FILENAME: hashlib.sha256(content).hexdigest()
    }


def test_social_lock_snapshot_rejects_same_inode_rewrite_during_read(
    tmp_path,
    monkeypatch,
):
    lock = tmp_path / private_state.SOCIAL_SESSION_LOCK_FILENAME
    original = json.dumps(
        {"pid": 123, "start_token": "old", "start_token_scheme": "test"},
        sort_keys=True,
    ).encode("utf-8")
    replacement = json.dumps(
        {"pid": 123, "start_token": "new", "start_token_scheme": "test"},
        sort_keys=True,
    ).encode("utf-8")
    assert len(original) == len(replacement)
    lock.write_bytes(original)
    old_timestamp = time.time() - 60
    os.utime(lock, (old_timestamp, old_timestamp))
    original_identity = lock.stat()
    real_read = private_state.os.read
    replaced = False

    def rewrite_after_read(fd, size):
        nonlocal replaced
        chunk = real_read(fd, size)
        if chunk and not replaced:
            replaced = True
            lock.write_bytes(replacement)
            os.utime(lock, (old_timestamp, old_timestamp))
        return chunk

    monkeypatch.setattr(private_state.os, "read", rewrite_after_read)
    monkeypatch.setattr(
        private_state,
        "classify_social_lock_owner",
        lambda *_args, **_kwargs: pytest.fail("an unstable lock must not be classified"),
    )

    state, owner = private_state.read_social_lock_owner_snapshot(lock)

    assert replaced is True
    assert os.path.samestat(original_identity, lock.stat())
    assert (state, owner) == (private_state.SOCIAL_LOCK_OWNER_UNKNOWN, None)


def test_retained_snapshot_rejects_same_inode_rewrite_during_read(
    tmp_path,
    monkeypatch,
):
    retained_root = tmp_path / "retained"
    retained_root.mkdir()
    credential = retained_root / private_state.COMMUNITY_AUTH_FILENAME
    original = b'{"credential":"old"}'
    replacement = b'{"credential":"new"}'
    credential.write_bytes(original)
    old_timestamp = time.time() - 60
    os.utime(credential, (old_timestamp, old_timestamp))
    original_identity = credential.stat()
    real_read = private_state.os.read
    replaced = False
    rewrite_blocked = False

    def rewrite_after_read(fd, size):
        nonlocal replaced, rewrite_blocked
        chunk = real_read(fd, size)
        if chunk and not replaced:
            replaced = True
            try:
                credential.write_bytes(replacement)
                os.utime(credential, (old_timestamp, old_timestamp))
            except PermissionError:
                # Windows opens private state without FILE_SHARE_WRITE, so an
                # in-place writer is denied instead of being detected later by
                # mutable timestamps (ctime is creation time on Windows).
                rewrite_blocked = True
        return chunk

    monkeypatch.setattr(private_state.os, "read", rewrite_after_read)

    if os.name == "nt":
        snapshot = private_state.snapshot_retained_community_state(retained_root)
        assert rewrite_blocked is True
        assert snapshot == {
            private_state.COMMUNITY_AUTH_FILENAME: hashlib.sha256(original).hexdigest()
        }
    else:
        with pytest.raises(OSError, match="changed while snapshotting"):
            private_state.snapshot_retained_community_state(retained_root)

    assert replaced is True
    assert os.path.samestat(original_identity, credential.stat())


@pytest.mark.skipif(
    not hasattr(os, "mkfifo") or not hasattr(os, "O_NONBLOCK"),
    reason="POSIX non-blocking FIFO reads are unavailable",
)
def test_social_lock_snapshot_does_not_block_when_lock_becomes_fifo(
    tmp_path,
    monkeypatch,
):
    lock = tmp_path / private_state.SOCIAL_SESSION_LOCK_FILENAME
    lock.write_text(
        json.dumps({"token": "123:owner", "pid": 123}),
        encoding="utf-8",
    )
    real_open = private_state.os.open
    opened_flags = []
    replaced = False

    def _replace_with_fifo_before_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal replaced
        if Path(path) == lock and not replaced:
            replaced = True
            lock.unlink()
            os.mkfifo(lock)
            opened_flags.append(flags)
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(private_state.os, "open", _replace_with_fifo_before_open)
    outcome = []

    def _read_raced_lock():
        outcome.append(private_state.read_social_lock_owner_snapshot(lock))

    worker = threading.Thread(target=_read_raced_lock, daemon=True)
    worker.start()
    worker.join(timeout=1)
    if worker.is_alive():
        # Release a regressed blocking reader so it cannot leak into later tests.
        writer_fd = real_open(lock, os.O_WRONLY | os.O_NONBLOCK)
        os.close(writer_fd)
        worker.join(timeout=1)

    assert not worker.is_alive(), "opening a raced social lock FIFO must be non-blocking"
    assert outcome == [(private_state.SOCIAL_LOCK_OWNER_UNKNOWN, None)]
    assert opened_flags and opened_flags[0] & os.O_NONBLOCK
    assert opened_flags[0] & os.O_NOFOLLOW


@pytest.mark.skipif(not HAS_SAFE_DIR_FD, reason="POSIX dirfd reads are unavailable")
def test_retained_snapshot_validates_opened_identity_before_reading(
    tmp_path,
    monkeypatch,
):
    retained_root = tmp_path / "retained" / "N.E.K.O"
    retained_root.mkdir(parents=True)
    credential = retained_root / private_state.COMMUNITY_AUTH_FILENAME
    credential.write_bytes(b"original")
    real_open = private_state.os.open
    replaced = False

    def _replace_with_regular_file_before_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal replaced
        if path == credential.name and dir_fd is not None and not replaced:
            replaced = True
            credential.unlink()
            credential.write_bytes(b"replacement")
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(
        private_state.os,
        "open",
        _replace_with_regular_file_before_open,
    )
    monkeypatch.setattr(
        private_state.os,
        "read",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("a replaced credential must not be read")
        ),
    )
    root_fd = real_open(retained_root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(OSError, match="changed while snapshotting"):
            private_state.snapshot_retained_community_state(
                retained_root,
                dir_fd=root_fd,
            )
    finally:
        os.close(root_fd)


def test_social_lock_owner_identity_reuse_and_unknown_stay_three_state():
    owner = {
        "pid": 123,
        "start_token": "old-start",
        "start_token_scheme": "test-scheme",
    }
    assert private_state.classify_social_lock_owner(
        owner,
        process_probe=lambda _pid: ("active", "new-start", "test-scheme"),
    ) == "orphaned"
    assert private_state.classify_social_lock_owner(
        owner,
        process_probe=lambda _pid: ("active", "old-start", "test-scheme"),
    ) == "active"
    assert private_state.classify_social_lock_owner(
        owner,
        process_probe=lambda _pid: ("unknown", "", "test-scheme"),
    ) == "unknown"


def test_current_platform_social_lock_process_probe_reports_a_real_start_identity():
    state, start_token, scheme = private_state.probe_social_lock_process(os.getpid())

    assert state == "active"
    assert start_token
    if private_state.sys.platform.startswith("linux"):
        assert scheme == "linux-proc-start-v1"
    elif private_state.sys.platform == "win32":
        assert scheme == "windows-powershell-start-v1"
    elif private_state.sys.platform == "darwin":
        assert scheme == "darwin-ps-lstart-v1"


def test_windows_social_lock_probe_hides_powershell(monkeypatch):
    captured = {}

    class _Result:
        returncode = 0
        stdout = "2026-01-01T00:00:00.0000000Z\n"
        stderr = ""

    monkeypatch.setattr(private_state.sys, "platform", "win32")
    monkeypatch.setattr(private_state.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(
        private_state.subprocess,
        "run",
        lambda *args, **kwargs: captured.update(kwargs) or _Result(),
    )

    state, token, scheme = private_state.probe_social_lock_process(123)

    assert state == "active"
    assert token
    assert scheme == "windows-powershell-start-v1"
    assert captured["creationflags"] == 0x08000000


@pytest.mark.parametrize(
    ("platform", "scheme"),
    (
        ("win32", "windows-powershell-start-v1"),
        ("darwin", "darwin-ps-lstart-v1"),
    ),
)
def test_platform_social_lock_probe_maps_decode_failure_to_unknown(
    monkeypatch,
    platform,
    scheme,
):
    def _raise_decode_error(*_args, **_kwargs):
        raise UnicodeDecodeError("platform", b"\xff", 0, 1, "invalid output")

    monkeypatch.setattr(private_state.sys, "platform", platform)
    monkeypatch.setattr(private_state.subprocess, "run", _raise_decode_error)

    assert private_state.probe_social_lock_process(123) == ("unknown", "", scheme)


def test_backend_social_lock_caches_its_own_process_identity(monkeypatch):
    calls = []
    monkeypatch.setattr(C, "_SOCIAL_LOCK_OWNER_IDENTITY", None)
    monkeypatch.setattr(
        C,
        "probe_social_lock_process",
        lambda pid: calls.append(pid) or ("active", "stable-start", "test-scheme"),
    )

    first = C._current_social_lock_record(f"{os.getpid()}:first")
    second = C._current_social_lock_record(f"{os.getpid()}:second")

    assert calls == [os.getpid()]
    assert first["start_token"] == second["start_token"] == "stable-start"


def test_social_lock_is_not_published_until_its_complete_record_is_durable(
    tmp_path,
    monkeypatch,
):
    session = tmp_path / "social_session.json"
    lock = Path(f"{session}.lock")
    observed_payload = None

    def _fail_before_publish(source, target):
        nonlocal observed_payload
        observed_payload = json.loads(Path(source).read_text(encoding="utf-8"))
        assert target == lock
        assert not lock.exists()
        raise OSError("simulated publish interruption")

    monkeypatch.setattr(C, "publish_without_replacing", _fail_before_publish)
    with pytest.raises(OSError, match="publish interruption"):
        with C._social_session_lock(session):
            pass

    assert observed_payload["schema_version"] == 2
    assert observed_payload["token"].startswith(f"{os.getpid()}:")
    assert observed_payload["pid"] == os.getpid()
    assert not lock.exists()
    assert not list(tmp_path.glob("*.tmp"))


def test_social_lock_publish_succeeds_when_platform_has_no_fchmod(tmp_path, monkeypatch):
    session = tmp_path / "social_session.json"
    lock = Path(f"{session}.lock")
    monkeypatch.delattr(C.os, "fchmod", raising=False)

    with C._social_session_lock(session):
        payload = json.loads(lock.read_text(encoding="utf-8"))
        assert payload["pid"] == os.getpid()

    assert not lock.exists()
    assert not list(tmp_path.glob("*.tmp"))


def test_social_lock_windows_delete_delegates_to_the_verified_handle(
    tmp_path,
    monkeypatch,
):
    lock = tmp_path / "social_session.json.lock"
    raw = b'{"token":"verified-owner"}'
    lock.write_bytes(raw)
    metadata = lock.stat()
    fingerprint = C._social_lock_fingerprint(raw)
    fd = os.open(lock, os.O_RDONLY)
    observed = {}

    def _snapshot(path, *, dir_fd=None, delete_access=False):
        observed["snapshot"] = (path, dir_fd, delete_access)
        return fd, metadata, fingerprint, None

    def _delete_handle(opened_fd):
        observed["deleted_fd"] = opened_fd

    with monkeypatch.context() as patch:
        patch.setattr(C.os, "name", "nt")
        patch.setattr(C, "_open_social_lock_snapshot", _snapshot)
        patch.setattr(C, "_delete_windows_social_lock_handle", _delete_handle)
        patch.setattr(
            C.os,
            "unlink",
            lambda *_args, **_kwargs: pytest.fail("Windows must not fall back to path unlink"),
        )
        assert C._unlink_social_lock_if_unchanged(
            lock,
            metadata,
            fingerprint,
        )

    assert observed == {
        "snapshot": (lock, None, True),
        "deleted_fd": fd,
    }
    with pytest.raises(OSError):
        os.fstat(fd)
    assert lock.exists(), "the fake handle deleter intentionally leaves the fixture in place"


def test_windows_social_lock_handle_contract_is_delete_shared_and_reparse_safe():
    dispatch_source = inspect.getsource(C._open_social_lock_fd)
    source = inspect.getsource(C._open_windows_social_lock_fd)
    delete_source = inspect.getsource(C._delete_windows_social_lock_handle)

    assert C._WINDOWS_DELETE_ACCESS == 0x00010000
    assert C._WINDOWS_FILE_SHARE_DELETE == 0x00000004
    assert C._WINDOWS_FILE_FLAG_OPEN_REPARSE_POINT == 0x00200000
    assert C._WINDOWS_FILE_DISPOSITION_INFO_CLASS == 4
    assert C._WINDOWS_ERROR_SHARING_VIOLATION == 32
    assert 'if os.name == "nt"' in dispatch_source
    assert "_open_windows_social_lock_fd" in dispatch_source
    assert "delete_access=delete_access" in dispatch_source
    assert "desired_access |= _WINDOWS_DELETE_ACCESS" in source
    assert "| _WINDOWS_FILE_SHARE_DELETE" in source
    assert "| _WINDOWS_FILE_FLAG_OPEN_REPARSE_POINT" in source
    assert "_WINDOWS_FILE_DISPOSITION_INFO_CLASS" in delete_source
    assert '("DeleteFile", wintypes.BOOLEAN)' in delete_source
    assert "_SocialLockBusyError" in source


@pytest.mark.skipif(os.name != "nt", reason="Windows sharing modes are unavailable")
def test_windows_social_lock_open_maps_sharing_violation_to_retryable_busy(tmp_path):
    import ctypes
    from ctypes import wintypes

    lock = tmp_path / "social_session.json.lock"
    lock.write_text('{"token":"peer"}', encoding="utf-8")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    exclusive_handle = create_file(
        os.fspath(lock),
        C._WINDOWS_GENERIC_READ,
        0,
        None,
        C._WINDOWS_OPEN_EXISTING,
        C._WINDOWS_FILE_ATTRIBUTE_NORMAL,
        None,
    )
    assert exclusive_handle != wintypes.HANDLE(-1).value
    try:
        with pytest.raises(C._SocialLockBusyError) as error:
            C._open_windows_social_lock_fd(lock, delete_access=False)
        assert error.value.errno == C._WINDOWS_ERROR_SHARING_VIOLATION
    finally:
        kernel32.CloseHandle(exclusive_handle)


@pytest.mark.skipif(os.name != "nt", reason="Windows handle deletion is unavailable")
def test_windows_social_lock_release_never_uses_path_unlink(tmp_path, monkeypatch):
    session = tmp_path / "social_session.json"
    lock = Path(f"{session}.lock")
    original_unlink = C.os.unlink

    def _reject_lock_path_unlink(path, *args, **kwargs):
        if Path(path) == lock:
            pytest.fail("verified Windows lock must be deleted by its open handle")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(C.os, "unlink", _reject_lock_path_unlink)
    with C._social_session_lock(session):
        assert lock.exists()

    assert not lock.exists()


@pytest.mark.parametrize("replacement_window", ("opening", "after_read"))
def test_social_lock_snapshot_classifies_name_replacement_as_retryable(
    tmp_path,
    monkeypatch,
    replacement_window,
):
    if os.name == "nt" and replacement_window == "after_read":
        pytest.skip("Windows cannot replace a lock file while this test keeps it open")
    lock = tmp_path / "social_session.json.lock"
    replacement = tmp_path / "replacement.lock"
    lock.write_text('{"token":"first"}', encoding="utf-8")
    replacement.write_text('{"token":"second"}', encoding="utf-8")
    original_open = C._open_social_lock_fd
    original_stat = C.os.stat
    replaced = False

    def _open_after_replacement(path, *args, **kwargs):
        nonlocal replaced
        if replacement_window == "opening" and Path(path) == lock and not replaced:
            replaced = True
            replacement.replace(lock)
        return original_open(path, *args, **kwargs)

    stat_calls = 0

    def _stat_before_replacement(path, *args, **kwargs):
        nonlocal replaced, stat_calls
        if Path(path) == lock:
            stat_calls += 1
            if replacement_window == "after_read" and stat_calls == 2:
                replaced = True
                replacement.replace(lock)
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(C, "_open_social_lock_fd", _open_after_replacement)
    monkeypatch.setattr(C.os, "stat", _stat_before_replacement)

    with pytest.raises(C._SocialLockReplacedError):
        C._read_social_lock_snapshot(lock)

    assert replaced is True
    assert json.loads(lock.read_text(encoding="utf-8"))["token"] == "second"


@pytest.mark.parametrize("use_dir_fd", (False, True), ids=("path", "dirfd"))
@pytest.mark.parametrize("race_stage", ("snapshot", "reclaim"))
def test_social_lock_acquisition_retries_name_replacement(
    tmp_path,
    monkeypatch,
    use_dir_fd,
    race_stage,
):
    if use_dir_fd and not HAS_SAFE_DIR_FD:
        pytest.skip("POSIX dirfd locks are unavailable")
    root = tmp_path / "root"
    root.mkdir()
    lock_path = root / "social_session.json.lock"
    lock_path.write_text('{"token":"previous"}', encoding="utf-8")
    monkeypatch.setattr(C, "_SOCIAL_SESSION_LOCK_POLL_SEC", 0)
    raced = False

    if race_stage == "snapshot":
        original_read = C._read_social_lock_snapshot

        def _read_after_replacement(*args, **kwargs):
            nonlocal raced
            if not raced:
                raced = True
                lock_path.unlink()
                raise C._SocialLockReplacedError("simulated name replacement")
            return original_read(*args, **kwargs)

        monkeypatch.setattr(C, "_read_social_lock_snapshot", _read_after_replacement)
    else:
        monkeypatch.setenv("NEKO_LAUNCHER_SINGLE_INSTANCE_PROVEN", "test-owner")

        def _reclaim_after_replacement(*_args, **_kwargs):
            nonlocal raced
            raced = True
            lock_path.unlink()
            raise C._SocialLockReplacedError("simulated reclaim revalidation race")

        monkeypatch.setattr(C, "_reclaim_orphaned_social_lock", _reclaim_after_replacement)

    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY) if use_dir_fd else None
    try:
        lock_context = (
            C._social_session_lock_at(root_fd)
            if use_dir_fd
            else C._social_session_lock(root / "social_session.json")
        )
        with lock_context:
            assert raced is True
            assert json.loads(lock_path.read_text(encoding="utf-8"))["pid"] == os.getpid()
    finally:
        if root_fd is not None:
            os.close(root_fd)

    assert not lock_path.exists()


@pytest.mark.parametrize("use_dir_fd", (False, True), ids=("path", "dirfd"))
@pytest.mark.parametrize("busy_stage", ("publish", "snapshot"))
def test_social_lock_acquisition_retries_transient_windows_sharing_conflict(
    tmp_path,
    monkeypatch,
    use_dir_fd,
    busy_stage,
):
    if use_dir_fd and not HAS_SAFE_DIR_FD:
        pytest.skip("POSIX dirfd locks are unavailable")
    root = tmp_path / "root"
    root.mkdir()
    lock_path = root / "social_session.json.lock"
    monkeypatch.setattr(C, "_SOCIAL_SESSION_LOCK_POLL_SEC", 0)
    calls = 0

    if busy_stage == "publish":
        original_publish = C._try_publish_social_lock

        def _publish_after_busy(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise C._SocialLockBusyError(32, "simulated sharing violation")
            return original_publish(*args, **kwargs)

        monkeypatch.setattr(C, "_try_publish_social_lock", _publish_after_busy)
    else:
        lock_path.write_text('{"token":"peer"}', encoding="utf-8")
        original_read = C._read_social_lock_snapshot

        def _read_after_busy(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise C._SocialLockBusyError(32, "simulated sharing violation")
            if calls == 2:
                lock_path.unlink(missing_ok=True)
                raise FileNotFoundError(lock_path)
            return original_read(*args, **kwargs)

        monkeypatch.setattr(C, "_read_social_lock_snapshot", _read_after_busy)

    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY) if use_dir_fd else None
    try:
        lock_context = (
            C._social_session_lock_at(root_fd)
            if use_dir_fd
            else C._social_session_lock(root / "social_session.json")
        )
        with lock_context:
            assert calls >= 1
            assert lock_path.exists()
    finally:
        if root_fd is not None:
            os.close(root_fd)

    assert not lock_path.exists()


@pytest.mark.parametrize("use_dir_fd", (False, True), ids=("path", "dirfd"))
def test_social_lock_reclaims_exact_lock_after_own_release_failure(
    tmp_path,
    monkeypatch,
    use_dir_fd,
):
    if use_dir_fd and not HAS_SAFE_DIR_FD:
        pytest.skip("POSIX dirfd locks are unavailable")
    root = tmp_path / "root"
    root.mkdir()
    lock_path = root / "social_session.json.lock"
    original_unlink = C._unlink_social_lock_if_unchanged
    fail_release = True

    def _fail_first_release(*args, **kwargs):
        nonlocal fail_release
        if fail_release:
            fail_release = False
            raise OSError("simulated transient release failure")
        return original_unlink(*args, **kwargs)

    monkeypatch.setattr(C, "_unlink_social_lock_if_unchanged", _fail_first_release)
    monkeypatch.setattr(C, "_SOCIAL_SESSION_LOCK_POLL_SEC", 0)
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY) if use_dir_fd else None
    try:
        first_context = (
            C._social_session_lock_at(root_fd)
            if use_dir_fd
            else C._social_session_lock(root / "social_session.json")
        )
        with first_context:
            first_token = json.loads(lock_path.read_text(encoding="utf-8"))["token"]
        assert lock_path.exists(), "the injected release failure must leave the exact lock"

        second_context = (
            C._social_session_lock_at(root_fd)
            if use_dir_fd
            else C._social_session_lock(root / "social_session.json")
        )
        with second_context:
            second_token = json.loads(lock_path.read_text(encoding="utf-8"))["token"]
            assert second_token != first_token
    finally:
        if root_fd is not None:
            os.close(root_fd)
        with C._SOCIAL_LOCK_RECOVERY_MUTEX:
            C._SOCIAL_LOCK_ABANDONED_OWNERSHIP.clear()

    assert not lock_path.exists()


def test_social_lock_reclaims_own_published_lock_after_verification_failure(
    tmp_path,
    monkeypatch,
):
    session = tmp_path / "social_session.json"
    lock_path = Path(f"{session}.lock")
    original_read = C._read_social_lock_snapshot
    read_calls = 0

    def _fail_initial_verification(*args, **kwargs):
        nonlocal read_calls
        read_calls += 1
        if read_calls <= 2:
            raise OSError("simulated unavailable published lock")
        return original_read(*args, **kwargs)

    monkeypatch.setattr(C, "_read_social_lock_snapshot", _fail_initial_verification)
    with pytest.raises(OSError, match="unavailable published lock"):
        with C._social_session_lock(session):
            pass
    assert lock_path.exists()

    try:
        with C._social_session_lock(session):
            assert json.loads(lock_path.read_text(encoding="utf-8"))["pid"] == os.getpid()
    finally:
        with C._SOCIAL_LOCK_RECOVERY_MUTEX:
            C._SOCIAL_LOCK_ABANDONED_OWNERSHIP.clear()

    assert not lock_path.exists()


def test_orphan_reclaim_publication_failure_does_not_deadlock_recovery_mutex(
    tmp_path,
    monkeypatch,
):
    recovery_mutex = C._SOCIAL_LOCK_RECOVERY_MUTEX
    recovery_mutex.acquire()
    try:
        reacquired = recovery_mutex.acquire(blocking=False)
        if reacquired:
            recovery_mutex.release()
    finally:
        recovery_mutex.release()
    assert reacquired, "orphan publication bookkeeping re-enters this mutex"

    lock_path = tmp_path / "social_session.json.lock"
    lock_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "owner_kind": "neko",
                "token": "999999:orphaned-owner",
                "pid": 999999,
                "created_at": 1,
            }
        ),
        encoding="utf-8",
    )
    expected_metadata, expected_fingerprint, expected_owner = (
        C._read_social_lock_snapshot(lock_path)
    )
    original_read = C._read_social_lock_snapshot
    read_calls = 0

    def _fail_replacement_verification(*args, **kwargs):
        nonlocal read_calls
        read_calls += 1
        # Revalidate the orphan first, then simulate Windows sharing conflicts
        # both while verifying and while trying to retire the published replacement.
        if read_calls in {2, 3}:
            raise C._SocialLockBusyError(32, "simulated sharing violation")
        return original_read(*args, **kwargs)

    @contextmanager
    def _recovery_authority():
        yield

    monkeypatch.setattr(C, "_read_social_lock_snapshot", _fail_replacement_verification)
    monkeypatch.setattr(
        C,
        "classify_social_lock_owner",
        lambda _owner: C.SOCIAL_LOCK_OWNER_ORPHANED,
    )
    monkeypatch.setattr(
        C.single_instance,
        "try_acquire_auxiliary_lock",
        lambda _name: _recovery_authority(),
    )

    try:
        with pytest.raises(C._SocialLockBusyError, match="sharing violation"):
            C._reclaim_orphaned_social_lock(
                lock_path,
                expected_metadata,
                expected_fingerprint,
                expected_owner,
                "current:replacement",
            )
        assert read_calls == 3
        location = C._social_lock_location_key(lock_path)
        with recovery_mutex:
            assert C._SOCIAL_LOCK_ABANDONED_OWNERSHIP[location] == (
                None,
                "token:current:replacement",
            )
    finally:
        with recovery_mutex:
            C._SOCIAL_LOCK_ABANDONED_OWNERSHIP.pop(
                C._social_lock_location_key(lock_path),
                None,
            )


def test_social_lock_never_reclaims_replacement_using_abandoned_ownership(tmp_path, monkeypatch):
    session = tmp_path / "social_session.json"
    lock_path = Path(f"{session}.lock")
    original_unlink = C._unlink_social_lock_if_unchanged
    fail_release = True

    def _fail_first_release(*args, **kwargs):
        nonlocal fail_release
        if fail_release:
            fail_release = False
            raise OSError("simulated transient release failure")
        return original_unlink(*args, **kwargs)

    monkeypatch.setattr(C, "_unlink_social_lock_if_unchanged", _fail_first_release)
    with C._social_session_lock(session):
        pass
    lock_path.unlink()
    replacement = {"token": "999999:replacement", "pid": 999999}
    lock_path.write_text(json.dumps(replacement), encoding="utf-8")
    monkeypatch.setattr(C, "_SOCIAL_SESSION_LOCK_TIMEOUT_SEC", 0)
    try:
        with pytest.raises(TimeoutError), C._social_session_lock(session):
            pass
    finally:
        with C._SOCIAL_LOCK_RECOVERY_MUTEX:
            C._SOCIAL_LOCK_ABANDONED_OWNERSHIP.clear()

    assert json.loads(lock_path.read_text(encoding="utf-8")) == replacement


@pytest.mark.parametrize("use_dir_fd", (False, True), ids=("path", "dirfd"))
def test_social_lock_acquisition_does_not_retry_unsafe_read_error(
    tmp_path,
    monkeypatch,
    use_dir_fd,
):
    if use_dir_fd and not HAS_SAFE_DIR_FD:
        pytest.skip("POSIX dirfd locks are unavailable")
    root = tmp_path / "root"
    root.mkdir()
    lock_path = root / "social_session.json.lock"
    lock_path.write_text('{"token":"previous"}', encoding="utf-8")
    calls = 0

    def _unsafe_read(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise OSError("unsafe social session lock")

    monkeypatch.setattr(C, "_read_social_lock_snapshot", _unsafe_read)
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY) if use_dir_fd else None
    try:
        lock_context = (
            C._social_session_lock_at(root_fd)
            if use_dir_fd
            else C._social_session_lock(root / "social_session.json")
        )
        with pytest.raises(OSError, match="unsafe social session lock"), lock_context:
            pass
    finally:
        if root_fd is not None:
            os.close(root_fd)

    assert calls == 1
    assert lock_path.exists()


def test_social_lock_name_replacement_obeys_busy_timeout(tmp_path, monkeypatch):
    session = tmp_path / "social_session.json"
    lock_path = Path(f"{session}.lock")
    lock_path.write_text('{"token":"previous"}', encoding="utf-8")
    monkeypatch.setattr(C, "_SOCIAL_SESSION_LOCK_TIMEOUT_SEC", 0)

    def _replace_during_snapshot(*_args, **_kwargs):
        raise C._SocialLockReplacedError("simulated name replacement")

    monkeypatch.setattr(C, "_read_social_lock_snapshot", _replace_during_snapshot)

    with pytest.raises(TimeoutError, match="social session lock is busy"):
        with C._social_session_lock(session):
            pass

    assert lock_path.exists()


@pytest.mark.parametrize("operation", ("load", "cleanup"))
def test_social_legacy_lock_is_held_through_publish_verify_and_delete(
    tmp_path,
    monkeypatch,
    operation,
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
    legacy = retained_root / "social_session.json"
    canonical = anchor_state / "social_session.json"
    before = {"token": "token-a"}
    concurrent = {"token": "token-b"}
    legacy.write_text(json.dumps(before), encoding="utf-8")

    attempted = threading.Event()
    finished = threading.Event()
    writer_errors: list[BaseException] = []

    def _writer():
        attempted.set()
        try:
            C._write_social_session_record(legacy, concurrent)
        except BaseException as exc:  # noqa: BLE001
            writer_errors.append(exc)
        finally:
            finished.set()

    writer = threading.Thread(target=_writer)
    original_read = C._read_private_json_state
    writer_started = False

    def _read_with_writer(path):
        nonlocal writer_started
        result = original_read(path)
        if path == legacy and canonical.exists() and not writer_started:
            writer_started = True
            writer.start()
            assert attempted.wait(timeout=1)
            time.sleep(0.05)
            assert not finished.is_set(), "writer must remain blocked until deletion commits"
        return result

    monkeypatch.setattr(C, "_read_private_json_state", _read_with_writer)
    if operation == "load":
        assert C._load_social_session() == before
    else:
        C.prepare_retained_community_state_cleanup(
            retained_root,
            config_manager=SimpleNamespace(local_state_dir=anchor_state),
        )

    writer.join(timeout=2)
    assert finished.is_set()
    assert writer_errors == []
    assert json.loads(canonical.read_text(encoding="utf-8")) == before
    assert json.loads(legacy.read_text(encoding="utf-8")) == concurrent


@pytest.mark.skipif(not HAS_SAFE_DIR_FD, reason="POSIX dirfd cleanup is unavailable")
def test_dirfd_cleanup_holds_legacy_social_lock_through_delete(tmp_path, monkeypatch):
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
    legacy = retained_root / "social_session.json"
    canonical = anchor_state / "social_session.json"
    before = {"token": "token-a"}
    concurrent = {"token": "token-b"}
    legacy.write_text(json.dumps(before), encoding="utf-8")
    attempted = threading.Event()
    finished = threading.Event()

    def _writer():
        attempted.set()
        C._write_social_session_record(legacy, concurrent)
        finished.set()

    writer = threading.Thread(target=_writer)
    original_read = C._read_private_json_state_at
    writer_started = False

    def _read_with_writer(dir_fd, filename):
        nonlocal writer_started
        result = original_read(dir_fd, filename)
        if filename == "social_session.json" and canonical.exists() and not writer_started:
            writer_started = True
            writer.start()
            assert attempted.wait(timeout=1)
            time.sleep(0.05)
            assert not finished.is_set(), "writer must remain blocked until fd-relative delete commits"
        return result

    monkeypatch.setattr(C, "_read_private_json_state_at", _read_with_writer)
    root_fd = os.open(retained_root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        C.prepare_retained_community_state_cleanup(
            retained_root,
            config_manager=SimpleNamespace(local_state_dir=anchor_state),
            retained_dir_fd=root_fd,
        )
    finally:
        os.close(root_fd)

    writer.join(timeout=2)
    assert finished.is_set()
    assert json.loads(canonical.read_text(encoding="utf-8")) == before
    assert json.loads(legacy.read_text(encoding="utf-8")) == concurrent


def test_social_lock_deduplicates_two_aliases_of_same_physical_parent(tmp_path, monkeypatch):
    physical_parent = tmp_path / "Physical"
    physical_parent.mkdir()
    first_alias = tmp_path / "first"
    second_alias = tmp_path / "SECOND"
    try:
        first_alias.symlink_to(physical_parent, target_is_directory=True)
        second_alias.symlink_to(physical_parent, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("filesystem aliases are unavailable on this platform")
    entered: list[Path] = []

    @contextmanager
    def _record_lock(path):
        entered.append(path)
        yield

    monkeypatch.setattr(C, "_social_session_lock", _record_lock)
    with C._social_session_locks(
        [
            first_alias / "social_session.json",
            second_alias / "social_session.json",
        ]
    ):
        pass

    assert len(entered) == 1


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


def test_backend_never_reclaims_a_pc_owned_orphan_lock(tmp_path, monkeypatch):
    session = tmp_path / "social_session.json"
    lock_path = Path(f"{session}.lock")
    orphan_pid = 999999
    record = {
        "schema_version": 2,
        "owner_kind": "pc",
        "token": f"{orphan_pid}:pc-orphan",
        "pid": orphan_pid,
    }
    lock_path.write_text(json.dumps(record), encoding="utf-8")
    monkeypatch.setenv("NEKO_LAUNCHER_SINGLE_INSTANCE_PROVEN", "test-owner")
    monkeypatch.setattr(C, "_SOCIAL_SESSION_LOCK_TIMEOUT_SEC", 0)
    monkeypatch.setattr(
        C,
        "classify_social_lock_owner",
        lambda _owner: "orphaned",
    )

    with pytest.raises(TimeoutError), C._social_session_lock(session):
        pass

    assert json.loads(lock_path.read_text(encoding="utf-8")) == record
