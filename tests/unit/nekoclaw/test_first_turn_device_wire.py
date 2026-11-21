"""Exercise the first HTTP request actually received by a local stand-in for the model.

Set NEKOCLAW_TEST_DSH_BINARY to the pinned dsh executable to include its real
ACP, MCP and OpenAI adapters. Both cases use an isolated home and loopback.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from config import nekoclaw_settings
from config.prompts.prompts_nekoclaw_builtin import BUILTIN_GROUP_GUIDANCE
from nekoclaw.builtin.provider import BuiltinToolProvider
from nekoclaw.dsh.driver import DshRuntime
from nekoclaw.dsh.profile import DshLaunch
from nekoclaw.runtime.core import InProcessRuntime
from nekoclaw.runtime.protocol import GatewaySpec, ModelConfig
from tests.unit.nekoclaw.fake_openai_server import FakeOpenAIServer, sse_finish, sse_text, sse_tool_call, sse_usage
from tests.unit.nekoclaw.fakes import Recorder, make_spec
from tests.unit.nekoclaw.host_fakes import make_host


@pytest.mark.parametrize("backend", ["builtin", "dsh"])
async def test_device_schemas_reach_the_model_and_leave_on_session_reuse(tmp_path, monkeypatch, backend):
    monkeypatch.setattr(nekoclaw_settings, "FIRST_TURN_DEVICE_TOOLS", True)
    events = Recorder()
    if backend == "dsh":
        binary = os.getenv("NEKOCLAW_TEST_DSH_BINARY")
        if not binary:
            pytest.skip("Set NEKOCLAW_TEST_DSH_BINARY to measure the pinned dsh locally")
        runtime = DshRuntime(events, launch=lambda: DshLaunch(home=tmp_path / "home", executable=Path(binary)))
    else:
        runtime = InProcessRuntime(events)
    provider = BuiltinToolProvider(state=lambda group: (True, ""))
    host = make_host(tmp_path, builtins=provider, plugins_enabled=lambda: False)
    baseline = None
    try:
        url = await host.gateway.start()
        with FakeOpenAIServer(lambda body: [sse_text("Done."), sse_finish()]) as server:
            model = ModelConfig(model="local-fixture", base_url=server.base_url, api_key="test-only",
                                auto_extra_body=False, supports_images=True)
            if backend == "dsh":
                await runtime.prepare(model)
                assert runtime.supports_device_schemas(model)
            for i, groups in enumerate([(), ("browser",), (), ("keyboard_mouse",), ("keyboard_mouse", "browser")]):
                commission_id = f"commission-{i}"
                token = host.gateway.issue(commission_id, supports_images=True, first_turn_groups=groups)
                spec = make_spec(tmp_path, model=model, gateway=GatewaySpec(url=url, token=token))
                start = len(events.events)
                await runtime.start(spec)
                opened = await events.wait_for(lambda e: e["type"] in ("started", "error"), timeout=90, start=start)
                assert opened["type"] == "started", opened
                requests = len(server.requests)
                await runtime.prompt(spec.session_id, "Reply with Done.", opening=True)
                await events.wait_ended(spec.session_id, timeout=40, start=start)
                assert len(server.requests) == requests + 1
                tools = server.requests[-1]["tools"]
                if not groups:
                    if baseline is None:
                        baseline = tools
                    assert tools == baseline
                functions = {tool["function"]["name"].split("__")[-1]: tool["function"] for tool in tools}
                expected = {item.tool_name: item for item in provider.available() if item.group in groups}
                assert {name for name in functions if name == "computer" or name.startswith("browser_")} == set(expected)
                for name, item in expected.items():
                    assert functions[name]["parameters"]["properties"] == item.input_schema["properties"]
                for group, guidance in BUILTIN_GROUP_GUIDANCE.items():
                    assert sum(guidance in tool["description"] for tool in functions.values()) == int(group in groups)
                assert {"capability_call", "capability_search"} <= functions.keys()
                start = len(events.events)
                await runtime.close(spec.session_id)
                await events.wait_for(lambda e: e["type"] == "ended" and e["reason"] == "closed", timeout=15, start=start)
                host.gateway.revoke(commission_id)
            if backend == "builtin":
                snapshot = json.loads((Path(spec.session_dir) / "system.json").read_text(encoding="utf-8"))
                assert not any(tool["name"] in expected for tool in snapshot["tools"])
    finally:
        await runtime.shutdown()
        await host.shutdown()


@pytest.mark.parametrize("browser_is_human", [True, False], ids=["two-user-instructions", "synthetic-browser"])
async def test_builtin_queued_device_instructions_survive_summary_then_overflow_retry(tmp_path, browser_is_human):
    events = Recorder()
    runtime = InProcessRuntime(events)
    provider = BuiltinToolProvider(state=lambda group: (True, ""))
    host = make_host(tmp_path, builtins=provider, plugins_enabled=lambda: False)
    long_reply = " ".join(f"step{i}" for i in range(1600))
    short_reply = " ".join(f"tail{i}" for i in range(500))
    summary = [sse_text("## Current Work\nFinished the previous steps."), sse_finish()]
    replies = iter([
        *([[sse_text(long_reply), sse_finish()]] * 3),
        [sse_text(short_reply), sse_finish()], [sse_text(short_reply), sse_finish(), sse_usage(29_000, 1_000)],
        summary,
        (400, {"error": {"message": "This model's maximum context length is 32000 tokens.",
                         "type": "invalid_request_error", "code": "context_length_exceeded"}}),
        summary,
        [sse_text(long_reply), sse_finish(), sse_usage(29_000, 1_000)], summary,
        [sse_text("Done."), sse_finish()],
    ])
    try:
        url = await host.gateway.start()
        token = host.gateway.issue("wire", supports_images=True, first_turn_groups=("browser", "keyboard_mouse"))
        with FakeOpenAIServer(lambda _: next(replies)) as server:
            model = ModelConfig(model="local-fixture", base_url=server.base_url, api_key="test-only",
                                auto_extra_body=False, supports_images=True, context_window=32_000, max_output_tokens=2_000)
            spec = make_spec(tmp_path, model=model, gateway=GatewaySpec(url=url, token=token))
            await runtime.start(spec)
            for i, prompt in enumerate(["Open the browser", "Step two.", "Step three.", "Step four.", "List browser tabs"]):
                start = len(events.events)
                await runtime.prompt(spec.session_id, prompt, opening=i == 0)
                ended = await events.wait_ended(spec.session_id, start=start)
                assert ended["reason"] == "completed", ended
            # Both steering messages are drained before one model request.
            # The previous, answered browser instruction is still in history.
            start = len(events.events)
            await runtime.prompt(spec.session_id, "Continue.", human=False)
            await runtime.steer(spec.session_id, "List browser tabs", human=browser_is_human)
            await runtime.steer(spec.session_id, "Also take a desktop screenshot")
            ended = await events.wait_ended(spec.session_id, start=start)
            assert ended["reason"] == "completed", ended
            assert len(events.of("compacted")) == 2
            assert len(server.requests) == 9
            initial, refused, retried = [server.requests[i]["tools"] for i in (0, 6, 8)]
            expected = [t for t in initial if browser_is_human or not t["function"]["name"].startswith("browser_")]
            assert {"computer", "browser_list_pages"} <= {t["function"]["name"] for t in initial}
            assert refused == retried == expected
            # The reply consumed the current instruction. An automatic wake
            # brings no fresh intent: the next unused cycle can fold again.
            start = len(events.events)
            await runtime.prompt(spec.session_id, "Continue.", human=False)
            await events.wait_ended(spec.session_id, start=start)
            assert len(events.of("compacted")) == 3 and len(server.requests) == 11
            final_names = {t["function"]["name"] for t in server.requests[-1]["tools"]}
            assert "computer" not in final_names and not any(name.startswith("browser_") for name in final_names)
    finally:
        await runtime.shutdown()
        await host.shutdown()


@pytest.mark.parametrize("scenario", ["manual-explicit-intent", "manual-queued-intents", "automatic-rediscovery", "truncated-summary"])
async def test_builtin_model_receives_folded_and_restored_browser_schema(tmp_path, monkeypatch, scenario):
    from utils.tokenize import count_tokens

    queued = scenario == "manual-queued-intents"
    groups = ("browser", "keyboard_mouse") if queued else ("browser",)
    automatic = scenario in ("automatic-rediscovery", "truncated-summary")
    rediscover = scenario == "automatic-rediscovery"
    failed = scenario == "truncated-summary"
    provider = BuiltinToolProvider(state=lambda group: (True, ""))
    host = make_host(tmp_path, builtins=provider, plugins_enabled=lambda: False)
    events = Recorder()
    runtime = InProcessRuntime(events)
    listings = []
    original_list = host.gateway.tool_definitions

    async def record_list(grant, **options):
        listings.append((options.get("compactions"), sorted(options.get("wanted") or ())))
        return await original_list(grant, **options)

    monkeypatch.setattr(host.gateway, "tool_definitions", record_list)
    long_answer = " ".join(f"step{i}" for i in range(1_600))
    ordinary_reply = [sse_text(long_answer), sse_finish()]
    replies = [ordinary_reply] * 2 + [
        ordinary_reply + ([sse_usage(29_000, 1_000)] if automatic else []),
        [sse_text("## Current Work\nOpen the browser."), sse_finish("length" if failed else "stop")],
        [sse_text("Open the browser."), sse_finish()], [sse_text("Done."), sse_finish()],
    ]
    if rediscover:
        replies.append([sse_tool_call("capability_search", {"group": "browser"}), sse_finish("tool_calls")])
    replies.append([sse_text("Done."), sse_finish()])
    script = iter(replies)
    try:
        url = await host.gateway.start()
        token = host.gateway.issue("wire", supports_images=True, first_turn_groups=groups)
        with FakeOpenAIServer(lambda _: next(script)) as server:
            model = ModelConfig(model="local-fixture", base_url=server.base_url, api_key="test-only",
                                auto_extra_body=False, supports_images=True, context_window=32_000, max_output_tokens=2_000)
            spec = make_spec(tmp_path, model=model, gateway=GatewaySpec(url=url, token=token))
            await runtime.start(spec)

            async def prompt(message, **kwargs):
                start = len(events.events)
                await runtime.prompt(spec.session_id, message, **kwargs)
                await events.wait_ended(spec.session_id, start=start)

            await prompt("Open the browser", opening=True)
            start = len(events.events)
            await runtime.compact(spec.session_id)
            await events.wait_ended(spec.session_id, start=start)
            assert not events.of("compacted") and listings == [(None, [])]
            await prompt("Summarize step one.")
            await prompt("Summarize step two.")
            if not automatic:
                start = len(events.events)
                await runtime.compact(spec.session_id)
                await events.wait_ended(spec.session_id, start=start)
            await prompt("Continue.")
            assert len(events.of("compacted")) == 1
            await prompt("Open the browser", human=False)
            if queued:
                start = len(events.events)
                await runtime.prompt(spec.session_id, "Continue.", human=False)
                await runtime.steer(spec.session_id, "List browser tabs")
                await runtime.steer(spec.session_id, "Also take a desktop screenshot")
                await events.wait_ended(spec.session_id, start=start)
            else:
                await prompt("Continue." if rediscover else "List browser tabs")
            tools = [request["tools"] for request in server.requests]
            assert len(tools) == (8 if rediscover else 7)
            if failed:
                # Forced fit still records the shortened conversation, but
                # a truncated checkpoint cannot withdraw a device schema.
                assert listings == [(None, [])] and all(row == tools[0] for row in tools)
                return
            assert tools[0] == tools[1] == tools[2] == tools[3] == tools[-1]
            assert tools[4] == tools[5]
            if rediscover:
                assert tools[6] == tools[5]
            devices = {c.tool_name for c in provider.available() if c.group in groups}
            def names(rows):
                return {d["function"]["name"] for d in rows}

            assert names(tools[0]) - names(tools[4]) == devices
            assert {"capability_search", "capability_call"} <= names(tools[4])
            assert not any(BUILTIN_GROUP_GUIDANCE["browser"] in tool["function"]["description"] for tool in tools[4])
            assert listings == [(None, []), (1, []), (None, [] if rediscover else sorted(groups))]
            encoded = [json.dumps(row, ensure_ascii=False, separators=(",", ":")) for row in tools]
            print("builtin tools (count, bytes, estimated tokens)",
                  [(len(row), len(raw.encode()), count_tokens(raw)) for row, raw in zip(tools, encoded, strict=True)],
                  "tools/list", listings)
    finally:
        await runtime.shutdown()
        await host.shutdown()
