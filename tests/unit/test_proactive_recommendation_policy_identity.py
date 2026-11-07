"""Real supported credential writes retire in-flight recommendation views."""

from contextlib import contextmanager
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from main_routers import card_drop_router as credentials
from services.chat_sync import host
from services.proactive_feedback.policy import PolicyRegistry


OWNER = {"key": "synthetic-owner", "user_id": None, "community": None}
USER_ID = "11111111-1111-4111-8111-111111111111"


@pytest.fixture
def paths(monkeypatch, tmp_path):
    auth = tmp_path / "community_auth.json"
    social = tmp_path / "social_session.json"
    monkeypatch.setattr(credentials, "_auth_path", lambda: auth)
    monkeypatch.setattr(credentials, "_social_session_path", lambda: social)
    monkeypatch.setattr(credentials, "_social_session_paths", lambda: [social])
    monkeypatch.setattr(credentials, "_clear_native_delegates", MagicMock())
    return auth, social


def write(kind, auth, social):
    if kind == "auth":
        return credentials._save_auth_unlocked({"access_token": "synthetic"})
    if kind == "social":
        return credentials._save_social_session_unlocked(
            social, "https://example.invalid", "synthetic", None,
            local_user_id=USER_ID, auth_source="oauth",
        )
    credentials._write_social_session_record(social, {"token": "synthetic"})
    return True


@pytest.mark.parametrize("kind", ["auth", "social", "record"])
def test_committed_credential_write_retires_view_without_new_policy_request(paths, kind):
    auth, social = paths
    token = host.saved_session_generation()
    value = PolicyRegistry()
    view = value.publish("synthetic-db", OWNER, "a" * 32, {})
    allowed = view.guard(["news"])
    assert allowed()
    assert write(kind, auth, social)
    committed = json.loads((auth if kind == "auth" else social).read_text(encoding="utf-8"))
    assert (committed.get("access_token") or committed.get("token")) == "synthetic"
    assert host.saved_session_generation() is not token
    # No registry observe/publish or second proactive request occurs here.
    assert not allowed()


@pytest.mark.parametrize("kind", ["auth", "social", "record"])
def test_failed_write_keeps_identity_when_nothing_was_committed(paths, monkeypatch, kind):
    auth, social = paths
    token = host.saved_session_generation()

    def fail(*_args):
        raise OSError("synthetic pre-commit write failure")

    monkeypatch.setattr(credentials, "_write_private_json", fail)
    if kind == "record":
        with pytest.raises(OSError):
            write(kind, auth, social)
    else:
        assert write(kind, auth, social) is False
    assert not auth.exists() and not social.exists()
    assert host.saved_session_generation() is token


@pytest.mark.parametrize("partial_failure", [False, True])
def test_real_logout_clear_invalidates_before_first_unlink(paths, monkeypatch, partial_failure):
    auth, social = paths
    auth.write_text('{"access_token":"synthetic"}', encoding="utf-8")
    social.write_text('{"token":"synthetic"}', encoding="utf-8")
    token = host.saved_session_generation()
    value = PolicyRegistry()
    allowed = value.publish("synthetic-db", OWNER, "a" * 32, {}).guard(["music"])
    original_unlink = Path.unlink
    attempted = []

    def unlink(path, *args, **kwargs):
        if path in {auth, social}:
            attempted.append(path)
            assert host.saved_session_generation() is not token
            assert not allowed()
            if partial_failure and path == social:
                raise PermissionError("synthetic partial clear")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", unlink)
    assert credentials._clear_auth() is (not partial_failure)
    assert attempted == [auth, social]
    assert not auth.exists()
    assert social.exists() is partial_failure
    assert not allowed()


def test_logout_that_cannot_acquire_writer_fence_preserves_identity(paths, monkeypatch):
    auth, social = paths
    auth.write_text("{}", encoding="utf-8")
    social.write_text("{}", encoding="utf-8")
    token = host.saved_session_generation()

    @contextmanager
    def blocked(_paths):
        raise TimeoutError("synthetic busy credential writer")
        yield

    monkeypatch.setattr(credentials, "_social_session_locks", blocked)
    assert credentials._clear_auth() is False
    assert auth.exists() and social.exists()
    assert host.saved_session_generation() is token
