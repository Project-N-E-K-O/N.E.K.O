"""Permission vocabulary, errors, cross-process translation and FakePermissions."""

from __future__ import annotations

import errno
import pickle

import pytest

from plugin.core.rpc import RpcRemoteError
from plugin.sdk.plugin import (
    Err,
    FakePermissions,
    MemoryTarget,
    PermissionDeniedError,
    PermissionState,
    SdkError,
    SystemPermissionDeniedError,
)
from plugin.sdk.shared.conversations.client import SdkConversationsV2Bus
from plugin.sdk.shared.core.call_chain_view import view_from_wire
from plugin.sdk.shared.core.plugins import Plugins
from plugin.sdk.shared.memory.client import MemoryClient
from plugin.sdk.shared.models.exceptions import PluginCallError
from plugin.sdk.shared.permissions import (
    LEGACY_PERMISSION_NAMES,
    LOCK_PERMISSIONS,
    PERMISSION_DENIED_REASONS,
    PERMISSION_IDS,
    PERMISSIONS,
    PLUGIN_PERMISSION_DENIED,
    UNENFORCED_PERMISSIONS,
    normalize_permission,
)

pytestmark = pytest.mark.plugin_unit

MEMORY = "plugin_sdk.main_agent.memory:read"
DENIED_WIRE = {
    "code": PLUGIN_PERMISSION_DENIED,
    "message": "memory read refused",
    "details": {"permission": MEMORY, "reason": "denied"},
}


# --- vocabulary ------------------------------------------------------------


def test_first_batch_vocabulary():
    assert PERMISSION_IDS == (
        "plugin_sdk.main_agent.memory:read",
        "plugin_sdk.main_agent.conversations:read",
        "plugin_sdk.models:invoke",
        "system.network:connect",
        "system.fs:read",
        "system.fs:write",
        "system.process:spawn",
        "system.env:read",
        "plugin_sdk.main_agent.messages:push",
    )
    assert tuple(PERMISSIONS) == PERMISSION_IDS
    assert LOCK_PERMISSIONS == {MEMORY, "plugin_sdk.main_agent.conversations:read"}
    assert UNENFORCED_PERMISSIONS == {"system.env:read", "plugin_sdk.main_agent.messages:push"}
    assert dict(LEGACY_PERMISSION_NAMES) == {
        "memory.read": MEMORY,
        "conversations.read": "plugin_sdk.main_agent.conversations:read",
        "messages.push": "plugin_sdk.main_agent.messages:push",
    }
    assert PLUGIN_PERMISSION_DENIED != "PERMISSION_DENIED"
    assert PERMISSION_DENIED_REASONS == ("not_declared", "denied", "undecided", "background", "receiver_lacks")


def test_normalize_permission():
    assert normalize_permission(MEMORY) == MEMORY
    assert normalize_permission("memory.read") == MEMORY
    for unknown in ("memory:read", "screen.capture", "", None, 3):
        assert normalize_permission(unknown) is None


def test_call_chain_allows_takes_ids_and_earlier_names():
    wire = {"hops": [["a", "plugin_entry", "x"]], "origin": {"kind": "run"}, "rights": [MEMORY]}
    view = view_from_wire("a", wire)
    assert view.allows(MEMORY) and view.allows("memory.read")
    assert not view.allows("messages.push") and not view.allows("screen.capture")
    unknown = view_from_wire("a", {**wire, "rights": None})
    assert not unknown.allows(MEMORY) and not unknown.allows("memory.read")


# --- errors ----------------------------------------------------------------


def test_permission_denied_error_shape():
    error = PermissionDeniedError(permission=MEMORY, reason="denied")
    assert isinstance(error, SdkError) and not isinstance(error, PermissionError)
    assert (error.code, error.permission, error.reason) == (PLUGIN_PERMISSION_DENIED, MEMORY, "denied")
    assert MEMORY in str(error)
    bare = PermissionDeniedError("refused")
    assert (bare.permission, bare.reason, str(bare)) == (None, None, "refused")  # no AttributeError
    assert PermissionDeniedError("x", code="OTHER").code == PLUGIN_PERMISSION_DENIED
    copy = pickle.loads(pickle.dumps(error))
    assert (type(copy), copy.permission, copy.reason, copy.code, str(copy)) == (
        PermissionDeniedError, MEMORY, "denied", PLUGIN_PERMISSION_DENIED, str(error))


def test_system_permission_denied_error_double_inheritance():
    assert SystemPermissionDeniedError.__mro__[1:] == (
        PermissionDeniedError, SdkError, RuntimeError, PermissionError, OSError, Exception, BaseException, object)
    error = SystemPermissionDeniedError("blocked", permission="system.fs:read", reason="not_declared", target="/x")
    assert (error.permission, error.reason, error.target, error.code) == (
        "system.fs:read", "not_declared", "/x", PLUGIN_PERMISSION_DENIED)
    assert error.errno == errno.EACCES and error.strerror is None and error.filename is None
    assert str(error) == "blocked" and "[Errno" not in str(error)
    only_message = SystemPermissionDeniedError("blocked")
    assert only_message.permission is None and only_message.reason is None
    for catch in (PermissionError, OSError, PermissionDeniedError, SdkError):
        with pytest.raises(catch):
            raise SystemPermissionDeniedError("blocked", permission="system.fs:read")
    copy = pickle.loads(pickle.dumps(error))
    assert type(copy) is SystemPermissionDeniedError
    assert (copy.permission, copy.reason, copy.target, copy.errno, str(copy)) == (
        "system.fs:read", "not_declared", "/x", errno.EACCES, "blocked")


def test_from_wire_only_takes_the_new_code_with_a_permission():
    rebuilt = PermissionDeniedError.from_wire(**DENIED_WIRE)
    assert (type(rebuilt), rebuilt.permission, rebuilt.reason, str(rebuilt)) == (
        PermissionDeniedError, MEMORY, "denied", "memory read refused")
    # Plugin config's own refusal keeps its code and is never rebuilt.
    assert PermissionDeniedError.from_wire("PERMISSION_DENIED", "own config only", {"permission": MEMORY}) is None
    assert PermissionDeniedError.from_wire(PLUGIN_PERMISSION_DENIED, "x", {"reason": "denied"}) is None
    assert PermissionDeniedError.from_wire(PLUGIN_PERMISSION_DENIED, "x", None) is None
    assert PermissionDeniedError.from_exception(RpcRemoteError(DENIED_WIRE)).permission == MEMORY
    assert PermissionDeniedError.from_exception(RpcRemoteError({"code": "PERMISSION_DENIED"})) is None


# --- translation points ----------------------------------------------------


class _Raising:
    def __init__(self, error):
        self.error = error

    async def _send_request_and_wait_async(self, **kwargs):
        raise self.error

    async def get(self, **kwargs):
        raise self.error

    async def get_message(self, message_id, **kwargs):
        raise self.error

    async def trigger_plugin_event(self, **kwargs):
        raise self.error


async def test_memory_client_returns_err_permission_denied():
    for call in ("search", "get_persona"):
        client = MemoryClient(_Raising(RpcRemoteError(DENIED_WIRE)))
        kwargs = {"query": "city"} if call == "search" else {}
        result = await getattr(client, call)(target=MemoryTarget.private("YUI"), **kwargs)
        assert isinstance(result, Err) and isinstance(result.error, PermissionDeniedError)
        assert (result.error.permission, result.error.reason) == (MEMORY, "denied")
    config_refusal = RpcRemoteError({"code": "PERMISSION_DENIED", "message": "own config only"})
    result = await MemoryClient(_Raising(config_refusal)).search(target=MemoryTarget.private("YUI"), query="x")
    assert type(result.error) is SdkError and result.error.code == "PERMISSION_DENIED"


async def test_conversations_raise_permission_denied():
    error = RpcRemoteError({**DENIED_WIRE, "details": {"permission": "plugin_sdk.main_agent.conversations:read"}})
    raising = _Raising(error)
    bus = SdkConversationsV2Bus(raising, host_ctx=raising)
    for call in (bus.get(), bus.get_message("m"), bus.get_reply("r"), bus.get_interaction("i")):
        with pytest.raises(PermissionDeniedError) as caught:
            await call
        assert caught.value.permission == "plugin_sdk.main_agent.conversations:read"
        assert caught.value.__cause__ is error
    other = _Raising(RpcRemoteError({"code": "PERMISSION_DENIED"}))
    with pytest.raises(RpcRemoteError):
        await SdkConversationsV2Bus(other, host_ctx=other).get_reply("r")


async def test_call_entry_returns_err_permission_denied():
    receiver_lacks = RpcRemoteError({**DENIED_WIRE, "details": {"permission": MEMORY, "reason": "receiver_lacks"}})
    result = await Plugins(_Raising(receiver_lacks)).call_entry("other:entry")
    assert isinstance(result, Err) and isinstance(result.error, PermissionDeniedError)
    assert result.error.reason == "receiver_lacks"
    other = await Plugins(_Raising(RpcRemoteError({"code": "PERMISSION_DENIED"}))).call_entry("other:entry")
    assert type(other.error) is PluginCallError and other.error.code == "PERMISSION_DENIED"


# --- FakePermissions -------------------------------------------------------


async def test_fake_permissions_states_requests_and_changes():
    perms = FakePermissions({MEMORY: "denied"}, answers={"system.network:connect": "granted"}, default="undecided")
    assert perms.get(MEMORY) == PermissionState("denied", enforced=True)
    assert perms.get("system.network:connect").status == "undecided"
    assert perms.get("system.env:read") == PermissionState("undecided", enforced=False)
    assert perms.get("plugin_sdk.main_agent.messages:push").enforced is False

    seen = []
    handle = perms.on_change(lambda permission, state: seen.append((permission, state.status)))
    assert (await perms.request("system.network:connect")).status == "granted"
    assert (await perms.request(MEMORY, wait=False)).status == "denied"  # no answer: unchanged
    perms.set(MEMORY, {"status": "granted", "background": True})
    handle.cancel()
    perms.set(MEMORY, "denied")
    assert seen == [("system.network:connect", "granted"), (MEMORY, "granted")]
    assert perms.requests == [("system.network:connect", True), (MEMORY, False)]
    assert perms.get(MEMORY).background is False


def test_fake_permissions_denial_and_switch():
    perms = FakePermissions({MEMORY: "denied", "plugin_sdk.main_agent.messages:push": "undecided"})
    error = perms.denial(MEMORY)
    assert isinstance(error, PermissionDeniedError) and (error.permission, error.reason) == (MEMORY, "denied")
    assert perms.denial("system.fs:read") is None  # default granted
    assert perms.denial("plugin_sdk.main_agent.messages:push") is None  # disclosure only
    switched = FakePermissions({MEMORY: PermissionState("denied", enforced=True)}, checks_disabled=True)
    assert switched.get(MEMORY).enforced is False and switched.denial(MEMORY) is None
    assert all(switched.get(p).enforced is False for p in PERMISSION_IDS)
    explicit = FakePermissions({MEMORY: {"status": "granted", "enforced": False}})
    assert explicit.get(MEMORY).enforced is False


def test_fake_permissions_rejects_unknown_ids_and_states():
    with pytest.raises(ValueError):
        FakePermissions({"memory.read": "denied"})
    with pytest.raises(ValueError):
        FakePermissions({MEMORY: "disclose"})
    with pytest.raises(ValueError):
        FakePermissions(default="maybe")
    with pytest.raises(ValueError):
        FakePermissions({MEMORY: {"status": "granted", "scope": "all"}})
    with pytest.raises(ValueError):
        FakePermissions().get("screen.capture")
