"""Crash and account-boundary contracts for the local PetCare alias outbox."""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import main_routers.pet_care_role_alias_outbox as outbox


UID = "a" * 32
ACCOUNT_A = "11111111-1111-4111-8111-111111111111"
ACCOUNT_B = "22222222-2222-4222-8222-222222222222"
RETIREMENT = {
    "character_uid": UID,
    "source_revision": f"delete:{UID}",
    "status": "retired",
}


class _Config:
    def __init__(self, root, *, live=True):
        self.local_state_dir = root / "state"
        self.live = live
        self.read_error: Exception | None = None
        self.authoritative_reads = 0

    def ensure_local_state_directory(self):
        self.local_state_dir.mkdir(parents=True, exist_ok=True)
        return True

    def load_characters(self, *, require_authoritative=False):
        assert require_authoritative is True
        self.authoritative_reads += 1
        if self.read_error is not None:
            raise self.read_error
        catgirls = {
            "Role": {"_reserved": {"character_uid": UID}}
        } if self.live else {}
        return {"当前猫娘": "", "猫娘": catgirls}

    @property
    def path(self):
        return self.local_state_dir / "pet_care_role_alias_outbox.json"


@pytest.fixture(autouse=True)
def _clear_process_fences():
    outbox._active_preparations.clear()
    yield
    outbox._active_preparations.clear()


def _saved(config):
    return json.loads(config.path.read_text(encoding="utf-8"))


def test_current_login_without_verified_bind_cannot_create_retirement(tmp_path, monkeypatch):
    config = _Config(tmp_path, live=True)
    monkeypatch.setattr(outbox, "saved_account_id", lambda: ACCOUNT_A)

    outbox.prepare_retirement(config, UID)
    config.live = False
    outbox.finish_retirement(config, UID)

    state = _saved(config)
    assert state["owners"] == {}
    assert state["events"] == {}
    assert outbox.list_ready_retirements(config, ACCOUNT_A) == []


def test_parallel_read_cannot_publish_transient_delete_that_rolls_back(tmp_path):
    config = _Config(tmp_path, live=True)
    outbox.remember_owner(config, UID, ACCOUNT_A)
    outbox.prepare_retirement(config, UID)

    # characters.json can be absent between its first atomic replace and a
    # later transaction rollback. The in-process fence must hide that window.
    config.live = False
    assert outbox.list_ready_retirements(config, ACCOUNT_A) == []
    config.live = True
    outbox.finish_retirement(config, UID)

    assert _saved(config)["events"] == {}


def test_committed_delete_is_account_scoped_and_acknowledged_independently(tmp_path):
    config = _Config(tmp_path, live=True)
    outbox.remember_owner(config, UID, ACCOUNT_A)
    outbox.remember_owner(config, UID, ACCOUNT_B)
    outbox.prepare_retirement(config, UID)
    config.live = False
    outbox.finish_retirement(config, UID)

    assert outbox.list_ready_retirements(config, ACCOUNT_A) == [RETIREMENT]
    assert outbox.list_ready_retirements(config, ACCOUNT_B) == [RETIREMENT]
    assert outbox.acknowledge_retirement(config, ACCOUNT_A, RETIREMENT["source_revision"])
    assert outbox.list_ready_retirements(config, ACCOUNT_A) == []
    assert outbox.list_ready_retirements(config, ACCOUNT_B) == [RETIREMENT]
    assert outbox.acknowledge_retirement(config, ACCOUNT_B, RETIREMENT["source_revision"])
    assert _saved(config) == {"version": 1, "owners": {}, "events": {}}


def test_process_crash_after_config_commit_recovers_prepared_row(tmp_path):
    config = _Config(tmp_path, live=True)
    outbox.remember_owner(config, UID, ACCOUNT_A)
    outbox.prepare_retirement(config, UID)
    config.live = False

    # A process restart loses only the volatile transaction fence. The
    # prepared row and authoritative role absence are enough to recover.
    outbox._active_preparations.clear()
    assert outbox.list_ready_retirements(config, ACCOUNT_A) == [RETIREMENT]
    assert _saved(config)["events"][RETIREMENT["source_revision"]]["phase"] == "ready"


def test_unreadable_character_config_keeps_prepared_row_for_retry(tmp_path):
    config = _Config(tmp_path, live=True)
    outbox.remember_owner(config, UID, ACCOUNT_A)
    outbox.prepare_retirement(config, UID)
    config.read_error = ValueError("corrupt characters.json")

    with pytest.raises(outbox.PetCareRoleAliasOutboxError, match="authoritative"):
        outbox.finish_retirement(config, UID)
    assert _saved(config)["events"][RETIREMENT["source_revision"]]["phase"] == "prepared"

    config.read_error = None
    config.live = False
    assert outbox.list_ready_retirements(config, ACCOUNT_A) == [RETIREMENT]


def test_corrupt_outbox_fails_closed_without_overwrite(tmp_path):
    config = _Config(tmp_path)
    config.ensure_local_state_directory()
    original = "{broken"
    config.path.write_text(original, encoding="utf-8")

    with pytest.raises(outbox.PetCareRoleAliasOutboxError, match="cannot read"):
        outbox.prepare_retirement(config, UID)
    assert config.path.read_text(encoding="utf-8") == original


def test_http_read_requires_same_origin_browser_and_exact_account(
    tmp_path, monkeypatch,
):
    config = _Config(tmp_path, live=True)
    outbox.remember_owner(config, UID, ACCOUNT_A)
    outbox.prepare_retirement(config, UID)
    config.live = False
    outbox.finish_retirement(config, UID)
    monkeypatch.setattr(outbox, "get_config_manager", lambda: config)
    monkeypatch.setattr(outbox, "saved_account_id", lambda: ACCOUNT_A)
    app = FastAPI()
    app.include_router(outbox.router)
    client = TestClient(
        app,
        base_url="http://127.0.0.1:48911",
        client=("127.0.0.1", 50000),
    )
    url = "/api/local-integrations/pet-care/role-alias-retirements"

    assert client.get(url).status_code == 403
    same_origin = {"Sec-Fetch-Site": "same-origin"}
    assert client.get(url, headers=same_origin).status_code == 422
    assert client.get(url, headers={
        **same_origin,
        "X-Neko-Expected-Account": ACCOUNT_B,
    }).status_code == 409
    response = client.get(url, headers={
        **same_origin,
        "X-Neko-Expected-Account": ACCOUNT_A,
    })
    assert response.status_code == 200
    assert response.json() == {"events": [RETIREMENT]}
    assert response.headers["cache-control"] == "no-store"
