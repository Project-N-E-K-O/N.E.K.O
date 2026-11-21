"""Observe the pinned ACP executable's native schemas on a real loopback model request."""

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from config import nekoclaw_settings
from config.prompts.prompts_nekoclaw_builtin import BUILTIN_GROUP_GUIDANCE
from nekoclaw.builtin.provider import BuiltinToolProvider
from nekoclaw.dsh import profile
from nekoclaw.dsh.driver import DshRuntime, _Session
from nekoclaw.dsh.host_tools import ON_ANSWERED
from nekoclaw.runtime.protocol import GatewaySpec, ModelConfig
from nekoclaw.runtime.tools.gateway import GatewayClient
from tests.unit.nekoclaw.fake_openai_server import FakeOpenAIServer, sse_finish, sse_text, sse_tool_call, sse_usage
from tests.unit.nekoclaw.fakes import Recorder, make_spec
from tests.unit.nekoclaw.host_fakes import make_host


async def test_device_refresh_does_not_consume_a_new_identical_instruction_or_rewind_epoch(tmp_path, monkeypatch):
    runtime = DshRuntime(Recorder(), launch=lambda: pytest.fail("No dsh process is required"))
    session = _Session(make_spec(tmp_path, gateway=GatewaySpec(url="http://127.0.0.1", token="fixture")),
                       asyncio.get_running_loop().create_future())
    process = SimpleNamespace(token="process", native_device_schemas=True, by_dsh_id={"dsh": session})
    runtime._processes[object()] = process
    session.process = process
    # An active run accepts further instructions without starting a new task.
    session.run = asyncio.current_task()
    session.in_turn = True
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def list_tools(client, *, compactions, wanted, idle_device_cycle):
        calls.append((compactions, wanted))
        entered.set()
        await release.wait()
        client.commission_tools = {"browser_navigate_page"}

    monkeypatch.setattr(GatewayClient, "list_tools", list_tools)
    runtime._queue(session, "Open the browser", human=True)
    await runtime._take_input(session, 0)
    first = asyncio.create_task(runtime._device_view("process", "dsh", 2, True))
    await entered.wait()
    runtime._queue(session, "Open the browser", human=True)
    runtime._queue(session, "Take a desktop screenshot", human=True)
    await runtime._take_input(session, 0)
    stale = asyncio.create_task(runtime._device_view("process", "dsh", 1, True))
    release.set()
    assert await first == await stale == ["browser_navigate_page"]
    assert session.device_intents == {"browser": 2, "keyboard_mouse": 3}
    # A receipt from the earlier turn cannot settle the newer instructions.
    session.device_completed_input = 1
    await runtime._device_view("process", "dsh", 4, True, idle_cycle=True, shown=("browser",))
    assert session.device_intents == {"browser": 2, "keyboard_mouse": 3}
    await runtime._device_view("process", "dsh", 5, True, shown=("browser",), same_turn=True)
    assert calls == [(2, ("browser",)), (4, ("browser", "keyboard_mouse")), (5, ("keyboard_mouse",))]
    assert session.device_intents == {"keyboard_mouse": 3}
    await runtime._device_view("process", "dsh", 6, True, shown=("keyboard_mouse",), same_turn=True)
    assert session.device_intents == {}


async def test_device_input_from_wait_activates_only_when_its_answer_is_delivered(tmp_path):
    runtime = DshRuntime(Recorder(), launch=lambda: pytest.fail("No dsh process is required"))
    session = _Session(make_spec(tmp_path, gateway=GatewaySpec(url="http://127.0.0.1", token="fixture")),
                       asyncio.get_running_loop().create_future())
    session.process = SimpleNamespace(native_device_schemas=True)
    session.run = asyncio.current_task()
    session.in_turn = True
    runtime._sessions[session.id] = session
    runtime._queue(session, "Open the browser", human=True)
    callbacks = []
    context = ON_ANSWERED.set(callbacks)
    try:
        for delivered in (False, True):
            assert await runtime._take_input(session, 0) == ["Open the browser"]
            assert session.device_intents == {}
            callbacks.pop()(delivered)
        assert session.device_intents == {"browser": 1}
    finally:
        ON_ANSWERED.reset(context)


@pytest.mark.parametrize(
    "scenario", ["search", "new_instruction", "queued", "queued_synthetic", "queued_abort", "synthetic", "failure",
                 "consecutive", "incomplete_profile"]
)
async def test_only_successful_dsh_compaction_changes_the_next_request(tmp_path, monkeypatch, record_property, scenario):
    binary = os.getenv("NEKOCLAW_TEST_DSH_BINARY")
    if not binary:
        pytest.skip("Set NEKOCLAW_TEST_DSH_BINARY to run the pinned dsh locally")
    monkeypatch.setattr(nekoclaw_settings, "FIRST_TURN_DEVICE_TOOLS", True)
    build_patch = profile.build_patch

    def compact_short_fixture(**kwargs):
        patch = build_patch(**kwargs)
        # Let a short local transcript qualify for the real summarizer.
        row = next(row for row in patch if row.get("id") == "compaction-basic")
        row["config"]["retainTokens"] = 1
        return patch

    monkeypatch.setattr(profile, "build_patch", compact_short_fixture)
    recorder = Recorder()
    runtime = DshRuntime(recorder, launch=lambda: profile.DshLaunch(home=tmp_path / "home", executable=Path(binary)))
    provider = BuiltinToolProvider(state=lambda group: (True, ""))
    host = make_host(tmp_path, builtins=provider, plugins_enabled=lambda: False)
    seen = []
    snapshots = []
    list_calls = []
    control_calls = []
    before_request, resume_request = asyncio.Event(), asyncio.Event()
    original_view = runtime._endpoint._device_view

    async def count_control_calls(process, session, epoch, refresh, idle_cycle, shown, same_turn, registered):
        control_calls.append((epoch, refresh, idle_cycle))
        if phase == "queued_abort":
            before_request.set()
            await resume_request.wait()
        return await original_view(process, session, epoch, refresh, idle_cycle, shown, same_turn, registered)

    monkeypatch.setattr(runtime._endpoint, "_device_view", count_control_calls)

    original_list = GatewayClient.list_tools

    async def count_gateway_lists(client, **kwargs):
        list_calls.append(kwargs)
        return await original_list(client, **kwargs)

    monkeypatch.setattr(GatewayClient, "list_tools", count_gateway_lists)
    phase = "initial"
    search = "mcp__gateway__capability_search"
    called = False

    def reply(body):
        nonlocal called
        schemas = body["tools"]
        names = [tool["function"]["name"] for tool in schemas]
        summary = "Output only the checkpoint text" in json.dumps(body["messages"][-1])
        size = len(json.dumps(schemas, ensure_ascii=False, separators=(",", ":")).encode())
        seen.append((phase, summary, names, size))
        snapshots.append({"phase": phase, "summary": summary, "tools": names, "schema_bytes": size})
        if summary:
            if scenario == "failure":
                return [sse_finish(), sse_usage(100, 0)]
            if scenario == "consecutive":
                # Shrink a real checkpoint twice while the first replacement
                # still exceeds the target. The stock retry count is one.
                n = sum(sample["summary"] for sample in snapshots)
                return [sse_text("Prior words. " * {1: 250, 2: 30}.get(n, 1)), sse_finish(), sse_usage(100, 10)]
            return [sse_text("Checkpoint: the prior task finished."), sse_finish(), sse_usage(100, 10)]
        if phase == "search" and not called:
            called = True
            return [sse_tool_call(search, {"group": "browser"}), sse_finish("tool_calls"), sse_usage(100, 10)]
        # Force threshold pressure using the same model-reported usage dsh sees in production.
        usage = 31000 if phase == "pressure" else 100
        if scenario == "consecutive":
            # Pressure on the next turn, then a provider-declared overflow on
            # its first ordinary request. The retry reuses the frozen header.
            ordinary = sum(not sample["summary"] for sample in snapshots)
            if ordinary == 2:
                snapshots[-1]["http_status"] = 400
                return 400, {"error": {"message": "This model's maximum context length is 10000 tokens.",
                                       "type": "invalid_request_error", "code": "context_length_exceeded"}}
            usage = 9000 if ordinary in (1, 3) else 100
        return [sse_text("Done."), sse_finish(), sse_usage(usage, 10)]

    async def turn(text, *, opening=False, human=True):
        start = len(recorder.events)
        await runtime.prompt(spec.session_id, text, opening=opening, human=human)
        result = await recorder.wait_ended(spec.session_id, timeout=40, start=start)
        assert result["reason"] == "completed", result

    try:
        url = await host.gateway.start()
        with FakeOpenAIServer(reply) as model:
            config = ModelConfig(model="fixture", base_url=model.base_url, api_key="test-only", auto_extra_body=False,
                                 supports_images=True, context_window=32000, max_output_tokens=1024)
            if scenario == "consecutive":
                config = config.model_copy(update={"context_window": 10000, "compact_at": 3500, "max_output_tokens": 1000})
            if scenario == "incomplete_profile":
                (tmp_path / "home" / "profiles" / profile.PROFILE).mkdir(parents=True)
            await runtime.prepare(config)
            if scenario == "incomplete_profile":
                # dsh repaired the recipe, but the running guard was installed
                # before the repair. Only the next launch may advertise it.
                assert profile.uses_stock_tool_presentation(tmp_path / "home")
                assert not runtime.supports_device_schemas(config)
                return
            assert runtime.supports_device_schemas(config)
            queued = scenario in ("queued", "queued_synthetic", "queued_abort")
            groups = ("browser", "keyboard_mouse") if queued else ("browser",)
            token = host.gateway.issue("commission", supports_images=True, first_turn_groups=groups)
            spec = make_spec(tmp_path, model=config, gateway=GatewaySpec(url=url, token=token))
            await runtime.start(spec)
            await recorder.wait_for(lambda event: event["type"] == "started")
            if scenario == "consecutive":
                await turn("Open the browser. " + "Sample. " * 3000, opening=True)
                phase = "second"
                await turn("Open the browser, then reply Done.")
                assert control_calls == [(2, True, True), (3, True, False)]
                assert len(list_calls) == 2
                assert list_calls[0]["wanted"] == ("browser",) and list_calls[0]["idle_device_cycle"] is True
                assert [("summary" if sample["summary"] else sample.get("http_status", 200))
                        for sample in snapshots if sample["phase"] == "second"] == ["summary", "summary", 400, "summary", 200]
                regular = [names for stage, summary, names, _ in seen if stage == "second" and not summary]
                assert len(regular) == 2 and all("mcp__gateway__browser_navigate_page" in names for names in regular)
                # The successful retry showed the instruction and its schemas.
                # This host-generated continuation must not renew that intent.
                phase = "synthetic"
                await turn("Open the browser, then reply Done.", human=False)
                ordinary = [names for stage, summary, names, _ in seen if stage == "synthetic" and not summary]
                assert ordinary and all("mcp__gateway__browser_navigate_page" not in names for names in ordinary)
                return
            await turn("Reply with Done.", opening=True)
            phase = "pressure"
            await turn("Read this synthetic history. " + "Sample. " * 6000)
            phase = "after"
            await turn("Now reply with Done.")
            assert any(summary for _, summary, _, _ in seen), seen
            browser = "mcp__gateway__browser_navigate_page"
            if scenario == "failure":
                assert browser in seen[-1][2]
                assert list_calls == [] and control_calls == []
                return
            phase = scenario
            if queued:
                computer = "mcp__gateway__computer"
                assert browser not in seen[-1][2] and computer not in seen[-1][2]
                start = len(recorder.events)
                await runtime.prompt(spec.session_id, "帮我打开浏览器")
                if scenario == "queued_abort":
                    await asyncio.wait_for(before_request.wait(), 10)
                    session = runtime._sessions[spec.session_id]
                    accepted = session.taken
                    await runtime.steer(spec.session_id, "再打开浏览器")
                    await runtime.follow_up(spec.session_id, "也截个桌面图")
                    await runtime.abort(spec.session_id)
                    await runtime.steer(spec.session_id, "也截个桌面图")
                    resume_request.set()
                    result = await recorder.wait_ended(spec.session_id, timeout=40, start=start)
                    assert result["reason"] == "aborted", result
                    assert session.device_intents == {"browser": accepted}
                    phase = "after_abort"
                    await turn("做完后告诉我")
                    assert browser in seen[-1][2] and computer not in seen[-1][2]
                    return
                await runtime.steer(spec.session_id, "也截个桌面图", human=scenario == "queued")
                await runtime.follow_up(spec.session_id, "做完后告诉我")
                result = await recorder.wait_ended(spec.session_id, timeout=40, start=start)
                assert result["reason"] == "completed", result
                ordinary = [names for stage, summary, names, _ in seen if stage == scenario and not summary]
                assert len(ordinary) == 1 and browser in ordinary[0]
                assert (computer in ordinary[0]) == (scenario == "queued")
                assert len(list_calls) == 2
                if scenario == "queued_synthetic":
                    phase = "human_desktop"
                    await turn("也截个桌面图")
                    assert computer in seen[-1][2] and len(list_calls) == 3
                return
            if scenario == "synthetic":
                before = len(list_calls)
                await turn("Open the browser.", human=False)
                assert not any(summary for stage, summary, _, _ in seen if stage == "synthetic")
                assert browser not in seen[-1][2] and len(list_calls) == before
                return
            if scenario == "search":
                await turn("Use the catalog to retrieve the previous tool group.")
                assert called
            else:
                await turn("Open the browser.")
            assert browser in seen[0][2]
            assert all(browser not in names for stage, summary, names, _ in seen if stage == "after" and not summary)
            assert browser in seen[-1][2], seen
            for body, (_, summary, names, _) in zip(model.requests, seen, strict=True):
                if not summary:
                    assert search in names
                    assert any(BUILTIN_GROUP_GUIDANCE["browser"] in tool["function"]["description"]
                               for tool in body["tools"]) == (browser in names)
            assert model.requests[0]["tools"] == model.requests[-1]["tools"]
            assert len(list_calls) == 2
    finally:
        record_property("request_tools", json.dumps(snapshots))
        record_property("gateway_lists", json.dumps(list_calls))
        record_property("control_calls", json.dumps(control_calls))
        await runtime.shutdown()
        await host.shutdown()
