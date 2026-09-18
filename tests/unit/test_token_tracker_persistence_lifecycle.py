import asyncio
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest


def _make_tracker(tmp_path: Path, monkeypatch):
    import utils.token_tracker as token_tracker
    import utils.token_tracker.reporting as reporting

    monkeypatch.setattr(
        token_tracker,
        "get_config_manager",
        lambda: SimpleNamespace(config_dir=tmp_path),
    )
    monkeypatch.setattr(token_tracker.atexit, "register", Mock())
    monkeypatch.setattr(reporting, "_TELEMETRY_SERVER_URL", "")
    return token_tracker.TokenTracker()


@pytest.mark.unit
def test_suspended_atexit_does_not_emit_save_or_flush_and_resume_reenables_save(
    tmp_path,
    monkeypatch,
):
    import utils.instrument as instrument

    tracker = _make_tracker(tmp_path, monkeypatch)
    tracker.record_app_start(process="agent_server")
    assert tracker._dirty is True
    instrument.snapshot()

    tracker.suspend_persistence("agent_server")
    tracker.save()
    assert tracker._dirty is True
    assert not (tmp_path / "token_usage.json").exists()

    save = Mock(wraps=tracker.save)
    event_flush = Mock()
    with patch.object(tracker, "save", save), patch.object(
        instrument, "event", Mock()
    ) as emit_event, patch.object(
        instrument, "counter", Mock()
    ) as emit_counter, patch.object(
        instrument, "histogram", Mock()
    ) as emit_histogram, patch(
        "utils.event_logger.EventLogger.get_instance",
        return_value=SimpleNamespace(flush=event_flush),
    ):
        tracker._atexit_save()

        save.assert_not_called()
        emit_event.assert_not_called()
        emit_counter.assert_not_called()
        emit_histogram.assert_not_called()
        event_flush.assert_not_called()
        assert tracker._dirty is True

        tracker.resume_persistence("agent_server")
        tracker.save()

    assert tracker._dirty is False
    assert (tmp_path / "token_usage.json").is_file()


@pytest.mark.unit
def test_merged_tracker_stays_suspended_until_every_service_owner_resumes(
    tmp_path,
    monkeypatch,
):
    tracker = _make_tracker(tmp_path, monkeypatch)

    tracker.suspend_persistence("agent_server")
    tracker.suspend_persistence("memory_server")
    tracker.suspend_persistence("main_server")

    tracker.resume_persistence("agent_server")
    assert tracker.is_persistence_suspended() is True
    tracker.resume_persistence("memory_server")
    assert tracker.is_persistence_suspended() is True
    tracker.resume_persistence("main_server")
    assert tracker.is_persistence_suspended() is False


@pytest.mark.unit
def test_get_existing_instance_does_not_construct_tracker(monkeypatch):
    from utils.token_tracker import TokenTracker

    monkeypatch.setattr(TokenTracker, "_instance", None)

    assert TokenTracker.get_existing_instance() is None


@pytest.mark.unit
def test_suspend_waits_for_inflight_save_before_returning(tmp_path, monkeypatch):
    import utils.token_tracker.reporting as reporting

    tracker = _make_tracker(tmp_path, monkeypatch)
    tracker.record(
        model="test",
        prompt_tokens=1,
        completion_tokens=0,
        total_tokens=1,
    )
    write_started = threading.Event()
    allow_write = threading.Event()
    suspend_returned = threading.Event()
    ordering = []
    real_atomic_write_json = reporting.atomic_write_json

    def _blocking_atomic_write(path, payload):
        write_started.set()
        assert allow_write.wait(timeout=2)
        real_atomic_write_json(path, payload)
        ordering.append("write_completed")

    def _suspend():
        tracker.suspend_persistence("agent_server")
        ordering.append("suspend_returned")
        suspend_returned.set()

    monkeypatch.setattr(reporting, "atomic_write_json", _blocking_atomic_write)
    save_thread = threading.Thread(target=tracker.save)
    save_thread.start()
    assert write_started.wait(timeout=2)

    suspend_thread = threading.Thread(target=_suspend)
    suspend_thread.start()
    suspend_was_blocked = not suspend_returned.wait(timeout=0.1)

    allow_write.set()
    save_thread.join(timeout=2)
    suspend_thread.join(timeout=2)

    assert not save_thread.is_alive()
    assert not suspend_thread.is_alive()
    assert suspend_was_blocked is True
    assert ordering == ["write_completed", "suspend_returned"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_suspend_waits_for_inflight_periodic_event_flush(tmp_path, monkeypatch):
    import utils.instrument as instrument

    tracker = _make_tracker(tmp_path, monkeypatch)
    tracker._save_interval = 0
    flush_started = threading.Event()
    allow_flush = threading.Event()
    suspend_started = threading.Event()
    suspend_returned = threading.Event()
    ordering = []

    def _blocking_flush():
        flush_started.set()
        assert allow_flush.wait(timeout=2)
        ordering.append("flush_completed")

    def _suspend():
        suspend_started.set()
        tracker.suspend_persistence("memory_server")
        ordering.append("suspend_returned")
        suspend_returned.set()

    monkeypatch.setattr(instrument, "has_data", lambda: False)
    monkeypatch.setattr(
        "utils.event_logger.EventLogger.get_instance",
        lambda: SimpleNamespace(flush=_blocking_flush),
    )
    periodic_task = asyncio.create_task(tracker._periodic_save_loop())
    assert await asyncio.to_thread(flush_started.wait, 2)

    suspend_task = asyncio.create_task(asyncio.to_thread(_suspend))
    assert await asyncio.to_thread(suspend_started.wait, 2)
    suspend_was_blocked = not await asyncio.to_thread(suspend_returned.wait, 0.1)

    allow_flush.set()
    await asyncio.wait_for(suspend_task, timeout=2)
    periodic_task.cancel()
    await asyncio.gather(periodic_task, return_exceptions=True)

    assert suspend_was_blocked is True
    assert ordering == ["flush_completed", "suspend_returned"]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("tracker_state", "recovery_mode", "expected_flushes"),
    [
        ("suspended", "", 0),
        ("active", "", 1),
        ("missing", "", 1),
        ("missing", "recovery_required", 0),
    ],
)
def test_crash_hook_respects_existing_tracker_persistence_gate(
    tmp_path,
    monkeypatch,
    tracker_state,
    recovery_mode,
    expected_flushes,
):
    import utils.instrument as instrument
    from utils.token_tracker import TokenTracker
    from utils.token_tracker import hooks

    tracker = None
    if tracker_state != "missing":
        tracker = _make_tracker(tmp_path, monkeypatch)
        if tracker_state == "suspended":
            tracker.suspend_persistence("agent_server")
    if recovery_mode:
        monkeypatch.setenv("NEKO_STORAGE_RECOVERY_MODE", recovery_mode)
    else:
        monkeypatch.delenv("NEKO_STORAGE_RECOVERY_MODE", raising=False)

    flush = Mock()
    original_hook = Mock()
    emit_event = Mock()
    emit_counter = Mock()
    monkeypatch.setattr(TokenTracker, "_instance", tracker)
    monkeypatch.setattr(
        TokenTracker,
        "get_instance",
        Mock(side_effect=AssertionError("crash hook must not construct TokenTracker")),
    )
    monkeypatch.delattr(sys, "_neko_crash_hook_installed", raising=False)
    monkeypatch.setattr(sys, "excepthook", original_hook)
    monkeypatch.setattr(instrument, "event", emit_event)
    monkeypatch.setattr(instrument, "counter", emit_counter)
    monkeypatch.setattr(
        "utils.event_logger.EventLogger.get_instance",
        lambda: SimpleNamespace(flush=flush),
    )

    hooks._install_crash_excepthook()
    sys.excepthook(RuntimeError, RuntimeError("boom"), None)

    assert flush.call_count == expected_flushes
    assert emit_event.call_count == 1
    emit_counter.assert_called_once_with("crash", error_class="RuntimeError")
    original_hook.assert_called_once()
