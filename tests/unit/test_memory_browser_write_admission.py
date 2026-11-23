# -*- coding: utf-8 -*-
"""Memory Browser writers addressed by the request body join the character fence.

The publication guard middleware only sees names in the URL path. Browser
writers that carry ``character_name`` in the JSON body must admit themselves
through ``runtime._begin_character_request`` instead, so that deleting or
renaming a character both rejects new writes (409) and waits for in-flight
ones before disposing the character's storage.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi import FastAPI

from tests.fastapi_routes import iter_routes

_PREFIX = "/internal/memory/browser/"

# path -> (handler attribute in app.memory_server.routes, JSON body without character_name)
_BODY_ADDRESSED_WRITERS: dict[str, tuple[str, dict]] = {
    _PREFIX + "persona/edit": (
        "browser_edit_persona",
        {"entity": "master", "entry_id": "p1", "text": "new text"},
    ),
    _PREFIX + "persona/flags": (
        "browser_set_persona_flags",
        {"entity": "master", "entry_id": "p1", "user_pinned": True},
    ),
    _PREFIX + "persona/archive": (
        "browser_archive_persona",
        {"entity": "master", "entry_id": "p1"},
    ),
    _PREFIX + "persona/restore_version": (
        "browser_restore_persona_version",
        {"entity": "master", "entry_id": "p1", "version_index": 0},
    ),
    _PREFIX + "correction/resolve": (
        "browser_resolve_correction",
        {"correction_index": 0, "action": "keep_new"},
    ),
    _PREFIX + "reflection/review": (
        "browser_review_reflection",
        {"reflection_id": "r1", "decision": "confirm"},
    ),
    _PREFIX + "fact/edit": (
        "browser_edit_fact",
        {"fact_id": "f1", "text": "new text"},
    ),
    _PREFIX + "fact/archive": (
        "browser_archive_fact",
        {"fact_id": "f1"},
    ),
    _PREFIX + "duplicate/merge": (
        "browser_merge_duplicate",
        {
            "kind": "persona", "entity": "master",
            "keep_id": "p1", "drop_id": "p2", "merged_text": "merged",
        },
    ),
    _PREFIX + "directive/add": (
        "browser_add_directive",
        {"term": "测试话题", "language": "zh"},
    ),
    _PREFIX + "directive/remove": (
        "browser_remove_directive",
        {"term": "测试话题", "language": "zh"},
    ),
}


class _Released(Exception):
    """Raised by the blocking fake once the test lets the write finish."""


class _FakeManager:
    """Stands in for every manager a browser writer touches.

    With ``gate=None`` any call fails the test: a fenced request must never
    reach storage. With a gate, the first call parks until the test opens it.
    """

    def __init__(self, gate: asyncio.Event | None = None, result=None):
        self.gate = gate
        self.result = result
        self.calls: list[str] = []

    def __getattr__(self, attr):
        if attr.startswith("_"):
            raise AttributeError(attr)

        async def _call(*_args, **_kwargs):
            self.calls.append(attr)
            if self.gate is None and self.result is None:
                raise AssertionError(f"{attr} reached storage while fenced")
            if self.gate is not None:
                await self.gate.wait()
                raise _Released(attr)
            return self.result

        return _call


@pytest.fixture
def browser_env(monkeypatch):
    from app import memory_server
    from app.memory_server import routes

    runtime = memory_server.runtime

    def install(manager: _FakeManager) -> None:
        monkeypatch.setattr(runtime, "persona_manager", manager)
        monkeypatch.setattr(runtime, "fact_store", manager)
        monkeypatch.setattr(runtime, "reflection_engine", manager)
        monkeypatch.setattr(
            "memory.user_directives.get_user_directives_manager", lambda: manager,
        )

    monkeypatch.setattr(routes, "assert_cloudsave_writable", lambda *a, **k: None)
    monkeypatch.setattr(routes, "_queue_long_term_capture", lambda *a, **k: None)

    probe = FastAPI()
    for path, (attr, _body) in _BODY_ADDRESSED_WRITERS.items():
        probe.post(path)(getattr(routes, attr))

    held: set[str] = set()

    def hold(name: str) -> None:
        memory_server.review._publication_held_derived_task_names.add(name)
        held.add(name)

    yield memory_server, probe, install, hold

    for name in held:
        memory_server.review._publication_held_derived_task_names.discard(name)


def _client(probe: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=probe), base_url="http://memory-browser-probe",
    )


@pytest.mark.unit
def test_every_body_addressed_browser_writer_is_listed():
    """A new body-addressed browser writer must be added here, which runs it through the fence tests."""
    from app import memory_server

    found: dict[str, object] = {}
    for route in iter_routes(memory_server.runtime.app.routes):
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", None) or set()
        if path.startswith(_PREFIX) and "{" not in path and "POST" in methods:
            found[path] = getattr(route, "endpoint", None)

    assert set(found) == set(_BODY_ADDRESSED_WRITERS)
    from app.memory_server import routes
    for path, endpoint in found.items():
        if endpoint is not None:
            assert endpoint is getattr(routes, _BODY_ADDRESSED_WRITERS[path][0])


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("path", sorted(_BODY_ADDRESSED_WRITERS))
async def test_body_addressed_writer_is_rejected_while_character_is_released(
    browser_env, path,
):
    memory_server, probe, install, hold = browser_env
    name = "删除中的记忆页角色"
    manager = _FakeManager()
    install(manager)
    hold(name)

    body = {"character_name": name, **_BODY_ADDRESSED_WRITERS[path][1]}
    async with _client(probe) as client:
        response = await client.post(path, json=body)

    assert response.status_code == 409
    assert manager.calls == []
    assert memory_server.runtime._active_character_requests.get(name, 0) == 0


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("path", sorted(_BODY_ADDRESSED_WRITERS))
async def test_release_waits_for_in_flight_body_addressed_writer(browser_env, path):
    memory_server, probe, install, hold = browser_env
    runtime = memory_server.runtime
    name = "写入中的记忆页角色"
    gate = asyncio.Event()
    manager = _FakeManager(gate=gate)
    install(manager)

    body = {"character_name": name, **_BODY_ADDRESSED_WRITERS[path][1]}
    async with _client(probe) as client:
        in_flight = asyncio.create_task(client.post(path, json=body))
        try:
            for _ in range(500):
                if manager.calls:
                    break
                await asyncio.sleep(0.001)
            assert manager.calls, "write never reached the manager"
            assert runtime._active_character_requests.get(name, 0) == 1

            # Release begins: new writes bounce, the admitted one keeps its slot.
            hold(name)
            second = await client.post(path, json=body)
            assert second.status_code == 409
            assert len(manager.calls) == 1

            loop = asyncio.get_running_loop()
            assert await runtime._wait_for_character_requests(
                name, loop.time() + 0.05,
            ) is False

            gate.set()
            with pytest.raises(_Released):
                await in_flight
            assert await runtime._wait_for_character_requests(
                name, loop.time() + 1.0,
            ) is True
            assert runtime._active_character_requests.get(name, 0) == 0
        finally:
            gate.set()
            if not in_flight.done():
                in_flight.cancel()
            await asyncio.gather(in_flight, return_exceptions=True)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_normal_browser_edit_is_admitted_and_releases_its_slot(browser_env):
    memory_server, probe, install, _hold = browser_env
    name = "正常编辑的记忆页角色"
    entry = {"id": "p1", "text": "new text"}
    manager = _FakeManager(result=entry)
    install(manager)

    body = {"character_name": name, **_BODY_ADDRESSED_WRITERS[_PREFIX + "persona/edit"][1]}
    async with _client(probe) as client:
        response = await client.post(_PREFIX + "persona/edit", json=body)

    assert response.status_code == 200
    assert response.json() == {"success": True, "entry": entry}
    assert manager.calls == ["aedit_persona_entry"]
    assert memory_server.runtime._active_character_requests.get(name, 0) == 0
