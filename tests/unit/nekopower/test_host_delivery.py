# Copyright 2025-2026 Project N.E.K.O. Team
# SPDX-License-Identifier: Apache-2.0
"""Recipient-side and whole-commission checks at the trusted adapter boundary."""

import asyncio
import json
import sqlite3
from contextlib import closing

import pytest
import pytest_asyncio

from nekopower.host import call_audit
from nekopower.host.delivery import Answer, DeliveryAuditFull, DeliveryDenied, Parcel, ResultWithheld, ScopeTooLarge
from nekopower.host.ledger import Ledger
from nekopower.host.reports import describe_permission, render_question
from tests.unit.nekopower.host_fakes import make_host

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
    assert len({message(kind) for kind in ("arguments", "value", "content")}) == 3
    assert all("A → B (work)" in message(kind) for kind in ("value", "content"))


def test_compact_report_points_to_full_question_without_offering_a_truncated_approval():
    title = "dsh:timer → neko:report " + ", ".join(f"resource-{n}" for n in range(20))
    question = {"kind": "permission", "category": "managed_delivery", "options": ["once", "deny"],
                "detail": {"direction": "value", "title": title}}
    assert title in render_question("任务" * 400, question, "zh")
    assert describe_permission(question, "zh", max_tokens=24) == "请查看这项任务的完整授权请求。"


@pytest.mark.asyncio
async def test_undisplayable_scope_requires_smaller_request_before_asking(env):
    host, cid, timer, report, questions = env
    timer.readable = report.readable = frozenset(f"resource-{n}" for n in range(300))
    with pytest.raises(ScopeTooLarge, match="fewer resources"):
        await host.delivery.invoke(cid, timer, report.identity, Parcel(None, timer.readable), one_way=True)
    assert questions == [] and not report.inputs


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
@pytest.mark.parametrize("early", [False, True])
@pytest.mark.parametrize("outcome", ["allow", "denied", "native_deny", "reregister", "end_run"])
async def test_queued_trigger_obtains_its_own_authorization_but_keeps_origin(env, early, outcome):
    host, cid, timer, report, questions = env
    timer.readable = WORK
    owner = Endpoint("session:owner", WORK)
    host.delivery.register(owner)
    ready, release = asyncio.Event(), asyncio.Event()
    jobs = []

    async def ask(_cid, question):
        questions.append(question)
        return "deny" if len(questions) == 2 and outcome == "denied" else "once"

    async def send():
        ready.set()
        if not early:
            await release.wait()
        await host.delivery.invoke(
            cid, report, owner.identity, Parcel("derived", PUBLIC), one_way=True, deferred=early
        )

    def change_recipient_or_run():
        if outcome == "native_deny":
            owner.readable = PUBLIC
        elif outcome == "reregister":
            host.delivery.unregister(report)
            host.delivery.register(report)
        elif outcome == "end_run":
            host.permissions.end_run(cid)

    async def trigger(invocation, arguments):
        jobs.append(asyncio.create_task(send()))
        await ready.wait()
        if early:
            change_recipient_or_run()

    report.trigger = trigger
    host.permissions.set_asker(ask)
    assert await host.delivery.invoke(cid, timer, report.identity, Parcel("input", WORK), one_way=True) is None
    if not early:
        change_recipient_or_run()
    release.set()
    if outcome == "allow":
        await jobs[0]
        assert owner.inputs == [Parcel("derived", WORK)] and len(questions) == 2
        rows = await host.ledger.run(lambda db: call_audit.read_trace(db, cid, now=0))
        triggers = [r for r in rows if r["kind"] == "trigger"]
        assert len(triggers) == 2 and triggers[1]["parent_call_id"] == triggers[0]["call_id"]
    else:
        with pytest.raises(DeliveryDenied):
            await jobs[0]
        assert not owner.inputs
    assert not timer.inputs


@pytest.mark.asyncio
@pytest.mark.parametrize("deferred", [False, True])
async def test_failed_trigger_does_not_release_an_early_worker(env, deferred):
    host, cid, timer, report, _ = env
    jobs = []
    done_before_failure = []
    ready = asyncio.Event()

    async def send():
        ready.set()
        await host.delivery.invoke(cid, report, timer.identity, Parcel(None, PUBLIC), one_way=True, deferred=deferred)

    async def trigger(invocation, arguments):
        jobs.append(asyncio.create_task(send()))
        await ready.wait()
        # The worker has reached invoke() and either finished or suspended.
        done_before_failure.append(jobs[0].done())
        raise RuntimeError("private error")

    report.trigger = trigger
    with pytest.raises(DeliveryDenied):
        await host.delivery.invoke(cid, timer, report.identity, Parcel(None, PUBLIC), one_way=True)
    assert done_before_failure == [not deferred]
    with pytest.raises(DeliveryDenied):
        await jobs[0]
    assert not timer.inputs


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
@pytest.mark.parametrize("available", [1, 2])
async def test_audit_exhaustion_reports_whether_the_target_ran(env, monkeypatch, available):
    host, cid, timer, report, _ = env
    # Allow the root and invoke. One more slot admits arguments but not the reply.
    monkeypatch.setattr(call_audit, "BUDGET_BYTES", (1 + available) * call_audit.RECORD_BYTES)
    expected = call_audit.AuditFull if available == 1 else DeliveryAuditFull
    with pytest.raises(expected) as failure:
        await host.delivery.invoke(cid, timer, report.identity, Parcel(None, PUBLIC), one_way=False)
    assert bool(report.inputs) == (available == 2)
    assert ("No new action" in str(failure.value)) == (available == 1)
    assert failure.value.__context__ is None


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["send_arguments", "deliver_value", "invoke"])
async def test_audit_close_must_commit_before_the_corresponding_handoff(env, kind):
    host, cid, timer, report, _ = env
    timer.readable = WORK
    report.response = Answer(value=Parcel("private result", WORK))
    await host.ledger.run(lambda db: db.execute(
        f"CREATE TRIGGER fail_close BEFORE UPDATE OF ended_at ON host_calls WHEN OLD.kind='{kind}' "
        "BEGIN SELECT RAISE(ABORT, 'simulated write failure'); END"
    ))
    expected = DeliveryDenied if kind == "send_arguments" else ResultWithheld
    with pytest.raises(expected) as failure:
        await host.delivery.invoke(cid, timer, report.identity, Parcel("private input", WORK), one_way=False)
    assert type(failure.value) is expected
    assert len(report.inputs) == int(kind != "send_arguments")
    assert "private" not in str(failure.value)


@pytest.mark.asyncio
async def test_payload_is_detached_before_each_permission_wait(env):
    host, cid, timer, report, _ = env
    timer.readable = WORK
    source = {"list": ["original input"]}
    result = {"list": ["original output"]}
    report.response = Answer(content=Parcel(result, WORK))

    async def allow(_cid, ask):
        source["list"][0] = "edited input"
        result["list"][0] = "edited output" if ask.detail["direction"] == "content" else "original output"
        return "once"

    host.permissions.set_asker(allow)
    answer = await host.delivery.invoke(cid, timer, report.identity, Parcel(source, WORK), one_way=False)
    assert report.inputs[0].data == {"list": ["original input"]}
    assert answer.content.data == {"list": ["original output"]}
    assert source["list"] == ["edited input"] and result["list"] == ["edited output"]


@pytest.mark.asyncio
async def test_model_content_requires_its_own_approval_after_program_value(env):
    host, cid, timer, report, questions = env
    timer.readable = WORK
    arguments = Parcel(None, PUBLIC)
    report.response = Answer(value=Parcel("program", WORK))
    for _ in range(2):
        assert await host.delivery.invoke(cid, timer, report.identity, arguments, one_way=False) == report.response

    async def deny_model(_cid, ask):
        questions.append(ask)
        return "deny"

    host.permissions.set_asker(deny_model)
    report.response = Answer(content=Parcel("model", WORK))
    with pytest.raises(ResultWithheld, match="target was invoked"):
        await host.delivery.invoke(cid, timer, report.identity, arguments, one_way=False)
    assert [ask.detail["direction"] for ask in questions] == ["value", "content"]
    rows = await host.ledger.run(lambda db: call_audit.read_trace(db, cid, now=0))
    assert any(row["kind"] == "deliver_content" and row["status"] == "denied" for row in rows)
    assert any(row["kind"] == "invoke" and row["reason"] == "result_withheld" for row in rows)


@pytest.mark.asyncio
async def test_oversize_result_is_withheld_after_invocation_without_a_retry_hint(env):
    host, cid, timer, report, questions = env
    timer.readable = report.readable = frozenset(f"resource-{n}" for n in range(300))
    report.response = Answer(content=Parcel("private business content", report.readable))
    with pytest.raises(ResultWithheld) as failure:
        await host.delivery.invoke(cid, timer, report.identity, Parcel(None, PUBLIC), one_way=False)
    assert str(failure.value) == "The target was invoked. Its result was withheld."
    assert failure.value.__context__ is None and not questions and len(report.inputs) == 1


@pytest.mark.asyncio
async def test_native_denial_unknown_adapter_and_forged_sender_never_reach_legacy_runs(
    env,
):
    host, cid, timer, report, _ = env
    report.native_allow = False
    with pytest.raises(DeliveryDenied):
        await host.delivery.invoke(cid, timer, report.identity, Parcel({}, PUBLIC), one_way=True)
    assert not report.inputs
    report.native_allow = True
    for sender, target in (
        (Endpoint(timer.identity, PRIVATE), report.identity),
        (timer, "neko:legacy_without_delivery_contract"),
    ):
        with pytest.raises(DeliveryDenied):
            await host.delivery.invoke(
                cid, sender, target, Parcel({}, PUBLIC), one_way=True
            )
    assert not report.inputs


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["sender", "recipient"])
async def test_replacement_must_obtain_its_own_session_approval(env, role):
    host, cid, timer, report, questions = env
    timer.readable = WORK
    await host.delivery.invoke(cid, timer, report.identity, Parcel("first", WORK), one_way=True)
    assert len(questions) == 1
    old = timer if role == "sender" else report
    host.delivery.unregister(old)
    new = Endpoint(old.identity, WORK)
    host.delivery.register(new)

    async def decline(_cid, ask):
        questions.append(ask)
        return "deny"

    host.permissions.set_asker(decline)
    with pytest.raises(DeliveryDenied):
        await host.delivery.invoke(cid, new if role == "sender" else timer, report.identity,
                                   Parcel("second", WORK), one_way=True)
    assert len(questions) == 2 and not new.inputs and len(report.inputs) == 1


@pytest.mark.asyncio
async def test_reregistering_same_object_while_asking_discards_late_answer(env):
    host, cid, timer, report, _ = env
    timer.readable = WORK
    asked, answer = asyncio.Event(), asyncio.Event()

    async def wait(_cid, ask):
        asked.set()
        await answer.wait()
        return "session"

    host.permissions.set_asker(wait)
    pending = asyncio.create_task(host.delivery.invoke(cid, timer, report.identity, Parcel("work", WORK), one_way=True))
    await asyncio.wait_for(asked.wait(), 5)
    host.delivery.unregister(report)
    host.delivery.register(report)
    answer.set()
    with pytest.raises(DeliveryDenied):
        await pending
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


@pytest.mark.parametrize("ending", ["deliver", "deny_destination", "cancel", "timeout"])
async def test_fetch_format_deliver_belongs_to_origin(env, ending):
    """A plugin sequences three steps. Only its trusted reader holds the fetched value."""
    host, root, timer, reader, questions = env
    child, _ = await host.ledger.create(
        lanlan_name="Lan", title="worker", dispatch_text="worker", source="dispatch", parent_id=root,
    )
    cid = child.id
    public = Parcel("blank", PUBLIC)
    source = Endpoint("builtin:data", WORK)
    formatter = Endpoint("dsh:formatter", PUBLIC)
    destination = Endpoint("session:destination", WORK)
    plugin = Endpoint("neko:workflow", PUBLIC)
    for endpoint in (source, formatter, destination, plugin):
        host.delivery.register(endpoint)

    hops, cached = [], {}
    entered = asyncio.Event()
    secret = "private source fixture"
    source.response = Answer(value=Parcel(secret, WORK))
    formatter.response = Answer(value=Parcel("[{}]", PUBLIC))

    async def invoke(sender, target, parcel, *, one_way=True):
        return await host.delivery.invoke(cid, sender, target.identity, parcel, one_way=one_way)

    async def read_or_deliver(ref, arguments):
        assert (ref.commission_id, ref.trace_id) == (cid, root)
        if arguments.data == "fetch":
            value = (await invoke(reader, source, public, one_way=False)).value
            cached[ref.commission_id] = value
            # Simulated usage receipt. No provider is contacted.
            await host.ledger.add_usage(commission_id=ref.commission_id, requests=0, tokens_in=7)
            hops.append("fetch")
            with pytest.raises(DeliveryDenied):
                await invoke(reader, formatter, value)
        else:
            value = cached.pop(ref.commission_id)
            await invoke(reader, destination, Parcel(arguments.data.format(value.data), value.scopes))
            hops.append("deliver")

    async def pipeline(ref, arguments):
        try:
            await invoke(plugin, reader, Parcel("fetch", PUBLIC))
            template = (await invoke(plugin, formatter, public, one_way=False)).value
            hops.append("format")
            await invoke(plugin, reader, template)
        finally:
            cached.pop(ref.commission_id, None)

    reader.trigger = read_or_deliver
    plugin.trigger = pipeline
    candidates = {e.identity for e in host.delivery.discover(reader, WORK)}
    assert destination.identity in candidates and formatter.identity not in candidates
    assert not host.delivery.discover(timer, WORK)
    impostor = Endpoint(timer.identity, WORK)
    inspected = []
    impostor.accepts = lambda scopes: inspected.append(scopes) or True
    assert not host.delivery.discover(impostor, PUBLIC)
    assert not inspected
    source.permits = lambda sender, *, one_way: sender == reader.identity and not one_way
    description = next(e for e in host.delivery.discover(reader, WORK) if e.identity == source.identity)
    assert description.can_call and not description.can_trigger
    assert not questions
    if ending == "deliver":
        for _ in range(2):
            assert await invoke(timer, plugin, public) is None
        assert hops == ["fetch", "format", "deliver"] * 2
        assert destination.inputs == [Parcel(f"[{secret}]", WORK)] * 2
        assert len(questions) == 2  # first restricted value, then the approved destination
    elif ending == "deny_destination":
        async def decide(_cid, question):
            questions.append(question)
            return "deny" if destination.identity in question.detail["title"] else "session"

        host.permissions.set_asker(decide)
        with pytest.raises(DeliveryDenied):
            await invoke(timer, plugin, public)
        assert hops == ["fetch", "format"] and not destination.inputs
    else:
        deadline = asyncio.timeout(None)

        async def wait_for_layout(ref, arguments):
            formatter.inputs.append(arguments)
            entered.set()
            await asyncio.Future()

        async def bounded():
            async with deadline:
                await invoke(timer, plugin, public)

        formatter.call = wait_for_layout
        work = asyncio.create_task(bounded())
        await asyncio.wait_for(entered.wait(), 5)
        if ending == "cancel":
            work.cancel()
            with pytest.raises(asyncio.CancelledError):
                await work
        else:
            deadline.reschedule(asyncio.get_running_loop().time())
            with pytest.raises(TimeoutError):
                await work
        assert hops == ["fetch"] and not destination.inputs
    assert not cached
    assert all(p == public for p in formatter.inputs)
    assert not timer.inputs
    rows = await host.ledger.run(lambda db: call_audit.read_trace(db, cid, now=0))
    assert all(r["trace_id"] == root for r in rows)
    assert all(r["commission_id"] == cid for r in rows if r["kind"] != "commission")
    assert secret not in json.dumps(rows) and not any(r["status"] == "running" for r in rows)
    if ending in ("cancel", "timeout"):
        assert any(r["status"] == "cancelled" and r["target"] == formatter.identity for r in rows)
    by_id = {r["call_id"]: r for r in rows}
    initiated = [r for r in rows if r["target"] == source.identity and r["kind"] == "invoke"]
    assert initiated and all(by_id[r["parent_call_id"]]["target"] == reader.identity for r in initiated)
    child_usage = await host.ledger.get(cid)
    assert child_usage.tokens_in == 7 * hops.count("fetch") and child_usage.requests == 0
    origin = await host.ledger.get(root)
    assert origin.tokens_in == 7 * hops.count("fetch") and origin.requests == 0



async def test_discovery_uses_the_processing_principal_and_inherited_scopes(env):
    host, cid, timer, reader, _ = env
    public_only = Endpoint("dsh:layout", PUBLIC)
    host.delivery.register(public_only)
    timer.readable = WORK

    def broken(*_args, **_kwargs):
        raise RuntimeError("private registration detail")

    faulty = Endpoint("dsh:broken", WORK)
    faulty.permits = broken
    host.delivery.register(faulty)
    assert faulty.identity not in {item.identity for item in host.delivery.discover(timer, WORK)}

    for broken_mode in (False, True):
        def partial_policy(_sender, *, one_way):
            if one_way == broken_mode:
                raise RuntimeError("private policy failure")
            return True

        faulty.permits = partial_policy
        entry, = [entry for entry in host.delivery.discover(timer, WORK) if entry.identity == faulty.identity]
        assert (entry.can_trigger, entry.can_call) == (not broken_mode, broken_mode)

    async def discover(ref, arguments):
        entries = {item.identity for item in host.delivery.discover(reader, PUBLIC)}
        assert public_only.identity not in entries and reader.identity in entries
        assert not host.delivery.discover(timer, PUBLIC)
        host.permissions.end_run(cid)
        assert not host.delivery.discover(reader, PUBLIC)

    reader.trigger = discover
    await host.delivery.invoke(cid, timer, reader.identity, Parcel("fixture", WORK), one_way=True)
