"""Market launch expiry and verified downloads, with no user data or network."""

import hashlib
import json
import threading
from types import SimpleNamespace

import pytest

from services.model_workshop import market
from services.model_workshop.paths import WorkshopError
from tests.fake_clock import patch_module_clock

OPERATION = "1" * 32


@pytest.fixture
def config(tmp_path):
    path = tmp_path / "characters.json"
    data = {"当前猫娘": "A", "猫娘": {
        "A": {"_reserved": {"character_id": "a" * 32}},
        "B": {"_reserved": {"character_id": "b" * 32}},
    }}
    path.write_text(json.dumps(data))
    return SimpleNamespace(app_docs_dir=tmp_path,
                           get_runtime_config_path=lambda _: path,
                           load_characters=lambda **_: json.loads(path.read_text()))


def test_switching_away_and_back_permanently_expires_application(config):
    row = market.Launches().create(config)
    path = config.get_runtime_config_path("characters.json")
    original = path.read_bytes()
    data = json.loads(original); data["当前猫娘"] = "B"
    path.write_text(json.dumps(data)); path.write_bytes(original)
    with pytest.raises(WorkshopError, match="target_changed"):
        row.check(config)


def test_new_root_or_same_name_new_identity_cannot_inherit_launch(config, tmp_path):
    row = market.Launches().create(config)
    data = config.load_characters()
    data["猫娘"]["A"]["_reserved"]["character_id"] = "c" * 32
    with pytest.raises(WorkshopError, match="target_changed"):
        row.check(config, data)
    config.app_docs_dir = tmp_path / "moved"
    with pytest.raises(WorkshopError, match="target_changed"):
        row.check(config)


def test_missing_role_file_fails_closed_without_a_filesystem_error(config):
    row = market.Launches().create(config)
    config.get_runtime_config_path('characters.json').unlink()
    with pytest.raises(WorkshopError, match='target_changed'):
        row.check(config)


def test_invalidated_idle_connections_release_launch_capacity(config):
    launches = market.Launches()
    for _ in range(market.MAX_LAUNCHES * 2):
        row = launches.create(config)
        row.connection.error = 'model_market_target_changed'
    assert launches.create(config).phase == 'browsing'


@pytest.mark.parametrize('change', ['logout', 'account', 'same_account_login'])
def test_account_session_change_revokes_a_connection_before_role_save(config, tmp_path, monkeypatch, change):
    path = tmp_path / 'account.json'
    monkeypatch.setattr(market, '_ACCOUNT_FILE', path)
    value = {'session_id': 'login-a', 'subject': 'account-a', 'client_id': 'desktop', 'auth_url': 'https://auth.test'}
    path.write_text(json.dumps(value))
    row = market.Launches().create(config)
    value['access_token'] = 'refreshed-token'
    path.write_text(json.dumps(value))
    row.check(config)  # Ordinary refresh preserves the existing account session.
    if change == 'logout':
        path.unlink()
    else:
        value['session_id'] = 'login-b'
        if change == 'account': value['subject'] = 'account-b'
        path.write_text(json.dumps(value))
    with pytest.raises(WorkshopError, match='market_auth_required'):
        row.check(config)
    assert row.connection.error == 'market_auth_required'


def test_selection_is_idempotent_and_launch_cannot_be_retargeted(config):
    launches = market.Launches(); row = launches.create(config)
    assert launches.select(config, row.request_id, 1, "a" * 64, 100, OPERATION) == (row, True)
    assert launches.select(config, row.request_id, 1, "a" * 64, 100, OPERATION) == (row, False)
    with pytest.raises(WorkshopError, match="selection_changed"):
        launches.select(config, row.request_id, 2, "a" * 64, 100, OPERATION)


@pytest.mark.parametrize("model_id,digest,size", [(True, "a" * 64, 1), (0, "a" * 64, 1),
    (1, "../escape", 1), (1, "a" * 64, True), (1, "a" * 64, 0),
    (1, "a" * 64, market.BundleLimits().archive_bytes + 1)])
def test_bad_selection_rejected_before_job(config, model_id, digest, size):
    launches = market.Launches(); row = launches.create(config)
    with pytest.raises(WorkshopError, match="invalid_selection"):
        launches.select(config, row.request_id, model_id, digest, size, OPERATION)
    assert row.phase == "browsing" and row.selection is None


def test_launch_and_active_limits_are_bounded(config, monkeypatch):
    launches = market.Launches()
    rows = [launches.create(config) for _ in range(market.MAX_LAUNCHES)]
    with pytest.raises(WorkshopError, match="busy"):
        launches.create(config)
    # Distinct roles exercise the global quota separately from per-role serialization.
    for index, row in enumerate(rows): row.character_id = str(index)
    for row in rows[:2]: launches.select(config, row.request_id, 1, "a" * 64, 1, OPERATION)
    with pytest.raises(WorkshopError, match="busy"):
        launches.select(config, rows[2].request_id, 1, "a" * 64, 1, OPERATION)
    patch_module_clock(monkeypatch, market, monotonic=lambda: rows[0].expires + 1)
    with pytest.raises(WorkshopError, match="expired"):
        rows[0].check(config)


def test_next_choice_keeps_old_receipts_and_requires_local_ready(config):
    """An old request remains idempotent after an explicitly admitted new choice."""
    launches = market.Launches(); first = launches.create(config)
    launches.select(config, first.request_id, 1, "a" * 64, 100, OPERATION)
    first.phase = "saved"
    with pytest.raises(WorkshopError, match="busy"):
        launches.select(config, first.request_id, 2, "b" * 64, 200, "2" * 32)
    first.connection.ready = True  # Native ready acknowledgement is tested through HTTP below.
    second, fresh = launches.select(config, first.request_id, 2, "b" * 64, 200, "2" * 32)
    assert fresh and second is not first and second.connection is first.connection
    assert first.selection == (1, "a" * 64, 100) and first.phase == "saved"
    assert launches.select(config, first.request_id, 1, "a" * 64, 100, OPERATION) == (first, False)
    assert not first.public()["can_select"]
    assert launches.get(first.request_id, OPERATION) is first


def test_multiple_pairings_cannot_replace_one_role_concurrently(config):
    launches = market.Launches()
    first, second = launches.create(config), launches.create(config)
    launches.select(config, first.request_id, 1, "a" * 64, 100, OPERATION)
    first.phase = "installed"
    with pytest.raises(WorkshopError, match="busy"):
        launches.select(config, second.request_id, 2, "b" * 64, 200, "2" * 32)


def test_operation_receipts_stay_bounded_without_replaying_evicted_writes(config, monkeypatch):
    launches = market.Launches(); row = launches.create(config)
    monkeypatch.setattr(market, "MAX_OPERATIONS", 1)
    launches.select(config, row.request_id, 1, "a" * 64, 100, OPERATION)
    row.phase = "failed"; row.connection.ready = True
    assert not row.public()["can_select"]
    with pytest.raises(WorkshopError, match="busy"):
        launches.select(config, row.request_id, 2, "b" * 64, 200, "2" * 32)
    assert launches.get(row.request_id, OPERATION) is row


@pytest.mark.parametrize("chunks,size,digest", [([b"abc"], 4, hashlib.sha256(b"abc").hexdigest()),
    ([b"abc"], 3, "a" * 64), ([b"abcd"], 3, hashlib.sha256(b"abc").hexdigest())])
def test_bad_network_body_never_returns_eof(chunks, size, digest):
    stream = market.PackageStream(chunks, digest, size, threading.Event())
    with pytest.raises(WorkshopError, match="package_changed"):
        while stream.read(1024 * 1024): pass


def test_stream_cancel_deadline_and_success(monkeypatch):
    cancelled = threading.Event()
    stream = market.PackageStream([b"abc"], hashlib.sha256(b"abc").hexdigest(), 3, cancelled)
    assert stream.read(1024) == b"abc"
    assert stream.read(1024) == b"" and stream.read(1024) == b""
    cancelled.set()
    with pytest.raises(WorkshopError, match="cancelled"): stream.read(1024)
    cancelled.clear()
    patch_module_clock(monkeypatch, market, monotonic=lambda: stream.deadline + 1)
    with pytest.raises(WorkshopError, match="timeout"): stream.read(1024)


def test_atomic_account_file_refresh_retries_without_revoking_the_connection(config, monkeypatch):
    value = {"session_id": "a", "subject": "same-account"}
    count = 0
    def read(*args):
        nonlocal count
        count += 1
        if count == 2:
            raise WorkshopError("model_workshop_state_unreadable")
        return value
    monkeypatch.setattr(market, "read_json", read)
    row = market.Launches().create(config)
    row.check(config)
    assert row.connection.error is None
    assert count == 3


@pytest.mark.parametrize('pending', [False, True])
def test_connection_binds_the_first_login_and_accepts_subject_confirmation(config, tmp_path, monkeypatch, pending):
    path = tmp_path / 'auth.json'; monkeypatch.setattr(market, '_ACCOUNT_FILE', path)
    session = {'session_id': 'same-login', 'subject': None, 'client_id': 'desktop', 'auth_url': 'https://auth.test'}
    if pending: path.write_text(json.dumps(session))
    launches = market.Launches(); row = launches.create(config)
    session['subject'] = 'verified-user'; path.write_text(json.dumps(session))
    row.check(config)
    # The first login belongs to the pairing, including later operation objects.
    second, _ = launches.select(config, row.request_id, 1, 'a' * 64, 1, OPERATION)
    assert second.connection is row.connection
    path.unlink()
    with pytest.raises(WorkshopError, match='market_auth_required'): second.check(config)


def test_transient_nonatomic_token_write_does_not_permanently_revoke_pairing(config, tmp_path, monkeypatch):
    path = tmp_path / 'auth.json'; monkeypatch.setattr(market, '_ACCOUNT_FILE', path)
    path.write_text(json.dumps({'session_id': 'login', 'subject': 'user'}))
    row = market.Launches().create(config)
    path.write_text('{')
    with pytest.raises(WorkshopError, match='market_auth_pending'): row.check(config)
    assert row.connection.error is None
    path.write_text(json.dumps({'session_id': 'login', 'subject': 'user'}))
    row.check(config)
