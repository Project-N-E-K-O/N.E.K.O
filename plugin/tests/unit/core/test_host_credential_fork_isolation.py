"""A forked plugin child must not inherit other hosts' uplink credentials.

Plugin processes are started with a bare ``multiprocessing.Process``, so on
POSIX they are forked from the process that owns ``state.plugin_hosts``. That
dict holds every running host's ``HostTransport``, including its per-host uplink
token and its endpoint strings -- enough for plugin B to build a
``ChildTransport`` with plugin A's credential and send frames A will accept as
its own.

``plane_bridge``'s hook covers only the single module-level ingest token; these
are per-host and live on shared state, so they need their own scrub.

Note what this can and cannot claim: the scrub removes the reachable path, not
the bytes. A plugin that goes looking through its own heap is not stopped by it
-- only a non-inheriting start method would be.
"""

from __future__ import annotations

import ast
import os
import sys
import threading
import weakref
from pathlib import Path
from types import SimpleNamespace

import pytest

from plugin.core import zmq_transport
from plugin.core import host as host_module


class _InheritedReferences(SimpleNamespace):
    def clear_inherited_plugin_references(self):
        self.plugin_hosts.clear()
        self._plugin_downlink_senders.clear()
        self._plugin_downlink_senders_lock = threading.Lock()


def _scrub():
    zmq_transport._scrub_inherited_transport_credentials()
    host_module._scrub_inherited_host_credentials()


@pytest.fixture(autouse=True)
def isolate_live_credentials(monkeypatch):
    monkeypatch.setattr(zmq_transport, "_HOST_TRANSPORTS", weakref.WeakSet())
    monkeypatch.setattr(host_module, "_PLUGIN_HOSTS", weakref.WeakSet())
    monkeypatch.setattr(host_module, "_FORKING_HOST", threading.local())
    monkeypatch.setattr(host_module, "state", _InheritedReferences(
        plugin_hosts={}, _plugin_downlink_senders={}
    ))


class _FakeTransport:
    def __init__(self, token: str) -> None:
        self._uplink_token = token
        self.downlink_endpoint = "tcp://127.0.0.1:5555"
        zmq_transport._HOST_TRANSPORTS.add(self)

    def clear_inherited_credentials(self):
        self._uplink_token = ""


@pytest.mark.plugin_unit
def test_the_scrub_blanks_tokens_and_empties_the_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mutation: drop the ``hosts.clear()`` or the token overwrite."""
    victim = _FakeTransport("victim-uplink-secret")
    host = _StartingHost("victim-model-secret")
    host.transport = victim
    hosts = {"victim": host}
    fake_state_mod = _InheritedReferences(plugin_hosts=hosts, _plugin_downlink_senders={})
    monkeypatch.setattr(host_module, "state", fake_state_mod)

    _scrub()

    assert hosts == {}, "继承来的 host 表还在，另一个插件的 transport 直接可读"
    assert host._model_gateway_token == ""
    assert victim._uplink_token == "", (
        "只丢了引用没打掉值——子进程里别处还引着这个对象就白清了"
    )


@pytest.mark.plugin_unit
def test_the_scrub_survives_a_child_that_never_imported_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """It runs in an ``after_in_child`` hook, where raising is not an option.

    Reading ``sys.modules`` rather than importing is deliberate: an import
    inside a fork hook can deadlock on the import lock a parent thread was
    holding at fork time.
    """
    monkeypatch.delitem(sys.modules, "plugin.core.state", raising=False)

    _scrub()  # must not raise


@pytest.mark.plugin_unit
def test_the_hook_is_wired_wherever_fork_exists() -> None:
    """Both pytest jobs run windows-latest, where ``os.fork`` does not exist.

    A fork-based behavioural test therefore skips everywhere it would run, and
    "the hook was never registered" would be an invisible mutation. This asserts
    the wiring from the source instead, and the registration flag on POSIX.
    """
    for owner, callback, flag in (
        (zmq_transport, "_scrub_inherited_transport_credentials", "_TRANSPORT_CREDENTIAL_FORK_HOOK_REGISTERED"),
        (host_module, "_scrub_inherited_host_credentials", "_HOST_CREDENTIAL_FORK_HOOK_REGISTERED"),
    ):
        tree = ast.parse(Path(owner.__file__).read_text(encoding="utf-8"))
        assert any(
            isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "register_at_fork"
            and any(kw.arg == "after_in_child" and isinstance(kw.value, ast.Name)
                    and kw.value.id == callback for kw in node.keywords)
            for node in ast.walk(tree)
        )
        if hasattr(os, "register_at_fork"):
            assert getattr(owner, flag)


class _StartingHost:
    def __init__(self, token):
        self.transport = _FakeTransport(token)
        self._model_gateway_token = token
        self._model_gateway_options = {"token": token}
        host_module._PLUGIN_HOSTS.add(self)

    def clear_inherited_credentials(self, *, keep_launch_options=False):
        self._model_gateway_token = ""
        if not keep_launch_options:
            self._model_gateway_options.clear()


class _StartingCommunication:
    def __init__(self, transport):
        self.transport = transport

    def send_plugin_response(self):
        pass


def test_scrub_accepts_a_host_still_constructing():
    host = host_module.PluginHost.__new__(host_module.PluginHost)
    host_module._PLUGIN_HOSTS.add(host)
    _scrub()
    assert host._model_gateway_token == ""


@pytest.mark.plugin_unit
def test_scrub_covers_unregistered_hosts_and_downlink_senders(monkeypatch):
    own = _StartingHost("own-token")
    sibling = _StartingHost("sibling-token")
    host_module._PLUGIN_HOSTS.update((own, sibling))
    host_module._FORKING_HOST.host = own
    sender = _StartingCommunication(sibling.transport)
    senders = {"sibling": sender.send_plugin_response}
    fake_state = _InheritedReferences(plugin_hosts={}, _plugin_downlink_senders=senders)
    monkeypatch.setattr(host_module, "state", fake_state)

    _scrub()

    assert sibling.transport._uplink_token == ""
    assert sibling._model_gateway_token == ""
    assert sibling._model_gateway_options == {}
    assert own._model_gateway_options == {"token": "own-token"}
    assert senders == {}
    assert not host_module._PLUGIN_HOSTS
    assert fake_state._plugin_downlink_senders_lock.acquire(blocking=False)
    fake_state._plugin_downlink_senders_lock.release()


@pytest.mark.plugin_unit
def test_transport_is_tracked_before_host_registration():
    transport = zmq_transport.HostTransport()
    try:
        assert transport in zmq_transport._HOST_TRANSPORTS
        _scrub()
        assert transport.uplink_token == ""
        assert not zmq_transport._HOST_TRANSPORTS
    finally:
        transport.close()


@pytest.mark.plugin_unit
@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires POSIX fork")
def test_real_fork_scrubs_inflight_sibling_credentials(monkeypatch):
    import select
    import signal

    own = _StartingHost("own-token")
    sibling = _StartingHost("sibling-token")
    host_module._PLUGIN_HOSTS.update((own, sibling))
    host_module._FORKING_HOST.host = own
    sender = _StartingCommunication(sibling.transport)
    fake_state = _InheritedReferences(
        plugin_hosts={}, _plugin_downlink_senders={"sibling": sender.send_plugin_response}
    )
    monkeypatch.setattr(host_module, "state", fake_state)
    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(read_fd)
        valid = (
            not sibling.transport._uplink_token
            and not sibling._model_gateway_options
            and own._model_gateway_options == {"token": "own-token"}
            and not fake_state._plugin_downlink_senders
        )
        os.write(write_fd, b"ok" if valid else b"failed")
        os._exit(0)
    os.close(write_fd)
    try:
        assert select.select([read_fd], [], [], 5)[0], "fork child did not finish"
        assert os.read(read_fd, 16) == b"ok"
        assert sibling.transport._uplink_token == "sibling-token"  # Parent is unchanged.
    finally:
        os.close(read_fd)
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        os.waitpid(pid, 0)


@pytest.mark.plugin_unit
def test_failed_host_scrub_does_not_skip_other_hosts_or_state(monkeypatch):
    class BrokenHost:
        def clear_inherited_credentials(self, **kwargs):
            raise RuntimeError("partial host")

    sibling = _StartingHost("sibling-token")
    broken = BrokenHost()
    monkeypatch.setattr(host_module, "_PLUGIN_HOSTS", [broken, sibling])
    fake_state = _InheritedReferences(plugin_hosts={}, _plugin_downlink_senders={"sibling": object()})
    monkeypatch.setattr(host_module, "state", fake_state)
    host_module._scrub_inherited_host_credentials()
    assert sibling._model_gateway_token == ""
    assert not fake_state._plugin_downlink_senders
    assert not host_module._PLUGIN_HOSTS
