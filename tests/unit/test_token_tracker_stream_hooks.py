"""Unit tests for streaming token-tracker wrappers on normal exit, break, close, and failure."""

from __future__ import annotations

import asyncio
from contextlib import aclosing
import gc
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import utils.token_tracker as tt
from utils.token_tracker.hooks import _handle_async_stream, _handle_sync_stream


def _chunk(prompt_tokens: int | None = None, completion_tokens: int | None = None, model: str = "gpt-test"):
    if prompt_tokens is None:
        return SimpleNamespace(usage=None, model=model)
    total = (prompt_tokens or 0) + (completion_tokens or 0)
    usage = SimpleNamespace(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total,
        prompt_tokens_details=None,
    )
    return SimpleNamespace(usage=usage, model=model)


class _FakeSyncStream:
    def __init__(self, items, error: BaseException | None = None):
        self._items = list(items)
        self._error = error
        self.closed = False
        self.exited = False

    def __iter__(self):
        for item in self._items:
            yield item
        if self._error is not None:
            raise self._error

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.exited = True
        return None


class _FakeAsyncStream:
    def __init__(self, items, error: BaseException | None = None):
        self._items = list(items)
        self._error = error
        self.closed = False
        self.exited = False

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for item in self._items:
            yield item
        if self._error is not None:
            raise self._error

    async def close(self):
        self.closed = True

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.exited = True
        return None


def test_sync_stream_normal_exhaustion_records_last_usage_once():
    rec = MagicMock()
    stream = _FakeSyncStream([_chunk(10, 2), _chunk(10, 5)])
    with patch.object(tt.TokenTracker, "get_instance", return_value=SimpleNamespace(record=rec)):
        wrapper = _handle_sync_stream(
            SimpleNamespace(), lambda *_a, **_kw: stream, (), {"model": "gpt-test"}, "conversation"
        )
        list(wrapper)
        wrapper.close()
        del wrapper
        gc.collect()

    rec.assert_called_once_with(
        model="gpt-test",
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=15,
        cached_tokens=0,
        call_type="conversation",
        success=True,
    )


def test_sync_stream_normal_exhaustion_without_usage_is_noop():
    rec = MagicMock()
    stream = _FakeSyncStream([_chunk(None), _chunk(None)])
    with patch.object(tt.TokenTracker, "get_instance", return_value=SimpleNamespace(record=rec)):
        wrapper = _handle_sync_stream(
            SimpleNamespace(), lambda *_a, **_kw: stream, (), {"model": "gpt-test"}, "conversation"
        )
        list(wrapper)
        wrapper.close()

    rec.assert_not_called()


def test_sync_stream_break_after_usage_chunk_records_once():
    rec = MagicMock()
    stream = _FakeSyncStream([_chunk(12, 3), _chunk(12, 8)])
    with patch.object(tt.TokenTracker, "get_instance", return_value=SimpleNamespace(record=rec)):
        wrapper = _handle_sync_stream(
            SimpleNamespace(), lambda *_a, **_kw: stream, (), {"model": "gpt-test"}, "conversation"
        )
        with wrapper:
            for _ in wrapper:
                break
        wrapper.close()
        del wrapper
        gc.collect()

    assert stream.exited is True
    assert stream.closed is True
    rec.assert_called_once_with(
        model="gpt-test",
        prompt_tokens=12,
        completion_tokens=3,
        total_tokens=15,
        cached_tokens=0,
        call_type="conversation",
        success=True,
    )


def test_sync_stream_break_before_usage_chunk_records_non_error_call_once():
    """Caller-initiated early break before usage chunk increments call_count without marking an API error."""
    rec = MagicMock()
    stream = _FakeSyncStream([_chunk(None), _chunk(10, 5)])
    with patch.object(tt.TokenTracker, "get_instance", return_value=SimpleNamespace(record=rec)):
        wrapper = _handle_sync_stream(
            SimpleNamespace(), lambda *_a, **_kw: stream, (), {"model": "gpt-early"}, "summary"
        )
        for _ in wrapper:
            break
        wrapper.close()

    rec.assert_called_once_with(
        model="gpt-early",
        prompt_tokens=0,
        completion_tokens=0,
        total_tokens=0,
        call_type="summary",
        success=True,
    )


def test_sync_stream_error_mid_stream_records_failed_once_and_reraises():
    rec = MagicMock()
    stream = _FakeSyncStream([_chunk(None)], error=RuntimeError("boom"))
    with patch.object(tt.TokenTracker, "get_instance", return_value=SimpleNamespace(record=rec)):
        wrapper = _handle_sync_stream(
            SimpleNamespace(), lambda *_a, **_kw: stream, (), {"model": "gpt-err"}, "conversation"
        )
        with pytest.raises(RuntimeError, match="boom"):
            list(wrapper)

    rec.assert_called_once_with(
        model="gpt-err",
        prompt_tokens=0,
        completion_tokens=0,
        total_tokens=0,
        call_type="conversation",
        success=False,
    )


def test_sync_stream_create_failure_records_once():
    rec = MagicMock()

    def failing_create(*_a, **_kw):
        raise RuntimeError("connect fail")

    with patch.object(tt.TokenTracker, "get_instance", return_value=SimpleNamespace(record=rec)):
        with pytest.raises(RuntimeError, match="connect fail"):
            _handle_sync_stream(
                SimpleNamespace(), failing_create, (), {"model": "gpt-create-err"}, "conversation"
            )

    rec.assert_called_once_with(
        model="gpt-create-err",
        prompt_tokens=0,
        completion_tokens=0,
        total_tokens=0,
        call_type="conversation",
        success=False,
    )


@pytest.mark.asyncio
async def test_async_stream_normal_exhaustion_and_close_records_once():
    rec = MagicMock()
    stream = _FakeAsyncStream([_chunk(20, 4), _chunk(20, 9)])

    async def fake_create(*_a, **_kw):
        return stream

    with patch.object(tt.TokenTracker, "get_instance", return_value=SimpleNamespace(record=rec)):
        wrapper = await _handle_async_stream(
            SimpleNamespace(), fake_create, (), {"model": "gpt-async"}, "conversation"
        )
        async for _ in wrapper:
            pass
        await wrapper.aclose()
        await wrapper.close()

    rec.assert_called_once_with(
        model="gpt-test",
        prompt_tokens=20,
        completion_tokens=9,
        total_tokens=29,
        cached_tokens=0,
        call_type="conversation",
        success=True,
    )


@pytest.mark.asyncio
async def test_async_stream_aclosing_break_before_usage_records_non_error_once():
    """Closing an async stream wrapper early via aclosing records success=True without inflating error_count."""
    rec = MagicMock()
    stream = _FakeAsyncStream([_chunk(None), _chunk(20, 9)])

    async def fake_create(*_a, **_kw):
        return stream

    with patch.object(tt.TokenTracker, "get_instance", return_value=SimpleNamespace(record=rec)):
        wrapper = await _handle_async_stream(
            SimpleNamespace(), fake_create, (), {"model": "gpt-async-abort"}, "conversation"
        )
        async with aclosing(wrapper):
            async for _ in wrapper:
                break

    assert stream.closed is True
    rec.assert_called_once_with(
        model="gpt-async-abort",
        prompt_tokens=0,
        completion_tokens=0,
        total_tokens=0,
        call_type="conversation",
        success=True,
    )


@pytest.mark.asyncio
async def test_async_stream_aenter_break_after_usage_and_gc_records_once():
    rec = MagicMock()
    stream = _FakeAsyncStream([_chunk(15, 5), _chunk(15, 10)])

    async def fake_create(*_a, **_kw):
        return stream

    with patch.object(tt.TokenTracker, "get_instance", return_value=SimpleNamespace(record=rec)):
        wrapper = await _handle_async_stream(
            SimpleNamespace(), fake_create, (), {"model": "gpt-async-ctx"}, "conversation"
        )
        async with wrapper:
            async for _ in wrapper:
                break
        await wrapper.aclose()
        del wrapper
        gc.collect()

    assert stream.exited is True
    rec.assert_called_once_with(
        model="gpt-test",
        prompt_tokens=15,
        completion_tokens=5,
        total_tokens=20,
        cached_tokens=0,
        call_type="conversation",
        success=True,
    )


@pytest.mark.asyncio
async def test_async_stream_error_records_failed_once():
    """Mid-stream Exception marks the recorded call as failed."""
    rec = MagicMock()
    stream = _FakeAsyncStream([_chunk(None)], error=RuntimeError("aborted"))

    async def fake_create(*_a, **_kw):
        return stream

    with patch.object(tt.TokenTracker, "get_instance", return_value=SimpleNamespace(record=rec)):
        wrapper = await _handle_async_stream(
            SimpleNamespace(), fake_create, (), {"model": "gpt-async-err"}, "emotion"
        )
        with pytest.raises(RuntimeError):
            async for _ in wrapper:
                pass
        await wrapper.aclose()

    rec.assert_called_once_with(
        model="gpt-async-err",
        prompt_tokens=0,
        completion_tokens=0,
        total_tokens=0,
        call_type="emotion",
        success=False,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("items", "expected_pt", "expected_ct", "expected_tt"),
    [
        ([_chunk(None)], 0, 0, 0),
        ([_chunk(7, 3), _chunk(None)], 7, 3, 10),
    ],
)
async def test_async_stream_cancelled_records_non_error_once(items, expected_pt, expected_ct, expected_tt):
    """Cancellation via CancelledError does not mark the call as an API error."""
    rec = MagicMock()
    stream = _FakeAsyncStream(items, error=asyncio.CancelledError("cancelled"))

    async def fake_create(*_a, **_kw):
        return stream

    with patch.object(tt.TokenTracker, "get_instance", return_value=SimpleNamespace(record=rec)):
        wrapper = await _handle_async_stream(
            SimpleNamespace(), fake_create, (), {"model": "gpt-async-cancel"}, "emotion"
        )
        with pytest.raises(asyncio.CancelledError):
            async for _ in wrapper:
                pass
        await wrapper.aclose()

    if expected_tt:
        rec.assert_called_once_with(
            model="gpt-test",
            prompt_tokens=expected_pt,
            completion_tokens=expected_ct,
            total_tokens=expected_tt,
            cached_tokens=0,
            call_type="emotion",
            success=True,
        )
    else:
        rec.assert_called_once_with(
            model="gpt-async-cancel",
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            call_type="emotion",
            success=True,
        )


@pytest.mark.asyncio
async def test_async_stream_create_failure_records_once():
    rec = MagicMock()

    async def failing_create(*_a, **_kw):
        raise RuntimeError("async connect fail")

    with patch.object(tt.TokenTracker, "get_instance", return_value=SimpleNamespace(record=rec)):
        with pytest.raises(RuntimeError, match="async connect fail"):
            await _handle_async_stream(
                SimpleNamespace(), failing_create, (), {"model": "gpt-async-create-err"}, "conversation"
            )

    rec.assert_called_once_with(
        model="gpt-async-create-err",
        prompt_tokens=0,
        completion_tokens=0,
        total_tokens=0,
        call_type="conversation",
        success=False,
    )


@pytest.mark.asyncio
async def test_async_stream_plain_break_records_without_gc():
    """Plain async break through ChatOpenAI.astream records before cyclic GC runs."""
    from utils.llm_client.openai_client import ChatOpenAI

    rec = MagicMock()

    class _FakeChoiceChunk:
        def __init__(self, text: str, usage=None):
            self.choices = [SimpleNamespace(delta=SimpleNamespace(content=text, tool_calls=None), finish_reason=None)]
            self.usage = usage

    async def _create_wrapped_stream(**_kwargs):
        raw_stream = _FakeAsyncStream([_FakeChoiceChunk("[PASS]"), _FakeChoiceChunk("tail")])
        return await _handle_async_stream(
            SimpleNamespace(), lambda *_a, **_kw: asyncio.sleep(0, result=raw_stream), (), {"model": "gpt-proactive"}, "conversation"
        )

    client = ChatOpenAI(
        model="gpt-proactive",
        api_key="sk-test",
        base_url="https://example.com/v1",
        max_completion_tokens=256,
        timeout=10.0,
    )

    was_enabled = gc.isenabled()
    gc.disable()
    try:
        with patch.object(
            client._aclient.chat.completions, "create", side_effect=_create_wrapped_stream
        ), patch.object(
            tt.TokenTracker, "get_instance", return_value=SimpleNamespace(record=rec)
        ):
            # 1. Explicit aclosing around ChatOpenAI.astream() records immediately on exit.
            async with aclosing(client.astream([{"role": "user", "content": "hi"}])) as stream:
                async for _ in stream:
                    break
            assert rec.call_count == 1

            # 2. Plain `async for ... in client.astream(...): break` (real caller pattern)
            # breaks no-GC cycle once Python finishes async-gen teardown on loop turns.
            rec.reset_mock()
            async with client:
                async for _ in client.astream([{"role": "user", "content": "hi"}]):
                    break
            for _ in range(3):
                if rec.call_count:
                    break
                await asyncio.sleep(0)
            assert rec.call_count == 1
            rec.assert_called_once_with(
                model="gpt-proactive",
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
                call_type="conversation",
                success=True,
            )
    finally:
        if was_enabled:
            gc.enable()
        await client.aclose()


def test_dropped_stream_wrapper_gc_under_tracker_lock_does_not_deadlock():
    """Dropping sync and async stream wrappers while holding TokenTracker._lock never deadlocks during GC."""
    import threading

    tracker = tt.TokenTracker.get_instance()
    error: list[BaseException] = []

    def _worker():
        try:
            sync_wrap = _handle_sync_stream(
                SimpleNamespace(),
                lambda *_a, **_kw: _FakeSyncStream([_chunk(None)]),
                (),
                {"model": "gpt-gc"},
                "conversation",
            )

            async def _build_async():
                return await _handle_async_stream(
                    SimpleNamespace(),
                    lambda *_a, **_kw: asyncio.sleep(0, result=_FakeAsyncStream([_chunk(None)])),
                    (),
                    {"model": "gpt-gc-async"},
                    "conversation",
                )

            async_wrap = asyncio.run(_build_async())
            async_gen = async_wrap.__aiter__()

            with tracker._lock:
                del sync_wrap
                del async_gen
                del async_wrap
                gc.collect()
        except BaseException as exc:  # pragma: no cover - surfaced by assertion below
            error.append(exc)

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    thread.join(timeout=3.0)
    assert not thread.is_alive(), "Deadlock detected during GC while TokenTracker._lock was held"
    assert error == []


@pytest.mark.parametrize(
    ("chunks", "expected_tt"),
    [
        ([_chunk(11, 7), _chunk(None)], 18),
        ([_chunk(None), _chunk(None)], 0),
    ],
)
def test_started_sync_iterator_dropped_under_tracker_lock_defers_and_drains_once(
    tmp_path, monkeypatch, chunks, expected_tt
):
    """Deleting a started sync iterator under TokenTracker._lock never deadlocks and drains on stats read."""
    import atexit
    from datetime import date
    import threading

    from utils.token_tracker import reporting

    monkeypatch.setattr(tt, "get_config_manager", lambda: SimpleNamespace(config_dir=tmp_path))
    monkeypatch.setattr(reporting, "_DO_NOT_TRACK", True)
    tracker = tt.TokenTracker()
    atexit.unregister(tracker._atexit_save)
    error: list[BaseException] = []
    wrapper_holder: list = []

    def _worker():
        try:
            with patch.object(tt.TokenTracker, "get_instance", return_value=tracker):
                wrapper = _handle_sync_stream(
                    SimpleNamespace(),
                    lambda *_a, **_kw: _FakeSyncStream(chunks),
                    (),
                    {"model": "gpt-iter-lock"},
                    "conversation",
                )
                wrapper_holder.append(wrapper)
                it = iter(wrapper)
                next(it)
                with tracker._lock:
                    del it
                    gc.collect()
        except BaseException as exc:  # pragma: no cover - surfaced by assertion below
            error.append(exc)

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    thread.join(timeout=3.0)
    assert not thread.is_alive(), "Deadlock detected closing started sync iterator while TokenTracker._lock was held"
    assert error == []
    assert len(tracker._pending_records) == 1
    assert tracker._dirty is False

    # Reading today's or window stats drains pending records and keeps idempotency on explicit close().
    today_stats = tracker.get_today_stats()["stats"]
    assert today_stats["call_count"] == 1
    assert today_stats["total_tokens"] == expected_tt
    assert len(tracker._pending_records) == 0

    with patch.object(tt.TokenTracker, "get_instance", return_value=tracker):
        wrapper_holder[0].close()

    stats = tracker.get_stats(days=1)["daily_stats"][date.today().isoformat()]
    assert stats["call_count"] == 1
    assert stats["total_tokens"] == expected_tt


def test_save_drains_deferred_iterator_records_even_when_dirty_was_false(tmp_path, monkeypatch):
    """Calling save() persists records queued in _pending_records even before _dirty becomes True."""
    import atexit
    from datetime import date
    import json

    from utils.token_tracker import reporting

    monkeypatch.setattr(tt, "get_config_manager", lambda: SimpleNamespace(config_dir=tmp_path))
    monkeypatch.setattr(reporting, "_DO_NOT_TRACK", True)
    tracker = tt.TokenTracker()
    atexit.unregister(tracker._atexit_save)

    with patch.object(tt.TokenTracker, "get_instance", return_value=tracker):
        wrapper = _handle_sync_stream(
            SimpleNamespace(),
            lambda *_a, **_kw: _FakeSyncStream([_chunk(5, 9), _chunk(None)]),
            (),
            {"model": "gpt-save-drain"},
            "conversation",
        )
        it = iter(wrapper)
        next(it)
        with tracker._lock:
            del it
            gc.collect()

    assert tracker._dirty is False
    assert len(tracker._pending_records) == 1

    tracker.save()
    assert tracker._dirty is False
    assert len(tracker._pending_records) == 0

    persisted = json.loads(tracker._storage_path.read_text(encoding="utf-8"))
    day = persisted["daily_stats"][date.today().isoformat()]
    assert day["call_count"] == 1
    assert day["total_tokens"] == 14


def test_drain_pending_locked_skips_malformed_entry_and_persists_valid_tail(tmp_path, monkeypatch, caplog):
    """Dropping one malformed queued entry in _pending_records logs a warning without breaking save or later entries."""
    import atexit
    from datetime import date
    import json

    from utils.token_tracker import reporting

    monkeypatch.setattr(tt, "get_config_manager", lambda: SimpleNamespace(config_dir=tmp_path))
    monkeypatch.setattr(reporting, "_DO_NOT_TRACK", True)
    tracker = tt.TokenTracker()
    atexit.unregister(tracker._atexit_save)

    today = date.today().isoformat()
    # Non-numeric prompt_tokens raises TypeError in +=; second entry must still apply cleanly.
    tracker._pending_records.append((today, "bad-model", "not-an-int", 0, 0, 0, "chat", True, {}, 0))
    tracker._pending_records.append((today, "good-model", 3, 4, 7, 0, "chat", True, {"ts": 1.0, "tt": 7}, 0))

    with caplog.at_level("WARNING"):
        tracker.save()

    assert len(tracker._pending_records) == 0
    assert any("failed to apply deferred record" in rec.message for rec in caplog.records)
    persisted = json.loads(tracker._storage_path.read_text(encoding="utf-8"))
    day = persisted["daily_stats"][today]
    assert day["call_count"] == 1
    assert day["total_tokens"] == 7
    assert "good-model" in day["by_model"]
