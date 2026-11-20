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
from tests.unit.nekoclaw.fake_openai_server import FakeOpenAIServer, sse_finish, sse_text
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
