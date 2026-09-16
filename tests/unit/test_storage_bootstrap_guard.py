# -*- coding: utf-8 -*-
"""Causal desktop-owner guard for launcher storage bootstrap boundaries."""

from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest

from launcher_core import runtime
from launcher_core import storage_bootstrap_guard as guard_module
from launcher_core.storage_bootstrap_guard import (
    STORAGE_BOOTSTRAP_GUARD_CAPABILITY_ENV,
    STORAGE_BOOTSTRAP_GUARD_FD_ENV,
    StorageBootstrapGuardChannel,
    StorageBootstrapGuardError,
)


LAUNCH_ID = "a" * 32


def _pipe_channel(*, timeout: float = 0.1):
    read_fd, write_fd = os.pipe()
    os.set_inheritable(read_fd, False)
    return (
        StorageBootstrapGuardChannel(
            read_fd,
            enabled=True,
            response_timeout=timeout,
        ),
        write_fd,
    )


def _response(payload: dict) -> bytes:
    return (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")


def _exact_response(payload: dict, decision: str = "ACK") -> dict:
    return {
        "protocol_version": 1,
        "decision": decision,
        "guard_id": payload["guard_id"],
        "launch_id": payload["launch_id"],
    }


@pytest.mark.unit
def test_guard_capability_missing_keeps_legacy_startup_and_clears_env(monkeypatch):
    environ = {STORAGE_BOOTSTRAP_GUARD_FD_ENV: "5"}
    monkeypatch.setattr(
        guard_module.os,
        "dup",
        lambda _fd: pytest.fail("disabled guard must not consume fd 5"),
    )

    channel = StorageBootstrapGuardChannel.from_environment(environ)
    emitted = []

    assert channel.enabled is False
    assert channel.request(
        phase="initial_bootstrap",
        launch_id=LAUNCH_ID,
        emit_event=lambda *args: emitted.append(args),
    ) is None
    assert environ == {}
    assert emitted == []


@pytest.mark.unit
def test_guard_environment_claims_only_fd5_and_makes_private_copy_non_inheritable(
    monkeypatch,
):
    source_read_fd, write_fd = os.pipe()
    real_dup = os.dup
    real_close = os.close
    closed = []

    def _duplicate_advertised_fd(fd):
        assert fd == 5
        return real_dup(source_read_fd)

    def _record_close(fd):
        closed.append(fd)
        if fd != 5:
            real_close(fd)

    monkeypatch.setattr(guard_module.os, "dup", _duplicate_advertised_fd)
    monkeypatch.setattr(guard_module.os, "close", _record_close)
    environ = {
        STORAGE_BOOTSTRAP_GUARD_CAPABILITY_ENV: "1",
        STORAGE_BOOTSTRAP_GUARD_FD_ENV: "5",
    }

    channel = StorageBootstrapGuardChannel.from_environment(environ)
    owned_fd = channel.fd
    try:
        assert channel.enabled is True
        assert owned_fd is not None
        assert os.get_inheritable(owned_fd) is False
        assert 5 in closed
        assert environ == {}
    finally:
        channel.close()
        real_close(source_read_fd)
        real_close(write_fd)


@pytest.mark.unit
def test_opted_in_guard_with_missing_fd_fails_closed_before_emitting_request():
    channel = StorageBootstrapGuardChannel.from_environment(
        {STORAGE_BOOTSTRAP_GUARD_CAPABILITY_ENV: "1"}
    )
    emitted = []

    assert channel.enabled is True
    with pytest.raises(StorageBootstrapGuardError, match="invalid_fd_capability"):
        channel.request(
            phase="initial_bootstrap",
            launch_id=LAUNCH_ID,
            emit_event=lambda *args: emitted.append(args),
        )
    assert emitted == []


@pytest.mark.unit
def test_guard_accepts_exact_ack_and_emits_correlated_release():
    channel, write_fd = _pipe_channel()
    events = []

    def _emit(event, payload):
        events.append((event, payload))
        if event == "storage_bootstrap_guard_request":
            os.write(write_fd, _response(_exact_response(payload)))

    try:
        guard_id = channel.request(
            phase="initial_bootstrap",
            launch_id=LAUNCH_ID,
            emit_event=_emit,
        )
        channel.release(guard_id, outcome="ready", emit_event=_emit)
    finally:
        channel.close()
        os.close(write_fd)

    assert len(guard_id) == 32
    assert guard_id != LAUNCH_ID
    assert events == [
        (
            "storage_bootstrap_guard_request",
            {
                "protocol_version": 1,
                "guard_id": guard_id,
                "launch_id": LAUNCH_ID,
                "phase": "initial_bootstrap",
            },
        ),
        (
            "storage_bootstrap_guard_release",
            {
                "protocol_version": 1,
                "guard_id": guard_id,
                "launch_id": LAUNCH_ID,
                "phase": "initial_bootstrap",
                "outcome": "ready",
            },
        ),
    ]


@pytest.mark.unit
def test_guard_abort_fails_closed_without_release():
    channel, write_fd = _pipe_channel()
    events = []

    def _emit(event, payload):
        events.append((event, payload))
        os.write(write_fd, _response(_exact_response(payload, "ABORT")))

    try:
        with pytest.raises(StorageBootstrapGuardError, match="aborted"):
            channel.request(
                phase="initial_bootstrap",
                launch_id=LAUNCH_ID,
                emit_event=_emit,
            )
    finally:
        channel.close()
        os.close(write_fd)

    assert [event for event, _payload in events] == ["storage_bootstrap_guard_request"]


@pytest.mark.unit
def test_guard_eof_fails_closed():
    channel, write_fd = _pipe_channel()
    os.close(write_fd)
    try:
        with pytest.raises(StorageBootstrapGuardError, match="eof"):
            channel.request(
                phase="initial_bootstrap",
                launch_id=LAUNCH_ID,
                emit_event=lambda *_args: None,
            )
    finally:
        channel.close()


@pytest.mark.unit
@pytest.mark.parametrize(
    "wire_payload",
    (
        b"not-json\n",
        _response(
            {
                "protocol_version": 1,
                "decision": "ACK",
                "guard_id": "b" * 32,
                "launch_id": LAUNCH_ID,
                "unexpected": True,
            }
        ),
        _response(
            {
                "protocol_version": True,
                "decision": "ACK",
                "guard_id": "b" * 32,
                "launch_id": LAUNCH_ID,
            }
        ),
        _response(
            {
                "protocol_version": 1,
                "decision": "ack",
                "guard_id": "b" * 32,
                "launch_id": LAUNCH_ID,
            }
        ),
    ),
)
def test_guard_rejects_invalid_response_frames(wire_payload):
    channel, write_fd = _pipe_channel()

    def _emit(_event, _payload):
        os.write(write_fd, wire_payload)

    try:
        with pytest.raises(StorageBootstrapGuardError, match="invalid_response"):
            channel.request(
                phase="initial_bootstrap",
                launch_id=LAUNCH_ID,
                emit_event=_emit,
            )
    finally:
        channel.close()
        os.close(write_fd)


@pytest.mark.unit
def test_guard_timeout_is_bounded_and_fails_closed():
    channel, write_fd = _pipe_channel(timeout=0.02)
    try:
        with pytest.raises(StorageBootstrapGuardError, match="timeout"):
            channel.request(
                phase="initial_bootstrap",
                launch_id=LAUNCH_ID,
                emit_event=lambda *_args: None,
            )
    finally:
        os.close(write_fd)
        channel.close()


@pytest.mark.unit
def test_guard_stale_id_cannot_authorize_current_request():
    channel, write_fd = _pipe_channel(timeout=0.02)

    def _emit(_event, payload):
        stale = _exact_response(payload)
        stale["guard_id"] = "b" * 32
        os.write(write_fd, _response(stale))

    try:
        with pytest.raises(StorageBootstrapGuardError, match="timeout"):
            channel.request(
                phase="initial_bootstrap",
                launch_id=LAUNCH_ID,
                emit_event=_emit,
            )
    finally:
        os.close(write_fd)
        channel.close()


@pytest.mark.unit
def test_guard_ignores_stale_id_but_accepts_following_exact_ack():
    channel, write_fd = _pipe_channel()

    def _emit(_event, payload):
        stale = _exact_response(payload)
        stale["launch_id"] = "b" * 32
        os.write(
            write_fd,
            _response(stale) + _response(_exact_response(payload)),
        )

    try:
        guard_id = channel.request(
            phase="initial_bootstrap",
            launch_id=LAUNCH_ID,
            emit_event=_emit,
        )
        channel.release(guard_id, outcome="ready", emit_event=lambda *_args: None)
    finally:
        os.close(write_fd)
        channel.close()

    assert len(guard_id) == 32


@pytest.mark.unit
def test_guard_requires_fresh_ack_for_consecutive_operations():
    channel, write_fd = _pipe_channel()
    requests = []
    releases = []

    def _emit(event, payload):
        if event == "storage_bootstrap_guard_request":
            requests.append(payload)
            os.write(write_fd, _response(_exact_response(payload)))
        else:
            releases.append(payload)

    try:
        first = channel.request(
            phase="initial_bootstrap",
            launch_id=LAUNCH_ID,
            emit_event=_emit,
        )
        channel.release(first, outcome="ready", emit_event=_emit)
        second = channel.request(
            phase="restart_resolution",
            launch_id=LAUNCH_ID,
            emit_event=_emit,
        )
        channel.release(second, outcome="no_restart", emit_event=_emit)
    finally:
        channel.close()
        os.close(write_fd)

    assert first != second
    assert [item["guard_id"] for item in requests] == [first, second]
    assert [item["guard_id"] for item in releases] == [first, second]
    assert [item["phase"] for item in requests] == [
        "initial_bootstrap",
        "restart_resolution",
    ]


@pytest.mark.unit
def test_guard_closes_private_fd_in_posix_fork_child_copy():
    channel, write_fd = _pipe_channel()
    try:
        channel._close_after_fork_in_child()
        assert channel.fd is None
        assert channel.enabled is False
    finally:
        os.close(write_fd)


class _RecordingGuard:
    def __init__(self, order, *, abort=False):
        self.order = order
        self.abort = abort

    def request(self, *, phase, launch_id, emit_event):
        self.order.append(("guard_request", phase))
        if self.abort:
            raise StorageBootstrapGuardError("aborted")
        return "c" * 32

    def release(self, guard_id, *, outcome, emit_event):
        self.order.append(("guard_release", outcome))


@pytest.mark.unit
def test_initial_guard_covers_layout_resolution_and_phase0(monkeypatch):
    order = []
    monkeypatch.setattr(runtime, "_storage_bootstrap_guard", _RecordingGuard(order))
    monkeypatch.setattr(runtime, "LAUNCH_ID", LAUNCH_ID)
    monkeypatch.setattr(
        runtime,
        "_resolve_storage_layout_for_launch",
        lambda: order.append(("resolve", None)) or {},
    )
    monkeypatch.setattr(
        runtime,
        "_prepare_cloudsave_runtime_for_launch",
        lambda: order.append(("phase0", None)) or {},
    )

    storage_bootstrap, limited = runtime._initialize_storage_generation_for_launch()

    assert storage_bootstrap == {}
    assert limited is False
    assert order == [
        ("guard_request", "initial_bootstrap"),
        ("resolve", None),
        ("phase0", None),
        ("guard_release", "ready"),
    ]


@pytest.mark.unit
def test_initial_guard_abort_prevents_layout_resolution(monkeypatch):
    order = []
    monkeypatch.setattr(
        runtime,
        "_storage_bootstrap_guard",
        _RecordingGuard(order, abort=True),
    )
    monkeypatch.setattr(runtime, "LAUNCH_ID", LAUNCH_ID)
    monkeypatch.setattr(
        runtime,
        "_resolve_storage_layout_for_launch",
        lambda: pytest.fail("storage resolution must not run after guard abort"),
    )

    with pytest.raises(StorageBootstrapGuardError, match="aborted"):
        runtime._initialize_storage_generation_for_launch()

    assert order == [("guard_request", "initial_bootstrap")]


@pytest.mark.unit
def test_initial_limited_recovery_releases_only_after_resolution(monkeypatch):
    order = []
    monkeypatch.setattr(runtime, "_storage_bootstrap_guard", _RecordingGuard(order))
    monkeypatch.setattr(runtime, "LAUNCH_ID", LAUNCH_ID)
    monkeypatch.setattr(
        runtime,
        "_resolve_storage_layout_for_launch",
        lambda: order.append(("resolve", None))
        or {"startup_limited": True, "limited_mode_reason": "recovery_required"},
    )
    monkeypatch.setattr(
        runtime,
        "_prepare_cloudsave_runtime_for_launch",
        lambda: pytest.fail("phase-0 must stay disabled in limited recovery"),
    )

    _storage_bootstrap, limited = runtime._initialize_storage_generation_for_launch()

    assert limited is True
    assert order == [
        ("guard_request", "initial_bootstrap"),
        ("resolve", None),
        ("guard_release", "recovery_limited"),
    ]


@pytest.mark.unit
def test_restart_entry_requests_new_guard_before_reading_storage(monkeypatch):
    order = []
    config_manager = SimpleNamespace(
        load_root_state=lambda: order.append(("root_state", None))
        or {"mode": runtime.ROOT_MODE_NORMAL},
    )
    monkeypatch.setattr(runtime, "_owner_death_in_progress", False)
    monkeypatch.setattr(runtime, "_storage_bootstrap_guard", _RecordingGuard(order))
    monkeypatch.setattr(runtime, "LAUNCH_ID", LAUNCH_ID)
    monkeypatch.setattr(
        runtime,
        "get_config_manager",
        lambda *_args, **_kwargs: order.append(("config", None)) or config_manager,
    )
    monkeypatch.setattr(
        runtime,
        "load_storage_migration",
        lambda _manager: order.append(("checkpoint", None)) or None,
    )
    monkeypatch.setattr(
        runtime,
        "_resolve_storage_layout_for_launch",
        lambda: order.append(("resolve", None))
        or {
            "layout": {"selected_root": "/tmp/N.E.K.O"},
            "migration_result": {"attempted": False, "completed": False},
        },
    )

    assert runtime._maybe_schedule_storage_restart() is False
    assert order[0] == ("guard_request", "restart_resolution")
    assert order[-1] == ("guard_release", "no_restart")
    assert order.index(("guard_request", "restart_resolution")) < order.index(
        ("config", None)
    )


@pytest.mark.unit
def test_restart_guard_abort_prevents_all_storage_reads(monkeypatch):
    order = []
    monkeypatch.setattr(runtime, "_owner_death_in_progress", False)
    monkeypatch.setattr(
        runtime,
        "_storage_bootstrap_guard",
        _RecordingGuard(order, abort=True),
    )
    monkeypatch.setattr(runtime, "LAUNCH_ID", LAUNCH_ID)
    monkeypatch.setattr(
        runtime,
        "get_config_manager",
        lambda *_args, **_kwargs: pytest.fail("storage read must not run after guard abort"),
    )

    with pytest.raises(StorageBootstrapGuardError, match="aborted"):
        runtime._maybe_schedule_storage_restart()

    assert order == [("guard_request", "restart_resolution")]
