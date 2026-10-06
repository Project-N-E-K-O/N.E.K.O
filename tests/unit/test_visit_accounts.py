# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Local map community account -> own ``visit_uid`` (visit design PR-09a, gap noted in PR-08)."""

from __future__ import annotations

import json
import os
import stat

import pytest

from main_routers.visit_router import accounts

UID_1 = "1" * 24
UID_2 = "2" * 24


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setattr(accounts, "config_dir_provider", lambda: tmp_path)
    return tmp_path


async def test_record_then_lookup(cfg):
    assert await accounts.lookup_visit_uid("u1") is None
    assert await accounts.record_account_visit_uid("u1", UID_1) is True
    assert await accounts.record_account_visit_uid("u1", UID_1) is False      # 没变就不重写
    assert await accounts.record_account_visit_uid("u2", UID_2) is True
    assert await accounts.lookup_visit_uid("u1") == UID_1
    assert await accounts.lookup_visit_uid("u2") == UID_2
    data = json.loads((cfg / "visit_accounts.json").read_text(encoding="utf-8"))
    assert data == {"accounts": {"u1": UID_1, "u2": UID_2}}
    if os.name != "nt":
        assert stat.S_IMODE((cfg / "visit_accounts.json").stat().st_mode) == 0o600


@pytest.mark.parametrize("account,uid", [("", UID_1), (None, UID_1), ("u1", "short"), ("u1", "G" * 24),
                                         ("x" * 200, UID_1), ("bad\nid", UID_1)])
async def test_bad_values_are_ignored(cfg, account, uid):
    assert await accounts.record_account_visit_uid(account, uid) is False
    assert not (cfg / "visit_accounts.json").exists()


async def test_damaged_file_reads_as_empty(cfg):
    (cfg / "visit_accounts.json").write_text("{not json", encoding="utf-8")
    assert await accounts.lookup_visit_uid("u1") is None
    (cfg / "visit_accounts.json").write_text(json.dumps({"accounts": {"u1": "nope", "u2": UID_2}}), encoding="utf-8")
    assert await accounts.lookup_visit_uid("u1") is None
    assert await accounts.lookup_visit_uid("u2") == UID_2


async def test_own_visit_uid_follows_the_signed_in_account(cfg, monkeypatch):
    await accounts.record_account_visit_uid("u1", UID_1)
    who = {"account": "u1"}

    async def local_account():
        return who["account"]

    monkeypatch.setattr(accounts, "local_account", local_account)
    assert await accounts.own_visit_uid() == UID_1
    who["account"] = "u9"
    assert await accounts.own_visit_uid() is None
    who["account"] = None
    assert await accounts.own_visit_uid() is None


async def test_local_account_reads_the_session_snapshot(monkeypatch):
    from main_routers import card_drop_router

    monkeypatch.setattr(card_drop_router, "_desktop_session_snapshot",
                        lambda: {"access_token": "t", "local_user_id": "u7"})
    assert await accounts.local_account() == "u7"
    monkeypatch.setattr(card_drop_router, "_desktop_session_snapshot", lambda: {"access_token": "", "local_user_id": "u7"})
    assert await accounts.local_account() is None
    monkeypatch.setattr(card_drop_router, "_desktop_session_snapshot", lambda: None)
    assert await accounts.local_account() is None
