"""One behaviour suite for ``FakePermissions`` and the real ``ctx.permissions``.

A plugin tested against the fake must see the same ``get`` / ``on_change``
behaviour in a real plugin process. Each case runs against both; the real
one is driven by host snapshots, the fake by ``set``.
"""

from __future__ import annotations

import functools

import pytest

from plugin.sdk.plugin import FakePermissions, PermissionState
from plugin.sdk.shared.permissions import PERMISSIONS, UNENFORCED_PERMISSIONS
from plugin.sdk.shared.permissions.runtime import PluginPermissions, state_to_wire

pytestmark = pytest.mark.plugin_unit

MEMORY = "plugin_sdk.main_agent.memory:read"
NETWORK = "system.network:connect"
PUSH = "plugin_sdk.main_agent.messages:push"
ENV = "system.env:read"
LISTEN = "system.network:listen"

# Fake-only test controls and host-only plumbing; everything else is what a
# plugin calls and must exist on both.
FAKE_TEST_CONTROLS = frozenset({"set", "denial"})
REAL_HOST_SIDE = frozenset({"apply", "revision"})


class FakeDriver:
    def __init__(self, states, *, checks_disabled=False):
        self.perms = FakePermissions(states, default="undecided", checks_disabled=checks_disabled)

    def change(self, permission, status, *, background=False):
        self.perms.set(permission, {"status": status, "background": background})


class RealDriver:
    """Builds snapshots the way the host does: every id, ``enforced`` from
    the vocabulary unless the master switch is on."""

    def __init__(self, states, *, checks_disabled=False):
        self.checks_disabled = checks_disabled
        self.states = {
            permission: (states.get(permission, "undecided"), False) for permission in PERMISSIONS
        }
        self.revision = 0
        self.perms = PluginPermissions()
        self._send()

    def _send(self):
        self.revision += 1
        wire = {
            permission: state_to_wire(
                PermissionState(
                    status,
                    background=background,
                    enforced=PERMISSIONS[permission].enforced and not self.checks_disabled,
                )
            )
            for permission, (status, background) in self.states.items()
        }
        assert self.perms.apply({"revision": self.revision, "states": wire})

    def change(self, permission, status, *, background=False):
        self.states[permission] = (status, background)
        self._send()


def _public(obj) -> frozenset[str]:
    return frozenset(name for name in dir(obj) if not name.startswith("_"))


def test_fake_and_real_offer_the_same_plugin_facing_methods():
    # A method only the fake has passes a plugin's tests and fails with
    # AttributeError in a real process; one only the real one has cannot be
    # tested against the fake. Instances, so attributes set in __init__ count.
    fake = _public(FakePermissions())
    real = _public(PluginPermissions())
    assert FAKE_TEST_CONTROLS <= fake and REAL_HOST_SIDE <= real
    assert fake - FAKE_TEST_CONTROLS == real - REAL_HOST_SIDE == {"get", "on_change"}


@pytest.fixture(params=[FakeDriver, RealDriver], ids=["fake", "real"])
def make(request):
    return request.param


def test_get_returns_the_status(make):
    driver = make({MEMORY: "granted", NETWORK: "denied"})
    assert driver.perms.get(MEMORY) == PermissionState("granted", enforced=True)
    assert driver.perms.get(NETWORK).status == "denied"
    assert driver.perms.get("system.fs:read").status == "undecided"


@pytest.mark.parametrize("permission", ["memory.read", "plugin_sdk.main_agent.memory:write", "", None])
def test_unknown_ids_raise_value_error(make, permission):
    driver = make({})
    with pytest.raises(ValueError):
        driver.perms.get(permission)


def test_disclosure_only_permissions_are_not_enforced(make):
    driver = make({PUSH: "granted"})
    for permission in PERMISSIONS:
        assert driver.perms.get(permission).enforced is (permission not in UNENFORCED_PERMISSIONS)
    assert driver.perms.get(PUSH) == PermissionState("granted", enforced=False)
    assert driver.perms.get(ENV).enforced is False
    assert driver.perms.get(LISTEN) == PermissionState("undecided", enforced=False)


def test_master_switch_makes_every_permission_unenforced(make):
    driver = make({MEMORY: "granted", NETWORK: "denied"}, checks_disabled=True)
    assert all(driver.perms.get(permission).enforced is False for permission in PERMISSIONS)
    assert driver.perms.get(NETWORK).status == "denied"


def test_on_change_reports_each_changed_permission(make):
    driver = make({MEMORY: "undecided"})
    seen = []
    driver.perms.on_change(lambda permission, state: seen.append((permission, state)))
    driver.change(MEMORY, "granted", background=True)
    assert seen == [(MEMORY, PermissionState("granted", background=True, enforced=True))]
    assert driver.perms.get(MEMORY).background is True


def test_on_change_is_quiet_when_nothing_changed(make):
    driver = make({MEMORY: "granted"})
    seen = []
    driver.perms.on_change(lambda permission, state: seen.append(permission))
    driver.change(MEMORY, "granted")
    assert seen == []


def test_every_subscriber_is_called(make):
    driver = make({})
    first, second = [], []
    driver.perms.on_change(lambda permission, state: first.append(state.status))
    driver.perms.on_change(lambda permission, state: second.append(state.status))
    driver.change(NETWORK, "denied")
    assert first == second == ["denied"]


def test_cancel_stops_notifications_and_is_idempotent(make):
    driver = make({})
    seen = []
    handle = driver.perms.on_change(lambda permission, state: seen.append(state.status))
    driver.change(MEMORY, "granted")
    handle.cancel()
    handle.cancel()
    handle.unsubscribe()
    driver.change(MEMORY, "denied")
    assert seen == ["granted"]


def test_cancel_removes_only_its_own_registration(make):
    class Recorder:
        """Callables that compare equal although they are distinct."""

        def __init__(self, name, seen):
            self.name, self.seen = name, seen

        def __eq__(self, other):
            return isinstance(other, Recorder)

        __hash__ = object.__hash__

        def __call__(self, permission, state):
            self.seen.append(self.name)

    driver = make({})
    seen = []
    first, second = Recorder("first", seen), Recorder("second", seen)
    driver.perms.on_change(first)
    handle = driver.perms.on_change(second)
    handle.cancel()
    driver.change(MEMORY, "granted")
    assert seen == ["first"]

    seen.clear()
    twice = Recorder("twice", seen)
    one = driver.perms.on_change(twice)
    driver.perms.on_change(twice)
    one.cancel()
    driver.change(MEMORY, "denied")
    assert sorted(seen) == ["first", "twice"]


def test_async_callbacks_are_rejected(make):
    driver = make({})

    async def callback(permission, state):
        return None

    with pytest.raises(TypeError):
        driver.perms.on_change(callback)

    class AsyncCallable:
        async def __call__(self, permission, state):
            return None

    with pytest.raises(TypeError):
        driver.perms.on_change(AsyncCallable())

    class SyncCallable:
        def __call__(self, permission, state):
            return None

    driver.perms.on_change(SyncCallable()).cancel()

    with pytest.raises(TypeError):
        driver.perms.on_change(functools.partial(AsyncCallable()))

    class SyncClassWithAsyncInstances:
        # Calling the class constructs an instance; nothing is awaited.
        async def __call__(self):
            return None

        def __init__(self, permission=None, state=None):
            pass

    driver.perms.on_change(SyncClassWithAsyncInstances).cancel()


# ---------------------------------------------------------------------------
# Real implementation only: snapshots from the host
# ---------------------------------------------------------------------------


class Log:
    def __init__(self):
        self.lines = []

    def __getattr__(self, level):
        return lambda message, *args: self.lines.append((level, message.format(*args)))


def _snapshot(revision, **statuses):
    return {
        "revision": revision,
        "states": {
            permission: {"status": statuses.get(permission, "undecided"), "background": False,
                         "enforced": PERMISSIONS[permission].enforced}
            for permission in PERMISSIONS
        },
    }


def test_before_any_snapshot_enforced_permissions_read_as_denied():
    perms = PluginPermissions()
    assert perms.revision is None
    assert perms.get(MEMORY) == PermissionState("denied", enforced=True)
    assert perms.get(PUSH) == PermissionState("undecided", enforced=False)


def test_an_older_revision_is_ignored():
    perms = PluginPermissions()
    perms.apply(_snapshot(1))
    seen = []
    perms.on_change(lambda permission, state: seen.append(state.status))
    assert perms.apply(_snapshot(5, **{MEMORY: "granted"}))
    assert not perms.apply(_snapshot(4, **{MEMORY: "denied"}))
    assert not perms.apply(_snapshot(5, **{MEMORY: "denied"}))
    assert perms.get(MEMORY).status == "granted" and perms.revision == 5
    assert seen == ["granted"]


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {"states": {}},
        {"revision": True, "states": {}},
        {"revision": 2, "states": []},
        {"revision": 2, "states": {MEMORY: {"status": "disclose"}}},
        {"revision": 2, "states": {MEMORY: {"status": "granted", "enforced": "yes"}}},
    ],
)
def test_a_malformed_snapshot_is_logged_and_the_copy_kept(payload):
    log = Log()
    perms = PluginPermissions(log)
    perms.apply(_snapshot(1, **{MEMORY: "granted"}))
    assert perms.apply(payload) is False
    assert perms.get(MEMORY).status == "granted" and perms.revision == 1
    assert log.lines and log.lines[0][0] == "error"


def test_ids_a_newer_host_knows_are_ignored_and_missing_ones_read_as_unavailable():
    perms = PluginPermissions()
    payload = {"revision": 1, "states": {"plugin_sdk.future:thing": {"status": "granted"},
                                         MEMORY: {"status": "granted"}}}
    assert perms.apply(payload)
    assert perms.get(MEMORY).status == "granted"
    assert perms.get(NETWORK).status == "denied"


def test_the_command_message_itself_is_a_valid_snapshot():
    perms = PluginPermissions()
    assert perms.apply({"type": "PERMISSIONS_UPDATE", **_snapshot(3, **{NETWORK: "granted"})})
    assert perms.get(NETWORK).status == "granted"


def test_a_failing_callback_is_logged_and_the_others_still_run():
    log = Log()
    perms = PluginPermissions(log)
    perms.apply(_snapshot(1))
    seen = []

    def broken(permission, state):
        raise RuntimeError("boom")

    perms.on_change(broken)
    perms.on_change(lambda permission, state: seen.append(permission))
    perms.apply(_snapshot(2, **{MEMORY: "granted"}))
    assert seen == [MEMORY]
    assert any(level == "exception" for level, _ in log.lines)


def test_a_callback_raising_cancelled_error_does_not_stop_the_others():
    import asyncio

    log = Log()
    perms = PluginPermissions(log)
    perms.apply(_snapshot(1))
    seen = []

    def cancels(permission, state):
        raise asyncio.CancelledError

    perms.on_change(cancels)
    perms.on_change(lambda permission, state: seen.append(permission))
    assert perms.apply(_snapshot(2, **{MEMORY: "granted"}))
    assert seen == [MEMORY]
    assert any(level == "exception" for level, _ in log.lines)


def test_a_callback_returning_a_coroutine_is_closed_and_logged():
    log = Log()
    perms = PluginPermissions(log)

    async def later():
        return None

    perms.apply(_snapshot(1))
    perms.on_change(lambda permission, state: later())
    perms.apply(_snapshot(2, **{MEMORY: "granted"}))
    assert any(level == "error" and "synchronous" in line for level, line in log.lines)


async def test_a_context_from_before_permissions_keeps_its_methods_and_falls_back():
    from types import SimpleNamespace

    from plugin.sdk.shared.core import context as context_module
    from plugin.sdk.shared.core.context import ensure_sdk_context

    old = SimpleNamespace(
        plugin_id="old", metadata={}, logger=None, config_path=None, bus=None, images=None, models=None,
    )
    for name in context_module._SDK_CONTEXT_METHOD_NAMES:
        setattr(old, name, lambda: None)
    calls = []

    async def run_update(**kwargs):  # no run_update_async on this context
        calls.append(("run_update", kwargs))
        return "updated"

    async def export_push(**kwargs):  # no export_push_async either
        calls.append(("export_push", kwargs))
        return "pushed"

    old.run_update, old.export_push = run_update, export_push
    context = ensure_sdk_context(old)
    assert context is not old and context.plugin_id == "old"
    assert ensure_sdk_context(context) is context
    assert await context.run_update(run_id="r", progress=0.5) == "updated"
    assert await context.export_push(export_type="text", text="hi") == "pushed"
    assert calls == [
        ("run_update", {"run_id": "r", "progress": 0.5}),
        ("export_push", {"export_type": "text", "text": "hi"}),
    ]
    context.marker = 1
    assert old.marker == 1
    assert context.permissions is context.permissions
    assert context.permissions.get(MEMORY) == PermissionState("denied", enforced=True)
    attached = PluginPermissions()
    context.permissions = attached  # or attached by the host later: it wins
    assert old.permissions is attached and context.permissions is attached


def test_a_context_with_permissions_is_used_as_is():
    from types import SimpleNamespace

    from plugin.sdk.shared.core import context as context_module
    from plugin.sdk.shared.core.context import ensure_sdk_context

    current = SimpleNamespace(**{name: None for name in context_module._SDK_CONTEXT_ATTR_NAMES})
    current.permissions = PluginPermissions()
    for name in context_module._SDK_CONTEXT_METHOD_NAMES:
        setattr(current, name, lambda: None)
    assert ensure_sdk_context(current) is current
