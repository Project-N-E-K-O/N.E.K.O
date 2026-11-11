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

"""The host under failure: storage errors, cancellation, races, bad names."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from nekoclaw.host import outbox as outbox_module
from nekoclaw.host.ledger import Ledger
from nekoclaw.host.outbox import OutboxSender, retry_delay
from nekoclaw.host.paths import HostPaths
from nekoclaw.host.permissions import (
    DEFAULT_MODES,
    DENIED_BY_SETTINGS,
    PermissionFileError,
    PermissionGate,
    PermissionStore,
)
from nekoclaw.host.reports import KIND_TASK_RESULT, Reporter
from tests.unit.nekoclaw.host_fakes import make_host, no_model


class Clock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


async def _file(ledger: Ledger, **values: Any) -> Any:
    fields = {"lanlan_name": "Lan", "title": "weather", "dispatch_text": "check the weather", "source": "dispatch"}
    fields.update(values)
    commission, created = await ledger.create(**fields)
    assert created
    return commission


async def _wait_for(predicate: Any, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.01)


# ---------------------------------------------------------------------------
# Ledger: what an earlier process left behind is failed in one transaction
# ---------------------------------------------------------------------------


async def test_reconcile_writes_status_and_event_together(tmp_path, monkeypatch):
    path = tmp_path / "ledger.db"
    clock = Clock()
    first = Ledger(path, clock=clock)
    commission = await _file(first)
    await first.transition(commission.id, "working")
    await first.close()

    original = Ledger._event

    def broken_event(self: Ledger, conn: Any, commission_id: str, kind: str, payload: dict[str, Any]) -> int:
        if kind == "status":
            raise sqlite3.OperationalError("disk I/O error")
        return original(self, conn, commission_id, kind, payload)

    monkeypatch.setattr(Ledger, "_event", broken_event)
    second = Ledger(path, clock=clock)
    with pytest.raises(sqlite3.OperationalError):
        await second.open()
    await second.close()

    # Neither the status nor its event was written.
    conn = sqlite3.connect(str(path))
    try:
        status = conn.execute("SELECT status FROM commissions WHERE id = ?", (commission.id,)).fetchone()[0]
    finally:
        conn.close()
    assert status == "working"

    monkeypatch.setattr(Ledger, "_event", original)
    third = Ledger(path, clock=clock)
    try:
        failed = await third.get(commission.id)
        events = [event.type for event in await third.events(commission.id)]
    finally:
        await third.close()
    assert failed.status == "failed"
    assert events.count("status") == 2


# ---------------------------------------------------------------------------
# Ledger: a status change and its report are stored together
# ---------------------------------------------------------------------------


@pytest.fixture
async def ledger(tmp_path: Any) -> Any:
    book = Ledger(tmp_path / "nekoclaw" / "ledger.db", clock=Clock())
    yield book
    await book.close()


async def test_transition_stores_its_report_in_the_same_transaction(ledger):
    commission = await _file(ledger)
    await ledger.transition(commission.id, "working")

    moved = await ledger.transition(commission.id, "done", reason="finished", outbox=[("task_result", {"text": "ok"})])

    assert moved.applied
    assert len(moved.outbox_ids) == 1
    pending = await ledger.outbox_pending()
    assert [(item.id, item.commission_id, item.kind, item.payload) for item in pending] == [
        (moved.outbox_ids[0], commission.id, "task_result", {"text": "ok"})
    ]


async def test_a_refused_transition_stores_no_report(ledger):
    commission = await _file(ledger)
    await ledger.transition(commission.id, "cancelled")

    moved = await ledger.transition(commission.id, "done", outbox=[("task_result", {"text": "late"})])

    assert not moved.applied
    assert moved.outbox_ids == ()
    assert await ledger.outbox_pending() == []


async def test_a_report_that_cannot_be_stored_undoes_the_transition(ledger, monkeypatch):
    commission = await _file(ledger)
    await ledger.transition(commission.id, "working")

    def broken(*_args: Any, **_kwargs: Any) -> int:
        raise sqlite3.OperationalError("disk full")

    monkeypatch.setattr(ledger, "_outbox_insert", broken)
    with pytest.raises(sqlite3.OperationalError):
        await ledger.transition(commission.id, "done", outbox=[("task_result", {"text": "ok"})])

    assert (await ledger.get(commission.id)).status == "working"
    assert [event.type for event in await ledger.events(commission.id)] == ["created", "status"]


class Kicks:
    def __init__(self) -> None:
        self.count = 0

    def kick(self) -> None:
        self.count += 1


async def test_reporter_moves_and_reports_in_one_step(ledger):
    sender = Kicks()
    reporter = Reporter(ledger, sender)  # type: ignore[arg-type]
    commission = await _file(ledger)
    await ledger.transition(commission.id, "working")

    report = reporter.task_result_report(
        commission, channel="familiar", success=True, summary="done it", status="completed"
    )
    moved = await reporter.transition(commission.id, "done", reason="finished", report=report, result_summary="x")

    assert report is not None and report[0] == KIND_TASK_RESULT
    assert moved.applied and moved.commission.result_summary == "x"
    pending = await ledger.outbox_pending()
    assert [item.payload["summary"] for item in pending] == ["done it"]
    assert sender.count == 1


async def test_reporter_builds_nothing_for_silent_or_child_commissions(ledger):
    reporter = Reporter(ledger)
    commission = await _file(ledger)
    child = await _file(ledger, source="familiar", parent_id=commission.id)

    silent = reporter.task_result_report(
        commission, channel="familiar", success=True, summary="s", status="completed", delivery_mode="silent"
    )
    for_child = reporter.task_result_report(child, channel="familiar", success=True, summary="s", status="completed")

    assert silent is None and for_child is None
    await ledger.transition(commission.id, "working")
    moved = await reporter.transition(commission.id, "done", report=silent)
    assert moved.applied
    assert await ledger.outbox_pending() == []


async def test_reporter_stores_nothing_when_the_move_is_refused(ledger):
    sender = Kicks()
    reporter = Reporter(ledger, sender)  # type: ignore[arg-type]
    commission = await _file(ledger)
    await ledger.transition(commission.id, "cancelled")
    report = reporter.task_result_report(commission, channel="familiar", success=True, summary="s", status="completed")

    moved = await reporter.transition(commission.id, "done", report=report)

    assert not moved.applied
    assert await ledger.outbox_pending() == []
    assert sender.count == 0


# ---------------------------------------------------------------------------
# Outbox
# ---------------------------------------------------------------------------


def test_retry_delay_does_not_overflow_after_many_failures():
    assert retry_delay(1025) == 300.0
    assert retry_delay(10**6) == 300.0


async def test_the_outbox_loop_retries_after_a_storage_error(ledger, monkeypatch):
    await ledger.outbox_add(None, "receipt", {"text": "hello"})
    monkeypatch.setattr(outbox_module, "retry_delay", lambda _attempts: 0.01)
    original = ledger.outbox_pending
    calls = {"n": 0}

    async def flaky(**kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise sqlite3.OperationalError("database is locked")
        return await original(**kwargs)

    monkeypatch.setattr(ledger, "outbox_pending", flaky)
    sent: list[int] = []

    async def send(item: Any) -> bool:
        sent.append(item.id)
        return True

    sender = OutboxSender(ledger, send, clock=time.time)
    try:
        sender.kick()
        await _wait_for(lambda: sent == [1])
    finally:
        await sender.close()


async def test_a_due_report_after_the_first_fifty_is_not_left_waiting(tmp_path):
    book = Ledger(tmp_path / "ledger.db")
    try:
        for index in range(51):
            await book.outbox_add(None, "receipt", {"n": index})
        # The first fifty have failed many times and back off for five minutes.
        await book.run(lambda conn: conn.execute("UPDATE outbox SET attempts = 20 WHERE id <= 50") and None)
        delivered = asyncio.Event()

        async def send(item: Any) -> bool:
            if item.id == 51:
                delivered.set()
                return True
            return False

        sender = OutboxSender(book, send)
        try:
            sender.kick()
            await asyncio.wait_for(delivered.wait(), 3.0)
        finally:
            await sender.close()
    finally:
        await book.close()


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("content", ["{not json", "[]", json.dumps({"categories": ["run_command"]})])
async def test_an_unreadable_permissions_file_denies_everything(tmp_path, content):
    path = tmp_path / "permissions.json"
    path.write_text(content, encoding="utf-8")
    store = PermissionStore(path)

    for category in DEFAULT_MODES:
        assert await store.mode(category) == "deny"
    assert await store.mode("mcp_server", "notes") == "deny"
    asked: list[Any] = []

    async def asker(commission_id: str, ask: Any) -> str:
        asked.append(ask)
        return "always"

    decision = await PermissionGate(store, asker).check("c1", "plugin_entry")
    assert (decision.allow, decision.reason) == (False, DENIED_BY_SETTINGS)
    assert asked == []
    snapshot = await store.snapshot()
    assert snapshot["unreadable"] is True
    assert set(snapshot["categories"].values()) == {"deny"}
    # Nothing is written over the damaged file.
    with pytest.raises(PermissionFileError):
        await store.set_mode("run_command", "allow")
    assert path.read_text(encoding="utf-8") == content

    # Once the file is repaired it is read again.
    path.write_text(json.dumps({"categories": {"run_command": "allow"}}), encoding="utf-8")
    assert await store.mode("run_command") == "allow"
    assert await store.mode("outside_write") == "first_ask"
    assert (await store.snapshot())["unreadable"] is False


async def test_a_permissions_path_that_cannot_be_read_denies_everything(tmp_path):
    path = tmp_path / "permissions.json"
    path.mkdir()
    store = PermissionStore(path)

    assert await store.mode("workdir_files") == "deny"


async def test_without_a_file_the_defaults_apply(tmp_path):
    store = PermissionStore(tmp_path / "permissions.json")

    assert await store.mode("plugin_entry") == "allow"
    assert (await store.snapshot())["unreadable"] is False


async def test_a_cancelled_set_mode_finishes_its_write_before_the_next_one(tmp_path):
    path = tmp_path / "permissions.json"
    store = PermissionStore(path)
    await store.mode("run_command")
    original = store._write
    started = threading.Event()
    release = threading.Event()
    written = threading.Event()
    calls = {"n": 0}

    def slow_write(categories: dict[str, str], mcp_servers: dict[str, str]) -> None:
        calls["n"] += 1
        first_call = calls["n"] == 1
        if first_call:
            started.set()
            release.wait(5.0)
        original(categories, mcp_servers)
        if first_call:
            written.set()

    store._write = slow_write  # type: ignore[method-assign]
    first = asyncio.ensure_future(store.set_mode("run_command", "allow"))
    await _wait_for(started.is_set)
    first.cancel()
    await asyncio.sleep(0.02)
    second = asyncio.ensure_future(store.set_mode("run_command", "deny"))
    await asyncio.sleep(0.05)
    release.set()
    await second
    with pytest.raises(asyncio.CancelledError):
        await first
    # Both writes are on disk; the later setting must be the one kept.
    await asyncio.to_thread(written.wait, 5.0)

    assert await PermissionStore(path).mode("run_command") == "deny"
    assert store._lock.locked() is False


async def test_a_setting_changed_while_asking_is_not_overwritten_by_always(tmp_path):
    store = PermissionStore(tmp_path / "permissions.json")

    async def asker(commission_id: str, ask: Any) -> str:
        # The user forbids the category in the console while the question waits.
        await store.set_mode("run_command", "deny")
        return "always"

    decision = await PermissionGate(store, asker).check("c1", "run_command")

    assert (decision.allow, decision.reason) == (False, DENIED_BY_SETTINGS)
    assert await store.mode("run_command") == "deny"
    assert await PermissionStore(tmp_path / "permissions.json").mode("run_command") == "deny"


async def test_always_after_a_change_to_ask_every_time_counts_once(tmp_path):
    store = PermissionStore(tmp_path / "permissions.json")

    async def asker(commission_id: str, ask: Any) -> str:
        await store.set_mode("run_command", "always_ask")
        return "always"

    decision = await PermissionGate(store, asker).check("c1", "run_command")

    assert decision.allow is True
    assert await store.mode("run_command") == "always_ask"


async def test_always_still_turns_the_category_to_allow_when_nothing_changed(tmp_path):
    store = PermissionStore(tmp_path / "permissions.json")

    async def asker(commission_id: str, ask: Any) -> str:
        return "always"

    decision = await PermissionGate(store, asker).check("c1", "run_command")

    assert decision.allow is True
    assert await store.mode("run_command") == "allow"


# ---------------------------------------------------------------------------
# Shutting down
# ---------------------------------------------------------------------------


def _slow_familiars(host: Any) -> tuple[asyncio.Event, asyncio.Event]:
    entered = asyncio.Event()
    release = asyncio.Event()
    original = host.familiars.shutdown

    async def slow() -> None:
        entered.set()
        await release.wait()
        await original()

    host.familiars.shutdown = slow
    return entered, release


async def test_a_cancelled_shutdown_still_finishes_and_a_retry_waits_for_it(tmp_path):
    host = make_host(tmp_path)
    await host.ledger.open()
    entered, release = _slow_familiars(host)

    first = asyncio.ensure_future(host.shutdown())
    await asyncio.wait_for(entered.wait(), 3.0)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert host.closed is False

    retry = asyncio.ensure_future(host.shutdown())
    await asyncio.sleep(0.02)
    assert not retry.done()
    release.set()
    await asyncio.wait_for(retry, 3.0)

    assert host.closed is True
    assert host.ledger.opened is False
    with pytest.raises(RuntimeError):
        await host.ledger.open()


@pytest.fixture
def mount() -> Any:
    from nekoclaw.host import mount as module

    module.reset_for_tests()
    yield module
    module.reset_for_tests()


def _unreachable(**_kwargs: Any) -> Any:
    raise RuntimeError("no plugin server in this test")


async def test_no_second_host_is_built_while_the_first_shuts_down(mount, tmp_path):
    mount.configure(root=tmp_path / "nekoclaw", familiar_model=no_model, client_factory=_unreachable)
    host = mount.get_host()
    entered, release = _slow_familiars(host)

    closing = asyncio.ensure_future(mount.shutdown_host())
    await asyncio.wait_for(entered.wait(), 3.0)
    try:
        assert mount.get_host() is host
        assert (await host.dispatch("anything", lanlan_name="Lan")).reason == "disabled"
    finally:
        release.set()
        await asyncio.wait_for(closing, 3.0)

    again = mount.get_host()
    try:
        assert again is not host
        assert host.closed is True
    finally:
        await mount.shutdown_host()


async def test_a_host_whose_shutdown_finished_in_the_background_is_replaced(mount, tmp_path):
    mount.configure(root=tmp_path / "nekoclaw", familiar_model=no_model, client_factory=_unreachable)
    host = mount.get_host()
    entered, release = _slow_familiars(host)

    closing = asyncio.ensure_future(mount.shutdown_host())
    await asyncio.wait_for(entered.wait(), 3.0)
    closing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await closing
    assert mount.get_host() is host
    release.set()
    await _wait_for(lambda: host.closed)

    again = mount.get_host()
    try:
        assert again is not host
    finally:
        await mount.shutdown_host()


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "commission_id, project",
    [
        ("../evil", None),
        ("..", None),
        (".", None),
        ("", None),
        ("a/b", None),
        ("a\\b", None),
        ("C:evil", None),
        ("abc", "../../outside"),
        ("abc", "/abs"),
        ("abc", "x\\..\\..\\y"),
        ("abc", "trailing."),
        ("abc", "bad\x00name"),
        ("CON", None),
        ("abc", "nul"),
        ("abc", "Aux"),
        ("abc", "prn"),
        ("abc", "nul.txt"),
        ("abc", "COM1"),
        ("abc", "com9.log"),
        ("abc", "LPT1"),
        ("abc", "lpt9.tar.gz"),
        ("abc", "Con .txt"),
        ("abc", "COM¹"),
    ],
)
def test_unsafe_names_never_become_directories(tmp_path, commission_id, project):
    paths = HostPaths(tmp_path)

    with pytest.raises(ValueError):
        paths.session_dir(commission_id, project)
    with pytest.raises(ValueError):
        paths.work_dir(commission_id, project)


def test_ordinary_names_stay_below_the_host_directory(tmp_path):
    paths = HostPaths(tmp_path)
    commission_id = "0123456789abcdef0123456789abcdef"

    for project in (None, "_daily", "notes-2026", "猫爪", "console", "nullable", "COM10", "LPT0", "auxiliary.txt", "my.con"):
        session = paths.session_dir(commission_id, project)
        assert session.name == commission_id
        assert Path(tmp_path) in session.parents
