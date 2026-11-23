"""Master switch, the deprecated policy variable, and every permission registration point.

A permission id missing from any registration point is silently not judged,
so each point is pinned to the vocabulary in plugin.sdk.shared.permissions.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from plugin import settings
from plugin.core import capability_analysis, capability_policy
from plugin.core.call_registry import CallLink, CallRegistry, Origin
from plugin.core.communication import PluginCommunicationResourceManager
from plugin.core.context import _attach_parent_request_id
from plugin.core.rpc import RpcContext, request_scope
from plugin.sdk.shared.permissions import LEGACY_PERMISSION_NAMES, PERMISSION_IDS, PERMISSIONS
from plugin.server.application.plugins import call_audit, capability_gate
from plugin.server.messaging.handlers import plugin_to_plugin

pytestmark = pytest.mark.plugin_unit

REPO = Path(__file__).resolve().parents[4]
MEMORY = "plugin_sdk.main_agent.memory:read"


@pytest.fixture
def switch(monkeypatch):
    def set_switch(host=False, legacy=None):
        monkeypatch.setattr(settings, "PLUGIN_PERMISSION_CHECKS_DISABLED", host)
        monkeypatch.setattr(settings, "PLUGIN_CAPABILITY_POLICY", legacy)

    set_switch()
    return set_switch


@pytest.fixture
def gate(monkeypatch, tmp_path):
    monkeypatch.setattr(capability_gate, "registry", CallRegistry())
    monkeypatch.setattr(capability_gate, "_last_written", {})
    monkeypatch.setattr(capability_gate, "granted_capabilities", lambda p: (frozenset(), True))
    monkeypatch.setattr(call_audit, "audit_directory", lambda: tmp_path)
    monkeypatch.setattr(call_audit, "_ring", call_audit.deque(maxlen=1000))
    monkeypatch.setattr(call_audit, "_decision_ring", call_audit.deque(maxlen=500))
    monkeypatch.setattr(call_audit, "_abandoned", None)
    call_audit.close()
    call_audit.reopen()
    yield
    call_audit.close()
    call_audit.reopen()


# --- master switch ---------------------------------------------------------


@pytest.mark.parametrize(
    ("host", "legacy", "sources"),
    [
        (False, None, ()),
        (True, None, ("host_config",)),
        (False, "off", ("legacy_env",)),
        (False, "observe", ("legacy_env",)),
        (False, "enforce", ()),
        (False, "bogus", ()),  # not a known value: ignored, checks stay on
        (False, "", ()),
        (True, "enforce", ("host_config",)),  # either source switching off wins
        (True, "off", ("host_config", "legacy_env")),
    ],
)
def test_switch_sources(switch, host, legacy, sources):
    switch(host, legacy)
    assert capability_policy.checks_disabled_sources() == sources
    assert capability_policy.checks_disabled() is bool(sources)
    assert capability_policy.current_mode() == ("observe" if sources else "enforce")


def test_is_enforced(switch):
    assert capability_policy.is_enforced(MEMORY)
    assert not capability_policy.is_enforced("plugin_sdk.main_agent.messages:push")
    assert not capability_policy.is_enforced("system.env:read")
    assert not capability_policy.is_enforced("memory.read")  # only ids
    switch(host=True)
    assert not any(capability_policy.is_enforced(p) for p in PERMISSION_IDS)


@pytest.mark.parametrize(
    ("legacy", "expected"),
    [("off", "turns the master switch on"), ("observe", "turns the master switch on"),
     ("enforce", "leaves permission checks in force"), ("nope", "is ignored")],
)
def test_startup_notices_for_the_deprecated_variable(switch, legacy, expected):
    switch(legacy=legacy)
    notices = capability_policy.startup_notices()
    assert expected in notices[0] and "deprecated" in notices[0]
    assert "NEKO_PLUGIN_CAPABILITY_POLICY" in notices[0]
    assert len(notices) == (2 if legacy in ("off", "observe") else 1)


def test_startup_notices_do_not_contradict_each_other(switch):
    switch(host=True, legacy="enforce")
    legacy_line, switch_line = capability_policy.startup_notices()
    assert "switches them off anyway" in legacy_line and "switched off" in switch_line


def test_permission_refusal_keeps_its_audit_code():
    error = RuntimeError()
    error.code = "PLUGIN_PERMISSION_DENIED"
    assert capability_gate.result_code(error) == "PLUGIN_PERMISSION_DENIED"


def test_startup_notices(switch):
    assert capability_policy.startup_notices() == []
    switch(host=True)
    (notice,) = capability_policy.startup_notices()
    assert "switched off" in notice and "host_config" in notice


def test_settings_read_the_variables(monkeypatch):
    monkeypatch.setenv("NEKO_PLUGIN_CAPABILITY_POLICY", " Observe ")
    assert settings._get_optional_lower_env("NEKO_PLUGIN_CAPABILITY_POLICY") == "observe"
    monkeypatch.delenv("NEKO_PLUGIN_CAPABILITY_POLICY")
    assert settings._get_optional_lower_env("NEKO_PLUGIN_CAPABILITY_POLICY") is None
    monkeypatch.setenv("NEKO_PLUGIN_PERMISSION_CHECKS_DISABLED", "true")
    assert settings._get_bool_env("NEKO_PLUGIN_PERMISSION_CHECKS_DISABLED", False) is True
    monkeypatch.delenv("NEKO_PLUGIN_PERMISSION_CHECKS_DISABLED")
    # Unset means checks in force, not the old "observe" default.
    assert settings._get_bool_env("NEKO_PLUGIN_PERMISSION_CHECKS_DISABLED", False) is False


# --- the guards run whatever the switch says -------------------------------


@pytest.mark.parametrize("disabled", [False, True])
async def test_delivery_is_registered_and_carries_the_chain(switch, gate, disabled):
    switch(host=disabled)
    sent = []

    async def send(req_id, msg, timeout, error_context):
        sent.append(dict(msg))
        return "ok"

    manager = PluginCommunicationResourceManager(plugin_id="a", transport=object())  # type: ignore[arg-type]
    manager._send_command_and_wait_local = send  # type: ignore[method-assign]
    result = await manager._send_delivery("r1", {"type": "TRIGGER"}, 1.0, "entry x", event_type="plugin_entry",
                                          event_id="x", call_link=CallLink(Origin("run")))
    assert result == "ok" and sent[0]["_call_chain"]["origin"]["kind"] == "run"
    (forward,) = [e for e in call_audit.entries() if e["kind"] == "forward"]
    assert forward["mode"] == ("observe" if disabled else "enforce")


@pytest.mark.parametrize("disabled", [False, True])
def test_check_judges_and_records(switch, gate, disabled):
    switch(host=disabled)
    decision = capability_gate.check("a", None, MEMORY, "MEMORY_SEARCH")
    assert decision is not None and decision.judged and decision.reason == "self_not_approved"
    (entry,) = call_audit.entries()
    assert (entry["verdict"], entry["capability"]) == ("would_deny", MEMORY)


@pytest.mark.parametrize("disabled", [False, True])
def test_push_carries_its_parent_request_id(switch, disabled):
    switch(host=disabled)
    payload = {}
    with request_scope({"request_id": "parent-1"}):
        _attach_parent_request_id(payload)
    assert payload == {"_rpc_parent_request_id": "parent-1"}
    outside = {}
    _attach_parent_request_id(outside)
    assert outside == {}


@pytest.mark.parametrize("disabled", [False, True])
async def test_plugin_to_plugin_links_the_caller(switch, gate, monkeypatch, disabled):
    switch(host=disabled)
    seen = []

    async def trigger_custom_event(**kwargs):
        from plugin.core.call_registry import current_delivery_link

        seen.append(current_delivery_link())
        return {"ok": True}

    monkeypatch.setattr(plugin_to_plugin.plugin_dispatch_service, "trigger_custom_event", trigger_custom_event)
    ctx = RpcContext(from_plugin="b", request_id="q", deadline=float("inf"), trace_id="t", depth=0)
    request = {"to_plugin": "a", "event_type": "plugin_entry", "event_id": "x", "args": {}}
    await plugin_to_plugin.handle_plugin_to_plugin(ctx, request)
    assert seen[0] is not None and seen[0].caller == "b"


def test_no_guard_on_the_switch_is_left():
    """The switch decides refusal only; nothing may skip judging because of it."""
    for path in (REPO / "plugin").rglob("*.py"):
        if "tests" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        assert "policy_enabled" not in text, path
        assert 'current_mode() == "off"' not in text, path


# --- registration points ---------------------------------------------------


def test_decide_judges_exactly_the_vocabulary():
    def granted(plugin):
        return frozenset()

    for permission in PERMISSION_IDS:
        assert capability_policy.decide(permission, ("a",), granted).judged, permission
    for name in (*LEGACY_PERMISSION_NAMES, "screen.capture"):
        assert not capability_policy.decide(name, ("a",), granted).judged, name


def test_operation_table_and_route_use_permission_ids():
    assert set(capability_gate.CAPABILITY_BY_OPERATION.values()) <= set(PERMISSION_IDS)
    for operation, permission in capability_gate.CAPABILITY_BY_OPERATION.items():
        expected = MEMORY if operation.startswith(("MEMORY_", "USER_CONTEXT")) else (
            "plugin_sdk.main_agent.conversations:read")
        assert permission == expected, operation
    for constant in (capability_gate.MEMORY_READ, capability_gate.CONVERSATIONS_READ, capability_gate.MESSAGES_PUSH):
        assert constant in PERMISSIONS


def test_gate_constants_are_read_from_the_vocabulary():
    """capability_gate spells no permission id itself: each one is looked up
    in PERMISSIONS, so an id that leaves the vocabulary fails at import."""
    path = REPO / "plugin/server/application/plugins/capability_gate.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    looked_up = {
        id(node.slice)
        for node in ast.walk(tree)
        if isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Name)
        and node.value.id == "PERMISSIONS"
    }
    stray = [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and node.value in PERMISSIONS
        and id(node) not in looked_up
    ]
    assert stray == []
    assert {capability_gate.MEMORY_READ, capability_gate.CONVERSATIONS_READ,
            capability_gate.MESSAGES_PUSH} == {
        MEMORY,
        "plugin_sdk.main_agent.conversations:read",
        "plugin_sdk.main_agent.messages:push",
    }


def test_every_permission_handed_to_the_gate_is_an_id():
    """Literals and gate constants passed to the decision helpers must be vocabulary ids."""
    positions = {"check": 2, "capability_checked": 0, "decide": 0, "is_enforced": 0}
    resolved = []
    for path in (REPO / "plugin").rglob("*.py"):
        if "tests" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", None)
            if name not in positions or len(node.args) <= positions[name]:
                continue
            arg = node.args[positions[name]]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                value = arg.value
            elif isinstance(arg, ast.Attribute) and isinstance(arg.value, ast.Name) and arg.value.id == "capability_gate":
                value = getattr(capability_gate, arg.attr)
            elif isinstance(arg, ast.Name) and arg.id.isupper():
                value = getattr(capability_gate, arg.id)
            else:
                continue  # a variable, e.g. the wrapper forwarding its own argument
            resolved.append((path.name, value))
            assert value in PERMISSIONS, f"{path}:{node.lineno} {value!r}"
    # The MESSAGE_PUSH route is one of them.
    assert ("communication.py", capability_gate.MESSAGES_PUSH) in resolved


def test_analyzer_names_all_map_to_permission_ids():
    assert capability_analysis.CAPABILITIES == ("conversations.read", "memory.read", "messages.push")
    for name in capability_analysis.CAPABILITIES:
        assert LEGACY_PERMISSION_NAMES[name] in PERMISSIONS


def test_rights_go_out_with_ids_and_earlier_names(gate):
    record = capability_gate.begin_call("a", "r1", "plugin_entry", "x", None)
    record.rights = frozenset({MEMORY, "system.fs:read"})
    assert capability_gate.chain_payload(record)["rights"] == sorted([MEMORY, "memory.read", "system.fs:read"])
    capability_gate.end_call(record, "ok", 0)


def test_plugin_manager_labels_every_permission_id():
    labels = (REPO / "frontend/plugin-manager/src/i18n/plugin-permissions.ts").read_text(encoding="utf-8")
    spec = (REPO / "frontend/plugin-manager/src/utils/pluginCapabilities.test.ts").read_text(encoding="utf-8")
    block = spec[spec.index("const PERMISSION_IDS = ["):]
    listed = tuple(re.findall(r"'([^']+)'", block[: block.index("]")]))
    assert listed == PERMISSION_IDS  # the vitest checks every locale for these
    for permission in PERMISSION_IDS:
        key = re.sub(r"[.:]", "_", permission)
        assert key in labels, permission
