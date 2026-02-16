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
        "/agent/analyze_request",
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
        "mcp_enabled",
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


def test_is_agent_api_ready_rejects_free_profile():
    manager = object.__new__(ConfigManager)
    manager.get_core_config = lambda: {"IS_FREE_VERSION": True}
    manager.get_model_api_config = lambda _model_type: {
        "model": "agent-model",
        "base_url": "https://example.invalid/v1",
        "api_key": "sk-test",
    }

    ready, reasons = manager.is_agent_api_ready()
    assert ready is False
    assert "free API 不支持 Agent 模式" in reasons


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


def test_publish_main_event_writes_json_line(monkeypatch):
    from brain.main_bridge import publish_main_event

    class DummyWriter:
        def __init__(self):
            self.buffer = b""
            self.closed = False
            self.drain_called = False
            self.wait_closed_called = False

        def write(self, data):
            self.buffer += data

        async def drain(self):
            self.drain_called = True

        def close(self):
            self.closed = True

        async def wait_closed(self):
            self.wait_closed_called = True

    writer = DummyWriter()

    async def fake_open_connection(host, port):
        assert host == "127.0.0.1"
        assert isinstance(port, int)
        return object(), writer

    monkeypatch.setattr("brain.main_bridge.asyncio.open_connection", fake_open_connection)

    ok = asyncio.run(publish_main_event({"type": "task_update", "ok": True}))
    assert ok is True
    assert writer.drain_called is True
    assert writer.closed is True
    assert writer.wait_closed_called is True
    payload = json.loads(writer.buffer.decode("utf-8").strip())
    assert payload["type"] == "task_update"
    assert payload["ok"] is True


def test_publish_main_event_returns_false_on_connection_error(monkeypatch):
    from brain.main_bridge import publish_main_event

    async def fake_open_connection(_host, _port):
        raise RuntimeError("boom")

    monkeypatch.setattr("brain.main_bridge.asyncio.open_connection", fake_open_connection)
    ok = asyncio.run(publish_main_event({"type": "x"}))
    assert ok is False


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
