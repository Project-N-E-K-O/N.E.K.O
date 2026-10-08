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



async def test_a_corrupt_map_is_kept_aside_before_recording(cfg):
    path = cfg / "visit_accounts.json"
    path.write_text("{not json", encoding="utf-8")
    assert await accounts.record_account_visit_uid("u2", UID_2) is True
    assert (cfg / "visit_accounts.json.corrupt").read_text(encoding="utf-8") == "{not json"   # 原文件留底
    assert await accounts.lookup_visit_uid("u2") == UID_2


async def test_a_corrupt_map_that_cannot_be_moved_aside_defers_the_write(cfg, monkeypatch):
    path = cfg / "visit_accounts.json"
    path.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(accounts, "move_aside", lambda *_a, **_k: None)
    with pytest.raises(accounts.AccountMapDeferred):
        await accounts.record_account_visit_uid("u2", UID_2)
    assert path.read_text(encoding="utf-8") == "{not json"  # 没覆盖


async def test_an_unreadable_map_is_not_replaced(cfg, monkeypatch):
    assert await accounts.record_account_visit_uid("u1", UID_1) is True

    def busy(*_args, **_kwargs):
        raise PermissionError("locked by another process")

    monkeypatch.setattr(accounts, "open", busy, raising=False)
    with pytest.raises(accounts.AccountMapDeferred):     # 写不成要报出来（调用方据此补写），不是「已记过」的 False
        await accounts.record_account_visit_uid("u2", UID_2)
    monkeypatch.undo()
    monkeypatch.setattr(accounts, "config_dir_provider", lambda: cfg)
    assert await accounts.lookup_visit_uid("u1") == UID_1                 # u1 的映射没被只含 u2 的表覆盖
    assert await accounts.record_account_visit_uid("u2", UID_2) is True   # 读得到了再记
