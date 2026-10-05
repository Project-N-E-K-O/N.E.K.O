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

"""Read-only ``GET /internal/memory/{name}/scoped_subjects`` (OD-18)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from memory.facts import FactStore
from memory.scopes import MemorySubject

NAME = "Neko"
VISIT_PART = MemorySubject.participant("neko_visit", "u_person")
VISIT_GROUP = MemorySubject.group_chat("neko_visit", "pair01")
VISIT_GP = MemorySubject.group_participant("neko_visit", "pair01", "c_cat")
VISIT_ARCHIVED = MemorySubject.participant("neko_visit", "u_old")
QQ_PART = MemorySubject.participant("qq", "10001")
LOOKALIKE = MemorySubject.participant("neko_visitor", "u_trap")


def _fact(fid: str, subject: MemorySubject, created_at: str, **extra) -> dict:
    return {
        "id": fid, "text": f"text {fid}", "importance": 5,
        "created_at": created_at, **subject.as_entry_fields(), **extra,
    }


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json.dumps(data, ensure_ascii=False).encode("utf-8"))


@pytest.fixture
def env(tmp_path, monkeypatch):
    from app.memory_server import routes, runtime

    memory_root = tmp_path / "memory"
    char_dir = memory_root / NAME
    assert str(char_dir).startswith(str(tmp_path))
    _write(char_dir / "facts.json", [
        _fact("f1", VISIT_PART, "2026-09-01T10:00:00"),
        _fact("f2", VISIT_PART, "2026-09-03T10:00:00"),
        _fact("f3", VISIT_GROUP, "2026-09-02T10:00:00"),
        _fact("f4", VISIT_GP, "2026-09-02T11:00:00"),
        _fact("f5", QQ_PART, "2026-09-05T10:00:00"),
        _fact("f6", LOOKALIKE, "2026-09-05T10:00:00"),
        {"id": "legacy", "text": "legacy private", "created_at": "2026-09-05T10:00:00"},
    ])
    _write(char_dir / "facts_archive.json", [
        _fact(
            "a1", VISIT_ARCHIVED, "2026-01-01T10:00:00",
            subject_archived_at="2026-04-01T00:00:00",
        ),
    ])
    _write(char_dir / "persona.json", {
        VISIT_PART.persona_section_key: {
            **VISIT_PART.as_entry_fields(),
            "display_name": "Mika",
            "facts": [{
                "id": "p1", "text": "persona line",
                "created_at": "2026-09-10T10:00:00",
                **VISIT_PART.as_entry_fields(),
            }],
        },
        VISIT_GROUP.persona_section_key: {
            **VISIT_GROUP.as_entry_fields(),
            "display_name": "串门群",
            "facts": [],
        },
        QQ_PART.persona_section_key: {
            **QQ_PART.as_entry_fields(),
            "display_name": "QQ 用户",
            "facts": [{"id": "p2", "text": "x", **QQ_PART.as_entry_fields()}],
        },
    })
    cm = MagicMock()
    cm.memory_dir = str(memory_root)
    fs = FactStore()
    fs._config_manager = cm
    reflections = [
        {"id": "r1", "text": "group reflection", "status": "confirmed",
         "created_at": "2026-09-04T10:00:00", **VISIT_GROUP.as_entry_fields()},
        {"id": "r2", "text": "qq reflection", "status": "confirmed",
         **QQ_PART.as_entry_fields()},
    ]
    _write(char_dir / "reflections.json", reflections)
    # 列表端点直接读盘：经 store 加载器取路径会 ensure_character_dir
    reflection_engine = SimpleNamespace(
        aload_reflections=AsyncMock(side_effect=AssertionError("must read reflections from disk")),
    )
    persona_manager = SimpleNamespace(
        aensure_persona=AsyncMock(side_effect=AssertionError("must not recover persona")),
    )
    monkeypatch.setattr(runtime, "_config_manager", cm)
    monkeypatch.setattr(runtime, "fact_store", fs)
    monkeypatch.setattr(runtime, "reflection_engine", reflection_engine)
    monkeypatch.setattr(runtime, "persona_manager", persona_manager)
    yield SimpleNamespace(routes=routes, runtime=runtime, root=memory_root, monkeypatch=monkeypatch)


def _snapshot(root: Path) -> dict:
    snap = {}
    for dirpath, dirnames, filenames in os.walk(root):
        snap[dirpath] = ("dir", tuple(sorted(dirnames)))
        for filename in filenames:
            path = os.path.join(dirpath, filename)
            stat = os.stat(path)
            with open(path, "rb") as handle:
                snap[path] = ("file", stat.st_mtime_ns, handle.read())
    return snap


async def test_platform_filter_uses_kind_and_platform_component(env):
    result = await env.routes.list_scoped_subjects(NAME, platform="neko_visit")
    by_key = {(row["subject_kind"], row["subject_id"]): row for row in result["subjects"]}
    assert set(by_key) == {
        ("participant", "neko_visit:u_person"),
        ("group_chat", "neko_visit:pair01"),
        ("group_participant", "neko_visit:pair01:c_cat"),
        ("participant", "neko_visit:u_old"),
    }
    person = by_key[("participant", "neko_visit:u_person")]
    assert person == {
        "subject_kind": "participant",
        "subject_id": "neko_visit:u_person",
        "scope": VISIT_PART.scope,
        "display_name": "Mika",
        "facts": 2,
        "reflections": 0,
        "persona": True,
        "prompt_locale": False,
        "corrections": 0,
        "staged": False,
        "last_write_at": "2026-09-10T10:00:00",
        "archived": False,
    }
    group = by_key[("group_chat", "neko_visit:pair01")]
    assert group["facts"] == 1 and group["reflections"] == 1
    assert group["persona"] is False and group["display_name"] == "串门群"
    assert group["last_write_at"] == "2026-09-04T10:00:00"
    member = by_key[("group_participant", "neko_visit:pair01:c_cat")]
    assert member["facts"] == 1 and member["display_name"] is None
    old = by_key[("participant", "neko_visit:u_old")]
    assert old["archived"] is True and old["facts"] == 1

    qq = await env.routes.list_scoped_subjects(NAME, platform="qq")
    assert [(row["subject_kind"], row["subject_id"]) for row in qq["subjects"]] == [
        ("participant", "qq:10001"),
    ]
    assert qq["subjects"][0]["reflections"] == 1


async def test_listing_writes_nothing(env):
    before = _snapshot(env.root)
    await env.routes.list_scoped_subjects(NAME, platform="neko_visit")
    unknown = await env.routes.list_scoped_subjects("Ghost", platform="neko_visit")
    assert unknown == {"subjects": []}
    assert _snapshot(env.root) == before
    assert not (env.root / "Ghost").exists()


async def test_uninitialized_runtime_answers_503(env):
    from fastapi import HTTPException

    env.monkeypatch.setattr(env.runtime, "_config_manager", None)
    with pytest.raises(HTTPException) as excinfo:
        await env.routes.list_scoped_subjects(NAME, platform="neko_visit")
    assert excinfo.value.status_code == 503


async def test_listing_does_not_need_the_write_side_components(env):
    # 只读接口直接读盘：受限模式下 persona / reflection 组件没起来也照常列出
    env.monkeypatch.setattr(env.runtime, "persona_manager", None)
    env.monkeypatch.setattr(env.runtime, "reflection_engine", None)
    env.monkeypatch.setattr(env.runtime, "fact_store", None)
    result = await env.routes.list_scoped_subjects(NAME, platform="neko_visit")
    assert result["subjects"]


@pytest.mark.parametrize("platform", ["", "neko_visit:x", "x" * 65])
async def test_invalid_platform_is_422(env, platform):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as excinfo:
        await env.routes.list_scoped_subjects(NAME, platform=platform)
    assert excinfo.value.status_code == 422


def test_limited_mode_answers_409_like_every_other_endpoint(env):
    runtime = env.runtime
    env.monkeypatch.setattr(runtime, "_memory_runtime_init_completed", False)
    env.monkeypatch.setattr(
        runtime, "get_storage_startup_blocking_reason", lambda _cm: "selection_required",
    )
    client = TestClient(runtime.app, base_url="http://127.0.0.1:48912", client=("127.0.0.1", 50000))
    response = client.get(f"/internal/memory/{NAME}/scoped_subjects", params={"platform": "neko_visit"})
    assert response.status_code == 409
    assert response.json()["error_code"] == "storage_startup_blocked"
    assert response.json()["limited_mode"] is True


def test_http_route_serves_the_listing_and_is_outside_the_write_fence(env):
    runtime = env.runtime
    env.monkeypatch.setattr(runtime, "_memory_runtime_init_completed", True)
    env.monkeypatch.setattr(runtime, "_memory_storage_blocked_after_init", False)
    client = TestClient(runtime.app, base_url="http://127.0.0.1:48912", client=("127.0.0.1", 50000))
    response = client.get(f"/internal/memory/{NAME}/scoped_subjects", params={"platform": "neko_visit"})
    assert response.status_code == 200
    assert len(response.json()["subjects"]) == 4
    assert "scoped_subjects" not in runtime._CHARACTER_SCOPED_WRITE_OPS
    assert runtime._character_write_name_from_path(
        f"/internal/memory/{NAME}/scoped_subjects", "GET",
    ) is None


async def test_listing_never_creates_the_character_directory(env):
    """Deletion racing the GET: the loaders' ensure_character_dir must never run."""
    import memory

    def _forbidden(*_args, **_kwargs):
        raise AssertionError("listing must not create character directories")

    # FactStore 与 reflection 持久化都在函数内 from memory import ensure_character_dir
    env.monkeypatch.setattr(memory, "ensure_character_dir", _forbidden)
    result = await env.routes.list_scoped_subjects(NAME, platform="neko_visit")
    assert result["subjects"]


async def test_fact_present_in_both_files_is_counted_once(env):
    """Archiving writes facts_archive.json first; a crash before facts.json is rewritten leaves both."""
    char_dir = env.root / NAME
    active = json.loads((char_dir / "facts.json").read_text(encoding="utf-8"))
    archive = json.loads((char_dir / "facts_archive.json").read_text(encoding="utf-8"))
    archive.append(next(f for f in active if f.get("id") == "f1"))
    _write(char_dir / "facts_archive.json", archive)
    result = await env.routes.list_scoped_subjects(NAME, platform="neko_visit")
    part = next(row for row in result["subjects"] if row["subject_id"] == VISIT_PART.subject_id)
    assert part["facts"] == 2


async def test_unhashable_fact_ids_do_not_break_the_listing(env):
    char_dir = env.root / NAME
    active = json.loads((char_dir / "facts.json").read_text(encoding="utf-8"))
    active.append({**_fact("bad", VISIT_PART, "2026-09-06T10:00:00"), "id": ["legacy", 1]})
    _write(char_dir / "facts.json", active)
    archive = json.loads((char_dir / "facts_archive.json").read_text(encoding="utf-8"))
    archive.append({**_fact("bad2", VISIT_PART, "2026-09-06T10:00:00"), "id": {"x": 1}})
    _write(char_dir / "facts_archive.json", archive)
    result = await env.routes.list_scoped_subjects(NAME, platform="neko_visit")
    part = next(row for row in result["subjects"] if row["subject_id"] == VISIT_PART.subject_id)
    assert part["facts"] == 4


async def test_active_fact_with_a_bad_id_keeps_its_subject_active(env):
    lone = MemorySubject.participant("neko_visit", "p_" + "9" * 24)
    char_dir = env.root / NAME
    active = json.loads((char_dir / "facts.json").read_text(encoding="utf-8"))
    active.append({**_fact("x", lone, "2026-09-06T10:00:00"), "id": ["legacy"]})
    _write(char_dir / "facts.json", active)
    result = await env.routes.list_scoped_subjects(NAME, platform="neko_visit")
    row = next(r for r in result["subjects"] if r["subject_id"] == lone.subject_id)
    assert row["facts"] == 1 and row["archived"] is False


async def test_same_bare_id_in_two_subjects_is_not_merged(env):
    char_dir = env.root / NAME
    archive = json.loads((char_dir / "facts_archive.json").read_text(encoding="utf-8"))
    archive.append(_fact("f3", VISIT_PART, "2026-08-01T10:00:00"))     # 与活跃池 f3（群）同裸 id
    _write(char_dir / "facts_archive.json", archive)
    result = await env.routes.list_scoped_subjects(NAME, platform="neko_visit")
    part = next(row for row in result["subjects"] if row["subject_id"] == VISIT_PART.subject_id)
    assert part["facts"] == 3



async def test_unhashable_subject_fields_do_not_break_the_listing(env):
    char_dir = env.root / NAME
    active = json.loads((char_dir / "facts.json").read_text(encoding="utf-8"))
    active.append({**_fact("odd", VISIT_PART, "2026-09-06T10:00:00"), "scope": ["bad"]})
    _write(char_dir / "facts.json", active)
    result = await env.routes.list_scoped_subjects(NAME, platform="neko_visit")
    assert any(row["subject_id"] == VISIT_GROUP.subject_id for row in result["subjects"])



async def test_damaged_persona_facts_do_not_break_the_listing(env):
    char_dir = env.root / NAME
    persona = json.loads((char_dir / "persona.json").read_text(encoding="utf-8"))
    persona[VISIT_GROUP.persona_section_key]["facts"] = 1
    _write(char_dir / "persona.json", persona)
    result = await env.routes.list_scoped_subjects(NAME, platform="neko_visit")
    # 一个 section 的 facts 坏了只当作空：其余 subject 照常列出
    assert any(row["subject_id"] == VISIT_PART.subject_id for row in result["subjects"])


async def test_deeply_nested_persona_does_not_break_the_listing(env):
    (env.root / NAME / "persona.json").write_text("[" * 100000 + "]" * 100000, encoding="utf-8")
    result = await env.routes.list_scoped_subjects(NAME, platform="neko_visit")
    # 嵌套过深的 persona 按读不出处理：其余 subject 照常列出
    assert any(row["subject_id"] == VISIT_GROUP.subject_id for row in result["subjects"])


async def test_subject_with_only_terminal_reflections_is_listed(env):
    only_reflection = MemorySubject.participant("neko_visit", "u_only_reflection")
    char_dir = env.root / NAME
    reflections = json.loads((char_dir / "reflections.json").read_text(encoding="utf-8"))
    reflections.append({"id": "r9", "text": "已晋升的反思", "status": "promoted",
                        **only_reflection.as_entry_fields()})
    _write(char_dir / "reflections.json", reflections)
    result = await env.routes.list_scoped_subjects(NAME, platform="neko_visit")
    # 已终结的反思仍在文件里、仍在清除面上：只剩它的 subject 也要能被找到
    assert any(row["subject_id"] == only_reflection.subject_id for row in result["subjects"])


async def test_terminal_reflections_do_not_make_an_archived_subject_active(env):
    char_dir = env.root / NAME
    reflections = json.loads((char_dir / "reflections.json").read_text(encoding="utf-8"))
    reflections.append({"id": "r10", "text": "已否决的反思", "status": "denied",
                        **VISIT_ARCHIVED.as_entry_fields()})
    _write(char_dir / "reflections.json", reflections)
    result = await env.routes.list_scoped_subjects(NAME, platform="neko_visit")
    row = next(r for r in result["subjects"] if r["subject_id"] == VISIT_ARCHIVED.subject_id)
    # 只剩归档事实与已终结反思：照样计入 reflections，但仍是归档状态
    assert row["reflections"] == 1 and row["archived"] is True


async def test_unhashable_reflection_status_does_not_break_the_listing(env):
    char_dir = env.root / NAME
    reflections = json.loads((char_dir / "reflections.json").read_text(encoding="utf-8"))
    reflections.append({"id": "r11", "text": "状态坏了", "status": ["promoted"],
                        **VISIT_GROUP.as_entry_fields()})
    _write(char_dir / "reflections.json", reflections)
    result = await env.routes.list_scoped_subjects(NAME, platform="neko_visit")
    assert any(row["subject_id"] == VISIT_GROUP.subject_id for row in result["subjects"])


async def test_locale_only_subject_is_listed(env):
    locale_only = MemorySubject.participant("neko_visit", "u_quiet")
    key = json.dumps(
        [locale_only.kind, locale_only.subject_id, locale_only.scope],
        ensure_ascii=False, separators=(",", ":"),
    )
    _write(env.root / NAME / "scoped_prompt_locales.json", {"subjects": {
        key: {"reserved_order": 5},                       # 带键生成中断只留下预留的语言行
        "not json": {"language": "zh"},
        json.dumps([QQ_PART.kind, QQ_PART.subject_id, QQ_PART.scope]): {"language": "zh"},
    }})
    result = await env.routes.list_scoped_subjects(NAME, platform="neko_visit")
    by_key = {(row["subject_kind"], row["subject_id"]): row for row in result["subjects"]}
    # 只存了语言的 subject 同样在清除的删除面上：要能被列出来
    row = by_key[("participant", "neko_visit:u_quiet")]
    assert row["prompt_locale"] is True and row["facts"] == 0 and row["persona"] is False
    assert row["archived"] is False
    # 别的平台的语言行不混进来；已有数据的 subject 不受影响
    assert ("participant", "qq:10001") not in by_key
    assert by_key[("participant", "neko_visit:u_person")]["prompt_locale"] is False


async def test_reflection_without_id_still_lists_its_subject(env):
    lost_id = MemorySubject.participant("neko_visit", "u_no_id")
    path = env.root / NAME / "reflections.json"
    rows = json.loads(path.read_text(encoding="utf-8"))
    rows.append({"text": "id 丢了的反思", "status": "confirmed", **lost_id.as_entry_fields()})
    _write(path, rows)
    result = await env.routes.list_scoped_subjects(NAME, platform="neko_visit")
    by_key = {(row["subject_kind"], row["subject_id"]): row for row in result["subjects"]}
    # 清除按 subject 删反思、不看 id：只剩这种行的 subject 同样要能被找到
    assert by_key[("participant", "neko_visit:u_no_id")]["reflections"] == 1


async def test_correction_only_subjects_are_listed(env):
    stamped = MemorySubject.participant("neko_visit", "u_corr")
    legacy = MemorySubject.participant("neko_visit", "u_legacy")
    _write(env.root / NAME / "persona_corrections.json", [
        {"old_text": "a", "new_text": "b", **stamped.as_entry_fields()},
        # 老版本没打 subject 戳、只在 entity 里记归属的待处理纠正
        {"old_text": "c", "new_text": "d", "entity": f"{legacy.persona_section_key}"},
        "garbage",
    ])
    result = await env.routes.list_scoped_subjects(NAME, platform="neko_visit")
    by_key = {(row["subject_kind"], row["subject_id"]): row for row in result["subjects"]}
    # 待处理的人设纠正同在删除面上：只剩它们的 subject 也要能被找到
    assert by_key[("participant", "neko_visit:u_corr")]["corrections"] == 1
    assert by_key[("participant", "neko_visit:u_legacy")]["corrections"] == 1


async def test_persona_entries_of_another_scope_in_a_shared_section_are_listed(env):
    shared = MemorySubject.participant("neko_visit", "u_shared")
    other_scope = MemorySubject.create("participant", "neko_visit:u_shared", scope="custom:other")
    path = env.root / NAME / "persona.json"
    persona = json.loads(path.read_text(encoding="utf-8"))
    # section key 不含 scope：两个 scope 的条目住在同一个 section 里，元数据只记了默认 scope
    persona[shared.persona_section_key] = {
        **shared.as_entry_fields(),
        "facts": [
            {"id": "p_default", "text": "a", **shared.as_entry_fields()},
            {"id": "p_other", "text": "b", **other_scope.as_entry_fields()},
        ],
    }
    _write(path, persona)
    result = await env.routes.list_scoped_subjects(NAME, platform="neko_visit")
    rows = {(row["subject_id"], row["scope"]): row for row in result["subjects"]}
    # 只剩 persona 的另一个 scope 同样要能被找到、被清除
    assert rows[("neko_visit:u_shared", "custom:other")]["persona"] is True
    assert rows[("neko_visit:u_shared", shared.scope)]["persona"] is True


async def test_staging_only_subject_is_listed(env):
    staged = MemorySubject.participant("neko_visit", "u_staged")
    _write(env.root / NAME / "idempotency_staging" / ("a" * 32 + ".json"), {
        "key": "k", "segments": [{"wire_key": staged.key, "subject": staged.as_entry_fields()}],
        "items": [], "applied": [],
    })
    result = await env.routes.list_scoped_subjects(NAME, platform="neko_visit")
    by_key = {(row["subject_kind"], row["subject_id"]): row for row in result["subjects"]}
    # 生成后、应用前崩溃留下的暂存可能是唯一的数据：同样要能被找到、被清除
    assert by_key[("participant", "neko_visit:u_staged")]["staged"] is True


async def test_staging_left_by_a_terminal_key_is_not_listed(env):
    from app.memory_server.idempotency import key_digest

    done_subject = MemorySubject.participant("neko_visit", "u_done")
    pending_subject = MemorySubject.participant("neko_visit", "u_pending")
    orphan_subject = MemorySubject.participant("neko_visit", "u_orphan")
    staging_dir = env.root / NAME / "idempotency_staging"
    for key, subject in (("k-done", done_subject), ("k-pending", pending_subject), ("k-orphan", orphan_subject)):
        _write(staging_dir / f"{key_digest(key)}.json", {
            "key": key, "segments": [{"wire_key": subject.key, "subject": subject.as_entry_fields()}],
            "items": [], "applied": [],
        })
    _write(env.root / NAME / "idempotency_keys.json", {"k-done": {"state": "done"}, "k-pending": {"state": "pending"}})
    result = await env.routes.list_scoped_subjects(NAME, platform="neko_visit")
    staged = {row["subject_id"] for row in result["subjects"] if row["staged"]}
    # 已终结的键收尾时删暂存失败留下的文件没有待应用的东西；pending 与没有记录的孤儿照常列
    assert staged == {"neko_visit:u_pending", "neko_visit:u_orphan"}


async def test_forgotten_locale_row_left_on_disk_is_not_listed(env):
    from app.memory_server import locale_state

    gone = MemorySubject.participant("neko_visit", "u_gone")
    key = json.dumps([gone.kind, gone.subject_id, gone.scope], ensure_ascii=False, separators=(",", ":"))
    _write(env.root / NAME / "scoped_prompt_locales.json", {"subjects": {key: {"language": "zh", "order": 5}}})
    # 清除已写下 cutoff、改写 sidecar 之前崩溃：这一行还在盘上
    env.monkeypatch.setattr(locale_state, "_subject_locale_forget_cutoffs_loaded", True)
    env.monkeypatch.setitem(locale_state._subject_locale_forget_cutoffs, (NAME, key), 10)
    result = await env.routes.list_scoped_subjects(NAME, platform="neko_visit")
    listed = {(row["subject_kind"], row["subject_id"]) for row in result["subjects"]}
    # 与语言加载器同口径滤掉：被清的 subject 不能一直以 prompt_locale 出现
    assert ("participant", "neko_visit:u_gone") not in listed


async def test_staging_of_a_key_with_an_unknown_state_is_still_listed(env):
    from app.memory_server.idempotency import key_digest

    subject = MemorySubject.participant("neko_visit", "u_odd")
    _write(env.root / NAME / "idempotency_staging" / f"{key_digest('k-odd')}.json", {
        "key": "k-odd", "segments": [{"wire_key": subject.key, "subject": subject.as_entry_fields()}],
        "items": [], "applied": [],
    })
    _write(env.root / NAME / "idempotency_keys.json", {"k-odd": {"request": "x"}})
    result = await env.routes.list_scoped_subjects(NAME, platform="neko_visit")
    staged = {row["subject_id"] for row in result["subjects"] if row["staged"]}
    # 只有明确终结（done / cancelled）的键才跳过：状态缺失 / 坏了时暂存可能是唯一的明文，照常列出
    assert staged == {"neko_visit:u_odd"}
