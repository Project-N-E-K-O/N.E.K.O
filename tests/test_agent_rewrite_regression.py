import ast
import asyncio
import json
from pathlib import Path

import pytest

from utils.config_manager import ConfigManager, get_config_manager


def _route_paths_from_decorators(py_file_path: str, target_name: str):
    source = Path(py_file_path).read_text(encoding="utf-8")
    tree = ast.parse(source)
    paths = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            func = decorator.func
            if not isinstance(func, ast.Attribute):
                continue
            if not isinstance(func.value, ast.Name) or func.value.id != target_name:
                continue
            if not decorator.args:
                continue
            first_arg = decorator.args[0]
            if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
                paths.add(first_arg.value)
    return paths


def _get_function_def(py_file_path: str, func_name: str):
    source = Path(py_file_path).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            return node
    raise AssertionError(f"function {func_name} not found in {py_file_path}")


def _gather_string_literals(node):
    values = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Constant) and isinstance(child.value, str):
            values.add(child.value)
    return values


def _contains_call(func_node, attr_name: str) -> bool:
    for child in ast.walk(func_node):
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
            if child.func.attr == attr_name:
                return True
    return False


def test_core_config_uses_agent_model_only():
    cfg = get_config_manager().get_core_config()
    assert "AGENT_MODEL" in cfg
    assert "AGENT_MODEL_URL" in cfg
    assert "AGENT_MODEL_API_KEY" in cfg

    legacy_keys = [k for k in cfg.keys() if k.startswith("COMPUTER_USE_")]
    assert legacy_keys == []


def test_agent_server_legacy_endpoints_removed():
    paths = _route_paths_from_decorators("agent_server.py", "app")
    assert "/process" not in paths
    assert "/plan" not in paths
    assert "/analyze_and_plan" not in paths


def test_main_agent_router_legacy_endpoints_removed():
    paths = _route_paths_from_decorators("main_routers/agent_router.py", "router")
    assert "/api/agent/task_status" not in paths
    assert "/api/agent/notify_task_result" not in paths


def test_main_agent_router_expected_proxy_endpoints_exist():
    paths = _route_paths_from_decorators("main_routers/agent_router.py", "router")
    for expected in {
        "/flags",
        "/health",
        "/tasks",
        "/tasks/{task_id}",
        "/computer_use/availability",
        "/browser_use/availability",
        "/mcp/availability",
    }:
        assert expected in paths


def test_agent_server_expected_event_driven_endpoints_exist():
    paths = _route_paths_from_decorators("agent_server.py", "app")
    for expected in {
        "/health",
        "/agent/flags",
        "/tasks",
        "/tasks/{task_id}",
        "/computer_use/availability",
        "/browser_use/availability",
    }:
        assert expected in paths


def test_agent_router_update_flags_keeps_user_plugin_forwarding():
    fn = _get_function_def("main_routers/agent_router.py", "update_agent_flags")
    literals = _gather_string_literals(fn)
    assert "user_plugin_enabled" in literals
    assert "/agent/flags" in literals


def test_agent_router_update_flags_has_safe_rollback_defaults():
    fn = _get_function_def("main_routers/agent_router.py", "update_agent_flags")
    required_keys = {
        "agent_enabled",
        "computer_use_enabled",
        "browser_use_enabled",
        "user_plugin_enabled",
    }

    found_rollback_dict = False
    for node in ast.walk(fn):
        if not isinstance(node, ast.Dict):
            continue
        key_values = set()
        all_false = True
        for key_node, value_node in zip(node.keys, node.values):
            if isinstance(key_node, ast.Constant) and isinstance(key_node.value, str):
                key_values.add(key_node.value)
            else:
                all_false = False
            if not (isinstance(value_node, ast.Constant) and value_node.value is False):
                all_false = False
        if required_keys.issubset(key_values) and all_false:
            found_rollback_dict = True
            break

    assert found_rollback_dict is True


def test_agent_router_command_syncs_core_flags_locally():
    fn = _get_function_def("main_routers/agent_router.py", "post_agent_command")
    assert _contains_call(fn, "update_agent_flags")


def test_agent_router_has_internal_analyze_request_endpoint():
    paths = _route_paths_from_decorators("main_routers/agent_router.py", "router")
    assert "/internal/analyze_request" in paths


def test_task_executor_format_messages_marks_latest_user_request():
    from brain.task_executor import DirectTaskExecutor

    executor = object.__new__(DirectTaskExecutor)
    conversation = [
        {"role": "user", "text": "帮我打开系统计算器"},
        {"role": "assistant", "text": "已经打开了"},
    ]
    output = executor._format_messages(conversation)
    assert "LATEST_USER_REQUEST: 帮我打开系统计算器" in output
    assert "assistant: 已经打开了" in output


def test_cross_server_analyze_request_no_http_fallback_endpoint():
    source = Path("main_logic/cross_server.py").read_text(encoding="utf-8")
    assert "/api/agent/internal/analyze_request" not in source


def test_is_agent_api_ready_allows_free_profile():
    manager = object.__new__(ConfigManager)
    manager.get_core_config = lambda: {"IS_FREE_VERSION": True}
    manager.get_model_api_config = lambda _model_type: {
        "model": "free-agent-model",
        "base_url": "https://lanlan.tech/text/v1",
        "api_key": "free-access",
    }

    ready, reasons = manager.is_agent_api_ready()
    assert ready is True
    assert len(reasons) == 0


@pytest.mark.parametrize(
    ("agent_api", "expected_reason"),
    [
        ({"model": "", "base_url": "https://u", "api_key": "k"}, "Agent 模型未配置"),
        ({"model": "m", "base_url": "", "api_key": "k"}, "Agent API URL 未配置"),
        ({"model": "m", "base_url": "https://u", "api_key": ""}, "Agent API Key 未配置或不可用"),
        ({"model": "m", "base_url": "https://u", "api_key": "free-access"}, "Agent API Key 未配置或不可用"),
    ],
)
def test_is_agent_api_ready_reports_missing_fields(agent_api, expected_reason):
    manager = object.__new__(ConfigManager)
    manager.get_core_config = lambda: {"IS_FREE_VERSION": False}
    manager.get_model_api_config = lambda _model_type: agent_api

    ready, reasons = manager.is_agent_api_ready()
    assert ready is False
    assert expected_reason in reasons


def test_get_model_api_config_agent_uses_agent_fields_without_custom_switch():
    manager = object.__new__(ConfigManager)
    manager.get_core_config = lambda: {
        "ENABLE_CUSTOM_API": False,
        "AGENT_MODEL": "agent-model",
        "AGENT_MODEL_URL": "https://agent.example/v1",
        "AGENT_MODEL_API_KEY": "agent-key",
        "OPENROUTER_API_KEY": "fallback-openrouter-key",
        "OPENROUTER_URL": "https://openrouter.example/v1",
    }

    cfg = manager.get_model_api_config("agent")
    assert cfg["is_custom"] is True
    assert cfg["model"] == "agent-model"
    assert cfg["base_url"] == "https://agent.example/v1"
    assert cfg["api_key"] == "agent-key"


def test_get_model_api_config_agent_falls_back_to_assist_when_agent_fields_incomplete():
    manager = object.__new__(ConfigManager)
    manager.get_core_config = lambda: {
        "ENABLE_CUSTOM_API": False,
        "AGENT_MODEL": "agent-model",
        "AGENT_MODEL_URL": "",
        "AGENT_MODEL_API_KEY": "agent-key",
        "OPENROUTER_API_KEY": "fallback-openrouter-key",
        "OPENROUTER_URL": "https://openrouter.example/v1",
    }

    cfg = manager.get_model_api_config("agent")
    assert cfg["is_custom"] is False
    assert cfg["model"] == "agent-model"
    assert cfg["base_url"] == "https://openrouter.example/v1"
    assert cfg["api_key"] == "fallback-openrouter-key"


def test_get_model_api_config_rejects_unknown_model_type():
    manager = object.__new__(ConfigManager)
    manager.get_core_config = lambda: {}

    with pytest.raises(ValueError):
        manager.get_model_api_config("unknown_type")


def test_get_model_api_config_realtime_fallback_uses_core_and_api_type():
    manager = object.__new__(ConfigManager)
    manager.get_core_config = lambda: {
        "ENABLE_CUSTOM_API": False,
        "CORE_MODEL": "core-model",
        "CORE_API_KEY": "core-key",
        "CORE_URL": "https://core.example/v1",
        "CORE_API_TYPE": "qwen",
    }

    cfg = manager.get_model_api_config("realtime")
    assert cfg["is_custom"] is False
    assert cfg["model"] == "core-model"
    assert cfg["api_key"] == "core-key"
    assert cfg["base_url"] == "https://core.example/v1"
    assert cfg["api_type"] == "qwen"


def test_get_model_api_config_realtime_custom_sets_local_api_type():
    manager = object.__new__(ConfigManager)
    manager.get_core_config = lambda: {
        "ENABLE_CUSTOM_API": True,
        "REALTIME_MODEL": "rt-model",
        "REALTIME_MODEL_URL": "http://localhost:1234/v1",
        "REALTIME_MODEL_API_KEY": "rt-key",
    }

    cfg = manager.get_model_api_config("realtime")
    assert cfg["is_custom"] is True
    assert cfg["model"] == "rt-model"
    assert cfg["base_url"] == "http://localhost:1234/v1"
    assert cfg["api_key"] == "rt-key"
    assert cfg["api_type"] == "local"


def test_get_model_api_config_tts_custom_prefers_qwen_profile(monkeypatch):
    manager = object.__new__(ConfigManager)
    manager.get_core_config = lambda: {
        "ENABLE_CUSTOM_API": False,
        "CORE_MODEL": "core-model",
        "ASSIST_API_KEY_QWEN": "qwen-key",
        "OPENROUTER_URL": "https://fallback.example/v1",
    }
    monkeypatch.setattr(
        "utils.config_manager.get_assist_api_profiles",
        lambda: {"qwen": {"OPENROUTER_URL": "https://qwen.example/v1"}},
    )

    cfg = manager.get_model_api_config("tts_custom")
    assert cfg["is_custom"] is False
    assert cfg["api_key"] == "qwen-key"
    assert cfg["base_url"] == "https://qwen.example/v1"






def test_publish_analyze_and_plan_event_writes_expected_payload(monkeypatch):
    from main_logic.agent_bridge import publish_analyze_and_plan_event

    class DummyWriter:
        def __init__(self):
            self.buffer = b""

        def write(self, data):
            self.buffer += data

        async def drain(self):
            return None

        def close(self):
            return None

        async def wait_closed(self):
            return None

    writer = DummyWriter()

    async def fake_open_connection(host, port):
        assert host == "127.0.0.1"
        assert isinstance(port, int)
        return object(), writer

    monkeypatch.setattr("main_logic.agent_bridge.asyncio.open_connection", fake_open_connection)

    messages = [{"role": "user", "content": "hello"}]
    ok = asyncio.run(publish_analyze_and_plan_event(messages, "LanLan"))
    assert ok is True
    payload = json.loads(writer.buffer.decode("utf-8").strip())
    assert payload["type"] == "analyze_and_plan"
    assert payload["messages"] == messages
    assert payload["lanlan_name"] == "LanLan"


def test_publish_analyze_and_plan_event_returns_false_on_error(monkeypatch):
    from main_logic.agent_bridge import publish_analyze_and_plan_event

    async def fake_open_connection(_host, _port):
        raise OSError("down")

    monkeypatch.setattr("main_logic.agent_bridge.asyncio.open_connection", fake_open_connection)
    ok = asyncio.run(publish_analyze_and_plan_event([], "LanLan"))
    assert ok is False


def test_agent_event_bus_publish_session_event_without_bridge_returns_false():
    import main_logic.agent_event_bus as bus

    bus.set_main_bridge(None)
    ok = asyncio.run(bus.publish_session_event({"type": "turn_end"}))
    assert ok is False


def test_agent_event_bus_publish_session_event_with_bridge(monkeypatch):
    import main_logic.agent_event_bus as bus

    class DummyBridge:
        def __init__(self):
            self.events = []

        async def publish_session_event(self, event):
            self.events.append(event)
            return True

    bridge = DummyBridge()
    bus.set_main_bridge(bridge)
    event = {"type": "turn_end", "session_id": "s1"}
    ok = asyncio.run(bus.publish_session_event(event))
    assert ok is True
    assert bridge.events == [event]
    bus.set_main_bridge(None)


def test_agent_event_bus_publish_analyze_request_reliably_with_ack():
    import main_logic.agent_event_bus as bus
    import threading

    class DummyBridge:
        def __init__(self):
            self.events = []
            self.owner_loop = None
            self.owner_thread_id = None

        async def publish_analyze_request(self, event):
            self.events.append(event)
            bus.notify_analyze_ack(event.get("event_id"))
            return True

    async def _run():
        bridge = DummyBridge()
        bridge.owner_loop = asyncio.get_running_loop()
        bridge.owner_thread_id = threading.get_ident()
        bus.set_main_bridge(bridge)
        try:
            ok = await bus.publish_analyze_request_reliably(
                lanlan_name="Tian",
                trigger="turn_end",
                messages=[{"role": "user", "text": "帮我打开系统计算器"}],
                ack_timeout_s=0.2,
                retries=0,
            )
            assert ok is True
            assert len(bridge.events) == 1
            assert bridge.events[0]["event_type"] == "analyze_request"
            assert bridge.events[0]["event_id"]
        finally:
            bus.set_main_bridge(None)

    asyncio.run(_run())


def test_agent_event_bus_publish_analyze_request_reliably_without_bridge_returns_false():
    import main_logic.agent_event_bus as bus

    bus.set_main_bridge(None)
    ok = asyncio.run(
        bus.publish_analyze_request_reliably(
            lanlan_name="Tian",
            trigger="turn_end",
            messages=[{"role": "user", "text": "hello"}],
            ack_timeout_s=0.05,
            retries=0,
        )
    )
    assert ok is False


@pytest.mark.skipif(
    __import__("importlib").util.find_spec("zmq") is None,
    reason="pyzmq not installed",
)
def test_zmq_sync_socket_roundtrip():
    """Integration test: verify sync ZMQ PUSH/PULL actually delivers on Windows."""
    import zmq
    import threading
    import time

    addr = "tcp://127.0.0.1:49901"
    ctx = zmq.Context()

    push = ctx.socket(zmq.PUSH)
    push.setsockopt(zmq.LINGER, 500)
    push.bind(addr)

    pull = ctx.socket(zmq.PULL)
    pull.setsockopt(zmq.LINGER, 500)
    pull.setsockopt(zmq.RCVTIMEO, 3000)
    pull.connect(addr)

    received = []

    def recv_fn():
        try:
            msg = pull.recv_json()
            received.append(msg)
        except zmq.Again:
            pass

    t = threading.Thread(target=recv_fn, daemon=True)
    t.start()

    time.sleep(0.1)
    push.send_json({"hello": "world"})

    t.join(timeout=4)
    pull.close()
    push.close()
    ctx.term()

    assert received == [{"hello": "world"}], f"Expected message not received: {received}"


@pytest.mark.skipif(
    __import__("importlib").util.find_spec("zmq") is None,
    reason="pyzmq not installed",
)
def test_zmq_bridge_end_to_end(monkeypatch):
    """Integration test: full MainBridge -> AgentBridge roundtrip via sync ZMQ."""
    import main_logic.agent_event_bus as bus

    import random
    base = random.randint(55000, 59000)
    test_pub_addr = f"tcp://127.0.0.1:{base}"
    test_push_addr = f"tcp://127.0.0.1:{base + 1}"
    test_analyze_addr = f"tcp://127.0.0.1:{base + 2}"
    monkeypatch.setattr(bus, "SESSION_PUB_ADDR", test_pub_addr)
    monkeypatch.setattr(bus, "AGENT_PUSH_ADDR", test_push_addr)
    monkeypatch.setattr(bus, "ANALYZE_PUSH_ADDR", test_analyze_addr)

    received_on_agent = []
    received_on_main = []

    async def fake_on_session_event(event):
        received_on_agent.append(event)
        if event.get("event_type") == "analyze_request":
            event_id = event.get("event_id")
            if event_id and agent_bridge.push is not None:
                agent_bridge.push.send_json(
                    {"event_type": "analyze_ack", "event_id": event_id},
                    __import__("zmq").NOBLOCK,
                )

    async def fake_on_agent_event(event):
        received_on_main.append(event)
        if event.get("event_type") == "analyze_ack":
            bus.notify_analyze_ack(event.get("event_id", ""))

    main_bridge = bus.MainServerAgentBridge(on_agent_event=fake_on_agent_event)
    agent_bridge = bus.AgentServerEventBridge(on_session_event=fake_on_session_event)

    async def _run():
        await main_bridge.start()
        await agent_bridge.start()

        await asyncio.sleep(0.3)

        bus.set_main_bridge(main_bridge)
        try:
            ok = await bus.publish_analyze_request_reliably(
                lanlan_name="TestChar",
                trigger="test",
                messages=[{"role": "user", "content": "hello"}],
                ack_timeout_s=2.0,
                retries=1,
            )
            assert ok is True, "analyze_request was not acked"

            await asyncio.sleep(0.5)
            assert any(
                e.get("event_type") == "analyze_request" for e in received_on_agent
            ), f"Agent did not receive analyze_request: {received_on_agent}"
            assert any(
                e.get("event_type") == "analyze_ack" for e in received_on_main
            ), f"Main did not receive analyze_ack: {received_on_main}"
        finally:
            bus.set_main_bridge(None)
            main_bridge._stop.set()
            agent_bridge._stop.set()
            await asyncio.sleep(1.5)
            for s in [main_bridge.pub, main_bridge.analyze_push, main_bridge.pull,
                       agent_bridge.sub, agent_bridge.analyze_pull, agent_bridge.push]:
                if s is not None:
                    try:
                        s.close(linger=0)
                    except Exception:
                        pass
            for ctx in [main_bridge.ctx, agent_bridge.ctx]:
                if ctx is not None:
                    try:
                        ctx.term()
                    except Exception:
                        pass

    asyncio.run(_run())


# ---------------------------------------------------------------------------
#  ZMQ PUB/SUB roundtrip (main → agent session events)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    __import__("importlib").util.find_spec("zmq") is None,
    reason="pyzmq not installed",
)
def test_zmq_pubsub_roundtrip(monkeypatch):
    """Real ZMQ PUB/SUB: main publishes session event, agent receives it."""
    import main_logic.agent_event_bus as bus
    import random

    base = random.randint(55100, 55900)
    monkeypatch.setattr(bus, "SESSION_PUB_ADDR", f"tcp://127.0.0.1:{base}")
    monkeypatch.setattr(bus, "AGENT_PUSH_ADDR", f"tcp://127.0.0.1:{base + 1}")
    monkeypatch.setattr(bus, "ANALYZE_PUSH_ADDR", f"tcp://127.0.0.1:{base + 2}")

    received = []

    async def on_session(event):
        received.append(event)

    async def on_agent(event):
        pass

    main_br = bus.MainServerAgentBridge(on_agent_event=on_agent)
    agent_br = bus.AgentServerEventBridge(on_session_event=on_session)

    async def _run():
        await main_br.start()
        await agent_br.start()
        await asyncio.sleep(0.3)
        bus.set_main_bridge(main_br)
        try:
            await main_br.publish_session_event({"event_type": "turn_end", "data": 42})
            await asyncio.sleep(1.0)
            assert any(e.get("event_type") == "turn_end" for e in received), \
                f"Agent did not receive PUB/SUB event: {received}"
        finally:
            bus.set_main_bridge(None)
            main_br._stop.set()
            agent_br._stop.set()
            await asyncio.sleep(1.5)
            for s in [main_br.pub, main_br.analyze_push, main_br.pull,
                       agent_br.sub, agent_br.analyze_pull, agent_br.push]:
                if s:
                    try: s.close(linger=0)
                    except Exception: pass
            for c in [main_br.ctx, agent_br.ctx]:
                if c:
                    try: c.term()
                    except Exception: pass

    asyncio.run(_run())


# ---------------------------------------------------------------------------
#  ZMQ PUSH/PULL roundtrip (agent → main)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    __import__("importlib").util.find_spec("zmq") is None,
    reason="pyzmq not installed",
)
def test_zmq_agent_to_main_push_pull(monkeypatch):
    """Real ZMQ PUSH/PULL: agent emits event, main receives it."""
    import main_logic.agent_event_bus as bus
    import random

    base = random.randint(56000, 56900)
    monkeypatch.setattr(bus, "SESSION_PUB_ADDR", f"tcp://127.0.0.1:{base}")
    monkeypatch.setattr(bus, "AGENT_PUSH_ADDR", f"tcp://127.0.0.1:{base + 1}")
    monkeypatch.setattr(bus, "ANALYZE_PUSH_ADDR", f"tcp://127.0.0.1:{base + 2}")

    received = []

    async def on_session(event):
        pass

    async def on_agent(event):
        received.append(event)

    main_br = bus.MainServerAgentBridge(on_agent_event=on_agent)
    agent_br = bus.AgentServerEventBridge(on_session_event=on_session)

    async def _run():
        await main_br.start()
        await agent_br.start()
        await asyncio.sleep(0.3)
        try:
            ok = await agent_br.emit_to_main({"event_type": "task_result", "task_id": "t1"})
            assert ok is True
            await asyncio.sleep(1.0)
            assert any(e.get("event_type") == "task_result" for e in received), \
                f"Main did not receive agent→main PUSH event: {received}"
        finally:
            main_br._stop.set()
            agent_br._stop.set()
            await asyncio.sleep(1.5)
            for s in [main_br.pub, main_br.analyze_push, main_br.pull,
                       agent_br.sub, agent_br.analyze_pull, agent_br.push]:
                if s:
                    try: s.close(linger=0)
                    except Exception: pass
            for c in [main_br.ctx, agent_br.ctx]:
                if c:
                    try: c.term()
                    except Exception: pass

    asyncio.run(_run())


# ---------------------------------------------------------------------------
#  _emit_main_event (agent_server.py)
# ---------------------------------------------------------------------------

def test_emit_main_event_sends_via_bridge():
    """_emit_main_event calls agent_bridge.emit_to_main when bridge is available."""
    source = Path("agent_server.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    func = None
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_emit_main_event":
            func = node
            break
    assert func is not None, "_emit_main_event not found"
    assert _contains_call(func, "emit_to_main"), \
        "_emit_main_event does not call emit_to_main"


def test_emit_main_event_no_http_fallback():
    """_emit_main_event must NOT contain any httpx or HTTP fallback code."""
    source = Path("agent_server.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    func = None
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_emit_main_event":
            func = node
            break
    assert func is not None
    func_source = ast.get_source_segment(source, func) or ""
    assert "httpx" not in func_source, "_emit_main_event still contains httpx HTTP fallback"
    assert "http://" not in func_source, "_emit_main_event still contains HTTP URL"


# ---------------------------------------------------------------------------
#  _on_session_event (agent_server.py)
# ---------------------------------------------------------------------------

def test_on_session_event_dispatches_ack_and_analyze():
    """_on_session_event creates tasks for ack emission and background analysis."""
    source = Path("agent_server.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    func = None
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_on_session_event":
            func = node
            break
    assert func is not None, "_on_session_event not found"
    func_src = ast.get_source_segment(source, func) or ""
    assert "analyze_ack" in func_src, "_on_session_event does not emit analyze_ack"
    assert "_background_analyze_and_plan" in func_src, \
        "_on_session_event does not call _background_analyze_and_plan"
    assert "create_task" in func_src, \
        "_on_session_event does not use create_task for async dispatch"


# ---------------------------------------------------------------------------
#  publish_session_event_threadsafe from different thread
# ---------------------------------------------------------------------------

def test_publish_session_event_threadsafe_from_different_thread():
    """Threadsafe publish correctly delivers from non-owner thread."""
    import main_logic.agent_event_bus as bus
    import threading

    published = []

    class DummyBridge:
        def __init__(self):
            self.owner_loop = None
            self.owner_thread_id = None

        async def publish_session_event(self, event):
            published.append(event)
            return True

        async def publish_session_event_threadsafe(self, event):
            if self.owner_loop is None:
                return False
            if threading.get_ident() == self.owner_thread_id:
                return await self.publish_session_event(event)
            try:
                cf = asyncio.run_coroutine_threadsafe(
                    self.publish_session_event(event), self.owner_loop,
                )
                return await asyncio.wrap_future(cf)
            except Exception:
                return False

    async def _run():
        bridge = DummyBridge()
        bridge.owner_loop = asyncio.get_running_loop()
        bridge.owner_thread_id = threading.get_ident()
        bus.set_main_bridge(bridge)

        result_holder = [None]
        error_holder = [None]

        async def _publish_from_thread():
            try:
                ok = await bus.publish_session_event_threadsafe(
                    {"event_type": "turn_end", "from_thread": True}
                )
                result_holder[0] = ok
            except Exception as e:
                error_holder[0] = e

        def thread_fn():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(_publish_from_thread())
            loop.close()

        t = threading.Thread(target=thread_fn)
        t.start()
        t.join(timeout=5)

        await asyncio.sleep(0.2)
        bus.set_main_bridge(None)
        assert error_holder[0] is None, f"Thread publish raised: {error_holder[0]}"
        assert result_holder[0] is True
        assert len(published) == 1
        assert published[0]["from_thread"] is True

    asyncio.run(_run())


# ---------------------------------------------------------------------------
#  Analyze request ack timeout + retry
# ---------------------------------------------------------------------------

def test_analyze_request_reliably_retries_on_timeout():
    """publish_analyze_request_reliably retries when ack times out."""
    import main_logic.agent_event_bus as bus
    import threading

    attempts = []

    class SlowAckBridge:
        def __init__(self):
            self.owner_loop = None
            self.owner_thread_id = None

        async def publish_analyze_request(self, event):
            attempts.append(event.get("event_id"))
            return True

    async def _run():
        bridge = SlowAckBridge()
        bridge.owner_loop = asyncio.get_running_loop()
        bridge.owner_thread_id = threading.get_ident()
        bus.set_main_bridge(bridge)
        try:
            ok = await bus.publish_analyze_request_reliably(
                lanlan_name="Test",
                trigger="test",
                messages=[{"role": "user", "content": "hi"}],
                ack_timeout_s=0.05,
                retries=2,
            )
            assert ok is False, "Should have failed after all retries"
            assert len(attempts) == 3, f"Expected 3 attempts (1 + 2 retries), got {len(attempts)}"
            assert all(eid == attempts[0] for eid in attempts), \
                "All attempts should use the same event_id"
        finally:
            bus.set_main_bridge(None)

    asyncio.run(_run())


def test_analyze_request_reliably_returns_true_on_delayed_ack():
    """publish_analyze_request_reliably succeeds when ack arrives within timeout."""
    import main_logic.agent_event_bus as bus
    import threading

    class DelayedAckBridge:
        def __init__(self):
            self.owner_loop = None
            self.owner_thread_id = None

        async def publish_analyze_request(self, event):
            eid = event.get("event_id")
            asyncio.get_running_loop().call_later(
                0.05, lambda: bus.notify_analyze_ack(eid)
            )
            return True

    async def _run():
        bridge = DelayedAckBridge()
        bridge.owner_loop = asyncio.get_running_loop()
        bridge.owner_thread_id = threading.get_ident()
        bus.set_main_bridge(bridge)
        try:
            ok = await bus.publish_analyze_request_reliably(
                lanlan_name="Test",
                trigger="test",
                messages=[{"role": "user", "content": "hi"}],
                ack_timeout_s=0.5,
                retries=0,
            )
            assert ok is True
        finally:
            bus.set_main_bridge(None)

    asyncio.run(_run())


# ---------------------------------------------------------------------------
#  Bridge not ready: all publish methods return False
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    __import__("importlib").util.find_spec("zmq") is None,
    reason="pyzmq not installed",
)
def test_real_bridge_not_started_returns_false():
    """MainServerAgentBridge.publish_* returns False before start() is called."""
    import main_logic.agent_event_bus as bus

    async def noop(event): pass

    bridge = bus.MainServerAgentBridge(on_agent_event=noop)
    agent_bridge = bus.AgentServerEventBridge(on_session_event=noop)

    async def _run():
        assert await bridge.publish_session_event({"t": 1}) is False
        assert await bridge.publish_analyze_request({"t": 1}) is False
        assert await agent_bridge.emit_to_main({"t": 1}) is False

    asyncio.run(_run())


# ---------------------------------------------------------------------------
#  _publish_analyze_request_with_fallback (cross_server.py)
# ---------------------------------------------------------------------------

def test_cross_server_publish_returns_true_on_success(monkeypatch):
    """_publish_analyze_request_with_fallback returns True when reliably delivered."""
    from main_logic.cross_server import _publish_analyze_request_with_fallback

    async def fake_reliably(**kw):
        return True

    monkeypatch.setattr(
        "main_logic.cross_server.publish_analyze_request_reliably",
        fake_reliably,
    )

    ok = asyncio.run(
        _publish_analyze_request_with_fallback("Tian", "turn_end", [{"role": "user", "content": "hi"}])
    )
    assert ok is True


def test_cross_server_publish_returns_false_on_failure(monkeypatch):
    """_publish_analyze_request_with_fallback returns False when delivery fails."""
    from main_logic.cross_server import _publish_analyze_request_with_fallback

    async def fake_reliably(**kw):
        return False

    monkeypatch.setattr(
        "main_logic.cross_server.publish_analyze_request_reliably",
        fake_reliably,
    )

    ok = asyncio.run(
        _publish_analyze_request_with_fallback("Tian", "turn_end", [{"role": "user", "content": "hi"}])
    )
    assert ok is False


def test_cross_server_publish_returns_false_on_exception(monkeypatch):
    """_publish_analyze_request_with_fallback returns False when exception is raised."""
    from main_logic.cross_server import _publish_analyze_request_with_fallback

    async def fake_reliably(**kw):
        raise RuntimeError("zmq exploded")

    monkeypatch.setattr(
        "main_logic.cross_server.publish_analyze_request_reliably",
        fake_reliably,
    )

    ok = asyncio.run(
        _publish_analyze_request_with_fallback("Tian", "turn_end", [{"role": "user", "content": "hi"}])
    )
    assert ok is False


def test_cross_server_publish_no_http_fallback():
    """_publish_analyze_request_with_fallback must NOT contain HTTP fallback."""
    source = Path("main_logic/cross_server.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    func = None
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_publish_analyze_request_with_fallback":
            func = node
            break
    assert func is not None
    func_src = ast.get_source_segment(source, func) or ""
    assert "aiohttp.ClientSession" not in func_src, \
        "_publish_analyze_request_with_fallback still contains HTTP fallback"
    assert "/agent/analyze_request" not in func_src, \
        "_publish_analyze_request_with_fallback still targets HTTP endpoint"


# ---------------------------------------------------------------------------
#  Concurrent analyze requests with correct ack matching
# ---------------------------------------------------------------------------

def test_concurrent_analyze_requests_match_acks_correctly():
    """Multiple concurrent analyze_request_reliably calls each get their own ack."""
    import main_logic.agent_event_bus as bus
    import threading

    ack_delays = {"req1": 0.05, "req2": 0.10}

    class ConcurrentBridge:
        def __init__(self):
            self.owner_loop = None
            self.owner_thread_id = None

        async def publish_analyze_request(self, event):
            eid = event.get("event_id")
            name = event.get("lanlan_name")
            delay = ack_delays.get(name, 0.05)
            asyncio.get_running_loop().call_later(
                delay, lambda: bus.notify_analyze_ack(eid)
            )
            return True

    async def _run():
        bridge = ConcurrentBridge()
        bridge.owner_loop = asyncio.get_running_loop()
        bridge.owner_thread_id = threading.get_ident()
        bus.set_main_bridge(bridge)
        try:
            results = await asyncio.gather(
                bus.publish_analyze_request_reliably(
                    lanlan_name="req1", trigger="t", messages=[{"r": "u"}],
                    ack_timeout_s=1.0, retries=0,
                ),
                bus.publish_analyze_request_reliably(
                    lanlan_name="req2", trigger="t", messages=[{"r": "u"}],
                    ack_timeout_s=1.0, retries=0,
                ),
            )
            assert results == [True, True], f"Expected both True, got {results}"
        finally:
            bus.set_main_bridge(None)

    asyncio.run(_run())
