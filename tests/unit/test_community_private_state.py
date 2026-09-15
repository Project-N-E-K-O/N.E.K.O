from __future__ import annotations

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


def test_single_legacy_root_with_mismatched_auth_and_social_tokens_fails_closed(
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

    assert C._load_auth() is None
    assert C._load_social_session() is None
    assert not anchor_state.exists() or not any(anchor_state.glob("*.json"))
    assert (retained_root / "community_auth.json").exists()
    assert (retained_root / "social_session.json").exists()


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
