# -*- coding: utf-8 -*-
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

"""Keyed /scoped_history: generate-then-apply journal, tombstones, key locks.

Drives the real route functions against a real ``FactStore`` rooted in
``tmp_path`` with a counting fake LLM (docs/design/visit-infrastructure.md
section 5 PR-08, ``test_memory_server_scoped_history_idempotency.py``).
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from memory import trust_store
from memory.facts import FactStore
from memory.scopes import MemorySubject, entry_matches_subject

NAME = "Neko"
PAIR = "0123456789abcdef01234567"
GROUP = {"subject_kind": "group_chat", "subject_id": f"neko_visit:{PAIR}"}
PART = {
    "subject_kind": "participant",
    "subject_id": "neko_visit:5f2c1b7e-3a4d-4e8f-9b10-2c3d4e5f6a7b",
}
GP = {
    "subject_kind": "group_participant",
    "subject_id": f"neko_visit:{PAIR}:c_89abcdef0123456789abcdef",
}
GROUP_KEY = f"group_chat:neko_visit:{PAIR}"
GP_KEY = f"group_participant:neko_visit:{PAIR}:c_89abcdef0123456789abcdef"
PART_KEY = "participant:neko_visit:5f2c1b7e-3a4d-4e8f-9b10-2c3d4e5f6a7b"

KEY_GROUP = "visit-digest:AbCdEfGhIjKlMnOpQrStUv:0:group:0"
KEY_SEGMENTS = "visit-digest:AbCdEfGhIjKlMnOpQrStUv:0:segments:0"


class FakeLLM:
    """Counts calls; returns queued payloads; can block on a gate."""

    def __init__(self, *responses):
        self.calls = 0
        self.responses = list(responses)
        self.gate: asyncio.Event | None = None
        self.entered: asyncio.Event | None = None

    async def __call__(self, prompt, lanlan_name, **kwargs):
        self.calls += 1
        if self.entered is not None:
            self.entered.set()
        if self.gate is not None:
            await self.gate.wait()
        response = (
            self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        )
        if isinstance(response, BaseException):
            raise response
        return copy.deepcopy(response)


class FakePersona:
    """Stands in for PersonaManager: records display names, forgets nothing."""

    def __init__(self):
        self.display_names: list[tuple[str, str]] = []

    async def aupdate_subject_display_name(self, name, subject, display_name, *, strict=False):
        self.display_names.append((subject.key, display_name))
        return True

    async def aforget_subject(self, name, subject):
        self.display_names = [
            row for row in self.display_names if row[0] != subject.key
        ]
        return {}


def _cm(root: Path):
    cm = MagicMock()
    cm.memory_dir = str(root)
    cm.aget_character_data = AsyncMock(return_value=(
        "主人", NAME, {}, {}, {"human": "主人", "system": "SYS"},
        {}, {}, {}, {},
    ))
    return cm


@pytest.fixture
def env(tmp_path, monkeypatch):
    from app.memory_server import idempotency, locale_state, routes, runtime

    memory_root = tmp_path / "memory"
    memory_root.mkdir()
    cm = _cm(memory_root)
    # 真实运行时根目录绝不能被碰到：所有路径都必须落在 tmp_path 下。
    assert str(memory_root).startswith(str(tmp_path))
    monkeypatch.setattr(runtime, "_config_manager", cm)
    monkeypatch.setattr(trust_store, "pool_path", lambda: str(tmp_path / "trust.json"))
    trust_store.reset_for_tests()

    fs = FactStore()
    fs._config_manager = cm
    llm = FakeLLM([])
    fs._allm_call_with_retries = llm
    persona = FakePersona()
    reflection = SimpleNamespace(
        abegin_subject_forget=AsyncMock(return_value=None),
        aend_subject_forget=AsyncMock(return_value=None),
        aforget_subject=AsyncMock(return_value={}),
    )
    resolver = SimpleNamespace(aforget_subject=AsyncMock(return_value={}))
    monkeypatch.setattr(runtime, "fact_store", fs)
    monkeypatch.setattr(runtime, "persona_manager", persona)
    monkeypatch.setattr(runtime, "reflection_engine", reflection)
    monkeypatch.setattr(runtime, "fact_dedup_resolver", resolver)
    monkeypatch.setattr(runtime, "_reload_lock", asyncio.Lock())
    monkeypatch.setattr(
        locale_state, "forget_subject_prompt_locale", lambda name, subject: 0,
    )
    assert idempotency.keys_path(NAME).startswith(str(tmp_path))
    yield SimpleNamespace(
        routes=routes, idem=idempotency, runtime=runtime, fs=fs, llm=llm,
        persona=persona, root=memory_root, monkeypatch=monkeypatch,
    )
    trust_store.reset_for_tests()


def _history(*texts: str) -> str:
    return json.dumps([{"role": "user", "content": text} for text in texts])


def _single_body(key: str | None = KEY_GROUP, **extra) -> dict:
    body = {
        "input_history": _history("我家阳台的猫薄荷长得很好", "下次带团子来玩"),
        "subject": GROUP,
        "display_name": "串门群",
    }
    if key is not None:
        body["idempotency_key"] = key
        # 带键请求必须为每个 wire subject 给出清除代数（缺了不能当成 0）
        body["subject_epochs"] = {GROUP_KEY: 0}
    body.update(extra)
    return body


def _segments_body(key: str | None = KEY_SEGMENTS, **extra) -> dict:
    body = {
        "segments": [
            {
                "input_history": _history("我最喜欢晒太阳了。"),
                "subject": GP,
                "speaker_label": "团子",
                "speaker_tier": "none",
                "speaker_id": "neko_visit:c_89abcdef0123456789abcdef",
                "display_name": "团子",
            },
            {
                "input_history": _history("She naps on the windowsill."),
                "subject": PART,
                "speaker_label": "Mika",
                "speaker_tier": "none",
                "speaker_id": "neko_visit:5f2c1b7e-3a4d-4e8f-9b10-2c3d4e5f6a7b",
                "display_name": "Mika",
            },
        ],
    }
    if key is not None:
        body["idempotency_key"] = key
        body["subject_epochs"] = {GP_KEY: 0, PART_KEY: 0}
    body.update(extra)
    return body


SINGLE_FACTS = [
    {"text": "家里阳台种着猫薄荷", "importance": 6},
    {"text": "下次会带团子来玩", "importance": 5},
]
# 第一段被清除后只送第二段去抽取：模型看到的是唯一的一段（段号 1）
PART_ONLY_BATCH_FACTS = [
    {"segment": 1, "facts": [
        {"text": "Mika 的猫下午在窗台睡觉", "importance": 6},
        {"text": "Mika 想再来玩", "importance": 5},
    ]},
]

BATCH_FACTS = [
    {"segment": 1, "facts": [{"text": "团子喜欢晒太阳", "importance": 6}]},
    {"segment": 2, "facts": [
        {"text": "Mika 的猫下午在窗台睡觉", "importance": 6},
        {"text": "Mika 想再来玩", "importance": 5},
    ]},
]


async def _post(env, body: dict):
    req = env.routes.ScopedHistoryRequest.model_validate(body)
    return await env.routes.process_scoped_history(NAME, req)


async def _forget(env, subject: dict, forget_epoch: int | None = None):
    body = {"subject": subject}
    if forget_epoch is not None:
        body["forget_epoch"] = forget_epoch
    req = env.routes.ScopedForgetRequest.model_validate(body)
    return await env.routes.forget_scoped_subject(NAME, req)


def _facts_of(env, subject: dict) -> list[dict]:
    domain = MemorySubject.create(subject["subject_kind"], subject["subject_id"])
    return [
        fact for fact in env.fs.load_facts_full(NAME)
        if entry_matches_subject(fact, domain)
    ]


def _staging_file(env, key: str) -> Path:
    return Path(env.idem.staging_path(NAME, key))


def _key_state(env, key: str) -> str | None:
    path = Path(env.idem.keys_path(NAME))
    if not path.exists():
        return None
    record = json.loads(path.read_text(encoding="utf-8")).get(key)
    return record.get("state") if record else None


def _fail_on_item(env, failing_seq: int):
    """Make the apply phase raise when it reaches ``failing_seq``."""
    original = env.routes._apply_keyed_item

    async def _flaky(lanlan_name, item, segment, generation):
        if item["seq"] == failing_seq:
            raise RuntimeError("injected crash during apply")
        return await original(lanlan_name, item, segment, generation)

    env.monkeypatch.setattr(env.routes, "_apply_keyed_item", _flaky)
    return original


# ── happy path / duplicate ─────────────────────────────────────────────────

async def test_single_keyed_applies_then_same_key_is_duplicate_with_zero_llm(env):
    env.llm.responses = [SINGLE_FACTS]
    first = await _post(env, _single_body())
    assert first["status"] == "processed"
    assert first["created"] == 2
    assert first["trust"]["persisted"] is None
    assert env.llm.calls == 1
    assert _key_state(env, KEY_GROUP) == "done"
    assert not _staging_file(env, KEY_GROUP).exists()
    rows = _facts_of(env, GROUP)
    digest = hashlib.sha256(KEY_GROUP.encode()).hexdigest()[:32]
    assert sorted(row["effect_key"] for row in rows) == [f"{digest}:0", f"{digest}:1"]
    assert (GROUP_KEY, "串门群") in env.persona.display_names

    again = await _post(env, _single_body())
    assert again == {
        "status": "processed",
        "duplicate": True,
        "subject": MemorySubject.create(**{
            "kind": GROUP["subject_kind"], "subject_id": GROUP["subject_id"],
        }).as_entry_fields(),
        "created": 0,
        "fact_ids": [],
        "trust": env.routes._keyed_null_trust_block(),
        "trust_events": [],
    }
    assert set(again["trust"]) == set(first["trust"])
    assert env.llm.calls == 1
    assert len(_facts_of(env, GROUP)) == 2


async def test_segments_keyed_applies_then_same_key_is_duplicate(env):
    env.llm.responses = [BATCH_FACTS]
    first = await _post(env, _segments_body())
    assert [seg["status"] for seg in first["segments"]] == ["ok", "ok"]
    assert [seg["created"] for seg in first["segments"]] == [1, 2]
    assert all(seg["trust"]["persisted"] in (True, None) for seg in first["segments"])
    assert _key_state(env, KEY_SEGMENTS) == "done"

    again = await _post(env, _segments_body())
    assert again["duplicate"] is True
    assert len(again["segments"]) == 2
    for segment in again["segments"]:
        assert segment["status"] == "ok"
        assert segment["created"] == 0
        assert segment["fact_ids"] == []
        assert segment["trust"]["persisted"] is None
        assert set(segment) == set(first["segments"][0])
    assert env.llm.calls == 1
    assert len(_facts_of(env, GP)) == 1
    assert len(_facts_of(env, PART)) == 2


# ── crash during apply → retry resumes from the journal ───────────────────

async def test_crash_mid_apply_retry_skips_llm_and_applies_only_the_rest(env):
    # 二次抽取会给出不同的事实：若重试重新调 LLM，池里就会出现近重复。
    env.llm.responses = [
        BATCH_FACTS,
        [{"segment": 1, "facts": [{"text": "团子很爱晒太阳", "importance": 6}]},
         {"segment": 2, "facts": [{"text": "Mika 家的猫爱睡窗台", "importance": 6}]}],
    ]
    original = _fail_on_item(env, failing_seq=2)
    with pytest.raises(HTTPException) as excinfo:
        await _post(env, _segments_body())
    assert excinfo.value.status_code == 503
    staging = json.loads(_staging_file(env, KEY_SEGMENTS).read_text(encoding="utf-8"))
    assert staging["state"] == "generated"
    assert [entry["seq"] for entry in staging["applied"]] == [0, 1]
    assert _key_state(env, KEY_SEGMENTS) == "pending"

    env.monkeypatch.setattr(env.routes, "_apply_keyed_item", original)
    result = await _post(env, _segments_body())
    assert env.llm.calls == 1
    assert [seg["created"] for seg in result["segments"]] == [1, 2]
    texts = sorted(row["text"] for row in _facts_of(env, GP) + _facts_of(env, PART))
    assert texts == sorted(["团子喜欢晒太阳", "Mika 的猫下午在窗台睡觉", "Mika 想再来玩"])
    assert (GP_KEY, "团子") in env.persona.display_names
    # 从暂存恢复的重试用本次请求带来的当前显示名补上
    assert (PART_KEY, "Mika") in env.persona.display_names
    assert _key_state(env, KEY_SEGMENTS) == "done"
    assert not _staging_file(env, KEY_SEGMENTS).exists()


async def test_fact_row_written_but_journal_not_updated_is_not_duplicated(env):
    """The effect key, not content dedup, is what stops the second write."""
    env.llm.responses = [SINGLE_FACTS]
    real_write = env.idem.write_staging
    state = {"armed": True}

    async def _crash_after_facts(lanlan_name, key, document):
        applied = document.get("applied") or []
        if state["armed"] and any("fact_ids" in entry for entry in applied):
            state["armed"] = False
            raise OSError("injected: facts persisted, journal write lost")
        await real_write(lanlan_name, key, document)

    env.monkeypatch.setattr(env.idem, "write_staging", _crash_after_facts)
    with pytest.raises(HTTPException) as excinfo:
        await _post(env, _single_body(display_name=None))
    assert excinfo.value.status_code == 503
    rows = _facts_of(env, GROUP)
    assert len(rows) == 2
    staging = json.loads(_staging_file(env, KEY_GROUP).read_text(encoding="utf-8"))
    assert staging["applied"] == []

    # 让精确 SHA 去重失效（模拟仲裁改写过这两行的 hash）：只剩 effect_key
    # 能挡住重放。
    for row in env.fs._facts[NAME]:
        row["hash"] = "rewritten-" + row["id"]
    env.fs.save_facts(NAME)

    result = await _post(env, _single_body(display_name=None))
    assert result["status"] == "processed"
    rows = _facts_of(env, GROUP)
    assert len(rows) == 2
    assert len({row["effect_key"] for row in rows}) == 2
    # 重放命中的效果把已有的行作为这次的结果带回：调用方拿得到这些事实的身份
    assert result["created"] == 2 and set(result["fact_ids"]) == {row["id"] for row in rows}
    assert env.llm.calls == 1
    assert _key_state(env, KEY_GROUP) == "done"


# ── forget interacts with the journal ─────────────────────────────────────

async def test_forget_after_partial_apply_cancels_key_and_retry_never_writes_back(env):
    env.llm.responses = [SINGLE_FACTS]
    original = _fail_on_item(env, failing_seq=1)  # seq0 facts, seq1 display_name
    with pytest.raises(HTTPException):
        await _post(env, _single_body())
    assert len(_facts_of(env, GROUP)) == 2
    assert _staging_file(env, KEY_GROUP).exists()

    result = await _forget(env, GROUP)
    assert result["status"] == "forgotten"
    assert not _staging_file(env, KEY_GROUP).exists()
    assert _key_state(env, KEY_GROUP) == "cancelled"
    assert _facts_of(env, GROUP) == []

    env.monkeypatch.setattr(env.routes, "_apply_keyed_item", original)
    again = await _post(env, _single_body())
    assert again["duplicate"] is True
    assert _facts_of(env, GROUP) == []
    assert env.llm.calls == 1
    assert (GROUP_KEY, "串门群") not in env.persona.display_names


async def test_forget_while_generation_in_flight_drops_every_item(env):
    """LLM hangs, forget (epoch 1) lands, the stale digest (epoch 0) is dropped."""
    env.llm.responses = [BATCH_FACTS]
    env.llm.gate = asyncio.Event()
    env.llm.entered = asyncio.Event()
    journals: list[dict] = []
    real_write = env.idem.write_staging

    async def _spy(lanlan_name, key, document):
        journals.append(copy.deepcopy(document))
        await real_write(lanlan_name, key, document)

    env.monkeypatch.setattr(env.idem, "write_staging", _spy)
    epochs = {GP_KEY: 0, PART_KEY: 0}
    task = asyncio.create_task(_post(env, _segments_body(subject_epochs=epochs)))
    await asyncio.wait_for(env.llm.entered.wait(), timeout=5)
    assert not _staging_file(env, KEY_SEGMENTS).exists()

    await _forget(env, GP, forget_epoch=1)
    await _forget(env, PART, forget_epoch=1)
    env.llm.gate.set()
    result = await asyncio.wait_for(task, timeout=5)

    assert [seg["created"] for seg in result["segments"]] == [0, 0]
    assert _facts_of(env, GP) == [] and _facts_of(env, PART) == []
    final_journal = journals[-1]
    assert final_journal["applied"]
    # 生成期间清除推进了 generation：产物在落暂存时就记为丢弃（墓碑是第二道）
    assert all(
        entry.get("dropped_tombstone") or entry.get("dropped_forget_during_generation")
        for entry in final_journal["applied"]
    )
    assert env.persona.display_names == []
    tombstones = json.loads(
        Path(env.idem.tombstones_path(NAME)).read_text(encoding="utf-8")
    )
    assert tombstones[GP_KEY]["forget_epoch"] == 1

    # 清除之后新开的一轮（代数 1）照常写入。
    env.llm.gate = None
    env.llm.responses = [BATCH_FACTS]
    fresh = await _post(env, _segments_body(
        key="visit-digest:NewVisitIdAaaaaaaaaaaa:0:segments:0",
        subject_epochs={GP_KEY: 1, PART_KEY: 1},
    ))
    assert [seg["created"] for seg in fresh["segments"]] == [1, 2]

    # 迟到的旧请求（清除之前发出、之后才到达，代数 0）照样被挡。
    env.llm.responses = [BATCH_FACTS]
    late = await _post(env, _segments_body(
        key="visit-digest:OldVisitIdBbbbbbbbbbbb:0:segments:0",
        subject_epochs={GP_KEY: 0, PART_KEY: 0},
    ))
    assert [seg["created"] for seg in late["segments"]] == [0, 0]
    assert len(_facts_of(env, GP)) == 1
    assert len(_facts_of(env, PART)) == 2


async def test_clock_rollback_does_not_change_the_tombstone_decision(env):
    """Only epochs are compared; a server clock 1 h behind changes nothing."""
    real_now = time.time()
    env.monkeypatch.setattr(
        env.idem, "time", SimpleNamespace(time=lambda: real_now - 3600),
    )
    await _forget(env, GROUP, forget_epoch=1)
    tombstones = json.loads(
        Path(env.idem.tombstones_path(NAME)).read_text(encoding="utf-8")
    )
    assert tombstones[GROUP_KEY]["forgotten_at"] < real_now - 3000

    # 旧代数的迟到请求：客户端时钟比清除时刻「晚」，按时间比会被放行。
    env.llm.responses = [SINGLE_FACTS]
    stale = await _post(env, _single_body(
        key="visit-digest:StaleVisitIdCccccccccc:0:group:0",
        subject_epochs={GROUP_KEY: 0},
        client_requested_at=real_now,
    ))
    assert stale["created"] == 0
    assert _facts_of(env, GROUP) == []

    # 新代数的请求：客户端时钟比清除时刻「早」，按时间比会被误挡。
    fresh = await _post(env, _single_body(
        key="visit-digest:FreshVisitIdDddddddddd:0:group:0",
        subject_epochs={GROUP_KEY: 1},
        client_requested_at=real_now - 7200,
    ))
    assert fresh["created"] == 2


async def test_forget_without_epoch_writes_no_tombstone(env):
    await _forget(env, GROUP)
    assert not Path(env.idem.tombstones_path(NAME)).exists()
    env.llm.responses = [SINGLE_FACTS]
    result = await _post(env, _single_body(subject_epochs={GROUP_KEY: 0}))
    assert result["created"] == 2


# ── staging file naming / crash during generation ─────────────────────────

async def test_key_with_colons_gets_a_hashed_staging_file_name(env):
    env.llm.responses = [SINGLE_FACTS]
    _fail_on_item(env, failing_seq=0)
    with pytest.raises(HTTPException):
        await _post(env, _single_body())
    path = _staging_file(env, KEY_GROUP)
    assert path.exists()
    assert path.name == hashlib.sha256(KEY_GROUP.encode()).hexdigest()[:32] + ".json"
    assert ":" not in path.name
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["key"] == KEY_GROUP
    assert document["subjects"] == [GROUP_KEY]


async def test_crash_during_generation_regenerates_on_retry(env):
    env.llm.responses = [RuntimeError("process died before the LLM returned"), SINGLE_FACTS]
    with pytest.raises(RuntimeError):
        await _post(env, _single_body())
    assert not _staging_file(env, KEY_GROUP).exists()
    # 调 LLM 之前就记了 pending（生成失败也留有可被清除取消的持久记录）；没有暂存，重试照常重新生成
    assert _key_state(env, KEY_GROUP) == "pending"

    result = await _post(env, _single_body())
    assert env.llm.calls == 2
    assert result["created"] == 2
    assert _key_state(env, KEY_GROUP) == "done"


async def test_pending_key_without_staging_regenerates_instead_of_duplicate(env):
    """Staging lost (e.g. swept) while the key is pending: regenerate, never skip."""
    env.llm.responses = [RuntimeError("LLM failed"), SINGLE_FACTS]
    with pytest.raises(RuntimeError):
        await _post(env, _single_body())                     # 记下带请求身份的 pending，生成失败、没有暂存
    assert _key_state(env, KEY_GROUP) == "pending" and not _staging_file(env, KEY_GROUP).exists()
    result = await _post(env, _single_body())
    assert result.get("duplicate") is None
    assert result["created"] == 2
    assert env.llm.calls == 2


async def test_identityless_pending_key_without_staging_is_not_adopted(env):
    await env.idem.update_key(NAME, KEY_GROUP, env.idem.transition("pending"))
    env.llm.responses = [SINGLE_FACTS]
    with pytest.raises(HTTPException) as excinfo:
        await _post(env, _single_body())
    # 既没有请求身份也没有暂存：核对不了是不是同一个请求，不能当成全新请求接手
    assert excinfo.value.status_code == 503 and env.llm.calls == 0


async def test_incomplete_batch_generation_is_502_and_stages_nothing(env):
    env.llm.responses = [[{"segment": 1, "facts": [{"text": "只有第一段", "importance": 5}]}]]
    with pytest.raises(HTTPException) as excinfo:
        await _post(env, _segments_body())
    assert excinfo.value.status_code == 502
    assert not _staging_file(env, KEY_SEGMENTS).exists()
    assert _key_state(env, KEY_SEGMENTS) == "pending"
    assert _facts_of(env, GP) == []


# ── unkeyed requests are unchanged ────────────────────────────────────────

@pytest.mark.parametrize("shape", ["single", "segments"])
async def test_unkeyed_request_ignores_new_fields_and_touches_no_journal(env, shape):
    if shape == "single":
        env.llm.responses = [SINGLE_FACTS]
        plain = await _post(env, _single_body(key=None))
        with_fields = await _post(env, _single_body(
            key=None, subject_epochs={GROUP_KEY: 3}, client_requested_at=1.0,
        ))
        assert plain["created"] == 2 and with_fields["created"] == 0
        assert set(plain) == set(with_fields)
        assert "duplicate" not in plain
    else:
        env.llm.responses = [BATCH_FACTS]
        plain = await _post(env, _segments_body(key=None))
        assert [seg["created"] for seg in plain["segments"]] == [1, 2]
        assert "duplicate" not in plain
    character_dir = env.root / NAME
    assert not (character_dir / "idempotency_keys.json").exists()
    assert not (character_dir / "idempotency_staging").exists()
    assert not any("effect_key" in row for row in env.fs.load_facts_full(NAME))


@pytest.mark.parametrize("shape", ["single", "segments"])
async def test_keyed_request_with_owner_signal_is_422(env, shape):
    if shape == "single":
        body = _single_body(speaker_is_owner=True)
    else:
        body = _segments_body()
        # admin 档才是合法的 owner 组合（否则既有校验先 422，测不到本守卫）。
        body["segments"][0]["speaker_is_owner"] = True
        body["segments"][0]["speaker_tier"] = "admin"
    with pytest.raises(HTTPException) as excinfo:
        await _post(env, body)
    assert excinfo.value.status_code == 422
    assert "idempotency_key does not support speaker_is_owner" in excinfo.value.detail
    assert env.llm.calls == 0


@pytest.mark.parametrize("bad_key", ["", "a b", "a\nb", "键", "x" * 129])
def test_idempotency_key_wire_validation(env, bad_key):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        env.routes.ScopedHistoryRequest.model_validate(_single_body(key=bad_key))


def test_subject_epochs_and_forget_epoch_reject_negative_values(env):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        env.routes.ScopedHistoryRequest.model_validate(
            _single_body(subject_epochs={GROUP_KEY: -1}),
        )
    with pytest.raises(ValidationError):
        env.routes.ScopedForgetRequest.model_validate(
            {"subject": GROUP, "forget_epoch": -1},
        )


# ── lock contracts ────────────────────────────────────────────────────────

async def test_twenty_keys_of_one_character_never_lose_a_record(env):
    real_read = env.idem._read_json_object

    def _slow_read(path):
        data = real_read(path)
        time.sleep(0.005)  # 放大「读完、写回之前」的窗口
        return data

    env.monkeypatch.setattr(env.idem, "_read_json_object", _slow_read)
    keys = [f"visit-digest:Visit{i:017d}:0:group:0" for i in range(20)]

    async def _lifecycle(key: str):
        await env.idem.update_key(NAME, key, env.idem.transition("pending"))
        await asyncio.sleep(0)
        await env.idem.update_key(NAME, key, env.idem.transition("done"))

    await asyncio.gather(*(_lifecycle(key) for key in keys))
    records = json.loads(Path(env.idem.keys_path(NAME)).read_text(encoding="utf-8"))
    assert set(records) == set(keys)
    assert all(records[key]["state"] == "done" for key in keys)


async def test_same_key_concurrent_retry_waits_and_returns_duplicate(env):
    env.llm.responses = [SINGLE_FACTS]
    env.llm.gate = asyncio.Event()
    env.llm.entered = asyncio.Event()
    first = asyncio.create_task(_post(env, _single_body()))
    await asyncio.wait_for(env.llm.entered.wait(), timeout=5)
    second = asyncio.create_task(_post(env, _single_body()))
    await asyncio.sleep(0.05)
    assert not second.done()
    env.llm.gate.set()
    first_result, second_result = await asyncio.wait_for(
        asyncio.gather(first, second), timeout=5,
    )
    assert first_result["created"] == 2
    assert second_result["duplicate"] is True
    assert env.llm.calls == 1
    assert len(_facts_of(env, GROUP)) == 2


def test_key_lock_and_character_lock_are_stable_per_identity(env):
    async def _check():
        idem = env.idem
        assert idem.key_lock(NAME, KEY_GROUP) is idem.key_lock(NAME, KEY_GROUP)
        assert idem.key_lock(NAME, KEY_GROUP) is not idem.key_lock(NAME, KEY_SEGMENTS)
        assert idem.idempotency_lock(NAME) is idem.idempotency_lock(NAME)
        assert idem.idempotency_lock(NAME) is not idem.idempotency_lock("Other")

    asyncio.run(_check())


# ── startup cleanup ───────────────────────────────────────────────────────

async def test_startup_cleanup_drops_expired_staging_and_tombstones_only(env):
    idem = env.idem
    now = time.time()
    ttl = 100.0
    await idem.write_staging(NAME, "old-key", {"subjects": [], "created_at": now - 500})
    await idem.write_staging(NAME, "new-key", {"subjects": [], "created_at": now - 5})
    await idem.record_tombstones(NAME, ["a:old"], 1, now=now - 500)
    await idem.record_tombstones(NAME, ["a:new"], 1, now=now - 5)
    for state, key in (("done", "k-done"), ("cancelled", "k-cancelled"), ("pending", "k-pending")):
        await idem.update_key(NAME, key, idem.transition(state))
        # 让记录本身也「很旧」：键记录无论多旧都不清。
    report = await idem.cleanup_expired([NAME], ttl_s=ttl, now=now)
    assert report == {"staging_removed": 1, "tombstones_removed": 1}
    assert not Path(idem.staging_path(NAME, "old-key")).exists()
    assert Path(idem.staging_path(NAME, "new-key")).exists()
    assert set(await idem.read_tombstones(NAME)) == {"a:new"}
    records = json.loads(Path(idem.keys_path(NAME)).read_text(encoding="utf-8"))
    assert {key: row["state"] for key, row in records.items()} == {
        "k-done": "done", "k-cancelled": "cancelled", "k-pending": "pending",
    }
    report = await idem.cleanup_expired([NAME], ttl_s=0, now=now + 10**9)
    # 再过很久：剩下的暂存与墓碑都过期被清，键记录仍一字不动
    assert report == {"staging_removed": 1, "tombstones_removed": 1}
    records_after = json.loads(Path(idem.keys_path(NAME)).read_text(encoding="utf-8"))
    assert records_after == records


async def test_tombstone_keeps_the_largest_epoch(env):
    idem = env.idem
    await idem.record_tombstones(NAME, [GROUP_KEY], 3)
    await idem.record_tombstones(NAME, [GROUP_KEY], 1)
    assert (await idem.read_tombstones(NAME))[GROUP_KEY]["forget_epoch"] == 3


def test_paths_stay_inside_the_test_root(env):
    for path in (
        env.idem.keys_path(NAME),
        env.idem.staging_path(NAME, KEY_GROUP),
        env.idem.tombstones_path(NAME),
    ):
        assert os.path.commonpath([str(env.root), path]) == str(env.root)


# ── review round 1 ────────────────────────────────────────────────────────

async def test_forget_without_epoch_during_generation_still_drops_the_write(env):
    """No staging to cancel and no tombstone: the pre-LLM generation must still win."""
    env.llm.responses = [SINGLE_FACTS]
    env.llm.gate = asyncio.Event()
    env.llm.entered = asyncio.Event()
    task = asyncio.create_task(_post(env, _single_body(display_name=None)))
    await asyncio.wait_for(env.llm.entered.wait(), timeout=5)
    assert not _staging_file(env, KEY_GROUP).exists()
    await _forget(env, GROUP)                      # 不带 forget_epoch：不写墓碑
    assert not Path(env.idem.tombstones_path(NAME)).exists()
    env.llm.gate.set()
    result = await asyncio.wait_for(task, timeout=5)
    assert result["created"] == 0
    assert _facts_of(env, GROUP) == []
    assert _key_state(env, KEY_GROUP) == "done"


async def test_unreadable_archive_fails_the_keyed_apply_instead_of_guessing(env):
    env.llm.responses = [SINGLE_FACTS]
    archive = Path(env.fs._facts_archive_path(NAME))
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_text("{not json", encoding="utf-8")
    with pytest.raises(HTTPException) as excinfo:
        await _post(env, _single_body(display_name=None))
    assert excinfo.value.status_code == 503
    assert _facts_of(env, GROUP) == []
    assert _staging_file(env, KEY_GROUP).exists()      # 留着暂存，同键重试只补应用
    archive.write_text("[]", encoding="utf-8")
    result = await _post(env, _single_body(display_name=None))
    assert result["created"] == 2 and env.llm.calls == 1


def test_idle_key_locks_are_dropped_from_the_registry(env):
    import gc

    async def _check():
        idem = env.idem
        for i in range(50):
            async with idem.key_lock(NAME, f"visit-digest:k{i}:0:group:0"):
                pass
        gc.collect()
        return sum(1 for (_loop, name, _key) in list(idem._key_locks.keys()) if name == NAME)

    assert asyncio.run(_check()) == 0


async def test_terminal_key_reused_for_another_request_is_rejected(env):
    env.llm.responses = [SINGLE_FACTS]
    await _post(env, _single_body(display_name=None))
    assert _key_state(env, KEY_GROUP) == "done"
    other = _single_body(display_name=None, subject=PART)
    with pytest.raises(HTTPException) as excinfo:
        await _post(env, other)
    assert excinfo.value.status_code == 422
    again = await _post(env, _single_body(display_name=None))
    assert again["duplicate"] is True and env.llm.calls == 1


async def test_startup_cleanup_keeps_the_staging_of_a_pending_key(env):
    idem = env.idem
    now = time.time()
    await idem.write_staging(NAME, "k-pending", {"key": "k-pending", "subjects": [], "created_at": now - 500})
    await idem.update_key(NAME, "k-pending", idem.transition("pending"))
    await idem.write_staging(NAME, "k-done", {"key": "k-done", "subjects": [], "created_at": now - 500})
    await idem.update_key(NAME, "k-done", idem.transition("done"))
    report = await idem.cleanup_expired([NAME], ttl_s=100.0, now=now)
    assert report["staging_removed"] == 1
    assert Path(idem.staging_path(NAME, "k-pending")).exists()
    assert not Path(idem.staging_path(NAME, "k-done")).exists()



async def test_pending_key_without_staging_rejects_another_request(env):
    env.llm.responses = [SINGLE_FACTS]
    _fail_on_item(env, failing_seq=0)
    with pytest.raises(HTTPException):
        await _post(env, _single_body(display_name=None))
    _staging_file(env, KEY_GROUP).unlink()
    assert _key_state(env, KEY_GROUP) == "pending"
    with pytest.raises(HTTPException) as excinfo:
        await _post(env, _single_body(display_name=None, subject=PART))
    assert excinfo.value.status_code == 422
    assert env.llm.calls == 1


async def test_pending_record_is_written_before_staging(env):
    """Failing between the two writes leaves a fingerprinted pending key, never an orphan staging file."""
    env.llm.responses = [SINGLE_FACTS, SINGLE_FACTS]
    real_write = env.idem.write_staging
    state = {"fail": True}

    async def flaky_write(lanlan_name, key, document):
        if state["fail"]:
            state["fail"] = False
            raise OSError("injected: pending recorded, staging not written")
        return await real_write(lanlan_name, key, document)

    env.monkeypatch.setattr(env.idem, "write_staging", flaky_write)
    with pytest.raises(HTTPException):
        await _post(env, _single_body(display_name=None))
    assert not _staging_file(env, KEY_GROUP).exists() and _key_state(env, KEY_GROUP) == "pending"
    with pytest.raises(HTTPException) as excinfo:
        await _post(env, _single_body(display_name=None, subject=PART))
    assert excinfo.value.status_code == 422
    result = await _post(env, _single_body(display_name=None))
    assert result["status"] == "processed" and _key_state(env, KEY_GROUP) == "done"


async def test_cancelling_staging_without_a_record_keeps_its_identity(env):
    idem = env.idem
    staging = {"key": KEY_GROUP, "shape": "single", "subjects": [GROUP_KEY],
               "segments": [{"wire_key": GROUP_KEY}], "request_hash": "h1", "items": [], "applied": []}
    await idem.write_staging(NAME, KEY_GROUP, staging)
    await env.routes._cancel_staged_writes_for_subjects(NAME, {GROUP_KEY})
    record = await idem.read_key(NAME, KEY_GROUP)
    assert record["state"] == "cancelled"
    assert record["request"] == {"shape": "single", "wire_keys": [GROUP_KEY], "content_hash": "h1"}


async def test_forget_during_generation_survives_a_crash_before_apply(env):
    env.llm.responses = [SINGLE_FACTS]
    env.llm.gate = asyncio.Event()
    env.llm.entered = asyncio.Event()
    real_apply = env.routes._apply_keyed_staging
    crash = {"armed": True}

    async def crash_before_apply(*args, **kwargs):
        if crash["armed"]:
            crash["armed"] = False
            raise RuntimeError("killed after staging, before apply")
        return await real_apply(*args, **kwargs)

    env.monkeypatch.setattr(env.routes, "_apply_keyed_staging", crash_before_apply)
    task = asyncio.create_task(_post(env, _single_body(display_name=None)))
    await asyncio.wait_for(env.llm.entered.wait(), timeout=5)
    await _forget(env, GROUP)                      # 不带代数的清除落在生成期间
    env.llm.gate.set()
    with pytest.raises(HTTPException):
        await asyncio.wait_for(task, timeout=5)
    env.llm.gate = None
    result = await _post(env, _single_body(display_name=None))     # 重试读到的是清除之后的 generation
    assert result["created"] == 0 and _facts_of(env, GROUP) == []


async def test_trust_inputs_are_part_of_the_request_identity(env):
    env.llm.responses = [BATCH_FACTS]
    await _post(env, _segments_body())
    changed = _segments_body()
    changed["segments"][0]["speaker_id"] = "neko_visit:c_other0000000000000000000"
    with pytest.raises(HTTPException) as excinfo:
        await _post(env, changed)
    assert excinfo.value.status_code == 422



async def test_same_key_with_different_content_is_rejected(env):
    env.llm.responses = [SINGLE_FACTS]
    await _post(env, _single_body(display_name=None))
    changed = _single_body(display_name=None, input_history=_history("完全不同的一批话"))
    with pytest.raises(HTTPException) as excinfo:
        await _post(env, changed)
    assert excinfo.value.status_code == 422
    # 显示名不进内容哈希：同一批句子换了显示名重试照样是 duplicate
    again = await _post(env, _single_body(display_name="新名字"))
    assert again["duplicate"] is True


async def test_pending_staging_rejects_a_retry_with_different_content(env):
    env.llm.responses = [SINGLE_FACTS]
    _fail_on_item(env, failing_seq=0)
    with pytest.raises(HTTPException):
        await _post(env, _single_body(display_name=None))
    path = Path(env.idem.keys_path(NAME))
    data = json.loads(path.read_text(encoding="utf-8"))
    data[KEY_GROUP].pop("request", None)            # 只剩暂存里的内容哈希可核对
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(HTTPException) as excinfo:
        await _post(env, _single_body(display_name=None, input_history=_history("另一批")))
    assert excinfo.value.status_code == 422


async def test_malformed_key_record_fails_closed(env):
    path = Path(env.idem.keys_path(NAME))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({KEY_GROUP: None}), encoding="utf-8")
    env.llm.responses = [SINGLE_FACTS]
    with pytest.raises(HTTPException) as excinfo:
        await _post(env, _single_body(display_name=None))
    assert excinfo.value.status_code == 503
    assert env.llm.calls == 0 and _facts_of(env, GROUP) == []


async def test_unreadable_active_facts_fail_the_keyed_apply(env):
    env.llm.responses = [SINGLE_FACTS]
    facts_path = Path(env.fs._facts_path(NAME))
    facts_path.write_text("{torn", encoding="utf-8")
    with pytest.raises(HTTPException) as excinfo:
        await _post(env, _single_body(display_name=None))
    assert excinfo.value.status_code == 503
    assert facts_path.read_text(encoding="utf-8") == "{torn"       # 没被覆盖



async def test_staged_retry_stamps_the_current_request_display_name_not_the_stale_one(env):
    env.llm.responses = [SINGLE_FACTS]
    original_apply = _fail_on_item(env, failing_seq=1)   # seq0 facts 已写，seq1 显示名中断
    with pytest.raises(HTTPException):
        await _post(env, _single_body(display_name="旧群名"))
    env.monkeypatch.setattr(env.routes, "_apply_keyed_item", original_apply)
    env.persona.display_names.clear()
    result = await _post(env, _single_body(display_name="新群名"))
    assert result["status"] == "processed"
    assert (GROUP_KEY, "新群名") in env.persona.display_names
    assert (GROUP_KEY, "旧群名") not in env.persona.display_names


# ── review round 6 ────────────────────────────────────────────────────────

async def test_forget_epoch_is_not_copied_onto_fanout_subjects(env):
    linked = MemorySubject.create(PART["subject_kind"], PART["subject_id"])
    original = env.routes._forget_fanout_targets

    def fanout(subject):
        return list(original(subject)) + [linked]

    env.monkeypatch.setattr(env.routes, "_forget_fanout_targets", fanout)
    await _forget(env, GROUP, forget_epoch=7)
    tombstones = json.loads(Path(env.idem.tombstones_path(NAME)).read_text(encoding="utf-8"))
    assert set(tombstones) == {GROUP_KEY}


async def test_same_key_in_another_language_is_rejected(env):
    env.llm.responses = [SINGLE_FACTS]
    await _post(env, _single_body(display_name=None, language="zh"))
    with pytest.raises(HTTPException) as excinfo:
        await _post(env, _single_body(display_name=None, language="en"))
    assert excinfo.value.status_code == 422


async def test_repaired_facts_file_is_not_overwritten_by_a_stale_empty_cache(env):
    facts_path = Path(env.fs._facts_path(NAME))
    facts_path.write_text("{torn", encoding="utf-8")
    assert await env.fs.aload_facts(NAME) == []            # 宽松加载器把坏文件缓存成空
    kept = {"id": "kept-1", "text": "修好的旧事实", "importance": 6, "hash": "h-kept",
            **{k: v for k, v in GROUP.items()}, "scope": f"{GROUP['subject_kind']}:{GROUP['subject_id']}"}
    facts_path.write_text(json.dumps([kept], ensure_ascii=False), encoding="utf-8")
    env.llm.responses = [SINGLE_FACTS]
    await _post(env, _single_body(display_name=None))
    on_disk = json.loads(facts_path.read_text(encoding="utf-8"))
    assert "kept-1" in {row.get("id") for row in on_disk}


async def test_tombstones_referenced_by_pending_staging_do_not_expire(env):
    idem = env.idem
    now = time.time()
    await idem.write_staging(NAME, "k-pending", {"key": "k-pending", "subjects": ["a:kept"],
                                                 "created_at": now - 500})
    await idem.update_key(NAME, "k-pending", idem.transition("pending"))
    await idem.record_tombstones(NAME, ["a:kept"], 2, now=now - 500)
    await idem.record_tombstones(NAME, ["a:gone"], 2, now=now - 500)
    await idem.cleanup_expired([NAME], ttl_s=100.0, now=now)
    assert set(await idem.read_tombstones(NAME)) == {"a:kept"}


async def test_startup_cleanup_skips_a_character_being_released(env):
    idem = env.idem
    now = time.time()
    await idem.record_tombstones(NAME, ["a:old"], 1, now=now - 500)
    env.monkeypatch.setattr(env.runtime, "_begin_character_request", lambda name: None)
    report = await idem.cleanup_expired([NAME], ttl_s=100.0, now=now)
    assert report == {"staging_removed": 0, "tombstones_removed": 0}
    assert set(await idem.read_tombstones(NAME)) == {"a:old"}



async def test_forget_landing_while_staging_is_written_is_caught_by_the_recheck(env):
    env.llm.responses = [SINGLE_FACTS]
    real_write = env.idem.write_staging
    real_apply = env.routes._apply_keyed_staging
    state = {"forgot": False, "crash": True}

    async def forget_then_write(lanlan_name, key, document):
        if not state["forgot"]:
            state["forgot"] = True
            # 清除整个落在「生成后的检查」与暂存落盘之间：两遍取消扫描都看不到暂存，
            # 键锁又被占着（跳过），只能靠暂存落盘之后的复核
            await _forget(env, GROUP)
        return await real_write(lanlan_name, key, document)

    async def crash_before_apply(*args, **kwargs):
        if state["crash"]:
            state["crash"] = False
            raise RuntimeError("killed after staging, before apply")
        return await real_apply(*args, **kwargs)

    env.monkeypatch.setattr(env.idem, "write_staging", forget_then_write)
    env.monkeypatch.setattr(env.routes, "_apply_keyed_staging", crash_before_apply)
    with pytest.raises(HTTPException):
        await _post(env, _single_body(display_name=None))
    # 请求在应用前中断：暂存留在盘上，但被清 subject 的抽取原文已随丢弃标记一并抹掉
    raw = _staging_file(env, KEY_GROUP).read_text(encoding="utf-8")
    assert all(fact["text"] not in raw for fact in SINGLE_FACTS)
    result = await _post(env, _single_body(display_name=None))
    assert result["created"] == 0 and _facts_of(env, GROUP) == []


# ── review round 9 ────────────────────────────────────────────────────────

async def test_key_record_with_unknown_state_fails_closed(env):
    path = Path(env.idem.keys_path(NAME))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({KEY_GROUP: {"written_at": 1.0}}), encoding="utf-8")
    env.llm.responses = [SINGLE_FACTS]
    with pytest.raises(HTTPException) as excinfo:
        await _post(env, _single_body(display_name=None))
    assert excinfo.value.status_code == 503 and env.llm.calls == 0


async def test_pending_key_without_staging_protects_its_tombstone(env):
    idem = env.idem
    now = time.time()
    await idem.update_key(NAME, KEY_GROUP, idem.transition(
        "pending", request={"shape": "single", "wire_keys": [GROUP_KEY], "content_hash": "h"}))
    await idem.record_tombstones(NAME, [GROUP_KEY], 2, now=now - 500)
    await idem.record_tombstones(NAME, ["a:gone"], 2, now=now - 500)
    await idem.cleanup_expired([NAME], ttl_s=100.0, now=now)
    assert set(await idem.read_tombstones(NAME)) == {GROUP_KEY}


async def test_tombstones_are_compared_on_the_wire_key_only(env):
    env.llm.responses = [SINGLE_FACTS]
    _fail_on_item(env, failing_seq=0)
    with pytest.raises(HTTPException):
        await _post(env, _single_body(display_name=None))
    staging = json.loads(_staging_file(env, KEY_GROUP).read_text(encoding="utf-8"))
    assert [seg["tombstone_keys"] for seg in staging["segments"]] == [[GROUP_KEY]]



async def test_key_record_with_unhashable_state_fails_closed(env):
    path = Path(env.idem.keys_path(NAME))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({KEY_GROUP: {"state": ["done"]}}), encoding="utf-8")
    env.llm.responses = [SINGLE_FACTS]
    with pytest.raises(HTTPException) as excinfo:
        await _post(env, _single_body(display_name=None))
    assert excinfo.value.status_code == 503


# ── review round 10 ───────────────────────────────────────────────────────

async def test_malformed_tombstone_fails_closed(env):
    path = Path(env.idem.tombstones_path(NAME))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({GROUP_KEY: {"forgotten_at": 1.0}}), encoding="utf-8")
    env.llm.responses = [SINGLE_FACTS]
    with pytest.raises(HTTPException) as excinfo:
        await _post(env, _single_body(display_name=None))
    assert excinfo.value.status_code == 503
    assert _facts_of(env, GROUP) == []


async def test_epochless_forget_cancels_a_pending_key_without_staging(env):
    idem = env.idem
    await idem.update_key(NAME, KEY_GROUP, idem.transition(
        "pending", request={"shape": "single", "wire_keys": [GROUP_KEY], "content_hash": "h"}))
    await _forget(env, GROUP)
    assert _key_state(env, KEY_GROUP) == "cancelled"


async def test_staged_writes_are_cancelled_before_the_erase_starts(env):
    env.llm.responses = [SINGLE_FACTS]
    _fail_on_item(env, failing_seq=0)
    with pytest.raises(HTTPException):
        await _post(env, _single_body(display_name=None))
    seen = {}
    real_forget = env.fs.aforget_subject

    async def spy(name, subject):
        seen.setdefault("state_at_erase", _key_state(env, KEY_GROUP))
        return await real_forget(name, subject)

    env.monkeypatch.setattr(env.fs, "aforget_subject", spy)
    await _forget(env, GROUP)
    assert seen["state_at_erase"] == "cancelled"



async def test_unreadable_key_file_does_not_block_a_forget_with_staging(env):
    env.llm.responses = [SINGLE_FACTS]
    original = _fail_on_item(env, failing_seq=1)  # seq0 facts, seq1 display_name
    with pytest.raises(HTTPException):
        await _post(env, _single_body())
    assert _key_state(env, KEY_GROUP) == "pending" and _staging_file(env, KEY_GROUP).exists()
    keys_file = Path(env.idem.keys_path(NAME))
    intact = keys_file.read_text(encoding="utf-8")
    keys_file.write_text("{torn", encoding="utf-8")
    result = await _forget(env, GROUP)                     # 不带 forget_epoch：没有墓碑
    assert result["status"] == "forgotten" and _facts_of(env, GROUP) == []
    # 键文件读不出、取消记不进去：改记在暂存里，暂存留着
    raw = _staging_file(env, KEY_GROUP).read_text(encoding="utf-8")
    staging = json.loads(raw)
    assert staging["cancelled_by_forget"] is True
    # 留下的只是取消标记：被清 subject 的抽取原文与显示名都不在磁盘上
    for fact in SINGLE_FACTS:
        assert fact["text"] not in raw
    assert "串门群" not in raw
    keys_file.write_text(intact, encoding="utf-8")         # 键文件修好，记录仍是 pending
    env.monkeypatch.setattr(env.routes, "_apply_keyed_item", original)
    again = await _post(env, _single_body())
    # 同键重试不会按清除之后的 generation 重新抽取写回
    assert again["duplicate"] is True and _facts_of(env, GROUP) == []
    assert env.llm.calls == 1
    assert _key_state(env, KEY_GROUP) == "cancelled" and not _staging_file(env, KEY_GROUP).exists()


@pytest.mark.parametrize("owner_state", ["pending", "done", None])
async def test_cleanup_judges_unreadable_staging_by_its_filename_owner(env, owner_state):
    idem = env.idem
    now = time.time()
    if owner_state is not None:
        await idem.update_key(NAME, "torn-key", idem.transition(owner_state))
    path = Path(idem.staging_path(NAME, "torn-key"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{torn", encoding="utf-8")
    os.utime(path, (now - 500, now - 500))
    report = await idem.cleanup_expired([NAME], ttl_s=100.0, now=now)
    # 按文件名反查到 pending 键就保留（它的唯一副本）；已终结或没有任何键记录的孤儿过期即删
    kept = owner_state == "pending"
    assert path.exists() is kept and report["staging_removed"] == (0 if kept else 1)


async def test_unreadable_key_file_does_not_block_a_forget(env):
    env.llm.responses = [SINGLE_FACTS]
    await _post(env, _single_body(key=None, display_name=None))       # 不带键写入两条事实
    assert _facts_of(env, GROUP)
    Path(env.idem.keys_path(NAME)).write_text("{torn", encoding="utf-8")
    result = await _forget(env, GROUP)
    assert result["status"] == "forgotten" and _facts_of(env, GROUP) == []


async def test_forget_cancels_a_pending_key_whose_staging_is_unreadable(env):
    env.llm.responses = [SINGLE_FACTS]
    _fail_on_item(env, failing_seq=1)
    with pytest.raises(HTTPException):
        await _post(env, _single_body())
    _staging_file(env, KEY_GROUP).write_text("{torn", encoding="utf-8")
    result = await _forget(env, GROUP)
    # 坏暂存不挡清除：按键记录取消，坏暂存一并删掉
    assert result["status"] == "forgotten" and _facts_of(env, GROUP) == []
    assert _key_state(env, KEY_GROUP) == "cancelled"
    assert not _staging_file(env, KEY_GROUP).exists()


async def test_forget_cancels_staging_written_after_its_staging_scan(env):
    env.llm.responses = [SINGLE_FACTS]
    original = _fail_on_item(env, failing_seq=1)
    with pytest.raises(HTTPException):
        await _post(env, _single_body())
    assert _staging_file(env, KEY_GROUP).exists()

    async def stale_snapshot(_name):
        return []          # 暂存扫描的快照取在这份暂存写成之前

    env.monkeypatch.setattr(env.idem, "list_staging", stale_snapshot)
    result = await _forget(env, GROUP)
    assert result["status"] == "forgotten" and _facts_of(env, GROUP) == []
    assert _key_state(env, KEY_GROUP) == "cancelled"
    assert not _staging_file(env, KEY_GROUP).exists()
    env.monkeypatch.setattr(env.routes, "_apply_keyed_item", original)
    again = await _post(env, _single_body())
    assert again["duplicate"] is True and _facts_of(env, GROUP) == []


@pytest.mark.parametrize("forget_epoch, kept", [(2, True), (3, False)])
async def test_forget_keeps_staging_issued_after_it_by_epoch(env, forget_epoch, kept):
    env.llm.responses = [SINGLE_FACTS]
    _fail_on_item(env, failing_seq=1)
    with pytest.raises(HTTPException):
        await _post(env, _single_body(subject_epochs={GROUP_KEY: 2}))
    await _forget(env, GROUP, forget_epoch=forget_epoch)
    # 请求代数 >= 这次清除的代数：它是知道这次清除之后才发起的新写入，不取消
    assert _staging_file(env, KEY_GROUP).exists() is kept
    assert _key_state(env, KEY_GROUP) == ("pending" if kept else "cancelled")


@pytest.mark.parametrize("crash_segment", [0, 1], ids=["before-forgotten-segment", "after-it"])
async def test_forget_of_one_segment_keeps_the_other_segments_for_retry(env, crash_segment):
    env.llm.responses = [BATCH_FACTS]
    original = env.routes._apply_keyed_item

    async def _flaky(lanlan_name, item, segment, generation):
        if item["segment"] == crash_segment and item["kind"] == "facts":
            raise RuntimeError("injected crash before the second segment")
        return await original(lanlan_name, item, segment, generation)

    env.monkeypatch.setattr(env.routes, "_apply_keyed_item", _flaky)
    with pytest.raises(HTTPException):
        await _post(env, _segments_body())
    assert len(_facts_of(env, GP)) == (0 if crash_segment == 0 else 1) and _facts_of(env, PART) == []
    result = await _forget(env, GP)
    assert result["status"] == "forgotten" and _facts_of(env, GP) == []
    # 只丢被清的那一段：键仍 pending、暂存留着，且其中没有被清段的抽取原文
    assert _key_state(env, KEY_SEGMENTS) == "pending"
    assert "团子喜欢晒太阳" not in _staging_file(env, KEY_SEGMENTS).read_text(encoding="utf-8")
    env.monkeypatch.setattr(env.routes, "_apply_keyed_item", original)
    again = await _post(env, _segments_body())
    assert again.get("duplicate") is None
    # 重试补写没被清的那一段，被清的那一段不会写回
    assert len(_facts_of(env, PART)) == 2 and _facts_of(env, GP) == []
    # 被清段已应用的结果也清掉：响应不再报出已被擦除的事实
    assert again["segments"][0]["created"] == 0 and again["segments"][0]["fact_ids"] == []
    # 被清段未应用的显示名项同样不补写（重试时显示名取自当前请求，不记丢弃就会写回）
    assert (GP_KEY, "团子") not in env.persona.display_names
    assert env.llm.calls == 1
    assert _key_state(env, KEY_SEGMENTS) == "done"


async def test_forget_over_a_malformed_tombstone_erases_and_keeps_it(env):
    env.llm.responses = [SINGLE_FACTS]
    await _post(env, _single_body(key=None, display_name=None))
    assert _facts_of(env, GROUP)
    path = Path(env.idem.tombstones_path(NAME))
    path.write_text(json.dumps({GROUP_KEY: {"forget_epoch": "9"}}), encoding="utf-8")
    with pytest.raises(HTTPException) as excinfo:
        await _forget(env, GROUP, forget_epoch=1)
    # 坏墓碑不挡擦除：照常擦掉；但完成标记记不上，回 503 让调用方重试到墓碑修好
    assert excinfo.value.status_code == 503 and _facts_of(env, GROUP) == []
    # 也不被较低的代数覆盖：原样留着（读路径对它 fail closed）
    assert json.loads(path.read_text(encoding="utf-8")) == {GROUP_KEY: {"forget_epoch": "9"}}
    with pytest.raises(env.idem.IdempotencyStateError):
        env.idem.tombstone_epoch(await env.idem.read_tombstones(NAME), [GROUP_KEY])


@pytest.mark.parametrize("owner_state", ["pending", "done"])
async def test_cleanup_judges_staging_by_its_filename_not_its_embedded_key(env, owner_state):
    idem = env.idem
    now = time.time()
    await idem.update_key(NAME, "key-a", idem.transition(owner_state))
    path = Path(idem.staging_path(NAME, "key-a"))
    path.parent.mkdir(parents=True, exist_ok=True)
    # key-a 的暂存文件里键被改成了 key-b（key-b 没有任何记录）
    path.write_text(json.dumps({"key": "key-b", "subjects": [], "created_at": now - 500}), encoding="utf-8")
    report = await idem.cleanup_expired([NAME], ttl_s=100.0, now=now)
    kept = owner_state == "pending"
    assert path.exists() is kept and report["staging_removed"] == (0 if kept else 1)



async def test_same_key_with_another_scope_is_a_different_request(env):
    env.llm.responses = [SINGLE_FACTS]
    await _post(env, _single_body())
    with pytest.raises(HTTPException) as excinfo:
        await _post(env, _single_body(subject={**GROUP, "scope": "another_scope"}))
    # 同 kind:id、不同 scope 是两个隔离的 subject：不能当作同一个请求回 duplicate
    assert excinfo.value.status_code == 422


async def test_forget_cancels_staging_by_its_routed_subject(env):
    idem = env.idem
    routed = {"subject_kind": "participant", "subject_id": "neko_visit:routed-person", "scope": "x"}
    await idem.update_key(NAME, KEY_GROUP, idem.transition("pending"))
    await idem.write_staging(NAME, KEY_GROUP, {
        "shape": "single", "subjects": [GROUP_KEY], "epochs": {}, "created_at": time.time(),
        "segments": [{"wire_key": GROUP_KEY, "subject": routed, "tombstone_keys": [GROUP_KEY]}],
        "items": [], "applied": [],
    })
    # 当前扇出已不含这份暂存的 wire subject，但它应用时写的是记下的路由后 subject
    cancelled = await env.routes._cancel_staged_writes_for_subjects(
        NAME, {"participant:neko_visit:routed-person"},
    )
    assert cancelled == 1 and _key_state(env, KEY_GROUP) == "cancelled"
    assert not _staging_file(env, KEY_GROUP).exists()


async def test_forget_scrubs_a_misplaced_staging_file_in_place(env):
    idem = env.idem
    path = _staging_file(env, KEY_GROUP)
    path.parent.mkdir(parents=True, exist_ok=True)
    # 文件名属于 KEY_GROUP，内容里的键却是另一个
    path.write_text(json.dumps({
        "key": "other-key", "shape": "single", "subjects": [GROUP_KEY], "created_at": time.time(),
        "segments": [{"wire_key": GROUP_KEY, "subject": GROUP}],
        "items": [{"seq": 0, "kind": "facts", "segment": 0, "facts": SINGLE_FACTS}], "applied": [],
    }, ensure_ascii=False), encoding="utf-8")
    other = _staging_file(env, "other-key")
    result = await _forget(env, GROUP)
    assert result["status"] == "forgotten"
    raw = path.read_text(encoding="utf-8")
    # 就地抹成取消标记：被清 subject 的原文不留；也不顺着内嵌键去碰别的路径
    assert "家里阳台种着猫薄荷" not in raw and json.loads(raw)["cancelled_by_forget"] is True
    assert not other.exists()
    with pytest.raises(idem.IdempotencyStateError):
        await idem.read_staging(NAME, KEY_GROUP)               # 同键重试照样 fail closed



async def test_replayed_or_stale_forget_does_not_erase_writes_made_after_it(env):
    env.llm.responses = [SINGLE_FACTS, SINGLE_FACTS]
    await _post(env, _single_body(key=None, display_name=None))
    first = await _forget(env, GROUP, forget_epoch=2)
    assert first["status"] == "forgotten" and _facts_of(env, GROUP) == []
    # 清除之后、带着新代数的合法写入
    await _post(env, _single_body(subject_epochs={GROUP_KEY: 2}, display_name=None))
    assert len(_facts_of(env, GROUP)) == 2
    for epoch in (2, 1):                       # 同代数重放、迟到的旧清除
        again = await _forget(env, GROUP, forget_epoch=epoch)
        assert again["status"] == "forgotten" and again.get("duplicate") is True
        assert len(_facts_of(env, GROUP)) == 2
    newer = await _forget(env, GROUP, forget_epoch=3)   # 更新的清除照常擦
    assert newer.get("duplicate") is None and _facts_of(env, GROUP) == []


async def test_forget_whose_erase_did_not_finish_is_not_skipped_on_retry(env):
    env.llm.responses = [SINGLE_FACTS]
    await _post(env, _single_body(key=None, display_name=None))
    # 墓碑已落盘、擦除还没完成（崩在两步之间）：重试必须照常擦
    await env.idem.record_tombstones(NAME, [GROUP_KEY], 2)
    result = await _forget(env, GROUP, forget_epoch=2)
    assert result.get("duplicate") is None and _facts_of(env, GROUP) == []


async def test_older_forget_rechecks_the_erased_epoch_under_the_transaction_locks(env):
    env.llm.responses = [SINGLE_FACTS, SINGLE_FACTS]
    await _post(env, _single_body(key=None, display_name=None))
    await _forget(env, GROUP, forget_epoch=3)
    await _post(env, _single_body(subject_epochs={GROUP_KEY: 3}, display_name=None))
    assert len(_facts_of(env, GROUP)) == 2
    real_check = env.routes._forget_epoch_already_erased
    calls = {"n": 0}

    async def check(*args):
        calls["n"] += 1
        if calls["n"] == 1:
            return False        # 锁外那次核对发生在较新的清除完成之前
        return await real_check(*args)

    env.monkeypatch.setattr(env.routes, "_forget_epoch_already_erased", check)
    stale = await _forget(env, GROUP, forget_epoch=2)
    # 持锁后再核一次：看到较新的清除已擦完，旧清除不再擦掉之后的合法写入
    assert stale.get("duplicate") is True and calls["n"] == 2
    assert len(_facts_of(env, GROUP)) == 2


async def test_record_pass_matches_late_staging_by_its_routed_subject(env):
    idem = env.idem
    routed = {"subject_kind": "participant", "subject_id": "neko_visit:routed-person", "scope": "x"}
    await idem.update_key(NAME, KEY_GROUP, idem.transition(
        "pending", request={"shape": "single", "wire_keys": [GROUP_KEY], "content_hash": "h"},
    ))
    await idem.write_staging(NAME, KEY_GROUP, {
        "shape": "single", "subjects": [GROUP_KEY], "epochs": {}, "created_at": time.time(),
        "segments": [{"wire_key": GROUP_KEY, "subject": routed, "tombstone_keys": [GROUP_KEY]}],
        "items": [], "applied": [],
    })

    async def stale_snapshot(_name):
        return []          # 暂存扫描的快照取在这份暂存写成之前

    env.monkeypatch.setattr(idem, "list_staging", stale_snapshot)
    cancelled = await env.routes._cancel_staged_writes_for_subjects(
        NAME, {"participant:neko_visit:routed-person"},
    )
    # 键记录里只有 wire key，但读到的暂存记着路由后的被清 subject：照样取消
    assert cancelled == 1 and _key_state(env, KEY_GROUP) == "cancelled"
    assert not _staging_file(env, KEY_GROUP).exists()


async def test_forget_is_not_blocked_by_an_unrelated_key_holding_its_lock(env):
    idem = env.idem
    await idem.update_key(NAME, "unrelated", idem.transition(
        "pending", request={"shape": "single", "wire_keys": [PART_KEY], "content_hash": "h"},
    ))
    async with idem.key_lock(NAME, "unrelated"):       # 无关请求正持着自己的键锁等 LLM
        cancelled = await asyncio.wait_for(
            env.routes._cancel_staged_writes_for_subjects(NAME, {GROUP_KEY}), timeout=2,
        )
    assert cancelled == 0 and _key_state(env, "unrelated") == "pending"


async def test_pending_record_keeps_routed_subjects_for_forget_without_staging(env):
    env.llm.responses = [SINGLE_FACTS]
    _fail_on_item(env, failing_seq=1)
    with pytest.raises(HTTPException):
        await _post(env, _single_body())
    records = json.loads(Path(env.idem.keys_path(NAME)).read_text(encoding="utf-8"))
    # 路由后实际写入的 subject 单独记在 pending 记录上，不进请求身份
    assert records[KEY_GROUP]["routed_keys"] == [GROUP_KEY]
    assert "routed_keys" not in records[KEY_GROUP]["request"]


async def test_record_pass_matches_routed_keys_when_staging_is_not_written_yet(env):
    idem = env.idem
    await idem.update_key(NAME, KEY_GROUP, idem.transition(
        "pending", request={"shape": "single", "wire_keys": [GROUP_KEY], "content_hash": "h"},
        routed_keys=["participant:neko_visit:routed-person"],
    ))
    # 暂存还没写成：只能靠记录里的路由后 subject 认出它
    cancelled = await env.routes._cancel_staged_writes_for_subjects(
        NAME, {"participant:neko_visit:routed-person"},
    )
    assert cancelled == 1 and _key_state(env, KEY_GROUP) == "cancelled"


async def test_concurrent_older_forget_waits_for_the_newer_one_to_publish_its_epoch(env):
    env.llm.responses = [SINGLE_FACTS, SINGLE_FACTS]
    await _post(env, _single_body(key=None, display_name=None))
    gate, reached = asyncio.Event(), asyncio.Event()
    real_mark = env.idem.mark_tombstone_erased

    async def slow_mark(*args, **kwargs):
        reached.set()
        await gate.wait()        # 较新的清除已擦完、放了擦除锁，还没记完成标记
        return await real_mark(*args, **kwargs)

    env.monkeypatch.setattr(env.idem, "mark_tombstone_erased", slow_mark)
    newer = asyncio.create_task(_forget(env, GROUP, forget_epoch=20))
    await reached.wait()
    # 较新清除之后、带着新代数的合法写入
    await _post(env, _single_body(subject_epochs={GROUP_KEY: 20}, display_name=None))
    assert len(_facts_of(env, GROUP)) == 2
    real_check = env.routes._forget_epoch_already_erased
    checks = {"n": 0}

    async def counted(*args):
        checks["n"] += 1
        return await real_check(*args)

    env.monkeypatch.setattr(env.routes, "_forget_epoch_already_erased", counted)
    older = asyncio.create_task(_forget(env, GROUP, forget_epoch=10))
    # 给旧清除足够时间：没有栅栏时它会跑到持锁复核（第 2 次核对）并擦除；
    # 有栅栏时它一直挡在栅栏外，一次核对都做不了
    for _ in range(200):
        if checks["n"] >= 2 or older.done():
            break
        await asyncio.sleep(0.01)
    gate.set()
    assert (await newer)["status"] == "forgotten"
    stale = await older
    # 旧清除排在较新清除公布完成代数之后才核对：不再擦掉那批合法写入
    assert stale.get("duplicate") is True
    assert len(_facts_of(env, GROUP)) == 2



async def test_generation_failing_after_a_forget_keeps_the_forget_on_the_record(env):
    env.llm.responses = [RuntimeError("LLM failed")]
    env.llm.gate = asyncio.Event()
    env.llm.entered = asyncio.Event()
    task = asyncio.create_task(_post(env, _single_body()))
    await asyncio.wait_for(env.llm.entered.wait(), timeout=5)
    # 生成期间的清除：键锁被占着，它不排队，只在记录上持久记下被清的 subject
    await _forget(env, GROUP)
    env.llm.gate.set()
    with pytest.raises(RuntimeError):
        await asyncio.wait_for(task, timeout=5)
    records = json.loads(Path(env.idem.keys_path(NAME)).read_text(encoding="utf-8"))
    assert records[KEY_GROUP]["state"] == "pending" and records[KEY_GROUP]["forgotten_keys"] == [GROUP_KEY]
    # 生成失败（或进程被杀）之后同键重试：重新生成但按这份记录丢弃被清的段，不写回旧内容
    env.llm.gate = None
    env.llm.responses = [SINGLE_FACTS]
    again = await _post(env, _single_body())
    assert again["created"] == 0 and _facts_of(env, GROUP) == []


async def test_generation_failing_after_forgetting_one_segment_keeps_the_others(env):
    env.llm.responses = [RuntimeError("LLM failed")]
    env.llm.gate = asyncio.Event()
    env.llm.entered = asyncio.Event()
    task = asyncio.create_task(_post(env, _segments_body()))
    await asyncio.wait_for(env.llm.entered.wait(), timeout=5)
    await _forget(env, GP)                                   # 只清其中一段的 subject
    env.llm.gate.set()
    with pytest.raises(RuntimeError):
        await asyncio.wait_for(task, timeout=5)
    env.llm.gate = None
    env.llm.responses = [PART_ONLY_BATCH_FACTS]
    prompts = []
    real_llm = env.fs._allm_call_with_retries

    async def recording(prompt, lanlan_name, **kwargs):
        prompts.append(str(prompt))
        return await real_llm(prompt, lanlan_name, **kwargs)

    env.fs._allm_call_with_retries = recording
    again = await _post(env, _segments_body())
    # 只丢被清的那一段，另一段照常补写，而不是整键取消
    assert _facts_of(env, GP) == [] and len(_facts_of(env, PART)) == 2
    assert [seg["created"] for seg in again["segments"]] == [0, 2]
    # 被清参与者的原文不再送去抽取
    assert prompts and all("我最喜欢晒太阳了" not in prompt for prompt in prompts)


async def test_terminal_key_record_without_request_identity_fails_closed(env):
    await env.idem.update_key(NAME, KEY_GROUP, env.idem.transition("done"))   # 记录坏了：没有请求身份
    with pytest.raises(HTTPException) as excinfo:
        await _post(env, _single_body())
    assert excinfo.value.status_code == 503 and env.llm.calls == 0


@pytest.mark.parametrize("damage", ["applied_seq_bool", "effect_key_foreign"])
async def test_damaged_journal_markers_fail_closed(env, damage):
    env.llm.responses = [SINGLE_FACTS]
    original = _fail_on_item(env, failing_seq=0)
    with pytest.raises(HTTPException):
        await _post(env, _single_body())
    env.monkeypatch.setattr(env.routes, "_apply_keyed_item", original)    # 重试时应用本身不再出错
    path = _staging_file(env, KEY_GROUP)
    staging = json.loads(path.read_text(encoding="utf-8"))
    facts_item = next(item for item in staging["items"] if item["kind"] == "facts")
    if damage == "applied_seq_bool":
        staging["applied"].append({"seq": True})            # True == 1：会把第 1 项当成已应用跳过
    else:
        facts_item["effect_keys"][0] = "0" * 32 + ":7"      # 形状合法、却不是这个键推出来的效果键
    path.write_text(json.dumps(staging, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(HTTPException) as excinfo:
        await _post(env, _single_body())
    assert excinfo.value.status_code == 503 and _facts_of(env, GROUP) == []


async def test_forget_cancels_a_pending_key_whose_generation_failed_earlier(env):
    env.llm.responses = [RuntimeError("LLM failed")]
    with pytest.raises(RuntimeError):
        await _post(env, _single_body())
    assert _key_state(env, KEY_GROUP) == "pending"       # 生成失败，留下 pending、没有暂存
    await _forget(env, GROUP)
    # 键锁空着：清除把它取消，之后同键重试只得到 duplicate
    assert _key_state(env, KEY_GROUP) == "cancelled"
    env.llm.responses = [SINGLE_FACTS]
    again = await _post(env, _single_body())
    assert again["duplicate"] is True and _facts_of(env, GROUP) == []


@pytest.mark.parametrize("damage", ["negative_segment", "segment_out_of_range", "seq_shuffled", "effect_keys_short"])
async def test_damaged_staging_items_fail_closed(env, damage):
    env.llm.responses = [SINGLE_FACTS]
    original = _fail_on_item(env, failing_seq=0)
    with pytest.raises(HTTPException):
        await _post(env, _single_body())
    env.monkeypatch.setattr(env.routes, "_apply_keyed_item", original)    # 重试时应用本身不再出错
    path = _staging_file(env, KEY_GROUP)
    staging = json.loads(path.read_text(encoding="utf-8"))
    facts_item = next(item for item in staging["items"] if item["kind"] == "facts")
    if damage == "negative_segment":
        facts_item["segment"] = -1
    elif damage == "segment_out_of_range":
        facts_item["segment"] = 5
    elif damage == "seq_shuffled":
        facts_item["seq"] = 99
    else:
        facts_item["effect_keys"] = facts_item["effect_keys"][:1]
    path.write_text(json.dumps(staging, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(HTTPException) as excinfo:
        await _post(env, _single_body())
    # 条目坏了绝不应用（负段号会写到另一个 subject 上）：503，什么都不写
    assert excinfo.value.status_code == 503 and _facts_of(env, GROUP) == []


async def test_deeply_nested_staging_does_not_block_a_forget(env):
    path = _staging_file(env, "deep-key")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[" * 100000 + "]" * 100000, encoding="utf-8")
    result = await _forget(env, GROUP)
    # 嵌套过深的暂存按读不出处理，不让每次清除都 500
    assert result["status"] == "forgotten"


async def test_client_key_named_like_the_forget_fence_does_not_deadlock(env):
    idem = env.idem
    clash = f"forget-fence:{GROUP_KEY}"
    await idem.update_key(NAME, clash, idem.transition(
        "pending", request={"shape": "single", "wire_keys": [GROUP_KEY], "content_hash": "h"},
    ))
    # 栅栏用独立的登记表：与它同名的客户端键不会让清除自己等自己
    result = await asyncio.wait_for(_forget(env, GROUP, forget_epoch=1), timeout=5)
    assert result["status"] == "forgotten" and _key_state(env, clash) == "cancelled"


async def test_keyed_write_requires_the_default_scope(env):
    with pytest.raises(HTTPException) as excinfo:
        await _post(env, _single_body(subject={**GROUP, "scope": "another_scope"}))
    # 墓碑 / 取消 / 已擦代数都按 kind:id 记：带键写入只接受默认 scope
    assert excinfo.value.status_code == 422 and env.llm.calls == 0


async def test_epoch_forget_requires_the_default_scope(env):
    with pytest.raises(HTTPException) as excinfo:
        await _forget(env, {**GROUP, "scope": "another_scope"}, forget_epoch=3)
    assert excinfo.value.status_code == 422


async def test_forgetting_another_scope_does_not_cancel_default_scope_writes(env):
    env.llm.responses = [SINGLE_FACTS]
    _fail_on_item(env, failing_seq=1)
    with pytest.raises(HTTPException):
        await _post(env, _single_body())
    assert _key_state(env, KEY_GROUP) == "pending"
    result = await _forget(env, {**GROUP, "scope": "another_scope"})
    # 清的是同一 kind:id 的另一个 scope：默认 scope 的带键写入与它无关，不取消
    assert result["status"] == "forgotten"
    assert _key_state(env, KEY_GROUP) == "pending" and _staging_file(env, KEY_GROUP).exists()


async def test_pending_key_issued_after_the_forget_is_not_cancelled_without_staging(env):
    env.llm.responses = [RuntimeError("LLM failed")]
    with pytest.raises(RuntimeError):
        await _post(env, _single_body(subject_epochs={GROUP_KEY: 5}))   # 知道代数 5 的清除之后才发起
    assert _key_state(env, KEY_GROUP) == "pending"
    await _forget(env, GROUP, forget_epoch=5)
    # 没有暂存，但记录里的请求代数 >= 清除代数：合法的新写入，不取消
    assert _key_state(env, KEY_GROUP) == "pending"
    env.llm.responses = [SINGLE_FACTS]
    again = await _post(env, _single_body(subject_epochs={GROUP_KEY: 5}))
    assert again["created"] == 2


async def test_corrupt_tombstone_file_does_not_block_an_epoch_forget(env):
    env.llm.responses = [SINGLE_FACTS]
    await _post(env, _single_body(key=None, display_name=None))
    path = Path(env.idem.tombstones_path(NAME))
    path.write_text("{torn", encoding="utf-8")                 # 整个墓碑文件读不出
    with pytest.raises(HTTPException) as excinfo:
        await _forget(env, GROUP, forget_epoch=2)
    # 辅助文件坏了不挡隐私清除：照常擦除；完成标记记不上，回 503 而不是成功
    assert excinfo.value.status_code == 503 and _facts_of(env, GROUP) == []
    # 文件修好之后重试：记上完成标记、回成功
    path.write_text("{}", encoding="utf-8")
    result = await _forget(env, GROUP, forget_epoch=2)
    assert result["status"] == "forgotten"
    assert env.idem.erased_epoch(await env.idem.read_tombstones(NAME), GROUP_KEY) == 2


async def test_keyed_request_without_epochs_is_rejected(env):
    body = _single_body()
    body.pop("subject_epochs")
    with pytest.raises(HTTPException) as excinfo:
        await _post(env, body)
    # 缺了的代数不能当成 0（否则被带代数清除过的 subject 之后的写入会全部被静默丢弃）
    assert excinfo.value.status_code == 422 and env.llm.calls == 0


async def test_retry_whose_every_segment_was_forgotten_skips_the_llm(env):
    await env.idem.update_key(NAME, KEY_GROUP, env.idem.transition(
        "pending", request={"shape": "single", "wire_keys": [GROUP_KEY], "content_hash": "h"},
    ))

    def mark(old):
        return {**old, "forgotten_keys": [GROUP_KEY]}

    await env.idem.update_key(NAME, KEY_GROUP, mark)
    env.monkeypatch.setattr(env.routes, "_keyed_request_hash", lambda _req: "h")
    result = await _post(env, _single_body())
    # 产物注定全部丢弃：不再持键锁跑一遍抽取，直接按取消收尾
    assert result["duplicate"] is True and env.llm.calls == 0
    assert _key_state(env, KEY_GROUP) == "cancelled"


async def test_key_file_read_rides_out_a_concurrent_replace(env, monkeypatch):
    from utils import file_utils

    await env.idem.update_key(NAME, KEY_GROUP, env.idem.transition("pending"))
    real_read = file_utils.read_json
    state = {"busy": True}

    def busy_once(path, **kwargs):
        if state["busy"]:
            state["busy"] = False
            exc = PermissionError(13, "sharing violation")
            exc.winerror = 32                                  # Windows：别的写入正在 os.replace
            raise exc
        return real_read(path, **kwargs)

    monkeypatch.setattr(file_utils, "read_json", busy_once)
    # 替换窗口里的瞬时共享冲突退避重试，不当成「状态读不出」
    assert (await env.idem.read_key(NAME, KEY_GROUP))["state"] == "pending"


async def test_forget_fails_when_the_completion_marker_cannot_be_written(env):
    async def broken(*_args, **_kwargs):
        raise OSError("disk full")

    env.monkeypatch.setattr(env.idem, "mark_tombstone_erased", broken)
    with pytest.raises(HTTPException) as excinfo:
        await _forget(env, GROUP, forget_epoch=1)
    # 「这个代数已擦完」没落盘就不回成功：调用方重试到它落盘
    assert excinfo.value.status_code == 500


async def test_damaged_applied_result_fields_fail_closed(env):
    env.llm.responses = [SINGLE_FACTS]
    original = _fail_on_item(env, failing_seq=1)               # seq0 已应用，seq1 中断
    with pytest.raises(HTTPException):
        await _post(env, _single_body())
    env.monkeypatch.setattr(env.routes, "_apply_keyed_item", original)
    path = _staging_file(env, KEY_GROUP)
    staging = json.loads(path.read_text(encoding="utf-8"))
    staging["applied"][0]["fact_ids"] = 1                      # 结果字段坏了
    path.write_text(json.dumps(staging, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(HTTPException) as excinfo:
        await _post(env, _single_body())
    assert excinfo.value.status_code == 503


async def test_fresh_write_routed_into_the_fanout_is_not_cancelled(env):
    idem = env.idem
    routed = {"subject_kind": "participant", "subject_id": "neko_visit:canonical", "scope": "participant:neko_visit:canonical"}
    await idem.update_key(NAME, KEY_GROUP, idem.transition("pending"))
    await idem.write_staging(NAME, KEY_GROUP, {
        "shape": "single", "subjects": [GROUP_KEY], "epochs": {GROUP_KEY: 3}, "created_at": time.time(),
        "segments": [{"wire_key": GROUP_KEY, "subject": routed, "tombstone_keys": [GROUP_KEY]}],
        "items": [], "applied": [],
    })
    # 清除 GROUP 扇出到它路由后的 canonical：交集是两个 key，但这段的 wire 就是请求 subject、
    # 代数够新，它是清除之后的合法写入
    cancelled = await env.routes._cancel_staged_writes_for_subjects(
        NAME, {GROUP_KEY, "participant:neko_visit:canonical"},
        request_subject_key=GROUP_KEY, forget_epoch=3,
    )
    assert cancelled == 0 and _key_state(env, KEY_GROUP) == "pending"


def test_replaying_a_forgotten_marker_drops_locale_items_too(env):
    doc = {
        "segments": [{"wire_key": GROUP_KEY, "subject": GROUP}],
        "items": [
            {"seq": 0, "kind": "locale", "segment": 0, "language": "zh", "order": 1},
            {"seq": 1, "kind": "facts", "segment": 0, "facts": SINGLE_FACTS, "effect_keys": ["a:0", "a:1"]},
        ],
        "applied": [],
    }
    env.routes._drop_segments_for_keys(doc, {GROUP_KEY})
    # 清除之后才预留的语言序号证明不了请求早于清除：一并丢弃
    assert {entry["seq"] for entry in doc["applied"]} == {0, 1}


@pytest.mark.parametrize("damage", ["foreign_destination", "malformed_fact"])
async def test_restored_journal_destinations_and_facts_are_validated(env, damage):
    env.llm.responses = [SINGLE_FACTS]
    original = _fail_on_item(env, failing_seq=0)
    with pytest.raises(HTTPException):
        await _post(env, _single_body())
    env.monkeypatch.setattr(env.routes, "_apply_keyed_item", original)
    path = _staging_file(env, KEY_GROUP)
    staging = json.loads(path.read_text(encoding="utf-8"))
    if damage == "foreign_destination":
        staging["segments"][0]["subject"] = PART                # 写入目标被改成另一个合法 subject
    else:
        facts_item = next(item for item in staging["items"] if item["kind"] == "facts")
        facts_item["facts"][0] = "not a fact"                  # 坏掉的事实会被静默跳过却记成已应用
    path.write_text(json.dumps(staging, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(HTTPException) as excinfo:
        await _post(env, _single_body())
    assert excinfo.value.status_code == 503
    assert _facts_of(env, GROUP) == [] and _facts_of(env, PART) == []


@pytest.mark.parametrize("damage", [
    "destination_scope", "tombstone_keys_emptied", "epochs_raised", "request_hash_removed",
    "applied_facts_without_evidence", "applied_facts_drop_marker_typo", "applied_facts_empty_ids",
    "locale_order_string",
    "locale_language_unsupported", "locale_language_other",
])
async def test_more_journal_damage_fails_closed(env, damage):
    env.llm.responses = [SINGLE_FACTS]
    failing_seq = 1 if damage.startswith("applied_facts_") else 0
    original = _fail_on_item(env, failing_seq=failing_seq)
    body = _single_body(language="zh") if damage.startswith("locale_") else _single_body()
    with pytest.raises(HTTPException):
        await _post(env, body)
    env.monkeypatch.setattr(env.routes, "_apply_keyed_item", original)
    path = _staging_file(env, KEY_GROUP)
    staging = json.loads(path.read_text(encoding="utf-8"))
    if damage == "destination_scope":
        staging["segments"][0]["subject"] = {**staging["segments"][0]["subject"], "scope": "another_scope"}
    elif damage == "tombstone_keys_emptied":
        staging["segments"][0]["tombstone_keys"] = []
    elif damage == "epochs_raised":
        staging["epochs"] = {GROUP_KEY: 99}
    elif damage == "request_hash_removed":
        staging.pop("request_hash")
    elif damage == "applied_facts_without_evidence":
        facts_seq = next(item["seq"] for item in staging["items"] if item["kind"] == "facts")
        staging["applied"] = [{"seq": facts_seq} if e["seq"] == facts_seq else e for e in staging["applied"]]
        assert any(e == {"seq": facts_seq} for e in staging["applied"])
    elif damage == "applied_facts_empty_ids":
        # 空的 fact_ids 也是合法结果：只凭它不能证明这一项应用过
        facts_seq = next(item["seq"] for item in staging["items"] if item["kind"] == "facts")
        staging["applied"] = [
            {"seq": facts_seq, "fact_ids": []} if e["seq"] == facts_seq else e for e in staging["applied"]
        ]
    elif damage == "applied_facts_drop_marker_typo":
        # 名字像丢弃标记、但不是本模块写的那几个：不能当成完成证据
        facts_seq = next(item["seq"] for item in staging["items"] if item["kind"] == "facts")
        staging["applied"] = [
            {"seq": facts_seq, "dropped_typo": True} if e["seq"] == facts_seq else e
            for e in staging["applied"]
        ]
        assert any(e == {"seq": facts_seq, "dropped_typo": True} for e in staging["applied"])
    else:
        locale = next((item for item in staging["items"] if item["kind"] == "locale"), None)
        if locale is None:
            pytest.skip("no locale item reserved for this request")
        if damage == "locale_order_string":
            locale["order"] = "1"
        elif damage == "locale_language_other":
            locale["language"] = "en"                           # 另一个受支持的语言码，与请求不符
        else:
            locale["language"] = "invalid"                    # 会被语言存储转成 None、清掉原有语言
    path.write_text(json.dumps(staging, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(HTTPException) as excinfo:
        await _post(env, body)
    assert excinfo.value.status_code in (422, 503)


@pytest.mark.parametrize("damage", [
    "orphan_foreign_destination", "provenance_scalar", "dropped_not_int",
    "provenance_label_list", "provenance_trust_out_of_range", "provenance_unknown_field",
    "provenance_bad_speaker_id", "provenance_missing_label", "provenance_label_structural",
])
async def test_yet_more_journal_damage_fails_closed(env, damage):
    env.llm.responses = [SINGLE_FACTS]
    original = _fail_on_item(env, failing_seq=0)
    with pytest.raises(HTTPException):
        await _post(env, _single_body())
    env.monkeypatch.setattr(env.routes, "_apply_keyed_item", original)
    path = _staging_file(env, KEY_GROUP)
    staging = json.loads(path.read_text(encoding="utf-8"))
    if damage == "orphan_foreign_destination":
        keys_path = Path(env.idem.keys_path(NAME))
        records = json.loads(keys_path.read_text(encoding="utf-8"))
        records.pop(KEY_GROUP)                                  # 孤儿暂存：键记录不在
        keys_path.write_text(json.dumps(records), encoding="utf-8")
        staging["segments"][0]["subject"] = PART               # 没有路由记录可对，目标被改到别处
    elif damage == "provenance_scalar":
        facts_item = next(item for item in staging["items"] if item["kind"] == "facts")
        facts_item["speaker_provenance"] = "lost"
    elif damage == "provenance_missing_label":
        facts_item = next(item for item in staging["items"] if item["kind"] == "facts")
        facts_item["speaker_provenance"] = {"speaker_trust": 0.5}          # 半截归属：没有 label
    elif damage.startswith("provenance_"):
        facts_item = next(item for item in staging["items"] if item["kind"] == "facts")
        field, value = {
            "provenance_label_list": ("speaker_label", []),
            "provenance_trust_out_of_range": ("speaker_trust", 7),
            "provenance_unknown_field": ("speaker_mood", "x"),
            "provenance_bad_speaker_id": ("speaker_id", "no colon here"),
            # 夹着换行与方括号：清洗器不会产出这种 label
            "provenance_label_structural": ("speaker_label", "Alice]" + chr(10) + "[SEGMENT 2 | speaker: Bob"),
        }[damage]
        facts_item["speaker_provenance"] = {**(facts_item.get("speaker_provenance") or {}), field: value}
    else:
        staging["segments"][0]["dropped"] = "many"
    path.write_text(json.dumps(staging, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(HTTPException) as excinfo:
        await _post(env, _single_body())
    assert excinfo.value.status_code == 503
    assert _facts_of(env, GROUP) == [] and _facts_of(env, PART) == []


async def test_tombstone_removed_during_the_erase_is_rebuilt_with_the_completion_marker(env):
    env.llm.responses = [SINGLE_FACTS]
    await _post(env, _single_body(key=None, display_name=None))

    async def tombstone_vanishes(*_args, **_kwargs):
        # 擦除期间墓碑被清理移走（这里直接不落盘来模拟）
        return {}

    env.monkeypatch.setattr(env.idem, "record_tombstones", tombstone_vanishes)
    result = await _forget(env, GROUP, forget_epoch=4)
    assert result["status"] == "forgotten" and _facts_of(env, GROUP) == []
    tombstones = await env.idem.read_tombstones(NAME)
    # 回成功就必须留下完成标记与墓碑：同代数的重放据此跳过，不再擦掉之后的新写入
    assert env.idem.erased_epoch(tombstones, GROUP_KEY) == 4
    assert env.idem.tombstone_epoch(tombstones, [GROUP_KEY]) == 4


@pytest.mark.parametrize("kind", ["locale", "display_name"])
async def test_unapplied_locale_or_display_item_marked_applied_without_evidence_fails_closed(env, kind):
    env.llm.responses = [SINGLE_FACTS]
    original = _fail_on_item(env, failing_seq=0)                # 一项都没应用就崩
    with pytest.raises(HTTPException):
        await _post(env, _single_body(language="zh"))
    env.monkeypatch.setattr(env.routes, "_apply_keyed_item", original)
    path = _staging_file(env, KEY_GROUP)
    staging = json.loads(path.read_text(encoding="utf-8"))
    seq = next(item["seq"] for item in staging["items"] if item["kind"] == kind)
    staging["applied"] = [*staging["applied"], {"seq": seq}]   # 只剩序号、没有完成证据
    path.write_text(json.dumps(staging, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(HTTPException) as excinfo:
        await _post(env, _single_body(language="zh"))
    assert excinfo.value.status_code == 503


async def test_retry_after_locale_and_facts_applied_resumes(env):
    env.llm.responses = [SINGLE_FACTS]
    staged_kinds = []
    original = env.routes._apply_keyed_item

    async def _flaky(lanlan_name, item, segment, generation):
        staged_kinds.append(item["kind"])
        if item["kind"] == "display_name":
            raise RuntimeError("injected crash during apply")
        return await original(lanlan_name, item, segment, generation)

    env.monkeypatch.setattr(env.routes, "_apply_keyed_item", _flaky)
    with pytest.raises(HTTPException):
        await _post(env, _single_body(language="zh"))
    env.monkeypatch.setattr(env.routes, "_apply_keyed_item", original)
    applied = json.loads(_staging_file(env, KEY_GROUP).read_text(encoding="utf-8"))["applied"]
    # 已应用的语言项、事实项都带着各自的完成证据，恢复时校验得过、接着补应用
    assert any(entry.get("locale_recorded") is True for entry in applied)
    result = await _post(env, _single_body(language="zh"))
    assert result["created"] == 2 and _key_state(env, KEY_GROUP) == "done"


async def test_cleanup_waits_for_a_retry_claiming_an_orphan_staging(env):
    idem = env.idem
    now = time.time()
    path = Path(idem.staging_path(NAME, "orphan-key"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"key": "orphan-key", "subjects": [], "created_at": now - 500}), encoding="utf-8")
    lock = idem.key_lock(NAME, "orphan-key")
    await lock.acquire()                                         # 同键重试正持键锁认领这份孤儿暂存
    try:
        sweep = asyncio.create_task(idem.cleanup_expired([NAME], ttl_s=100.0, now=now))
        for _ in range(20):
            await asyncio.sleep(0)
        await idem.update_key(NAME, "orphan-key", idem.transition("pending"))
    finally:
        lock.release()
    report = await sweep
    # 枚举前的键记录快照里它还是孤儿；删之前拿键锁重读，已被认领成 pending 的不删
    assert path.exists() and report["staging_removed"] == 0


@pytest.mark.parametrize("damage", ["applied_object", "items_scalar", "item_scalar", "items_empty_object",
                                    "applied_scalar_entry", "applied_bad_seq", "item_bad_kind",
                                    "kept_effect_keys_scalar", "applied_null"])
async def test_malformed_partly_forgotten_journal_does_not_block_the_forget(env, damage):
    env.llm.responses = [BATCH_FACTS]
    original = env.routes._apply_keyed_item

    async def _flaky(lanlan_name, item, segment, generation):
        if item["segment"] == 1 and item["kind"] == "facts":
            raise RuntimeError("injected crash before the second segment")
        return await original(lanlan_name, item, segment, generation)

    env.monkeypatch.setattr(env.routes, "_apply_keyed_item", _flaky)
    with pytest.raises(HTTPException):
        await _post(env, _segments_body())
    env.monkeypatch.setattr(env.routes, "_apply_keyed_item", original)
    path = _staging_file(env, KEY_SEGMENTS)
    staging = json.loads(path.read_text(encoding="utf-8"))
    if damage == "applied_object":
        staging["applied"] = {}
    elif damage == "items_scalar":
        staging["items"] = 1
    elif damage == "applied_null":
        staging["applied"] = None                                # 字段在、值却是 null
    elif damage == "kept_effect_keys_scalar":
        # 没被清的那段（PART）还没应用的事实项，载荷坏了：留下来也永远重放不了
        part_facts = next(item for item in staging["items"] if item["kind"] == "facts" and item["segment"] == 1)
        part_facts["effect_keys"] = 5
    elif damage == "item_bad_kind":
        staging["items"][0]["kind"] = "bogus"                   # 条目是对象，但类型坏了
    elif damage == "applied_bad_seq":
        staging["applied"].append({"seq": "bad"})               # 对象，但序号坏了
    elif damage == "applied_scalar_entry":
        staging["applied"].append(7)                             # 已应用记录里混进一个标量
    elif damage == "items_empty_object":
        staging["items"] = {}                                    # 假值：不能经 `or []` 当成空列表放过
    else:
        staging["items"].append(7)
    path.write_text(json.dumps(staging, ensure_ascii=False), encoding="utf-8")
    assert _facts_of(env, GP)
    result = await _forget(env, GP)
    # 只丢被清段做不了：整个键按取消处理，隐私擦除照常完成
    assert result["status"] == "forgotten" and _facts_of(env, GP) == []
    assert _key_state(env, KEY_SEGMENTS) == "cancelled"


async def test_restored_journal_missing_a_locale_item_fails_closed(env):
    env.llm.responses = [SINGLE_FACTS]
    original = _fail_on_item(env, failing_seq=0)
    with pytest.raises(HTTPException):
        await _post(env, _single_body(language="zh"))
    env.monkeypatch.setattr(env.routes, "_apply_keyed_item", original)
    path = _staging_file(env, KEY_GROUP)
    staging = json.loads(path.read_text(encoding="utf-8"))
    items = [item for item in staging["items"] if item["kind"] != "locale"]
    assert len(items) < len(staging["items"])
    for position, item in enumerate(items):
        item["seq"] = position                                   # 序号重排后其余检查都过得去
    staging["items"] = items
    path.write_text(json.dumps(staging, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(HTTPException) as excinfo:
        await _post(env, _single_body(language="zh"))
    # 语言在请求身份里、开轮时必有一项：整条没了就不能按它收尾
    assert excinfo.value.status_code == 503 and _key_state(env, KEY_GROUP) == "pending"


async def test_restored_journal_missing_its_display_item_is_completed_from_the_retry(env):
    env.llm.responses = [SINGLE_FACTS]
    original = _fail_on_item(env, failing_seq=0)
    with pytest.raises(HTTPException):
        await _post(env, _single_body())
    env.monkeypatch.setattr(env.routes, "_apply_keyed_item", original)
    path = _staging_file(env, KEY_GROUP)
    staging = json.loads(path.read_text(encoding="utf-8"))
    assert staging["items"][-1]["kind"] == "display_name"
    staging["items"].pop()                                       # 末尾的显示名项整条丢了
    path.write_text(json.dumps(staging, ensure_ascii=False), encoding="utf-8")
    result = await _post(env, _single_body())
    # 用重试请求带的显示名补回这一项，不能就此收尾、永久漏掉
    assert result["created"] == 2 and _key_state(env, KEY_GROUP) == "done"
    assert (GROUP_KEY, "串门群") in env.persona.display_names


async def test_damaged_erased_marker_never_skips_a_forget(env):
    env.llm.responses = [SINGLE_FACTS]
    await _post(env, _single_body(key=None, display_name=None))
    path = Path(env.idem.tombstones_path(NAME))
    path.write_text(json.dumps({GROUP_KEY: {"forget_epoch": "bad", "erased_epoch": 999}}), encoding="utf-8")
    with pytest.raises(HTTPException):
        await _forget(env, GROUP, forget_epoch=5)
    # 围栏坏了的行上的完成标记不算数：照常擦除，不当成已擦过回 duplicate
    assert _facts_of(env, GROUP) == []
    assert env.idem.erased_epoch({GROUP_KEY: {"forget_epoch": 3, "erased_epoch": 4}}, GROUP_KEY) is None


async def test_erase_behind_a_higher_fence_marks_that_fence_erased(env):
    env.llm.responses = [SINGLE_FACTS]
    await _post(env, _single_body(key=None, display_name=None))
    # 更新的清除（代数 9）已立起墓碑、还没擦就失败了
    await env.idem.record_tombstones(NAME, {GROUP_KEY}, 9)
    result = await _forget(env, GROUP, forget_epoch=4)
    assert result["status"] == "forgotten" and _facts_of(env, GROUP) == []
    # 这次擦除在代数 9 的围栏之后进行：一并记为擦到 9，代数 9 的重试不再擦掉之后的合法写入
    assert env.idem.erased_epoch(await env.idem.read_tombstones(NAME), GROUP_KEY) == 9


async def test_forget_scrubs_a_journal_whose_subject_index_is_damaged(env):
    env.llm.responses = [SINGLE_FACTS]
    original = _fail_on_item(env, failing_seq=0)
    with pytest.raises(HTTPException):
        await _post(env, _single_body())
    env.monkeypatch.setattr(env.routes, "_apply_keyed_item", original)
    path = _staging_file(env, KEY_GROUP)
    staging = json.loads(path.read_text(encoding="utf-8"))
    staging.pop("subjects")                                    # 索引丢了，各段仍指认被清的 subject
    path.write_text(json.dumps(staging, ensure_ascii=False), encoding="utf-8")
    Path(env.idem.keys_path(NAME)).unlink()                    # 孤儿：没有键记录可按记录取消
    await _forget(env, GROUP)
    # 不能凭坏索引跳过：抽取原文不能留在磁盘上，之后的同键重试也不能把清除前的事实写回去
    assert "猫薄荷" not in (path.read_text(encoding="utf-8") if path.exists() else "")
    result = await _post(env, _single_body())
    assert result.get("duplicate") is True and _facts_of(env, GROUP) == []


async def test_restored_locale_item_with_non_positive_order_fails_closed(env):
    env.llm.responses = [SINGLE_FACTS]
    original = _fail_on_item(env, failing_seq=0)
    with pytest.raises(HTTPException):
        await _post(env, _single_body(language="zh"))
    env.monkeypatch.setattr(env.routes, "_apply_keyed_item", original)
    path = _staging_file(env, KEY_GROUP)
    staging = json.loads(path.read_text(encoding="utf-8"))
    next(item for item in staging["items"] if item["kind"] == "locale")["order"] = -1
    path.write_text(json.dumps(staging, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(HTTPException) as excinfo:
        await _post(env, _single_body(language="zh"))
    assert excinfo.value.status_code == 503


async def test_cleanup_continues_past_a_malformed_key_record(env):
    idem = env.idem
    now = time.time()
    keys_path = Path(idem.keys_path(NAME))
    keys_path.parent.mkdir(parents=True, exist_ok=True)
    keys_path.write_text(json.dumps({"bad-key": "not a record", "done-key": {"state": "done"}}), encoding="utf-8")
    paths = {}
    for key in ("bad-key", "done-key"):
        paths[key] = Path(idem.staging_path(NAME, key))
        paths[key].parent.mkdir(parents=True, exist_ok=True)
        paths[key].write_text(json.dumps({"key": key, "subjects": [], "created_at": now - 500}), encoding="utf-8")
    real_list = idem.list_staging

    async def bad_first(name):
        rows = await real_list(name)
        return sorted(rows, key=lambda row: Path(row[0]) != paths["bad-key"])   # 坏记录的暂存先被扫到

    env.monkeypatch.setattr(idem, "list_staging", bad_first)
    report = await idem.cleanup_expired([NAME], ttl_s=100.0, now=now)
    # 坏的那条只保留它自己的暂存，其余过期暂存照常清理
    assert paths["bad-key"].exists() and not paths["done-key"].exists()
    assert report["staging_removed"] == 1


async def test_forget_drops_an_unreadable_orphan_journal(env):
    owned = Path(env.idem.staging_path(NAME, "owned-key"))
    orphan = Path(env.idem.staging_path(NAME, "orphan-key"))
    orphan.parent.mkdir(parents=True, exist_ok=True)
    await env.idem.update_key(NAME, "owned-key", env.idem.transition("done"))
    for path in (owned, orphan):
        path.write_text('{"key": "x", "facts": ["猫薄荷"', encoding="utf-8")        # 读不出
    await _forget(env, GROUP)
    # 没有键记录认领、读不出的暂存认不出涉及谁：删掉，不留抽取原文、也不让修好后被认领
    assert not orphan.exists()
    # 有键记录认领的不归这一步管
    assert owned.exists()


async def test_retry_spelling_out_the_default_scope_is_the_same_request(env):
    from memory.scopes import MemorySubject

    env.llm.responses = [SINGLE_FACTS]
    original = _fail_on_item(env, failing_seq=0)
    with pytest.raises(HTTPException):
        await _post(env, _single_body())
    env.monkeypatch.setattr(env.routes, "_apply_keyed_item", original)
    default_scope = MemorySubject.create(GROUP["subject_kind"], GROUP["subject_id"]).scope
    result = await _post(env, _single_body(subject={**GROUP, "scope": default_scope}))
    # 省略 scope 与显式写默认 scope 是同一个 subject：同键重试照常接着应用，不回 422
    assert result["created"] == 2 and env.llm.calls == 1


async def test_over_fence_erased_marker_is_repaired_by_the_next_erase(env):
    env.llm.responses = [SINGLE_FACTS]
    await _post(env, _single_body(key=None, display_name=None))
    path = Path(env.idem.tombstones_path(NAME))
    path.write_text(json.dumps({GROUP_KEY: {"forget_epoch": 5, "erased_epoch": 999, "forgotten_at": 1.0}}),
                    encoding="utf-8")
    result = await _forget(env, GROUP, forget_epoch=5)
    assert result["status"] == "forgotten" and _facts_of(env, GROUP) == []
    # 回成功就必须留下读端认的完成标记：超出围栏的坏标记被这次擦除改写成 5
    assert env.idem.erased_epoch(await env.idem.read_tombstones(NAME), GROUP_KEY) == 5


async def test_cancelled_flag_on_an_unstripped_journal_fails_closed(env):
    env.llm.responses = [SINGLE_FACTS]
    original = _fail_on_item(env, failing_seq=0)
    with pytest.raises(HTTPException):
        await _post(env, _single_body())
    env.monkeypatch.setattr(env.routes, "_apply_keyed_item", original)
    path = _staging_file(env, KEY_GROUP)
    staging = json.loads(path.read_text(encoding="utf-8"))
    staging["cancelled_by_forget"] = True                       # 只改了一个布尔值，条目都还在
    path.write_text(json.dumps(staging, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(HTTPException) as excinfo:
        await _post(env, _single_body())
    # 不是清除写出的抹干净形状：不能凭这个布尔值把整个请求记成取消
    assert excinfo.value.status_code == 503 and _key_state(env, KEY_GROUP) == "pending"
    assert path.exists()


async def test_forgotten_locale_only_segment_gets_no_display_name_on_retry(env):
    env.llm.responses = [[
        {"segment": 1, "facts": []},                             # 被清的那段没抽出事实
        {"segment": 2, "facts": [{"text": "Mika 的猫下午在窗台睡觉", "importance": 6}]},
    ]]
    body = _segments_body(language="zh")
    body["segments"][0].pop("display_name")                      # 开轮时这段也没有显示名：只剩语言项
    original = env.routes._apply_keyed_item

    async def _flaky(lanlan_name, item, segment, generation):
        if item["segment"] == 1 and item["kind"] == "facts":
            raise RuntimeError("injected crash before the second segment")
        return await original(lanlan_name, item, segment, generation)

    env.monkeypatch.setattr(env.routes, "_apply_keyed_item", _flaky)
    with pytest.raises(HTTPException):
        await _post(env, body)
    env.monkeypatch.setattr(env.routes, "_apply_keyed_item", original)
    await _forget(env, GP)
    assert _key_state(env, KEY_SEGMENTS) == "pending"
    retry = _segments_body(language="zh")                       # 重试带上了显示名（不在请求身份里）
    await _post(env, retry)
    # 被清除丢弃的段不补显示名：清除前的键不能把元数据盖到之后重建的 section 上
    assert (GP_KEY, "团子") not in env.persona.display_names
    assert (PART_KEY, "Mika") in env.persona.display_names


async def test_damaged_forgotten_keys_do_not_block_the_erase(env):
    env.llm.responses = [SINGLE_FACTS]
    await _post(env, _single_body(key=None, display_name=None))
    await env.idem.update_key(NAME, KEY_GROUP, env.idem.transition(
        "pending", request={"shape": "single", "wire_keys": [GROUP_KEY], "content_hash": "h"},
    ))

    def damage(old):
        return {**old, "forgotten_keys": [{"bad": 1}, "kept:key"]}   # 夹着对象的坏旧值

    await env.idem.update_key(NAME, KEY_GROUP, damage)
    lock = env.idem.key_lock(NAME, KEY_GROUP)
    await lock.acquire()                                         # 同键请求正在生成、还没有暂存
    try:
        result = await _forget(env, GROUP)
    finally:
        lock.release()
    # 坏的辅助状态不能让清除在擦除之前就 500：照常擦除，能用的旧键保留、补上这次的
    assert result["status"] == "forgotten" and _facts_of(env, GROUP) == []
    record = json.loads(Path(env.idem.keys_path(NAME)).read_text(encoding="utf-8"))[KEY_GROUP]
    assert record["forgotten_keys"] == sorted({"kept:key", GROUP_KEY})


async def test_retry_after_a_failed_generation_reuses_the_reserved_locale_order(env):
    env.llm.responses = [RuntimeError("LLM failed"), SINGLE_FACTS]
    with pytest.raises(RuntimeError):
        await _post(env, _single_body(language="zh"))
    record = json.loads(Path(env.idem.keys_path(NAME)).read_text(encoding="utf-8"))[KEY_GROUP]
    (first_order,) = record["locale_orders"]
    original = _fail_on_item(env, failing_seq=0)
    with pytest.raises(HTTPException):
        await _post(env, _single_body(language="zh"))
    env.monkeypatch.setattr(env.routes, "_apply_keyed_item", original)
    staging = json.loads(_staging_file(env, KEY_GROUP).read_text(encoding="utf-8"))
    locale = next(item for item in staging["items"] if item["kind"] == "locale")
    # 生成失败后的同键重试沿用第一次预留的序号，不拿更新的序号把旧请求往后排
    assert locale["order"] == first_order


async def test_malformed_locale_reservation_fails_closed(env):
    env.llm.responses = [RuntimeError("LLM failed"), SINGLE_FACTS]
    with pytest.raises(RuntimeError):
        await _post(env, _single_body(language="zh"))

    def damage(old):
        return {**old, "locale_orders": ["bad"]}

    await env.idem.update_key(NAME, KEY_GROUP, damage)
    with pytest.raises(HTTPException) as excinfo:
        await _post(env, _single_body(language="zh"))
    # 已有的预留坏了：不能另分一批记不上的新序号、每次重试都把旧请求往后排
    assert excinfo.value.status_code == 503 and env.llm.calls == 1


async def test_fenced_segment_does_not_move_the_trust_pool(env):
    captured = []
    real = env.routes._apply_trust_for_segments

    async def capture(states):
        captured.append(list(states))
        return await real(states)

    env.monkeypatch.setattr(env.routes, "_apply_trust_for_segments", capture)
    await _forget(env, GROUP, forget_epoch=5)                   # 清除之后才到的旧请求（代数 0）
    env.llm.responses = [SINGLE_FACTS]
    body = _single_body(
        speaker_label="Mika", speaker_id="neko_visit:5f2c1b7e", speaker_tier="none",
        speaker_activity_events=[{"id": "evt-00001", "count": 1}],
    )
    await _post(env, body)
    # 记忆被墓碑挡下的段，它的 activity 也不能进信赖池
    (states,) = captured
    assert env.routes._trust_mutation_for(states[0]) is None
    assert _facts_of(env, GROUP) == []


async def test_duplicate_forget_still_cancels_a_late_pre_forget_staging(env):
    env.llm.responses = [SINGLE_FACTS]
    await _forget(env, GROUP, forget_epoch=5)                   # 原擦除已完成
    real_apply = env.routes._apply_keyed_staging

    async def crash(*_args, **_kwargs):
        raise RuntimeError("process killed before applying")

    env.monkeypatch.setattr(env.routes, "_apply_keyed_staging", crash)
    with pytest.raises(HTTPException):
        await _post(env, _single_body())                        # 之后才到的清除前请求：落了暂存就崩
    env.monkeypatch.setattr(env.routes, "_apply_keyed_staging", real_apply)
    assert _staging_file(env, KEY_GROUP).exists()
    result = await _forget(env, GROUP, forget_epoch=5)          # 重放同一代数的清除
    # 不再擦存储（duplicate），但残留的明文暂存照常取消，不会被 TTL 清理永久保护
    assert result.get("duplicate") is True
    assert _key_state(env, KEY_GROUP) == "cancelled" and not _staging_file(env, KEY_GROUP).exists()


async def test_erase_completion_restores_a_fence_lowered_meanwhile(env):
    path = Path(env.idem.tombstones_path(NAME))
    path.parent.mkdir(parents=True, exist_ok=True)
    # 擦除期间旧的高围栏被清理移走、又被一次较低代数的清除重建成 3
    path.write_text(json.dumps({GROUP_KEY: {"forget_epoch": 3, "forgotten_at": 1.0}}), encoding="utf-8")
    await env.idem.mark_tombstone_erased(NAME, GROUP_KEY, 3, covered_epoch=8)
    row = json.loads(path.read_text(encoding="utf-8"))[GROUP_KEY]
    # 完成的是更高的代数：围栏抬回去、完成标记记 8，较高代数的重放据此跳过
    assert row["forget_epoch"] == 8 and row["erased_epoch"] == 8
    assert env.idem.erased_epoch({GROUP_KEY: row}, GROUP_KEY) == 8


async def test_swapped_segment_destinations_fail_closed(env):
    env.llm.responses = [BATCH_FACTS]
    original = env.routes._apply_keyed_item

    async def _flaky(lanlan_name, item, segment, generation):
        raise RuntimeError("injected crash before applying anything")

    env.monkeypatch.setattr(env.routes, "_apply_keyed_item", _flaky)
    with pytest.raises(HTTPException):
        await _post(env, _segments_body())
    env.monkeypatch.setattr(env.routes, "_apply_keyed_item", original)
    path = _staging_file(env, KEY_SEGMENTS)
    staging = json.loads(path.read_text(encoding="utf-8"))
    first, second = staging["segments"]
    first["subject"], second["subject"] = second["subject"], first["subject"]   # 两段目标互换
    path.write_text(json.dumps(staging, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(HTTPException) as excinfo:
        await _post(env, _segments_body())
    # 两个目标都在整批的路由集合里，但不在各自的位置上：不能按它写进彼此的记忆域
    assert excinfo.value.status_code == 503
    assert _facts_of(env, GP) == [] and _facts_of(env, PART) == []


async def test_stale_duplicate_forget_cancels_against_the_effective_fence(env):
    env.llm.responses = [SINGLE_FACTS]
    await _forget(env, GROUP, forget_epoch=10)                  # 围栏与完成标记都是 10
    real_apply = env.routes._apply_keyed_staging

    async def crash(*_args, **_kwargs):
        raise RuntimeError("process killed before applying")

    env.monkeypatch.setattr(env.routes, "_apply_keyed_staging", crash)
    with pytest.raises(HTTPException):
        await _post(env, _single_body(subject_epochs={GROUP_KEY: 7}))   # 早于围栏的迟到请求
    env.monkeypatch.setattr(env.routes, "_apply_keyed_staging", real_apply)
    result = await _forget(env, GROUP, forget_epoch=5)          # 陈旧的清除重放
    # 按有效围栏 10 比：代数 7 的暂存早于它，照常取消，不因 7 > 5 被当成合法新写入留下
    assert result.get("duplicate") is True
    assert _key_state(env, KEY_GROUP) == "cancelled"
    assert not _staging_file(env, KEY_GROUP).exists()


async def test_malformed_positional_routes_fail_closed(env):
    env.llm.responses = [BATCH_FACTS]

    async def _crash(lanlan_name, item, segment, generation):
        raise RuntimeError("injected crash before applying anything")

    original = env.routes._apply_keyed_item
    env.monkeypatch.setattr(env.routes, "_apply_keyed_item", _crash)
    with pytest.raises(HTTPException):
        await _post(env, _segments_body())
    env.monkeypatch.setattr(env.routes, "_apply_keyed_item", original)
    path = _staging_file(env, KEY_SEGMENTS)
    staging = json.loads(path.read_text(encoding="utf-8"))
    first, second = staging["segments"]
    first["subject"], second["subject"] = second["subject"], first["subject"]
    path.write_text(json.dumps(staging, ensure_ascii=False), encoding="utf-8")

    def truncate(old):
        return {**old, "routed_positions": old["routed_positions"][:1]}   # 截断按位置的路由

    await env.idem.update_key(NAME, KEY_SEGMENTS, truncate)
    with pytest.raises(HTTPException) as excinfo:
        await _post(env, _segments_body())
    # 有这个字段却坏了：不能退回只看集合的核对，否则互换的目标就放过去了
    assert excinfo.value.status_code == 503
    assert _facts_of(env, GP) == [] and _facts_of(env, PART) == []


async def test_failed_display_name_write_is_retried_not_marked_done(env):
    env.llm.responses = [SINGLE_FACTS]
    real = env.persona.aupdate_subject_display_name
    calls = {"n": 0}

    async def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("persona.json locked")
        return await real(*args, **kwargs)

    env.monkeypatch.setattr(env.persona, "aupdate_subject_display_name", flaky)
    with pytest.raises(HTTPException):
        await _post(env, _single_body())
    # 显示名没写成：这一项不能记成已应用、键不能收尾
    assert _key_state(env, KEY_GROUP) == "pending"
    assert (GROUP_KEY, "串门群") not in env.persona.display_names
    await _post(env, _single_body())
    assert _key_state(env, KEY_GROUP) == "done" and (GROUP_KEY, "串门群") in env.persona.display_names


async def test_corrupt_payload_in_the_forgotten_segment_keeps_the_other_segment(env):
    env.llm.responses = [BATCH_FACTS]
    original = env.routes._apply_keyed_item

    async def _crash(lanlan_name, item, segment, generation):
        raise RuntimeError("injected crash before applying anything")

    env.monkeypatch.setattr(env.routes, "_apply_keyed_item", _crash)
    with pytest.raises(HTTPException):
        await _post(env, _segments_body())
    env.monkeypatch.setattr(env.routes, "_apply_keyed_item", original)
    path = _staging_file(env, KEY_SEGMENTS)
    staging = json.loads(path.read_text(encoding="utf-8"))
    gp_display = next(item for item in staging["items"] if item["kind"] == "display_name" and item["segment"] == 0)
    gp_display["display_name"] = 5                              # 坏在要被清除的那段
    path.write_text(json.dumps(staging, ensure_ascii=False), encoding="utf-8")
    await _forget(env, GP)
    # 被清段的载荷本来就要抹掉：不因它取消整个键，没被清的段照常重试补写
    assert _key_state(env, KEY_SEGMENTS) == "pending"
    await _post(env, _segments_body())
    assert len(_facts_of(env, PART)) == 2 and _facts_of(env, GP) == []
    assert _key_state(env, KEY_SEGMENTS) == "done"


async def test_keyed_display_name_write_runs_strict(env):
    env.llm.responses = [SINGLE_FACTS]
    seen = []

    async def record(name, subject, display_name, **kwargs):
        seen.append(kwargs.get("strict"))
        return True

    env.monkeypatch.setattr(env.persona, "aupdate_subject_display_name", record)
    await _post(env, _single_body())
    # 带键日志路径要求读不出 persona 时抛出（返回 False 分不清是失败还是正常空操作）
    assert seen == [True]


async def test_cleanup_protects_tombstones_named_only_by_segments(env):
    idem = env.idem
    now = time.time()
    path = Path(idem.staging_path(NAME, "orphan-seg"))
    path.parent.mkdir(parents=True, exist_ok=True)
    # 索引丢了，各段仍指认 GROUP；它还是新的（未过期），会被留下
    path.write_text(json.dumps({
        "key": "orphan-seg", "created_at": now,
        "segments": [{"wire_key": GROUP_KEY, "subject": GROUP}],
    }), encoding="utf-8")
    tombstones = Path(idem.tombstones_path(NAME))
    tombstones.write_text(json.dumps({GROUP_KEY: {"forget_epoch": 1, "forgotten_at": now - 500}}), encoding="utf-8")
    await idem.cleanup_expired([NAME], ttl_s=100.0, now=now)
    # 留下来的暂存仍引用这个 subject：它的墓碑不能先过期，否则之后被认领时清除前的事实会写回
    assert path.exists() and GROUP_KEY in json.loads(tombstones.read_text(encoding="utf-8"))


async def test_locale_orders_are_recorded_before_the_reservation_is_durable(env):
    from app.memory_server import locale_state

    env.llm.responses = [SINGLE_FACTS]
    real_reserve = locale_state.reserve_subject_prompt_locale_orders

    def crash(*_args, **_kwargs):
        raise RuntimeError("killed between recording and reserving")

    env.monkeypatch.setattr(locale_state, "reserve_subject_prompt_locale_orders", crash)
    with pytest.raises(Exception):
        await _post(env, _single_body(language="zh"))
    record = json.loads(Path(env.idem.keys_path(NAME)).read_text(encoding="utf-8"))[KEY_GROUP]
    (recorded,) = record["locale_orders"]                      # 预留落盘之前就已记在键上
    env.monkeypatch.setattr(locale_state, "reserve_subject_prompt_locale_orders", real_reserve)
    original = _fail_on_item(env, failing_seq=0)
    with pytest.raises(HTTPException):
        await _post(env, _single_body(language="zh"))
    env.monkeypatch.setattr(env.routes, "_apply_keyed_item", original)
    staging = json.loads(_staging_file(env, KEY_GROUP).read_text(encoding="utf-8"))
    locale = next(item for item in staging["items"] if item["kind"] == "locale")
    assert locale["order"] == recorded


async def test_restored_retry_refreshes_an_already_written_display_name(env):
    env.llm.responses = [BATCH_FACTS]
    original = env.routes._apply_keyed_item

    async def _flaky(lanlan_name, item, segment, generation):
        if item["segment"] == 1 and item["kind"] == "facts":
            raise RuntimeError("injected crash after the first segment's display name")
        return await original(lanlan_name, item, segment, generation)

    env.monkeypatch.setattr(env.routes, "_apply_keyed_item", _flaky)
    with pytest.raises(HTTPException):
        await _post(env, _segments_body())
    env.monkeypatch.setattr(env.routes, "_apply_keyed_item", original)
    assert (GP_KEY, "团子") in env.persona.display_names       # 第一段的显示名已写过、已记进日志
    retry = _segments_body()
    retry["segments"][0]["display_name"] = "团子新名"           # 键停在 pending 期间改了名字
    await _post(env, retry)
    # 显示名不在请求身份里：恢复的重试按当前值再盖一次，不能带着旧名收尾
    assert (GP_KEY, "团子新名") in env.persona.display_names
    assert _key_state(env, KEY_SEGMENTS) == "done"


async def test_restored_journal_missing_a_facts_item_fails_closed(env):
    env.llm.responses = [BATCH_FACTS]

    async def _crash(lanlan_name, item, segment, generation):
        raise RuntimeError("injected crash before applying anything")

    original = env.routes._apply_keyed_item
    env.monkeypatch.setattr(env.routes, "_apply_keyed_item", _crash)
    with pytest.raises(HTTPException):
        await _post(env, _segments_body())
    env.monkeypatch.setattr(env.routes, "_apply_keyed_item", original)
    path = _staging_file(env, KEY_SEGMENTS)
    staging = json.loads(path.read_text(encoding="utf-8"))
    items = [item for item in staging["items"] if not (item["kind"] == "facts" and item["segment"] == 1)]
    assert len(items) < len(staging["items"])
    for position, item in enumerate(items):
        item["seq"] = position                                   # 序号重排，其余逐项核对都过得去
    staging["items"] = items
    path.write_text(json.dumps(staging, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(HTTPException) as excinfo:
        await _post(env, _segments_body())
    # 事实项整条丢了：不能按剩下的收尾、永久漏掉已生成的事实
    assert excinfo.value.status_code == 503 and _key_state(env, KEY_SEGMENTS) == "pending"


async def test_unrelated_subject_epochs_do_not_change_the_request_identity(env):
    env.llm.responses = [SINGLE_FACTS]
    original = _fail_on_item(env, failing_seq=0)
    with pytest.raises(HTTPException):
        await _post(env, _single_body())
    env.monkeypatch.setattr(env.routes, "_apply_keyed_item", original)
    # 重试多带了一个与本请求无关的 subject 的代数：效果完全相同，不能 422
    result = await _post(env, _single_body(subject_epochs={GROUP_KEY: 0, "participant:neko_visit:other": 3}))
    assert result["created"] == 2 and env.llm.calls == 1


async def test_retry_does_not_reserve_locale_for_a_forgotten_segment(env):
    import tempfile

    from app.memory_server import locale_state

    env.llm.responses = [RuntimeError("LLM failed"), PART_ONLY_BATCH_FACTS]
    with pytest.raises(RuntimeError):
        await _post(env, _segments_body(language="zh"))

    def forgotten_meanwhile(old):
        # 生成期间到达的清除只在记录上记下被清的 subject（键锁被占着），清除本身已删掉它的语言行
        return {**old, "forgotten_keys": [GP_KEY], "locale_orders": None}

    await env.idem.update_key(NAME, KEY_SEGMENTS, forgotten_meanwhile)
    sidecar = Path(locale_state._subject_locale_path(NAME))
    assert str(sidecar).startswith(tempfile.gettempdir())       # 绝不碰真实运行时根目录
    locale_state.invalidate_prompt_locale_caches()
    if sidecar.exists():
        sidecar.unlink()                                         # 被清后语言存储里没有 GP 了
    locale_state.invalidate_prompt_locale_caches()
    original = env.routes._apply_keyed_item

    async def _crash(lanlan_name, item, segment, generation):
        raise RuntimeError("injected crash before applying anything")

    env.monkeypatch.setattr(env.routes, "_apply_keyed_item", _crash)
    with pytest.raises(HTTPException):
        await _post(env, _segments_body(language="zh"))         # 落了暂存（被清段没有语言项）就崩
    env.monkeypatch.setattr(env.routes, "_apply_keyed_item", original)
    # 从暂存恢复：被清除丢弃的段没有语言项也能通过恢复校验
    result = await _post(env, _segments_body(language="zh"))
    assert result["segments"][1]["created"] == 2
    rows = json.loads(sidecar.read_text(encoding="utf-8")).get("subjects", {}) if sidecar.exists() else {}
    # 被清的段不再预留：不能把被清 subject 重新写进语言存储
    assert not any(GP["subject_id"] in key for key in rows)
    assert any(PART["subject_id"] in key for key in rows)


async def test_listing_skips_a_forgotten_staging_segment(env):
    env.llm.responses = [BATCH_FACTS]
    original = env.routes._apply_keyed_item

    async def _flaky(lanlan_name, item, segment, generation):
        if item["segment"] == 1 and item["kind"] == "facts":
            raise RuntimeError("injected crash before the second segment")
        return await original(lanlan_name, item, segment, generation)

    env.monkeypatch.setattr(env.routes, "_apply_keyed_item", _flaky)
    with pytest.raises(HTTPException):
        await _post(env, _segments_body())
    env.monkeypatch.setattr(env.routes, "_apply_keyed_item", original)
    await _forget(env, GP)
    assert _key_state(env, KEY_SEGMENTS) == "pending"           # 暂存留给没被清的段
    result = await env.routes.list_scoped_subjects(NAME, platform="neko_visit")
    staged = {row["subject_id"] for row in result["subjects"] if row["staged"]}
    # 被清的段已抹掉内容：不能作为「待应用的暂存」让已清除的对象又冒出来
    assert GP["subject_id"] not in staged and PART["subject_id"] in staged


@pytest.mark.parametrize("damage", ["kept_locale_removed", "kept_locale_other_language"])
async def test_partial_forget_cancels_a_journal_with_a_broken_kept_locale(env, damage):
    env.llm.responses = [BATCH_FACTS]
    original = env.routes._apply_keyed_item

    async def _crash(lanlan_name, item, segment, generation):
        raise RuntimeError("injected crash before applying anything")

    env.monkeypatch.setattr(env.routes, "_apply_keyed_item", _crash)
    with pytest.raises(HTTPException):
        await _post(env, _segments_body(language="zh"))
    env.monkeypatch.setattr(env.routes, "_apply_keyed_item", original)
    path = _staging_file(env, KEY_SEGMENTS)
    staging = json.loads(path.read_text(encoding="utf-8"))
    part_locale = next(item for item in staging["items"] if item["kind"] == "locale" and item["segment"] == 1)
    if damage == "kept_locale_removed":
        staging["items"] = [item for item in staging["items"] if item is not part_locale]
        for position, item in enumerate(staging["items"]):
            item["seq"] = position
    else:
        part_locale["language"] = "en"
    path.write_text(json.dumps(staging, ensure_ascii=False), encoding="utf-8")
    await _forget(env, GP)
    # 留下来的段语言项坏了：之后的重试只会 503，不能只丢被清段留着它，整键取消
    assert _key_state(env, KEY_SEGMENTS) == "cancelled"


async def test_forget_epochs_endpoint_reports_the_current_fence(env):
    await _forget(env, GROUP, forget_epoch=6)
    result = await env.routes.get_forget_epochs(NAME, subject=[GROUP_KEY, PART_KEY])
    # 有墓碑的 key 报当前围栏，没有的不出现
    assert result == {"epochs": {GROUP_KEY: 6}}
    Path(env.idem.tombstones_path(NAME)).write_text(json.dumps({GROUP_KEY: {"forget_epoch": "bad"}}), encoding="utf-8")
    with pytest.raises(HTTPException) as excinfo:
        await env.routes.get_forget_epochs(NAME, subject=[GROUP_KEY])
    # 认不出的围栏不能当成「没有围栏」
    assert excinfo.value.status_code == 503
