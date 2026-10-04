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

    env.monkeypatch.setattr(env.runtime, "fact_store", None)
    with pytest.raises(HTTPException) as excinfo:
        await env.routes.list_scoped_subjects(NAME, platform="neko_visit")
    assert excinfo.value.status_code == 503


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
    client = TestClient(runtime.app, base_url="http://127.0.0.1:48912")
    response = client.get(f"/internal/memory/{NAME}/scoped_subjects", params={"platform": "neko_visit"})
    assert response.status_code == 409
    assert response.json()["error_code"] == "storage_startup_blocked"
    assert response.json()["limited_mode"] is True


def test_http_route_serves_the_listing_and_is_outside_the_write_fence(env):
    runtime = env.runtime
    env.monkeypatch.setattr(runtime, "_memory_runtime_init_completed", True)
    env.monkeypatch.setattr(runtime, "_memory_storage_blocked_after_init", False)
    client = TestClient(runtime.app, base_url="http://127.0.0.1:48912")
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
