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

    async def aupdate_subject_display_name(self, name, subject, display_name):
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
    body.update(extra)
    return body


SINGLE_FACTS = [
    {"text": "家里阳台种着猫薄荷", "importance": 6},
    {"text": "下次会带团子来玩", "importance": 5},
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
    assert _key_state(env, KEY_GROUP) is None

    result = await _post(env, _single_body())
    assert env.llm.calls == 2
    assert result["created"] == 2
    assert _key_state(env, KEY_GROUP) == "done"


async def test_pending_key_without_staging_regenerates_instead_of_duplicate(env):
    """Staging lost (e.g. swept) while the key is pending: regenerate, never skip."""
    await env.idem.update_key(NAME, KEY_GROUP, env.idem.transition("pending"))
    env.llm.responses = [SINGLE_FACTS]
    result = await _post(env, _single_body())
    assert result.get("duplicate") is None
    assert result["created"] == 2
    assert env.llm.calls == 1


async def test_incomplete_batch_generation_is_502_and_stages_nothing(env):
    env.llm.responses = [[{"segment": 1, "facts": [{"text": "只有第一段", "importance": 5}]}]]
    with pytest.raises(HTTPException) as excinfo:
        await _post(env, _segments_body())
    assert excinfo.value.status_code == 502
    assert not _staging_file(env, KEY_SEGMENTS).exists()
    assert _key_state(env, KEY_SEGMENTS) is None
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

    async def write_then_forget(lanlan_name, key, document):
        if not state["forgot"]:
            state["forgot"] = True
            # 清除整个落在「生成后的检查」与「暂存落盘」之间：它的取消扫描看不到这份暂存
            await _forget(env, GROUP)
        await real_write(lanlan_name, key, document)

    async def crash_before_apply(*args, **kwargs):
        if state["crash"]:
            state["crash"] = False
            raise RuntimeError("killed after staging, before apply")
        return await real_apply(*args, **kwargs)

    env.monkeypatch.setattr(env.idem, "write_staging", write_then_forget)
    env.monkeypatch.setattr(env.routes, "_apply_keyed_staging", crash_before_apply)
    with pytest.raises(HTTPException):
        await _post(env, _single_body(display_name=None))
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
