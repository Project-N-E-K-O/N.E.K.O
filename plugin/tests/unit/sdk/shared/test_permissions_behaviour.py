"""One behaviour suite for ``FakePermissions`` and the real ``ctx.permissions``.

A plugin tested against the fake must see the same ``get`` / ``on_change``
behaviour in a real plugin process. Each case runs against both; the real
one is driven by host snapshots, the fake by ``set``.
"""

from __future__ import annotations

import pytest

from plugin.sdk.plugin import FakePermissions, PermissionState
from plugin.sdk.shared.permissions import PERMISSIONS, UNENFORCED_PERMISSIONS
from plugin.sdk.shared.permissions.runtime import PluginPermissions, state_to_wire

pytestmark = pytest.mark.plugin_unit

MEMORY = "plugin_sdk.main_agent.memory:read"
NETWORK = "system.network:connect"
PUSH = "plugin_sdk.main_agent.messages:push"
ENV = "system.env:read"


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


def test_async_callbacks_are_rejected(make):
    driver = make({})

    async def callback(permission, state):
        return None

    with pytest.raises(TypeError):
        driver.perms.on_change(callback)


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


def test_a_callback_returning_a_coroutine_is_closed_and_logged():
    log = Log()
    perms = PluginPermissions(log)

    async def later():
        return None

    perms.apply(_snapshot(1))
    perms.on_change(lambda permission, state: later())
    perms.apply(_snapshot(2, **{MEMORY: "granted"}))
    assert any(level == "error" and "synchronous" in line for level, line in log.lines)
