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
    # Never touch actual runtime roots: every path must lie under tmp_path.
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
        # Keyed requests must provide a clearing generation for every wire subject; missing values cannot mean zero.
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
# After clearing the first segment, extract only the second: the model sees one segment, numbered 1.
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


async def _forget(env, subject: dict, forget_epoch: int | None = None, target_epochs: dict | None = None):
    body = {"subject": subject}
    if forget_epoch is not None:
        body["forget_epoch"] = forget_epoch
    if target_epochs is not None:
        body["target_epochs"] = target_epochs
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
    # A second extraction returns different facts; retries invoking the LLM again would introduce near-duplicates in the pool.
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
    # Retries restored from staging fill in the current display name from this request.
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

    # Disable exact SHA deduplication (simulate adjudication rewriting these two rows' hashes): only effect_key
    # can prevent replay.
    for row in env.fs._facts[NAME]:
        row["hash"] = "rewritten-" + row["id"]
    env.fs.save_facts(NAME)

    result = await _post(env, _single_body(display_name=None))
    assert result["status"] == "processed"
    rows = _facts_of(env, GROUP)
    assert len(rows) == 2
    assert len({row["effect_key"] for row in rows}) == 2
    # Replay-hit effects return existing rows as this request's result, exposing those facts' identities to the caller.
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
    # Clearing advanced generation during generation: mark output discarded when staging it (tombstones provide a second defense).
    assert all(
        entry.get("dropped_tombstone") or entry.get("dropped_forget_during_generation")
        for entry in final_journal["applied"]
    )
    assert env.persona.display_names == []
    tombstones = json.loads(
        Path(env.idem.tombstones_path(NAME)).read_text(encoding="utf-8")
    )
    assert tombstones[GP_KEY]["forget_epoch"] == 1

    # A new pass after clearing (generation 1) writes normally.
    env.llm.gate = None
    env.llm.responses = [BATCH_FACTS]
    fresh = await _post(env, _segments_body(
        key="visit-digest:NewVisitIdAaaaaaaaaaaa:0:segments:0",
        subject_epochs={GP_KEY: 1, PART_KEY: 1},
    ))
    assert [seg["created"] for seg in fresh["segments"]] == [1, 2]

    # Late old requests sent before clearing but arriving afterward (generation 0) are still blocked.
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

    # Late request with old generation: its client clock is later than clearing, so time comparisons would allow it.
    env.llm.responses = [SINGLE_FACTS]
    stale = await _post(env, _single_body(
        key="visit-digest:StaleVisitIdCccccccccc:0:group:0",
        subject_epochs={GROUP_KEY: 0},
        client_requested_at=real_now,
    ))
    assert stale["created"] == 0
    assert _facts_of(env, GROUP) == []

    # New-generation request: its client clock is earlier than clearing, so time comparisons would wrongly block it.
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
    # Persist pending before invoking the LLM (generation failure still leaves a durable record clearing can cancel); without staging, retry generates again normally.
    assert _key_state(env, KEY_GROUP) == "pending"

    result = await _post(env, _single_body())
    assert env.llm.calls == 2
    assert result["created"] == 2
    assert _key_state(env, KEY_GROUP) == "done"


async def test_pending_key_without_staging_regenerates_instead_of_duplicate(env):
    """Staging lost (e.g. swept) while the key is pending: regenerate, never skip."""
    env.llm.responses = [RuntimeError("LLM failed"), SINGLE_FACTS]
    with pytest.raises(RuntimeError):
        await _post(env, _single_body())                     # Record pending with request identity; generation fails without staging.
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
    # Neither request identity nor staging exists: cannot verify the same request, so do not take over as a new one.
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
        # Only the admin profile is a valid owner combination; otherwise existing validation returns 422 before reaching this guard.
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
        time.sleep(0.005)  # Widen the window between reading and writing back.
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
        # Make the record itself old too: keyed records are never cleared regardless of age.
    report = await idem.cleanup_expired([NAME], ttl_s=ttl, now=now)
    assert report == {"staging_removed": 1, "tombstones_removed": 1, "retired_removed": 0}
    assert not Path(idem.staging_path(NAME, "old-key")).exists()
    assert Path(idem.staging_path(NAME, "new-key")).exists()
    assert set(await idem.read_tombstones(NAME)) == {"a:new"}
    records = json.loads(Path(idem.keys_path(NAME)).read_text(encoding="utf-8"))
    assert {key: row["state"] for key, row in records.items()} == {
        "k-done": "done", "k-cancelled": "cancelled", "k-pending": "pending",
    }
    report = await idem.cleanup_expired([NAME], ttl_s=0, now=now + 10**9)
    # Much later, remaining staging and tombstones expire and are cleared, while keyed records remain byte-for-byte unchanged.
    assert report == {"staging_removed": 1, "tombstones_removed": 1, "retired_removed": 0}
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
    await _forget(env, GROUP)                      # Without forget_epoch, write no tombstone.
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
    assert _staging_file(env, KEY_GROUP).exists()      # Retain staging; same-key retry only completes application.
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
    await _forget(env, GROUP)                      # Clearing without a generation occurs during generation.
    env.llm.gate.set()
    with pytest.raises(HTTPException):
        await asyncio.wait_for(task, timeout=5)
    env.llm.gate = None
    result = await _post(env, _single_body(display_name=None))     # Retry reads the generation after clearing.
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
    # Display names do not enter content hashes: retrying the same sentences with a different display name is still duplicate.
    again = await _post(env, _single_body(display_name="新名字"))
    assert again["duplicate"] is True


async def test_pending_staging_rejects_a_retry_with_different_content(env):
    env.llm.responses = [SINGLE_FACTS]
    _fail_on_item(env, failing_seq=0)
    with pytest.raises(HTTPException):
        await _post(env, _single_body(display_name=None))
    path = Path(env.idem.keys_path(NAME))
    data = json.loads(path.read_text(encoding="utf-8"))
    data[KEY_GROUP].pop("request", None)            # Only the staged content hash remains for verification.
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
    assert facts_path.read_text(encoding="utf-8") == "{torn"       # Not overwritten.



async def test_staged_retry_stamps_the_current_request_display_name_not_the_stale_one(env):
    env.llm.responses = [SINGLE_FACTS]
    original_apply = _fail_on_item(env, failing_seq=1)   # seq0 facts written; seq1 display-name update interrupted.
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
    assert await env.fs.aload_facts(NAME) == []            # The permissive loader caches a corrupt file as empty.
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
    assert report == {"staging_removed": 0, "tombstones_removed": 0, "retired_removed": 0}
    assert set(await idem.read_tombstones(NAME)) == {"a:old"}



async def test_forget_landing_while_staging_is_written_is_caught_by_the_recheck(env):
    env.llm.responses = [SINGLE_FACTS]
    real_write = env.idem.write_staging
    real_apply = env.routes._apply_keyed_staging
    state = {"forgot": False, "crash": True}

    async def forget_then_write(lanlan_name, key, document):
        if not state["forgot"]:
            state["forgot"] = True
            # Clearing occurs entirely between the post-generation check and staging persistence: neither cancellation scan sees staging,
            # and the occupied key lock is skipped; only the check after staging persistence can catch it.
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
    # Request interrupted before application: staging remains on disk, but cleared subjects' extraction source was erased with discard markers.
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
    result = await _forget(env, GROUP)                     # Without forget_epoch, no tombstone exists.
    assert result["status"] == "forgotten" and _facts_of(env, GROUP) == []
    # Key file unreadable and cancellation cannot be recorded: record it in staging instead, retaining staging.
    raw = _staging_file(env, KEY_GROUP).read_text(encoding="utf-8")
    staging = json.loads(raw)
    assert staging["cancelled_by_forget"] is True
    # Only cancellation markers remain: cleared subjects' extraction source and display names are absent from disk.
    for fact in SINGLE_FACTS:
        assert fact["text"] not in raw
    assert "串门群" not in raw
    keys_file.write_text(intact, encoding="utf-8")         # Key file repaired; record remains pending.
    env.monkeypatch.setattr(env.routes, "_apply_keyed_item", original)
    again = await _post(env, _single_body())
    # Same-key retry must not regenerate and write using the post-clear generation.
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
    # Retain staging whose filename resolves to a pending key (its only copy); remove expired orphans with terminal or absent key records.
    kept = owner_state == "pending"
    assert path.exists() is kept and report["staging_removed"] == (0 if kept else 1)


async def test_unreadable_key_file_does_not_block_a_forget(env):
    env.llm.responses = [SINGLE_FACTS]
    await _post(env, _single_body(key=None, display_name=None))       # Write two facts without a key.
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
    # Corrupt staging does not block clearing: cancel by key record and delete corrupt staging too.
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
        return []          # The staging-scan snapshot predates this staging file's creation.

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
    # Request generation >= this clear's generation: a new write initiated knowing about the clear, so do not cancel.
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
    # Discard only the cleared segment: key stays pending and staging remains without that segment's extraction source.
    assert _key_state(env, KEY_SEGMENTS) == "pending"
    assert "团子喜欢晒太阳" not in _staging_file(env, KEY_SEGMENTS).read_text(encoding="utf-8")
    env.monkeypatch.setattr(env.routes, "_apply_keyed_item", original)
    again = await _post(env, _segments_body())
    assert again.get("duplicate") is None
    # Retry completes the uncleared segment without restoring the cleared one.
    assert len(_facts_of(env, PART)) == 2 and _facts_of(env, GP) == []
    # Clear already-applied results for the cleared segment too: responses must no longer expose erased facts.
    assert again["segments"][0]["created"] == 0 and again["segments"][0]["fact_ids"] == []
    # Do not complete the cleared segment's unapplied display-name item either: retry gets the current request's name and would restore it without a discard marker.
    assert (GP_KEY, "团子") not in env.persona.display_names
    assert env.llm.calls == 1
    assert _key_state(env, KEY_SEGMENTS) == "done"


async def test_forget_over_a_malformed_tombstone_erases_and_rebuilds_it(env):
    env.llm.responses = [SINGLE_FACTS]
    await _post(env, _single_body(key=None, display_name=None))
    assert _facts_of(env, GROUP)
    path = Path(env.idem.tombstones_path(NAME))
    path.write_text(json.dumps({GROUP_KEY: {"forget_epoch": "9"}}), encoding="utf-8")
    result = await _forget(env, GROUP, forget_epoch=1)
    # Corrupt tombstones do not block erasure: erase normally, then rebuild this row using the current fence + completion marker instead of permanent 503.
    assert result["status"] == "forgotten" and _facts_of(env, GROUP) == []
    row = json.loads(path.read_text(encoding="utf-8"))[GROUP_KEY]
    assert row["forget_epoch"] == 1 and row["erased_epoch"] == 1


@pytest.mark.parametrize("owner_state", ["pending", "done"])
async def test_cleanup_judges_staging_by_its_filename_not_its_embedded_key(env, owner_state):
    idem = env.idem
    now = time.time()
    await idem.update_key(NAME, "key-a", idem.transition(owner_state))
    path = Path(idem.staging_path(NAME, "key-a"))
    path.parent.mkdir(parents=True, exist_ok=True)
    # key-a's staging file contains a key changed to key-b, which has no record.
    path.write_text(json.dumps({"key": "key-b", "subjects": [], "created_at": now - 500}), encoding="utf-8")
    report = await idem.cleanup_expired([NAME], ttl_s=100.0, now=now)
    kept = owner_state == "pending"
    assert path.exists() is kept and report["staging_removed"] == (0 if kept else 1)



async def test_same_key_with_another_scope_is_a_different_request(env):
    env.llm.responses = [SINGLE_FACTS]
    await _post(env, _single_body())
    with pytest.raises(HTTPException) as excinfo:
        await _post(env, _single_body(subject={**GROUP, "scope": "another_scope"}))
    # The same kind:id with different scopes denotes isolated subjects, not one request eligible for duplicate.
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
    # Current fanout no longer includes this staging file's wire subject, but application wrote its recorded routed subject.
    cancelled = await env.routes._cancel_staged_writes_for_subjects(
        NAME, {"participant:neko_visit:routed-person"},
    )
    assert cancelled == 1 and _key_state(env, KEY_GROUP) == "cancelled"
    assert not _staging_file(env, KEY_GROUP).exists()


async def test_forget_scrubs_a_misplaced_staging_file_in_place(env):
    idem = env.idem
    path = _staging_file(env, KEY_GROUP)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Filename belongs to KEY_GROUP, but embedded content names another key.
    path.write_text(json.dumps({
        "key": "other-key", "shape": "single", "subjects": [GROUP_KEY], "created_at": time.time(),
        "segments": [{"wire_key": GROUP_KEY, "subject": GROUP}],
        "items": [{"seq": 0, "kind": "facts", "segment": 0, "facts": SINGLE_FACTS}], "applied": [],
    }, ensure_ascii=False), encoding="utf-8")
    other = _staging_file(env, "other-key")
    result = await _forget(env, GROUP)
    assert result["status"] == "forgotten"
    raw = path.read_text(encoding="utf-8")
    # Erase in place into a cancellation marker, retaining no cleared-subject source; do not follow embedded keys to other paths.
    assert "家里阳台种着猫薄荷" not in raw and json.loads(raw)["cancelled_by_forget"] is True
    assert not other.exists()
    with pytest.raises(idem.IdempotencyStateError):
        await idem.read_staging(NAME, KEY_GROUP)               # Same-key retry still fails closed.



async def test_replayed_or_stale_forget_does_not_erase_writes_made_after_it(env):
    env.llm.responses = [SINGLE_FACTS, SINGLE_FACTS]
    await _post(env, _single_body(key=None, display_name=None))
    first = await _forget(env, GROUP, forget_epoch=2)
    assert first["status"] == "forgotten" and _facts_of(env, GROUP) == []
    # Legitimate write after clearing, carrying a new generation.
    await _post(env, _single_body(subject_epochs={GROUP_KEY: 2}, display_name=None))
    assert len(_facts_of(env, GROUP)) == 2
    for epoch in (2, 1):                       # Replay of the same generation; late older clearing.
        again = await _forget(env, GROUP, forget_epoch=epoch)
        assert again["status"] == "forgotten" and again.get("duplicate") is True
        assert len(_facts_of(env, GROUP)) == 2
    newer = await _forget(env, GROUP, forget_epoch=3)   # Newer clearing erases normally.
    assert newer.get("duplicate") is None and _facts_of(env, GROUP) == []


async def test_forget_whose_erase_did_not_finish_is_not_skipped_on_retry(env):
    env.llm.responses = [SINGLE_FACTS]
    await _post(env, _single_body(key=None, display_name=None))
    # Tombstone persisted but erasure unfinished (crash between the steps): retry must still erase.
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
            return False        # The unlocked check occurs before newer clearing completes.
        return await real_check(*args)

    env.monkeypatch.setattr(env.routes, "_forget_epoch_already_erased", check)
    stale = await _forget(env, GROUP, forget_epoch=2)
    # Recheck under the lock: newer clearing is complete, so older clearing must not erase subsequent legitimate writes.
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
        return []          # The staging-scan snapshot predates this staging file's creation.

    env.monkeypatch.setattr(idem, "list_staging", stale_snapshot)
    cancelled = await env.routes._cancel_staged_writes_for_subjects(
        NAME, {"participant:neko_visit:routed-person"},
    )
    # Key record contains only the wire key, but staging records the routed cleared subject: still cancel.
    assert cancelled == 1 and _key_state(env, KEY_GROUP) == "cancelled"
    assert not _staging_file(env, KEY_GROUP).exists()


async def test_forget_is_not_blocked_by_an_unrelated_key_holding_its_lock(env):
    idem = env.idem
    await idem.update_key(NAME, "unrelated", idem.transition(
        "pending", request={"shape": "single", "wire_keys": [PART_KEY], "content_hash": "h"},
    ))
    async with idem.key_lock(NAME, "unrelated"):       # An unrelated request holds its own key lock while awaiting the LLM.
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
    # Record the actual routed write subject separately on pending records, outside request identity.
    assert records[KEY_GROUP]["routed_keys"] == [GROUP_KEY]
    assert "routed_keys" not in records[KEY_GROUP]["request"]


async def test_record_pass_matches_routed_keys_when_staging_is_not_written_yet(env):
    idem = env.idem
    await idem.update_key(NAME, KEY_GROUP, idem.transition(
        "pending", request={"shape": "single", "wire_keys": [GROUP_KEY], "content_hash": "h"},
        routed_keys=["participant:neko_visit:routed-person"],
    ))
    # Staging does not yet exist; recognize it only through the record's routed subject. Some request subjects remain uncleared: record only cleared ones,
    # without canceling the entire key.
    cancelled = await env.routes._cancel_staged_writes_for_subjects(
        NAME, {"participant:neko_visit:routed-person"},
    )
    record = json.loads(Path(idem.keys_path(NAME)).read_text(encoding="utf-8"))[KEY_GROUP]
    assert cancelled == 1 and record["state"] == "pending"
    assert record["forgotten_keys"] == ["participant:neko_visit:routed-person"]
    # Every subject in the request was cleared: cancel the entire key.
    await env.routes._cancel_staged_writes_for_subjects(
        NAME, {"participant:neko_visit:routed-person", GROUP_KEY},
    )
    assert _key_state(env, KEY_GROUP) == "cancelled"


async def test_concurrent_older_forget_waits_for_the_newer_one_to_publish_its_epoch(env):
    env.llm.responses = [SINGLE_FACTS, SINGLE_FACTS]
    await _post(env, _single_body(key=None, display_name=None))
    gate, reached = asyncio.Event(), asyncio.Event()
    real_mark = env.idem.mark_tombstone_erased

    async def slow_mark(*args, **kwargs):
        reached.set()
        await gate.wait()        # Newer clearing finished erasure and released the erasure lock but has not written its completion marker.
        return await real_mark(*args, **kwargs)

    env.monkeypatch.setattr(env.idem, "mark_tombstone_erased", slow_mark)
    newer = asyncio.create_task(_forget(env, GROUP, forget_epoch=20))
    await reached.wait()
    # Legitimate write after newer clearing, carrying a new generation.
    await _post(env, _single_body(subject_epochs={GROUP_KEY: 20}, display_name=None))
    assert len(_facts_of(env, GROUP)) == 2
    real_check = env.routes._forget_epoch_already_erased
    checks = {"n": 0}

    async def counted(*args):
        checks["n"] += 1
        return await real_check(*args)

    env.monkeypatch.setattr(env.routes, "_forget_epoch_already_erased", counted)
    older = asyncio.create_task(_forget(env, GROUP, forget_epoch=10))
    # Give older clearing enough time: without a fence it reaches the locked recheck (second check) and erases;
    # with a fence it remains blocked outside, unable to perform even one check.
    for _ in range(200):
        if checks["n"] >= 2 or older.done():
            break
        await asyncio.sleep(0.01)
    gate.set()
    assert (await newer)["status"] == "forgotten"
    stale = await older
    # Older clearing checks only after newer clearing publishes its completed generation; it no longer erases legitimate writes.
    assert stale.get("duplicate") is True
    assert len(_facts_of(env, GROUP)) == 2



async def test_generation_failing_after_a_forget_keeps_the_forget_on_the_record(env):
    env.llm.responses = [RuntimeError("LLM failed")]
    env.llm.gate = asyncio.Event()
    env.llm.entered = asyncio.Event()
    task = asyncio.create_task(_post(env, _single_body()))
    await asyncio.wait_for(env.llm.entered.wait(), timeout=5)
    # Clearing during generation: the key lock is occupied, so do not queue; durably record cleared subjects on the record only.
    await _forget(env, GROUP)
    env.llm.gate.set()
    with pytest.raises(RuntimeError):
        await asyncio.wait_for(task, timeout=5)
    records = json.loads(Path(env.idem.keys_path(NAME)).read_text(encoding="utf-8"))
    assert records[KEY_GROUP]["state"] == "pending" and records[KEY_GROUP]["forgotten_keys"] == [GROUP_KEY]
    # Same-key retry after generation fails (or process termination): regenerate but discard cleared segments according to this record without restoring old content.
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
    await _forget(env, GP)                                   # Clear the subject of only one segment.
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
    # Discard only the cleared segment and complete the other normally, rather than canceling the entire key.
    assert _facts_of(env, GP) == [] and len(_facts_of(env, PART)) == 2
    assert [seg["created"] for seg in again["segments"]] == [0, 2]
    # Do not send the cleared participant's original text for extraction again.
    assert prompts and all("我最喜欢晒太阳了" not in prompt for prompt in prompts)


async def test_terminal_key_record_without_request_identity_fails_closed(env):
    await env.idem.update_key(NAME, KEY_GROUP, env.idem.transition("done"))   # Corrupt record: no request identity.
    with pytest.raises(HTTPException) as excinfo:
        await _post(env, _single_body())
    assert excinfo.value.status_code == 503 and env.llm.calls == 0


@pytest.mark.parametrize("damage", ["applied_seq_bool", "effect_key_foreign"])
async def test_damaged_journal_markers_fail_closed(env, damage):
    env.llm.responses = [SINGLE_FACTS]
    original = _fail_on_item(env, failing_seq=0)
    with pytest.raises(HTTPException):
        await _post(env, _single_body())
    env.monkeypatch.setattr(env.routes, "_apply_keyed_item", original)    # Application itself no longer fails during retry.
    path = _staging_file(env, KEY_GROUP)
    staging = json.loads(path.read_text(encoding="utf-8"))
    facts_item = next(item for item in staging["items"] if item["kind"] == "facts")
    if damage == "applied_seq_bool":
        staging["applied"].append({"seq": True})            # True == 1: would skip item 1 as already applied.
    else:
        facts_item["effect_keys"][0] = "0" * 32 + ":7"      # Valid shape, but not an effect key derived from this key.
    path.write_text(json.dumps(staging, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(HTTPException) as excinfo:
        await _post(env, _single_body())
    assert excinfo.value.status_code == 503 and _facts_of(env, GROUP) == []


async def test_forget_cancels_a_pending_key_whose_generation_failed_earlier(env):
    env.llm.responses = [RuntimeError("LLM failed")]
    with pytest.raises(RuntimeError):
        await _post(env, _single_body())
    assert _key_state(env, KEY_GROUP) == "pending"       # Generation fails, leaving pending without staging.
    await _forget(env, GROUP)
    # Key lock available: clearing cancels it, and subsequent same-key retry returns only duplicate.
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
    env.monkeypatch.setattr(env.routes, "_apply_keyed_item", original)    # Application itself no longer fails during retry.
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
    # Never apply corrupt items (a negative segment number writes to another subject): return 503 and write nothing.
    assert excinfo.value.status_code == 503 and _facts_of(env, GROUP) == []


async def test_deeply_nested_staging_does_not_block_a_forget(env):
    path = _staging_file(env, "deep-key")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[" * 100000 + "]" * 100000, encoding="utf-8")
    result = await _forget(env, GROUP)
    # Treat excessively nested staging as unreadable instead of returning 500 on every clear.
    assert result["status"] == "forgotten"


async def test_client_key_named_like_the_forget_fence_does_not_deadlock(env):
    idem = env.idem
    clash = f"forget-fence:{GROUP_KEY}"
    await idem.update_key(NAME, clash, idem.transition(
        "pending", request={"shape": "single", "wire_keys": [GROUP_KEY], "content_hash": "h"},
    ))
    # Fences use a separate registry: a same-named client key cannot make clearing wait on itself.
    result = await asyncio.wait_for(_forget(env, GROUP, forget_epoch=1), timeout=5)
    assert result["status"] == "forgotten" and _key_state(env, clash) == "cancelled"


async def test_keyed_write_requires_the_default_scope(env):
    with pytest.raises(HTTPException) as excinfo:
        await _post(env, _single_body(subject={**GROUP, "scope": "another_scope"}))
    # Tombstones / cancellation / erased generations are recorded by kind:id; keyed writes accept only the default scope.
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
    # Clearing another scope of the same kind:id is unrelated to the default-scope keyed write; do not cancel it.
    assert result["status"] == "forgotten"
    assert _key_state(env, KEY_GROUP) == "pending" and _staging_file(env, KEY_GROUP).exists()


async def test_pending_key_issued_after_the_forget_is_not_cancelled_without_staging(env):
    env.llm.responses = [RuntimeError("LLM failed")]
    with pytest.raises(RuntimeError):
        await _post(env, _single_body(subject_epochs={GROUP_KEY: 5}))   # Initiated only after learning of generation-5 clearing.
    assert _key_state(env, KEY_GROUP) == "pending"
    await _forget(env, GROUP, forget_epoch=5)
    # No staging, but record request generation >= clear generation: legitimate new write, do not cancel.
    assert _key_state(env, KEY_GROUP) == "pending"
    env.llm.responses = [SINGLE_FACTS]
    again = await _post(env, _single_body(subject_epochs={GROUP_KEY: 5}))
    assert again["created"] == 2


async def test_corrupt_tombstone_file_does_not_block_an_epoch_forget(env):
    env.llm.responses = [SINGLE_FACTS]
    await _post(env, _single_body(key=None, display_name=None))
    path = Path(env.idem.tombstones_path(NAME))
    path.write_text("{torn", encoding="utf-8")                 # Entire tombstone file unreadable.
    result = await _forget(env, GROUP, forget_epoch=2)
    # Corrupt auxiliary files must not block privacy clearing: erase normally, rename corrupt files into quarantine, and rebuild from this clear, avoiding permanent 503.
    assert result["status"] == "forgotten" and _facts_of(env, GROUP) == []
    assert env.idem.erased_epoch(await env.idem.read_tombstones(NAME), GROUP_KEY) == 2
    assert list(path.parent.glob(path.name + ".corrupt-*"))


async def test_keyed_request_without_epochs_is_rejected(env):
    body = _single_body()
    body.pop("subject_epochs")
    with pytest.raises(HTTPException) as excinfo:
        await _post(env, body)
    # Missing generation cannot mean zero, or every write after generation-bearing clearing would be silently discarded.
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
    # All output is destined for discard: do not hold the key lock for another extraction; finish directly as canceled.
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
            exc.winerror = 32                                  # Windows: another writer is performing os.replace.
            raise exc
        return real_read(path, **kwargs)

    monkeypatch.setattr(file_utils, "read_json", busy_once)
    # Back off and retry transient sharing conflicts during replacement rather than treating state as unreadable.
    assert (await env.idem.read_key(NAME, KEY_GROUP))["state"] == "pending"


async def test_forget_fails_when_the_completion_marker_cannot_be_written(env):
    async def broken(*_args, **_kwargs):
        raise OSError("disk full")

    env.monkeypatch.setattr(env.idem, "mark_tombstone_erased", broken)
    with pytest.raises(HTTPException) as excinfo:
        await _forget(env, GROUP, forget_epoch=1)
    # Do not report success until this generation's erasure completion is durable; callers retry until persisted.
    assert excinfo.value.status_code == 500


async def test_damaged_applied_result_fields_fail_closed(env):
    env.llm.responses = [SINGLE_FACTS]
    original = _fail_on_item(env, failing_seq=1)               # seq0 applied; seq1 interrupted.
    with pytest.raises(HTTPException):
        await _post(env, _single_body())
    env.monkeypatch.setattr(env.routes, "_apply_keyed_item", original)
    path = _staging_file(env, KEY_GROUP)
    staging = json.loads(path.read_text(encoding="utf-8"))
    staging["applied"][0]["fact_ids"] = 1                      # Corrupt result field.
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
    # Clearing GROUP fans out to its routed canonical subject: intersection contains two keys, but this segment's wire is the request subject,
    # with a sufficiently new generation, making it a legitimate post-clear write.
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
    # Language sequence numbers reserved only after clearing cannot prove the request predates clearing: discard too.
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
        staging["segments"][0]["subject"] = PART                # Write target changed to another valid subject.
    else:
        facts_item = next(item for item in staging["items"] if item["kind"] == "facts")
        facts_item["facts"][0] = "not a fact"                  # Corrupt facts would be silently skipped yet marked applied.
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
        # Empty fact_ids is a valid result too and alone cannot prove this item was applied.
        facts_seq = next(item["seq"] for item in staging["items"] if item["kind"] == "facts")
        staging["applied"] = [
            {"seq": facts_seq, "fact_ids": []} if e["seq"] == facts_seq else e for e in staging["applied"]
        ]
    elif damage == "applied_facts_drop_marker_typo":
        # Names resemble discard markers but are not those written by this module: they cannot prove completion.
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
            locale["language"] = "en"                           # Another supported language code, inconsistent with the request.
        else:
            locale["language"] = "invalid"                    # Language storage would convert it to None and clear the existing language.
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
        records.pop(KEY_GROUP)                                  # Orphan staging: key record absent.
        keys_path.write_text(json.dumps(records), encoding="utf-8")
        staging["segments"][0]["subject"] = PART               # No routing record to verify; target changed elsewhere.
    elif damage == "provenance_scalar":
        facts_item = next(item for item in staging["items"] if item["kind"] == "facts")
        facts_item["speaker_provenance"] = "lost"
    elif damage == "provenance_missing_label":
        facts_item = next(item for item in staging["items"] if item["kind"] == "facts")
        facts_item["speaker_provenance"] = {"speaker_trust": 0.5}          # Partial ownership: no label.
    elif damage.startswith("provenance_"):
        facts_item = next(item for item in staging["items"] if item["kind"] == "facts")
        field, value = {
            "provenance_label_list": ("speaker_label", []),
            "provenance_trust_out_of_range": ("speaker_trust", 7),
            "provenance_unknown_field": ("speaker_mood", "x"),
            "provenance_bad_speaker_id": ("speaker_id", "no colon here"),
            # Embedded newline and square brackets: the sanitizer cannot produce such a label.
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
        # Tombstone removed by cleanup during erasure; simulate by not persisting it here.
        return {}

    env.monkeypatch.setattr(env.idem, "record_tombstone_epochs", tombstone_vanishes)
    result = await _forget(env, GROUP, forget_epoch=4)
    assert result["status"] == "forgotten" and _facts_of(env, GROUP) == []
    tombstones = await env.idem.read_tombstones(NAME)
    # Success must leave a completion marker and tombstone: same-generation replay uses them to skip erasure of subsequent new writes.
    assert env.idem.erased_epoch(tombstones, GROUP_KEY) == 4
    assert env.idem.tombstone_epoch(tombstones, [GROUP_KEY]) == 4


@pytest.mark.parametrize("kind", ["locale", "display_name"])
async def test_unapplied_locale_or_display_item_marked_applied_without_evidence_fails_closed(env, kind):
    env.llm.responses = [SINGLE_FACTS]
    original = _fail_on_item(env, failing_seq=0)                # Crash before applying any item.
    with pytest.raises(HTTPException):
        await _post(env, _single_body(language="zh"))
    env.monkeypatch.setattr(env.routes, "_apply_keyed_item", original)
    path = _staging_file(env, KEY_GROUP)
    staging = json.loads(path.read_text(encoding="utf-8"))
    seq = next(item["seq"] for item in staging["items"] if item["kind"] == kind)
    staging["applied"] = [*staging["applied"], {"seq": seq}]   # Only sequence numbers remain, without completion evidence.
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
    # Applied language/fact items each carry completion evidence; recovery validates it and completes remaining application.
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
    await lock.acquire()                                         # Same-key retry holds the key lock to claim this orphan staging.
    try:
        sweep = asyncio.create_task(idem.cleanup_expired([NAME], ttl_s=100.0, now=now))
        for _ in range(20):
            await asyncio.sleep(0)
        await idem.update_key(NAME, "orphan-key", idem.transition("pending"))
    finally:
        lock.release()
    report = await sweep
    # The pre-enumeration key-record snapshot still shows an orphan; lock and reread before deletion, retaining staging claimed as pending.
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
        staging["applied"] = None                                # Field exists but value is null.
    elif damage == "kept_effect_keys_scalar":
        # Corrupt payload in an unapplied fact item of uncleared PART: retaining it would never allow replay.
        part_facts = next(item for item in staging["items"] if item["kind"] == "facts" and item["segment"] == 1)
        part_facts["effect_keys"] = 5
    elif damage == "item_bad_kind":
        staging["items"][0]["kind"] = "bogus"                   # Item is an object but has a corrupt type.
    elif damage == "applied_bad_seq":
        staging["applied"].append({"seq": "bad"})               # Object with corrupt sequence number.
    elif damage == "applied_scalar_entry":
        staging["applied"].append(7)                             # A scalar mixed into applied records.
    elif damage == "items_empty_object":
        staging["items"] = {}                                    # Falsy value: must not pass as an empty list via `or []`.
    else:
        staging["items"].append(7)
    path.write_text(json.dumps(staging, ensure_ascii=False), encoding="utf-8")
    assert _facts_of(env, GP)
    result = await _forget(env, GP)
    # Cannot discard only the cleared segment: cancel the whole key while completing privacy erasure normally.
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
        item["seq"] = position                                   # Other checks still pass after sequence-number reordering.
    staging["items"] = items
    path.write_text(json.dumps(staging, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(HTTPException) as excinfo:
        await _post(env, _single_body(language="zh"))
    # Language belongs to request identity and must have an item at pass creation; if the entire item disappears, it cannot authorize completion.
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
    staging["items"].pop()                                       # Entire trailing display-name item missing.
    path.write_text(json.dumps(staging, ensure_ascii=False), encoding="utf-8")
    result = await _post(env, _single_body())
    # Restore this item using the retry request's display name; do not finalize and permanently omit it.
    assert result["created"] == 2 and _key_state(env, KEY_GROUP) == "done"
    assert (GROUP_KEY, "串门群") in env.persona.display_names


async def test_damaged_erased_marker_never_skips_a_forget(env):
    env.llm.responses = [SINGLE_FACTS]
    await _post(env, _single_body(key=None, display_name=None))
    path = Path(env.idem.tombstones_path(NAME))
    path.write_text(json.dumps({GROUP_KEY: {"forget_epoch": "bad", "erased_epoch": 999}}), encoding="utf-8")
    result = await _forget(env, GROUP, forget_epoch=5)
    # A completion marker on a row with a corrupt fence is invalid: erase normally instead of returning duplicate, then rebuild the row from this clear.
    assert result.get("duplicate") is None and _facts_of(env, GROUP) == []
    assert env.idem.erased_epoch(await env.idem.read_tombstones(NAME), GROUP_KEY) == 5
    assert env.idem.erased_epoch({GROUP_KEY: {"forget_epoch": 3, "erased_epoch": 4}}, GROUP_KEY) is None


async def test_erase_behind_a_higher_fence_marks_that_fence_erased(env):
    env.llm.responses = [SINGLE_FACTS]
    await _post(env, _single_body(key=None, display_name=None))
    # Newer clearing (generation 9) erected a tombstone but failed before erasing.
    await env.idem.record_tombstones(NAME, {GROUP_KEY}, 9)
    result = await _forget(env, GROUP, forget_epoch=4)
    assert result["status"] == "forgotten" and _facts_of(env, GROUP) == []
    # This erasure occurs after generation 9's fence: record completion through 9 too, so its retry cannot erase subsequent legitimate writes.
    assert env.idem.erased_epoch(await env.idem.read_tombstones(NAME), GROUP_KEY) == 9


async def test_forget_scrubs_a_journal_whose_subject_index_is_damaged(env):
    env.llm.responses = [SINGLE_FACTS]
    original = _fail_on_item(env, failing_seq=0)
    with pytest.raises(HTTPException):
        await _post(env, _single_body())
    env.monkeypatch.setattr(env.routes, "_apply_keyed_item", original)
    path = _staging_file(env, KEY_GROUP)
    staging = json.loads(path.read_text(encoding="utf-8"))
    staging.pop("subjects")                                    # Index missing, but individual segments still identify the cleared subject.
    path.write_text(json.dumps(staging, ensure_ascii=False), encoding="utf-8")
    Path(env.idem.keys_path(NAME)).unlink()                    # Orphan: no key record to cancel through.
    await _forget(env, GROUP)
    # Do not skip because the index is corrupt: extraction source must leave disk, and future same-key retries must not restore pre-clear facts.
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
        return sorted(rows, key=lambda row: Path(row[0]) != paths["bad-key"])   # The corrupt record's staging is scanned first.

    env.monkeypatch.setattr(idem, "list_staging", bad_first)
    report = await idem.cleanup_expired([NAME], ttl_s=100.0, now=now)
    # The corrupt record retains only its own staging; other expired staging is cleaned normally.
    assert paths["bad-key"].exists() and not paths["done-key"].exists()
    assert report["staging_removed"] == 1


async def test_forget_scrubs_an_unreadable_orphan_journal(env):
    owned = Path(env.idem.staging_path(NAME, "owned-key"))
    orphan = Path(env.idem.staging_path(NAME, KEY_GROUP))
    orphan.parent.mkdir(parents=True, exist_ok=True)
    await env.idem.update_key(NAME, "owned-key", env.idem.transition("done"))
    for path in (owned, orphan):
        path.write_text('{"key": "x", "facts": ["猫薄荷"', encoding="utf-8")        # Unreadable.
    await _forget(env, GROUP)
    # Unreadable staging unclaimed by key records cannot identify involved subjects: erase in place into a source-free placeholder, retaining no extraction source.
    assert "猫薄荷" not in orphan.read_text(encoding="utf-8")
    # Staging claimed by a key record is outside this step's scope.
    assert "猫薄荷" in owned.read_text(encoding="utf-8")
    # Same-key retry finishes as canceled; never treat it as absent staging and regenerate cleared subjects.
    env.llm.responses = [SINGLE_FACTS]
    result = await _post(env, _single_body())
    assert result.get("duplicate") is True and env.llm.calls == 0 and _facts_of(env, GROUP) == []
    assert _key_state(env, KEY_GROUP) == "cancelled"


async def test_unreadable_staging_without_a_key_file_is_left_alone(env):
    orphan = Path(env.idem.staging_path(NAME, "orphan-key"))
    orphan.parent.mkdir(parents=True, exist_ok=True)
    orphan.write_text('{"key": "x", "facts": ["猫薄荷"', encoding="utf-8")
    assert not Path(env.idem.keys_path(NAME)).exists()
    await _forget(env, GROUP)
    # Staging exists but no key file: ownership unknown (lost / restored); preserve unchanged.
    assert "猫薄荷" in orphan.read_text(encoding="utf-8")


async def test_retry_spelling_out_the_default_scope_is_the_same_request(env):
    from memory.scopes import MemorySubject

    env.llm.responses = [SINGLE_FACTS]
    original = _fail_on_item(env, failing_seq=0)
    with pytest.raises(HTTPException):
        await _post(env, _single_body())
    env.monkeypatch.setattr(env.routes, "_apply_keyed_item", original)
    default_scope = MemorySubject.create(GROUP["subject_kind"], GROUP["subject_id"]).scope
    result = await _post(env, _single_body(subject={**GROUP, "scope": default_scope}))
    # Omitted scope and explicit default scope denote the same subject: same-key retry continues application normally instead of returning 422.
    assert result["created"] == 2 and env.llm.calls == 1


async def test_over_fence_erased_marker_is_repaired_by_the_next_erase(env):
    env.llm.responses = [SINGLE_FACTS]
    await _post(env, _single_body(key=None, display_name=None))
    path = Path(env.idem.tombstones_path(NAME))
    path.write_text(json.dumps({GROUP_KEY: {"forget_epoch": 5, "erased_epoch": 999, "forgotten_at": 1.0}}),
                    encoding="utf-8")
    result = await _forget(env, GROUP, forget_epoch=5)
    assert result["status"] == "forgotten" and _facts_of(env, GROUP) == []
    # Success must leave a completion marker recognized by readers: this erasure rewrites an invalid beyond-fence marker to 5.
    assert env.idem.erased_epoch(await env.idem.read_tombstones(NAME), GROUP_KEY) == 5


async def test_cancelled_flag_on_an_unstripped_journal_fails_closed(env):
    env.llm.responses = [SINGLE_FACTS]
    original = _fail_on_item(env, failing_seq=0)
    with pytest.raises(HTTPException):
        await _post(env, _single_body())
    env.monkeypatch.setattr(env.routes, "_apply_keyed_item", original)
    path = _staging_file(env, KEY_GROUP)
    staging = json.loads(path.read_text(encoding="utf-8"))
    staging["cancelled_by_forget"] = True                       # Only one boolean changed; all items remain.
    path.write_text(json.dumps(staging, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(HTTPException) as excinfo:
        await _post(env, _single_body())
    # Not the fully scrubbed shape written by clearing: this boolean alone cannot cancel the entire request.
    assert excinfo.value.status_code == 503 and _key_state(env, KEY_GROUP) == "pending"
    assert path.exists()


async def test_forgotten_locale_only_segment_gets_no_display_name_on_retry(env):
    env.llm.responses = [[
        {"segment": 1, "facts": []},                             # No facts extracted from the cleared segment.
        {"segment": 2, "facts": [{"text": "Mika 的猫下午在窗台睡觉", "importance": 6}]},
    ]]
    body = _segments_body(language="zh")
    body["segments"][0].pop("display_name")                      # This segment also lacked a display name at pass creation, leaving only the language item.
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
    retry = _segments_body(language="zh")                       # Retry supplies a display name outside request identity.
    await _post(env, retry)
    # Do not fill display names for cleared/discarded segments: pre-clear keys must not overwrite metadata in subsequently rebuilt sections.
    assert (GP_KEY, "团子") not in env.persona.display_names
    assert (PART_KEY, "Mika") in env.persona.display_names


async def test_damaged_forgotten_keys_do_not_block_the_erase(env):
    env.llm.responses = [SINGLE_FACTS]
    await _post(env, _single_body(key=None, display_name=None))
    await env.idem.update_key(NAME, KEY_GROUP, env.idem.transition(
        "pending", request={"shape": "single", "wire_keys": [GROUP_KEY], "content_hash": "h"},
        routed_keys=["participant:neko_visit:routed"],
    ))

    def damage(old):
        return {**old, "forgotten_keys": [{"bad": 1}, "kept:key"]}   # Corrupt old value containing an object.

    await env.idem.update_key(NAME, KEY_GROUP, damage)
    lock = env.idem.key_lock(NAME, KEY_GROUP)
    await lock.acquire()                                         # Same-key request is generating with no staging yet.
    try:
        result = await _forget(env, GROUP)
    finally:
        lock.release()
    # Corrupt auxiliary state must not make clearing return 500 before erasure; erase normally. Old markers are the only evidence of clearing during generation,
    # so do not simply discard corrupt ones: conservatively record every subject in this key record.
    assert result["status"] == "forgotten" and _facts_of(env, GROUP) == []
    record = json.loads(Path(env.idem.keys_path(NAME)).read_text(encoding="utf-8"))[KEY_GROUP]
    assert record["forgotten_keys"] == sorted({GROUP_KEY, "participant:neko_visit:routed"})


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
    # Same-key retry after generation failure reuses initially reserved sequence numbers instead of moving an old request later with newer numbers.
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
    # Existing reservations corrupt: do not allocate an unrecordable new batch that moves the old request later on every retry.
    assert excinfo.value.status_code == 503 and env.llm.calls == 1


async def test_fenced_segment_does_not_move_the_trust_pool(env):
    captured = []
    real = env.routes._apply_trust_for_segments

    async def capture(states):
        captured.append(list(states))
        return await real(states)

    env.monkeypatch.setattr(env.routes, "_apply_trust_for_segments", capture)
    await _forget(env, GROUP, forget_epoch=5)                   # Old request arriving only after clearing (generation 0).
    env.llm.responses = [SINGLE_FACTS]
    body = _single_body(
        speaker_label="Mika", speaker_id="neko_visit:5f2c1b7e", speaker_tier="none",
        speaker_activity_events=[{"id": "evt-00001", "count": 1}],
    )
    await _post(env, body)
    # A segment whose memories are blocked by tombstones must not add its activity to the trust pool either.
    (states,) = captured
    assert env.routes._trust_mutation_for(states[0]) is None
    assert _facts_of(env, GROUP) == []


async def test_duplicate_forget_still_cancels_a_late_pre_forget_staging(env):
    env.llm.responses = [SINGLE_FACTS]
    await _forget(env, GROUP, forget_epoch=5)                   # Original erasure completed.
    real_apply = env.routes._apply_keyed_staging

    async def crash(*_args, **_kwargs):
        raise RuntimeError("process killed before applying")

    env.monkeypatch.setattr(env.routes, "_apply_keyed_staging", crash)
    with pytest.raises(HTTPException):
        await _post(env, _single_body())                        # Pre-clear request arrives later: crash immediately after staging.
    env.monkeypatch.setattr(env.routes, "_apply_keyed_staging", real_apply)
    assert _staging_file(env, KEY_GROUP).exists()
    result = await _forget(env, GROUP, forget_epoch=5)          # Replay clearing for the same generation.
    # Do not erase storage again (duplicate), but cancel leftover plaintext staging normally so TTL cleanup does not protect it forever.
    assert result.get("duplicate") is True
    assert _key_state(env, KEY_GROUP) == "cancelled" and not _staging_file(env, KEY_GROUP).exists()


async def test_erase_completion_restores_a_fence_lowered_meanwhile(env):
    path = Path(env.idem.tombstones_path(NAME))
    path.parent.mkdir(parents=True, exist_ok=True)
    # During erasure, cleanup removed the old high fence, then lower-generation clearing rebuilt it as 3.
    path.write_text(json.dumps({GROUP_KEY: {"forget_epoch": 3, "forgotten_at": 1.0}}), encoding="utf-8")
    await env.idem.mark_tombstone_erased(NAME, GROUP_KEY, 3, covered_epoch=8)
    row = json.loads(path.read_text(encoding="utf-8"))[GROUP_KEY]
    # A higher generation completed: restore the fence upward and mark completion at 8 so higher-generation replay skips.
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
    first["subject"], second["subject"] = second["subject"], first["subject"]   # Swap the two segment targets.
    path.write_text(json.dumps(staging, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(HTTPException) as excinfo:
        await _post(env, _segments_body())
    # Both targets belong to the batch's routed set but occupy incorrect positions; never write them into each other's memory domains.
    assert excinfo.value.status_code == 503
    assert _facts_of(env, GP) == [] and _facts_of(env, PART) == []


async def test_stale_duplicate_forget_cancels_against_the_effective_fence(env):
    env.llm.responses = [SINGLE_FACTS]
    await _forget(env, GROUP, forget_epoch=10)                  # Fence and completion marker are both 10.
    real_apply = env.routes._apply_keyed_staging

    async def crash(*_args, **_kwargs):
        raise RuntimeError("process killed before applying")

    env.monkeypatch.setattr(env.routes, "_apply_keyed_staging", crash)
    with pytest.raises(HTTPException):
        await _post(env, _single_body(subject_epochs={GROUP_KEY: 7}))   # Late request predating the fence.
    env.monkeypatch.setattr(env.routes, "_apply_keyed_staging", real_apply)
    result = await _forget(env, GROUP, forget_epoch=5)          # Stale clearing replay.
    # Compare against effective fence 10: generation-7 staging predates it and is canceled, not retained as a legitimate new write because 7 > 5.
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
        return {**old, "routed_positions": old["routed_positions"][:1]}   # Truncate positional routing.

    await env.idem.update_key(NAME, KEY_SEGMENTS, truncate)
    with pytest.raises(HTTPException) as excinfo:
        await _post(env, _segments_body())
    # This field exists but is corrupt: do not fall back to set-only verification, which would accept swapped targets.
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
    # Display name not written: do not mark this item applied or finalize the key.
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
    gp_display["display_name"] = 5                              # Corruption lies in the segment being cleared.
    path.write_text(json.dumps(staging, ensure_ascii=False), encoding="utf-8")
    await _forget(env, GP)
    # The cleared segment's payload must be erased anyway: do not cancel the entire key; retry and complete uncleared segments normally.
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
    # Keyed logging paths must raise on unreadable persona; False cannot distinguish failure from a legitimate no-op.
    assert seen == [True]


async def test_cleanup_protects_tombstones_named_only_by_segments(env):
    idem = env.idem
    now = time.time()
    path = Path(idem.staging_path(NAME, "orphan-seg"))
    path.parent.mkdir(parents=True, exist_ok=True)
    # Index missing, but segments still identify GROUP; staging is still fresh and will be retained.
    path.write_text(json.dumps({
        "key": "orphan-seg", "created_at": now,
        "segments": [{"wire_key": GROUP_KEY, "subject": GROUP}],
    }), encoding="utf-8")
    tombstones = Path(idem.tombstones_path(NAME))
    tombstones.write_text(json.dumps({GROUP_KEY: {"forget_epoch": 1, "forgotten_at": now - 500}}), encoding="utf-8")
    await idem.cleanup_expired([NAME], ttl_s=100.0, now=now)
    # Retained staging still references this subject: its tombstone must not expire first, or a later claim could restore pre-clear facts.
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
    (recorded,) = record["locale_orders"]                      # Recorded on the key before reservation persistence.
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
    assert (GP_KEY, "团子") in env.persona.display_names       # The first segment's display name was written and logged already.
    retry = _segments_body()
    retry["segments"][0]["display_name"] = "团子新名"           # Name changed while the key remained pending.
    await _post(env, retry)
    # Display names are outside request identity: recovery retry must overwrite with the current value rather than finalize with an old name.
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
        item["seq"] = position                                   # Sequence numbers reordered; every other item-level check passes.
    staging["items"] = items
    path.write_text(json.dumps(staging, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(HTTPException) as excinfo:
        await _post(env, _segments_body())
    # Entire fact item missing: do not finalize the remainder and permanently omit generated facts.
    assert excinfo.value.status_code == 503 and _key_state(env, KEY_SEGMENTS) == "pending"


async def test_unrelated_subject_epochs_do_not_change_the_request_identity(env):
    env.llm.responses = [SINGLE_FACTS]
    original = _fail_on_item(env, failing_seq=0)
    with pytest.raises(HTTPException):
        await _post(env, _single_body())
    env.monkeypatch.setattr(env.routes, "_apply_keyed_item", original)
    # Retry includes an extra generation for an unrelated subject: identical effect, so do not return 422.
    result = await _post(env, _single_body(subject_epochs={GROUP_KEY: 0, "participant:neko_visit:other": 3}))
    assert result["created"] == 2 and env.llm.calls == 1


async def test_retry_does_not_reserve_locale_for_a_forgotten_segment(env):
    import tempfile

    from app.memory_server import locale_state

    env.llm.responses = [RuntimeError("LLM failed"), PART_ONLY_BATCH_FACTS]
    with pytest.raises(RuntimeError):
        await _post(env, _segments_body(language="zh"))

    def forgotten_meanwhile(old):
        # Clearing arriving during generation only records cleared subjects (key lock occupied); clearing itself already removed the language row.
        return {**old, "forgotten_keys": [GP_KEY], "locale_orders": None}

    await env.idem.update_key(NAME, KEY_SEGMENTS, forgotten_meanwhile)
    sidecar = Path(locale_state._subject_locale_path(NAME))
    assert str(sidecar).startswith(tempfile.gettempdir())       # Never touch real runtime root directories.
    locale_state.invalidate_prompt_locale_caches()
    if sidecar.exists():
        sidecar.unlink()                                         # GP is absent from language storage after clearing.
    locale_state.invalidate_prompt_locale_caches()
    original = env.routes._apply_keyed_item

    async def _crash(lanlan_name, item, segment, generation):
        raise RuntimeError("injected crash before applying anything")

    env.monkeypatch.setattr(env.routes, "_apply_keyed_item", _crash)
    with pytest.raises(HTTPException):
        await _post(env, _segments_body(language="zh"))         # Crash after staging, with no language item for the cleared segment.
    env.monkeypatch.setattr(env.routes, "_apply_keyed_item", original)
    # Restore from staging: cleared/discarded segments pass recovery validation without language items.
    result = await _post(env, _segments_body(language="zh"))
    assert result["segments"][1]["created"] == 2
    rows = json.loads(sidecar.read_text(encoding="utf-8")).get("subjects", {}) if sidecar.exists() else {}
    # Do not reserve again for cleared segments or restore cleared subjects to language storage.
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
    assert _key_state(env, KEY_SEGMENTS) == "pending"           # Retain staging for uncleared segments.
    result = await env.routes.list_scoped_subjects(NAME, platform="neko_visit")
    staged = {row["subject_id"] for row in result["subjects"] if row["staged"]}
    # The cleared segment's content was erased: do not treat it as pending staging and resurrect cleared objects.
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
    # Retained segment has a corrupt language item; retries would only return 503. Cancel the whole key rather than retaining it after discarding only the cleared segment.
    assert _key_state(env, KEY_SEGMENTS) == "cancelled"


async def test_forget_epochs_endpoint_reports_the_current_fence(env):
    await _forget(env, GROUP, forget_epoch=6)
    result = await env.routes.get_forget_epochs(NAME, subject=[GROUP_KEY, PART_KEY])
    # Keys with tombstones report their current fences; others are absent.
    assert result == {"epochs": {GROUP_KEY: 6}}
    Path(env.idem.tombstones_path(NAME)).write_text(json.dumps({GROUP_KEY: {"forget_epoch": "bad"}}), encoding="utf-8")
    with pytest.raises(HTTPException) as excinfo:
        await env.routes.get_forget_epochs(NAME, subject=[GROUP_KEY])
    # An unrecognizable fence cannot mean no fence.
    assert excinfo.value.status_code == 503


async def test_forget_marked_while_recording_orders_skips_the_reservation(env):
    import tempfile

    from app.memory_server import locale_state

    sidecar = Path(locale_state._subject_locale_path(NAME))
    assert str(sidecar).startswith(tempfile.gettempdir())       # Never touch real runtime root directories.
    locale_state.invalidate_prompt_locale_caches()
    if sidecar.exists():
        sidecar.unlink()
    locale_state.invalidate_prompt_locale_caches()
    real_update = env.idem.update_key

    async def racing_update(name, key, fn):
        result = await real_update(name, key, fn)
        record = json.loads(Path(env.idem.keys_path(NAME)).read_text(encoding="utf-8")).get(key) or {}
        if record.get("locale_orders") and not record.get("forgotten_keys"):
            # At the moment sequence numbers are recorded, clearing adds the first segment to forgotten_keys.
            await real_update(name, key, lambda old: {**old, "forgotten_keys": [GP_KEY]})
        return result

    env.monkeypatch.setattr(env.idem, "update_key", racing_update)
    env.llm.responses = [RuntimeError("LLM failed")]
    with pytest.raises(RuntimeError):
        await _post(env, _segments_body(language="zh"))
    rows = json.loads(sidecar.read_text(encoding="utf-8")).get("subjects", {}) if sidecar.exists() else {}
    # Recheck before persisting reservations: do not reserve newly cleared segments or restore them to language storage.
    assert not any(GP["subject_id"] in key for key in rows)
    assert any(PART["subject_id"] in key for key in rows)


def test_a_reservation_older_than_a_forget_does_not_recreate_the_locale_row():
    import tempfile

    from app.memory_server import locale_state
    from memory.scopes import MemorySubject

    name = "LocaleRaceChar"
    subject = MemorySubject.participant("neko_visit", "u_race")
    sidecar = Path(locale_state._subject_locale_path(name))
    assert str(sidecar).startswith(tempfile.gettempdir())
    (stale,) = locale_state.allocate_subject_prompt_locale_orders(name, [subject])
    locale_state.forget_subject_prompt_locale(name, subject)     # Clearing completes after allocation but before reservation persistence.
    locale_state.reserve_subject_prompt_locale_orders(name, [subject], orders=[stale])
    rows = json.loads(sidecar.read_text(encoding="utf-8")).get("subjects", {}) if sidecar.exists() else {}
    # Pre-clear reservations will necessarily be rejected: do not use them to restore cleared subjects to language storage.
    assert not any("u_race" in key for key in rows)


async def test_facts_item_moved_to_another_segment_fails_closed(env):
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
    gp_facts = next(item for item in staging["items"] if item["kind"] == "facts" and item["segment"] == 0)
    gp_facts["segment"] = 1                                     # Sequence numbers, effect keys, and segment targets remain valid.
    path.write_text(json.dumps(staging, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(HTTPException) as excinfo:
        await _post(env, _segments_body())
    # Fact item moved to another segment: do not write this segment's facts into another subject's memory domain.
    assert excinfo.value.status_code == 503
    assert _facts_of(env, GP) == [] and _facts_of(env, PART) == []


async def test_staging_deletes_pass_the_cloudsave_gate(env):
    calls = []

    def gate(_cm, *, operation, target):
        calls.append((operation, target))

    env.monkeypatch.setattr(env.idem, "assert_cloudsave_writable", gate)
    path = Path(env.idem.staging_path(NAME, KEY_GROUP))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")
    await env.idem.delete_staging(NAME, KEY_GROUP)
    # Staging deletion uses the write gate too: memory directories cannot be modified during cloudsave read-only mode / snapshot import.
    assert ("delete", f"memory/{NAME}/idempotency_staging/{path.name}") in calls


async def test_restored_retry_drops_segments_forgotten_during_generation(env):
    env.llm.responses = [BATCH_FACTS]

    async def _crash(lanlan_name, item, segment, generation):
        raise RuntimeError("injected crash before applying anything")

    original = env.routes._apply_keyed_item
    env.monkeypatch.setattr(env.routes, "_apply_keyed_item", _crash)
    with pytest.raises(HTTPException):
        await _post(env, _segments_body())
    env.monkeypatch.setattr(env.routes, "_apply_keyed_item", original)

    def forgotten_meanwhile(old):
        # Generation-time clearing without a generation only recorded cleared subjects on the record.
        return {**old, "forgotten_keys": [GP_KEY]}

    await env.idem.update_key(NAME, KEY_SEGMENTS, forgotten_meanwhile)
    await _post(env, _segments_body())
    # Recovery likewise discards cleared segments using record markers, never restoring facts extracted before clearing.
    assert _facts_of(env, GP) == [] and len(_facts_of(env, PART)) == 2


async def test_failing_pre_erase_cancellation_does_not_block_the_forget(env):
    env.llm.responses = [SINGLE_FACTS]
    await _post(env, _single_body(key=None, display_name=None))
    real = env.routes._cancel_staged_writes_for_subjects
    calls = {"n": 0}

    async def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            calls["first_best_effort"] = kwargs.get("best_effort")
            raise env.idem.IdempotencyStateError("staging replaced mid-scan")
        return await real(*args, **kwargs)

    env.monkeypatch.setattr(env.routes, "_cancel_staged_writes_for_subjects", flaky)
    result = await _forget(env, GROUP)
    # Pre-erasure scanning is best-effort: auxiliary staging errors must not cause clearing to return 500 before deleting anything.
    assert result["status"] == "forgotten" and _facts_of(env, GROUP) == [] and calls["n"] == 2
    # Pre-erasure scanning tolerates individual failures; post-erasure scanning does not.
    assert calls["first_best_effort"] is True


async def test_legacy_integer_fact_ids_in_the_journal_do_not_wedge_the_key(env):
    env.llm.responses = [SINGLE_FACTS]
    original = _fail_on_item(env, failing_seq=1)
    with pytest.raises(HTTPException):
        await _post(env, _single_body())
    env.monkeypatch.setattr(env.routes, "_apply_keyed_item", original)
    path = _staging_file(env, KEY_GROUP)
    staging = json.loads(path.read_text(encoding="utf-8"))
    entry = next(e for e in staging["applied"] if e.get("facts_applied"))
    entry["reconciled"] = [12345]                               # reconcile finds a fact with a legacy integer ID.
    path.write_text(json.dumps(staging, ensure_ascii=False), encoding="utf-8")
    result = await _post(env, _single_body())
    # Legacy integer IDs are valid application results: do not classify this staging as corrupt and leave the key permanently at 503.
    assert _key_state(env, KEY_GROUP) == "done" and result.get("duplicate") is None


async def test_locale_only_segment_forgotten_after_staging_is_marked_on_disk(env):
    from memory.scopes import coerce_subject

    env.llm.responses = [[
        {"segment": 1, "facts": []},
        {"segment": 2, "facts": [{"text": "Mika 的猫下午在窗台睡觉", "importance": 6}]},
    ]]
    body = _segments_body(language="zh")
    body["segments"][0].pop("display_name")                      # Only the language item remains in the first segment.
    real_write = env.idem.write_staging
    writes = {"n": 0}

    async def write_then_forget(name, key, document):
        await real_write(name, key, document)
        writes["n"] += 1
        if writes["n"] == 1:
            # Clearing advances the first segment's generation after staging persistence but before rechecking.
            env.fs._bump_subject_forget_generation(NAME, coerce_subject(GP))

    async def _crash(*_args, **_kwargs):
        # Process killed after rechecking but before any application-loop writes: only the recheck's own writes remain on disk.
        raise RuntimeError("injected crash before applying anything")

    env.monkeypatch.setattr(env.idem, "write_staging", write_then_forget)
    env.monkeypatch.setattr(env.routes, "_apply_keyed_staging", _crash)
    with pytest.raises(HTTPException):
        await _post(env, body)
    staging = json.loads(_staging_file(env, KEY_SEGMENTS).read_text(encoding="utf-8"))
    # Persist segment-level discard markers even for language-only segments, or recovery retry will add a display name.
    assert staging["segments"][0].get("dropped_by_forget") is True


async def test_forget_landing_during_apply_fences_the_segment_from_trust(env):
    from memory.scopes import coerce_subject

    env.llm.responses = [SINGLE_FACTS]
    original = env.routes._apply_keyed_item

    async def forget_meanwhile(lanlan_name, item, segment, generation):
        if item["kind"] == "facts":
            env.fs._bump_subject_forget_generation(NAME, coerce_subject(GROUP))   # Clearing without a generation arrives.
        return await original(lanlan_name, item, segment, generation)

    captured = []
    real_trust = env.routes._apply_trust_for_segments

    async def capture(states):
        captured.append(list(states))
        return await real_trust(states)

    env.monkeypatch.setattr(env.routes, "_apply_keyed_item", forget_meanwhile)
    env.monkeypatch.setattr(env.routes, "_apply_trust_for_segments", capture)
    body = _single_body(
        speaker_label="Mika", speaker_id="neko_visit:5f2c1b7e", speaker_tier="none",
        speaker_activity_events=[{"id": "evt-00002", "count": 1}],
    )
    await _post(env, body)
    # The fact layer silently discarded these writes: trust isolation and memory discard share one criterion, excluding activity from the trust pool.
    (states,) = captured
    assert env.routes._trust_mutation_for(states[0]) is None


async def test_display_name_that_keeps_failing_stops_blocking_the_key(env):
    env.llm.responses = [SINGLE_FACTS]

    async def always_fail(*args, **kwargs):
        raise OSError("persona.json is read-only")

    env.monkeypatch.setattr(env.persona, "aupdate_subject_display_name", always_fail)
    outcomes = []
    for _ in range(3):
        try:
            await _post(env, _single_body())
            outcomes.append("ok")
        except HTTPException as exc:
            outcomes.append(exc.status_code)
    # Display data: abandon the item after several failed writes rather than blocking the key and trust writes indefinitely.
    assert outcomes == [503, 503, "ok"] and _key_state(env, KEY_GROUP) == "done"


async def test_duplicate_forget_cancels_only_the_request_subject(env):
    from memory.scopes import coerce_subject

    seen = []

    async def capture(lanlan_name, subject_keys, **kwargs):
        seen.append(set(subject_keys))
        return 0

    env.monkeypatch.setattr(env.routes, "_cancel_staged_writes_for_subjects", capture)
    subject, fanned = coerce_subject(GROUP), coerce_subject(PART)
    result = await env.routes._forget_duplicate_after_cancel(NAME, subject, [subject, fanned], 5)
    # Duplicate clearing cancels only the request subject's own staging: later fanout writes with their own generations are incomparable and legitimate.
    assert result.get("duplicate") is True and seen == [{GROUP_KEY}]


async def test_epoch_found_erased_under_the_locks_still_cancels_staging(env):
    env.llm.responses = [SINGLE_FACTS]
    await _forget(env, GROUP, forget_epoch=10)
    real_apply = env.routes._apply_keyed_staging

    async def crash(*_args, **_kwargs):
        raise RuntimeError("process killed before applying")

    env.monkeypatch.setattr(env.routes, "_apply_keyed_staging", crash)
    with pytest.raises(HTTPException):
        await _post(env, _single_body(subject_epochs={GROUP_KEY: 7}))
    env.monkeypatch.setattr(env.routes, "_apply_keyed_staging", real_apply)
    real_check = env.routes._forget_epoch_already_erased
    checks = {"n": 0}

    async def erased_only_under_locks(*args):
        checks["n"] += 1
        return False if checks["n"] == 1 else await real_check(*args)

    real_cancel = env.routes._cancel_staged_writes_for_subjects
    cancels = {"n": 0}

    async def staging_written_after_pre_pass(*args, **kwargs):
        cancels["n"] += 1
        if cancels["n"] == 1:
            return 0                                             # Staging was not yet written during pre-erasure scanning.
        return await real_cancel(*args, **kwargs)

    env.monkeypatch.setattr(env.routes, "_forget_epoch_already_erased", erased_only_under_locks)
    env.monkeypatch.setattr(env.routes, "_cancel_staged_writes_for_subjects", staging_written_after_pre_pass)
    result = await _forget(env, GROUP, forget_epoch=5)
    # The unlocked recheck passed but the locked recheck detects completed erasure: run cancellation after unlocking too, leaving no pre-clear plaintext staging.
    assert result.get("duplicate") is True and checks["n"] == 2
    assert _key_state(env, KEY_GROUP) == "cancelled"


async def test_malformed_forgotten_keys_marker_fails_closed(env):
    env.llm.responses = [RuntimeError("LLM failed"), SINGLE_FACTS]
    with pytest.raises(RuntimeError):
        await _post(env, _single_body())
    await env.idem.update_key(NAME, KEY_GROUP, lambda old: {**old, "forgotten_keys": GROUP_KEY})
    with pytest.raises(HTTPException) as excinfo:
        await _post(env, _single_body())
    # The only durable evidence of clearing during generation is corrupt: do not interpret it as empty and reextract cleared subjects.
    assert excinfo.value.status_code == 503 and env.llm.calls == 1


async def test_none_locale_order_outside_a_forgotten_segment_fails_closed(env):
    env.llm.responses = [RuntimeError("LLM failed"), SINGLE_FACTS]
    with pytest.raises(RuntimeError):
        await _post(env, _single_body(language="zh"))
    await env.idem.update_key(NAME, KEY_GROUP, lambda old: {**old, "locale_orders": [None]})
    with pytest.raises(HTTPException) as excinfo:
        await _post(env, _single_body(language="zh"))
    # None is valid only on cleared segments; elsewhere it is corrupt and must not silently skip language writes.
    assert excinfo.value.status_code == 503


async def test_record_with_routed_keys_but_no_positions_fails_closed(env):
    env.llm.responses = [SINGLE_FACTS]
    original = _fail_on_item(env, failing_seq=0)
    with pytest.raises(HTTPException):
        await _post(env, _single_body())
    env.monkeypatch.setattr(env.routes, "_apply_keyed_item", original)

    def drop_positions(old):
        return {name: value for name, value in old.items() if name != "routed_positions"}

    await env.idem.update_key(NAME, KEY_GROUP, drop_positions)
    with pytest.raises(HTTPException) as excinfo:
        await _post(env, _single_body())
    # Both fields are always written together: when only the set remains, do not fall back to set-only verification.
    assert excinfo.value.status_code == 503


async def test_unreadable_tombstones_at_the_trust_step_answer_503(env):
    env.llm.responses = [SINGLE_FACTS]
    real_trust = env.routes._apply_trust_for_segments

    async def trust_crash(_states):
        raise RuntimeError("killed after every item was journaled")

    env.monkeypatch.setattr(env.routes, "_apply_trust_for_segments", trust_crash)
    with pytest.raises(Exception):
        await _post(env, _single_body())
    env.monkeypatch.setattr(env.routes, "_apply_trust_for_segments", real_trust)
    Path(env.idem.tombstones_path(NAME)).write_text("{torn", encoding="utf-8")
    with pytest.raises(HTTPException) as excinfo:
        await _post(env, _single_body())
    # Cannot identify segments blocked by clearing: do not discard all trust writes and finalize normally; return 503 until tombstones are readable.
    assert excinfo.value.status_code == 503 and _key_state(env, KEY_GROUP) == "pending"


async def test_pre_erase_pass_skips_only_the_failing_journal(env):
    other = KEY_GROUP.replace("group:0", "group:1")
    env.llm.responses = [SINGLE_FACTS, SINGLE_FACTS]
    original = _fail_on_item(env, failing_seq=0)
    for key in (KEY_GROUP, other):
        with pytest.raises(HTTPException):
            await _post(env, _single_body(key=key))
    env.monkeypatch.setattr(env.routes, "_apply_keyed_item", original)
    real_read = env.idem.read_staging

    async def read_fails_for_one(lanlan_name, key):
        if key == KEY_GROUP:
            raise OSError("staging locked by another process")
        return await real_read(lanlan_name, key)

    env.monkeypatch.setattr(env.idem, "read_staging", read_fails_for_one)
    cancelled = await env.routes._cancel_staged_writes_for_subjects(NAME, {GROUP_KEY}, best_effort=True)
    # Pre-erasure scanning tolerates individual failures: skip only the failing staging file and cancel other staging involving cleared subjects normally.
    assert cancelled == 1 and _key_state(env, other) == "cancelled" and _key_state(env, KEY_GROUP) == "pending"
    with pytest.raises(OSError):
        # Post-erasure scanning does not tolerate failures: propagate errors so the entire clear retries.
        await env.routes._cancel_staged_writes_for_subjects(NAME, {GROUP_KEY})


async def test_cancellation_marker_inside_a_real_journal_fails_closed(env):
    env.llm.responses = [SINGLE_FACTS]
    original = _fail_on_item(env, failing_seq=0)
    with pytest.raises(HTTPException):
        await _post(env, _single_body())
    env.monkeypatch.setattr(env.routes, "_apply_keyed_item", original)
    path = _staging_file(env, KEY_GROUP)
    staging = json.loads(path.read_text(encoding="utf-8"))
    staging[env.idem.UNREADABLE_CANCELLED_MARKER] = True
    path.write_text(json.dumps(staging, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(HTTPException) as excinfo:
        await _post(env, _single_body())
    # Accept only placeholders without other fields: a marker mixed into a normal log is corruption, not proof of cancellation allowing loss of unapplied effects.
    assert excinfo.value.status_code == 503 and _key_state(env, KEY_GROUP) == "pending"


async def test_concurrent_erase_marks_on_a_corrupt_tombstone_file_keep_both_rows(env):
    import asyncio

    path = Path(env.idem.tombstones_path(NAME))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    await asyncio.gather(
        env.idem.mark_tombstone_erased(NAME, "A", 3),
        env.idem.mark_tombstone_erased(NAME, "B", 4),
    )
    tombstones = await env.idem.read_tombstones(NAME)
    # Two clears finish concurrently: the later one must not skip its row merely because it reads the file rebuilt by the first, reporting success without fence/completion markers.
    assert env.idem.erased_epoch(tombstones, "A") == 3 and env.idem.erased_epoch(tombstones, "B") == 4


async def test_failed_backup_copy_does_not_block_the_rebuild(env):
    import shutil

    path = Path(env.idem.tombstones_path(NAME))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")

    def no_space(*args, **kwargs):
        raise OSError("no space left on device")

    env.monkeypatch.setattr(shutil, "copy2", no_space)
    await env.idem.mark_tombstone_erased(NAME, GROUP_KEY, 2)
    # Backup copies are diagnostic only: even if copying fails, record erasure completion, or same-generation retry erases again.
    assert env.idem.erased_epoch(await env.idem.read_tombstones(NAME), GROUP_KEY) == 2


async def test_failed_rebuild_of_a_corrupt_tombstone_file_leaves_it_in_place(env):
    path = Path(env.idem.tombstones_path(NAME))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")

    def disk_full(*args, **kwargs):
        raise OSError("no space left on device")

    env.monkeypatch.setattr(env.idem, "atomic_write_json", disk_full)
    with pytest.raises(OSError):
        await env.idem.mark_tombstone_erased(NAME, GROUP_KEY, 2)
    # Cannot persist the rebuild: preserve the corrupt file at the original path (readers still fail closed), never turn it into absent tombstones.
    assert path.read_text(encoding="utf-8") == "{not json"
    with pytest.raises(env.idem.IdempotencyStateError):
        await env.idem.read_tombstones(NAME)


async def test_tombstone_read_failure_is_not_treated_as_corruption(env):
    path = Path(env.idem.tombstones_path(NAME))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"other": {"forgotten_at": 1.0, "forget_epoch": 9}}), encoding="utf-8")
    real_read = env.idem.read_json_tolerating_replace

    def locked(target, *args, **kwargs):
        if Path(target) == path:
            raise PermissionError("sharing violation")
        return real_read(target, *args, **kwargs)

    env.monkeypatch.setattr(env.idem, "read_json_tolerating_replace", locked)
    with pytest.raises(env.idem.IdempotencyStateError):
        await env.idem.mark_tombstone_erased(NAME, GROUP_KEY, 2)
    # Read failure does not imply corruption: do not quarantine a healthy tombstone file and lose other subjects' fences.
    assert not list(path.parent.glob(path.name + ".corrupt-*"))
    assert json.loads(path.read_text(encoding="utf-8"))["other"]["forget_epoch"] == 9


async def test_corrupt_tombstone_file_is_not_renamed_while_writes_are_fenced(env):
    from utils.cloudsave_runtime import MaintenanceModeError

    path = Path(env.idem.tombstones_path(NAME))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")

    def fenced(*args, **kwargs):
        raise MaintenanceModeError("snapshot_import")

    env.monkeypatch.setattr(env.idem, "assert_cloudsave_writable", fenced)
    with pytest.raises(MaintenanceModeError):
        await env.idem.mark_tombstone_erased(NAME, GROUP_KEY, 2)
    # Renaming during read-only mode / snapshot import without writing back would leave no tombstone file: pass the gate before renaming.
    assert path.exists() and not list(path.parent.glob(path.name + ".corrupt-*"))


async def test_display_name_after_a_segment_dropped_during_apply_is_not_written(env):
    from memory.scopes import coerce_subject

    env.llm.responses = [SINGLE_FACTS]
    original = env.routes._apply_keyed_item
    kinds = []

    async def forget_meanwhile(lanlan_name, item, segment, generation):
        kinds.append(item["kind"])
        if item["kind"] == "facts":
            env.fs._bump_subject_forget_generation(NAME, coerce_subject(GROUP))   # Clearing without a generation arrives.
        return await original(lanlan_name, item, segment, generation)

    env.monkeypatch.setattr(env.routes, "_apply_keyed_item", forget_meanwhile)
    await _post(env, _single_body())
    # Segment just marked discarded by clearing: its display-name item after facts must not restore the name clearing just erased.
    assert env.routes._KEYED_ITEM_DISPLAY_NAME not in kinds and "facts" in kinds


async def test_given_up_display_name_is_written_once_persona_recovers(env):
    env.llm.responses = [SINGLE_FACTS]
    real_update = env.persona.aupdate_subject_display_name
    written = []

    async def always_fail(*args, **kwargs):
        raise OSError("persona.json is read-only")

    async def record(*args, **kwargs):
        written.append(args)
        return await real_update(*args, **kwargs)

    env.monkeypatch.setattr(env.persona, "aupdate_subject_display_name", always_fail)
    for _ in range(2):
        with pytest.raises(HTTPException):
            await _post(env, _single_body())
    real_trust = env.routes._apply_trust_for_segments

    async def trust_crash(_states):
        raise RuntimeError("trust pool write failed")

    env.monkeypatch.setattr(env.routes, "_apply_trust_for_segments", trust_crash)
    with pytest.raises(Exception):
        await _post(env, _single_body())                          # Third attempt abandons the display name, but trust-pool persistence fails.
    env.monkeypatch.setattr(env.routes, "_apply_trust_for_segments", real_trust)
    env.monkeypatch.setattr(env.persona, "aupdate_subject_display_name", record)
    await _post(env, _single_body())
    # Retry abandoned display-name items during final refresh too: once persona becomes writable, fill the name before key finalization.
    assert _key_state(env, KEY_GROUP) == "done" and len(written) == 1


async def test_failing_display_name_refresh_does_not_block_the_recovery_retry(env):
    env.llm.responses = [SINGLE_FACTS]
    real_trust = env.routes._apply_trust_for_segments

    async def trust_crash(_states):
        raise RuntimeError("trust pool write failed")

    env.monkeypatch.setattr(env.routes, "_apply_trust_for_segments", trust_crash)
    with pytest.raises(Exception):
        await _post(env, _single_body())                          # Display name written, but trust-pool persistence fails.
    env.monkeypatch.setattr(env.routes, "_apply_trust_for_segments", real_trust)

    async def always_fail(*args, **kwargs):
        raise OSError("persona.json is read-only")

    env.monkeypatch.setattr(env.persona, "aupdate_subject_display_name", always_fail)
    await _post(env, _single_body(display_name="新名字"))
    # The final refresh to current values is best-effort: log write failures and finalize the key normally.
    assert _key_state(env, KEY_GROUP) == "done"


async def test_scalar_wire_keys_do_not_break_the_conservative_forgotten_merge(env):
    env.llm.responses = [SINGLE_FACTS]
    await _post(env, _single_body(key=None, display_name=None))
    await env.idem.update_key(NAME, KEY_GROUP, env.idem.transition(
        "pending", request={"shape": "single", "wire_keys": [GROUP_KEY], "content_hash": "h"},
        routed_keys="pq",                                          # List corrupted into a string.
    ))
    await env.idem.update_key(NAME, KEY_GROUP, lambda old: {**old, "forgotten_keys": [{"bad": 1}]})
    lock = env.idem.key_lock(NAME, KEY_GROUP)
    await lock.acquire()
    try:
        result = await _forget(env, GROUP)
    finally:
        lock.release()
    # Record list corrupted into a scalar: do not cause TypeError/500 during clearing or split strings into individual characters.
    record = json.loads(Path(env.idem.keys_path(NAME)).read_text(encoding="utf-8"))[KEY_GROUP]
    assert result["status"] == "forgotten" and record["forgotten_keys"] == [GROUP_KEY]


async def test_forget_of_one_segment_after_a_failed_generation_keeps_the_other_segment(env):
    # Do not extract the cleared segment again: retry has only the first segment remaining.
    env.llm.responses = [RuntimeError("LLM 502"), [BATCH_FACTS[0]]]
    with pytest.raises(RuntimeError):
        await _post(env, _segments_body())
    assert _key_state(env, KEY_SEGMENTS) == "pending"      # Generation failed: pending, no staging, key lock released.
    await _forget(env, PART)
    # Only one segment cleared: do not cancel the whole key, permanently losing the other's memories because duplicate responses prevent client resends.
    assert _key_state(env, KEY_SEGMENTS) == "pending"
    result = await _post(env, _segments_body())
    assert result.get("duplicate") is None and _key_state(env, KEY_SEGMENTS) == "done"
    assert _facts_of(env, GP) and _facts_of(env, PART) == []


async def test_orphan_staging_without_its_identity_is_cancelled_with_a_placeholder(env):
    env.llm.responses = [SINGLE_FACTS]
    original = _fail_on_item(env, failing_seq=0)
    with pytest.raises(HTTPException):
        await _post(env, _single_body())
    env.monkeypatch.setattr(env.routes, "_apply_keyed_item", original)
    path = _staging_file(env, KEY_GROUP)
    staging = json.loads(path.read_text(encoding="utf-8"))
    staging.pop("shape")                                       # Staging request-identity fields corrupt.
    path.write_text(json.dumps(staging, ensure_ascii=False), encoding="utf-8")
    keys = json.loads(Path(env.idem.keys_path(NAME)).read_text(encoding="utf-8"))
    keys.pop(KEY_GROUP)                                        # Orphan staging: no key record.
    Path(env.idem.keys_path(NAME)).write_text(json.dumps(keys), encoding="utf-8")
    await _forget(env, GROUP)
    with pytest.raises(HTTPException) as excinfo:
        await _post(env, _single_body())
    # Cancellation record uses an identity placeholder matching no request: retries get key-already-used 422 rather than permanent 503.
    assert excinfo.value.status_code == 422


@pytest.mark.parametrize("failure", ["transient", "corrupt"])
async def test_keys_file_read_failure_during_forget(env, failure):
    env.llm.responses = [SINGLE_FACTS]
    await _post(env, _single_body(key=None, display_name=None))
    real_read = env.idem._read_json_object
    keys_path = Path(env.idem.keys_path(NAME))

    def failing(path):
        if Path(path) == keys_path:
            if failure == "transient":
                raise env.idem.IdempotencyStateError("idempotency_keys.json unreadable: sharing violation")
            raise env.idem.IdempotencyCorruptError("idempotency_keys.json is not an object")
        return real_read(path)

    env.monkeypatch.setattr(env.idem, "_read_json_object", failing)
    if failure == "corrupt":
        # Corrupt content: keyed requests already fail closed; skip claiming and complete clearing normally.
        assert (await _forget(env, GROUP))["status"] == "forgotten"
    else:
        # Temporarily unreadable: next same-key retry may succeed, so do not silently skip claiming; clearing errors and retries.
        with pytest.raises(Exception):
            await _forget(env, GROUP)


async def test_cleanup_skips_a_staging_whose_key_lock_is_held(env):
    env.llm.responses = [SINGLE_FACTS]
    original = _fail_on_item(env, failing_seq=0)
    with pytest.raises(HTTPException):
        await _post(env, _single_body())
    env.monkeypatch.setattr(env.routes, "_apply_keyed_item", original)
    await env.idem.update_key(NAME, KEY_GROUP, env.idem.transition("done"))   # Residual staging not deleted at finalization.
    lock = env.idem.key_lock(NAME, KEY_GROUP)
    await lock.acquire()                                        # Same-key request holds its lock while invoking the LLM.
    try:
        report = await asyncio.wait_for(env.idem.cleanup_expired([NAME], ttl_s=0, now=time.time() + 10), 5)
    finally:
        lock.release()
    # Cleanup holds a character request lease: do not queue behind the key lock; leave this staging for next startup cleanup.
    assert report["staging_removed"] == 0 and _staging_file(env, KEY_GROUP).exists()


async def test_damaged_staging_identity_does_not_overwrite_the_records_own(env):
    env.llm.responses = [SINGLE_FACTS]
    original = _fail_on_item(env, failing_seq=0)
    with pytest.raises(HTTPException):
        await _post(env, _single_body())
    env.monkeypatch.setattr(env.routes, "_apply_keyed_item", original)
    path = _staging_file(env, KEY_GROUP)
    staging = json.loads(path.read_text(encoding="utf-8"))
    staging.pop("shape")                                       # Staging identity corrupt, key-record identity intact.
    path.write_text(json.dumps(staging, ensure_ascii=False), encoding="utf-8")
    await _forget(env, GROUP)
    result = await _post(env, _single_body())
    # Do not overwrite the correct record identity with a placeholder: same-key retries should get duplicate normally, not perpetual 422.
    assert result["duplicate"] is True and _key_state(env, KEY_GROUP) == "cancelled"


async def test_duplicate_reply_removes_a_terminal_keys_leftover_staging(env):
    env.llm.responses = [SINGLE_FACTS]
    original = _fail_on_item(env, failing_seq=0)
    with pytest.raises(HTTPException):
        await _post(env, _single_body())
    env.monkeypatch.setattr(env.routes, "_apply_keyed_item", original)
    await env.idem.update_key(NAME, KEY_GROUP, env.idem.transition("done"))   # Residual staging not deleted at finalization.
    result = await _post(env, _single_body())
    # Delete it while returning duplicate under the key lock, without waiting for startup cleanup that skips occupied key locks.
    assert result["duplicate"] is True and not _staging_file(env, KEY_GROUP).exists()


# ── fan-out targets fenced at their own epochs (target_epochs) ─────────────

def _fan_out_to(env, *linked_subjects: dict) -> None:
    """Make every forget's identity expansion also erase ``linked_subjects``."""
    original = env.routes._forget_fanout_targets
    linked = [MemorySubject.create(s["subject_kind"], s["subject_id"]) for s in linked_subjects]

    def fanout(subject):
        base = list(original(subject))
        keys = {target.key for target in base}
        extra = [target for target in linked if target.key not in keys]
        return sorted(base + extra, key=lambda item: (item.key, item.scope))

    env.monkeypatch.setattr(env.routes, "_forget_fanout_targets", fanout)


def _tombstones(env) -> dict:
    path = Path(env.idem.tombstones_path(NAME))
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


async def _stage_then_crash(env, body: dict) -> None:
    """Leave a pending key with its staging on disk, nothing applied."""
    real_apply = env.routes._apply_keyed_staging

    async def crash(*_args, **_kwargs):
        raise RuntimeError("process killed before applying")

    env.monkeypatch.setattr(env.routes, "_apply_keyed_staging", crash)
    with pytest.raises(HTTPException):
        await _post(env, body)
    env.monkeypatch.setattr(env.routes, "_apply_keyed_staging", real_apply)


async def test_opted_in_forget_missing_a_fanout_target_is_409_before_anything(env):
    env.llm.responses = [SINGLE_FACTS]
    await _post(env, _single_body(key=None, display_name=None))
    _fan_out_to(env, PART)
    with pytest.raises(HTTPException) as excinfo:
        await _forget(env, GROUP, forget_epoch=7, target_epochs={})
    # target_epochs supplied but missing fanout targets: list missing keys, write no tombstones, and erase no memories.
    assert excinfo.value.status_code == 409
    assert excinfo.value.detail == {"error": "missing_target_epochs", "missing_target_epochs": [PART_KEY]}
    assert _tombstones(env) == {} and _facts_of(env, GROUP)


async def test_forget_without_target_epochs_keeps_the_old_behaviour(env):
    _fan_out_to(env, PART)
    result = await _forget(env, GROUP, forget_epoch=7)
    # Without target_epochs, do not return 409; erase fanout targets normally without tombstones, matching previous behavior.
    assert result["status"] == "forgotten"
    assert set(_tombstones(env)) == {GROUP_KEY}


async def test_fanout_target_is_fenced_at_its_own_epoch_against_a_delayed_write(env):
    _fan_out_to(env, PART)
    full = await _forget(env, GROUP, forget_epoch=7, target_epochs={PART_KEY: 2})
    # Full erasure: actual erasure covers the entire fanout.
    assert full["erased_subjects"] == full["forgotten_subjects"]
    tombstones = _tombstones(env)
    # Each key records its own generation: fanout target is 2, not request subject's 7; mark all complete after erasure.
    assert tombstones[GROUP_KEY]["forget_epoch"] == 7 and tombstones[GROUP_KEY]["erased_epoch"] == 7
    assert tombstones[PART_KEY]["forget_epoch"] == 2 and tombstones[PART_KEY]["erased_epoch"] == 2
    # Previously unseen PART digest (generation 1) began before clearing but arrives afterward: no key record or staging,
    # so cancellation scans miss it and generation is already post-clear; only PART's own tombstone can block it.
    env.llm.responses = [SINGLE_FACTS]
    late = "visit-digest:LateLateLateLateLateLa:0:group:0"
    await _post(env, _single_body(key=late, subject=PART, display_name=None, subject_epochs={PART_KEY: 1}))
    assert _facts_of(env, PART) == []
    # A new post-clear pass (client advanced PART to 2) writes normally, unblocked by request subject's 7.
    fresh = "visit-digest:FreshFreshFreshFreshFr:0:group:0"
    await _post(env, _single_body(key=fresh, subject=PART, display_name=None, subject_epochs={PART_KEY: 2}))
    assert len(_facts_of(env, PART)) == len(SINGLE_FACTS)


async def test_fanout_fence_is_durable_before_the_erase_finishes(env):
    _fan_out_to(env, PART)

    async def marker_lost(*_args, **_kwargs):
        raise OSError("disk full")

    env.monkeypatch.setattr(env.idem, "mark_tombstone_erased", marker_lost)
    with pytest.raises(HTTPException):
        await _forget(env, GROUP, forget_epoch=7, target_epochs={PART_KEY: 2})
    # Erasure finished but completion marker not persisted (caller has not retried): fanout tombstones must already be durable before erasure,
    # blocking pre-clear writes arriving meanwhile, rather than being added alongside completion markers afterward.
    assert _tombstones(env)[PART_KEY]["forget_epoch"] == 2
    env.llm.responses = [SINGLE_FACTS]
    late = "visit-digest:LateLateLateLateLateLa:0:group:0"
    await _post(env, _single_body(key=late, subject=PART, display_name=None, subject_epochs={PART_KEY: 1}))
    assert _facts_of(env, PART) == []


async def test_target_epochs_outside_the_fanout_are_not_recorded(env):
    await _forget(env, GROUP, forget_epoch=3, target_epochs={PART_KEY: 9})
    # Unlinked between requests: extra supplied keys outside this erasure surface receive no tombstones.
    assert set(_tombstones(env)) == {GROUP_KEY}


@pytest.mark.parametrize("staged_epoch, kept", [(2, True), (1, False)])
async def test_fanout_target_staging_is_judged_by_its_own_fence(env, staged_epoch, kept):
    env.llm.responses = [SINGLE_FACTS]
    await _stage_then_crash(env, _single_body(subject=PART, display_name=None, subject_epochs={PART_KEY: staged_epoch}))
    _fan_out_to(env, PART)
    await _forget(env, GROUP, forget_epoch=5, target_epochs={PART_KEY: 2})
    # Wire is a fanout target with generation meeting its own fence: legitimate post-clear write, do not cancel; cancel pre-fence writes normally.
    assert _staging_file(env, KEY_GROUP).exists() is kept
    assert _key_state(env, KEY_GROUP) == ("pending" if kept else "cancelled")


async def test_mixed_batch_drops_only_the_stale_fanout_segment(env):
    env.llm.responses = [BATCH_FACTS]
    await _stage_then_crash(env, _segments_body(subject_epochs={GP_KEY: 0, PART_KEY: 2}))
    _fan_out_to(env, GP, PART)
    await _forget(env, GROUP, forget_epoch=5, target_epochs={GP_KEY: 1, PART_KEY: 2})
    # GP segment predates its fence (0 < 1) and is discarded; PART is new enough (2 >= 2) and retained: keep pending without canceling the whole key.
    assert _key_state(env, KEY_SEGMENTS) == "pending"
    again = await _post(env, _segments_body(subject_epochs={GP_KEY: 0, PART_KEY: 2}))
    assert _facts_of(env, GP) == [] and len(_facts_of(env, PART)) == 2
    assert again.get("duplicate") is None and env.llm.calls == 1


@pytest.mark.parametrize("staged_epoch, kept", [(2, True), (1, False)])
async def test_duplicate_forget_cancels_late_fanout_staging_by_its_own_fence(env, staged_epoch, kept):
    _fan_out_to(env, PART)
    await _forget(env, GROUP, forget_epoch=5, target_epochs={PART_KEY: 2})   # Original erasure completed.
    env.llm.responses = [SINGLE_FACTS]
    await _stage_then_crash(env, _single_body(subject=PART, display_name=None, subject_epochs={PART_KEY: staged_epoch}))
    result = await _forget(env, GROUP, forget_epoch=5, target_epochs={PART_KEY: 2})
    # Replay uses the duplicate shortcut: fanout generations are comparable, so cancel residual pre-fence staging and retain newer staging.
    assert result.get("duplicate") is True
    assert _staging_file(env, KEY_GROUP).exists() is kept
    assert _key_state(env, KEY_GROUP) == ("pending" if kept else "cancelled")


async def test_fanout_markers_land_before_the_request_subjects(env):
    _fan_out_to(env, PART)
    real_mark = env.idem.mark_tombstone_erased

    async def broken_for_part(name, subject_key, *args, **kwargs):
        if subject_key == PART_KEY:
            raise OSError("disk full")
        return await real_mark(name, subject_key, *args, **kwargs)

    env.monkeypatch.setattr(env.idem, "mark_tombstone_erased", broken_for_part)
    with pytest.raises(HTTPException) as excinfo:
        await _forget(env, GROUP, forget_epoch=5, target_epochs={PART_KEY: 2})
    assert excinfo.value.status_code == 500
    # Request subject's completion marker must not persist before fanout targets' markers, or duplicate shortcuts on retry prevent completing targets forever.
    assert env.idem.erased_epoch(_tombstones(env), GROUP_KEY) is None
    env.monkeypatch.setattr(env.idem, "mark_tombstone_erased", real_mark)
    result = await _forget(env, GROUP, forget_epoch=5, target_epochs={PART_KEY: 2})
    assert result.get("duplicate") is None
    assert env.idem.erased_epoch(_tombstones(env), PART_KEY) == 2


async def test_replay_naming_a_newly_linked_target_erases_only_that_target(env):
    await _forget(env, GROUP, forget_epoch=5)                   # Original erasure completed before any account linkage.
    env.llm.responses = [SINGLE_FACTS]
    await _post(env, _single_body(key=None, display_name=None))  # Legitimate GROUP memories written after clearing.
    env.llm.responses = [SINGLE_FACTS]
    await _post(env, _single_body(key=None, subject=PART, display_name=None))
    assert _facts_of(env, GROUP) and _facts_of(env, PART)
    _fan_out_to(env, PART)                                      # PART linked afterward.
    result = await _forget(env, GROUP, forget_epoch=5, target_epochs={PART_KEY: 1})
    # Replay includes the new target's generation: erase only the target not yet cleared through that generation, without duplicate or re-erasing GROUP.
    assert result.get("duplicate") is None
    assert _facts_of(env, PART) == [] and _facts_of(env, GROUP)
    assert env.idem.erased_epoch(_tombstones(env), PART_KEY) == 1
    # Receipt identifies supplemental erasure: only PART erased this time, while forgotten_subjects lists the entire fanout.
    assert [s["subject_id"] for s in result["erased_subjects"]] == [PART["subject_id"]]
    assert {s["subject_id"] for s in result["forgotten_subjects"]} == {GROUP["subject_id"], PART["subject_id"]}
    again = await _forget(env, GROUP, forget_epoch=5, target_epochs={PART_KEY: 1})
    assert again.get("duplicate") is True and again["erased_subjects"] == []


async def test_partial_replay_does_not_mark_the_request_subject_through_a_higher_fence(env):
    await _forget(env, GROUP, forget_epoch=5)
    # Fence 8, completion marker 5: existing write paths rewrite completion markers when raising fences; construct this state directly
    # to protect the invariant that supplemental fanout erasure does not touch the request subject's completion marker.
    path = Path(env.idem.tombstones_path(NAME))
    data = _tombstones(env)
    data[GROUP_KEY] = {"forgotten_at": time.time(), "forget_epoch": 8, "erased_epoch": 5}
    path.write_text(json.dumps(data), encoding="utf-8")
    env.llm.responses = [SINGLE_FACTS]
    await _post(env, _single_body(key=None, display_name=None))  # GROUP memories generation 8 should have erased.
    _fan_out_to(env, PART)
    await _forget(env, GROUP, forget_epoch=5, target_epochs={PART_KEY: 1})
    # Only PART was supplementally erased: GROUP was not, so its completion marker cannot rise to the existing fence 8.
    assert env.idem.erased_epoch(_tombstones(env), GROUP_KEY) == 5
    retry = await _forget(env, GROUP, forget_epoch=8, target_epochs={PART_KEY: 1})
    # Generation-8 retry erases normally without the duplicate shortcut.
    assert retry.get("duplicate") is None and _facts_of(env, GROUP) == []


async def test_unfenced_wire_routed_into_the_forgotten_subject_is_marked_without_staging(env):
    idem = env.idem
    await idem.update_key(NAME, KEY_SEGMENTS, idem.transition(
        "pending",
        request={"shape": "segments", "wire_keys": [GROUP_KEY, PART_KEY], "content_hash": "h"},
        routed_keys=[GROUP_KEY], routed_positions=[GROUP_KEY, GROUP_KEY],
        epochs={GROUP_KEY: 5},
    ))
    # Record only, no staging: GROUP wire is new enough, but PART, already unlinked and outside fanout, was positionally routed into GROUP.
    await env.routes._cancel_staged_writes_for_subjects(
        NAME, {GROUP_KEY}, request_subject_key=GROUP_KEY, forget_epoch=5,
    )
    record = json.loads(Path(idem.keys_path(NAME)).read_text(encoding="utf-8"))[KEY_SEGMENTS]
    # GROUP's freshness cannot justify accepting the whole record: record PART routed into the cleared subject and discard that segment on retry.
    assert record["state"] == "pending"
    assert record.get("forgotten_keys") == [PART_KEY]


async def test_tombstones_are_raised_per_key_at_their_own_epochs(env):
    idem = env.idem
    await idem.record_tombstone_epochs(NAME, {GROUP_KEY: 7, PART_KEY: 2})
    await idem.record_tombstone_epochs(NAME, {GROUP_KEY: 3, PART_KEY: 4})
    rows = _tombstones(env)
    # Keys are independently monotonic: GROUP is not lowered to 3, PART rises to 4, with no interference.
    assert rows[GROUP_KEY]["forget_epoch"] == 7 and rows[PART_KEY]["forget_epoch"] == 4
    path = Path(idem.tombstones_path(NAME))
    damaged = {**rows, GP_KEY: {"forget_epoch": "9"}}
    path.write_text(json.dumps(damaged), encoding="utf-8")
    await idem.record_tombstone_epochs(NAME, {GP_KEY: 1, PART_KEY: 5})
    rows = _tombstones(env)
    # Preserve corrupt rows unchanged (readers fail closed), while other keys in the same write rise normally.
    assert rows[GP_KEY] == {"forget_epoch": "9"} and rows[PART_KEY]["forget_epoch"] == 5


def test_damaged_routed_positions_fall_back_to_the_routed_keys(env):
    record = {
        "request": {"wire_keys": [PART_KEY]},
        "routed_positions": [123],                              # Length matches, content corrupt.
        "routed_keys": [GROUP_KEY],
        "epochs": {PART_KEY: 2},
    }
    marks = env.routes._stale_record_marks(record, {GROUP_KEY}, {GROUP_KEY: 5, PART_KEY: 2})
    # Corrupt positional routing cannot count as alignment: conservatively mark cleared destinations using routed_keys instead.
    assert GROUP_KEY in marks


async def test_retry_after_a_partly_recorded_completion_skips_marked_targets(env):
    _fan_out_to(env, PART)
    real_mark = env.idem.mark_tombstone_erased

    async def broken_for_group(name, subject_key, *args, **kwargs):
        if subject_key == GROUP_KEY:
            raise OSError("disk full")
        return await real_mark(name, subject_key, *args, **kwargs)

    env.monkeypatch.setattr(env.idem, "mark_tombstone_erased", broken_for_group)
    with pytest.raises(HTTPException):
        await _forget(env, GROUP, forget_epoch=5, target_epochs={PART_KEY: 1})
    env.monkeypatch.setattr(env.idem, "mark_tombstone_erased", real_mark)
    assert env.idem.erased_epoch(_tombstones(env), PART_KEY) == 1
    env.llm.responses = [SINGLE_FACTS]
    await _post(env, _single_body(key=None, subject=PART, display_name=None))   # Legitimate writes after PART erasure.
    result = await _forget(env, GROUP, forget_epoch=5, target_epochs={PART_KEY: 1})
    # Retry completes the request subject; PART's completion marker is already durable, so never re-erase its subsequent writes.
    assert result.get("duplicate") is None
    assert _facts_of(env, PART) and env.idem.erased_epoch(_tombstones(env), GROUP_KEY) == 5


async def test_partial_retry_still_cancels_a_stale_journal_of_a_completed_target(env):
    _fan_out_to(env, PART)
    real_mark = env.idem.mark_tombstone_erased

    async def broken_for_group(name, subject_key, *args, **kwargs):
        if subject_key == GROUP_KEY:
            raise OSError("disk full")
        return await real_mark(name, subject_key, *args, **kwargs)

    env.monkeypatch.setattr(env.idem, "mark_tombstone_erased", broken_for_group)
    with pytest.raises(HTTPException):
        await _forget(env, GROUP, forget_epoch=5, target_epochs={PART_KEY: 2})
    env.monkeypatch.setattr(env.idem, "mark_tombstone_erased", real_mark)
    # After PART's completion marker persisted, a pre-clear PART request (generation 1) crashed after staging.
    env.llm.responses = [SINGLE_FACTS]
    await _stage_then_crash(env, _single_body(subject=PART, display_name=None, subject_epochs={PART_KEY: 1}))
    result = await _forget(env, GROUP, forget_epoch=5, target_epochs={PART_KEY: 2})
    # Only GROUP is supplementally erased now, but cancellation scanning still covers generation-bearing PART and cancels old plaintext staging.
    assert result.get("duplicate") is None
    assert [s["subject_id"] for s in result["erased_subjects"]] == [GROUP["subject_id"]]
    assert _key_state(env, KEY_GROUP) == "cancelled" and not _staging_file(env, KEY_GROUP).exists()


async def test_partial_replay_does_not_mark_a_target_through_its_own_higher_fence(env):
    group = MemorySubject.create(GROUP["subject_kind"], GROUP["subject_id"])
    part = MemorySubject.create(PART["subject_kind"], PART["subject_id"])
    # GROUP and PART are mutual fanout targets (same participant).
    env.monkeypatch.setattr(env.routes, "_forget_fanout_targets", lambda subject: sorted(
        [group, part], key=lambda t: (t.key, t.scope),
    ))
    await _forget(env, GROUP, forget_epoch=5, target_epochs={PART_KEY: 1})         # 1. GROUP=5、PART=1
    env.llm.responses = [SINGLE_FACTS]
    await _post(env, _single_body(key=None, display_name=None))                    # 2. Legitimate writes to GROUP afterward.
    real_begin = env.fs.abegin_subject_forget

    async def fail_once(name, subject):
        raise RuntimeError("erase failed before it started")

    env.monkeypatch.setattr(env.fs, "abegin_subject_forget", fail_once)
    with pytest.raises(HTTPException):
        await _forget(env, PART, forget_epoch=10)                                 # 3. PART fence 10, failure before erasure.
    env.monkeypatch.setattr(env.fs, "abegin_subject_forget", real_begin)
    await _forget(env, GROUP, forget_epoch=5, target_epochs={PART_KEY: 1})         # 4. Replay: supplementally erase only PART.
    # PART records completion using only this pass's generation, not fence 10 from the attempt that failed before erasure.
    assert env.idem.erased_epoch(_tombstones(env), PART_KEY) == 1
    retry = await _forget(env, PART, forget_epoch=10)                              # 5. Retry that PART clear.
    assert retry.get("duplicate") is None and _facts_of(env, GROUP) == []


async def test_replay_after_unlink_still_cancels_a_stale_journal_of_the_former_target(env):
    original = env.routes._forget_fanout_targets
    _fan_out_to(env, PART)
    await _forget(env, GROUP, forget_epoch=5, target_epochs={PART_KEY: 2})   # Original erasure: PART fence is 2.
    env.monkeypatch.setattr(env.routes, "_forget_fanout_targets", original)  # PART unlinked afterward.
    env.llm.responses = [SINGLE_FACTS]
    await _stage_then_crash(env, _single_body(subject=PART, display_name=None, subject_epochs={PART_KEY: 1}))
    result = await _forget(env, GROUP, forget_epoch=5, target_epochs={PART_KEY: 2})
    # PART is no longer in fanout, so do not erase it again; its previous fence still cancels residual pre-clear plaintext staging (generation 1).
    assert result.get("duplicate") is True
    assert _key_state(env, KEY_GROUP) == "cancelled" and not _staging_file(env, KEY_GROUP).exists()


async def test_never_fenced_key_outside_the_fanout_is_not_cancelled(env):
    env.llm.responses = [SINGLE_FACTS]
    await _stage_then_crash(env, _single_body(subject=PART, display_name=None, subject_epochs={PART_KEY: 1}))
    await _forget(env, GROUP, forget_epoch=5, target_epochs={PART_KEY: 2})
    # PART was never fenced by clearing and is outside current fanout: its writes are legitimate; do not cancel based on caller-supplied generation.
    assert _key_state(env, KEY_GROUP) == "pending" and _staging_file(env, KEY_GROUP).exists()
    assert PART_KEY not in _tombstones(env)


async def test_overlapping_alias_forgets_serialize_until_markers_land(env):
    _fan_out_to(env, PART)                                      # GROUP and GP fanouts both include PART.
    entered, gate = asyncio.Event(), asyncio.Event()
    real_cancel = env.routes._cancel_staged_writes_for_subjects

    async def slow_post_erase_cancel(*args, **kwargs):
        if not kwargs.get("best_effort") and not entered.is_set():
            entered.set()
            await gate.wait()
        return await real_cancel(*args, **kwargs)

    env.monkeypatch.setattr(env.routes, "_cancel_staged_writes_for_subjects", slow_post_erase_cancel)
    begun: list[str] = []
    real_begin = env.fs.abegin_subject_forget

    async def spy_begin(name, subject):
        begun.append(subject.key)
        return await real_begin(name, subject)

    env.monkeypatch.setattr(env.fs, "abegin_subject_forget", spy_begin)
    first = asyncio.create_task(_forget(env, GROUP, forget_epoch=5, target_epochs={PART_KEY: 2}))
    await asyncio.wait_for(entered.wait(), timeout=5)           # First erasure completed and released its lock, but has not written completion markers.
    second = asyncio.create_task(_forget(env, GP, forget_epoch=3, target_epochs={PART_KEY: 2}))
    await asyncio.sleep(0.05)
    # Second clear names another alias but shares PART: it must wait until the first writes completion markers.
    assert begun.count(PART_KEY) == 1 and GP_KEY not in begun
    gate.set()
    await asyncio.wait_for(first, timeout=5)
    result = await asyncio.wait_for(second, timeout=5)
    # It sees PART already erased through 2, so erases only GP without re-erasing PART and losing legitimate intervening memories.
    assert begun.count(PART_KEY) == 1
    assert [s["subject_id"] for s in result["erased_subjects"]] == [GP["subject_id"]]


async def test_fanout_growing_while_fences_are_taken_retakes_them(env):
    linked = MemorySubject.create(PART["subject_kind"], PART["subject_id"])
    original = env.routes._forget_fanout_targets
    calls = {"n": 0}

    def growing_fanout(subject):
        calls["n"] += 1
        base = list(original(subject))
        # PART was unlinked when the first outer fence was acquired, then became linked.
        return base if calls["n"] == 1 else sorted(base + [linked], key=lambda t: (t.key, t.scope))

    env.monkeypatch.setattr(env.routes, "_forget_fanout_targets", growing_fanout)
    fenced: list[str] = []
    real_fence = env.idem.forget_fence

    def spy_fence(name, subject_key):
        fenced.append(subject_key)
        return real_fence(name, subject_key)

    env.monkeypatch.setattr(env.idem, "forget_fence", spy_fence)
    result = await _forget(env, GROUP, forget_epoch=5, target_epochs={PART_KEY: 2})
    # Fanout gains PART during fence acquisition: release and reacquire, erasing only after acquiring PART's fence too.
    assert result["status"] == "forgotten" and PART_KEY in fenced
    assert env.idem.erased_epoch(_tombstones(env), PART_KEY) == 2


async def test_fanout_fence_raised_before_the_erase_locks_is_recorded_as_erased(env):
    _fan_out_to(env, PART)
    real_plan = env.routes._targets_still_to_erase
    calls = {"n": 0}

    async def plan(*args):
        calls["n"] += 1
        if calls["n"] == 2:
            # After unlocked fence reads but before erasure, another clear raises PART's fence to 6.
            await env.idem.record_tombstones(NAME, {PART_KEY}, 6)
        return await real_plan(*args)

    env.monkeypatch.setattr(env.routes, "_targets_still_to_erase", plan)
    await _forget(env, GROUP, forget_epoch=5, target_epochs={PART_KEY: 2})
    # This erasure follows fence 6: mark completion through the last fence present before erasure, so generation-6 retry does not re-erase.
    assert calls["n"] == 2
    assert env.idem.erased_epoch(_tombstones(env), PART_KEY) == 6


async def test_recheck_under_the_locks_narrows_to_the_unerased_targets(env):
    await _forget(env, GROUP, forget_epoch=5)
    env.llm.responses = [SINGLE_FACTS]
    await _post(env, _single_body(key=None, display_name=None))
    env.llm.responses = [SINGLE_FACTS]
    await _post(env, _single_body(key=None, subject=PART, display_name=None))
    _fan_out_to(env, PART)
    real_check = env.routes._forget_epoch_already_erased
    calls = {"n": 0}

    async def check(*args):
        calls["n"] += 1
        if calls["n"] == 1:
            return False        # Unlocked check precedes request-subject erasure completion.
        return await real_check(*args)

    env.monkeypatch.setattr(env.routes, "_forget_epoch_already_erased", check)
    result = await _forget(env, GROUP, forget_epoch=5, target_epochs={PART_KEY: 1})
    # Locked check finds request subject already erased: erase only PART's outstanding generation, leaving GROUP's subsequent legitimate writes untouched.
    assert result.get("duplicate") is None and calls["n"] >= 2
    assert _facts_of(env, PART) == [] and _facts_of(env, GROUP)


async def test_failed_generation_keeps_the_fresh_wire_of_a_mixed_batch(env):
    env.llm.responses = [RuntimeError("model down")]
    with pytest.raises(RuntimeError):
        await _post(env, _segments_body(subject_epochs={GP_KEY: 0, PART_KEY: 2}))
    assert _key_state(env, KEY_SEGMENTS) == "pending" and not _staging_file(env, KEY_SEGMENTS).exists()
    _fan_out_to(env, GP, PART)
    await _forget(env, GROUP, forget_epoch=5, target_epochs={GP_KEY: 1, PART_KEY: 2})
    # Record only, no staging: GP predates its fence, PART is new enough. Record only GP, without canceling the entire key.
    assert _key_state(env, KEY_SEGMENTS) == "pending"
    env.llm.responses = [PART_ONLY_BATCH_FACTS]
    again = await _post(env, _segments_body(subject_epochs={GP_KEY: 0, PART_KEY: 2}))
    assert again.get("duplicate") is None
    assert _facts_of(env, GP) == [] and len(_facts_of(env, PART)) == 2


@pytest.mark.parametrize("body, detail", [
    ({"subject": GROUP, "target_epochs": {PART_KEY: 1}}, "target_epochs requires forget_epoch"),
    ({"subject": GROUP, "forget_epoch": 3, "target_epochs": {GROUP_KEY: 4}}, "target_epochs disagrees with forget_epoch"),
    ({"subject": GROUP, "forget_epoch": 3, "target_epochs": {f"participant:neko_visit:p{i}": 1 for i in range(65)}},
     "invalid target_epochs keys"),
    ({"subject": GROUP, "forget_epoch": 3, "target_epochs": {"": 1}}, "invalid target_epochs keys"),
], ids=["no-forget-epoch", "disagrees", "too-many", "empty-key"])
async def test_unusable_target_epochs_are_422(env, body, detail):
    req = env.routes.ScopedForgetRequest.model_validate(body)
    with pytest.raises(HTTPException) as excinfo:
        await env.routes.forget_scoped_subject(NAME, req)
    assert excinfo.value.status_code == 422 and excinfo.value.detail == detail
    assert _tombstones(env) == {}


def test_target_epochs_reject_negative_values(env):
    with pytest.raises(Exception):
        env.routes.ScopedForgetRequest.model_validate(
            {"subject": GROUP, "forget_epoch": 1, "target_epochs": {PART_KEY: -1}},
        )


# ── #3337 additional verification tests: fixes predating #3292 merge that original tests did not protect ──


async def test_orphan_scrub_reads_the_key_records_under_the_character_lock(env):
    idem = env.idem
    path = Path(idem.staging_path(NAME, KEY_GROUP))
    path.parent.mkdir(parents=True, exist_ok=True)
    await idem.update_key(NAME, "other-key", idem.transition("done"))          # Key file exists but is unclaimed.
    path.write_text('{"key": "x", "facts": ["猫薄荷"', encoding="utf-8")        # Unreadable.
    keys_file = os.path.normcase(os.path.abspath(idem.keys_path(NAME)))
    lock = idem.idempotency_lock(NAME)
    held_at_read = []
    real_read = idem._read_json_object

    def spy(target):
        if os.path.normcase(os.path.abspath(target)) == keys_file:
            held_at_read.append(lock.locked())
        return real_read(target)

    env.monkeypatch.setattr(idem, "_read_json_object", spy)
    assert await idem.drop_unreadable_orphan_staging(NAME, str(path)) is True
    # Read ownership key records under the character lock: update_key may claim this path as pending between an unlocked snapshot and rewriting.
    assert held_at_read and all(held_at_read)
    assert "猫薄荷" not in path.read_text(encoding="utf-8")


async def test_legacy_integer_fact_ids_are_journaled_as_strings(env):
    env.llm.responses = [SINGLE_FACTS]
    real_persist = env.fs._apersist_new_facts

    async def legacy_ids(lanlan_name, extracted, **kwargs):
        created = await real_persist(lanlan_name, extracted, **kwargs)
        # Both reconcile hits and newly created results include legacy integer-ID facts (still supported by FactStore).
        kwargs["reconciled_facts"].append({**created[0], "id": 12345})
        return [{**created[0], "id": 67890}, *created[1:]]

    env.monkeypatch.setattr(env.fs, "_apersist_new_facts", legacy_ids)
    original = _fail_on_item(env, failing_seq=1)
    with pytest.raises(HTTPException):
        await _post(env, _single_body())
    env.monkeypatch.setattr(env.routes, "_apply_keyed_item", original)
    staging = json.loads(_staging_file(env, KEY_GROUP).read_text(encoding="utf-8"))
    entry = next(e for e in staging["applied"] if e.get("facts_applied"))
    # Record all as strings, matching _keyed_fact_identity.
    assert "12345" in entry["reconciled"] and "67890" in entry["fact_ids"]
    assert all(isinstance(v, str) for v in entry["reconciled"] + entry["fact_ids"])


async def test_keyed_fact_reads_ride_out_a_concurrent_replace(env):
    import builtins

    fs = env.fs
    active, archive = Path(fs._facts_path(NAME)), Path(fs._facts_archive_path(NAME))
    active.parent.mkdir(parents=True, exist_ok=True)
    active.write_text(json.dumps([{"id": "f1", "text": "家里阳台种着猫薄荷"}]), encoding="utf-8")
    archive.write_text(json.dumps([{"id": "f0", "effect_key": "ek-0"}]), encoding="utf-8")
    watched = {os.path.normcase(os.path.abspath(p)): 0 for p in (active, archive)}
    real_open = builtins.open

    def busy_twice(file, *args, **kwargs):
        if isinstance(file, (str, os.PathLike)):
            target = os.path.normcase(os.path.abspath(os.fspath(file)))
            if target in watched and watched[target] < 2:
                watched[target] += 1
                # Archival/deduplication writer is performing os.replace: a Windows sharing conflict.
                exc = PermissionError(13, "Access is denied")
                exc.winerror = 32
                raise exc
        return real_open(file, *args, **kwargs)

    env.monkeypatch.setattr(builtins, "open", busy_twice)
    # Keyed fact application reads these files in a worker thread: back off and retry replacement-window sharing conflicts rather than declaring unreadable.
    assert await asyncio.to_thread(fs._assert_active_facts_readable, NAME) == 1
    assert await asyncio.to_thread(fs._read_archived_effect_keys, NAME) == {"ek-0"}
    assert set(watched.values()) == {2}


async def test_forget_after_the_recheck_fences_an_activity_only_segment_from_trust(env):
    from memory.scopes import coerce_subject

    env.llm.responses = [[]]                                     # No facts extracted: segment contains only activity.
    real_apply = env.routes._apply_keyed_staging

    async def forget_before_apply(*args, **kwargs):
        # Clearing without a generation advances generation after the post-persistence recheck but before the trust step.
        env.fs._bump_subject_forget_generation(NAME, coerce_subject(GROUP))
        return await real_apply(*args, **kwargs)

    captured = []
    real_trust = env.routes._apply_trust_for_segments

    async def capture(states):
        captured.append(list(states))
        return await real_trust(states)

    env.monkeypatch.setattr(env.routes, "_apply_keyed_staging", forget_before_apply)
    env.monkeypatch.setattr(env.routes, "_apply_trust_for_segments", capture)
    body = _single_body(
        display_name=None,
        speaker_label="Mika", speaker_id="neko_visit:5f2c1b7e", speaker_tier="none",
        speaker_activity_events=[{"id": "evt-00003", "count": 1}],
    )
    await _post(env, body)
    # Segments without fact items to compare generation must also respect clearing: exclude activity from the trust pool.
    (states,) = captured
    assert env.routes._trust_mutation_for(states[0]) is None


async def test_activity_only_segment_without_a_forget_still_moves_the_trust_pool(env):
    env.llm.responses = [[]]
    captured = []
    real_trust = env.routes._apply_trust_for_segments

    async def capture(states):
        captured.append(list(states))
        return await real_trust(states)

    env.monkeypatch.setattr(env.routes, "_apply_trust_for_segments", capture)
    body = _single_body(
        display_name=None,
        speaker_label="Mika", speaker_id="neko_visit:5f2c1b7e", speaker_tier="none",
        speaker_activity_events=[{"id": "evt-00004", "count": 1}],
    )
    await _post(env, body)
    # Control: without intervening clearing, this segment's activity enters the pool normally.
    (states,) = captured
    assert env.routes._trust_mutation_for(states[0]) is not None


async def test_trust_step_fence_mark_is_on_disk_for_the_same_key_retry(env):
    from memory.scopes import coerce_subject

    env.llm.responses = [[]]
    real_apply = env.routes._apply_keyed_staging
    real_trust = env.routes._apply_trust_for_segments

    async def forget_before_apply(*args, **kwargs):
        env.fs._bump_subject_forget_generation(NAME, coerce_subject(GROUP))
        return await real_apply(*args, **kwargs)

    async def trust_crash(_states):
        raise RuntimeError("killed at the trust step")

    env.monkeypatch.setattr(env.routes, "_apply_keyed_staging", forget_before_apply)
    env.monkeypatch.setattr(env.routes, "_apply_trust_for_segments", trust_crash)
    body = _single_body(
        display_name=None,
        speaker_label="Mika", speaker_id="neko_visit:5f2c1b7e", speaker_tier="none",
        speaker_activity_events=[{"id": "evt-00005", "count": 1}],
    )
    with pytest.raises(Exception):
        await _post(env, body)
    env.monkeypatch.setattr(env.routes, "_apply_keyed_staging", real_apply)
    captured = []

    async def capture(states):
        captured.append(list(states))
        return await real_trust(states)

    env.monkeypatch.setattr(env.routes, "_apply_trust_for_segments", capture)
    await _post(env, body)
    # Recovery retry cannot read pre-generation generation; only durable segment-level markers identify this segment as clearing-blocked.
    (states,) = captured
    assert env.routes._trust_mutation_for(states[0]) is None


async def test_forget_cannot_slip_between_the_trust_recheck_and_the_trust_write(env):
    from memory.scopes import coerce_subject

    env.llm.responses = [[]]
    subject = coerce_subject(GROUP)
    captured_generation = env.fs._subject_forget_generation(NAME, subject)
    real_fenced = env.routes._fenced_segments
    real_trust = env.routes._apply_trust_for_segments
    forgets = []

    async def forget():
        # Advance generation through scoped_forget's same entry point, holding the fact store's persistence lock.
        await env.fs.abegin_subject_forget(NAME, subject)
        await env.fs.aend_subject_forget(NAME, subject)

    async def forget_after_the_recheck(lanlan_name, staging):
        forgets.append(asyncio.create_task(forget()))
        for _ in range(5):
            await asyncio.sleep(0)
        return await real_fenced(lanlan_name, staging)

    seen = []

    async def capture(states):
        moved = env.routes._trust_mutation_for(states[0]) is not None
        seen.append((moved, env.fs._subject_forget_generation(NAME, subject) != captured_generation))
        return await real_trust(states)

    env.monkeypatch.setattr(env.routes, "_fenced_segments", forget_after_the_recheck)
    env.monkeypatch.setattr(env.routes, "_apply_trust_for_segments", capture)
    body = _single_body(
        display_name=None,
        speaker_label="Mika", speaker_id="neko_visit:5f2c1b7e", speaker_tier="none",
        speaker_activity_events=[{"id": "evt-00006", "count": 1}],
    )
    await _post(env, body)
    await asyncio.gather(*forgets)
    # Clearing cannot intervene between recheck and trust write: activity entering the pool proves generation still matched its pre-generation value when written.
    ((moved, generation_moved),) = seen
    assert not (moved and generation_moved)
    assert env.fs._subject_forget_generation(NAME, subject) != captured_generation    # Clearing did occur.


async def test_cancelled_request_keeps_the_guard_until_the_trust_worker_finishes(env):
    import threading

    env.llm.responses = [[]]
    entered, release = threading.Event(), threading.Event()
    real_write = trust_store._with_pool_write

    def slow_write(mutator):
        entered.set()
        release.wait(5)
        return real_write(mutator)

    env.monkeypatch.setattr(trust_store, "_with_pool_write", slow_write)
    body = _single_body(
        display_name=None,
        speaker_label="Mika", speaker_id="neko_visit:5f2c1b7e", speaker_tier="none",
        speaker_activity_events=[{"id": "evt-00007", "count": 1}],
    )
    request = asyncio.create_task(_post(env, body))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        request.cancel()
        for _ in range(20):
            await asyncio.sleep(0)
        # Threaded pool writes cannot be canceled: retain the persistence lock until completion, preventing clearing from advancing generation before persistence.
        assert env.fs._get_persist_alock(NAME).locked()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await request
    assert not env.fs._get_persist_alock(NAME).locked()


async def test_restored_retry_fences_an_activity_only_segment_forgotten_during_apply(env):
    from memory.scopes import coerce_subject

    env.llm.responses = [[]]
    real_trust = env.routes._apply_trust_for_segments

    async def trust_crash(_states):
        raise RuntimeError("killed at the trust step")

    env.monkeypatch.setattr(env.routes, "_apply_trust_for_segments", trust_crash)
    body = _single_body(
        display_name=None,
        speaker_label="Mika", speaker_id="neko_visit:5f2c1b7e", speaker_tier="none",
        speaker_activity_events=[{"id": "evt-00008", "count": 1}],
    )
    with pytest.raises(Exception):
        await _post(env, body)
    real_apply = env.routes._apply_keyed_staging

    async def forget_during_apply(*args, **kwargs):
        # While recovery retry applies under the key lock, generation-less clearing advances generation; pre-erasure scanning happened to skip this staging.
        env.fs._bump_subject_forget_generation(NAME, coerce_subject(GROUP))
        return await real_apply(*args, **kwargs)

    captured = []

    async def capture(states):
        captured.append(list(states))
        return await real_trust(states)

    env.monkeypatch.setattr(env.routes, "_apply_keyed_staging", forget_during_apply)
    env.monkeypatch.setattr(env.routes, "_apply_trust_for_segments", capture)
    await _post(env, body)
    # Recovery retry also rechecks against generation read before application, excluding this segment's activity from the trust pool.
    (states,) = captured
    assert env.routes._trust_mutation_for(states[0]) is None


async def test_cancelled_trust_write_returns_only_after_its_worker_finished(env):
    import threading

    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    real_write = trust_store._with_pool_write

    def slow_write(mutator):
        entered.set()
        release.wait(5)
        try:
            return real_write(mutator)
        finally:
            finished.set()

    env.monkeypatch.setattr(trust_store, "_with_pool_write", slow_write)
    mutation = trust_store.TrustMutation(
        speaker_account_id="neko_visit:5f2c1b7e",
        activity_events=(trust_store.ActivityEvent(id="evt-00009", count=1),),
        signal_events=(), channel=None,
    )
    # Cancellation scanning cancels the task performing this write itself, not only the outer request: the thread cannot stop, so task completion must await its write.
    write = asyncio.create_task(trust_store.aapply_trust_mutations([mutation]))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        write.cancel()
        for _ in range(20):
            await asyncio.sleep(0)
        assert not write.done()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await write
    assert finished.is_set()


async def test_restored_retry_drops_facts_forgotten_during_apply_on_the_same_baseline(env):
    from memory.scopes import coerce_subject

    env.llm.responses = [SINGLE_FACTS]
    original = _fail_on_item(env, failing_seq=0)
    body = _single_body(
        speaker_label="Mika", speaker_id="neko_visit:5f2c1b7e", speaker_tier="none",
        speaker_activity_events=[{"id": "evt-00010", "count": 1}],
    )
    with pytest.raises(HTTPException):
        await _post(env, body)                                   # Staging persisted; no fact items written.
    env.monkeypatch.setattr(env.routes, "_apply_keyed_item", original)
    real_apply = env.routes._apply_keyed_staging
    subject = coerce_subject(GROUP)

    async def forget_during_apply(*args, **kwargs):
        # Pre-erasure scanning skipped this staging: generation-less clearing completes entirely during recovery retry application.
        await env.fs.abegin_subject_forget(NAME, subject)
        await env.fs.aend_subject_forget(NAME, subject)
        return await real_apply(*args, **kwargs)

    captured = []
    real_trust = env.routes._apply_trust_for_segments

    async def capture(states):
        captured.append(list(states))
        return await real_trust(states)

    env.monkeypatch.setattr(env.routes, "_apply_keyed_staging", forget_during_apply)
    env.monkeypatch.setattr(env.routes, "_apply_trust_for_segments", capture)
    await _post(env, body)
    # Fact and trust layers share one baseline: do not restore pre-clear generated facts afterward or add activity to the pool.
    assert _facts_of(env, GROUP) == []
    (states,) = captured
    assert env.routes._trust_mutation_for(states[0]) is None


def _one_segment_staging(subject: dict) -> dict:
    return {"segments": [{"subject": dict(subject), "wire_key": GROUP_KEY}], "items": [], "applied": []}


async def test_restored_baseline_watches_the_journal_subject_not_todays_routing(env):
    from memory.scopes import coerce_subject

    routes = env.routes
    staging = _one_segment_staging(GROUP)                        # Staging records GROUP.
    contexts = [{"subject": coerce_subject(GP)}]                 # Routing later changes to resolve GP.
    baseline = routes._capture_forget_generations(NAME, routes._staged_segment_subjects(staging, contexts))
    env.fs._bump_subject_forget_generation(NAME, coerce_subject(GP))
    # Only the currently routed subject changed: this staging writes GROUP and must not be misclassified as cleared.
    assert routes._mark_items_forgotten_during_generation(NAME, staging, contexts, baseline) == set()
    env.fs._bump_subject_forget_generation(NAME, coerce_subject(GROUP))
    # The staged subject was cleared: recognize it.
    assert routes._mark_items_forgotten_during_generation(NAME, staging, contexts, baseline) == {0}
    assert staging["segments"][0].get("dropped_by_forget") is True


async def test_baseline_captured_inside_a_running_forget_still_fences(env):
    from memory.scopes import coerce_subject

    routes = env.routes
    subject = coerce_subject(GROUP)
    staging = _one_segment_staging(GROUP)
    contexts = [{"subject": subject}]
    await env.fs.abegin_subject_forget(NAME, subject)
    try:
        # Baseline falls after abegin advances but before aend advances: generation matches, but the fact layer currently rejects this segment.
        baseline = routes._capture_forget_generations(NAME, [subject])
        assert routes._mark_items_forgotten_during_generation(NAME, staging, contexts, baseline) == {0}
    finally:
        await env.fs.aend_subject_forget(NAME, subject)


async def test_cancelled_staging_write_returns_only_after_the_file_is_written(env):
    import threading

    idem = env.idem
    entered, release = threading.Event(), threading.Event()
    real_write = idem._write_json_object

    def slow_write(path, data):
        entered.set()
        release.wait(5)
        return real_write(path, data)

    env.monkeypatch.setattr(idem, "_write_json_object", slow_write)
    write = asyncio.create_task(idem.write_staging(NAME, KEY_GROUP, {"segments": [], "items": []}))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        write.cancel()
        for _ in range(20):
            await asyncio.sleep(0)
        # Caller holds the key lock: do not return or unlock while the threaded write remains running.
        assert not write.done()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await write
    assert _staging_file(env, KEY_GROUP).exists()


@pytest.mark.parametrize("responses", [[], SINGLE_FACTS], ids=["activity_only", "facts"])
async def test_request_arriving_inside_a_running_forget_is_fenced_from_trust(env, responses):
    from memory.scopes import coerce_subject

    env.llm.responses = [responses]
    subject = coerce_subject(GROUP)
    captured = []
    real_trust = env.routes._apply_trust_for_segments

    async def capture(states):
        captured.append(list(states))
        return await real_trust(states)

    env.monkeypatch.setattr(env.routes, "_apply_trust_for_segments", capture)
    body = _single_body(
        display_name=None,
        speaker_label="Mika", speaker_id="neko_visit:5f2c1b7e", speaker_tier="none",
        speaker_activity_events=[{"id": "evt-00011", "count": 1}],
    )
    await env.fs.abegin_subject_forget(NAME, subject)            # Clearing advanced generation but has not finished.
    try:
        await _post(env, body)
    finally:
        await env.fs.aend_subject_forget(NAME, subject)
    # The fact layer rejects this segment during clearing; the trust step blocks activity by the same criterion.
    assert _facts_of(env, GROUP) == []
    (states,) = captured
    assert env.routes._trust_mutation_for(states[0]) is None


async def test_restored_retry_keeps_its_journal_subject_when_routing_changed(env):
    from memory import subject_identity
    from memory.scopes import coerce_subject

    env.llm.responses = [SINGLE_FACTS]
    original = _fail_on_item(env, failing_seq=0)
    body = _single_body(display_name=None)
    with pytest.raises(HTTPException):
        await _post(env, body)                                   # Staging's recorded write target is GROUP.
    env.monkeypatch.setattr(env.routes, "_apply_keyed_item", original)
    rerouted = coerce_subject(GP)
    env.fs._bump_subject_forget_generation(NAME, rerouted)       # The two subjects have different counters.
    real_canonical = subject_identity.canonical_subject

    def reroute(subject, snap=None):
        if subject.key == GROUP_KEY:
            return rerouted                                      # Routing later changes to resolve GP.
        return real_canonical(subject, snap)

    env.monkeypatch.setattr(subject_identity, "canonical_subject", reroute)
    result = await _post(env, body)
    # Recovery retry writes GROUP as staged: use GROUP's baseline too, not GP's counter, which would wrongly discard facts.
    assert result["created"] == 2 and len(_facts_of(env, GROUP)) == 2


@pytest.mark.parametrize("responses", [SINGLE_FACTS, []], ids=["facts", "display_name_only"])
async def test_restored_retry_inside_a_running_forget_writes_no_display_name(env, responses):
    from memory.scopes import coerce_subject

    env.llm.responses = [responses]
    original = _fail_on_item(env, failing_seq=0)
    with pytest.raises(HTTPException):
        await _post(env, _single_body())                         # Staging persisted; interrupted at the first item.
    env.monkeypatch.setattr(env.routes, "_apply_keyed_item", original)
    subject = coerce_subject(GROUP)
    await env.fs.abegin_subject_forget(NAME, subject)            # Recovery retry overlaps clearing in progress.
    try:
        await _post(env, _single_body())
    finally:
        await env.fs.aend_subject_forget(NAME, subject)
    # The fact layer rejects this segment now: its display name must not enter the cleared subject either, since persona writes lack clearing protection.
    assert [row for row in env.persona.display_names if row[0] == GROUP_KEY] == []
    assert _facts_of(env, GROUP) == []


def _fail_fence_writes_once(env):
    """Make the first journal write carrying a forget drop mark fail (a transient disk error)."""
    real_write = env.idem.write_staging
    state = {"failed": False}

    async def flaky(name, key, document):
        marked = any(
            isinstance(segment, dict) and segment.get("dropped_by_forget") is True
            for segment in document.get("segments") or []
        )
        if marked and not state["failed"]:
            state["failed"] = True
            raise OSError("disk hiccup while persisting the fence")
        return await real_write(name, key, document)

    env.monkeypatch.setattr(env.idem, "write_staging", flaky)
    return state


def _capture_trust(env):
    captured = []
    real_trust = env.routes._apply_trust_for_segments

    async def capture(states):
        captured.append(list(states))
        return await real_trust(states)

    env.monkeypatch.setattr(env.routes, "_apply_trust_for_segments", capture)
    return captured


_ACTIVITY_BODY = dict(
    display_name=None,
    speaker_label="Mika", speaker_id="neko_visit:5f2c1b7e", speaker_tier="none",
)


async def test_failed_trust_step_fence_write_leaves_a_durable_fallback(env):
    from memory.scopes import coerce_subject

    env.llm.responses = [[]]
    body = _single_body(**_ACTIVITY_BODY, speaker_activity_events=[{"id": "evt-00012", "count": 1}])
    real_apply = env.routes._apply_keyed_staging

    async def forget_before_apply(*args, **kwargs):
        env.fs._bump_subject_forget_generation(NAME, coerce_subject(GROUP))
        return await real_apply(*args, **kwargs)

    env.monkeypatch.setattr(env.routes, "_apply_keyed_staging", forget_before_apply)
    state = _fail_fence_writes_once(env)
    with pytest.raises(HTTPException) as excinfo:
        await _post(env, body)
    assert excinfo.value.status_code == 503 and state["failed"]
    # Staging lacks a marker, but the key record identifies blocked subjects; recovery retry discards this segment accordingly.
    record = json.loads(Path(env.idem.keys_path(NAME)).read_text(encoding="utf-8"))[KEY_GROUP]
    assert GROUP_KEY in record["forgotten_keys"]
    env.monkeypatch.setattr(env.routes, "_apply_keyed_staging", real_apply)
    captured = _capture_trust(env)
    await _post(env, body)                                       # Same-key retry takes its baseline after clearing.
    (states,) = captured
    assert env.routes._trust_mutation_for(states[0]) is None


async def test_failed_restored_fence_write_leaves_a_durable_fallback(env):
    from memory.scopes import coerce_subject

    env.llm.responses = [SINGLE_FACTS]
    original = _fail_on_item(env, failing_seq=0)
    with pytest.raises(HTTPException):
        await _post(env, _single_body())
    env.monkeypatch.setattr(env.routes, "_apply_keyed_item", original)
    subject = coerce_subject(GROUP)
    state = _fail_fence_writes_once(env)
    await env.fs.abegin_subject_forget(NAME, subject)
    try:
        with pytest.raises(HTTPException) as excinfo:
            await _post(env, _single_body())                     # Recovery retry overlaps clearing; marker write fails.
        assert excinfo.value.status_code == 503 and state["failed"]
    finally:
        await env.fs.aend_subject_forget(NAME, subject)
    record = json.loads(Path(env.idem.keys_path(NAME)).read_text(encoding="utf-8"))[KEY_GROUP]
    assert GROUP_KEY in record["forgotten_keys"]                 # Use the key-record fallback.
    await _post(env, _single_body())                             # Retry again after clearing: baseline is now fresh.
    # The key-record fallback marker still discards this segment, writing neither display name nor facts into the cleared subject.
    assert [row for row in env.persona.display_names if row[0] == GROUP_KEY] == []
    assert _facts_of(env, GROUP) == []


async def test_failed_post_write_recheck_fence_write_leaves_a_durable_fallback(env):
    from memory.scopes import coerce_subject

    env.llm.responses = [SINGLE_FACTS]
    real_write = env.idem.write_staging
    writes = {"n": 0}

    async def write_then_forget(name, key, document):
        await real_write(name, key, document)
        writes["n"] += 1
        if writes["n"] == 1:
            # Generation-less clearing advances generation after initial staging persistence but before rechecking.
            env.fs._bump_subject_forget_generation(NAME, coerce_subject(GROUP))

    env.monkeypatch.setattr(env.idem, "write_staging", write_then_forget)
    state = _fail_fence_writes_once(env)                         # Writing the newly added recheck marker fails.
    with pytest.raises(HTTPException) as excinfo:
        await _post(env, _single_body())
    assert excinfo.value.status_code == 503 and state["failed"]
    record = json.loads(Path(env.idem.keys_path(NAME)).read_text(encoding="utf-8"))[KEY_GROUP]
    assert GROUP_KEY in record["forgotten_keys"]
    env.monkeypatch.setattr(env.idem, "write_staging", real_write)
    await _post(env, _single_body())                             # Same-key retry takes its baseline after clearing.
    # The generation path's post-staging recheck has a fallback too: do not restore pre-clear facts or write display names.
    assert _facts_of(env, GROUP) == []
    assert [row for row in env.persona.display_names if row[0] == GROUP_KEY] == []


def _bump_before_apply(env):
    from memory.scopes import coerce_subject

    real_apply = env.routes._apply_keyed_staging

    async def forget_before_apply(*args, **kwargs):
        env.fs._bump_subject_forget_generation(NAME, coerce_subject(GROUP))
        return await real_apply(*args, **kwargs)

    env.monkeypatch.setattr(env.routes, "_apply_keyed_staging", forget_before_apply)


def _forgotten_keys_on_record(env) -> list:
    record = json.loads(Path(env.idem.keys_path(NAME)).read_text(encoding="utf-8"))[KEY_GROUP]
    return record.get("forgotten_keys") or []


async def test_cancelled_fence_write_still_records_the_fallback(env):
    env.llm.responses = [[]]
    _bump_before_apply(env)
    real_write = env.idem.write_staging

    async def cancelled_on_fence(name, key, document):
        if any(isinstance(seg, dict) and seg.get("dropped_by_forget") is True for seg in document.get("segments") or []):
            # Threaded write fails while the request is canceled; the waiter observes only cancellation.
            raise asyncio.CancelledError()
        return await real_write(name, key, document)

    env.monkeypatch.setattr(env.idem, "write_staging", cancelled_on_fence)
    body = _single_body(**_ACTIVITY_BODY, speaker_activity_events=[{"id": "evt-00013", "count": 1}])
    with pytest.raises(asyncio.CancelledError):
        await _post(env, body)
    # Write success is unknown: still record blocked subjects on the key record.
    assert GROUP_KEY in _forgotten_keys_on_record(env)


async def test_fallback_fence_record_completes_before_the_key_lock_is_released(env):
    env.llm.responses = [[]]
    _bump_before_apply(env)
    state = _fail_fence_writes_once(env)
    real_update = env.idem.update_key
    entered, gate = asyncio.Event(), asyncio.Event()

    async def gated_update(name, key, fn):
        if state["failed"]:                                      # The write after staging failure is the fallback.
            entered.set()
            await gate.wait()
        return await real_update(name, key, fn)

    env.monkeypatch.setattr(env.idem, "update_key", gated_update)
    body = _single_body(**_ACTIVITY_BODY, speaker_activity_events=[{"id": "evt-00014", "count": 1}])
    request = asyncio.create_task(_post(env, body))
    await asyncio.wait_for(entered.wait(), 5)
    request.cancel()
    for _ in range(20):
        await asyncio.sleep(0)
    # Fallback unfinished: do not finish the request or release its key lock yet.
    assert not request.done() and env.idem.key_lock(NAME, KEY_GROUP).locked()
    gate.set()
    with pytest.raises(asyncio.CancelledError):
        await request
    assert GROUP_KEY in _forgotten_keys_on_record(env)


async def test_fallback_fence_keys_are_the_newly_fenced_journal_subjects_only(env):
    from memory.scopes import coerce_subject

    staging = {
        "segments": [
            # Segments discarded earlier for other reasons may share routing targets with valid segments; do not use them to block others.
            {"subject": dict(PART), "wire_key": GP_KEY, "dropped_by_forget": True},
            {"subject": dict(GROUP), "wire_key": PART_KEY, "dropped_by_forget": True},
        ],
        "items": [], "applied": [],
    }
    # Newly blocked segments: recorded staging targets + stable wire keys, excluding all previously discarded segments' keys.
    assert env.routes._fenced_journal_keys(staging, {1}) == {GROUP_KEY, PART_KEY}


async def test_fallback_task_cancelled_alone_still_answers_503(env):
    env.llm.responses = [[]]
    _bump_before_apply(env)
    state = _fail_fence_writes_once(env)
    real_update = env.idem.update_key

    async def swept_update(name, key, fn):
        if state["failed"]:
            # Shutdown cancellation scanning cancels only the fallback task; the caller remains uncanceled.
            asyncio.current_task().cancel()
            await asyncio.sleep(0)
        return await real_update(name, key, fn)

    env.monkeypatch.setattr(env.idem, "update_key", swept_update)
    body = _single_body(**_ACTIVITY_BODY, speaker_activity_events=[{"id": "evt-00015", "count": 1}])
    with pytest.raises(HTTPException) as excinfo:
        await _post(env, body)
    # An uncanceled caller must not receive CancelledError: report 503 normally for failed fallback persistence.
    assert excinfo.value.status_code == 503


async def test_fallback_fence_survives_a_lost_journal_and_changed_routing(env):
    from memory import subject_identity
    from memory.scopes import coerce_subject

    env.llm.responses = [SINGLE_FACTS]
    real_canonical = subject_identity.canonical_subject
    route = {"to": coerce_subject(GP)}

    def reroute(subject, snap=None):
        if subject.key == GROUP_KEY:
            return route["to"]
        return real_canonical(subject, snap)

    env.monkeypatch.setattr(subject_identity, "canonical_subject", reroute)
    real_apply = env.routes._apply_keyed_staging

    async def forget_before_apply(*args, **kwargs):
        env.fs._bump_subject_forget_generation(NAME, coerce_subject(GP))   # Clear the routing target used at that time.
        return await real_apply(*args, **kwargs)

    env.monkeypatch.setattr(env.routes, "_apply_keyed_staging", forget_before_apply)
    _fail_fence_writes_once(env)
    with pytest.raises(HTTPException):
        await _post(env, _single_body())                         # Marker write fails, leaving only the key-record fallback.
    env.monkeypatch.setattr(env.routes, "_apply_keyed_staging", real_apply)
    _staging_file(env, KEY_GROUP).unlink()                       # Staging subsequently disappears.
    route["to"] = coerce_subject(PART)                           # Routing changes too.
    await _post(env, _single_body())
    # Fallback carries stable wire keys: regeneration still recognizes this segment, preventing pre-clear input from entering a new target.
    assert _facts_of(env, PART) == [] and _facts_of(env, GP) == []


async def test_fallback_written_while_the_caller_is_cancelled_is_reported_as_written(env):
    env.llm.responses = [[]]
    _bump_before_apply(env)
    state = _fail_fence_writes_once(env)
    real_update = env.idem.update_key
    caller = {}

    async def update_then_cancel_caller(name, key, fn):
        result = await real_update(name, key, fn)
        if state["failed"]:
            caller["task"].cancel()                              # Fallback finishes exactly when the caller is canceled.
        return result

    env.monkeypatch.setattr(env.idem, "update_key", update_then_cancel_caller)
    logged = []
    env.monkeypatch.setattr(env.routes.logger, "warning", lambda msg, *a, **k: logged.append(str(msg)))
    body = _single_body(**_ACTIVITY_BODY, speaker_activity_events=[{"id": "evt-00016", "count": 1}])
    caller["task"] = asyncio.ensure_future(_post(env, body))
    with pytest.raises((asyncio.CancelledError, HTTPException)):
        await caller["task"]
    assert GROUP_KEY in _forgotten_keys_on_record(env)
    # Record successful persistence as success, never as failure.
    assert any("recorded in forgotten_keys" in line for line in logged)
    assert not any("cancelled before it was written" in line for line in logged)


async def test_recheck_reports_only_the_segments_it_newly_fenced(env):
    from memory.scopes import coerce_subject

    routes = env.routes
    staging = {
        "segments": [
            {"subject": dict(GP), "wire_key": GP_KEY, "dropped_by_forget": True},     # An earlier recheck already blocked it.
            {"subject": dict(PART), "wire_key": PART_KEY},
        ],
        "items": [
            {"seq": 0, "segment": 0, "kind": "facts", "facts": []},
            {"seq": 1, "segment": 1, "kind": "facts", "facts": [{"text": "Mika 的猫下午在窗台睡觉"}]},
        ],
        "applied": [{"seq": 0, "dropped_forget_during_generation": True}],
    }
    contexts = [{"subject": coerce_subject(GP)}, {"subject": coerce_subject(PART)}]
    baseline = routes._capture_forget_generations(NAME, routes._staged_segment_subjects(staging, contexts))
    env.fs._bump_subject_forget_generation(NAME, coerce_subject(GP))      # Segment 0 was already blocked; generation still differs.
    env.fs._bump_subject_forget_generation(NAME, coerce_subject(PART))    # Segment 1 is newly cleared now.
    # Report only newly blocked segment 1, without adding segment 0 to the fallback again.
    assert routes._mark_items_forgotten_during_generation(NAME, staging, contexts, baseline) == {1}
