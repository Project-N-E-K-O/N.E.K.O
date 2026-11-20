# Copyright 2025-2026 Project N.E.K.O. Team
# SPDX-License-Identifier: Apache-2.0
"""Recipient-side and whole-commission checks at the trusted adapter boundary."""

import asyncio
import json
import sqlite3
from contextlib import closing

import pytest
import pytest_asyncio

from nekoclaw.host import call_audit
from nekoclaw.host.delivery import Answer, DeliveryDenied, Parcel
from nekoclaw.host.ledger import Ledger
from nekoclaw.host.reports import describe_permission
from tests.unit.nekoclaw.host_fakes import make_host

WORK = frozenset({"conversations:work"})
PRIVATE = frozenset({"conversations:private"})
PUBLIC = frozenset()


class Endpoint:
    def __init__(self, identity, readable):
        self.identity = identity
        self.readable = readable
        self.started = []
        self.inputs = []
        self.native_allow = True
        self.response = Answer()
        self.wait = None

    def permits(self, sender, *, one_way):
        return self.native_allow

    def accepts(self, scopes):
        return scopes <= self.readable

    async def trigger(self, invocation, arguments):
        self.started.append(invocation)
        self.inputs.append(arguments)
        # Only the source may route the report to its approved destination.
        # Even a misbehaving implementation cannot return data on this path.
        return self.response

    async def call(self, invocation, arguments):
        self.inputs.append(arguments)
        if self.wait:
            await self.wait.wait()
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


@pytest.mark.parametrize("locale", ["zh", "zh-TW", "en", "ja", "ko", "ru", "es", "pt"])
def test_question_distinguishes_arguments_from_results_in_the_session_language(locale):
    def message(direction):
        return describe_permission(
            {
                "category": "managed_delivery",
                "detail": {"direction": direction, "title": "A → B (work)"},
            },
            locale,
        )

    assert message("arguments") != message("result")
    assert "A → B (work)" in message("result")


@pytest_asyncio.fixture
async def env(tmp_path):
    host = make_host(tmp_path)
    try:
        commission, _ = await host.ledger.create(
            lanlan_name="Lan", title="test", dispatch_text="test", source="dispatch"
        )
        questions = []

        async def allow(_commission, ask):
            questions.append(ask)
            return "session"

        host.permissions.set_asker(allow)
        timer = Endpoint("dsh:timer", PUBLIC)
        report = Endpoint("neko:report", WORK | PRIVATE)
        for endpoint in (timer, report):
            host.delivery.register(endpoint)
        yield host, commission.id, timer, report, questions
    finally:
        await host.shutdown()


@pytest.mark.asyncio
async def test_one_way_work_has_no_reply_handle_or_private_arguments(env):
    host, cid, timer, report, questions = env
    report.response = Answer(
        Parcel({"private": True}, WORK), Parcel("private summary", WORK)
    )
    assert (
        await host.delivery.invoke(
            cid, timer, report.identity, Parcel("start", PUBLIC), one_way=True
        )
        is None
    )
    assert len(report.started) == 1 and report.started[0].commission_id == cid
    with pytest.raises(DeliveryDenied):
        await host.delivery.invoke(
            cid, report, timer.identity, Parcel("private summary", WORK), one_way=True
        )
    assert not timer.inputs and not questions
    with pytest.raises(DeliveryDenied):
        await host.delivery.invoke(
            cid, timer, report.identity, Parcel("start", PUBLIC), one_way=False
        )


@pytest.mark.asyncio
async def test_each_channel_has_its_own_recipient_check_and_derived_data_inherits_inputs(
    env,
):
    host, cid, timer, report, questions = env
    timer.readable = WORK
    report.response = Answer(
        Parcel("private program value", PRIVATE), Parcel("visible text", WORK)
    )
    for _ in range(2):
        answer = await host.delivery.invoke(
            cid,
            timer,
            report.identity,
            Parcel("public template", PUBLIC),
            one_way=False,
        )
        assert answer == Answer(content=Parcel("visible text", WORK))
    assert len(questions) == 1 and "dsh:timer" in questions[0].detail["title"]
    report.response = Answer(content=Parcel("derived summary", PUBLIC))
    timer.readable = PUBLIC
    with pytest.raises(DeliveryDenied):
        await host.delivery.invoke(
            cid, timer, report.identity, Parcel("work input", WORK), one_way=False
        )
    async with asyncio.timeout(3):
        rows = await host.ledger.run(lambda db: call_audit.read_trace(db, cid, now=0))
    assert any(row["grant_id"] and row["scope_ref"] for row in rows)
    audit = json.dumps(rows)
    for text in (
        "visible text",
        "private program value",
        "work input",
        "derived summary",
    ):
        assert text not in audit


@pytest.mark.asyncio
async def test_native_denial_unknown_adapter_and_forged_sender_never_reach_legacy_runs(
    env,
):
    host, cid, timer, report, _ = env
    report.native_allow = False
    for sender, target in (
        (timer, report.identity),
        (Endpoint(timer.identity, PRIVATE), report.identity),
        (timer, "neko:legacy_without_delivery_contract"),
    ):
        with pytest.raises(DeliveryDenied):
            await host.delivery.invoke(
                cid, sender, target, Parcel({}, PUBLIC), one_way=True
            )
    assert not report.inputs


@pytest.mark.asyncio
async def test_replacement_during_execution_cannot_receive_result_or_error_details(env):
    host, cid, timer, report, _ = env
    report.wait = asyncio.Event()
    report.response = Answer(value=Parcel("restricted value", PUBLIC))
    pending = asyncio.create_task(
        host.delivery.invoke(
            cid, timer, report.identity, Parcel({}, PUBLIC), one_way=False
        )
    )
    async with asyncio.timeout(5):
        while not report.inputs:
            await asyncio.sleep(0.005)
    host.delivery.unregister(timer)
    replacement = Endpoint(timer.identity, WORK)
    host.delivery.register(replacement)
    report.wait.set()
    with pytest.raises(DeliveryDenied):
        await pending
    report.response = RuntimeError("private contents of a failed request")
    with pytest.raises(DeliveryDenied) as failure:
        await host.delivery.invoke(
            cid, replacement, report.identity, Parcel({}, PUBLIC), one_way=False
        )
    assert "private" not in str(failure.value)
    assert failure.value.__context__ is None


@pytest.mark.asyncio
async def test_previous_audit_schema_is_readable_before_and_after_host_upgrade(
    tmp_path,
):
    path = tmp_path / "old.db"
    ledger = Ledger(path)
    try:
        item, _ = await ledger.create(
            lanlan_name="Lan", title="test", dispatch_text="test", source="dispatch"
        )
        async with call_audit.call(ledger, item.id, "device", "browser"):
            pass
    finally:
        await ledger.close()
    with closing(sqlite3.connect(path)) as db:
        db.row_factory = sqlite3.Row
        for field in ("grant_id", "scope_ref"):
            db.execute(f"ALTER TABLE host_calls DROP COLUMN {field}")
        assert all(
            row["grant_id"] is None for row in call_audit.read_trace(db, item.id, now=0)
        )
    reopened = Ledger(path)
    try:
        fields = await reopened.run(
            lambda db: {r[1] for r in db.execute("PRAGMA table_info(host_calls)")}
        )
        assert {"grant_id", "scope_ref"} <= fields
    finally:
        await reopened.close()


@pytest.mark.asyncio
async def test_nested_work_preserves_new_read_scope_through_forward_and_return(env):
    host, cid, timer, report, _ = env
    reader = Endpoint("neko:reader", WORK)
    reader.response = Answer(value=Parcel("sensitive", WORK))
    host.delivery.register(reader)

    async def read_and_format(_invocation, _arguments):
        answer = await host.delivery.invoke(
            cid, report, reader.identity, Parcel({}, PUBLIC), one_way=False
        )
        assert answer.value.data == "sensitive"
        disguised = Parcel("derived private summary", PUBLIC)
        with pytest.raises(DeliveryDenied):
            await host.delivery.invoke(
                cid, report, timer.identity, disguised, one_way=True
            )
        return Answer(content=disguised)

    report.call = read_and_format
    with pytest.raises(DeliveryDenied):
        await host.delivery.invoke(
            cid, timer, report.identity, Parcel("start", PUBLIC), one_way=False
        )
    assert not timer.inputs


@pytest.mark.asyncio
async def test_revoking_the_input_grant_while_the_target_works_stops_the_return(env):
    host, cid, timer, report, _ = env
    timer.readable = WORK
    report.wait = asyncio.Event()
    report.response = Answer(value=Parcel("reply", PUBLIC))
    pending = asyncio.create_task(
        host.delivery.invoke(
            cid, timer, report.identity, Parcel("input", WORK), one_way=False
        )
    )
    async with asyncio.timeout(5):
        while not report.inputs:
            await asyncio.sleep(0.005)
    rows = await host.ledger.run(lambda db: call_audit.read_trace(db, cid, now=0))
    input_grant = next(
        row["grant_id"] for row in rows if row["kind"] == "send_arguments"
    )
    host.permissions.revoke_grant(input_grant)
    report.wait.set()
    with pytest.raises(DeliveryDenied):
        await pending
